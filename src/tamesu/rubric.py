"""Machine-readable rubrics: one YAML file feeds the judge schema, the review pack, and the report."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_yaml
from .errors import ConfigError
from .identity import digest_value
from .tasks.structured_text import validate_json_schema

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ACCEPTANCE_RULES = {"all_dimensions_pass"}
NO_REASON = "none"  # reason_code for a passing dimension; reserved, never a failure code


@dataclass(frozen=True)
class Dimension:
    id: str
    question: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class Rubric:
    name: str
    dimensions: tuple[Dimension, ...]
    acceptance_rule: str
    rationale: str | None
    sha256: str
    path: Path

    @property
    def sha8(self) -> str:
        return self.sha256.split(":", 1)[1][:8]

    @property
    def dimension_ids(self) -> tuple[str, ...]:
        return tuple(dimension.id for dimension in self.dimensions)

    def dimension(self, dimension_id: str) -> Dimension:
        for dimension in self.dimensions:
            if dimension.id == dimension_id:
                return dimension
        raise KeyError(dimension_id)


def validate_rubric(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = {"schema_version", "name", "description", "rationale", "dimensions", "acceptance"}
    for key in sorted(set(document) - allowed):
        errors.append(f"rubric.{key} is not a recognized field")
    if document.get("schema_version") != 1:
        errors.append("rubric.schema_version must be 1")
    name = document.get("name")
    if not isinstance(name, str) or not ID_PATTERN.fullmatch(name):
        errors.append("rubric.name must be lowercase kebab-case")
    dimensions = document.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("rubric.dimensions must be a non-empty list")
        dimensions = []
    seen: set[str] = set()
    for index, raw in enumerate(dimensions):
        location = f"rubric.dimensions[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{location} must be a mapping")
            continue
        for key in sorted(set(raw) - {"id", "question", "reason_codes"}):
            errors.append(f"{location}.{key} is not a recognized field")
        dimension_id = raw.get("id")
        if not isinstance(dimension_id, str) or not ID_PATTERN.fullmatch(dimension_id):
            errors.append(f"{location}.id must be lowercase kebab-case")
        elif dimension_id in seen:
            errors.append(f"{location}.id is duplicated: {dimension_id}")
        else:
            seen.add(dimension_id)
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{location}.question must be a non-empty string")
        codes = raw.get("reason_codes")
        if not isinstance(codes, list) or not codes:
            errors.append(f"{location}.reason_codes must be a non-empty list")
            continue
        for code in codes:
            if not isinstance(code, str) or not ID_PATTERN.fullmatch(code):
                errors.append(f"{location}.reason_codes entries must be lowercase kebab-case")
            elif code == NO_REASON:
                errors.append(f"{location}.reason_codes must not use the reserved code 'none'")
        if len(set(map(str, codes))) != len(codes):
            errors.append(f"{location}.reason_codes contains duplicates")
    acceptance = document.get("acceptance")
    if not isinstance(acceptance, dict):
        errors.append("rubric.acceptance must be a mapping")
    else:
        for key in sorted(set(acceptance) - {"rule"}):
            errors.append(f"rubric.acceptance.{key} is not a recognized field")
        if acceptance.get("rule") not in ACCEPTANCE_RULES:
            errors.append(f"rubric.acceptance.rule must be one of {sorted(ACCEPTANCE_RULES)}")
    return errors


def load_rubric(path: Path) -> Rubric:
    document = load_yaml(path)
    errors = validate_rubric(document)
    rationale = document.get("rationale")
    if isinstance(rationale, str) and not (path.parent / rationale).is_file():
        errors.append(f"rubric.rationale does not exist: {rationale}")
    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise ConfigError(f"Invalid rubric {path}:\n{joined}")
    return Rubric(
        name=document["name"],
        dimensions=tuple(
            Dimension(d["id"], d["question"].strip(), tuple(d["reason_codes"]))
            for d in document["dimensions"]
        ),
        acceptance_rule=document["acceptance"]["rule"],
        rationale=rationale if isinstance(rationale, str) else None,
        sha256=digest_value(document),
        path=path,
    )


def render_rubric_text(rubric: Rubric) -> str:
    """The rubric as plain text for judge prompts: one yes/no claim per dimension."""
    lines = []
    for dimension in rubric.dimensions:
        codes = ", ".join(dimension.reason_codes)
        lines.append(f"- {dimension.id}: {dimension.question} (if it fails, reason_code is one of: {codes})")
    return "\n".join(lines)


def judge_output_schema(rubric: Rubric) -> dict[str, Any]:
    """Strict-mode friendly schema: every field required, reason_code 'none' when passing."""
    dimension_schemas = {
        dimension.id: {
            "type": "object",
            "additionalProperties": False,
            "required": ["pass", "reason_code", "explanation"],
            "properties": {
                "pass": {"type": "boolean"},
                "reason_code": {"type": "string", "enum": [NO_REASON, *dimension.reason_codes]},
                "explanation": {"type": "string"},
            },
        }
        for dimension in rubric.dimensions
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["dimensions"],
        "properties": {
            "dimensions": {
                "type": "object",
                "additionalProperties": False,
                "required": list(rubric.dimension_ids),
                "properties": dimension_schemas,
            }
        },
    }


def validate_judge_output(output: Any, rubric: Rubric) -> list[str]:
    errors = validate_json_schema(output, judge_output_schema(rubric))
    if errors:
        return errors
    for dimension_id, result in output["dimensions"].items():
        if result["pass"] and result["reason_code"] != NO_REASON:
            errors.append(f"dimension {dimension_id} passed but gave reason_code {result['reason_code']!r}")
        if not result["pass"] and result["reason_code"] == NO_REASON:
            errors.append(f"dimension {dimension_id} failed without a reason_code")
    return errors


def overall_pass(dimensions: dict[str, dict[str, Any]], rubric: Rubric) -> bool:
    """The verdict is computed from dimension results; judges are never asked for one."""
    if rubric.acceptance_rule == "all_dimensions_pass":
        return all(dimensions[dimension_id]["pass"] is True for dimension_id in rubric.dimension_ids)
    raise ConfigError(f"Unsupported acceptance rule: {rubric.acceptance_rule}")
