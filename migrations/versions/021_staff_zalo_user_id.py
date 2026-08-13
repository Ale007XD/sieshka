"""staff.zalo_user_id — third messenger-identity column, mirrors max_user_id/
telegram_user_id from 017_staff.sql (sprint_zalo_staff_column).

Revision ID: 021_staff_zalo_user_id
Revises: 020_staff_role_staff
Create Date: 2026-08-11
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "021_staff_zalo_user_id"
down_revision: str | None = "020_staff_role_staff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "021_staff_zalo_user_id.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_staff_zalo_user_id")
    op.execute("ALTER TABLE staff DROP COLUMN IF EXISTS zalo_user_id")
