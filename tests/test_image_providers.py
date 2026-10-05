from __future__ import annotations

import base64
import io
import json
import os
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from image_fixtures import EVAL_ID, ImageProject, png_bytes
from test_providers import FakeResponse, request_payload

from tamesu.config import load_eval_context
from tamesu.errors import ConfigError, ProviderError
from tamesu.models import ImageGenerationRequest
from tamesu.planner import build_plan
from tamesu.pricing import estimate_plan_cost
from tamesu.providers import ImageGenerationProvider
from tamesu.providers.meta import MetaProvider
from tamesu.providers.models import MODELS, image_cost
from tamesu.providers.openai import GrokProvider, OpenAIProvider

PNG = png_bytes((32, 24), seed="fixture")
B64 = base64.b64encode(PNG).decode()
REQUEST = ImageGenerationRequest(prompt="a fictional lamp", size="1024x1024", output_format="png")

META_FIXTURE = {
    "created": 1784584435,
    "data": [{"b64_json": B64}],
    "output_format": "png",
    "background": "opaque",
    "usage": {"input_tokens": 9831, "output_tokens": 749, "total_tokens": 10580},
}
OPENAI_FIXTURE = {
    "created": 1784584500,
    "data": [{"b64_json": B64, "revised_prompt": "a fictional desk lamp, studio lighting"}],
    "output_format": "png",
    "quality": "low",
    "size": "1024x1024",
    "usage": {
        "input_tokens": 40,
        "input_tokens_details": {"text_tokens": 40, "image_tokens": 0},
        "output_tokens": 200,
    },
}


def http_error(status: int, body: dict, request_id: str = "req-err") -> urllib.error.HTTPError:
    headers = Message()
    headers["x-request-id"] = request_id
    return urllib.error.HTTPError("https://x", status, "err", headers, io.BytesIO(json.dumps(body).encode()))


