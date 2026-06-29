"""tests/integration/test_orders_api.py — httpx AsyncClient against FastAPI app.

Requires Docker (testcontainers). Skipped if not available.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import router as admin_router
from app.api.routes.orders import router as orders_router
from app.services.order_service import OrderService


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
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(orders_router)
    app.include_router(admin_router)

    from app.api.routes.admin import get_order_service as admin_get_service
    from app.api.routes.orders import get_order_service as orders_get_service

    async def _test_service() -> OrderService:
        return OrderService(session_factory=session_factory)

    app.dependency_overrides[orders_get_service] = _test_service
    app.dependency_overrides[admin_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestOrdersAPI:
    async def test_create_order_returns_201(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [{"sku": "coffee", "qty": 2}],
            "delivery_address": "Moscow, Red Square 1",
        }
        resp = await client.post("/orders", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["state"] == "DRAFT"
        assert data["delivery_address"] == "Moscow, Red Square 1"

    async def test_create_and_transition(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [],
            "delivery_address": "SPb",
        }
        create_resp = await client.post("/orders", json=payload)
        assert create_resp.status_code == 201
        order_id = create_resp.json()["id"]

        event_resp = await client.post(
            f"/orders/{order_id}/events",
            json="CONFIRM",
        )
        assert event_resp.status_code == 200
        event_data = event_resp.json()
        assert event_data["success"] is True
        assert event_data["new_state"] == "CONFIRMED"

    async def test_invalid_event_rejected(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [],
            "delivery_address": "Kazan",
        }
        create_resp = await client.post("/orders", json=payload)
        order_id = create_resp.json()["id"]

        event_resp = await client.post(
            f"/orders/{order_id}/events",
            json="PAYMENT_CONFIRMED",
        )
        assert event_resp.status_code == 200
        event_data = event_resp.json()
        assert event_data["success"] is False

    async def test_admin_list_orders(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/orders")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_admin_list_orders_with_filter(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [],
            "delivery_address": "Ekb",
        }
        await client.post("/orders", json=payload)

        resp = await client.get("/admin/orders", params={"state": "DRAFT"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert all(o["state"] == "DRAFT" for o in data)
