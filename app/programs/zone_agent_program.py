"""
app/programs/zone_agent_program.py — nano-vm Program(s) for ZoneAgent.

FLOW (collect):
  LLM step (process_input) -> TOOL validate_command -> CONDITION check
    -> success: TOOL collect_zone_command (terminal)
    -> failure: TOOL report_collect_failure (terminal)

FLOW (apply — sprint_m7_agent_apply_phase_pattern CONVENTION):
  validate_apply_zone_command [TOOL] -> CONDITION(valid) ->
    apply_zone_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]
    report_invalid_zone_command [TOOL, is_terminal]

CONSTRAINTS (same as menu_agent_program.py / schedule_agent_program.py):
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - Numeric sentinel: validate_* returns 0/1, CONDITION reads "$<step>.output < 1"
  - DSL args referencing a prior step's output ALWAYS "$<step.id>.output"
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_COLLECT_ZONE = Program(
    name="zone_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You manage the delivery zones of a food delivery business.\n"
                "Turn the instruction below into a JSON command with EXACTLY these "
                "fields:\n"
                '  - "action": one of "create", "update", "deactivate"\n'
                '  - "name": the NEW zone name (string) when action=create or '
                'when action=update renames a zone; otherwise null\n'
                '  - "delivery_time_minutes": positive integer ETA in minutes '
                'when action=create or action=update changes the ETA; otherwise null\n'
                '  - "target_zone_name": the name of the EXISTING zone this '
                'instruction refers to (string) when action=update or '
                'action=deactivate; otherwise null\n'
                "\n"
                'The verbs "deactivate", "disable", "remove" and "delete" all mean '
                'action="deactivate" (a soft retirement — the zone is no longer '
                "offered to new customers but its history is preserved).\n"
                "\n"
                "Examples:\n"
                '  "Добавь зону Балахня, 15 минут" -> '
                '{"action": "create", "name": "Балахня", '
                '"delivery_time_minutes": 15, "target_zone_name": null}\n'
                '  "деактивируй зону Отдалённые районы" -> '
                '{"action": "deactivate", "name": null, '
                '"delivery_time_minutes": null, '
                '"target_zone_name": "Отдалённые районы"}\n'
                '  "переименуй зону Город в Город-Центр" -> '
                '{"action": "update", "name": "Город-Центр", '
                '"delivery_time_minutes": null, "target_zone_name": "Город"}\n'
                '  "поменяй время доставки для Города на 30 минут" -> '
                '{"action": "update", "name": null, '
                '"delivery_time_minutes": 30, "target_zone_name": "Город"}\n'
                "\n"
                "Instruction:\n"
                "$input_text"
            ),
            system=(
                "You are a delivery zone instruction parser. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_zone_command",
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
            tool="collect_zone_command",
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


PROGRAM_APPLY_ZONE = Program(
    name="zone_agent_apply",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_zone_command",
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
            tool="apply_zone_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_zone_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)
