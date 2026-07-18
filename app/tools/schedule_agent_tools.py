"""
app/tools/schedule_agent_tools.py — nano-vm Tools for ScheduleAgent programs.

Two phases live here (see app/agents/README.md — "Agent apply-phase CONVENTION"):

COLLECT phase (NOT mutation):
  - validate_schedule_command / collect_schedule_command / report_collect_failure
  - stops at a terminal JSON command; writes NOTHING to Postgres.

APPLY phase (the ONLY phase allowed to write to ScheduleWindow):
  - validate_apply_schedule_command  [TOOL] numeric sentinel 0/1 for CONDITION
  - apply_schedule_command           [TOOL, is_terminal] the ONE write step
  - report_invalid_schedule_command  [TOOL, is_terminal] invalid-branch terminal

Command schema (structured output of the LLM, consumed by both phases):
  {
    "period": "morning" | "evening",
    "action": "set_hours" | "reset_to_default",
    "scope":  "today" | "permanent",
    "start_time": "HH:MM" | None,
    "end_time":   "HH:MM" | None
  }
  scope is extracted by the LLM: a temporal marker ("сегодня", "сейчас",
  "только сегодня") → "today"; its absence → "permanent". Ambiguous phrasing
  is NOT default-guessed either way — it is skipped as unparseable by
  validate_schedule_command (returns 0).

CONSTRAINTS (same discipline as menu_agent_tools.py):
  - Numeric sentinel returns (0/1) for CONDITION-consumed validators only.
  - apply_schedule_command is is_terminal with NO downstream CONDITION, so it
    MUST raise on any write failure — never return a sentinel nobody consumes.
  - session is a named first parameter, closure-injected via functools.partial.
  - No session.commit() inside any tool.
  - validate_* is an early-rejection convenience only; apply re-verifies at
    write time inside the same transaction.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_PERIODS = ("morning", "evening")
_VALID_ACTIONS = ("set_hours", "reset_to_default")
_VALID_SCOPES = ("today", "permanent")


def _parse_time(value: Any) -> datetime.time | None:
    """Parse a "HH:MM" string into a time, or None if not a valid time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.time.fromisoformat(value.strip())
    except ValueError:
        return None


def _required_command_shape(command: Any) -> dict[str, Any] | None:
    """Return the normalized command if structurally well-formed, else None.

    Shared by both validators so they agree on what a parseable command is.
    Does NOT judge business validity (scope×action combos, time ordering) —
    that is layered on by the caller.
    """
    if not isinstance(command, dict):
        return None
    period = command.get("period")
    action = command.get("action")
    scope = command.get("scope")
    if period not in _VALID_PERIODS:
        return None
    if action not in _VALID_ACTIONS:
        return None
    if scope not in _VALID_SCOPES:
        return None
    return {
        "period": period,
        "action": action,
        "scope": scope,
        "start_time": command.get("start_time"),
        "end_time": command.get("end_time"),
    }


# ---------------------------------------------------------------------------
# COLLECT phase (not mutation)
# ---------------------------------------------------------------------------


