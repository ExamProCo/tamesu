from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import EvalContext
from .tasks import task_for


def build_report(context: EvalContext, run_dir: Path, run_manifest: dict[str, Any]) -> dict[str, Any]:
    return task_for(context.evaluation, context.case).build_report(context, run_dir, run_manifest)


def rescore_run(context: EvalContext, run_dir: Path) -> dict[str, Any]:
    return task_for(context.evaluation, context.case).rescore_run(context, run_dir)


def refresh_reports(context: EvalContext, run_ids: list[str]) -> None:
    """Rebuild run reports after new evidence (judgments, reviews); makes no provider call."""
    from .config import load_yaml

    task = task_for(context.evaluation, context.case)
    for run_id in sorted(set(run_ids)):
        run_dir = context.eval_dir / "runs" / run_id
        task.build_report(context, run_dir, load_yaml(run_dir / "run.yml"))
    sync_analysis_quietly(context)


def sync_analysis_quietly(context: EvalContext, *, stamp: bool = False) -> None:
    """Refresh analysis.md; a problem here must never fail a run, judgment, or import."""
    import sys

    from .analysis import sync_analysis

    try:
        sync_analysis(context, stamp=stamp)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not update analysis.md: {exc}", file=sys.stderr)
