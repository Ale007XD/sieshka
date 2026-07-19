"""own review fix (2026-07-19) — orders.zone_id INTEGER -> UUID.

Revision ID: 011_zone_id_uuid
Revises: 010_checkout_columns
Create Date: 2026-07-19
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "011_zone_id_uuid"
down_revision: str | None = "010_checkout_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "011_zone_id_uuid.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS zone_id")
    op.execute("ALTER TABLE orders ADD COLUMN zone_id INTEGER")
