"""app/startup.py — startup-time validation for nano-vm programs."""

from __future__ import annotations

import logging

from nano_vm.validator import IssueSeverity, ProgramValidator

from app.programs.llm_fallback_program import PROVIDER_FALLBACK
from app.programs.menu_agent_program import (
    PROGRAM_COLLECT_ORDER as MENU_AGENT_PROGRAM,
)
from app.programs.order_agent_program import (
    PROGRAM_COLLECT_ORDER as ORDER_AGENT_PROGRAM,
)
from app.programs.order_programs import (
    PROGRAM_CANCEL,
    PROGRAM_CONFIRM,
    PROGRAM_PAYMENT_CONFIRMATION,
    PROGRAM_PAYMENT_FAILED,
    PROGRAM_REQUEST_PAYMENT,
    PROGRAM_START_COOKING,
)
from app.programs.promotion_agent_program import (
    PROGRAM_COLLECT_ORDER as PROMOTION_AGENT_PROGRAM,
)
from app.programs.support_agent_program import (
    PROGRAM_COLLECT_ORDER as SUPPORT_AGENT_PROGRAM,
)

logger = logging.getLogger(__name__)

_ALL_PROGRAMS = [
    PROGRAM_REQUEST_PAYMENT,
    PROGRAM_PAYMENT_CONFIRMATION,
    PROGRAM_START_COOKING,
    PROGRAM_CONFIRM,
    PROGRAM_PAYMENT_FAILED,
    PROGRAM_CANCEL,
    PROVIDER_FALLBACK,
    MENU_AGENT_PROGRAM,
    ORDER_AGENT_PROGRAM,
    PROMOTION_AGENT_PROGRAM,
    SUPPORT_AGENT_PROGRAM,
]


def validate_all_programs() -> None:
    """Validate every registered nano-vm Program at startup.

    Raises RuntimeError if any program has ERROR-severity issues.
    WARNING-severity issues (e.g. NO_FAILURE_TERMINAL) are logged but do
    not block startup.
    """
    any_errors = False
    for program in _ALL_PROGRAMS:
        report = ProgramValidator(program).validate()
        if report.is_valid():
            logger.info("Program '%s': VALID", program.name)
        else:
            errors = [i for i in report.issues if i.severity == IssueSeverity.ERROR]
            warnings = [i for i in report.issues if i.severity == IssueSeverity.WARNING]
            for issue in errors:
                logger.error(
                    "Program '%s' [%s] %s: %s",
                    program.name,
                    issue.kind.value,
                    issue.step_id,
                    issue.detail,
                )
            for issue in warnings:
                logger.warning(
                    "Program '%s' [%s] %s: %s",
                    program.name,
                    issue.kind.value,
                    issue.step_id,
                    issue.detail,
                )
            if errors:
                any_errors = True

    if any_errors:
        raise RuntimeError("Program validation failed — see ERROR logs above")
