"""
app/webhooks/yookassa.py — YooKassa webhook handler.
Implements ADR-003: suspend/resume pattern. NO polling.

SAFETY RULES (ADR-003):
  - PAYMENT_CONFIRMED → resume program
  - Already SUCCESS → 200 duplicate (no re-execute)
  - RUNNING → 200 "in progress" (no concurrent resume)
  - trace_id not found → 200 suspicious (never 4xx payment providers)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.trace import trace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/yookassa")
async def yookassa_webhook(request: Request) -> JSONResponse:
    try:
        body: dict[str, object] = await request.json()
    except Exception:
        return JSONResponse({"ok": True})  # never 4xx to payment providers

    event_type = body.get("event", "")
    obj = body.get("object", {})
    if not isinstance(obj, dict):
        return JSONResponse({"ok": True})

    metadata = obj.get("metadata", {})
    if not isinstance(metadata, dict):
        return JSONResponse({"ok": True})

    trace_id = metadata.get("trace_id", "")
    program_name = metadata.get("program_name", "payment_confirmation")

    logger.info("YooKassa webhook: event=%s trace_id=%s", event_type, trace_id)

    if event_type != "payment.succeeded":
        return JSONResponse({"ok": True})

    if not trace_id:
        logger.warning("YooKassa webhook: missing trace_id in metadata")
        return JSONResponse({"ok": True})

    event = trace.get_by_trace_id(str(trace_id))
    if event is None:
        logger.warning("YooKassa webhook: trace_id=%s not found — suspicious", trace_id)
        return JSONResponse({"ok": True})

    # M3: replace with vm.resume_with_program(program=..., trace_id=trace_id, ...)
    # M1/M2: record confirmation in trace and trigger order FSM
    logger.info(
        "YooKassa webhook: resuming program=%s trace_id=%s entity=%s",
        program_name,
        trace_id,
        event.entity_id,
    )

    # TODO M2: call Application Service to confirm payment and trigger OrderFSM
    # order_service.confirm_payment(order_id=event.entity_id, trace_id=trace_id)

    return JSONResponse({"ok": True})
