from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import write_text
from .config import load_yaml
from .models import Plan
from .planner import read_run_manifests


LOWER_IS_BETTER_METRICS = {
    "generation_failure_rate",
    "invalid_output_rate",
    "safety_filter_rate",
    "invalid_artifact_rate",
    "cost_per_accepted_usd",
    "mean_cost_usd",
    "median_latency_ms",
}


def status_summary(plan: Plan) -> dict[str, Any]:
    manifests = read_run_manifests(plan.context.eval_dir)
    banked_ids = set(plan.banked_run_ids.values())
    stale_ids = set(plan.stale_run_ids)
    planned_fingerprints = {spec.specification_fingerprint for spec in plan.specs}
    counts = {
        "planned": len(plan.specs),
        "banked": len(plan.banked_run_ids),
        "owed": len(plan.owed_specs),
        "partial": 0,
        "failed": 0,
        "stale": 0,
        "extra": 0,
    }
    details: list[dict[str, Any]] = []
    for manifest in manifests:
        run_id = str(manifest.get("run_id", "unknown"))
        state = manifest.get("state", "unknown")
        specification = manifest.get("identity", {}).get("specification_fingerprint")
        if run_id in banked_ids:
            classification = "banked"
        elif manifest.get("probe"):
            classification = "failed" if state == "failed" else "partial"
        elif run_id in stale_ids:
            classification = "stale"
        elif specification not in planned_fingerprints:
            classification = "stale"
        elif state in {"planned", "running", "partial"}:
            classification = "partial"
        elif state == "failed":
            classification = "failed"
        elif state == "complete":
            classification = "extra"
        else:
            classification = "failed"
        if classification in counts and classification != "banked":
            counts[classification] += 1
        details.append(
            {
                "run_id": run_id,
                "label": manifest.get("label"),
                "state": state,
                "classification": classification,
            }
        )
    return {"counts": counts, "runs": details}


