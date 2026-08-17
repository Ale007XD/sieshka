"""products.sort — display-order column for the admin reorder UI +
storefront ordering (sprint_menu_product_reorder). Mirrors categories.sort
from 004_menu.sql.

Revision ID: 024_products_sort
Revises: 023_orders_client_telegram_uid
Create Date: 2026-08-17
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "024_products_sort"
down_revision: str | None = "023_orders_client_telegram_uid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "024_products_sort.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_products_category_sort")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS sort")
