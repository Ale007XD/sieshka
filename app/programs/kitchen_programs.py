"""
app/programs/kitchen_programs.py — nano-vm Programs for kitchen ticket
transitions (sprint_kitchen_governance_migration, 2026-08-16).

Completes the M3 migration that app/tools/kitchen_tools.py started
(2026-06-30, "M3+: registered with GovernedToolExecutor") but was never
finished: KitchenService kept writing through the M1/M2 KitchenFSM +
KitchenRepository.write_state path (see those modules' own docstrings —
both self-label as the pre-M3 path) instead of routing through
ExecutionVM/GovernedToolExecutor like orders already do
(app/programs/order_programs.py). Kitchen state transitions previously
bypassed the governed contour entirely — direct PG writes with no Trace/
ExecutionReceipt, the exact class of architectural violation the project's
founding principle rules out ("all meaningful business state transitions
route through governed nano-vm Programs" — extends to the whole operational
contour, not just orders).

Each event has its own dedicated terminal tool (write_kitchen_state_queued
etc.) rather than a generic transition_kitchen_state(from,to) like orders'
build_simple_program — those 4 tools already existed, already encode their
own expected-current-state check internally (FOR UPDATE + raise on
mismatch), and already had 12 passing unit tests (test_kitchen_tools.py)
before this migration. Reusing them as-is completes the original intent
instead of discarding and re-deriving it.

CONSTRAINTS: same as order_programs.py — terminal step last (it's the only
step here), no downstream CONDITION on write_result (all 4 tools already
raise on failure per CONSTRAINTS.md "Terminal TOOL step failure
propagation").
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

PROGRAM_KITCHEN_QUEUE = Program(
    name="kitchen_queue",
    steps=[
        Step(
            id="write_state",
            type=StepType.TOOL,
            tool="write_kitchen_state_queued",
            args={"ticket_id": "$ticket_id"},
            output_key="write_result",
            is_terminal=True,
        ),
    ],
)

PROGRAM_KITCHEN_START_PREP = Program(
    name="kitchen_start_prep",
    steps=[
        Step(
            id="write_state",
            type=StepType.TOOL,
            tool="write_kitchen_state_preparing",
            args={"ticket_id": "$ticket_id"},
            output_key="write_result",
            is_terminal=True,
        ),
    ],
)

PROGRAM_KITCHEN_MARK_READY = Program(
    name="kitchen_mark_ready",
    steps=[
        Step(
            id="write_state",
            type=StepType.TOOL,
            tool="write_kitchen_state_ready",
            args={"ticket_id": "$ticket_id"},
            output_key="write_result",
            is_terminal=True,
        ),
    ],
)

PROGRAM_KITCHEN_HAND_OFF = Program(
    name="kitchen_hand_off",
    steps=[
        Step(
            id="write_state",
            type=StepType.TOOL,
            tool="write_kitchen_state_handed_off",
            args={"ticket_id": "$ticket_id"},
            output_key="write_result",
            is_terminal=True,
        ),
    ],
)

# ---------------------------------------------------------------------------
# Program registry — dispatch by KitchenEvent
# ---------------------------------------------------------------------------

EVENT_PROGRAM_MAP: dict[str, Program] = {
    "QUEUE": PROGRAM_KITCHEN_QUEUE,
    "START_PREP": PROGRAM_KITCHEN_START_PREP,
    "MARK_READY": PROGRAM_KITCHEN_MARK_READY,
    "HAND_OFF": PROGRAM_KITCHEN_HAND_OFF,
}
