"""app/startup.py — startup-time validation for nano-vm programs."""

from __future__ import annotations

import logging

from nano_vm.validator import IssueSeverity, ProgramValidator

from app.programs.order_programs import (
    PROGRAM_CANCEL,
    PROGRAM_CONFIRM,
    PROGRAM_PAYMENT_CONFIRMATION,
    PROGRAM_PAYMENT_FAILED,
    PROGRAM_REQUEST_PAYMENT,
    PROGRAM_START_COOKING,
)

logger = logging.getLogger(__name__)

_ALL_PROGRAMS = [
    PROGRAM_REQUEST_PAYMENT,
    PROGRAM_PAYMENT_CONFIRMATION,
    PROGRAM_START_COOKING,
    PROGRAM_CONFIRM,
    PROGRAM_PAYMENT_FAILED,
    PROGRAM_CANCEL,
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
