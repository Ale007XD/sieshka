"""app/web/telegram_auth.py — Telegram Mini App per-request staff identity
resolution (sprint_telegram_miniapp_auth).

Unlike app/web/zalo_auth.py's get_current_zalo_staff() (which must make a
live call to Zalo's /me endpoint because Zalo's access_token is opaque),
Telegram Mini App initData is self-contained and offline-verifiable — the
exact same HMAC chain MAX uses (app/services/max_webapp_auth.py). So this
follows MAX's checkout.py pattern (pure computation, no round trip) for the
signature check, but wraps it as a FastAPI dependency shaped like
get_current_zalo_staff() (401 for a bad/missing signature, 403 for a real
Telegram user who isn't a registered staff member) since MAX's own staff
flow doesn't go through a Mini App webview at all (it uses bot inline
keyboards + callback_query, see app/webhooks/max.py) and so never needed
this dependency shape.

Header: X-Telegram-Init-Data — mirrors X-Max-Init-Data (checkout.py) /
X-Zalo-Access-Token (zalo_auth.py) naming convention for the equivalent
role: the client-supplied credential that must be verified before use.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.domains.staff.models import Staff
from app.services.max_webapp_auth import validate_init_data
from app.services.staff_service import StaffService

logger = logging.getLogger(__name__)


def get_staff_service() -> StaffService:
    return StaffService()


async def get_current_telegram_staff(
    request: Request,
    staff: StaffService = Depends(get_staff_service),
) -> Staff:
    """Resolves the calling staff member from a signature-validated
    Telegram Mini App initData string, or raises 401/403 — same status-code
    semantics as get_current_zalo_staff() (401: not a real/valid credential,
    403: a known identity without staff authorization).
    """
    web_app_data = request.headers.get("X-Telegram-Init-Data")
    if not web_app_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Telegram-Init-Data",
        )

    telegram_user_id = validate_init_data(web_app_data, settings.TELEGRAM_BOT_TOKEN)
    if telegram_user_id is None:
        logger.warning("Telegram Mini App auth: initData validation failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Telegram init data",
        )

    staff_member = await staff.find_by_telegram_user_id(telegram_user_id)
    if staff_member is None:
        logger.warning(
            "Telegram Mini App auth: unknown/inactive staff telegram_user_id=%s",
            telegram_user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a registered staff member",
        )

    return staff_member
