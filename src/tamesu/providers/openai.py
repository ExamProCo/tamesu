from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from ..models import ImageGenerationRequest, ImageGenerationResponse, ProviderResponse
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

    IMAGE_FORMATS = ("png", "jpeg", "webp")
    QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
    BACKGROUNDS = ("transparent", "opaque", "auto")
    MODERATION = ("low", "auto")

    def validate_image_parameters(
        self, model: str, request: ImageGenerationRequest
    ) -> list[str]:
        errors: list[str] = []
        spec = registered(model)
        if spec is None or "image_output" not in spec.capabilities:
            errors.append(f"model {model!r} is not a registered openai image model")
        if request.output_format and request.output_format not in self.IMAGE_FORMATS:
            errors.append(f"openai output_format must be one of {', '.join(self.IMAGE_FORMATS)}")
        options = dict(request.options)
        quality = options.pop("quality", None)
        if quality is not None and quality not in self.QUALITIES:
            errors.append(f"openai provider_options.quality must be one of {', '.join(self.QUALITIES)}")
        background = options.pop("background", None)
        if background is not None and background not in self.BACKGROUNDS:
            errors.append(f"openai provider_options.background must be one of {', '.join(self.BACKGROUNDS)}")
        if background == "transparent" and request.output_format == "jpeg":
            errors.append("openai transparent background needs png or webp output")
        compression = options.pop("output_compression", None)
        if compression is not None:
            if isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100:
                errors.append("openai provider_options.output_compression must be an integer 0-100")
            if request.output_format not in ("jpeg", "webp"):
                errors.append("openai output_compression applies only to jpeg or webp output")
        moderation = options.pop("moderation", None)
        if moderation is not None and moderation not in self.MODERATION:
            errors.append(f"openai provider_options.moderation must be one of {', '.join(self.MODERATION)}")
        for key in sorted(options):
            errors.append(f"openai does not accept provider option {key!r}")
        return errors

    def generate_image(
        self, *, model: str, request: ImageGenerationRequest, timeout_seconds: int
    ) -> ImageGenerationResponse:
        problems = self.validate_image_parameters(model, request)
        if problems:
            raise ProviderError("; ".join(problems), retryable=False)
        payload: dict[str, Any] = {"model": model, "prompt": request.prompt, "n": 1}
        if request.size:
            payload["size"] = request.size
        if request.output_format:
            payload["output_format"] = request.output_format
        payload.update(request.options)

        base_url = os.environ.get(self.base_url_environment, self.default_base_url).rstrip("/")
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
            # A moderation block returns no image; whether it is billed is not confirmed,
            # so the cost stays unknown rather than being recorded as zero.
            return ImageGenerationResponse(
                images=(),
                request_id=exc.request_id,
                usage={},
                cost_usd=None,
                response_metadata={"model": model, "requested_model": model, **exc.response_metadata},
                refusal_reason=reason,
            )
        usage_document = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        details = usage_document.get("input_tokens_details")
        details = details if isinstance(details, dict) else {}
        input_tokens = usage_document.get("input_tokens", usage_document.get("prompt_tokens"))
        usage: dict[str, Any] = {}
        if input_tokens is not None or "output_tokens" in usage_document or "completion_tokens" in usage_document:
            image_in = int(details.get("image_tokens", 0) or 0)
            total_in = int(input_tokens or 0)
            usage = {
                "input_tokens": total_in,
                "text_input_tokens": int(details.get("text_tokens", total_in - image_in)),
                "image_input_tokens": image_in,
                "output_tokens": int(
                    usage_document.get("output_tokens", usage_document.get("completion_tokens", 0)) or 0
                ),
            }
        images = decode_b64_images(
            document.get("data"),
            media_type_for(document.get("output_format") or request.output_format or "png"),
            self.name,
        )
        request_id = headers.get("x-request-id") if headers else None
        metadata = {
            "model": document.get("model", model),
            "requested_model": model,
            "created": document.get("created"),
            "quality": document.get("quality"),
            "size": document.get("size"),
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
