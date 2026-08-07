"""tests/unit/test_notification_tools.py — notify_staff_new_kitchen_ticket tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.domains.kitchen.fsm import KitchenState
from app.tools.notification_tools import notify_staff_new_kitchen_ticket


class TestNotifyStaffNewKitchenTicket:
    async def test_calls_notify_kitchen_ticket_state_with_new_state(self) -> None:
        mock_notify = AsyncMock()
        with patch(
            "app.services.max_staff_notify.notify_kitchen_ticket_state", mock_notify
        ):
            result = await notify_staff_new_kitchen_ticket(
                order_id="order-1", ticket_id="ticket-1"
            )

        assert result == "NOTIFIED"
        mock_notify.assert_awaited_once_with(
            ticket_id="ticket-1", order_id="order-1", state=KitchenState.NEW
        )

    async def test_never_raises_even_if_notify_fails(self) -> None:
        """Governed Program terminal-tool contract: this must not raise, or a
        MAX notification failure would fail the whole Trace and roll back the
        already-committed order->COOKING transition (see order_service.py::
        transition_order — session.commit() only runs on TraceStatus.SUCCESS).
        notify_kitchen_ticket_state itself never raises (tested separately in
        test_max_staff_notify.py); this asserts the tool-level contract holds
        even if that internal guarantee were ever violated by a future edit.
        """
        with patch(
            "app.services.max_staff_notify.notify_kitchen_ticket_state",
            AsyncMock(side_effect=RuntimeError("should never propagate")),
        ):
            result = await notify_staff_new_kitchen_ticket(
                order_id="order-1", ticket_id="ticket-1"
            )

        # NOTE: this documents current behavior — the underlying import is
        # bound at call time inside notify_staff_new_kitchen_ticket via a
        # local import, so patching app.services.max_staff_notify's module-
        # level name IS the effective patch target (not a re-imported alias).
        assert result == "NOTIFIED"
