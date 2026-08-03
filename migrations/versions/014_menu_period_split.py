"""menu_period split — time_period (morning/evening/both) vs fulfillment_scope
(delivery/pickup/both). See DECISIONS.md 2026-08-03 menu_period-collision.

Revision ID: 014_menu_period_split
Revises: 013_delivery_zones_fee
Create Date: 2026-08-03
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "014_menu_period_split"
down_revision: str | None = "013_delivery_zones_fee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "014_menu_period_split.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE products RENAME COLUMN time_period_override TO menu_period_override")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_time_period_override_check")
    op.execute(
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS "
        "menu_period VARCHAR(16) NOT NULL DEFAULT 'both'"
    )
    op.execute(
        "UPDATE categories SET menu_period = CASE "
        "WHEN fulfillment_scope IN ('delivery', 'pickup') THEN fulfillment_scope "
        "ELSE time_period END"
    )
    op.execute("ALTER TABLE categories DROP CONSTRAINT IF EXISTS categories_time_period_check")
    op.execute(
        "ALTER TABLE categories DROP CONSTRAINT IF EXISTS "
        "categories_fulfillment_scope_check"
    )
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS time_period")
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS fulfillment_scope")