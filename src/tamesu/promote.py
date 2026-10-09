"""Promotion: copy accepted images into a new, versioned downstream dataset with provenance.

Existing dataset versions are frozen by contract, so promotion only ever creates a new
directory. Products with no accepted image are listed, never dropped silently: they are a
finding about the pipeline that the downstream experiment must handle.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acceptance import run_evidence
from .artifacts import write_yaml
from .config import ID_PATTERN, load_yaml
from .errors import ExecutionError
from .identity import digest_file
from .models import EvalContext
from .planner import build_plan, items_by_id
from .tasks import supports_promotion, task_for

DESTINATION_PATTERN = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)/([a-z0-9]+(?:-[a-z0-9]+)*)$")


@dataclass
class PromotionPlan:
    destination: Path
    dataset: dict[str, Any]
    copies: list[tuple[Path, Path]] = field(default_factory=list)
    excluded: list[tuple[str, str, str]] = field(default_factory=list)  # item, run, reason
    unpromoted_products: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)


def plan_promotion(context: EvalContext, to: str) -> PromotionPlan:
    if not supports_promotion(task_for(context.evaluation, context.case)):
        raise ExecutionError(f"Task of {context.eval_id} has no accepted-image output to promote.")
    if context.evaluation["status"] != "complete":
        raise ExecutionError(
            f"Cannot promote {context.eval_id}: the eval is {context.evaluation['status']!r}; "
            "close it first so acceptance is final."
        )
    match = DESTINATION_PATTERN.fullmatch(to)
    if match is None:
        raise ExecutionError("--to must look like <case>/<new-dataset-name> (lowercase kebab-case).")
    case_name, dataset_name = match.groups()
    case_dir = context.project_root / "cases" / case_name
    if not (case_dir / "case.yml").is_file():
        raise ExecutionError(f"Destination case does not exist: cases/{case_name}")
    destination = case_dir / "datasets" / dataset_name
    if destination.exists():
        raise ExecutionError(
            f"{destination.relative_to(context.project_root)} already exists. Dataset versions are "
            "frozen: promote into a new version name instead."
        )

    plan = build_plan(context)
    if plan.owed_specs or not plan.banked_run_ids:
        raise ExecutionError("Promotion needs every planned run banked.")
    runs = sorted(plan.banked_run_ids.values())
    evidence_policy: dict[str, Any] = {}
    item_map = items_by_id(context)
    accepted: dict[str, list[dict[str, Any]]] = {item_id: [] for item_id in item_map}
    result = PromotionPlan(destination=destination, dataset={})

    for run_id in runs:
        run_dir = context.eval_dir / "runs" / run_id
        evidence = run_evidence(context, run_dir)
        evidence_policy = evidence["policy"]
        manifest = load_yaml(run_dir / "run.yml")
        for entry in evidence["items"]:
            item_id = entry["item_id"]
            state = entry["acceptance"]["state"]
            if state != "accepted":
                reason = ", ".join(entry["acceptance"]["reasons"] or entry["acceptance"]["awaiting"]) or state
                result.excluded.append((item_id, run_id, f"{state}: {reason}"))
                continue
            item_dir = run_dir / "items" / item_id
            image = load_yaml(item_dir / "result.yml")["output"]
            source = item_dir / image["path"]
            if not source.is_file() or digest_file(source) != image["sha256"]:
                raise ExecutionError(f"Accepted image changed on disk; refusing to promote: {run_id}/{item_id}")
            accepted[item_id].append(
                {"run_id": run_id, "manifest": manifest, "evidence": entry, "image": image, "source": source,
                 "item_dir": item_dir}
            )

    selection = evidence_policy.get("selection", "none")
    entries: list[dict[str, Any]] = []
    for item_id, candidates in accepted.items():
        if not candidates:
            result.unpromoted_products.append(item_id)
            continue
        chosen = candidates[:1] if selection == "first_pass" else candidates
        for candidate in chosen:
            multiple = len(chosen) > 1
            label = candidate["manifest"]["label"].replace("--", "-")
            entry_id = f"{item_id}-{label}" if multiple else item_id
            if not ID_PATTERN.fullmatch(entry_id):
                raise ExecutionError(f"Cannot derive a valid dataset item ID from {entry_id!r}")
            filename = f"{entry_id}{candidate['source'].suffix}"
            if entry_id in result.promoted:
                raise ExecutionError(f"Two accepted images would share the dataset item ID {entry_id!r}")
            result.copies.append((candidate["source"], destination / "images" / filename))
            result.promoted.append(entry_id)
            entries.append(_dataset_entry(context, entry_id, item_map[item_id], candidate, filename, evidence_policy))
        for skipped in candidates[len(chosen):]:
            result.excluded.append((item_id, skipped["run_id"], "accepted, but not selected under selection: first_pass"))

    if not entries:
        raise ExecutionError(
            "No accepted images to promote. Products without one: " + ", ".join(result.unpromoted_products)
        )
    result.dataset = {
        "schema_version": 1,
        "name": match.group(2),
        "description": (
            f"Accepted images promoted from {context.eval_id}. Every entry records its source run, "
            "image checksum, generator, prompt fingerprint, and review decisions."
        ),
        "items": entries,
    }
    return result


def _dataset_entry(
    context: EvalContext,
    entry_id: str,
    item: dict[str, Any],
    candidate: dict[str, Any],
    filename: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    manifest, evidence = candidate["manifest"], candidate["evidence"]
    resolved = manifest["resolved"]
    provider_response = candidate["item_dir"] / "provider_response.yml"
    returned_model = None
    if provider_response.is_file():
        returned_model = load_yaml(provider_response).get("response_metadata", {}).get("model")
    result = load_yaml(candidate["item_dir"] / "result.yml")
    block = context.evaluation["evaluation"]
    return {
        "id": entry_id,
        "input": dict(item.get("input", {})),
        "assets": {"reference_image": [f"images/{filename}"]},
        "expected": None,
        "metadata": {
            "source": {
                "eval": context.eval_id,
                "run_id": candidate["run_id"],
                "item_id": evidence["item_id"],
            },
            "image_sha256": candidate["image"]["sha256"],
            "generator": {
                "provider": resolved["provider"],
                "model": resolved["model"],
                "returned_model": returned_model,
            },
            "prompt_fingerprint": result["prompt"]["prompt_sha256"],
            "run_specification_fingerprint": manifest["identity"]["specification_fingerprint"],
            "acceptance": {"requires": policy["requires"], "selection": policy["selection"]},
            "review": [dict(review) for review in evidence["human"]["reviews"]],
            "model_judge": [dict(entry) for entry in evidence["model_judge"]["judgments"]],
            "has_human_review": bool(block.get("human_review")),
        },
    }


def execute_promotion(context: EvalContext, plan: PromotionPlan) -> None:
    if plan.destination.exists():
        raise ExecutionError(f"{plan.destination} already exists; dataset versions are frozen.")
    staging = plan.destination.with_name(f".{plan.destination.name}.promoting")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        (staging / "images").mkdir(parents=True)
        for source, target in plan.copies:
            copy = staging / "images" / target.name
            shutil.copyfile(source, copy)
            if digest_file(copy) != digest_file(source):
                raise ExecutionError(f"Checksum mismatch while copying {source.name}")
        write_yaml(staging / "dataset.yml", plan.dataset)
        write_yaml(
            staging / "promotion.yml",
            {
                "schema_version": 1,
                "eval": context.eval_id,
                "promoted": plan.promoted,
                "excluded": [{"item": i, "run": r, "reason": why} for i, r, why in plan.excluded],
                "products_without_accepted_image": plan.unpromoted_products,
            },
        )
        staging.rename(plan.destination)  # a half-written dataset version never appears
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
