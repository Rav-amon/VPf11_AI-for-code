package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

type responseBody struct {
	Data    map[string]any `json:"data"`
	Error   *errorBody     `json:"error"`
	Message string         `json:"message"`
}

func newTestServer(t *testing.T) *server {
	t.Helper()
	service, err := newServer(filepath.Join(t.TempDir(), "api.db"))
	if err != nil {
		t.Fatalf("не удалось открыть базу: %v", err)
	}
	t.Cleanup(service.close)
	return service
}

func perform(
	service *server,
	method string,
	path string,
	body string,
	contentType string,
) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	if contentType != "" {
		request.Header.Set("Content-Type", contentType)
	}
	recorder := httptest.NewRecorder()
	service.ServeHTTP(recorder, request)
	return recorder
}

func decode(t *testing.T, recorder *httptest.ResponseRecorder) responseBody {
	t.Helper()
	var payload responseBody
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("ответ не JSON: %s", recorder.Body.String())
	}
	return payload
}

func TestCreateAndReadUser(t *testing.T) {
	service := newTestServer(t)
	created := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"O'Brien"}`,
		"application/json",
	)
	if created.Code != http.StatusCreated {
		t.Fatalf("статус создания: %d", created.Code)
	}
	createdBody := decode(t, created)
	if createdBody.Error != nil || createdBody.Message != "Пользователь создан" {
		t.Fatalf("конверт создания: %+v", createdBody)
	}
	userID := int64(createdBody.Data["id"].(float64))

	loaded := perform(service, http.MethodGet, "/api/v1/users/"+itoa(userID), "", "")
	if loaded.Code != http.StatusOK {
		t.Fatalf("статус чтения: %d", loaded.Code)
	}
	loadedBody := decode(t, loaded)
	if loadedBody.Data["name"] != "O'Brien" || loadedBody.Data["id"] != float64(userID) {
		t.Fatalf("данные пользователя: %+v", loadedBody.Data)
	}
}

func TestNameIsStoredLiterally(t *testing.T) {
	service := newTestServer(t)
	name := "x' OR 1=1 --"
	created := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"x' OR 1=1 --"}`,
		"application/json",
	)
	userID := int64(decode(t, created).Data["id"].(float64))
	loaded := decode(t, perform(
		service,
		http.MethodGet,
		"/api/v1/users/"+itoa(userID),
		"",
		"",
	))
	if loaded.Data["name"] != name {
		t.Fatalf("имя изменилось: %#v", loaded.Data["name"])
	}
}

func TestValidationAndMissingUser(t *testing.T) {
	service := newTestServer(t)

	blank := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"   "}`,
		"application/json",
	)
	if blank.Code != http.StatusBadRequest || decode(t, blank).Error.Code != "invalid_name" {
		t.Fatalf("пустое имя: %d %s", blank.Code, blank.Body.String())
	}

	broken := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		"not-json",
		"text/plain",
	)
	if broken.Code != http.StatusBadRequest || decode(t, broken).Error.Code != "invalid_body" {
		t.Fatalf("плохое тело: %d %s", broken.Code, broken.Body.String())
	}

	missing := perform(service, http.MethodGet, "/api/v1/users/999999", "", "")
	missingBody := decode(t, missing)
	if missing.Code != http.StatusNotFound || missingBody.Error.Code != "user_not_found" {
		t.Fatalf("нет пользователя: %d %+v", missing.Code, missingBody.Error)
	}

	injected := perform(service, http.MethodGet, "/api/v1/users/1%20OR%201=1", "", "")
	if injected.Code != http.StatusNotFound || decode(t, injected).Error.Code != "not_found" {
		t.Fatalf("подставленный id: %d %s", injected.Code, injected.Body.String())
	}

	negative := perform(service, http.MethodGet, "/api/v1/users/-1", "", "")
	if negative.Code != http.StatusBadRequest || decode(t, negative).Error.Code != "invalid_user_id" {
		t.Fatalf("отрицательный id: %d %s", negative.Code, negative.Body.String())
	}
}

func TestActiveUsersAreCapped(t *testing.T) {
	service := newTestServer(t)
	created := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"Анна"}`,
		"application/json",
	)
	userID := int64(decode(t, created).Data["id"].(float64))
	path := "/api/v1/users/" + itoa(userID) + "/active"

	var group sync.WaitGroup
	for range maxActiveUsers + 3 {
		group.Add(1)
		go func() {
			defer group.Done()
			recorder := perform(service, http.MethodPost, path, "", "")
			if recorder.Code != http.StatusOK {
				t.Errorf("отметка: %d", recorder.Code)
			}
		}()
	}
	group.Wait()

	listed := decode(t, perform(service, http.MethodGet, "/api/v1/active-users", "", ""))
	rawIDs, ok := listed.Data["active_user_ids"].([]any)
	if !ok || len(rawIDs) != maxActiveUsers {
		t.Fatalf("список активных: %#v", listed.Data["active_user_ids"])
	}
	for _, rawID := range rawIDs {
		if int64(rawID.(float64)) != userID {
			t.Fatalf("в списке чужой id: %#v", rawIDs)
		}
	}

	ghost := perform(service, http.MethodPost, "/api/v1/users/999999/active", "", "")
	if ghost.Code != http.StatusNotFound {
		t.Fatalf("чужая отметка: %d", ghost.Code)
	}
}

func TestWrongMethodAndUnknownRoute(t *testing.T) {
	service := newTestServer(t)
	method := perform(service, http.MethodGet, "/api/v1/users", "", "")
	if method.Code != http.StatusMethodNotAllowed {
		t.Fatalf("метод: %d", method.Code)
	}
	if decode(t, method).Error.Code != "method_not_allowed" {
		t.Fatalf("код метода: %s", method.Body.String())
	}
	unknown := perform(service, http.MethodGet, "/api/v1/missing", "", "")
	if unknown.Code != http.StatusNotFound || decode(t, unknown).Error.Code != "not_found" {
		t.Fatalf("маршрут: %d %s", unknown.Code, unknown.Body.String())
	}
}

func TestNameLengthUsesCharacters(t *testing.T) {
	service := newTestServer(t)
	accepted := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"`+strings.Repeat("я", maxNameLength)+`"}`,
		"application/json",
	)
	if accepted.Code != http.StatusCreated {
		t.Fatalf("граница длины: %d %s", accepted.Code, accepted.Body.String())
	}
	rejected := perform(
		service,
		http.MethodPost,
		"/api/v1/users",
		`{"name":"`+strings.Repeat("я", maxNameLength+1)+`"}`,
		"application/json",
	)
	if rejected.Code != http.StatusBadRequest {
		t.Fatalf("длинное имя принято: %d", rejected.Code)
	}
}

func itoa(value int64) string {
	return strconv.FormatInt(value, 10)
}
