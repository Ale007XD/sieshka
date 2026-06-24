"""
app/domains/schedule/fsm.py
BusinessScheduleFSM — M1 custom implementation.
M3: replaced by ExecutionVM + nano-vm-mcp.

CONSTRAINT: transition() accepts EVENT not new_state.
CONSTRAINT: graph-only — no business rules inside.
CONSTRAINT: cyclic graph OPEN→CLOSING_SOON→CLOSED→OPEN — no terminal states.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from app.fsm.core.base import BaseFSM, TransitionResult

logger = logging.getLogger(__name__)


class BusinessScheduleState(str, Enum):
    OPEN = "OPEN"
    CLOSING_SOON = "CLOSING_SOON"
    CLOSED = "CLOSED"


class BusinessScheduleEvent(str, Enum):
    CLOSING_WARNING = "CLOSING_WARNING"
    CLOSE = "CLOSE"
    OPEN = "OPEN"


# Graph: allowed transitions per state — cyclic, no terminal states
SCHEDULE_TRANSITIONS: dict[
    BusinessScheduleState,
    dict[BusinessScheduleEvent, BusinessScheduleState],
] = {
    BusinessScheduleState.OPEN: {
        BusinessScheduleEvent.CLOSING_WARNING: BusinessScheduleState.CLOSING_SOON,
    },
    BusinessScheduleState.CLOSING_SOON: {
        BusinessScheduleEvent.CLOSE: BusinessScheduleState.CLOSED,
    },
    BusinessScheduleState.CLOSED: {
        BusinessScheduleEvent.OPEN: BusinessScheduleState.OPEN,
    },
}


class BusinessScheduleFSM(BaseFSM[BusinessScheduleState, BusinessScheduleEvent]):
    """
    In-memory + callback-based BusinessScheduleFSM for M1.
    Cyclic graph: OPEN → CLOSING_SOON → CLOSED → OPEN.
    No terminal states — every state has at least one outgoing transition.

    state_reader/state_writer: callables injected by Application Service.
    This keeps FSM testable without a database.
    """

    def __init__(
        self,
        state_reader: Any,  # Callable[[str], BusinessScheduleState]
        state_writer: Any,  # Callable[[str, BusinessScheduleState], None]
    ) -> None:
        self._read = state_reader
        self._write = state_writer

    def get_current_state(self, entity_id: str) -> BusinessScheduleState:
        return self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: BusinessScheduleState) -> list[BusinessScheduleEvent]:
        """Graph-level only. Business rules → PolicyProvider."""
        return list(SCHEDULE_TRANSITIONS.get(state, {}).keys())

    def transition(
        self,
        entity_id: str,
        event: BusinessScheduleEvent,
    ) -> TransitionResult:
        """
        Attempt transition. Writes new state via state_writer (terminal tool).
        state_writer is the ONLY place allowed to write schedule.state.
        """
        current = self._read(entity_id)
        allowed = SCHEDULE_TRANSITIONS.get(current, {})

        if event not in allowed:
            logger.warning(
                "BusinessScheduleFSM: rejected %s event=%s from state=%s",
                entity_id,
                event,
                current,
            )
            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=event,
                reason=f"Event {event!r} not allowed from state {current!r}",
            )

        new_state = allowed[event]
        self._write(entity_id, new_state)  # terminal tool — atomic PG write
        logger.info(
            "BusinessScheduleFSM: %s %s → %s via %s",
            entity_id,
            current,
            new_state,
            event,
        )
        return TransitionResult(
            success=True,
            new_state=new_state,
            rejected_event=None,
            reason=None,
        )