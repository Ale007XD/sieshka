#!/usr/bin/env bash
# Sieshka backup restore VERIFICATION. Run from PROJECT ROOT: ./deploy/restore.sh <dump.sql>
# Restores into a throwaway DB to prove the backup is valid, then drops it.
# (Real prod restore requires stopping the app and loading into `sieshka`.)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then echo "usage: $0 <dump.sql>"; exit 1; fi
DUMP="$1"
COMPOSE="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"

DB="restore_test_$(date +%s)"
echo "==> Creating throwaway DB $DB and restoring $DUMP into it..."
$COMPOSE exec -T postgres createdb -U sieshka "$DB"
$COMPOSE exec -T postgres psql -U sieshka -d "$DB" < "$DUMP"

echo "==> Restore succeeded into $DB. Dropping test DB."
$COMPOSE exec -T postgres dropdb -U sieshka "$DB"
echo "Backup $DUMP is VALID. For a real restore: stop app, drop/recreate 'sieshka', load dump, start app."
