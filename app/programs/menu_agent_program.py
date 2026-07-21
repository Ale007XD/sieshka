"""
app/programs/menu_agent_program.py — nano-vm Program for MenuAgent.

FLOW:
  LLM step (process_input) → TOOL validate_command → CONDITION check
    → success: TOOL confirm_command (terminal)
    → failure: TOOL report_collect_failure (terminal)

CONSTRAINTS:
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - String sentinel: use 0/1 in validate_command output, not string literals
  - Program DSL args referencing a prior step's output ALWAYS
    "$<step.id>.output", NEVER "$<output_key>.output"
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_COLLECT_ORDER = Program(
    name="menu_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You are a menu processing assistant.\n"
                "Analyze the menu input below and generate a structured command "
                "in JSON format.\n"
                "The JSON MUST contain these fields:\n"
                '  - "menu_id" (string)\n'
                '  - "items" (array of objects, each with "sku" and "qty")\n'
                '  - "category" (string)\n'
                "\n"
                "Input:\n"
                "$input_text"
            ),
            system=(
                "You are a menu processing assistant. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_menu_command",
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
            tool="collect_menu_command",
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


# ---------------------------------------------------------------------------
# APPLY phase (sprint_m7_agent_apply_phase_pattern) — the reference implementation
# of the shared "agent apply-phase CONVENTION" (see app/agents/README.md).
#
# Shape:
#   validate_command [TOOL] → CONDITION(valid) →
#     apply_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]  (valid)
#     report_invalid [TOOL, is_terminal]                               (invalid)
#
# There is NO LLM step here: the confirmed command already came out of the
# COLLECT phase's terminal JSON. The apply phase turns that confirmed command
# into the ONE governed write.
#
# The command dict is placed in the Program context by MenuAgent.apply_menu, so
# both TOOL steps reference it via "$command" (a whole typed dict, NOT free text).
# ---------------------------------------------------------------------------

PROGRAM_APPLY_MENU = Program(
    name="menu_agent_apply",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_command",
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
            tool="apply_menu_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)


# ---------------------------------------------------------------------------
# APPLY phase — category creation (same 4-step CONVENTION shape as
# PROGRAM_APPLY_MENU; only tool names and program name differ).
#
# Shape:
#   validate_command [TOOL] → CONDITION(valid) →
#     apply_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]  (valid)
#     report_invalid [TOOL, is_terminal]                               (invalid)
#
# Command dict placed in context by MenuAgent.apply_category; referenced
# via "$command" (typed dict, NOT free text — no LLM step here).
# ---------------------------------------------------------------------------

PROGRAM_APPLY_CATEGORY = Program(
    name="menu_agent_apply_category",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_category_command",
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
            tool="apply_category_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_category_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)


# ---------------------------------------------------------------------------
# APPLY phase — product update. Same 4-step CONVENTION shape.
# Command dict: {product_id: str, name?: str, category?: str,
#                price_rub?: int, description?: str, image_url?: str,
#                is_active?: bool}
# ---------------------------------------------------------------------------

PROGRAM_UPDATE_PRODUCT = Program(
    name="menu_agent_update_product",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_update_product_command",
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
            tool="apply_update_product_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_update_product_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)
