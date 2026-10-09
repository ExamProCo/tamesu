from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from . import present_blocks as blocks
from . import lifecycle as lc
from .analysis import split_front_matter, strip_generated_facts
from .artifacts import write_json, write_text
from .config import load_eval_context_from_path, load_yaml
from .errors import ConfigError, TamesuError
from .present_design import build_design
from .presenters import present_item
from .present_facts import computed_facts
from .packaging import SECRET_PATTERNS, build_inventory, collect_payload, inventory_digest, load_publication
from .planner import build_plan
from .pricing import estimate_plan_cost
from .providers.models import PROVIDER_KEYS
from .reporting import (
    LOWER_IS_BETTER_METRICS,
    SINGLE_SAMPLE_CAVEAT,
    metric_range_sentence,
    single_sample,
    comparison_rows,
    image_outcome,
    image_row_counts,
    status_summary,
)
from .tasks import task_for


CSS = """\
:root {
  --background: #fff;
  --text: #111;
  --muted: #555;
  --border: #d6d6d6;
  --link: #0645ad;
}
* { box-sizing: border-box; }
html { background: var(--background); color: var(--text); scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--background);
  color: var(--text);
  font: 18px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page-shell {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  width: 100%;
}
.document-nav {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow: auto;
  border-right: 1px solid var(--border);
  padding: 2.5rem 1.5rem;
  font-size: 16px;
  line-height: 1.55;
}
.document-nav > a {
  display: block;
  margin-bottom: 1.5rem;
  color: var(--text);
  font-size: 18px;
  font-weight: 700;
  text-decoration: none;
}
.tree, .tree ul { margin: 0; padding: 0; list-style: none; }
.tree ul {
  margin: .4rem 0 .4rem .4rem;
  padding-left: 1rem;
  border-left: 1px solid var(--border);
}
.tree li { margin: .45rem 0; }
.tree a { color: var(--text); text-decoration: none; }
.tree a:hover, .tree a:focus { text-decoration: underline; }
.tree a[aria-current="page"], .tree a[aria-current="location"] { font-weight: 700; }
.tree-label { color: var(--text); font-weight: 700; }
main { min-width: 0; width: 100%; padding: 2.5rem 3rem 6rem; }
header { padding-bottom: 2rem; border-bottom: 1px solid var(--border); }
section { margin-top: 3rem; scroll-margin-top: 2rem; }
h1, h2, h3, h4 { color: var(--text); line-height: 1.3; scroll-margin-top: 2rem; }
h1 { margin: 0 0 1rem; font-size: 2.25rem; }
h2 { margin: 2.5rem 0 .75rem; font-size: 1.55rem; }
h3 { margin: 2rem 0 .5rem; font-size: 1.2rem; }
h4 { margin: 1.5rem 0 .5rem; font-size: 1rem; }
p { max-width: 72ch; margin: .5rem 0 1rem; }
a { color: var(--link); text-underline-offset: .15em; }
code { font-size: .9em; }
.document-type { margin-bottom: .5rem; color: var(--muted); }
.lead { font-size: 1.1rem; }
.meta { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; color: var(--muted); }
.muted { color: var(--muted); }
.key-values { margin: 0; border-top: 1px solid var(--border); }
.key-values > div {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 1rem;
  padding: .65rem 0;
  border-bottom: 1px solid var(--border);
}
.key-values dt { font-weight: 600; }
.key-values dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.grid, .arm-list { display: block; }
.card { padding: 1.25rem 0; border-top: 1px solid var(--border); }
.card:last-child { border-bottom: 1px solid var(--border); }
.card h3, .card h4 { margin-top: 0; }
.status { font-weight: 700; }
.status.invalid { color: #8a1c1c; }
.status.draft { color: #6b5b00; }
.status.ready { color: #1f4e79; }
.status.awaiting-review, .status.partial { color: #8a4b00; }
.status.evidence-complete, .status.closed { color: #1d5e2f; }
.headline { color: #8a1c1c; font-size: 14px; }
.excerpt { max-height: 22rem; }
.design > summary { font-size: 1.05rem; }
details { margin: .75rem 0; }
summary { cursor: pointer; font-weight: 600; }
.table-wrap { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 16px; }
th, td { padding: .65rem .75rem; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
th { border-bottom: 2px solid var(--text); }
.run-table, .evidence-table { width: 100%; overflow-x: auto; border-top: 2px solid var(--text); }
.run-header, .run-evidence > summary {
  display: grid;
  grid-template-columns: minmax(180px, 1.4fr) minmax(150px, 1fr) 90px 110px 120px 110px;
  gap: 1rem;
  min-width: 900px;
  align-items: center;
}
.run-header, .evidence-header { padding: .65rem .75rem; font-size: 16px; font-weight: 700; }
.run-evidence { min-width: 900px; margin: 0; border-top: 1px solid var(--border); }
.run-evidence > summary, .evidence-row > summary { padding: .7rem .75rem; font-weight: 400; }
.run-evidence > summary:hover, .evidence-row > summary:hover { background: #f6f6f6; }
.run-details { padding: 1rem .75rem 1.5rem; border-top: 1px solid var(--border); }
.run-meta { display: flex; flex-wrap: wrap; max-width: none; gap: .25rem 1.5rem; color: var(--muted); }
.evidence-header, .evidence-row > summary {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 120px 120px;
  gap: 1rem;
  min-width: 520px;
  align-items: center;
}
.evidence-row { min-width: 520px; margin: 0; border-top: 1px solid var(--border); }
.evidence-details { padding: 1rem .75rem 1.5rem; border-top: 1px solid var(--border); }
.item-image { max-width: 100%; height: auto; border-radius: 6px; border: 1px solid #ddd; }
.evidence-details-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.evidence-details h3 { margin-top: 0; }
.evidence-details pre { margin: .5rem 0 0; }
pre {
  overflow: auto;
  padding: 1rem;
  border: 1px solid var(--border);
  background: #f6f6f6;
  color: var(--text);
  font: 16px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.notice { padding-left: 1rem; border-left: 3px solid var(--text); }
@media (max-width: 850px) {
  .page-shell { display: block; }
  .document-nav {
    position: static;
    height: auto;
    padding: 1.25rem;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  .document-nav > a { margin-bottom: .75rem; }
  .tree > li > ul { columns: 2; }
  main { padding: 2rem 1rem 4rem; }
  .evidence-details-grid { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  body { font-size: 17px; }
  .tree > li > ul { columns: 1; }
  .key-values > div { grid-template-columns: 130px minmax(0, 1fr); }
}
"""