class AdapterContractTests(unittest.TestCase):
    """The same logical request must give the same normalized shape from every adapter."""

    @patch.dict(os.environ, {"META_API_KEY": "secret", "OPENAI_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_meta_and_openai_return_the_same_normalized_shape(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(META_FIXTURE, x_request_id="meta-req")  # type: ignore[attr-defined]
        meta = MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        urlopen.return_value = FakeResponse(OPENAI_FIXTURE, x_request_id="oai-req")  # type: ignore[attr-defined]
        openai = OpenAIProvider().generate_image(model="gpt-image-2", request=REQUEST, timeout_seconds=30)
        for response in (meta, openai):
            self.assertEqual(len(response.images), 1)
            self.assertEqual(response.images[0].data, PNG)
            self.assertEqual(response.images[0].provider_media_type, "image/png")
            self.assertIsNone(response.refusal_reason)
            self.assertIsInstance(response.cost_usd, float)
            self.assertEqual(set(response.usage) & {"input_tokens", "output_tokens"}, {"input_tokens", "output_tokens"})
        self.assertEqual((meta.request_id, openai.request_id), ("meta-req", "oai-req"))
        self.assertIsNone(meta.images[0].revised_prompt)
        self.assertEqual(openai.images[0].revised_prompt, "a fictional desk lamp, studio lighting")
        self.assertEqual(meta.response_metadata["requested_model"], "muse-image-1.0")

    def test_both_adapters_implement_the_capability_but_other_providers_do_not(self) -> None:
        self.assertIsInstance(MetaProvider(), ImageGenerationProvider)
        self.assertIsInstance(OpenAIProvider(), ImageGenerationProvider)
        self.assertNotIsInstance(GrokProvider(), ImageGenerationProvider)

    def test_provider_only_options_are_rejected_by_the_wrong_adapter(self) -> None:
        meta_only = ImageGenerationRequest("p", options={"reasoning_strength": "low"})
        openai_only = ImageGenerationRequest("p", options={"quality": "low"})
        self.assertTrue(OpenAIProvider().validate_image_parameters("gpt-image-2", meta_only))
        self.assertTrue(MetaProvider().validate_image_parameters("muse-image-1.0", openai_only))
        self.assertEqual(MetaProvider().validate_image_parameters("muse-image-1.0", meta_only), [])
        self.assertEqual(OpenAIProvider().validate_image_parameters("gpt-image-2", openai_only), [])

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_wrong_adapter_options_never_reach_the_network(self, urlopen: object) -> None:
        with self.assertRaises(ProviderError):
            MetaProvider().generate_image(
                model="muse-image-1.0",
                request=ImageGenerationRequest("p", options={"quality": "high"}),
                timeout_seconds=30,
            )
        urlopen.assert_not_called()  # type: ignore[attr-defined]


class MetaImageTests(unittest.TestCase):
    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_request_shape_and_flat_price(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(META_FIXTURE)  # type: ignore[attr-defined]
        request = ImageGenerationRequest(
            "a fictional lamp",
            size="1024x1024",
            output_format="png",
            options={
                "reasoning_strength": "low",
                "tool_enablement": {"enable_image_search": False, "enable_web_search": False, "enable_shell": False},
            },
        )
        response = MetaProvider().generate_image(model="muse-image-1.0", request=request, timeout_seconds=30)
        http, payload = request_payload(urlopen)
        self.assertTrue(http.full_url.endswith("/images/generations"))
        self.assertEqual(http.get_header("Authorization"), "Bearer secret")
        self.assertEqual(
            payload,
            {
                "model": "muse-image-1.0",
                "prompt": "a fictional lamp",
                "n": 1,
                "response_format": "b64_json",
                "size": "1024x1024",
                "output_format": "png",
                "reasoning_strength": "low",
                "tool_enablement": {"enable_image_search": False, "enable_web_search": False, "enable_shell": False},
            },
        )
        self.assertEqual(response.cost_usd, 0.01)
        self.assertEqual(response.usage, {"input_tokens": 9831, "output_tokens": 749})

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_empty_data_is_a_confirmed_free_safety_filter(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse({"created": 1, "data": [], "usage": {}})  # type: ignore[attr-defined]
        response = MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        self.assertEqual((response.images, response.refusal_reason, response.cost_usd), ((), "safety_filter", 0.0))

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_safety_error_400_is_a_refusal_not_a_failure(self, urlopen: object) -> None:
        urlopen.side_effect = http_error(  # type: ignore[attr-defined]
            400, {"error": {"code": "safety_filtered", "message": "blocked"}}
        )
        response = MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        self.assertEqual(response.refusal_reason, "safety_filtered")
        self.assertEqual(response.cost_usd, 0.0)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_plain_bad_request_stays_a_non_retryable_error(self, urlopen: object) -> None:
        urlopen.side_effect = http_error(400, {"error": {"message": "n must be 1-10"}})  # type: ignore[attr-defined]
        with self.assertRaises(ProviderError) as raised:
            MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        self.assertFalse(raised.exception.retryable)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_server_error_is_retryable_with_unknown_cost(self, urlopen: object) -> None:
        urlopen.side_effect = http_error(503, {"error": {"message": "overloaded"}})  # type: ignore[attr-defined]
        with self.assertRaises(ProviderError) as raised:
            MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        self.assertTrue(raised.exception.retryable)
        self.assertIsNone(raised.exception.cost_usd)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_invalid_base64_is_rejected(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse({"data": [{"b64_json": "!!!not base64!!!"}]})  # type: ignore[attr-defined]
        with self.assertRaises(ProviderError):
            MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)

    @patch.dict(os.environ, {"META_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_url_only_responses_are_not_followed(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse({"data": [{"url": "https://cdn/x.png"}]})  # type: ignore[attr-defined]
        with self.assertRaises(ProviderError):
            MetaProvider().generate_image(model="muse-image-1.0", request=REQUEST, timeout_seconds=30)
        self.assertEqual(urlopen.call_count, 1)  # type: ignore[attr-defined]


class OpenAIImageTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_request_shape_and_token_cost_from_usage(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse(OPENAI_FIXTURE)  # type: ignore[attr-defined]
        request = ImageGenerationRequest(
            "a fictional lamp", size="1024x1024", output_format="png", options={"quality": "low", "background": "opaque"}
        )
        response = OpenAIProvider().generate_image(model="gpt-image-2", request=request, timeout_seconds=30)
        http, payload = request_payload(urlopen)
        self.assertTrue(http.full_url.endswith("/images/generations"))
        self.assertEqual(
            payload,
            {
                "model": "gpt-image-2",
                "prompt": "a fictional lamp",
                "n": 1,
                "size": "1024x1024",
                "output_format": "png",
                "quality": "low",
                "background": "opaque",
            },
        )
        expected = (40 * 5.0 + 200 * 30.0) / 1_000_000
        self.assertAlmostEqual(response.cost_usd or 0, expected, places=6)
        self.assertEqual(response.response_metadata["quality"], "low")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_returned_model_id_is_recorded_beside_the_requested_one(self, urlopen: object) -> None:
        urlopen.return_value = FakeResponse({**OPENAI_FIXTURE, "model": "gpt-image-2-2026-09-01"})  # type: ignore[attr-defined]
        response = OpenAIProvider().generate_image(model="gpt-image-2", request=REQUEST, timeout_seconds=30)
        self.assertEqual(response.response_metadata["model"], "gpt-image-2-2026-09-01")
        self.assertEqual(response.response_metadata["requested_model"], "gpt-image-2")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_moderation_block_is_a_refusal_with_unknown_cost(self, urlopen: object) -> None:
        urlopen.side_effect = http_error(  # type: ignore[attr-defined]
            400,
            {"error": {"type": "image_generation_user_error", "code": "moderation_blocked",
                       "moderation_details": {"moderation_stage": "output", "categories": ["violence"]}}},
        )
        response = OpenAIProvider().generate_image(model="gpt-image-2", request=REQUEST, timeout_seconds=30)
        self.assertEqual((response.images, response.refusal_reason), ((), "moderation_blocked"))
        self.assertIsNone(response.cost_usd)  # not confirmed free, so never recorded as zero

    def test_option_combinations_are_validated_before_any_call(self) -> None:
        validate = OpenAIProvider().validate_image_parameters
        self.assertTrue(validate("gpt-image-2", ImageGenerationRequest("p", output_format="png", options={"output_compression": 50})))
        self.assertTrue(validate("gpt-image-2", ImageGenerationRequest("p", output_format="jpeg", options={"background": "transparent"})))
        self.assertTrue(validate("gpt-image-2", ImageGenerationRequest("p", options={"quality": "ultra"})))
        self.assertEqual(validate("gpt-image-2", ImageGenerationRequest("p", output_format="webp", options={"output_compression": 50})), [])


class PricingTests(unittest.TestCase):
    def test_flat_token_and_matrix_strategies(self) -> None:
        self.assertEqual(image_cost("muse-image-1.0", {}, {}, 1), 0.01)
        self.assertEqual(image_cost("muse-image-1.0", {}, {}, 0), 0.0)
        tokens = {"text_input_tokens": 1_000_000, "image_input_tokens": 0, "output_tokens": 1_000_000}
        self.assertEqual(image_cost("gpt-image-2", {}, tokens, 1), 35.0)
        self.assertIsNone(image_cost("gpt-image-2", {}, {}, 1))  # no usage: unknown, not zero
        self.assertIsNone(image_cost("not-a-model", {}, {}, 1))

    def test_matrix_pricing_is_unknown_for_unlisted_combinations(self) -> None:
        from tamesu.providers.models import ImageMatrixPricing

        pricing = ImageMatrixPricing({("low", "1024x1024"): 0.011})
        params = {"image": {"size": "1024x1024"}, "provider_options": {"quality": "low"}}
        self.assertEqual(pricing.cost(params, {}, 2), 0.022)
        params["image"]["size"] = "2048x2048"
        self.assertIsNone(pricing.cost(params, {}, 2))

    def test_openai_image_models_are_registered_with_the_image_capability(self) -> None:
        for model in ("gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "muse-image-1.0"):
            self.assertIn("image_output", MODELS[model].capabilities)
            self.assertNotIn("text_output", MODELS[model].capabilities)


class RealModelPlanningTests(unittest.TestCase):
    """The same eval plans for either real model by changing only the runs entry and options."""

    def setUp(self) -> None:
        self.project = ImageProject(products={f"p{i}": f"product {i}" for i in range(50)})
        self.addCleanup(self.project._temporary.cleanup)

    def plan(self, model: str, provider: str, options: str):
        self.project.write_eval(model=model, provider=provider, options=options)
        return build_plan(load_eval_context(self.project.root, EVAL_ID))

    def test_muse_plan_is_exact_for_fifty_products(self) -> None:
        options = (
            "      reasoning_strength: low\n"
            "      tool_enablement: {enable_image_search: false, enable_web_search: false, enable_shell: false}"
        )
        cost = estimate_plan_cost(self.plan("muse-image-1.0", "meta", options))
        self.assertEqual(cost.estimated_usd, 0.5)  # 50 * $0.01
        self.assertEqual(cost.maximum_usd, 1.0)  # 50 * (1 retry + 1) * $0.01
        self.assertTrue(cost.fully_priced)

    def test_openai_estimate_is_reported_unknown_never_muse_priced(self) -> None:
        cost = estimate_plan_cost(self.plan("gpt-image-2", "openai", "      quality: low"))
        self.assertFalse(cost.fully_priced)
        self.assertEqual(cost.unknown_models, ("gpt-image-2",))
        self.assertEqual(cost.estimated_usd, 0.0)

    def test_cross_provider_options_fail_lint(self) -> None:
        self.project.write_eval(model="gpt-image-2", provider="openai", options="      reasoning_strength: low")
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(self.project.root, EVAL_ID)
        self.assertIn("openai does not accept provider option 'reasoning_strength'", str(caught.exception))

    def test_text_model_for_a_provider_with_no_image_adapter_is_rejected(self) -> None:
        self.project.write_eval(model="grok-4.6", provider="grok", options="      quality: low")
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(self.project.root, EVAL_ID)
        self.assertIn("lacks capabilities", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
