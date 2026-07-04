"""
app/agents/promotion_agent.py — PromotionAgent: manages promotional campaigns.

ALLOWED: create/modify promotion config (metadata only)
FORBIDDEN: execute promotions directly against customers

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

from app.policy.policy_snapshot import PROMOTION_AGENT_POLICY_SNAPSHOT
from app.programs.promotion_agent_program import PROGRAM_COLLECT_ORDER
from app.tools.promotion_agent_tools import (
    collect_promotion_command,
    report_collect_failure,
    validate_promotion_command,
)

logger = logging.getLogger(__name__)


@dataclass
class PromotionAgentResult:
    success: bool
    command: dict[str, Any] | None = None
    raw_output: str | None = None
    error: str | None = None


class _VMProtocol(Protocol):
    async def run(self, program: Program, context: dict[str, Any] | None = None) -> Trace: ...
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None: ...


class PromotionAgent:
    """Manages promotional campaign configuration (NOT execution).

    Usage:
        agent = PromotionAgent()
        result = await agent.manage_promotion({
            "input_text": "Create a 20% off summer sale",
            "promotion_id": "promo-uuid-here",
        })
        if result.success:
            command = result.command  # validated structured command dict
    """

    ALLOWED = "create/modify promotion config (metadata only)"
    FORBIDDEN = "execute promotions directly against customers"

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
        executor = GovernedToolExecutor(policy=PROMOTION_AGENT_POLICY_SNAPSHOT)
        for name, fn in _AGENT_TOOLS.items():
            governed = _governed_tool(fn, name, executor)
            vm.register_tool(name, governed)
        return vm

    async def manage_promotion(self, input_data: dict[str, Any]) -> PromotionAgentResult:
        """Process raw promotion input and return a structured command.

        Args:
            input_data: dict with keys 'input_text', 'promotion_id',
                       'discount' (optional), 'start_date' (optional).

        Returns:
            PromotionAgentResult with success flag and structured command dict.
        """
        vm = self._vm if self._vm is not None else self._build_vm()
        context: dict[str, Any] = {
            "input_text": input_data.get("input_text", ""),
            "promotion_id": input_data.get("promotion_id", ""),
            "discount": input_data.get("discount", 0),
            "start_date": input_data.get("start_date", ""),
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
