from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

from test_providers import SCHEMA, FakeResponse, request_payload

from tamesu.errors import ProviderError
from tamesu.models import ImagePart, TextPart
from tamesu.providers import StructuredMultimodalProvider
from tamesu.providers.meta import MetaProvider


class MetaMultimodalTests(unittest.TestCase):
    def test_meta_implements_the_multimodal_capability(self) -> None:
        self.assertIsInstance(MetaProvider(), StructuredMultimodalProvider)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_image_is_sent_as_a_base64_data_url_part(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "id": "chat_vision_1",
                "model": "muse-spark-1.3",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"label":"a"}'}}],
                "usage": {"prompt_tokens": 1500, "completion_tokens": 40},
            }
        )
        png = b"\x89PNG\r\n\x1a\nfake"
        response = MetaProvider().generate_structured(
            model="muse-spark-1.3",
            system_prompt="system",
            parts=(TextPart("judge this"), ImagePart(png, "image/png")),
            output_schema=SCHEMA,
            parameters={"effort": "low", "max_tokens": 500},
            timeout_seconds=30,
        )
        request, payload = request_payload(urlopen)
        self.assertTrue(request.full_url.endswith("/chat/completions"))
        system, user = payload["messages"]
        self.assertEqual(system, {"role": "system", "content": "system"})
        self.assertEqual(user["content"][0], {"type": "text", "text": "judge this"})
        self.assertEqual(
            user["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
            },
        )
        self.assertEqual(payload["max_completion_tokens"], 500)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(response.text, '{"label":"a"}')
        self.assertEqual(response.request_id, "chat_vision_1")
        self.assertGreater(response.cost_usd or 0, 0)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_model_without_image_input_is_rejected_before_any_call(self, urlopen: object) -> None:
        with self.assertRaises(ProviderError) as raised:
            MetaProvider().generate_structured(
                model="muse-spark-1.2-contributor",
                system_prompt="s",
                parts=(ImagePart(b"x", "image/png"),),
                output_schema=SCHEMA,
                parameters={},
                timeout_seconds=30,
            )
        self.assertIn("does not accept image input", str(raised.exception))
        urlopen.assert_not_called()  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
