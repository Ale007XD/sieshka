"""
app/agents/menu_agent.py — MenuAgent: two-phase agent (collect, then apply).

COLLECT phase (ALLOWED: collect input, generate command; FORBIDDEN: mutate state):
    collect_menu() runs the LLM → validate → confirm flow and stops at a
    terminal JSON command. It writes NOTHING to Postgres.

APPLY phase (sprint_m7_agent_apply_phase_pattern):
    apply_menu() takes a confirmed command and runs the shared apply-phase
    CONVENTION Program (validate_command → CONDITION → apply_command | report_invalid).
    apply_command is the ONLY step in the whole agent allowed to write to
    Postgres, and it goes through the SAME GovernedToolExecutor.check() gate as
    every other write Tool. See app/agents/README.md for the CONVENTION.
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
    MENU_AGENT_APPLY_CATEGORY_POLICY_SNAPSHOT,
    MENU_AGENT_APPLY_POLICY_SNAPSHOT,
    MENU_AGENT_POLICY_SNAPSHOT,
    MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT,
)
from app.programs.menu_agent_program import (
    PROGRAM_APPLY_CATEGORY,
    PROGRAM_APPLY_MENU,
    PROGRAM_COLLECT_ORDER,
    PROGRAM_UPDATE_PRODUCT,
)
from app.tools.menu_agent_tools import (
    apply_category_command,
    apply_menu_command,
    apply_update_product_command,
    collect_menu_command,
    report_collect_failure,
    report_invalid_category_command,
    report_invalid_command,
    report_invalid_update_product_command,
    validate_apply_category_command,
    validate_apply_command,
    validate_menu_command,
    validate_update_product_command,
)

logger = logging.getLogger(__name__)


@dataclass
class MenuAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


@dataclass
class MenuApplyResult:
    """Outcome of the apply phase.

    applied=True  → the command landed in Postgres (Trace SUCCESS, valid branch).
    applied=False + error is None → command was rejected by validate (invalid
        branch reached its terminal cleanly; nothing written).
    applied=False + error set → the apply write failed (Trace FAILED; raised).
    """

    applied: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    trace_id: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class MenuAgent:
    """Collects menu input, generates structured command, then applies it.

    Usage:
        agent = MenuAgent()
        result = await agent.collect_menu({...})
        if result.success:
            apply = await agent.apply_menu(result.command)
    """

    ALLOWED = "collect input, generate command"
    FORBIDDEN = "modify menu state directly (collect phase only)"

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
        executor = GovernedToolExecutor(policy=MENU_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm

    def _build_apply_vm(self, session: AsyncSession) -> _VMProtocol:
        """Session-bound VM for the apply phase.

        The write tools take a `session` first parameter closure-injected via
        functools.partial (never serialised in Trace — CONSTRAINTS.md
        "Tool-authoring: side-effect session boundary"). Every tool is wrapped
        by GovernedToolExecutor so menu:write is enforced on the write step.
        """
        return self._build_generic_apply_vm(
            session, _APPLY_TOOLS, _APPLY_SESSION_TOOLS, MENU_AGENT_APPLY_POLICY_SNAPSHOT,
        )

    def _build_apply_category_vm(self, session: AsyncSession) -> _VMProtocol:
        """Session-bound VM for the category apply phase. Same wiring shape as
        _build_apply_vm — separate tool set (category tools), same policy
        snapshot family (menu:* capability domain), own PolicySnapshot per the
        apply-phase CONVENTION (policy_snapshot.py already had the 3 tool
        names + snapshot pre-provisioned: MENU_AGENT_APPLY_CATEGORY_POLICY_SNAPSHOT)."""
        return self._build_generic_apply_vm(
            session, _APPLY_CATEGORY_TOOLS, _APPLY_CATEGORY_SESSION_TOOLS,
            MENU_AGENT_APPLY_CATEGORY_POLICY_SNAPSHOT,
        )

    def _build_generic_apply_vm(
        self,
        session: AsyncSession,
        tools: dict[str, Callable[..., Any]],
        session_tools: frozenset[str],
        policy: Any,
    ) -> _VMProtocol:
        from nano_vm.adapters import MockLLMAdapter
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(
            llm=MockLLMAdapter(""),
            cursor_repository=cursor,
        )
        executor = GovernedToolExecutor(policy=policy)
        for name, fn in tools.items():
            governed = _governed_tool(fn, name, executor)
            if name in session_tools:
                vm.register_tool(name, functools.partial(governed, session=session))
            else:
                vm.register_tool(name, governed)
        return vm

    async def collect_menu(self, input_data: dict[str, Any]) -> MenuAgentResult:
        """Process raw menu input and return a structured command.

        Args:
            input_data: dict with keys 'input_text', 'menu_id',
                       'items' (optional), 'category' (optional).

        Returns:
            MenuAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
            "menu_id": input_data.get("menu_id", ""),
            "items": input_data.get("items", []),
            "category": input_data.get("category", ""),
        }

        _report = ProgramValidator(PROGRAM_COLLECT_ORDER).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_COLLECT_ORDER.name}' validation failed: "
                f"{_report.summary()}"
            )

        trace = await vm.run(PROGRAM_COLLECT_ORDER, context=context)

        if trace.status == TraceStatus.SUCCESS:
            confirm_step = next(
                (s for s in trace.steps if s.step_id == "confirm_command"), None
            )
            if confirm_step and confirm_step.output:
                raw = str(confirm_step.output)
                try:
                    command = json.loads(raw)
                    return MenuAgentResult(
                        success=True, command=command, raw_output=raw,
                    )
                except (json.JSONDecodeError, ValueError):
                    return MenuAgentResult(
                        success=True, command=None, raw_output=raw,
                    )

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            if fail_step:
                raw = str(fail_step.output) if fail_step.output else ""
                error_msg = raw or "Command validation failed"
                return MenuAgentResult(success=False, error=error_msg)

            return MenuAgentResult(
                success=False, error="No command output in trace",
            )

        error_msg = trace.error or "Agent execution failed"
        return MenuAgentResult(
            success=False, error=error_msg,
        )

    async def apply_menu(self, command: dict[str, Any]) -> MenuApplyResult:
        """Apply a confirmed command to Postgres via the governed apply Program.

        The apply command schema written to the products table is:
          {name: str, category: str, price_rub: int,
           description?: str, image_url?: str}
        (`category` is resolved to a category row by name at write time.)

        Commit/rollback is owned here (the caller of the write tools), NOT inside
        the tool — see CONSTRAINTS.md "Tool-authoring: side-effect session
        boundary". SUCCESS commits; a FAILED trace (the write raised) rolls back.
        """
        _report = ProgramValidator(PROGRAM_APPLY_MENU).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_APPLY_MENU.name}' validation failed: "
                f"{_report.summary()}"
            )

        # Injected VM (tests) takes over session wiring itself.
        if self._apply_vm is not None:
            return await self._run_apply(self._apply_vm, PROGRAM_APPLY_MENU, command, session=None)

        if self._session_factory is None:
            from app.db import async_session_factory

            self._session_factory = async_session_factory

        async with self._session_factory() as session:
            vm = self._build_apply_vm(session)
            return await self._run_apply(vm, PROGRAM_APPLY_MENU, command, session=session)

    async def apply_category(self, command: dict[str, Any]) -> MenuApplyResult:
        """Apply a confirmed category command to Postgres via the governed apply Program.

        The apply command schema written to the categories table is:
          {name: str, parent_category?: str,
           menu_period?: "both"|"delivery"|"pickup", sort?: int}
        (`parent_category` is resolved to a category row by name at write time.)

        Commit/rollback follows the same convention as apply_menu: SUCCESS
        commits; a FAILED trace rolls back (CONSTRAINTS.md "Tool-authoring:
        side-effect session boundary").
        """
        _report = ProgramValidator(PROGRAM_APPLY_CATEGORY).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_APPLY_CATEGORY.name}' validation failed: "
                f"{_report.summary()}"
            )

        if self._apply_vm is not None:
            return await self._run_apply(
                self._apply_vm, PROGRAM_APPLY_CATEGORY, command, session=None
            )

        if self._session_factory is None:
            from app.db import async_session_factory

            self._session_factory = async_session_factory

        async with self._session_factory() as session:
            vm = self._build_apply_category_vm(session)
            return await self._run_apply(
                vm, PROGRAM_APPLY_CATEGORY, command, session=session
            )

    async def update_product(self, command: dict[str, Any]) -> MenuApplyResult:
        """Update an existing product via the governed update Program.

        Command schema: {product_id: str (UUID), name?: str, category?: str,
                         price_rub?: int, description?: str, image_url?: str,
                         is_active?: bool}
        Only non-None fields are written; absent fields are left unchanged.
        Commit/rollback at-caller convention, same as apply_menu/apply_category.
        """
        _report = ProgramValidator(PROGRAM_UPDATE_PRODUCT).validate()
        if not _report.is_valid():
            raise RuntimeError(
                f"Program '{PROGRAM_UPDATE_PRODUCT.name}' validation failed: "
                f"{_report.summary()}"
            )

        if self._apply_vm is not None:
            return await self._run_apply(
                self._apply_vm, PROGRAM_UPDATE_PRODUCT, command, session=None
            )

        if self._session_factory is None:
            from app.db import async_session_factory

            self._session_factory = async_session_factory

        async with self._session_factory() as session:
            vm = self._build_generic_apply_vm(
                session,
                _UPDATE_PRODUCT_TOOLS,
                _UPDATE_PRODUCT_SESSION_TOOLS,
                MENU_AGENT_UPDATE_PRODUCT_POLICY_SNAPSHOT,
            )
            return await self._run_apply(
                vm, PROGRAM_UPDATE_PRODUCT, command, session=session
            )

    async def _run_apply(
        self,
        vm: _VMProtocol,
        program: Program,
        command: dict[str, Any],
        session: AsyncSession | None,
    ) -> MenuApplyResult:
        trace = await vm.run(program, context={"command": command})

        if trace.status == TraceStatus.SUCCESS:
            apply_step = next(
                (s for s in trace.steps if s.step_id == "apply_command"), None
            )
            if apply_step is not None and apply_step.output is not None:
                if session is not None:
                    await session.commit()
                out = apply_step.output
                result = out if isinstance(out, dict) else {"output": out}
                return MenuApplyResult(applied=True, result=result, trace_id=trace.trace_id)

            # Invalid branch reached its terminal cleanly — nothing written.
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
            logger.info("_run_apply: command rejected (%s)", reason)
            return MenuApplyResult(applied=False, error=None, trace_id=trace.trace_id)

        # Trace FAILED — the write raised. Roll back; surface the error.
        if session is not None:
            await session.rollback()
        error_msg = trace.error or "apply execution failed"
        logger.error("_run_apply: apply failed — %s", error_msg)
        return MenuApplyResult(applied=False, error=error_msg, trace_id=trace.trace_id)


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
    "validate_menu_command": validate_menu_command,
    "collect_menu_command": collect_menu_command,
    "report_collect_failure": report_collect_failure,
}

_APPLY_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_apply_command": validate_apply_command,
    "apply_menu_command": apply_menu_command,
    "report_invalid_command": report_invalid_command,
}

# Apply-phase tools that need the closure-injected session (DB side-effect).
_APPLY_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_apply_command",
    "apply_menu_command",
})

_APPLY_CATEGORY_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_apply_category_command": validate_apply_category_command,
    "apply_category_command": apply_category_command,
    "report_invalid_category_command": report_invalid_category_command,
}

# Category apply-phase tools that need the closure-injected session.
_APPLY_CATEGORY_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_apply_category_command",
    "apply_category_command",
})

_UPDATE_PRODUCT_TOOLS: dict[str, Callable[..., Any]] = {
    "validate_update_product_command": validate_update_product_command,
    "apply_update_product_command": apply_update_product_command,
    "report_invalid_update_product_command": report_invalid_update_product_command,
}

# Product update-phase tools that need the closure-injected session.
_UPDATE_PRODUCT_SESSION_TOOLS: frozenset[str] = frozenset({
    "validate_update_product_command",
    "apply_update_product_command",
})
