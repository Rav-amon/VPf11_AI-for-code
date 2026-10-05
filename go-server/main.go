// Сервер пользователей: создание, чтение и короткий список активных.
package main

import (
	"errors"
	"log"
	"net/http"
	"os"
	"time"
)

const (
	defaultListenAddr = "127.0.0.1:5000"
	defaultDBPath     = "test.db"
	readHeaderTimeout = 5 * time.Second
	readTimeout       = 10 * time.Second
	writeTimeout      = 10 * time.Second
)

func main() {
	dbPath := os.Getenv("API_DB_PATH")
	if dbPath == "" {
		dbPath = defaultDBPath
	}
	// В контейнере адрес задаётся как 0.0.0.0:5000, иначе порт снаружи не виден.
	listenAddr := os.Getenv("API_LISTEN_ADDR")
	if listenAddr == "" {
		listenAddr = defaultListenAddr
	}

	service, err := newServer(dbPath)
	if err != nil {
		log.Fatalf("не удалось открыть базу: %v", err)
	}
	defer service.close()

	httpServer := &http.Server{
		Addr:              listenAddr,
		Handler:           service,
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		WriteTimeout:      writeTimeout,
	}
	log.Printf("сервер слушает http://%s", listenAddr)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("сервер остановлен: %v", err)
	}
}
