"""tests/unit/test_inventory_agent.py — InventoryAgent unit tests with a
mocked VM. sprint_inventory_restock_agent (2026-08)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import Program, Step, StepResult, StepStatus, StepType, Trace, TraceStatus

from app.agents.inventory_agent import InventoryAgent

_VALID_RESTOCK_COMMAND_JSON = '{"sku": "burger-firm", "quantity": 50}'
_INVALID_RESTOCK_COMMAND_JSON = "not valid json"


def _make_collect_trace_success(output: str) -> Trace:
    return Trace(
        program_name="inventory_agent_collect",
        status=TraceStatus.SUCCESS,
        steps=[
            StepResult(step_id="process_input", status=StepStatus.SUCCESS, output=output),
            StepResult(step_id="validate_command", status=StepStatus.SUCCESS, output=1),
            StepResult(
                step_id="check_valid", status=StepStatus.SUCCESS, output="confirm_command"
            ),
            StepResult(step_id="confirm_command", status=StepStatus.SUCCESS, output=output),
        ],
        final_output=output,
    )


def _make_collect_trace_failure() -> Trace:
    return Trace(
        program_name="inventory_agent_collect",
        status=TraceStatus.SUCCESS,
        steps=[
            StepResult(
                step_id="process_input", status=StepStatus.SUCCESS,
                output=_INVALID_RESTOCK_COMMAND_JSON,
            ),
            StepResult(step_id="validate_command", status=StepStatus.SUCCESS, output=0),
            StepResult(
                step_id="check_valid", status=StepStatus.SUCCESS, output="validation_failed"
            ),
            StepResult(
                step_id="validation_failed", status=StepStatus.SUCCESS, output="FAILED:0"
            ),
        ],
        final_output="FAILED:0",
    )


def _make_collect_trace_llm_error() -> Trace:
    return Trace(
        program_name="inventory_agent_collect",
        status=TraceStatus.FAILED,
        steps=[
            StepResult(
                step_id="process_input", status=StepStatus.FAILED, output="",
                error="LLM call failed",
            ),
        ],
        error="LLM call failed",
    )


def _make_apply_trace_success() -> Trace:
    return Trace(
        program_name="inventory_agent_apply",
        status=TraceStatus.SUCCESS,
        trace_id="test-trace-1",
        steps=[
            StepResult(step_id="validate_command", status=StepStatus.SUCCESS, output=1),
            StepResult(
                step_id="check_valid", status=StepStatus.SUCCESS, output="apply_command"
            ),
            StepResult(
                step_id="apply_command", status=StepStatus.SUCCESS,
                output={"applied": True, "sku": "burger-firm", "quantity": 50},
            ),
        ],
    )


def _make_apply_trace_rejected() -> Trace:
    return Trace(
        program_name="inventory_agent_apply",
        status=TraceStatus.SUCCESS,
        trace_id="test-trace-2",
        steps=[
            StepResult(step_id="validate_command", status=StepStatus.SUCCESS, output=0),
            StepResult(
                step_id="check_valid", status=StepStatus.SUCCESS, output="report_invalid"
            ),
            StepResult(
                step_id="report_invalid", status=StepStatus.SUCCESS,
                output="REJECTED:sku not found",
            ),
        ],
    )


class TestManageRestock:
    async def test_success(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_collect_trace_success(_VALID_RESTOCK_COMMAND_JSON)
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(vm=mock_vm)
        result = await agent.manage_restock({"input_text": "Добавь 50 на burger-firm"})

        assert result.success is True
        assert result.command == {"sku": "burger-firm", "quantity": 50}
        assert result.error is None

    async def test_invalid_json(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_collect_trace_failure()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(vm=mock_vm)
        result = await agent.manage_restock({"input_text": "gibberish"})

        assert result.success is False
        assert result.command is None
        assert result.error is not None

    async def test_llm_failure(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_collect_trace_llm_error()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(vm=mock_vm)
        result = await agent.manage_restock({"input_text": "restock something"})

        assert result.success is False
        assert result.error == "LLM call failed"

    async def test_real_vm_valid_json(self) -> None:
        """Integration-style: real VM + MockLLMAdapter returning valid JSON."""
        llm = MockLLMAdapter(_VALID_RESTOCK_COMMAND_JSON)
        agent = InventoryAgent()
        with patch.object(agent, "_build_vm") as mock_build:
            from nano_vm.vm import ExecutionVM
            from nano_vm_mcp.handlers import GovernedToolExecutor

            from app.agents.inventory_agent import _AGENT_TOOLS, _governed_tool
            from app.db_nano import StoreCursorRepository, get_store
            from app.policy.policy_snapshot import INVENTORY_AGENT_POLICY_SNAPSHOT

            cursor = StoreCursorRepository(get_store())
            vm = ExecutionVM(llm=llm, cursor_repository=cursor)
            executor = GovernedToolExecutor(policy=INVENTORY_AGENT_POLICY_SNAPSHOT)
            for name, fn in _AGENT_TOOLS.items():
                governed = _governed_tool(fn, name, executor)
                vm.register_tool(name, governed)
            mock_build.return_value = vm

            result = await agent.manage_restock({"input_text": "Добавь 50 на burger-firm"})

        assert result.success is True
        assert result.command == {"sku": "burger-firm", "quantity": 50}

    async def test_real_vm_invalid_json(self) -> None:
        llm = MockLLMAdapter("not valid json at all")
        agent = InventoryAgent()
        with patch.object(agent, "_build_vm") as mock_build:
            from nano_vm.vm import ExecutionVM
            from nano_vm_mcp.handlers import GovernedToolExecutor

            from app.agents.inventory_agent import _AGENT_TOOLS, _governed_tool
            from app.db_nano import StoreCursorRepository, get_store
            from app.policy.policy_snapshot import INVENTORY_AGENT_POLICY_SNAPSHOT

            cursor = StoreCursorRepository(get_store())
            vm = ExecutionVM(llm=llm, cursor_repository=cursor)
            executor = GovernedToolExecutor(policy=INVENTORY_AGENT_POLICY_SNAPSHOT)
            for name, fn in _AGENT_TOOLS.items():
                governed = _governed_tool(fn, name, executor)
                vm.register_tool(name, governed)
            mock_build.return_value = vm

            result = await agent.manage_restock({"input_text": "bad input"})

        assert result.success is False
        assert result.command is None

    async def test_program_validation_fails_on_bad_program(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(vm=mock_vm)

        with patch(
            "app.agents.inventory_agent.PROGRAM_COLLECT_RESTOCK",
            Program(
                name="bad_program",
                steps=[
                    Step(id="s1", type=StepType.TOOL, tool="collect_restock_command",
                         is_terminal=False, next_step="nonexistent"),
                ],
            ),
        ):
            with pytest.raises(RuntimeError, match="Program 'bad_program' validation failed"):
                await agent.manage_restock({"input_text": "test"})


class TestApplyRestock:
    async def test_success(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_apply_trace_success()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(apply_vm=mock_vm)
        result = await agent.apply_restock({"sku": "burger-firm", "quantity": 50})

        assert result.applied is True
        assert result.result == {"applied": True, "sku": "burger-firm", "quantity": 50}
        assert result.trace_id == "test-trace-1"

    async def test_rejected(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_apply_trace_rejected()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(apply_vm=mock_vm)
        result = await agent.apply_restock({"sku": "ghost", "quantity": 5})

        assert result.applied is False
        assert result.error is not None

    async def test_program_validation_fails_on_bad_program(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.register_tool = MagicMock()

        agent = InventoryAgent(apply_vm=mock_vm)

        with patch(
            "app.agents.inventory_agent.PROGRAM_APPLY_RESTOCK",
            Program(
                name="bad_apply_program",
                steps=[
                    Step(id="s1", type=StepType.TOOL, tool="apply_restock_command",
                         is_terminal=False, next_step="nonexistent"),
                ],
            ),
        ):
            with pytest.raises(
                RuntimeError, match="Program 'bad_apply_program' validation failed"
            ):
                await agent.apply_restock({"sku": "coffee", "quantity": 5})