async def validate_schedule_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if the LLM output is a parseable schedule command, else 0.

    Ambiguous phrasing (no clear temporal marker AND no clear "always/from now
    on") yields no usable `scope`, so it is rejected as unparseable — we do NOT
    default-guess scope either way. That ambiguity lives in the `scope` field
    being absent or outside the known values, which this check rejects.
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_schedule_command: empty LLM output")
        return 0
    import json

    try:
        data = json.loads(llm_output)
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_schedule_command: invalid JSON")
        return 0

    parsed = _required_command_shape(data)
    if parsed is None:
        logger.warning("validate_schedule_command: malformed command shape")
        return 0
    if parsed["action"] == "set_hours":
        start = _parse_time(parsed["start_time"])
        end = _parse_time(parsed["end_time"])
        if start is None or end is None:
            logger.warning("validate_schedule_command: set_hours needs valid times")
            return 0
    logger.info("validate_schedule_command: parseable command")
    return 1


async def collect_schedule_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command."""
    logger.info("collect_schedule_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that schedule collection failed."""
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"


# ---------------------------------------------------------------------------
# APPLY phase (the ONLY phase allowed to write to ScheduleWindow)
# ---------------------------------------------------------------------------


async def validate_apply_schedule_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> int:
    """Early-rejection convenience for the apply phase. Numeric sentinel.

    Returns 1 only when the command is well-formed AND the action/scope combo
    is valid AND, for set_hours, the times parse and start < end. Returns 0
    otherwise. Not the enforcement point — apply_schedule_command re-checks.
    """
    parsed = _required_command_shape(command)
    if parsed is None:
        logger.warning("validate_apply_schedule_command: malformed command")
        return 0

    if parsed["action"] == "set_hours":
        start = _parse_time(parsed["start_time"])
        end = _parse_time(parsed["end_time"])
        if start is None or end is None:
            logger.warning("validate_apply_schedule_command: invalid times")
            return 0
        if start >= end:
            logger.warning("validate_apply_schedule_command: start_time >= end_time")
            return 0
    elif parsed["action"] == "reset_to_default":
        # reset_to_default is valid ONLY with scope="today". There is no
        # separately-stored "original config.py value" to revert a permanent
        # row to; the permanent row IS the source of truth. Reverting it would
        # need a named target the command does not carry, so it is rejected.
        if parsed["scope"] != "today":
            logger.warning(
                "validate_apply_schedule_command: reset_to_default requires "
                "scope=today"
            )
            return 0

    logger.info("validate_apply_schedule_command: valid at validate time")
    return 1


async def apply_schedule_command(
    session: AsyncSession,
    command: Any,
    **kwargs: object,
) -> dict[str, Any]:
    """Terminal tool: write the confirmed schedule command to ScheduleWindow.

    The ONLY step in the whole agent Program allowed to write to Postgres, via
    the SAME GovernedToolExecutor capability gate (schedule:write).

    MUST raise on any write failure (constraint violation / race). It is
    is_terminal with no downstream CONDITION reading its output, so a returned
    sentinel would leave Trace.status == SUCCESS regardless of what happened in
    Postgres.

    Behavior per scope:
      permanent + set_hours   : UPSERT row WHERE period=X AND effective_date IS NULL
      today     + set_hours   : UPSERT row WHERE period=X AND effective_date=CURRENT_DATE
      today     + reset_to_default : DELETE row WHERE period=X AND effective_date=CURRENT_DATE

    A unique_violation here means a genuine race (two concurrent today-override
    writes for the same period) — raise a clear ValueError, do NOT swallow it.
    """
    parsed = _required_command_shape(command)
    if parsed is None:
        raise ValueError("apply_schedule_command: malformed command")
    period = parsed["period"]
    action = parsed["action"]
    scope = parsed["scope"]

    # Re-verify the business rules at write time (TOCTOU): the earlier validate
    # result cannot be trusted across the validate→apply interleave.
    if action == "set_hours":
        start = _parse_time(parsed["start_time"])
        end = _parse_time(parsed["end_time"])
        if start is None or end is None:
            raise ValueError("apply_schedule_command: invalid times at write time")
        if start >= end:
            raise ValueError(
                "apply_schedule_command: start_time >= end_time at write time"
            )

        # UPSERT: a period may have at most one permanent row (partial unique
        # index uq_schedule_windows_permanent) and at most one dated row per day
        # (uq_schedule_windows_today_override). ON CONFLICT DO UPDATE keeps the
        # exactly-one-per-slice invariant without a separate existence check.
        # For today scope we bind CURRENT_DATE via a literal expression so the
        # conflict target (effective_date = CURRENT_DATE) matches the insert.
        insert_sql = (
            "INSERT INTO schedule_windows "
            "(period, start_time, end_time, is_active, effective_date) "
            "VALUES (:period, :start, :end, TRUE, :effective) "
            "ON CONFLICT (period) WHERE effective_date IS NULL "
            "DO UPDATE SET start_time = EXCLUDED.start_time, "
            "end_time = EXCLUDED.end_time, is_active = TRUE "
        )
        today_insert_sql = (
            "INSERT INTO schedule_windows "
            "(period, start_time, end_time, is_active, effective_date) "
            "VALUES (:period, :start, :end, TRUE, CURRENT_DATE) "
            "ON CONFLICT (period, effective_date) WHERE effective_date IS NOT NULL "
            "DO UPDATE SET start_time = EXCLUDED.start_time, "
            "end_time = EXCLUDED.end_time, is_active = TRUE "
        )
        if scope == "permanent":
            await session.execute(
                text(insert_sql),
                {
                    "period": period,
                    "start": start,
                    "end": end,
                    "effective": None,
                },
            )
        else:
            await session.execute(
                text(today_insert_sql),
                {"period": period, "start": start, "end": end},
            )
        logger.info(
            "apply_schedule_command: upserted %s window (scope=%s)",
            period,
            scope,
        )
        return {
            "applied": True,
            "period": period,
            "action": action,
            "scope": scope,
        }

    # action == reset_to_default, scope == today (validated above)
    assert action == "reset_to_default" and scope == "today"
    before = await session.execute(
        text(
            "SELECT COUNT(*) AS n FROM schedule_windows "
            "WHERE period = :period AND effective_date = CURRENT_DATE"
        ),
        {"period": period},
    )
    deleted = int(before.one()._mapping["n"])
    await session.execute(
        text(
            "DELETE FROM schedule_windows "
            "WHERE period = :period AND effective_date = CURRENT_DATE"
        ),
        {"period": period},
    )
    # Idempotent: deleting a row that isn't there is not an error (deleted == 0),
    # it just means there was nothing to reset.
    logger.info(
        "apply_schedule_command: reset %s override (deleted=%s)", period, deleted
    )
    return {
        "applied": True,
        "period": period,
        "action": action,
        "scope": scope,
        "deleted": deleted,
    }


async def report_invalid_schedule_command(reason: str, **kwargs: object) -> str:
    """Terminal tool: invalid-branch terminal for the apply phase."""
    logger.warning("report_invalid_schedule_command: %s", reason)
    return f"INVALID:{reason}"
