"""Shared fixtures for image-generation tests: a temp project and a scriptable fake provider."""

from __future__ import annotations

import hashlib
import io
import tempfile
import textwrap
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image

from tamesu.errors import ProviderError
import json

from tamesu.models import (
    GeneratedImage,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImagePart,
    ProviderResponse,
    TextPart,
)
from tamesu.providers.models import (
    IMAGE_OUTPUT_CAPABILITIES,
    MODELS,
    VISION_CAPABILITIES,
    FlatImagePricing,
    ModelSpec,
)

FAKE_MODEL = "fake-image-1"
FAKE_JUDGE = "fake-judge-1"
EVAL_ID = "product-reference/baseline/fake-baseline"

PRODUCTS = {
    "aurora-lamp": "a fictional desk lamp",
    "birch-kettle": "a fictional kettle",
}


def png_bytes(size: tuple[int, int] = (64, 48), color: tuple[int, int, int] | None = None, seed: str = "x") -> bytes:
    """Deterministic PNG; a gradient unless `color` asks for one solid colour."""
    width, height = size
    image = Image.new("RGB", size)
    if color is not None:
        image.paste(color, (0, 0, width, height))
    else:
        base = hashlib.sha256(seed.encode()).digest()
        pixels = [
            ((x * 255 // max(width - 1, 1) + base[0]) % 256, (y * 255 // max(height - 1, 1) + base[1]) % 256, base[2])
            for y in range(height)
            for x in range(width)
        ]
        image.putdata(pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageProvider:
    """Behaviour is chosen by markers in the rendered prompt, so tests need no per-call setup."""

    name = "meta"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.requests: list[ImageGenerationRequest] = []
        self.transient_failures = 0
        self.provider_errors: list[str] = []
        self.judge_calls: list[dict[str, Any]] = []
        self.judge_failures: list[ProviderError] = []
        self.judge_cost = 0.002

    def credential_error(self) -> None:
        return None

    def validate_image_parameters(self, model: str, request: ImageGenerationRequest) -> list[str]:
        errors = []
        for key in request.options:
            if key not in {"reasoning_strength", "tool_enablement"}:
                errors.append(f"fake provider does not accept provider option {key!r}")
        return errors

    def generate_image(
        self, *, model: str, request: ImageGenerationRequest, timeout_seconds: int
    ) -> ImageGenerationResponse:
        self.calls += 1
        self.prompts.append(request.prompt)
        self.requests.append(request)
        if self.transient_failures:
            self.transient_failures -= 1
            raise ProviderError("temporary fixture failure", retryable=True)
        usage = {"input_tokens": 10, "output_tokens": 0}
        metadata = {"model": model}
        prompt = request.prompt
        if "REFUSE" in prompt:
            return ImageGenerationResponse(
                images=(), request_id=f"fake-{self.calls}", usage=usage, cost_usd=0.0,
                response_metadata=metadata, refusal_reason="safety_filter",
            )
        if "CORRUPT" in prompt:
            image = GeneratedImage(png_bytes()[:40], "image/png")
        elif "SOLID" in prompt:
            image = GeneratedImage(png_bytes(color=(10, 20, 30)), "image/png")
        elif "LYING" in prompt:
            image = GeneratedImage(png_bytes(seed=prompt), "image/jpeg")
        elif "TWO" in prompt:
            return ImageGenerationResponse(
                images=(GeneratedImage(png_bytes(), "image/png"), GeneratedImage(png_bytes(seed="b"), "image/png")),
                request_id=f"fake-{self.calls}", usage=usage, cost_usd=0.02, response_metadata=metadata,
            )
        else:
            width, height = (int(part) for part in (request.size or "64x48").split("x"))
            image = GeneratedImage(
                png_bytes((width, height), seed=prompt), "image/png", revised_prompt=f"revised: {prompt[:20]}"
            )
        return ImageGenerationResponse(
            images=(image,), request_id=f"fake-{self.calls}", usage=usage, cost_usd=0.01,
            response_metadata=metadata,
        )


    def generate_structured(
        self, *, model, system_prompt, parts, output_schema, parameters, timeout_seconds
    ) -> ProviderResponse:
        text = next(part.text for part in parts if isinstance(part, TextPart))
        image = next(part for part in parts if isinstance(part, ImagePart))
        self.judge_calls.append(
            {"model": model, "system": system_prompt, "text": text, "image": image, "schema": output_schema}
        )
        if self.judge_failures:
            raise self.judge_failures.pop(0)
        dimension_schemas = output_schema["properties"]["dimensions"]["properties"]
        dimensions = {}
        for index, (dimension_id, schema) in enumerate(dimension_schemas.items()):
            fail = "BADJUDGE" in text and index == 0
            dimensions[dimension_id] = {
                "pass": not fail,
                "reason_code": schema["properties"]["reason_code"]["enum"][1] if fail else "none",
                "explanation": "looks wrong" if fail else "looks right",
            }
        body = "not json at all" if "GARBAGE" in text else json.dumps({"dimensions": dimensions})
        return ProviderResponse(
            text=body,
            request_id=f"judge-{len(self.judge_calls)}",
            usage={"input_tokens": 100, "output_tokens": 30},
            cost_usd=self.judge_cost,
            response_metadata={"model": model},
        )


RUBRIC = """\
schema_version: 1
name: product-reference-quality
dimensions:
  - id: single-product-composition
    question: Exactly one complete product, isolated, fully in frame.
    reason_codes: [multiple-products, cropped]
  - id: fictional-packaging
    question: Packaging shows no real brand or readable logo.
    reason_codes: [real-brand, readable-text]
acceptance:
  rule: all_dimensions_pass
"""

JUDGE_SYSTEM = "You are a strict image reviewer.\nRubric:\n{{ rubric.text }}\n"
JUDGE_USER = "Product brief: {{ item.input.brief }}\nJudge the attached image.\n"

JUDGE_BLOCK = """\
  model_judges:
    - id: vision-judge-1
      provider: meta
      model: fake-judge-1
      rubric: ../../rubrics/product-reference-quality.yml
      prompts:
        system: ../../prompts/judge-system.md
        user: ../../prompts/judge-user.md
      repetitions: 1
      parameters:
        max_tokens: 1000
"""

EVAL_TEMPLATE = """\
schema_version: 1
name: fake-baseline
status: active
question: Can the pipeline generate a usable reference image per product?
description: Fake-provider baseline for the image_generation task.
task: image_generation
dataset: ../../../../datasets/products-v1/dataset.yml

defaults:
  repetitions: {repetitions}
  concurrency: 1
  timeout_seconds: 30
  retries: 1
  budget_usd: 5.00
{judge_budget}  parameters:
    image:
      size: 64x48
      output_format: png
    provider_options:
{options}

arms:
  - id: baseline
    description: One product per image.
    prompts:
      prompt: ../../prompts/generate.md

runs:
  - model: {model}
    provider: {provider}
    arms: [baseline]

evaluation:
  mechanical:
{mechanical}
{judges}
metrics:
  primary: generation_success_rate
  secondary:
    - mechanical_pass_rate
    - mechanical_pass_rate_given_image
    - safety_filter_rate
    - invalid_artifact_rate
    - mean_cost_usd
"""


class ImageProject:
    """A throwaway Tamesu project with one image_generation eval and a fake provider."""

    def __init__(
        self,
        products: dict[str, str] | None = None,
        mechanical: list[str] | None = None,
        judges: bool = False,
        review: bool = False,
        required_reviews: int = 1,
        requires: list[str] | None = None,
        judge_role: str | None = None,
        on_disagreement: str = "adjudicate",
        selection: str = "none",
        repetitions: int = 1,
    ) -> None:
        self.selection = selection
        self.repetitions = repetitions
        self.judges = judges
        self.review = review
        self.required_reviews = required_reviews
        self.requires = requires if requires is not None else (["mechanical", "human"] if review else None)
        self.judge_role = judge_role
        self.on_disagreement = on_disagreement
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name) / "project"
        self.provider = FakeImageProvider()
        self._stack = ExitStack()
        products = products if products is not None else PRODUCTS
        case_dir = self.root / "cases" / "product-reference"
        (case_dir / "datasets" / "products-v1").mkdir(parents=True)
        (case_dir / "experiments" / "baseline" / "prompts").mkdir(parents=True)
        (case_dir / "experiments" / "baseline" / "evals" / "fake-baseline").mkdir(parents=True)
        (case_dir / "case.yml").write_text(
            textwrap.dedent(
                """\
                schema_version: 1
                name: product-reference
                title: Product Reference
                description: Generate fictional product reference images.
                business_use: Reference images for scanner experiments.
                current_problem: Reference images are scarce.
                technical_uncertainty: Whether generated images are usable.
                default_task: image_generation
                """
            )
        )
        items = "".join(
            f"  - id: {item_id}\n    input:\n      brief: {brief}\n    expected: {{}}\n"
            for item_id, brief in products.items()
        )
        (case_dir / "datasets" / "products-v1" / "dataset.yml").write_text(
            "schema_version: 1\nname: products-v1\ndescription: Fictional products.\nitems:\n" + items
        )
        (case_dir / "experiments" / "baseline" / "prompts" / "generate.md").write_text(
            "Photograph of {{ item.input.brief }}.\n"
        )
        self.eval_path = case_dir / "experiments" / "baseline" / "evals" / "fake-baseline" / "eval.yml"
        experiment = case_dir / "experiments" / "baseline"
        (experiment / "rubrics").mkdir()
        (experiment / "rubrics" / "product-reference-quality.yml").write_text(RUBRIC)
        (experiment / "prompts" / "judge-system.md").write_text(JUDGE_SYSTEM)
        (experiment / "prompts" / "judge-user.md").write_text(JUDGE_USER)
        self.rubric_path = experiment / "rubrics" / "product-reference-quality.yml"
        self.write_eval(mechanical or [])
        self.eval_dir = self.eval_path.parent

    def write_eval(
        self,
        mechanical: list[str] | None = None,
        model: str = FAKE_MODEL,
        provider: str = "meta",
        options: str = "      reasoning_strength: low",
    ) -> None:
        block = "\n".join(f"    - {entry}" for entry in (mechanical or [])) or "    []"
        self.eval_path.write_text(
            EVAL_TEMPLATE.format(
                model=model,
                repetitions=self.repetitions,
                provider=provider,
                options=options,
                mechanical=block,
                judges=(JUDGE_BLOCK if self.judges else "") + self._policy_block(),
                judge_budget="  judge_budget_usd: 1.00\n" if self.judges else "",
            )
        )

    def _policy_block(self) -> str:
        lines = []
        if self.review:
            lines += ["  human_review:", "    rubric: ../../rubrics/product-reference-quality.yml"]
        if self.requires is not None:
            lines += ["  acceptance:", f"    requires: [{', '.join(self.requires)}]"]
            if self.review:
                lines += [
                    "    human:",
                    f"      required_reviews_per_item: {self.required_reviews}",
                    f"      on_disagreement: {self.on_disagreement}",
                ]
            if self.judge_role:
                lines += ["    model_judge:", f"      role: {self.judge_role}"]
            lines += [f"    selection: {self.selection}"]
        return "\n".join(lines) + "\n" if lines else ""

    def __enter__(self) -> "ImageProject":
        self._stack.enter_context(
            patch.dict(
                MODELS,
                {
                    FAKE_MODEL: ModelSpec(
                        "meta", (), capabilities=IMAGE_OUTPUT_CAPABILITIES, image_pricing=FlatImagePricing(0.01)
                    ),
                    FAKE_JUDGE: ModelSpec("meta", (), 1.0, 4.0, capabilities=VISION_CAPABILITIES),
                },
            )
        )
        self._stack.enter_context(patch("tamesu.runner.get_provider", return_value=self.provider))
        self._stack.enter_context(patch("tamesu.providers.get_provider", return_value=self.provider))
        self._stack.enter_context(patch("tamesu.judging.get_provider", return_value=self.provider))
        self._stack.enter_context(patch("tamesu.judging.time.sleep"))
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stack.close()
        self._temporary.cleanup()

    def set_brief(self, item_id: str, brief: str) -> None:
        path = self.root / "cases/product-reference/datasets/products-v1/dataset.yml"
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.strip() == f"- id: {item_id}":
                lines[index + 2] = f"      brief: {brief}"
        path.write_text("\n".join(lines) + "\n")
