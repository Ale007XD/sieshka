from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.auth import get_current_username
from app.web.routes import router as web_router

pytestmark: list[object] = []


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


@pytest.fixture
def app() -> FastAPI:
    _app = FastAPI()
    _app.state.templates = Jinja2Templates(directory=_templates_dir())
    _app.include_router(web_router, dependencies=[Depends(get_current_username)])
    return _app


class TestDashboardAuth:
    async def test_no_credentials_returns_401(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            assert resp.status_code == 401
            assert resp.headers["www-authenticate"] == "Basic"

    async def test_wrong_credentials_returns_401(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/ui/",
                auth=("wrong", "wrong-password"),
            )
            assert resp.status_code == 401
            assert resp.headers["www-authenticate"] == "Basic"

    async def test_correct_credentials_returns_200(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/ui/",
                auth=("admin", "test-password"),
            )
            assert resp.status_code == 200

    async def test_logger_not_called_with_password(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("app.web.auth.logger") as mock_logger:
                await client.get(
                    "/admin/ui/",
                    auth=("admin", "test-password"),
                )
                for call_args in mock_logger.call_args_list:
                    args_str = str(call_args)
                    assert "test-password" not in args_str
                    assert "Basic " not in args_str
