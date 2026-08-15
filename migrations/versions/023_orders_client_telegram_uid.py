"""orders.client_telegram_uid — attribution column, mirrors client_max_uid
from 010_checkout_columns.sql (sprint_telegram_miniapp_auth).

Revision ID: 023_orders_client_telegram_uid
Revises: 022_orders_client_zalo_uid
Create Date: 2026-08-15
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "023_orders_client_telegram_uid"
down_revision: str | None = "022_orders_client_zalo_uid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "023_orders_client_telegram_uid.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS client_telegram_uid")
