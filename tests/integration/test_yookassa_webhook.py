"""tests/integration/test_yookassa_webhook.py — ADR-003 safety branches.

Tests duplicate/concurrent/not-found guard branches.
All branches MUST return 200 (never 4xx to YooKassa).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.orders.models import OrderState
from app.services.payment_service import PaymentService, YooKassaClient
from app.trace import TraceEvent, trace
from app.webhooks.yookassa import get_payment_service
from app.webhooks.yookassa import router as yookassa_router


@pytest.fixture
async def session_factory(
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


@pytest.fixture
async def order_id(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as session:
        from sqlalchemy import text

        oid = str(uuid.uuid4())
        await session.execute(
            text(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES (:id, :customer_id, :state, :items, :addr)"
            ),
            {
                "id": uuid.UUID(oid),
                "customer_id": uuid.uuid4(),
                "state": OrderState.PAYMENT_PENDING.value,
                "items": "[]",
                "addr": "Moscow",
            },
        )
        await session.commit()
    return oid


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    order_id: str,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(yookassa_router)

    def _test_payment_service() -> PaymentService:
        yookassa = YooKassaClient(shop_id="test", secret_key="test")
        yookassa.create_payment = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "id": str(uuid.uuid4()),
                "status": "pending",
                "confirmation": {"confirmation_url": "https://test.confirm"},
            }
        )
        return PaymentService(
            session_factory=session_factory,
            yookassa=yookassa,
        )

    app.dependency_overrides[get_payment_service] = _test_payment_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def trace_event(order_id: str) -> Generator[TraceEvent, None, None]:
    """Record a trace event so the webhook can look it up. Cleanup after test."""
    tid = str(uuid.uuid4())
    event = TraceEvent(
        trace_id=tid,
        entity_id=order_id,
        domain="orders",
        event="PAYMENT_REQUESTED",
        from_state="CONFIRMED",
        to_state="PAYMENT_PENDING",
    )
    trace._events.append(event)
    yield event
    # Cleanup: remove events added during this test
    trace._events.clear()


class TestYooKassaWebhook:
    """ADR-003 safety branches: duplicate, concurrent, not-found."""

    async def test_not_found_trace_id_returns_200(
        self, client: AsyncClient, order_id: str
    ) -> None:
        payload = {
            "id": str(uuid.uuid4()),
            "event": "payment.succeeded",
            "object": {
                "id": str(uuid.uuid4()),
                "metadata": {"trace_id": "non-existent-trace-id"},
            },
        }
        resp = await client.post("/webhooks/yookassa", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_duplicate_webhook_returns_200(
        self, client: AsyncClient, order_id: str, trace_event: TraceEvent,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        event_id = str(uuid.uuid4())
        payment_id = str(uuid.uuid4())

        # Create a payment record so the first call succeeds
        async with session_factory() as session:
            from sqlalchemy import text

            await session.execute(
                text(
                    "INSERT INTO payments "
                "(order_id, provider, provider_id, amount, currency, state) "
                    "VALUES (:order_id, 'yookassa', :provider_id, 1500.00, 'RUB', 'PENDING')"
                ),
                {"order_id": uuid.UUID(order_id), "provider_id": payment_id},
            )
            await session.commit()

        payload = {
            "id": event_id,
            "event": "payment.succeeded",
            "object": {
                "id": payment_id,
                "metadata": {
                    "trace_id": trace_event.trace_id,
                    "order_id": order_id,
                },
            },
        }

        resp1 = await client.post("/webhooks/yookassa", json=payload)
        assert resp1.status_code == 200

        resp2 = await client.post("/webhooks/yookassa", json=payload)
        assert resp2.status_code == 200
        assert resp2.json() == {"ok": True}

    async def test_not_yookassa_event_type_returns_200(
        self, client: AsyncClient
    ) -> None:
        payload = {
            "id": str(uuid.uuid4()),
            "event": "payment.waiting_for_capture",
            "object": {
                "id": str(uuid.uuid4()),
                "metadata": {"trace_id": "irrelevant"},
            },
        }
        resp = await client.post("/webhooks/yookassa", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_missing_trace_id_in_metadata_returns_200(
        self, client: AsyncClient
    ) -> None:
        payload = {
            "id": str(uuid.uuid4()),
            "event": "payment.succeeded",
            "object": {
                "id": str(uuid.uuid4()),
                "metadata": {},
            },
        }
        resp = await client.post("/webhooks/yookassa", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_invalid_json_body_returns_200(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/webhooks/yookassa",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_successful_webhook_updates_order_state(
        self, client: AsyncClient, order_id: str, trace_event: TraceEvent,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        payment_id = str(uuid.uuid4())

        # Create a payment record first
        async with session_factory() as session:
            from sqlalchemy import text

            await session.execute(
                text(
                    "INSERT INTO payments "
                "(order_id, provider, provider_id, amount, currency, state) "
                    "VALUES (:order_id, 'yookassa', :provider_id, 1500.00, 'RUB', 'PENDING')"
                ),
                {"order_id": uuid.UUID(order_id), "provider_id": payment_id},
            )
            await session.commit()

        payload = {
            "id": str(uuid.uuid4()),
            "event": "payment.succeeded",
            "object": {
                "id": payment_id,
                "metadata": {
                    "trace_id": trace_event.trace_id,
                    "order_id": order_id,
                },
            },
        }

        resp = await client.post("/webhooks/yookassa", json=payload)
        assert resp.status_code == 200

        async with session_factory() as session:
            from sqlalchemy import text

            result = await session.execute(
                text("SELECT state FROM orders WHERE id = :id"),
                {"id": uuid.UUID(order_id)},
            )
            state = result.scalar_one()
            # After webhook: PAYMENT_CONFIRMED → PAID → START_COOKING → COOKING
            assert state in (OrderState.PAID.value, OrderState.COOKING.value)
