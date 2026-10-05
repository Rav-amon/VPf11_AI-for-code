# HTTP API пользователей на Go

Перенос сервера из `api.py`. Маршруты, коды ответов и формат JSON те же. Библиотека `utils.py` в этот каталог не входит.

Каталог называется `go-server`, через дефис. Папки `go_server` нет.

```powershell
cd C:\Users\Ravil\Desktop\VPf11_AI-for-code\go-server
```

Локально без переменных сервер слушает `http://127.0.0.1:5000`. В Docker он слушает `0.0.0.0:5000`, чтобы порт был доступен снаружи контейнера. База — файл SQLite. Список активных id хранится в памяти и очищается при остановке.

## Что установить на Windows 11

Для запуска и переноса через Docker Hub нужен [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). На Windows 11 ему нужен WSL2. Если установка просит WSL, в PowerShell от администратора:

```powershell
wsl --install
```

После этого перезагрузите компьютер и откройте Docker Desktop. Дождитесь, пока движок запущен. Пока Desktop закрыт, `docker build` отвечает, что канал `dockerDesktopLinuxEngine` не найден.

Go для Docker не нужен: компилятор уже есть внутри образа. Python нужен только для скрипта проверки эндпоинтов.

## Запуск в Docker

Сборка и запуск из этого каталога:

```powershell
docker build -t users-api .
docker run --rm -p 5000:5000 -v users-data:/data users-api
```

Сначала дождитесь окончания `docker build`. Если сборка прервалась, локального образа нет. Команда `docker run users-api` тогда пытается скачать его с Docker Hub и падает с `pull access denied`: такого публичного репозитория нет.

Внутри контейнера сервер слушает порт `5000`. Проброс `-p 8080:8080` неверен. Если снаружи нужен порт `8080`:

```powershell
docker run --rm -p 8080:5000 -v users-data:/data users-api
```

Остановка контейнера, запущенного без `-d`: `Ctrl+C`. База лежит в томе `users-data`, файл внутри контейнера — `/data/test.db`.

Если порт `5000` занят процессом `python api.py`, остановите его перед запуском контейнера.

## Проверка эндпоинтов

Сервер уже должен слушать порт `5000`. В PowerShell файл из текущей папки сам по себе не запускается. Нужны `python` и `.\`:

```powershell
python .\check-endpoints.py http://127.0.0.1:5000
```

Тот же запуск через обёртку:

```powershell
.\check-endpoints.ps1 http://127.0.0.1:5000
```

Команда `check-endpoints.py` без `python .\` завершается ошибкой «term is not recognized». Успех — четыре строки `OK` и код выхода `0`.

## Запуск через Go

Этот способ работает, только если `go version` находит Go 1.26 или новее. На чистой Windows 11 команды `go` нет, пока Go не установлен с [https://go.dev/dl/](https://go.dev/dl/) и не открыт новый терминал.

```powershell
go run .
```

Остановка: `Ctrl+C`. Рядом появится `test.db`. Другой файл базы:

```powershell
$env:API_DB_PATH = "C:\data\api.db"
go run .
```

Каталог для файла базы должен уже существовать. Сборка exe:

```powershell
go build -o users-api.exe .
.\users-api.exe
```

Адрес можно сменить переменной `API_LISTEN_ADDR`. Пример для доступа из локальной сети: `0.0.0.0:5000`.

Проверка пакета без поднятого сервера:

```powershell
go test
```

Компилятор C не нужен. SQLite подключается драйвером `modernc.org/sqlite`.

## Перенос через Docker Hub

Зарегистрируйтесь на [https://hub.docker.com](https://hub.docker.com) и подставьте своё имя вместо `ВАШ_ЛОГИН`.

На этом компьютере, при запущенном Docker Desktop:

```powershell
docker login
docker build -t ВАШ_ЛОГИН/users-api:1.0.0 .
docker push ВАШ_ЛОГИН/users-api:1.0.0
```

На другом компьютере или сервере достаточно Docker. Исходники и Go не нужны.

```powershell
docker login
docker pull ВАШ_ЛОГИН/users-api:1.0.0
docker run -d --name users-api --restart unless-stopped -p 5000:5000 -v users-data:/data ВАШ_ЛОГИН/users-api:1.0.0
```

Остановка на сервере: `docker stop users-api`.

## Формат ответа

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

Имя обрезается по краям. Пустое имя и имя длиннее 200 символов отклоняются. Длина считается в символах. Одинаковые имена сохранять можно.

Тело `POST /api/v1/users` должно быть JSON-объектом с заголовком `Content-Type: application/json`. Размер тела ограничен 1 МБ.

## Маршруты

### Создать пользователя

`POST /api/v1/users`

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/users `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"name":"Анна"}'
```

Ответ `201`:

```json
{
  "data": { "id": 1 },
  "error": null,
  "message": "Пользователь создан"
}
```

Нет заголовка JSON или тело не объект — `400`, код `invalid_body`. Имя не строка, пустое или слишком длинное — `400`, код `invalid_name`.

### Прочитать пользователя

`GET /api/v1/users/<id>`

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5000/api/v1/users/1
```

Ответ `200`:

```json
{
  "data": { "id": 1, "name": "Анна" },
  "error": null,
  "message": "Пользователь найден"
}
```

Нет записи — `404`, код `user_not_found`. Id меньше 1 — `400`, код `invalid_user_id`. Если сегмент пути не целое число, ответ `404` с кодом `not_found`.

### Отметить активным

`POST /api/v1/users/<id>/active`

Пользователь должен уже существовать. В памяти остаётся не больше 5 последних id. Шестая отметка вытесняет самую старую. Один id может повторяться.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/users/1/active
```

Ответ `200`:

```json
{
  "data": { "active_user_ids": [1] },
  "error": null,
  "message": "Пользователь отмечен активным"
}
```

### Список активных

`GET /api/v1/active-users`

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5000/api/v1/active-users
```

Ответ `200`:

```json
{
  "data": { "active_user_ids": [1] },
  "error": null,
  "message": "Список активных пользователей получен"
}
```

## Коды ошибок

| Код | Статус | Когда возникает |
| --- | --- | --- |
| `invalid_body` | 400 | Тело не JSON-объект, неверный тип содержимого или тело больше 1 МБ |
| `invalid_name` | 400 | Имя пустое, длиннее 200 символов или не строка |
| `invalid_user_id` | 400 | Id меньше 1 |
| `user_not_found` | 404 | Пользователь с таким id не найден |
| `not_found` | 404 | Маршрут не найден |
| `method_not_allowed` | 405 | Метод не поддерживается этим маршрутом |
| `database_error` | 500 | Сбой SQLite |
| `internal_error` | 500 | Непредвиденная ошибка сервера |

Клиенту не отдаются текст SQL и внутренние исключения. Техническая причина пишется в журнал процесса.

## Хранение

Таблица создаётся при старте:

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
```

Имя передаётся в запрос параметром. Запись в базу идёт через одно соединение. Список активных id защищён блокировкой.

При `go run .` из каталога `go-server` база — локальный `test.db`. Он не общий с `test.db` Python-сервера, если тот запущен из корня проекта. В Docker база — `/data/test.db` в томе `users-data`. Свой путь задаёт переменная `API_DB_PATH`.
