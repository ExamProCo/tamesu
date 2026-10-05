from __future__ import annotations

import base64
import binascii
import os
from typing import Any

from ..errors import ProviderError
from ..models import GeneratedImage
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
    return (
        f"{' or '.join(names)} is not set in the effective environment "
        "(process, project, workspace, global, or --config-dir)"
    )


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


REFUSAL_ERROR_MARKERS = ("moderation", "safety", "content_policy", "content_filter")


def decode_b64_images(
    items: Any, media_type: str | None, provider_name: str
) -> tuple[GeneratedImage, ...]:
    """Decode `data[].b64_json` entries. A provider URL is never fetched: bytes only."""
    if items is None:
        return ()
    if not isinstance(items, list):
        raise ProviderError(f"{provider_name} image response data must be a list", retryable=False)
    images = []
    for entry in items:
        encoded = entry.get("b64_json") if isinstance(entry, dict) else None
        if not isinstance(encoded, str):
            raise ProviderError(
                f"{provider_name} image response entry had no b64_json payload", retryable=False
            )
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError(f"{provider_name} returned invalid base64 image data", retryable=False) from exc
        revised = entry.get("revised_prompt")
        images.append(GeneratedImage(data, media_type, revised if isinstance(revised, str) else None))
    return tuple(images)


def refusal_code(error: ProviderError) -> str | None:
    """A stable refusal code when a 400 is the provider declining on safety grounds."""
    if error.status_code != 400:
        return None
    code = str(error.response_metadata.get("error_code", "")).lower()
    kind = str(error.response_metadata.get("error_type", "")).lower()
    if any(marker in code or marker in kind for marker in REFUSAL_ERROR_MARKERS):
        return code or "safety_filter"
    return None


def media_type_for(output_format: Any) -> str | None:
    return {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}.get(
        str(output_format).lower()
    )
