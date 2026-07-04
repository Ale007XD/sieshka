"""
app/agents/order_agent.py — OrderAgent: collects input, generates structured command.

ALLOWED:  collect input, generate command
FORBIDDEN: modify order state directly (table §4)

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

from app.policy.policy_snapshot import ORDER_AGENT_POLICY_SNAPSHOT
from app.programs.order_agent_program import PROGRAM_COLLECT_ORDER
from app.tools.order_agent_tools import (
    collect_order_command,
    report_collect_failure,
    validate_order_command,
)

logger = logging.getLogger(__name__)


@dataclass
class OrderAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class OrderAgent:
    """Collects order input, generates structured command (NOT state mutation).

    Usage:
        agent = OrderAgent()
        result = await agent.collect_order({
            "input_text": "Customer wants 2 coffees delivered to Moscow",
            "customer_id": "uuid-here",
        })
        if result.success:
            command = result.command  # validated structured command dict
    """

    ALLOWED = "collect input, generate command"
    FORBIDDEN = "modify order state directly"

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
        executor = GovernedToolExecutor(policy=ORDER_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm

    async def collect_order(self, input_data: dict[str, Any]) -> OrderAgentResult:
        """Process raw order input and return a structured command.

        Args:
            input_data: dict with keys 'input_text', 'customer_id',
                       'items' (optional), 'delivery_address' (optional).

        Returns:
            OrderAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
            "customer_id": input_data.get("customer_id", ""),
            "items": input_data.get("items", []),
            "delivery_address": input_data.get("delivery_address", ""),
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
                    return OrderAgentResult(
                        success=True, command=command, raw_output=raw,
                    )
                except (json.JSONDecodeError, ValueError):
                    return OrderAgentResult(
                        success=True, command=None, raw_output=raw,
                    )

            fail_step = next(
                (s for s in trace.steps if s.step_id == "validation_failed"), None
            )
            if fail_step:
                raw = str(fail_step.output) if fail_step.output else ""
                error_msg = raw or "Command validation failed"
                return OrderAgentResult(success=False, error=error_msg)

            return OrderAgentResult(
                success=False, error="No command output in trace",
            )

        error_msg = trace.error or "Agent execution failed"
        return OrderAgentResult(
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
    "validate_order_command": validate_order_command,
    "collect_order_command": collect_order_command,
    "report_collect_failure": report_collect_failure,
}
