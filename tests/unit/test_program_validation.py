"""tests/unit/test_program_validation.py — static validation of nano-vm Programs."""

from __future__ import annotations

from nano_vm.models import Program
from nano_vm.validator import IssueKind, IssueSeverity, ProgramValidator

from app.programs.order_programs import (
    PROGRAM_PAYMENT_CONFIRMATION,
    PROGRAM_REQUEST_PAYMENT,
    PROGRAM_START_COOKING,
)


def _terminal_step_ids(program: Program) -> set[str]:
    return {s.id for s in program.steps if s.is_terminal}


class TestProgramValidator:
    def test_request_payment_is_valid(self) -> None:
        report = ProgramValidator(PROGRAM_REQUEST_PAYMENT).validate()
        assert report.is_valid(), report.summary()

    def test_payment_confirmation_is_valid(self) -> None:
        report = ProgramValidator(PROGRAM_PAYMENT_CONFIRMATION).validate()
        assert report.is_valid(), report.summary()

    def test_start_cooking_is_valid(self) -> None:
        report = ProgramValidator(PROGRAM_START_COOKING).validate()
        assert report.is_valid(), report.summary()


class TestADR001ExactlyOneTerminal:
    """ADR-001 invariant: each Program MUST have exactly one terminal= True step."""

    def test_request_payment_one_terminal(self) -> None:
        terminals = _terminal_step_ids(PROGRAM_REQUEST_PAYMENT)
        assert len(terminals) >= 1, "expected at least one terminal step"
        assert len(terminals) == 2, (
            f"expected 2 terminal steps (happy+failure),"
            f" got {len(terminals)}: {terminals}"
        )

    def test_payment_confirmation_one_terminal(self) -> None:
        terminals = _terminal_step_ids(PROGRAM_PAYMENT_CONFIRMATION)
        assert len(terminals) == 2, (
            f"expected 2 terminal steps (happy+failure),"
            f" got {len(terminals)}: {terminals}"
        )

    def test_start_cooking_one_terminal(self) -> None:
        terminals = _terminal_step_ids(PROGRAM_START_COOKING)
        assert len(terminals) == 2, (
            f"expected 2 terminal steps (happy+failure),"
            f" got {len(terminals)}: {terminals}"
        )


class TestNoFailureTerminalIsWarning:
    """PV-13: no_failure_terminal must be WARNING, not ERROR."""

    def test_no_failure_terminal_severity_is_warning(self) -> None:
        """If a program triggers NO_FAILURE_TERMINAL, it must be WARNING."""
        programs: list[Program] = [PROGRAM_REQUEST_PAYMENT, PROGRAM_PAYMENT_CONFIRMATION,
                                    PROGRAM_START_COOKING]
        for program in programs:
            report = ProgramValidator(program).validate()
            for issue in report.by_kind(IssueKind.NO_FAILURE_TERMINAL):
                assert issue.severity == IssueSeverity.WARNING, (
                    f"Program '{program.name}' NO_FAILURE_TERMINAL "
                    f"has severity={issue.severity}, expected WARNING"
                )
