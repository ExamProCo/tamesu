"""`analysis.md`: the interpretation of an eval's evidence.

The file holds authored or model-assisted prose only. Facts computed from evidence are rendered
live by `present` and are never written here, so they cannot go stale. `run`, `judge` and
`review import` never touch this file; `tamesu analyze` creates it deliberately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .artifacts import write_text
from .errors import ExecutionError
from .models import EvalContext
from .planner import build_plan
from .present_facts import reviewers
from .tasks import task_for

TODO = "TODO"
FACTS_HEADING = "### Evidence at a glance"


def analysis_path(context: EvalContext) -> Path:
    return context.eval_dir / "analysis.md"


def scaffold_analysis(context: EvalContext) -> str:
    """An authored template. It carries no computed facts; `present` shows those live."""
    plan = build_plan(context)
    if not plan.banked_run_ids:
        raise ExecutionError("No banked runs yet. Run the eval first (tamesu run), then scaffold the analysis.")
    evaluation = context.evaluation
    reviewable = task_for(evaluation, context.case).supports_review
    technical_uncertainty = " ".join(str(context.case.get("technical_uncertainty", "")).split())
    items = len(context.dataset.get("items", []))
    seen = reviewers(plan) if reviewable else set()

    uncertain = [
        f"- The dataset has {items} authored item(s) and each row ran "
        f"{evaluation['defaults']['repetitions']} repetition(s).",
    ]
    if reviewable:
        uncertain.append(
            f"- Review came from {len(seen) or 'no'} reviewer(s)"
            + (f" ({', '.join(sorted(seen))})" if seen else "")
            + ". One reviewer's judgments are not a measure of reviewer agreement."
        )
        if evaluation["evaluation"].get("model_judges"):
            uncertain.append(
                "- Check the judge-vs-human agreement table in `evaluation-report.md`; it is withheld "
                "below ten paired items."
            )
    uncertain.append(f"- {TODO}: what this eval cannot tell you (other models, products, prompts, scale).")

    return "\n".join(
        [
            "This interpretation is based on `evaluation-report.md` and the compatible completed runs.",
            "The counts, costs and review status are shown live above it by `tamesu present`; do not "
            "copy them here.",
            "",
            "## Answer to the technical uncertainty",
            "",
            f"The case asked: {technical_uncertainty}" if technical_uncertainty else "",
            "",
            f"{TODO}: answer that in two or three sentences, citing the counts. Say what the "
            "evidence supports and what it does not.",
            "",
            "## What remains uncertain",
            "",
            *uncertain,
            "",
            "## Next experiments",
            "",
            f"- {TODO}: the next experiment, changing one factor.",
            f"- {TODO}: another.",
            "",
        ]
    ).replace("\n\n\n", "\n\n")


def split_front_matter(source: str) -> tuple[dict[str, Any], str]:
    if source.startswith("---\n"):
        closing = source.find("\n---\n", 4)
        if closing >= 0:
            try:
                loaded = yaml.safe_load(source[4:closing])
            except yaml.YAMLError:
                return {}, source
            if isinstance(loaded, dict):
                return loaded, source[closing + 5 :]
    return {}, source


def strip_generated_facts(body: str) -> str:
    """Older files carried a generated "Evidence at a glance" section; the live one replaces it."""
    lines = body.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(FACTS_HEADING)), None)
    if start is None:
        return body
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return "\n".join(lines[:start] + lines[end:])


def stamp_existing(context: EvalContext) -> bool:
    """Bind an existing analysis to the evidence at close. Never creates the file."""
    path = analysis_path(context)
    if not path.is_file():
        return False
    from .presenting import evidence_digest

    metadata, body = split_front_matter(path.read_text(encoding="utf-8"))
    metadata["evidence_digest"] = evidence_digest(context.eval_dir)
    write_text(path, "---\n" + yaml.safe_dump(metadata, sort_keys=True).rstrip("\n") + "\n---\n\n" + body.lstrip("\n"))
    return True
