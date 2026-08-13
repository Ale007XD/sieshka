"""app/web/zalo_auth.py — Zalo Mini App per-request identity resolution.

Design choice vs MAX's initData (app/services/max_webapp_auth.py): MAX's
WebAppData is a self-contained, offline-verifiable HMAC signature (no round
trip to MAX's servers needed). Zalo Mini App's getAccessToken() returns an
opaque OAuth-style token with no offline verification path — the only way
to confirm it's real is to call Zalo's own /me endpoint (ZaloClient.
get_user_profile(), sprint_zalo_client). So unlike checkout.py's
validate_init_data() (pure computation), get_current_zalo_staff() below
makes a live external call on every request.

This mirrors the project's established "never trust an unverified client
claim" philosophy (see max_webapp_auth.py's own docstring on why
client_max_uid could not be trusted) rather than inventing a new session/
JWT subsystem — no such mechanism exists anywhere else in this codebase
(dashboard auth is HTTP Basic; MAX checkout re-validates per-request). The
tradeoff is explicit: every staff button-press pays one extra Zalo API
round trip. If this becomes a measured UX problem (not a hypothetical one),
a short-TTL cache of validated (access_token -> zalo_user_id) would be the
next step — not implemented now (YAGNI).

Header: X-Zalo-Access-Token — mirrors X-Max-Init-Data's naming convention
(checkout.py) for the equivalent role: the client-supplied credential that
must be verified before use.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status

from app.domains.staff.models import Staff
from app.services.staff_service import StaffService
from app.services.zalo_client import ZaloClient, ZaloProfileError, zalo_client

logger = logging.getLogger(__name__)


def get_staff_service() -> StaffService:
    return StaffService()


def get_zalo_client() -> ZaloClient:
    return zalo_client


async def get_current_zalo_staff(
    request: Request,
    staff: StaffService = Depends(get_staff_service),
    client: ZaloClient = Depends(get_zalo_client),
) -> Staff:
    """Resolves the calling staff member from a live-validated Zalo access
    token, or raises 401/403. Distinguishes "token doesn't check out with
    Zalo" (401 — not a real/valid credential) from "token is real but this
    Zalo user isn't a registered, active staff member" (403 — a known
    identity without the required authorization), matching the semantics
    HTTPException already gives callers elsewhere in this codebase (e.g.
    app/web/auth.py's dashboard 401 vs a hypothetical inactive-account 403).
    """
    token = request.headers.get("X-Zalo-Access-Token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Zalo-Access-Token",
        )

    try:
        profile = await client.get_user_profile(token)
    except ZaloProfileError as e:
        logger.warning("Zalo Mini App auth: token validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Zalo access token",
        ) from e

    zalo_user_id = profile.get("id")
    if not zalo_user_id:
        logger.warning("Zalo Mini App auth: profile response missing id")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Zalo access token",
        )

    staff_member = await staff.find_by_zalo_user_id(str(zalo_user_id))
    if staff_member is None:
        logger.warning(
            "Zalo Mini App auth: unknown/inactive staff zalo_user_id=%s", zalo_user_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a registered staff member",
        )

    return staff_member
