"""Inspect tasks for existing text/image manifests.

Inspect schedules samples, retries model calls, enforces limits, and writes transcripts.
Tamesu only converts their final outputs to the existing portable evidence shape.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .errors import ProviderError
from .identity import digest_value
from .models import GeneratedImage, ImageGenerationResponse, ProviderResponse
from .tasks import task_for
from .providers import get_provider


def validate_builtin_execution(evaluation: dict, errors: list[str]) -> None:
    block = evaluation.get("execution") or {}
    if set(block) - {"backend", "limits"}:
        errors.append("Built-in Inspect tasks accept only execution.backend and execution.limits; use artifact_bundle for a custom task file")
    limits = block.get("limits", {})
    if not isinstance(limits, dict):
        errors.append("execution.limits must be a mapping")
        return
    if evaluation.get("task") == "image_generation" and "cost_limit" in limits:
        errors.append("Built-in image transport records image API costs after generation; Inspect cost_limit requires a token-priced model. Use budget_usd for estimated image exposure, or a custom Inspect task for priced model limits.")
    from .backends.inspect_backend import LIMIT_KEYS
    for key, value in limits.items():
        if key not in LIMIT_KEYS or isinstance(value, bool) or not isinstance(value, (float, int)) or value <= 0:
            errors.append(f"execution.limits.{key} must be a supported positive limit")


def builtin_task(run: Any, input_path: Path):
    from inspect_ai import Task
    from inspect_ai.dataset import json_dataset
    from inspect_ai.scorer import Score, accuracy, scorer
    from inspect_ai.solver import generate

    semantic = task_for(run.context.evaluation, run.context.case)
    names = [next(iter(s)) if isinstance(s, dict) else s for s in run.context.evaluation['evaluation']['mechanical']]

    def make_scorer(name):
        @scorer(metrics=[accuracy()], name=name)
        def check():
            async def score(state, target):
                if semantic.name == 'structured_text':
                    from .scoring import score_output
                    _, scores = score_output(state.output.completion, expected=run.selected_item(str(state.sample_id)).get('expected'), output_schema=run.context.output_schema or {})
                else:
                    response = decode_image_response(state.output.metadata or {})
                    item_id = str(state.sample_id)
                    material = semantic.materialize(run.context, run.spec, run.selected_item(item_id), run.run_dir / 'items' / item_id, response)
                    scores = material.result_fields.get('mechanical_scores', {})
                value = scores.get(name, False)
                passed = value.get('pass', False) if isinstance(value, dict) else value is True
                return Score(value=1.0 if passed else 0.0)
            return score
        return check()

    return Task(name=semantic.name, dataset=json_dataset(str(input_path)), solver=generate(), scorer=[make_scorer(n) for n in names])


def image_model(run: Any):
    """Wrap image transport in Inspect's model API so Inspect controls execution.

    Image APIs do not expose a chat completion interface. Retain their request/response
    conversion here rather than retaining the former per-item runner.
    """
    from inspect_ai.model import ModelAPI, ModelOutput, ModelUsage, get_model, modelapi
    from .tasks.image_generation import build_image_request

    class ImageAPI(ModelAPI):
        async def generate(self, input, tools, tool_choice, config):
            provider = get_provider(run.spec.provider)
            request = build_image_request(input[-1].text, run.spec.parameters)
            response = await asyncio.to_thread(provider.generate_image, model=run.spec.model, request=request, timeout_seconds=run.spec.timeout_seconds)
            body = asdict(response)
            for image in body['images']:
                image['data'] = base64.b64encode(image['data']).decode('ascii')
            usage = response.usage
            output = ModelOutput.from_content(model=run.spec.model, content='Image generated' if response.images else 'No image')
            output.usage = ModelUsage(input_tokens=usage.get('input_tokens', 0), output_tokens=usage.get('output_tokens', 0), total_tokens=usage.get('total_tokens', 0), total_cost=response.cost_usd)
            output.metadata = {'image_response': body}
            return output

        def should_retry(self, ex):
            return isinstance(ex, ProviderError) and ex.retryable

    @modelapi(name="tamesu-image")
    def image_api():
        return ImageAPI

    return get_model(f"tamesu-image/{run.spec.model}", memoize=False)


def decode_image_response(metadata: dict) -> ImageGenerationResponse:
    body = dict(metadata['image_response'])
    body['images'] = tuple(GeneratedImage(**{**image, 'data':base64.b64decode(image['data'])}) for image in body['images'])
    return ImageGenerationResponse(**body)


def adapt_builtin_result(run: Any, item_id: str, item_dir: Path, sample: Any, result: dict) -> None:
    task = task_for(run.context.evaluation, run.context.case)
    item = run.selected_item(item_id)
    if task.name == 'image_generation':
        response = decode_image_response(sample.output.metadata or {})
        result['generation']['cost_usd'] = response.cost_usd
    else:
        response = ProviderResponse(text=sample.output.completion, request_id=getattr(sample.output, 'response_id', None), usage=result['generation']['usage'], cost_usd=result['generation']['cost_usd'])
    material = task.materialize(run.context, run.spec, item, item_dir, response)
    result["generation"]["provider_request_id"] = response.request_id
    result.update(material.result_fields)
    result['artifacts'] = material.artifacts
    result['prompt'] = task.prepare(run.context, run.spec, item).prompt_hashes
    result['prompt']['input_sha256'] = digest_value(item.get('input', {}))
    if task.name == 'structured_text':
        result['outcome'] = {'status':'generated', 'reason':'ok'}
