"""M1 PostgreSQL schema — orders, kitchen, delivery, inventory, payments.

Revision ID: 001_initial_schema
Revises:
Create Date: 2025-06-26
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "001_initial_schema.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS idempotency_keys CASCADE")
    op.execute("DROP TABLE IF EXISTS payments CASCADE")
    op.execute("DROP TABLE IF EXISTS customers CASCADE")
    op.execute("DROP TABLE IF EXISTS inventory CASCADE")
    op.execute("DROP TABLE IF EXISTS delivery_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS kitchen_tickets CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
