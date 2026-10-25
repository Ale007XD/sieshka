"""
app/webhooks/yookassa.py — YooKassa webhook handler.
Implements ADR-003: suspend/resume pattern. NO polling.

SAFETY RULES (ADR-003):
  - PAYMENT_CONFIRMED -> resume program
  - Already SUCCESS -> 200 duplicate (no re-execute)
  - RUNNING -> 200 "in progress" (no concurrent resume)
  - trace_id not found -> 200 suspicious (never 4xx payment providers)

security_fix_vuln02 (2026-10-25, audit P2): the notification body is
unauthenticated client input — before any state transition runs, the payment
is re-read from YooKassa's API (PaymentService.verify_provider_payment) and
the event must match the provider's own status plus the recorded amount.
Deliberate ADR-003 deviation: if YooKassa's API is temporarily unreachable we
return 502 (NOT 200) so YooKassa retries the notification later — silently
accepting an unverifiable event was the exact vulnerability class this fixes.
"""
from __future__ import annotations

import logging

import httpx
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

    if event_type not in ("payment.succeeded", "payment.canceled"):
        return JSONResponse({"ok": True})

    if not trace_id or not payment_id:
        logger.warning("YooKassa webhook: missing fields (trace_id or payment_id)")
        return JSONResponse({"ok": True})

    event = trace.get_by_trace_id(str(trace_id))
    if event is None:
        logger.warning("YooKassa webhook: trace_id=%s not found — suspicious", trace_id)
        return JSONResponse({"ok": True})

    logger.info(
        "YooKassa webhook: resuming program=%s event=%s trace_id=%s entity=%s",
        program_name,
        event_type,
        trace_id,
        event.entity_id,
    )

    # security_fix_vuln02 (audit P2): never act on the notification body
    # alone. Re-read the payment from YooKassa and require the provider's
    # own status + amount to match before any transition is attempted.
    expected_status = "succeeded" if event_type == "payment.succeeded" else "canceled"
    try:
        verified = await payment_service.verify_provider_payment(
            str(payment_id), expected_status
        )
    except httpx.HTTPError as exc:
        logger.error(
            "YooKassa webhook: provider API unavailable for payment %s (%s) "
            "— returning 502 so YooKassa retries (ADR-003 deviation, "
            "security_fix_vuln02)",
            payment_id, exc,
        )
        return JSONResponse(
            {"ok": False, "error": "provider verification unavailable"},
            status_code=502,
        )
    if not verified:
        logger.warning(
            "YooKassa webhook: provider verification FAILED for payment %s "
            "(event=%s) — notification does not match YooKassa API, "
            "no state change",
            payment_id, event_type,
        )
        return JSONResponse({"ok": True})

    if event_type == "payment.succeeded":
        result = await payment_service.confirm_payment(
            order_id=event.entity_id,
            provider_id=str(payment_id),
            trace_id=str(trace_id),
        )
        skip_log_prefix = "confirm_payment"
    else:
        result = await payment_service.fail_payment(
            order_id=event.entity_id,
            provider_id=str(payment_id),
            trace_id=str(trace_id),
        )
        skip_log_prefix = "fail_payment"

    if not result.success:
        logger.info(
            "YooKassa webhook: %s skipped (%s) — 200 returned",
            skip_log_prefix,
            result.reason,
        )

    return JSONResponse({"ok": True})