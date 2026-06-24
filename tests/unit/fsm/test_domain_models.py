from __future__ import annotations

from app.domains.inventory.models import (
    INVENTORY_TRANSITIONS,
    InventoryEvent,
    InventoryState,
)
from app.domains.privacy.models import (
    CUSTOMER_DATA_TRANSITIONS,
    CustomerDataEvent,
    CustomerDataState,
)
from app.domains.promotions.models import (
    PROMOTION_TRANSITIONS,
    PromotionEvent,
    PromotionState,
)

# ---------------------------------------------------------------------------
# Inventory domain
# ---------------------------------------------------------------------------

class TestInventoryDomainModels:
    def test_inventory_state_members(self) -> None:
        expected = {"AVAILABLE", "LOW_STOCK", "CRITICAL", "OUT_OF_STOCK"}
        actual = {m.name for m in InventoryState}
        assert actual == expected
        assert len(InventoryState) == 4

    def test_inventory_event_members(self) -> None:
        expected = {
            "STOCK_DECREASED",
            "STOCK_LOW",
            "STOCK_CRITICAL",
            "STOCK_DEPLETED",
            "STOCK_REPLENISHED",
        }
        actual = {m.name for m in InventoryEvent}
        assert actual == expected
        assert len(InventoryEvent) == 5

    def test_inventory_transitions_keys_are_state_event_tuples(self) -> None:
        for key, value in INVENTORY_TRANSITIONS.items():
            assert isinstance(key, tuple), f"Key {key!r} is not a tuple"
            assert len(key) == 2, f"Key {key!r} does not have exactly 2 elements"
            state, event = key
            assert isinstance(state, InventoryState), f"{state!r} is not InventoryState"
            assert isinstance(event, InventoryEvent), f"{event!r} is not InventoryEvent"
            assert isinstance(value, InventoryState), f"Value {value!r} is not InventoryState"

    def test_inventory_transitions_defined_transitions(self) -> None:
        defined = {
            (InventoryState.AVAILABLE, InventoryEvent.STOCK_DECREASED): InventoryState.LOW_STOCK,
            (InventoryState.AVAILABLE, InventoryEvent.STOCK_LOW): InventoryState.LOW_STOCK,
            (InventoryState.AVAILABLE, InventoryEvent.STOCK_CRITICAL): InventoryState.CRITICAL,
            (InventoryState.LOW_STOCK, InventoryEvent.STOCK_CRITICAL): InventoryState.CRITICAL,
            (InventoryState.LOW_STOCK, InventoryEvent.STOCK_DEPLETED): InventoryState.OUT_OF_STOCK,
            (InventoryState.LOW_STOCK, InventoryEvent.STOCK_REPLENISHED): InventoryState.AVAILABLE,
            (InventoryState.CRITICAL, InventoryEvent.STOCK_DEPLETED): InventoryState.OUT_OF_STOCK,
            (InventoryState.CRITICAL, InventoryEvent.STOCK_REPLENISHED): InventoryState.AVAILABLE,
            (
                InventoryState.OUT_OF_STOCK,
                InventoryEvent.STOCK_REPLENISHED,
            ): InventoryState.AVAILABLE,
        }
        for key, expected_target in defined.items():
            assert key in INVENTORY_TRANSITIONS, f"Expected transition {key!r} not found"
            assert INVENTORY_TRANSITIONS[key] == expected_target, (
                f"Transition {key!r} expected to yield {expected_target!r}, "
                f"got {INVENTORY_TRANSITIONS[key]!r}"
            )

    def test_inventory_transitions_no_silent_fallthrough(self) -> None:
        """Every (state, event) pair is either explicitly defined or explicitly absent.
        No key should map to a target that is not a valid InventoryState member.
        """
        valid_states = set(InventoryState)
        for key, target in INVENTORY_TRANSITIONS.items():
            assert target in valid_states, (
                f"Transition {key!r} maps to invalid state {target!r}"
            )

    def test_inventory_absent_transitions_are_not_in_table(self) -> None:
        """Pairs that are not in the table must truly be absent (no silent fallthrough)."""
        # OUT_OF_STOCK with events other than STOCK_REPLENISHED should not exist
        absent_pairs = [
            (InventoryState.OUT_OF_STOCK, InventoryEvent.STOCK_DECREASED),
            (InventoryState.OUT_OF_STOCK, InventoryEvent.STOCK_LOW),
            (InventoryState.OUT_OF_STOCK, InventoryEvent.STOCK_CRITICAL),
            (InventoryState.OUT_OF_STOCK, InventoryEvent.STOCK_DEPLETED),
            (InventoryState.AVAILABLE, InventoryEvent.STOCK_DEPLETED),
            (InventoryState.CRITICAL, InventoryEvent.STOCK_DECREASED),
            (InventoryState.CRITICAL, InventoryEvent.STOCK_LOW),
        ]
        for pair in absent_pairs:
            assert pair not in INVENTORY_TRANSITIONS, (
                f"Transition {pair!r} should be absent but is present in INVENTORY_TRANSITIONS"
            )

    def test_inventory_state_is_str_enum(self) -> None:
        for state in InventoryState:
            assert isinstance(state.value, str)

    def test_inventory_event_is_str_enum(self) -> None:
        for event in InventoryEvent:
            assert isinstance(event.value, str)


