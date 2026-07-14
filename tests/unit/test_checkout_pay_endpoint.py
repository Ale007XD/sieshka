"""tests/unit/test_checkout_pay_endpoint.py — POST /orders/{id}/pay wiring.

Unit-level: no Docker. Mocks OrderService.get_order and PaymentService.create_payment
via FastAPI dependency overrides, exercising the customer-facing outbound payment path.
"""
from __future__ import annotations

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


def _make_order(state: OrderState) -> OrderRead:
    return OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=state,
        items=[],
        delivery_address="Moscow, Red Square 1",
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
