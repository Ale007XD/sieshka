from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.repositories.narrative_receipt_repo import NarrativeReceiptRepository
from app.services.narrative_receipt_service import NarrativeReceipt, NarrativeReceiptService
from app.services.trace_analyzer import ExecutionReceipt, RejectedTransition, TraceHealthReport


class TestNarrativeReceiptModel:
    def test_create(self) -> None:
        nr = NarrativeReceipt(
            decision="approve",
            reason="All good",
            rules=("all_transitions_successful",),
            trace_ids=("trace-1",),
        )
        assert nr.decision == "approve"
        assert nr.reason == "All good"
        assert nr.rules == ("all_transitions_successful",)
        assert nr.trace_ids == ("trace-1",)

class TestNarrativeReceiptService:
    @staticmethod
    def _er(**overrides: Any) -> ExecutionReceipt:
        kwargs: dict[str, Any] = {
            "trace_id": "trace-1",
            "trace_hash": "abc",
            "final_status": "success",
            "resumable": False,
            "replayable": False,
            "blocked_actions": 0,
            "escalations": 0,
            "rejected_transitions": (),
            "health": TraceHealthReport(
                total_steps=1, successful_steps=1, failed_steps=0
            ),
        }
        kwargs.update(overrides)
        return ExecutionReceipt(**kwargs)

    def test_all_successful_approves(self) -> None:
        svc = NarrativeReceiptService()
        result = svc.generate([self._er()])
        assert result.decision == "approve"
        assert "all_transitions_successful" in result.rules
        assert result.trace_ids == ("trace-1",)

    def test_rejected_transitions_rejects(self) -> None:
        svc = NarrativeReceiptService()
        rejected = (RejectedTransition(step_id="s1", step_index=0, error="fail"),)
        result = svc.generate([self._er(rejected_transitions=rejected, final_status="failed")])
        assert result.decision == "reject"
        assert "rejected_transitions_detected" in result.rules
        assert "execution_failed" in result.rules

    def test_blocked_actions_escalates(self) -> None:
        svc = NarrativeReceiptService()
        result = svc.generate([self._er(blocked_actions=2)])
        assert result.decision == "escalate"
        assert "actions_blocked" in result.rules

    def test_escalations_escalates(self) -> None:
        svc = NarrativeReceiptService()
        result = svc.generate([self._er(escalations=1)])
        assert result.decision == "escalate"
        assert "escalations_raised" in result.rules

    def test_escalate_overrides_reject(self) -> None:
        svc = NarrativeReceiptService()
        rejected = (RejectedTransition(step_id="s1", step_index=0, error="fail"),)
        result = svc.generate([
            self._er(
                rejected_transitions=rejected,
                final_status="failed",
                blocked_actions=1,
            )
        ])
        assert result.decision == "escalate"

    def test_multiple_traces(self) -> None:
        svc = NarrativeReceiptService()
        er1 = self._er(trace_id="trace-1")
        er2 = self._er(trace_id="trace-2")
        result = svc.generate([er1, er2])
        assert result.trace_ids == ("trace-1", "trace-2")
        assert result.decision == "approve"

    def test_mixed_receipts(self) -> None:
        svc = NarrativeReceiptService()
        er1 = self._er(trace_id="trace-1")
        rejected = (RejectedTransition(step_id="s2", step_index=0, error="timeout"),)
        er2 = self._er(trace_id="trace-2", rejected_transitions=rejected, final_status="failed")
        result = svc.generate([er1, er2])
        assert result.decision == "reject"
        assert "rejected_transitions_detected" in result.rules

    def test_reason_includes_details(self) -> None:
        svc = NarrativeReceiptService()
        rejected = (RejectedTransition(step_id="s1", step_index=0, error="fail"),)
        result = svc.generate([self._er(rejected_transitions=rejected)])
        assert "s1" in result.reason
        assert "rejected transition" in result.reason.lower()


class TestNarrativeReceiptRepository:
    async def test_save(self) -> None:
        session = AsyncMock()
        repo = NarrativeReceiptRepository(session=session)
        nr = NarrativeReceipt(
            decision="approve",
            reason="ok",
            rules=("rule1",),
            trace_ids=("trace-1",),
        )
        receipt_id = await repo.save(nr)
        assert isinstance(receipt_id, str)
        assert len(receipt_id) > 0
        session.execute.assert_awaited_once()

    async def test_save_and_find(self) -> None:
        session = AsyncMock()
        mock_row = MagicMock()
        mock_row.decision = "approve"
        mock_row.reason = "ok"
        mock_row.rules = json.dumps(["rule1"])
        mock_row.trace_ids = json.dumps(["trace-1"])
        session.execute.return_value = MagicMock(one_or_none=MagicMock(return_value=mock_row))

        repo = NarrativeReceiptRepository(session=session)
        nr = NarrativeReceipt(
            decision="approve",
            reason="ok",
            rules=("rule1",),
            trace_ids=("trace-1",),
        )
        await repo.save(nr)

        found = await repo.find_by_trace_id("trace-1")
        assert found is not None
        assert found.decision == "approve"
        assert found.rules == ("rule1",)
        assert found.trace_ids == ("trace-1",)

    async def test_find_by_trace_id_not_found(self) -> None:
        session = AsyncMock()
        session.execute.return_value = MagicMock(one_or_none=MagicMock(return_value=None))

        repo = NarrativeReceiptRepository(session=session)
        found = await repo.find_by_trace_id("nonexistent")
        assert found is None

    async def test_find_by_trace_id_single_session(self) -> None:
        session = AsyncMock()
        mock_row = MagicMock()
        mock_row.decision = "reject"
        mock_row.reason = "bad"
        mock_row.rules = json.dumps(["rejected_transitions_detected"])
        mock_row.trace_ids = json.dumps(["trace-x"])
        session.execute.return_value = MagicMock(one_or_none=MagicMock(return_value=mock_row))

        repo = NarrativeReceiptRepository(session=session)
        found = await repo.find_by_trace_id("trace-x")
        assert found is not None
        assert found.decision == "reject"
        assert found.trace_ids == ("trace-x",)
