"""scripts/import_delivery_zones.py — one-time import for Alex's 3-zone dataset.

Usage: python -m scripts.import_delivery_zones

Loads data/delivery_zones.json directly (same import-now principle as
menu_domain — the data is already in hand, so don't defer to a later sprint).
Zones carry NO price field; we only store name, delivery_time_minutes, is_active.

Gate check: all 3 zones present and is_active = True.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _load_zones() -> list[dict[str, object]]:
    path = Path(__file__).resolve().parents[1] / "data" / "delivery_zones.json"
    with open(path, encoding="utf-8") as f:
        data: list[dict[str, object]] = json.load(f)
    return data


async def import_zones() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    zones = _load_zones()

    async with factory() as session:
        for zone in zones:
            await session.execute(
                text(
                    "INSERT INTO delivery_zones "
                    "(external_id, name, delivery_time_minutes, is_active) "
                    "VALUES (:external_id, :name, :delivery_time_minutes, :is_active) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "external_id": str(zone["id"]),
                    "name": zone["name"],
                    "delivery_time_minutes": zone["delivery_time_minutes"],
                    "is_active": zone.get("is_active", True),
                },
            )
        await session.commit()

    # Gate check
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM delivery_zones "
                "WHERE is_active = TRUE"
            )
        )
        active_count = result.scalar()
        print(f"Import complete: {active_count} active delivery zones loaded.")
        assert active_count == 3, f"Expected 3 active zones, got {active_count}"

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(import_zones())
