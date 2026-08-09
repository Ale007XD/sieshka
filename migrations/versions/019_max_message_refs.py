"""max_message_refs — message_id tracking for MAX edit-in-place status cards.

Revision ID: 019_max_message_refs
Revises: 018_order_discount_rub
Create Date: 2026-08-09
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "019_max_message_refs"
down_revision: str | None = "018_order_discount_rub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "019_max_message_refs.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS max_message_refs")
