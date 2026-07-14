#!/usr/bin/env bash
# Sieshka production deploy. Run from the PROJECT ROOT: ./deploy/deploy.sh
#
# Preconditions (see deploy/README.md):
#   - repo checked out on a CI-GREEN commit
#   - .env present at project root, chmod 600, NEVER committed
#   - DNS A record for the domain -> VPS IP; ports 80/443 open
#   - fresh postgres volume so initdb.d applies 001-004.sql (or run alembic)
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"

echo "==> Build images..."
$COMPOSE build

echo "==> Start stack (caddy + app + postgres)..."
$COMPOSE up -d

echo "==> Wait for postgres readiness..."
$COMPOSE exec -T postgres sh -c 'until pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 1; done'

echo "==> Schema check (expect tables from 001-004 migrations):"
$COMPOSE exec -T postgres psql -U sieshka -d sieshka -c '\dt'

echo "==> App startup logs — validate_all_programs() must appear WITHOUT traceback:"
$COMPOSE logs --tail=100 app

echo "==> Health (via Caddy + TLS):"
if curl -fsS https://new.siesh-ka.ru/health; then echo "HEALTH OK"; else echo "HEALTH FAILED"; exit 1; fi

echo "==> Deploy complete."
echo "    Smoke test : GET /docs, GET /menu, POST /orders, POST /orders/{id}/pay"
echo "    SSL check  : curl -Iv https://new.siesh-ka.ru"
echo "    Certs      : docker compose exec caddy caddy list-certificates"
