"""inventory_movements — delta-log ledger for inventory quantity changes.
See DECISIONS.md 2026-08 sprint_inventory_ledger.

Revision ID: 016_inventory_movements
Revises: 015_products_sku
Create Date: 2026-08-04
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "016_inventory_movements"
down_revision: str | None = "015_products_sku"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "016_inventory_movements.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory_movements")
