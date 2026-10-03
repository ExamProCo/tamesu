from __future__ import annotations

import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import JsonlWriter, write_json, write_text, write_yaml
from .config import load_yaml
from .discovery import find_run_dir
from .errors import ExecutionError, ProviderError
from .identity import digest_bytes, digest_file
from .models import EvalContext, RunSpec
from .planner import build_plan, items_by_id, limit_spec
from .providers import get_provider
from .pricing import estimate_plan_cost, recorded_cost
from .scoring import build_report, score_output
from .tasks.structured_text import render_prompts


def run_eval(
    context: EvalContext,
    *,
    only: str | None = None,
    limit_items: int | None = None,
    force: bool = False,
) -> list[str]:
    if context.evaluation["status"] != "active":
        raise ExecutionError(
            f"Eval status must be active for execution; found {context.evaluation['status']!r}."
        )
    if limit_items is not None and limit_items < 1:
        raise ExecutionError("--limit-items must be at least 1")

    plan = build_plan(context)
    specs = [spec for spec in plan.specs if only is None or spec.model == only]
    if not specs:
        raise ExecutionError("No planned run configurations match the requested filters.")
    if not force and limit_items is None:
        specs = [
            spec
            for spec in specs
            if spec.specification_fingerprint not in plan.banked_run_ids
        ]
    if limit_items is not None:
        specs = [limit_spec(spec, min(limit_items, len(spec.item_ids))) for spec in specs]
    if not specs:
        return []

    _check_budget(plan, tuple(specs))
    _check_credentials(specs)
    run_ids: list[str] = []
    for spec in specs:
        run_ids.append(execute_spec(context, spec))
    return run_ids


def execute_spec(
    context: EvalContext,
    spec: RunSpec,
    *,
    existing_run_dir: Path | None = None,
) -> str:
    now = datetime.now(UTC)
    run_id = (
        existing_run_dir.name
        if existing_run_dir
        else f"{spec.label}--{now.strftime('%Y%m%d-%H%M%S-%f')}--"
        f"{spec.specification_fingerprint.split(':', 1)[1][:8]}"
    )
    run_dir = existing_run_dir or context.eval_dir / "runs" / run_id
    log_path = context.eval_dir / "logs" / f"{run_id}.jsonl"
    log = JsonlWriter(log_path)
    provider = get_provider(spec.provider)
    started_at = _utc_now()

    previous_manifest: dict[str, Any] = {}
    if existing_run_dir and (run_dir / "run.yml").is_file():
        previous_manifest = load_yaml(run_dir / "run.yml")
        started_at = previous_manifest.get("started_at", started_at)

    manifest = _run_manifest(
        context,
        spec,
        run_id=run_id,
        state="running",
        started_at=started_at,
        finished_at=None,
        totals=previous_manifest.get("totals", {}),
    )
    write_yaml(run_dir / "run.yml", manifest)

    item_map = items_by_id(context)
    selected = [(item_id, item_map[item_id]) for item_id in spec.item_ids]
    results: list[dict[str, Any]] = []

    def work(item_id: str, item: dict[str, Any]) -> dict[str, Any]:
        return _process_item(context, spec, run_id, run_dir, log, provider, item_id, item)

    try:
        if spec.concurrency == 1 or len(selected) <= 1:
            for item_id, item in selected:
                results.append(work(item_id, item))
        else:
            with ThreadPoolExecutor(max_workers=min(spec.concurrency, len(selected))) as executor:
                futures = {
                    executor.submit(work, item_id, item): item_id for item_id, item in selected
                }
                for future in as_completed(futures):
                    results.append(future.result())
    except KeyboardInterrupt:
        manifest["state"] = "partial"
        manifest["finished_at"] = _utc_now()
        write_yaml(run_dir / "run.yml", manifest)
        raise

    all_complete = len(results) == len(selected) and all(
        result.get("state") == "complete" for result in results
    )
    has_permanent_failure = any(
        result.get("state") == "failed"
        and result.get("error", {}).get("retryable") is False
        for result in results
    )
    if all_complete and not spec.probe:
        state = "complete"
    elif has_permanent_failure:
        state = "failed"
    else:
        state = "partial"
    totals = _totals(results)
    manifest = _run_manifest(
        context,
        spec,
        run_id=run_id,
        state=state,
        started_at=started_at,
        finished_at=_utc_now(),
        totals=totals,
    )
    write_yaml(run_dir / "run.yml", manifest)
    build_report(context, run_dir, manifest)
    return run_id


