"""
app/programs/order_agent_program.py — nano-vm Program for OrderAgent.

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
    name="order_agent_collect",
    steps=[
        Step(
            id="process_input",
            type=StepType.LLM,
            prompt=(
                "You are an order processing assistant.\n"
                "Analyze the order input below and generate a structured command "
                "in JSON format.\n"
                "The JSON MUST contain these fields:\n"
                '  - "customer_id" (string)\n'
                '  - "items" (array of objects, each with "sku" and "qty")\n'
                '  - "delivery_address" (string)\n'
                "\n"
                "Input:\n"
                "$input_text"
            ),
            system=(
                "You are an order processing assistant. "
                "Output ONLY valid JSON. No explanation, no markdown."
            ),
            output_key="llm_output",
            next_step="validate_command",
        ),
        Step(
            id="validate_command",
            type=StepType.TOOL,
            tool="validate_order_command",
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
            tool="collect_order_command",
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