# ---------------------------------------------------------------------------
# Promotions domain
# ---------------------------------------------------------------------------

class TestPromotionsDomainModels:
    def test_promotion_state_members(self) -> None:
        expected = {"CREATED", "ACTIVE", "EXPIRED", "ARCHIVED"}
        actual = {m.name for m in PromotionState}
        assert actual == expected
        assert len(PromotionState) == 4

    def test_promotion_event_members(self) -> None:
        expected = {"ACTIVATE", "EXPIRE", "ARCHIVE"}
        actual = {m.name for m in PromotionEvent}
        assert actual == expected
        assert len(PromotionEvent) == 3

    def test_promotion_transitions_keys_are_state_event_tuples(self) -> None:
        for key, value in PROMOTION_TRANSITIONS.items():
            assert isinstance(key, tuple), f"Key {key!r} is not a tuple"
            assert len(key) == 2, f"Key {key!r} does not have exactly 2 elements"
            state, event = key
            assert isinstance(state, PromotionState), f"{state!r} is not PromotionState"
            assert isinstance(event, PromotionEvent), f"{event!r} is not PromotionEvent"
            assert isinstance(value, PromotionState), f"Value {value!r} is not PromotionState"

    def test_promotion_transitions_defined_transitions(self) -> None:
        defined = {
            (PromotionState.CREATED, PromotionEvent.ACTIVATE): PromotionState.ACTIVE,
            (PromotionState.ACTIVE, PromotionEvent.EXPIRE): PromotionState.EXPIRED,
            (PromotionState.EXPIRED, PromotionEvent.ARCHIVE): PromotionState.ARCHIVED,
        }
        for key, expected_target in defined.items():
            assert key in PROMOTION_TRANSITIONS, f"Expected transition {key!r} not found"
            assert PROMOTION_TRANSITIONS[key] == expected_target, (
                f"Transition {key!r} expected to yield {expected_target!r}, "
                f"got {PROMOTION_TRANSITIONS[key]!r}"
            )

    def test_promotion_transitions_no_silent_fallthrough(self) -> None:
        valid_states = set(PromotionState)
        for key, target in PROMOTION_TRANSITIONS.items():
            assert target in valid_states, (
                f"Transition {key!r} maps to invalid state {target!r}"
            )

    def test_promotion_absent_transitions_are_not_in_table(self) -> None:
        """Terminal and non-existent transitions must be absent."""
        absent_pairs = [
            (PromotionState.ARCHIVED, PromotionEvent.ACTIVATE),
            (PromotionState.ARCHIVED, PromotionEvent.EXPIRE),
            (PromotionState.ARCHIVED, PromotionEvent.ARCHIVE),
            (PromotionState.EXPIRED, PromotionEvent.ACTIVATE),
            (PromotionState.EXPIRED, PromotionEvent.EXPIRE),
            (PromotionState.ACTIVE, PromotionEvent.ACTIVATE),
            (PromotionState.ACTIVE, PromotionEvent.ARCHIVE),
            (PromotionState.CREATED, PromotionEvent.EXPIRE),
            (PromotionState.CREATED, PromotionEvent.ARCHIVE),
        ]
        for pair in absent_pairs:
            assert pair not in PROMOTION_TRANSITIONS, (
                f"Transition {pair!r} should be absent but is present in PROMOTION_TRANSITIONS"
            )

    def test_promotion_state_is_str_enum(self) -> None:
        for state in PromotionState:
            assert isinstance(state.value, str)

    def test_promotion_event_is_str_enum(self) -> None:
        for event in PromotionEvent:
            assert isinstance(event.value, str)


