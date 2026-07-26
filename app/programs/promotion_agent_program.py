"""
app/programs/promotion_agent_program.py — nano-vm Program for PromotionAgent.

COLLECT: LLM step (process_input) → TOOL validate_command → CONDITION check
  → success: TOOL confirm_command (terminal)
  → failure: TOOL report_collect_failure (terminal)

APPLY (mirrors zone/schedule/menu CONVENTION):
  validate_apply_command [TOOL] → CONDITION(valid) →
    apply_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]
    report_invalid [TOOL, is_terminal]

Command shape matches the real `promotions` table (id, name, discount, state) +
its FSM transition table (CREATED→ACTIVE→EXPIRED→ARCHIVED), NOT an invented
promotion_id/start_date shape that has no backing columns:
  {"action": "create"|"activate"|"expire"|"archive",
   "name": str|null,               # required for action=create
   "discount": number|null,        # required for action=create
   "target_promotion_name": str|null}  # required for activate/expire/archive

CONSTRAINTS:
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - String sentinel: use 0/1 in validate_command output, not string literals
  - Program DSL args referencing a prior step's output ALWAYS
    "$<step.id>.output", NEVER "$<output_key>.output"
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_COLLECT_PROMOTION = Program(
    name="promotion_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You are a promotion management assistant.\n"
                "Analyze the instruction below and generate a structured command "
                "in JSON format.\n"
                "The JSON MUST contain these fields:\n"
                '  - "action" (string, one of: "create", "activate", "expire", "archive")\n'
                '  - "name" (string or null) — the promotion name, ONLY for action=create\n'
                '  - "discount" (number or null) — percent off, ONLY for action=create\n'
                '  - "target_promotion_name" (string or null) — the EXISTING promotion '
                'this instruction refers to, for action=activate/expire/archive\n'
                "\n"
                "Examples:\n"
                '  "Создай акцию Летняя, скидка 20%" -> '
                '{"action":"create","name":"Летняя","discount":20,"target_promotion_name":null}\n'
                '  "активируй акцию Летняя" -> '
                '{"action":"activate","name":null,"discount":null,"target_promotion_name":"Летняя"}\n'
                "\n"
                "Input:\n"
                "$input_text"
            ),
            system=(
                "You are a promotion management assistant. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_promotion_command",
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
            tool="collect_promotion_command",
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


PROGRAM_APPLY_PROMOTION = Program(
    name="promotion_agent_apply",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_promotion_command",
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
            tool="apply_promotion_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_promotion_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)