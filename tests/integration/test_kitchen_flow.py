"""tests/integration/test_kitchen_flow.py — KitchenRepository + KitchenService + cross-domain.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.kitchen.fsm import KitchenEvent, KitchenFSM, KitchenState
from app.domains.orders.fsm import OrderFSM
from app.domains.orders.models import OrderEvent, OrderState
from app.repositories.kitchen_repo import KitchenRepository
from app.repositories.order_repo import OrderRepository
from app.services.kitchen_service import KitchenService

UUID = uuid.UUID


@pytest.fixture
async def repo(postgres_dsn: str) -> AsyncGenerator[KitchenRepository, None]:
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
        yield KitchenRepository(session)

    await engine.dispose()


@pytest.fixture
async def service(
    postgres_dsn: str,
) -> AsyncGenerator[KitchenService, None]:
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
    yield KitchenService(session_factory=factory)

    await engine.dispose()


@pytest.fixture
async def seeded_order(repo: KitchenRepository) -> str:
    """Insert an order and return its id."""
    order_id = uuid.uuid4()
    await repo._session.execute(
        text(
            "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
            "VALUES (:id, :cid, :state, :items, :addr)"
        ),
        {
            "id": order_id,
            "cid": uuid.uuid4(),
            "state": OrderState.PAID.value,
            "items": "[]",
            "addr": "Test",
        },
    )
    await repo._session.commit()
    return str(order_id)


class TestKitchenRepository:
    async def test_get_state_returns_new(self, repo: KitchenRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "Addr"},
        )
        await repo._session.commit()

        ticket_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO kitchen_tickets (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": ticket_id, "oid": order_id, "state": KitchenState.NEW.value},
        )
        await repo._session.commit()

        state = await repo.get_state(str(ticket_id))
        assert state == KitchenState.NEW

    async def test_write_state_updates_state(self, repo: KitchenRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "Addr"},
        )
        await repo._session.commit()

        ticket_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO kitchen_tickets (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": ticket_id, "oid": order_id, "state": KitchenState.NEW.value},
        )
        await repo._session.commit()

        await repo.write_state(str(ticket_id), KitchenState.QUEUED)

        result = await repo._session.execute(
            text("SELECT state FROM kitchen_tickets WHERE id = :id"),
            {"id": ticket_id},
        )
        row = result.scalar_one()
        assert row == KitchenState.QUEUED.value

    async def test_create_inserts_ticket(self, repo: KitchenRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "Addr"},
        )
        await repo._session.commit()

        ticket_id = await repo.create(str(order_id))
        await repo._session.commit()

        result = await repo._session.execute(
            text("SELECT state, order_id FROM kitchen_tickets WHERE id = :id"),
            {"id": UUID(ticket_id)},
        )
        row = result.one()
        assert row._mapping["state"] == KitchenState.NEW.value
        assert row._mapping["order_id"] == order_id

    async def test_fsm_wiring_updates_db_through_repo(self, repo: KitchenRepository) -> None:
        order_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :cid, :state, :items, :addr)"
            ),
            {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "Addr"},
        )
        await repo._session.commit()

        ticket_id = uuid.uuid4()
        await repo._session.execute(
            text(
                "INSERT INTO kitchen_tickets (id, order_id, state) "
                "VALUES (:id, :oid, :state)"
            ),
            {"id": ticket_id, "oid": order_id, "state": KitchenState.NEW.value},
        )
        await repo._session.commit()

        fsm = KitchenFSM(
            state_reader=repo.get_state,
            state_writer=repo.write_state,
        )
        await fsm.handle_event(str(ticket_id), KitchenEvent.QUEUE)
        await repo._session.commit()

        assert await repo.get_state(str(ticket_id)) == KitchenState.QUEUED


class TestKitchenService:
    async def test_create_ticket(self, service: KitchenService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "A"},
            )
            await session.commit()

        ticket = await service.create_ticket(str(order_id))
        assert ticket.state == KitchenState.NEW
        assert ticket.order_id == order_id

    async def test_transition_ticket(self, service: KitchenService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "A"},
            )
            await session.commit()

        ticket = await service.create_ticket(str(order_id))

        result = await service.transition_ticket(str(ticket.id), KitchenEvent.QUEUE)
        assert result.success is True
        assert result.new_state == KitchenState.QUEUED

    async def test_list_tickets(self, service: KitchenService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "A"},
            )
            await session.commit()

        await service.create_ticket(str(order_id))
        tickets = await service.list_tickets()
        assert len(tickets) >= 1
        assert any(t.order_id == order_id for t in tickets)

    async def test_invalid_event_rejected(self, service: KitchenService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "A"},
            )
            await session.commit()

        ticket = await service.create_ticket(str(order_id))

        result = await service.transition_ticket(str(ticket.id), KitchenEvent.HAND_OFF)
        assert result.success is False
        assert result.new_state is None

    async def test_list_tickets_with_filter(self, service: KitchenService) -> None:
        order_id = uuid.uuid4()
        async with service._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {"id": order_id, "cid": uuid.uuid4(), "state": "PAID", "items": "[]", "addr": "A"},
            )
            await session.commit()

        ticket = await service.create_ticket(str(order_id))

        await service.transition_ticket(str(ticket.id), KitchenEvent.QUEUE)

        queued = await service.list_tickets(state_filter=KitchenState.QUEUED)
        assert any(t.id == ticket.id for t in queued)

        new_tickets = await service.list_tickets(state_filter=KitchenState.NEW)
        assert not any(t.id == ticket.id for t in new_tickets)


class TestCrossDomainKitchenOnCooking:
    """order COOKING event → auto-create kitchen_ticket in single PG transaction."""

    @pytest.fixture
    async def fixture_session(
        self,
        postgres_dsn: str,
    ) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
        engine = create_async_engine(postgres_dsn)
        schema_path = (
            Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
        )
        schema = schema_path.read_text()

        raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(raw_dsn)
        try:
            await conn.execute(schema)
        finally:
            await conn.close()

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield factory

        await engine.dispose()

    async def test_start_cooking_creates_kitchen_ticket(
        self,
        fixture_session: async_sessionmaker[AsyncSession],
    ) -> None:
        order_id = uuid.uuid4()
        async with fixture_session() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": OrderState.PAID.value,
                    "items": "[]",
                    "addr": "Moscow",
                },
            )
            await session.commit()

            repo = OrderRepository(session)
            fsm = OrderFSM(
                state_reader=repo.get_state,
                state_writer=repo.write_state,
            )

            result = await fsm.handle_event(str(order_id), OrderEvent.START_COOKING)
            assert result.success is True
            assert result.new_state == OrderState.COOKING

            kitchen_repo = KitchenRepository(session)
            ticket_id = await kitchen_repo.create(str(order_id))
            await session.commit()

            ticket_state = await kitchen_repo.get_state(ticket_id)
            assert ticket_state == KitchenState.NEW

    async def test_order_cooking_without_kitchen_ticket_not_possible(
        self,
        fixture_session: async_sessionmaker[AsyncSession],
    ) -> None:
        order_id = uuid.uuid4()
        async with fixture_session() as session:
            await session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                    "VALUES (:id, :cid, :state, :items, :addr)"
                ),
                {
                    "id": order_id,
                    "cid": uuid.uuid4(),
                    "state": OrderState.PAID.value,
                    "items": "[]",
                    "addr": "SPb",
                },
            )
            await session.commit()

        from app.services.order_service import OrderService

        service = OrderService(session_factory=fixture_session)
        result = await service.transition_order(str(order_id), OrderEvent.START_COOKING)
        assert result.success is True
        assert result.new_state == OrderState.COOKING

        async with fixture_session() as session:
            ticket_count = await session.execute(
                text("SELECT COUNT(*) FROM kitchen_tickets WHERE order_id = :oid"),
                {"oid": order_id},
            )
            assert ticket_count.scalar_one() == 1
