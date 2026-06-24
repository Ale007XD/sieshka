"""
app/domains/privacy/fsm.py
CustomerDataFSM — M1 custom implementation.
M3: replaced by ExecutionVM + nano-vm-mcp.

CONSTRAINT: transition() accepts EVENT not new_state.
CONSTRAINT: graph-only — no business rules inside.
NOTE: ANONYMIZED/DELETED states align with nano-vm GdprEraseEvent naming.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.privacy.models import (
    CUSTOMER_DATA_TRANSITIONS,
    CustomerDataEvent,
    CustomerDataState,
)
from app.fsm.core.base import BaseFSM, TransitionResult

logger = logging.getLogger(__name__)


class CustomerDataFSM(BaseFSM[CustomerDataState, CustomerDataEvent]):
    """
    In-memory + callback-based CustomerDataFSM for M1.
    state_reader/state_writer: callables injected by Application Service.
    This keeps FSM testable without a database.

    GDPR alignment: ANONYMIZED and DELETED states correspond to the
    nano-vm core GdprEraseEvent — naming is intentionally preserved here.
    """

    def __init__(
        self,
        state_reader: Any,  # Callable[[str], CustomerDataState]
        state_writer: Any,  # Callable[[str, CustomerDataState], None]
    ) -> None:
        self._read = state_reader
        self._write = state_writer

    def get_current_state(self, entity_id: str) -> CustomerDataState:
        return self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: CustomerDataState) -> list[CustomerDataEvent]:
        """Graph-level only. Business rules → PolicyProvider."""
        return [
            event
            for (s, event) in CUSTOMER_DATA_TRANSITIONS
            if s == state
        ]

    def transition(
        self,
        entity_id: str,
        event: CustomerDataEvent,
    ) -> TransitionResult:
        """
        Attempt transition. Writes new state via state_writer (terminal tool).
        state_writer is the ONLY place allowed to write customer_data.state.
        """
        current = self._read(entity_id)
        new_state = CUSTOMER_DATA_TRANSITIONS.get((current, event))

        if new_state is None:
            logger.warning(
                "CustomerDataFSM: rejected %s event=%s from state=%s",
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

        self._write(entity_id, new_state)  # terminal tool — atomic PG write
        logger.info(
            "CustomerDataFSM: %s %s → %s via %s",
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