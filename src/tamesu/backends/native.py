from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..errors import ExecutionError
from ..models import EvalContext, RunSpec
from ..providers import get_provider as _default_get_provider
from . import BackendRun


class NativeBackend:
    """Tamesu's per-item provider loop with threaded item workers."""

    name = "native"

    def validate_eval(
        self, evaluation: dict[str, Any], eval_dir: Path, project_root: Path, errors: list[str]
    ) -> None:
        if evaluation.get("execution"):
            errors.append("eval.execution is only valid with backend: inspect")

    def check_ready(self, context: EvalContext, specs: list[RunSpec], *, trust_code: bool) -> None:
        for provider_name in sorted({spec.provider for spec in specs}):
            error = _get_provider(provider_name).credential_error()
            if error:
                raise ExecutionError(f"{provider_name}: {error}")

    def max_attempts(self, spec: RunSpec) -> int:
        return spec.retries + 1

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
        return {}, []

    def estimate_item_cost(
        self, task: Any, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        return task.estimate_item_cost(context, spec, item)

    def run_manifest_extra(self, context: EvalContext, spec: RunSpec, run_dir: Path) -> dict[str, Any]:
        return {}

    def execute(self, run: BackendRun) -> None:
        from .. import runner

        provider = _get_provider(run.spec.provider)

        def work(item_id: str, item: dict[str, Any]) -> dict[str, Any]:
            result = runner._process_item(
                run.context, run.spec, run.run_id, run.run_dir, run.log, provider, item_id, item
            )
            run.progress.item_done(item_id, result)
            return result

        if run.spec.concurrency == 1 or len(run.selected) <= 1:
            for item_id, item in run.selected:
                run.results.append(work(item_id, item))
            return
        with ThreadPoolExecutor(
            max_workers=min(run.spec.concurrency, len(run.selected))
        ) as executor:
            futures = {
                executor.submit(work, item_id, item): item_id for item_id, item in run.selected
            }
            for future in as_completed(futures):
                run.results.append(future.result())


def _get_provider(name: str) -> Any:
    """Resolve through tamesu.runner so provider substitution there keeps working."""
    from .. import runner

    return getattr(runner, "get_provider", _default_get_provider)(name)
