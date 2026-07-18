"""
app/tools/zone_agent_tools.py — nano-vm Tools for ZoneAgent programs.

Two phases live here (see app/agents/README.md — "Agent apply-phase CONVENTION"):

COLLECT phase (NOT mutation):
  - validate_zone_command / collect_zone_command / report_collect_failure
  - stops at a terminal JSON command; writes NOTHING to Postgres.

APPLY phase (the ONLY phase allowed to write to DeliveryZone):
  - validate_apply_zone_command  [TOOL] numeric sentinel 0/1 for CONDITION
  - apply_zone_command           [TOOL, is_terminal] the ONE write step
  - report_invalid_zone_command  [TOOL, is_terminal] invalid-branch terminal

Command schema (structured output of the LLM, consumed by both phases):
  {
    "action": "create" | "update" | "deactivate",
    "name": str | None,                       # for create (the new zone name)
    "delivery_time_minutes": int | None,      # for create / update
    "target_zone_name": str | None            # for update / deactivate (which
                                              #   existing zone this refers to)
  }
  "deactivate" or "delete" in the parsed intent both map to is_active=False
  (SOFT delete only — see apply_zone_command note).

CONSTRAINTS (same discipline as menu_agent_tools.py / schedule_agent_tools.py):
  - Numeric sentinel returns (0/1) for CONDITION-consumed validators only.
  - apply_zone_command is is_terminal with NO downstream CONDITION, so it MUST
    raise on any write failure — never return a sentinel nobody consumes.
  - session is a named first parameter, closure-injected via functools.partial.
  - No session.commit() inside any tool.
  - validate_* is an early-rejection convenience only; apply re-verifies at
    write time inside the same transaction (TOCTOU).

UNIQUENESS (sprint_m7_zone_agent HARD RULE): a name must be unique among ACTIVE
zones. The DB-level enforcement is the partial unique index
idx_delivery_zones_name_active (migrations/009_zone_name_unique_index.sql).
validate_zone_command does an earlier SELECT (early rejection); apply_zone_command
relies on the index being REAL — a plain INSERT (NOT ON CONFLICT DO UPDATE,
because zone create must REJECT a collision, not overwrite a different row)
surfaces a unique_violation that apply_zone_command catches and re-raises as a
clear ValueError. update/reactivate paths target a specific already-resolved id
and use a plain UPDATE (no ON CONFLICT needed there).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_ACTIONS = ("create", "update", "deactivate")


def _required_command_shape(command: Any) -> dict[str, Any] | None:
    """Return the normalized command if structurally well-formed, else None.

    Shared by both validators so they agree on what a parseable command is.
    Does NOT judge business validity (name-in-use, target resolution) — that is
    layered on by the caller.
    """
    if not isinstance(command, dict):
        return None
    action = command.get("action")
    if action not in _VALID_ACTIONS:
        return None
    name = command.get("name")
    delivery_time_minutes = command.get("delivery_time_minutes")
    target_zone_name = command.get("target_zone_name")

    normalized: dict[str, Any] = {
        "action": action,
        "name": name if isinstance(name, str) and name.strip() else None,
        "delivery_time_minutes": delivery_time_minutes,
        "target_zone_name": (
            target_zone_name
            if isinstance(target_zone_name, str) and target_zone_name.strip()
            else None
        ),
    }

    if action == "create":
        # create requires a name + a positive integer delivery_time_minutes.
        if normalized["name"] is None:
            return None
        if isinstance(delivery_time_minutes, bool) or not isinstance(
            delivery_time_minutes, int
        ):
            return None
        if delivery_time_minutes <= 0:
            return None
    elif action in ("update", "deactivate"):
        # update/deactivate require a target zone name to resolve.
        if normalized["target_zone_name"] is None:
            return None
        if action == "update":
            # update requires at least one of name/delivery_time_minutes to
            # actually change something; delivery_time_minutes, if present,
            # must be a positive integer.
            if normalized["name"] is None and delivery_time_minutes is None:
                return None
            if delivery_time_minutes is not None:
                if isinstance(delivery_time_minutes, bool) or not isinstance(
                    delivery_time_minutes, int
                ):
                    return None
                if delivery_time_minutes <= 0:
                    return None
    return normalized


# ---------------------------------------------------------------------------
# COLLECT phase (not mutation)
# ---------------------------------------------------------------------------


async def validate_zone_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if the LLM output is a parseable zone command, else 0.

    Ambiguous phrasing (no clear action, or an action whose required fields are
    absent) is rejected as unparseable — we do NOT default-guess.
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_zone_command: empty LLM output")
        return 0
    try:
        data = json.loads(llm_output)
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_zone_command: invalid JSON")
        return 0

    parsed = _required_command_shape(data)
    if parsed is None:
        logger.warning("validate_zone_command: malformed command shape")
        return 0
    logger.info("validate_zone_command: parseable command")
    return 1


async def collect_zone_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command."""
    logger.info("collect_zone_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that zone collection failed."""
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase (the ONLY phase allowed to write to DeliveryZone)
# ---------------------------------------------------------------------------


async def validate_apply_zone_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience for the apply phase. Numeric sentinel.

    Returns 1 only when the command is well-formed AND (at validate time) the
    active-name uniqueness / target-resolution invariants hold. Returns 0
    otherwise. Not the enforcement point — apply_zone_command re-verifies at
    write time inside the same transaction (TOCTOU).
    """
    parsed = _required_command_shape(command)
    if parsed is None:
        logger.warning("validate_apply_zone_command: malformed command")
        return 0

    action = parsed["action"]
    if action == "create":
        assert parsed["name"] is not None
        dup = await session.execute(
            text(
                "SELECT id FROM delivery_zones "
                "WHERE lower(name) = lower(:name) AND is_active"
            ),
            {"name": parsed["name"]},
        )
        if dup.fetchall():
            logger.warning(
                "validate_apply_zone_command: active zone name '%s' already in use",
                parsed["name"],
            )
            return 0

    elif action in ("update", "deactivate"):
        assert parsed["target_zone_name"] is not None
        matches = await session.execute(
            text(
                "SELECT id FROM delivery_zones "
                "WHERE lower(name) = lower(:name) AND is_active"
            ),
            {"name": parsed["target_zone_name"]},
        )
        rows = matches.fetchall()
        if len(rows) != 1:
            logger.warning(
                "validate_apply_zone_command: target '%s' resolves to %d rows",
                parsed["target_zone_name"], len(rows),
            )
            return 0

    logger.info("validate_apply_zone_command: valid at validate time")
    return 1


def _is_unique_violation(err: Exception) -> bool:
    """Return True if `err` (or its cause) is a Postgres unique_violation."""
    cause: object = err
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        code = getattr(cause, "sqlstate", None)
        if code == "23505":
            return True
        cause = getattr(cause, "__cause__", None)
    # asyncpg driver_error carries .sqlstate too; fall back to a substring check
    # only when sqlstate is unavailable (driver/integration quirks).
    text_repr = str(err)
    return (
        "duplicate key value violates unique constraint" in text_repr
        or "idx_delivery_zones_name_active" in text_repr
    )


async def apply_zone_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: write the confirmed zone command to DeliveryZone.

    The ONLY step in the whole agent Program allowed to write to Postgres, via
    the SAME GovernedToolExecutor capability gate (delivery:write).

    MUST raise on any write failure. It is is_terminal with no downstream
    CONDITION reading its output, so a returned sentinel would leave
    Trace.status == SUCCESS regardless of what happened in Postgres.

    Behavior per action:
      create     : plain INSERT of a new active row. A name collision against an
                   ACTIVE zone surfaces as a unique_violation on
                   idx_delivery_zones_name_active — caught and re-raised as a
                   clear ValueError (NOT swallowed, NOT a silent overwrite).
      update     : plain UPDATE of the already-resolved target row's
                   name / delivery_time_minutes (target id resolved first, so no
                   ON CONFLICT needed — we are not creating a row).
      deactivate : "deactivate"/"delete" both map to is_active = FALSE (SOFT
                   delete only). The retired row stays resolvable for any past
                   order's zone_id; the public GET /api/delivery-zones filters to
                   is_active = TRUE so it silently drops out of the offer set.

    TOCTOU RE-CHECK: validate_apply_zone_command ran earlier, in its own step,
    and must not be trusted here. A concurrent second agent invocation could
    interleave between validate and apply, so this step RE-VERIFIES the same
    invariants at write time. The create path's re-check is the DB index itself
    (a genuine second insert with the same normalized name races into the
    unique_violation catch below). update/deactivate re-resolve the target under
    a row lock (FOR UPDATE) so a concurrent retire/rename cannot change which
    row we touch.
    """
    parsed = _required_command_shape(command)
    if parsed is None:
        raise ValueError("apply_zone_command: malformed command")
    action = parsed["action"]

    # ----- create -----------------------------------------------------------
    if action == "create":
        assert parsed["name"] is not None
        assert isinstance(parsed["delivery_time_minutes"], int)
        # Re-check name-not-in-use AT WRITE TIME, but the real guard is the
        # partial unique index: if a concurrent apply inserted the same active
        # name between here and the INSERT, the INSERT raises unique_violation
        # and we convert it to a clear ValueError below (not an unexplained
        # asyncpg error). A plain INSERT (NOT ON CONFLICT DO UPDATE) is
        # deliberate: zone create must REJECT a collision, never overwrite a
        # different zone's row.
        dup = await session.execute(
            text(
                "SELECT id FROM delivery_zones "
                "WHERE lower(name) = lower(:name) AND is_active"
            ),
            {"name": parsed["name"]},
        )
        if dup.fetchall():
            logger.error(
                "apply_zone_command: active zone name '%s' already in use "
                "at write time",
                parsed["name"],
            )
            raise ValueError(
                f"zone name already in use by an active zone: {parsed['name']!r}"
            )
        try:
            result = await session.execute(
                text(
                    "INSERT INTO delivery_zones "
                    "(name, delivery_time_minutes, is_active) "
                    "VALUES (:name, :dtm, TRUE) "
                    "RETURNING id, name, delivery_time_minutes, is_active"
                ),
                {
                    "name": parsed["name"],
                    "dtm": parsed["delivery_time_minutes"],
                },
            )
        except IntegrityError as err:
            if _is_unique_violation(err):
                logger.error(
                    "apply_zone_command: unique_violation on create '%s' "
                    "(raced at write time)",
                    parsed["name"],
                )
                raise ValueError(
                    f"zone name already in use by an active zone: "
                    f"{parsed['name']!r}"
                ) from err
            raise
        row = result.one()._mapping
        logger.info(
            "apply_zone_command: created zone '%s' (id=%s)",
            parsed["name"], row["id"],
        )
        return {
            "applied": True,
            "action": "create",
            "id": str(row["id"]),
            "name": row["name"],
            "delivery_time_minutes": row["delivery_time_minutes"],
            "is_active": row["is_active"],
        }

    # ----- update / deactivate ----------------------------------------------
    assert parsed["target_zone_name"] is not None
    # Re-resolve the target AT WRITE TIME under a row lock, so a concurrent
    # retire/rename cannot change which row we touch between validate and apply.
    target = await session.execute(
        text(
            "SELECT id, name FROM delivery_zones "
            "WHERE lower(name) = lower(:name) AND is_active FOR UPDATE"
        ),
        {"name": parsed["target_zone_name"]},
    )
    matches = target.fetchall()
    if len(matches) != 1:
        logger.error(
            "apply_zone_command: target '%s' resolves to %d rows at write time "
            "(expected exactly 1 active zone)",
            parsed["target_zone_name"], len(matches),
        )
        raise ValueError(
            f"target zone not uniquely resolvable at write time: "
            f"{parsed['target_zone_name']!r} ({len(matches)} matches)"
        )
    zone_id = matches[0]._mapping["id"]

    if action == "update":
        # update may change name and/or delivery_time_minutes.
        new_name = parsed["name"] if parsed["name"] is not None else None
        # If renaming, re-check the new name does not collide with another
        # ACTIVE zone (excluding self), relying on the same partial index guard
        # via the unique_violation catch on UPDATE-conflict too.
        if new_name is not None:
            dup = await session.execute(
                text(
                    "SELECT id FROM delivery_zones "
                    "WHERE lower(name) = lower(:name) AND is_active "
                    "AND id <> :self"
                ),
                {"name": new_name, "self": zone_id},
            )
            if dup.fetchall():
                logger.error(
                    "apply_zone_command: rename to '%s' collides with another "
                    "active zone at write time",
                    new_name,
                )
                raise ValueError(
                    f"zone name already in use by an active zone: {new_name!r}"
                )
        try:
            result = await session.execute(
                text(
                    "UPDATE delivery_zones SET "
                    "name = COALESCE(:name, name), "
                    "delivery_time_minutes = COALESCE(:dtm, delivery_time_minutes) "
                    "WHERE id = :id "
                    "RETURNING id, name, delivery_time_minutes, is_active"
                ),
                {
                    "id": zone_id,
                    "name": new_name,
                    "dtm": parsed["delivery_time_minutes"],
                },
            )
        except IntegrityError as err:
            if _is_unique_violation(err):
                logger.error(
                    "apply_zone_command: unique_violation on update to '%s'",
                    new_name,
                )
                raise ValueError(
                    f"zone name already in use by an active zone: "
                    f"{new_name!r}"
                ) from err
            raise
        row = result.one()._mapping
        logger.info(
            "apply_zone_command: updated zone id=%s (now '%s')",
            zone_id, row["name"],
        )
        return {
            "applied": True,
            "action": "update",
            "id": str(row["id"]),
            "name": row["name"],
            "delivery_time_minutes": row["delivery_time_minutes"],
            "is_active": row["is_active"],
        }

    # action == deactivate (covers both "deactivate" and "delete" intents)
    assert action == "deactivate"
    await session.execute(
        text("UPDATE delivery_zones SET is_active = FALSE WHERE id = :id"),
        {"id": zone_id},
    )
    logger.info("apply_zone_command: deactivated zone id=%s", zone_id)
    return {
        "applied": True,
        "action": "deactivate",
        "id": str(zone_id),
        "is_active": False,
    }


async def report_invalid_zone_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the apply phase."""
    logger.warning("report_invalid_zone_command: %s", reason)
    return f"INVALID:{reason}"
