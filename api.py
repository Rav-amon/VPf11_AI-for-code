"""HTTP API пользователей: создание, чтение и список активных."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import deque
from collections.abc import Callable
from typing import TypeVar

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_INTERNAL = 500

MAX_NAME_LENGTH = 200
MAX_ACTIVE_USERS = 5
DB_TIMEOUT_SECONDS = 5.0
DB_PATH = os.environ.get('API_DB_PATH', 'test.db')

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
"""

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.json.ensure_ascii = False

_db_lock = threading.Lock()
_active_lock = threading.Lock()
# Общий список только под блокировкой. Поток на каждый запрос здесь не нужен.
_active_users: deque[int] = deque(maxlen=MAX_ACTIVE_USERS)

TResult = TypeVar('TResult')


class ApiError(Exception):
    """Ошибка запроса с кодом для клиента и HTTP-статусом."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        """Возвращает ошибку в едином виде."""
        return {
            'code': self.code,
            'message': self.message,
            'details': self.details,
        }


def _fail(error: ApiError):
    """Собирает ответ с пустыми данными и заполненной ошибкой."""
    return jsonify({
        'data': None,
        'error': error.to_dict(),
        'message': error.message,
    }), error.status


def _ok(data: object, message: str, status: int):
    """Собирает успешный ответ."""
    return jsonify({
        'data': data,
        'error': None,
        'message': message,
    }), status


def _log_database_error(reason: str) -> None:
    """Пишет техническую причину сбоя базы без тела запроса."""
    logger.error(
        '%s',
        {
            'level': 'error',
            'message': 'Ошибка базы данных',
            'context': {'code': 'database_error', 'reason': reason},
        },
    )


def _run(operation: Callable[[sqlite3.Connection], TResult]) -> TResult:
    """Открывает соединение на время операции и закрывает его в любом случае."""
    with _db_lock:
        connection = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        try:
            connection.row_factory = sqlite3.Row
            connection.executescript(SCHEMA_SQL)
            result = operation(connection)
            connection.commit()
            return result
        except ApiError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            _log_database_error(str(exc))
            raise ApiError(
                'database_error',
                'Не удалось выполнить операцию с базой данных',
                HTTP_INTERNAL,
            ) from exc
        finally:
            connection.close()


def _require_name(name: object) -> str:
    """Проверяет имя и убирает крайние пробелы."""
    if not isinstance(name, str):
        raise ApiError(
            'invalid_name',
            'Имя должно быть строкой',
            HTTP_BAD_REQUEST,
            {'received_type': type(name).__name__},
        )
    normalized = name.strip()
    if not normalized or len(normalized) > MAX_NAME_LENGTH:
        raise ApiError(
            'invalid_name',
            'Имя должно быть непустым и не длиннее допустимого',
            HTTP_BAD_REQUEST,
            {'max_length': MAX_NAME_LENGTH},
        )
    return normalized


def _read_json_object() -> dict[str, object]:
    """Читает тело запроса и требует JSON-объект."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(
            'invalid_body',
            'Тело запроса должно быть JSON-объектом',
            HTTP_BAD_REQUEST,
        )
    return payload


def _insert_user(name: str) -> int:
    """Сохраняет пользователя через параметр запроса и возвращает id."""

    def insert(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            'INSERT INTO users (name) VALUES (?)',
            (name,),
        )
        user_id = cursor.lastrowid
        if user_id is None:
            raise ApiError(
                'database_error',
                'База не вернула идентификатор пользователя',
                HTTP_INTERNAL,
            )
        return int(user_id)

    return _run(insert)


def _select_user(user_id: int) -> dict[str, object] | None:
    """Возвращает пользователя по id или None, если записи нет."""

    def read(connection: sqlite3.Connection) -> dict[str, object] | None:
        row = connection.execute(
            'SELECT id, name FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {'id': int(row['id']), 'name': str(row['name'])}

    return _run(read)


def _require_existing_user(user_id: int) -> dict[str, object]:
    """Проверяет id и наличие строки в базе."""
    if user_id < 1:
        raise ApiError(
            'invalid_user_id',
            'Идентификатор пользователя должен быть положительным целым',
            HTTP_BAD_REQUEST,
        )
    user = _select_user(user_id)
    if user is None:
        raise ApiError(
            'user_not_found',
            'Пользователь не найден',
            HTTP_NOT_FOUND,
            {'user_id': user_id},
        )
    return user


@app.post('/api/v1/users')
def add_user():
    """Создаёт пользователя и возвращает его id."""
    payload = _read_json_object()
    name = _require_name(payload.get('name', ''))
    user_id = _insert_user(name)
    return _ok(
        {'id': user_id},
        'Пользователь создан',
        HTTP_CREATED,
    )


@app.get('/api/v1/users/<int:user_id>')
def get_user(user_id: int):
    """Возвращает пользователя. Для чтения используется код 200."""
    user = _require_existing_user(user_id)
    return _ok(user, 'Пользователь найден', HTTP_OK)


@app.post('/api/v1/users/<int:user_id>/active')
def mark_user_active(user_id: int):
    """Помечает существующего пользователя активным."""
    _require_existing_user(user_id)
    with _active_lock:
        _active_users.append(user_id)
        active_ids = list(_active_users)
    return _ok(
        {'active_user_ids': active_ids},
        'Пользователь отмечен активным',
        HTTP_OK,
    )


@app.get('/api/v1/active-users')
def list_active_users():
    """Возвращает копию списка активных id."""
    with _active_lock:
        active_ids = list(_active_users)
    return _ok(
        {'active_user_ids': active_ids},
        'Список активных пользователей получен',
        HTTP_OK,
    )


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    """Превращает ошибку контракта в JSON-ответ."""
    return _fail(error)


@app.errorhandler(HTTP_NOT_FOUND)
def handle_not_found(_error: HTTPException):
    """Отвечает на неизвестный маршрут."""
    return _fail(ApiError('not_found', 'Маршрут не найден', HTTP_NOT_FOUND))


@app.errorhandler(HTTP_METHOD_NOT_ALLOWED)
def handle_method_not_allowed(_error: HTTPException):
    """Отвечает, если метод не подходит маршруту."""
    return _fail(ApiError(
        'method_not_allowed',
        'Метод не поддерживается',
        HTTP_METHOD_NOT_ALLOWED,
    ))


@app.errorhandler(Exception)
def handle_unexpected(error: Exception):
    """Логирует сбой и не отдаёт клиенту внутренние детали."""
    if isinstance(error, HTTPException):
        return error
    logger.error(
        '%s',
        {
            'level': 'error',
            'message': 'Необработанная ошибка API',
            'context': {'reason': error.__class__.__name__},
        },
    )
    return _fail(ApiError(
        'internal_error',
        'Внутренняя ошибка сервера',
        HTTP_INTERNAL,
    ))


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
