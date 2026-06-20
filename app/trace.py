"""
app/trace.py — Lightweight Trace for M1/M2.
M3: replaced by nano-vm Trace + TraceAnalyzer.

Stores transition events with trace_id for M2 YooKassa suspend/resume wiring.
DESIGN: same trace_id field on every entity — backward compatible with M3.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class TraceEvent:
    trace_id: str
    entity_id: str
    domain: str
    event: str
    from_state: str
    to_state: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, object] = field(default_factory=dict)


class LightweightTrace:
    """
    In-memory trace for M1/M2.
    M3 migration: swap with nano-vm TraceAnalyzer — same trace_id field.
    """

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def record(
        self,
        entity_id: str,
        domain: str,
        event: str,
        from_state: str,
        to_state: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        trace_id = str(uuid.uuid4())
        self._events.append(
            TraceEvent(
                trace_id=trace_id,
                entity_id=entity_id,
                domain=domain,
                event=event,
                from_state=from_state,
                to_state=to_state,
                metadata=metadata or {},
            )
        )
        return trace_id

    def get_events(self, entity_id: str) -> list[TraceEvent]:
        return [e for e in self._events if e.entity_id == entity_id]

    def get_by_trace_id(self, trace_id: str) -> TraceEvent | None:
        return next((e for e in self._events if e.trace_id == trace_id), None)


# Module-level singleton for M1/M2
# M3: replace with nano-vm-mcp store
trace = LightweightTrace()
