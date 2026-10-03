from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from ..models import ProviderResponse
from .common import credential, credential_error, standard_parameters
from .http import post_json
from .models import cost_for_usage, normalize_openai_usage


class MetaProvider:
    name = "meta"

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
        max_tokens, effort, temperature, _thinking, remaining = standard_parameters(
            model, parameters
        )
        structured_output = str(remaining.pop("structured_output", "json_schema"))
        strict = bool(remaining.pop("strict", True))
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": messages,
        }
        if structured_output == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "tamesu_output",
                    "schema": output_schema,
                    "strict": strict,
                },
            }
        elif structured_output == "json_object":
            payload["response_format"] = {"type": "json_object"}
            messages[0]["content"] = (
                f"{system_prompt}\n\nReturn JSON matching this schema:\n{output_schema}"
            )
        elif structured_output != "none":
            raise ProviderError(
                "meta structured_output must be json_schema, json_object, or none"
            )
        if effort:
            payload["reasoning_effort"] = effort
        if temperature is not None:
            payload["temperature"] = temperature
        payload.update(remaining)

        base_url = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
        document, headers = post_json(
            f"{base_url}/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {credential(self.name)}"},
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
        )
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("meta response did not contain choices")
        choice = choices[0]
        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        raw_usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        prompt_details = raw_usage.get("prompt_tokens_details", {})
        cached = prompt_details.get("cached_tokens", 0) if isinstance(prompt_details, dict) else 0
        usage = normalize_openai_usage(
            raw_usage.get("prompt_tokens"), raw_usage.get("completion_tokens"), cached
        )
        request_id = document.get("id") or headers.get("x-request-id")
        response_metadata = {
            "model": document.get("model", model),
            "finish_reason": choice.get("finish_reason"),
        }
        if not isinstance(text, str) or not text.strip():
            if choice.get("finish_reason") == "length":
                raise ProviderError(
                    f"meta model hit max_completion_tokens ({max_tokens}); "
                    "raise max_tokens or lower effort",
                    retryable=False,
                    request_id=str(request_id) if request_id else None,
                    usage=usage,
                    cost_usd=cost_for_usage(model, usage),
                    response_metadata=response_metadata,
                )
            raise ProviderError(
                "meta response did not contain output text",
                request_id=str(request_id) if request_id else None,
                usage=usage,
                cost_usd=cost_for_usage(model, usage),
                response_metadata=response_metadata,
            )

        return ProviderResponse(
            text=text,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            cost_usd=cost_for_usage(model, usage),
            response_metadata=response_metadata,
        )
