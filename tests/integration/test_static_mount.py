"""tests/integration/test_static_mount.py — static files mount smoke test.

NOTE (2026-08-17): originally asserted on css/placeholder.css. dead_code_audit
(commit 107b6b5, 2026-08-16) correctly removed that file — real assets
(css/theme.css) had already landed via sprint_m7_static_assets_wiring — but
this test wasn't updated to match, so it's been asserting a 404-that-should-
be-a-404 as a failure ever since. Never caught: .github/workflows/ci.yml's
`test` job runs tests/unit/ only, no Postgres service, so tests/integration/
never executed on CI at all. Fixed to check the real, current asset.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app


class TestStaticMount:
    async def test_static_theme_css_served_200(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/static/css/theme.css")
            assert resp.status_code == 200
            assert "Sieshka" in resp.text

    async def test_static_mount_404s_for_missing_files_not_500(self) -> None:
        """No index.html exists under app/web/static/ (it's an icon/css/js
        asset folder, not a browsable directory) — StaticFiles(html=True)
        correctly 404s on the bare '/static/' root, so a 200 there was never
        a valid assertion (this replaces the original
        test_static_dir_trailing_slash_200, which asserted exactly that and
        had never actually passed). Guards the real, verified behavior: a
        missing file under the mount 404s cleanly rather than 500ing."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/static/does-not-exist.txt")
            assert resp.status_code == 404
