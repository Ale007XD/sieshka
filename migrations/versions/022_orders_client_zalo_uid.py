"""orders.client_zalo_uid — attribution column, mirrors client_max_uid from
010_checkout_columns.sql (sprint_zalo_storefront_auth).

Revision ID: 022_orders_client_zalo_uid
Revises: 021_staff_zalo_user_id
Create Date: 2026-08-13
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "022_orders_client_zalo_uid"
down_revision: str | None = "021_staff_zalo_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "022_orders_client_zalo_uid.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS client_zalo_uid")
