"""Inspect AI execution backend.

One Tamesu run becomes one Inspect evaluation over a frozen, Tamesu-derived dataset. The
Inspect log is supporting evidence; canonical item evidence is ingested from it and every
retained log is digest-pinned in the run manifest and call log.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import __version__
from ..artifacts import write_yaml
from ..config import load_yaml, resolve_contained
from ..errors import ConfigError, ExecutionError
from ..identity import digest_bytes, digest_file, digest_value
from ..models import EvalContext, RunSpec
from ..tasks import task_for
from ..providers.models import PROVIDER_KEYS, estimate_cost, registered
from . import BackendRun

ADAPTER_VERSION = 1
INSPECT_SPEC = ">=0.3.277,<0.4"
DEFAULT_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
MAX_ARTIFACTS_PER_ITEM = 200
TEXT_LIMIT = 4000
SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".sh", ".lock"}
SOURCE_PREFIXES = ("Dockerfile", "requirements", "compose", "docker-compose")
LIMIT_KEYS = {"token_limit", "time_limit", "message_limit", "working_limit", "cost_limit"}
ARTIFACT_KEYS = {"required", "allowed", "review", "max_bytes"}
EXECUTION_KEYS = {
    "backend",
    "file",
    "task",
    "task_args",
    "sources",
    "limits",
    "artifacts",
    "allow_mutable_images",
}
ROLE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
TASK_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Route:
    def __init__(self, prefix: str, base_url_env: str | None, default_base_url: str | None) -> None:
        self.prefix = prefix
        self.base_url_env = base_url_env
        self.default_base_url = default_base_url

    def base_url(self) -> str | None:
        if self.base_url_env and os.environ.get(self.base_url_env):
            return os.environ[self.base_url_env]
        return self.default_base_url


ROUTES: dict[str, Route] = {
    "meta": Route("openai-api/meta", "META_BASE_URL", "https://api.meta.ai/v1"),
    "openai": Route("openai", None, None),
    "anthropic": Route("anthropic", None, None),
}


def execution_block(evaluation: dict[str, Any]) -> dict[str, Any]:
    return dict(evaluation.get("execution") or {})


def model_uri(provider: str, model: str) -> str:
    return f"{ROUTES[provider].prefix}/{model}"


def base_url_origin(provider: str) -> str | None:
    url = ROUTES[provider].base_url()
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{parts.scheme}://{host}{':' + str(parts.port) if parts.port else ''}"


def installed_inspect_version() -> str | None:
    try:
        return metadata.version("inspect-ai")
    except metadata.PackageNotFoundError:
        return None


# -- lint -------------------------------------------------------------------------------


def declared_sources(evaluation: dict[str, Any], eval_dir: Path, project_root: Path) -> list[Path]:
    block = execution_block(evaluation)
    paths = []
    for raw in block.get("sources", []) or []:
        paths.append(resolve_contained(eval_dir, raw, project_root))
    return paths


def image_references(paths: list[Path]) -> list[tuple[Path, str]]:
    """Every container image a declared source pulls: compose `image:` and Dockerfile FROM."""
    import yaml

    refs: list[tuple[Path, str]] = []
    for path in paths:
        name = path.name
        if name.startswith(("compose", "docker-compose")) and path.suffix in {".yaml", ".yml"}:
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for service in (document.get("services") or {}).values():
                if isinstance(service, dict) and isinstance(service.get("image"), str):
                    refs.append((path, service["image"]))
        elif name.startswith("Dockerfile"):
            stages: set[str] = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
                if match:
                    if match.group(1).lower() not in stages and match.group(1).lower() != "scratch":
                        refs.append((path, match.group(1)))
                    if match.group(2):
                        stages.add(match.group(2).lower())
    return refs


def validate_execution(
    evaluation: dict[str, Any], eval_dir: Path, project_root: Path, errors: list[str]
) -> None:
    block = evaluation.get("execution")
    if not isinstance(block, dict):
        errors.append("eval.execution must be a mapping when backend is inspect")
        return
    for key in sorted(set(block) - EXECUTION_KEYS):
        errors.append(f"eval.execution.{key} is not a recognized field")
    file_raw = block.get("file")
    task_name = block.get("task")
    task_file: Path | None = None
    if not isinstance(file_raw, str) or not file_raw.endswith(".py"):
        errors.append("eval.execution.file must be a path to a Python file")
    else:
        try:
            task_file = resolve_contained(eval_dir, file_raw, project_root)
        except ConfigError as exc:
            errors.append(str(exc))
    if not isinstance(task_name, str) or not TASK_NAME.fullmatch(task_name):
        errors.append("eval.execution.task must be a Python identifier naming an Inspect @task")
    task_args = block.get("task_args", {})
    if not isinstance(task_args, dict):
        errors.append("eval.execution.task_args must be a mapping")
    elif "dataset" in task_args:
        errors.append("eval.execution.task_args.dataset is reserved: Tamesu supplies the frozen dataset")

    sources: list[Path] = []
    raw_sources = block.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        errors.append("eval.execution.sources must list every local file that defines the execution")
    else:
        for raw in raw_sources:
            if not isinstance(raw, str):
                errors.append("eval.execution.sources entries must be paths")
                continue
            try:
                sources.append(resolve_contained(eval_dir, raw, project_root))
            except ConfigError as exc:
                errors.append(str(exc))
    if task_file is not None and sources and task_file not in sources:
        errors.append("eval.execution.sources must include eval.execution.file")
    if task_file is not None and sources:
        declared = set(sources)
        for candidate in _source_like_files(task_file.parent):
            if candidate not in declared:
                errors.append(
                    "eval.execution.sources is missing "
                    f"{candidate.relative_to(project_root.resolve()).as_posix()} "
                    "(an execution or build file next to the task must be declared for identity)"
                )

    limits = block.get("limits")
    if not isinstance(limits, dict):
        errors.append("eval.execution.limits must be a mapping with cost_limit and time_limit")
    else:
        for key in sorted(set(limits) - LIMIT_KEYS):
            errors.append(f"eval.execution.limits.{key} is not a recognized limit")
        for key in ("cost_limit", "time_limit"):
            if key not in limits:
                errors.append(f"eval.execution.limits.{key} is required (budget must be finite)")
        for key, value in limits.items():
            if key in LIMIT_KEYS and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            ):
                errors.append(f"eval.execution.limits.{key} must be a positive number")

    artifacts = block.get("artifacts", {})
    if not isinstance(artifacts, dict):
        errors.append("eval.execution.artifacts must be a mapping")
    else:
        for key in sorted(set(artifacts) - ARTIFACT_KEYS):
            errors.append(f"eval.execution.artifacts.{key} is not a recognized field")
        for key in ("required", "allowed"):
            roles = artifacts.get(key, [])
            if not isinstance(roles, list) or any(
                not isinstance(r, str) or not ROLE.fullmatch(r) for r in roles
            ):
                errors.append(f"eval.execution.artifacts.{key} must be a list of role names")
        review = artifacts.get("review")
        if review is not None and (not isinstance(review, str) or not ROLE.fullmatch(review)):
            errors.append("eval.execution.artifacts.review must be a role name")
        max_bytes = artifacts.get("max_bytes")
        if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1):
            errors.append("eval.execution.artifacts.max_bytes must be a positive integer")

    if not block.get("allow_mutable_images", False):
        for path, image in image_references(sources):
            if "@sha256:" not in image:
                errors.append(
                    f"{path.name}: image {image!r} is a mutable tag; pin it as name@sha256:<digest> "
                    "or set eval.execution.allow_mutable_images: true"
                )

    for index, run in enumerate(evaluation.get("runs") or []):
        if not isinstance(run, dict):
            continue
        provider = run.get("provider")
        model = run.get("model")
        if provider not in ROUTES:
            errors.append(
                f"eval.runs[{index}].provider {provider!r} has no Inspect route "
                f"(supported: {', '.join(sorted(ROUTES))})"
            )
        spec = registered(model) if isinstance(model, str) else None
        if spec is None or spec.input_usd_per_million is None or spec.output_usd_per_million is None:
            errors.append(
                f"eval.runs[{index}] model {model!r} needs registered pricing so cost_limit can be enforced"
            )


def _source_like_files(directory: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part.startswith(".") or part in {"staging", "__pycache__"} for part in relative.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in SOURCE_SUFFIXES or path.name.startswith(SOURCE_PREFIXES):
            found.append(path.resolve())
    return found


# -- frozen dataset ---------------------------------------------------------------------


def frozen_samples(
    context: EvalContext, prompts: dict[str, Path], items: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """The exact Inspect dataset: one sample per Tamesu item, ID preserved, prompts rendered."""
    from ..tasks.structured_text import render_prompts

    samples = []
    for item_id, item in items:
        system_prompt, user_prompt = render_prompts(prompts, item)
        expected = item.get("expected")
        samples.append(
            {
                "id": item_id,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "target": expected if isinstance(expected, str) else json.dumps(expected, sort_keys=True),
                "metadata": {"tamesu_item_id": item_id, **(item.get("metadata") or {})},
            }
        )
    return samples


def _dump_samples(samples: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(sample, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        for sample in samples
    )


# -- backend ----------------------------------------------------------------------------


class InspectBackend:
    name = "inspect"

    def validate_eval(
        self, evaluation: dict[str, Any], eval_dir: Path, project_root: Path, errors: list[str]
    ) -> None:
        validate_execution(evaluation, eval_dir, project_root, errors)

    def max_attempts(self, spec: RunSpec) -> int:
        return 1

    def check_ready(self, context: EvalContext, specs: list[RunSpec], *, trust_code: bool) -> None:
        if (context.case_dir / ".untrusted-origin").exists() and not trust_code:
            raise ExecutionError(
                "This case was unpacked from a package and its Inspect task is executable Python "
                "that runs on this machine. Read the code under the case's execution sources, then "
                "re-run with --trust-code."
            )
        if installed_inspect_version() is None:
            raise ExecutionError(
                f"inspect-ai {INSPECT_SPEC} is not installed; run: pip install 'tamesu[inspect]'"
            )
        for provider in sorted({spec.provider for spec in specs}):
            names = PROVIDER_KEYS[provider]
            if not any(os.environ.get(name) for name in names):
                raise ExecutionError(f"{provider}: {' or '.join(names)} is not set in the effective environment")
        sources = declared_sources(context.evaluation, context.eval_dir, context.project_root)
        if image_references(sources) or any(p.name.startswith(("compose", "Dockerfile")) for p in sources):
            self._check_docker()

    @staticmethod
    def _check_docker() -> None:
        if shutil.which("docker") is None:
            raise ExecutionError("docker is required by this eval's sandbox but was not found on PATH")
        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ExecutionError("docker is installed but its daemon is not reachable") from exc

    def identity(
        self,
        context: EvalContext,
        *,
        provider: str,
        model: str,
        prompts: dict[str, Path],
        item_ids: tuple[str, ...],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Path]]:
        from ..planner import items_by_id

        block = execution_block(context.evaluation)
        sources = declared_sources(context.evaluation, context.eval_dir, context.project_root)
        item_map = items_by_id(context)
        samples = frozen_samples(context, prompts, [(i, item_map[i]) for i in item_ids])
        extra = {
            "execution": {
                "backend": "inspect",
                "adapter_version": ADAPTER_VERSION,
                "inspect_version": installed_inspect_version(),
                "file": block.get("file"),
                "task": block.get("task"),
                "task_args": block.get("task_args", {}),
                "limits": block.get("limits", {}),
                "artifacts": block.get("artifacts", {}),
                "model_uri": model_uri(provider, model),
                "base_url_origin": base_url_origin(provider),
                "frozen_dataset_sha256": digest_bytes(_dump_samples(samples)),
                "images": sorted({image for _, image in image_references(sources)}),
            }
        }
        return extra, sources

    def estimate_item_cost(
        self, task: Any, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        limit = execution_block(context.evaluation).get("limits", {}).get("cost_limit")
        if not isinstance(limit, (int, float)):
            return None
        # Agent loops cannot be estimated; the finite per-sample cost_limit is the exposure.
        return float(limit), float(limit)

    def run_manifest_extra(self, context: EvalContext, spec: RunSpec, run_dir: Path) -> dict[str, Any]:
        block = execution_block(context.evaluation)
        extra: dict[str, Any] = {
            "backend": "inspect",
            "adapter_version": ADAPTER_VERSION,
            "inspect_version": installed_inspect_version(),
            "model_uri": model_uri(spec.provider, spec.model),
            "base_url_origin": base_url_origin(spec.provider),
            "task": {"file": block.get("file"), "name": block.get("task"), "args": block.get("task_args", {})},
            "limits": block.get("limits", {}),
        }
        lineage = run_dir / "inspect" / "lineage.yml"
        if lineage.is_file():
            extra["logs"] = load_yaml(lineage).get("logs", [])
        return extra

    # -- execution ----------------------------------------------------------------------

    def execute(self, run: BackendRun) -> None:
        context, spec = run.context, run.spec
        inspect_dir = run.run_dir / "inspect"
        inspect_dir.mkdir(parents=True, exist_ok=True)
        input_path = self._freeze_dataset(run, inspect_dir)

        pending: list[str] = []
        for item_id, _ in run.selected:
            result_path = run.run_dir / "items" / item_id / "result.yml"
            if result_path.is_file():
                previous = load_yaml(result_path)
                error = previous.get("error", {})
                if previous.get("state") == "complete" or (
                    previous.get("state") == "failed" and not error.get("retryable", False)
                ):
                    run.results.append(previous)
                    run.progress.item_done(item_id, previous)
                    continue
            pending.append(item_id)
        if not pending:
            return

        lineage_path = inspect_dir / "lineage.yml"
        lineage = load_yaml(lineage_path).get("logs", []) if lineage_path.is_file() else []
        attempt = len(lineage) + 1
        log_dir = inspect_dir / f"attempt-{attempt:02d}"
        staging = inspect_dir / "staging" / f"attempt-{attempt:02d}"
        staging.mkdir(parents=True, exist_ok=True)

        log = self._run_inspect(run, input_path, log_dir, staging, pending)
        log_files = sorted(log_dir.glob("*.eval"))
        if len(log_files) != 1:
            raise ExecutionError(f"Expected exactly one Inspect log in {log_dir}, found {len(log_files)}")
        digest = digest_file(log_files[0])
        relative = log_files[0].relative_to(context.eval_dir).as_posix()
        lineage.append(
            {
                "attempt": attempt,
                "path": relative,
                "sha256": digest,
                "status": log.status,
                "samples": len(log.samples or []),
                "inspect_version": installed_inspect_version(),
            }
        )
        write_yaml(lineage_path, {"logs": lineage})
        run.log.append(
            "inspect_log",
            at=_utc_now(),
            run_id=run.run_id,
            attempt=attempt,
            path=relative,
            sha256=digest,
            status=log.status,
            samples=len(log.samples or []),
            inspect_version=installed_inspect_version(),
        )
        self._ingest(run, log, pending, staging, relative)

    def _freeze_dataset(self, run: BackendRun, inspect_dir: Path) -> Path:
        samples = frozen_samples(run.context, run.spec.prompts, run.selected)
        data = _dump_samples(samples)
        path = inspect_dir / "input.jsonl"
        if path.is_file():
            if digest_file(path) != digest_bytes(data):
                raise ExecutionError(
                    "The frozen Inspect dataset no longer matches the items and prompts; "
                    "start a fresh run instead of resuming."
                )
        else:
            path.write_bytes(data)
        return path

    def _run_inspect(
        self, run: BackendRun, input_path: Path, log_dir: Path, staging: Path, pending: list[str]
    ) -> Any:
        try:
            from inspect_ai import eval as inspect_eval
            from inspect_ai.model import ModelCost
        except ImportError as exc:  # pragma: no cover - guarded by check_ready
            raise ExecutionError(f"inspect-ai is not importable: {exc}") from exc

        context, spec = run.context, run.spec
        block = execution_block(context.evaluation)
        limits = dict(block.get("limits", {}))
        task_file = resolve_contained(context.eval_dir, block["file"], context.project_root)
        uri = model_uri(spec.provider, spec.model)
        model_spec = registered(spec.model)
        assert model_spec is not None
        generation: dict[str, Any] = {"max_retries": spec.retries, "timeout": spec.timeout_seconds}
        parameters = dict(spec.parameters)
        if "effort" in parameters:
            generation["reasoning_effort"] = parameters.pop("effort")
        if "max_tokens" in parameters or "max_output_tokens" in parameters:
            generation["max_tokens"] = parameters.pop("max_tokens", parameters.pop("max_output_tokens", None))
        if "temperature" in parameters:
            generation["temperature"] = parameters.pop("temperature")
        for key in parameters:
            raise ExecutionError(f"Parameter {key!r} has no Inspect mapping in adapter version {ADAPTER_VERSION}")

        previous = os.environ.get("TAMESU_STAGING_DIR")
        os.environ["TAMESU_STAGING_DIR"] = str(staging)
        try:
            logs = inspect_eval(
                f"{task_file}@{block['task']}",
                model=uri,
                model_base_url=ROUTES[spec.provider].base_url(),
                task_args={**block.get("task_args", {}), "dataset": str(input_path)},
                sample_id=pending,
                epochs=1,
                log_dir=str(log_dir),
                log_format="eval",
                log_samples=True,
                log_model_api=False,
                display="none",
                fail_on_error=False,
                retry_on_error=0,
                max_samples=spec.concurrency,
                max_sandboxes=spec.concurrency,
                model_cost_config={
                    uri: ModelCost(
                        input=model_spec.input_usd_per_million,
                        output=model_spec.output_usd_per_million,
                        input_cache_write=model_spec.input_usd_per_million,
                        input_cache_read=model_spec.input_usd_per_million,
                    )
                },
                **limits,
                **generation,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise ExecutionError(f"Inspect could not run {block['task']}: {run.log.safe_text(exc)}") from exc
        finally:
            if previous is None:
                os.environ.pop("TAMESU_STAGING_DIR", None)
            else:
                os.environ["TAMESU_STAGING_DIR"] = previous
        if len(logs) == 1:
            return logs[0]
        if not logs:
            # Ctrl-C: Inspect swallows the interrupt and returns nothing, but writes the cancelled
            # log it had so far. Recover it so the run is partial and resumable, not failed.
            written = sorted(log_dir.glob("*.eval"))
            if len(written) == 1:
                from inspect_ai.log import read_eval_log

                return read_eval_log(str(written[0]))
            raise KeyboardInterrupt("Inspect was interrupted before it wrote a log")
        raise ExecutionError(f"Inspect returned {len(logs)} logs; expected one")

    # -- ingestion ----------------------------------------------------------------------

    def _ingest(
        self, run: BackendRun, log: Any, pending: list[str], staging: Path, log_relative: str
    ) -> None:
        from .. import runner

        by_id: dict[str, Any] = {}
        expected = set(pending)
        for sample in log.samples or []:
            sample_id = str(sample.id)
            if sample_id not in expected:
                raise ExecutionError(f"Inspect log contains unexpected sample {sample_id!r}")
            if sample_id in by_id:
                raise ExecutionError(f"Inspect log contains duplicate sample {sample_id!r}")
            by_id[sample_id] = sample

        block = execution_block(run.context.evaluation)
        required_scorers = _scorer_names(run.context.evaluation)
        for item_id in pending:
            item_dir = run.run_dir / "items" / item_id
            item_dir.mkdir(parents=True, exist_ok=True)
            sample = by_id.get(item_id)
            result = self._item_result(
                run, item_id, item_dir, sample, log, staging, required_scorers, block, log_relative
            )
            result_path = item_dir / "result.yml"
            write_yaml(result_path, result)
            if result.get("artifacts"):
                run.log.append(
                    "artifacts_written",
                    at=_utc_now(),
                    run_id=run.run_id,
                    item_id=item_id,
                    artifacts=result["artifacts"],
                )
            runner._log_item_terminal(run.context, run.run_id, item_id, result_path, result, run.log)
            run.progress.item_done(item_id, result)
            run.results.append(result)

    def _item_result(
        self,
        run: BackendRun,
        item_id: str,
        item_dir: Path,
        sample: Any,
        log: Any,
        staging: Path,
        required_scorers: list[str],
        block: dict[str, Any],
        log_relative: str,
    ) -> dict[str, Any]:
        def failed(message: str, kind: str, retryable: bool = True) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "item_id": item_id,
                "state": "failed",
                "attempts": 1,
                "error": {"type": kind, "message": run.log.safe_text(message)[:TEXT_LIMIT], "retryable": retryable},
                "inspect": {"log": log_relative, "log_status": log.status},
            }

        if sample is None:
            return failed(
                f"no sample in the Inspect log (log status {log.status}); the run was cancelled "
                "or failed before this item",
                "MissingSample",
            )
        if sample.error is not None:
            return failed(str(sample.error.message), "SampleError")
        pass_at = _scorer_pass_at(run.context.evaluation)
        scores = {
            name: _score_record(score, pass_at.get(name, 1.0))
            for name, score in (sample.scores or {}).items()
        }
        missing_scorers = [name for name in required_scorers if name not in scores]
        if missing_scorers:
            return failed(
                f"scorer(s) missing from the sample: {', '.join(missing_scorers)}",
                "MissingScore",
                retryable=False,
            )
        artifacts, rejected, missing = _ingest_artifacts(
            staging / item_id, item_dir, run.context.eval_dir, block.get("artifacts", {})
        )
        usage = _usage(sample)
        cost = None
        if usage:
            cost = estimate_cost(run.spec.model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        limit = getattr(sample, "limit", None)
        result = {
            "schema_version": 1,
            "item_id": item_id,
            "state": "complete",
            "attempts": 1,
            "mechanical_scores": scores,
            "artifacts": artifacts,
            "missing_artifacts": missing,
            "rejected_artifacts": rejected,
            "generation": {
                "provider_request_id": None,
                "latency_ms": int(sample.total_time * 1000) if sample.total_time is not None else None,
                "usage": usage,
                "cost_usd": cost,
                "response_metadata": {},
            },
            "inspect": {
                "log": log_relative,
                "log_status": log.status,
                "stop_reason": _stop_reason(sample),
                "limit": {"type": limit.type, "limit": limit.limit} if limit is not None else None,
            },
        }
        adapt = getattr(task_for(run.context.evaluation, run.context.case), "adapt_ingested_result", None)
        if adapt is not None:
            adapt(run.context, run.selected_item(item_id), item_dir, result)
        return result


def _stop_reason(sample: Any) -> str | None:
    output = getattr(sample, "output", None)
    choices = getattr(output, "choices", None) or []
    return str(choices[0].stop_reason) if choices else None


def _scorer_names(evaluation: dict[str, Any]) -> list[str]:
    from ..tasks.base import parse_mechanical_entry

    names = []
    for entry in evaluation.get("evaluation", {}).get("mechanical", []) or []:
        parsed = parse_mechanical_entry(entry)
        if parsed:
            names.append(parsed[0])
    return names


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _scorer_pass_at(evaluation: dict[str, Any]) -> dict[str, float]:
    """Per-scorer pass threshold from `mechanical: [{name: {pass_at: 0.8}}]` (default 1.0)."""
    from ..tasks.base import parse_mechanical_entry

    thresholds: dict[str, float] = {}
    for entry in evaluation.get("evaluation", {}).get("mechanical", []) or []:
        parsed = parse_mechanical_entry(entry)
        if parsed:
            thresholds[parsed[0]] = float(parsed[1].get("pass_at", 1.0))
    return thresholds


def _passes(value: Any, pass_at: float) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) >= pass_at
    return value == "C"  # Inspect's CORRECT


def _score_record(score: Any, pass_at: float = 1.0) -> dict[str, Any]:
    metadata = _json_safe(score.metadata) if score.metadata else None
    if metadata is not None and len(json.dumps(metadata)) > 8192:
        metadata = {"truncated": True}
    return {
        "value": _json_safe(score.value),
        "pass": _passes(score.value, pass_at),
        "answer": score.answer if isinstance(score.answer, str) else None,
        "explanation": (score.explanation or "")[:TEXT_LIMIT] or None,
        "metadata": metadata,
    }


def _usage(sample: Any) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}
    for entry in (sample.model_usage or {}).values():
        totals["input_tokens"] += entry.input_tokens or 0
        totals["output_tokens"] += entry.output_tokens or 0
        totals["total_tokens"] += entry.total_tokens or 0
        totals["reasoning_tokens"] += entry.reasoning_tokens or 0
    return totals if totals["total_tokens"] or totals["input_tokens"] else {}


def _ingest_artifacts(
    stage: Path, item_dir: Path, eval_dir: Path, policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """Validate and copy staged artifacts. Unsafe or undeclared entries are rejected one by one."""
    from ..inspect_support import FILES_DIR, MANIFEST_NAME

    required = list(policy.get("required", []))
    allowed = set(policy.get("allowed", [])) | set(required)
    max_bytes = int(policy.get("max_bytes", DEFAULT_MAX_ARTIFACT_BYTES))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    manifest_path = stage / MANIFEST_NAME
    entries: list[Any] = []
    if manifest_path.is_file():
        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejected.append({"name": MANIFEST_NAME, "reason": "manifest is not valid JSON"})
    if not isinstance(entries, list):
        rejected.append({"name": MANIFEST_NAME, "reason": "manifest must be a list"})
        entries = []
    root = (stage / FILES_DIR).resolve()
    seen: set[str] = set()
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        label = name if isinstance(name, str) else repr(entry)[:80]
        reason = _artifact_rejection(entry, root, allowed, seen, len(accepted), max_bytes)
        if reason:
            rejected.append({"name": label, "reason": reason})
            continue
        seen.add(name)
        source = root / name
        target = item_dir / "artifacts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        accepted.append(
            {
                "role": entry["role"],
                "path": target.relative_to(eval_dir).as_posix(),
                "sha256": digest_file(target),
                "media_type": entry["media_type"],
                "size": target.stat().st_size,
            }
        )
    present = {artifact["role"] for artifact in accepted}
    return accepted, rejected, [role for role in required if role not in present]


def _artifact_rejection(
    entry: Any, root: Path, allowed: set[str], seen: set[str], count: int, max_bytes: int
) -> str | None:
    from pathlib import PurePosixPath

    if not isinstance(entry, dict):
        return "entry is not a mapping"
    role, name, media_type = entry.get("role"), entry.get("name"), entry.get("media_type")
    if not isinstance(role, str) or role not in allowed:
        return f"role {role!r} is not declared in eval.execution.artifacts"
    if not isinstance(media_type, str) or not MEDIA_TYPE.fullmatch(media_type):
        return "invalid media type"
    if not isinstance(name, str):
        return "name must be a string"
    posix = PurePosixPath(name)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts or "\\" in name:
        return "unsafe path"
    if name in seen:
        return "duplicate name"
    if count >= MAX_ARTIFACTS_PER_ITEM:
        return "too many artifacts"
    candidate = root.joinpath(*posix.parts)
    walk = root
    for part in posix.parts:
        walk = walk / part
        if walk.is_symlink():
            return "symlink in path"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return "file is missing or escapes the staging directory"
    if not resolved.is_file():
        return "not a regular file"
    if resolved.stat().st_size > max_bytes:
        return f"larger than {max_bytes} bytes"
    return None


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def verify_logs(run_dir: Path, eval_dir: Path) -> list[str]:
    """Digest mismatches between recorded Inspect logs and the files on disk (stale evidence)."""
    lineage = run_dir / "inspect" / "lineage.yml"
    if not lineage.is_file():
        return []
    problems = []
    for entry in load_yaml(lineage).get("logs", []):
        path = eval_dir / entry["path"]
        if not path.is_file():
            problems.append(f"{entry['path']}: missing")
        elif digest_file(path) != entry["sha256"]:
            problems.append(f"{entry['path']}: digest changed since ingestion")
    return problems
