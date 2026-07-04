"""
app/tools/menu_agent_tools.py — nano-vm Tools for MenuAgent programs.

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for validate_menu_command return value
  - Agent tools MUST NOT modify menu state directly (table §4)
  - All tools governed via GovernedToolExecutor — never direct repo/PG calls
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def validate_menu_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if LLM output contains valid structured command, 0 otherwise.

    Validates that the JSON contains required fields:
    menu_id (str), items (list of dict), category (str).
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_menu_command: empty LLM output")
        return 0
    try:
        import json

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
