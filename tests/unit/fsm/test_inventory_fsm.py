from __future__ import annotations

from app.domains.inventory.fsm import InventoryFSM
from app.domains.inventory.models import InventoryEvent, InventoryState
from app.fsm.core.base import TransitionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_fsm(initial_state: InventoryState) -> tuple[InventoryFSM, dict[str, InventoryState]]:
    """Return (fsm, store) where store holds entity state by id."""
    store: dict[str, InventoryState] = {"item": initial_state}

    async def reader(entity_id: str) -> InventoryState:
        return store[entity_id]

    async def writer(entity_id: str, state: InventoryState) -> None:
        store[entity_id] = state

    return InventoryFSM(state_reader=reader, state_writer=writer), store


# ---------------------------------------------------------------------------
# Constructor / get_current_state
# ---------------------------------------------------------------------------

class TestInventoryFSMInit:
    async def test_get_current_state_returns_initial(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        assert await fsm.get_current_state("item") == InventoryState.AVAILABLE

    async def test_get_current_state_low_stock(self) -> None:
        fsm, _ = make_fsm(InventoryState.LOW_STOCK)
        assert await fsm.get_current_state("item") == InventoryState.LOW_STOCK

    async def test_get_current_state_critical(self) -> None:
        fsm, _ = make_fsm(InventoryState.CRITICAL)
        assert await fsm.get_current_state("item") == InventoryState.CRITICAL

    async def test_get_current_state_out_of_stock(self) -> None:
        fsm, _ = make_fsm(InventoryState.OUT_OF_STOCK)
        assert await fsm.get_current_state("item") == InventoryState.OUT_OF_STOCK


# ---------------------------------------------------------------------------
# get_allowed_events
# ---------------------------------------------------------------------------

class TestGetAllowedEvents:
    def test_available_allowed_events(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        allowed = fsm.get_allowed_events(InventoryState.AVAILABLE)
        assert set(allowed) == {
            InventoryEvent.STOCK_DECREASED,
            InventoryEvent.STOCK_LOW,
            InventoryEvent.STOCK_CRITICAL,
        }

    def test_low_stock_allowed_events(self) -> None:
        fsm, _ = make_fsm(InventoryState.LOW_STOCK)
        allowed = fsm.get_allowed_events(InventoryState.LOW_STOCK)
        assert set(allowed) == {
            InventoryEvent.STOCK_CRITICAL,
            InventoryEvent.STOCK_DEPLETED,
            InventoryEvent.STOCK_REPLENISHED,
        }

    def test_critical_allowed_events(self) -> None:
        fsm, _ = make_fsm(InventoryState.CRITICAL)
        allowed = fsm.get_allowed_events(InventoryState.CRITICAL)
        assert set(allowed) == {
            InventoryEvent.STOCK_DEPLETED,
            InventoryEvent.STOCK_REPLENISHED,
        }

    def test_out_of_stock_allowed_events(self) -> None:
        fsm, _ = make_fsm(InventoryState.OUT_OF_STOCK)
        allowed = fsm.get_allowed_events(InventoryState.OUT_OF_STOCK)
        assert set(allowed) == {InventoryEvent.STOCK_REPLENISHED}

    def test_returns_list(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        result = fsm.get_allowed_events(InventoryState.AVAILABLE)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Happy-path transitions: AVAILABLE
# ---------------------------------------------------------------------------

class TestTransitionsFromAvailable:
    async def test_stock_decreased_to_low_stock(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert result.success is True
        assert result.new_state == InventoryState.LOW_STOCK
        assert result.rejected_event is None
        assert result.reason is None
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_stock_low_to_low_stock(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_LOW)
        assert result.success is True
        assert result.new_state == InventoryState.LOW_STOCK
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_stock_critical_to_critical(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)
        assert result.success is True
        assert result.new_state == InventoryState.CRITICAL
        assert store["item"] == InventoryState.CRITICAL

    async def test_state_persisted_after_transition(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert await fsm.get_current_state("item") == InventoryState.LOW_STOCK


# ---------------------------------------------------------------------------
# Happy-path transitions: LOW_STOCK
# ---------------------------------------------------------------------------

class TestTransitionsFromLowStock:
    async def test_stock_critical_to_critical(self) -> None:
        fsm, store = make_fsm(InventoryState.LOW_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)
        assert result.success is True
        assert result.new_state == InventoryState.CRITICAL
        assert store["item"] == InventoryState.CRITICAL

    async def test_stock_depleted_to_out_of_stock(self) -> None:
        fsm, store = make_fsm(InventoryState.LOW_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.success is True
        assert result.new_state == InventoryState.OUT_OF_STOCK
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_stock_replenished_to_available(self) -> None:
        fsm, store = make_fsm(InventoryState.LOW_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_REPLENISHED)
        assert result.success is True
        assert result.new_state == InventoryState.AVAILABLE
        assert store["item"] == InventoryState.AVAILABLE


# ---------------------------------------------------------------------------
# Happy-path transitions: CRITICAL
# ---------------------------------------------------------------------------

class TestTransitionsFromCritical:
    async def test_stock_depleted_to_out_of_stock(self) -> None:
        fsm, store = make_fsm(InventoryState.CRITICAL)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.success is True
        assert result.new_state == InventoryState.OUT_OF_STOCK
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_stock_replenished_to_available(self) -> None:
        fsm, store = make_fsm(InventoryState.CRITICAL)
        result = await fsm.transition("item", InventoryEvent.STOCK_REPLENISHED)
        assert result.success is True
        assert result.new_state == InventoryState.AVAILABLE
        assert store["item"] == InventoryState.AVAILABLE


# ---------------------------------------------------------------------------
# Happy-path transitions: OUT_OF_STOCK
# ---------------------------------------------------------------------------

class TestTransitionsFromOutOfStock:
    async def test_stock_replenished_to_available(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_REPLENISHED)
        assert result.success is True
        assert result.new_state == InventoryState.AVAILABLE
        assert store["item"] == InventoryState.AVAILABLE


# ---------------------------------------------------------------------------
# Rejection: invalid events from each state
# ---------------------------------------------------------------------------

class TestRejectedTransitions:
    async def test_available_stock_depleted_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == InventoryEvent.STOCK_DEPLETED
        assert result.reason is not None
        assert store["item"] == InventoryState.AVAILABLE  # state unchanged

    async def test_available_stock_replenished_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_REPLENISHED)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == InventoryEvent.STOCK_REPLENISHED
        assert store["item"] == InventoryState.AVAILABLE

    async def test_low_stock_stock_decreased_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.LOW_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == InventoryEvent.STOCK_DECREASED
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_low_stock_stock_low_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.LOW_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_LOW)
        assert result.success is False
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_critical_stock_decreased_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.CRITICAL)
        result = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == InventoryEvent.STOCK_DECREASED
        assert store["item"] == InventoryState.CRITICAL

    async def test_critical_stock_low_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.CRITICAL)
        result = await fsm.transition("item", InventoryEvent.STOCK_LOW)
        assert result.success is False
        assert store["item"] == InventoryState.CRITICAL

    async def test_critical_stock_critical_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.CRITICAL)
        result = await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)
        assert result.success is False
        assert store["item"] == InventoryState.CRITICAL

    async def test_out_of_stock_stock_decreased_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event == InventoryEvent.STOCK_DECREASED
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_out_of_stock_stock_depleted_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.success is False
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_out_of_stock_stock_low_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_LOW)
        assert result.success is False
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_out_of_stock_stock_critical_rejected(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        result = await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)
        assert result.success is False
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_rejection_reason_contains_event_name(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.reason is not None
        assert "STOCK_DEPLETED" in result.reason

    async def test_rejection_reason_contains_state_name(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert result.reason is not None
        assert "AVAILABLE" in result.reason


# ---------------------------------------------------------------------------
# TransitionResult shape
# ---------------------------------------------------------------------------

class TestTransitionResultShape:
    async def test_success_result_fields(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert isinstance(result, TransitionResult)
        assert result.success is True
        assert result.new_state is not None
        assert result.rejected_event is None
        assert result.reason is None

    async def test_failure_result_fields(self) -> None:
        fsm, _ = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert isinstance(result, TransitionResult)
        assert result.success is False
        assert result.new_state is None
        assert result.rejected_event is not None
        assert result.reason is not None


# ---------------------------------------------------------------------------
# handle_event alias (inherited from BaseFSM)
# ---------------------------------------------------------------------------

class TestHandleEventAlias:
    async def test_handle_event_delegates_to_transition(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.handle_event("item", InventoryEvent.STOCK_DECREASED)
        assert result.success is True
        assert result.new_state == InventoryState.LOW_STOCK
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_handle_event_rejection(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        result = await fsm.handle_event("item", InventoryEvent.STOCK_DEPLETED)
        assert result.success is False
        assert store["item"] == InventoryState.AVAILABLE


# ---------------------------------------------------------------------------
# Multi-step / chained transitions
# ---------------------------------------------------------------------------

class TestChainedTransitions:
    async def test_available_to_out_of_stock_via_low_stock(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        r1 = await fsm.transition("item", InventoryEvent.STOCK_DECREASED)
        assert r1.success is True
        assert store["item"] == InventoryState.LOW_STOCK

        r2 = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert r2.success is True
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_available_to_out_of_stock_via_critical(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        r1 = await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)
        assert r1.success is True
        assert store["item"] == InventoryState.CRITICAL

        r2 = await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)
        assert r2.success is True
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_replenish_from_out_of_stock_and_degrade_again(self) -> None:
        fsm, store = make_fsm(InventoryState.OUT_OF_STOCK)
        r1 = await fsm.transition("item", InventoryEvent.STOCK_REPLENISHED)
        assert r1.success is True
        assert store["item"] == InventoryState.AVAILABLE

        r2 = await fsm.transition("item", InventoryEvent.STOCK_LOW)
        assert r2.success is True
        assert store["item"] == InventoryState.LOW_STOCK

    async def test_full_degradation_path(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)

        await fsm.transition("item", InventoryEvent.STOCK_LOW)  # AVAILABLE -> LOW_STOCK
        assert store["item"] == InventoryState.LOW_STOCK

        await fsm.transition("item", InventoryEvent.STOCK_CRITICAL)  # LOW_STOCK -> CRITICAL
        assert store["item"] == InventoryState.CRITICAL

        await fsm.transition("item", InventoryEvent.STOCK_DEPLETED)  # CRITICAL -> OUT_OF_STOCK
        assert store["item"] == InventoryState.OUT_OF_STOCK

    async def test_rejected_transition_does_not_advance_state(self) -> None:
        fsm, store = make_fsm(InventoryState.AVAILABLE)
        await fsm.transition("item", InventoryEvent.STOCK_DECREASED)  # -> LOW_STOCK
        await fsm.transition("item", InventoryEvent.STOCK_DECREASED)  # rejected from LOW_STOCK
        assert store["item"] == InventoryState.LOW_STOCK


# ---------------------------------------------------------------------------
# Multiple independent entities share no state
# ---------------------------------------------------------------------------

class TestMultipleEntities:
    async def test_two_entities_independent(self) -> None:
        store: dict[str, InventoryState] = {
            "item-a": InventoryState.AVAILABLE,
            "item-b": InventoryState.OUT_OF_STOCK,
        }

        async def reader(entity_id: str) -> InventoryState:
            return store[entity_id]

        async def writer(entity_id: str, state: InventoryState) -> None:
            store[entity_id] = state

        fsm = InventoryFSM(state_reader=reader, state_writer=writer)

        await fsm.transition("item-a", InventoryEvent.STOCK_DECREASED)
        assert store["item-a"] == InventoryState.LOW_STOCK
        assert store["item-b"] == InventoryState.OUT_OF_STOCK  # unchanged

        await fsm.transition("item-b", InventoryEvent.STOCK_REPLENISHED)
        assert store["item-b"] == InventoryState.AVAILABLE
        assert store["item-a"] == InventoryState.LOW_STOCK  # unchanged
