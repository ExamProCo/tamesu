"""Execution backends: how a run's items are executed.

A task decides what an output means (artifacts, scores, reports, review). A backend decides
how the output is produced. `native` is Tamesu's per-item provider loop; `inspect` runs one
frozen Inspect AI evaluation and ingests its log as Tamesu evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..logging import CallLogWriter
from ..models import EvalContext, RunSpec


@dataclass
class BackendRun:
    """Everything a backend needs to execute one run."""

    context: EvalContext
    spec: RunSpec
    run_id: str
    run_dir: Path
    log: CallLogWriter
    selected: list[tuple[str, dict[str, Any]]]
    progress: Any
    resumed: bool = False
    results: list[dict[str, Any]] = field(default_factory=list)

    def selected_item(self, item_id: str) -> dict[str, Any]:
        return dict(self.selected)[item_id]


class Backend(Protocol):
    name: str

    def validate_eval(
        self, evaluation: dict[str, Any], eval_dir: Path, project_root: Path, errors: list[str]
    ) -> None: ...

    def check_ready(self, context: EvalContext, specs: list[RunSpec], *, trust_code: bool) -> None:
        """Raise ExecutionError when the run cannot start safely (credentials, tooling, trust)."""

    def max_attempts(self, spec: RunSpec) -> int:
        """Worst-case executions of one item, used to size budget exposure."""

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
        """(extra specification fields, extra files whose digests are part of content identity)."""

    def estimate_item_cost(
        self, task: Any, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None: ...

    def run_manifest_extra(self, context: EvalContext, spec: RunSpec, run_dir: Path) -> dict[str, Any]:
        """Backend-specific fields recorded under `execution` in run.yml."""

    def execute(self, run: BackendRun) -> None:
        """Execute every selected item, appending one result mapping per item to run.results.

        Results are appended as they arrive so an interrupt or failure keeps what finished.
        """


def backend_name(evaluation: dict[str, Any]) -> str:
    return str((evaluation.get("execution") or {}).get("backend", "native"))


def get_backend(name: str) -> Backend:
    if name == "native":
        from .native import NativeBackend

        return NativeBackend()
    if name == "inspect":
        from .inspect_backend import InspectBackend

        return InspectBackend()
    raise ValueError(f"Unsupported execution backend: {name}")


def backend_for(evaluation: dict[str, Any]) -> Backend:
    return get_backend(backend_name(evaluation))


SUPPORTED_BACKENDS = ("native", "inspect")
