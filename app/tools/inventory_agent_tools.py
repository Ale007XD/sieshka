"""
app/tools/inventory_agent_tools.py — nano-vm Tools for InventoryAgent programs.

COLLECT phase (NOT mutation): validate_restock_command / collect_restock_command
  — stops at a terminal JSON command, writes NOTHING. report_collect_failure is
  reused from promotion_agent_tools.py (fully generic — logs + FAILED: sentinel,
  no promotion-specific logic).

APPLY phase (the ONLY phase allowed to write):
  validate_apply_restock_command  [TOOL] numeric sentinel 0/1
  apply_restock_command           [TOOL, is_terminal] the ONE write step —
    delegates to inventory_tools.increment_inventory (reason=RESTOCK_AGENT),
    reusing sprint_inventory_ledger's movement-write path rather than
    duplicating it.
  report_invalid_restock_command  [TOOL, is_terminal] invalid-branch terminal

Command shape (single action — restock only, no state machine like promotions):
  {"sku": str, "quantity": int}

sku must already exist in `inventory` (sprint_inventory_menu_sync's sync_from_menu
or manual creation) — this agent restocks existing items, it does not create
new sku rows (that's Generate SKUs / Sync from menu on the Menu/Inventory admin
pages, a separate deliberate admin action).

CONSTRAINTS (same discipline as menu/zone/promotion apply tools):
  - Numeric sentinel returns (0/1) for CONDITION-consumed validators only.
  - apply_restock_command has NO downstream CONDITION reading its output ->
    MUST raise on any write failure (CONSTRAINTS.md "Terminal TOOL step
    failure propagation").
  - session is a named first parameter, closure-injected — never opened
    independently inside a tool, never calls commit() (caller's job).
  - validate_* is early-rejection only; apply_* re-verifies at write time
    under FOR UPDATE (TOCTOU) — same discipline as increment_inventory itself
    already locking the row.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# COLLECT phase (not mutation)
# ---------------------------------------------------------------------------


async def validate_restock_command(
    llm_output: str,
    diagnostics: dict[str, str] | None = None,
    **kwargs: object,
) -> int:
    """Returns 1 if LLM output is a well-formed restock command, 0 otherwise.

    `diagnostics` is a closure-injected side-channel (same convention as
    `session` elsewhere) so the calling agent method can surface a real
    reason instead of the numeric CONDITION sentinel — see
    promotion_agent_tools.py::validate_promotion_command's docstring.
    """
    def _reject(reason: str) -> int:
        logger.warning("validate_restock_command: %s", reason)
        if diagnostics is not None:
            diagnostics["reason"] = reason
        return 0

    if not llm_output or not llm_output.strip():
        return _reject("empty instruction — nothing to parse")
    try:
        data = json.loads(llm_output)
    except (json.JSONDecodeError, ValueError):
        return _reject("could not parse a command from the instruction")
    if not isinstance(data, dict):
        return _reject("parsed command is not an object")

    sku = data.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return _reject("could not identify which sku to restock")

    quantity = data.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return _reject("could not identify a whole-number quantity to add")
    if quantity <= 0:
        return _reject(f"quantity must be positive, got {quantity}")

    logger.info("validate_restock_command: valid command (sku=%s, quantity=%d)", sku, quantity)
    return 1


async def collect_restock_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command."""
    logger.info("collect_restock_command: command collected")
    return command


# ---------------------------------------------------------------------------
# APPLY phase (the ONLY phase allowed to write to Postgres)
# ---------------------------------------------------------------------------


def _required_apply_fields(command: Any) -> tuple[str, int] | None:
    """Extract (sku, quantity) if well-formed. Shared by validator and write
    step so both agree on one definition of "well-formed apply command" —
    same pattern as menu/zone/promotion apply tools."""
    if not isinstance(command, dict):
        return None
    sku = command.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return None
    quantity = command.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return None
    if quantity <= 0:
        return None
    return sku.strip(), quantity


async def validate_apply_restock_command(
    session: AsyncSession,
    command: Any,
    diagnostics: dict[str, str] | None = None,
    **kwargs: object,
) -> int:
    """Early-rejection convenience. NOT the enforcement point —
    apply_restock_command re-verifies everything at write time (TOCTOU, via
    increment_inventory's own FOR UPDATE lock)."""
    def _reject(reason: str) -> int:
        logger.warning("validate_apply_restock_command: %s", reason)
        if diagnostics is not None:
            diagnostics["reason"] = reason
        return 0

    parsed = _required_apply_fields(command)
    if parsed is None:
        return _reject("malformed command")
    sku, _quantity = parsed

    existing = await session.execute(
        text("SELECT id FROM inventory WHERE sku = :sku"),
        {"sku": sku},
    )
    if not existing.fetchall():
        return _reject(
            f"sku {sku!r} not found in inventory — restock only adds to an "
            f"existing item, use Sync from menu / Generate SKUs to create it first"
        )

    logger.info("validate_apply_restock_command: sku=%s valid at validate time", sku)
    return 1


async def apply_restock_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: restocks one sku via increment_inventory.

    is_terminal, no downstream CONDITION -> MUST raise on any write failure
    (CONSTRAINTS.md "Terminal TOOL step failure propagation"). Delegates the
    actual UPDATE + ledger write to increment_inventory (reason=RESTOCK_AGENT)
    rather than duplicating that logic — increment_inventory already does the
    FOR UPDATE lock (TOCTOU re-check) and raises ValueError on sku not found,
    which this function lets propagate unchanged.
    """
    from app.tools.inventory_tools import increment_inventory

    parsed = _required_apply_fields(command)
    if parsed is None:
        raise ValueError("apply_restock_command: malformed command")
    sku, quantity = parsed

    await increment_inventory(
        session, sku=sku, quantity=quantity, reason="RESTOCK_AGENT", source_type="agent",
    )
    logger.info("apply_restock_command: sku=%s quantity=%d", sku, quantity)
    return {"applied": True, "sku": sku, "quantity": quantity}


async def report_invalid_restock_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the apply phase."""
    logger.warning("report_invalid_restock_command: %s", reason)
    return f"REJECTED:{reason}"
