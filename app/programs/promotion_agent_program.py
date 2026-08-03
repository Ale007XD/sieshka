"""
app/programs/promotion_agent_program.py — nano-vm Program for PromotionAgent.

COLLECT: LLM step (process_input) → TOOL validate_command → CONDITION check
  → success: TOOL confirm_command (terminal)
  → failure: TOOL report_collect_failure (terminal)

APPLY (mirrors zone/schedule/menu CONVENTION):
  validate_apply_command [TOOL] → CONDITION(valid) →
    apply_command [TOOL, GovernedToolExecutor-wrapped, is_terminal]
    report_invalid [TOOL, is_terminal]

Command shape matches the real `promotions` table (id, name, discount, state,
effect_type, trigger_code) + its FSM transition table (CREATED→ACTIVE→
EXPIRED→ARCHIVED), NOT an invented promotion_id/start_date shape that has no
backing columns:
  {"action": "create"|"activate"|"expire"|"archive",
   "name": str|null,               # required for action=create
   "effect_type": str|null,        # required for action=create: one of
                                    # PERCENT_DISCOUNT|FIXED_AMOUNT|FREE_DELIVERY
   "discount": number|null,        # required for action=create unless
                                    # effect_type=FREE_DELIVERY
   "trigger_code": str|null,       # optional for action=create — code the
                                    # customer redeems at checkout; null means
                                    # not customer-redeemable, name-only
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
                '  - "effect_type" (string or null) — ONLY for action=create, one of:\n'
                '      "PERCENT_DISCOUNT" (percent off the order),\n'
                '      "FIXED_AMOUNT" (fixed rubles off the order),\n'
                '      "FREE_DELIVERY" (delivery fee waived, no discount amount)\n'
                '  - "discount" (number or null) — ONLY for action=create. The percent '
                "(0-100) if effect_type=PERCENT_DISCOUNT, or the ruble amount if "
                "effect_type=FIXED_AMOUNT. Null if effect_type=FREE_DELIVERY.\n"
                '  - "trigger_code" (string or null) — ONLY for action=create, the exact '
                "code the customer types at checkout to redeem this promo (e.g. "
                '"ЛЕТО2026"). Null if the instruction does not specify one — a promotion '
                "without a code cannot be redeemed by customers, only referenced by name.\n"
                '  - "target_promotion_name" (string or null) — the EXISTING promotion '
                'this instruction refers to, for action=activate/expire/archive\n'
                "\n"
                "Examples:\n"
                '  "Создай акцию Летняя, скидка 20%, код ЛЕТО20" -> '
                '{"action":"create","name":"Летняя","effect_type":"PERCENT_DISCOUNT",'
                '"discount":20,"trigger_code":"ЛЕТО20","target_promotion_name":null}\n'
                '  "Создай акцию Скидос99, скидка 99 рублей по коду СКИДОС99" -> '
                '{"action":"create","name":"Скидос99","effect_type":"FIXED_AMOUNT",'
                '"discount":99,"trigger_code":"СКИДОС99","target_promotion_name":null}\n'
                '  "Создай акцию Бесплатная доставка по коду ДОСТАВКА0" -> '
                '{"action":"create","name":"Бесплатная доставка",'
                '"effect_type":"FREE_DELIVERY","discount":null,'
                '"trigger_code":"ДОСТАВКА0","target_promotion_name":null}\n'
                '  "активируй акцию Летняя" -> '
                '{"action":"activate","name":null,"effect_type":null,"discount":null,'
                '"trigger_code":null,"target_promotion_name":"Летняя"}\n'
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