def resume_run(context: EvalContext, run_id: str) -> str:
    if context.evaluation["status"] != "active":
        raise ExecutionError("An eval must be active before a partial run can resume.")
    run_dir = find_run_dir(context.project_root, run_id)
    manifest = load_yaml(run_dir / "run.yml")
    if manifest.get("eval_id") != context.eval_id:
        raise ExecutionError(f"Run {run_id} does not belong to {context.eval_id}.")
    if manifest.get("state") == "complete":
        raise ExecutionError(f"Run {run_id} is already complete.")
    if manifest.get("state") == "failed":
        raise ExecutionError(f"Run {run_id} has a permanent failure and cannot resume.")

    resolved = manifest.get("resolved", {})
    plan = build_plan(context)
    candidates = [
        spec
        for spec in plan.specs
        if spec.provider == resolved.get("provider")
        and spec.model == resolved.get("model")
        and spec.arm_id == resolved.get("arm")
        and spec.repetition == resolved.get("repetition")
    ]
    if not candidates:
        raise ExecutionError("The run no longer matches a planned configuration.")
    spec = candidates[0]
    saved_items = tuple(manifest.get("selection", {}).get("item_ids", []))
    if manifest.get("probe"):
        if saved_items != spec.item_ids[: len(saved_items)]:
            raise ExecutionError("The probe item selection no longer matches the dataset.")
        spec = limit_spec(spec, len(saved_items))
    elif saved_items != spec.item_ids:
        raise ExecutionError("The run item selection no longer matches the eval.")

    identity = manifest.get("identity", {})
    mismatches: list[str] = []
    if identity.get("specification_fingerprint") != spec.specification_fingerprint:
        mismatches.append("specification")
    if identity.get("content_fingerprint") != spec.content_fingerprint:
        mismatches.append("content")
    if mismatches:
        raise ExecutionError(
            f"Cannot resume {run_id}: {' and '.join(mismatches)} fingerprint changed."
        )

    _check_budget(plan, (spec,))
    _check_credentials([spec])
    return execute_spec(context, spec, existing_run_dir=run_dir)


def _process_item(
    context: EvalContext,
    spec: RunSpec,
    run_id: str,
    run_dir: Path,
    log: JsonlWriter,
    provider: Any,
    item_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    item_dir = run_dir / "items" / item_id
    result_path = item_dir / "result.yml"
    previous: dict[str, Any] | None = None
    if result_path.is_file():
        previous = load_yaml(result_path)
        if previous.get("state") == "complete":
            return previous
        error = previous.get("error", {})
        if previous.get("state") == "failed" and not error.get("retryable", False):
            return previous

    starting_attempts = int(previous.get("attempts", 0)) if previous else 0
    try:
        system_prompt, user_prompt = render_prompts(spec.prompts, item)
    except Exception as exc:
        result = {
            "schema_version": 1,
            "item_id": item_id,
            "state": "failed",
            "attempts": starting_attempts,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
            },
        }
        write_yaml(result_path, result)
        return result

    final_error: ProviderError | None = None
    response = None
    latency_ms = 0
    total_attempts = starting_attempts
    for attempt_offset in range(spec.retries + 1):
        attempt = starting_attempts + attempt_offset + 1
        total_attempts = attempt
        attempt_started = time.monotonic()
        try:
            response = provider.generate_text(
                model=spec.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema=context.output_schema or {},
                parameters=spec.parameters,
                timeout_seconds=spec.timeout_seconds,
            )
            latency_ms = round((time.monotonic() - attempt_started) * 1000)
            log.append(
                {
                    "run_id": run_id,
                    "item_id": item_id,
                    "stage": "generate",
                    "attempt": attempt,
                    "at": _utc_now(),
                    "provider": spec.provider,
                    "model": spec.model,
                    "ok": True,
                    "duration_ms": latency_ms,
                    "provider_request_id": response.request_id,
                    "usage": response.usage,
                    "cost_usd": response.cost_usd,
                }
            )
            break
        except ProviderError as exc:
            latency_ms = round((time.monotonic() - attempt_started) * 1000)
            final_error = exc
            log.append(
                {
                    "run_id": run_id,
                    "item_id": item_id,
                    "stage": "generate",
                    "attempt": attempt,
                    "at": _utc_now(),
                    "provider": spec.provider,
                    "model": spec.model,
                    "ok": False,
                    "duration_ms": latency_ms,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "status_code": exc.status_code,
                        "provider_request_id": exc.request_id,
                    },
                }
            )
            if not exc.retryable or attempt_offset >= spec.retries:
                break
            time.sleep(min(2**attempt_offset, 5))

    if response is None:
        assert final_error is not None
        result = {
            "schema_version": 1,
            "item_id": item_id,
            "state": "failed",
            "attempts": total_attempts,
            "generation": {
                "provider_request_id": final_error.request_id,
                "latency_ms": latency_ms,
                "usage": {},
                "cost_usd": None,
            },
            "prompt": {
                "system_sha256": digest_bytes(system_prompt.encode("utf-8")),
                "user_sha256": digest_bytes(user_prompt.encode("utf-8")),
            },
            "error": {
                "type": type(final_error).__name__,
                "message": str(final_error),
                "retryable": final_error.retryable,
                "status_code": final_error.status_code,
                "provider_request_id": final_error.request_id,
            },
        }
        write_yaml(result_path, result)
        return result

    raw_path = item_dir / "output.txt"
    write_text(raw_path, response.text)
    parsed, scores = score_output(
        response.text,
        expected=item.get("expected"),
        output_schema=context.output_schema or {},
    )
    output_path = raw_path
    media_type = "text/plain"
    if parsed is not None:
        output_path = item_dir / "output.json"
        write_json(output_path, parsed)
        media_type = "application/json"

    result = {
        "schema_version": 1,
        "item_id": item_id,
        "state": "complete",
        "attempts": total_attempts,
        "output": {
            "path": output_path.name,
            "sha256": digest_file(output_path),
            "media_type": media_type,
        },
        "generation": {
            "provider_request_id": response.request_id,
            "latency_ms": latency_ms,
            "usage": response.usage,
            "cost_usd": response.cost_usd,
            "response_metadata": response.response_metadata,
        },
        "prompt": {
            "system_sha256": digest_bytes(system_prompt.encode("utf-8")),
            "user_sha256": digest_bytes(user_prompt.encode("utf-8")),
        },
        "mechanical_scores": scores,
    }
    write_yaml(result_path, result)
    return result


