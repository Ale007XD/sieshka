"""
app/domains/orders/fsm.py
OrderFSM — M1 custom implementation.
M3: replaced by ExecutionVM + nano-vm-mcp.

CONSTRAINT: transition() accepts EVENT not new_state.
CONSTRAINT: graph-only — no business rules inside.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.orders.models import ORDER_TRANSITIONS, OrderEvent, OrderState
from app.fsm.core.base import BaseFSM, TransitionResult

logger = logging.getLogger(__name__)


class OrderFSM(BaseFSM[OrderState, OrderEvent]):
    """
    In-memory + callback-based OrderFSM for M1.
    state_reader/state_writer: callables injected by Application Service.
    This keeps FSM testable without a database.
    """

    def __init__(
        self,
        state_reader: Any,  # Callable[[str], OrderState]
        state_writer: Any,  # Callable[[str, OrderState], None]  ← terminal tool writes PG
    ) -> None:
        self._read = state_reader
        self._write = state_writer

    def get_current_state(self, entity_id: str) -> OrderState:
        return self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: OrderState) -> list[OrderEvent]:
        """Graph-level only. Business rules → PolicyProvider."""
        return list(ORDER_TRANSITIONS.get(state, {}).keys())

    def transition(
        self,
        entity_id: str,
        event: OrderEvent,
    ) -> TransitionResult:
        """
        Attempt transition. Writes new state via state_writer (terminal tool).
        state_writer is the ONLY place allowed to write order.state.
        """
        current = self._read(entity_id)
        allowed = ORDER_TRANSITIONS.get(current, {})

        if event not in allowed:
            logger.warning(
                "OrderFSM: rejected %s event=%s from state=%s",
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
        self._write(entity_id, new_state)  # terminal tool — atomic PG write  # terminal-tool
        logger.info(
            "OrderFSM: %s %s → %s via %s",
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
