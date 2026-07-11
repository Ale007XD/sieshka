"""tests/integration/conftest.py — shared fixtures for integration tests."""
from __future__ import annotations

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings


def _is_docker_available() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _is_docker_available(),
        reason="Docker required for integration tests",
    ),
]


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """
    Использует уже работающий sieshka-postgres-1 вместо testcontainers'
    PostgresContainer (зависает на initdb в Docker Desktop/WSL2 при
    создании нового тома — подтверждено ручным репро).

    Применяет alembic-миграции на свежей тестовой БД.

    ВАЖНО: migrations/env.py::run_migrations_online() игнорирует
    config.get_main_option("sqlalchemy.url") и alembic.ini целиком —
    подключается напрямую через create_async_engine(settings.DATABASE_URL).
    Единственный рабочий способ направить миграции на тестовую БД —
    временно подменить settings.DATABASE_URL перед command.upgrade().
    """
    test_db_name = "sieshka_test_db"
    admin_dsn = (
        f"host=127.0.0.1 port=5432 "
        f"user={settings.POSTGRES_USER} password={settings.POSTGRES_PASSWORD} "
        f"dbname=postgres"
    )

    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        cur.execute(f"CREATE DATABASE {test_db_name}")

    test_dsn = admin_dsn.replace("dbname=postgres", f"dbname={test_db_name}")

    test_async_url = (
        f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@127.0.0.1:5432/{test_db_name}"
    )
    original_database_url = settings.DATABASE_URL
    settings.DATABASE_URL = test_async_url
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    finally:
        settings.DATABASE_URL = original_database_url

    yield test_dsn

    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{test_db_name}' AND pid <> pg_backend_pid();"
        )
        cur.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
