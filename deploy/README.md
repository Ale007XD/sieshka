# Sieshka — предбоевое развёртывание (pre-prod / staging)

Инфраструктура-как-код. Деплой превращается в:

```bash
git checkout <ci-green-sha>
cp deploy/.env.example.prod .env && chmod 600 .env   # заполнить значения
./deploy/deploy.sh
```

Базовый `docker-compose.yml` не трогаем — прод живёт в overlay
`deploy/docker-compose.prod.yml`. Локальная разработка остаётся на правилах
dev-файла.

---

## 0. Что есть в репо (и что учтено)

- `Dockerfile`: uvicorn на `:8000`, healthcheck на `/health`.
- `docker-compose.yml`: postgres + app; монтирует `./migrations` в
  `docker-entrypoint-initdb.d` (накатывает `001–004.sql` на **первом** старте
  пустой БД). App **сам миграции не гонит** (lifespan только
  `validate_all_programs()`).
- `SQLITE_PATH` по умолчанию `/app/data/...` → совпадает с томом `./data:/app/data`.
- `/admin/*` auth-gated (argon2), но **без rate-limit** — закрыто только на proxy.

## 1. VPS (Ubuntu 24.04)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin ufw curl fail2ban
sudo usermod -aG docker $USER   # перелогинься
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
sudo systemctl enable --now fail2ban   # минимум шума по SSH/брутфорсу
```

## 2. DNS

A-запись домена (напр. `new.siesh-ka.ru`) → публичный IP VPS. Порты 80/443 открыты.

## 3. Репо на CI-GREEN коммите

```bash
git clone <repo> ~/sieshka && cd ~/sieshka
git checkout <sha-ci-green>
cp deploy/.env.example.prod .env && chmod 600 .env   # НЕ коммитить .env
```

## 4. `.env` (критичное для прод-домена)

Обязательно переопредели слабый дефолт `postgres` (`sieshka/sieshka`):

```dotenv
POSTGRES_PASSWORD=<STRONG_PW>
DATABASE_URL=postgresql+asyncpg://sieshka:<STRONG_PW>@postgres:5432/sieshka
SQLITE_PATH=/app/data/sieshka_nano_vm.db
MENU_TIMEZONE=Europe/Moscow
YOOKASSA_RETURN_URL=https://new.siesh-ka.ru/payment/return
DASHBOARD_PASSWORD_HASH=<argon2 hash>   # не plaintext
DEBUG=false
LOG_LEVEL=INFO
```

LLM-ключи (OpenRouter первичный, Yandex/GigaChat fallback): без них агенты
поднимают `RuntimeError("all providers failed")`, но FSM/заказы работают.

## 5. Деплой

```bash
./deploy/deploy.sh
```

Скрипт: собирает образы, поднимает стек, ждёт readiness postgres,
**проверяет схему** (`\dt`), **показывает логи app** (ищем чистый
`validate_all_programs()` без traceback), и делает `curl https://<domain>/health`.

## 6. Предбоевые проверки (smoke, именно в этом порядке)

```bash
curl -fsS https://new.siesh-ka.ru/health
curl -fsS https://new.siesh-ka.ru/docs
curl -fsS https://new.siesh-ka.ru/menu
# POST /orders ; POST /orders/{id}/pay  (YooKassa в test-режиме)
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml \
  exec -T app python scripts/validate_all_programs.py   # 11 программ
```

SSL / сертификат:

```bash
curl -Iv https://new.siesh-ka.ru
docker compose exec caddy caddy list-certificates
```

## 7. Процедура обновления (надо уже сейчас)

```bash
git fetch
git checkout <new-ci-green-sha>
./deploy/deploy.sh          # build + up; caddy/old app заменяются
curl -fsS https://new.siesh-ka.ru/health
```

## 8. Резерв / восстановление

```bash
./deploy/backup.sh                       # dumps -> backups/sieshka_<ts>.sql
./deploy/restore.sh backups/sieshka_*.sql  # ВЕРИФИКАЦИЯ бэкапа (в тестовую БД)
```

Многие делают бэкапы, но не проверяют восстановление — `restore.sh` проверяет
каждый дамп в throwaway-БД и удаляет её. Реальное восстановление prod требует
остановки app и загрузки в `sieshka`.

## 9. Что включено в overlay (и почему)

- **Caddy** на 80/443 с авто-TLS (Let's Encrypt) + security-заголовки
  (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options DENY`).
- **app не публикует 8000 наружу** — только Caddy (`expose: ["8000"]`).
- **postgres не публикует 5432** наружу.
- **Лимиты ресурсов**: app `mem_limit 1g / cpus 1.0`, postgres `512m / 1.0`
  (runaway LLM не съест VPS).
- **Ротация логов Docker**: `json-file`, `max-size 10m`, `max-file 5`.

## 10. Известные риски (закрывать до реального трафика)

- **Нет rate-limit на `/admin`** — только basic-auth. Для устойчивого публичного
  трафика собери caddy с плагином `caddy-ratelimit` (раскомментируй блок в
  `deploy/Caddyfile`).
- **initdb.d — временное решение.** `001–004.sql` применяются только на первом
  старте пустой БД. **Не используй initdb.d на prod после появления первой
  Alembic-миграции** — он расходится со схемой alembic. Переведи на
  `alembic upgrade head` и уберите монтирование `initdb.d`.
- **YooKassa RETURN_URL** должен совпадать с доменом; для предбоевых — test-платежи.
- **LLM-ключи** — без них AI-слой молчит (см. п.4).

## 11. Файлы

```
deploy/
  docker-compose.prod.yml   # overlay (caddy, лимиты, ротация, нет публичных портов)
  Caddyfile                 # TLS + security headers (+ опц. rate-limit)
  .env.example.prod         # prod-шаблон (секреты через .env на хосте)
  deploy.sh                 # build + up + readiness + schema + logs + health
  backup.sh                 # pg_dump
  restore.sh                # верификация бэкапа в тестовую БД
```
