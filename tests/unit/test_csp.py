"""tests/unit/test_csp.py — Content-Security-Policy header builder."""
from __future__ import annotations

from app.web.csp import _build_csp_header, make_nonce


class TestBuildCspHeader:
    def test_contains_nonce(self) -> None:
        nonce = "abc123"
        csp = _build_csp_header(nonce)
        assert f"'nonce-{nonce}'" in csp

    def test_allows_self(self) -> None:
        csp = _build_csp_header("n")
        assert "'self'" in csp

    def test_allows_yookassa_script_origin(self) -> None:
        csp = _build_csp_header("n")
        assert "https://yookassa.ru" in csp

    def test_allows_max_bridge_script_origin(self) -> None:
        """sprint_max_storefront: shop_base.html loads max-web-app.js from
        st.max.ru as a bare (non-nonce) <script src> — needs its own origin
        exception, same as the pre-existing YooKassa one."""
        csp = _build_csp_header("n")
        assert "https://st.max.ru" in csp

    def test_max_bridge_origin_in_script_src_directive_specifically(self) -> None:
        csp = _build_csp_header("n")
        script_src_directive = next(
            part for part in csp.split(";") if part.strip().startswith("script-src")
        )
        assert "https://st.max.ru" in script_src_directive

    def test_frame_src_unaffected_by_max_bridge_addition(self) -> None:
        # MAX Bridge is a same-window script, not an iframe — must not leak
        # into frame-src.
        csp = _build_csp_header("n")
        frame_src_directive = next(
            part for part in csp.split(";") if part.strip().startswith("frame-src")
        )
        assert "st.max.ru" not in frame_src_directive


class TestMakeNonce:
    def test_returns_nonempty_string(self) -> None:
        assert isinstance(make_nonce(), str)
        assert len(make_nonce()) > 0

    def test_two_calls_differ(self) -> None:
        assert make_nonce() != make_nonce()
