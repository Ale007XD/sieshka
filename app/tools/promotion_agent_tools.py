"""
app/tools/promotion_agent_tools.py — nano-vm Tools for PromotionAgent programs.

COLLECT phase (NOT mutation): validate_promotion_command / collect_promotion_command
  / report_collect_failure — stops at a terminal JSON command, writes NOTHING.

APPLY phase (the ONLY phase allowed to write):
  validate_apply_promotion_command  [TOOL] numeric sentinel 0/1
  apply_promotion_command           [TOOL, is_terminal] the ONE write step
  report_invalid_promotion_command  [TOOL, is_terminal] invalid-branch terminal

Command shape matches the real `promotions` table + its FSM transition table
(app/domains/promotions/models.py::PROMOTION_TRANSITIONS) — CREATED->ACTIVE->
EXPIRED->ARCHIVED via ACTIVATE/EXPIRE/ARCHIVE events:
  {"action": "create"|"activate"|"expire"|"archive",
   "name": str|None, "discount": number|None,
   "target_promotion_name": str|None}

CONSTRAINTS (same discipline as menu/zone apply tools):
  - Numeric sentinel returns (0/1) for CONDITION-consumed validators only.
  - apply_promotion_command has NO downstream CONDITION reading its output ->
    MUST raise on any write failure (CONSTRAINTS.md "Terminal TOOL step
    failure propagation").
  - session is a named first parameter, closure-injected — never opened
    independently inside a tool, never calls commit() (caller's job).
  - validate_* is early-rejection only; apply_* re-verifies at write time
    under FOR UPDATE (TOCTOU — a concurrent second invocation can interleave).
  - State transitions are re-checked against PROMOTION_TRANSITIONS at write
    time using the CURRENT state under lock, not whatever validate saw —
    same discipline as order_tools.py's transition_order_state race-guard.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.promotions.models import PROMOTION_TRANSITIONS, PromotionEvent, PromotionState

logger = logging.getLogger(__name__)

_ACTION_TO_EVENT: dict[str, PromotionEvent] = {
    "activate": PromotionEvent.ACTIVATE,
    "expire": PromotionEvent.EXPIRE,
    "archive": PromotionEvent.ARCHIVE,
}


# ---------------------------------------------------------------------------
# COLLECT phase (not mutation)
# ---------------------------------------------------------------------------


async def validate_promotion_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if LLM output is a well-formed promotion command, 0 otherwise."""
    if not llm_output or not llm_output.strip():
        logger.warning("validate_promotion_command: empty LLM output")
        return 0
    try:
        data = json.loads(llm_output)
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_promotion_command: invalid JSON")
        return 0
    if not isinstance(data, dict):
        return 0
    action = data.get("action")
    if action not in ("create", "activate", "expire", "archive"):
        logger.warning("validate_promotion_command: invalid action %r", action)
        return 0
    if action == "create":
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            return 0
        discount = data.get("discount")
        if isinstance(discount, bool) or not isinstance(discount, (int, float)):
            return 0
    else:
        target = data.get("target_promotion_name")
        if not isinstance(target, str) or not target.strip():
            return 0
    logger.info("validate_promotion_command: valid command (action=%s)", action)
    return 1


async def collect_promotion_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command."""
    logger.info("collect_promotion_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that promotion collection failed."""
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase (the ONLY phase allowed to write to Postgres)
# ---------------------------------------------------------------------------


def _required_apply_fields(
    command: Any,
) -> tuple[str, str | None, float | None, str | None] | None:
    """Extract (action, name?, discount?, target_promotion_name?) if well-formed.

    Shared by validator and write step so both agree on one definition of
    "well-formed apply command" — same pattern as menu/zone/category tools.
    """
    if not isinstance(command, dict):
        return None
    action = command.get("action")
    if action not in ("create", "activate", "expire", "archive"):
        return None

    name = command.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        return None

    discount = command.get("discount")
    if discount is not None and (
        isinstance(discount, bool) or not isinstance(discount, (int, float))
    ):
        return None

    target_promotion_name = command.get("target_promotion_name")
    if target_promotion_name is not None and (
        not isinstance(target_promotion_name, str) or not target_promotion_name.strip()
    ):
        return None

    if action == "create":
        if name is None or discount is None:
            return None
    else:
        if target_promotion_name is None:
            return None

    return (
        action,
        name.strip() if name else None,
        float(discount) if discount is not None else None,
        target_promotion_name.strip() if target_promotion_name else None,
    )


