from __future__ import annotations

from dataclasses import dataclass

from .models import Plan, RunSpec
from .planner import items_by_id
from .tasks import task_for


@dataclass(frozen=True)
class PlanCost:
    estimated_usd: float
    maximum_usd: float
    unknown_models: tuple[str, ...]

    @property
    def fully_priced(self) -> bool:
        return not self.unknown_models


@dataclass(frozen=True)
class RecordedCost:
    known_usd: float
    unknown_runs: int


def estimate_plan_cost(plan: Plan, specs: tuple[RunSpec, ...] | None = None) -> PlanCost:
    selected = plan.owed_specs if specs is None else specs
    item_map = items_by_id(plan.context)
    task = task_for(plan.context.evaluation, plan.context.case)
    estimated = 0.0
    maximum = 0.0
    unknown: set[str] = set()

    for spec in selected:
        attempts = spec.retries + 1
        spec_estimated = 0.0
        spec_maximum = 0.0
        for item_id in spec.item_ids:
            per_call = task.estimate_item_cost(plan.context, spec, item_map[item_id])
            if per_call is None:
                unknown.add(spec.model)
                break
            spec_estimated += per_call[0]
            spec_maximum += per_call[1] * attempts
        else:
            estimated += spec_estimated
            maximum += spec_maximum

    return PlanCost(
        estimated_usd=round(estimated, 6),
        maximum_usd=round(maximum, 6),
        unknown_models=tuple(sorted(unknown)),
    )


def recorded_cost(plan: Plan) -> RecordedCost:
    known = 0.0
    unknown = 0
    runs_dir = plan.context.eval_dir / "runs"
    if not runs_dir.is_dir():
        return RecordedCost(known_usd=0.0, unknown_runs=0)
    from .config import load_yaml

    for path in runs_dir.glob("*/run.yml"):
        try:
            value = load_yaml(path).get("totals", {}).get("cost_usd")
        except Exception:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            known += float(value)
        else:
            unknown += 1
    return RecordedCost(known_usd=round(known, 6), unknown_runs=unknown)