def evidence_digest(eval_dir: Path) -> str:
    files = sorted(
        [*eval_dir.glob("runs/*/report.yml"), *eval_dir.glob("runs/*/items/*/result.yml")],
        key=lambda path: path.relative_to(eval_dir).as_posix().encode("utf-8"),
    )
    inventory = []
    for path in files:
        inventory.append(
            {
                "path": path.relative_to(eval_dir).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    from .packaging import inventory_digest

    return inventory_digest(inventory)


def build_case_data(case_dir: Path, *, include_text_artifacts: bool = True) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    case = load_yaml(case_dir / "case.yml")
    from .config import _validate_case

    case_errors: list[str] = []
    _validate_case(case, case_dir, case_errors)
    if case_errors:  # a broken case.yml is not one eval's problem: refuse, with the reasons
        raise ConfigError("Validation failed for case.yml:\n" + "\n".join(f"- {e}" for e in case_errors))
    publication = load_publication(case_dir, required=False) or {
        "name": case.get("name", case_dir.name),
        "publisher": "unpublished",
        "version": "unpublished",
        "title": case.get("title", case_dir.name),
        "summary": case.get("description", ""),
        "authors": [],
        "license": "not specified",
        "tags": [],
        "profile": "full",
    }
    profile = publication.get("profile", "full")
    content_digest = inventory_digest(build_inventory(collect_payload(case_dir, profile))) if (case_dir / "publication.yml").is_file() else None
    experiments: dict[str, dict[str, Any]] = {}
    totals = {
        "planned_runs": 0, "banked_runs": 0, "owed_runs": 0,
        "completed_items": 0, "selected_items": 0, "failed_items": 0,
        "input_tokens": 0, "output_tokens": 0,
    }
    known_cost = 0.0
    cost_complete = True

    for eval_path in sorted(case_dir.glob("experiments/*/evals/*/eval.yml")):
        experiment_dir = eval_path.parents[2]
        experiment_id = experiment_dir.name
        experiment = experiments.setdefault(
            experiment_id,
            {
                "id": experiment_id,
                "title": experiment_id.replace("-", " ").title(),
                "overview": _read_optional(experiment_dir / "README.md"),
                "evals": [],
            },
        )
        if profile == "report-only" or (case_dir / "package-view.yml").is_file():
            eval_data = _view_only_eval(eval_path)
            rows = eval_data["rows"]
            totals["banked_runs"] += len(rows)
            for row in rows:
                totals["selected_items"] += int(row.get("items", 0))
                totals["completed_items"] += int(row.get("completed_items", 0))
                totals["failed_items"] += int(row.get("failed_items", 0))
                if row.get("total_cost_usd") is None:
                    cost_complete = False
                else:
                    known_cost += float(row["total_cost_usd"])
                totals["input_tokens"] += int(row.get("input_tokens") or 0)
                totals["output_tokens"] += int(row.get("output_tokens") or 0)
        else:
            try:
                context = load_eval_context_from_path(_project_root_for_case(case_dir), eval_path)
                plan = build_plan(context)
                summary = status_summary(plan)
                rows = comparison_rows(plan)
                counts = summary["counts"]
                eval_data = _eval_data(
                    context, plan, summary, rows, include_text_artifacts=include_text_artifacts
                )
            except Exception as exc:  # noqa: BLE001 - one broken eval must not blank the page
                eval_data = _invalid_eval(eval_path, exc, internal=not isinstance(exc, TamesuError))
                rows, counts = [], {"planned": 0, "banked": 0, "owed": 0}
            totals["planned_runs"] += counts["planned"]
            totals["banked_runs"] += counts["banked"]
            totals["owed_runs"] += counts["owed"]
            for row in rows:
                totals["selected_items"] += int(row["items"])
                totals["completed_items"] += int(row["completed_items"])
                totals["failed_items"] += int(row["failed_items"])
                if row["total_cost_usd"] is None:
                    cost_complete = False
                else:
                    known_cost += float(row["total_cost_usd"])
                totals["input_tokens"] += int(row.get("input_tokens") or 0)
                totals["output_tokens"] += int(row.get("output_tokens") or 0)
        experiment["evals"].append(eval_data)

    data = {
        "schema_version": 1,
        "tamesu_version": __version__,
        "case": {
            "name": case.get("name", case_dir.name),
            "title": publication.get("title") or case.get("title"),
            "summary": publication.get("summary") or case.get("description"),
            "description": case.get("description", ""),
            "business_use": case.get("business_use", ""),
            "current_problem": case.get("current_problem", ""),
            "technical_uncertainty": case.get("technical_uncertainty", ""),
            "readme": _read_optional(case_dir / "README.md"),
        },
        "publication": publication,
        "content_digest": content_digest,
        "profile": profile,
        "view_only": profile == "report-only" or (case_dir / "package-view.yml").is_file(),
        "datasets": _case_datasets(experiments),
        "experiments": list(experiments.values()),
        "totals": {**totals, "cost_usd": known_cost if cost_complete else None, "known_cost_usd": known_cost},
    }
    return _clean_tree(data)


def present_case(
    case_dir: Path,
    output_dir: Path | None = None,
    *,
    strict: bool = False,
    include_text_artifacts: bool = True,
) -> Path:
    """Render the case to static HTML. Offline and read-only: it writes only under the output.

    `strict` still writes the page, then raises if any eval was invalid (for CI).
    `include_text_artifacts=False` withholds logs and source from the rendering (public sites).
    """
    case_dir = case_dir.resolve()
    data = build_case_data(case_dir, include_text_artifacts=include_text_artifacts)
    if output_dir is None:
        project_root = _project_root_for_case(case_dir)
        destination = (
            project_root / "build" / "present" / str(data["case"]["name"])
        ).resolve()
    else:
        destination = output_dir.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (temporary / "assets").mkdir(parents=True)
        (temporary / "experiments").mkdir(parents=True)
        write_text(temporary / "assets" / "style.css", CSS)
        if not data["view_only"]:
            _copy_dataset_assets(case_dir, temporary / "assets" / "data")
            _copy_assets(case_dir, data, temporary)
        write_json(temporary / "data.json", data)
        write_text(temporary / "index.html", _case_html(data))
        for experiment in data["experiments"]:
            write_text(temporary / "experiments" / f"{experiment['id']}.html", _experiment_html(data, experiment))
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    if strict:
        invalid = [
            f"{experiment['id']}/{e['id']}: {'; '.join((e.get('lifecycle') or {}).get('errors', []))}"
            for experiment in data["experiments"]
            for e in experiment["evals"]
            if e.get("status") == lc.INVALID
        ]
        if invalid:
            raise TamesuError(
                f"Rendered {destination}, but {len(invalid)} eval(s) are invalid:\n- " + "\n- ".join(invalid)
            )
    return destination


def _copy_assets(case_dir: Path, data: dict[str, Any], destination_root: Path) -> None:
    """Copy files items asked to show. Sources must lie in the case; targets stay under assets/."""
    root = case_dir.resolve()
    base = (destination_root / "assets").resolve()
    for experiment in data.get("experiments", []):
        for evaluation in experiment.get("evals", []):
            for item in evaluation.get("items", []):
                for entry in item.pop("copy", []) or []:
                    source = Path(entry["source"]).resolve()
                    target = (destination_root / entry["target"]).resolve()
                    try:
                        source.relative_to(root)
                        target.relative_to(base)
                    except ValueError:
                        continue
                    if not source.is_file():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    limit = int(entry.get("max_bytes") or 0)
                    if limit:
                        with source.open("rb") as stream:
                            payload = stream.read(limit + 1)
                        if len(payload) > limit:
                            payload = payload[:limit] + b"\n[truncated: file is larger than the presentation limit]\n"
                        text = blocks.sanitize_text(payload.decode("utf-8", errors="replace"))
                        target.write_text(text, encoding="utf-8")
                    else:
                        shutil.copyfile(source, target)


def _invalid_eval(eval_path: Path, exc: Exception, *, internal: bool = False) -> dict[str, Any]:
    """A card for an eval that cannot be loaded: the reason, and whatever raw fields are readable."""
    root = str(eval_path.parents[6]) + "/"
    message = str(exc).replace(root, "")  # never print the author's local directory into a page
    errors = [blocks.sanitize_text(line[2:].strip()) for line in message.splitlines() if line.startswith("- ")] or [
        blocks.sanitize_text(message.splitlines()[0] if message else type(exc).__name__)
    ]
    try:
        raw = load_yaml(eval_path)
    except Exception:  # noqa: BLE001
        raw = {}
    state = lc.invalid(errors)
    if internal:
        # Not the author's mistake: say so, so nobody hunts for a problem in their manifest.
        errors = [f"internal error while building this page: {type(exc).__name__}: {blocks.sanitize_text(str(exc).replace(root, ''))}"]
        state = lc.Lifecycle(
            lc.INVALID, lc.LABELS[lc.INVALID],
            "Tamesu could not build this eval's page because of an internal error, not because the eval is "
            "invalid. Please report it.", errors=tuple(errors),
        )
    eval_id = eval_path.parent.name
    return {
        "id": eval_id,
        "eval_id": "/".join((eval_path.parents[4].name, eval_path.parents[2].name, eval_id)),
        "question": raw.get("question") or "(unreadable)",
        "description": raw.get("description") or "",
        "status": state.state,
        "lifecycle": state.as_dict(),
        "primary_metric": (raw.get("metrics") or {}).get("primary") if isinstance(raw.get("metrics"), dict) else None,
        "observed_outcome": {"summary": "This eval could not be loaded, so nothing can be shown for it.", "caveat": state.reason},
        "leader": None,
        "coverage": {},
        "rows": [],
        "dataset": {"name": None, "description": None, "items": None, "path": raw.get("dataset")},
        "method": {
            "task": raw.get("task"),
            "arms": raw.get("arms") if isinstance(raw.get("arms"), list) else [],
            "runs": [],
            "defaults": {},
            "metrics": raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
            "output_schema": raw.get("output_schema"),
            "prompts": [],
        },
        "items": [],
        "evidence_runs": [],
        "analysis": None,
        "facts": [],
        "design": None,
        "reproduce": None,
    }


def _eval_data(
    context: Any,
    plan: Any,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    include_text_artifacts: bool = True,
) -> dict[str, Any]:
    evaluation = context.evaluation
    counts = summary["counts"]
    state = lc.lifecycle_for(context, plan, counts)
    primary = evaluation["metrics"]["primary"]
    task = task_for(evaluation, context.case)
    measured = [row for row in rows if isinstance(row.get("primary_mean"), (int, float))]
    reverse = primary not in LOWER_IS_BETTER_METRICS
    # A leader is named only when the result is decided: not while evidence is owed, not from a
    # single sample, and never before anything has run.
    leader = (
        sorted(measured, key=lambda row: float(row["primary_mean"]), reverse=reverse)[0]
        if measured and state.state not in lc.NO_RESULT_STATES and not state.pending_items and not single_sample(measured)
        else None
    )
    for row in rows:
        row.update(_token_totals(context.eval_dir, row.get("run_ids", [])))
    analysis = _analysis_data(context.eval_dir)
    items: list[dict[str, Any]] = []
    evidence_runs: list[dict[str, Any]] = []
    dataset_items = {
        str(item.get("id")): item
        for item in context.dataset.get("items", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    for spec in plan.specs:
        run_id = plan.banked_run_ids.get(spec.specification_fingerprint)
        if run_id is None:
            continue
        run_dir = context.eval_dir / "runs" / run_id
        run = load_yaml(run_dir / "run.yml")
        resolved = run.get("resolved")
        if not isinstance(resolved, dict):
            resolved = {}
        run_items: list[dict[str, Any]] = []
        report_items: dict[str, dict[str, Any]] = {}
        if (run_dir / "report.yml").is_file():
            report_items = {
                str(entry.get("item_id")): entry
                for entry in load_yaml(run_dir / "report.yml").get("items", [])
                if isinstance(entry, dict)
            }
        for result_path in sorted(run_dir.glob("items/*/result.yml")):
            result = load_yaml(result_path)
            output_path = result_path.parent / "output.txt"
            dataset_item = dataset_items.get(result_path.parent.name, {})
            view = present_item(
                task.name,
                blocks.ItemContext(
                    eval_dir=context.eval_dir,
                    case_dir=context.case_dir,
                    item_dir=result_path.parent,
                    run_id=run_id,
                    result=result,
                    item=dataset_item,
                    report_entry=report_items.get(result_path.parent.name, {}),
                    include_text_artifacts=include_text_artifacts,
                )
            )
            item_data = {
                "run_id": run_id,
                "model": resolved.get("model"),
                "arm": resolved.get("arm"),
                "repetition": resolved.get("repetition"),
                "item_id": result_path.parent.name,
                "state": result.get("state"),
                "scores": result.get("mechanical_scores", result.get("scores", {})),
                "input": dataset_item.get("input"),
                "expected": dataset_item.get("expected"),
                "assets": dataset_item.get("assets", []),
                "output": _clean_untrusted(output_path.read_text(encoding="utf-8", errors="replace")) if output_path.is_file() else None,
                "view": view.as_dict(),
                "copy": view.copy,
            }
            items.append(item_data)
            run_items.append(item_data)
        selection = run.get("selection")
        selected_ids = selection.get("item_ids", []) if isinstance(selection, dict) else []
        run_totals = run.get("totals")
        if not isinstance(run_totals, dict):
            run_totals = {}
        usage = _token_totals(context.eval_dir, [run_id])
        evidence_runs.append(
            {
                "run_id": run_id,
                "model": resolved.get("model"),
                "arm": resolved.get("arm"),
                "repetition": resolved.get("repetition"),
                "state": run.get("state"),
                "selected_items": len(selected_ids) if isinstance(selected_ids, list) else len(run_items),
                "completed_items": sum(item.get("state") == "complete" for item in run_items),
                "failed_items": sum(item.get("state") == "failed" for item in run_items),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cost_usd": run_totals.get("cost_usd"),
                "duration_ms": run_totals.get("duration_ms"),
                "execution": _execution_provenance(run),
                "items": run_items,
            }
        )
    prompts: list[dict[str, str]] = []
    for arm in evaluation.get("arms", []):
        for role, relative in (arm.get("prompts") or {}).items():
            path = (context.eval_dir / relative).resolve()
            prompts.append({"arm": str(arm.get("id")), "role": str(role), "path": relative, "text": _clean_untrusted(path.read_text(encoding="utf-8", errors="replace"))})
    cost = estimate_plan_cost(plan)
    required_environment = sorted(
        {
            key
            for spec in plan.specs
            for key in PROVIDER_KEYS.get(
                str(spec.provider),
                (f"{str(spec.provider).upper().replace('-', '_')}_API_KEY",),
            )
        }
    )
    review = bool(task.supports_review)
    if state.state in lc.NO_RESULT_STATES:
        outcome = {
            "summary": "Nothing has run yet, so this eval has no result. The design below is what will be run.",
            "caveat": state.reason,
        }
    elif review:
        outcome = _image_observed_outcome(plan)
    else:
        outcome = _observed_outcome(rows, primary)
    facts = computed_facts(plan, review=review) if plan.banked_run_ids else []
    return {
        "id": context.eval_dir.name,
        "eval_id": context.eval_id,
        "question": evaluation.get("question"),
        "description": evaluation.get("description"),
        "status": state.state,
        "lifecycle": state.as_dict(),
        "primary_metric": primary,
        "observed_outcome": outcome,
        "leader": {"model": leader["model"], "arm": leader["arm"], "value": leader["primary_mean"]} if leader else None,
        "coverage": counts,
        "rows": rows,
        "dataset": {
            "name": context.dataset.get("name"),
            "description": context.dataset.get("description"),
            "items": len(context.dataset.get("items", [])),
            "path": evaluation.get("dataset"),
        },
        "method": {
            "task": evaluation.get("task", context.case.get("default_task")),
            "arms": evaluation.get("arms", []),
            "runs": evaluation.get("runs", []),
            "defaults": evaluation.get("defaults", {}),
            "metrics": evaluation.get("metrics", {}),
            "output_schema": evaluation.get("output_schema"),
            "prompts": prompts,
        },
        "items": items,
        "evidence_runs": evidence_runs,
        "analysis": analysis,
        "facts": facts,
        "design": build_design(context, plan, state),
        "reproduce": {
            "commands": [f"tamesu plan {context.eval_id}", f"tamesu run {context.eval_id}", f"tamesu rescore <run-id>"],
            "environment": required_environment,
            "estimated_cost_usd": cost.estimated_usd if cost.fully_priced else None,
            "maximum_cost_usd": cost.maximum_usd if cost.fully_priced else None,
        },
    }


def _execution_provenance(run: dict[str, Any]) -> dict[str, Any] | None:
    execution = run.get("execution")
    if not isinstance(execution, dict):
        return None
    logs = [
        {"attempt": entry.get("attempt"), "status": entry.get("status"), "sha256": entry.get("sha256"), "samples": entry.get("samples")}
        for entry in execution.get("logs", [])
        if isinstance(entry, dict)
    ]
    return {
        "backend": execution.get("backend"),
        "inspect_version": execution.get("inspect_version"),
        "adapter_version": execution.get("adapter_version"),
        "model_uri": execution.get("model_uri"),
        "base_url_origin": execution.get("base_url_origin"),
        "logs": logs,
    }


def _view_only_eval(eval_path: Path) -> dict[str, Any]:
    evaluation = load_yaml(eval_path)
    rows: list[dict[str, Any]] = []
    for report_path in sorted(eval_path.parent.glob("runs/*/report.yml")):
        report = load_yaml(report_path)
        manifest_path = report_path.parent / "run.yml"
        manifest = load_yaml(manifest_path) if manifest_path.is_file() else {}
        rows.append(
            {
                "model": manifest.get("model", report.get("model", "unknown")),
                "arm": manifest.get("arm_id", report.get("arm_id", "unknown")),
                "repetitions": 1,
                "items": report.get("completion", {}).get("selected_items", 0),
                "completed_items": report.get("completion", {}).get("completed_items", 0),
                "failed_items": report.get("completion", {}).get("failed_items", 0),
                "primary_metric": evaluation.get("metrics", {}).get("primary"),
                "primary_mean": report.get("metrics", {}).get(evaluation.get("metrics", {}).get("primary")),
                "metrics": report.get("metrics", {}),
                "total_cost_usd": report.get("totals", {}).get("cost_usd"),
                "run_ids": [report.get("run_id", report_path.parent.name)],
                **_token_totals(eval_path.parent, [report_path.parent.name]),
            }
        )
    primary = evaluation.get("metrics", {}).get("primary")
    measured = [row for row in rows if isinstance(row.get("primary_mean"), (int, float))]
    reverse = primary not in LOWER_IS_BETTER_METRICS
    leader = (
        sorted(measured, key=lambda row: float(row["primary_mean"]), reverse=reverse)[0]
        if measured and not single_sample(measured)
        else None
    )
    closed = evaluation.get("status") == "complete"
    view_state = lc.Lifecycle(
        lc.CLOSED if closed else lc.EVIDENCE_COMPLETE if rows else lc.DRAFT,
        lc.LABELS[lc.CLOSED if closed else lc.EVIDENCE_COMPLETE if rows else lc.DRAFT],
        "Report-only package: outputs and review state are withheld.",
    )
    return {
        "id": eval_path.parent.name,
        "eval_id": "/".join((eval_path.parents[4].name, eval_path.parents[2].name, eval_path.parent.name)),
        "question": evaluation.get("question"), "description": evaluation.get("description"),
        "status": view_state.state, "lifecycle": view_state.as_dict(), "facts": [], "design": None,
        "primary_metric": primary,
        "observed_outcome": _observed_outcome(rows, primary),
        "leader": {"model": leader["model"], "arm": leader["arm"], "value": leader["primary_mean"]} if leader else None,
        "coverage": {}, "rows": rows, "dataset": {"name": "withheld", "description": None, "items": None, "path": None},
        "method": {"task": evaluation.get("task"), "arms": evaluation.get("arms", []), "runs": evaluation.get("runs", []), "defaults": evaluation.get("defaults", {}), "metrics": evaluation.get("metrics", {}), "output_schema": evaluation.get("output_schema"), "prompts": []},
        "items": [], "evidence_runs": [], "analysis": _analysis_data(eval_path.parent), "reproduce": None,
    }


def _analysis_data(eval_dir: Path) -> dict[str, Any] | None:
    """The authored or model-assisted interpretation, labelled with who wrote it."""
    path = eval_dir / "analysis.md"
    if not path.is_file():
        return None
    source = path.read_text(encoding="utf-8", errors="replace")
    metadata, body = split_front_matter(source)
    body = strip_generated_facts(body)  # older files embedded a generated section; the live one replaces it
    digest = evidence_digest(eval_dir)
    matches = metadata.get("evidence_digest") == digest
    assisted = metadata.get("generated_by") == "model"
    unfinished = any(re.search(r"\bTODO\b", line) for line in body.splitlines())
    if assisted:
        who = f"Model-assisted analysis ({metadata.get('model', 'unknown model')}), written {metadata.get('created_at', 'at an unknown time')}"
        label = f"{who}; matches the shown evidence" if matches else f"{who}; STALE: the evidence changed after it was written"
    elif unfinished:
        label = "Draft analysis: unfinished (TODO lines remain)"
    elif matches:
        label = "Analysis, matches the shown evidence"
    else:
        label = "Author commentary, not verified against these results"
    return {
        "kind": "assisted" if assisted else "authored",
        "label": label,
        "matches_evidence": matches,
        "evidence_digest": digest,
        "declared_evidence_digest": metadata.get("evidence_digest"),
        "generated_by": metadata.get("generated_by"),
        "model": metadata.get("model"),
        "created_at": metadata.get("created_at"),
        "body": _clean_untrusted(body),
    }


def _case_html(data: dict[str, Any]) -> str:
    case = data["case"]
    publication = data["publication"]
    cards = []
    for experiment in data["experiments"]:
        evals = "".join(
            f"<li>{_badge(e)} — {_e(e['question'])}"
            + (f" — leader: {_e(e['leader']['model'])} / {_e(e['leader']['arm'])} ({_number(e['leader']['value'])})" if e.get("leader") else "")
            + f"<br><span class=\"muted\">{_e((e.get('lifecycle') or {}).get('reason'))}</span></li>"
            for e in experiment["evals"]
        )
        summary = lc.summarize([str(e["status"]) for e in experiment["evals"]])
        cards.append(f"<article class=\"card\"><h3><a href=\"experiments/{_attr(experiment['id'])}.html\">{_e(experiment['title'])}</a></h3><p class=\"muted\">{_e(summary)}</p><ul>{evals}</ul></article>")
    lineage = publication.get("forked_from")
    package_rows: list[tuple[str, Any]] = [
        ("Publisher", publication.get("publisher")),
        ("Package", publication.get("name")),
        ("Version", publication.get("version")),
        ("License", publication.get("license")),
        ("Profile", data["profile"]),
        ("Content digest", data.get("content_digest") or "unpackaged"),
        ("Tamesu version", data["tamesu_version"]),
    ]
    if isinstance(lineage, dict):
        package_rows.append(
            (
                "Forked from",
                f"{lineage.get('publisher')}/{lineage.get('name')} "
                f"{lineage.get('version')} · {lineage.get('content_digest')}",
            )
        )
    evidence_rows = [
        ("Planned runs", data["totals"]["planned_runs"]),
        ("Banked runs", data["totals"]["banked_runs"]),
        ("Runs owed", data["totals"]["owed_runs"]),
        ("Selected items", data["totals"]["selected_items"]),
        ("Completed items", data["totals"]["completed_items"]),
        ("Failed items", data["totals"]["failed_items"]),
        ("Input tokens", f"{data['totals']['input_tokens']:,}"),
        ("Output tokens", f"{data['totals']['output_tokens']:,}"),
        ("Recorded cost", _money(data["totals"]["cost_usd"])),
    ]
    trust = "Outputs and datasets are withheld; the reported numbers cannot be reproduced from this package." if data["view_only"] else ("This package contains outputs that can be rescored." if data["profile"] == "rescorable" else "This package contains the evidence needed to inspect, rescore, and re-run the case.")
    dataset_cards: list[str] = []
    for dataset in data["datasets"]:
        dataset_cards.append(
            f"<article class=\"card\" id=\"dataset-{_attr(_fragment(dataset.get('name')))}\">"
            f"<h3>{_e(dataset.get('name'))}</h3>"
            f"<p>{_e(dataset.get('description') or 'Dataset details are withheld by this package profile.')}</p>"
            f"<p class=\"muted\">{_e(dataset.get('items')) if dataset.get('items') is not None else 'Unknown'} items</p></article>"
        )
    datasets = "".join(dataset_cards)
    body = f"""
<header><p class=\"document-type\">Tamesu case study</p><h1>{_e(case['title'])}</h1><p>{_e(case['summary'])}</p></header>
<section id=\"case-overview\"><h2>Case overview</h2><p>{_e(case['description'])}</p><h3>Business use</h3><p>{_e(case['business_use'])}</p><h3>Current problem</h3><p>{_e(case['current_problem'])}</p><h3>Technical uncertainty</h3><p>{_e(case['technical_uncertainty'])}</p></section>
<section id=\"experiments\"><h2>Experiments</h2><div class=\"grid\">{''.join(cards) or '<p>No experiments found.</p>'}</div></section>
<section id=\"datasets\"><h2>Datasets</h2><div class=\"grid\">{datasets or '<p>No datasets are used by this case.</p>'}</div></section>
<section id=\"evidence-coverage\"><h2>Evidence coverage</h2>{_key_values(evidence_rows)}</section>
<section id=\"package-information\"><h2>Package information</h2>{_key_values(package_rows)}</section>
<section id=\"trust\"><h2>Trust and verification</h2><p class=\"notice\">{_e(trust)}</p></section>"""
    return _document(
        str(case["title"]), body, "assets/style.css", _navigation(data)
    )


def _badge(evaluation: dict[str, Any]) -> str:
    state = evaluation.get("lifecycle") or {}
    label = state.get("label") or str(evaluation.get("status", "")).replace("-", " ").title()
    return f"<span class=\"status {_attr(evaluation.get('status'))}\">{_e(label)}</span>"


def _facts_html(lines: list[str]) -> str:
    """Computed facts: "- " bullets with optional "  - " children, rendered as a nested list."""
    top: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("  - "):
            if top:
                top[-1][1].append(line[4:].strip())
        elif line.startswith("- "):
            top.append((line[2:].strip(), []))
    items = ""
    for text, children in top:
        kids = "<ul>" + "".join(f"<li>{_inline_markup(c)}</li>" for c in children) + "</ul>" if children else ""
        items += f"<li>{_inline_markup(text)}{kids}</li>"
    return f"<ul>{items}</ul>" if items else ""


def _design_html(evaluation: dict[str, Any]) -> str:
    design = evaluation.get("design")
    if not design:
        return ""
    eid = evaluation["id"]
    open_attr = " open" if evaluation["status"] in lc.NO_RESULT_STATES else ""
    matrix_rows = "".join(
        f"<tr><td>{_e(r['model'])}</td><td>{_e(r['provider'])}</td><td><code>{_e(r['arm'])}</code></td>"
        f"<td>{_e(r['repetitions'])}</td><td>{_e(r['items'])}</td><td>{_e(r['banked'])} / {_e(r['owed'])}</td>"
        f"<td><code>{_e(json.dumps(r['parameters'], sort_keys=True))}</code></td></tr>"
        for r in design["matrix"]
    )
    matrix = (
        '<div class="table-wrap"><table><thead><tr><th>Model</th><th>Provider</th><th>Arm</th><th>Repetitions</th>'
        f'<th>Items</th><th>Banked / owed</th><th>Parameters</th></tr></thead><tbody>{matrix_rows}</tbody></table></div>'
    )
    shown = design["items"]["shown"]
    inline = shown[:25]
    item_rows = "".join(
        f"<tr><td><code>{_e(i['id'])}</code></td><td>{_e(_short(i['input']))}</td><td>{_e(_short(i['expected']))}</td></tr>"
        for i in inline
    )
    more = design["items"]["total"] - len(inline)
    items_html = (
        f'<p>{_e(design["items"]["total"])} item(s).</p><div class="table-wrap"><table><thead><tr><th>Item</th><th>Input</th>'
        f'<th>Expected</th></tr></thead><tbody>{item_rows}</tbody></table></div>'
        + (f'<p class="muted">{_e(more)} more item(s) are in the dataset file.</p>' if more > 0 else "")
    )
    previews = ""
    for preview in design["prompt_preview"]:
        if "error" in preview:
            previews += f'<article class="card"><h4><code>{_e(preview["arm"])}</code></h4><p class="notice">This arm cannot render its prompt: {_e(preview["error"])}</p></article>'
            continue
        roles = "".join(
            f"<details><summary>{_e(role.title())} · item <code>{_e(preview['item_id'])}</code></summary><pre>{_e(text)}</pre></details>"
            for role, text in preview["prompts"].items()
        )
        previews += f'<article class="card"><h4><code>{_e(preview["arm"])}</code></h4><p class="muted">Exactly what a model is sent for the first item.</p>{roles}</article>'
    scoring = design["scoring"]
    mech = "".join(
        f"<li><code>{_e(m['name'])}</code>" + (f" <span class=\"muted\">{_e(json.dumps(m['parameters'], sort_keys=True))}</span>" if m["parameters"] else "") + "</li>"
        for m in scoring["mechanical"]
    )
    review = scoring.get("human_review")
    if review and "error" in review:
        review_html = f'<p class="notice">Review rubric cannot be loaded: {_e(review["error"])}</p>'
    elif review:
        dims = "".join(
            f"<tr><td><code>{_e(d['id'])}</code></td><td>{_e(d['question'])}</td><td>{_e(', '.join(d['reason_codes']))}</td></tr>"
            for d in review["dimensions"]
        )
        review_html = (
            f'<h4>Blinded human review: <code>{_e(review["rubric"])}</code></h4><div class="table-wrap"><table><thead><tr><th>Dimension</th>'
            f'<th>Question</th><th>Failure reasons</th></tr></thead><tbody>{dims}</tbody></table></div>'
        )
    else:
        review_html = ""
    acceptance = scoring.get("acceptance")
    acceptance_html = (
        f'<p>Acceptance requires: {_e(", ".join(acceptance["requires"]))}; {_e(acceptance["required_reviews_per_item"])} '
        f'review(s) per item; disagreement: {_e(acceptance["on_disagreement"])}.</p>'
        if acceptance
        else ""
    )
    judges = scoring.get("model_judges") or []
    judges_html = (
        "<p>Model judges: " + ", ".join(f"<code>{_e(j['id'])}</code> ({_e(j['role'])})" for j in judges) + "</p>" if judges else ""
    )
    execution = design["execution"]
    exec_rows: list[tuple[str, Any]] = [("Backend", execution["backend"])]
    if execution["backend"] == "inspect":
        exec_rows += [
            ("Inspect task", f"{execution['file']} :: {execution['task']}"),
            ("Inspect version", execution.get("inspect_version") or "not installed"),
            ("Declared sources", ", ".join(execution["sources"])),
            ("Limits", json.dumps(execution["limits"], sort_keys=True)),
            ("Artifact roles", json.dumps(execution["artifacts"], sort_keys=True)),
        ]
        for image in execution["images"]:
            exec_rows.append((f"Image ({image['source']})", f"{image['image']} — {'pinned' if image['pinned'] else 'MUTABLE TAG'}"))
        for route in execution["routes"]:
            exec_rows.append((f"Route {route['model']}", f"{route.get('model_uri')} at {route.get('base_url_origin') or 'provider default'}"))
        exec_rows.append(("Not checked by this page", ", ".join(execution["not_checked"])))
    else:
        for route in execution["routes"]:
            exec_rows.append((f"Provider for {route['model']}", route["provider"]))
    cost = design["cost"]
    cost_rows = [
        ("Budget ceiling", _money(cost["budget_usd"])),
        ("Estimated cost of the full design", _money(cost["design_estimated_usd"])),
        ("Maximum exposure of the full design", _money(cost["design_maximum_usd"])),
        ("Recorded so far", _money(cost["recorded_known_usd"])),
    ]
    if cost["unknown_pricing"]:
        cost_rows.append(("Unknown pricing", ", ".join(cost["unknown_pricing"])))
    blockers = design["blockers"]
    blockers_html = (
        '<p class="notice">Blockers: ' + "; ".join(_e(b) for b in blockers) + "</p>" if blockers else '<p class="muted">No static blockers.</p>'
    )
    return (
        f'<h2 id="{_attr(eid)}-design">Design</h2>'
        f'<details class="design"{open_attr}><summary>What will run, what is sent, how it is judged and what it can cost</summary>'
        f'<h3>Run matrix</h3>{matrix}<h3>Dataset</h3>{items_html}<h3>Prompts sent</h3>{previews or "<p>No arms.</p>"}'
        f'<h3>How it is judged</h3><ul>{mech}</ul>{judges_html}{review_html}{acceptance_html}'
        f'<h3>How it executes</h3>{_key_values(exec_rows)}<h3>Cost and blockers</h3>{_key_values(cost_rows)}{blockers_html}</details>'
    )


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False) if value is not None else ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _experiment_html(data: dict[str, Any], experiment: dict[str, Any]) -> str:
    sections: list[str] = []
    for evaluation in experiment["evals"]:
        state = evaluation.get("lifecycle") or {}
        reason = f'<p class="muted">{_e(state.get("reason"))}</p>' if state.get("reason") else ""
        modifiers = "".join(f"<li>{_e(m)}</li>" for m in state.get("modifiers", []))
        modifiers_html = f'<ul class="muted">{modifiers}</ul>' if modifiers else ""
        status_html = f"<p><strong>Status:</strong> {_badge(evaluation)}</p>{reason}{modifiers_html}"
        if evaluation["status"] == lc.INVALID:
            problems = "".join(f"<li>{_e(error)}</li>" for error in state.get("errors", []))
            sections.append(
                f'<section id="{_attr(evaluation["id"])}">{status_html}<h2>Hypothesis</h2><p class="lead">{_e(evaluation["question"])}</p>'
                f'<p>{_e(evaluation["description"])}</p><h2 id="{_attr(evaluation["id"])}-problems">Problems to fix</h2>'
                f'<p class="notice">This eval could not be loaded. The rest of the case is shown normally.</p><ul>{problems}</ul>'
                f'<h2 id="{_attr(evaluation["id"])}-arms">Arms</h2>{_arms_html(evaluation)}</section>'
            )
            continue
        rows = evaluation["rows"]
        metrics = [evaluation.get("primary_metric"), *evaluation.get("method", {}).get("metrics", {}).get("secondary", [])]
        headings = "".join(f"<th>{_e(metric)}</th>" for metric in metrics if metric)
        table_rows = ""
        for row in rows:
            cells = "".join(f"<td>{_number(row.get('metrics', {}).get(metric, row.get('primary_mean') if metric == evaluation.get('primary_metric') else None))}</td>" for metric in metrics if metric)
            table_rows += f"<tr><td>{_e(row.get('model'))}</td><td>{_e(row.get('arm'))}</td><td>{_e(row.get('repetitions'))}</td><td>{_e(row.get('completed_items'))}/{_e(row.get('items'))}</td>{cells}<td>{_e(row.get('input_tokens'))}</td><td>{_e(row.get('output_tokens'))}</td><td>{_money(row.get('total_cost_usd'))}</td></tr>"
        no_results = evaluation["status"] in lc.NO_RESULT_STATES
        if not table_rows:
            message = "Nothing has run yet; see Design above." if no_results else "No compatible completed evidence."
            table_rows = f"<tr><td colspan=\"{7 + len([m for m in metrics if m])}\">{_e(message)}</td></tr>"
        arms = _arms_html(evaluation)
        evidence_runs = _run_evidence_html(evaluation.get("evidence_runs", []))
        analysis = evaluation.get("analysis")
        analysis_html = (
            f"<h2 id=\"{_attr(evaluation['id'])}-conclusion\">Conclusion and next experiments</h2>"
            f"<p class=\"muted\">{_e(analysis['label'])}</p>{_markup(analysis['body'])}"
            if analysis
            else ""
        )
        facts = evaluation.get("facts") or []
        facts_html = (
            f"<h2 id=\"{_attr(evaluation['id'])}-facts\">Evidence at a glance</h2>"
            "<p class=\"muted\">Computed from the stored evidence each time this page is built. "
            "It is not saved in any file, so it cannot be out of date.</p>" + _facts_html(facts)
            if facts
            else ""
        )
        outcome = evaluation.get("observed_outcome", {})
        reproduce = evaluation.get("reproduce")
        if reproduce:
            commands = "\n".join(reproduce["commands"])
            env = ", ".join(reproduce["environment"]) or "none"
            cost_text = (
                f"Estimated cost of the remaining runs: {_money(reproduce['estimated_cost_usd'])}; maximum additional exposure: {_money(reproduce['maximum_cost_usd'])}."
                if (evaluation.get("coverage") or {}).get("owed")
                else "No runs are owed, so there is no additional cost to reproduce what is banked."
            )
            reproduce_html = f"<h2>Reproduce</h2><pre>{_e(commands)}</pre><p>Required environment variable names: <code>{_e(env)}</code>. {_e(cost_text)}</p>"
        else:
            reproduce_html = "<h2>Reproduce</h2><p class=\"notice\">Unavailable for a report-only package.</p>"
        coverage = evaluation.get("coverage", {})
        limitations = [f"{coverage.get('owed', 0)} planned runs are still owed.", f"{coverage.get('failed', 0)} failed runs and {coverage.get('stale', 0)} stale runs are excluded."]
        dataset_description = evaluation["dataset"].get("description")
        dataset_description_html = (
            f"<p>{_e(dataset_description)}</p>" if dataset_description else ""
        )
        sections.append(f"""
<section id=\"{_attr(evaluation['id'])}\">{status_html}<h2>Hypothesis</h2><p class=\"lead\">{_e(evaluation['question'])}</p><p>{_e(evaluation['description'])}</p>
<h2 id=\"{_attr(evaluation['id'])}-method\">Method</h2><p>Dataset: <code>{_e(evaluation['dataset'].get('name'))}</code> ({_e(evaluation['dataset'].get('items'))} items). Primary metric: <code>{_e(evaluation.get('primary_metric'))}</code>.</p>{dataset_description_html}{_design_html(evaluation)}<h2 id=\"{_attr(evaluation['id'])}-arms\">Arms</h2>{arms}
<h2 id=\"{_attr(evaluation['id'])}-outcome\">Observed outcome</h2><p class=\"lead\">{_e(outcome.get('summary'))}</p><p>{_e(outcome.get('caveat'))}</p>
<h2 id=\"{_attr(evaluation['id'])}-results\">Results</h2><div class=\"table-wrap\"><table><thead><tr><th>Model</th><th>Arm</th><th>Repetitions</th><th>Coverage</th>{headings}<th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead><tbody>{table_rows}</tbody></table></div>
{facts_html}{analysis_html}<h2 id=\"{_attr(evaluation['id'])}-evidence\">Run evidence</h2>{evidence_runs or '<p>No run outputs are included.</p>'}
<h2 id=\"{_attr(evaluation['id'])}-limitations\">Limitations and coverage</h2><ul>{''.join(f'<li>{_e(value)}</li>' for value in limitations)}</ul>{reproduce_html}</section>""")
    body = f"<header id=\"experiment-overview\"><p><a href=\"../index.html\">← {_e(data['case']['title'])}</a></p><p class=\"document-type\">Experiment</p><h1>{_e(experiment['title'])}</h1></header>{''.join(sections)}"
    return _document(
        str(experiment["title"]),
        body,
        "../assets/style.css",
        _navigation(data, current_experiment=str(experiment["id"])),
    )


def _document(title: str, body: str, stylesheet: str, navigation: str) -> str:
    return f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'\"><title>{_e(title)}</title><link rel=\"stylesheet\" href=\"{_attr(stylesheet)}\"></head><body><div class=\"page-shell\">{navigation}<main>{body}</main></div></body></html>\n"


def _navigation(data: dict[str, Any], current_experiment: str | None = None) -> str:
    on_experiment_page = current_experiment is not None
    case_page = "../index.html" if on_experiment_page else "index.html"
    case_items = [
        ("Case overview", "case-overview"),
        ("Datasets", "datasets"),
        ("Evidence coverage", "evidence-coverage"),
        ("Package information", "package-information"),
        ("Trust and verification", "trust"),
    ]
    case_links = "".join(
        f"<li><a href=\"{_attr(case_page)}#{_attr(fragment)}\">{_e(label)}</a></li>"
        for label, fragment in case_items
    )
    dataset_links = "".join(
        f"<li><a href=\"{_attr(case_page)}#dataset-{_attr(_fragment(dataset.get('name')))}\">"
        f"{_e(dataset.get('name'))}</a></li>"
        for dataset in data.get("datasets", [])
    )
    experiment_items: list[str] = []
    for experiment in data.get("experiments", []):
        experiment_id = str(experiment.get("id"))
        experiment_page = (
            f"{_attr(experiment_id)}.html"
            if on_experiment_page
            else f"experiments/{_attr(experiment_id)}.html"
        )
        current = ' aria-current="page"' if experiment_id == current_experiment else ""
        evaluation_items: list[str] = []
        for evaluation in experiment.get("evals", []):
            eval_id = str(evaluation.get("id"))
            eval_base = f"{experiment_page}#{_attr(eval_id)}"
            arms = "".join(
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-arm-{_attr(_fragment(arm.get('id')))}\">"
                f"{_e(arm.get('id'))}</a></li>"
                for arm in evaluation.get("method", {}).get("arms", [])
            )
            arm_branch = (
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-arms\">Arms</a>"
                f"<ul>{arms}</ul></li>"
                if arms
                else ""
            )
            conclusion_link = (
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-conclusion\">"
                "Conclusion and next experiments</a></li>"
                if evaluation.get("analysis")
                else ""
            )
            if evaluation.get("status") == lc.INVALID:
                evaluation_items.append(
                    f"<li><a href=\"{eval_base}\">{_e(eval_id.replace('-', ' ').title())}</a> "
                    f"<span class=\"muted\">(invalid)</span></li>"
                )
                continue
            design_link = (
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-design\">Design</a></li>"
                if evaluation.get("design")
                else ""
            )
            facts_link = (
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-facts\">Evidence at a glance</a></li>"
                if evaluation.get("facts")
                else ""
            )
            evaluation_items.append(
                f"<li><a href=\"{eval_base}\">{_e(eval_id.replace('-', ' ').title())}</a>"
                f"<ul><li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-method\">Method</a></li>"
                f"{design_link}{arm_branch}"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-outcome\">Observed outcome</a></li>"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-results\">Results</a></li>"
                f"{facts_link}{conclusion_link}"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-evidence\">Run evidence</a></li>"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-limitations\">Limitations</a></li>"
                f"</ul></li>"
            )
        experiment_items.append(
            f"<li><a href=\"{_attr(experiment_page)}#experiment-overview\"{current}>"
            f"{_e(experiment.get('title'))}</a><ul>{''.join(evaluation_items)}</ul></li>"
        )

    datasets_branch = f"<ul>{dataset_links}</ul>" if dataset_links else ""
    case_links = case_links.replace(
        f">Datasets</a></li>", f">Datasets</a>{datasets_branch}</li>"
    )
    return (
        f"<nav class=\"document-nav\" aria-label=\"Case study\">"
        f"<a href=\"{_attr(case_page)}\">{_e(data['case']['title'])}</a>"
        f"<ul class=\"tree\"><li><span class=\"tree-label\">Case</span><ul>{case_links}</ul></li>"
        f"<li><a class=\"tree-label\" href=\"{_attr(case_page)}#experiments\">Experiments</a>"
        f"<ul>{''.join(experiment_items)}</ul></li>"
        f"</ul></nav>"
    )


def _markup(value: Any) -> str:
    if not value:
        return ""
    lines = _clean_untrusted(str(value)).splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    def flush() -> None:
        if paragraph:
            output.append(
                f"<p>{_inline_markup(' '.join(part.strip() for part in paragraph))}</p>"
            )
            paragraph.clear()
    def flush_list() -> None:
        if list_items:
            output.append(
                "<ul>"
                + "".join(f"<li>{_inline_markup(item)}</li>" for item in list_items)
                + "</ul>"
            )
            list_items.clear()
    for line in lines:
        if not line.strip():
            flush(); flush_list()
        elif line.startswith("- "):
            flush(); list_items.append(line[2:].strip())
        elif line.startswith("### "):
            flush(); flush_list(); output.append(f"<h4>{_inline_markup(line[4:])}</h4>")
        elif line.startswith("## "):
            flush(); flush_list(); output.append(f"<h3>{_inline_markup(line[3:])}</h3>")
        elif line.startswith("# "):
            flush(); flush_list(); output.append(f"<h2>{_inline_markup(line[2:])}</h2>")
        else:
            flush_list()
            paragraph.append(line)
    flush(); flush_list()
    return "".join(output)


def _inline_markup(value: str) -> str:
    parts = re.split(r"(`[^`\n]+`)", value)
    return "".join(
        f"<code>{_e(part[1:-1])}</code>" if part.startswith("`") and part.endswith("`") else _e(part)
        for part in parts
    )


def _arms_html(evaluation: dict[str, Any]) -> str:
    method = evaluation.get("method", {})
    prompts_by_arm: dict[str, list[dict[str, str]]] = {}
    for prompt in method.get("prompts", []):
        prompts_by_arm.setdefault(str(prompt.get("arm")), []).append(prompt)

    cards: list[str] = []
    for arm in method.get("arms", []):
        arm_id = str(arm.get("id", "unknown"))
        prompt_details = "".join(
            f"<details><summary>{_e(prompt.get('role', '').title())} prompt · "
            f"<code>{_e(prompt.get('path'))}</code></summary>"
            f"<pre>{_e(prompt.get('text'))}</pre></details>"
            for prompt in prompts_by_arm.get(arm_id, [])
        )
        parameters = arm.get("parameters")
        parameters_html = (
            "<p><strong>Parameter overrides</strong></p>"
            f"<pre>{_e(json.dumps(parameters, sort_keys=True, indent=2))}</pre>"
            if isinstance(parameters, dict) and parameters
            else ""
        )
        prompt_content = prompt_details or '<p class="muted">No prompt files declared.</p>'
        cards.append(
            f"<article class=\"card\" id=\"{_attr(evaluation.get('id'))}-arm-{_attr(_fragment(arm_id))}\">"
            f"<h3><code>{_e(arm_id)}</code></h3>"
            f"<p>{_e(arm.get('description'))}</p>{parameters_html}"
            f"{prompt_content}</article>"
        )
    return f"<div class=\"arm-list\">{''.join(cards)}</div>" if cards else "<p>No arms declared.</p>"


def _run_evidence_html(runs: Any) -> str:
    if not isinstance(runs, list) or not runs:
        return ""
    rows: list[str] = []
    column_label = "Exact match"
    for run in runs:
        if not isinstance(run, dict):
            continue
        items = run.get("items") if isinstance(run.get("items"), list) else []
        views = [item.get("view") or {} for item in items if isinstance(item, dict)]
        if views:
            column_label = views[0].get("column_label") or column_label
        successes = sum(1 for view in views if view.get("success") is True)
        completed = int(run.get("completed_items") or 0)
        selected = int(run.get("selected_items") or 0)
        repetition = run.get("repetition")
        state = str(run.get("state") or "unknown").replace("-", " ").title()
        input_tokens = run.get("input_tokens")
        output_tokens = run.get("output_tokens")
        duration_ms = run.get("duration_ms")
        meta = (
            '<p class="run-meta">'
            f"<span><strong>Status:</strong> {_e(state)}</span>"
            f"<span><strong>Run ID:</strong> <code>{_e(run.get('run_id'))}</code></span>"
            f"<span><strong>Input tokens:</strong> {_e(f'{input_tokens:,}' if isinstance(input_tokens, int) else 'unknown')}</span>"
            f"<span><strong>Output tokens:</strong> {_e(f'{output_tokens:,}' if isinstance(output_tokens, int) else 'unknown')}</span>"
            f"<span><strong>Duration:</strong> {_e(f'{duration_ms / 1000:.2f} seconds' if isinstance(duration_ms, (int, float)) else 'unknown')}</span>"
            f"<span><strong>Failed items:</strong> {_e(run.get('failed_items'))}</span>"
            "</p>"
        )
        execution = run.get("execution")
        if isinstance(execution, dict):
            logs = "; ".join(
                f"attempt {entry.get('attempt')}: {entry.get('status')}, {entry.get('samples')} sample(s), {str(entry.get('sha256'))[:19]}…"
                for entry in execution.get("logs", [])
            )
            meta += (
                '<p class="run-meta">'
                f"<span><strong>Backend:</strong> {_e(execution.get('backend'))}</span>"
                f"<span><strong>Model route:</strong> <code>{_e(execution.get('model_uri'))}</code> at {_e(execution.get('base_url_origin'))}</span>"
                f"<span><strong>Inspect:</strong> {_e(execution.get('inspect_version'))} (adapter {_e(execution.get('adapter_version'))})</span>"
                f"<span><strong>Logs (digest-pinned, not published):</strong> {_e(logs or 'none')}</span>"
                "</p>"
            )
        item_table = _item_evidence_html(items)
        rows.append(
            '<details class="run-evidence"><summary>'
            f"<span>{_e(run.get('model'))}</span>"
            f"<span><code>{_e(run.get('arm'))}</code></span>"
            f"<span>{_e(repetition)}</span>"
            f"<span>{_e(completed)}/{_e(selected)}</span>"
            f"<span>{_e(successes)}/{_e(selected)}</span>"
            f"<span>{_money(run.get('cost_usd'))}</span>"
            f"</summary><div class=\"run-details\">{meta}{item_table}</div></details>"
        )
    if not rows:
        return ""
    success_label = {"Acceptance": "Accepted", "Score": "Full score"}.get(column_label, column_label.title() + "es" if column_label == "Exact match" else column_label)
    return (
        '<div class="run-table">'
        '<div class="run-header"><span>Model</span><span>Arm</span><span>Repetition</span>'
        f'<span>Coverage</span><span>{_e(success_label)}</span><span>Cost</span></div>'
        + "".join(rows)
        + "</div>"
    )


def _item_evidence_html(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    rows: list[str] = []
    label = "Exact match"
    for item in items:
        if not isinstance(item, dict):
            continue
        view = item.get("view") or {}
        label = view.get("column_label") or label
        state = str(item.get("state") or "unknown").replace("-", " ").title()
        headline = view.get("headline")
        headline_html = f'<br><span class="headline">{_e(headline)}</span>' if headline else ""
        rows.append(
            '<details class="evidence-row"><summary>'
            f"<span><code>{_e(item.get('item_id'))}</code>{headline_html}</span>"
            f"<span>{_e(state)}</span>"
            f"<span>{_e(view.get('column_value', '—'))}</span>"
            f"</summary>{blocks.render_item_view(view)}</details>"
        )
    if not rows:
        return ""
    return (
        '<div class="evidence-table">'
        '<div class="evidence-header"><span>Item</span><span>Status</span>'
        f'<span>{_e(label)}</span></div>'
        + "".join(rows)
        + "</div>"
    )


def _clean_untrusted(value: str) -> str:
    cleaned = re.sub(r"(?i)\b(?:javascript|data|vbscript)\s*:", "[unsafe-link-removed]", value)
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[secret-redacted]", cleaned)
    return cleaned


def _clean_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_tree(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_clean_tree(child) for child in value]
    if isinstance(value, tuple):
        return [_clean_tree(child) for child in value]
    if isinstance(value, str):
        return _clean_untrusted(value)
    return value


def _read_optional(path: Path) -> str | None:
    return _clean_untrusted(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else None


def _case_datasets(experiments: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for experiment in experiments.values():
        for evaluation in experiment["evals"]:
            dataset = evaluation.get("dataset")
            if not isinstance(dataset, dict):
                continue
            identity = (dataset.get("name"), dataset.get("path"))
            if identity in seen:
                continue
            seen.add(identity)
            datasets.append(dataset)
    return datasets


def _copy_dataset_assets(case_dir: Path, destination: Path) -> None:
    copied: set[Path] = set()
    for eval_path in sorted(case_dir.glob("experiments/*/evals/*/eval.yml")):
        evaluation = load_yaml(eval_path)
        raw_dataset = evaluation.get("dataset")
        if not isinstance(raw_dataset, str):
            continue
        dataset_path = (eval_path.parent / raw_dataset).resolve()
        if not dataset_path.is_file():
            continue
        dataset = load_yaml(dataset_path)
        values: list[str] = []
        _asset_strings(dataset.get("shared_assets"), values)
        for item in dataset.get("items", []):
            if isinstance(item, dict):
                _asset_strings(item.get("assets"), values)
        for raw in values:
            asset = (dataset_path.parent / raw).resolve()
            try:
                relative = asset.relative_to(case_dir)
            except ValueError:
                continue
            if asset.is_file() and asset not in copied:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(asset, target)
                copied.add(asset)


def _asset_strings(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, list):
        for child in value:
            _asset_strings(child, output)
    elif isinstance(value, dict):
        for child in value.values():
            _asset_strings(child, output)


def _token_totals(eval_dir: Path, run_ids: Any) -> dict[str, int | None]:
    totals = {"input_tokens": 0, "output_tokens": 0}
    found = False
    complete = True
    for run_id in run_ids if isinstance(run_ids, (list, tuple)) else []:
        for result_path in sorted((eval_dir / "runs" / str(run_id)).glob("items/*/result.yml")):
            result = load_yaml(result_path)
            usage = result.get("generation", {}).get("usage")
            if not isinstance(usage, dict):
                complete = False
                continue
            found = True
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    totals[key] += value
                else:
                    complete = False
    if not found or not complete:
        return {"input_tokens": None, "output_tokens": None}
    return totals


def _project_root_for_case(case_dir: Path) -> Path:
    if case_dir.parent.name == "cases":
        return case_dir.parent.parent
    for parent in case_dir.parents:
        if (parent / "cases" / case_dir.name).resolve() == case_dir:
            return parent
    raise TamesuError(f"Case is not inside a cases/ directory: {case_dir}")


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _attr(value: Any) -> str:
    return _e(value)


def _fragment(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "item").lower()).strip("-")
    return normalized or "item"


def _key_values(rows: list[tuple[str, Any]]) -> str:
    values = "".join(
        f"<div><dt>{_e(label)}</dt><dd>{_e(value)}</dd></div>"
        for label, value in rows
    )
    return f'<dl class="key-values">{values}</dl>'


def _number(value: Any) -> str:
    return "—" if isinstance(value, bool) or not isinstance(value, (int, float)) else f"{value:.4f}"


def _image_observed_outcome(plan: Any) -> dict[str, str]:
    summary, caveat = image_outcome(image_row_counts(plan))
    return {"summary": summary, "caveat": caveat}


def _observed_outcome(rows: list[dict[str, Any]], primary: Any) -> dict[str, str]:
    metric = str(primary or "primary metric")
    measured = [
        row for row in rows if isinstance(row.get("primary_mean"), (int, float))
    ]
    caveat = (
        "This is an observed comparison. It does not establish why the values differ or "
        "whether the result will generalize beyond this evaluation."
    )
    if not measured:
        return {
            "summary": "No compatible completed evidence is available to answer the hypothesis.",
            "caveat": caveat,
        }
    if single_sample(measured):
        caveat = SINGLE_SAMPLE_CAVEAT + " " + caveat
    reverse = metric not in LOWER_IS_BETTER_METRICS
    ordered = sorted(
        measured, key=lambda row: float(row["primary_mean"]), reverse=reverse
    )
    first = ordered[0]
    if len(ordered) >= 3 and float(first["primary_mean"]) != float(ordered[-1]["primary_mean"]):
        return {"summary": metric_range_sentence(measured, metric).replace("`", ""), "caveat": caveat}
    if len(ordered) == 1:
        return {
            "summary": (
                f"{_outcome_row_label(first)} recorded {_metric_value(metric, first['primary_mean'])} "
                f"for {_metric_name(metric)}, but there is no second result to compare."
            ),
            "caveat": caveat,
        }
    last = ordered[-1]
    difference = abs(float(first["primary_mean"]) - float(last["primary_mean"]))
    if difference == 0:
        summary = (
            f"{_outcome_row_label(first)} and {_outcome_row_label(last)} both recorded "
            f"{_metric_value(metric, first['primary_mean'])} for {_metric_name(metric)}."
        )
    else:
        preference = "higher" if reverse else "lower"
        summary = (
            f"{_outcome_row_label(first)} recorded {preference} {_metric_name(metric)}: "
            f"{_metric_value(metric, first['primary_mean'])} compared with "
            f"{_metric_value(metric, last['primary_mean'])} for {_outcome_row_label(last)}. "
            f"The observed difference was {_metric_difference(metric, difference)}."
        )
    return {"summary": summary, "caveat": caveat}


def _outcome_row_label(row: dict[str, Any]) -> str:
    return f"{row.get('model')} / {row.get('arm')}"


def _metric_name(metric: str) -> str:
    return metric.replace("_", " ")


def _metric_value(metric: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    if ("rate" in metric or "accuracy" in metric) and 0 <= float(value) <= 1:
        return f"{float(value) * 100:.2f}%"
    return f"{float(value):.4f}"


def _metric_difference(metric: str, value: float) -> str:
    if "rate" in metric or "accuracy" in metric:
        return f"{value * 100:.2f} percentage points"
    return f"{value:.4f}"


def _money(value: Any) -> str:
    return "unknown" if isinstance(value, bool) or not isinstance(value, (int, float)) else f"${value:.6f}"
