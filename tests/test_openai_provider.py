from __future__ import annotations

import json
import os
import unittest
from email.message import Message
from unittest.mock import patch

from tamesu.providers.openai import OpenAIProvider


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode()
        self.headers = Message()
        self.headers["x-request-id"] = "request-header-id"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class OpenAIProviderTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("urllib.request.urlopen")
    def test_structured_response_request(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "resp_123",
                "status": "completed",
                "model": "gpt-5.4-mini",
                "output_text": '{"label":"a"}',
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
        provider = OpenAIProvider()
        response = provider.generate_text(
            model="gpt-5.4-mini",
            system_prompt="Return JSON.",
            user_prompt="Classify this.",
            output_schema={
                "type": "object",
                "required": ["label"],
                "properties": {"label": {"type": "string"}},
            },
            parameters={},
            timeout_seconds=30,
        )

        self.assertEqual(response.text, '{"label":"a"}')
        self.assertEqual(response.request_id, "resp_123")
        request = urlopen.call_args.args[0]  # type: ignore[attr-defined]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["instructions"], "Return JSON.")
        self.assertEqual(payload["input"], "Classify this.")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(response.cost_usd, 0.000017)
        self.assertNotIn("test-key", request.data.decode())
