"""products.sku — links menu items to inventory rows.
See DECISIONS.md 2026-08 sprint_inventory_menu_sync.

Revision ID: 015_products_sku
Revises: 014_menu_period_split
Create Date: 2026-08-04
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "015_products_sku"
down_revision: str | None = "014_menu_period_split"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "015_products_sku.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_products_sku")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS sku")
