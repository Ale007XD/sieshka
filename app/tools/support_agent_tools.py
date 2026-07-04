"""
app/tools/support_agent_tools.py — nano-vm Tools for SupportAgent programs.

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for validate_support_command return value
  - Agent tools MUST NOT modify support ticket state directly (table §4)
  - All tools governed via GovernedToolExecutor — never direct repo/PG calls
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def validate_support_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if LLM output contains valid structured command, 0 otherwise.

    Validates that the JSON contains required fields:
    support_ticket_id (str), customer_id (str), issue_type (str), description (str).
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_support_command: empty LLM output")
        return 0
    try:
        import json

        data = json.loads(llm_output)
        if not isinstance(data, dict):
            return 0
        if "support_ticket_id" not in data:
            logger.warning("validate_support_command: missing support_ticket_id")
            return 0
        if "customer_id" not in data:
            logger.warning("validate_support_command: missing customer_id")
            return 0
        if "issue_type" not in data or not isinstance(data["issue_type"], str):
            logger.warning("validate_support_command: missing/invalid issue_type")
            return 0
        if "description" not in data or not isinstance(data["description"], str):
            logger.warning("validate_support_command: missing/invalid description")
            return 0
        logger.info("validate_support_command: valid command")
        return 1
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_support_command: invalid JSON")
        return 0


async def collect_support_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command.

    This is the success terminal — the structured command passes through
    for downstream execution via GovernedToolExecutor.
    """
    logger.info("collect_support_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that support collection failed.

    reason is the numeric sentinel (0/1) converted to string by the
    CONDITION step branch that leads here.
    """
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"
