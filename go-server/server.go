package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"

	_ "modernc.org/sqlite"
)

const (
	maxNameLength  = 200
	maxActiveUsers = 5
	maxBodyBytes   = 1 << 20
	dbBusyTimeout  = 5000
)

var userIDPattern = regexp.MustCompile(`^-?[0-9]+$`)

// server хранит соединение с SQLite и список активных id в памяти процесса.
type server struct {
	db     *sql.DB
	active activeUsers
}

type activeUsers struct {
	mu  sync.Mutex
	ids []int64
}

type apiError struct {
	Code    string
	Message string
	Status  int
	Details map[string]any
	cause   error
}

type errorBody struct {
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Details map[string]any `json:"details"`
}

type envelope struct {
	Data    any        `json:"data"`
	Error   *errorBody `json:"error"`
	Message string     `json:"message"`
}

type createdUser struct {
	ID int64 `json:"id"`
}

type storedUser struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type activeList struct {
	ActiveUserIDs []int64 `json:"active_user_ids"`
}

// statusWriter запоминает, что ответ уже начат, чтобы не писать его второй раз.
type statusWriter struct {
	http.ResponseWriter
	wrote bool
}

func (writer *statusWriter) WriteHeader(status int) {
	writer.wrote = true
	writer.ResponseWriter.WriteHeader(status)
}

func (writer *statusWriter) Write(payload []byte) (int, error) {
	writer.wrote = true
	return writer.ResponseWriter.Write(payload)
}

func newServer(dbPath string) (*server, error) {
	database, err := sql.Open("sqlite", sqliteDSN(dbPath))
	if err != nil {
		return nil, err
	}
	// Одно соединение сериализует запись и убирает гонку за файлом SQLite.
	database.SetMaxOpenConns(1)
	if _, err := database.Exec(schemaSQL); err != nil {
		_ = database.Close()
		return nil, err
	}
	return &server{db: database}, nil
}

func (service *server) close() {
	if service.db != nil {
		_ = service.db.Close()
	}
}

const schemaSQL = `
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
`

func sqliteDSN(dbPath string) string {
	absolute, err := filepath.Abs(dbPath)
	if err != nil {
		absolute = dbPath
	}
	// Путь в file-URL начинается с корня, иначе Windows-диск теряется.
	location := &url.URL{Scheme: "file", Path: "/" + filepath.ToSlash(absolute)}
	query := location.Query()
	query.Set("_pragma", "busy_timeout("+strconv.Itoa(dbBusyTimeout)+")")
	location.RawQuery = query.Encode()
	return location.String()
}

func (service *server) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	writer := &statusWriter{ResponseWriter: response}
	defer func() {
		if recovered := recover(); recovered != nil {
			log.Printf(
				`{"level":"error","message":"Необработанная ошибка API","context":{"reason":"%T"}}`,
				recovered,
			)
			if !writer.wrote {
				internalError().write(writer)
			}
		}
	}()
	service.route(writer, request)
}

func (service *server) route(response http.ResponseWriter, request *http.Request) {
	switch request.URL.Path {
	case "/api/v1/users":
		if request.Method != http.MethodPost {
			methodNotAllowed().write(response)
			return
		}
		service.addUser(response, request)
	case "/api/v1/active-users":
		if request.Method != http.MethodGet {
			methodNotAllowed().write(response)
			return
		}
		service.listActiveUsers(response)
	default:
		service.routeUser(response, request)
	}
}

func (service *server) routeUser(response http.ResponseWriter, request *http.Request) {
	const prefix = "/api/v1/users/"
	if !strings.HasPrefix(request.URL.Path, prefix) {
		notFound().write(response)
		return
	}
	rest := strings.TrimPrefix(request.URL.Path, prefix)
	if idRaw, found := strings.CutSuffix(rest, "/active"); found && !strings.Contains(idRaw, "/") {
		service.markActive(response, request, idRaw)
		return
	}
	if strings.Contains(rest, "/") {
		notFound().write(response)
		return
	}
	service.getUser(response, request, rest)
}

