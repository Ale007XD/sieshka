"""
app/agents/promotion_agent.py — PromotionAgent: manages promotional campaigns.

ALLOWED: create/modify promotion config (metadata only)
FORBIDDEN: execute promotions directly against customers

Agent output goes through GovernedToolExecutor — never directly to repository/PG.
"""
from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from nano_vm.models import Program, Trace, TraceStatus
from nano_vm.validator import ProgramValidator
from nano_vm_mcp.handlers import GovernedToolExecutor
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.policy.policy_snapshot import (
    PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT,
    PROMOTION_AGENT_POLICY_SNAPSHOT,
)
from app.programs.promotion_agent_program import (
    PROGRAM_APPLY_PROMOTION,
    PROGRAM_COLLECT_PROMOTION,
)
from app.tools.promotion_agent_tools import (
    apply_promotion_command,
    collect_promotion_command,
    report_collect_failure,
    report_invalid_promotion_command,
    validate_apply_promotion_command,
    validate_promotion_command,
)

logger = logging.getLogger(__name__)


@dataclass
class PromotionAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


@dataclass
class PromotionApplyResult:
    """Outcome of the apply phase — same contract as ZoneApplyResult/MenuApplyResult.

    applied=True  -> the command landed in Postgres (Trace SUCCESS, valid branch).
    applied=False + error is None -> command rejected by validate (invalid
        branch reached its terminal cleanly; nothing written).
    applied=False + error set -> the apply write failed (Trace FAILED; raised).
    """

    applied: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    trace_id: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class PromotionAgent:
    """Manages promotional campaign configuration (NOT execution).

    Usage:
        agent = PromotionAgent()
        result = await agent.manage_promotion({
            "input_text": "Create a 20% off summer sale",
        })
        if result.success:
            command = result.command  # validated structured command dict
            apply = await agent.apply_promotion(command)
    """

    ALLOWED = "create/modify promotion config (metadata only)"
    FORBIDDEN = "execute promotions directly against customers"

    def __init__(
        self,
        vm: _VMProtocol | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        apply_vm: _VMProtocol | None = None,
    ) -> None:
        self._vm = vm
        self._session_factory = session_factory
        self._apply_vm = apply_vm

    def _build_vm(self) -> _VMProtocol:
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store
        from app.llm.fallback import FallbackLLMAdapter

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(
            llm=FallbackLLMAdapter(),
            cursor_repository=cursor,
        )
        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm

    def _build_apply_vm(self, session: AsyncSession) -> _VMProtocol:
        """Session-bound VM for the apply phase — same wiring shape as
        ZoneAgent._build_apply_vm / MenuAgent._build_apply_vm."""
        from nano_vm.adapters import MockLLMAdapter
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_APPLY_POLICY_SNAPSHOT)
        for name, fn in _APPLY_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            if name in _APPLY_SESSION_TOOLS:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)
        return vm

    async def manage_promotion(self, input_data: dict[str, Any]) -> PromotionAgentResult:
        """Process raw promotion input and return a structured command.

        Args:
            input_data: dict with key 'input_text' (the natural-language
                        instruction).

        Returns:
            PromotionAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
        }

        _report = ProgramValidator(PROGRAM_COLLECT_PROMOTION).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_COLLECT_PROMOTION.name}' validation failed: "
                f"{_report.summary()}"
            )

        trace = await vm.run(PROGRAM_COLLECT_PROMOTION, context=context)

        if trace.status == TraceStatus.SUCCESS:
            confirm_step = next(
                (s for s in trace.steps if s.step_id == "confirm_command"), None
            )
            if confirm_step and confirm_step.output:
                raw = str(confirm_step.output)
                try:
                    command = json.loads(raw)
                    return PromotionAgentResult(
                        success=True, command=command, raw_output=raw,
                    )
                except (json.JSONDecodeError, ValueError):
                    return PromotionAgentResult(
                        success=True, command=None, raw_output=raw,
                    )

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            if fail_step:
                raw = str(fail_step.output) if fail_step.output else ""
                error_msg = raw or "Command validation failed"
                return PromotionAgentResult(success=False, error=error_msg)

            return PromotionAgentResult(
                success=False, error="No command output in trace",
            )

        error_msg = trace.error or "Agent execution failed"
        return PromotionAgentResult(
            success=False, error=error_msg,
        )

    async def apply_promotion(self, command: dict[str, Any]) -> PromotionApplyResult:
        """Apply a confirmed command via the governed apply Program.

        Commit/rollback owned here (caller of the write tools), not inside
        the tool — same convention as apply_zone/apply_menu/apply_category.
        """
        _report = ProgramValidator(PROGRAM_APPLY_PROMOTION).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_APPLY_PROMOTION.name}' validation failed: "
                f"{_report.summary()}"
            )

        if self._apply_vm is not None:
            return await self._run_apply(self._apply_vm, command, session=None)

        if self._session_factory is None:
            from app.db import async_session_factory

            self._session_factory = async_session_factory

        async with self._session_factory() as session:
            vm = self._build_apply_vm(session)
            return await self._run_apply(vm, command, session=session)

    async def _run_apply(
        self,
        vm: _VMProtocol,
        command: dict[str, Any],
        session: AsyncSession | None,
    ) -> PromotionApplyResult:
        trace = await vm.run(PROGRAM_APPLY_PROMOTION, context={"command": command})

        if trace.trace_id:
            from app.db_nano import get_store

            get_store().save_trace(
                trace_id=trace.trace_id,
                program_id=trace.program_name,
                status=trace.status.value,
                steps_count=len(trace.steps),
                total_cost=trace.total_cost_usd() or 0.0,
                trace=trace.model_dump(mode="json"),
            )

        if trace.status == TraceStatus.SUCCESS:
            apply_step = next(
                (s for s in trace.steps if s.step_id == "apply_command"), None
            )
            if apply_step is not None and apply_step.output is not None:
                if session is not None:
                    await session.commit()
                out = apply_step.output
                result = out if isinstance(out, dict) else {"output": out}
                return PromotionApplyResult(applied=True, result=result, trace_id=trace.trace_id)

            if session is not None:
                await session.rollback()
            invalid_step = next(
                (s for s in trace.steps if s.step_id == "report_invalid"), None
            )
            reason = (
                str(invalid_step.output)
                if invalid_step and invalid_step.output
                else "command rejected"
            )
            logger.info("apply_promotion: command rejected (%s)", reason)
            return PromotionApplyResult(applied=False, error=None, trace_id=trace.trace_id)

        if session is not None:
            await session.rollback()
        error_msg = trace.error or "apply execution failed"
        logger.error("apply_promotion: apply failed — %s", error_msg)
        return PromotionApplyResult(applied=False, error=error_msg, trace_id=trace.trace_id)


def _governed_tool(
    fn: Callable[..., Any],
    tool_name: str,
    executor: GovernedToolExecutor,
) -> Callable[..., Any]:
    async def wrapper(**kwargs: object) -> Any:
        executor.check(tool_name)
        return await fn(**kwargs)
    return wrapper


_AGENT_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_promotion_command": validate_promotion_command,
    "collect_promotion_command": collect_promotion_command,
    "report_collect_failure": report_collect_failure,
}

_APPLY_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_apply_promotion_command": validate_apply_promotion_command,
    "apply_promotion_command": apply_promotion_command,
    "report_invalid_promotion_command": report_invalid_promotion_command,
}

_APPLY_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_apply_promotion_command",
    "apply_promotion_command",
})
