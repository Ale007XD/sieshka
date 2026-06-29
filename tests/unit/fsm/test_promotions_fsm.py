from __future__ import annotations

from app.domains.promotions.fsm import PromotionFSM
from app.domains.promotions.models import PromotionEvent, PromotionState
from app.fsm.core.base import TransitionResult


def make_fsm(initial_state: PromotionState) -> tuple[PromotionFSM, dict[str, PromotionState]]:
    store: dict[str, PromotionState] = {"promo": initial_state}

    async def reader(entity_id: str) -> PromotionState:
        return store[entity_id]

    async def writer(entity_id: str, state: PromotionState) -> None:
        store[entity_id] = state

    return PromotionFSM(state_reader=reader, state_writer=writer), store


class TestPromotionFSMInit:
    async def test_get_current_state_created(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        assert await fsm.get_current_state("promo") == PromotionState.CREATED

    async def test_get_current_state_active(self) -> None:
        fsm, _ = make_fsm(PromotionState.ACTIVE)
        assert await fsm.get_current_state("promo") == PromotionState.ACTIVE

    async def test_get_current_state_expired(self) -> None:
        fsm, _ = make_fsm(PromotionState.EXPIRED)
        assert await fsm.get_current_state("promo") == PromotionState.EXPIRED

    async def test_get_current_state_archived(self) -> None:
        fsm, _ = make_fsm(PromotionState.ARCHIVED)
        assert await fsm.get_current_state("promo") == PromotionState.ARCHIVED


class TestGetAllowedEvents:
    def test_created_allowed_events(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        allowed = fsm.get_allowed_events(PromotionState.CREATED)
        assert allowed == [PromotionEvent.ACTIVATE]

    def test_active_allowed_events(self) -> None:
        fsm, _ = make_fsm(PromotionState.ACTIVE)
        allowed = fsm.get_allowed_events(PromotionState.ACTIVE)
        assert allowed == [PromotionEvent.EXPIRE]

    def test_expired_allowed_events(self) -> None:
        fsm, _ = make_fsm(PromotionState.EXPIRED)
        allowed = fsm.get_allowed_events(PromotionState.EXPIRED)
        assert allowed == [PromotionEvent.ARCHIVE]

    def test_archived_allowed_events_empty(self) -> None:
        fsm, _ = make_fsm(PromotionState.ARCHIVED)
        allowed = fsm.get_allowed_events(PromotionState.ARCHIVED)
        assert allowed == []

    def test_returns_list(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        result = fsm.get_allowed_events(PromotionState.CREATED)
        assert isinstance(result, list)


class TestTransitionsFromCreated:
    async def test_activate_to_active(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert result.success is True
        assert result.new_state == PromotionState.ACTIVE
        assert result.rejected_event is None
        assert result.reason is None
        assert store["promo"] == PromotionState.ACTIVE

    async def test_state_persisted_after_transition(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert await fsm.get_current_state("promo") == PromotionState.ACTIVE


class TestTransitionsFromActive:
    async def test_expire_to_expired(self) -> None:
        fsm, store = make_fsm(PromotionState.ACTIVE)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert result.success is True
        assert result.new_state == PromotionState.EXPIRED
        assert store["promo"] == PromotionState.EXPIRED


class TestTransitionsFromExpired:
    async def test_archive_to_archived(self) -> None:
        fsm, store = make_fsm(PromotionState.EXPIRED)
        result = await fsm.transition("promo", PromotionEvent.ARCHIVE)
        assert result.success is True
        assert result.new_state == PromotionState.ARCHIVED
        assert store["promo"] == PromotionState.ARCHIVED


class TestTransitionsFromArchived:
    async def test_no_outgoing_transitions(self) -> None:
        fsm, store = make_fsm(PromotionState.ARCHIVED)
        for event in PromotionEvent:
            result = await fsm.transition("promo", event)
            assert result.success is False
            assert result.new_state is None
            assert store["promo"] == PromotionState.ARCHIVED


class TestRejectedTransitions:
    async def test_created_expire_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == PromotionEvent.EXPIRE
        assert store["promo"] == PromotionState.CREATED

    async def test_created_archive_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.ARCHIVE)
        assert result.success is False
        assert store["promo"] == PromotionState.CREATED

    async def test_active_activate_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.ACTIVE)
        result = await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert result.success is False
        assert result.new_state is None
        assert store["promo"] == PromotionState.ACTIVE

    async def test_active_archive_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.ACTIVE)
        result = await fsm.transition("promo", PromotionEvent.ARCHIVE)
        assert result.success is False
        assert store["promo"] == PromotionState.ACTIVE

    async def test_expired_activate_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.EXPIRED)
        result = await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert result.success is False
        assert store["promo"] == PromotionState.EXPIRED

    async def test_expired_expire_rejected(self) -> None:
        fsm, store = make_fsm(PromotionState.EXPIRED)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert result.success is False
        assert store["promo"] == PromotionState.EXPIRED

    async def test_rejection_reason_contains_event_name(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert result.reason is not None
        assert "EXPIRE" in result.reason

    async def test_rejection_reason_contains_state_name(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert result.reason is not None
        assert "CREATED" in result.reason


class TestTransitionResultShape:
    async def test_success_result_fields(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state is not None
        assert result.rejected_event is None
        assert result.reason is None

    async def test_failure_result_fields(self) -> None:
        fsm, _ = make_fsm(PromotionState.CREATED)
        result = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event is not None
        assert result.reason is not None


class TestHandleEventAlias:
    async def test_handle_event_delegates_to_transition(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        result = await fsm.handle_event("promo", PromotionEvent.ACTIVATE)
        assert result.success is True
        assert result.new_state == PromotionState.ACTIVE
        assert store["promo"] == PromotionState.ACTIVE

    async def test_handle_event_rejection(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        result = await fsm.handle_event("promo", PromotionEvent.EXPIRE)
        assert result.success is False
        assert store["promo"] == PromotionState.CREATED


class TestChainedTransitions:
    async def test_full_degradation_path(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)

        r = await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert r.success
        assert r.new_state == PromotionState.ACTIVE

        r = await fsm.transition("promo", PromotionEvent.EXPIRE)
        assert r.success
        assert r.new_state == PromotionState.EXPIRED

        r = await fsm.transition("promo", PromotionEvent.ARCHIVE)
        assert r.success
        assert r.new_state == PromotionState.ARCHIVED

    async def test_rejected_transition_does_not_advance_state(self) -> None:
        fsm, store = make_fsm(PromotionState.CREATED)
        await fsm.transition("promo", PromotionEvent.ACTIVATE)
        await fsm.transition("promo", PromotionEvent.ACTIVATE)
        assert store["promo"] == PromotionState.ACTIVE


class TestMultipleEntities:
    async def test_two_entities_independent(self) -> None:
        store: dict[str, PromotionState] = {
            "promo-a": PromotionState.CREATED,
            "promo-b": PromotionState.ARCHIVED,
        }

        async def reader(entity_id: str) -> PromotionState:
            return store[entity_id]

        async def writer(entity_id: str, state: PromotionState) -> None:
            store[entity_id] = state

        fsm = PromotionFSM(state_reader=reader, state_writer=writer)

        await fsm.transition("promo-a", PromotionEvent.ACTIVATE)
        assert store["promo-a"] == PromotionState.ACTIVE
        assert store["promo-b"] == PromotionState.ARCHIVED

        result = await fsm.transition("promo-b", PromotionEvent.ACTIVATE)
        assert result.success is False
        assert store["promo-b"] == PromotionState.ARCHIVED
        assert store["promo-a"] == PromotionState.ACTIVE
