"""M7 schedule windows override table.

Revision ID: 007_schedule_windows
Revises: 006_delivery_zones
Create Date: 2026-07-18
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "007_schedule_windows"
down_revision: str | None = "006_delivery_zones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "007_schedule_windows.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS schedule_windows CASCADE")
