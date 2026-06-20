"""
app/fsm/core/base.py
BaseFSM — event-driven transition executor.
Graph-only: no business logic, no PolicyProvider calls.
Business rules → Application Service → PolicyProvider → BaseFSM.transition()
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

StateType = TypeVar("StateType")
EventType = TypeVar("EventType")


@dataclass(frozen=True)
class TransitionResult:
    """
    Result of a single FSM transition.
    M3: StepResult from nano-vm is the natural successor of TransitionResult.
    """

    success: bool
    new_state: object | None  # None if transition rejected
    rejected_event: object | None  # populated on rejection
    reason: str | None  # human-readable rejection reason


class BaseFSM(ABC, Generic[StateType, EventType]):
    """
    Abstract base for all domain FSMs.

    CONTRACT:
      - transition() accepts EVENT, never new_state
      - get_allowed_events() returns graph-level events ONLY
      - Business rules live in PolicyProvider, NOT here

    M1/M2: custom implementation (this class)
    M3: replaced by ExecutionVM + nano-vm-mcp as gateway
    """

    @abstractmethod
    def transition(
        self,
        entity_id: str,
        event: EventType,
    ) -> TransitionResult:
        """
        Attempt a transition triggered by event.
        FORBIDDEN: accepting new_state as parameter.
        FORBIDDEN: business rule checks inside this method.
        """
        ...

    @abstractmethod
    def get_current_state(self, entity_id: str) -> StateType:
        """Retrieve current state from storage (PostgreSQL entity table)."""
        ...

    @abstractmethod
    def get_allowed_events(self, state: StateType) -> list[EventType]:
        """
        Graph-level allowed events from given state.
        Does NOT reflect business rules — only the FSM graph structure.
        """
        ...

    def handle_event(
        self,
        entity_id: str,
        event: EventType,
    ) -> TransitionResult:
        """Alias for transition() — typed event variant."""
        return self.transition(entity_id, event)
