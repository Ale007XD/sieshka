"""app/services/max_webapp_auth.py — MAX Mini App initData (WebAppData) validation.

Implements https://dev.max.ru/docs/webapps/validation exactly (identical
construction to Telegram Mini Apps' initData validation, confirmed against
the official Habr writeup of the same doc — both independently describe the
same HMAC chain):
    secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
    signature  = hex(HMAC_SHA256(key=secret_key, msg=launch_params))
launch_params is every WebAppData sub-param except hash, URL-decoded, sorted
by key, joined as "key=value" with "\n" (0x0A).

sprint_max_storefront: before this module, checkout.py accepted a client-
submitted `client_max_uid` integer in the request body completely
unverified — migrations/010_checkout_columns.sql's own comment says
"persisted only", i.e. it was never something to trust, only to display back.
Any client could claim to be any MAX user id. This module is what lets the
checkout endpoint upgrade that field from "an unverified client claim" to
"a value MAX itself signed this request" — see app/api/routes/checkout.py's
use of validate_init_data() for the actual trust boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_SECRET_KEY_SEED = b"WebAppData"


def _compute_secret_key(bot_token: str) -> bytes:
    return hmac.new(_SECRET_KEY_SEED, bot_token.encode("utf-8"), hashlib.sha256).digest()


def validate_init_data(
    web_app_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> int | None:
    """Validates a raw MAX WebAppData string, returns the verified MAX user
    id on success, None otherwise (missing/duplicate hash, bad signature,
    stale auth_date, or no bot token configured — every failure mode logs a
    reason and returns None rather than raising, since a failed verification
    is an ordinary "don't trust this" outcome for a checkout flow, not an
    exceptional one).

    web_app_data: the raw WebAppData value exactly as the client received it
    (key=value pairs joined by "&", each value URL-encoded) — e.g. what a
    client forwards as-is from window.WebApp.initData. Parsing it here,
    rather than accepting a pre-parsed dict, is what lets this function
    enforce "hash appears exactly once" per the spec (step 3 of the doc's
    algorithm) instead of trusting a caller's own parsing.
    """
    if not bot_token:
        logger.warning("validate_init_data: MAX_BOT_TOKEN not configured")
        return None
    if not web_app_data:
        return None

    pairs = [p.split("=", 1) for p in web_app_data.split("&") if "=" in p]
    hashes = [v for k, v in pairs if k == "hash"]
    if len(hashes) != 1:
        logger.warning("validate_init_data: hash missing or duplicated")
        return None
    original_hash = hashes[0]

    decoded = sorted(((k, unquote(v)) for k, v in pairs if k != "hash"), key=lambda kv: kv[0])
    launch_params = "\n".join(f"{k}={v}" for k, v in decoded)

    secret_key = _compute_secret_key(bot_token)
    computed_hash = hmac.new(
        secret_key, launch_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, original_hash):
        logger.warning("validate_init_data: signature mismatch")
        return None

    values = dict(decoded)

    auth_date_raw = values.get("auth_date")
    if auth_date_raw is not None:
        try:
            auth_date = int(auth_date_raw)
        except ValueError:
            logger.warning("validate_init_data: malformed auth_date=%r", auth_date_raw)
            return None
        if time.time() - auth_date > max_age_seconds:
            logger.warning("validate_init_data: expired auth_date=%s", auth_date)
            return None

    user_raw = values.get("user")
    if not user_raw:
        logger.warning("validate_init_data: no user field in WebAppData")
        return None
    try:
        user_obj = json.loads(user_raw)
        user_id = int(user_obj["id"])
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("validate_init_data: malformed user field: %s", e)
        return None

    return user_id