async def validate_apply_promotion_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience. NOT the enforcement point — apply_promotion_command
    re-verifies everything at write time (TOCTOU)."""
    parsed = _required_apply_fields(command)
    if parsed is None:
        logger.warning("validate_apply_promotion_command: malformed command")
        return 0
    action, name, discount, target_promotion_name = parsed

    if action == "create":
        assert name is not None and discount is not None
        if discount < 0 or discount > 100:
            logger.warning("validate_apply_promotion_command: discount %s out of range", discount)
            return 0
        existing = await session.execute(
            text("SELECT id FROM promotions WHERE lower(name) = lower(:name)"),
            {"name": name},
        )
        if existing.fetchall():
            logger.warning("validate_apply_promotion_command: name '%s' already in use", name)
            return 0
        return 1

    assert target_promotion_name is not None
    row = await session.execute(
        text("SELECT id, state FROM promotions WHERE lower(name) = lower(:name)"),
        {"name": target_promotion_name},
    )
    matches = row.fetchall()
    if len(matches) != 1:
        logger.warning(
            "validate_apply_promotion_command: '%s' resolves to %d rows",
            target_promotion_name, len(matches),
        )
        return 0

    current_state = PromotionState(matches[0]._mapping["state"])
    event = _ACTION_TO_EVENT[action]
    if (current_state, event) not in PROMOTION_TRANSITIONS:
        logger.warning(
            "validate_apply_promotion_command: %s not allowed from state %s",
            event, current_state,
        )
        return 0

    logger.info("validate_apply_promotion_command: action=%s valid at validate time", action)
    return 1


async def apply_promotion_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: create a promotion or transition an existing one's state.

    is_terminal, no downstream CONDITION -> MUST raise on any write failure
    (CONSTRAINTS.md "Terminal TOOL step failure propagation").

    TOCTOU RE-CHECK: row locked with FOR UPDATE; name-uniqueness (create) and
    FSM-transition validity (activate/expire/archive) are re-verified against
    the CURRENT state under lock, not whatever validate saw earlier — same
    race-guard discipline as order_tools.py::transition_order_state.
    """
    parsed = _required_apply_fields(command)
    if parsed is None:
        raise ValueError("apply_promotion_command: malformed command")
    action, name, discount, target_promotion_name = parsed

    if action == "create":
        assert name is not None and discount is not None
        if discount < 0 or discount > 100:
            raise ValueError(f"discount out of range at write time: {discount!r}")
        existing = await session.execute(
            text("SELECT id FROM promotions WHERE lower(name) = lower(:name) FOR UPDATE"),
            {"name": name},
        )
        if existing.fetchall():
            raise ValueError(f"promotion name already in use at write time: {name!r}")

        result = await session.execute(
            text(
                "INSERT INTO promotions (name, discount, state) "
                "VALUES (:name, :discount, 'CREATED') RETURNING id"
            ),
            {"name": name, "discount": discount},
        )
        row = result.fetchone()
        assert row is not None
        promo_id: UUID = row._mapping["id"]
        logger.info("apply_promotion_command: created promotion '%s' (id=%s)", name, promo_id)
        return {"applied": True, "action": "create", "name": name, "promotion_id": str(promo_id)}

    assert target_promotion_name is not None
    locked = await session.execute(
        text(
            "SELECT id, state FROM promotions WHERE lower(name) = lower(:name) FOR UPDATE"
        ),
        {"name": target_promotion_name},
    )
    matches = locked.fetchall()
    if len(matches) != 1:
        logger.error(
            "apply_promotion_command: '%s' resolves to %d rows at write time",
            target_promotion_name, len(matches),
        )
        raise ValueError(
            f"promotion not uniquely resolvable at write time: "
            f"{target_promotion_name!r} ({len(matches)} matches)"
        )
    promo_id = matches[0]._mapping["id"]
    current_state = PromotionState(matches[0]._mapping["state"])
    event = _ACTION_TO_EVENT[action]
    new_state = PROMOTION_TRANSITIONS.get((current_state, event))
    if new_state is None:
        logger.error(
            "apply_promotion_command: %s not allowed from state %s at write time (id=%s)",
            event, current_state, promo_id,
        )
        raise ValueError(
            f"invalid transition at write time: {event.value} from {current_state.value}"
        )

    await session.execute(
        text("UPDATE promotions SET state = :new_state WHERE id = :id"),
        {"new_state": new_state.value, "id": promo_id},
    )
    logger.info(
        "apply_promotion_command: %s -> %s (id=%s)", current_state.value, new_state.value, promo_id
    )
    return {
        "applied": True,
        "action": action,
        "promotion_id": str(promo_id),
        "from_state": current_state.value,
        "to_state": new_state.value,
    }


async def report_invalid_promotion_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the apply phase."""
    logger.warning("report_invalid_promotion_command: %s", reason)
    return f"INVALID:{reason}"