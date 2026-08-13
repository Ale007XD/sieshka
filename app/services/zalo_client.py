"""app/services/zalo_client.py — raw Zalo Open API transport client.

sprint_zalo_client scope: transport primitives only (server-to-server profile
lookup) — no staff ACL, no OrderEvent/KitchenEvent business logic, no OA
messaging. That logic is channel/role-specific and belongs to
sprint_zalo_webhook_adapter and sprint_zalo_staff_notify, both of which will
import this client rather than duplicating HTTP calls — same layering as
MaxClient (app/services/max_client.py).

ZALO_APP_ID vs MINI_APP_ID: the app-level identifier (used here, for
appsecret_proof + Graph API calls) is distinct from the Mini-App-level
identifier (used only by the Mini App frontend / zmp CLI, never by this
server-side client) — per Zalo's own docs warning not to confuse them.

appsecret_proof: required by Zalo's Graph API (graph.zalo.me) for
server-side calls since 2024-01-01 — HMAC-SHA256(secret_key, access_token).
Mirrors Facebook Graph API's identical mechanism.

IP / outbound proxy: Zalo OpenAPI has, since 2024-02-29, withheld certain
user-data-related response fields (not the whole API) from non-Vietnam
App/Webhook IPs (see DECISIONS.md sprint_zalo_client, 2026-08-11 doc-check
session — this replaces the unverified "requires VN IP" claim from earlier
draft plans). settings.ZALO_API_PROXY_URL is None-safe: empty string (the
default) means "call directly, no proxy" — set only once/if a specific
field is empirically confirmed missing from the current host's IP. This is
config, not client logic — do not hardcode a proxy here (same pattern as
SSL_CERT_FILE for GigaChat/MAX, Dockerfile 2026-07-27).

TLS: no separate cert bootstrap needed — graph.zalo.me/openapi.mini.zalo.me
chain to public CAs, unlike MAX's Mintsifry Russian Trusted Root CA case.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class ZaloProfileError(Exception):
    """get_user_profile failed — invalid/expired access_token, network
    error, or malformed response. Callers (sprint_zalo_storefront_auth's
    /api/auth/zalo) turn this into a clean 401/502, not a raw 500."""


class ZaloClient:
    """Raw Zalo Open API client via httpx (server-to-server calls only)."""

    def __init__(
        self,
        app_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._app_id = app_id if app_id is not None else settings.ZALO_APP_ID
        self._secret_key = (
            secret_key if secret_key is not None else settings.ZALO_APP_SECRET_KEY
        )
        self._base_url = base_url or settings.ZALO_API_BASE_URL
        proxy = proxy_url if proxy_url is not None else settings.ZALO_API_PROXY_URL
        self._proxy = proxy or None  # "" (default) -> None, no proxy

    @property
    def configured(self) -> bool:
        return bool(self._app_id) and bool(self._secret_key)

    def _appsecret_proof(self, access_token: str) -> str:
        """HMAC-SHA256(secret_key, access_token) — required by graph.zalo.me
        for every server-side call using a user access_token."""
        return hmac.new(
            self._secret_key.encode(),
            access_token.encode(),
            hashlib.sha256,
        ).hexdigest()

    async def get_user_profile(self, access_token: str) -> dict[str, Any]:
        """GET /me — resolve a Mini App user's profile from their
        access_token. Raises ZaloProfileError on any failure; callers decide
        how to surface that (this client never returns a partial/guessed
        profile)."""
        if not self.configured:
            raise ZaloProfileError("ZALO_APP_ID/ZALO_APP_SECRET_KEY not configured")

        proof = self._appsecret_proof(access_token)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, proxy=self._proxy) as client:
                resp = await client.get(
                    f"{self._base_url}/me",
                    headers={
                        "access_token": access_token,
                        "appsecret_proof": proof,
                    },
                    params={"fields": "id,name,picture"},
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Zalo /me HTTP error: %s", e.response.text)
            raise ZaloProfileError(f"Zalo /me HTTP {e.response.status_code}") from e
        except ValueError as e:
            logger.error("Zalo /me JSON parse error: %s", e)
            raise ZaloProfileError("Zalo /me returned non-JSON response") from e
        except httpx.RequestError as e:
            logger.error("Zalo /me request error: %s", e)
            raise ZaloProfileError("Zalo /me request failed") from e

        if "error" in data or "id" not in data:
            logger.error("Zalo /me error payload: %s", data)
            raise ZaloProfileError(f"Zalo /me error response: {data}")

        return data


zalo_client = ZaloClient()
