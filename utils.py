"""Операции с пользователями: SQLite, проверка пароля и активные сессии."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
from collections import deque
from collections.abc import Callable
from typing import TypedDict, TypeVar

# Лимиты входных данных. Значения вынесены, чтобы не размазывать их по проверкам.
MAX_NAME_LENGTH = 200
MAX_PASSWORD_LENGTH = 1024
MAX_INPUT_TAGS = 50
MAX_TAG_LENGTH = 64
MAX_ACTIVE_USERS = 5
PBKDF2_ITERATIONS = 200_000
SALT_SIZE_BYTES = 16
DB_TIMEOUT_SECONDS = 5.0
TAG_NEW = 'new'

DB_PATH = os.environ.get('USERS_DB_PATH', 'users.db')

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tags TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_hashes (
    user_id INTEGER PRIMARY KEY,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);
"""

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()
_active_lock = threading.Lock()
# Ограниченная очередь: при переполнении сам вытесняется самый старый id.
_active_users: deque[int] = deque(maxlen=MAX_ACTIVE_USERS)

TResult = TypeVar('TResult')


class UserRecord(TypedDict):
    """Запись пользователя, которую возвращает чтение из базы."""

    id: int
    name: str
    tags: list[str]


class UserError(Exception):
    """Ошибка контракта модуля: код, сообщение и дополнительные детали."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        """Возвращает ошибку в едином виде для API и логов."""
        return {
            'code': self.code,
            'message': self.message,
            'details': self.details,
        }


def _log_database_error(reason: str) -> None:
    """Пишет техническую причину сбоя базы. Пароли сюда не попадают."""
    logger.error(
        '%s',
        {
            'level': 'error',
            'message': 'Ошибка базы данных',
            'context': {'code': 'database_error', 'reason': reason},
        },
    )


def _require_name(name: object) -> str:
    """Проверяет имя и возвращает его без крайних пробелов."""
    if not isinstance(name, str):
        raise UserError(
            'invalid_name',
            'Имя должно быть строкой',
            {'received_type': type(name).__name__},
        )
    normalized = name.strip()
    if not normalized or len(normalized) > MAX_NAME_LENGTH:
        raise UserError(
            'invalid_name',
            'Имя должно быть непустым и не длиннее допустимого',
            {'max_length': MAX_NAME_LENGTH},
        )
    return normalized


def _require_password(password: object) -> str:
    """Проверяет пароль, не обрезая его: пробелы могут быть частью секрета."""
    if not isinstance(password, str) or password == '':
        raise UserError('invalid_password', 'Пароль должен быть непустой строкой')
    if len(password) > MAX_PASSWORD_LENGTH:
        raise UserError(
            'invalid_password',
            'Пароль слишком длинный',
            {'max_length': MAX_PASSWORD_LENGTH},
        )
    return password


def _require_user_id(user_id: object) -> int:
    """Отсекает bool: в Python он является подклассом int."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise UserError(
            'invalid_user_id',
            'Идентификатор пользователя должен быть положительным целым',
        )
    return user_id


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """Копирует теги и добавляет метку нового пользователя в копию."""
    if tags is None:
        source: list[str] = []
    elif isinstance(tags, list):
        source = list(tags)
    else:
        raise UserError('invalid_tags', 'Теги должны быть списком строк')

    if len(source) > MAX_INPUT_TAGS:
        raise UserError(
            'invalid_tags',
            'Слишком много тегов',
            {'max_count': MAX_INPUT_TAGS},
        )

    for tag in source:
        if not isinstance(tag, str) or tag.strip() == '' or len(tag) > MAX_TAG_LENGTH:
            raise UserError('invalid_tags', 'Каждый тег должен быть непустой строкой')

    return [*source, TAG_NEW]


