"""tests/unit/fsm/test_kitchen_delivery_fsm.py"""
from __future__ import annotations

from app.domains.delivery.fsm import DeliveryEvent, DeliveryFSM, DeliveryState
from app.domains.kitchen.fsm import KitchenEvent, KitchenFSM, KitchenState


def make_kitchen(initial: KitchenState) -> tuple[KitchenFSM, dict[str, KitchenState]]:
    store: dict[str, KitchenState] = {"t1": initial}
    fsm = KitchenFSM(
        state_reader=lambda eid: store[eid],
        state_writer=lambda eid, s: store.update({eid: s}),
    )
    return fsm, store


def make_delivery(initial: DeliveryState) -> tuple[DeliveryFSM, dict[str, DeliveryState]]:
    store: dict[str, DeliveryState] = {"d1": initial}
    fsm = DeliveryFSM(
        state_reader=lambda eid: store[eid],
        state_writer=lambda eid, s: store.update({eid: s}),
    )
    return fsm, store


class TestKitchenFSM:
    def test_happy_path(self) -> None:
        fsm, store = make_kitchen(KitchenState.NEW)
        events = [
            KitchenEvent.QUEUE,
            KitchenEvent.START_PREP,
            KitchenEvent.MARK_READY,
            KitchenEvent.HAND_OFF,
        ]
        for event in events:
            r = fsm.transition("t1", event)
            assert r.success, f"Failed on {event}: {r.reason}"
        assert store["t1"] == KitchenState.HANDED_OFF

    def test_reject_invalid(self) -> None:
        fsm, store = make_kitchen(KitchenState.HANDED_OFF)
        r = fsm.transition("t1", KitchenEvent.QUEUE)
        assert not r.success
        assert store["t1"] == KitchenState.HANDED_OFF


class TestDeliveryFSM:
    def test_happy_path(self) -> None:
        fsm, store = make_delivery(DeliveryState.UNASSIGNED)
        events = [
            DeliveryEvent.ASSIGN,
            DeliveryEvent.PICKUP,
            DeliveryEvent.START_ROUTE,
            DeliveryEvent.COMPLETE,
        ]
        for event in events:
            r = fsm.transition("d1", event)
            assert r.success, f"Failed on {event}: {r.reason}"
        assert store["d1"] == DeliveryState.DELIVERED

    def test_fail_path(self) -> None:
        fsm, store = make_delivery(DeliveryState.ON_ROUTE)
        r = fsm.transition("d1", DeliveryEvent.FAIL)
        assert r.success
        assert store["d1"] == DeliveryState.FAILED
