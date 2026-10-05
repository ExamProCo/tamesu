"""Model judging: a separate, repeatable, paid stage that never touches generation state.

Each judge call writes a new immutable record under `items/<item>/judgments/`. Judges see
the image, the product brief, and the rubric, and nothing else: the prompt context is an
allow-list, so a template that references arm, model, run, repetition, or cost fails to
render instead of leaking it.
"""

from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import write_yaml_exclusive
from .config import load_yaml
from .errors import ExecutionError, ProviderError
from .identity import digest_bytes, digest_file, digest_value
from .logging import CallLogWriter
from .models import EvalContext, ImagePart, TextPart
from .evidence import select_generated_items
from .providers import StructuredMultimodalProvider, get_provider
from .providers.models import estimate_cost, max_tokens_for
from .rubric import (
    Rubric,
    judge_output_schema,
    load_rubric,
    overall_pass,
    render_rubric_text,
    validate_judge_output,
)
from .tasks.structured_text import render_template

IMAGE_INPUT_TOKEN_ESTIMATE = 2_000  # budget assumption; the provider reports real usage
SEQUENCE_WIDTH = 4


@dataclass(frozen=True)
class JudgeSpec:
    id: str
    provider: str
    model: str
    rubric: Rubric
    prompts: dict[str, Path]
    repetitions: int
    parameters: dict[str, Any]


@dataclass(frozen=True)
class JudgeTarget:
    run_id: str
    run_dir: Path
    item_id: str
    item: dict[str, Any]
    image_path: Path
    media_type: str


@dataclass
class JudgeSummary:
    judged: int = 0
    ok: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    unknown_costs: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    judgment_paths: list[Path] = field(default_factory=list)


def load_judges(context: EvalContext) -> list[JudgeSpec]:
    from .config import resolve_contained

    judges: list[JudgeSpec] = []
    for raw in context.evaluation.get("evaluation", {}).get("model_judges", []) or []:
        rubric = load_rubric(resolve_contained(context.eval_dir, raw["rubric"], context.project_root))
        judges.append(
            JudgeSpec(
                id=raw["id"],
                provider=raw["provider"],
                model=raw["model"],
                rubric=rubric,
                prompts={
                    role: resolve_contained(context.eval_dir, raw["prompts"][role], context.project_root)
                    for role in ("system", "user")
                },
                repetitions=int(raw.get("repetitions", 1)),
                parameters=dict(raw.get("parameters", {})),
            )
        )
    return judges


def judge_prompt_context(item: dict[str, Any], rubric: Rubric) -> dict[str, Any]:
    """The only values a judge prompt may reference. Anything else is a template error."""
    return {
        "item": {"id": item["id"], "input": item.get("input", {})},
        "rubric": {"name": rubric.name, "text": render_rubric_text(rubric)},
    }


def render_judge_prompts(judge: JudgeSpec, item: dict[str, Any]) -> tuple[str, str]:
    values = judge_prompt_context(item, judge.rubric)
    rendered = {
        role: render_template(path.read_text(encoding="utf-8"), values, path)
        for role, path in judge.prompts.items()
    }
    return rendered["system"], rendered["user"]


# -- stored judgments --------------------------------------------------------------------


def judgments_dir(item_dir: Path) -> Path:
    return item_dir / "judgments"


def load_judgments(item_dir: Path) -> list[dict[str, Any]]:
    directory = judgments_dir(item_dir)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.yml")):
        record = load_yaml(path)
        record["_path"] = path
        records.append(record)
    return records


def judgment_currency(record: dict[str, Any], *, rubric_sha256: str | None, image_sha256: str | None) -> str:
    """current, stale_rubric, or stale_image: a stale record still exists, it just no longer counts."""
    if image_sha256 is not None and record.get("image_sha256") != image_sha256:
        return "stale_image"
    if rubric_sha256 is not None and record.get("rubric", {}).get("sha256") != rubric_sha256:
        return "stale_rubric"
    return "current"


