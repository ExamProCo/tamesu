from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .models import Plan, RunSpec
from .planner import items_by_id
from .providers.models import estimate_cost, max_tokens_for, registered
from .tasks.structured_text import render_prompts


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
    schema_text = json.dumps(plan.context.output_schema or {}, separators=(",", ":"))
    estimated = 0.0
    maximum = 0.0
    unknown: set[str] = set()

    for spec in selected:
        model_spec = registered(spec.model)
        if (
            model_spec is None
            or model_spec.input_usd_per_million is None
            or model_spec.output_usd_per_million is None
        ):
            unknown.add(spec.model)
            continue
        token_cap = int(
            spec.parameters.get(
                "max_tokens",
                spec.parameters.get("max_output_tokens", max_tokens_for(spec.model)),
            )
        )
        attempts = spec.retries + 1
        for item_id in spec.item_ids:
            item = item_map[item_id]
            system_prompt, user_prompt = render_prompts(spec.prompts, item)
            input_tokens = _token_estimate(system_prompt + user_prompt + schema_text)
            expected_text = json.dumps(item.get("expected", {}), separators=(",", ":"))
            expected_output_tokens = max(32, _token_estimate(expected_text) * 2)
            estimated_call = estimate_cost(
                spec.model, input_tokens, min(token_cap, expected_output_tokens)
            )
            maximum_call = estimate_cost(spec.model, input_tokens, token_cap)
            assert estimated_call is not None and maximum_call is not None
            estimated += estimated_call
            maximum += maximum_call * attempts

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


def _token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))
