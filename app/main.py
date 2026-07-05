"""app/main.py — FastAPI application."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.api.routes.admin import router as admin_router
from app.api.routes.delivery import router as delivery_router
from app.api.routes.kitchen import router as kitchen_router
from app.api.routes.orders import router as orders_router
from app.config import settings
from app.startup import validate_all_programs
from app.telemetry import configure_otel
from app.web.auth import get_current_username
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
app.include_router(admin_router, dependencies=[Depends(get_current_username)])
app.include_router(kitchen_router)
app.include_router(delivery_router)
app.include_router(web_router, dependencies=[Depends(get_current_username)])
app.include_router(yookassa_router)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.0"})


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({
        "service": "Sieshka",
        "architecture": "nano-vm governed FSM",
        "milestones": {
            "M1": "Foundation — CURRENT",
            "M2": "Business Operations",
            "M3": "nano-vm Integration",
            "M4": "AI Layer",
            "M5": "Observability",
        },
    })
