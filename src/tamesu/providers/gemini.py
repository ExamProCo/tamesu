from __future__ import annotations

import os
import urllib.parse
from typing import Any

from ..errors import ProviderError
from ..models import ProviderResponse
from .common import credential, credential_error, standard_parameters
from .http import post_json
from .models import cost_for_usage, registered


EFFORT_THINKING_BUDGET = {
    "minimal": 512,
    "low": 1_024,
    "medium": 4_096,
    "high": 8_192,
    "xhigh": 16_384,
    "max": 24_576,
}


class GeminiProvider:
    name = "gemini"

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
        generation: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": output_schema,
            "maxOutputTokens": max_tokens,
        }
        if temperature is not None:
            generation["temperature"] = temperature
        thinking_config = self._thinking_config(model, effort, thinking)
        if thinking_config:
            generation["thinkingConfig"] = thinking_config
        generation.update(remaining.pop("generation_config", {}))
        if remaining:
            unsupported = ", ".join(sorted(remaining))
            raise ProviderError(f"unsupported Gemini parameters: {unsupported}")

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": generation,
        }
        base_url = os.environ.get(
            "TAMESU_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        encoded_model = urllib.parse.quote(model, safe="")
        document, headers = post_json(
            f"{base_url}/models/{encoded_model}:generateContent",
            payload,
            headers={"x-goog-api-key": credential(self.name)},
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
        )
        candidates = document.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            feedback = document.get("promptFeedback", {})
            reason = feedback.get("blockReason", "no reason given") if isinstance(feedback, dict) else "no reason given"
            raise ProviderError(f"gemini returned no candidates ({reason})")
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise ProviderError(f"gemini model hit maxOutputTokens ({max_tokens})")
        content = candidate.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "".join(
            part["text"]
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise ProviderError(f"gemini returned no text (finishReason {finish_reason})")

        raw_usage = document.get("usageMetadata") if isinstance(document.get("usageMetadata"), dict) else {}
        usage = {
            "input_tokens": int(raw_usage.get("promptTokenCount", 0)),
            "output_tokens": int(raw_usage.get("candidatesTokenCount", 0))
            + int(raw_usage.get("thoughtsTokenCount", 0)),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": int(raw_usage.get("cachedContentTokenCount", 0)),
        }
        request_id = headers.get("x-request-id")
        return ProviderResponse(
            text=text,
            request_id=request_id,
            usage=usage,
            cost_usd=cost_for_usage(model, usage),
            response_metadata={"finish_reason": finish_reason, "model": model},
        )

    @staticmethod
    def _thinking_config(model: str, effort: str | None, thinking: Any) -> dict[str, Any] | None:
        if isinstance(thinking, dict):
            return thinking
        spec = registered(model)
        style = spec.thinking if spec else None
        if style == "level" and effort:
            return {"thinkingLevel": effort}
        if style == "budget":
            budget = thinking if thinking is not None else EFFORT_THINKING_BUDGET.get(effort or "")
            return {"thinkingBudget": int(budget)} if budget is not None else None
        return None
