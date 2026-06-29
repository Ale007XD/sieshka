"""
app/webhooks/yookassa.py — YooKassa webhook handler.
Implements ADR-003: suspend/resume pattern. NO polling.

SAFETY RULES (ADR-003):
  - PAYMENT_CONFIRMED -> resume program
  - Already SUCCESS -> 200 duplicate (no re-execute)
  - RUNNING -> 200 "in progress" (no concurrent resume)
  - trace_id not found -> 200 suspicious (never 4xx payment providers)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.services.payment_service import PaymentService
from app.trace import trace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_payment_service() -> PaymentService:
    return PaymentService()


@router.post("/yookassa")
async def yookassa_webhook(
    request: Request,
    payment_service: PaymentService = Depends(get_payment_service),
) -> JSONResponse:
    try:
        body: dict[str, object] = await request.json()
    except Exception:
        return JSONResponse({"ok": True})  # never 4xx to payment providers

    event_type = body.get("event", "")
    obj = body.get("object", {})
    if not isinstance(obj, dict):
        return JSONResponse({"ok": True})

    event_id = body.get("id", "")
    if not isinstance(event_id, str):
        event_id = ""

    metadata = obj.get("metadata", {})
    if not isinstance(metadata, dict):
        return JSONResponse({"ok": True})

    trace_id = metadata.get("trace_id", "")
    program_name = metadata.get("program_name", "payment_confirmation")
    payment_id = obj.get("id", "")

    logger.info(
        "YooKassa webhook: event=%s event_id=%s trace_id=%s",
        event_type, event_id, trace_id,
    )

    if event_type != "payment.succeeded":
        return JSONResponse({"ok": True})

    if not trace_id or not payment_id or not event_id:
        logger.warning("YooKassa webhook: missing fields (trace_id, payment_id, or event_id)")
        return JSONResponse({"ok": True})

    event = trace.get_by_trace_id(str(trace_id))
    if event is None:
        logger.warning("YooKassa webhook: trace_id=%s not found — suspicious", trace_id)
        return JSONResponse({"ok": True})

    logger.info(
        "YooKassa webhook: resuming program=%s trace_id=%s entity=%s",
        program_name,
        trace_id,
        event.entity_id,
    )

    result = await payment_service.confirm_payment(
        order_id=event.entity_id,
        provider_id=str(payment_id),
        event_id=str(event_id),
    )

    if not result.success:
        logger.info(
            "YooKassa webhook: confirm_payment skipped (%s) — 200 returned",
            result.reason,
        )

    return JSONResponse({"ok": True})
