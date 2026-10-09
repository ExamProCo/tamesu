"""artifact_bundle: an item produces a bundle of declared artifacts plus scorer results.

The task owns what the output means: which artifact roles exist, how scorer results become
metrics, and how the report reads. It does not execute anything; an execution backend
(currently only `inspect`) produces the evidence and the task builds the report from it.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..artifacts import write_yaml
from ..models import EvalContext, RunSpec
from .base import Materialized, PreparedItem, TaskValidation
from .structured_text import render_prompts


class _Names(frozenset):
    """A name set that also accepts parameterized members such as `mean_score:<scorer>`."""

    def __init__(self, *args: Any) -> None:
        super().__init__()

    def __contains__(self, item: object) -> bool:  # type: ignore[override]
        return True


STATIC_METRICS = frozenset(
    {
        "artifact_complete_rate",
        "generation_failure_rate",
        "limit_hit_rate",
        "completion_rate",
        "median_latency_ms",
        "mean_cost_usd",
        "mean_total_tokens",
        # present when a review artifact is declared (execution.artifacts.review)
        "render_rate",
        "mechanical_pass_rate",
        "mechanical_pass_rate_given_artifact",
        "accepted_rate",
        "accepted_rate_given_artifact",
        "human_pass_rate",
        "human_pass_rate_given_artifact",
        "review_completion_rate",
        "cost_per_accepted_usd",
    }
)
PARAMETERIZED_METRICS = ("mean_score", "full_credit_rate")


class _Metrics(frozenset):
    def __contains__(self, item: object) -> bool:  # type: ignore[override]
        if super().__contains__(item):
            return True
        return isinstance(item, str) and item.split(":", 1)[0] in PARAMETERIZED_METRICS


class ArtifactBundleTask:
    name = "artifact_bundle"
    prompt_roles = ("system", "user")
    supported_backends = frozenset({"inspect"})
    supports_judges = False
    supports_promotion = False
    # Review works on one declared image artifact per item (execution.artifacts.review); the
    # ingested result is shaped like an image-generation result so the shared review, acceptance
    # and reporting code applies unchanged.
    supports_review = True
    required_capabilities = frozenset({"text_output"})
    mechanical_scorers = _Names()  # names of Inspect scorers; checked at ingestion
    built_in_metrics = _Metrics(STATIC_METRICS)

    def code_files(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate_eval(
        self, evaluation: dict[str, Any], eval_dir: Path, project_root: Path, errors: list[str]
    ) -> TaskValidation:
        if evaluation.get("output_schema"):
            errors.append("eval.output_schema is not used by artifact_bundle")
        scorers = _scorer_names(evaluation)
        if not scorers:
            errors.append(
                "eval.evaluation.mechanical must name at least one Inspect scorer for artifact_bundle"
            )
        block = evaluation.get("evaluation", {}) or {}
        review_role = (evaluation.get("execution") or {}).get("artifacts", {}).get("review")
        if (block.get("human_review") or block.get("acceptance")) and not review_role:
            errors.append(
                "human_review and acceptance need eval.execution.artifacts.review naming the image "
                "artifact role reviewers should see"
            )
        if review_role and review_role not in (
            set((evaluation.get("execution") or {}).get("artifacts", {}).get("required", []))
            | set((evaluation.get("execution") or {}).get("artifacts", {}).get("allowed", []))
        ):
            errors.append("eval.execution.artifacts.review must be one of the required or allowed roles")
        metrics = evaluation.get("metrics", {})
        for metric in [metrics.get("primary"), *(metrics.get("secondary") or [])]:
            if isinstance(metric, str) and ":" in metric:
                kind, scorer = metric.split(":", 1)
                if kind in PARAMETERIZED_METRICS and scorer not in scorers:
                    errors.append(f"metric {metric!r} names a scorer not listed in evaluation.mechanical")
        return TaskValidation()

    def validate_mechanical(
        self, name: str, params: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        for key in sorted(set(params) - {"pass_at"}):
            errors.append(f"{location}.{key} is not a recognized parameter; configure the scorer in the Inspect task")
        threshold = params.get("pass_at")
        if "pass_at" in params and (
            isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or threshold <= 0
        ):
            errors.append(f"{location}.pass_at must be a positive number")

    def validate_run_parameters(
        self, provider: str, model: str, parameters: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        from ..config import _validate_model_parameters

        _validate_model_parameters(model, parameters, location, errors)

    def identity_evaluation(self, evaluation_block: dict[str, Any]) -> dict[str, Any]:
        return evaluation_block

    def render_prompts(self, prompts: dict[str, Path], item: dict[str, Any]) -> dict[str, str]:
        system_prompt, user_prompt = render_prompts(prompts, item)
        return {"system": system_prompt, "user": user_prompt}

    def _native_only(self) -> None:
        raise NotImplementedError("artifact_bundle runs only on an execution backend that supplies results")

    def prepare(self, context: EvalContext, spec: RunSpec, item: dict[str, Any]) -> PreparedItem:
        self._native_only()
        raise AssertionError

    def call_provider(self, provider: Any, context: EvalContext, spec: RunSpec, prepared: PreparedItem) -> Any:
        self._native_only()

    def materialize(
        self, context: EvalContext, spec: RunSpec, item: dict[str, Any], item_dir: Path, response: Any
    ) -> Materialized:
        self._native_only()
        raise AssertionError

    def adapt_ingested_result(
        self, context: EvalContext, item: dict[str, Any], item_dir: Path, result: dict[str, Any]
    ) -> None:
        """Give an ingested result the fields shared review and acceptance code reads.

        `prompt.input_sha256` lets review notice a dataset item that changed after the run.
        When a review artifact is declared, `outcome` and `output` describe that image; an
        absent or undecodable image is an `invalid_artifact` outcome, never a pass.
        """
        from ..identity import digest_value
        from PIL import Image

        result["prompt"] = {"input_sha256": digest_value(item.get("input", {}))}
        role = ((context.evaluation.get("execution") or {}).get("artifacts") or {}).get("review")
        if not role:
            return
        match = next((a for a in result.get("artifacts", []) if a["role"] == role), None)
        if match is None:
            result["outcome"] = {"status": "invalid_artifact", "reason": "missing_review_artifact"}
            return
        path = context.eval_dir / match["path"]
        if not str(match["media_type"]).startswith("image/"):
            result["outcome"] = {"status": "invalid_artifact", "reason": "review_artifact_not_an_image"}
            return
        try:
            with Image.open(path) as image:
                image.load()
                width, height = image.size
        except Exception:  # noqa: BLE001 - any decode failure is the same outcome
            result["outcome"] = {"status": "invalid_artifact", "reason": "undecodable_image"}
            return
        result["outcome"] = {"status": "generated", "reason": "ok"}
        result["output"] = {
            "path": path.relative_to(item_dir).as_posix(),
            "sha256": match["sha256"],
            "media_type": match["media_type"],
            "width": width,
            "height": height,
        }

    def estimate_item_cost(
        self, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        return None  # the execution backend sizes exposure

    def build_report(
        self, context: EvalContext, run_dir: Path, run_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        from ..config import load_yaml

        selected_ids = list(run_manifest.get("selection", {}).get("item_ids", []))
        results: list[dict[str, Any]] = []
        for item_id in selected_ids:
            path = run_dir / "items" / item_id / "result.yml"
            results.append(load_yaml(path) if path.is_file() else {"item_id": item_id, "state": "missing"})
        selected = len(selected_ids)
        complete = [r for r in results if r.get("state") == "complete"]
        scorers = _scorer_names(context.evaluation)

        def rate(numerator: int) -> float:
            return numerator / selected if selected else 0.0

        metrics: dict[str, Any] = {
            "completion_rate": rate(len(complete)),
            "generation_failure_rate": rate(selected - len(complete)),
            "artifact_complete_rate": rate(sum(1 for r in complete if not r.get("missing_artifacts"))),
            "limit_hit_rate": rate(sum(1 for r in complete if (r.get("inspect") or {}).get("limit"))),
        }
        for scorer in scorers:
            values = [_numeric(r.get("mechanical_scores", {}).get(scorer, {}).get("value")) for r in complete]
            numbers = [v for v in values if v is not None]
            # Denominator for mean_score is completed items with a numeric score; failed items
            # are reported through completion_rate and generation_failure_rate instead.
            metrics[f"mean_score:{scorer}"] = statistics.fmean(numbers) if numbers else None
            metrics[f"full_credit_rate:{scorer}"] = rate(sum(1 for v in numbers if v >= 1.0))
        latencies = [
            r.get("generation", {}).get("latency_ms")
            for r in complete
            if isinstance(r.get("generation", {}).get("latency_ms"), int)
        ]
        metrics["median_latency_ms"] = statistics.median(latencies) if latencies else None
        tokens = [r.get("generation", {}).get("usage", {}).get("total_tokens") for r in complete]
        tokens = [t for t in tokens if isinstance(t, int)]
        metrics["mean_total_tokens"] = statistics.fmean(tokens) if tokens else None
        costs = [r.get("generation", {}).get("cost_usd") for r in complete]
        if complete and all(isinstance(c, (int, float)) for c in costs):
            metrics["mean_cost_usd"] = sum(costs) / len(costs)
            total_cost: float | None = sum(costs)
        else:
            metrics["mean_cost_usd"] = None
            total_cost = None

        evidence: dict[str, Any] | None = None
        evidence_items: dict[str, Any] = {}
        review_role = ((context.evaluation.get("execution") or {}).get("artifacts") or {}).get("review")
        if review_role:
            from ..acceptance import build_image_evidence

            evidence = build_image_evidence(context, run_dir, results, total_cost)
            evidence_items = {entry["item_id"]: entry for entry in evidence.pop("items")}
            shared = evidence["metrics"]
            generated = [r for r in results if (r.get("outcome") or {}).get("status") == "generated"]
            metrics.update(
                {
                    "render_rate": rate(len(generated)),
                    "mechanical_pass_rate": rate(
                        sum(1 for r in generated if _mechanical_ok(r))
                    ),
                    "mechanical_pass_rate_given_artifact": (
                        sum(1 for r in generated if _mechanical_ok(r)) / len(generated) if generated else None
                    ),
                    "accepted_rate": shared["product_reference_pass_rate"],
                    "accepted_rate_given_artifact": shared["product_reference_pass_rate_given_image"],
                    "human_pass_rate": shared["human_pass_rate"],
                    "human_pass_rate_given_artifact": shared["human_pass_rate_given_image"],
                    "review_completion_rate": shared["review_completion_rate"],
                    "cost_per_accepted_usd": shared["cost_per_accepted_usd"],
                }
            )
        requested = [context.evaluation["metrics"]["primary"], *context.evaluation["metrics"].get("secondary", [])]
        report = {
            "schema_version": 1,
            "run_id": run_manifest["run_id"],
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "scorer_version": __version__,
            "identity": dict(run_manifest.get("identity", {})),
            "completion": {
                "selected_items": selected,
                "completed_items": len(complete),
                "failed_items": selected - len(complete),
            },
            "metrics": {name: metrics.get(name) for name in requested},
            "totals": {"cost_usd": total_cost},
            "items": [
                {
                    "item_id": r.get("item_id"),
                    "state": r.get("state"),
                    "mechanical_scores": r.get("mechanical_scores", {}),
                    "missing_artifacts": r.get("missing_artifacts", []),
                    "limit": (r.get("inspect") or {}).get("limit"),
                    **(
                        {
                            "outcome": r.get("outcome"),
                            "output": {
                                key: (r.get("output") or {}).get(key)
                                for key in ("path", "sha256", "media_type", "width", "height")
                            },
                            "evidence": evidence_items.get(r.get("item_id")),
                        }
                        if review_role
                        else {}
                    ),
                }
                for r in results
            ],
        }
        if evidence is not None:
            report["evidence"] = evidence
        write_yaml(run_dir / "report.yml", report)
        return report

    def rescore_run(self, context: EvalContext, run_dir: Path) -> dict[str, Any]:
        """Rebuild the report from ingested results. Inspect scores are never recomputed here."""
        from ..backends.inspect_backend import verify_logs
        from ..config import load_yaml
        from ..errors import ExecutionError

        problems = verify_logs(run_dir, context.eval_dir)
        if problems:
            raise ExecutionError(
                "Inspect log evidence is stale or corrupt: " + "; ".join(problems)
            )
        return self.build_report(context, run_dir, load_yaml(run_dir / "run.yml"))


def _scorer_names(evaluation: dict[str, Any]) -> list[str]:
    from .base import parse_mechanical_entry

    names = []
    for entry in evaluation.get("evaluation", {}).get("mechanical", []) or []:
        parsed = parse_mechanical_entry(entry)
        if parsed:
            names.append(parsed[0])
    return names


def _mechanical_ok(result: dict[str, Any]) -> bool:
    scores = result.get("mechanical_scores", {})
    return bool(scores) and all(isinstance(s, dict) and s.get("pass") is True for s in scores.values())


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return {"C": 1.0, "I": 0.0, "P": 0.5}.get(value)
    return None
