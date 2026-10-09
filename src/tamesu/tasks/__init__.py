from __future__ import annotations

from .base import Task
from .structured_text import render_prompts, validate_json_schema
from .artifact_bundle import ArtifactBundleTask
from .image_generation import ImageGenerationTask
from .structured_text_task import StructuredTextTask

TASKS: dict[str, Task] = {
    "structured_text": StructuredTextTask(),
    "image_generation": ImageGenerationTask(),
    "artifact_bundle": ArtifactBundleTask(),
}


def get_task(name: str) -> Task:
    try:
        return TASKS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported task: {name}") from exc


def supported_backends(task: Task) -> frozenset[str]:
    """Execution backends a task runs on. Defaults to native; declared in a task module only when
    that module is not part of run identity (image_generation's is, so it relies on the default)."""
    return getattr(task, "supported_backends", frozenset({"native"}))


def supports_promotion(task: Task) -> bool:
    return getattr(task, "supports_promotion", task.name == "image_generation")


def task_for(context_like: dict, case: dict | None = None) -> Task:
    """Resolve the task named by an eval (falling back to the case default)."""
    name = context_like.get("task", (case or {}).get("default_task"))
    return get_task(name)


__all__ = [
    "TASKS",
    "Task",
    "get_task",
    "render_prompts",
    "supported_backends",
    "supports_promotion",
    "task_for",
    "validate_json_schema",
]
