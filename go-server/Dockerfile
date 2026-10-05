# Сборка API пользователей. Контекст — каталог go-server.
FROM golang:1.27-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/users-api .

FROM alpine:3.22

RUN addgroup -S app && adduser -S -G app -u 65532 app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=build /out/users-api /usr/local/bin/users-api

USER app
WORKDIR /data

# Слушаем все интерфейсы контейнера. База лежит в томе /data.
ENV API_LISTEN_ADDR=0.0.0.0:5000
ENV API_DB_PATH=/data/test.db

EXPOSE 5000
ENTRYPOINT ["/usr/local/bin/users-api"]
