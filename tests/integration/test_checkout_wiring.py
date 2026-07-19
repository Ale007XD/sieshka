"""tests/integration/test_checkout_wiring.py — POST /api/orders HTTP round-trip.

Requires Docker (skipped otherwise). Exercises the full path against a real
Postgres: customer find-or-create, price snapshotting, server-total compute,
YooKassa embedded confirmation_token for card, and no-token cash path. The
YooKassa client is mocked at the PaymentService boundary so no real payment
intent is ever created (per sprint_m7_checkout_wiring BLOCKING QUESTION).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.checkout import router as checkout_router
from app.api.routes.orders import router as orders_router
from app.services.customer_service import CustomerService
from app.services.menu_service import MenuService
from app.services.order_service import OrderService


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    schema = schema_path.read_text()
    checkout_migration = (
        Path(__file__).resolve().parents[2] / "migrations" / "010_checkout_columns.sql"
    )
    checkout_sql = checkout_migration.read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
        await conn.execute(checkout_sql)
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(checkout_router)
    app.include_router(orders_router)

    async def _order_svc() -> OrderService:
        return OrderService(session_factory=session_factory)

    async def _customer_svc() -> CustomerService:
        return CustomerService(session_factory=session_factory)

    async def _menu_svc() -> MenuService:
        return MenuService(session_factory=session_factory)

    from app.api.routes.checkout import (
        get_customer_service,
        get_menu_service,
        get_order_service,
    )

    app.dependency_overrides[get_order_service] = _order_svc
    app.dependency_overrides[get_customer_service] = _customer_svc
    app.dependency_overrides[get_menu_service] = _menu_svc

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_product(factory: async_sessionmaker[AsyncSession], price: int = 150) -> uuid.UUID:
    pid = uuid.uuid4()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, price_rub, is_active) "
                "VALUES (:id, 'Latte', :price, TRUE)"
            ),
            {"id": pid, "price": price},
        )
        await session.commit()
    return pid


async def test_cash_checkout_creates_order_no_token(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pid = await _seed_product(session_factory, 150)
    body = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": "pickup",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": "cash",
        "zone_id": None,
        "items": [{"product_id": str(pid), "qty": 2}],
        "idempotency_key": str(uuid.uuid4()),
        "client_max_uid": None,
    }
    resp = await client.post("/api/orders", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "confirmation_token" not in data or data["confirmation_token"] is None

    order_id = uuid.UUID(data["order_id"])
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT state, total_rub FROM orders WHERE id = :id"),
                {"id": order_id},
            )
        ).fetchone()
    assert row is not None
    assert row._mapping["state"] == "CONFIRMED"
    # pickup -> no delivery fee: 150 * 2 = 300
    assert row._mapping["total_rub"] == 300


async def test_card_checkout_returns_confirmation_token(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pid = await _seed_product(session_factory, 150)
    body = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": "delivery",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": "yookassa_card",
        "zone_id": 1,
        "items": [{"product_id": str(pid), "qty": 2}],
        "idempotency_key": str(uuid.uuid4()),
        "client_max_uid": None,
    }
    fake_payment = {
        "confirmation_url": "",
        "confirmation_token": "tok_embedded_123",
        "payment_id": "pay_1",
        "trace_id": "tr_1",
    }
    with patch(
        "app.api.routes.checkout.PaymentService.create_payment",
        new=AsyncMock(return_value=fake_payment),
    ):
        resp = await client.post("/api/orders", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["confirmation_token"] == "tok_embedded_123"

    order_id = uuid.UUID(data["order_id"])
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT state, total_rub FROM orders WHERE id = :id"),
                {"id": order_id},
            )
        ).fetchone()
    assert row is not None
    # delivery -> + DELIVERY_FEE (99): 150 * 2 + 99 = 399
    assert row._mapping["total_rub"] == 399
    assert row._mapping["state"] == "PAYMENT_PENDING"


async def test_idempotency_reuse_no_second_order(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pid = await _seed_product(session_factory, 150)
    body = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": "pickup",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": "cash",
        "zone_id": None,
        "items": [{"product_id": str(pid), "qty": 1}],
        "idempotency_key": str(uuid.uuid4()),
        "client_max_uid": None,
    }
    r1 = await client.post("/api/orders", json=body)
    r2 = await client.post("/api/orders", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["order_id"] == r2.json()["order_id"]
