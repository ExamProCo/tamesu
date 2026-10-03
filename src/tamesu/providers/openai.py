from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from ..models import ProviderResponse
from .common import credential, credential_error, standard_parameters
from .http import post_json
from .models import cost_for_usage, normalize_openai_usage


class ResponsesProvider:
    name = "openai"
    base_url_environment = "TAMESU_OPENAI_BASE_URL"
    default_base_url = "https://api.openai.com/v1"

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
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": max_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "tamesu_output",
                    "schema": output_schema,
                    "strict": True,
                }
            },
        }
        if effort:
            payload["reasoning"] = {"effort": effort}
        if temperature is not None:
            payload["temperature"] = temperature
        reserved = {"model", "instructions", "input", "text", "stream", "max_output_tokens"}
        conflicting = sorted(reserved & set(remaining))
        if conflicting:
            raise ProviderError(
                f"{self.name} parameters are managed by Tamesu: {', '.join(conflicting)}"
            )
        payload.update(remaining)

        base_url = os.environ.get(self.base_url_environment, self.default_base_url).rstrip("/")
        document, headers = post_json(
            f"{base_url}/responses",
            payload,
            headers={"Authorization": f"Bearer {credential(self.name)}"},
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
        )
        status = document.get("status")
        if status == "incomplete":
            reason = document.get("incomplete_details", {}).get("reason", "unknown reason")
            raise ProviderError(f"{self.name} response was incomplete: {reason}", retryable=True)

        text = _extract_output_text(document, self.name)
        usage_document = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        input_details = usage_document.get("input_tokens_details", {})
        cached_tokens = input_details.get("cached_tokens", 0) if isinstance(input_details, dict) else 0
        usage = normalize_openai_usage(
            usage_document.get("input_tokens"),
            usage_document.get("output_tokens"),
            cached_tokens,
        )
        request_id = document.get("id") or headers.get("x-request-id")
        return ProviderResponse(
            text=text,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            cost_usd=cost_for_usage(model, usage),
            response_metadata={
                "status": status,
                "model": document.get("model", model),
            },
        )


class OpenAIProvider(ResponsesProvider):
    name = "openai"
    base_url_environment = "TAMESU_OPENAI_BASE_URL"
    default_base_url = "https://api.openai.com/v1"


class GrokProvider(ResponsesProvider):
    name = "grok"
    base_url_environment = "GROK_BASE_URL"
    default_base_url = "https://api.x.ai/v1"


def _extract_output_text(document: dict[str, Any], provider_name: str) -> str:
    direct = document.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    texts: list[str] = []
    refusals: list[str] = []
    output = document.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                continue
            for part in item["content"]:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                if part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refusals.append(part["refusal"])
    if texts:
        return "".join(texts)
    if refusals:
        raise ProviderError(f"{provider_name} refused the request: {' '.join(refusals)}")
    raise ProviderError(f"{provider_name} response did not contain output text", retryable=False)
