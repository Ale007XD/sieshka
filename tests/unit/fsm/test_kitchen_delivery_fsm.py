"""tests/unit/fsm/test_kitchen_delivery_fsm.py"""
from __future__ import annotations

from app.domains.delivery.fsm import DeliveryEvent, DeliveryFSM, DeliveryState
from app.domains.kitchen.fsm import KitchenEvent, KitchenFSM, KitchenState


def make_kitchen(initial: KitchenState) -> tuple[KitchenFSM, dict[str, KitchenState]]:
    store: dict[str, KitchenState] = {"t1": initial}

    async def reader(eid: str) -> KitchenState:
        return store[eid]

    async def writer(eid: str, s: KitchenState) -> None:
        store[eid] = s

    fsm = KitchenFSM(state_reader=reader, state_writer=writer)
    return fsm, store


def make_delivery(initial: DeliveryState) -> tuple[DeliveryFSM, dict[str, DeliveryState]]:
    store: dict[str, DeliveryState] = {"d1": initial}

    async def reader(eid: str) -> DeliveryState:
        return store[eid]

    async def writer(eid: str, s: DeliveryState) -> None:
        store[eid] = s

    fsm = DeliveryFSM(state_reader=reader, state_writer=writer)
    return fsm, store


class TestKitchenFSM:
    async def test_happy_path(self) -> None:
        fsm, store = make_kitchen(KitchenState.NEW)
        events = [
            KitchenEvent.QUEUE,
            KitchenEvent.START_PREP,
            KitchenEvent.MARK_READY,
            KitchenEvent.HAND_OFF,
        ]
        for event in events:
            r = await fsm.transition("t1", event)
            assert r.success, f"Failed on {event}: {r.reason}"
        assert store["t1"] == KitchenState.HANDED_OFF

    async def test_reject_invalid(self) -> None:
        fsm, store = make_kitchen(KitchenState.HANDED_OFF)
        r = await fsm.transition("t1", KitchenEvent.QUEUE)
        assert not r.success
        assert store["t1"] == KitchenState.HANDED_OFF


class TestDeliveryFSM:
    async def test_happy_path(self) -> None:
        fsm, store = make_delivery(DeliveryState.UNASSIGNED)
        events = [
            DeliveryEvent.ASSIGN,
            DeliveryEvent.PICKUP,
            DeliveryEvent.START_ROUTE,
            DeliveryEvent.COMPLETE,
        ]
        for event in events:
            r = await fsm.transition("d1", event)
            assert r.success, f"Failed on {event}: {r.reason}"
        assert store["d1"] == DeliveryState.DELIVERED

    async def test_fail_path(self) -> None:
        fsm, store = make_delivery(DeliveryState.ON_ROUTE)
        r = await fsm.transition("d1", DeliveryEvent.FAIL)
        assert r.success
        assert store["d1"] == DeliveryState.FAILED
