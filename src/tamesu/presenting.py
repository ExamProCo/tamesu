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
from .artifacts import write_json, write_text
from .config import load_eval_context_from_path, load_yaml
from .errors import TamesuError
from .packaging import SECRET_PATTERNS, build_inventory, collect_payload, inventory_digest, load_publication
from .planner import build_plan
from .pricing import estimate_plan_cost
from .providers.models import PROVIDER_KEYS
from .reporting import LOWER_IS_BETTER_METRICS, comparison_rows, status_summary


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


def build_case_data(case_dir: Path) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    case = load_yaml(case_dir / "case.yml")
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
            context = load_eval_context_from_path(_project_root_for_case(case_dir), eval_path)
            plan = build_plan(context)
            summary = status_summary(plan)
            rows = comparison_rows(plan)
            counts = summary["counts"]
            eval_data = _eval_data(context, plan, summary, rows)
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


def present_case(case_dir: Path, output_dir: Path | None = None) -> Path:
    case_dir = case_dir.resolve()
    data = build_case_data(case_dir)
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
    return destination


def _eval_data(context: Any, plan: Any, summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = context.evaluation
    counts = summary["counts"]
    state = "complete" if not counts["owed"] and counts["banked"] == counts["planned"] else "partial" if counts["banked"] or counts["partial"] or counts["failed"] else "not-run"
    primary = evaluation["metrics"]["primary"]
    measured = [row for row in rows if isinstance(row.get("primary_mean"), (int, float))]
    reverse = primary not in LOWER_IS_BETTER_METRICS
    leader = sorted(measured, key=lambda row: float(row["primary_mean"]), reverse=reverse)[0] if measured else None
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
        for result_path in sorted(run_dir.glob("items/*/result.yml")):
            result = load_yaml(result_path)
            output_path = result_path.parent / "output.txt"
            dataset_item = dataset_items.get(result_path.parent.name, {})
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
    return {
        "id": context.eval_dir.name,
        "eval_id": context.eval_id,
        "question": evaluation.get("question"),
        "description": evaluation.get("description"),
        "status": state,
        "primary_metric": primary,
        "observed_outcome": _observed_outcome(rows, primary),
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
        "reproduce": {
            "commands": [f"tamesu plan {context.eval_id}", f"tamesu run {context.eval_id}", f"tamesu rescore <run-id>"],
            "environment": required_environment,
            "estimated_cost_usd": cost.estimated_usd if cost.fully_priced else None,
            "maximum_cost_usd": cost.maximum_usd if cost.fully_priced else None,
        },
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
    leader = sorted(measured, key=lambda row: float(row["primary_mean"]), reverse=reverse)[0] if measured else None
    return {
        "id": eval_path.parent.name,
        "eval_id": "/".join((eval_path.parents[4].name, eval_path.parents[2].name, eval_path.parent.name)),
        "question": evaluation.get("question"), "description": evaluation.get("description"),
        "status": "complete" if rows else "not-run", "primary_metric": primary,
        "observed_outcome": _observed_outcome(rows, primary),
        "leader": {"model": leader["model"], "arm": leader["arm"], "value": leader["primary_mean"]} if leader else None,
        "coverage": {}, "rows": rows, "dataset": {"name": "withheld", "description": None, "items": None, "path": None},
        "method": {"task": evaluation.get("task"), "arms": evaluation.get("arms", []), "runs": evaluation.get("runs", []), "defaults": evaluation.get("defaults", {}), "metrics": evaluation.get("metrics", {}), "output_schema": evaluation.get("output_schema"), "prompts": []},
        "items": [], "evidence_runs": [], "analysis": _analysis_data(eval_path.parent), "reproduce": None,
    }


def _analysis_data(eval_dir: Path) -> dict[str, Any] | None:
    path = eval_dir / "analysis.md"
    if not path.is_file():
        return None
    source = path.read_text(encoding="utf-8", errors="replace")
    metadata: dict[str, Any] = {}
    body = source
    if source.startswith("---\n"):
        closing = source.find("\n---\n", 4)
        if closing >= 0:
            try:
                loaded = yaml.safe_load(source[4:closing])
                if isinstance(loaded, dict):
                    metadata = loaded
                    body = source[closing + 5 :]
            except yaml.YAMLError:
                pass
    digest = evidence_digest(eval_dir)
    matches = metadata.get("evidence_digest") == digest
    return {
        "label": "Analysis, matches the shown evidence" if matches else "Author commentary, not verified against these results",
        "matches_evidence": matches,
        "evidence_digest": digest,
        "declared_evidence_digest": metadata.get("evidence_digest"),
        "body": _clean_untrusted(body),
    }


def _case_html(data: dict[str, Any]) -> str:
    case = data["case"]
    publication = data["publication"]
    cards = []
    for experiment in data["experiments"]:
        evals = "".join(
            f"<li><span class=\"status {_e(e['status'])}\">{_e(str(e['status']).replace('-', ' ').title())}</span> — {_e(e['question'])}"
            + (f" — leader: {_e(e['leader']['model'])} / {_e(e['leader']['arm'])} ({_number(e['leader']['value'])})" if e.get("leader") else "") + "</li>"
            for e in experiment["evals"]
        )
        cards.append(f"<article class=\"card\"><h3><a href=\"experiments/{_attr(experiment['id'])}.html\">{_e(experiment['title'])}</a></h3><ul>{evals}</ul></article>")
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


def _experiment_html(data: dict[str, Any], experiment: dict[str, Any]) -> str:
    sections: list[str] = []
    for evaluation in experiment["evals"]:
        rows = evaluation["rows"]
        metrics = [evaluation.get("primary_metric"), *evaluation.get("method", {}).get("metrics", {}).get("secondary", [])]
        headings = "".join(f"<th>{_e(metric)}</th>" for metric in metrics if metric)
        table_rows = ""
        for row in rows:
            cells = "".join(f"<td>{_number(row.get('metrics', {}).get(metric, row.get('primary_mean') if metric == evaluation.get('primary_metric') else None))}</td>" for metric in metrics if metric)
            table_rows += f"<tr><td>{_e(row.get('model'))}</td><td>{_e(row.get('arm'))}</td><td>{_e(row.get('repetitions'))}</td><td>{_e(row.get('completed_items'))}/{_e(row.get('items'))}</td>{cells}<td>{_e(row.get('input_tokens'))}</td><td>{_e(row.get('output_tokens'))}</td><td>{_money(row.get('total_cost_usd'))}</td></tr>"
        if not table_rows:
            table_rows = f"<tr><td colspan=\"{7 + len([m for m in metrics if m])}\">No compatible completed evidence.</td></tr>"
        arms = _arms_html(evaluation)
        evidence_runs = _run_evidence_html(evaluation.get("evidence_runs", []))
        analysis = evaluation.get("analysis")
        analysis_html = (
            f"<h2 id=\"{_attr(evaluation['id'])}-conclusion\">Conclusion and next experiments</h2>"
            f"<p class=\"muted\">{_e(analysis['label'])}</p>{_markup(analysis['body'])}"
            if analysis
            else ""
        )
        outcome = evaluation.get("observed_outcome", {})
        reproduce = evaluation.get("reproduce")
        reproduce_html = ""
        if reproduce:
            commands = "\n".join(reproduce["commands"])
            env = ", ".join(reproduce["environment"]) or "none"
            reproduce_html = f"<h2>Reproduce</h2><pre>{_e(commands)}</pre><p>Required environment variable names: <code>{_e(env)}</code>. Estimated cost: {_money(reproduce['estimated_cost_usd'])}; maximum additional exposure: {_money(reproduce['maximum_cost_usd'])}.</p>"
        else:
            reproduce_html = "<h2>Reproduce</h2><p class=\"notice\">Unavailable for a report-only package.</p>"
        coverage = evaluation.get("coverage", {})
        limitations = [f"{coverage.get('owed', 0)} planned runs are still owed.", f"{coverage.get('failed', 0)} failed runs and {coverage.get('stale', 0)} stale runs are excluded."]
        dataset_description = evaluation["dataset"].get("description")
        dataset_description_html = (
            f"<p>{_e(dataset_description)}</p>" if dataset_description else ""
        )
        sections.append(f"""
<section id=\"{_attr(evaluation['id'])}\"><p><strong>Status:</strong> <span class=\"status {_attr(evaluation['status'])}\">{_e(str(evaluation['status']).replace('-', ' ').title())}</span></p><h2>Hypothesis</h2><p class=\"lead\">{_e(evaluation['question'])}</p><p>{_e(evaluation['description'])}</p>
<h2 id=\"{_attr(evaluation['id'])}-method\">Method</h2><p>Dataset: <code>{_e(evaluation['dataset'].get('name'))}</code> ({_e(evaluation['dataset'].get('items'))} items). Primary metric: <code>{_e(evaluation.get('primary_metric'))}</code>.</p>{dataset_description_html}<h2 id=\"{_attr(evaluation['id'])}-arms\">Arms</h2>{arms}
<h2 id=\"{_attr(evaluation['id'])}-outcome\">Observed outcome</h2><p class=\"lead\">{_e(outcome.get('summary'))}</p><p>{_e(outcome.get('caveat'))}</p>
<h2 id=\"{_attr(evaluation['id'])}-results\">Results</h2><div class=\"table-wrap\"><table><thead><tr><th>Model</th><th>Arm</th><th>Repetitions</th><th>Coverage</th>{headings}<th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead><tbody>{table_rows}</tbody></table></div>
{analysis_html}<h2 id=\"{_attr(evaluation['id'])}-evidence\">Run evidence</h2>{evidence_runs or '<p>No run outputs are included.</p>'}
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
            evaluation_items.append(
                f"<li><a href=\"{eval_base}\">{_e(eval_id.replace('-', ' ').title())}</a>"
                f"<ul><li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-method\">Method</a></li>"
                f"{arm_branch}"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-outcome\">Observed outcome</a></li>"
                f"<li><a href=\"{_attr(experiment_page)}#{_attr(eval_id)}-results\">Results</a></li>"
                f"{conclusion_link}"
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
    for run in runs:
        if not isinstance(run, dict):
            continue
        items = run.get("items") if isinstance(run.get("items"), list) else []
        exact_matches = sum(
            isinstance(item, dict)
            and isinstance(item.get("scores"), dict)
            and item["scores"].get("exact_match") is True
            for item in items
        )
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
        item_table = _item_evidence_html(items)
        rows.append(
            '<details class="run-evidence"><summary>'
            f"<span>{_e(run.get('model'))}</span>"
            f"<span><code>{_e(run.get('arm'))}</code></span>"
            f"<span>{_e(repetition)}</span>"
            f"<span>{_e(completed)}/{_e(selected)}</span>"
            f"<span>{_e(exact_matches)}/{_e(selected)}</span>"
            f"<span>{_money(run.get('cost_usd'))}</span>"
            f"</summary><div class=\"run-details\">{meta}{item_table}</div></details>"
        )
    if not rows:
        return ""
    return (
        '<div class="run-table">'
        '<div class="run-header"><span>Model</span><span>Arm</span><span>Repetition</span>'
        '<span>Coverage</span><span>Exact matches</span><span>Cost</span></div>'
        + "".join(rows)
        + "</div>"
    )


def _item_evidence_html(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        scores = item.get("scores")
        if not isinstance(scores, dict):
            scores = {}
        exact_match = scores.get("exact_match")
        exact_match_label = (
            "Yes" if exact_match is True else "No" if exact_match is False else "—"
        )
        state = str(item.get("state") or "unknown").replace("-", " ").title()
        details = (
            '<div class="evidence-details">'
            '<div class="evidence-details-grid">'
            f"<div><h3>Input</h3><pre>{_e(json.dumps(item.get('input'), sort_keys=True, ensure_ascii=False, indent=2))}</pre></div>"
            f"<div><h3>Expected</h3><pre>{_e(json.dumps(item.get('expected'), sort_keys=True, ensure_ascii=False, indent=2))}</pre></div>"
            f"<div><h3>Output</h3><pre>{_e(item.get('output') or 'No stored output')}</pre></div>"
            "</div>"
            f"<h3>Scores</h3><pre>{_e(json.dumps(scores, sort_keys=True, ensure_ascii=False, indent=2))}</pre>"
            "</div>"
        )
        rows.append(
            '<details class="evidence-row"><summary>'
            f"<span><code>{_e(item.get('item_id'))}</code></span>"
            f"<span>{_e(state)}</span>"
            f"<span>{_e(exact_match_label)}</span>"
            f"</summary>{details}</details>"
        )
    if not rows:
        return ""
    return (
        '<div class="evidence-table">'
        '<div class="evidence-header"><span>Item</span><span>Status</span>'
        '<span>Exact match</span></div>'
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
    reverse = metric not in LOWER_IS_BETTER_METRICS
    ordered = sorted(
        measured, key=lambda row: float(row["primary_mean"]), reverse=reverse
    )
    first = ordered[0]
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
