"""
app/agents/inventory_agent.py — InventoryAgent: restocks inventory from a
free-text instruction (sprint_inventory_restock_agent, 2026-08).

ALLOWED: add stock to an existing sku (increment_inventory, reason=RESTOCK_AGENT)
FORBIDDEN: create new sku rows, set an absolute quantity (that's the inline-edit
  admin action from sprint_inventory_restock_inline), touch any table besides
  inventory/inventory_movements.

Agent output goes through GovernedToolExecutor — never directly to repository/PG.
Architecture mirrors PromotionAgent exactly (see app/agents/promotion_agent.py) —
same collect/apply two-Program split, same diagnostics side-channel, same
_governed_tool wrapper. Scoped down because restock has one action, not a
multi-action state machine like promotions (create/activate/expire/archive).
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
    INVENTORY_AGENT_APPLY_POLICY_SNAPSHOT,
    INVENTORY_AGENT_POLICY_SNAPSHOT,
)
from app.programs.inventory_agent_program import PROGRAM_APPLY_RESTOCK, PROGRAM_COLLECT_RESTOCK
from app.tools.inventory_agent_tools import (
    apply_restock_command,
    collect_restock_command,
    report_invalid_restock_command,
    validate_apply_restock_command,
    validate_restock_command,
)
from app.tools.promotion_agent_tools import report_collect_failure

logger = logging.getLogger(__name__)


@dataclass
class InventoryAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


@dataclass
class InventoryApplyResult:
    """Outcome of the apply phase — same contract as PromotionApplyResult."""

    applied: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    trace_id: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class InventoryAgent:
    """Restocks inventory from a free-text instruction.

    Usage:
        agent = InventoryAgent()
        collect = await agent.manage_restock({"input_text": "Добавь 50 на burger-firm"})
        if collect.success:
            apply = await agent.apply_restock(collect.command)
    """

    ALLOWED = "add stock to an existing sku"
    FORBIDDEN = "create new sku rows, set an absolute quantity, touch any other table"

    def __init__(
        self,
        vm: _VMProtocol | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        apply_vm: _VMProtocol | None = None,
    ) -> None:
        self._vm = vm
        self._session_factory = session_factory
        self._apply_vm = apply_vm

    def _build_vm(self, diagnostics: dict[str, str] | None = None) -> _VMProtocol:
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store
        from app.llm.fallback import FallbackLLMAdapter

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(llm=FallbackLLMAdapter(), cursor_repository=cursor)
        executor = GovernedToolExecutor(policy=INVENTORY_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            if name == "validate_restock_command" and diagnostics is not None:
                vm.register_tool(name, functools.partial(governed, diagnostics=diagnostics))
            else:
                vm.register_tool(name, governed)
        return vm

    def _build_apply_vm(
        self, session: AsyncSession, diagnostics: dict[str, str] | None = None
    ) -> _VMProtocol:
        """Session-bound VM for the apply phase — same wiring shape as
        PromotionAgent._build_apply_vm / MenuAgent._build_apply_vm."""
        from nano_vm.adapters import MockLLMAdapter
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(llm=MockLLMAdapter(""), cursor_repository=cursor)
        executor = GovernedToolExecutor(policy=INVENTORY_AGENT_APPLY_POLICY_SNAPSHOT)
        for name, fn in _APPLY_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            if name == "validate_apply_restock_command" and diagnostics is not None:
                vm.register_tool(
                    name, functools.partial(governed, session=session, diagnostics=diagnostics)
                )
            elif name in _APPLY_SESSION_TOOLS:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)
        return vm

    async def manage_restock(self, input_data: dict[str, Any]) -> InventoryAgentResult:
        """Process raw restock input and return a structured command.

        Args:
            input_data: dict with key 'input_text' (the natural-language
                        instruction).
        """
        diagnostics: dict[str, str] = {}
        vm = self._vm if self._vm is not None else self._build_vm(diagnostics)
        context: dict[str, Any] = {"input_text": input_data.get("input_text", "")}

        _report = ProgramValidator(PROGRAM_COLLECT_RESTOCK).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_COLLECT_RESTOCK.name}' validation failed: "
                f"{_report.summary()}"
            )

        trace = await vm.run(PROGRAM_COLLECT_RESTOCK, context=context)

        if trace.status == TraceStatus.SUCCESS:
            confirm_step = next(
                (s for s in trace.steps if s.step_id == "confirm_command"), None
            )
            if confirm_step and confirm_step.output:
                raw = str(confirm_step.output)
                try:
                    command = json.loads(raw)
                    return InventoryAgentResult(success=True, command=command, raw_output=raw)
                except (json.JSONDecodeError, ValueError):
                    return InventoryAgentResult(success=True, command=None, raw_output=raw)

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            error_msg = (
                diagnostics.get("reason")
                or (str(fail_step.output) if fail_step and fail_step.output else None)
                or "Command validation failed"
            )
            if fail_step:
                return InventoryAgentResult(success=False, error=error_msg)

            return InventoryAgentResult(
                success=False, error=diagnostics.get("reason") or "No command output in trace"
            )

        error_msg = trace.error or "Agent execution failed"
        return InventoryAgentResult(success=False, error=error_msg)

    async def apply_restock(self, command: dict[str, Any]) -> InventoryApplyResult:
        """Apply a confirmed command via the governed apply Program.

        Commit/rollback owned here (caller of the write tools), not inside
        the tool — same convention as apply_promotion/apply_zone/apply_menu.
        """
        _report = ProgramValidator(PROGRAM_APPLY_RESTOCK).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_APPLY_RESTOCK.name}' validation failed: "
                f"{_report.summary()}"
            )

        diagnostics: dict[str, str] = {}

        if self._apply_vm is not None:
            return await self._run_apply(
                self._apply_vm, command, session=None, diagnostics=diagnostics
            )

        if self._session_factory is None:
            from app.db import async_session_factory

            self._session_factory = async_session_factory

        async with self._session_factory() as session:
            vm = self._build_apply_vm(session, diagnostics)
            return await self._run_apply(vm, command, session=session, diagnostics=diagnostics)

    async def _run_apply(
        self,
        vm: _VMProtocol,
        command: dict[str, Any],
        session: AsyncSession | None,
        diagnostics: dict[str, str] | None = None,
    ) -> InventoryApplyResult:
        if diagnostics is None:
            diagnostics = {}
        trace = await vm.run(PROGRAM_APPLY_RESTOCK, context={"command": command})

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
                return InventoryApplyResult(applied=True, result=result, trace_id=trace.trace_id)

            if session is not None:
                await session.rollback()
            invalid_step = next(
                (s for s in trace.steps if s.step_id == "report_invalid"), None
            )
            reason = (
                diagnostics.get("reason")
                or (str(invalid_step.output) if invalid_step and invalid_step.output else None)
                or "command rejected"
            )
            logger.info("apply_restock: command rejected (%s)", reason)
            return InventoryApplyResult(applied=False, error=reason, trace_id=trace.trace_id)

        if session is not None:
            await session.rollback()
        error_msg = trace.error or "apply execution failed"
        logger.error("apply_restock: apply failed — %s", error_msg)
        return InventoryApplyResult(applied=False, error=error_msg, trace_id=trace.trace_id)


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
    "validate_restock_command": validate_restock_command,
    "collect_restock_command": collect_restock_command,
    "report_collect_failure": report_collect_failure,
}

_APPLY_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_apply_restock_command": validate_apply_restock_command,
    "apply_restock_command": apply_restock_command,
    "report_invalid_restock_command": report_invalid_restock_command,
}

_APPLY_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_apply_restock_command",
    "apply_restock_command",
})
