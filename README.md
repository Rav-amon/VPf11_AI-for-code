# Пользователи: библиотека и HTTP API

Папки `go_server` нет. Каталог Go-сервера называется `go-server`, через дефис:

```powershell
cd C:\Users\Ravil\Desktop\VPf11_AI-for-code\go-server
```

Проверка эндпоинтов. Сначала в одном окне запустите сервер, затем в другом выполните скрипт.

```powershell
cd C:\Users\Ravil\Desktop\VPf11_AI-for-code
python api.py
```

```powershell
cd C:\Users\Ravil\Desktop\VPf11_AI-for-code\go-server
python .\check-endpoints.py http://127.0.0.1:5000
```

В PowerShell нельзя писать просто `check-endpoints.py`: оболочка не запускает файл из текущей папки. Перед именем нужен `python .\`.

Успех — четыре строки `OK` и код выхода `0`.

Проект состоит из двух независимых частей:

- `utils.py` — библиотека работы с пользователями, паролями и активными сессиями.
- `api.py` — HTTP API на Flask для создания пользователя, чтения по id и короткого списка активных.

У каждой части своя база SQLite и свой список активных id. Вызов маршрута API не меняет данные `utils.py`.

## Требования

- Python 3.10 или новее.
- Flask 3.1.1 для HTTP API. Библиотеке `utils.py` сторонние пакеты не нужны.

## Установка

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS и Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Запуск API

```bash
python api.py
```

Сервер слушает `http://127.0.0.1:5000`. Режим отладки выключен.

База API по умолчанию — файл `test.db` в текущем каталоге. Другой путь задаётся переменной окружения:

```bash
set API_DB_PATH=C:\data\api.db
python api.py
```

В PowerShell используйте `$env:API_DB_PATH = "C:\data\api.db"`.

Таблица `users` создаётся при первом запросе: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `name TEXT NOT NULL`.

## Формат ответа API

Успех:

```json
{
  "data": {},
  "error": null,
  "message": "Пользователь создан"
}
```

Ошибка:

```json
{
  "data": null,
  "error": {
    "code": "user_not_found",
    "message": "Пользователь не найден",
    "details": {}
  },
  "message": "Пользователь не найден"
}
```

Имя обрезается по краям. Пустая строка и строка длиннее 200 символов отклоняются. Повторяющиеся имена разрешены.

## Маршруты

### Создать пользователя

`POST /api/v1/users`

```bash
curl -X POST http://127.0.0.1:5000/api/v1/users ^
  -H "Content-Type: application/json" ^
  -d "{\"name\": \"Анна\"}"
```

Ответ `201`:

```json
{
  "data": { "id": 1 },
  "error": null,
  "message": "Пользователь создан"
}
```

Тело должно быть JSON-объектом. Иначе код ошибки `invalid_body` и статус `400`. Нестроковое или пустое имя даёт `invalid_name` и статус `400`.

### Прочитать пользователя

`GET /api/v1/users/<id>`

`id` — целое число от 1. Ответ `200`:

```json
{
  "data": { "id": 1, "name": "Анна" },
  "error": null,
  "message": "Пользователь найден"
}
```

Нет записи — `404` и код `user_not_found`. Отрицательный id — `400` и код `invalid_user_id`. Значение, которое не является целым числом, не попадает в маршрут и возвращает `404` с кодом `not_found`.

### Отметить активным

`POST /api/v1/users/<id>/active`

Пользователь должен уже существовать. В памяти процесса хранится не больше 5 последних id. Шестая отметка вытесняет самую старую. Один и тот же id может встретиться несколько раз.

Ответ `200`:

```json
{
  "data": { "active_user_ids": [1] },
  "error": null,
  "message": "Пользователь отмечен активным"
}
```

Список живёт только в памяти процесса и очищается при перезапуске.

### Список активных

`GET /api/v1/active-users`

Ответ `200`:

```json
{
  "data": { "active_user_ids": [1] },
  "error": null,
  "message": "Список активных пользователей получен"
}
```

## Коды ошибок API

| Код | Статус | Когда возникает |
| --- | --- | --- |
| `invalid_body` | 400 | Тело запроса не является JSON-объектом |
| `invalid_name` | 400 | Имя пустое, слишком длинное или не строка |
| `invalid_user_id` | 400 | Id меньше 1 |
| `user_not_found` | 404 | Пользователь с таким id не найден |
| `not_found` | 404 | Маршрут не найден |
| `method_not_allowed` | 405 | Метод не поддерживается этим маршрутом |
| `database_error` | 500 | Сбой SQLite |
| `internal_error` | 500 | Непредвиденная ошибка сервера |

Клиенту не отдаются текст SQL и внутренние исключения. Техническая причина пишется в лог.

## Библиотека utils.py

Модуль открывает свою базу `users.db`. Путь меняется переменной `USERS_DB_PATH`. Схема создаётся при первом вызове.

```python
import utils

user_id = utils.add_user('Анна', ['admin'])
utils.store_password(user_id, 'secret')
utils.verify_password(user_id, 'secret')  # True
utils.set_active(user_id)
utils.get_active_users()  # копия списка, не больше 5 id
utils.get_user_by_name('Анна')
```

`add_user` копирует переданный список тегов и добавляет к копии метку `new`. Исходный список не меняется. Без тегов сохраняется `['new']`. Функция возвращает `int`.

`get_user_by_name` возвращает словарь `{id, name, tags}` или `None`. При нескольких записях с одним именем берётся запись с наименьшим id.

`store_password` пишет в таблицу `password_hashes` соль и отпечаток PBKDF2-HMAC-SHA256 (200 000 итераций). Исходный пароль не сохраняется. Повторный вызов заменяет отпечаток. `verify_password` возвращает `False` и для неверного пароля, и для неизвестного пользователя.

`set_active` и `get_active_users` работают со списком в памяти этого процесса. Это отдельный список, не тот, что у HTTP API.

Ошибки библиотеки — исключение `UserError`. У него есть поля `code`, `message`, `details` и метод `to_dict()`.

| Код | Когда возникает |
| --- | --- |
| `invalid_name` | Имя пустое, длиннее 200 символов или не строка |
| `invalid_password` | Пароль пустой, не строка или длиннее 1024 символов |
| `invalid_user_id` | Id не является положительным целым. `True` тоже отклоняется |
| `invalid_tags` | Теги не список строк, тег пустой, тег длиннее 64 символов, тегов больше 50 или JSON в базе повреждён |
| `user_not_found` | `store_password` вызван для отсутствующего id |
| `invalid_password_hash` | Сохранённая соль не является hex-строкой |
| `database_error` | Сбой SQLite или база не вернула id |

Пробелы в пароле не обрезаются: они входят в секрет.

## Ограничения

- Активные id обоих модулей пропадают после остановки процесса.
- API и библиотека не проверяют уникальность имени.
- Пароли доступны только через `utils.py`. В HTTP API маршрута для пароля нет.
- Сервер API привязан к `127.0.0.1:5000`.
