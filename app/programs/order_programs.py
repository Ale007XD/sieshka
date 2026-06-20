"""
app/programs/order_confirm.py — nano-vm Program: DRAFT → CONFIRMED.
M3+: executed via ExecutionVM.

CONSTRAINTS:
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - No allowed_outputs on free-text LLM steps
  - CONDITION steps separate from TOOL steps (ProgramValidator BFS)
  - String sentinel: use 0/1 output_key, not string literals as RHS
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

# Program: CONFIRMED → PAYMENT_PENDING (create YooKassa payment)
PROGRAM_REQUEST_PAYMENT = Program(
    name="order_request_payment",
    steps=[
        Step(
            id="validate_order",
            type=StepType.TOOL,
            tool="validate_order_items",
            args={"order_id": "$order_id"},
            output_key="validation_result",
            next_step="check_validation",
        ),
        Step(
            id="check_validation",
            type=StepType.CONDITION,
            condition="$validation_result.output < 1",  # 0=invalid, 1=valid (numeric sentinel)
            then="validation_failed",
            otherwise="create_payment",
        ),
        Step(
            id="create_payment",
            type=StepType.TOOL,
            tool="yookassa_create_payment",
            args={
                "order_id": "$order_id",
                "amount": "$amount",
                "currency": "RUB",
            },
            output_key="payment_result",
            next_step="write_payment_state",
        ),
        Step(
            id="write_payment_state",
            type=StepType.TOOL,
            tool="write_order_state_payment_pending",  # terminal tool — writes PG
            args={"order_id": "$order_id", "payment_id": "$payment_result.output"},
            output_key="write_result",
            is_terminal=True,
        ),
        Step(
            id="validation_failed",
            type=StepType.TOOL,
            tool="log_validation_failure",
            args={"order_id": "$order_id"},
            output_key="fail_result",
            is_terminal=True,
        ),
    ],
)

# Program: PAYMENT_PENDING → PAID (YooKassa webhook resume)
PROGRAM_PAYMENT_CONFIRMATION = Program(
    name="payment_confirmation",
    steps=[
        Step(
            id="verify_payment",
            type=StepType.TOOL,
            tool="yookassa_verify_payment",
            args={"order_id": "$order_id", "payment_id": "$payment_id"},
            output_key="verify_result",
            next_step="check_verify",
        ),
        Step(
            id="check_verify",
            type=StepType.CONDITION,
            condition="$verify_result.output < 1",  # 0=failed, 1=confirmed
            then="payment_failed",
            otherwise="write_paid_state",
        ),
        Step(
            id="write_paid_state",
            type=StepType.TOOL,
            tool="write_order_state_paid",  # terminal tool — writes PG  # terminal-tool
            args={"order_id": "$order_id"},
            output_key="write_result",
            is_terminal=True,
        ),
        Step(
            id="payment_failed",
            type=StepType.TOOL,
            tool="write_order_state_payment_failed",  # terminal-tool
            args={"order_id": "$order_id"},
            output_key="fail_result",
            is_terminal=True,
        ),
    ],
)

# Program: PAID → COOKING (M3 nano-vm integration)
PROGRAM_START_COOKING = Program(
    name="order_start_cooking",
    steps=[
        Step(
            id="reserve_inventory",
            type=StepType.TOOL,
            tool="reserve_inventory_items",
            args={"order_id": "$order_id"},
            output_key="inventory_result",
            next_step="check_inventory",
        ),
        Step(
            id="check_inventory",
            type=StepType.CONDITION,
            condition="$inventory_result.output < 1",  # 0=insufficient, 1=reserved
            then="inventory_failed",
            otherwise="create_kitchen_ticket",
        ),
        Step(
            id="create_kitchen_ticket",
            type=StepType.TOOL,
            tool="create_kitchen_ticket",
            args={"order_id": "$order_id"},
            output_key="ticket_id",
            next_step="write_cooking_state",
        ),
        Step(
            id="write_cooking_state",
            type=StepType.TOOL,
            tool="write_order_state_cooking",  # terminal-tool
            args={"order_id": "$order_id", "ticket_id": "$ticket_id.output"},
            output_key="write_result",
            is_terminal=True,
        ),
        Step(
            id="inventory_failed",
            type=StepType.TOOL,
            tool="notify_inventory_insufficient",
            args={"order_id": "$order_id"},
            output_key="notify_result",
            is_terminal=True,
        ),
    ],
)