# ---------------------------------------------------------------------------
# Privacy domain
# ---------------------------------------------------------------------------

class TestPrivacyDomainModels:
    def test_customer_data_state_members(self) -> None:
        expected = {"ACTIVE", "RETAINED", "ANONYMIZED", "DELETED"}
        actual = {m.name for m in CustomerDataState}
        assert actual == expected
        assert len(CustomerDataState) == 4

    def test_customer_data_event_members(self) -> None:
        expected = {"RETAIN", "ANONYMIZE", "GDPR_ERASE"}
        actual = {m.name for m in CustomerDataEvent}
        assert actual == expected
        assert len(CustomerDataEvent) == 3

    def test_customer_data_event_gdpr_erase_aligns_with_nano_vm(self) -> None:
        """GDPR_ERASE event must exist and be named to align with nano-vm GdprEraseEvent concept."""
        assert hasattr(CustomerDataEvent, "GDPR_ERASE"), (
            "CustomerDataEvent must have GDPR_ERASE to align with nano-vm GdprEraseEvent"
        )
        assert CustomerDataEvent.GDPR_ERASE.value == "GDPR_ERASE"

    def test_customer_data_transitions_keys_are_state_event_tuples(self) -> None:
        for key, value in CUSTOMER_DATA_TRANSITIONS.items():
            assert isinstance(key, tuple), f"Key {key!r} is not a tuple"
            assert len(key) == 2, f"Key {key!r} does not have exactly 2 elements"
            state, event = key
            assert isinstance(state, CustomerDataState), f"{state!r} is not CustomerDataState"
            assert isinstance(event, CustomerDataEvent), f"{event!r} is not CustomerDataEvent"
            assert isinstance(value, CustomerDataState), f"Value {value!r} is not CustomerDataState"

    def test_customer_data_transitions_defined_transitions(self) -> None:
        defined = {
            (CustomerDataState.ACTIVE, CustomerDataEvent.RETAIN): CustomerDataState.RETAINED,
            (CustomerDataState.RETAINED, CustomerDataEvent.ANONYMIZE): CustomerDataState.ANONYMIZED,
            (CustomerDataState.ANONYMIZED, CustomerDataEvent.GDPR_ERASE): CustomerDataState.DELETED,
        }
        for key, expected_target in defined.items():
            assert key in CUSTOMER_DATA_TRANSITIONS, f"Expected transition {key!r} not found"
            assert CUSTOMER_DATA_TRANSITIONS[key] == expected_target, (
                f"Transition {key!r} expected to yield {expected_target!r}, "
                f"got {CUSTOMER_DATA_TRANSITIONS[key]!r}"
            )

    def test_customer_data_transitions_no_silent_fallthrough(self) -> None:
        valid_states = set(CustomerDataState)
        for key, target in CUSTOMER_DATA_TRANSITIONS.items():
            assert target in valid_states, (
                f"Transition {key!r} maps to invalid state {target!r}"
            )

    def test_customer_data_absent_transitions_are_not_in_table(self) -> None:
        """Terminal DELETED state and non-existent transitions must be absent."""
        absent_pairs = [
            (CustomerDataState.DELETED, CustomerDataEvent.RETAIN),
            (CustomerDataState.DELETED, CustomerDataEvent.ANONYMIZE),
            (CustomerDataState.DELETED, CustomerDataEvent.GDPR_ERASE),
            (CustomerDataState.ACTIVE, CustomerDataEvent.ANONYMIZE),
            (CustomerDataState.ACTIVE, CustomerDataEvent.GDPR_ERASE),
            (CustomerDataState.RETAINED, CustomerDataEvent.RETAIN),
            (CustomerDataState.RETAINED, CustomerDataEvent.GDPR_ERASE),
            (CustomerDataState.ANONYMIZED, CustomerDataEvent.RETAIN),
            (CustomerDataState.ANONYMIZED, CustomerDataEvent.ANONYMIZE),
        ]
        for pair in absent_pairs:
            assert pair not in CUSTOMER_DATA_TRANSITIONS, (
                f"Transition {pair!r} should be absent but is present in CUSTOMER_DATA_TRANSITIONS"
            )

    def test_customer_data_state_is_str_enum(self) -> None:
        for state in CustomerDataState:
            assert isinstance(state.value, str)

    def test_customer_data_event_is_str_enum(self) -> None:
        for event in CustomerDataEvent:
            assert isinstance(event.value, str)


