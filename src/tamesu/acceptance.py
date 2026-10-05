"""Acceptance and image-eval evidence, computed only from stored files (no provider calls).

Mechanical, model-judge, and human evidence stay in separate columns and are never blended
into one score. The eval's declared acceptance policy says which of them gate an image.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any

from .config import load_yaml
from .judging import load_judgments, load_judges, judgment_currency
from .models import EvalContext
from .policy import Acceptance, resolved_acceptance
from .review import human_state, load_review_rubric, load_reviews, split_reviews
from .rubric import Rubric
from .tasks.image_generation import OUTCOME_GENERATED, is_stale

MIN_AGREEMENT_SAMPLES = 10  # below this, kappa is withheld rather than reported misleadingly


def item_evidence(
    context: EvalContext,
    run_dir: Path,
    result: dict[str, Any],
    acceptance: Acceptance,
    *,
    judge_rubrics: dict[str, Rubric],
    human_rubric: Rubric | None,
) -> dict[str, Any]:
    item_id = result["item_id"]
    item_dir = run_dir / "items" / item_id
    outcome = (result.get("outcome") or {}).get("status")
    generated = outcome == OUTCOME_GENERATED
    stale_image = generated and is_stale(item_dir, result)
    image_sha = result.get("output", {}).get("sha256") if generated else None

    scores = result.get("mechanical_scores", {})
    mechanical_pass = (
        generated
        and not stale_image
        and bool(scores)
        and all(isinstance(s, dict) and s.get("pass") is True for s in scores.values())
    )
    evidence: dict[str, Any] = {
        "item_id": item_id,
        "outcome": result.get("outcome"),
        "image_sha256": image_sha,
        "stale_image": stale_image,
        "mechanical": {"verdict": "pass" if mechanical_pass else "fail", "scorers": scores},
    }

    # model judge column ----------------------------------------------------------------
    judgments = []
    stale_judgments = 0
    for record in load_judgments(item_dir):
        judge_id = record.get("judge", {}).get("id")
        rubric = judge_rubrics.get(judge_id)
        if rubric is None:
            stale_judgments += 1  # judge no longer declared
            continue
        if judgment_currency(record, rubric_sha256=rubric.sha256, image_sha256=image_sha) != "current":
            stale_judgments += 1
            continue
        judgments.append(record)
    ok = [record for record in judgments if record.get("status") == "ok"]
    judge_verdict: str | None = None
    if ok:
        judge_verdict = "pass" if all(r["verdict"] == "pass" for r in ok) else "fail"
    evidence["model_judge"] = {
        "verdict": judge_verdict,
        "judgments": [
            {"judgment_id": r["judgment_id"], "judge": r["judge"]["id"], "status": r["status"], "verdict": r.get("verdict")}
            for r in judgments
        ],
        "failed_calls": sum(1 for r in judgments if r.get("status") != "ok"),
        "stale_records": stale_judgments,
        "_dimensions": [r["dimensions"] for r in ok],
    }

    # human column ----------------------------------------------------------------------
    current: list[dict[str, Any]] = []
    stale_reviews: list[dict[str, Any]] = []
    if human_rubric is not None and generated and not stale_image:
        current, stale_reviews = split_reviews(
            load_reviews(item_dir), image_sha256=image_sha, rubric_sha256=human_rubric.sha256
        )
    state = human_state(current, acceptance) if human_rubric is not None else {"state": "n/a", "verdict": None, "owed": 0}
    evidence["human"] = {
        "verdict": state["verdict"],
        "state": state["state"],
        "owed": state["owed"] if generated and not stale_image else 0,
        "reviews": [
            {
                "review_id": r["review_id"],
                "reviewer": r["reviewer"],
                "verdict": r["verdict"],
                "submitted_at": r["submitted_at"],
            }
            for r in current
        ],
        "stale_records": len(stale_reviews),
        "_dimensions": [r["dimensions"] for r in current],
    }

    # acceptance ------------------------------------------------------------------------
    column = {"mechanical": "mechanical", "model_judge": "model_judge", "human": "human"}
    reasons: list[str] = []
    pending: list[str] = []
    if not generated:
        reasons.append(f"no_image:{outcome or 'missing'}")
    elif stale_image:
        reasons.append("stale_image")
    else:
        for source in acceptance.requires:
            verdict = evidence[column[source]]["verdict"]
            if verdict == "fail":
                reasons.append(f"{source}_failed")
            elif verdict is None:
                pending.append(source)
    if reasons:
        decision = "rejected"
    elif pending:
        decision = "pending"
    else:
        decision = "accepted"
    evidence["acceptance"] = {"state": decision, "reasons": reasons, "awaiting": pending}
    return evidence


def build_image_evidence(
    context: EvalContext, run_dir: Path, results: list[dict[str, Any]], totals_cost: float | None
) -> dict[str, Any]:
    block = context.evaluation.get("evaluation", {})
    acceptance = resolved_acceptance(block)
    judges = load_judges(context) if block.get("model_judges") else []
    judge_rubrics = {judge.id: judge.rubric for judge in judges}
    human_rubric = load_review_rubric(context) if block.get("human_review") else None

    planned = len(results)
    items = [
        item_evidence(context, run_dir, result, acceptance, judge_rubrics=judge_rubrics, human_rubric=human_rubric)
        for result in results
        if result.get("state") == "complete"
    ]
    generated = [e for e in items if (e["outcome"] or {}).get("status") == OUTCOME_GENERATED]
    accepted = [e for e in items if e["acceptance"]["state"] == "accepted"]
    pending = [e for e in items if e["acceptance"]["state"] == "pending"]
    rejected = [e for e in items if e["acceptance"]["state"] == "rejected"]

    def rate(count: int, denominator: int) -> float | None:
        return count / denominator if denominator else None

    judged = [e for e in generated if e["model_judge"]["verdict"] is not None]
    reviewed = [e for e in generated if e["human"]["verdict"] is not None]
    judge_pass = sum(1 for e in judged if e["model_judge"]["verdict"] == "pass")
    human_pass = sum(1 for e in reviewed if e["human"]["verdict"] == "pass")
    mechanical_pass = sum(1 for e in generated if e["mechanical"]["verdict"] == "pass")

    judge_cost = _run_judge_cost(run_dir)
    cost = {
        "generation_usd": totals_cost,
        "judge_usd": judge_cost,
        "total_usd": None if totals_cost is None or judge_cost is None else round(totals_cost + judge_cost, 6),
    }
    cost["per_accepted_usd"] = (
        round(cost["total_usd"] / len(accepted), 6) if accepted and cost["total_usd"] is not None else None
    )

    owed_reviews = sum(e["human"]["owed"] for e in generated)
    needs_reviews = human_rubric is not None
    evidence = {
        "policy": {
            "requires": list(acceptance.requires),
            "required_reviews_per_item": acceptance.required_reviews_per_item if needs_reviews else None,
            "on_disagreement": acceptance.on_disagreement if needs_reviews else None,
            "model_judge_role": acceptance.judge_role,
            "selection": acceptance.selection,
        },
        "counts": {
            "planned": planned,
            "generated": len(generated),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "pending": len(pending),
            "stale_images": sum(1 for e in items if e["stale_image"]),
            "stale_judgments": sum(e["model_judge"]["stale_records"] for e in items),
            "stale_reviews": sum(e["human"]["stale_records"] for e in items),
        },
        "columns": {
            "mechanical": {"pass": mechanical_pass, "of_generated": len(generated)},
            "model_judge": {"pass": judge_pass, "judged": len(judged), "of_generated": len(generated)},
            "human": {"pass": human_pass, "reviewed": len(reviewed), "of_generated": len(generated)},
        },
        "review": {
            "reviewed_items": len(reviewed),
            "generated_items": len(generated),
            "owed_reviews": owed_reviews,
            "completion": rate(len(reviewed), len(generated)) if needs_reviews else None,
        },
        "dimensions": {
            "human": _dimension_stats([d for e in generated for d in e["human"]["_dimensions"]]),
            "model_judge": _dimension_stats([d for e in generated for d in e["model_judge"]["_dimensions"]]),
        },
        "agreement": _agreement(generated),
        "cost": cost,
        "metrics": {
            "product_reference_pass_rate": rate(len(accepted), planned),
            "product_reference_pass_rate_given_image": rate(len(accepted), len(generated)),
            "model_judge_pass_rate": rate(judge_pass, planned) if judges else None,
            "model_judge_pass_rate_given_image": rate(judge_pass, len(judged)) if judges else None,
            "human_pass_rate": rate(human_pass, planned) if needs_reviews else None,
            "human_pass_rate_given_image": rate(human_pass, len(reviewed)) if needs_reviews else None,
            "review_completion_rate": rate(len(reviewed), len(generated)) if needs_reviews else None,
            "cost_per_accepted_usd": cost["per_accepted_usd"],
        },
    }
    for entry in items:
        entry["model_judge"].pop("_dimensions")
        entry["human"].pop("_dimensions")
    evidence["items"] = items
    return evidence


def _run_judge_cost(run_dir: Path) -> float | None:
    total, unknown = 0.0, False
    for path in run_dir.glob("items/*/judgments/*.yml"):
        cost = load_yaml(path).get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            total += float(cost)
        else:
            unknown = True
    return None if unknown else round(total, 6)


def _dimension_stats(dimension_sets: list[dict[str, Any]]) -> dict[str, Any]:
    """Failure rate and top reason codes per dimension, over every current verdict."""
    evaluated: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    reasons: dict[str, Counter[str]] = {}
    for dimensions in dimension_sets:
        for dimension_id, outcome in dimensions.items():
            evaluated[dimension_id] += 1
            if not outcome["pass"]:
                failed[dimension_id] += 1
                reasons.setdefault(dimension_id, Counter())[outcome["reason_code"]] += 1
    return {
        dimension_id: {
            "evaluated": evaluated[dimension_id],
            "failed": failed[dimension_id],
            "failure_rate": failed[dimension_id] / evaluated[dimension_id],
            "top_reasons": dict(reasons.get(dimension_id, Counter()).most_common(5)),
        }
        for dimension_id in sorted(evaluated)
    }


def _majority(dimension_sets: list[dict[str, Any]], dimension_id: str) -> bool | None:
    votes = [d[dimension_id]["pass"] for d in dimension_sets if dimension_id in d]
    passes = sum(1 for vote in votes if vote)
    if not votes or passes * 2 == len(votes):
        return None  # no evidence, or a tie: not counted as agreement or disagreement
    return passes * 2 > len(votes)


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    a_pass = sum(1 for a, _ in pairs if a) / n
    b_pass = sum(1 for _, b in pairs if b) / n
    expected = a_pass * b_pass + (1 - a_pass) * (1 - b_pass)
    if math.isclose(expected, 1.0):
        return None  # both raters constant: kappa is undefined
    return (observed - expected) / (1 - expected)


def _agreement(generated: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge-vs-human agreement per dimension; withheld when the sample is too small."""
    dimension_ids = sorted(
        {d for e in generated for dims in e["human"]["_dimensions"] + e["model_judge"]["_dimensions"] for d in dims}
    )
    report: dict[str, Any] = {}
    for dimension_id in dimension_ids:
        pairs: list[tuple[bool, bool]] = []
        for entry in generated:
            human = _majority(entry["human"]["_dimensions"], dimension_id)
            judge = _majority(entry["model_judge"]["_dimensions"], dimension_id)
            if human is not None and judge is not None:
                pairs.append((human, judge))
        n = len(pairs)
        if not n:
            continue
        sufficient = n >= MIN_AGREEMENT_SAMPLES
        kappa = cohens_kappa(pairs) if sufficient else None
        report[dimension_id] = {
            "n": n,
            "raw_agreement": round(sum(1 for a, b in pairs if a == b) / n, 4),
            "kappa": None if kappa is None else round(kappa, 4),
            "insufficient_data": not sufficient,
        }
    return report


def run_evidence(context: EvalContext, run_dir: Path) -> dict[str, Any]:
    """Evidence for one run, recomputed from stored files."""
    manifest = load_yaml(run_dir / "run.yml")
    results = []
    for item_id in manifest.get("selection", {}).get("item_ids", []):
        path = run_dir / "items" / item_id / "result.yml"
        results.append(load_yaml(path) if path.is_file() else {"item_id": item_id, "state": "missing"})
    costs = [r.get("generation", {}).get("cost_usd") for r in results if r.get("state") == "complete"]
    known = bool(costs) and all(isinstance(c, (int, float)) for c in costs)
    return build_image_evidence(context, run_dir, results, sum(costs) if known else None)
