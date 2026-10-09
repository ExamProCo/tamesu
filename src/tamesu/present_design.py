"""The design of an eval, as plain data: what will run, what each model is sent, how it is
judged, how it executes, what it can cost and what stops it.

Everything here is computed from files. Nothing probes Docker, contacts a provider, or imports
project Python, so it is valid before anything has run.
"""
from __future__ import annotations

import os
from typing import Any

from . import present_blocks as blocks
from .backends import backend_name
from .lifecycle import Lifecycle
from .models import EvalContext, Plan
from .planner import items_by_id
from .policy import resolved_acceptance
from .pricing import estimate_plan_cost, recorded_cost
from .providers.models import PROVIDER_KEYS
from .tasks import task_for
from .tasks.base import parse_mechanical_entry

MAX_DESIGN_ITEMS = 200
PREVIEW_ITEM_LIMIT = 1


def build_design(context: EvalContext, plan: Plan, state: Lifecycle) -> dict[str, Any]:
    evaluation = context.evaluation
    task = task_for(evaluation, context.case)
    return {
        "matrix": _matrix(plan),
        "items": _items(context),
        "prompt_preview": _prompt_preview(context, plan, task),
        "scoring": _scoring(context),
        "execution": _execution(context, plan),
        "cost": _cost(plan),
        "blockers": _blockers(context, plan, state),
    }


def _matrix(plan: Plan) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for spec in plan.specs:
        key = (spec.provider, spec.model, spec.arm_id)
        group = groups.setdefault(
            key,
            {
                "provider": spec.provider,
                "model": spec.model,
                "arm": spec.arm_id,
                "repetitions": 0,
                "items": len(spec.item_ids),
                "banked": 0,
                "owed": 0,
                "parameters": spec.parameters,
            },
        )
        group["repetitions"] += 1
        if spec.specification_fingerprint in plan.banked_run_ids:
            group["banked"] += 1
        else:
            group["owed"] += 1
    return list(groups.values())


def _items(context: EvalContext) -> dict[str, Any]:
    items = [item for item in context.dataset.get("items", []) if isinstance(item, dict)]
    shown = [
        {
            "id": item.get("id"),
            "input": item.get("input"),
            "expected": item.get("expected"),
            "metadata": item.get("metadata"),
        }
        for item in items[:MAX_DESIGN_ITEMS]
    ]
    return {"total": len(items), "shown": shown, "omitted": max(0, len(items) - MAX_DESIGN_ITEMS)}


def _prompt_preview(context: EvalContext, plan: Plan, task: Any) -> list[dict[str, Any]]:
    """The exact text each arm sends for the first item, or the template error that stops it."""
    item_map = items_by_id(context)
    previews: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in plan.specs:
        if spec.arm_id in seen or not spec.item_ids:
            continue
        seen.add(spec.arm_id)
        item_id = spec.item_ids[0]
        entry: dict[str, Any] = {"arm": spec.arm_id, "item_id": item_id}
        try:
            rendered = task.render_prompts(spec.prompts, item_map[item_id])
            entry["prompts"] = {role: blocks.sanitize_text(text) for role, text in rendered.items()}
        except Exception as exc:  # noqa: BLE001 - surfacing a template problem is the point
            entry["error"] = blocks.sanitize_text(f"{type(exc).__name__}: {exc}")
        previews.append(entry)
    return previews


