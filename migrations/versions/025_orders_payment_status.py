"""orders.payment_status / paid_at / paid_by

NOTE: copy `revision` / `down_revision` style from 024_products_sort.py — the
ids below assume numeric-string ids ("024" -> "025"). Adjust if 024 uses hashes.
"""
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders "
        "ADD COLUMN IF NOT EXISTS payment_status varchar(16) NOT NULL DEFAULT 'UNPAID', "
        "ADD COLUMN IF NOT EXISTS paid_at timestamptz, "
        "ADD COLUMN IF NOT EXISTS paid_by varchar(64)"
    )
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_payment_status_check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_payment_status_check "
        "CHECK (payment_status IN ('UNPAID', 'PAID'))"
    )
    # Backfill: YooKassa orders that got past payment were paid by definition.
    op.execute(
        "UPDATE orders SET payment_status = 'PAID', paid_at = updated_at, paid_by = 'yookassa' "
        "WHERE payment_method LIKE 'yookassa_%' "
        "AND state NOT IN ('DRAFT', 'CONFIRMED', 'PAYMENT_PENDING', 'CANCELLED') "
        "AND payment_status = 'UNPAID'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_payment_status_check")
    op.execute(
        "ALTER TABLE orders DROP COLUMN IF EXISTS paid_by, "
        "DROP COLUMN IF EXISTS paid_at, DROP COLUMN IF EXISTS payment_status"
    )
