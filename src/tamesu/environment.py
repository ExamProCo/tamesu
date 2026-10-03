from __future__ import annotations

import ast
import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError


_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EnvironmentLoadResult:
    sources: tuple[Path, ...]
    loaded_keys: tuple[str, ...]


def load_environment(
    project_root: Path | None,
    *,
    config_dir: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
    home: Path | None = None,
) -> EnvironmentLoadResult:
    """Load layered dotenv configuration into an environment-like mapping.

    Precedence, lowest to highest: global files, enclosing Git-workspace files,
    project files, the existing process environment, and an explicitly selected
    config directory.
    """

    target = os.environ if environ is None else environ
    original = dict(target)
    home_dir = (Path.home() if home is None else home).expanduser().resolve()
    explicit = config_dir
    if explicit is None and original.get("TAMESU_CONFIG_DIR"):
        explicit = Path(original["TAMESU_CONFIG_DIR"])

    ordinary_paths = [home_dir / ".tamesu" / ".env"]
    if project_root is not None:
        root = project_root.expanduser().resolve()
        workspace_root = _find_workspace_root(root)
        if workspace_root is not None and workspace_root != root:
            ordinary_paths.extend(
                (workspace_root / ".env", workspace_root / ".tamesu" / ".env")
            )
        ordinary_paths.extend((root / ".env", root / ".tamesu" / ".env"))

    sources: list[Path] = []
    values: dict[str, str] = {}
    loaded_keys: set[str] = set()
    seen: set[Path] = set()
    for path in ordinary_paths:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        parsed = parse_dotenv(resolved)
        values.update(parsed)
        loaded_keys.update(parsed)
        sources.append(resolved)

    values.update(original)

    if explicit is not None:
        explicit_dir = explicit.expanduser().resolve()
        if not explicit_dir.is_dir():
            raise ConfigError(f"Configuration directory does not exist: {explicit_dir}")
        explicit_path = explicit_dir / ".env"
        if not explicit_path.is_file():
            raise ConfigError(f"Configuration directory has no .env file: {explicit_dir}")
        resolved = explicit_path.resolve()
        parsed = parse_dotenv(resolved)
        values.update(parsed)
        loaded_keys.update(parsed)
        if resolved not in seen:
            sources.append(resolved)

    target.update(values)
    return EnvironmentLoadResult(
        sources=tuple(sources), loaded_keys=tuple(sorted(loaded_keys))
    )


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"Could not read environment file {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"Invalid environment entry in {path}:{line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise ConfigError(f"Invalid environment key in {path}:{line_number}: {key!r}")
        values[key] = _parse_value(raw_value.strip(), path, line_number)
    return values


def _parse_value(value: str, path: Path, line_number: int) -> str:
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        escaped = False
        closing = None
        for index in range(1, len(value)):
            character = value[index]
            if quote == '"' and character == "\\" and not escaped:
                escaped = True
                continue
            if character == quote and not escaped:
                closing = index
                break
            escaped = False
        if closing is None:
            raise ConfigError(f"Unclosed quoted value in {path}:{line_number}")
        trailing = value[closing + 1 :].strip()
        if trailing and not trailing.startswith("#"):
            raise ConfigError(f"Unexpected text after value in {path}:{line_number}")
        literal = value[: closing + 1]
        try:
            parsed = ast.literal_eval(literal)
        except (SyntaxError, ValueError) as exc:
            raise ConfigError(f"Invalid quoted value in {path}:{line_number}") from exc
        if not isinstance(parsed, str):
            raise ConfigError(f"Environment value must be text in {path}:{line_number}")
        return parsed
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _find_workspace_root(project_root: Path) -> Path | None:
    for candidate in (project_root, *project_root.parents):
        if (candidate / ".git").exists():
            return candidate
    return None
