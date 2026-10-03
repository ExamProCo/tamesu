from __future__ import annotations

import json
import os
import re
import urllib.parse
from typing import Any

from ..errors import ProviderError
from ..models import ProviderResponse
from .common import credential, credential_error, standard_parameters
from .http import post_json
from .models import cost_for_usage


class BedrockProvider:
    name = "bedrock"

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
        structured_output = str(remaining.pop("structured_output", "tool"))
        if remaining:
            unsupported = ", ".join(sorted(remaining))
            raise ProviderError(f"unsupported Bedrock parameters: {unsupported}")

        system_text = system_prompt
        payload: dict[str, Any] = {
            "system": [{"text": system_text}],
            "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": 0 if temperature is None else temperature,
            },
            "additionalModelRequestFields": {"inferenceConfig": {"topK": 1}},
        }
        if effort:
            payload["additionalModelRequestFields"]["reasoningConfig"] = {
                "type": "enabled",
                "maxReasoningEffort": effort,
            }
        if structured_output == "tool":
            payload["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "tamesu_output",
                            "description": "Return the structured answer.",
                            "inputSchema": {"json": _nova_tool_schema(output_schema)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "tamesu_output"}},
            }
        elif structured_output == "prompt":
            payload["system"][0]["text"] = (
                f"{system_text}\n\nReturn only JSON matching this schema, with no prose "
                f"and no code fence:\n{json.dumps(output_schema, indent=2)}"
            )
        else:
            raise ProviderError("bedrock structured_output must be tool or prompt")

        region = os.environ.get("BEDROCK_REGION", "us-east-1")
        model_id = _bedrock_model_id(model, region)
        base_url = os.environ.get(
            "TAMESU_BEDROCK_BASE_URL", f"https://bedrock-runtime.{region}.amazonaws.com"
        ).rstrip("/")
        encoded_model = urllib.parse.quote(model_id, safe="")
        document, headers = post_json(
            f"{base_url}/model/{encoded_model}/converse",
            payload,
            headers={"Authorization": f"Bearer {credential(self.name)}"},
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
        )
        stop_reason = document.get("stopReason")
        if stop_reason == "max_tokens":
            raise ProviderError(f"bedrock model hit maxTokens ({max_tokens})")
        output = document.get("output", {})
        message = output.get("message", {}) if isinstance(output, dict) else {}
        blocks = message.get("content", []) if isinstance(message, dict) else []
        text = _bedrock_text(blocks, structured_output, stop_reason)

        raw_usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        usage = {
            "input_tokens": int(raw_usage.get("inputTokens", 0)),
            "output_tokens": int(raw_usage.get("outputTokens", 0)),
            "cache_creation_input_tokens": int(raw_usage.get("cacheWriteInputTokens", 0)),
            "cache_read_input_tokens": int(raw_usage.get("cacheReadInputTokens", 0)),
        }
        return ProviderResponse(
            text=text,
            request_id=headers.get("x-amzn-requestid"),
            usage=usage,
            cost_usd=cost_for_usage(model, usage),
            response_metadata={"stop_reason": stop_reason, "model": model_id},
        )


def _nova_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {key: schema[key] for key in ("type", "properties", "required") if key in schema}


def _bedrock_model_id(model: str, region: str) -> str:
    if not re.match(r"^(us|eu|jp)\.", model):
        return model
    if re.match(r"^(us|ca)-", region):
        geo = "us"
    elif re.match(r"^(eu|il)-", region):
        geo = "eu"
    elif re.match(r"^ap-northeast-[13]$", region):
        geo = "jp"
    else:
        return re.sub(r"^(us|eu|jp)\.", "", model)
    return re.sub(r"^(us|eu|jp)\.", f"{geo}.", model)


def _bedrock_text(blocks: Any, mode: str, stop_reason: Any) -> str:
    if not isinstance(blocks, list):
        blocks = []
    if mode == "tool":
        for block in blocks:
            if not isinstance(block, dict):
                continue
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict) and tool_use.get("name") == "tamesu_output":
                return json.dumps(tool_use.get("input"))
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        raise ProviderError(
            f"bedrock model did not call tamesu_output (stopReason {stop_reason}): {text[:200]}"
        )
    text = "".join(
        block["text"]
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ProviderError(f"bedrock response contained no JSON object: {text[:200]}")
    return match.group(0)
