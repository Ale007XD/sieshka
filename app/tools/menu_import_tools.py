"""app/tools/menu_import_tools.py — nano-vm Tool for the menu CSV import Program.

Single terminal TOOL step (`apply_menu_import`) that upserts the already
validated `valid_rows` in ONE batch. This is the ONLY place that mutates
product rows during an import — wrapped by GovernedToolExecutor exactly like
every other write Tool in this project (see app.services.menu_import_service
for the governed wiring).

CONSTRAINTS:
  - session is a named first parameter — closure-injected by functools.partial
    in menu_import_service._build_vm(), never serialised in Trace/Receipt
  - All PG writes happen inside the single session transaction opened by the
    calling MenuImportService; commit/rollback is owned by the service, NOT here
  - Upsert identity is BY NAME (case-insensitive exact match) — the only
    identity strategy for products in this project
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def apply_menu_import(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    **kwargs: object,
) -> dict[str, int]:
    """Terminal tool: batch-upsert already-validated product rows.

    `rows` is a list of ProductRow.model_dump() dicts (name, category_id,
    price_rub, description, image_url, is_active). All field-level validation
    and category/name resolution already happened in parse_and_validate_csv,
    so this step only does the DB write.

    Returns {"imported": N, "rows": M} where N is the count successfully
    written and M is the number of rows handed to the tool.
    """
    imported = 0
    for row in rows:
        name = row["name"]
        category_id_raw = row.get("category_id")
        # Defensive: category_id_raw is normally a str (caller model_dump(mode=
        # "json")'d it before putting it in Trace context), but tolerate an
        # already-UUID instance too — UUID(<UUID instance>) raises (it wants a
        # hex string/bytes, not another UUID), which silently broke every row
        # with a non-null category through this exact path before 2026-07-15.
        if isinstance(category_id_raw, UUID):
            category_id: UUID | None = category_id_raw
        elif category_id_raw:
            category_id = UUID(category_id_raw)
        else:
            category_id = None
        price_rub = row.get("price_rub")
        description = row.get("description")
        image_url = row.get("image_url")
        is_active = bool(row.get("is_active"))

        existing = await session.execute(
            text("SELECT id FROM products WHERE lower(name) = lower(:name)"),
            {"name": name},
        )
        matches = existing.fetchall()
        if len(matches) > 1:
            # Ambiguity should have been caught at parse time; defensively skip
            # rather than guess which row to update.
            logger.warning("apply_menu_import: ambiguous name '%s' — skipped", name)
            continue

        params: dict[str, Any] = {
            "name": name,
            "category_id": category_id,
            "price_rub": price_rub,
            "description": description,
            "image_url": image_url,
            "is_active": is_active,
        }

        if not matches:
            await session.execute(
                text(
                    "INSERT INTO products "
                    "(name, category_id, price_rub, description, image_url, is_active) "
                    "VALUES (:name, :category_id, :price_rub, :description, "
                    ":image_url, :is_active)"
                ),
                params,
            )
        else:
            params["id"] = matches[0]._mapping["id"]
            await session.execute(
                text(
                    "UPDATE products SET "
                    "category_id = :category_id, "
                    "price_rub = :price_rub, "
                    "description = :description, "
                    "image_url = :image_url, "
                    "is_active = :is_active "
                    "WHERE id = :id"
                ),
                params,
            )
        imported += 1

    logger.info("apply_menu_import: imported %d/%d rows", imported, len(rows))
    return {"imported": imported, "rows": len(rows)}
