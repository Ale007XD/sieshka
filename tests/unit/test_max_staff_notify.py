"""tests/unit/test_max_staff_notify.py — keyboard builders + broadcast logic.

Mocked StaffService/MaxClient — no DB/Docker required.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

from app.domains.kitchen.fsm import KitchenState
from app.domains.orders.models import OrderState
from app.domains.staff.models import Staff, StaffRole
from app.services.max_staff_notify import (
    _fetch_order_details,
    _kitchen_keyboard,
    _order_keyboard,
    notify_admin_kitchen_ticket_state,
    notify_admin_order_state,
    notify_courier_order_state,
    notify_kitchen_ticket_state,
)


def _staff(role: StaffRole, max_user_id: int) -> Staff:
    return Staff(id=uuid.uuid4(), name="Test", role=role, max_user_id=max_user_id)


class TestKitchenKeyboard:
    def test_new_state_offers_queue_button(self) -> None:
        kb = _kitchen_keyboard("ticket-1", KitchenState.NEW)
        buttons = kb[0]["payload"]["buttons"][0]
        assert len(buttons) == 1
        payload = json.loads(buttons[0]["payload"])
        assert payload == {"kind": "kitchen", "id": "ticket-1", "event": "QUEUE"}

    def test_handed_off_terminal_state_has_no_buttons(self) -> None:
        assert _kitchen_keyboard("ticket-1", KitchenState.HANDED_OFF) == []

    def test_each_state_offers_exactly_its_graph_events(self) -> None:
        kb = _kitchen_keyboard("t1", KitchenState.PREPARING)
        buttons = kb[0]["payload"]["buttons"][0]
        events = {json.loads(b["payload"])["event"] for b in buttons}
        assert events == {"MARK_READY"}


class TestOrderKeyboard:
    def test_courier_sees_assign_courier_at_packing(self) -> None:
        kb = _order_keyboard("order-1", OrderState.PACKING, StaffRole.courier)
        buttons = kb[0]["payload"]["buttons"][0]
        events = {json.loads(b["payload"])["event"] for b in buttons}
        assert events == {"ASSIGN_COURIER"}

    def test_admin_sees_cancel_when_allowed_by_graph(self) -> None:
        kb = _order_keyboard("order-1", OrderState.CONFIRMED, StaffRole.admin)
        buttons = kb[0]["payload"]["buttons"][0]
        events = {json.loads(b["payload"])["event"] for b in buttons}
        assert events == {"CANCEL"}

    def test_courier_sees_nothing_at_cooking_state(self) -> None:
        # COOKING's only graph event is START_PACKING, not courier-permitted.
        assert _order_keyboard("order-1", OrderState.COOKING, StaffRole.courier) == []

    def test_terminal_state_has_no_buttons_for_any_role(self) -> None:
        assert _order_keyboard("order-1", OrderState.DELIVERED, StaffRole.courier) == []
        assert _order_keyboard("order-1", OrderState.CANCELLED, StaffRole.admin) == []


class TestNotifyKitchenTicketState:
    async def test_broadcasts_to_all_active_kitchen_staff(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [
            _staff(StaffRole.kitchen, 111),
            _staff(StaffRole.kitchen, 222),
        ]
        client = AsyncMock()

        await notify_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.NEW,
            staff_service=staff_service,
            client=client,
        )

        staff_service.list_active_by_role.assert_awaited_once_with(StaffRole.kitchen)
        assert client.send_message.await_count == 2
        recipients = {c.args[0] for c in client.send_message.await_args_list}
        assert recipients == {111, 222}

    async def test_skips_recipients_without_max_user_id(self) -> None:
        staff_service = AsyncMock()
        base = _staff(StaffRole.kitchen, 111)
        staff_no_max = base.model_copy(update={"max_user_id": None})
        staff_service.list_active_by_role.return_value = [staff_no_max]
        client = AsyncMock()

        await notify_kitchen_ticket_state(
            "ticket-1", "order-1", KitchenState.NEW, staff_service=staff_service, client=client
        )

        client.send_message.assert_not_awaited()

    async def test_terminal_state_never_calls_staff_lookup(self) -> None:
        staff_service = AsyncMock()
        client = AsyncMock()

        await notify_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.HANDED_OFF,
            staff_service=staff_service,
            client=client,
        )

        staff_service.list_active_by_role.assert_not_awaited()
        client.send_message.assert_not_awaited()

    async def test_staff_lookup_failure_is_swallowed(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.side_effect = RuntimeError("db down")
        client = AsyncMock()

        # Must not raise.
        await notify_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.NEW,
            staff_service=staff_service,
            client=client,
        )

        client.send_message.assert_not_awaited()

    async def test_send_failure_for_one_recipient_does_not_block_others(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [
            _staff(StaffRole.kitchen, 111),
            _staff(StaffRole.kitchen, 222),
        ]
        client = AsyncMock()
        client.send_message.side_effect = [RuntimeError("network"), "mid-ok"]

        # Must not raise despite the first recipient's send failing.
        await notify_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.NEW,
            staff_service=staff_service,
            client=client,
        )

        assert client.send_message.await_count == 2


class TestNotifyCourierOrderState:
    async def test_broadcasts_to_all_active_couriers(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [_staff(StaffRole.courier, 333)]
        client = AsyncMock()

        await notify_courier_order_state(
            "order-1", OrderState.PACKING, staff_service=staff_service, client=client
        )

        staff_service.list_active_by_role.assert_awaited_once_with(StaffRole.courier)
        client.send_message.assert_awaited_once()
        assert client.send_message.await_args.args[0] == 333

    async def test_no_actionable_state_skips_staff_lookup_entirely(self) -> None:
        staff_service = AsyncMock()
        client = AsyncMock()

        await notify_courier_order_state(
            "order-1", OrderState.DELIVERED, staff_service=staff_service, client=client
        )

        staff_service.list_active_by_role.assert_not_awaited()
        client.send_message.assert_not_awaited()


class TestNotifyAdminOrderState:
    """2026-08-08: admin is an OBSERVER role — unlike kitchen/courier, it must
    NOT no-op when there's no actionable button (see function docstring)."""

    async def test_sends_even_without_keyboard_attachments_empty(self) -> None:
        # COOKING has no admin-actionable event (CANCEL only applies
        # pre-cooking) — admin must still receive the status line.
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [_staff(StaffRole.admin, 999)]
        client = AsyncMock()

        await notify_admin_order_state(
            "order-1", OrderState.COOKING, staff_service=staff_service, client=client
        )

        staff_service.list_active_by_role.assert_awaited_once_with(StaffRole.admin)
        client.send_message.assert_awaited_once()
        assert client.send_message.await_args.args[0] == 999
        assert client.send_message.await_args.kwargs["attachments"] == []

    async def test_sends_with_cancel_keyboard_when_allowed(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [_staff(StaffRole.admin, 999)]
        client = AsyncMock()

        await notify_admin_order_state(
            "order-1", OrderState.CONFIRMED, staff_service=staff_service, client=client
        )

        kwargs = client.send_message.await_args.kwargs
        payload = json.loads(kwargs["attachments"][0]["payload"]["buttons"][0][0]["payload"])
        assert payload == {"kind": "order", "id": "order-1", "event": "CANCEL"}

    async def test_skips_recipients_without_max_user_id(self) -> None:
        staff_service = AsyncMock()
        base = _staff(StaffRole.admin, 999)
        staff_service.list_active_by_role.return_value = [
            base.model_copy(update={"max_user_id": None})
        ]
        client = AsyncMock()

        await notify_admin_order_state(
            "order-1", OrderState.COOKING, staff_service=staff_service, client=client
        )

        client.send_message.assert_not_awaited()

    async def test_staff_lookup_failure_is_swallowed(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.side_effect = RuntimeError("db down")
        client = AsyncMock()

        # Must not raise.
        await notify_admin_order_state(
            "order-1", OrderState.CONFIRMED, staff_service=staff_service, client=client
        )

        client.send_message.assert_not_awaited()


class TestNotifyAdminKitchenTicketState:
    """Informational only — no _kitchen_keyboard concept applies to admin,
    so this always attempts staff lookup/send regardless of ticket state."""

    async def test_broadcasts_with_no_attachments(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [_staff(StaffRole.admin, 999)]
        client = AsyncMock()

        await notify_admin_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.PREPARING,
            staff_service=staff_service,
            client=client,
        )

        staff_service.list_active_by_role.assert_awaited_once_with(StaffRole.admin)
        client.send_message.assert_awaited_once()
        assert client.send_message.await_args.args[0] == 999
        assert client.send_message.await_args.kwargs["attachments"] == []

    async def test_terminal_handed_off_state_still_notifies_admin(self) -> None:
        # Unlike notify_kitchen_ticket_state (no-ops on HANDED_OFF — nothing
        # actionable left for kitchen), admin still wants the final update.
        staff_service = AsyncMock()
        staff_service.list_active_by_role.return_value = [_staff(StaffRole.admin, 999)]
        client = AsyncMock()

        await notify_admin_kitchen_ticket_state(
            "ticket-1",
            "order-1",
            KitchenState.HANDED_OFF,
            staff_service=staff_service,
            client=client,
        )

        client.send_message.assert_awaited_once()

    async def test_staff_lookup_failure_is_swallowed(self) -> None:
        staff_service = AsyncMock()
        staff_service.list_active_by_role.side_effect = RuntimeError("db down")
        client = AsyncMock()

        await notify_admin_kitchen_ticket_state(
            "ticket-1", "order-1", KitchenState.NEW, staff_service=staff_service, client=client
        )

        client.send_message.assert_not_awaited()


class _FakeRow:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping


class _FakeResult:
    def __init__(self, row: _FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> _FakeRow | None:
        return self._row


class _FakeSession:
    def __init__(self, row: _FakeRow | None) -> None:
        self._row = row

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        return _FakeResult(self._row)


class TestFetchOrderDetails:
    """2026-08-08: composition/address/phone/comment/payment-status block
    shared by all three notify_* functions. Self-contained DB access (see
    module docstring) — mocked here via a fake async_session_factory rather
    than a real Postgres connection."""

    def _patch_factory(
        self, monkeypatch: object, row: _FakeRow | None
    ) -> None:  # pragma: no cover - typed via pytest below
        import app.services.max_staff_notify as module

        monkeypatch.setattr(module, "async_session_factory", lambda: _FakeSession(row))

    async def test_formats_composition_address_comment(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        row = _FakeRow(
            {
                "items": [
                    {"name": "Кола 1.5 л", "qty": 1},
                    {"name": "Пицца Маргарита", "qty": 2},
                ],
                "delivery_address": "ул. Ленина 5, кв 10",
                "comment": "Домофон не работает",
                "payment_method": "cash",
                "order_state": "COOKING",
                "customer_phone": "+79991234567",
            }
        )
        self._patch_factory(monkeypatch, row)

        details = await _fetch_order_details("order-1")

        assert "Кола 1.5 л x1" in details
        assert "Пицца Маргарита x2" in details
        assert "ул. Ленина 5, кв 10" in details
        assert "+79991234567" in details
        assert "Домофон не работает" in details
        assert "Наличные" in details

    async def test_yookassa_paid_state_shows_paid(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        row = _FakeRow(
            {
                "items": [],
                "delivery_address": None,
                "comment": None,
                "payment_method": "yookassa_card",
                "order_state": "COOKING",
                "customer_phone": None,
            }
        )
        self._patch_factory(monkeypatch, row)

        details = await _fetch_order_details("order-1")

        assert "оплачено" in details

    async def test_yookassa_payment_pending_state_shows_pending(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        row = _FakeRow(
            {
                "items": [],
                "delivery_address": None,
                "comment": None,
                "payment_method": "yookassa_card",
                "order_state": "PAYMENT_PENDING",
                "customer_phone": None,
            }
        )
        self._patch_factory(monkeypatch, row)

        details = await _fetch_order_details("order-1")

        assert "ожидает оплаты" in details

    async def test_missing_row_returns_empty_string(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        self._patch_factory(monkeypatch, None)

        details = await _fetch_order_details("order-1")

        assert details == ""

    async def test_query_failure_returns_empty_string_not_raise(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import app.services.max_staff_notify as module

        def _boom() -> object:
            raise RuntimeError("connection refused")

        monkeypatch.setattr(module, "async_session_factory", _boom)

        details = await _fetch_order_details("order-1")

        assert details == ""
