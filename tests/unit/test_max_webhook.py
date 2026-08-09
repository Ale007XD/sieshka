"""tests/unit/test_max_webhook.py — MAX webhook role-gated dispatch, mocked services.

No DB/Docker required — StaffService/KitchenService/OrderService/MaxClient are
all mocked via dependency_overrides. Behavioral coverage for the role_gate ->
governed-transition dispatch belongs here; a real end-to-end run against
Postgres is out of scope for this sprint (mirrors the transport-only /
dispatch-only split already used for test_max_client.py).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.domains.kitchen.fsm import KitchenState
from app.domains.orders.models import OrderState
from app.domains.staff.models import Staff, StaffRole
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.max_client import MaxClient
from app.services.order_service import OrderService
from app.services.staff_service import StaffService
from app.webhooks.max import (
    get_kitchen_service,
    get_max_client,
    get_order_service,
    get_staff_service,
)
from app.webhooks.max import router as max_router


def _staff(role: StaffRole, max_user_id: int = 111) -> Staff:
    return Staff(id=uuid.uuid4(), name="Test", role=role, max_user_id=max_user_id)


class _Mocks:
    def __init__(self) -> None:
        self.staff = AsyncMock(spec=StaffService)
        # Chain-notify (sprint_max_staff_notify) calls list_active_by_role
        # after every successful dispatch; default to "no staff registered
        # yet" so existing dispatch-focused tests don't need to care about it.
        self.staff.list_active_by_role = AsyncMock(return_value=[])
        self.kitchen = AsyncMock(spec=KitchenService)
        self.kitchen.get_order_id = AsyncMock(return_value="order-1")
        self.orders = AsyncMock(spec=OrderService)
        self.max_client = AsyncMock(spec=MaxClient)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


@pytest.fixture
async def client(mocks: _Mocks) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(max_router)
    app.dependency_overrides[get_staff_service] = lambda: mocks.staff
    app.dependency_overrides[get_kitchen_service] = lambda: mocks.kitchen
    app.dependency_overrides[get_order_service] = lambda: mocks.orders
    app.dependency_overrides[get_max_client] = lambda: mocks.max_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _callback_body(
    *,
    max_user_id: int = 111,
    payload: dict[str, object] | None,
    callback_id: str = "cb-1",
) -> dict[str, object]:
    return {
        "update_type": "message_callback",
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": max_user_id},
            "payload": json.dumps(payload) if payload is not None else None,
        },
    }


class TestWebhookSecret:
    async def test_wrong_secret_returns_403(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "correct-secret")
        resp = await client.post(
            "/webhooks/max",
            json={"update_type": "bot_started"},
            headers={"X-Max-Bot-Api-Secret": "wrong"},
        )
        assert resp.status_code == 403

    async def test_correct_secret_proceeds(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "correct-secret")
        resp = await client.post(
            "/webhooks/max",
            json={"update_type": "bot_started"},
            headers={"X-Max-Bot-Api-Secret": "correct-secret"},
        )
        assert resp.status_code == 200

    async def test_no_secret_configured_skips_check(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_WEBHOOK_SECRET", "")
        resp = await client.post("/webhooks/max", json={"update_type": "bot_started"})
        assert resp.status_code == 200


class TestWebhookIgnoredUpdateTypes:
    async def test_bot_started_acknowledged_no_service_calls(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        resp = await client.post("/webhooks/max", json={"update_type": "bot_started"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mocks.staff.find_by_max_user_id.assert_not_awaited()

    async def test_message_created_acknowledged(self, client: AsyncClient) -> None:
        resp = await client.post("/webhooks/max", json={"update_type": "message_created"})
        assert resp.status_code == 200

    async def test_unknown_update_type_ignored(self, client: AsyncClient) -> None:
        resp = await client.post("/webhooks/max", json={"update_type": "something_else"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "ignored": True}

    async def test_invalid_json_body_returns_200(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/webhooks/max",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestWebhookAcl:
    async def test_unknown_staff_denied(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.staff.find_by_max_user_id.return_value = None

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "kitchen", "id": "t1", "event": "QUEUE"}),
        )

        assert resp.status_code == 200
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Доступ запрещён"
        )
        mocks.kitchen.transition_ticket.assert_not_awaited()

    async def test_invalid_payload_json_denied(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)

        resp = await client.post(
            "/webhooks/max",
            json={
                "update_type": "message_callback",
                "callback": {
                    "callback_id": "cb-1",
                    "user": {"user_id": 111},
                    "payload": "not-json{{",
                },
            },
        )

        assert resp.status_code == 200
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Некорректные данные кнопки"
        )

    async def test_incomplete_payload_denied(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "kitchen"}),  # missing id/event
        )

        assert resp.status_code == 200
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Недостаточно данных"
        )


class TestKitchenDispatch:
    async def test_kitchen_role_allowed_event_dispatches_and_answers_success(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=True, new_state=KitchenState.QUEUED, rejected_event=None, reason=None
        )

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "QUEUE"}
            ),
        )

        assert resp.status_code == 200
        mocks.kitchen.transition_ticket.assert_awaited_once()
        args = mocks.kitchen.transition_ticket.await_args.args
        assert args[0] == "ticket-1"
        assert args[1].value == "QUEUE"
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="✅ QUEUED"
        )

    async def test_kitchen_rejected_transition_answers_failure_reason(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=False,
            new_state=None,
            rejected_event=None,
            reason="ticket not in QUEUED state",
        )

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "START_PREP"}
            ),
        )

        assert resp.status_code == 200
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="❌ ticket not in QUEUED state"
        )

    async def test_non_kitchen_role_denied_kitchen_event(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.courier)

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "QUEUE"}
            ),
        )

        assert resp.status_code == 200
        mocks.kitchen.transition_ticket.assert_not_awaited()
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Недопустимое действие для вашей роли"
        )

    async def test_unknown_kitchen_event_string_denied(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "NOT_A_REAL_EVENT"}
            ),
        )

        assert resp.status_code == 200
        mocks.kitchen.transition_ticket.assert_not_awaited()
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Недопустимое действие для вашей роли"
        )


class TestOrderDispatch:
    async def test_courier_allowed_event_dispatches(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.courier)
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True,
            new_state=OrderState.DELIVERING,
            rejected_event=None,
            reason=None,
        )

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "order", "id": "order-1", "event": "PICKUP"}),
        )

        assert resp.status_code == 200
        mocks.orders.transition_order.assert_awaited_once()
        args = mocks.orders.transition_order.await_args.args
        assert args[0] == "order-1"
        assert args[1].value == "PICKUP"
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="✅ DELIVERING"
        )

    async def test_courier_denied_cancel(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.courier)

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "order", "id": "order-1", "event": "CANCEL"}),
        )

        assert resp.status_code == 200
        mocks.orders.transition_order.assert_not_awaited()
        mocks.max_client.answer_callback.assert_awaited_once_with(
            "cb-1", notification="Недопустимое действие для вашей роли"
        )

    async def test_admin_allowed_cancel(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.admin)
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True, new_state=OrderState.CANCELLED, rejected_event=None, reason=None
        )

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "order", "id": "order-1", "event": "CANCEL"}),
        )

        assert resp.status_code == 200
        mocks.orders.transition_order.assert_awaited_once()

    async def test_admin_denied_pickup(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.admin)

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "order", "id": "order-1", "event": "PICKUP"}),
        )

        assert resp.status_code == 200
        mocks.orders.transition_order.assert_not_awaited()


class TestChainNotify:
    """sprint_max_staff_notify: after a successful dispatch, the webhook sends
    the NEXT allowed step's notification to the relevant role (no message-
    editing in v1 — see app/services/max_staff_notify.py docstring)."""

    async def test_kitchen_success_chain_notifies_next_stage(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=True, new_state=KitchenState.QUEUED, rejected_event=None, reason=None
        )
        mocks.kitchen.get_order_id.return_value = "order-1"
        # 2026-08-08: admin now also observes kitchen-ticket progress
        # (notify_admin_kitchen_ticket_state, informational, no keyboard) —
        # role-aware side_effect so kitchen and admin recipients are
        # distinguishable in the assertions below.
        mocks.staff.list_active_by_role = AsyncMock(
            side_effect=lambda role: (
                [_staff(StaffRole.kitchen, max_user_id=222)]
                if role == StaffRole.kitchen
                else [_staff(StaffRole.admin, max_user_id=999)]
            )
        )
        mock_send = AsyncMock(return_value="mid-x")
        mocks.max_client.send_message = mock_send

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "QUEUE"}
            ),
        )

        assert resp.status_code == 200
        mocks.kitchen.get_order_id.assert_awaited_once_with("ticket-1")
        assert mock_send.await_count == 2
        recipients = {call.args[0] for call in mock_send.await_args_list}
        assert recipients == {222, 999}
        kitchen_call = next(c for c in mock_send.await_args_list if c.args[0] == 222)
        # next button should be START_PREP (allowed from QUEUED)
        payload = json.loads(
            kitchen_call.kwargs["attachments"][0]["payload"]["buttons"][0][0]["payload"]
        )
        assert payload == {"kind": "kitchen", "id": "ticket-1", "event": "START_PREP"}
        admin_call = next(c for c in mock_send.await_args_list if c.args[0] == 999)
        # admin's kitchen-progress card is informational — no buttons at all
        assert admin_call.kwargs["attachments"] == []

    async def test_kitchen_handed_off_no_further_chain_notify(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=True, new_state=KitchenState.HANDED_OFF, rejected_event=None, reason=None
        )
        mock_send = AsyncMock()
        mocks.max_client.send_message = mock_send

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "HAND_OFF"}
            ),
        )

        assert resp.status_code == 200
        mock_send.assert_not_awaited()

    async def test_courier_success_chain_notifies_next_stage(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.courier)
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True,
            new_state=OrderState.COURIER_ASSIGNED,
            rejected_event=None,
            reason=None,
        )
        # 2026-08-08: admin now also observes order-level progress
        # (notify_admin_order_state no longer no-ops on an empty keyboard —
        # COURIER_ASSIGNED has no admin-actionable button, but admin still
        # gets the status line). Role-aware side_effect distinguishes the
        # two recipients in the assertions below.
        mocks.staff.list_active_by_role = AsyncMock(
            side_effect=lambda role: (
                [_staff(StaffRole.courier, max_user_id=333)]
                if role == StaffRole.courier
                else [_staff(StaffRole.admin, max_user_id=999)]
            )
        )
        mock_send = AsyncMock(return_value="mid-y")
        mocks.max_client.send_message = mock_send

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "order", "id": "order-1", "event": "ASSIGN_COURIER"}
            ),
        )

        assert resp.status_code == 200
        assert mock_send.await_count == 2
        recipients = {call.args[0] for call in mock_send.await_args_list}
        assert recipients == {333, 999}
        courier_call = next(c for c in mock_send.await_args_list if c.args[0] == 333)
        payload = json.loads(
            courier_call.kwargs["attachments"][0]["payload"]["buttons"][0][0]["payload"]
        )
        assert payload == {"kind": "order", "id": "order-1", "event": "PICKUP"}
        admin_call = next(c for c in mock_send.await_args_list if c.args[0] == 999)
        # COURIER_ASSIGNED has no admin-actionable event (CANCEL only, and
        # only pre-cooking) — admin still gets the status line, no keyboard.
        assert admin_call.kwargs["attachments"] == []

    async def test_admin_cancel_triggers_no_chain_notify(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.admin)
        mocks.orders.transition_order.return_value = TransitionResult(
            success=True, new_state=OrderState.CANCELLED, rejected_event=None, reason=None
        )
        mock_send = AsyncMock()
        mocks.max_client.send_message = mock_send

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(payload={"kind": "order", "id": "order-1", "event": "CANCEL"}),
        )

        assert resp.status_code == 200
        mock_send.assert_not_awaited()

    async def test_rejected_transition_triggers_no_chain_notify(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_max_user_id.return_value = _staff(StaffRole.kitchen)
        mocks.kitchen.transition_ticket.return_value = TransitionResult(
            success=False, new_state=None, rejected_event=None, reason="not allowed"
        )
        mock_send = AsyncMock()
        mocks.max_client.send_message = mock_send

        resp = await client.post(
            "/webhooks/max",
            json=_callback_body(
                payload={"kind": "kitchen", "id": "ticket-1", "event": "START_PREP"}
            ),
        )

        assert resp.status_code == 200
        mock_send.assert_not_awaited()
