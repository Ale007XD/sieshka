"""tests/integration/conftest.py — shared fixtures for integration tests."""
from __future__ import annotations

from collections.abc import Generator

import pytest


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
        reason="Docker required for testcontainers",
    ),
]


@pytest.fixture(scope="session")
def postgres_dsn() -> Generator[str, None, None]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        dsn = pg.get_connection_url().replace("psycopg2", "asyncpg")
        yield dsn
