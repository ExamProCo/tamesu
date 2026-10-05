"""Keep an image eval's `analysis.md` (the authored interpretation shown by `present`) in step
with its evidence, with no commands for the author to run. See `sync_analysis`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import write_text
from .config import load_yaml
from .errors import ExecutionError
from .models import EvalContext
from .planner import build_plan
from .presenting import evidence_digest
from .reporting import comparison_rows, image_outcome, image_row_counts, _format_metric_value
from .tasks import task_for

TODO = "TODO"


def analysis_path(context: EvalContext) -> Path:
    return context.eval_dir / "analysis.md"


def scaffold_analysis(context: EvalContext) -> str:
    plan = build_plan(context)
    if not plan.banked_run_ids:
        raise ExecutionError(
            "No banked runs yet. Generate first (tamesu run), then scaffold the analysis."
        )
    evaluation = context.evaluation
    image_eval = task_for(evaluation, context.case).supports_review
    facts = _image_facts(plan) if image_eval else _text_facts(plan)
    technical_uncertainty = " ".join(str(context.case.get("technical_uncertainty", "")).split())
    items = len(context.dataset.get("items", []))
    reviewers = _reviewers(plan) if image_eval else set()

    uncertain = [
        f"- The dataset has {items} authored item(s) and each row ran "
        f"{evaluation['defaults']['repetitions']} repetition(s).",
    ]
    if image_eval:
        uncertain.append(
            f"- Review came from {len(reviewers) or 'no'} reviewer(s)"
            + (f" ({', '.join(sorted(reviewers))})" if reviewers else "")
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
            "",
            "## Answer to the technical uncertainty",
            "",
            f"The case asked: {technical_uncertainty}" if technical_uncertainty else "",
            "",
            f"{TODO}: answer that in two or three sentences, citing the counts below. Say what the "
            "evidence supports and what it does not.",
            "",
            facts_block(facts),
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


FACTS_HEADING = "### Evidence at a glance"


def facts_block(facts: list[str]) -> str:
    return "\n".join(
        [
            FACTS_HEADING,
            "",
            "_Generated from stored results and rewritten automatically after every run, judgment, "
            "and review. Write your own text outside this section._",
            "",
            *facts,
        ]
    )


def sync_analysis(context: EvalContext, *, stamp: bool = False) -> Path | None:
    """Keep `analysis.md` in step with the evidence; the author never has to run a command.

    Creates a draft once the eval has a banked run; afterwards rewrites only the generated
    "Evidence at a glance" section, leaving everything the author wrote alone. With `stamp`
    (used by `close`, when evidence becomes final) it records the evidence digest. Only evals
    whose task stores reviewable artifacts get an automatic file.
    """
    if not task_for(context.evaluation, context.case).supports_review:
        return None
    path = analysis_path(context)
    try:
        if not path.is_file():
            write_text(path, scaffold_analysis(context))
        else:
            plan = build_plan(context)
            if plan.banked_run_ids:
                source = path.read_text(encoding="utf-8")
                refreshed = _replace_facts(source, facts_block(_image_facts(plan)))
                if refreshed != source:
                    write_text(path, refreshed)
    except ExecutionError:
        return None  # nothing banked yet
    if stamp:
        _stamp(context)
    return path


def _replace_facts(source: str, block: str) -> str:
    lines = source.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(FACTS_HEADING)), None)
    if start is None:
        return source  # the author removed the section; respect that
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    tail = lines[end:]
    new = block.split("\n") + ([""] if tail else [])
    return "\n".join(lines[:start] + new + tail)


def _image_facts(plan: Any) -> list[str]:
    context = plan.context
    summary, caveat = image_outcome(image_row_counts(plan))
    lines = [f"- Observed: {summary}", f"- Caveat: {caveat}"]
    spec_by_fingerprint = {spec.specification_fingerprint: spec for spec in plan.specs}
    for fingerprint, run_id in sorted(plan.banked_run_ids.items(), key=lambda pair: pair[1]):
        report_path = context.eval_dir / "runs" / run_id / "report.yml"
        evidence = load_yaml(report_path).get("evidence") if report_path.is_file() else None
        if not evidence:
            continue
        spec = spec_by_fingerprint[fingerprint]
        counts = evidence["counts"]
        lines.append(
            f"- {spec.model} / {spec.arm_id} (repetition {spec.repetition}): {counts['generated']} of "
            f"{counts['planned']} produced an image; {counts['accepted']} accepted, "
            f"{counts['rejected']} rejected, {counts['pending']} pending."
        )
        for source, label in (("human", "Human review"), ("model_judge", "Model judge")):
            for dimension_id, stats in evidence["dimensions"][source].items():
                if stats["failed"]:
                    reasons = ", ".join(f"{code} ({n})" for code, n in stats["top_reasons"].items())
                    lines.append(
                        f"  - {label} failed `{dimension_id}` on {stats['failed']} of {stats['evaluated']}: {reasons}"
                    )
        for dimension_id, stats in evidence["agreement"].items():
            note = " (too few paired items to trust)" if stats["insufficient_data"] else ""
            lines.append(
                f"  - Judge vs human on `{dimension_id}`: {stats['raw_agreement']:.0%} agreement over "
                f"{stats['n']} item(s){note}"
            )
        cost = evidence["cost"]
        if cost["total_usd"] is not None:
            lines.append(f"  - Cost: ${cost['total_usd']:.4f} total (generation ${cost['generation_usd']:.4f}, judging ${cost['judge_usd']:.4f}).")
    return lines


def _text_facts(plan: Any) -> list[str]:
    primary = plan.context.evaluation["metrics"]["primary"]
    lines = []
    for row in comparison_rows(plan):
        lines.append(
            f"- {row['model']} / {row['arm']}: {primary} = {_format_metric_value(primary, row['primary_mean'])} "
            f"over {row['repetitions']} repetition(s), {row['items']} item(s)."
        )
    return lines or ["- No compatible completed runs."]


def _reviewers(plan: Any) -> set[str]:
    reviewers: set[str] = set()
    for run_id in plan.banked_run_ids.values():
        for path in (plan.context.eval_dir / "runs" / run_id).glob("items/*/reviews/*.yml"):
            reviewers.add(str(load_yaml(path).get("reviewer")))
    return reviewers


def _stamp(context: EvalContext) -> None:
    """Record the evidence digest in the front matter (done by `close`, when evidence is final)."""
    path = analysis_path(context)
    source = path.read_text(encoding="utf-8")
    body = source
    if source.startswith("---\n"):
        closing = source.find("\n---\n", 4)
        if closing >= 0:
            body = source[closing + 5 :]
    digest = evidence_digest(context.eval_dir)
    write_text(path, f"---\nevidence_digest: {digest}\n---\n\n{body.lstrip(chr(10))}")
