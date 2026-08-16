"""app/web/csp.py — per-request CSP nonce + Content-Security-Policy header.

Every customer template's <script> tag carries {{ csp_nonce }}. A fresh nonce
is generated per request and both injected into the template context (via the
``csp_nonce`` dependency) and written to a ``Content-Security-Policy`` response
header (via ``add_csp_header`` middleware) so the browser only executes
inline scripts bearing that exact nonce.

The policy explicitly allows:
  - 'self'            : the /static/ JS files (cart.js, menu.js)
  - https://yookassa.ru : cart.js dynamically injects the YooKassa widget
                          script from there; a strict CSP without this
                          exception would silently break the payment widget.
  - https://st.max.ru : shop_base.html loads the MAX Bridge SDK
                        (max-web-app.js, sprint_max_storefront) from here as
                        a bare <script src>, not nonce-tagged — a third-party
                        CDN script can't carry our per-request nonce, so it
                        needs its own origin exception, same shape as the
                        YooKassa one above.
  - https://telegram.org : shop_base.html AND telegram_staff.html load the
                        Telegram Mini App Bridge SDK (telegram-web-app.js,
                        sprint_telegram_miniapp_frontend) from here, same
                        bare-<script src> reasoning as the MAX entry above.
"""
from __future__ import annotations

import base64
import secrets
from collections.abc import MutableMapping
from typing import Any, cast

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

_CSP_NONCE_STATE_KEY = "csp_nonce"

# YooKassa injects its checkout widget script from this origin. Required for
# the embedded payment flow to load — flagged as an explicit allow because the
# default strict policy would otherwise block it.
_YOOKASSA_ORIGIN = "https://yookassa.ru"

# MAX Bridge SDK (https://dev.max.ru/docs/webapps/bridge) — populates
# window.WebApp for the mini-app storefront (sprint_max_storefront).
_MAX_BRIDGE_ORIGIN = "https://st.max.ru"

# Telegram Mini App Bridge SDK — populates window.Telegram.WebApp for both
# the storefront (shop_base.html) and the staff panel (telegram_staff.html),
# sprint_telegram_miniapp_frontend. Without this, the bridge <script src>
# tag is silently blocked by the browser (no console-visible error on the
# page itself) — window.Telegram simply never appears, initData is always
# null, and every Telegram-specific code path degrades to "not running
# inside Telegram" even when it actually is. Found during a pre-deploy
# config check (2026-08-16), not caught by any earlier gate: neither ruff/
# mypy/pytest exercise a real browser CSP enforcement, so this class of bug
# has no automated test coverage in this repo yet.
_TELEGRAM_BRIDGE_ORIGIN = "https://telegram.org"


def make_nonce() -> str:
    """Generate a cryptographically random, base64url-safe CSP nonce."""
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")


def csp_nonce(request: Request) -> str:
    """FastAPI dependency: return (and cache on the request) this request's nonce.

    The nonce is chosen by ``CSPMiddleware`` once per request (stored in
    ``scope["_csp_nonce"]``) so the template context and the response CSP
    header are byte-identical. Falls back to a fresh nonce only if the
    middleware is not installed.
    """
    nonce = getattr(request.state, _CSP_NONCE_STATE_KEY, None)
    if nonce is None:
        nonce = request.scope.get("_csp_nonce") or make_nonce()
        setattr(request.state, _CSP_NONCE_STATE_KEY, nonce)
    return nonce


def _build_csp_header(nonce: str) -> str:
    return (
        "default-src 'self'; "
        "img-src 'self' data:; "
        f"script-src 'nonce-{nonce}' 'self' {_YOOKASSA_ORIGIN} {_MAX_BRIDGE_ORIGIN} "
        f"{_TELEGRAM_BRIDGE_ORIGIN}; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "frame-src 'self' https://yookassa.ru; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


class CSPMiddleware:
    """Attach a per-request Content-Security-Policy header to HTML responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Wrap send to intercept the response headers for HTML responses.
        nonce = make_nonce()
        sent = {"started": False}

        async def _send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw_headers = cast("list[tuple[bytes, bytes]]", message["headers"])
                headers = MutableHeaders(raw=raw_headers)
                ctype = headers.get("content-type", "")
                if ctype.startswith("text/html"):
                    headers["Content-Security-Policy"] = _build_csp_header(nonce)
                    # MutableHeaders wraps a copy — push the updated list back
                    # into the outgoing message so Starlette actually sends it.
                    message["headers"] = headers.raw
                sent["started"] = True
            await send(message)

        # Expose the generated nonce to downstream request handling so the
        # template context matches the header. We cannot recover it from the
        # ASGI scope cheaply, so the dependency regenerates the same value by
        # reading request.state set during the route via csp_nonce(); but the
        # header nonce is authoritative and matches what we set below.
        scope["_csp_nonce"] = nonce
        await self.app(scope, receive, _send)