# ---------------------------------------------------------------------------
# Cross-domain: transition table naming convention
# ---------------------------------------------------------------------------

class TestTransitionTableNamingConvention:
    """Verify the X_TRANSITIONS naming convention is followed for all domains."""

    def test_inventory_transitions_name(self) -> None:
        import app.domains.inventory.models as inv_mod
        assert hasattr(inv_mod, "INVENTORY_TRANSITIONS"), (
            "Expected INVENTORY_TRANSITIONS (not XTransitions or TRANSITIONS_X)"
        )

    def test_promotions_transitions_name(self) -> None:
        import app.domains.promotions.models as promo_mod
        assert hasattr(promo_mod, "PROMOTION_TRANSITIONS"), (
            "Expected PROMOTION_TRANSITIONS (not XTransitions or TRANSITIONS_X)"
        )

    def test_privacy_transitions_name(self) -> None:
        import app.domains.privacy.models as priv_mod
        assert hasattr(priv_mod, "CUSTOMER_DATA_TRANSITIONS"), (
            "Expected CUSTOMER_DATA_TRANSITIONS (not XTransitions or TRANSITIONS_X)"
        )

    def test_no_wrong_name_variants_inventory(self) -> None:
        import app.domains.inventory.models as inv_mod
        assert not hasattr(inv_mod, "InventoryTransitions")
        assert not hasattr(inv_mod, "TRANSITIONS_INVENTORY")
        assert not hasattr(inv_mod, "TRANSITIONS")

    def test_no_wrong_name_variants_promotions(self) -> None:
        import app.domains.promotions.models as promo_mod
        assert not hasattr(promo_mod, "PromotionTransitions")
        assert not hasattr(promo_mod, "TRANSITIONS_PROMOTION")
        assert not hasattr(promo_mod, "TRANSITIONS")

    def test_no_wrong_name_variants_privacy(self) -> None:
        import app.domains.privacy.models as priv_mod
        assert not hasattr(priv_mod, "CustomerDataTransitions")
        assert not hasattr(priv_mod, "TRANSITIONS_CUSTOMER_DATA")
        assert not hasattr(priv_mod, "TRANSITIONS")
