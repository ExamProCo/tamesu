from __future__ import annotations

from pathlib import Path

from .errors import ConfigError


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "cases").is_dir():
            return candidate
    raise ConfigError(
        f"No Tamesu project found from {current}. Run the command inside a directory "
        "containing cases/."
    )


def eval_path(project_root: Path, eval_id: str) -> Path:
    parts = eval_id.split("/")
    if len(parts) != 3 or any(not part for part in parts):
        raise ConfigError("Eval IDs must use <case>/<experiment>/<eval>.")
    case_name, experiment_name, eval_name = parts
    return (
        project_root
        / "cases"
        / case_name
        / "experiments"
        / experiment_name
        / "evals"
        / eval_name
        / "eval.yml"
    )


def discover_eval_paths(project_root: Path) -> list[Path]:
    return sorted(project_root.glob("cases/*/experiments/*/evals/*/eval.yml"))


def eval_id_from_path(project_root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(project_root.resolve())
    parts = relative.parts
    if (
        len(parts) != 7
        or parts[0] != "cases"
        or parts[2] != "experiments"
        or parts[4] != "evals"
        or parts[6] != "eval.yml"
    ):
        raise ConfigError(f"Unexpected eval path: {path}")
    return f"{parts[1]}/{parts[3]}/{parts[5]}"


def resolve_eval_ref(project_root: Path, ref: str) -> str:
    """Expand a short eval reference (`<eval>` or `<experiment>/<eval>`) to its full ID if unique."""
    parts = ref.strip("/").split("/")
    if len(parts) == 3 or not 1 <= len(parts) <= 2 or any(not part for part in parts):
        return ref
    matches = [
        eval_id
        for eval_id in (eval_id_from_path(project_root, path) for path in discover_eval_paths(project_root))
        if eval_id.split("/")[-len(parts):] == parts
    ]
    if not matches:
        raise ConfigError(f"Eval not found: {ref}")
    if len(matches) > 1:
        raise ConfigError(f"Eval reference {ref!r} is ambiguous; use one of: " + ", ".join(matches))
    return matches[0]


def find_run_dir(project_root: Path, run_id: str) -> Path:
    matches = list(project_root.glob(f"cases/*/experiments/*/evals/*/runs/{run_id}"))
    if not matches:
        raise ConfigError(f"Run not found: {run_id}")
    if len(matches) > 1:
        raise ConfigError(f"Run ID is not unique in this project: {run_id}")
    return matches[0]