def _run_manifest(
    context: EvalContext,
    spec: RunSpec,
    *,
    run_id: str,
    state: str,
    started_at: str,
    finished_at: str | None,
    totals: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "label": spec.label,
        "eval_id": context.eval_id,
        "state": state,
        "probe": spec.probe,
        "started_at": started_at,
        "finished_at": finished_at,
        "identity": {
            "specification_fingerprint": spec.specification_fingerprint,
            "content_fingerprint": spec.content_fingerprint,
            "content_inventory": spec.content_inventory,
        },
        "selection": {"item_ids": list(spec.item_ids)},
        "resolved": {
            "task": context.evaluation.get("task", context.case.get("default_task")),
            "provider": spec.provider,
            "model": spec.model,
            "arm": spec.arm_id,
            "repetition": spec.repetition,
            "concurrency": spec.concurrency,
            "timeout_seconds": spec.timeout_seconds,
            "retries": spec.retries,
            "budget_usd": spec.budget_usd,
            "parameters": spec.parameters,
            "prompts": {
                role: path.relative_to(context.project_root).as_posix()
                for role, path in spec.prompts.items()
            },
        },
        "provenance": {
            "tamesu_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "totals": totals,
    }


def _totals(results: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = sum(int(result.get("attempts", 0)) for result in results)
    latencies = [
        result.get("generation", {}).get("latency_ms")
        for result in results
        if isinstance(result.get("generation", {}).get("latency_ms"), int)
    ]
    costs = [result.get("generation", {}).get("cost_usd") for result in results]
    total_cost = (
        sum(costs)
        if costs and all(isinstance(cost, (int, float)) for cost in costs)
        else None
    )
    return {
        "attempts": attempts,
        "retries": max(0, attempts - len(results)),
        "duration_ms": sum(latencies),
        "cost_usd": total_cost,
    }


def _check_credentials(specs: list[RunSpec]) -> None:
    for provider_name in sorted({spec.provider for spec in specs}):
        provider = get_provider(provider_name)
        error = provider.credential_error()
        if error:
            raise ExecutionError(f"{provider_name}: {error}")


def _check_budget(plan: Any, specs: tuple[RunSpec, ...]) -> None:
    estimate = estimate_plan_cost(plan, specs)
    spent = recorded_cost(plan)
    ceiling = float(plan.context.evaluation["defaults"]["budget_usd"])
    projected = spent.known_usd + estimate.maximum_usd
    if projected > ceiling:
        raise ExecutionError(
            f"Recorded cost plus maximum priced exposure ${projected:.6f} exceeds the "
            f"${ceiling:.2f} budget ceiling. Lower max_tokens, reduce the plan, or "
            "raise budget_usd."
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
