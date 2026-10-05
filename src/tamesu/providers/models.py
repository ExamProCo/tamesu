from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PRICING_VERIFIED_AT = "2026-10-05"


TEXT_CAPABILITIES = frozenset({"text_output", "structured_output"})
VISION_CAPABILITIES = TEXT_CAPABILITIES | {"image_input"}
IMAGE_OUTPUT_CAPABILITIES = frozenset({"image_output"})


@dataclass(frozen=True)
class FlatImagePricing:
    """A flat price per generated image, independent of size or quality."""

    usd_per_image: float

    def price(self, parameters: dict[str, Any]) -> float | None:
        return self.usd_per_image

    def cost(self, parameters: dict[str, Any], usage: dict[str, Any], images: int) -> float | None:
        return round(self.usd_per_image * images, 6)


@dataclass(frozen=True)
class ImageMatrixPricing:
    """Per-image prices keyed by (quality, size). Missing combinations are unknown."""

    usd_per_image: dict[tuple[str, str], float]

    def price(self, parameters: dict[str, Any]) -> float | None:
        image = parameters.get("image") if isinstance(parameters.get("image"), dict) else {}
        options = (
            parameters.get("provider_options")
            if isinstance(parameters.get("provider_options"), dict)
            else {}
        )
        key = (str(options.get("quality", "")), str(image.get("size", "")))
        return self.usd_per_image.get(key)

    def cost(self, parameters: dict[str, Any], usage: dict[str, Any], images: int) -> float | None:
        unit = self.price(parameters)
        return None if unit is None else round(unit * images, 6)


@dataclass(frozen=True)
class ImageTokenPricing:
    """Token-billed image models. The bill is exact from usage, but a plan cannot predict
    output tokens for a quality/size, so planning stays unknown unless a table is supplied."""

    text_input_usd_per_million: float
    image_input_usd_per_million: float
    image_output_usd_per_million: float
    expected_output_tokens: dict[tuple[str, str], int] = field(default_factory=dict)

    def price(self, parameters: dict[str, Any]) -> float | None:
        image = parameters.get("image") if isinstance(parameters.get("image"), dict) else {}
        options = (
            parameters.get("provider_options")
            if isinstance(parameters.get("provider_options"), dict)
            else {}
        )
        tokens = self.expected_output_tokens.get((str(options.get("quality", "")), str(image.get("size", ""))))
        if tokens is None:
            return None
        return round(tokens * self.image_output_usd_per_million / 1_000_000, 6)

    def cost(self, parameters: dict[str, Any], usage: dict[str, Any], images: int) -> float | None:
        if "output_tokens" not in usage:
            return None
        text_in = int(usage.get("text_input_tokens", usage.get("input_tokens", 0)))
        image_in = int(usage.get("image_input_tokens", 0))
        return round(
            (
                text_in * self.text_input_usd_per_million
                + image_in * self.image_input_usd_per_million
                + int(usage["output_tokens"]) * self.image_output_usd_per_million
            )
            / 1_000_000,
            6,
        )


ImagePricing = FlatImagePricing | ImageMatrixPricing | ImageTokenPricing


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    efforts: tuple[str, ...]
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    max_tokens: int = 16_000
    thinking: str | None = None
    capabilities: frozenset[str] = TEXT_CAPABILITIES
    image_pricing: ImagePricing | None = None


ANTHROPIC_EFFORTS = ("low", "medium", "high", "xhigh", "max")
ANTHROPIC_46_EFFORTS = ("low", "medium", "high", "max")
OPENAI_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
GEMINI_EFFORTS = ("minimal", "low", "medium", "high")
META_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
GROK_EFFORTS = ("low", "medium", "high", "xhigh")
GROK_45_EFFORTS = ("low", "medium", "high")
NOVA_EFFORTS = ("low", "medium", "high")


# USD per million tokens, from OpenAI's pricing page (verified 2026-10-05).
OPENAI_IMAGE_2_PRICING = ImageTokenPricing(5.0, 8.0, 30.0)

