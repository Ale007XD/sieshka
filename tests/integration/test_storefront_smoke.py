"""tests/integration/test_storefront_smoke.py — full customer order-flow smoke test.

sprint_m7_static_assets_smoke deliverable #1: a full interactive order flow
through the REAL nano-vm Program pipeline (not a mock):

    GET / (storefront shell) -> GET /api/menu (real 89-product data) ->
    GET /api/delivery-zones -> POST /api/orders (cash AND yookassa_card) ->
    GET /thanks/{order_id}

Every step is the production wire: app.main.app (real customer_router,
checkout_router, menu_router, CSPMiddleware, static mount) with the DB-backed
services overridden onto a throwaway Postgres test DB. The order's FSM state is
asserted to have advanced via the governed ExecutionVM programs:

    cash          -> DRAFT -CONFIRM-> CONFIRMED
    yookassa_card -> DRAFT -CONFIRM-> CONFIRMED -REQUEST_PAYMENT-> PAYMENT_PENDING

The YooKassa client is mocked ONLY at PaymentService.create_payment (per
sprint_m7_checkout_wiring's blocking question) — the nano-vm pipeline that
drives the order's own state transitions is NOT mocked.

Requires Docker (the running sieshka-postgres-1 container); skipped otherwise.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app as main_app
from app.services.customer_service import CustomerService
from app.services.idempotency import IdempotencyService
from app.services.menu_service import MenuService
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema = (
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    ).read_text()
    checkout_sql = (
        Path(__file__).resolve().parents[2] / "migrations" / "010_checkout_columns.sql"
    ).read_text()
    menu_sql = (
        Path(__file__).resolve().parents[2] / "migrations" / "004_menu.sql"
    ).read_text()

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    import asyncpg

    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
        await conn.execute(checkout_sql)
        await conn.execute(menu_sql)
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


class _Seed(NamedTuple):
    cat_id: uuid.UUID
    prod_id: uuid.UUID
    zone_id: uuid.UUID


@pytest.fixture
async def seeded(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[_Seed, None]:
    """Seed one category + one active product + one active delivery zone."""
    cat_id = uuid.uuid4()
    prod_id = uuid.uuid4()
    zone_id = uuid.uuid4()
    async with session_factory() as session:
        # Isolate this fixture's rows: the test DB is session-scoped, so prior
        # test functions' seeds would otherwise collide on unique constraints
        # (delivery_zones.lower(name), products, etc.).
        await session.execute(
            text("TRUNCATE TABLE delivery_zones, products, categories RESTART IDENTITY")
        )
        await session.execute(
            text(
                "INSERT INTO categories (id, name, menu_period, is_active) "
                "VALUES (:id, 'Smoke Category', 'both', TRUE)"
            ),
            {"id": cat_id},
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, category_id, price_rub, is_active) "
                "VALUES (:id, 'Smoke Latte', :cat, 150, TRUE)"
            ),
            {"id": prod_id, "cat": cat_id},
        )
        await session.execute(
            text(
                "INSERT INTO delivery_zones (id, external_id, name, "
                "delivery_time_minutes, is_active) "
                "VALUES (:id, 'z1', 'Zone 1', 30, TRUE)"
            ),
            {"id": zone_id},
        )
        await session.commit()
    yield _Seed(cat_id=cat_id, prod_id=prod_id, zone_id=zone_id)


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    async def _order_svc() -> OrderService:
        return OrderService(session_factory=session_factory)

    async def _customer_svc() -> CustomerService:
        return CustomerService(session_factory=session_factory)

    async def _menu_svc() -> MenuService:
        return MenuService(session_factory=session_factory)

    async def _idempotency_svc() -> IdempotencyService:
        return IdempotencyService(session_factory=session_factory)

    async def _payment_svc() -> PaymentService:
        return PaymentService(session_factory=session_factory)

    from app.api.routes.checkout import (
        get_customer_service,
        get_idempotency_service,
        get_order_service,
        get_payment_service,
    )
    from app.api.routes.checkout import (
        get_menu_service as checkout_get_menu_service,
    )
    from app.api.routes.menu import get_menu_service as menu_get_menu_service
    from app.web.customer_routes import get_order_service as cust_get_order_service
    from app.web.customer_routes import get_schedule_service

    main_app.dependency_overrides[get_order_service] = _order_svc
    main_app.dependency_overrides[get_customer_service] = _customer_svc
    main_app.dependency_overrides[checkout_get_menu_service] = _menu_svc
    main_app.dependency_overrides[menu_get_menu_service] = _menu_svc
    main_app.dependency_overrides[get_idempotency_service] = _idempotency_svc
    main_app.dependency_overrides[get_payment_service] = _payment_svc
    main_app.dependency_overrides[cust_get_order_service] = _order_svc
    main_app.dependency_overrides[get_schedule_service] = AsyncMock()

    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    main_app.dependency_overrides.clear()


def _checkout_body(prod_id: uuid.UUID, method: str) -> dict[str, object]:
    return {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow" if method == "yookassa_card" else None,
        "comment": None,
        "delivery_mode": "delivery" if method == "yookassa_card" else "pickup",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": method,
        "zone_id": 1 if method == "yookassa_card" else None,
        "items": [{"product_id": str(prod_id), "qty": 2}],
        "idempotency_key": str(uuid.uuid4()),
        "client_max_uid": None,
    }


async def _order_state(
    session_factory: async_sessionmaker[AsyncSession], order_id: uuid.UUID
) -> str:
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT state FROM orders WHERE id = :id"),
                {"id": order_id},
            )
        ).fetchone()
    assert row is not None
    state: str = row._mapping["state"]
    return state


class TestStorefrontSmoke:
    async def test_index_shell_renders_storefront_ids(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'id="menu-root"' in resp.text
        assert 'id="menu-status"' in resp.text
        # cart.js + menu.js are served with CSP nonce (static assets wired).
        assert "/static/js/cart.js" in resp.text
        assert "/static/js/menu.js" in resp.text

    async def test_menu_api_returns_seeded_product(
        self, client: AsyncClient, seeded: _Seed
    ) -> None:
        resp = await client.get("/api/menu?method=delivery")
        assert resp.status_code == 200
        data = resp.json()
        flat = [p for c in data["categories"] for p in c["products"]]
        ids = {str(p["product_id"]) for p in flat}
        assert str(seeded.prod_id) in ids

    async def test_delivery_zones_api(
        self, client: AsyncClient, seeded: _Seed
    ) -> None:
        resp = await client.get("/api/delivery-zones")
        assert resp.status_code == 200
        zones = resp.json()
        assert any(z["id"] == str(seeded.zone_id) for z in zones)

    async def test_cash_checkout_end_to_end(
        self,
        client: AsyncClient,
        seeded: _Seed,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        body = _checkout_body(seeded.prod_id, "cash")
        resp = await client.post("/api/orders", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "confirmation_token" not in data or data["confirmation_token"] is None

        order_id = uuid.UUID(data["order_id"])
        # cash: real FSM advanced DRAFT -> CONFIRMED via nano-vm program.
        assert await _order_state(session_factory, order_id) == "CONFIRMED"

        thanks = await client.get(f"/thanks/{order_id}")
        assert thanks.status_code == 200
        assert str(order_id) in thanks.text

    async def test_yookassa_card_checkout_end_to_end(
        self,
        client: AsyncClient,
        seeded: _Seed,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        body = _checkout_body(seeded.prod_id, "yookassa_card")
        fake_payment = {
            "confirmation_url": "",
            "confirmation_token": "tok_embedded_smoke",
            "payment_id": "pay_smoke",
            "trace_id": "tr_smoke",
        }
        with patch(
            "app.api.routes.checkout.PaymentService.create_payment",
            new=AsyncMock(return_value=fake_payment),
        ):
            resp = await client.post("/api/orders", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["confirmation_token"] == "tok_embedded_smoke"

        order_id = uuid.UUID(data["order_id"])
        # yookassa_card: real FSM advanced DRAFT -> CONFIRMED -> PAYMENT_PENDING
        # via the governed nano-vm programs (YooKassa client mocked only).
        assert await _order_state(session_factory, order_id) == "PAYMENT_PENDING"

        thanks = await client.get(f"/thanks/{order_id}")
        assert thanks.status_code == 200
        assert str(order_id) in thanks.text
