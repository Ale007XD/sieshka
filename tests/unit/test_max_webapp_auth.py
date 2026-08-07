"""tests/unit/test_max_webapp_auth.py — MAX WebAppData/initData validation.

Round-trip tests build a real valid WebAppData string using the exact
algorithm from https://dev.max.ru/docs/webapps/validation, then validate it —
this exercises the real HMAC chain both ways, not just against a hardcoded
fixture hash (which would only prove the test data matches itself, not that
the implementation matches the spec).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import quote

from app.services.max_webapp_auth import validate_init_data

_BOT_TOKEN = "test-bot-token-12345"


def _build_web_app_data(
    *,
    bot_token: str = _BOT_TOKEN,
    user_id: int = 67890,
    auth_date: int | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    """Builds a validly-signed WebAppData string per the doc's own algorithm
    (independent re-implementation here, deliberately not calling anything
    from app.services.max_webapp_auth, so this is a real cross-check)."""
    if auth_date is None:
        auth_date = int(time.time())

    user_json = json.dumps(
        {"id": user_id, "first_name": "Max", "last_name": "User", "username": None}
    )
    params = {
        "user": user_json,
        "auth_date": str(auth_date),
        "query_id": "4c0ab423-342b-4e45-aea4-2747dbc500cd",
    }
    if extra:
        params.update(extra)

    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()

    encoded_pairs = [f"{k}={quote(v, safe='')}" for k, v in params.items()]
    encoded_pairs.append(f"hash={signature}")
    return "&".join(encoded_pairs)


class TestValidInitData:
    def test_valid_data_returns_user_id(self) -> None:
        data = _build_web_app_data(user_id=67890)
        assert validate_init_data(data, _BOT_TOKEN) == 67890

    def test_different_user_id_round_trips(self) -> None:
        data = _build_web_app_data(user_id=111222)
        assert validate_init_data(data, _BOT_TOKEN) == 111222

    def test_extra_fields_do_not_break_validation(self) -> None:
        data = _build_web_app_data(
            extra={"ip": "192.168.0.1", "chat": json.dumps({"id": 12345, "type": "DIALOG"})}
        )
        assert validate_init_data(data, _BOT_TOKEN) == 67890


class TestTamperedOrMalformedInitData:
    def test_wrong_bot_token_rejected(self) -> None:
        data = _build_web_app_data()
        assert validate_init_data(data, "wrong-token") is None

    def test_tampered_user_id_rejected(self) -> None:
        data = _build_web_app_data(user_id=67890)
        tampered = data.replace("67890", "99999")
        assert validate_init_data(tampered, _BOT_TOKEN) is None

    def test_missing_hash_rejected(self) -> None:
        data = _build_web_app_data()
        without_hash = "&".join(p for p in data.split("&") if not p.startswith("hash="))
        assert validate_init_data(without_hash, _BOT_TOKEN) is None

    def test_duplicate_hash_rejected(self) -> None:
        data = _build_web_app_data()
        assert validate_init_data(data + "&hash=deadbeef", _BOT_TOKEN) is None

    def test_empty_string_rejected(self) -> None:
        assert validate_init_data("", _BOT_TOKEN) is None

    def test_no_bot_token_configured_rejected(self) -> None:
        data = _build_web_app_data()
        assert validate_init_data(data, "") is None

    def test_missing_user_field_rejected(self) -> None:
        auth_date = int(time.time())
        params = {"auth_date": str(auth_date)}
        launch_params = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
        signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
        data = f"auth_date={auth_date}&hash={signature}"
        assert validate_init_data(data, _BOT_TOKEN) is None

    def test_malformed_user_json_rejected(self) -> None:
        auth_date = int(time.time())
        params = {"user": "not-json{{", "auth_date": str(auth_date)}
        launch_params = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", _BOT_TOKEN.encode(), hashlib.sha256).digest()
        signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
        data = f"user=not-json%7B%7B&auth_date={auth_date}&hash={signature}"
        assert validate_init_data(data, _BOT_TOKEN) is None


class TestExpiry:
    def test_stale_auth_date_rejected(self) -> None:
        old = int(time.time()) - 90000  # > 86400s default max_age
        data = _build_web_app_data(auth_date=old)
        assert validate_init_data(data, _BOT_TOKEN) is None

    def test_fresh_auth_date_accepted(self) -> None:
        recent = int(time.time()) - 60
        data = _build_web_app_data(auth_date=recent)
        assert validate_init_data(data, _BOT_TOKEN) == 67890

    def test_custom_max_age_respected(self) -> None:
        five_min_ago = int(time.time()) - 300
        data = _build_web_app_data(auth_date=five_min_ago)
        assert validate_init_data(data, _BOT_TOKEN, max_age_seconds=60) is None
        assert validate_init_data(data, _BOT_TOKEN, max_age_seconds=600) == 67890
