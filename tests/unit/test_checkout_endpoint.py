"""tests/unit/test_checkout_endpoint.py — POST /api/orders wiring (mocked, no Docker).

Exercises the real cart.js contract through FastAPI with every downstream
service mocked, so the full request/response + idempotency behaviour runs in
CI unit tests (integration tests are skipped without Docker).

Covers:
  - cash path returns {ok, order_id} and NO confirmation_token;
  - card path returns {ok, order_id, confirmation_token};
  - idempotency_key reuse (same key, same request) never creates a second order;
  - missing zone_id for non-pickup -> 400;
  - unsupported payment_method -> 400.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.checkout import (
    get_customer_service,
    get_idempotency_service,
    get_menu_service,
    get_order_service,
    get_payment_service,
)
from app.api.routes.checkout import (
    router as checkout_router,
)
from app.domains.customer.models import Customer
from app.domains.menu.models import MenuProductItem
from app.domains.orders.models import OrderRead, OrderState


def _product_snapshot(pid):
    return MenuProductItem(
        product_id=pid,
        name="Item",
        price_rub=100,
        available=True,
        cta_type="add_to_cart",
        reason_code=None,
    )


def _build_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    app.include_router(checkout_router)

    order_svc = AsyncMock()
    customer_svc = AsyncMock()
    menu_svc = AsyncMock()
    menu_svc.get_product_snapshot = AsyncMock(side_effect=_product_snapshot)
    payment_svc = AsyncMock()
    idem_svc = AsyncMock()

    app.dependency_overrides[get_order_service] = lambda: order_svc
    app.dependency_overrides[get_customer_service] = lambda: customer_svc
    app.dependency_overrides[get_menu_service] = lambda: menu_svc
    app.dependency_overrides[get_payment_service] = lambda: payment_svc
    app.dependency_overrides[get_idempotency_service] = lambda: idem_svc

    return app, {
        "order": order_svc,
        "customer": customer_svc,
        "menu": menu_svc,
        "payment": payment_svc,
        "idem": idem_svc,
    }


def _body(payment_method: str, delivery_mode: str = "delivery", **over) -> dict:
    base = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": delivery_mode,
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": payment_method,
        "zone_id": 1,
        "items": [{"product_id": str(uuid4()), "qty": 2}],
        "idempotency_key": str(uuid4()),
        "client_max_uid": None,
    }
    base.update(over)
    return base


async def test_cash_path_no_confirmation_token() -> None:
    app, svc = _build_app()
    svc["idem"].check_and_record = AsyncMock(return_value=True)
    svc["customer"].find_or_create_by_phone = AsyncMock(
        return_value=Customer(id=uuid4(), name="Ivan", phone="+79991234567")
    )
    created = OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=OrderState.DRAFT,
        items=[],
        delivery_address="Moscow",
    )
    svc["order"].create_order_from_checkout = AsyncMock(return_value=created)
    svc["order"].transition_order = AsyncMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("cash"))

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["order_id"] == str(created.id)
    assert "confirmation_token" not in data or data["confirmation_token"] is None


async def test_card_path_returns_confirmation_token() -> None:
    app, svc = _build_app()
    svc["idem"].check_and_record = AsyncMock(return_value=True)
    svc["customer"].find_or_create_by_phone = AsyncMock(
        return_value=Customer(id=uuid4(), name="Ivan", phone="+79991234567")
    )
    created = OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=OrderState.DRAFT,
        items=[],
        delivery_address="Moscow",
    )
    svc["order"].create_order_from_checkout = AsyncMock(return_value=created)
    svc["payment"].create_payment = AsyncMock(
        return_value={
            "confirmation_url": "",
            "confirmation_token": "tok_abc123",
            "payment_id": "pay_1",
            "trace_id": "tr_1",
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("yookassa_card"))

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["confirmation_token"] == "tok_abc123"


async def test_idempotency_reuse_does_not_create_second_order() -> None:
    app, svc = _build_app()
    # First call inserts, second is a duplicate (returns False).
    svc["idem"].check_and_record = AsyncMock(side_effect=[True, False])
    svc["idem"].get_payload = AsyncMock(
        return_value={
            "phone": "+79991234567",
            "payment_method": "cash",
            "item_count": 1,
            "order_id": str(uuid4()),
        }
    )
    svc["customer"].find_or_create_by_phone = AsyncMock(
        return_value=Customer(id=uuid4(), name="Ivan", phone="+79991234567")
    )
    created = OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=OrderState.DRAFT,
        items=[],
        delivery_address="Moscow",
    )
    svc["order"].create_order_from_checkout = AsyncMock(return_value=created)

    body = _body("cash")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/orders", json=body)
        second = await client.post("/api/orders", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["order_id"] == svc["idem"].get_payload.return_value["order_id"]
    # create_order_from_checkout must have been called exactly once.
    svc["order"].create_order_from_checkout.assert_awaited_once()


async def test_missing_zone_id_for_delivery_returns_400() -> None:
    app, svc = _build_app()
    svc["idem"].check_and_record = AsyncMock(return_value=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/orders",
            json=_body("cash", delivery_mode="delivery", zone_id=None),
        )

    assert resp.status_code == 400


async def test_unsupported_payment_method_returns_400() -> None:
    app, svc = _build_app()
    svc["idem"].check_and_record = AsyncMock(return_value=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("bitcoin"))

    assert resp.status_code == 400
