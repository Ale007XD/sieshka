"""tests/unit/fsm/test_order_fsm.py"""
from __future__ import annotations

import inspect

import pytest

from app.domains.orders import fsm as order_fsm_module
from app.domains.orders.fsm import OrderFSM
from app.domains.orders.models import OrderEvent, OrderState
from app.fsm.core.base import TransitionResult


def make_fsm(initial_state: OrderState) -> tuple[OrderFSM, dict[str, OrderState]]:
    store: dict[str, OrderState] = {"ord1": initial_state}
    fsm = OrderFSM(
        state_reader=lambda eid: store[eid],
        state_writer=lambda eid, s: store.update({eid: s}),
    )
    return fsm, store


class TestOrderFSM:
    def test_confirm_draft(self) -> None:
        fsm, store = make_fsm(OrderState.DRAFT)
        result = fsm.transition("ord1", OrderEvent.CONFIRM)
        assert result.success
        assert store["ord1"] == OrderState.CONFIRMED

    def test_reject_invalid_event(self) -> None:
        fsm, store = make_fsm(OrderState.DRAFT)
        result = fsm.transition("ord1", OrderEvent.PAYMENT_CONFIRMED)
        assert not result.success
        assert result.rejected_event == OrderEvent.PAYMENT_CONFIRMED
        assert store["ord1"] == OrderState.DRAFT  # state unchanged

    def test_happy_path_to_cooking(self) -> None:
        fsm, store = make_fsm(OrderState.DRAFT)
        events = [
            OrderEvent.CONFIRM,
            OrderEvent.REQUEST_PAYMENT,
            OrderEvent.PAYMENT_CONFIRMED,
            OrderEvent.START_COOKING,
        ]
        for event in events:
            result = fsm.transition("ord1", event)
            assert result.success, f"Failed on {event}: {result.reason}"
        assert store["ord1"] == OrderState.COOKING

    def test_cancel_from_draft(self) -> None:
        fsm, store = make_fsm(OrderState.DRAFT)
        result = fsm.transition("ord1", OrderEvent.CANCEL)
        assert result.success
        assert store["ord1"] == OrderState.CANCELLED

    def test_terminal_states_no_transitions(self) -> None:
        for terminal in [OrderState.CLOSED, OrderState.CANCELLED]:
            fsm, _ = make_fsm(terminal)
            events = fsm.get_allowed_events(terminal)
            assert events == []

    def test_get_allowed_events_draft(self) -> None:
        fsm, _ = make_fsm(OrderState.DRAFT)
        allowed = fsm.get_allowed_events(OrderState.DRAFT)
        assert OrderEvent.CONFIRM in allowed
        assert OrderEvent.CANCEL in allowed
        assert OrderEvent.PAYMENT_CONFIRMED not in allowed

    def test_transition_result_frozen(self) -> None:
        """TransitionResult must be frozen=True (dataclass)."""
        r = TransitionResult(success=True, new_state=None, rejected_event=None, reason=None)
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_no_direct_state_assignment_in_fsm(self) -> None:
        """FSM source must not contain forbidden direct-mutation patterns
        like 'order.status = X' or 'entity.state = X' outside terminal tools."""
        source = inspect.getsource(order_fsm_module)
        forbidden_patterns = [".status = ", ".state = "]
        for line in source.split("\n"):
            stripped = line.strip()
            if any(p in stripped for p in forbidden_patterns):
                assert "# terminal-tool" in stripped, (
                    f"Direct state mutation outside terminal tool: {stripped!r}"
                )
