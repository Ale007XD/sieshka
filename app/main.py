"""app/main.py — FastAPI application."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.routes.admin import router as admin_router
from app.api.routes.checkout import router as checkout_router
from app.api.routes.delivery import router as delivery_router
from app.api.routes.kitchen import router as kitchen_router
from app.api.routes.menu import router as menu_router
from app.api.routes.orders import router as orders_router
from app.config import settings
from app.startup import validate_all_programs
from app.telemetry import configure_otel
from app.web.auth import get_current_username
from app.web.csp import CSPMiddleware
from app.web.customer_routes import router as customer_router
from app.web.routes import router as web_router
from app.webhooks.yookassa import router as yookassa_router

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_otel()
    validate_all_programs()
    yield


app = FastAPI(
    title="Sieshka Food Delivery",
    description="nano-vm governed food delivery platform",
    version="0.1.0",
    lifespan=lifespan,
)

templates_dir = Path(__file__).resolve().parent / "web" / "templates"
app.state.templates = Jinja2Templates(directory=str(templates_dir))

app.include_router(orders_router)
app.include_router(checkout_router)
app.include_router(admin_router, dependencies=[Depends(get_current_username)])
app.include_router(kitchen_router)
app.include_router(delivery_router)
app.include_router(menu_router)
app.include_router(web_router, dependencies=[Depends(get_current_username)])
app.include_router(customer_router)
app.include_router(yookassa_router)

static_dir = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")


class NoIndexMiddleware:
    """Attach `X-Robots-Tag: noindex, nofollow` to every response.

    Staging/sibling domain guard (sprint_m7_staging_deploy): siesh-ka.online
    serves the same catalog as the live site and must never be indexed. nginx
    also sets this header at the proxy layer (deploy/nginx-siesh-ka-online.conf),
    but enforcing it here too means the guard survives even if the proxy header
    is ever dropped. Applied to ALL responses (HTML, JSON, static) so nothing
    leaks into search engines.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=cast("list[tuple[bytes, bytes]]", message["headers"]))
                headers["X-Robots-Tag"] = "noindex, nofollow"
                message["headers"] = headers.raw
            await send(message)

        await self.app(scope, receive, _send)

app.add_middleware(NoIndexMiddleware)
app.add_middleware(CSPMiddleware)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.0"})


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse({
        "service": "Sieshka",
        "architecture": "nano-vm governed FSM",
        "milestones": {
            "M1": "Foundation — DONE",
            "M2": "Business Operations — DONE",
            "M3": "nano-vm Integration — DONE",
            "M4": "AI Layer — DONE",
            "M5": "Observability — DONE",
            "M6": "Restaurateur Dashboard — DONE",
            "M7": "Customer Storefront — DONE",
        },
        "dashboard": "/admin/ui/ (auth required)",
        "store": "/",
    })
