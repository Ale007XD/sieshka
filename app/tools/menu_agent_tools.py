"""
app/tools/menu_agent_tools.py — nano-vm Tools for MenuAgent programs.

Two phases live here (see app/agents/README.md — "Agent apply-phase CONVENTION"):

COLLECT phase (existing, NOT mutation):
  - validate_menu_command / collect_menu_command / report_collect_failure
  - stops at a terminal JSON command; writes NOTHING to Postgres.

APPLY phase (this sprint — the ONLY phase allowed to write):
  - validate_apply_command  [TOOL] numeric sentinel 0/1 for a CONDITION consumer
  - apply_menu_command      [TOOL, is_terminal] the ONE write step, governed
  - report_invalid_command  [TOOL, is_terminal] invalid-branch terminal

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for CONDITION-consumed validators only.
  - apply_menu_command has NO downstream CONDITION reading its output, so per
    CONSTRAINTS.md "Terminal TOOL step failure propagation" it MUST *raise* on
    any write failure — never return an "ERROR"/0/1 sentinel that nothing
    consumes, or Trace.status stays SUCCESS regardless of what happened in PG.
  - session is a named first parameter (not **kwargs) — closure-injected by
    functools.partial in MenuAgent._build_apply_vm(); never serialised in Trace.
  - No session.commit() inside any tool — commit/rollback is the caller's job
    (CONSTRAINTS.md "Tool-authoring: side-effect session boundary").
  - validate_* is an early-rejection convenience only. apply_menu_command MUST
    re-verify at write time inside the same transaction (TOCTOU: a concurrent
    second agent invocation can interleave between validate and apply).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# COLLECT phase (existing — not mutation)
# ---------------------------------------------------------------------------


async def validate_menu_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if LLM output contains valid structured command, 0 otherwise.

    Validates that the JSON contains required fields:
    menu_id (str), items (list of dict), category (str).
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_menu_command: empty LLM output")
        return 0
    try:
        data = json.loads(llm_output)
        if not isinstance(data, dict):
            return 0
        if "menu_id" not in data:
            logger.warning("validate_menu_command: missing menu_id")
            return 0
        if "items" not in data or not isinstance(data["items"], list):
            logger.warning("validate_menu_command: missing/invalid items")
            return 0
        if "category" not in data or not isinstance(data["category"], str):
            logger.warning("validate_menu_command: missing/invalid category")
            return 0
        logger.info("validate_menu_command: valid command")
        return 1
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_menu_command: invalid JSON")
        return 0


async def collect_menu_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command.

    This is the success terminal — the structured command passes through
    for downstream execution via GovernedToolExecutor.
    """
    logger.info("collect_menu_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that menu collection failed.

    reason is the numeric sentinel (0/1) converted to string by the
    CONDITION step branch that leads here.
    """
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase (this sprint — the ONLY phase allowed to write to Postgres)
# ---------------------------------------------------------------------------


def _required_apply_fields(command: Any) -> tuple[str, str, int] | None:
    """Extract (name, category, price_rub) if the command is shaped correctly.

    Returns None on any structural problem so both the validator and the write
    step share one definition of "well-formed apply command".
    """
    if not isinstance(command, dict):
        return None
    name = command.get("name")
    category = command.get("category")
    price_rub = command.get("price_rub")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(category, str) or not category.strip():
        return None
    if isinstance(price_rub, bool) or not isinstance(price_rub, int):
        return None
    if price_rub < 0:
        return None
    return name.strip(), category.strip(), price_rub


async def validate_apply_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience for the apply phase. Numeric sentinel.

    Returns 1 when the command is well-formed AND (at validate time) the target
    category resolves to exactly one row and the product name is not already in
    use. Returns 0 otherwise. This is NOT the enforcement point — apply_menu_command
    re-verifies everything at write time (see its docstring re: TOCTOU).
    """
    parsed = _required_apply_fields(command)
    if parsed is None:
        logger.warning("validate_apply_command: malformed command")
        return 0
    name, category, _price = parsed

    cat = await session.execute(
        text("SELECT id FROM categories WHERE lower(name) = lower(:name)"),
        {"name": category},
    )
    cat_matches = cat.fetchall()
    if len(cat_matches) != 1:
        logger.warning(
            "validate_apply_command: category '%s' resolves to %d rows",
            category, len(cat_matches),
        )
        return 0

    prod = await session.execute(
        text("SELECT id FROM products WHERE lower(name) = lower(:name)"),
        {"name": name},
    )
    if prod.fetchall():
        logger.warning("validate_apply_command: product name '%s' already in use", name)
        return 0

    logger.info("validate_apply_command: '%s' valid at validate time", name)
    return 1


async def apply_menu_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: write the confirmed command to the products table.

    This is the ONLY step in the whole agent Program allowed to write to
    Postgres, and it runs through the SAME GovernedToolExecutor capability-gate
    (menu:write) every other write Tool goes through.

    FAILURE MODE: is_terminal with NO downstream CONDITION reading its output,
    so it MUST raise on any write failure — never return a sentinel that nothing
    consumes (CONSTRAINTS.md "Terminal TOOL step failure propagation").

    TOCTOU RE-CHECK: validate_apply_command ran earlier, in its own step, and
    must not be trusted here. A concurrent second agent invocation could
    interleave between validate and apply. So this step RE-VERIFIES at write
    time, inside this transaction, using SELECT ... FOR UPDATE on the category
    row (same race-guard shape as order_tools.py, DECISIONS.md 2026-07-01/02):
      - the target category still resolves to exactly one row, and
      - the product name is still not in use.
    A violation raises → Trace.status becomes FAILED and the caller rolls back.
    """
    parsed = _required_apply_fields(command)
    if parsed is None:
        raise ValueError("apply_menu_command: malformed command")
    name, category, price_rub = parsed

    description = command.get("description")
    image_url = command.get("image_url")

    # Re-resolve the category AT WRITE TIME under a row lock, so a concurrent
    # apply cannot delete/rename it out from under us between the two SELECTs.
    cat = await session.execute(
        text(
            "SELECT id FROM categories WHERE lower(name) = lower(:name) FOR UPDATE"
        ),
        {"name": category},
    )
    cat_matches = cat.fetchall()
    if len(cat_matches) != 1:
        logger.error(
            "apply_menu_command: category '%s' resolves to %d rows at write time",
            category, len(cat_matches),
        )
        raise ValueError(
            f"category not uniquely resolvable at write time: {category!r} "
            f"({len(cat_matches)} matches)"
        )
    category_id: UUID = cat_matches[0]._mapping["id"]

    # Re-check name-not-in-use AT WRITE TIME. A concurrent apply that inserted
    # the same product name between validate and now must lose here, not silently
    # create a duplicate.
    prod = await session.execute(
        text("SELECT id FROM products WHERE lower(name) = lower(:name)"),
        {"name": name},
    )
    if prod.fetchall():
        logger.error(
            "apply_menu_command: product name '%s' already in use at write time", name
        )
        raise ValueError(f"product name already in use at write time: {name!r}")

    await session.execute(
        text(
            "INSERT INTO products "
            "(name, category_id, price_rub, description, image_url, is_active) "
            "VALUES (:name, :category_id, :price_rub, :description, :image_url, TRUE)"
        ),
        {
            "name": name,
            "category_id": category_id,
            "price_rub": price_rub,
            "description": description,
            "image_url": image_url,
        },
    )
    logger.info("apply_menu_command: wrote product '%s' (category_id=%s)", name, category_id)
    return {"applied": True, "name": name, "category_id": str(category_id)}


async def report_invalid_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the apply phase.

    `reason` is the numeric sentinel (0/1) converted to string by the CONDITION
    branch that leads here. Like report_collect_failure this is a *consumed*
    branch — it does not raise; the Program deliberately reached a valid,
    successful "command was invalid, nothing written" terminal.
    """
    logger.warning("report_invalid_command: %s", reason)
    return f"INVALID:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase — category creation (mirrors product apply above)
# ---------------------------------------------------------------------------


def _required_apply_category_fields(
    command: Any,
) -> tuple[str, str | None, str, int] | None:
    """Extract (name, parent_category|None, menu_period, sort) if well-formed."""
    if not isinstance(command, dict):
        return None
    name = command.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    parent_category = command.get("parent_category")
    if parent_category is not None and (
        not isinstance(parent_category, str) or not parent_category.strip()
    ):
        return None
    menu_period = command.get("menu_period", "both")
    if menu_period not in ("both", "delivery", "pickup"):
        return None
    sort = command.get("sort", 0)
    if isinstance(sort, bool) or not isinstance(sort, int):
        return None
    return (
        name.strip(),
        parent_category.strip() if parent_category else None,
        menu_period,
        sort,
    )


async def validate_apply_category_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience for category apply. Numeric sentinel.

    NOT the enforcement point — apply_category_command re-verifies at write
    time (TOCTOU, same shape as validate_apply_command).
    """
    parsed = _required_apply_category_fields(command)
    if parsed is None:
        logger.warning("validate_apply_category_command: malformed command")
        return 0
    name, parent_category, _menu_period, _sort = parsed

    existing = await session.execute(
        text("SELECT id FROM categories WHERE lower(name) = lower(:name)"),
        {"name": name},
    )
    if existing.fetchall():
        logger.warning(
            "validate_apply_category_command: name '%s' already in use", name
        )
        return 0

    if parent_category is not None:
        parent = await session.execute(
            text("SELECT id FROM categories WHERE lower(name) = lower(:name)"),
            {"name": parent_category},
        )
        parent_matches = parent.fetchall()
        if len(parent_matches) != 1:
            logger.warning(
                "validate_apply_category_command: parent '%s' resolves to %d rows",
                parent_category, len(parent_matches),
            )
            return 0

    logger.info("validate_apply_category_command: '%s' valid at validate time", name)
    return 1


async def apply_category_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: write a new category row.

    is_terminal, no downstream CONDITION → MUST raise on any write failure
    (CONSTRAINTS.md "Terminal TOOL step failure propagation").

    TOCTOU RE-CHECK at write time, same shape as apply_menu_command. NOTE:
    categories.name carries no UNIQUE constraint (migrations/004_menu.sql,
    index only) — this check-then-insert still has a narrow race window under
    true concurrency, matching the existing (also unindexed-unique) behaviour
    of apply_menu_command against products.name. Flagged as a known gap, not
    silently patched here with a schema change.
    """
    parsed = _required_apply_category_fields(command)
    if parsed is None:
        raise ValueError("apply_category_command: malformed command")
    name, parent_category, menu_period, sort = parsed

    parent_id: UUID | None = None
    if parent_category is not None:
        parent = await session.execute(
            text(
                "SELECT id FROM categories WHERE lower(name) = lower(:name) FOR UPDATE"
            ),
            {"name": parent_category},
        )
        parent_matches = parent.fetchall()
        if len(parent_matches) != 1:
            logger.error(
                "apply_category_command: parent '%s' resolves to %d rows at write time",
                parent_category, len(parent_matches),
            )
            raise ValueError(
                f"parent category not uniquely resolvable at write time: "
                f"{parent_category!r} ({len(parent_matches)} matches)"
            )
        parent_id = parent_matches[0]._mapping["id"]

    existing = await session.execute(
        text("SELECT id FROM categories WHERE lower(name) = lower(:name)"),
        {"name": name},
    )
    if existing.fetchall():
        logger.error(
            "apply_category_command: name '%s' already in use at write time", name
        )
        raise ValueError(f"category name already in use at write time: {name!r}")

    result = await session.execute(
        text(
            "INSERT INTO categories (name, parent_category_id, menu_period, sort, is_active) "
            "VALUES (:name, :parent_id, :menu_period, :sort, TRUE) "
            "RETURNING id"
        ),
        {"name": name, "parent_id": parent_id, "menu_period": menu_period, "sort": sort},
    )
    row = result.fetchone()
    assert row is not None
    category_id: UUID = row._mapping["id"]
    logger.info("apply_category_command: wrote category '%s' (id=%s)", name, category_id)
    return {"applied": True, "name": name, "category_id": str(category_id)}


async def report_invalid_category_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the category apply phase."""
    logger.warning("report_invalid_category_command: %s", reason)
    return f"INVALID:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase — product update (mirrors zone_agent update action pattern)
# ---------------------------------------------------------------------------


def _required_update_product_fields(
    command: Any,
) -> tuple[str, str | None, str | None, int | None, str | None, str | None, bool | None] | None:
    """Extract (product_id, name, category, price_rub, description, image_url, is_active).

    product_id is required (UUID string). All other fields are optional —
    None means "leave unchanged" (COALESCE pattern at write time).
    Returns None on any structural problem.
    """
    if not isinstance(command, dict):
        return None
    product_id = command.get("product_id")
    if not isinstance(product_id, str) or not product_id.strip():
        return None

    name = command.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        return None

    category = command.get("category")
    if category is not None and (not isinstance(category, str) or not category.strip()):
        return None

    price_rub = command.get("price_rub")
    if price_rub is not None:
        if isinstance(price_rub, bool) or not isinstance(price_rub, int):
            return None
        if price_rub < 0:
            return None

    description = command.get("description")
    if description is not None and not isinstance(description, str):
        return None

    image_url = command.get("image_url")
    if image_url is not None and not isinstance(image_url, str):
        return None

    is_active_raw = command.get("is_active")
    is_active: bool | None
    if is_active_raw is None or isinstance(is_active_raw, bool):
        is_active = is_active_raw
    elif isinstance(is_active_raw, str):
        lowered = is_active_raw.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            is_active = True
        elif lowered in {"false", "0", "no", "off", ""}:
            is_active = False
        else:
            return None
    elif isinstance(is_active_raw, int) and not isinstance(is_active_raw, bool):
        if is_active_raw == 1:
            is_active = True
        elif is_active_raw == 0:
            is_active = False
        else:
            return None
    else:
        return None

    return (
        product_id.strip(),
        name.strip() if name else None,
        category.strip() if category else None,
        price_rub,
        description,
        image_url,
        is_active,
    )


async def validate_update_product_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience for product update. Numeric sentinel.

    NOT the enforcement point — apply_update_product_command re-verifies at
    write time (TOCTOU, same shape as validate_apply_command).
    """
    parsed = _required_update_product_fields(command)
    if parsed is None:
        logger.warning("validate_update_product_command: malformed command")
        return 0
    product_id, _name, category, _price, _desc, _img, _active = parsed

    existing = await session.execute(
        text("SELECT id FROM products WHERE id = :id"),
        {"id": product_id},
    )
    if not existing.fetchall():
        logger.warning(
            "validate_update_product_command: product_id '%s' not found", product_id
        )
        return 0

    if category is not None:
        cat = await session.execute(
            text("SELECT id FROM categories WHERE lower(name) = lower(:name)"),
            {"name": category},
        )
        if len(cat.fetchall()) != 1:
            logger.warning(
                "validate_update_product_command: category '%s' not uniquely resolvable",
                category,
            )
            return 0

    logger.info("validate_update_product_command: product_id '%s' valid", product_id)
    return 1


async def apply_update_product_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: update an existing product row.

    is_terminal, no downstream CONDITION → MUST raise on any write failure.

    TOCTOU RE-CHECK: product row locked with FOR UPDATE; category re-resolved
    inside the same transaction (same discipline as apply_menu_command).

    Only fields present and non-None in the command are updated; absent fields
    are left unchanged via COALESCE (same pattern as zone_agent update action).
    """
    parsed = _required_update_product_fields(command)
    if parsed is None:
        raise ValueError("apply_update_product_command: malformed command")
    product_id, name, category, price_rub, description, image_url, is_active = parsed

    # Re-check product exists and lock the row.
    existing = await session.execute(
        text("SELECT id FROM products WHERE id = :id FOR UPDATE"),
        {"id": product_id},
    )
    if not existing.fetchall():
        logger.error(
            "apply_update_product_command: product_id '%s' not found at write time",
            product_id,
        )
        raise ValueError(f"product not found at write time: {product_id!r}")

    # Resolve category if provided.
    category_id: UUID | None = None
    if category is not None:
        cat = await session.execute(
            text(
                "SELECT id FROM categories WHERE lower(name) = lower(:name) FOR UPDATE"
            ),
            {"name": category},
        )
        cat_matches = cat.fetchall()
        if len(cat_matches) != 1:
            logger.error(
                "apply_update_product_command: category '%s' resolves to %d rows at write time",
                category, len(cat_matches),
            )
            raise ValueError(
                f"category not uniquely resolvable at write time: {category!r} "
                f"({len(cat_matches)} matches)"
            )
        category_id = cat_matches[0]._mapping["id"]

    await session.execute(
        text(
            "UPDATE products SET "
            "name        = COALESCE(:name,        name), "
            "category_id = COALESCE(:category_id, category_id), "
            "price_rub   = COALESCE(:price_rub,   price_rub), "
            "description = COALESCE(:description, description), "
            "image_url   = COALESCE(:image_url,   image_url), "
            "is_active   = COALESCE(:is_active,   is_active) "
            "WHERE id = :product_id"
        ),
        {
            "product_id": product_id,
            "name": name,
            "category_id": category_id,
            "price_rub": price_rub,
            "description": description,
            "image_url": image_url,
            "is_active": is_active,
        },
    )
    logger.info("apply_update_product_command: updated product id=%s", product_id)
    return {"applied": True, "product_id": product_id}


async def report_invalid_update_product_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the product update phase."""
    logger.warning("report_invalid_update_product_command: %s", reason)
    return f"INVALID:{reason}"
