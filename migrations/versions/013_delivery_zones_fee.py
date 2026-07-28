"""zone_agent — per-zone delivery_fee_rub, replaces global flat fee.

Revision ID: 013_delivery_zones_fee
Revises: 012_promotions_effect_type
Create Date: 2026-07-28
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "013_delivery_zones_fee"
down_revision: str | None = "012_promotions_effect_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "013_delivery_zones_fee.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE delivery_zones DROP COLUMN IF EXISTS delivery_fee_rub")