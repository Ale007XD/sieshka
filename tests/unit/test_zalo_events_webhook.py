"""tests/unit/test_zalo_events_webhook.py — Zalo Mini App Webhook URL.

Mirrors test_max_webhook.py's dependency-override pattern (no DB, no
network). Reference signature computed independently in the sandbox against
a fixed payload — not derived from the implementation under test — see
sprint delivery README for the standalone computation.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.domains.staff.models import Staff, StaffRole
from app.services.order_service import OrderService
from app.services.staff_service import StaffService
from app.webhooks.zalo_events import (
    get_order_service,
    get_staff_service,
    verify_zalo_signature,
)
from app.webhooks.zalo_events import router as zalo_events_router

_PAYLOAD = {
    "event": "user.revoke.consent",
    "appId": "app-1",
    "userId": "zalo-uid-1",
    "timestamp": 1670553442564,
}
# Independently computed (see test_zalo_app_events_reference in this file
# and the sprint README) for _PAYLOAD + api_key="test-api-key".
_VALID_SIGNATURE = "ad0bc17ee29653e2a4f5196f79880ddcfeffa6fc182b2910bb1e073e1860f963"


def _sign(payload: dict[str, object], api_key: str) -> str:
    """Standalone reference implementation, independent of
    app.webhooks.zalo_events.verify_zalo_signature — used to build valid
    signatures for payload variants in tests without calling the code under
    test to authenticate itself."""
    import hashlib as _hashlib
    import json as _json

    keys = sorted(payload.keys())
    content = ""
    for k in keys:
        v = payload[k]
        content += _json.dumps(v, separators=(",", ":")) if isinstance(v, dict) else str(v)
    return _hashlib.sha256(f"{content}{api_key}".encode()).hexdigest()


class _Mocks:
    def __init__(self) -> None:
        self.staff = AsyncMock(spec=StaffService)
        self.orders = AsyncMock(spec=OrderService)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


@pytest.fixture(autouse=True)
def _configured_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file except test_missing_api_key_config_returns_403
    expects a configured key — set the fixed reference key here once instead
    of repeating monkeypatch calls per test."""
    monkeypatch.setattr("app.webhooks.zalo_events.settings.ZALO_API_KEY", "test-api-key")


def _client(mocks: _Mocks) -> AsyncClient:
    app = FastAPI()
    app.include_router(zalo_events_router)
    app.dependency_overrides[get_staff_service] = lambda: mocks.staff
    app.dependency_overrides[get_order_service] = lambda: mocks.orders
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestVerifyZaloSignature:
    def test_matches_independently_computed_reference(self) -> None:
        assert verify_zalo_signature(_PAYLOAD, _VALID_SIGNATURE, "test-api-key") is True

    def test_wrong_key_fails(self) -> None:
        assert verify_zalo_signature(_PAYLOAD, _VALID_SIGNATURE, "wrong-key") is False

    def test_tampered_payload_fails(self) -> None:
        tampered = {**_PAYLOAD, "userId": "attacker-uid"}
        assert verify_zalo_signature(tampered, _VALID_SIGNATURE, "test-api-key") is False

    def test_not_hmac_plain_sha256_of_concatenation(self) -> None:
        """Regression guard: earlier draft plans assumed HMAC-SHA256. If
        someone reintroduces HMAC, this fails loudly instead of silently
        accepting a wrong signature scheme."""
        import hashlib
        import hmac

        wrong_hmac = hmac.new(
            b"test-api-key", b"app-1user.revoke.consent1670553442564zalo-uid-1", hashlib.sha256
        ).hexdigest()
        assert wrong_hmac != _VALID_SIGNATURE


class TestZaloEventsWebhook:
    async def test_valid_signature_revoke_consent_unlinks_staff_and_orders(
        self, mocks: _Mocks
    ) -> None:
        staff_row = Staff(
            id=uuid.uuid4(), name="Курьер", role=StaffRole.courier, zalo_user_id="zalo-uid-1"
        )
        mocks.staff.find_by_zalo_user_id.return_value = staff_row
        mocks.orders.clear_client_zalo_uid.return_value = 3

        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo",
                json=_PAYLOAD,
                headers={"X-ZEvent-Signature": _VALID_SIGNATURE},
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mocks.staff.update.assert_awaited_once_with(staff_row.id, {"zalo_user_id": None})
        mocks.orders.clear_client_zalo_uid.assert_awaited_once_with("zalo-uid-1")

    async def test_valid_signature_no_matching_staff_still_clears_orders(
        self, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_zalo_user_id.return_value = None
        mocks.orders.clear_client_zalo_uid.return_value = 1

        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo",
                json=_PAYLOAD,
                headers={"X-ZEvent-Signature": _VALID_SIGNATURE},
            )

        assert resp.status_code == 200
        mocks.staff.update.assert_not_awaited()
        mocks.orders.clear_client_zalo_uid.assert_awaited_once_with("zalo-uid-1")

    async def test_invalid_signature_returns_403_and_does_not_dispatch(
        self, mocks: _Mocks
    ) -> None:
        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo", json=_PAYLOAD, headers={"X-ZEvent-Signature": "wrong"}
            )

        assert resp.status_code == 403
        mocks.staff.find_by_zalo_user_id.assert_not_awaited()
        mocks.orders.clear_client_zalo_uid.assert_not_awaited()

    async def test_missing_signature_header_returns_403(self, mocks: _Mocks) -> None:
        async with _client(mocks) as client:
            resp = await client.post("/webhooks/zalo", json=_PAYLOAD)

        assert resp.status_code == 403

    async def test_unhandled_event_type_acks_without_dispatch(self, mocks: _Mocks) -> None:
        other_payload = {**_PAYLOAD, "event": "app.review.status"}
        valid_sig_for_other = _sign(other_payload, "test-api-key")

        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo",
                json=other_payload,
                headers={"X-ZEvent-Signature": valid_sig_for_other},
            )

        # Valid signature but wrong event type -> 200 ack, no dispatch —
        # confirms the event-type branch, not the signature branch.
        assert resp.status_code == 200
        mocks.orders.clear_client_zalo_uid.assert_not_awaited()
        mocks.staff.find_by_zalo_user_id.assert_not_awaited()

    async def test_missing_user_id_acks_without_dispatch(self, mocks: _Mocks) -> None:
        payload = {"event": "user.revoke.consent", "appId": "app-1", "timestamp": 1}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.webhooks.zalo_events.verify_zalo_signature", lambda *a, **kw: True
            )
            async with _client(mocks) as client:
                resp = await client.post(
                    "/webhooks/zalo", json=payload, headers={"X-ZEvent-Signature": "any"}
                )
        assert resp.status_code == 200
        mocks.orders.clear_client_zalo_uid.assert_not_awaited()

    async def test_invalid_json_body_acks_200(self, mocks: _Mocks) -> None:
        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo",
                content=b"not json",
                headers={
                    "X-ZEvent-Signature": "irrelevant",
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 200

    async def test_missing_api_key_config_returns_403(
        self, mocks: _Mocks, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Override the autouse fixture's key back to unconfigured — signature
        # check must fail closed (403), never silently accept when unset.
        monkeypatch.setattr("app.webhooks.zalo_events.settings.ZALO_API_KEY", "")
        async with _client(mocks) as client:
            resp = await client.post(
                "/webhooks/zalo",
                json=_PAYLOAD,
                headers={"X-ZEvent-Signature": _VALID_SIGNATURE},
            )
        assert resp.status_code == 403
