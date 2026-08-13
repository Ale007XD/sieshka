"""tests/unit/test_checkout_zalo_attribution.py — client_zalo_uid verification
branch in POST /api/orders (sprint_zalo_storefront_auth).

Mirrors test_checkout_endpoint.py's fully-mocked pattern (no DB, no Docker).
Covers exactly the new branch added to checkout.py — the rest of the
checkout flow is already covered there and not re-tested here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.checkout import (
    get_customer_service,
    get_idempotency_service,
    get_menu_service,
    get_order_service,
    get_payment_service,
    get_zone_service,
)
from app.api.routes.checkout import router as checkout_router
from app.domains.customer.models import Customer
from app.domains.delivery.zones import DeliveryZone
from app.domains.menu.models import MenuProductItem
from app.domains.orders.models import OrderRead, OrderState
from app.services.zalo_client import ZaloProfileError


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
    idem_svc.check_and_record = AsyncMock(return_value=True)
    zone_svc = AsyncMock()
    zone_svc.get_by_id = AsyncMock(
        return_value=DeliveryZone(
            id=uuid4(),
            external_id=None,
            name="Test Zone",
            delivery_time_minutes=30,
            is_active=True,
            delivery_fee_rub=99,
        )
    )

    app.dependency_overrides[get_order_service] = lambda: order_svc
    app.dependency_overrides[get_customer_service] = lambda: customer_svc
    app.dependency_overrides[get_menu_service] = lambda: menu_svc
    app.dependency_overrides[get_payment_service] = lambda: payment_svc
    app.dependency_overrides[get_idempotency_service] = lambda: idem_svc
    app.dependency_overrides[get_zone_service] = lambda: zone_svc

    customer_svc.find_or_create_by_phone = AsyncMock(
        return_value=Customer(id=uuid4(), name="Ivan", phone="+79991234567")
    )
    created = OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=OrderState.DRAFT,
        items=[],
        delivery_address="Moscow",
    )
    order_svc.create_order_from_checkout = AsyncMock(return_value=created)
    order_svc.transition_order = AsyncMock()

    return app, {"order": order_svc}


def _body(**over) -> dict:
    base = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": "pickup",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": "cash",
        "zone_id": None,
        "items": [{"product_id": str(uuid4()), "qty": 2}],
        "idempotency_key": str(uuid4()),
    }
    base.update(over)
    return base


class TestChecktoutZaloAttribution:
    async def test_no_header_leaves_client_zalo_uid_none(self) -> None:
        app, svc = _build_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/orders", json=_body())

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_zalo_uid is None

    async def test_valid_token_sets_verified_client_zalo_uid(self) -> None:
        app, svc = _build_app()

        with patch(
            "app.api.routes.checkout.zalo_client.get_user_profile",
            AsyncMock(return_value={"id": "zalo-uid-42"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/orders",
                    json=_body(),
                    headers={"X-Zalo-Access-Token": "tok"},
                )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_zalo_uid == "zalo-uid-42"

    async def test_a_client_supplied_client_zalo_uid_is_overridden_not_trusted(self) -> None:
        """Same non-trust posture as client_max_uid: a client-submitted value
        in the JSON body must never survive unverified."""
        app, svc = _build_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/orders", json=_body(client_zalo_uid="attacker-claimed-id")
            )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_zalo_uid is None

    async def test_invalid_token_degrades_to_none_order_still_created(self) -> None:
        app, svc = _build_app()

        with patch(
            "app.api.routes.checkout.zalo_client.get_user_profile",
            AsyncMock(side_effect=ZaloProfileError("expired token")),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/orders",
                    json=_body(),
                    headers={"X-Zalo-Access-Token": "bad-tok"},
                )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_zalo_uid is None
