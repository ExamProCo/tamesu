from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_yaml, resolve_contained
from .identity import digest_value, inventory_files
from .models import EvalContext, Plan, RunSpec
from .backends import backend_for
from .tasks import get_task


def build_plan(context: EvalContext) -> Plan:
    evaluation = context.evaluation
    defaults = evaluation["defaults"]
    items = evaluation_items(context)
    item_ids = tuple(item["id"] for item in items)
    arm_by_id = {arm["id"]: arm for arm in evaluation["arms"]}

    specs: list[RunSpec] = []
    for run in evaluation["runs"]:
        repetitions = run.get("repetitions", defaults["repetitions"])
        for arm_id in run["arms"]:
            arm = arm_by_id[arm_id]
            prompt_paths = {
                role: resolve_contained(context.eval_dir, raw_path, context.project_root)
                for role, raw_path in arm["prompts"].items()
            }
            parameters = dict(defaults.get("parameters", {}))
            parameters.update(arm.get("parameters", {}))
            parameters.update(run.get("parameters", {}))
            for repetition in range(1, repetitions + 1):
                specs.append(
                    make_run_spec(
                        context,
                        provider=run["provider"],
                        model=run["model"],
                        arm_id=arm_id,
                        arm=arm,
                        repetition=repetition,
                        item_ids=item_ids,
                        prompts=prompt_paths,
                        parameters=parameters,
                        probe=False,
                    )
                )

    manifests = read_run_manifests(context.eval_dir)
    banked: dict[str, str] = {}
    stale: list[str] = []
    partial: list[str] = []
    by_specification = {spec.specification_fingerprint: spec for spec in specs}
    labels = {spec.label for spec in specs}
    for manifest in manifests:
        identity = manifest.get("identity", {})
        specification = identity.get("specification_fingerprint")
        content = identity.get("content_fingerprint")
        state = manifest.get("state")
        run_id = str(manifest.get("run_id", "unknown"))
        spec = by_specification.get(specification)
        if manifest.get("probe") and state in {"planned", "running", "partial"}:
            partial.append(run_id)
        elif spec and _content_matches(manifest, spec, context):
            if state == "complete" and not manifest.get("probe", False):
                banked.setdefault(specification, run_id)
            elif state in {"planned", "running", "partial"}:
                partial.append(run_id)
        elif manifest.get("label") in labels:
            stale.append(run_id)

    return Plan(
        context=context,
        specs=tuple(specs),
        banked_run_ids=banked,
        stale_run_ids=tuple(sorted(stale)),
        partial_run_ids=tuple(sorted(partial)),
    )


def make_run_spec(
    context: EvalContext,
    *,
    provider: str,
    model: str,
    arm_id: str,
    arm: dict[str, Any],
    repetition: int,
    item_ids: tuple[str, ...],
    prompts: dict[str, Path],
    parameters: dict[str, Any],
    probe: bool,
) -> RunSpec:
    defaults = context.evaluation["defaults"]
    label = f"{_slug(model)}--{arm_id}--rep{repetition}"
    specification_task = context.evaluation.get("task", context.case.get("default_task"))
    specification_payload = {
        "schema_version": 1,
        "eval_id": context.eval_id,
        "task": specification_task,
        "provider": provider,
        "model": model,
        "arm": arm_id,
        "repetition": repetition,
        "item_ids": list(item_ids),
        "parameters": parameters,
        "timeout_seconds": defaults["timeout_seconds"],
        "retries": defaults["retries"],
        "evaluation": get_task(specification_task).identity_evaluation(
            context.evaluation.get("evaluation", {})
        ),
        "metrics": context.evaluation.get("metrics", {}),
        "probe": probe,
    }
    backend_fields, backend_files = backend_for(context.evaluation).identity(
        context,
        provider=provider,
        model=model,
        prompts=prompts,
        item_ids=item_ids,
        parameters=parameters,
    )
    specification_payload.update(backend_fields)
    execution_paths = [
        *prompts.values(),
        Path(__file__).with_name("scoring.py"),
        *get_task(specification_task).code_files(),
        *backend_files,
    ]
    if context.output_schema_path:
        execution_paths.append(context.output_schema_path)
    execution_inventory = inventory_files(execution_paths, context.project_root)
    inventory = inventory_files(
        [
            context.case_dir / "case.yml",
            context.dataset_path,
            *execution_paths,
        ],
        context.project_root,
    )
    content_payload = {
        "schema_version": 2,
        "framework_version": __version__,
        "dataset": {
            key: value
            for key, value in context.dataset.items()
            if key != "description"
        },
        "inventory": execution_inventory,
    }
    return RunSpec(
        eval_id=context.eval_id,
        label=label,
        provider=provider,
        model=model,
        arm_id=arm_id,
        repetition=repetition,
        item_ids=item_ids,
        prompts=prompts,
        parameters=parameters,
        timeout_seconds=defaults["timeout_seconds"],
        retries=defaults["retries"],
        concurrency=defaults["concurrency"],
        budget_usd=float(defaults["budget_usd"]),
        specification_fingerprint=digest_value(specification_payload),
        content_fingerprint=digest_value(content_payload),
        content_inventory=inventory,
        probe=probe,
    )


def limit_spec(spec: RunSpec, limit: int) -> RunSpec:
    limited_items = spec.item_ids[:limit]
    fingerprint = digest_value(
        {
            "original_specification_fingerprint": spec.specification_fingerprint,
            "item_ids": list(limited_items),
            "probe": True,
        }
    )
    return replace(
        spec,
        item_ids=limited_items,
        specification_fingerprint=fingerprint,
        probe=True,
    )


def evaluation_items(context: EvalContext) -> list[dict[str, Any]]:
    return [item for item in context.dataset["items"] if isinstance(item, dict)]


def items_by_id(context: EvalContext) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in evaluation_items(context)}


def read_run_manifests(eval_dir: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    runs_dir = eval_dir / "runs"
    if not runs_dir.is_dir():
        return manifests
    for path in sorted(runs_dir.glob("*/run.yml")):
        try:
            manifests.append(load_yaml(path))
        except Exception:
            continue
    return manifests


def _content_matches(
    manifest: dict[str, Any], spec: RunSpec, context: EvalContext
) -> bool:
    identity = manifest.get("identity", {})
    if identity.get("content_fingerprint") == spec.content_fingerprint:
        return True

    # Runs written before content fingerprint schema 2 hashed the complete case and
    # dataset files. Preserve those immutable runs when the only changed file is
    # case.yml and every execution input still has its recorded digest. The resolved
    # task is checked explicitly because it is the only case field used at execution.
    stored_inventory = identity.get("content_inventory")
    if not isinstance(stored_inventory, dict):
        return False
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("tamesu_version") != __version__:
        return False
    resolved = manifest.get("resolved")
    if not isinstance(resolved, dict):
        return False
    task = context.evaluation.get("task", context.case.get("default_task"))
    if resolved.get("task") != task:
        return False

    try:
        case_label = (
            context.case_dir / "case.yml"
        ).resolve().relative_to(context.project_root.resolve()).as_posix()
    except ValueError:
        case_label = "case.yml"
    stored_execution = {
        key: value for key, value in stored_inventory.items() if key != case_label
    }
    current_execution = {
        key: value for key, value in spec.content_inventory.items() if key != case_label
    }
    return stored_execution == current_execution


def _slug(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in normalized.split("-") if part)
