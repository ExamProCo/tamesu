from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalContext:
    project_root: Path
    case_dir: Path
    experiment_dir: Path
    eval_dir: Path
    eval_id: str
    case: dict[str, Any]
    dataset: dict[str, Any]
    evaluation: dict[str, Any]
    dataset_path: Path
    output_schema: dict[str, Any] | None = None
    output_schema_path: Path | None = None


@dataclass(frozen=True)
class RunSpec:
    eval_id: str
    label: str
    provider: str
    model: str
    arm_id: str
    repetition: int
    item_ids: tuple[str, ...]
    prompts: dict[str, Path]
    parameters: dict[str, Any]
    timeout_seconds: int
    retries: int
    concurrency: int
    budget_usd: float
    specification_fingerprint: str
    content_fingerprint: str
    content_inventory: dict[str, str] = field(default_factory=dict)
    probe: bool = False


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    request_id: str | None
    usage: dict[str, Any]
    cost_usd: float | None
    response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    context: EvalContext
    specs: tuple[RunSpec, ...]
    banked_run_ids: dict[str, str]
    stale_run_ids: tuple[str, ...]
    partial_run_ids: tuple[str, ...]

    @property
    def total_items(self) -> int:
        return sum(len(spec.item_ids) for spec in self.specs)

    @property
    def owed_specs(self) -> tuple[RunSpec, ...]:
        return tuple(
            spec for spec in self.specs if spec.specification_fingerprint not in self.banked_run_ids
        )
