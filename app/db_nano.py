"""app/db_nano.py — nano-vm-mcp SQLite WAL store init (separate from PG engine)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nano_vm.models import StateContext, Trace
from nano_vm_mcp.store import ProgramStore

from app.config import settings

_store: ProgramStore | None = None


def get_store() -> ProgramStore:
    global _store
    if _store is None:
        Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
        _store = ProgramStore(settings.SQLITE_PATH)
    return _store


class StoreCursorRepository:
    """Bridges ExecutionVM CursorRepository protocol with ProgramStore.
    
    Persists suspend/resume state via ProgramStore state_contexts + traces tables.
    """

    def __init__(self, store: ProgramStore) -> None:
        self._store = store

    async def save(
        self, trace_id: str, step_id: str, state: StateContext, trace: Trace
    ) -> None:
        ctx_dict: dict[str, Any] = {
            "step_id": step_id,
            "data": state.data,
            "step_outputs": state.step_outputs,
        }
        self._store.save_state_context(trace_id, ctx_dict)
        self._store.save_trace(
            trace_id=trace_id,
            program_id=trace.program_name,
            status=trace.status.value,
            steps_count=len(trace.steps),
            total_cost=trace.total_cost_usd() or 0.0,
            trace=trace.model_dump(),
        )

    async def load(
        self, trace_id: str
    ) -> tuple[str, StateContext, Trace] | None:
        ctx_dict = self._store.load_state_context(trace_id)
        if ctx_dict is None:
            return None
        step_id = ctx_dict.get("step_id", "")
        state = StateContext(
            data=ctx_dict.get("data", {}),
            step_outputs=ctx_dict.get("step_outputs", {}),
        )
        trace_data = self._store.get_trace(trace_id)
        if trace_data is None:
            return None
        trace = Trace.model_validate(trace_data)
        return step_id, state, trace

    async def delete(self, trace_id: str) -> None:
        self._store.delete_state_context(trace_id)
