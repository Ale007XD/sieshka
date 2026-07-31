#!/bin/sh
# Runs on every container start, before the app process. Applies the alembic
# migration chain against whatever Postgres DATABASE_URL currently points at.
#
# Root cause this closes (2026-07-30, sieshka-new-postgres-1 staging incident):
# docker-entrypoint-initdb.d only runs on a brand-new/empty postgres volume —
# on a long-lived volume (this one was 9 days old) it never runs again, so
# every migration added after the volume's first init (005..013 in this
# incident) silently never reached the real DB unless someone remembered to
# `docker compose exec app alembic upgrade head` by hand after every deploy.
# That manual step is documented (DEPLOYMENT.md §3.4 step 3) but is exactly
# the kind of step a human skips under deploy pressure — moving it here makes
# it unskippable instead of just "well-documented".
#
# Idempotent by construction: alembic checks alembic_version and no-ops if
# already at head; every migration's SQL body uses IF NOT EXISTS / ADD COLUMN
# IF NOT EXISTS (see migrations/*.sql), so re-running against an
# already-migrated DB on every restart is harmless, not just "usually fine".
#
# NON-GOAL: concurrent-replica safety. docker-compose.yml and
# deploy/docker-compose.prod.yml define no `replicas` for the app service —
# single instance only. If that ever changes, two containers racing this
# step on first boot need a lock (e.g. pg_advisory_lock wrapped around
# `alembic upgrade head`) — not implemented, not needed today.
set -e

echo "[entrypoint] alembic upgrade head..."
alembic upgrade head
echo "[entrypoint] migrations OK, starting app"

exec "$@"