def _scoring(context: EvalContext) -> dict[str, Any]:
    from .rubric import load_rubric

    block = context.evaluation.get("evaluation", {}) or {}
    mechanical = []
    for entry in block.get("mechanical", []) or []:
        parsed = parse_mechanical_entry(entry)
        if parsed:
            mechanical.append({"name": parsed[0], "parameters": parsed[1]})
    scoring: dict[str, Any] = {
        "mechanical": mechanical,
        "metrics": context.evaluation.get("metrics", {}),
        "human_review": None,
        "acceptance": None,
        "model_judges": [
            {"id": judge.get("id"), "role": judge.get("role")}
            for judge in (block.get("model_judges") or [])
            if isinstance(judge, dict)
        ],
    }
    review = block.get("human_review")
    if review:
        try:
            from .config import resolve_contained

            rubric = load_rubric(resolve_contained(context.eval_dir, review["rubric"], context.project_root))
            scoring["human_review"] = {
                "rubric": rubric.name,
                "dimensions": [
                    {"id": d.id, "question": d.question, "reason_codes": list(d.reason_codes)}
                    for d in rubric.dimensions
                ],
            }
        except Exception as exc:  # noqa: BLE001
            scoring["human_review"] = {"error": blocks.sanitize_text(str(exc))}
    if block.get("acceptance") or review:
        acceptance = resolved_acceptance(block)
        scoring["acceptance"] = {
            "requires": list(acceptance.requires),
            "required_reviews_per_item": acceptance.required_reviews_per_item,
            "on_disagreement": acceptance.on_disagreement,
        }
    return scoring


def _execution(context: EvalContext, plan: Plan) -> dict[str, Any]:
    backend = backend_name(context.evaluation)
    execution: dict[str, Any] = {"backend": backend, "routes": []}
    providers = sorted({(spec.provider, spec.model) for spec in plan.specs})
    if backend != "inspect":
        execution["routes"] = [{"provider": p, "model": m} for p, m in providers]
        return execution

    from .backends import inspect_backend as ib

    block = ib.execution_block(context.evaluation)
    sources = ib.declared_sources(context.evaluation, context.eval_dir, context.project_root)
    images = [
        {"source": path.name, "image": image, "pinned": "@sha256:" in image}
        for path, image in ib.image_references(sources)
    ]
    execution.update(
        {
            "inspect_version": ib.installed_inspect_version(),
            "file": block.get("file"),
            "task": block.get("task"),
            "task_args": block.get("task_args", {}),
            "sources": [str(p.relative_to(context.project_root)) for p in sources],
            "limits": block.get("limits", {}),
            "artifacts": block.get("artifacts", {}),
            "images": images,
            "needs_docker": bool(images) or any(p.name.startswith(("compose", "Dockerfile")) for p in sources),
            "not_checked": ["Docker daemon reachable", "inspect-ai installed"],
            "routes": [
                {
                    "provider": p,
                    "model": m,
                    "model_uri": ib.model_uri(p, m) if p in ib.ROUTES else None,
                    "base_url_origin": ib.base_url_origin(p) if p in ib.ROUTES else None,
                }
                for p, m in providers
            ],
        }
    )
    return execution


def _cost(plan: Plan) -> dict[str, Any]:
    full = estimate_plan_cost(plan, plan.specs)
    owed = estimate_plan_cost(plan)
    spent = recorded_cost(plan)
    return {
        "budget_usd": float(plan.context.evaluation["defaults"]["budget_usd"]),
        "design_estimated_usd": full.estimated_usd if full.fully_priced else None,
        "design_maximum_usd": full.maximum_usd if full.fully_priced else None,
        "owed_maximum_usd": owed.maximum_usd if owed.fully_priced else None,
        "unknown_pricing": list(full.unknown_models),
        "recorded_known_usd": spent.known_usd,
    }


def _blockers(context: EvalContext, plan: Plan, state: Lifecycle) -> list[str]:
    """Static blockers only: nothing is probed, so this never needs Docker or a network."""
    found: list[str] = []
    if context.evaluation.get("status") == "draft":
        found.append("eval status is 'draft'; run `tamesu activate` after reviewing the plan")
    for provider in sorted({spec.provider for spec in plan.owed_specs}):
        names = PROVIDER_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))
        if not any(os.environ.get(name) for name in names):
            found.append(f"{' or '.join(names)} is not set in the environment")
    if (context.case_dir / ".untrusted-origin").exists() and backend_name(context.evaluation) == "inspect":
        found.append("case came from a package: its Inspect task needs `tamesu run --trust-code` after review")
    return found
