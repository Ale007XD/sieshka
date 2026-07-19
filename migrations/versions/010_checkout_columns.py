"""sprint_m7_checkout_wiring — orders checkout columns.

BUGFIX (2026-07-19, own review — found while patching zone_id's type):
migrations/010_checkout_columns.sql existed on disk with no alembic revision
file at all — `alembic upgrade head` never applied it. Every local dev DB
that had these columns got them some other way (manual psql, or an earlier
ad-hoc run), masking the gap. A genuinely fresh environment (staging_deploy,
the next sprint after this one) would fail on the very first checkout with
"column zone_id does not exist" the moment orders.zone_id/delivery_mode/
comment/client_max_uid/total_rub/payment_method are referenced.

Revision ID: 010_checkout_columns
Revises: 009_zone_name_unique_index
Create Date: 2026-07-19
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _split_sql import run_sql_file
from alembic import op

revision: str = "010_checkout_columns"
down_revision: str | None = "009_zone_name_unique_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "010_checkout_columns.sql"
    run_sql_file(op, sql_path)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE orders "
        "DROP COLUMN IF EXISTS delivery_mode, "
        "DROP COLUMN IF EXISTS zone_id, "
        "DROP COLUMN IF EXISTS comment, "
        "DROP COLUMN IF EXISTS client_max_uid, "
        "DROP COLUMN IF EXISTS total_rub, "
        "DROP COLUMN IF EXISTS payment_method"
    )