func (service *server) addUser(response http.ResponseWriter, request *http.Request) {
	payload, bodyErr := readJSONObject(request)
	if bodyErr != nil {
		bodyErr.write(response)
		return
	}
	rawName, present := payload["name"]
	name, nameErr := requireName(rawName, present)
	if nameErr != nil {
		nameErr.write(response)
		return
	}
	userID, insertErr := service.insertUser(name)
	if insertErr != nil {
		insertErr.write(response)
		return
	}
	writeOK(response, http.StatusCreated, createdUser{ID: userID}, "Пользователь создан")
}

func (service *server) getUser(
	response http.ResponseWriter,
	request *http.Request,
	rawID string,
) {
	userID, ok := parseUserID(rawID)
	if !ok {
		notFound().write(response)
		return
	}
	if request.Method != http.MethodGet {
		methodNotAllowed().write(response)
		return
	}
	if userID < 1 {
		invalidUserID().write(response)
		return
	}
	user, findErr := service.selectUser(userID)
	if findErr != nil {
		findErr.write(response)
		return
	}
	if user == nil {
		userNotFound(userID).write(response)
		return
	}
	writeOK(response, http.StatusOK, user, "Пользователь найден")
}

func (service *server) markActive(
	response http.ResponseWriter,
	request *http.Request,
	rawID string,
) {
	userID, ok := parseUserID(rawID)
	if !ok {
		notFound().write(response)
		return
	}
	if request.Method != http.MethodPost {
		methodNotAllowed().write(response)
		return
	}
	if userID < 1 {
		invalidUserID().write(response)
		return
	}
	user, findErr := service.selectUser(userID)
	if findErr != nil {
		findErr.write(response)
		return
	}
	if user == nil {
		userNotFound(userID).write(response)
		return
	}
	writeOK(response, http.StatusOK, activeList{
		ActiveUserIDs: service.active.add(userID),
	}, "Пользователь отмечен активным")
}

func (service *server) listActiveUsers(response http.ResponseWriter) {
	writeOK(response, http.StatusOK, activeList{
		ActiveUserIDs: service.active.list(),
	}, "Список активных пользователей получен")
}

func (users *activeUsers) add(userID int64) []int64 {
	users.mu.Lock()
	defer users.mu.Unlock()
	users.ids = append(users.ids, userID)
	if len(users.ids) > maxActiveUsers {
		users.ids = users.ids[len(users.ids)-maxActiveUsers:]
	}
	return copyIDs(users.ids)
}

func (users *activeUsers) list() []int64 {
	users.mu.Lock()
	defer users.mu.Unlock()
	return copyIDs(users.ids)
}

func copyIDs(ids []int64) []int64 {
	copied := make([]int64, len(ids))
	copy(copied, ids)
	return copied
}

func (service *server) insertUser(name string) (int64, *apiError) {
	result, err := service.db.Exec(`INSERT INTO users (name) VALUES (?)`, name)
	if err != nil {
		return 0, databaseError(err)
	}
	userID, err := result.LastInsertId()
	if err != nil || userID < 1 {
		return 0, databaseError(err)
	}
	return userID, nil
}

func (service *server) selectUser(userID int64) (*storedUser, *apiError) {
	var user storedUser
	err := service.db.QueryRow(
		`SELECT id, name FROM users WHERE id = ?`,
		userID,
	).Scan(&user.ID, &user.Name)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, databaseError(err)
	}
	return &user, nil
}

