"""One definition of where an eval is in its life, shared by present, status and report.

The state is derived from facts on disk, never from run counts alone: an eval whose runs are
all banked can still be waiting for blinded review, and `close` is a deliberate author act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import load_yaml
from .models import EvalContext, Plan

INVALID = "invalid"
DRAFT = "draft"
READY = "ready"
PARTIAL = "partial"
AWAITING_REVIEW = "awaiting-review"
EVIDENCE_COMPLETE = "evidence-complete"
CLOSED = "closed"

STATES = (INVALID, DRAFT, READY, PARTIAL, AWAITING_REVIEW, EVIDENCE_COMPLETE, CLOSED)
LABELS = {
    INVALID: "Invalid",
    DRAFT: "Draft",
    READY: "Ready to run",
    PARTIAL: "Partially run",
    AWAITING_REVIEW: "Awaiting review",
    EVIDENCE_COMPLETE: "Evidence complete",
    CLOSED: "Closed",
}
# States in which no result exists yet, so nothing may be presented as an outcome.
NO_RESULT_STATES = frozenset({INVALID, DRAFT, READY})


@dataclass(frozen=True)
class Lifecycle:
    state: str
    label: str
    reason: str
    modifiers: tuple[str, ...] = ()
    pending_items: int = 0
    owed_reviews: int = 0
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "label": self.label,
            "reason": self.reason,
            "modifiers": list(self.modifiers),
            "pending_items": self.pending_items,
            "owed_reviews": self.owed_reviews,
            "errors": list(self.errors),
        }


def invalid(errors: list[str]) -> Lifecycle:
    return Lifecycle(
        INVALID,
        LABELS[INVALID],
        f"The eval does not validate ({len(errors)} problem(s)); fix it, then run `tamesu lint`.",
        errors=tuple(errors),
    )


def pending_evidence(plan: Plan) -> tuple[int, int]:
    """(items still pending, reviews owed) summed over banked runs' stored reports."""
    pending = owed = 0
    for run_id in plan.banked_run_ids.values():
        report_path = plan.context.eval_dir / "runs" / run_id / "report.yml"
        if not report_path.is_file():
            continue
        evidence = load_yaml(report_path).get("evidence")
        if not isinstance(evidence, dict):
            continue
        pending += int(evidence.get("counts", {}).get("pending", 0))
        owed += int(evidence.get("review", {}).get("owed_reviews", 0))
    return pending, owed


def lifecycle_for(context: EvalContext, plan: Plan, counts: dict[str, int]) -> Lifecycle:
    """Derive the state. `counts` is `status_summary(plan)["counts"]`."""
    eval_id = context.eval_id
    pending, owed_reviews = pending_evidence(plan)
    modifiers: list[str] = []
    if counts.get("stale"):
        modifiers.append(f"{counts['stale']} stale run(s) excluded")
    if counts.get("failed"):
        modifiers.append(f"{counts['failed']} failed run(s)")

    status = context.evaluation.get("status")
    if status == "complete":
        gaps = 0
        closing = context.eval_dir / "closing.yml"
        if closing.is_file():
            gaps = int(load_yaml(closing).get("pending_items", 0) or 0)
        if gaps:
            modifiers.insert(0, f"closed with {gaps} item(s) still pending review")
        return Lifecycle(
            CLOSED, LABELS[CLOSED], "The author closed this eval; its evidence is final.",
            tuple(modifiers), pending, owed_reviews,
        )
    if status == "draft":
        return Lifecycle(
            DRAFT,
            LABELS[DRAFT],
            f"Check it with `tamesu plan {eval_id}`, then `tamesu activate {eval_id}`. "
            "A draft cannot run.",
            tuple(modifiers),
        )
    if not counts.get("banked") and not counts.get("partial") and not counts.get("failed"):
        return Lifecycle(
            READY, LABELS[READY], f"Active and nothing has run: `tamesu run {eval_id}`.", tuple(modifiers)
        )
    if counts.get("owed") or counts.get("partial") or counts.get("failed"):
        return Lifecycle(
            PARTIAL,
            LABELS[PARTIAL],
            f"{counts.get('banked', 0)} of {counts.get('planned', 0)} planned run(s) are banked and "
            f"{counts.get('owed', 0)} are still owed.",
            tuple(modifiers),
            pending,
            owed_reviews,
        )
    if pending:
        return Lifecycle(
            AWAITING_REVIEW,
            LABELS[AWAITING_REVIEW],
            f"All planned runs are banked, but {pending} item(s) await required review or judgment "
            f"({owed_reviews} review(s) owed).",
            tuple(modifiers),
            pending,
            owed_reviews,
        )
    return Lifecycle(
        EVIDENCE_COMPLETE,
        LABELS[EVIDENCE_COMPLETE],
        f"All planned runs are banked and no evidence is owed. `tamesu close {eval_id}` when final.",
        tuple(modifiers),
    )


def summarize(states: list[str]) -> str:
    """'1 invalid, 2 awaiting review' for case and experiment pages."""
    parts = []
    for state in STATES:
        count = states.count(state)
        if count:
            parts.append(f"{count} {LABELS[state].lower()}")
    return ", ".join(parts) or "no evals"
