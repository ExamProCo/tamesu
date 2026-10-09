from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..artifacts import write_json, write_text
from ..identity import digest_bytes, digest_file, digest_value
from ..models import EvalContext, RunSpec
from ..providers.models import estimate_cost, max_tokens_for, registered
from .base import Materialized, PreparedItem, TaskValidation
from .structured_text import render_prompts


class StructuredTextTask:
    name = "structured_text"
    prompt_roles = ("system", "user")
    supported_backends = frozenset({"native"})
    supports_judges = False
    supports_review = False
    required_capabilities = frozenset({"text_output", "structured_output"})
    mechanical_scorers = frozenset({"valid_json", "schema_valid", "exact_match"})
    built_in_metrics = frozenset(
        {
            "exact_match_rate",
            "category_accuracy",
            "priority_accuracy",
            "requires_human_accuracy",
            "invalid_output_rate",
            "generation_failure_rate",
            "median_latency_ms",
            "mean_cost_usd",
        }
    )

    def code_files(self) -> tuple[Path, ...]:
        return (Path(__file__).with_name("structured_text.py"),)

    def validate_eval(
        self,
        evaluation: dict[str, Any],
        eval_dir: Path,
        project_root: Path,
        errors: list[str],
    ) -> TaskValidation:
        from ..config import _load_json, resolve_contained
        from ..errors import ConfigError

        raw_schema = evaluation.get("output_schema")
        if not isinstance(raw_schema, str) or not raw_schema:
            errors.append("eval.output_schema is required for structured_text")
            return TaskValidation()
        try:
            path = resolve_contained(eval_dir, raw_schema, project_root)
            return TaskValidation(path, _load_json(path))
        except ConfigError as exc:
            errors.append(str(exc))
            return TaskValidation()

    def validate_mechanical(
        self, name: str, params: dict[str, Any], location: str, errors: list[str]
    ) -> None:
        if params:
            errors.append(f"{location} takes no parameters for structured_text")

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

    def prepare(self, context: EvalContext, spec: RunSpec, item: dict[str, Any]) -> PreparedItem:
        rendered = self.render_prompts(spec.prompts, item)
        hashes = {
            "system_sha256": digest_bytes(rendered["system"].encode("utf-8")),
            "user_sha256": digest_bytes(rendered["user"].encode("utf-8")),
        }
        return PreparedItem(
            payload=rendered,
            request_metadata={
                **hashes,
                "output_schema_sha256": digest_value(context.output_schema or {}),
                "parameters_sha256": digest_value(spec.parameters),
            },
            prompt_hashes=hashes,
        )

    def call_provider(
        self, provider: Any, context: EvalContext, spec: RunSpec, prepared: PreparedItem
    ) -> Any:
        return provider.generate_text(
            model=spec.model,
            system_prompt=prepared.payload["system"],
            user_prompt=prepared.payload["user"],
            output_schema=context.output_schema or {},
            parameters=spec.parameters,
            timeout_seconds=spec.timeout_seconds,
        )

    def materialize(
        self,
        context: EvalContext,
        spec: RunSpec,
        item: dict[str, Any],
        item_dir: Path,
        response: Any,
    ) -> Materialized:
        from ..scoring import score_output

        raw_path = item_dir / "output.txt"
        write_text(raw_path, response.text)
        parsed, scores = score_output(
            response.text,
            expected=item.get("expected"),
            output_schema=context.output_schema or {},
        )
        output_path = raw_path
        media_type = "text/plain"
        if parsed is not None:
            output_path = item_dir / "output.json"
            write_json(output_path, parsed)
            media_type = "application/json"

        artifacts = [
            {
                "role": "raw_provider_output",
                "path": raw_path.relative_to(context.eval_dir).as_posix(),
                "sha256": digest_file(raw_path),
                "media_type": "text/plain",
            }
        ]
        if output_path != raw_path:
            artifacts.append(
                {
                    "role": "task_output",
                    "path": output_path.relative_to(context.eval_dir).as_posix(),
                    "sha256": digest_file(output_path),
                    "media_type": media_type,
                }
            )
        else:
            artifacts[0]["role"] = "task_output"
        return Materialized(
            result_fields={
                "output": {
                    "path": output_path.name,
                    "sha256": digest_file(output_path),
                    "media_type": media_type,
                },
                "mechanical_scores": scores,
            },
            artifacts=artifacts,
        )

    def estimate_item_cost(
        self, context: EvalContext, spec: RunSpec, item: dict[str, Any]
    ) -> tuple[float, float] | None:
        model_spec = registered(spec.model)
        if (
            model_spec is None
            or model_spec.input_usd_per_million is None
            or model_spec.output_usd_per_million is None
        ):
            return None
        schema_text = json.dumps(context.output_schema or {}, separators=(",", ":"))
        token_cap = int(
            spec.parameters.get(
                "max_tokens",
                spec.parameters.get("max_output_tokens", max_tokens_for(spec.model)),
            )
        )
        rendered = self.render_prompts(spec.prompts, item)
        input_tokens = _token_estimate(rendered["system"] + rendered["user"] + schema_text)
        expected_text = json.dumps(item.get("expected", {}), separators=(",", ":"))
        expected_output_tokens = max(32, _token_estimate(expected_text) * 2)
        estimated = estimate_cost(spec.model, input_tokens, min(token_cap, expected_output_tokens))
        maximum = estimate_cost(spec.model, input_tokens, token_cap)
        assert estimated is not None and maximum is not None
        return estimated, maximum

    def build_report(
        self, context: EvalContext, run_dir: Path, run_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        from ..scoring import build_report

        return build_report(context, run_dir, run_manifest)

    def rescore_run(self, context: EvalContext, run_dir: Path) -> dict[str, Any]:
        from ..scoring import rescore_run

        return rescore_run(context, run_dir)


def _token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))
