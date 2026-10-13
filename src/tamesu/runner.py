from __future__ import annotations

import platform
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import write_yaml
from .config import load_yaml
from .discovery import find_run_dir
from .backends import BackendRun, backend_for
from .errors import ExecutionError
from .identity import digest_file
from .logging import CallLogWriter
from .models import EvalContext, RunSpec
from .planner import build_plan, items_by_id, limit_spec
from .selection import RunOptions, selected_plan
from .pricing import estimate_plan_cost, recorded_cost
from .run_report import build_report
from .tasks import task_for


def run_eval(
    context: EvalContext,
    *,
    only: str | None = None,
    limit_items: int | None = None,
    force: bool = False,
    trust_code: bool = False,
    options: RunOptions | None = None,
) -> list[str]:
    plan = selected_plan(context, options or RunOptions(model=only, limit_items=limit_items))
    context = plan.context
    specs = list(plan.specs)
    if not force:
        specs = [spec for spec in specs if spec.probe or spec.specification_fingerprint not in plan.banked_run_ids]
    if not specs:
        return []

    _check_budget(plan, tuple(specs))
    backend_for(context.evaluation).check_ready(context, specs, trust_code=trust_code)
    run_ids: list[str] = []
    spent_this_run = 0.0
    for index, spec in enumerate(specs, start=1):
        from dataclasses import replace
        remaining = float(context.evaluation['defaults']['budget_usd']) - spent_this_run
        if remaining <= 0:
            raise ExecutionError('Run budget exhausted')
        spec = replace(spec, budget_usd=remaining)
        try:
            run_id = execute_spec(context, spec, position=(index, len(specs)))
        except (KeyboardInterrupt, ExecutionError):
            _freeze_comparisons(context)
            raise
        run_ids.append(run_id)
        manifest = load_yaml(context.eval_dir / 'runs' / run_id / 'run.yml')
        cost = manifest.get('totals', {}).get('cost_usd')
        if isinstance(cost, (int, float)):
            spent_this_run += cost
        elif index < len(specs):
            # Unknown billing cannot support a guaranteed remaining allowance.
            spent_this_run += min(spec.budget_usd, context.evaluation.get('execution', {}).get('limits', {}).get('cost_limit', spec.budget_usd))
    _freeze_comparisons(context)
    return run_ids


