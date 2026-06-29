"""tests/integration/test_delivery_flow.py — DeliveryRepository + DeliveryService integration.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.delivery.fsm import DeliveryEvent, DeliveryFSM, DeliveryState
from app.repositories.delivery_repo import DeliveryRepository
from app.services.delivery_service import DeliveryService

UUID = uuid.UUID


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


@pytest.fixture
def postgres_dsn() -> Generator[str, None, None]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        dsn = pg.get_connection_url().replace("psycopg2", "asyncpg")
        yield dsn


@pytest.fixture
async def repo(postgres_dsn: str) -> AsyncGenerator[DeliveryRepository, None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    schema = schema_path.read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
    finally:
        await conn.close()

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield DeliveryRepository(session)

    await engine.dispose()


@pytest.fixture
async def service(
    postgres_dsn: str,
) -> AsyncGenerator[DeliveryService, None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    schema = schema_path.read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield DeliveryService(session_factory=factory)

    await engine.dispose()


def _seed_order(repo: DeliveryRepository, order_id: uuid.UUID | None = None) -> uuid.UUID:
    """Helper to insert an order row."""
    if order_id is None:
        order_id = uuid.uuid4()
    return order_id


class TestDeliveryRepository:
    async def test_get_state_returns_unassigned(self, repo: DeliveryRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid.uuid4(),
                "state": "DELIVERING",
                "items": "[]",
                "addr": "A",
            },
        )
        await repo._session.commit()

        task_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO delivery_tasks (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": task_id, "oid": order_id, "state": DeliveryState.UNASSIGNED.value},
        )
        await repo._session.commit()

        state = await repo.get_state(str(task_id))
        assert state == DeliveryState.UNASSIGNED

    async def test_write_state_updates_state(self, repo: DeliveryRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid.uuid4(),
                "state": "DELIVERING",
                "items": "[]",
                "addr": "A",
            },
        )
        await repo._session.commit()

        task_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO delivery_tasks (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": task_id, "oid": order_id, "state": DeliveryState.UNASSIGNED.value},
        )
        await repo._session.commit()

        await repo.write_state(str(task_id), DeliveryState.ASSIGNED)

        result = await repo._session.execute(
            text("SELECT state FROM delivery_tasks WHERE id = :id"),
            {"id": task_id},
        )
        row = result.scalar_one()
        assert row == DeliveryState.ASSIGNED.value

    async def test_create_inserts_task(self, repo: DeliveryRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid.uuid4(),
                "state": "DELIVERING",
                "items": "[]",
                "addr": "A",
            },
        )
        await repo._session.commit()

        task_id = await repo.create(str(order_id))
        await repo._session.commit()

        result = await repo._session.execute(
            text("SELECT state, order_id FROM delivery_tasks WHERE id = :id"),
            {"id": UUID(task_id)},
        )
        row = result.one()
        assert row._mapping["state"] == DeliveryState.UNASSIGNED.value
        assert row._mapping["order_id"] == order_id

    async def test_fsm_wiring_updates_db_through_repo(self, repo: DeliveryRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {
                "id": order_id,
                "cid": uuid.uuid4(),
                "state": "DELIVERING",
                "items": "[]",
                "addr": "A",
            },
        )
        await repo._session.commit()

        task_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO delivery_tasks (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": task_id, "oid": order_id, "state": DeliveryState.UNASSIGNED.value},
        )
        await repo._session.commit()

        fsm = DeliveryFSM(
            state_reader=repo.get_state,
            state_writer=repo.write_state,
        )
        await fsm.handle_event(str(task_id), DeliveryEvent.ASSIGN)
        await repo._session.commit()

        assert await repo.get_state(str(task_id)) == DeliveryState.ASSIGNED


class TestDeliveryService:
    async def test_create_task(self, service: DeliveryService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": "DELIVERING",
                    "items": "[]",
                    "addr": "A",
                },
            )
            await session.commit()

        task = await service.create_task(str(order_id))
        assert task.state == DeliveryState.UNASSIGNED
        assert task.order_id == order_id

    async def test_transition_task(self, service: DeliveryService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": "DELIVERING",
                    "items": "[]",
                    "addr": "A",
                },
            )
            await session.commit()

        task = await service.create_task(str(order_id))

        result = await service.transition_task(str(task.id), DeliveryEvent.ASSIGN)
        assert result.success is True
        assert result.new_state == DeliveryState.ASSIGNED

    async def test_list_tasks(self, service: DeliveryService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": "DELIVERING",
                    "items": "[]",
                    "addr": "A",
                },
            )
            await session.commit()

        await service.create_task(str(order_id))
        tasks = await service.list_tasks()
        assert len(tasks) >= 1
        assert any(t.order_id == order_id for t in tasks)

    async def test_invalid_event_rejected(self, service: DeliveryService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": "DELIVERING",
                    "items": "[]",
                    "addr": "A",
                },
            )
            await session.commit()

        task = await service.create_task(str(order_id))

        result = await service.transition_task(str(task.id), DeliveryEvent.COMPLETE)
        assert result.success is False
        assert result.new_state is None

    async def test_list_tasks_with_filter(self, service: DeliveryService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": "DELIVERING",
                    "items": "[]",
                    "addr": "A",
                },
            )
            await session.commit()

        task = await service.create_task(str(order_id))

        unassigned = await service.list_tasks(state_filter=DeliveryState.UNASSIGNED)
        assert any(t.id == task.id for t in unassigned)

        assigned = await service.list_tasks(state_filter=DeliveryState.ASSIGNED)
        assert not any(t.id == task.id for t in assigned)
