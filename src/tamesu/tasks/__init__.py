from __future__ import annotations

from .base import Task
from .structured_text import render_prompts, validate_json_schema
from .image_generation import ImageGenerationTask
from .structured_text_task import StructuredTextTask

TASKS: dict[str, Task] = {
    "structured_text": StructuredTextTask(),
    "image_generation": ImageGenerationTask(),
}


def get_task(name: str) -> Task:
    try:
        return TASKS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported task: {name}") from exc


def task_for(context_like: dict, case: dict | None = None) -> Task:
    """Resolve the task named by an eval (falling back to the case default)."""
    name = context_like.get("task", (case or {}).get("default_task"))
    return get_task(name)


__all__ = ["TASKS", "Task", "get_task", "render_prompts", "task_for", "validate_json_schema"]