def recorded_judge_cost(eval_dir: Path) -> tuple[float, int]:
    known, unknown = 0.0, 0
    for path in (eval_dir / "runs").glob("*/items/*/judgments/*.yml"):
        try:
            cost = load_yaml(path).get("cost_usd")
        except Exception:
            continue
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            known += float(cost)
        else:
            unknown += 1
    return round(known, 6), unknown


def _next_sequence(item_dir: Path, judge: JudgeSpec) -> int:
    prefix = f"{judge.id}--{judge.rubric.sha8}--"
    highest = 0
    directory = judgments_dir(item_dir)
    if directory.is_dir():
        for path in directory.glob(f"{prefix}*.yml"):
            match = re.fullmatch(re.escape(prefix) + r"(\d+)\.yml", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


# -- orchestration -----------------------------------------------------------------------


def judge_eval(
    context: EvalContext,
    *,
    run_id: str | None = None,
    judge_id: str | None = None,
    limit_items: int | None = None,
) -> JudgeSummary:
    if context.evaluation["status"] != "active":
        raise ExecutionError(
            f"Eval status must be active for judging; found {context.evaluation['status']!r}."
        )
    judges = load_judges(context)
    if judge_id is not None:
        judges = [judge for judge in judges if judge.id == judge_id]
    if not judges:
        raise ExecutionError("No model judges are declared (or none match --judge).")
    if limit_items is not None and limit_items < 1:
        raise ExecutionError("--limit-items must be at least 1")

    summary = JudgeSummary()
    targets = _collect_targets(context, run_id, limit_items, summary)
    if not targets:
        return summary

    _check_judge_capabilities(judges)
    _check_judge_budget(context, judges, targets)

    jobs: list[tuple[JudgeSpec, JudgeTarget, int]] = []
    for judge in judges:
        for target in targets:
            item_dir = target.run_dir / "items" / target.item_id
            first = _next_sequence(item_dir, judge)
            for offset in range(judge.repetitions):
                jobs.append((judge, target, first + offset))  # sequence fixed before threads start

    logs: dict[str, CallLogWriter] = {}
    for target in targets:
        if target.run_id not in logs:
            logs[target.run_id] = CallLogWriter(context.eval_dir / "logs" / f"{target.run_id}.jsonl")
            logs[target.run_id].append(
                "judge_started",
                at=_utc_now(),
                run_id=target.run_id,
                judges=[judge.id for judge in judges],
            )

    workers = max(1, int(context.evaluation["defaults"]["concurrency"]))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        records = list(
            executor.map(
                lambda job: _judge_once(context, job[0], job[1], job[2], logs[job[1].run_id]),
                jobs,
            )
        )
    for path, record in records:
        summary.judged += 1
        summary.judgment_paths.append(path)
        if record["status"] == "ok":
            summary.ok += 1
        else:
            summary.failed += 1
        cost = record.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            summary.cost_usd += float(cost)
        else:
            summary.unknown_costs += 1
    for run, log in logs.items():
        log.append("judge_finished", at=_utc_now(), run_id=run, judged=summary.judged, failed=summary.failed)
    summary.cost_usd = round(summary.cost_usd, 6)
    from .run_report import refresh_reports

    refresh_reports(context, list(logs))
    return summary


def _collect_targets(
    context: EvalContext, run_id: str | None, limit_items: int | None, summary: JudgeSummary
) -> list[JudgeTarget]:
    selection = select_generated_items(
        context, run_id, limit_per_run=limit_items, honor_prerequisites=True
    )
    summary.skipped.extend(selection.skipped)
    return [
        JudgeTarget(
            run_id=entry.run_id,
            run_dir=entry.run_dir,
            item_id=entry.item_id,
            item=entry.item,
            image_path=entry.image_path,
            media_type=entry.media_type,
        )
        for entry in selection.items
    ]


def _check_judge_capabilities(judges: list[JudgeSpec]) -> None:
    for provider_name in sorted({judge.provider for judge in judges}):
        provider = get_provider(provider_name)
        if not isinstance(provider, StructuredMultimodalProvider):
            raise ExecutionError(
                f"{provider_name}: provider has no multimodal structured-output adapter"
            )
        error = provider.credential_error()
        if error:
            raise ExecutionError(f"{provider_name}: {error}")


def estimate_judge_call(judge: JudgeSpec, item: dict[str, Any]) -> tuple[float, float] | None:
    system, user = render_judge_prompts(judge, item)
    schema_text = json.dumps(judge_output_schema(judge.rubric), separators=(",", ":"))
    input_tokens = math.ceil(len((system + user + schema_text).encode("utf-8")) / 4) + IMAGE_INPUT_TOKEN_ESTIMATE
    cap = int(judge.parameters.get("max_tokens", judge.parameters.get("max_output_tokens", max_tokens_for(judge.model))))
    expected = estimate_cost(judge.model, input_tokens, min(cap, 400))
    maximum = estimate_cost(judge.model, input_tokens, cap)
    if expected is None or maximum is None:
        return None
    return expected, maximum


def _check_judge_budget(context: EvalContext, judges: list[JudgeSpec], targets: list[JudgeTarget]) -> None:
    retries = int(context.evaluation["defaults"]["retries"])
    exposure = 0.0
    for judge in judges:
        for target in targets:
            per_call = estimate_judge_call(judge, target.item)
            if per_call is None:
                break  # unpriced judge: exposure stays unknown, never coerced to zero
            exposure += per_call[1] * (retries + 1) * judge.repetitions
    spent, _unknown = recorded_judge_cost(context.eval_dir)
    ceiling = float(context.evaluation["defaults"]["judge_budget_usd"])
    if spent + exposure > ceiling:
        raise ExecutionError(
            f"Recorded judge cost plus maximum judge exposure ${spent + exposure:.6f} exceeds the "
            f"${ceiling:.2f} judge budget ceiling (generation budget is separate). Lower max_tokens, "
            "judge fewer items, or raise defaults.judge_budget_usd."
        )


# -- one judge call ----------------------------------------------------------------------


def _judge_once(
    context: EvalContext, judge: JudgeSpec, target: JudgeTarget, sequence: int, log: CallLogWriter
) -> tuple[Path, dict[str, Any]]:
    provider = get_provider(judge.provider)
    item_dir = target.run_dir / "items" / target.item_id
    image_bytes = target.image_path.read_bytes()
    image_sha = digest_bytes(image_bytes)
    system_prompt, user_prompt = render_judge_prompts(judge, target.item)
    schema = judge_output_schema(judge.rubric)
    request_metadata = {
        "judge_id": judge.id,
        "system_sha256": digest_bytes(system_prompt.encode("utf-8")),
        "user_sha256": digest_bytes(user_prompt.encode("utf-8")),
        "image_sha256": image_sha,
        "rubric_sha256": judge.rubric.sha256,
        "parameters_sha256": digest_value(judge.parameters),
    }
    judgment_id = f"{judge.id}--{judge.rubric.sha8}--{sequence:0{SEQUENCE_WIDTH}d}"
    retries = int(context.evaluation["defaults"]["retries"])
    timeout = int(context.evaluation["defaults"]["timeout_seconds"])

    record: dict[str, Any] = {
        "schema_version": 1,
        "judgment_id": judgment_id,
        "run_id": target.run_id,
        "item_id": target.item_id,
        "created_at": _utc_now(),
        "judge": {
            "id": judge.id,
            "provider": judge.provider,
            "model": judge.model,
            "parameters": judge.parameters,
            "parameters_sha256": request_metadata["parameters_sha256"],
        },
        "rubric": {
            "name": judge.rubric.name,
            "sha256": judge.rubric.sha256,
            "path": judge.rubric.path.relative_to(context.project_root).as_posix()
            if judge.rubric.path.is_relative_to(context.project_root)
            else judge.rubric.path.name,
        },
        "image_sha256": image_sha,
        "prompt": {
            "system_sha256": request_metadata["system_sha256"],
            "user_sha256": request_metadata["user_sha256"],
        },
    }

    response = None
    final_error: ProviderError | None = None
    attempts = 0
    latency_ms = 0
    for offset in range(retries + 1):
        attempts = offset + 1
        started = time.monotonic()
        try:
            response = provider.generate_structured(
                model=judge.model,
                system_prompt=system_prompt,
                parts=(TextPart(user_prompt), ImagePart(image_bytes, target.media_type)),
                output_schema=schema,
                parameters=judge.parameters,
                timeout_seconds=timeout,
            )
            latency_ms = round((time.monotonic() - started) * 1000)
            log.append(
                "provider_attempt",
                run_id=target.run_id,
                item_id=target.item_id,
                stage="judge",
                attempt=attempts,
                at=_utc_now(),
                provider=judge.provider,
                model=judge.model,
                ok=True,
                duration_ms=latency_ms,
                request=request_metadata,
                provider_request_id=response.request_id,
                response_metadata=response.response_metadata,
                usage=response.usage,
                cost_usd=response.cost_usd,
            )
            break
        except Exception as exc:
            latency_ms = round((time.monotonic() - started) * 1000)
            provider_error = exc if isinstance(exc, ProviderError) else ProviderError(
                f"unexpected {type(exc).__name__}: {exc}", retryable=False
            )
            final_error = provider_error
            log.append(
                "provider_attempt",
                run_id=target.run_id,
                item_id=target.item_id,
                stage="judge",
                attempt=attempts,
                at=_utc_now(),
                provider=judge.provider,
                model=judge.model,
                ok=False,
                duration_ms=latency_ms,
                request=request_metadata,
                provider_request_id=provider_error.request_id,
                response_metadata=provider_error.response_metadata,
                usage=provider_error.usage,
                cost_usd=provider_error.cost_usd,
                error={
                    "type": type(provider_error).__name__,
                    "message": log.safe_text(provider_error),
                    "retryable": provider_error.retryable,
                    "status_code": provider_error.status_code,
                },
            )
            if not provider_error.retryable or offset >= retries:
                break
            time.sleep(min(2**offset, 5))

    record["attempts"] = attempts
    record["latency_ms"] = latency_ms
    if response is None:
        assert final_error is not None
        record.update(
            status="error",
            provider_request_id=final_error.request_id,
            usage=final_error.usage,
            cost_usd=final_error.cost_usd,
            error={
                "type": type(final_error).__name__,
                "message": log.safe_text(final_error),
                "retryable": final_error.retryable,
                "status_code": final_error.status_code,
            },
        )
    else:
        record.update(
            provider_request_id=response.request_id,
            usage=response.usage,
            cost_usd=response.cost_usd,
            response_metadata=log.redact(response.response_metadata),
            raw_output_sha256=digest_bytes(response.text.encode("utf-8")),
        )
        errors: list[str]
        try:
            output = json.loads(response.text)
            errors = validate_judge_output(output, judge.rubric)
        except json.JSONDecodeError as exc:
            output, errors = None, [f"output is not valid JSON: {exc.msg}"]
        if errors:
            record.update(status="invalid_output", error={"errors": errors}, raw_output=response.text)
        else:
            dimensions = output["dimensions"]
            record.update(
                status="ok",
                dimensions=dimensions,
                verdict="pass" if overall_pass(dimensions, judge.rubric) else "fail",
            )

    path = judgments_dir(item_dir) / f"{judgment_id}.yml"
    write_yaml_exclusive(path, record)
    log.append(
        "artifacts_written",
        at=_utc_now(),
        run_id=target.run_id,
        item_id=target.item_id,
        artifacts=[
            {
                "role": "judgment",
                "path": path.relative_to(context.eval_dir).as_posix(),
                "sha256": digest_file(path),
                "media_type": "application/yaml",
            }
        ],
    )
    return path, record


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