func readJSONObject(request *http.Request) (map[string]any, *apiError) {
	mediaType := strings.ToLower(request.Header.Get("Content-Type"))
	if !strings.Contains(mediaType, "application/json") {
		return nil, invalidBody()
	}
	limited := io.LimitReader(request.Body, maxBodyBytes+1)
	raw, err := io.ReadAll(limited)
	if err != nil || len(raw) > maxBodyBytes {
		return nil, invalidBody()
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var payload any
	if err := decoder.Decode(&payload); err != nil || decoder.More() {
		return nil, invalidBody()
	}
	object, ok := payload.(map[string]any)
	if !ok {
		return nil, invalidBody()
	}
	return object, nil
}

func requireName(value any, present bool) (string, *apiError) {
	if !present {
		value = ""
	}
	text, ok := value.(string)
	if !ok {
		return "", &apiError{
			Code:    "invalid_name",
			Message: "Имя должно быть строкой",
			Status:  http.StatusBadRequest,
			Details: map[string]any{"received_type": pythonTypeName(value)},
		}
	}
	normalized := strings.TrimFunc(text, unicode.IsSpace)
	if normalized == "" || utf8.RuneCountInString(normalized) > maxNameLength {
		return "", &apiError{
			Code:    "invalid_name",
			Message: "Имя должно быть непустым и не длиннее допустимого",
			Status:  http.StatusBadRequest,
			Details: map[string]any{"max_length": maxNameLength},
		}
	}
	return normalized, nil
}

func pythonTypeName(value any) string {
	switch typed := value.(type) {
	case string:
		return "str"
	case bool:
		return "bool"
	case nil:
		return "NoneType"
	case json.Number:
		if _, err := typed.Int64(); err == nil && !strings.ContainsAny(typed.String(), ".eE") {
			return "int"
		}
		return "float"
	case []any:
		return "list"
	case map[string]any:
		return "dict"
	default:
		return "unknown"
	}
}

func parseUserID(raw string) (int64, bool) {
	if !userIDPattern.MatchString(raw) {
		return 0, false
	}
	userID, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, false
	}
	return userID, true
}

func (failure *apiError) write(response http.ResponseWriter) {
	if failure.cause != nil {
		log.Printf(
			`{"level":"error","message":"%s","context":{"code":"%s","reason":%q}}`,
			failure.Message,
			failure.Code,
			failure.cause.Error(),
		)
	}
	details := failure.Details
	if details == nil {
		details = map[string]any{}
	}
	writeJSON(response, failure.Status, envelope{
		Data: nil,
		Error: &errorBody{
			Code:    failure.Code,
			Message: failure.Message,
			Details: details,
		},
		Message: failure.Message,
	})
}

func writeOK(response http.ResponseWriter, status int, data any, message string) {
	writeJSON(response, status, envelope{
		Data:    data,
		Error:   nil,
		Message: message,
	})
}

func writeJSON(response http.ResponseWriter, status int, payload envelope) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(payload); err != nil {
		log.Printf(
			`{"level":"error","message":"Необработанная ошибка API","context":{"reason":"json"}}`,
		)
		http.Error(response, "", http.StatusInternalServerError)
		return
	}
	response.Header().Set("Content-Type", "application/json; charset=utf-8")
	response.WriteHeader(status)
	_, _ = response.Write(buffer.Bytes())
}

func invalidBody() *apiError {
	return &apiError{
		Code:    "invalid_body",
		Message: "Тело запроса должно быть JSON-объектом",
		Status:  http.StatusBadRequest,
	}
}

func invalidUserID() *apiError {
	return &apiError{
		Code:    "invalid_user_id",
		Message: "Идентификатор пользователя должен быть положительным целым",
		Status:  http.StatusBadRequest,
	}
}

func userNotFound(userID int64) *apiError {
	return &apiError{
		Code:    "user_not_found",
		Message: "Пользователь не найден",
		Status:  http.StatusNotFound,
		Details: map[string]any{"user_id": userID},
	}
}

func notFound() *apiError {
	return &apiError{
		Code:    "not_found",
		Message: "Маршрут не найден",
		Status:  http.StatusNotFound,
	}
}

func methodNotAllowed() *apiError {
	return &apiError{
		Code:    "method_not_allowed",
		Message: "Метод не поддерживается",
		Status:  http.StatusMethodNotAllowed,
	}
}

func databaseError(cause error) *apiError {
	message := "Не удалось выполнить операцию с базой данных"
	if cause == nil {
		message = "База не вернула идентификатор пользователя"
		cause = errMissingID
	}
	return &apiError{
		Code:    "database_error",
		Message: message,
		Status:  http.StatusInternalServerError,
		cause:   cause,
	}
}

func internalError() *apiError {
	return &apiError{
		Code:    "internal_error",
		Message: "Внутренняя ошибка сервера",
		Status:  http.StatusInternalServerError,
	}
}

var errMissingID = errString("база не вернула идентификатор")

type errString string

func (value errString) Error() string {
	return string(value)
}
