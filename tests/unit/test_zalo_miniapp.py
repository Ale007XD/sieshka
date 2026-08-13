"""tests/unit/test_zalo_miniapp.py — Zalo Mini App staff-action endpoints.

Mocked KitchenService/OrderService, auth dependency overridden directly to a
fixed Staff (auth mechanics themselves are covered by test_zalo_auth.py) —
this file is purely about role-gated dispatch parity with MAX
(test_max_webhook.py), now sharing app.services.staff_dispatch.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.zalo_miniapp import (
    get_kitchen_service,
    get_order_service,
)
from app.api.routes.zalo_miniapp import router as zalo_router
from app.domains.kitchen.fsm import KitchenState
from app.domains.orders.models import OrderState
from app.domains.staff.models import Staff, StaffRole
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.order_service import OrderService
from app.web.zalo_auth import get_current_zalo_staff


def _staff(role: StaffRole) -> Staff:
    return Staff(id=uuid.uuid4(), name="Test", role=role, zalo_user_id="zalo-uid-1")


class _Mocks:
    def __init__(self) -> None:
        self.kitchen = AsyncMock(spec=KitchenService)
        self.orders = AsyncMock(spec=OrderService)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


def _client_as(role: StaffRole, mocks: _Mocks) -> AsyncClient:
    app = FastAPI()
    app.include_router(zalo_router)
    app.dependency_overrides[get_current_zalo_staff] = lambda: _staff(role)
    app.dependency_overrides[get_kitchen_service] = lambda: mocks.kitchen
    app.dependency_overrides[get_order_service] = lambda: mocks.orders
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestKitchenTransition:
    async def test_kitchen_role_allowed(self, mocks: _Mocks) -> None:
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=True, new_state=KitchenState.PREPARING, rejected_event=None, reason=None
        )
        async with _client_as(StaffRole.kitchen, mocks) as client:
            resp = await client.post(
                "/api/zalo/kitchen/ticket-1/transition", json={"event": "START_PREP"}
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_courier_role_denied_for_kitchen(self, mocks: _Mocks) -> None:
        async with _client_as(StaffRole.courier, mocks) as client:
            resp = await client.post(
                "/api/zalo/kitchen/ticket-1/transition", json={"event": "START_PREP"}
            )
        assert resp.status_code == 403
        mocks.kitchen.transition_ticket.assert_not_awaited()

    async def test_staff_role_allowed_for_kitchen(self, mocks: _Mocks) -> None:
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=True, new_state=KitchenState.READY, rejected_event=None, reason=None
        )
        async with _client_as(StaffRole.staff, mocks) as client:
            resp = await client.post(
                "/api/zalo/kitchen/ticket-1/transition", json={"event": "MARK_READY"}
            )
        assert resp.status_code == 200

    async def test_unknown_event_denied(self, mocks: _Mocks) -> None:
        async with _client_as(StaffRole.kitchen, mocks) as client:
            resp = await client.post(
                "/api/zalo/kitchen/ticket-1/transition", json={"event": "NOT_A_REAL_EVENT"}
            )
        assert resp.status_code == 403


class TestOrderTransition:
    async def test_admin_role_allowed_cancel(self, mocks: _Mocks) -> None:
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True, new_state=OrderState.CANCELLED, rejected_event=None, reason=None
        )
        async with _client_as(StaffRole.admin, mocks) as client:
            resp = await client.post(
                "/api/zalo/orders/order-1/transition", json={"event": "CANCEL"}
            )
        assert resp.status_code == 200

    async def test_kitchen_role_denied_for_order(self, mocks: _Mocks) -> None:
        async with _client_as(StaffRole.kitchen, mocks) as client:
            resp = await client.post(
                "/api/zalo/orders/order-1/transition", json={"event": "CANCEL"}
            )
        assert resp.status_code == 403
        mocks.orders.transition_order.assert_not_awaited()

    async def test_courier_role_allowed_pickup(self, mocks: _Mocks) -> None:
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True, new_state=OrderState.DELIVERING, rejected_event=None, reason=None
        )
        async with _client_as(StaffRole.courier, mocks) as client:
            resp = await client.post(
                "/api/zalo/orders/order-1/transition", json={"event": "PICKUP"}
            )
        assert resp.status_code == 200

    async def test_courier_role_denied_cancel(self, mocks: _Mocks) -> None:
        async with _client_as(StaffRole.courier, mocks) as client:
            resp = await client.post(
                "/api/zalo/orders/order-1/transition", json={"event": "CANCEL"}
            )
        assert resp.status_code == 403
