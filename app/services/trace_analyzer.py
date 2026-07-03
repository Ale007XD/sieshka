from __future__ import annotations

from nano_vm.models import StepStatus, Trace, TraceStatus
from nano_vm_mcp.store import ProgramStore
from pydantic import BaseModel

from app.db_nano import StoreCursorRepository, get_store


class RejectedTransition(BaseModel):
    step_id: str
    step_index: int
    error: str


class TraceHealthReport(BaseModel):
    total_steps: int
    successful_steps: int
    failed_steps: int
    total_duration_ms: float | None = None
    total_cost_usd: float | None = None


class ExecutionReceipt(BaseModel):
    trace_id: str
    trace_hash: str
    final_status: str
    resumable: bool
    replayable: bool
    blocked_actions: int = 0
    escalations: int = 0
    rejected_transitions: tuple[RejectedTransition, ...]
    health: TraceHealthReport


class TraceAnalyzer:
    def __init__(self, store: ProgramStore | None = None) -> None:
        self._store = store or get_store()
        self._cursor = StoreCursorRepository(self._store)

    async def receipt(self, trace_id: str) -> ExecutionReceipt:
        result = await self._cursor.load(trace_id)
        if result is None:
            raise ValueError(f"Trace {trace_id!r} not found")
        _, _, trace = result
        return self._build_receipt(trace)

    @staticmethod
    def _build_receipt(trace: Trace) -> ExecutionReceipt:
        rejected: list[RejectedTransition] = []
        for idx, step in enumerate(trace.steps):
            if step.status == StepStatus.FAILED:
                rejected.append(
                    RejectedTransition(
                        step_id=step.step_id,
                        step_index=idx,
                        error=step.error or "Unknown error",
                    )
                )

        health = TraceHealthReport(
            total_steps=len(trace.steps),
            successful_steps=sum(
                1 for s in trace.steps if s.status == StepStatus.SUCCESS
            ),
            failed_steps=sum(
                1 for s in trace.steps if s.status == StepStatus.FAILED
            ),
            total_duration_ms=trace.duration_ms,
            total_cost_usd=trace.total_cost_usd(),
        )

        return ExecutionReceipt(
            trace_id=trace.trace_id,
            trace_hash=trace.canonical_snapshot_hash(),
            final_status=trace.status.value,
            resumable=trace.status == TraceStatus.SUSPENDED,
            replayable=trace.status == TraceStatus.FAILED,
            blocked_actions=0,
            escalations=0,
            rejected_transitions=tuple(rejected),
            health=health,
        )
