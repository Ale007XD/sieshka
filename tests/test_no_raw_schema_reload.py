"""tests/test_no_raw_schema_reload.py — guard against raw-schema replay in tests.

Sprint context: the recurring class of bug in this codebase has been tests
silently replaying ``migrations/*.sql`` files directly against a test DB,
bypassing Alembic. That bypass produced schema drift between the test DB
and the real DB on every new migration, and exactly that drift caused the
checkout wiring to fail intermittently against the wrong column set.

The fix was to make Alembic (via ``tests/integration/conftest.py``) the
single source of truth for integration-test schema. This test makes the
contract load-bearing: it scans the test tree for any reference to a raw
migration filename and fails CI if one reappears. If a future contributor
needs a new raw SQL file in a test fixture, they have to update this list
deliberately, which forces a code review of "do we really need this, or
is Alembic missing a migration?"
"""
from __future__ import annotations

from pathlib import Path


def test_no_raw_schema_reload() -> None:
    root = Path("tests")

    forbidden = (
        "001_initial_schema.sql",
        "004_menu.sql",
        "010_checkout_columns.sql",
    )

    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in text:
                offenders.append(f"{path} still reloads raw schema {name}")

    assert not offenders, "\n".join(offenders)
