from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    ImageGenerationProvider,
    Provider,
    StructuredMultimodalProvider,
    TextGenerationProvider,
)
from .bedrock import BedrockProvider
from .gemini import GeminiProvider
from .meta import MetaProvider
from .openai import GrokProvider, OpenAIProvider


def get_provider(name: str) -> Provider:
    providers: dict[str, type] = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "meta": MetaProvider,
        "grok": GrokProvider,
        "bedrock": BedrockProvider,
        "gemini": GeminiProvider,
    }
    try:
        provider_type = providers[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {name}") from exc
    return provider_type()


__all__ = [
    "ImageGenerationProvider",
    "Provider",
    "StructuredMultimodalProvider",
    "TextGenerationProvider",
    "get_provider",
]
