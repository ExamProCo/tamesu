from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..errors import ConfigError


TEMPLATE_EXPRESSION = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*}}")


def render_prompts(prompt_paths: dict[str, Path], item: dict[str, Any]) -> tuple[str, str]:
    values = {"item": item}
    rendered: dict[str, str] = {}
    for role in ("system", "user"):
        path = prompt_paths[role]
        source = path.read_text(encoding="utf-8")
        rendered[role] = render_template(source, values, path)
    return rendered["system"], rendered["user"]


def render_template(source: str, values: dict[str, Any], path: Path | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        expression = match.group(1)
        current: Any = values
        for part in expression.split("."):
            if not isinstance(current, dict) or part not in current:
                location = f" in {path}" if path else ""
                raise ConfigError(f"Unknown template value {expression!r}{location}")
            current = current[part]
        if isinstance(current, (dict, list)):
            raise ConfigError(f"Template value {expression!r} must be scalar")
        return str(current)

    rendered = TEMPLATE_EXPRESSION.sub(replace, source)
    if "{{" in rendered or "}}" in rendered:
        location = f" in {path}" if path else ""
        raise ConfigError(f"Unsupported template expression{location}")
    return rendered


def validate_json_schema(value: Any, schema: dict[str, Any], location: str = "output") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _matches_type(value, expected_type):
        errors.append(f"{location} must be {expected_type}")
        return errors

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{location} must be one of {enum!r}")

    if expected_type == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{location}.{key} is required")
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            for key in value:
                if key not in properties:
                    errors.append(f"{location}.{key} is not allowed")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(
                        validate_json_schema(value[key], child_schema, f"{location}.{key}")
                    )

    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, item_schema, f"{location}[{index}]"))
    return errors


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True
