from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext

from app.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["argon2"])

security = HTTPBasic(auto_error=False)


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

    if credentials.username != settings.DASHBOARD_USER:
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
