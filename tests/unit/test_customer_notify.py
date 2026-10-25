"""tests/unit/test_customer_notify.py — customer-facing order-status push
(sprint_customer_notify, 2026-10-25).

Covers the notify module's contract (fire-and-forget, never raises, no-ops
without attribution/channels) and the staff_dispatch hook that carries
DELIVERING/DELIVERED/CANCELLED from every staff channel to the customer.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.config import settings
from app.domains.orders.models import OrderState
from app.domains.staff.models import StaffRole
from app.fsm.core.base import TransitionResult
from app.services import customer_notify
from app.services import staff_dispatch as dispatch_mod
from app.services.customer_notify import notify_customer_order_state
from app.services.staff_dispatch import dispatch_kitchen, dispatch_order


class _FakeSession:
    def __init__(self) -> None:
        self.row: SimpleNamespace | None = None
        self.error: Exception | None = None

    async def execute(self, *args: object, **kwargs: object) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(one_or_none=lambda: self.row)


class _FakeCtx:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSessionFactory:
    """Mimics async_sessionmaker: plain call -> async context manager."""

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.calls = 0

    def __call__(self) -> _FakeCtx:
        self.calls += 1
        return _FakeCtx(self.session)


def _row(max_uid: int | None, tg_uid: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        _mapping={"client_max_uid": max_uid, "client_telegram_uid": tg_uid}
    )


@pytest.fixture
def db_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeSessionFactory:
    from app import db as db_mod

    fake = _FakeSessionFactory()
    monkeypatch.setattr(db_mod, "async_session_factory", fake)
    return fake


@pytest.fixture
def max_client_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from app.services import max_client as mc_mod

    client = AsyncMock()
    client.send_message = AsyncMock(return_value="mid-1")
    monkeypatch.setattr(mc_mod, "max_client", client)
    return client


@pytest.fixture
def telegram_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from app.services import notification_service as ns_mod

    send = AsyncMock()
    monkeypatch.setattr(ns_mod.notification_service, "send_telegram", send)
    return send


class TestNotifyCustomerOrderState:
    async def test_internal_state_is_noop(
        self, db_factory: _FakeSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        await notify_customer_order_state(str(uuid4()), OrderState.CONFIRMED)
        assert db_factory.calls == 0

    async def test_no_channels_configured_is_noop(
        self, db_factory: _FakeSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
        await notify_customer_order_state(str(uuid4()), OrderState.COOKING)
        assert db_factory.calls == 0

    async def test_non_uuid_order_id_skipped(
        self, db_factory: _FakeSessionFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        await notify_customer_order_state("not-a-uuid", OrderState.DELIVERED)
        assert db_factory.calls == 0

    async def test_max_customer_pushed(
        self,
        db_factory: _FakeSessionFactory,
        max_client_mock: AsyncMock,
        telegram_mock: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
        order_id = str(uuid4())
        db_factory.session.row = _row(max_uid=111, tg_uid=None)

        await notify_customer_order_state(order_id, OrderState.DELIVERED)

        max_client_mock.send_message.assert_awaited_once()
        args = max_client_mock.send_message.call_args.args
        assert args[0] == 111
        assert "доставлен" in args[1]
        assert order_id[:8] in args[1]
        telegram_mock.assert_not_awaited()

    async def test_telegram_customer_pushed(
        self,
        db_factory: _FakeSessionFactory,
        max_client_mock: AsyncMock,
        telegram_mock: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "")
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "tok")
        db_factory.session.row = _row(max_uid=None, tg_uid=222)

        await notify_customer_order_state(str(uuid4()), OrderState.DELIVERING)

        telegram_mock.assert_awaited_once()
        args = telegram_mock.call_args.args
        assert args[0] == "222"
        assert "Курьер в пути" in args[1]
        max_client_mock.send_message.assert_not_awaited()

    async def test_payment_failed_event_label_pushed(
        self,
        db_factory: _FakeSessionFactory,
        max_client_mock: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        db_factory.session.row = _row(max_uid=111, tg_uid=None)

        await notify_customer_order_state(str(uuid4()), "PAYMENT_FAILED")

        assert "Оплата не прошла" in max_client_mock.send_message.call_args.args[1]

    async def test_db_failure_swallowed(
        self,
        db_factory: _FakeSessionFactory,
        max_client_mock: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        db_factory.session.error = RuntimeError("db down")

        # must not raise — the notification contract is fire-and-forget
        await notify_customer_order_state(str(uuid4()), OrderState.CANCELLED)
        max_client_mock.send_message.assert_not_awaited()

    async def test_order_not_found_swallowed(
        self,
        db_factory: _FakeSessionFactory,
        max_client_mock: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "MAX_BOT_TOKEN", "tok")
        db_factory.session.row = None

        await notify_customer_order_state(str(uuid4()), OrderState.CANCELLED)
        max_client_mock.send_message.assert_not_awaited()


class TestStaffDispatchHook:
    async def test_successful_order_dispatch_notifies_customer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(dispatch_mod, "notify_customer_order_state", notify)
        orders = AsyncMock()
        orders.transition_order = AsyncMock(
            return_value=TransitionResult(
                success=True, new_state=OrderState.DELIVERED,
                rejected_event=None, reason=None,
            )
        )

        result = await dispatch_order(orders, StaffRole.courier, "o-1", "DELIVER")

        assert result is not None and result.success
        notify.assert_awaited_once_with("o-1", OrderState.DELIVERED)

    async def test_rejected_order_dispatch_skips_customer_notify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(dispatch_mod, "notify_customer_order_state", notify)
        orders = AsyncMock()
        orders.transition_order = AsyncMock(
            return_value=TransitionResult(
                success=False, new_state=None,
                rejected_event=None, reason="not allowed",
            )
        )

        result = await dispatch_order(orders, StaffRole.courier, "o-1", "DELIVER")

        assert result is not None and not result.success
        notify.assert_not_awaited()

    async def test_role_denied_event_skips_customer_notify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kitchen role firing a courier-only event must not reach the
        transition (or the customer) at all."""
        notify = AsyncMock()
        monkeypatch.setattr(dispatch_mod, "notify_customer_order_state", notify)
        orders = AsyncMock()

        result = await dispatch_order(orders, StaffRole.kitchen, "o-1", "DELIVER")

        assert result is None
        orders.transition_order.assert_not_awaited()
        notify.assert_not_awaited()

    async def test_kitchen_dispatch_never_notifies_customer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notify = AsyncMock()
        monkeypatch.setattr(dispatch_mod, "notify_customer_order_state", notify)
        kitchen = AsyncMock()
        kitchen.transition_ticket = AsyncMock(
            return_value=TransitionResult(
                success=True, new_state=None, rejected_event=None, reason=None,
            )
        )

        result = await dispatch_kitchen(kitchen, StaffRole.kitchen, "t-1", "QUEUE")

        assert result is not None
        notify.assert_not_awaited()


def test_state_text_map_covers_only_customer_meaningful_states() -> None:
    assert set(customer_notify._STATE_TEXT) == {
        "COOKING", "DELIVERING", "DELIVERED", "CANCELLED", "PAYMENT_FAILED",
    }
