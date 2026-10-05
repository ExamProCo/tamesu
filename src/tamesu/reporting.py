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
    "mean_cost_usd",
    "median_latency_ms",
}


def status_summary(plan: Plan) -> dict[str, Any]:
    manifests = read_run_manifests(plan.context.eval_dir)
    banked_ids = set(plan.banked_run_ids.values())
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
    if difference == 0:
        return [
            f"All reported rows recorded the same observed `{primary}` of "
            f"{_format_metric_value(primary, first['primary_mean'])}. No difference was "
            "observed on the primary metric."
        ]
    direction = "highest" if reverse else "lowest"
    better = "Higher" if reverse else "Lower"
    return [
        f"`{_row_label(first)}` recorded the {direction} observed `{primary}` at "
        f"{_format_metric_value(primary, first['primary_mean'])}. The observed spread "
        f"from `{_row_label(last)}` was {_format_difference(primary, difference)}. "
        f"{better} values are treated as preferable for this metric.",
        "",
        "This comparison reports an observed difference; it does not identify its cause.",
    ]


def _row_label(row: dict[str, Any]) -> str:
    return f"{row['model']} / {row['arm']}"


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
    if metric.endswith("_cost_usd"):
        return f"${number:.6f}"
    return f"{number:.4f}"


def _format_difference(metric: str, value: float) -> str:
    if metric.endswith("_rate") or metric.endswith("_accuracy"):
        return f"{value:.4f} ({value * 100:.2f} percentage points)"
    return _format_metric_value(metric, value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