# Ported from Shiori's evaluated provider registry. Prices are USD per million tokens and
# should be reviewed against first-party pricing before long-running comparisons.
MODELS: dict[str, ModelSpec] = {
    # Anthropic
    "claude-haiku-4-5": ModelSpec("anthropic", (), 1.0, 5.0, max_tokens=8_000),
    "claude-sonnet-5": ModelSpec("anthropic", ANTHROPIC_EFFORTS, 3.0, 15.0),
    "claude-opus-5": ModelSpec("anthropic", ANTHROPIC_EFFORTS, 5.0, 25.0),
    "claude-fable-5": ModelSpec("anthropic", ANTHROPIC_EFFORTS, 10.0, 50.0),
    "claude-opus-4-8": ModelSpec("anthropic", ANTHROPIC_EFFORTS, 5.0, 25.0),
    "claude-opus-4-7": ModelSpec("anthropic", ANTHROPIC_EFFORTS, 5.0, 25.0),
    "claude-opus-4-6": ModelSpec("anthropic", ANTHROPIC_46_EFFORTS, 5.0, 25.0),
    "claude-sonnet-4-6": ModelSpec("anthropic", ANTHROPIC_46_EFFORTS, 3.0, 15.0),
    # OpenAI
    "gpt-5.6-sol": ModelSpec("openai", OPENAI_EFFORTS, 5.0, 30.0),
    "gpt-5.6-terra": ModelSpec("openai", OPENAI_EFFORTS, 2.0, 12.0),
    "gpt-5.6-luna": ModelSpec("openai", OPENAI_EFFORTS, 0.20, 1.20),
    "gpt-5.5": ModelSpec("openai", OPENAI_EFFORTS, 5.0, 30.0),
    "gpt-5.5-pro": ModelSpec("openai", OPENAI_EFFORTS, 30.0, 180.0),
    "gpt-5.4": ModelSpec("openai", OPENAI_EFFORTS, 2.50, 15.0),
    "gpt-5.4-mini": ModelSpec("openai", OPENAI_EFFORTS, 0.75, 4.50),
    "gpt-5.4-nano": ModelSpec("openai", OPENAI_EFFORTS, 0.20, 1.25),
    # Meta
    "muse-spark-1.3": ModelSpec(
        "meta", META_EFFORTS, 1.25, 4.25, capabilities=VISION_CAPABILITIES
    ),
    "muse-spark-1.2": ModelSpec(
        "meta", META_EFFORTS, 1.25, 4.25, capabilities=VISION_CAPABILITIES
    ),
    "muse-spark-1.1": ModelSpec(
        "meta", META_EFFORTS, 1.25, 4.25, capabilities=VISION_CAPABILITIES
    ),
    "muse-spark-1.2-contributor": ModelSpec("meta", META_EFFORTS),
    # Image generation (not token-priced text models; see image_pricing)
    "muse-image-1.0": ModelSpec(
        "meta", (), capabilities=IMAGE_OUTPUT_CAPABILITIES, image_pricing=FlatImagePricing(0.01)
    ),
    "gpt-image-2": ModelSpec(
        "openai", (), capabilities=IMAGE_OUTPUT_CAPABILITIES, image_pricing=OPENAI_IMAGE_2_PRICING
    ),
    "gpt-image-2.5-sunburst": ModelSpec(
        "openai", (), capabilities=IMAGE_OUTPUT_CAPABILITIES, image_pricing=OPENAI_IMAGE_2_PRICING
    ),
    "gpt-image-2.5-flare": ModelSpec(
        "openai", (), capabilities=IMAGE_OUTPUT_CAPABILITIES, image_pricing=OPENAI_IMAGE_2_PRICING
    ),
    # xAI
    "grok-4.6": ModelSpec("grok", GROK_EFFORTS, 2.0, 6.0),
    "grok-4.5": ModelSpec("grok", GROK_45_EFFORTS, 2.0, 6.0),
    "grok-4.3": ModelSpec("grok", (), 1.25, 2.50),
    "grok-build-0.1": ModelSpec("grok", (), 1.0, 2.0),
    "grok-4.20-0309-reasoning": ModelSpec("grok", (), 1.25, 2.50),
    "grok-4.20-0309-non-reasoning": ModelSpec("grok", (), 1.25, 2.50),
    "grok-4.20-multi-agent-0309": ModelSpec("grok", (), 1.25, 2.50),
    # Amazon Nova through Bedrock
    "us.amazon.nova-2-lite-v1:0": ModelSpec("bedrock", NOVA_EFFORTS, 0.30, 2.50),
    "us.amazon.nova-pro-v1:0": ModelSpec("bedrock", (), 0.80, 3.20, max_tokens=5_000),
    # Google
    "gemini-3.1-pro-preview": ModelSpec("gemini", GEMINI_EFFORTS, 2.0, 12.0, thinking="level"),
    "gemini-3.7-flash": ModelSpec("gemini", GEMINI_EFFORTS, 0.75, 3.75, thinking="level"),
    "gemini-3.6-flash": ModelSpec("gemini", GEMINI_EFFORTS, 0.75, 3.75, thinking="level"),
    "gemini-3.5-flash": ModelSpec("gemini", GEMINI_EFFORTS, 1.50, 9.0, thinking="level"),
    "gemini-3.5-flash-lite": ModelSpec("gemini", GEMINI_EFFORTS, 0.30, 2.50, thinking="level"),
    "gemini-3.1-flash-lite": ModelSpec("gemini", GEMINI_EFFORTS, 0.25, 1.50, thinking="level"),
    "gemini-2.5-pro": ModelSpec("gemini", ANTHROPIC_EFFORTS, 1.25, 10.0, thinking="budget"),
    "gemini-2.5-flash": ModelSpec("gemini", ANTHROPIC_EFFORTS, 0.30, 2.50, thinking="budget"),
    "gemini-2.5-flash-lite": ModelSpec("gemini", ANTHROPIC_EFFORTS, 0.10, 0.40, thinking="budget"),
}


PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "meta": ("META_API_KEY",),
    "grok": ("GROK_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "bedrock": ("AWS_BEARER_TOKEN_BEDROCK", "BEDROCK_API_KEY"),
}


def registered(model: str) -> ModelSpec | None:
    return MODELS.get(model)


def max_tokens_for(model: str) -> int:
    spec = registered(model)
    return spec.max_tokens if spec else 16_000


def validate_effort(model: str, effort: str | None) -> str | None:
    if effort is None:
        return None
    spec = registered(model)
    if spec and effort not in spec.efforts:
        allowed = ", ".join(spec.efforts) if spec.efforts else "none"
        return f"model {model!r} does not accept effort {effort!r}; allowed: {allowed}"
    return None


def normalize_openai_usage(
    prompt_tokens: Any, completion_tokens: Any, cached_tokens: Any = 0
) -> dict[str, int]:
    cached = int(cached_tokens or 0)
    return {
        "input_tokens": max(int(prompt_tokens or 0) - cached, 0),
        "output_tokens": int(completion_tokens or 0),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cached,
    }


def cost_for_usage(model: str, usage: dict[str, Any]) -> float | None:
    spec = registered(model)
    if not spec or spec.input_usd_per_million is None or spec.output_usd_per_million is None:
        return None
    input_rate = spec.input_usd_per_million
    output_rate = spec.output_usd_per_million
    input_cost = (
        int(usage.get("input_tokens", 0)) * input_rate
        + int(usage.get("cache_creation_input_tokens", 0)) * input_rate * 1.25
        + int(usage.get("cache_read_input_tokens", 0)) * input_rate * 0.1
    ) / 1_000_000
    output_cost = int(usage.get("output_tokens", 0)) * output_rate / 1_000_000
    return round(input_cost + output_cost, 6)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    return cost_for_usage(
        model,
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    )


def models_for_provider(provider: str) -> dict[str, ModelSpec]:
    return {name: spec for name, spec in MODELS.items() if spec.provider == provider}


def image_cost(
    model: str, parameters: dict[str, Any], usage: dict[str, Any], images: int
) -> float | None:
    """Exact cost of an image call from its usage, or None when it cannot be priced."""
    spec = registered(model)
    if spec is None or spec.image_pricing is None:
        return None
    return spec.image_pricing.cost(parameters, usage, images)
