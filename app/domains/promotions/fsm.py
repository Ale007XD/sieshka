"""
app/domains/promotions/fsm.py
PromotionFSM — M1 custom implementation.
M3: replaced by ExecutionVM + nano-vm-mcp.

CONSTRAINT: transition() accepts EVENT not new_state.
CONSTRAINT: graph-only — no business rules inside.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.promotions.models import PROMOTION_TRANSITIONS, PromotionEvent, PromotionState
from app.fsm.core.base import BaseFSM, TransitionResult

logger = logging.getLogger(__name__)


class PromotionFSM(BaseFSM[PromotionState, PromotionEvent]):
    """
    In-memory + callback-based PromotionFSM for M1.
    state_reader/state_writer: callables injected by Application Service.
    This keeps FSM testable without a database.
    """

    def __init__(
        self,
        state_reader: Any,  # Callable[[str], PromotionState]
        state_writer: Any,  # Callable[[str, PromotionState], None]
    ) -> None:
        self._read = state_reader
        self._write = state_writer

    async def get_current_state(self, entity_id: str) -> PromotionState:
        return await self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: PromotionState) -> list[PromotionEvent]:
        """Graph-level only. Business rules → PolicyProvider."""
        return [
            event
            for (s, event) in PROMOTION_TRANSITIONS
            if s == state
        ]

    async def transition(
        self,
        entity_id: str,
        event: PromotionEvent,
    ) -> TransitionResult:
        """
        Attempt transition. Writes new state via state_writer (terminal tool).
        state_writer is the ONLY place allowed to write promotion.state.
        """
        current = await self._read(entity_id)
        new_state = PROMOTION_TRANSITIONS.get((current, event))

        if new_state is None:
            logger.warning(
                "PromotionFSM: rejected %s event=%s from state=%s",
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

        await self._write(entity_id, new_state)  # terminal tool — atomic PG write
        logger.info(
            "PromotionFSM: %s %s → %s via %s",
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