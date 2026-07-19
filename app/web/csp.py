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
        f"script-src 'nonce-{nonce}' 'self' {_YOOKASSA_ORIGIN}; "
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
