"""tests/unit/test_checkout_endpoint.py — POST /api/orders wiring (mocked, no Docker).

Exercises the real cart.js contract through FastAPI with every downstream
service mocked, so the full request/response + idempotency behaviour runs in
CI unit tests (integration tests are skipped without Docker).

Covers:
  - cash path returns {ok, order_id} and NO confirmation_url;
  - sbp/sberbank paths return {ok, order_id, confirmation_url} (manual
    integration redirect flow — no embedded widget, see payment_service.py);
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
    get_zone_service,
)
from app.api.routes.checkout import (
    router as checkout_router,
)
from app.domains.customer.models import Customer
from app.domains.delivery.zones import DeliveryZone
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
    zone_svc = AsyncMock()
    # Default: a resolvable zone with a flat 99-RUB fee, matching the
    # pre-per-zone-fee global default — most tests don't care about the
    # exact fee value, only that zone_id resolves without error.
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

    return app, {
        "order": order_svc,
        "customer": customer_svc,
        "menu": menu_svc,
        "payment": payment_svc,
        "idem": idem_svc,
        "zone": zone_svc,
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
        "zone_id": str(uuid4()),  # BUGFIX (2026-07-19): was `1` (int) — zone_id
        # is UUID now, matching delivery_zones.id's real type
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


async def test_sbp_path_returns_confirmation_url() -> None:
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
            "confirmation_url": "https://yookassa.ru/payments/external/confirmation?...",
            "confirmation_token": "",
            "payment_id": "pay_1",
            "trace_id": "tr_1",
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("yookassa_sbp"))

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["confirmation_url"]
    assert not data.get("confirmation_token")
    call_kwargs = svc["payment"].create_payment.call_args.kwargs
    assert call_kwargs["confirmation_type"] == "redirect"
    assert call_kwargs["payment_method_data"] == {"type": "sbp"}


async def test_sbp_path_return_url_carries_order_id() -> None:
    """sprint_fix_online_payment_funnel (2026-08-19): return_url must embed
    order_id so GET /payment/return can redirect the customer to their own
    /thanks/{order_id} instead of a bare, order-less bounce. Previously
    /payment/return didn't even exist (live 404)."""
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
            "confirmation_url": "https://yookassa.ru/payments/external/confirmation?...",
            "confirmation_token": "",
            "payment_id": "pay_1",
            "trace_id": "tr_1",
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("yookassa_sbp"))

    assert resp.status_code == 200
    call_kwargs = svc["payment"].create_payment.call_args.kwargs
    assert call_kwargs["return_url"].endswith(f"?order_id={created.id}")


async def test_sbp_path_does_not_notify_staff_before_payment_confirmed() -> None:
    """sprint_fix_online_payment_funnel (2026-08-19): checkout.py must NOT
    call notify_admin_order_state/notify_staff_card at payment-link-creation
    time — the customer hasn't paid yet. Reported live: order card appeared
    in MAX for a payment that was never completed, while the kitchen board
    never showed the order (create_kitchen_ticket only runs on
    START_COOKING, downstream of PaymentService.confirm_payment(), which
    now owns this notification — see test_payment_service.py)."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import patch

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
            "confirmation_url": "https://yookassa.ru/payments/external/confirmation?...",
            "confirmation_token": "",
            "payment_id": "pay_1",
            "trace_id": "tr_1",
        }
    )

    with (
        patch(
            "app.api.routes.checkout.notify_admin_order_state", _AsyncMock(),
        ) as mock_admin,
        patch(
            "app.api.routes.checkout.notify_staff_card", _AsyncMock(),
        ) as mock_staff,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            resp = await client.post("/api/orders", json=_body("yookassa_sbp"))

        assert resp.status_code == 200
        mock_admin.assert_not_called()
        mock_staff.assert_not_called()


async def test_card_path_yookassa_rejection_returns_502_json_not_plain_500() -> None:
    """sprint_yookassa_live_error_surfacing (2026-08-17): a YooKassa 4xx/5xx
    rejection (e.g. live-mode moderation/receipt/auth issues) must not
    propagate as an unhandled exception — FastAPI's default handler returns
    plain-text "Internal Server Error" for that, which crashes cart.js's
    `await response.json()` with a SyntaxError before the user ever sees a
    real message. Must come back as valid JSON with an HTTPException-shaped
    body instead."""
    import httpx

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

    fake_response = httpx.Response(
        status_code=400,
        json={"type": "error", "code": "invalid_request", "description": "bad request"},
        request=httpx.Request("POST", "https://api.yookassa.ru/v3/payments"),
    )
    svc["payment"].create_payment = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "400 Bad Request", request=fake_response.request, response=fake_response,
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("yookassa_sbp"))

    assert resp.status_code == 502
    data = resp.json()  # must not raise — this is the whole point of the fix
    assert "detail" in data


async def test_card_path_yookassa_network_error_returns_502_json() -> None:
    import httpx

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
        side_effect=httpx.ConnectError("connection refused")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/orders", json=_body("yookassa_sbp"))

    assert resp.status_code == 502
    data = resp.json()
    assert "detail" in data


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
