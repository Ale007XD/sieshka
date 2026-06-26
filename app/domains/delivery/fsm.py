"""app/domains/delivery/fsm.py"""
from __future__ import annotations

from enum import Enum
from typing import Any

from app.fsm.core.base import BaseFSM, TransitionResult


class DeliveryState(str, Enum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    ON_ROUTE = "ON_ROUTE"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class DeliveryEvent(str, Enum):
    ASSIGN = "ASSIGN"
    PICKUP = "PICKUP"
    START_ROUTE = "START_ROUTE"
    COMPLETE = "COMPLETE"
    FAIL = "FAIL"


DELIVERY_TRANSITIONS: dict[DeliveryState, dict[DeliveryEvent, DeliveryState]] = {
    DeliveryState.UNASSIGNED: {DeliveryEvent.ASSIGN: DeliveryState.ASSIGNED},
    DeliveryState.ASSIGNED: {
        DeliveryEvent.PICKUP: DeliveryState.PICKED_UP,
        DeliveryEvent.FAIL: DeliveryState.FAILED,
    },
    DeliveryState.PICKED_UP: {DeliveryEvent.START_ROUTE: DeliveryState.ON_ROUTE},
    DeliveryState.ON_ROUTE: {
        DeliveryEvent.COMPLETE: DeliveryState.DELIVERED,
        DeliveryEvent.FAIL: DeliveryState.FAILED,
    },
    DeliveryState.DELIVERED: {},
    DeliveryState.FAILED: {},
}


class DeliveryFSM(BaseFSM[DeliveryState, DeliveryEvent]):
    def __init__(self, state_reader: Any, state_writer: Any) -> None:
        self._read = state_reader
        self._write = state_writer

    async def get_current_state(self, entity_id: str) -> DeliveryState:
        return await self._read(entity_id)  # type: ignore[no-any-return]

    def get_allowed_events(self, state: DeliveryState) -> list[DeliveryEvent]:
        return list(DELIVERY_TRANSITIONS.get(state, {}).keys())

    async def transition(self, entity_id: str, event: DeliveryEvent) -> TransitionResult:
        current = await self._read(entity_id)
        allowed = DELIVERY_TRANSITIONS.get(current, {})
        if event not in allowed:
            return TransitionResult(
                success=False, new_state=None, rejected_event=event,
                reason=f"Event {event!r} not allowed from state {current!r}",
            )
        new_state = allowed[event]
        await self._write(entity_id, new_state)  # terminal-tool
        return TransitionResult(success=True, new_state=new_state, rejected_event=None, reason=None)
