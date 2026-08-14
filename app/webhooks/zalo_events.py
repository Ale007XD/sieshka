"""app/webhooks/zalo_events.py — Zalo Mini App Webhook URL (Open APIs).

sprint_zalo_app_events scope: exactly one event type. Official docs
(docs.zaloplatforms.com/docs/MA/openApis/open/webhook/) confirm the
self-developed Mini App Webhook URL fires only user.revoke.consent (user
revokes consent / requests data deletion). "App review status" events
(originally assumed in earlier draft plans, alongside this one) belong to a
*different* doc track — Zalo's partner/solution-integration APIs (agencies
managing multiple client Mini Apps), not a single self-developed Mini App
like this one. Not implemented here — would be dead code, no such event
will ever arrive at this deployment's webhook.

Also NOT this: Zalo OA (Official Account) chatbot events (user_send_text,
message_callback, user_follow) — a different Zalo product entirely. See
app/api/routes/zalo_miniapp.py's module docstring for the full correction
history (2026-08-13 doc-check session).

Signature (docs.zaloplatforms.com/.../verifysignature, verified against
official source, not the HMAC assumption in earlier draft plans — see
app/config.py's ZALO_API_KEY comment): plain SHA256, not HMAC —
    sha256(sorted_keys_concatenated_values + ZALO_API_KEY)
Top-level keys sorted alphabetically; a nested object value is
JSON.stringify'd (compact, no spaces) before concatenation — this endpoint's
payload has no nested objects, so that branch is exercised by no current
test, only documented for a future payload shape.

Body schema (confirmed):
    {"event": "user.revoke.consent", "appId": "...", "userId": "...",
     "timestamp": 1670553442564}

Action on a valid event: anonymize every trace of that Zalo user id in our
own data — the two columns actually introduced across sprint_zalo_*
(staff.zalo_user_id via StaffService.update() unlink, orders.client_zalo_uid
via the new OrderService.clear_client_zalo_uid()). This is deliberately
narrow and does NOT wire the pre-existing, unrelated CustomerDataFSM
(app/domains/privacy/) — that scaffold has no persistence layer, no
state_reader/state_writer, no callers anywhere in the codebase (a dormant
M1 gap that predates this sprint, out of scope to silently expand into
here).
"""
from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.order_service import OrderService
from app.services.staff_service import StaffService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_REVOKE_CONSENT_EVENT = "user.revoke.consent"


def get_staff_service() -> StaffService:
    return StaffService()


def get_order_service() -> OrderService:
    return OrderService()


def verify_zalo_signature(payload: dict[str, object], signature: str, api_key: str) -> bool:
    """sha256(sorted-keys content + api_key) — see module docstring."""
    keys = sorted(payload.keys())
    content = ""
    for k in keys:
        value = payload[k]
        content += json.dumps(value, separators=(",", ":")) if isinstance(value, dict) else str(
            value
        )
    expected = hashlib.sha256(f"{content}{api_key}".encode()).hexdigest()
    return expected == signature


@router.post("/zalo")
async def zalo_events_webhook(
    request: Request,
    staff: StaffService = Depends(get_staff_service),
    orders: OrderService = Depends(get_order_service),
) -> JSONResponse:
    signature = request.headers.get("X-ZEvent-Signature")

    try:
        payload: dict[str, object] = await request.json()
    except Exception:
        logger.warning("Zalo events webhook: invalid JSON body")
        return JSONResponse({"ok": True})  # never fail Zalo's delivery retry loop

    if not settings.ZALO_API_KEY or not signature or not verify_zalo_signature(
        payload, signature, settings.ZALO_API_KEY
    ):
        logger.warning("Zalo events webhook: invalid signature")
        return JSONResponse({"ok": False}, status_code=403)

    event = payload.get("event")
    if event != _REVOKE_CONSENT_EVENT:
        logger.info("Zalo events webhook: ignoring unhandled event=%r", event)
        return JSONResponse({"ok": True})

    zalo_user_id = payload.get("userId")
    if not zalo_user_id or not isinstance(zalo_user_id, str):
        logger.warning("Zalo events webhook: user.revoke.consent missing userId")
        return JSONResponse({"ok": True})

    staff_member = await staff.find_by_zalo_user_id(zalo_user_id)
    if staff_member is not None:
        await staff.update(staff_member.id, {"zalo_user_id": None})

    cleared_orders = await orders.clear_client_zalo_uid(zalo_user_id)

    logger.info(
        "Zalo events webhook: user.revoke.consent processed for userId=%s "
        "(staff_unlinked=%s, orders_cleared=%d)",
        zalo_user_id,
        staff_member is not None,
        cleared_orders,
    )
    return JSONResponse({"ok": True})
