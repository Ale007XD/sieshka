"""M6 promotions table (dashboard panel).

Revision ID: 003_promotions
Revises: 002_narrative_receipt
Create Date: 2026-07-05
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "003_promotions"
down_revision: str | None = "002_narrative_receipt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "003_promotions.sql"
    sql = sql_path.read_text()
    op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS promotions CASCADE")
