from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..artifacts import write_bytes, write_yaml
from ..identity import digest_bytes, digest_file, digest_value
from ..logging import redact_with_environment
from ..models import EvalContext, ImageGenerationRequest, RunSpec
from ..providers.models import registered
from .base import Materialized, PreparedItem, TaskValidation, parse_mechanical_entry
from .image_checks import (
    FORMAT_NAMES,
    SCORERS,
    IntegrityFailure,
    ScorerContext,
    inspect_image,
    run_scorers,
    validate_scorer_params,
)
from .structured_text import render_template

SIZE_PATTERN = re.compile(r"^[1-9][0-9]*x[1-9][0-9]*$")
ALLOWED_PARAMETER_KEYS = {"image", "provider_options"}
ALLOWED_IMAGE_KEYS = {"size", "output_format", "count"}

OUTCOME_GENERATED = "generated"
OUTCOME_SAFETY_FILTERED = "safety_filtered"
OUTCOME_INVALID = "invalid_artifact"


def declared_scorers(evaluation: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    entries = evaluation.get("evaluation", {}).get("mechanical", [])
    declared = [parse_mechanical_entry(entry) for entry in entries]
    return [entry for entry in declared if entry is not None]


def build_image_request(prompt: str, parameters: dict[str, Any]) -> ImageGenerationRequest:
    image = parameters.get("image") if isinstance(parameters.get("image"), dict) else {}
    options = (
        parameters.get("provider_options")
        if isinstance(parameters.get("provider_options"), dict)
        else {}
    )
    output_format = image.get("output_format")
    return ImageGenerationRequest(
        prompt=prompt,
        count=int(image.get("count", 1)),
        size=image.get("size"),
        output_format=str(output_format).lower() if output_format else None,
        options=dict(options),
    )


class ImageGenerationTask:
    name = "image_generation"
    prompt_roles = ("prompt",)
    supports_judges = True
    supports_review = True
    required_capabilities = frozenset({"image_output"})
    mechanical_scorers = frozenset(SCORERS)
    built_in_metrics = frozenset(
        {
            "generation_success_rate",
            "safety_filter_rate",
            "invalid_artifact_rate",
            "generation_failure_rate",
            "mechanical_pass_rate",
            "mechanical_pass_rate_given_image",
            "median_latency_ms",
            "mean_cost_usd",
            # derived from judge and review evidence by tamesu.acceptance
            "product_reference_pass_rate",
            "product_reference_pass_rate_given_image",
            "model_judge_pass_rate",
            "model_judge_pass_rate_given_image",
            "human_pass_rate",
            "human_pass_rate_given_image",
            "review_completion_rate",
            "cost_per_accepted_usd",
        }
    )

    def code_files(self) -> tuple[Path, ...]:
        here = Path(__file__).parent
        return (
            here / "image_generation.py",
            here / "image_checks.py",
            here / "structured_text.py",  # shared prompt renderer
        )

    # -- validation ---------------------------------------------------------------

    def validate_eval(
        self,
        evaluation: dict[str, Any],
        eval_dir: Path,
        project_root: Path,
        errors: list[str],
    ) -> TaskValidation:
        if "output_schema" in evaluation:
            errors.append("eval.output_schema is not used by image_generation")
        return TaskValidation()

    def validate_mechanical(
        self, name: str, params: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        validate_scorer_params(name, params, location, errors)

    def validate_run_parameters(
        self, provider: str, model: str, parameters: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        for key in sorted(set(parameters) - ALLOWED_PARAMETER_KEYS):
            errors.append(
                f"{location}.parameters.{key} is not valid for image_generation; "
                "use parameters.image and parameters.provider_options"
            )
        image = parameters.get("image", {})
        if not isinstance(image, dict):
            errors.append(f"{location}.parameters.image must be a mapping")
            return
        for key in sorted(set(image) - ALLOWED_IMAGE_KEYS):
            errors.append(f"{location}.parameters.image.{key} is not a recognized field")
        size = image.get("size")
        if size is not None and (not isinstance(size, str) or not SIZE_PATTERN.fullmatch(size)):
            errors.append(f"{location}.parameters.image.size must look like 1024x1024")
        output_format = image.get("output_format")
        if output_format is not None and (
            not isinstance(output_format, str) or output_format.lower() not in FORMAT_NAMES
        ):
            errors.append(f"{location}.parameters.image.output_format must be png, jpeg, or webp")
        count = image.get("count", 1)
        if isinstance(count, bool) or count != 1:
            errors.append(f"{location}.parameters.image.count must be 1 in this version")
        options = parameters.get("provider_options", {})
        if not isinstance(options, dict):
            errors.append(f"{location}.parameters.provider_options must be a mapping")
            return
        if errors:
            return
        # Adapter-owned validation runs at plan time, before credentials and any paid call.
        from ..providers import ImageGenerationProvider, get_provider

        try:
            adapter = get_provider(provider)
        except ValueError:
            return
        if not isinstance(adapter, ImageGenerationProvider):
            errors.append(f"{location}: provider {provider!r} has no image generation adapter")
            return
        for message in adapter.validate_image_parameters(model, build_image_request("", parameters)):
            errors.append(f"{location}: {message}")

    def identity_evaluation(self, evaluation_block: dict[str, Any]) -> dict[str, Any]:
        # Judges and reviewers act on stored images and may be added or repeated after a run
        # banks, so only what shapes generation and acceptance belongs in run identity.
        return {
            key: evaluation_block[key]
            for key in ("mechanical", "acceptance")
            if key in evaluation_block
        }

    # -- execution ----------------------------------------------------------------

    def render_prompts(self, prompts: dict[str, Path], item: dict[str, Any]) -> dict[str, str]:
        path = prompts["prompt"]
        return {"prompt": render_template(path.read_text(encoding="utf-8"), {"item": item}, path)}

    def prepare(self, context: EvalContext, spec: RunSpec, item: dict[str, Any]) -> PreparedItem:
        prompt = self.render_prompts(spec.prompts, item)["prompt"]
        request = build_image_request(prompt, spec.parameters)
        hashes = {
            "prompt_sha256": digest_bytes(prompt.encode("utf-8")),
            # Lets later stages (judging, review) detect a dataset item edited after generation.
            "input_sha256": digest_value(item.get("input", {})),
        }
        return PreparedItem(
            payload=request,
            request_metadata={
                **hashes,
                "parameters_sha256": digest_value(spec.parameters),
                "image": {
                    "count": request.count,
                    "size": request.size,
                    "output_format": request.output_format,
                },
            },
            prompt_hashes=hashes,
        )

    def call_provider(
        self, provider: Any, context: EvalContext, spec: RunSpec, prepared: PreparedItem
    ) -> Any:
        return provider.generate_image(
            model=spec.model, request=prepared.payload, timeout_seconds=spec.timeout_seconds
        )

    def materialize(
        self,
        context: EvalContext,
        spec: RunSpec,
        item: dict[str, Any],
        item_dir: Path,
        response: Any,
    ) -> Materialized:
        request = build_image_request("", spec.parameters)
        images = tuple(response.images)
        revised_prompt = images[0].revised_prompt if len(images) == 1 else None
        provider_claim = images[0].provider_media_type if len(images) == 1 else None

        artifacts: list[dict[str, Any]] = []
        output: dict[str, Any] | None = None
        mechanical: dict[str, Any] = {}
        detected_media_type: str | None = None

        if not images:
            if response.refusal_reason:
                status, reason = OUTCOME_SAFETY_FILTERED, response.refusal_reason
            else:
                status, reason = OUTCOME_INVALID, "no_image_returned"
        elif len(images) != 1:
            status, reason = OUTCOME_INVALID, "unexpected_image_count"
        else:
            data = images[0].data
            try:
                decoded = inspect_image(data)
            except IntegrityFailure as exc:
                status, reason = OUTCOME_INVALID, exc.reason
                mechanical = {
                    "decodable_image": {"pass": False, "detail": {"reason": exc.reason}}
                }
                if data:
                    raw_path = item_dir / "invalid-0001.bin"
                    write_bytes(raw_path, data)
                    artifacts.append(
                        {
                            "role": "raw_provider_output",
                            "path": raw_path.relative_to(context.eval_dir).as_posix(),
                            "sha256": digest_file(raw_path),
                            "media_type": "application/octet-stream",
                        }
                    )
            else:
                status, reason = OUTCOME_GENERATED, "ok"
                detected_media_type = decoded.media_type
                image_path = item_dir / f"output-0001.{decoded.extension}"
                write_bytes(image_path, data)
                output = {
                    "path": image_path.name,
                    "sha256": digest_file(image_path),
                    "media_type": decoded.media_type,
                    "width": decoded.width,
                    "height": decoded.height,
                }
                artifacts.append(
                    {
                        "role": "task_output",
                        "path": image_path.relative_to(context.eval_dir).as_posix(),
                        "sha256": output["sha256"],
                        "media_type": decoded.media_type,
                        "width": decoded.width,
                        "height": decoded.height,
                    }
                )
                mechanical = _score(data, decoded, context.evaluation, request.output_format)

        provider_document = {
            "schema_version": 1,
            "request_id": response.request_id,
            "outcome": {"status": status, "reason": reason},
            "revised_prompt": revised_prompt,
            "provider_media_type": provider_claim,
            "detected_media_type": detected_media_type,
            "media_type_mismatch": bool(
                provider_claim and detected_media_type and provider_claim != detected_media_type
            ),
            "usage": response.usage,
            "cost_usd": response.cost_usd,
            "response_metadata": response.response_metadata,
        }
        provider_path = item_dir / "provider_response.yml"
        write_yaml(provider_path, redact_with_environment(provider_document))

        fields: dict[str, Any] = {
            "outcome": {"status": status, "reason": reason},
            "mechanical_scores": mechanical,
            "provider_response": {
                "path": provider_path.name,
                "sha256": digest_file(provider_path),
                "revised_prompt": revised_prompt,
            },
            "artifacts": artifacts,
        }
        if output is not None:
            fields["output"] = output
        return Materialized(result_fields=fields, artifacts=artifacts)

    def estimate_item_cost(
        self, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        model_spec = registered(spec.model)
        if model_spec is None or model_spec.image_pricing is None:
            return None
        price = model_spec.image_pricing.price(spec.parameters)
        if price is None:
            return None
        count = int(spec.parameters.get("image", {}).get("count", 1))
        return price * count, price * count

    # -- reporting ----------------------------------------------------------------

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
        stale = {
            r["item_id"]
            for r in results
            if _outcome(r) == OUTCOME_GENERATED and is_stale(run_dir / "items" / r["item_id"], r)
        }

        def rate(count: int) -> float:
            return count / selected if selected else 0.0

        generated = [r for r in results if _outcome(r) == OUTCOME_GENERATED]
        mechanical_pass = [r for r in generated if r["item_id"] not in stale and _mechanical_ok(r)]
        latencies = [
            r.get("generation", {}).get("latency_ms")
            for r in results
            if isinstance(r.get("generation", {}).get("latency_ms"), int)
        ]
        costs = [r.get("generation", {}).get("cost_usd") for r in complete]
        all_costs_known = bool(complete) and all(isinstance(c, (int, float)) for c in costs)
        metrics: dict[str, Any] = {
            "generation_success_rate": rate(len(generated)),
            "safety_filter_rate": rate(sum(1 for r in results if _outcome(r) == OUTCOME_SAFETY_FILTERED)),
            "invalid_artifact_rate": rate(sum(1 for r in results if _outcome(r) == OUTCOME_INVALID)),
            "generation_failure_rate": rate(selected - len(complete)),
            "mechanical_pass_rate": rate(len(mechanical_pass)),
            "mechanical_pass_rate_given_image": (
                len(mechanical_pass) / len(generated) if generated else None
            ),
            "median_latency_ms": statistics.median(latencies) if latencies else None,
            "mean_cost_usd": sum(costs) / len(costs) if all_costs_known else None,
        }
        from ..acceptance import build_image_evidence

        evidence = build_image_evidence(
            context, run_dir, results, sum(costs) if all_costs_known else None
        )
        evidence_items = {entry["item_id"]: entry for entry in evidence.pop("items")}
        metrics.update(evidence["metrics"])
        requested = [
            context.evaluation["metrics"]["primary"],
            *context.evaluation["metrics"].get("secondary", []),
        ]
        reasons = Counter(
            f"{_outcome(r)}:{r.get('outcome', {}).get('reason')}"
            for r in complete
            if _outcome(r) != OUTCOME_GENERATED
        )
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
                "generated_items": len(generated),
                "safety_filtered_items": sum(
                    1 for r in results if _outcome(r) == OUTCOME_SAFETY_FILTERED
                ),
                "invalid_artifact_items": sum(
                    1 for r in results if _outcome(r) == OUTCOME_INVALID
                ),
                "stale_items": sorted(stale),
                "outcome_reasons": dict(sorted(reasons.items())),
            },
            "metrics": {name: metrics.get(name) for name in requested},
            "totals": {"cost_usd": sum(costs) if all_costs_known else None},
            "evidence": evidence,
            "items": [self._report_item(r, evidence_items.get(r.get("item_id"))) for r in results],
        }
        write_yaml(run_dir / "report.yml", report)
        return report

    def _report_item(self, result: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "item_id": result.get("item_id"),
            "state": result.get("state"),
            "outcome": result.get("outcome"),
            "mechanical_scores": result.get("mechanical_scores", {}),
        }
        output = result.get("output")
        if isinstance(output, dict):
            item["output"] = {
                key: output.get(key) for key in ("path", "sha256", "media_type", "width", "height")
            }
        revised = result.get("provider_response", {}).get("revised_prompt")
        if revised:
            item["revised_prompt"] = revised
        if evidence is not None:
            # separate columns; no blended score
            item["model_judge"] = evidence["model_judge"]
            item["human"] = evidence["human"]
            item["acceptance"] = evidence["acceptance"]
        return item

    def rescore_run(self, context: EvalContext, run_dir: Path) -> dict[str, Any]:
        from ..config import load_yaml

        manifest = load_yaml(run_dir / "run.yml")
        requested_format = (
            manifest.get("resolved", {}).get("parameters", {}).get("image", {}).get("output_format")
        )
        for item_id in manifest.get("selection", {}).get("item_ids", []):
            item_dir = run_dir / "items" / item_id
            result_path = item_dir / "result.yml"
            if not result_path.is_file():
                continue
            result = load_yaml(result_path)
            if _outcome(result) != OUTCOME_GENERATED or is_stale(item_dir, result):
                continue  # stale evidence is reported, never re-scored as if it were current
            data = (item_dir / result["output"]["path"]).read_bytes()
            result["mechanical_scores"] = _score(
                data, inspect_image(data), context.evaluation, requested_format
            )
            write_yaml(result_path, result)
        return self.build_report(context, run_dir, manifest)


def _score(
    data: bytes, decoded: Any, evaluation: dict[str, Any], requested_format: str | None
) -> dict[str, Any]:
    declared = declared_scorers(evaluation)
    scores = run_scorers(data, decoded, declared, ScorerContext(requested_format))
    for name, params in declared:
        if params.get("prerequisite"):
            scores[name]["prerequisite"] = True
    return scores


def _outcome(result: dict[str, Any]) -> str | None:
    outcome = result.get("outcome")
    return outcome.get("status") if isinstance(outcome, dict) else None


def prerequisites_met(result: dict[str, Any]) -> bool:
    """True unless a scorer declared `prerequisite: true` failed; such images are not judged."""
    scores = result.get("mechanical_scores", {})
    return all(
        score.get("pass") is True
        for score in scores.values()
        if isinstance(score, dict) and score.get("prerequisite")
    )


def _mechanical_ok(result: dict[str, Any]) -> bool:
    scores = result.get("mechanical_scores", {})
    return bool(scores) and all(
        isinstance(score, dict) and score.get("pass") is True for score in scores.values()
    )


def is_stale(item_dir: Path, result: dict[str, Any]) -> bool:
    """True when the stored image no longer matches the checksum recorded at generation."""
    output = result.get("output")
    if not isinstance(output, dict):
        return False
    path = item_dir / str(output.get("path", ""))
    return not path.is_file() or digest_file(path) != output.get("sha256")
