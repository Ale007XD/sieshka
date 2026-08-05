"""scripts/cleanup_duplicate_item_skus.py — one-time cleanup for products
that got an ITEM-<id> sku minted BEFORE the legacy-adoption fix (2026-08),
when a matching pre-existing inventory row (hand-picked sku, e.g.
'burger-firm') already existed for the same product name. See DECISIONS.md
sprint_inventory_menu_sync legacy-adoption fix.

Finds: products.sku LIKE 'ITEM-%' whose product.name (case-insensitive,
trimmed) matches an inventory row with a DIFFERENT sku that is NOT already
claimed by another product.

For each match:
  1. products.sku: ITEM-<id> -> legacy sku (adopts the real, stocked row)
  2. the orphan inventory row (sku=ITEM-<id>, quantity=0, created by the
     pre-fix sync run) is deleted — it never had real stock, it was a
     disconnected duplicate.

DRY-RUN BY DEFAULT — prints the plan, changes nothing. Pass --apply to
actually write.

Usage:
  python -m scripts.cleanup_duplicate_item_skus            # dry-run, prints plan
  python -m scripts.cleanup_duplicate_item_skus --apply     # applies the plan
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


async def _find_candidates(session: AsyncSession) -> list[dict[str, object]]:
    result = await session.execute(
        text(
            "SELECT p.id AS product_id, p.name AS product_name, p.sku AS item_sku, "
            "inv.sku AS legacy_sku, inv.quantity AS legacy_quantity, "
            "inv.state AS legacy_state "
            "FROM products p "
            "JOIN inventory inv "
            "  ON lower(trim(inv.name)) = lower(trim(p.name)) "
            " AND inv.sku != p.sku "
            "WHERE p.sku LIKE 'ITEM-%' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM products p2 WHERE p2.sku = inv.sku"
            "  )"
        )
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def _apply(session: AsyncSession, candidates: list[dict[str, object]]) -> None:
    for c in candidates:
        await session.execute(
            text("UPDATE products SET sku = :legacy_sku WHERE id = :product_id"),
            {"legacy_sku": c["legacy_sku"], "product_id": c["product_id"]},
        )
        await session.execute(
            text("DELETE FROM inventory WHERE sku = :item_sku"),
            {"item_sku": c["item_sku"]},
        )
    await session.commit()


async def run(apply: bool) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        candidates = await _find_candidates(session)

        if not candidates:
            print("No duplicate ITEM-<id> rows found. Nothing to do.")
            await engine.dispose()
            return

        print(f"Found {len(candidates)} candidate(s):\n")
        for c in candidates:
            print(
                f"  product_id={c['product_id']} name={c['product_name']!r}\n"
                f"    {c['item_sku']}  (orphan, quantity=0, will be DELETED from inventory)\n"
                f"    -> {c['legacy_sku']}  "
                f"(legacy, quantity={c['legacy_quantity']}, state={c['legacy_state']}, "
                f"products.sku will be set to this)\n"
            )

        if not apply:
            print("Dry-run only — no changes made. Re-run with --apply to write these changes.")
            await engine.dispose()
            return

        await _apply(session, candidates)
        print(f"Applied: {len(candidates)} product(s) re-linked, {len(candidates)} orphan "
              f"inventory row(s) deleted.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Actually write changes (default: dry-run)"
    )
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
