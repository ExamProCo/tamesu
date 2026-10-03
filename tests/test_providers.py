from __future__ import annotations

import json
import os
import unittest
from email.message import Message
from unittest.mock import patch

from tamesu.providers.anthropic import AnthropicProvider
from tamesu.providers.bedrock import BedrockProvider
from tamesu.providers.gemini import GeminiProvider
from tamesu.errors import ProviderError
from tamesu.providers.meta import MetaProvider
from tamesu.providers.openai import GrokProvider


SCHEMA = {
    "type": "object",
    "required": ["label"],
    "properties": {"label": {"type": "string"}},
}


class FakeResponse:
    def __init__(self, payload: dict[str, object], **headers: str) -> None:
        self.payload = json.dumps(payload).encode()
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name.replace("_", "-")] = value

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def request_payload(urlopen: object) -> tuple[object, dict[str, object]]:
    request = urlopen.call_args.args[0]  # type: ignore[attr-defined]
    return request, json.loads(request.data)


class ProviderSerializationTests(unittest.TestCase):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_anthropic(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "msg_1",
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"label":"a"}'}],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        )
        response = AnthropicProvider().generate_text(
            model="claude-sonnet-5",
            system_prompt="system",
            user_prompt="user",
            output_schema=SCHEMA,
            parameters={"effort": "high", "max_tokens": 200},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertTrue(request.full_url.endswith("/messages"))
        self.assertEqual(payload["output_config"]["effort"], "high")
        self.assertEqual(payload["output_config"]["format"]["schema"], SCHEMA)
        self.assertGreater(response.cost_usd or 0, 0)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_meta(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "chat_1",
                "model": "muse-spark-1.2",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"label":"a"}'}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        MetaProvider().generate_text(
            model="muse-spark-1.2",
            system_prompt="system",
            user_prompt="user",
            output_schema=SCHEMA,
            parameters={"effort": "medium"},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertTrue(request.full_url.endswith("/chat/completions"))
        self.assertEqual(payload["max_completion_tokens"], 16_000)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_meta_token_cap_preserves_usage_and_cost(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "chat_capped",
                "model": "muse-spark-1.2",
                "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 256},
            }
        )
        with self.assertRaises(ProviderError) as raised:
            MetaProvider().generate_text(
                model="muse-spark-1.2",
                system_prompt="system",
                user_prompt="user",
                output_schema=SCHEMA,
                parameters={"effort": "minimal", "max_tokens": 256},
                timeout_seconds=30,
            )

        error = raised.exception
        self.assertIn("max_completion_tokens (256)", str(error))
        self.assertEqual(error.request_id, "chat_capped")
        self.assertEqual(error.usage["output_tokens"], 256)
        self.assertGreater(error.cost_usd or 0, 0)

    @patch.dict(os.environ, {"GROK_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_grok(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "resp_1",
                "status": "completed",
                "model": "grok-4.6",
                "output_text": '{"label":"a"}',
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        )
        GrokProvider().generate_text(
            model="grok-4.6",
            system_prompt="system",
            user_prompt="user",
            output_schema=SCHEMA,
            parameters={"effort": "low"},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertEqual(request.full_url, "https://api.x.ai/v1/responses")
        self.assertEqual(payload["reasoning"], {"effort": "low"})

    @patch.dict(os.environ, {"GEMINI_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_gemini(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": '{"label":"a"}'}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
            }
        )
        GeminiProvider().generate_text(
            model="gemini-3.7-flash",
            system_prompt="system",
            user_prompt="user",
            output_schema=SCHEMA,
            parameters={"effort": "high"},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertIn("gemini-3.7-flash:generateContent", request.full_url)
        generation = payload["generationConfig"]
        self.assertEqual(generation["responseJsonSchema"], SCHEMA)
        self.assertEqual(generation["thinkingConfig"], {"thinkingLevel": "high"})

    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_bedrock(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "stopReason": "tool_use",
                "output": {
                    "message": {
                        "content": [
                            {"toolUse": {"name": "tamesu_output", "input": {"label": "a"}}}
                        ]
                    }
                },
                "usage": {"inputTokens": 10, "outputTokens": 4},
            },
            x_amzn_requestid="bedrock-1",
        )
        response = BedrockProvider().generate_text(
            model="us.amazon.nova-2-lite-v1:0",
            system_prompt="system",
            user_prompt="user",
            output_schema=SCHEMA,
            parameters={"effort": "low"},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertIn("/converse", request.full_url)
        self.assertEqual(
            payload["toolConfig"]["toolChoice"], {"tool": {"name": "tamesu_output"}}
        )
        self.assertEqual(json.loads(response.text), {"label": "a"})


if __name__ == "__main__":
    unittest.main()
