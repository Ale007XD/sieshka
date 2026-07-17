"""M7 customers table.

Revision ID: 005_customer
Revises: 004_menu
Create Date: 2026-07-17
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "005_customer"
down_revision: str | None = "004_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "005_customer.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customers CASCADE")
