"""staff — role lookup table for MAX/Telegram channel adapter role_gate().
See DECISIONS.md sprint_staff_table.

Revision ID: 017_staff
Revises: 016_inventory_movements
Create Date: 2026-08-05
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "017_staff"
down_revision: str | None = "016_inventory_movements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "017_staff.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS staff")
