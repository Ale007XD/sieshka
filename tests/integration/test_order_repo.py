"""tests/integration/test_order_repo.py — OrderRepository integration tests.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.orders.models import OrderState
from app.repositories.order_repo import OrderRepository

pytestmark = pytest.mark.integration


def _is_docker_available() -> bool:
    try:
        import subprocess
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _is_docker_available(),
        reason="Docker required for testcontainers",
    ),
]


@pytest.fixture
def postgres_dsn() -> str:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        dsn = pg.get_connection_url().replace("psycopg2", "asyncpg")
        yield dsn


@pytest.fixture
async def repo(postgres_dsn: str) -> OrderRepository:
    engine = create_async_engine(postgres_dsn)
    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    schema = schema_path.read_text()

    async with engine.begin() as conn:
        await conn.execute(text(schema))

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield OrderRepository(session)

    await engine.dispose()


class TestOrderRepository:
    async def test_get_state_returns_draft(self, repo: OrderRepository) -> None:
        order_id = uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid4(),
                "state": OrderState.DRAFT.value,
                "items": "[]",
                "addr": "Test Address",
            },
        )
        await repo._session.commit()

        state = await repo.get_state(str(order_id))
        assert state == OrderState.DRAFT

    async def test_write_state_updates_state(self, repo: OrderRepository) -> None:
        order_id = uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid4(),
                "state": OrderState.DRAFT.value,
                "items": "[]",
                "addr": "Test Address",
            },
        )
        await repo._session.commit()

        await repo.write_state(str(order_id), OrderState.CONFIRMED)

        result = await repo._session.execute(
            text("SELECT state FROM orders WHERE id = :id"),
            {"id": order_id},
        )
        row = result.scalar_one()
        assert row == OrderState.CONFIRMED.value

    async def test_get_state_after_write(self, repo: OrderRepository) -> None:
        order_id = uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid4(),
                "state": OrderState.CONFIRMED.value,
                "items": "[]",
                "addr": "Test Address",
            },
        )
        await repo._session.commit()

        await repo.write_state(str(order_id), OrderState.PAYMENT_PENDING)
        state = await repo.get_state(str(order_id))
        assert state == OrderState.PAYMENT_PENDING
