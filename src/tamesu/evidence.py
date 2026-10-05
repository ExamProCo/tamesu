"""Shared selection of stored image evidence that later stages (judging, review) may act on."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_yaml
from .discovery import find_run_dir
from .errors import ExecutionError
from .identity import digest_value
from .models import EvalContext
from .planner import build_plan, items_by_id
from .tasks.image_generation import OUTCOME_GENERATED, is_stale, prerequisites_met


@dataclass(frozen=True)
class RunItem:
    run_id: str
    run_dir: Path
    item_id: str
    item: dict[str, Any]
    item_dir: Path
    result: dict[str, Any]

    @property
    def image_path(self) -> Path:
        return self.item_dir / self.result["output"]["path"]

    @property
    def image_sha256(self) -> str:
        return self.result["output"]["sha256"]

    @property
    def media_type(self) -> str:
        return self.result["output"]["media_type"]


@dataclass
class Selection:
    items: list[RunItem] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)  # run, item, reason


def resolve_run_dirs(context: EvalContext, run_id: str | None) -> list[Path]:
    if run_id is not None:
        run_dir = find_run_dir(context.project_root, run_id)
        if load_yaml(run_dir / "run.yml").get("eval_id") != context.eval_id:
            raise ExecutionError(f"Run {run_id} does not belong to {context.eval_id}.")
        return [run_dir]
    plan = build_plan(context)
    run_dirs = [context.eval_dir / "runs" / banked for banked in sorted(plan.banked_run_ids.values())]
    if not run_dirs:
        raise ExecutionError("No banked runs found. Pass --run to act on a specific run.")
    return run_dirs


def select_generated_items(
    context: EvalContext,
    run_id: str | None = None,
    *,
    limit_per_run: int | None = None,
    honor_prerequisites: bool = False,
) -> Selection:
    """Generated images that are current: present, unmodified, and from an unchanged dataset item."""
    selection = Selection()
    item_map = items_by_id(context)
    for run_dir in resolve_run_dirs(context, run_id):
        manifest = load_yaml(run_dir / "run.yml")
        taken = 0
        for item_id in manifest.get("selection", {}).get("item_ids", []):
            if limit_per_run is not None and taken >= limit_per_run:
                break
            item_dir = run_dir / "items" / item_id
            result_path = item_dir / "result.yml"
            reason: str | None = None
            result: dict[str, Any] = {}
            if not result_path.is_file():
                reason = "no result"
            else:
                result = load_yaml(result_path)
                if (result.get("outcome") or {}).get("status") != OUTCOME_GENERATED:
                    reason = "no generated image"
                elif is_stale(item_dir, result):
                    reason = "stale image"
                elif item_id not in item_map or result.get("prompt", {}).get(
                    "input_sha256"
                ) != digest_value(item_map[item_id].get("input", {})):
                    reason = "dataset item changed since generation"
                elif honor_prerequisites and not prerequisites_met(result):
                    reason = "failed a prerequisite mechanical check"
            if reason:
                selection.skipped.append((run_dir.name, item_id, reason))
                continue
            taken += 1
            selection.items.append(
                RunItem(run_dir.name, run_dir, item_id, item_map[item_id], item_dir, result)
            )
    return selection
