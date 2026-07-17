"""tests/integration/test_delivery_zones_api.py — httpx AsyncClient against
GET /api/delivery-zones (public, no auth).

Gate: the route MUST resolve to exactly /api/delivery-zones in a live TestClient
request (NOT /delivery/api/delivery-zones). We assert the path explicitly.

Requires Docker (existing postgres). Skipped if not available.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.menu import get_menu_service
from app.api.routes.menu import router as menu_router
from app.services.menu_service import MenuService


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = (
        Path(__file__).resolve().parents[2] / "migrations" / "006_delivery_zones.sql"
    )
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    import asyncpg

    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema_path.read_text())
        await conn.execute("TRUNCATE TABLE delivery_zones")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def seed_zones(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data_path = (
        Path(__file__).resolve().parents[2] / "data" / "delivery_zones.json"
    )
    import json

    zones = json.loads(data_path.read_text(encoding="utf-8"))
    async with session_factory() as session:
        for zone in zones:
            await session.execute(
                text(
                    "INSERT INTO delivery_zones "
                    "(external_id, name, delivery_time_minutes, is_active) "
                    "VALUES (:external_id, :name, :delivery_time_minutes, :is_active)"
                ),
                {
                    "external_id": str(zone["id"]),
                    "name": zone["name"],
                    "delivery_time_minutes": zone["delivery_time_minutes"],
                    "is_active": zone.get("is_active", True),
                },
            )
        await session.commit()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(menu_router)

    async def _test_service() -> MenuService:
        return MenuService(session_factory=session_factory)

    app.dependency_overrides[get_menu_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestDeliveryZonesAPI:
    async def test_route_resolves_exactly(
        self,
        client: AsyncClient,
        seed_zones: None,
    ) -> None:
        # Gate: the route MUST resolve to exactly /api/delivery-zones,
        # NOT /delivery/api/delivery-zones.
        resp = await client.get("/api/delivery-zones")
        assert resp.status_code == 200
        assert resp.request.url.path == "/api/delivery-zones"

        # And the wrong path must NOT resolve (registration on menu_router only).
        wrong = await client.get("/delivery/api/delivery-zones")
        assert wrong.status_code == 404

    async def test_returns_active_zones_shape(
        self,
        client: AsyncClient,
        seed_zones: None,
    ) -> None:
        resp = await client.get("/api/delivery-zones")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3

        # checkout.html reads zone.name / zone.delivery_time_minutes only.
        names = {z["name"] for z in data}
        assert "Балахня" in names
        assert "Город-Аэропорт-Дом Отдыха" in names
        assert "Отдаленные районы" in names

        for z in data:
            assert set(z.keys()) >= {"id", "name", "delivery_time_minutes"}

        balahna = next(z for z in data if z["name"] == "Балахня")
        assert balahna["delivery_time_minutes"] == 15

    async def test_public_no_auth_required(
        self,
        client: AsyncClient,
        seed_zones: None,
    ) -> None:
        # No Authorization header at all — must still succeed (customer-facing).
        resp = await client.get("/api/delivery-zones")
        assert resp.status_code == 200
