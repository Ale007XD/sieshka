"""staff role 'staff' — full-authority MAX role (kitchen+courier+admin actions).

Revision ID: 020_staff_role_staff
Revises: 019_max_message_refs
Create Date: 2026-08-09
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "020_staff_role_staff"
down_revision: str | None = "019_max_message_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "020_staff_role_staff.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE staff DROP CONSTRAINT IF EXISTS staff_role_check")
    op.execute(
        "ALTER TABLE staff ADD CONSTRAINT staff_role_check "
        "CHECK (role IN ('kitchen', 'courier', 'admin'))"
    )
