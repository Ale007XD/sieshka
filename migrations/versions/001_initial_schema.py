"""M1 PostgreSQL schema — orders, kitchen, delivery, inventory, payments.

Revision ID: 001_initial_schema
Revises:
Create Date: 2025-06-26
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "001_initial_schema.sql"
    sql = sql_path.read_text()
    op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS idempotency_keys CASCADE")
    op.execute("DROP TABLE IF EXISTS payments CASCADE")
    op.execute("DROP TABLE IF EXISTS customers CASCADE")
    op.execute("DROP TABLE IF EXISTS inventory CASCADE")
    op.execute("DROP TABLE IF EXISTS delivery_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS kitchen_tickets CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
