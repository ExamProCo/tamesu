"""Facts computed from evidence at render time. Never persisted into anyone's prose."""
from __future__ import annotations

from typing import Any

from .config import load_yaml
from .reporting import comparison_rows, image_outcome, image_row_counts, _format_metric_value


def computed_facts(plan: Any, *, review: bool) -> list[str]:
    """Markdown-ish lines: top-level "- " bullets with "  - " children."""
    return image_facts(plan) if review else text_facts(plan)


def image_facts(plan: Any) -> list[str]:
    context = plan.context
    lines: list[str] = []  # the outcome sentence is shown separately; do not repeat it here
    spec_by_fingerprint = {spec.specification_fingerprint: spec for spec in plan.specs}
    for fingerprint, run_id in sorted(plan.banked_run_ids.items(), key=lambda pair: pair[1]):
        report_path = context.eval_dir / "runs" / run_id / "report.yml"
        evidence = load_yaml(report_path).get("evidence") if report_path.is_file() else None
        if not evidence:
            continue
        spec = spec_by_fingerprint[fingerprint]
        counts = evidence["counts"]
        lines.append(
            f"- {spec.model} / {spec.arm_id} (repetition {spec.repetition}): {counts['generated']} of "
            f"{counts['planned']} produced an image; {counts['accepted']} accepted, "
            f"{counts['rejected']} rejected, {counts['pending']} pending."
        )
        for source, label in (("human", "Human review"), ("model_judge", "Model judge")):
            for dimension_id, stats in evidence["dimensions"][source].items():
                if stats["failed"]:
                    reasons = ", ".join(f"{code} ({n})" for code, n in stats["top_reasons"].items())
                    lines.append(
                        f"  - {label} failed `{dimension_id}` on {stats['failed']} of {stats['evaluated']}: {reasons}"
                    )
        for dimension_id, stats in evidence["agreement"].items():
            note = " (too few paired items to trust)" if stats["insufficient_data"] else ""
            lines.append(
                f"  - Judge vs human on `{dimension_id}`: {stats['raw_agreement']:.0%} agreement over "
                f"{stats['n']} item(s){note}"
            )
        cost = evidence["cost"]
        if cost["total_usd"] is not None:
            lines.append(f"  - Cost: ${cost['total_usd']:.4f} total (generation ${cost['generation_usd']:.4f}, judging ${cost['judge_usd']:.4f}).")
    return lines


def text_facts(plan: Any) -> list[str]:
    primary = plan.context.evaluation["metrics"]["primary"]
    lines = []
    for row in comparison_rows(plan):
        lines.append(
            f"- {row['model']} / {row['arm']}: {primary} = {_format_metric_value(primary, row['primary_mean'])} "
            f"over {row['repetitions']} repetition(s), {row['items']} item(s)."
        )
    return lines or ["- No compatible completed runs."]


def reviewers(plan: Any) -> set[str]:
    reviewers: set[str] = set()
    for run_id in plan.banked_run_ids.values():
        for path in (plan.context.eval_dir / "runs" / run_id).glob("items/*/reviews/*.yml"):
            reviewers.add(str(load_yaml(path).get("reviewer")))
    return reviewers


