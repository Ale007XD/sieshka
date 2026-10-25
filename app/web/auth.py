from __future__ import annotations

import hmac
import logging
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext

from app.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["argon2"])

security = HTTPBasic(auto_error=False)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def require_same_origin(request: Request) -> None:
    """security_fix_vuln06 (2026-10-25, audit P5): CSRF guard for the
    Basic-auth browser surfaces (/admin/ui, /admin).

    HTTP Basic credentials are attached by the browser automatically —
    including on cross-site form/fetch POSTs — so Basic auth alone provides
    zero CSRF protection. For mutating methods, when a browser supplies an
    Origin header it must name this same host; a mismatched (or "null")
    origin is rejected. Requests without Origin (curl, server-to-server,
    existing tests) pass through — Basic auth still applies to them.
    """
    if request.method not in _MUTATING_METHODS:
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    origin_netloc = urlparse(origin).netloc
    host = request.headers.get("host", "")
    if not origin_netloc or not host or origin_netloc != host:
        logger.warning(
            "CSRF guard: rejected %s %s with Origin=%r (host=%r)",
            request.method, request.url.path, origin, host,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin request rejected",
        )


async def get_current_username(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if credentials is None:
        logger.warning("Dashboard auth: no credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )

    # security_fix (audit P9): constant-time username comparison (compare_digest
    # needs bytes to be safe for arbitrary non-ASCII input).
    if not hmac.compare_digest(
        credentials.username.encode("utf-8"), settings.DASHBOARD_USER.encode("utf-8")
    ):
        logger.warning("Dashboard auth: invalid username attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    if not settings.DASHBOARD_PASSWORD_HASH:
        logger.warning("Dashboard auth: no password hash configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    if not pwd_context.verify(credentials.password, settings.DASHBOARD_PASSWORD_HASH):
        logger.warning("Dashboard auth: invalid password attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username
