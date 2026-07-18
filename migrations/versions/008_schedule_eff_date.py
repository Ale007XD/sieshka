"""sprint_m7_schedule_agent — effective_date on schedule_windows.

Revision ID: 008_schedule_eff_date
Revises: 007_schedule_windows
Create Date: 2026-07-18
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "008_schedule_eff_date"
down_revision: str | None = "007_schedule_windows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1] / "008_schedule_windows_effective_date.sql"
    )
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_schedule_windows_today_override"
    )
    op.execute("DROP INDEX IF EXISTS uq_schedule_windows_permanent")
    op.execute("ALTER TABLE schedule_windows DROP COLUMN IF EXISTS effective_date")
    op.execute(
        "ALTER TABLE schedule_windows "
        "ADD CONSTRAINT uq_schedule_windows_period UNIQUE (period)"
    )
