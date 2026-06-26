from __future__ import annotations

from app.domains.schedule.fsm import (
    BusinessScheduleEvent,
    BusinessScheduleFSM,
    BusinessScheduleState,
)
from app.fsm.core.base import TransitionResult


def make_fsm(
    initial_state: BusinessScheduleState,
) -> tuple[BusinessScheduleFSM, dict[str, BusinessScheduleState]]:
    store: dict[str, BusinessScheduleState] = {"schedule": initial_state}

    def reader(entity_id: str) -> BusinessScheduleState:
        return store[entity_id]

    def writer(entity_id: str, state: BusinessScheduleState) -> None:
        store[entity_id] = state

    return BusinessScheduleFSM(state_reader=reader, state_writer=writer), store


class TestScheduleFSMInit:
    def test_get_current_state_open(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        assert fsm.get_current_state("schedule") == BusinessScheduleState.OPEN

    def test_get_current_state_closing_soon(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSING_SOON)
        assert fsm.get_current_state("schedule") == BusinessScheduleState.CLOSING_SOON

    def test_get_current_state_closed(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSED)
        assert fsm.get_current_state("schedule") == BusinessScheduleState.CLOSED


class TestGetAllowedEvents:
    def test_open_allowed_events(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        allowed = fsm.get_allowed_events(BusinessScheduleState.OPEN)
        assert allowed == [BusinessScheduleEvent.CLOSING_WARNING]

    def test_closing_soon_allowed_events(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSING_SOON)
        allowed = fsm.get_allowed_events(BusinessScheduleState.CLOSING_SOON)
        assert allowed == [BusinessScheduleEvent.CLOSE]

    def test_closed_allowed_events(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSED)
        allowed = fsm.get_allowed_events(BusinessScheduleState.CLOSED)
        assert allowed == [BusinessScheduleEvent.OPEN]

    def test_returns_list(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.get_allowed_events(BusinessScheduleState.OPEN)
        assert isinstance(result, list)


class TestTransitionsFromOpen:
    def test_closing_warning_to_closing_soon(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSING_SOON
        assert result.rejected_event is None
        assert result.reason is None
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    def test_state_persisted_after_transition(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert fsm.get_current_state("schedule") == BusinessScheduleState.CLOSING_SOON


class TestTransitionsFromClosingSoon:
    def test_close_to_closed(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSED
        assert store["schedule"] == BusinessScheduleState.CLOSED


class TestTransitionsFromClosed:
    def test_open_to_open(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.OPEN
        assert store["schedule"] == BusinessScheduleState.OPEN


class TestRejectedTransitions:
    def test_open_close_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == BusinessScheduleEvent.CLOSE
        assert store["schedule"] == BusinessScheduleState.OPEN

    def test_open_open_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.OPEN

    def test_closing_soon_closing_warning_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    def test_closing_soon_open_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    def test_closed_closing_warning_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSED

    def test_closed_close_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSED

    def test_rejection_reason_contains_event_name(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.reason is not None
        assert "CLOSE" in result.reason

    def test_rejection_reason_contains_state_name(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.reason is not None
        assert "OPEN" in result.reason


class TestTransitionResultShape:
    def test_success_result_fields(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state is not None
        assert result.rejected_event is None
        assert result.reason is None

    def test_failure_result_fields(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event is not None
        assert result.reason is not None


class TestHandleEventAlias:
    def test_handle_event_delegates_to_transition(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.handle_event("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSING_SOON
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    def test_handle_event_rejection(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = fsm.handle_event("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.OPEN


class TestChainedTransitions:
    def test_cyclic_path_returns_to_open(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)

        fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

        fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert store["schedule"] == BusinessScheduleState.CLOSED

        fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert store["schedule"] == BusinessScheduleState.OPEN

    def test_rejected_transition_does_not_advance_state(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON


class TestMultipleEntities:
    def test_two_entities_independent(self) -> None:
        store: dict[str, BusinessScheduleState] = {
            "venue-a": BusinessScheduleState.OPEN,
            "venue-b": BusinessScheduleState.CLOSED,
        }

        def reader(entity_id: str) -> BusinessScheduleState:
            return store[entity_id]

        def writer(entity_id: str, state: BusinessScheduleState) -> None:
            store[entity_id] = state

        fsm = BusinessScheduleFSM(state_reader=reader, state_writer=writer)

        fsm.transition("venue-a", BusinessScheduleEvent.CLOSING_WARNING)
        assert store["venue-a"] == BusinessScheduleState.CLOSING_SOON
        assert store["venue-b"] == BusinessScheduleState.CLOSED

        fsm.transition("venue-b", BusinessScheduleEvent.OPEN)
        assert store["venue-b"] == BusinessScheduleState.OPEN
        assert store["venue-a"] == BusinessScheduleState.CLOSING_SOON
