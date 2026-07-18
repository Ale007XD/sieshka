"""
app/agents/zone_agent.py — ZoneAgent: two-phase agent (collect, then apply).

COLLECT phase (ALLOWED: collect input, generate command; FORBIDDEN: mutate state):
    collect_zone() runs the LLM -> validate -> confirm flow and stops at a
    terminal JSON command. It writes NOTHING to Postgres.

APPLY phase (sprint_m7_agent_apply_phase_pattern):
    apply_zone() takes a confirmed command and runs the shared apply-phase
    CONVENTION Program (validate_command -> CONDITION -> apply_command |
    report_invalid). apply_command is the ONLY step allowed to write to
    DeliveryZone, through the SAME GovernedToolExecutor.check() gate as every
    other write Tool. See app/agents/README.md for the CONVENTION.
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
    ZONE_AGENT_APPLY_POLICY_SNAPSHOT,
    ZONE_AGENT_POLICY_SNAPSHOT,
)
from app.programs.zone_agent_program import (
    PROGRAM_APPLY_ZONE,
    PROGRAM_COLLECT_ZONE,
)
from app.tools.zone_agent_tools import (
    apply_zone_command,
    collect_zone_command,
    report_collect_failure,
    report_invalid_zone_command,
    validate_apply_zone_command,
    validate_zone_command,
)

logger = logging.getLogger(__name__)


@dataclass
class ZoneAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


@dataclass
class ZoneApplyResult:
    """Outcome of the apply phase.

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
    async def run(
        self, program: Program, context: dict[str, Any] | None = None
    ) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class ZoneAgent:
    """Collects zone input, generates structured command, then applies it."""

    ALLOWED = "collect input, generate command"
    FORBIDDEN = "modify zone state directly (collect phase only)"

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
        executor = GovernedToolExecutor(policy=ZONE_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm

    def _build_apply_vm(self, session: AsyncSession) -> _VMProtocol:
        """Session-bound VM for the apply phase.

        Write tools take a `session` first parameter closure-injected via
        functools.partial (never serialised in Trace). Every tool is wrapped by
        GovernedToolExecutor so delivery:write is enforced on the write step.
        """
        from nano_vm.adapters import MockLLMAdapter
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(
            llm=MockLLMAdapter(""),
            cursor_repository=cursor,
        )
        executor = GovernedToolExecutor(policy=ZONE_AGENT_APPLY_POLICY_SNAPSHOT)
        for name, fn in _APPLY_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            if name in _APPLY_SESSION_TOOLS:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)
        return vm

    async def collect_zone(self, input_data: dict[str, Any]) -> ZoneAgentResult:
        """Process raw zone instruction and return a structured command.

        Args:
            input_data: dict with key 'input_text' (the natural-language
                        instruction).

        Returns:
            ZoneAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
        }

        _report = ProgramValidator(PROGRAM_COLLECT_ZONE).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_COLLECT_ZONE.name}' validation failed: "
                f"{_report.summary()}"
            )

        trace = await vm.run(PROGRAM_COLLECT_ZONE, context=context)

        if trace.status == TraceStatus.SUCCESS:
            confirm_step = next(
                (s for s in trace.steps if s.step_id == "confirm_command"), None
            )
            if confirm_step and confirm_step.output:
                raw = str(confirm_step.output)
                try:
                    command = json.loads(raw)
                    return ZoneAgentResult(
                        success=True, command=command, raw_output=raw,
                    )
                except (json.JSONDecodeError, ValueError):
                    return ZoneAgentResult(
                        success=True, command=None, raw_output=raw,
                    )

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            if fail_step:
                raw = str(fail_step.output) if fail_step.output else ""
                error_msg = raw or "Command validation failed"
                return ZoneAgentResult(success=False, error=error_msg)

            return ZoneAgentResult(
                success=False, error="No command output in trace",
            )

        error_msg = trace.error or "Agent execution failed"
        return ZoneAgentResult(success=False, error=error_msg)

    async def apply_zone(self, command: dict[str, Any]) -> ZoneApplyResult:
        """Apply a confirmed command to Postgres via the governed apply Program.

        Commit/rollback is owned here (the caller of the write tools), NOT inside
        the tool. SUCCESS commits; a FAILED trace (the write raised) rolls back.
        """
        _report = ProgramValidator(PROGRAM_APPLY_ZONE).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_APPLY_ZONE.name}' validation failed: "
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
    ) -> ZoneApplyResult:
        trace = await vm.run(PROGRAM_APPLY_ZONE, context={"command": command})
        trace_id = trace.trace_id

        if trace.status == TraceStatus.SUCCESS:
            apply_step = next(
                (s for s in trace.steps if s.step_id == "apply_command"), None
            )
            if apply_step is not None and apply_step.output is not None:
                if session is not None:
                    await session.commit()
                out = apply_step.output
                result = out if isinstance(out, dict) else {"output": out}
                return ZoneApplyResult(
                    applied=True, result=result, trace_id=trace_id
                )

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
            logger.info("apply_zone: command rejected (%s)", reason)
            return ZoneApplyResult(applied=False, error=None, trace_id=trace_id)

        if session is not None:
            await session.rollback()
        error_msg = trace.error or "apply execution failed"
        logger.error("apply_zone: apply failed — %s", error_msg)
        return ZoneApplyResult(applied=False, error=error_msg, trace_id=trace_id)


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
    "validate_zone_command": validate_zone_command,
    "collect_zone_command": collect_zone_command,
    "report_collect_failure": report_collect_failure,
}

_APPLY_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_apply_zone_command": validate_apply_zone_command,
    "apply_zone_command": apply_zone_command,
    "report_invalid_zone_command": report_invalid_zone_command,
}

# Apply-phase tools that need the closure-injected session (DB side-effect).
_APPLY_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_apply_zone_command",
    "apply_zone_command",
})
