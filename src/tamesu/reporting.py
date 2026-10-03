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
            classification = "partial"
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
            if isinstance(report.get("metrics", {}).get(primary), (int, float))
        ]
        failure_values = [
            report.get("metrics", {}).get("generation_failure_rate")
            for report in reports
            if isinstance(
                report.get("metrics", {}).get("generation_failure_rate"), (int, float)
            )
        ]
        latency_values = [
            report.get("metrics", {}).get("median_latency_ms")
            for report in reports
            if isinstance(report.get("metrics", {}).get("median_latency_ms"), (int, float))
        ]
        cost_values = [
            report.get("totals", {}).get("cost_usd")
            for report in reports
            if isinstance(report.get("totals", {}).get("cost_usd"), (int, float))
        ]
        total_items = sum(
            int(report.get("completion", {}).get("selected_items", 0)) for report in reports
        )
        rows.append(
            {
                "model": model,
                "arm": arm,
                "repetitions": len(reports),
                "items": total_items,
                "primary_metric": primary,
                "primary_mean": statistics.mean(primary_values) if primary_values else None,
                "primary_stdev": (
                    statistics.pstdev(primary_values) if len(primary_values) > 1 else 0.0
                )
                if primary_values
                else None,
                "generation_failure_rate": (
                    statistics.mean(failure_values) if failure_values else None
                ),
                "median_latency_ms": (
                    statistics.median(latency_values) if latency_values else None
                ),
                "total_cost_usd": sum(cost_values) if len(cost_values) == len(reports) else None,
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


def _format_number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.4f}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
