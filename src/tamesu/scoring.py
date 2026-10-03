from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import write_yaml
from .config import load_yaml
from .models import EvalContext
from .tasks.structured_text import validate_json_schema


def score_output(
    raw_text: str,
    *,
    expected: Any,
    output_schema: dict[str, Any],
) -> tuple[Any | None, dict[str, Any]]:
    parsed: Any | None = None
    parse_error: str | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        parse_error = f"{exc.msg} at line {exc.lineno}, column {exc.colno}"

    schema_errors = (
        validate_json_schema(parsed, output_schema) if parse_error is None else [parse_error]
    )
    valid_json = parse_error is None
    schema_valid = valid_json and not schema_errors
    exact_match = schema_valid and parsed == expected

    field_matches: dict[str, bool] = {}
    if isinstance(expected, dict):
        for key, value in expected.items():
            field_matches[key] = isinstance(parsed, dict) and parsed.get(key) == value

    scores = {
        "valid_json": valid_json,
        "schema_valid": schema_valid,
        "exact_match": exact_match,
        "field_matches": field_matches,
        "schema_errors": schema_errors,
    }
    return parsed, scores


def build_report(context: EvalContext, run_dir: Path, run_manifest: dict[str, Any]) -> dict[str, Any]:
    selected_ids = list(run_manifest.get("selection", {}).get("item_ids", []))
    results: list[dict[str, Any]] = []
    for item_id in selected_ids:
        path = run_dir / "items" / item_id / "result.yml"
        if path.is_file():
            results.append(load_yaml(path))
        else:
            results.append({"item_id": item_id, "state": "missing"})

    selected_count = len(selected_ids)
    complete_results = [result for result in results if result.get("state") == "complete"]
    failed_count = selected_count - len(complete_results)

    def rate(numerator: int) -> float:
        return numerator / selected_count if selected_count else 0.0

    exact_matches = sum(
        1 for result in results if result.get("mechanical_scores", {}).get("exact_match") is True
    )
    invalid_outputs = sum(
        1
        for result in results
        if result.get("state") == "complete"
        and result.get("mechanical_scores", {}).get("schema_valid") is not True
    )
    metrics: dict[str, Any] = {
        "exact_match_rate": rate(exact_matches),
        "invalid_output_rate": rate(invalid_outputs),
        "generation_failure_rate": rate(failed_count),
    }

    expected_fields: set[str] = set()
    for item in context.dataset["items"]:
        expected = item.get("expected") if isinstance(item, dict) else None
        if isinstance(expected, dict):
            expected_fields.update(expected)
    for field in sorted(expected_fields):
        matches = sum(
            1
            for result in results
            if result.get("mechanical_scores", {}).get("field_matches", {}).get(field) is True
        )
        metrics[f"{field}_accuracy"] = rate(matches)

    latencies = [
        result.get("generation", {}).get("latency_ms")
        for result in results
        if isinstance(result.get("generation", {}).get("latency_ms"), int)
    ]
    metrics["median_latency_ms"] = statistics.median(latencies) if latencies else None

    costs = [result.get("generation", {}).get("cost_usd") for result in complete_results]
    if complete_results and all(isinstance(cost, (int, float)) for cost in costs):
        metrics["mean_cost_usd"] = sum(costs) / len(costs)
        total_cost: float | None = sum(costs)
    else:
        metrics["mean_cost_usd"] = None
        total_cost = None

    requested_metrics = [
        context.evaluation["metrics"]["primary"],
        *context.evaluation["metrics"].get("secondary", []),
    ]
    filtered_metrics = {name: metrics.get(name) for name in requested_metrics}

    report = {
        "schema_version": 1,
        "run_id": run_manifest["run_id"],
        "generated_at": _utc_now(),
        "scorer_version": __version__,
        "identity": dict(run_manifest.get("identity", {})),
        "completion": {
            "selected_items": selected_count,
            "completed_items": len(complete_results),
            "failed_items": failed_count,
        },
        "metrics": filtered_metrics,
        "totals": {"cost_usd": total_cost},
        "items": [
            {
                "item_id": result.get("item_id"),
                "state": result.get("state"),
                "mechanical_scores": result.get("mechanical_scores", {}),
            }
            for result in results
        ],
    }
    write_yaml(run_dir / "report.yml", report)
    return report


def rescore_run(context: EvalContext, run_dir: Path) -> dict[str, Any]:
    run_manifest = load_yaml(run_dir / "run.yml")
    expected_by_id = {
        item["id"]: item.get("expected")
        for item in context.dataset["items"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for item_id in run_manifest.get("selection", {}).get("item_ids", []):
        item_dir = run_dir / "items" / item_id
        result_path = item_dir / "result.yml"
        raw_path = item_dir / "output.txt"
        if not result_path.is_file() or not raw_path.is_file():
            continue
        result = load_yaml(result_path)
        _, scores = score_output(
            raw_path.read_text(encoding="utf-8"),
            expected=expected_by_id.get(item_id),
            output_schema=context.output_schema or {},
        )
        result["mechanical_scores"] = scores
        write_yaml(result_path, result)
    return build_report(context, run_dir, run_manifest)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