def execute_spec(
    context: EvalContext,
    spec: RunSpec,
    *,
    existing_run_dir: Path | None = None,
    position: tuple[int, int] | None = None,
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
    log = CallLogWriter(log_path)
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
    try:
        log.append(
            "run_started",
            at=_utc_now(),
            run_id=run_id,
            resumed=existing_run_dir is not None,
            provider=spec.provider,
            model=spec.model,
            arm=spec.arm_id,
            repetition=spec.repetition,
            selected_items=len(spec.item_ids),
        )
    except Exception as exc:
        manifest["state"] = "failed"
        manifest["finished_at"] = _utc_now()
        write_yaml(run_dir / "run.yml", manifest)
        raise ExecutionError(
            f"Could not initialize the call log for {run_id}: {log.safe_text(exc)}"
        ) from exc

    item_map = items_by_id(context)
    selected = [(item_id, item_map[item_id]) for item_id in spec.item_ids]
    results: list[dict[str, Any]] = []

    progress = _Progress(
        total=len(selected),
        header=(
            f"run {position[0]}/{position[1]} " if position else "run "
        )
        + f"{spec.label} ({len(selected)} item(s), concurrency {spec.concurrency})",
    )

    backend_run = BackendRun(
        context=context,
        spec=spec,
        run_id=run_id,
        run_dir=run_dir,
        log=log,
        selected=selected,
        progress=progress,
        resumed=existing_run_dir is not None,
        results=results,
    )
    progress.start()
    try:
        backend_for(context.evaluation).execute(backend_run)
    except KeyboardInterrupt:
        totals = _totals(results)
        manifest = _run_manifest(
            context,
            spec,
            run_id=run_id,
            state="partial",
            started_at=started_at,
            finished_at=_utc_now(),
            totals=totals,
        )
        write_yaml(run_dir / "run.yml", manifest)
        log.append(
            "run_finished",
            at=_utc_now(),
            run_id=run_id,
            state="partial",
            totals=totals,
            interrupted=True,
        )
        raise
    except Exception as exc:
        totals = _totals(results)
        safe_message = log.safe_text(exc)
        manifest = _run_manifest(
            context,
            spec,
            run_id=run_id,
            state="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            totals=totals,
        )
        write_yaml(run_dir / "run.yml", manifest)
        log.append(
            "framework_error",
            at=_utc_now(),
            run_id=run_id,
            error={"type": type(exc).__name__, "message": safe_message},
        )
        log.append(
            "run_finished",
            at=_utc_now(),
            run_id=run_id,
            state="failed",
            totals=totals,
        )
        raise ExecutionError(f"Run {run_id} failed: {safe_message}") from exc

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
    try:
        write_yaml(run_dir / "run.yml", manifest)
        build_report(context, run_dir, manifest)
        report_path = run_dir / "report.yml"
        log.append(
            "run_finished",
            at=_utc_now(),
            run_id=run_id,
            state=state,
            totals=totals,
            report={
                "path": report_path.relative_to(context.eval_dir).as_posix(),
                "sha256": digest_file(report_path),
            },
        )
    except Exception as exc:
        safe_message = log.safe_text(exc)
        fallback_state = "failed" if has_permanent_failure else "partial"
        fallback = _run_manifest(
            context,
            spec,
            run_id=run_id,
            state=fallback_state,
            started_at=started_at,
            finished_at=_utc_now(),
            totals=totals,
        )
        write_yaml(run_dir / "run.yml", fallback)
        try:
            log.append(
                "framework_error",
                at=_utc_now(),
                run_id=run_id,
                error={"type": type(exc).__name__, "message": safe_message},
            )
            log.append(
                "run_finished",
                at=_utc_now(),
                run_id=run_id,
                state=fallback_state,
                totals=totals,
            )
        except Exception:
            pass
        raise ExecutionError(f"Could not finalize run {run_id}: {safe_message}") from exc
    progress.finish(state)
    return run_id


class _Progress:
    """Thread-safe progress lines on stderr; stdout stays reserved for results."""

    def __init__(self, *, total: int, header: str) -> None:
        self.total = total
        self.header = header
        self.done = 0
        self.failed = 0
        self.cost = 0.0
        self.cost_known = True
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def start(self) -> None:
        print(f"[{self.header}] started", file=sys.stderr, flush=True)

    def item_done(self, item_id: str, result: dict[str, Any]) -> None:
        state = result.get("state", "unknown")
        cost = result.get("generation", {}).get("cost_usd")
        with self.lock:
            self.done += 1
            if state != "complete":
                self.failed += 1
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                self.cost += cost
            elif state == "complete":
                self.cost_known = False
            elapsed = time.monotonic() - self.started
            remaining = self.total - self.done
            eta = f" eta {_duration(elapsed / self.done * remaining)}" if remaining else ""
            cost_text = f"${self.cost:.4f}" + ("" if self.cost_known else "+")
            failed = f" failed={self.failed}" if self.failed else ""
            print(
                f"  [{self.done}/{self.total}] {item_id} {state}"
                f"  elapsed {_duration(elapsed)}{eta}  cost {cost_text}{failed}",
                file=sys.stderr,
                flush=True,
            )

    def finish(self, state: str) -> None:
        print(
            f"[{self.header}] {state} in {_duration(time.monotonic() - self.started)}",
            file=sys.stderr,
            flush=True,
        )


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def resume_run(context: EvalContext, run_id: str, *, trust_code: bool = False) -> str:
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
    if not candidates and manifest.get('probe'):
        variants = [spec for spec in plan.specs if spec.provider == resolved.get('provider') and spec.model == resolved.get('model') and spec.arm_id == resolved.get('arm')]
        options = manifest.get('selection', {}).get('options', {})
        repetition = resolved.get('repetition')
        if variants and isinstance(repetition, int) and 1 <= repetition <= options.get('repetitions', 0):
            from .planner import make_run_spec
            variant = variants[0]
            candidates = [make_run_spec(context, provider=variant.provider, model=variant.model, arm_id=variant.arm_id,
                arm=next(arm for arm in context.evaluation['arms'] if arm['id'] == variant.arm_id),
                repetition=repetition, item_ids=variant.item_ids, prompts=variant.prompts, parameters=variant.parameters, probe=False)]
    if not candidates:
        raise ExecutionError("The run no longer matches a planned configuration.")
    spec = candidates[0]
    saved_items = tuple(manifest.get("selection", {}).get("item_ids", []))
    if manifest.get("probe"):
        if not set(saved_items).issubset(spec.item_ids):
            raise ExecutionError("The probe item selection no longer matches the dataset.")
        from dataclasses import replace
        from .identity import digest_value
        spec = replace(spec, item_ids=saved_items, probe=True, specification_fingerprint=digest_value({
            "original_specification_fingerprint": spec.specification_fingerprint,
            "item_ids": list(saved_items), "probe": True}))
    elif saved_items != spec.item_ids:
        raise ExecutionError("The run item selection no longer matches the eval.")

    from dataclasses import replace
    spec = replace(spec, concurrency=resolved.get("concurrency", spec.concurrency), budget_usd=resolved.get("budget_usd", spec.budget_usd), selection_options=manifest.get("selection", {}).get("options", {}))
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

    evaluation = dict(context.evaluation)
    evaluation['defaults'] = dict(evaluation['defaults'], budget_usd=spec.budget_usd)
    context = replace(context, evaluation=evaluation)
    plan = replace(plan, context=context)
    _check_budget(plan, (spec,))
    backend_for(context.evaluation).check_ready(context, [spec], trust_code=trust_code)
    result = execute_spec(context, spec, existing_run_dir=run_dir)
    _freeze_comparisons(context)
    return result


def _log_item_terminal(
    context: EvalContext,
    run_id: str,
    item_id: str,
    result_path: Path,
    result: dict[str, Any],
    log: CallLogWriter,
) -> None:
    log.append(
        "item_finished",
        at=_utc_now(),
        run_id=run_id,
        item_id=item_id,
        state=result.get("state"),
        attempts=result.get("attempts", 0),
        result={
            "path": result_path.relative_to(context.eval_dir).as_posix(),
            "sha256": digest_file(result_path),
        },
        error=result.get("error"),
    )


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
    manifest = {
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
        "selection": {"item_ids": list(spec.item_ids), "options": spec.selection_options},
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
                role: {"text": path.text} if hasattr(path, "text") else path.relative_to(context.project_root).as_posix()
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
    backend = backend_for(context.evaluation)
    manifest["execution"] = backend.run_manifest_extra(
        context, spec, context.eval_dir / "runs" / run_id
    )
    return manifest


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


def _check_budget(plan: Any, specs: tuple[RunSpec, ...]) -> None:
    estimate = estimate_plan_cost(plan, specs)
    ceiling = float(plan.context.evaluation["defaults"]["budget_usd"])
    projected = estimate.maximum_usd
    if projected > ceiling:
        raise ExecutionError(
            f"Maximum priced exposure ${projected:.6f} exceeds the "
            f"${ceiling:.2f} budget ceiling. Lower max_tokens, reduce the plan, or "
            "raise budget_usd."
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _freeze_comparisons(context: EvalContext) -> None:
    requested = [context.evaluation['metrics']['primary'], *context.evaluation['metrics'].get('secondary', [])]
    if not any('/' in key for key in requested):
        return
    from collections import defaultdict
    from .inspect_metrics import freeze_statistics
    groups = defaultdict(list)
    plan = build_plan(context)
    for spec in plan.specs:
        run_id = plan.banked_run_ids.get(spec.specification_fingerprint)
        if run_id:
            groups[(spec.provider, spec.model, spec.arm_id)].append(run_id)
    for run_ids in groups.values():
        freeze_statistics(context, run_ids)
