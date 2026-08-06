"""
app/programs/inventory_agent_program.py — nano-vm Program for InventoryAgent.

COLLECT: LLM step (process_input) -> TOOL validate_command -> CONDITION check
  -> success: TOOL confirm_command (terminal)
  -> failure: TOOL report_collect_failure (terminal)

APPLY (mirrors promotion/zone/menu CONVENTION):
  validate_apply_command [TOOL] -> CONDITION(valid) ->
    apply_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]
    report_invalid [TOOL, is_terminal]

Command shape — single action (restock only, no state machine):
  {"sku": str, "quantity": int}   # quantity is the amount to ADD, always > 0

CONSTRAINTS:
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - Numeric sentinel: use 0/1 in validate_command output, not string literals
  - Program DSL args referencing a prior step's output ALWAYS
    "$<step.id>.output", NEVER "$<output_key>.output"
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_COLLECT_RESTOCK = Program(
    name="inventory_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You are an inventory restocking assistant.\n"
                "Analyze the instruction below and generate a structured command "
                "in JSON format.\n"
                "The JSON MUST contain these fields:\n"
                '  - "sku" (string) — the exact SKU of the item to restock. Use the '
                "SKU shown in the instruction if given verbatim; if the instruction "
                "names the item instead of its SKU, use your best guess at the SKU "
                "text as written (validation will reject it if it doesn't exist).\n"
                '  - "quantity" (whole number, > 0) — how many units to ADD to the '
                "current stock. This is always an addition, never an absolute value "
                "or a subtraction.\n"
                "\n"
                "Examples:\n"
                '  "Добавь 50 штук на burger-firm" -> '
                '{"sku":"burger-firm","quantity":50}\n'
                '  "Пополни shaurma-bbq на 30" -> '
                '{"sku":"shaurma-bbq","quantity":30}\n'
                '  "Привезли ещё 100 кваса, sku ITEM-B732AFF2" -> '
                '{"sku":"ITEM-B732AFF2","quantity":100}\n'
                '  "restock coffee-beans by 20 units" -> '
                '{"sku":"coffee-beans","quantity":20}\n'
                "\n"
                "Input:\n"
                "$input_text"
            ),
            system=(
                "You are an inventory restocking assistant. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_restock_command",
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
            tool="collect_restock_command",
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


PROGRAM_APPLY_RESTOCK = Program(
    name="inventory_agent_apply",
    steps=[
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_apply_restock_command",
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
            tool="apply_restock_command",
            args={"command": "$command"},
            output_key="apply_result",
            is_terminal=True,
        ),
        Step(
            id="report_invalid",
            type=StepType.TOOL,
            tool="report_invalid_restock_command",
            args={"reason": "$validate_command.output"},
            output_key="invalid_result",
            is_terminal=True,
        ),
    ],
)
