"""
app/programs/schedule_agent_program.py — nano-vm Program(s) for ScheduleAgent.

FLOW (collect):
  LLM step (process_input) → TOOL validate_command → CONDITION check
    → success: TOOL collect_schedule_command (terminal)
    → failure: TOOL report_collect_failure (terminal)

FLOW (apply — sprint_m7_agent_apply_phase_pattern CONVENTION):
  validate_apply_schedule_command [TOOL] → CONDITION(valid) →
    apply_schedule_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]
    report_invalid_schedule_command [TOOL, is_terminal]

CONSTRAINTS (same as menu_agent_program.py):
  - Terminal step LAST in steps[] (FSM starts from index 0)
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - Numeric sentinel: validate_* returns 0/1, CONDITION reads "$<step>.output < 1"
  - DSL args referencing a prior step's output ALWAYS "$<step.id>.output"
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_COLLECT_SCHEDULE = Program(
    name="schedule_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You control the morning/evening service windows of a food "
                "delivery business.\n"
                "Turn the instruction below into a JSON command with EXACTLY these "
                "fields:\n"
                '  - "period": "morning" or "evening"\n'
                '  - "action": "set_hours" to change the window, or '
                '"reset_to_default" to revert todays override\n'
                '  - "scope": "today" if a temporal marker is present'
                ' ("сегодня", "сейчас", "только сегодня", "на сегодня"), '
                'otherwise "permanent" (clear markers like "всегда", '
                '"отныне", "с этого момента" also mean "permanent"). '
                "If the instruction is ambiguous — neither a clear one-day marker "
                'nor a clear "always" — the command is unparseable: output '
                '{"period": "morning", "action": "set_hours", "scope": '
                '"ambiguous"} and nothing else.\n'
                '  - "start_time": "HH:MM" (24h, local) when action=set_hours, '
                "else null\n"
                '  - "end_time": "HH:MM" (24h, local) when action=set_hours, '
                "else null\n"
                "\n"
                "Examples:\n"
                '  "закрой сегодня раньше, в 20:00" -> '
                '{"period": "evening", "action": "set_hours", "scope": "today", '
                '"start_time": "00:00", "end_time": "20:00"}\n'
                '  "сдвинь утреннее меню на час позже" -> '
                '{"period": "morning", "action": "set_hours", "scope": '
                '"permanent", "start_time": "01:00", "end_time": "16:00"}\n'
                '  "верни вечернее меню на обычное время" -> '
                '{"period": "evening", "action": "reset_to_default", '
                '"scope": "today", "start_time": null, "end_time": null}\n'
                "\n"
                "Instruction:\n"
                "$input_text"
            ),
            system=(
                "You are a schedule window parser. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_schedule_command",
            args={"llm_output": "$process_input.output"},
            output_key="validation_result",
            next_step="check_valid",
        ),
        Step(
            id="check_valid",
            type=StepType.CONDITION,
            condition="$validate_command.output < 1",
            then="validation_failed",
            otherwise="confirm_command",
        ),
        Step(
            id="confirm_command",
            type=StepType.TOOL,
            tool="collect_schedule_command",
            args={"command": "$process_input.output"},
            output_key="agent_result",
            is_terminal=True,
        ),
        Step(
            id="validation_failed",
            type=StepType.TOOL,
            tool="report_collect_failure",
            args={"reason": "$validate_command.output"},
            output_key="fail_result",
            is_terminal=True,
        ),
    ],
)


PROGRAM_APPLY_SCHEDULE = Program(
    name="schedule_agent_apply",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_schedule_command",
            args={"command": "$command"},
            output_key="validation_result",
            next_step="check_valid",
        ),
        Step(
            id="check_valid",
            type=StepType.CONDITION,
            condition="$validate_command.output < 1",
            then="report_invalid",
            otherwise="apply_command",
        ),
        Step(
            id="apply_command",
            type=StepType.TOOL,
            tool="apply_schedule_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_schedule_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)
