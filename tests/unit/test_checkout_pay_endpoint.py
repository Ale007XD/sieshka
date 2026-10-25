"""tests/unit/test_checkout_pay_endpoint.py — POST /orders/{id}/pay wiring.

Unit-level: no Docker. Mocks OrderService.get_order and PaymentService.create_payment
via FastAPI dependency overrides, exercising the customer-facing outbound payment path.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.orders import (
    get_order_service,
    get_payment_service,
)
from app.api.routes.orders import (
    router as orders_router,
)
from app.domains.orders.models import OrderRead, OrderState


def _make_order(state: OrderState, total_rub: int | None = 199) -> OrderRead:
    return OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=state,
        items=[],
        delivery_address="Moscow, Red Square 1",
        total_rub=total_rub,
    )


def _build_app(order: OrderRead | None, payment_result: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(orders_router)

    order_svc = AsyncMock()
    order_svc.get_order = AsyncMock(return_value=order)
    payment_svc = AsyncMock()
    payment_svc.create_payment = AsyncMock(return_value=payment_result)

    app.dependency_overrides[get_order_service] = lambda: order_svc
    app.dependency_overrides[get_payment_service] = lambda: payment_svc
    return app


async def test_pay_returns_confirmation_url() -> None:
    order = _make_order(OrderState.CONFIRMED)
    payment_result = {
        "confirmation_url": "https://yookassa.ru/confirm/abc",
        "payment_id": "pay_123",
        "trace_id": "trace_xyz",
    }
    app = _build_app(order, payment_result)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay", json={"amount": "199.00"}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["confirmation_url"] == "https://yookassa.ru/confirm/abc"
    assert data["payment_id"] == "pay_123"
    assert data["trace_id"] == "trace_xyz"


async def test_pay_404_when_order_missing() -> None:
    app = _build_app(None, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{uuid4()}/pay", json={"amount": "199.00"}
        )

    assert resp.status_code == 404


async def test_pay_409_when_not_confirmed() -> None:
    order = _make_order(OrderState.DRAFT)
    app = _build_app(order, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay", json={"amount": "199.00"}
        )

    assert resp.status_code == 409


# sprint_pay_endpoint_curation_fix (2026-08-27): regression coverage for the
# confirmation_type=embedded / unrestricted payment_method_data gap this
# legacy endpoint had — see app/api/routes/orders.py module docstring.


async def test_pay_defaults_to_curated_sbp_redirect() -> None:
    """No payment_method in the request body -> defaults to sbp, forced redirect."""
    order = _make_order(OrderState.CONFIRMED)
    payment_result = {
        "confirmation_url": "https://yookassa.ru/confirm/abc",
        "payment_id": "pay_123",
        "trace_id": "trace_xyz",
    }
    app = _build_app(order, payment_result)
    payment_svc = app.dependency_overrides[get_payment_service]()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay", json={"amount": "199.00"}
        )

    assert resp.status_code == 200
    _, kwargs = payment_svc.create_payment.call_args
    assert kwargs["confirmation_type"] == "redirect"
    assert kwargs["payment_method_data"] == {"type": "sbp"}


async def test_pay_yookassa_sberbank_maps_to_sberbank_type() -> None:
    order = _make_order(OrderState.CONFIRMED)
    payment_result = {
        "confirmation_url": "https://yookassa.ru/confirm/abc",
        "payment_id": "pay_123",
        "trace_id": "trace_xyz",
    }
    app = _build_app(order, payment_result)
    payment_svc = app.dependency_overrides[get_payment_service]()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay",
            json={"amount": "199.00", "payment_method": "yookassa_sberbank"},
        )

    assert resp.status_code == 200
    _, kwargs = payment_svc.create_payment.call_args
    assert kwargs["confirmation_type"] == "redirect"
    assert kwargs["payment_method_data"] == {"type": "sberbank"}


async def test_pay_rejects_unknown_payment_method() -> None:
    """bank_card (or anything else) must be rejected, not silently forwarded."""
    order = _make_order(OrderState.CONFIRMED)
    app = _build_app(order, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay",
            json={"amount": "199.00", "payment_method": "bank_card"},
        )

    assert resp.status_code == 400


# security_fix_vuln03 (audit P3): the charged amount must come from the
# server's order.total_rub, never from the request body; the post-payment
# redirect must come from settings.YOOKASSA_RETURN_URL.


async def test_pay_rejects_amount_below_order_total() -> None:
    """The VULN-03 attack: pay 1 RUB for a 199 RUB order — must 409."""
    order = _make_order(OrderState.CONFIRMED, total_rub=199)
    app = _build_app(order, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay", json={"amount": "1.00"}
        )

    assert resp.status_code == 409


async def test_pay_rejects_order_without_recorded_total() -> None:
    """Pre-migration orders with total_rub=None must not be priceable."""
    order = _make_order(OrderState.CONFIRMED, total_rub=None)
    app = _build_app(order, {})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay", json={"amount": "199.00"}
        )

    assert resp.status_code == 409


async def test_pay_ignores_client_return_url() -> None:
    """A client-supplied return_url (open-redirect vector) must be ignored in
    favor of settings.YOOKASSA_RETURN_URL + order_id, mirroring checkout.py."""
    from app.config import settings

    order = _make_order(OrderState.CONFIRMED, total_rub=199)
    payment_result = {
        "confirmation_url": "https://yookassa.ru/confirm/abc",
        "payment_id": "pay_123",
        "trace_id": "trace_xyz",
    }
    app = _build_app(order, payment_result)
    payment_svc = app.dependency_overrides[get_payment_service]()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/orders/{order.id}/pay",
            json={
                "amount": "199.00",
                "return_url": "https://evil.example/post-payment",
            },
        )

    assert resp.status_code == 200
    _, kwargs = payment_svc.create_payment.call_args
    assert kwargs["return_url"] == f"{settings.YOOKASSA_RETURN_URL}?order_id={order.id}"
    assert kwargs["amount"] == Decimal(199)
