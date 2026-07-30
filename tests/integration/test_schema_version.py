"""tests/integration/test_schema_version.py — guard against schema-drift tech debt.

sprint_m7_checkout_wiring was haunted by the same class of bug as the VPS
alembic_version drift at the start of this conversation: integration tests
silently replaying raw `migrations/*.sql` against a test DB instead of going
through Alembic, so the test schema drifted away from what `alembic upgrade
head` would have produced. The bootstrap path in
``tests/integration/conftest.py`` is now the SINGLE source of truth for
integration-test schema; individual test fixtures must never re-execute raw
migration SQL.

This test makes that contract explicit and load-bearing: a future contributor
who adds a second Alembic head (or splits the migration graph) gets an
immediate red light from CI rather than a silent "why does this test only
fail on Jenkins" mystery.

`alembic.script.ScriptDirectory` is the same API `command.upgrade` uses
internally — if the number of heads diverges from 1, both `alembic upgrade
head` and the integration-test bootstrap will start silently applying only
one branch.
"""
from __future__ import annotations

from alembic.script import ScriptDirectory


def test_single_alembic_head() -> None:
    script = ScriptDirectory("migrations")
    assert len(script.get_heads()) == 1
