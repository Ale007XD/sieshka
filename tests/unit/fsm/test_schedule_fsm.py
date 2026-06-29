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

    async def reader(entity_id: str) -> BusinessScheduleState:
        return store[entity_id]

    async def writer(entity_id: str, state: BusinessScheduleState) -> None:
        store[entity_id] = state

    return BusinessScheduleFSM(state_reader=reader, state_writer=writer), store


class TestScheduleFSMInit:
    async def test_get_current_state_open(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        assert await fsm.get_current_state("schedule") == BusinessScheduleState.OPEN

    async def test_get_current_state_closing_soon(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSING_SOON)
        assert await fsm.get_current_state("schedule") == BusinessScheduleState.CLOSING_SOON

    async def test_get_current_state_closed(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.CLOSED)
        assert await fsm.get_current_state("schedule") == BusinessScheduleState.CLOSED


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
    async def test_closing_warning_to_closing_soon(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSING_SOON
        assert result.rejected_event is None
        assert result.reason is None
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    async def test_state_persisted_after_transition(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert await fsm.get_current_state("schedule") == BusinessScheduleState.CLOSING_SOON


class TestTransitionsFromClosingSoon:
    async def test_close_to_closed(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSED
        assert store["schedule"] == BusinessScheduleState.CLOSED


class TestTransitionsFromClosed:
    async def test_open_to_open(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = await fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.OPEN
        assert store["schedule"] == BusinessScheduleState.OPEN


class TestRejectedTransitions:
    async def test_open_close_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == BusinessScheduleEvent.CLOSE
        assert store["schedule"] == BusinessScheduleState.OPEN

    async def test_open_open_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.OPEN

    async def test_closing_soon_closing_warning_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    async def test_closing_soon_open_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSING_SOON)
        result = await fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    async def test_closed_closing_warning_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSED

    async def test_closed_close_rejected(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.CLOSED)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.CLOSED

    async def test_rejection_reason_contains_event_name(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.reason is not None
        assert "CLOSE" in result.reason

    async def test_rejection_reason_contains_state_name(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert result.reason is not None
        assert "OPEN" in result.reason


class TestTransitionResultShape:
    async def test_success_result_fields(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state is not None
        assert result.rejected_event is None
        assert result.reason is None

    async def test_failure_result_fields(self) -> None:
        fsm, _ = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event is not None
        assert result.reason is not None


class TestHandleEventAlias:
    async def test_handle_event_delegates_to_transition(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.handle_event("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert result.success is True
        assert result.new_state == BusinessScheduleState.CLOSING_SOON
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON

    async def test_handle_event_rejection(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        result = await fsm.handle_event("schedule", BusinessScheduleEvent.CLOSE)
        assert result.success is False
        assert store["schedule"] == BusinessScheduleState.OPEN


class TestChainedTransitions:
    async def test_cyclic_path_returns_to_open(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)

        r = await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert r.success
        assert r.new_state == BusinessScheduleState.CLOSING_SOON

        r = await fsm.transition("schedule", BusinessScheduleEvent.CLOSE)
        assert r.success
        assert r.new_state == BusinessScheduleState.CLOSED

        r = await fsm.transition("schedule", BusinessScheduleEvent.OPEN)
        assert r.success
        assert r.new_state == BusinessScheduleState.OPEN

    async def test_rejected_transition_does_not_advance_state(self) -> None:
        fsm, store = make_fsm(BusinessScheduleState.OPEN)
        await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        await fsm.transition("schedule", BusinessScheduleEvent.CLOSING_WARNING)
        assert store["schedule"] == BusinessScheduleState.CLOSING_SOON


class TestMultipleEntities:
    async def test_two_entities_independent(self) -> None:
        store: dict[str, BusinessScheduleState] = {
            "venue-a": BusinessScheduleState.OPEN,
            "venue-b": BusinessScheduleState.CLOSED,
        }

        async def reader(entity_id: str) -> BusinessScheduleState:
            return store[entity_id]

        async def writer(entity_id: str, state: BusinessScheduleState) -> None:
            store[entity_id] = state

        fsm = BusinessScheduleFSM(state_reader=reader, state_writer=writer)

        await fsm.transition("venue-a", BusinessScheduleEvent.CLOSING_WARNING)
        assert store["venue-a"] == BusinessScheduleState.CLOSING_SOON
        assert store["venue-b"] == BusinessScheduleState.CLOSED

        r = await fsm.transition("venue-b", BusinessScheduleEvent.OPEN)
        assert r.success
        assert r.new_state == BusinessScheduleState.OPEN
        assert store["venue-a"] == BusinessScheduleState.CLOSING_SOON
