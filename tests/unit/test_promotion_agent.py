"""tests/unit/test_promotion_agent.py — PromotionAgent unit tests with MockLLMAdapter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nano_vm.adapters import MockLLMAdapter
from nano_vm.models import Program, Step, StepResult, StepStatus, StepType, Trace, TraceStatus

from app.agents.promotion_agent import PromotionAgent
from app.tools.promotion_agent_tools import (
    collect_promotion_command,
    report_collect_failure,
    validate_promotion_command,
)

_VALID_PROMOTION_COMMAND_JSON = (
    '{"action": "create", "name": "Летняя", '
    '"discount": 20.0, "target_promotion_name": null}'
)

_VALID_TRANSITION_COMMAND_JSON = (
    '{"action": "activate", "name": null, '
    '"discount": null, "target_promotion_name": "Летняя"}'
)

_INVALID_PROMOTION_COMMAND_JSON = "not valid json"


def _make_trace_success(output: str) -> Trace:
    return Trace(
        program_name="promotion_agent_collect",
        status=TraceStatus.SUCCESS,
        steps=[
            StepResult(
                step_id="process_input",
                status=StepStatus.SUCCESS,
                output=_VALID_PROMOTION_COMMAND_JSON,
            ),
            StepResult(
                step_id="validate_command",
                status=StepStatus.SUCCESS,
                output=1,
            ),
            StepResult(
                step_id="check_valid",
                status=StepStatus.SUCCESS,
                output="confirm_command",
            ),
            StepResult(
                step_id="confirm_command",
                status=StepStatus.SUCCESS,
                output=output,
            ),
        ],
        final_output=output,
    )


def _make_trace_failure() -> Trace:
    return Trace(
        program_name="promotion_agent_collect",
        status=TraceStatus.SUCCESS,
        steps=[
            StepResult(
                step_id="process_input",
                status=StepStatus.SUCCESS,
                output=_INVALID_PROMOTION_COMMAND_JSON,
            ),
            StepResult(
                step_id="validate_command",
                status=StepStatus.SUCCESS,
                output=0,
            ),
            StepResult(
                step_id="check_valid",
                status=StepStatus.SUCCESS,
                output="validation_failed",
            ),
            StepResult(
                step_id="validation_failed",
                status=StepStatus.SUCCESS,
                output="FAILED:0",
            ),
        ],
        final_output="FAILED:0",
    )


def _make_trace_pending() -> Trace:
    return Trace(
        program_name="promotion_agent_collect",
        status=TraceStatus.FAILED,
        steps=[
            StepResult(
                step_id="process_input",
                status=StepStatus.FAILED,
                output="",
                error="LLM call failed",
            ),
        ],
        error="LLM call failed",
    )


class TestValidatePromotionCommand:
    async def test_valid_json_create(self) -> None:
        result = await validate_promotion_command(_VALID_PROMOTION_COMMAND_JSON)
        assert result == 1

    async def test_valid_json_transition(self) -> None:
        """action=activate/expire/archive only needs target_promotion_name —
        no DB check happens here (that's validate_apply_promotion_command's
        job); this is a structural-shape check only."""
        result = await validate_promotion_command(_VALID_TRANSITION_COMMAND_JSON)
        assert result == 1

    async def test_empty_input(self) -> None:
        result = await validate_promotion_command("")
        assert result == 0

    async def test_invalid_json(self) -> None:
        result = await validate_promotion_command("not json")
        assert result == 0

    async def test_missing_action(self) -> None:
        result = await validate_promotion_command(
            '{"name": "X", "discount": 20.0, "target_promotion_name": null}'
        )
        assert result == 0

    async def test_invalid_action_value(self) -> None:
        result = await validate_promotion_command(
            '{"action": "delete", "name": "X", "discount": 20.0, '
            '"target_promotion_name": null}'
        )
        assert result == 0

    async def test_create_missing_name(self) -> None:
        result = await validate_promotion_command(
            '{"action": "create", "name": null, "discount": 20.0, '
            '"target_promotion_name": null}'
        )
        assert result == 0

    async def test_create_missing_discount(self) -> None:
        result = await validate_promotion_command(
            '{"action": "create", "name": "X", "discount": null, '
            '"target_promotion_name": null}'
        )
        assert result == 0

    async def test_create_discount_not_number(self) -> None:
        result = await validate_promotion_command(
            '{"action": "create", "name": "X", "discount": "twenty", '
            '"target_promotion_name": null}'
        )
        assert result == 0

    async def test_transition_missing_target_promotion_name(self) -> None:
        result = await validate_promotion_command(
            '{"action": "activate", "name": null, "discount": null, '
            '"target_promotion_name": null}'
        )
        assert result == 0


class TestCollectPromotionCommand:
    async def test_passthrough(self) -> None:
        result = await collect_promotion_command("test-command")
        assert result == "test-command"


class TestReportCollectFailure:
    async def test_failure_message(self) -> None:
        result = await report_collect_failure("0")
        assert result == "FAILED:0"


class TestPromotionAgent:
    async def test_manage_promotion_success(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_trace_success(_VALID_PROMOTION_COMMAND_JSON)
        mock_vm.register_tool = MagicMock()

        agent = PromotionAgent(vm=mock_vm)
        result = await agent.manage_promotion({
            "input_text": "Создай акцию Летняя, скидка 20%",
        })

        assert result.success is True
        assert result.command is not None
        assert result.command["action"] == "create"
        assert result.command["name"] == "Летняя"
        assert result.command["discount"] == 20.0
        assert result.error is None

    async def test_manage_promotion_invalid_json(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_trace_failure()
        mock_vm.register_tool = MagicMock()

        agent = PromotionAgent(vm=mock_vm)
        result = await agent.manage_promotion({
            "input_text": "invalid input",
        })

        assert result.success is False
        assert result.command is None
        assert result.error is not None

    async def test_manage_promotion_vm_failure(self) -> None:
        mock_vm = AsyncMock()
        mock_vm.run.return_value = _make_trace_pending()
        mock_vm.register_tool = MagicMock()

        agent = PromotionAgent(vm=mock_vm)
        result = await agent.manage_promotion({
            "input_text": "sale",
        })

        assert result.success is False
        assert result.command is None
        assert result.error == "LLM call failed"

    async def test_real_vm_valid_json(self) -> None:
        """Integration-style: builds real VM with MockLLMAdapter returning valid JSON."""
        llm = MockLLMAdapter(_VALID_PROMOTION_COMMAND_JSON)
        agent = PromotionAgent()
        with patch.object(agent, "_build_vm") as mock_build:
            from nano_vm.vm import ExecutionVM
            from nano_vm_mcp.handlers import GovernedToolExecutor

            from app.agents.promotion_agent import _AGENT_TOOLS, _governed_tool
            from app.db_nano import StoreCursorRepository, get_store
            from app.policy.policy_snapshot import PROMOTION_AGENT_POLICY_SNAPSHOT

            cursor = StoreCursorRepository(get_store())
            vm = ExecutionVM(llm=llm, cursor_repository=cursor)
            executor = GovernedToolExecutor(policy=PROMOTION_AGENT_POLICY_SNAPSHOT)
            for name, fn in _AGENT_TOOLS.items():
                governed = _governed_tool(fn, name, executor)
                vm.register_tool(name, governed)
            mock_build.return_value = vm

            result = await agent.manage_promotion({
                "input_text": "Создай акцию Летняя, скидка 20%",
            })

        assert result.success is True
        assert result.command is not None
        assert result.command["action"] == "create"
        assert result.command["name"] == "Летняя"
        assert result.command["discount"] == 20.0

    async def test_real_vm_invalid_json(self) -> None:
        """Real VM with MockLLMAdapter returning invalid JSON → failure path."""
        llm = MockLLMAdapter("not valid json at all")
        agent = PromotionAgent()
        with patch.object(agent, "_build_vm") as mock_build:
            from nano_vm.vm import ExecutionVM
            from nano_vm_mcp.handlers import GovernedToolExecutor

            from app.agents.promotion_agent import _AGENT_TOOLS, _governed_tool
            from app.db_nano import StoreCursorRepository, get_store
            from app.policy.policy_snapshot import PROMOTION_AGENT_POLICY_SNAPSHOT

            cursor = StoreCursorRepository(get_store())
            vm = ExecutionVM(llm=llm, cursor_repository=cursor)
            executor = GovernedToolExecutor(policy=PROMOTION_AGENT_POLICY_SNAPSHOT)
            for name, fn in _AGENT_TOOLS.items():
                governed = _governed_tool(fn, name, executor)
                vm.register_tool(name, governed)
            mock_build.return_value = vm

            result = await agent.manage_promotion({
                "input_text": "bad input",
            })

        assert result.success is False
        assert result.command is None
        assert result.error is not None

    async def test_program_validation_fails_on_bad_program(self) -> None:
        """Verify that an invalid program raises RuntimeError."""
        mock_vm = AsyncMock()
        mock_vm.register_tool = MagicMock()

        agent = PromotionAgent(vm=mock_vm)

        with patch(
            "app.agents.promotion_agent.PROGRAM_COLLECT_PROMOTION",
            Program(
                name="bad_program",
                steps=[
                    Step(id="s1", type=StepType.TOOL, tool="collect_promotion_command",
                         is_terminal=False, next_step="nonexistent"),
                ],
            ),
        ):
            with pytest.raises(RuntimeError, match="Program \'bad_program\' validation failed"):
                await agent.manage_promotion({
                    "input_text": "test",
                })