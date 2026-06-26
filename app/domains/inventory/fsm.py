"""
app/domains/inventory/fsm.py
InventoryFSM — M1 custom implementation.
M3: replaced by ExecutionVM + nano-vm-mcp.

CONSTRAINT: transition() accepts EVENT not new_state.
CONSTRAINT: graph-only — no business rules inside.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.inventory.models import INVENTORY_TRANSITIONS, InventoryEvent, InventoryState
from app.fsm.core.base import BaseFSM, TransitionResult

logger = logging.getLogger(__name__)


class InventoryFSM(BaseFSM[InventoryState, InventoryEvent]):
    """
    In-memory + callback-based InventoryFSM for M1.
    state_reader/state_writer: callables injected by Application Service.
    This keeps FSM testable without a database.
    """

    def __init__(
        self,
        state_reader: Any,  # Callable[[str], InventoryState]
        state_writer: Any,  # Callable[[str, InventoryState], None]
    ) -> None:
        self._read = state_reader
        self._write = state_writer

    async def get_current_state(self, entity_id: str) -> InventoryState:
        return await self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: InventoryState) -> list[InventoryEvent]:
        """Graph-level only. Business rules → PolicyProvider."""
        return [
            event
            for (s, event) in INVENTORY_TRANSITIONS
            if s == state
        ]

    async def transition(
        self,
        entity_id: str,
        event: InventoryEvent,
    ) -> TransitionResult:
        """
        Attempt transition. Writes new state via state_writer (terminal tool).
        state_writer is the ONLY place allowed to write inventory.state.
        """
        current = await self._read(entity_id)
        new_state = INVENTORY_TRANSITIONS.get((current, event))

        if new_state is None:
            logger.warning(
                "InventoryFSM: rejected %s event=%s from state=%s",
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
            "InventoryFSM: %s %s → %s via %s",
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