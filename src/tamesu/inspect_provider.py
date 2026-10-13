"""Inspect model transport for the legacy analysis and image-judge utilities.

New automated grading belongs in an Inspect task's scorers. These compatibility
utilities still write the existing review records, but no longer call Tamesu's
native text-provider implementations.
"""
from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from .errors import ProviderError
from .models import ImagePart, ProviderResponse, TextPart
from .providers.models import PROVIDER_KEYS, cost_for_usage


class InspectProvider:
    def __init__(self, name: str):
        self.name = name

    def credential_error(self) -> str | None:
        names = PROVIDER_KEYS[self.name]
        if not any(os.environ.get(key) for key in names):
            return f"{' or '.join(names)} is not set in the effective environment"
        return None

    def generate_text(self, *, user_prompt: str, **kwargs: Any) -> ProviderResponse:
        return self.generate_structured(parts=(TextPart(user_prompt),), **kwargs)

    def generate_structured(
        self, *, model: str, system_prompt: str, parts: tuple,
        output_schema: dict, parameters: dict, timeout_seconds: int,
    ) -> ProviderResponse:
        from inspect_ai.model import (
            ChatMessageSystem, ChatMessageUser, GenerateConfig, ResponseSchema, get_model,
        )
        from inspect_ai.model import ContentImage, ContentText
        from .backends.inspect_backend import ROUTES

        if self.name in ROUTES:
            route = ROUTES[self.name]
            uri, base_url = f"{route.prefix}/{model}", route.base_url()
        else:
            prefix = {"gemini": "google", "grok": "grok", "bedrock": "bedrock"}[self.name]
            uri, base_url = f"{prefix}/{model}", None
        key = next((os.environ[k] for k in PROVIDER_KEYS[self.name] if os.environ.get(k)), None)
        resolved = get_model(uri, base_url=base_url, api_key=key, memoize=False)
        content = []
        for part in parts:
            if isinstance(part, TextPart):
                content.append(ContentText(text=part.text))
            elif isinstance(part, ImagePart):
                data = base64.b64encode(part.data).decode('ascii')
                content.append(ContentImage(image=f"data:{part.media_type};base64,{data}"))
            else:
                raise TypeError(f"Unsupported content part: {type(part).__name__}")
        config = GenerateConfig(
            timeout=timeout_seconds, max_retries=0,
            max_tokens=parameters.get('max_tokens', parameters.get('max_output_tokens')),
            temperature=parameters.get('temperature'), reasoning_effort=parameters.get('effort'),
            response_schema=ResponseSchema(name='tamesu_output', json_schema=output_schema),
        )

        async def generate():
            async with resolved:
                return await resolved.generate([
                    ChatMessageSystem(content=system_prompt), ChatMessageUser(content=content),
                ], config=config)

        try:
            output = asyncio.run(generate())
        except Exception as exc:
            raise ProviderError(str(exc), retryable=resolved.api.should_retry(exc)) from exc
        usage = output.usage.model_dump(exclude_none=True) if output.usage else {}
        cost = output.usage.total_cost if output.usage else None
        if cost is None:
            cost = cost_for_usage(model, usage)
        return ProviderResponse(
            text=output.completion, request_id=output.response_id, usage=usage,
            cost_usd=cost, response_metadata={},
        )


def get_provider(name: str) -> InspectProvider:
    if name not in PROVIDER_KEYS:
        raise ValueError(f"Unsupported provider: {name}")
    return InspectProvider(name)
