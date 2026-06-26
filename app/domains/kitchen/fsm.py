"""
app/domains/kitchen/fsm.py
KitchenTicketFSM — M2 implementation.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from app.fsm.core.base import BaseFSM, TransitionResult


class KitchenState(str, Enum):
    NEW = "NEW"
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    READY = "READY"
    HANDED_OFF = "HANDED_OFF"


class KitchenEvent(str, Enum):
    QUEUE = "QUEUE"
    START_PREP = "START_PREP"
    MARK_READY = "MARK_READY"
    HAND_OFF = "HAND_OFF"


KITCHEN_TRANSITIONS: dict[KitchenState, dict[KitchenEvent, KitchenState]] = {
    KitchenState.NEW: {KitchenEvent.QUEUE: KitchenState.QUEUED},
    KitchenState.QUEUED: {KitchenEvent.START_PREP: KitchenState.PREPARING},
    KitchenState.PREPARING: {KitchenEvent.MARK_READY: KitchenState.READY},
    KitchenState.READY: {KitchenEvent.HAND_OFF: KitchenState.HANDED_OFF},
    KitchenState.HANDED_OFF: {},
}


class KitchenFSM(BaseFSM[KitchenState, KitchenEvent]):
    def __init__(self, state_reader: Any, state_writer: Any) -> None:
        self._read = state_reader
        self._write = state_writer

    async def get_current_state(self, entity_id: str) -> KitchenState:
        return await self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: KitchenState) -> list[KitchenEvent]:
        return list(KITCHEN_TRANSITIONS.get(state, {}).keys())

    async def transition(self, entity_id: str, event: KitchenEvent) -> TransitionResult:
        current = await self._read(entity_id)
        allowed = KITCHEN_TRANSITIONS.get(current, {})
        if event not in allowed:
            return TransitionResult(
                success=False,
                new_state=None,
                rejected_event=event,
                reason=f"Event {event!r} not allowed from state {current!r}",
            )
        new_state = allowed[event]
        await self._write(entity_id, new_state)  # terminal-tool
        return TransitionResult(success=True, new_state=new_state, rejected_event=None, reason=None)
