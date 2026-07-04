"""
app/tools/order_agent_tools.py — nano-vm Tools for OrderAgent programs.

CONSTRAINTS:
  - Numeric sentinel returns (0/1) for validate_order_command return value
  - Agent tools MUST NOT modify order state directly (table §4)
  - All tools governed via GovernedToolExecutor — never direct repo/PG calls
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def validate_order_command(llm_output: str, **kwargs: object) -> int:
    """Returns 1 if LLM output contains valid structured command, 0 otherwise.

    Validates that the JSON contains required fields:
    customer_id (str), items (list of dict), delivery_address (str).
    """
    if not llm_output or not llm_output.strip():
        logger.warning("validate_order_command: empty LLM output")
        return 0
    try:
        data = json.loads(llm_output)
        if not isinstance(data, dict):
            return 0
        if "customer_id" not in data:
            logger.warning("validate_order_command: missing customer_id")
            return 0
        if "items" not in data or not isinstance(data["items"], list):
            logger.warning("validate_order_command: missing/invalid items")
            return 0
        if "delivery_address" not in data or not isinstance(data["delivery_address"], str):
            logger.warning("validate_order_command: missing/invalid delivery_address")
            return 0
        logger.info("validate_order_command: valid command")
        return 1
    except (json.JSONDecodeError, ValueError):
        logger.warning("validate_order_command: invalid JSON")
        return 0


async def collect_order_command(command: str, **kwargs: object) -> str:
    """Terminal tool: confirms and returns the structured command.

    This is the success terminal — the structured command passes through
    for downstream execution via GovernedToolExecutor.
    """
    logger.info("collect_order_command: command collected")
    return command


async def report_collect_failure(reason: str, **kwargs: object) -> str:
    """Terminal tool: reports that order collection failed.

    reason is the numeric sentinel (0/1) converted to string by the
    CONDITION step branch that leads here.
    """
    logger.warning("report_collect_failure: %s", reason)
    return f"FAILED:{reason}"
