"""M4 NarrativeReceipt table.

Revision ID: 002_narrative_receipt
Revises: 001_initial_schema
Create Date: 2026-07-04
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "002_narrative_receipt"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "002_narrative_receipt.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS narrative_receipts CASCADE")
