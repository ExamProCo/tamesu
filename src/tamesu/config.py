from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .discovery import eval_id_from_path, eval_path
from .errors import ConfigError
from .models import EvalContext
from .providers.models import registered, validate_effort
from .tasks import TASKS, get_task
from .tasks.base import parse_mechanical_entry


ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_TASKS = set(TASKS)
SUPPORTED_PROVIDERS = {"anthropic", "openai", "meta", "grok", "bedrock", "gemini"}


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Expected a YAML mapping in {path}")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Expected a JSON object in {path}")
    return value


def resolve_contained(base: Path, raw_path: str, project_root: Path) -> Path:
    candidate = (base / raw_path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ConfigError(f"Path escapes the project: {raw_path}") from exc
    if not candidate.is_file():
        raise ConfigError(f"Referenced file does not exist: {candidate}")
    return candidate


def _require_mapping(value: Any, location: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{location} must be a mapping")
        return {}
    return value


def _require_list(value: Any, location: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{location} must be a list")
        return []
    return value


def _required_string(mapping: dict[str, Any], key: str, location: str, errors: list[str]) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location}.{key} must be a non-empty string")
        return ""
    return value


def _validate_id(value: str, location: str, errors: list[str]) -> None:
    if value and not ID_PATTERN.fullmatch(value):
        errors.append(f"{location} must use lowercase kebab-case")


def _validate_schema_version(mapping: dict[str, Any], location: str, errors: list[str]) -> None:
    if mapping.get("schema_version") != 1:
        errors.append(f"{location}.schema_version must be 1")


def _reject_unknown(
    mapping: dict[str, Any], allowed: set[str], location: str, errors: list[str]
) -> None:
    for key in sorted(set(mapping) - allowed):
        errors.append(f"{location}.{key} is not a recognized field")


def _validate_case(case: dict[str, Any], case_dir: Path, errors: list[str]) -> None:
    _reject_unknown(
        case,
        {
            "schema_version",
            "name",
            "title",
            "description",
            "business_use",
            "current_problem",
            "technical_uncertainty",
            "default_task",
        },
        "case",
        errors,
    )
    _validate_schema_version(case, "case", errors)
    name = _required_string(case, "name", "case", errors)
    _validate_id(name, "case.name", errors)
    if name and name != case_dir.name:
        errors.append(f"case.name must match directory name {case_dir.name!r}")
    _required_string(case, "title", "case", errors)
    _required_string(case, "description", "case", errors)
    _required_string(case, "business_use", "case", errors)
    _required_string(case, "current_problem", "case", errors)
    _required_string(case, "technical_uncertainty", "case", errors)
    task = case.get("default_task")
    if task is not None and task not in SUPPORTED_TASKS:
        errors.append(f"case.default_task is not implemented: {task!r}")


def _validate_dataset(dataset: dict[str, Any], errors: list[str]) -> None:
    _reject_unknown(
        dataset,
        {"schema_version", "name", "description", "shared_assets", "items"},
        "dataset",
        errors,
    )
    _validate_schema_version(dataset, "dataset", errors)
    name = _required_string(dataset, "name", "dataset", errors)
    _validate_id(name, "dataset.name", errors)
    _required_string(dataset, "description", "dataset", errors)
    items = _require_list(dataset.get("items"), "dataset.items", errors)
    if not items:
        errors.append("dataset.items must contain at least one item")
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _require_mapping(raw_item, f"dataset.items[{index}]", errors)
        _reject_unknown(
            item,
            {"id", "input", "assets", "expected", "metadata"},
            f"dataset.items[{index}]",
            errors,
        )
        item_id = _required_string(item, "id", f"dataset.items[{index}]", errors)
        _validate_id(item_id, f"dataset.items[{index}].id", errors)
        if item_id in seen:
            errors.append(f"dataset item ID is duplicated: {item_id}")
        seen.add(item_id)
        if not isinstance(item.get("input"), dict):
            errors.append(f"dataset item {item_id or index} must contain an input mapping")
        if "expected" not in item:
            errors.append(f"dataset item {item_id or index} must contain expected output")


def _positive_int(value: Any, location: str, errors: list[str], minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        errors.append(f"{location} must be an integer greater than or equal to {minimum}")
        return minimum
    return value


def _validate_eval(
    evaluation: dict[str, Any],
    eval_dir: Path,
    project_root: Path,
    case: dict[str, Any],
    dataset: dict[str, Any],
    errors: list[str],
) -> tuple[Path | None, dict[str, Any] | None]:
    _reject_unknown(
        evaluation,
        {
            "schema_version",
            "name",
            "status",
            "question",
            "description",
            "task",
            "dataset",
            "output_schema",
            "defaults",
            "arms",
            "runs",
            "evaluation",
            "metrics",
        },
        "eval",
        errors,
    )
    _validate_schema_version(evaluation, "eval", errors)
    name = _required_string(evaluation, "name", "eval", errors)
    _validate_id(name, "eval.name", errors)
    if name and name != eval_dir.name:
        errors.append(f"eval.name must match directory name {eval_dir.name!r}")
    status = evaluation.get("status")
    if status not in {"draft", "active", "complete"}:
        errors.append("eval.status must be draft, active, or complete")
    _required_string(evaluation, "question", "eval", errors)
    _required_string(evaluation, "description", "eval", errors)
    task = evaluation.get("task", case.get("default_task"))
    if task not in SUPPORTED_TASKS:
        errors.append(f"eval.task is not implemented: {task!r}")

    defaults = _require_mapping(evaluation.get("defaults"), "eval.defaults", errors)
    _reject_unknown(
        defaults,
        {
            "repetitions",
            "concurrency",
            "timeout_seconds",
            "retries",
            "budget_usd",
            "judge_budget_usd",
            "parameters",
        },
        "eval.defaults",
        errors,
    )
    _positive_int(defaults.get("repetitions"), "eval.defaults.repetitions", errors)
    _positive_int(defaults.get("concurrency"), "eval.defaults.concurrency", errors)
    _positive_int(defaults.get("timeout_seconds"), "eval.defaults.timeout_seconds", errors)
    _positive_int(defaults.get("retries"), "eval.defaults.retries", errors, minimum=0)
    budget = defaults.get("budget_usd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0:
        errors.append("eval.defaults.budget_usd must be a non-negative number")
    parameters = defaults.get("parameters", {})
    if not isinstance(parameters, dict):
        errors.append("eval.defaults.parameters must be a mapping")

    task_impl = get_task(task) if task in SUPPORTED_TASKS else None
    output_schema_path: Path | None = None
    output_schema: dict[str, Any] | None = None
    if task_impl is not None:
        validation = task_impl.validate_eval(evaluation, eval_dir, project_root, errors)
        output_schema_path = validation.output_schema_path
        output_schema = validation.output_schema

    arms = _require_list(evaluation.get("arms"), "eval.arms", errors)
    if not arms:
        errors.append("eval.arms must contain at least one arm")
    arm_ids: set[str] = set()
    for index, raw_arm in enumerate(arms):
        arm = _require_mapping(raw_arm, f"eval.arms[{index}]", errors)
        _reject_unknown(
            arm,
            {"id", "description", "prompts", "references", "parameters"},
            f"eval.arms[{index}]",
            errors,
        )
        arm_id = _required_string(arm, "id", f"eval.arms[{index}]", errors)
        _validate_id(arm_id, f"eval.arms[{index}].id", errors)
        if arm_id in arm_ids:
            errors.append(f"eval arm ID is duplicated: {arm_id}")
        arm_ids.add(arm_id)
        _required_string(arm, "description", f"eval.arms[{index}]", errors)
        prompts = _require_mapping(arm.get("prompts"), f"eval.arms[{index}].prompts", errors)
        for role in task_impl.prompt_roles if task_impl else ():
            raw_prompt = prompts.get(role)
            if not isinstance(raw_prompt, str) or not raw_prompt:
                errors.append(f"eval arm {arm_id or index} must define prompts.{role}")
                continue
            try:
                resolve_contained(eval_dir, raw_prompt, project_root)
            except ConfigError as exc:
                errors.append(str(exc))
        if "parameters" in arm and not isinstance(arm["parameters"], dict):
            errors.append(f"eval arm {arm_id or index}.parameters must be a mapping")

    runs = _require_list(evaluation.get("runs"), "eval.runs", errors)
    if not runs:
        errors.append("eval.runs must contain at least one run entry")
    for index, raw_run in enumerate(runs):
        run = _require_mapping(raw_run, f"eval.runs[{index}]", errors)
        _reject_unknown(
            run,
            {"model", "provider", "arms", "parameters", "repetitions"},
            f"eval.runs[{index}]",
            errors,
        )
        model = _required_string(run, "model", f"eval.runs[{index}]", errors)
        provider = _required_string(run, "provider", f"eval.runs[{index}]", errors)
        if provider and provider not in SUPPORTED_PROVIDERS:
            errors.append(f"eval.runs[{index}].provider is not implemented: {provider!r}")
        model_spec = registered(model)
        if model_spec and provider and model_spec.provider != provider:
            errors.append(
                f"eval.runs[{index}] model {model!r} belongs to provider "
                f"{model_spec.provider!r}, not {provider!r}"
            )
        run_arms = _require_list(run.get("arms"), f"eval.runs[{index}].arms", errors)
        if not run_arms:
            errors.append(f"eval.runs[{index}].arms must not be empty")
        for arm_id in run_arms:
            if arm_id not in arm_ids:
                errors.append(f"eval.runs[{index}] references unknown arm: {arm_id!r}")
        if "repetitions" in run:
            _positive_int(run["repetitions"], f"eval.runs[{index}].repetitions", errors)
        if "parameters" in run and not isinstance(run["parameters"], dict):
            errors.append(f"eval.runs[{index}].parameters must be a mapping")
        if model and isinstance(parameters, dict):
            selected_arms = [arm for arm in arms if arm.get("id") in run_arms]
            for arm in selected_arms:
                merged_parameters = dict(parameters)
                if isinstance(arm.get("parameters"), dict):
                    merged_parameters.update(arm["parameters"])
                if isinstance(run.get("parameters"), dict):
                    merged_parameters.update(run["parameters"])
                location = f"eval.runs[{index}] with arm {arm.get('id')!r}"
                if task_impl is not None:
                    task_impl.validate_run_parameters(
                        provider, model, merged_parameters, location, errors
                    )
                    _validate_task_capabilities(task_impl, model, location, errors)

    evaluation_block = _require_mapping(
        evaluation.get("evaluation"), "eval.evaluation", errors
    )
    _reject_unknown(
        evaluation_block,
        {"mechanical", "model_judges", "human_review", "acceptance"},
        "eval.evaluation",
        errors,
    )
    mechanical = _require_list(
        evaluation_block.get("mechanical"), "eval.evaluation.mechanical", errors
    )
    for scorer_index, scorer in enumerate(mechanical):
        parsed = parse_mechanical_entry(scorer)
        if parsed is None:
            errors.append(
                f"eval.evaluation.mechanical[{scorer_index}] must be a scorer name or a "
                "single-key mapping of scorer name to parameters"
            )
            continue
        scorer_name, scorer_params = parsed
        if task_impl is None:
            continue
        if scorer_name not in task_impl.mechanical_scorers:
            errors.append(f"Unknown mechanical scorer: {scorer_name!r}")
        else:
            task_impl.validate_mechanical(
                scorer_name,
                scorer_params,
                f"eval.evaluation.mechanical[{scorer_index}] ({scorer_name})",
                errors,
            )
    if evaluation_block.get("model_judges"):
        if task_impl is not None and not task_impl.supports_judges:
            errors.append(f"model_judges are not supported by task {task_impl.name!r}")
        else:
            from .policy import validate_model_judges

            validate_model_judges(
                evaluation_block["model_judges"],
                defaults=defaults,
                eval_dir=eval_dir,
                project_root=project_root,
                errors=errors,
            )
    if task_impl is not None and (
        evaluation_block.get("human_review") or evaluation_block.get("acceptance")
    ):
        if not task_impl.supports_review:
            errors.append(
                f"human_review and acceptance are not supported by task {task_impl.name!r}"
            )
        else:
            from .policy import validate_acceptance, validate_human_review

            if evaluation_block.get("human_review"):
                validate_human_review(
                    evaluation_block["human_review"],
                    eval_dir=eval_dir,
                    project_root=project_root,
                    errors=errors,
                )
            validate_acceptance(evaluation_block, errors)

    metrics = _require_mapping(evaluation.get("metrics"), "eval.metrics", errors)
    _reject_unknown(metrics, {"primary", "secondary"}, "eval.metrics", errors)
    primary = _required_string(metrics, "primary", "eval.metrics", errors)
    secondary = metrics.get("secondary", [])
    secondary_values = _require_list(secondary, "eval.metrics.secondary", errors)
    for metric in [primary, *secondary_values]:
        if metric and task_impl is not None and metric not in task_impl.built_in_metrics:
            errors.append(f"Unknown metric: {metric!r}")

    item_ids = [item.get("id") for item in dataset.get("items", []) if isinstance(item, dict)]
    if len(item_ids) != len(set(item_ids)):
        errors.append("dataset items must have unique IDs")
    return output_schema_path, output_schema


def _validate_task_capabilities(
    task: Any, model: str, location: str, errors: list[str]
) -> None:
    spec = registered(model)
    if spec is None:
        if "text_output" not in task.required_capabilities:
            errors.append(
                f"{location}: model {model!r} is not registered; task {task.name!r} "
                "needs a registered model so its capabilities and pricing are known"
            )
        return
    missing = sorted(task.required_capabilities - spec.capabilities)
    if missing:
        errors.append(
            f"{location}: model {model!r} lacks capabilities required by "
            f"{task.name!r}: {', '.join(missing)}"
        )


def _validate_model_parameters(
    model: str, parameters: dict[str, Any], location: str, errors: list[str]
) -> None:
    if "max_tokens" in parameters and "max_output_tokens" in parameters:
        errors.append(f"{location} cannot set both max_tokens and max_output_tokens")
    for key in ("max_tokens", "max_output_tokens"):
        if key in parameters:
            value = parameters[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"{location}.parameters.{key} must be a positive integer")
    effort = parameters.get("effort")
    if effort is not None and not isinstance(effort, str):
        errors.append(f"{location}.parameters.effort must be a string")
    elif isinstance(effort, str):
        error = validate_effort(model, effort)
        if error:
            errors.append(f"{location}: {error}")


def load_eval_context(project_root: Path, eval_id: str) -> EvalContext:
    project_root = project_root.resolve()
    path = eval_path(project_root, eval_id)
    if not path.is_file():
        raise ConfigError(f"Eval not found: {eval_id}")
    eval_dir = path.parent
    experiment_dir = eval_dir.parent.parent
    case_dir = experiment_dir.parent.parent
    case_path = case_dir / "case.yml"

    if (case_dir / "package-view.yml").is_file():
        raise ConfigError(
            f"{eval_id} belongs to a report-only, view-only package; "
            "run and rescore require the withheld dataset and outputs"
        )

    case = load_yaml(case_path)
    evaluation = load_yaml(path)
    raw_dataset_path = evaluation.get("dataset")
    if not isinstance(raw_dataset_path, str) or not raw_dataset_path:
        raise ConfigError("eval.dataset must be a non-empty path")
    dataset_path = resolve_contained(eval_dir, raw_dataset_path, project_root)
    dataset = load_yaml(dataset_path)

    errors: list[str] = []
    _validate_case(case, case_dir, errors)
    _validate_dataset(dataset, errors)
    output_schema_path, output_schema = _validate_eval(
        evaluation, eval_dir, project_root, case, dataset, errors
    )
    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise ConfigError(f"Validation failed for {eval_id}:\n{joined}")

    return EvalContext(
        project_root=project_root,
        case_dir=case_dir,
        experiment_dir=experiment_dir,
        eval_dir=eval_dir,
        eval_id=eval_id,
        case=case,
        dataset=dataset,
        evaluation=evaluation,
        dataset_path=dataset_path,
        output_schema=output_schema,
        output_schema_path=output_schema_path,
    )


def load_eval_context_from_path(project_root: Path, path: Path) -> EvalContext:
    return load_eval_context(project_root, eval_id_from_path(project_root, path))
