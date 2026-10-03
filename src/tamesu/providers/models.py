from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PRICING_VERIFIED_AT = "2026-08-25"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    efforts: tuple[str, ...]
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    max_tokens: int = 16_000
    thinking: str | None = None


ANTHROPIC_EFFORTS = ("low", "medium", "high", "xhigh", "max")
ANTHROPIC_46_EFFORTS = ("low", "medium", "high", "max")
OPENAI_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
GEMINI_EFFORTS = ("minimal", "low", "medium", "high")
META_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
GROK_EFFORTS = ("low", "medium", "high", "xhigh")
GROK_45_EFFORTS = ("low", "medium", "high")
NOVA_EFFORTS = ("low", "medium", "high")


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
    "muse-spark-1.2": ModelSpec("meta", META_EFFORTS, 1.25, 4.25),
    "muse-spark-1.1": ModelSpec("meta", META_EFFORTS, 1.25, 4.25),
    "muse-spark-1.2-contributor": ModelSpec("meta", META_EFFORTS),
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
