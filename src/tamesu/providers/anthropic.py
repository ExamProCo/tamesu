from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from ..models import ProviderResponse
from .common import credential, credential_error, standard_parameters
from .http import post_json
from .models import cost_for_usage


class AnthropicProvider:
    name = "anthropic"

    def credential_error(self) -> str | None:
        return credential_error(self.name)

    def generate_text(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
        parameters: dict[str, Any],
        timeout_seconds: int,
    ) -> ProviderResponse:
        max_tokens, effort, temperature, thinking, remaining = standard_parameters(
            model, parameters
        )
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system_prompt}],
            "messages": [{"role": "user", "content": user_prompt}],
            "output_config": {
                "format": {"type": "json_schema", "schema": output_schema}
            },
        }
        if effort:
            payload["output_config"]["effort"] = effort
        if temperature is not None:
            payload["temperature"] = temperature
        if thinking is not None:
            payload["thinking"] = thinking
        payload.update(remaining)

        base_url = os.environ.get(
            "TAMESU_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"
        ).rstrip("/")
        document, headers = post_json(
            f"{base_url}/messages",
            payload,
            headers={
                "x-api-key": credential(self.name),
                "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
            },
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
        )
        stop_reason = document.get("stop_reason")
        if stop_reason == "refusal":
            raise ProviderError("anthropic model declined the request")
        if stop_reason == "max_tokens":
            raise ProviderError(f"anthropic model hit max_tokens ({max_tokens})")
        content = document.get("content")
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ] if isinstance(content, list) else []
        text = "".join(texts)
        if not text.strip():
            raise ProviderError("anthropic response did not contain output text")

        raw_usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        usage = {
            "input_tokens": int(raw_usage.get("input_tokens", 0)),
            "output_tokens": int(raw_usage.get("output_tokens", 0)),
            "cache_creation_input_tokens": int(
                raw_usage.get("cache_creation_input_tokens", 0)
            ),
            "cache_read_input_tokens": int(raw_usage.get("cache_read_input_tokens", 0)),
        }
        request_id = document.get("id") or headers.get("request-id")
        return ProviderResponse(
            text=text,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            cost_usd=cost_for_usage(model, usage),
            response_metadata={"model": document.get("model", model), "stop_reason": stop_reason},
        )
