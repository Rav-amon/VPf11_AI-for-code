"""Проверяет каждый эндпоинт HTTP API пользователей."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = 'http://127.0.0.1:5000'
REQUEST_TIMEOUT_SECONDS = 10
SAMPLE_NAME = 'Проверка'

EndpointResult = tuple[bool, str]


def request_json(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    """Отправляет запрос и возвращает статус вместе с JSON-телом."""
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, _read_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _read_json(error.read())


def _read_json(raw: bytes) -> dict[str, object]:
    """Разбирает тело ответа. Пустое тело не считается успешным JSON-объектом."""
    if raw == b'':
        raise ValueError('пустой ответ')
    parsed = json.loads(raw.decode('utf-8'))
    if not isinstance(parsed, dict):
        raise ValueError('ответ не является JSON-объектом')
    return parsed


def _expect(
    status: int,
    body: dict[str, object],
    expected_status: int,
    expected_message: str,
) -> str | None:
    """Возвращает текст проблемы, если статус, конверт или сообщение не совпали."""
    if status != expected_status:
        return f'статус {status}, ожидался {expected_status}'
    if body.get('error') is not None:
        return f'в успешном ответе есть ошибка: {body.get("error")}'
    if body.get('message') != expected_message:
        return f'сообщение {body.get("message")!r}'
    if not isinstance(body.get('data'), dict):
        return 'поле data не является объектом'
    return None


def check_create(base_url: str) -> tuple[EndpointResult, int]:
    """POST /api/v1/users создаёт пользователя и возвращает его id."""
    status, body = request_json(
        'POST',
        f'{base_url}/api/v1/users',
        {'name': SAMPLE_NAME},
    )
    problem = _expect(status, body, 201, 'Пользователь создан')
    if problem is not None:
        return (False, f'POST /api/v1/users: {problem}'), 0
    data = body['data']
    if not isinstance(data, dict):
        return (False, 'POST /api/v1/users: нет объекта data'), 0
    user_id = data.get('id')
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
        return (False, f'POST /api/v1/users: неверный id {user_id!r}'), 0
    return (True, f'POST /api/v1/users -> {user_id}'), user_id


def check_read(base_url: str, user_id: int) -> EndpointResult:
    """GET /api/v1/users/{id} возвращает ту же запись."""
    status, body = request_json('GET', f'{base_url}/api/v1/users/{user_id}')
    problem = _expect(status, body, 200, 'Пользователь найден')
    if problem is not None:
        return False, f'GET /api/v1/users/{{id}}: {problem}'
    data = body['data']
    if not isinstance(data, dict):
        return False, 'GET /api/v1/users/{id}: нет объекта data'
    if data.get('id') != user_id or data.get('name') != SAMPLE_NAME:
        return False, f'GET /api/v1/users/{{id}}: данные {data!r}'
    return True, f'GET /api/v1/users/{user_id}'


def check_mark_active(base_url: str, user_id: int) -> EndpointResult:
    """POST /api/v1/users/{id}/active добавляет id в список активных."""
    status, body = request_json('POST', f'{base_url}/api/v1/users/{user_id}/active')
    problem = _expect(status, body, 200, 'Пользователь отмечен активным')
    if problem is not None:
        return False, f'POST /api/v1/users/{{id}}/active: {problem}'
    data = body['data']
    if not isinstance(data, dict):
        return False, 'POST /api/v1/users/{id}/active: нет объекта data'
    active_ids = data.get('active_user_ids')
    if not isinstance(active_ids, list) or user_id not in active_ids:
        return False, f'POST /api/v1/users/{{id}}/active: список {active_ids!r}'
    return True, f'POST /api/v1/users/{user_id}/active'


def check_list_active(base_url: str, user_id: int) -> EndpointResult:
    """GET /api/v1/active-users отдаёт тот же список."""
    status, body = request_json('GET', f'{base_url}/api/v1/active-users')
    problem = _expect(status, body, 200, 'Список активных пользователей получен')
    if problem is not None:
        return False, f'GET /api/v1/active-users: {problem}'
    data = body['data']
    if not isinstance(data, dict):
        return False, 'GET /api/v1/active-users: нет объекта data'
    active_ids = data.get('active_user_ids')
    if not isinstance(active_ids, list) or user_id not in active_ids:
        return False, f'GET /api/v1/active-users: список {active_ids!r}'
    return True, 'GET /api/v1/active-users'


def main() -> int:
    """Запускает проверки и возвращает 0, только если прошли все четыре маршрута."""
    base_url = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else DEFAULT_BASE_URL
    try:
        created, user_id = check_create(base_url)
        results = [created]
        if created[0]:
            results.append(check_read(base_url, user_id))
            results.append(check_mark_active(base_url, user_id))
            results.append(check_list_active(base_url, user_id))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
        print(f'Не удалось обратиться к {base_url}: {error}')
        return 1

    for passed, label in results:
        print(('OK' if passed else 'FAIL'), label)
    if all(passed for passed, _label in results) and len(results) == 4:
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
