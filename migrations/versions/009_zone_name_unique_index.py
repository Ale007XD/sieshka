"""sprint_m7_zone_agent — partial unique index on active zone name.

Revision ID: 009_zone_name_unique_index
Revises: 008_schedule_eff_date
Create Date: 2026-07-18
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "009_zone_name_unique_index"
down_revision: str | None = "008_schedule_eff_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1] / "009_zone_name_unique_index.sql"
    )
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_delivery_zones_name_active")