def comparison_rows(plan: Plan) -> list[dict[str, Any]]:
    primary = plan.context.evaluation["metrics"]["primary"]
    requested_metrics = [
        primary,
        *plan.context.evaluation["metrics"].get("secondary", []),
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    spec_by_fingerprint = {spec.specification_fingerprint: spec for spec in plan.specs}
    for fingerprint, run_id in plan.banked_run_ids.items():
        spec = spec_by_fingerprint[fingerprint]
        report_path = plan.context.eval_dir / "runs" / run_id / "report.yml"
        if not report_path.is_file():
            continue
        report = load_yaml(report_path)
        grouped[(spec.model, spec.arm_id)].append(report)

    rows: list[dict[str, Any]] = []
    for (model, arm), reports in sorted(grouped.items()):
        primary_values = [
            report.get("metrics", {}).get(primary)
            for report in reports
            if _is_number(report.get("metrics", {}).get(primary))
        ]
        failure_values = [
            report.get("metrics", {}).get("generation_failure_rate")
            for report in reports
            if _is_number(report.get("metrics", {}).get("generation_failure_rate"))
        ]
        latency_values = [
            report.get("metrics", {}).get("median_latency_ms")
            for report in reports
            if _is_number(report.get("metrics", {}).get("median_latency_ms"))
        ]
        cost_values = [
            report.get("totals", {}).get("cost_usd")
            for report in reports
            if _is_number(report.get("totals", {}).get("cost_usd"))
        ]
        total_items = sum(
            int(report.get("completion", {}).get("selected_items", 0)) for report in reports
        )
        completed_items = sum(
            int(report.get("completion", {}).get("completed_items", 0)) for report in reports
        )
        failed_items = sum(
            int(report.get("completion", {}).get("failed_items", 0)) for report in reports
        )
        metric_means: dict[str, float | None] = {}
        for metric in requested_metrics:
            values = [
                report.get("metrics", {}).get(metric)
                for report in reports
                if _is_number(report.get("metrics", {}).get(metric))
            ]
            metric_means[metric] = statistics.mean(values) if values else None
        rows.append(
            {
                "model": model,
                "arm": arm,
                "repetitions": len(reports),
                "items": total_items,
                "completed_items": completed_items,
                "failed_items": failed_items,
                "primary_metric": primary,
                "primary_mean": statistics.mean(primary_values) if primary_values else None,
                "primary_stdev": (
                    statistics.pstdev(primary_values) if len(primary_values) > 1 else 0.0
                )
                if primary_values
                else None,
                "generation_failure_rate": (
                    statistics.mean(failure_values)
                    if failure_values
                    else failed_items / total_items if total_items else None
                ),
                "median_latency_ms": (
                    statistics.median(latency_values) if latency_values else None
                ),
                "total_cost_usd": sum(cost_values) if len(cost_values) == len(reports) else None,
                "metrics": metric_means,
                "run_ids": [report["run_id"] for report in reports],
            }
        )
    return rows


def build_leaderboard(plan: Plan) -> Path:
    rows = comparison_rows(plan)
    primary = plan.context.evaluation["metrics"]["primary"]
    lines = [
        f"# {plan.context.evaluation['name']} leaderboard",
        "",
        plan.context.evaluation["question"].strip(),
        "",
        f"- Eval: `{plan.context.eval_id}`",
        f"- Dataset: `{plan.context.dataset['name']}`",
        f"- Status: `{plan.context.evaluation['status']}`",
        f"- Primary metric: `{primary}`",
        f"- Generated: `{_utc_now()}`",
        f"- Scorer: `tamesu {__version__}`",
        "",
        "| Model | Arm | Repetitions | Items | Primary mean | Std. dev. | Failure rate | Cost (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    if not rows:
        lines.append("| — | — | 0 | 0 | — | — | — | — |")
    for row in rows:
        lines.append(
            "| {model} | {arm} | {repetitions} | {items} | {primary} | {stdev} | "
            "{failure} | {cost} |".format(
                model=row["model"],
                arm=row["arm"],
                repetitions=row["repetitions"],
                items=row["items"],
                primary=_format_number(row["primary_mean"]),
                stdev=_format_number(row["primary_stdev"]),
                failure=_format_number(row["generation_failure_rate"]),
                cost=_format_number(row["total_cost_usd"]),
            )
        )
    lines.extend(["", "## Runs", ""])
    if not rows:
        lines.append("No compatible completed runs are available.")
    for row in rows:
        lines.append(f"### {row['model']} / {row['arm']}")
        lines.append("")
        for run_id in row["run_ids"]:
            lines.append(f"- [{run_id}](runs/{run_id}/report.yml)")
        lines.append("")

    path = plan.context.eval_dir / "leaderboard.md"
    write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def _state_line(plan: Plan, counts: dict[str, int]) -> str:
    from .lifecycle import lifecycle_for

    state = lifecycle_for(plan.context, plan, counts)
    return f"**{state.label}**. {state.reason}"


def build_evaluation_report(plan: Plan) -> Path:
    rows = comparison_rows(plan)
    summary = status_summary(plan)
    counts = summary["counts"]
    primary = plan.context.evaluation["metrics"]["primary"]
    secondary = plan.context.evaluation["metrics"].get("secondary", [])
    dataset_items = len(plan.context.dataset.get("items", []))

    lines = [
        f"# {plan.context.evaluation['name']} evaluation report",
        "",
        plan.context.evaluation["question"].strip(),
        "",
        f"- Eval: `{plan.context.eval_id}`",
        f"- Dataset: `{plan.context.dataset['name']}` ({dataset_items} authored items)",
        f"- Status: `{plan.context.evaluation['status']}`",
        f"- State: {_state_line(plan, counts)}",
        f"- Primary metric: `{primary}`",
        f"- Generated: `{_utc_now()}`",
        f"- Scorer: `tamesu {__version__}`",
        "",
        "## What happened",
        "",
    ]

    if counts["owed"]:
        lines.append(
            f"The eval is incomplete: {counts['banked']} of {counts['planned']} planned "
            f"runs are banked and {counts['owed']} are still owed. The comparisons below "
            "use only compatible completed runs."
        )
    else:
        lines.append(
            f"All {counts['planned']} planned runs are banked as compatible completed "
            "evidence."
        )
    lines.append("")
    if _is_image_eval(plan):
        summary, caveat = image_outcome(image_row_counts(plan))
        lines.extend([summary, "", caveat])
    else:
        lines.extend(_observed_result(rows, primary))

    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Model | Arm | Repetitions | Items | Primary mean | Std. dev. | "
            "Failure rate | Cost (USD) |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if not rows:
        lines.append("| — | — | 0 | 0 | — | — | — | — |")
    for row in rows:
        lines.append(
            "| {model} | {arm} | {repetitions} | {items} | {primary} | {stdev} | "
            "{failure} | {cost} |".format(
                model=row["model"],
                arm=row["arm"],
                repetitions=row["repetitions"],
                items=row["items"],
                primary=_format_number(row["primary_mean"]),
                stdev=_format_number(row["primary_stdev"]),
                failure=_format_number(row["generation_failure_rate"]),
                cost=_format_number(row["total_cost_usd"]),
            )
        )

    if secondary and rows:
        lines.extend(["", "## Diagnostic metrics", ""])
        for row in rows:
            lines.append(f"### {row['model']} / {row['arm']}")
            lines.append("")
            for metric in secondary:
                lines.append(
                    f"- `{metric}`: "
                    f"{_format_metric_value(metric, row['metrics'].get(metric))}"
                )
            lines.append("")

    lines.extend(_image_sections(plan))

    lines.extend(["## Evidence coverage", ""])
    lines.append(f"- Planned runs: {counts['planned']}")
    lines.append(f"- Banked runs: {counts['banked']}")
    lines.append(f"- Runs still owed: {counts['owed']}")
    lines.append(f"- Partial runs excluded: {counts['partial']}")
    lines.append(f"- Failed runs excluded: {counts['failed']}")
    lines.append(f"- Stale runs excluded: {counts['stale']}")
    lines.append(f"- Extra runs excluded: {counts['extra']}")

    lines.extend(
        [
            "",
            "## How to read this report",
            "",
            f"`Primary mean` is the average `{primary}` across compatible repetitions. "
            "`Std. dev.` describes variation between those repetition-level scores. "
            "`Failure rate` measures execution failures, not incorrect but usable answers. "
            "Cost is the recorded total for the runs in that row.",
            "",
            "This report describes observed differences. It does not establish why a row "
            "performed differently or whether the result will generalize beyond the "
            "authored dataset.",
            "",
            "## Limitations",
            "",
            f"- The dataset contains {dataset_items} authored items.",
        ]
    )
    if rows:
        minimum_repetitions = min(int(row["repetitions"]) for row in rows)
        lines.append(
            f"- The smallest reported row contains {minimum_repetitions} repetition(s); "
            "dispersion estimates are limited by that count."
        )
    if counts["owed"]:
        lines.append("- Planned evidence is still missing, so the comparison is provisional.")
    if any(row["failed_items"] for row in rows):
        lines.append("- At least one reported row contains failed item executions.")
    if any(row["total_cost_usd"] is None for row in rows):
        lines.append("- Cost is not fully known for every reported row.")
    lines.append(
        "- Read item results and experiment context before making a deployment or product "
        "decision."
    )

    lines.extend(["", "## Run evidence", ""])
    if not rows:
        lines.append("No compatible completed run reports are available.")
    for row in rows:
        lines.append(f"### {row['model']} / {row['arm']}")
        lines.append("")
        for run_id in row["run_ids"]:
            lines.append(f"- [{run_id}](runs/{run_id}/report.yml)")
        lines.append("")

    path = plan.context.eval_dir / "evaluation-report.md"
    write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


SMALL_SAMPLE = 10  # fewer images per row than this is never described as conclusive


def image_row_counts(plan: Plan) -> dict[tuple[str, str], dict[str, int]]:
    """Accepted, planned, and pending image counts per (model, arm), from banked run reports."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    spec_by_fingerprint = {spec.specification_fingerprint: spec for spec in plan.specs}
    for fingerprint, run_id in plan.banked_run_ids.items():
        report_path = plan.context.eval_dir / "runs" / run_id / "report.yml"
        if not report_path.is_file():
            continue
        evidence = load_yaml(report_path).get("evidence")
        if not evidence:
            continue
        spec = spec_by_fingerprint[fingerprint]
        row = counts.setdefault((spec.model, spec.arm_id), {"accepted": 0, "planned": 0, "pending": 0})
        for key in row:
            row[key] += int(evidence["counts"][key])
    return counts


def image_outcome(counts: dict[tuple[str, str], dict[str, int]]) -> tuple[str, str]:
    """(summary, caveat) for an image eval, in counts, and honest about how little one image proves."""
    if not counts:
        return (
            "No compatible completed evidence is available to answer the question.",
            "Nothing can be concluded yet.",
        )

    def label(key: tuple[str, str]) -> str:
        return f"{key[0]} / {key[1]}"

    def share(row: dict[str, int]) -> str:
        n = row["planned"]
        return f"{row['accepted']} of {n} images accepted ({row['accepted'] / n * 100:.1f}%)" if n else "no images"

    pending = sum(row["pending"] for row in counts.values())
    total = sum(row["planned"] for row in counts.values())
    single = all(row["planned"] == 1 for row in counts.values())
    notes: list[str] = []
    if pending:
        # No acceptance result exists while required evidence is owed: never name a leader or
        # a tie, and never summarize several rows by naming two of them.
        summary = (
            f"Review is incomplete: {pending} of {total} item(s) await required review or "
            "judgment, so there is no acceptance result yet. Mechanical results are in the table."
        )
        if single:
            notes.append(SINGLE_SAMPLE_CAVEAT)
        notes.append(
            "Pending items count as not accepted in the rates below, so those numbers will change."
        )
        return summary, " ".join(notes)

    ordered = sorted(counts, key=lambda k: counts[k]["accepted"] / counts[k]["planned"] if counts[k]["planned"] else 0, reverse=True)
    if len(ordered) == 1:
        key = ordered[0]
        only = counts[key]
        summary = f"{label(key)}: {share(only)}. There is no second row to compare."
        if 0 < only["planned"] < SMALL_SAMPLE:
            notes.append(
                f"With only {only['planned']} image(s), a single image changes this result by "
                f"{100 / only['planned']:.1f} percentage points."
            )
    elif len(ordered) >= 3:
        low, high = ordered[-1], ordered[0]
        a, b = counts[low], counts[high]
        if a["accepted"] * b["planned"] == b["accepted"] * a["planned"]:
            summary = f"All {len(ordered)} rows had the same acceptance: {share(a)}."
        else:
            summary = (
                f"Across {len(ordered)} rows, acceptance ranges from {share(a)} ({label(low)}) "
                f"to {share(b)} ({label(high)})."
            )
        smallest = min(row["planned"] for row in counts.values())
        if smallest < SMALL_SAMPLE:
            notes.append(
                f"With only {smallest} image(s) per row, gaps this size are within what a single image "
                "can change."
            )
    else:
        first, last = ordered[0], ordered[-1]
        a, b = counts[first], counts[last]
        if a["accepted"] * b["planned"] == b["accepted"] * a["planned"]:
            summary = f"{label(first)} and {label(last)} accepted the same share: {share(a)}."
        else:
            summary = f"{label(first)}: {share(a)}. {label(last)}: {share(b)}."
            smallest = min(row["planned"] for row in counts.values())
            one_image_apart = a["planned"] == b["planned"] and abs(a["accepted"] - b["accepted"]) <= 1
            if smallest < SMALL_SAMPLE or one_image_apart:
                notes.append(
                    f"With only {smallest} image(s) per row, this gap is within what a single image can "
                    "change. It does not show that either row is better."
                )
    if single and len(ordered) > 1:
        notes.append(SINGLE_SAMPLE_CAVEAT)
    notes.append(
        "Acceptance reflects the declared reviewers' judgments. This is an observed comparison; it "
        "does not establish why the rows differ or whether the result generalizes."
    )
    return summary, " ".join(notes)


def _image_sections(plan: Plan) -> list[str]:
    """Image evals: mechanical, model-judge, and human evidence side by side, never blended."""
    from .tasks import task_for

    context = plan.context
    if not task_for(context.evaluation, context.case).supports_review:
        return []
    lines: list[str] = []
    for fingerprint, run_id in sorted(plan.banked_run_ids.items(), key=lambda pair: pair[1]):
        run_dir = context.eval_dir / "runs" / run_id
        report_path = run_dir / "report.yml"
        if not report_path.is_file():
            continue
        report = load_yaml(report_path)
        evidence = report.get("evidence")
        if not evidence:
            continue
        counts, columns = evidence["counts"], evidence["columns"]
        policy = evidence["policy"]
        lines.extend([f"## Image evidence: {run_id}", ""])
        lines.append(
            f"Acceptance requires: {', '.join(policy['requires'])}"
            + (f"; model judge role: {policy['model_judge_role']}" if policy["model_judge_role"] else "")
            + f"; selection: {policy['selection']}."
        )
        lines.extend(
            [
                "",
                f"- Planned: {counts['planned']}; generated an image: {counts['generated']}",
                f"- Accepted: {counts['accepted']}; rejected: {counts['rejected']}; pending: {counts['pending']} "
                "(generation failures stay in the end-to-end denominator)",
                f"- End-to-end pass rate: {_format_number(evidence['metrics']['product_reference_pass_rate'])}; "
                f"given an image: {_format_number(evidence['metrics']['product_reference_pass_rate_given_image'])}",
                "",
                "| Evidence | Passed | Evaluated | Of generated |",
                "|---|---:|---:|---:|",
                f"| Mechanical | {columns['mechanical']['pass']} | {columns['mechanical']['of_generated']} | "
                f"{columns['mechanical']['of_generated']} |",
                f"| Model judge | {columns['model_judge']['pass']} | {columns['model_judge']['judged']} | "
                f"{columns['model_judge']['of_generated']} |",
                f"| Human | {columns['human']['pass']} | {columns['human']['reviewed']} | "
                f"{columns['human']['of_generated']} |",
                "",
            ]
        )
        review = evidence["review"]
        if review["completion"] is not None:
            lines.append(
                f"Review completion: {review['reviewed_items']}/{review['generated_items']} "
                f"({review['owed_reviews']} review(s) owed)."
            )
            lines.append("")
        for source, label in (("human", "Human"), ("model_judge", "Model judge")):
            stats = evidence["dimensions"][source]
            if not stats:
                continue
            lines.extend([f"### {label} dimension failures", "", "| Dimension | Failed | Evaluated | Top reasons |", "|---|---:|---:|---|"])
            for dimension_id, row in stats.items():
                reasons = ", ".join(f"{code} ({n})" for code, n in row["top_reasons"].items()) or "—"
                lines.append(f"| {dimension_id} | {row['failed']} | {row['evaluated']} | {reasons} |")
            lines.append("")
        if evidence["agreement"]:
            lines.extend(
                [
                    "### Judge vs human agreement",
                    "",
                    "| Dimension | n | Raw agreement | Cohen's kappa | Note |",
                    "|---|---:|---:|---:|---|",
                ]
            )
            for dimension_id, row in evidence["agreement"].items():
                note = "too few paired items for kappa to mean anything" if row["insufficient_data"] else ""
                lines.append(
                    f"| {dimension_id} | {row['n']} | {_format_number(row['raw_agreement'])} | "
                    f"{_format_number(row['kappa'])} | {note} |"
                )
            lines.append("")
        cost = evidence["cost"]
        lines.append(
            "Cost: generation {g}, judging {j}, total {t}, per accepted image {a}.".format(
                g=_money(cost["generation_usd"]),
                j=_money(cost["judge_usd"]),
                t=_money(cost["total_usd"]),
                a=_money(cost["per_accepted_usd"]),
            )
        )
        lines.extend(["", "### Items", "", "| Item | Outcome | Mechanical | Model judge | Human | Acceptance | Evidence |", "|---|---|---|---|---|---|---|"])
        for item in report["items"]:
            item_id = item["item_id"]
            outcome = (item.get("outcome") or {}).get("status", item.get("state"))
            mechanical = "pass" if item.get("mechanical_scores") and all(
                v.get("pass") for v in item["mechanical_scores"].values()
            ) else "fail"
            judge = (item.get("model_judge") or {}).get("verdict") or "—"
            human = (item.get("human") or {}).get("verdict") or (item.get("human") or {}).get("state") or "—"
            acceptance = (item.get("acceptance") or {}).get("state", "—")
            base = f"runs/{run_id}/items/{item_id}"
            links = []
            if item.get("output"):
                links.append(f"[image]({base}/{item['output']['path']})")
            item_dir = run_dir / "items" / item_id
            for sub in ("judgments", "reviews"):
                for path in sorted((item_dir / sub).glob("*.yml")) if (item_dir / sub).is_dir() else []:
                    links.append(f"[{sub[:-1]}]({base}/{sub}/{path.name})")
            lines.append(
                f"| {item_id} | {outcome} | {mechanical} | {judge} | {human} | {acceptance} | {' '.join(links) or '—'} |"
            )
        lines.append("")
    return lines


def _is_image_eval(plan: Plan) -> bool:
    from .tasks import task_for

    return task_for(plan.context.evaluation, plan.context.case).supports_review


def _money(value: Any) -> str:
    return f"${value:.6f}" if _is_number(value) else "unknown"


def _observed_result(rows: list[dict[str, Any]], primary: str) -> list[str]:
    measured = [row for row in rows if _is_number(row.get("primary_mean"))]
    if not measured:
        return ["No compatible completed runs are available for comparison."]
    if len(measured) == 1:
        row = measured[0]
        return [
            f"`{_row_label(row)}` recorded `{primary}` of "
            f"{_format_metric_value(primary, row['primary_mean'])}. There is no second "
            "row to compare."
        ]

    reverse = primary not in LOWER_IS_BETTER_METRICS
    ordered = sorted(measured, key=lambda row: float(row["primary_mean"]), reverse=reverse)
    first = ordered[0]
    last = ordered[-1]
    difference = abs(float(first["primary_mean"]) - float(last["primary_mean"]))
    caveat = [SINGLE_SAMPLE_CAVEAT, ""] if single_sample(measured) else []
    if difference == 0:
        return [
            f"All reported rows recorded the same observed `{primary}` of "
            f"{_format_metric_value(primary, first['primary_mean'])}. No difference was "
            "observed on the primary metric.",
            "",
            *caveat,
        ]
    if len(measured) >= 3:
        return [
            metric_range_sentence(measured, primary),
            "",
            *caveat,
            "This comparison reports an observed difference; it does not identify its cause.",
        ]
    direction = "highest" if reverse else "lowest"
    better = "Higher" if reverse else "Lower"
    return [
        f"`{_row_label(first)}` recorded the {direction} observed `{primary}` at "
        f"{_format_metric_value(primary, first['primary_mean'])}. The observed spread "
        f"from `{_row_label(last)}` was {_format_difference(primary, difference)}. "
        f"{better} values are treated as preferable for this metric.",
        "",
        *caveat,
        "This comparison reports an observed difference; it does not identify its cause.",
    ]


def _row_label(row: dict[str, Any]) -> str:
    return f"{row['model']} / {row['arm']}"


SINGLE_SAMPLE_CAVEAT = (
    "Each row has a single item and a single repetition, so this is not a comparison: it shows "
    "what happened once, not how often."
)


def single_sample(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(
        int(row.get("items") or 0) == 1 and int(row.get("repetitions") or 0) == 1 for row in rows
    )


def metric_range_sentence(rows: list[dict[str, Any]], metric: str) -> str:
    """Range over three or more rows: never name a subset as if it summarized the rest."""
    measured = sorted(
        (row for row in rows if _is_number(row.get("primary_mean"))),
        key=lambda row: float(row["primary_mean"]),
    )
    low, high = measured[0], measured[-1]
    return (
        f"Across {len(measured)} rows, `{metric}` ranges from "
        f"{_format_metric_value(metric, low['primary_mean'])} ({_row_label(low)}) to "
        f"{_format_metric_value(metric, high['primary_mean'])} ({_row_label(high)})."
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_number(value: Any) -> str:
    if not _is_number(value):
        return "—"
    return f"{value:.4f}"


def _format_metric_value(metric: str, value: Any) -> str:
    if not _is_number(value):
        return "—"
    number = float(value)
    if metric.endswith("_rate") or metric.endswith("_accuracy"):
        return f"{number:.4f} ({number * 100:.2f}%)"
    if metric.endswith("_latency_ms"):
        return f"{number:.1f} ms"
    if metric.endswith("_cost_usd") or metric.endswith("_accepted_usd"):
        return f"${number:.6f}"
    return f"{number:.4f}"


def _format_difference(metric: str, value: float) -> str:
    if metric.endswith("_rate") or metric.endswith("_accuracy"):
        return f"{value:.4f} ({value * 100:.2f} percentage points)"
    return _format_metric_value(metric, value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
