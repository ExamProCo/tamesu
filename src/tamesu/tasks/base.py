from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..models import EvalContext, RunSpec


@dataclass(frozen=True)
class PreparedItem:
    """A rendered, task-specific request for one dataset item."""

    payload: Any
    request_metadata: dict[str, Any]
    prompt_hashes: dict[str, str]


@dataclass(frozen=True)
class Materialized:
    """Result fields and artifacts produced from one provider response."""

    result_fields: dict[str, Any]
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TaskValidation:
    output_schema_path: Path | None = None
    output_schema: dict[str, Any] | None = None


def parse_mechanical_entry(entry: Any) -> tuple[str, dict[str, Any]] | None:
    """A mechanical scorer is a bare name or a single-key mapping of name to parameters."""
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict) and len(entry) == 1:
        name, params = next(iter(entry.items()))
        if isinstance(name, str) and (params is None or isinstance(params, dict)):
            return name, dict(params or {})
    return None


class Task(Protocol):
    name: str
    prompt_roles: tuple[str, ...]
    required_capabilities: frozenset[str]
    mechanical_scorers: frozenset[str]
    built_in_metrics: frozenset[str]
    supports_judges: bool
    supports_review: bool

    def code_files(self) -> tuple[Path, ...]:
        """Source files whose contents affect run identity."""

    def validate_eval(
        self,
        evaluation: dict[str, Any],
        eval_dir: Path,
        project_root: Path,
        errors: list[str],
    ) -> TaskValidation: ...

    def validate_mechanical(
        self, name: str, params: dict[str, Any], location: str, errors: list[str]
    ) -> None: ...

    def validate_run_parameters(
        self, provider: str, model: str, parameters: dict[str, Any], location: str, errors: list[str]
    ) -> None: ...

    def identity_evaluation(self, evaluation_block: dict[str, Any]) -> dict[str, Any]:
        """The part of the eval block that is part of a generation run's identity."""

    def render_prompts(self, prompts: dict[str, Path], item: dict[str, Any]) -> dict[str, str]: ...

    def prepare(self, context: EvalContext, spec: RunSpec, item: dict[str, Any]) -> PreparedItem: ...

    def call_provider(
        self, provider: Any, context: EvalContext, spec: RunSpec, prepared: PreparedItem
    ) -> Any: ...

    def materialize(
        self,
        context: EvalContext,
        spec: RunSpec,
        item: dict[str, Any],
        item_dir: Path,
        response: Any,
    ) -> Materialized: ...

    def estimate_item_cost(
        self, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        """(expected, maximum) per attempt in USD, or None when the price is unknown."""

    def build_report(
        self, context: EvalContext, run_dir: Path, run_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Write and return the run's report.yml from stored evidence."""

    def rescore_run(self, context: EvalContext, run_dir: Path) -> dict[str, Any]:
        """Recompute scores and the report from stored evidence; never calls a provider."""
