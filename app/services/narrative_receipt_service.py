from __future__ import annotations

from pydantic import BaseModel

from app.services.trace_analyzer import ExecutionReceipt


class NarrativeReceipt(BaseModel):
    decision: str
    reason: str
    rules: tuple[str, ...]
    trace_ids: tuple[str, ...]


_RULE_MAP: list[tuple[str, str]] = [
    ("rejected_transitions_detected", "reject"),
    ("execution_failed", "reject"),
    ("actions_blocked", "escalate"),
    ("escalations_raised", "escalate"),
    ("all_transitions_successful", "approve"),
]


class NarrativeReceiptService:
    def generate(self, execution_receipts: list[ExecutionReceipt]) -> NarrativeReceipt:
        trace_ids = tuple(r.trace_id for r in execution_receipts)
        matched_rules: list[str] = []
        decision = "approve"
        reasons: list[str] = []

        for er in execution_receipts:
            if er.rejected_transitions:
                matched_rules.append("rejected_transitions_detected")
                ids = ", ".join(t.step_id for t in er.rejected_transitions)
                reasons.append(
                    f"Trace {er.trace_id}: rejected transition(s) at step(s) {ids}"
                )

            if len(er.rejected_transitions) > 0 and er.final_status == "failed":
                matched_rules.append("execution_failed")

            if er.blocked_actions > 0:
                matched_rules.append("actions_blocked")
                reasons.append(
                    f"Trace {er.trace_id}: {er.blocked_actions} action(s) blocked"
                )

            if er.escalations > 0:
                matched_rules.append("escalations_raised")
                reasons.append(
                    f"Trace {er.trace_id}: {er.escalations} escalation(s)"
                )

        if "actions_blocked" in matched_rules or "escalations_raised" in matched_rules:
            decision = "escalate"
        elif "rejected_transitions_detected" in matched_rules or (
            "execution_failed" in matched_rules
        ):
            decision = "reject"
        else:
            matched_rules.append("all_transitions_successful")
            reasons.append("All transitions completed successfully")

        return NarrativeReceipt(
            decision=decision,
            reason="; ".join(reasons),
            rules=tuple(matched_rules),
            trace_ids=trace_ids,
        )
