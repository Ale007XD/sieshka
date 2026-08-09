"""order discount_rub — persisted promo discount snapshot for thanks page/receipt.

Revision ID: 018_order_discount_rub
Revises: 017_staff
Create Date: 2026-08-08
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "018_order_discount_rub"
down_revision: str | None = "017_staff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "018_order_discount_rub.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS discount_rub")
