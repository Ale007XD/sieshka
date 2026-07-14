#!/usr/bin/env bash
# Sieshka production backup. Run from PROJECT ROOT: ./deploy/backup.sh
# Dumps the postgres DB to backups/sieshka_<timestamp>.sql
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"
mkdir -p backups
OUT="backups/sieshka_$(date +%Y%m%d_%H%M%S).sql"

$COMPOSE exec -T postgres pg_dump -U sieshka sieshka > "$OUT"
echo "Backed up to $OUT ($(wc -l < "$OUT") lines)"
echo "Verify before trusting: ./deploy/restore.sh $OUT"