def _decode_tags(raw: str) -> list[str]:
    """Превращает JSON-текст из базы обратно в список строк."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserError(
            'invalid_tags',
            'Не удалось прочитать теги пользователя',
        ) from exc

    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise UserError('invalid_tags', 'Теги пользователя имеют неверный формат')
    return list(parsed)


def _derive_password_hash(password: str, salt: bytes) -> str:
    """Считает стойкий отпечаток пароля. Одинаковые входные данные дают тот же результат."""
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        PBKDF2_ITERATIONS,
    )
    return digest.hex()


def _run(operation: Callable[[sqlite3.Connection], TResult]) -> TResult:
    """Открывает соединение на время операции и закрывает его при любом исходе."""
    with _db_lock:
        connection = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            connection.executescript(SCHEMA_SQL)
            result = operation(connection)
            connection.commit()
            return result
        except UserError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            _log_database_error(str(exc))
            raise UserError(
                'database_error',
                'Не удалось выполнить операцию с базой данных',
                {'reason': exc.__class__.__name__},
            ) from exc
        finally:
            connection.close()


def add_user(name: str, tags: list[str] | None = None) -> int:
    """Добавляет пользователя и возвращает его числовой id.

    Список тегов копируется. Метка нового пользователя попадает только в копию,
    поэтому аргумент вызывающего кода остаётся прежним.
    """
    normalized_name = _require_name(name)
    normalized_tags = _normalize_tags(tags)
    payload = json.dumps(normalized_tags, ensure_ascii=False)

    def insert(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            'INSERT INTO users (name, tags) VALUES (?, ?)',
            (normalized_name, payload),
        )
        user_id = cursor.lastrowid
        if user_id is None:
            raise UserError('database_error', 'База не вернула идентификатор пользователя')
        return int(user_id)

    return _run(insert)


def store_password(user_id: int, password: str) -> None:
    """Сохраняет соль и отпечаток пароля в базе. Исходный пароль не записывается."""
    valid_user_id = _require_user_id(user_id)
    valid_password = _require_password(password)
    salt = os.urandom(SALT_SIZE_BYTES)
    # Отпечаток считается до захвата блокировки базы: PBKDF2 намеренно медленный.
    password_hash = _derive_password_hash(valid_password, salt)
    salt_hex = salt.hex()

    def write(connection: sqlite3.Connection) -> None:
        existing = connection.execute(
            'SELECT id FROM users WHERE id = ?',
            (valid_user_id,),
        ).fetchone()
        if existing is None:
            raise UserError(
                'user_not_found',
                'Пользователь не найден',
                {'user_id': valid_user_id},
            )
        connection.execute(
            """
            INSERT INTO password_hashes (user_id, salt, password_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                salt = excluded.salt,
                password_hash = excluded.password_hash
            """,
            (valid_user_id, salt_hex, password_hash),
        )

    _run(write)


def verify_password(user_id: int, password: str) -> bool:
    """Проверяет пароль по сохранённому отпечатку.

    Отсутствие пользователя и неверный пароль дают один и тот же ответ False,
    чтобы по результату нельзя было узнать, есть ли такая запись.
    """
    valid_user_id = _require_user_id(user_id)
    valid_password = _require_password(password)

    def read(connection: sqlite3.Connection) -> tuple[str, str] | None:
        row = connection.execute(
            'SELECT salt, password_hash FROM password_hashes WHERE user_id = ?',
            (valid_user_id,),
        ).fetchone()
        if row is None:
            return None
        # Копируем значения до закрытия соединения: строка результата к нему привязана.
        return (str(row['salt']), str(row['password_hash']))

    stored = _run(read)
    if stored is None:
        return False

    salt_hex, stored_hash = stored
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise UserError(
            'invalid_password_hash',
            'Сохранённый отпечаток пароля повреждён',
        ) from exc

    actual_hash = _derive_password_hash(valid_password, salt)
    if len(actual_hash) != len(stored_hash):
        return False
    return hmac.compare_digest(actual_hash, stored_hash)


def set_active(user_id: int) -> None:
    """Помечает пользователя активным. В списке остаётся не больше заданного предела."""
    valid_user_id = _require_user_id(user_id)
    with _active_lock:
        _active_users.append(valid_user_id)


def get_active_users() -> list[int]:
    """Возвращает копию активных id, чтобы вызывающий код не менял внутренний список."""
    with _active_lock:
        return list(_active_users)


def get_user_by_name(name: str) -> UserRecord | None:
    """Ищет пользователя по имени. Если записи нет, возвращает None."""
    normalized_name = _require_name(name)

    def read(connection: sqlite3.Connection) -> tuple[int, str, str] | None:
        row = connection.execute(
            """
            SELECT id, name, tags
            FROM users
            WHERE name = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (normalized_name,),
        ).fetchone()
        if row is None:
            return None
        return (int(row['id']), str(row['name']), str(row['tags']))

    stored = _run(read)
    if stored is None:
        return None

    user_id, user_name, tags_raw = stored
    return {
        'id': user_id,
        'name': user_name,
        'tags': _decode_tags(tags_raw),
    }
