from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from .models import PROVIDER_KEYS, max_tokens_for, validate_effort


def credential(provider: str) -> str:
    names = PROVIDER_KEYS[provider]
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    raise ProviderError(f"{' or '.join(names)} is not set", retryable=False)


def credential_error(provider: str) -> str | None:
    names = PROVIDER_KEYS[provider]
    if any(os.environ.get(name, "") for name in names):
        return None
    return f"{' or '.join(names)} is not set"


def standard_parameters(model: str, parameters: dict[str, Any]) -> tuple[int, str | None, Any, Any, dict[str, Any]]:
    remaining = dict(parameters)
    max_tokens = int(
        remaining.pop("max_tokens", remaining.pop("max_output_tokens", max_tokens_for(model)))
    )
    effort = remaining.pop("effort", None)
    if effort is not None:
        effort = str(effort)
    error = validate_effort(model, effort)
    if error:
        raise ProviderError(error, retryable=False)
    temperature = remaining.pop("temperature", None)
    thinking = remaining.pop("thinking", None)
    return max_tokens, effort, temperature, thinking, remaining
