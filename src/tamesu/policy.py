"""Validation of the post-generation parts of an eval: model judges, human review, acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .providers.models import registered, validate_effort

JUDGE_REQUIRED_CAPABILITIES = frozenset({"image_input", "structured_output"})
JUDGE_PROMPT_ROLES = ("system", "user")


def validate_model_judges(
    judges: Any,
    *,
    defaults: dict[str, Any],
    eval_dir: Path,
    project_root: Path,
    errors: list[str],
) -> None:
    from .config import _positive_int, _validate_id, resolve_contained
    from .rubric import load_rubric

    if not isinstance(judges, list):
        errors.append("eval.evaluation.model_judges must be a list")
        return
    budget = defaults.get("judge_budget_usd")
    if judges and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0):
        errors.append("eval.defaults.judge_budget_usd is required (non-negative) when model_judges are declared")
    seen: set[str] = set()
    for index, judge in enumerate(judges):
        location = f"eval.evaluation.model_judges[{index}]"
        if not isinstance(judge, dict):
            errors.append(f"{location} must be a mapping")
            continue
        for key in sorted(set(judge) - {"id", "provider", "model", "rubric", "prompts", "repetitions", "parameters"}):
            errors.append(f"{location}.{key} is not a recognized field")
        judge_id = judge.get("id")
        if not isinstance(judge_id, str) or not judge_id:
            errors.append(f"{location}.id must be a non-empty string")
        else:
            _validate_id(judge_id, f"{location}.id", errors)
            if judge_id in seen:
                errors.append(f"{location}.id is duplicated: {judge_id}")
            seen.add(judge_id)
        provider, model = judge.get("provider"), judge.get("model")
        if not isinstance(provider, str) or not provider:
            errors.append(f"{location}.provider must be a non-empty string")
        if not isinstance(model, str) or not model:
            errors.append(f"{location}.model must be a non-empty string")
        elif isinstance(provider, str):
            spec = registered(model)
            if spec is None:
                errors.append(f"{location}: judge model {model!r} is not registered")
            else:
                if spec.provider != provider:
                    errors.append(
                        f"{location}: model {model!r} belongs to provider {spec.provider!r}, not {provider!r}"
                    )
                missing = sorted(JUDGE_REQUIRED_CAPABILITIES - spec.capabilities)
                if missing:
                    errors.append(f"{location}: model {model!r} lacks judge capabilities: {', '.join(missing)}")
        if "repetitions" in judge:
            _positive_int(judge["repetitions"], f"{location}.repetitions", errors)
        parameters = judge.get("parameters", {})
        if not isinstance(parameters, dict):
            errors.append(f"{location}.parameters must be a mapping")
        elif isinstance(model, str):
            effort = parameters.get("effort")
            if effort is not None:
                message = validate_effort(model, str(effort))
                if message:
                    errors.append(f"{location}: {message}")
        rubric_path = judge.get("rubric")
        if not isinstance(rubric_path, str) or not rubric_path:
            errors.append(f"{location}.rubric must be a path")
        else:
            try:
                load_rubric(resolve_contained(eval_dir, rubric_path, project_root))
            except ConfigError as exc:
                errors.append(str(exc))
        prompts = judge.get("prompts")
        if not isinstance(prompts, dict):
            errors.append(f"{location}.prompts must be a mapping")
            continue
        for role in JUDGE_PROMPT_ROLES:
            raw = prompts.get(role)
            if not isinstance(raw, str) or not raw:
                errors.append(f"{location}.prompts.{role} must be a path")
                continue
            try:
                resolve_contained(eval_dir, raw, project_root)
            except ConfigError as exc:
                errors.append(str(exc))


# -- human review and acceptance --------------------------------------------------------

REQUIRES = ("mechanical", "model_judge", "human")
ON_DISAGREEMENT = ("adjudicate", "reject")
JUDGE_ROLES = ("screen", "gate")
SELECTIONS = ("none", "first_pass")


@dataclass(frozen=True)
class Acceptance:
    requires: tuple[str, ...]
    required_reviews_per_item: int
    on_disagreement: str
    judge_role: str | None
    selection: str


def resolved_acceptance(evaluation_block: dict[str, Any]) -> Acceptance:
    """The declared acceptance policy, with defaults for evals that declare none."""
    raw = evaluation_block.get("acceptance") or {}
    human = raw.get("human") or {}
    judge = raw.get("model_judge") or {}
    return Acceptance(
        requires=tuple(raw.get("requires", ["mechanical"])),
        required_reviews_per_item=int(human.get("required_reviews_per_item", 1)),
        on_disagreement=str(human.get("on_disagreement", "adjudicate")),
        judge_role=judge.get("role"),
        selection=str(raw.get("selection", "none")),
    )


def validate_human_review(
    human_review: Any, *, eval_dir: Path, project_root: Path, errors: list[str]
) -> None:
    from .config import resolve_contained
    from .rubric import load_rubric

    if not isinstance(human_review, dict):
        errors.append("eval.evaluation.human_review must be a mapping")
        return
    for key in sorted(set(human_review) - {"rubric"}):
        errors.append(f"eval.evaluation.human_review.{key} is not a recognized field")
    raw = human_review.get("rubric")
    if not isinstance(raw, str) or not raw:
        errors.append("eval.evaluation.human_review.rubric must be a path")
        return
    try:
        load_rubric(resolve_contained(eval_dir, raw, project_root))
    except ConfigError as exc:
        errors.append(str(exc))


def validate_acceptance(block: dict[str, Any], errors: list[str]) -> None:
    from .config import _positive_int

    acceptance = block.get("acceptance")
    has_human = bool(block.get("human_review"))
    has_judges = bool(block.get("model_judges"))
    if acceptance is None:
        if has_human:
            errors.append(
                "eval.evaluation.human_review is declared but eval.evaluation.acceptance does not "
                "require human review; declare acceptance.requires including 'human'"
            )
        return
    where = "eval.evaluation.acceptance"
    if not isinstance(acceptance, dict):
        errors.append(f"{where} must be a mapping")
        return
    for key in sorted(set(acceptance) - {"requires", "human", "model_judge", "selection"}):
        errors.append(f"{where}.{key} is not a recognized field")
    requires = acceptance.get("requires")
    if not isinstance(requires, list) or not requires or any(r not in REQUIRES for r in requires):
        errors.append(f"{where}.requires must be a non-empty list drawn from {', '.join(REQUIRES)}")
        requires = []
    if len(set(map(str, requires))) != len(requires):
        errors.append(f"{where}.requires contains duplicates")

    human = acceptance.get("human", {})
    if not isinstance(human, dict):
        errors.append(f"{where}.human must be a mapping")
        human = {}
    for key in sorted(set(human) - {"required_reviews_per_item", "on_disagreement"}):
        errors.append(f"{where}.human.{key} is not a recognized field")
    if "required_reviews_per_item" in human:
        _positive_int(human["required_reviews_per_item"], f"{where}.human.required_reviews_per_item", errors)
    if human.get("on_disagreement", "adjudicate") not in ON_DISAGREEMENT:
        errors.append(f"{where}.human.on_disagreement must be one of {', '.join(ON_DISAGREEMENT)}")

    judge = acceptance.get("model_judge", {})
    if not isinstance(judge, dict):
        errors.append(f"{where}.model_judge must be a mapping")
        judge = {}
    for key in sorted(set(judge) - {"role"}):
        errors.append(f"{where}.model_judge.{key} is not a recognized field")
    role = judge.get("role")
    if role is not None and role not in JUDGE_ROLES:
        errors.append(f"{where}.model_judge.role must be one of {', '.join(JUDGE_ROLES)}")

    if acceptance.get("selection", "none") not in SELECTIONS:
        errors.append(f"{where}.selection must be one of {', '.join(SELECTIONS)}")

    if "human" in requires and not has_human:
        errors.append(f"{where} requires human review but eval.evaluation.human_review is not declared")
    if has_human and "human" not in requires:
        errors.append(f"eval.evaluation.human_review is declared but {where}.requires does not include 'human'")
    if "model_judge" in requires:
        if not has_judges:
            errors.append(f"{where} requires model_judge but no model_judges are declared")
        if role != "gate":
            errors.append(
                f"{where}.model_judge.role must be 'gate' to appear in requires; a 'screen' judge "
                "is informational and can never be the sole gate for promotion"
            )
    elif role == "gate":
        errors.append(f"{where}.model_judge.role is 'gate' but model_judge is not in requires")
