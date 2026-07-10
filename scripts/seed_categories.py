"""scripts/seed_categories.py — one-time seed for the 24-row category dataset.

Usage: python -m scripts.seed_categories

Loads data/categories.json, resolves parent by NAME in two passes:
  1. Insert all rows (parent_category_id = NULL)
  2. Resolve parent_name -> parent_category_id FK

Run against real data: python -m scripts.seed_categories
Gate check: count of categories == 24
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _load_categories() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "data" / "categories.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def seed() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    categories = _load_categories()
    name_to_id: dict[str, str | None] = {}

    async with factory() as session:
        # Pass 1: insert all rows with parent_category_id = NULL
        for cat in categories:
            result = await session.execute(
                text(
                    "INSERT INTO categories (external_id, name, menu_period, sort, is_active) "
                    "VALUES (:external_id, :name, :menu_period, :sort, :is_active) "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id, name"
                ),
                {
                    "external_id": str(cat["id"]),
                    "name": cat["name"],
                    "menu_period": cat["menu_period"],
                    "sort": cat["sort"],
                    "is_active": cat["is_active"],
                },
            )
            row = result.first()
            if row:
                name_to_id[row._mapping["name"]] = str(row._mapping["id"])

        await session.commit()

    # Pass 2: resolve parent_name -> parent_category_id
    async with factory() as session:
        for cat in categories:
            parent_name = cat.get("parent")
            if parent_name:
                parent_id = name_to_id.get(parent_name)
                if parent_id:
                    await session.execute(
                        text(
                            "UPDATE categories SET parent_category_id = :parent_id "
                            "WHERE name = :name"
                        ),
                        {"parent_id": parent_id, "name": cat["name"]},
                    )
        await session.commit()

    # Gate check
    async with factory() as session:
        result = await session.execute(text("SELECT count(*) FROM categories"))
        count = result.scalar()
        print(f"Seed complete: {count} categories loaded.")
        assert count == 24, f"Expected 24 categories, got {count}"

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
