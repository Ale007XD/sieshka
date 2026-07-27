"""promo_agent — promotions.effect_type/trigger_code + orders.promo_code.

Revision ID: 012_promotions_effect_type
Revises: 011_zone_id_uuid
Create Date: 2026-07-27
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "012_promotions_effect_type"
down_revision: str | None = "011_zone_id_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "012_promotions_effect_type.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_promotions_trigger_code")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS promo_code")
    op.execute("ALTER TABLE promotions DROP CONSTRAINT IF EXISTS chk_promotions_effect_type")
    op.execute("ALTER TABLE promotions DROP COLUMN IF EXISTS effect_type")
    op.execute("ALTER TABLE promotions DROP COLUMN IF EXISTS trigger_code")
    op.execute("ALTER TABLE promotions ALTER COLUMN discount TYPE NUMERIC(5, 2)")