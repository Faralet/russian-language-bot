# Деплой Mini App (когда появится домен)

Готовый план. Выполняется на VPS в `/opt/bot` через консоль TimeWeb. Большинство шагов я делаю сам, от тебя нужен только домен + DNS.

## 0. Предусловия (твоя часть)
- Домен или поддомен под Mini App, например `app.example.ru`.
- A-запись этого домена на IP `85.239.38.64` (в панели TimeWeb, если домен там же).
- На VPS открыты порты 80 и 443 (нужно Caddy для выпуска SSL).

## 1. Обновить код на сервере
```bash
cd /opt/bot
cp -r /opt/bot /opt/bot_backup_$(date +%Y%m%d_%H%M)   # бэкап
git pull --ff-only                                     # подтянуть новый код (miniapp/, app/webapp/, Caddyfile, docker-compose.miniapp.yml)
```
(Если деплой не через git — распаковать свежий архив, как делали раньше.)

## 2. Добавить переменные в .env
```bash
# заменить app.example.ru на реальный домен
printf '\nMINIAPP_URL=https://app.example.ru\nMINIAPP_DOMAIN=app.example.ru\n' >> .env
```
Проверить, что не задвоилось: `grep MINIAPP .env`

## 3. Поднять API + Caddy (и пересобрать бота с кнопкой Mini App)
```bash
docker-compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.miniapp.yml up -d --build
```
- Поднимутся 4 сервиса: `bot`, `db`, `api` (uvicorn на 127.0.0.1:8081), `caddy` (80/443, авто-HTTPS).
- Бот при старте сам выставит кнопку «Открыть приложение» (читает `MINIAPP_URL`).

## 4. Проверка
```bash
docker-compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.miniapp.yml ps
docker-compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.miniapp.yml logs --tail=40 caddy   # выпуск сертификата
curl -s https://app.example.ru/api/health                                                                              # ожидаем {"ok":true}
```
В Telegram: открыть бота → кнопка-меню «Открыть приложение» (слева от поля ввода) → должен открыться Mini App с реальными данными.

## 5. Если что-то не так
- Caddy не выпускает сертификат → проверить, что DNS уже указывает на `85.239.38.64` (может занять до 24 ч) и порты 80/443 открыты.
- 401 в API → проверить, что бот и API используют один и тот же `BOT_TOKEN` (initData валидируется по нему).
- Откат: остановить новые сервисы `docker-compose ... -f docker-compose.miniapp.yml down`, бот и БД продолжат работать.

## Архитектура
- Фронтенд (статика `miniapp/index.html`) и API (`app/webapp/`) — на одном домене через Caddy (`/api/*` → uvicorn, остальное → статика).
- Авторизация: Telegram initData (HMAC по токену бота) — `app/webapp/auth.py`.
- Данные: та же PostgreSQL и логика бота (банк 876, прогноз балла, уровни).
- Бот и Mini App используют одну БД, прогресс общий.
