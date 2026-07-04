"""
app/agents/support_agent.py — SupportAgent: handles customer support requests.

ALLOWED: collect/support ticket details, generate structured command
FORBIDDEN: resolve tickets directly (state mutation)

Agent output goes through GovernedToolExecutor — never directly to repository/PG.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from nano_vm.models import Program, Trace, TraceStatus
from nano_vm.validator import ProgramValidator
from nano_vm_mcp.handlers import GovernedToolExecutor

from app.policy.policy_snapshot import SUPPORT_AGENT_POLICY_SNAPSHOT
from app.programs.support_agent_program import PROGRAM_COLLECT_ORDER
from app.tools.support_agent_tools import (
    collect_support_command,
    report_collect_failure,
    validate_support_command,
)

logger = logging.getLogger(__name__)


@dataclass
class SupportAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class SupportAgent:
    """Collects support input, generates structured command (NOT resolution).

    Usage:
        agent = SupportAgent()
        result = await agent.collect_support({
            "input_text": "Cannot login to account",
            "ticket_id": "ticket-uuid-here",
            "customer_id": "customer-uuid-here",
        })
        if result.success:
            command = result.command  # validated structured command dict
    """

    ALLOWED = "collect/support ticket details, generate structured command"
    FORBIDDEN = "resolve tickets directly (state mutation)"

    def __init__(self, vm: _VMProtocol | None = None) -> None:
        self._vm = vm

    def _build_vm(self) -> _VMProtocol:
        from nano_vm.adapters import MockLLMAdapter
        from nano_vm.vm import ExecutionVM

        from app.db_nano import StoreCursorRepository, get_store

        cursor = StoreCursorRepository(get_store())
        vm = ExecutionVM(
            llm=MockLLMAdapter(""),
            cursor_repository=cursor,
        )
        executor = GovernedToolExecutor(policy=SUPPORT_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm  # type: ignore[no-any-return]

    async def collect_support(self, input_data: dict[str, Any]) -> SupportAgentResult:
        """Process raw support input and return a structured command.

        Args:
            input_data: dict with keys 'input_text', 'ticket_id',
                       'customer_id', 'issue_type' (optional).

        Returns:
            SupportAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
            "ticket_id": input_data.get("ticket_id", ""),
            "customer_id": input_data.get("customer_id", ""),
            "issue_type": input_data.get("issue_type", ""),
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
                    return SupportAgentResult(
                        success=True, command=command, raw_output=raw,
                    )
                except (json.JSONDecodeError, ValueError):
                    return SupportAgentResult(
                        success=True, command=None, raw_output=raw,
                    )

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            if fail_step:
                raw = str(fail_step.output) if fail_step.output else ""
                error_msg = raw or "Command validation failed"
                return SupportAgentResult(success=False, error=error_msg)

            return SupportAgentResult(
                success=False, error="No command output in trace",
            )

        error_msg = trace.error or "Agent execution failed"
        return SupportAgentResult(
            success=False, error=error_msg,
        )


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
    "validate_support_command": validate_support_command,
    "collect_support_command": collect_support_command,
    "report_collect_failure": report_collect_failure,
}
