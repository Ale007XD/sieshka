"""
app/programs/menu_import_program.py — nano-vm Program for CSV product import.

GOVERNANCE ARCHITECTURE CORRECTION: this Program has NO LLM step. The CSV
input is already structured (parsed + validated by menu_import_service.
parse_and_validate_csv before this Program runs), so there is nothing for an
LLM to interpret. This is a deliberate, named exception to "agents parse
natural language" — NOT a bypass of governance itself. The single TOOL step
is wrapped by GovernedToolExecutor exactly like every other write Tool, and
the run still produces ONE Trace / ExecutionReceipt for the whole import
operation.

One Program run per CSV upload → ONE Trace covering the entire catalog update
(not one Trace per row — the meaningful business transition is "the catalog was
updated from this file", mirroring how order-state Programs produce one Trace
per transition, not one per touched DB row).

CONSTRAINTS:
  - Terminal step LAST in steps[] array (FSM starts from index 0)
  - The tool arg references the parsed rows via "$valid_rows" (a value placed
    in the Program context by MenuImportService.import_csv), NOT free text
"""
from __future__ import annotations

from nano_vm.models import Program, Step, StepType

MENU_IMPORT_PROGRAM = Program(
    name="menu_import",
    steps=[
        Step(
            id="apply_import",
            type=StepType.TOOL,
            tool="apply_menu_import",
            args={"rows": "$valid_rows"},
            output_key="import_result",
            is_terminal=True,
        ),
    ],
)
