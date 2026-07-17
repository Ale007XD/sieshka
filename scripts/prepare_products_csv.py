"""scripts/prepare_products_csv.py — ONE-TIME PREP for sprint_m7_menu_csv_import.

Converts Alex's original products.json (89 rows: id/name/category_id/
menu_period_override/price_rub/is_active) into a CSV matching this sprint's
exact column spec (Name, Category, Description, Price Rub, Photo Url):

  - Category column is populated from category_id, resolved against the
    categories already seeded by sprint_m7_menu_domain (by external_id).
  - Description / Photo Url are blank (not present in the original export).

The resulting CSV is then imported with the EXACT SAME governed Program built
by this sprint — no parallel import path. Run once:

    python -m scripts.prepare_products_csv

Outputs: data/products_initial.csv  (then upload via /admin/menu/import-csv
or run it programmatically through MenuImportService.import_csv).
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _load_products_json() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "data" / "products.json"
    if not path.exists():
        sys.stderr.write(
            f"ERROR: {path} not found.\n"
            "Place Alex's original products.json (89 rows) at data/products.json "
            "before running this one-time prep script.\n"
        )
        raise SystemExit(2)
    with open(path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


async def _load_category_id_to_name(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(
        text("SELECT external_id, name FROM categories")
    )
    return {
        str(row._mapping["external_id"]): row._mapping["name"]
        for row in result.fetchall()
    }


def _resolve_category(row: dict[str, Any], id_to_name: dict[str, str]) -> str:
    """Return the category NAME for a product row, or '' if unassigned."""
    raw = row.get("category_id")
    if raw is None or raw == "":
        return ""
    return id_to_name.get(str(raw), "")


async def prepare() -> Path:
    products = _load_products_json()

    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        id_to_name = await _load_category_id_to_name(session)

    out_path = Path(__file__).resolve().parents[1] / "data" / "products_initial.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Category", "Description", "Price Rub", "Photo Url"])
        for prod in products:
            name = prod.get("name", "")
            category = _resolve_category(prod, id_to_name)
            price = prod.get("price_rub")
            price_str = "" if price is None else str(price)
            writer.writerow([name, category, "", price_str, ""])

    await engine.dispose()
    print(f"Wrote {len(products)} rows to {out_path}")
    return out_path


if __name__ == "__main__":
    asyncio.run(prepare())
