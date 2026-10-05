from __future__ import annotations

import base64
import os
from typing import Any

from ..errors import ProviderError
from ..models import (
    ContentPart,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImagePart,
    ProviderResponse,
    TextPart,
)
from .common import (
    credential,
    credential_error,
    decode_b64_images,
    media_type_for,
    refusal_code,
    standard_parameters,
)
from .http import post_json
from .models import cost_for_usage, image_cost, normalize_openai_usage, registered


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
        return self._chat(
            model=model,
            system_prompt=system_prompt,
            user_content=user_prompt,
            output_schema=output_schema,
            parameters=parameters,
            timeout_seconds=timeout_seconds,
        )

    def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        parts: tuple[ContentPart, ...],
        output_schema: dict[str, Any],
        parameters: dict[str, Any],
        timeout_seconds: int,
    ) -> ProviderResponse:
        spec = registered(model)
        if spec is not None and "image_input" not in spec.capabilities and any(
            isinstance(part, ImagePart) for part in parts
        ):
            raise ProviderError(f"meta model {model!r} does not accept image input", retryable=False)
        return self._chat(
            model=model,
            system_prompt=system_prompt,
            user_content=[_content_part(part) for part in parts],
            output_schema=output_schema,
            parameters=parameters,
            timeout_seconds=timeout_seconds,
        )

    # -- image generation -----------------------------------------------------------

    IMAGE_FORMATS = ("webp", "png", "jpeg")
    TOOL_SWITCHES = ("enable_image_search", "enable_web_search", "enable_shell")

    def validate_image_parameters(
        self, model: str, request: ImageGenerationRequest
    ) -> list[str]:
        errors: list[str] = []
        spec = registered(model)
        if spec is None or "image_output" not in spec.capabilities:
            errors.append(f"model {model!r} is not a registered meta image model")
        if request.output_format and request.output_format not in self.IMAGE_FORMATS:
            errors.append(f"meta output_format must be one of {', '.join(self.IMAGE_FORMATS)}")
        options = dict(request.options)
        strength = options.pop("reasoning_strength", None)
        if strength is not None and strength not in ("high", "low"):
            errors.append("meta provider_options.reasoning_strength must be high or low")
        tools = options.pop("tool_enablement", None)
        if tools is not None:
            if not isinstance(tools, dict) or set(tools) - set(self.TOOL_SWITCHES) or not all(
                isinstance(value, bool) for value in tools.values()
            ):
                errors.append(
                    "meta provider_options.tool_enablement must map "
                    f"{', '.join(self.TOOL_SWITCHES)} to booleans"
                )
        for key in sorted(options):
            errors.append(f"meta does not accept provider option {key!r}")
        return errors

    def generate_image(
        self, *, model: str, request: ImageGenerationRequest, timeout_seconds: int
    ) -> ImageGenerationResponse:
        problems = self.validate_image_parameters(model, request)
        if problems:
            raise ProviderError("; ".join(problems), retryable=False)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "n": 1,  # one call per item keeps retry, cost, and item-to-image mapping unambiguous
            "response_format": "b64_json",  # inline bytes: no URL expiry or download failure mode
        }
        if request.size:
            payload["size"] = request.size
        if request.output_format:
            payload["output_format"] = request.output_format
        payload.update(request.options)

        base_url = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
        try:
            document, headers = post_json(
                f"{base_url}/images/generations",
                payload,
                headers={"Authorization": f"Bearer {credential(self.name)}"},
                timeout_seconds=timeout_seconds,
                provider_name=self.name,
            )
        except ProviderError as exc:
            reason = refusal_code(exc)
            if reason is None:
                raise
            # Billing excludes safety-filtered requests, and the provider confirmed no image.
            return ImageGenerationResponse(
                images=(),
                request_id=exc.request_id,
                usage={},
                cost_usd=0.0,
                response_metadata={"model": model, "requested_model": model, **exc.response_metadata},
                refusal_reason=reason,
            )
        raw_usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        usage = {
            "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
            "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
        }
        images = decode_b64_images(
            document.get("data"), media_type_for(document.get("output_format")), self.name
        )
        request_id = headers.get("x-request-id") if headers else None
        metadata = {
            "model": document.get("model", model),
            "requested_model": model,
            "created": document.get("created"),
            "output_format": document.get("output_format"),
            "background": document.get("background"),
        }
        priced_parameters = {
            "image": {"size": request.size, "output_format": request.output_format},
            "provider_options": request.options,
        }
        return ImageGenerationResponse(
            images=images,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            cost_usd=image_cost(model, priced_parameters, usage, len(images)),
            response_metadata={k: v for k, v in metadata.items() if v is not None},
            refusal_reason="safety_filter" if not images else None,
        )

    def _chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str | list[dict[str, Any]],
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
            {"role": "user", "content": user_content},
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


def _content_part(part: ContentPart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    encoded = base64.b64encode(part.data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{part.media_type};base64,{encoded}"}}
