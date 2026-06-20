"""tests/unit/test_trace.py"""
from __future__ import annotations

from app.trace import LightweightTrace


class TestLightweightTrace:
    def test_record_and_retrieve(self) -> None:
        t = LightweightTrace()
        trace_id = t.record("ord1", "orders", "CONFIRM", "DRAFT", "CONFIRMED")
        events = t.get_events("ord1")
        assert len(events) == 1
        assert events[0].trace_id == trace_id
        assert events[0].from_state == "DRAFT"
        assert events[0].to_state == "CONFIRMED"

    def test_get_by_trace_id(self) -> None:
        t = LightweightTrace()
        trace_id = t.record("ord1", "orders", "CONFIRM", "DRAFT", "CONFIRMED")
        event = t.get_by_trace_id(trace_id)
        assert event is not None
        assert event.entity_id == "ord1"

    def test_get_by_trace_id_not_found(self) -> None:
        t = LightweightTrace()
        assert t.get_by_trace_id("nonexistent") is None

    def test_multiple_entities(self) -> None:
        t = LightweightTrace()
        t.record("ord1", "orders", "CONFIRM", "DRAFT", "CONFIRMED")
        t.record("ord2", "orders", "CONFIRM", "DRAFT", "CONFIRMED")
        assert len(t.get_events("ord1")) == 1
        assert len(t.get_events("ord2")) == 1

    def test_metadata_stored(self) -> None:
        t = LightweightTrace()
        t.record("ord1", "orders", "PAYMENT", "CONFIRMED", "PAYMENT_PENDING",
                 metadata={"trace_id": "yookassa-123"})
        event = t.get_events("ord1")[0]
        assert event.metadata["trace_id"] == "yookassa-123"
