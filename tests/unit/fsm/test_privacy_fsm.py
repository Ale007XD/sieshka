from __future__ import annotations

from app.domains.privacy.fsm import CustomerDataFSM
from app.domains.privacy.models import (
    CUSTOMER_DATA_TRANSITIONS,
    CustomerDataEvent,
    CustomerDataState,
)
from app.fsm.core.base import TransitionResult


def make_fsm(
    initial_state: CustomerDataState,
) -> tuple[CustomerDataFSM, dict[str, CustomerDataState]]:
    store: dict[str, CustomerDataState] = {"customer": initial_state}

    async def reader(entity_id: str) -> CustomerDataState:
        return store[entity_id]

    async def writer(entity_id: str, state: CustomerDataState) -> None:
        store[entity_id] = state

    return CustomerDataFSM(state_reader=reader, state_writer=writer), store


class TestPrivacyFSMInit:
    async def test_get_current_state_active(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        assert await fsm.get_current_state("customer") == CustomerDataState.ACTIVE

    async def test_get_current_state_retained(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.RETAINED)
        assert await fsm.get_current_state("customer") == CustomerDataState.RETAINED

    async def test_get_current_state_anonymized(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ANONYMIZED)
        assert await fsm.get_current_state("customer") == CustomerDataState.ANONYMIZED

    async def test_get_current_state_deleted(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.DELETED)
        assert await fsm.get_current_state("customer") == CustomerDataState.DELETED


class TestGetAllowedEvents:
    def test_active_allowed_events(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        allowed = fsm.get_allowed_events(CustomerDataState.ACTIVE)
        assert allowed == [CustomerDataEvent.RETAIN]

    def test_retained_allowed_events(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.RETAINED)
        allowed = fsm.get_allowed_events(CustomerDataState.RETAINED)
        assert allowed == [CustomerDataEvent.ANONYMIZE]

    def test_anonymized_allowed_events(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ANONYMIZED)
        allowed = fsm.get_allowed_events(CustomerDataState.ANONYMIZED)
        assert allowed == [CustomerDataEvent.GDPR_ERASE]

    def test_deleted_allowed_events_empty(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.DELETED)
        allowed = fsm.get_allowed_events(CustomerDataState.DELETED)
        assert allowed == []

    def test_returns_list(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        result = fsm.get_allowed_events(CustomerDataState.ACTIVE)
        assert isinstance(result, list)


class TestTransitionsFromActive:
    async def test_retain_to_retained(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert result.success is True
        assert result.new_state == CustomerDataState.RETAINED
        assert result.rejected_event is None
        assert result.reason is None
        assert store["customer"] == CustomerDataState.RETAINED

    async def test_state_persisted_after_transition(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert await fsm.get_current_state("customer") == CustomerDataState.RETAINED


class TestTransitionsFromRetained:
    async def test_anonymize_to_anonymized(self) -> None:
        fsm, store = make_fsm(CustomerDataState.RETAINED)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert result.success is True
        assert result.new_state == CustomerDataState.ANONYMIZED
        assert store["customer"] == CustomerDataState.ANONYMIZED


class TestTransitionsFromAnonymized:
    async def test_gdpr_erase_to_deleted(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ANONYMIZED)
        result = await fsm.transition("customer", CustomerDataEvent.GDPR_ERASE)
        assert result.success is True
        assert result.new_state == CustomerDataState.DELETED
        assert store["customer"] == CustomerDataState.DELETED


class TestTransitionsFromDeleted:
    async def test_no_outgoing_transitions(self) -> None:
        fsm, store = make_fsm(CustomerDataState.DELETED)
        for event in CustomerDataEvent:
            result = await fsm.transition("customer", event)
            assert result.success is False
            assert result.new_state is None
            assert store["customer"] == CustomerDataState.DELETED


class TestRejectedTransitions:
    async def test_active_anonymize_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == CustomerDataEvent.ANONYMIZE
        assert store["customer"] == CustomerDataState.ACTIVE

    async def test_active_gdpr_erase_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.GDPR_ERASE)
        assert result.success is False
        assert store["customer"] == CustomerDataState.ACTIVE

    async def test_retained_retain_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.RETAINED)
        result = await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert result.success is False
        assert store["customer"] == CustomerDataState.RETAINED

    async def test_retained_gdpr_erase_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.RETAINED)
        result = await fsm.transition("customer", CustomerDataEvent.GDPR_ERASE)
        assert result.success is False
        assert store["customer"] == CustomerDataState.RETAINED

    async def test_anonymized_retain_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ANONYMIZED)
        result = await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert result.success is False
        assert store["customer"] == CustomerDataState.ANONYMIZED

    async def test_anonymized_anonymize_rejected(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ANONYMIZED)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert result.success is False
        assert store["customer"] == CustomerDataState.ANONYMIZED

    async def test_rejection_reason_contains_event_name(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert result.reason is not None
        assert "ANONYMIZE" in result.reason

    async def test_rejection_reason_contains_state_name(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert result.reason is not None
        assert "ACTIVE" in result.reason


class TestGDPRIrreversibility:
    def test_deleted_get_allowed_events_empty(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.DELETED)
        assert fsm.get_allowed_events(CustomerDataState.DELETED) == []

    def test_no_backward_transition_exists(self) -> None:
        linear_order = [
            CustomerDataState.ACTIVE,
            CustomerDataState.RETAINED,
            CustomerDataState.ANONYMIZED,
            CustomerDataState.DELETED,
        ]
        for (current, event), new_state in CUSTOMER_DATA_TRANSITIONS.items():
            current_idx = linear_order.index(current)
            new_idx = linear_order.index(new_state)
            assert new_idx > current_idx, (
                f"Backward transition: {current} --{event}--> {new_state}"
            )


class TestTransitionResultShape:
    async def test_success_result_fields(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state is not None
        assert result.rejected_event is None
        assert result.reason is None

    async def test_failure_result_fields(self) -> None:
        fsm, _ = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event is not None
        assert result.reason is not None


class TestHandleEventAlias:
    async def test_handle_event_delegates_to_transition(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.handle_event("customer", CustomerDataEvent.RETAIN)
        assert result.success is True
        assert result.new_state == CustomerDataState.RETAINED
        assert store["customer"] == CustomerDataState.RETAINED

    async def test_handle_event_rejection(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        result = await fsm.handle_event("customer", CustomerDataEvent.ANONYMIZE)
        assert result.success is False
        assert store["customer"] == CustomerDataState.ACTIVE


class TestChainedTransitions:
    async def test_full_degradation_path(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)

        await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert store["customer"] == CustomerDataState.RETAINED

        await fsm.transition("customer", CustomerDataEvent.ANONYMIZE)
        assert store["customer"] == CustomerDataState.ANONYMIZED

        await fsm.transition("customer", CustomerDataEvent.GDPR_ERASE)
        assert store["customer"] == CustomerDataState.DELETED

    async def test_rejected_transition_does_not_advance_state(self) -> None:
        fsm, store = make_fsm(CustomerDataState.ACTIVE)
        await fsm.transition("customer", CustomerDataEvent.RETAIN)
        await fsm.transition("customer", CustomerDataEvent.RETAIN)
        assert store["customer"] == CustomerDataState.RETAINED


class TestMultipleEntities:
    async def test_two_entities_independent(self) -> None:
        store: dict[str, CustomerDataState] = {
            "customer-a": CustomerDataState.ACTIVE,
            "customer-b": CustomerDataState.DELETED,
        }

        async def reader(entity_id: str) -> CustomerDataState:
            return store[entity_id]

        async def writer(entity_id: str, state: CustomerDataState) -> None:
            store[entity_id] = state

        fsm = CustomerDataFSM(state_reader=reader, state_writer=writer)

        await fsm.transition("customer-a", CustomerDataEvent.RETAIN)
        assert store["customer-a"] == CustomerDataState.RETAINED
        assert store["customer-b"] == CustomerDataState.DELETED

        result = await fsm.transition("customer-b", CustomerDataEvent.RETAIN)
        assert result.success is False
        assert store["customer-b"] == CustomerDataState.DELETED
        assert store["customer-a"] == CustomerDataState.RETAINED
