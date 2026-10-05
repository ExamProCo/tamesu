from __future__ import annotations

import io
import struct
import unittest
import zlib
from pathlib import Path

from image_fixtures import EVAL_ID, FAKE_MODEL, ImageProject, png_bytes

from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ConfigError
from tamesu.identity import digest_file
from tamesu.planner import build_plan
from tamesu.pricing import estimate_plan_cost
from tamesu.runner import resume_run, run_eval
from tamesu.scoring import rescore_run  # noqa: F401  (structured path still importable)
from tamesu.run_report import rescore_run as rescore_any
from tamesu.tasks.image_checks import IntegrityFailure, inspect_image


def _png_with_header(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")


class ImageIntegrityTests(unittest.TestCase):
    def reason(self, data: bytes) -> str:
        with self.assertRaises(IntegrityFailure) as caught:
            inspect_image(data)
        return caught.exception.reason

    def test_valid_formats_are_detected_from_bytes(self) -> None:
        from PIL import Image

        for fmt, media in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            buffer = io.BytesIO()
            Image.new("RGB", (8, 6), (1, 2, 3)).save(buffer, format=fmt)
            decoded = inspect_image(buffer.getvalue())
            self.assertEqual((decoded.media_type, decoded.width, decoded.height), (media, 8, 6))

    def test_failures_have_stable_reason_codes(self) -> None:
        self.assertEqual(self.reason(b""), "empty_bytes")
        self.assertEqual(self.reason(b"not an image at all"), "undecodable")
        self.assertEqual(self.reason(png_bytes()[:40]), "undecodable")
        self.assertEqual(self.reason(_png_with_header(9000, 9000)), "size_limit_exceeded")
        self.assertEqual(self.reason(_png_with_header(20000, 20000)), "decompression_bomb")

    def test_disallowed_raster_format(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, format="GIF")
        self.assertEqual(self.reason(buffer.getvalue()), "disallowed_format")

    def test_zero_dimension_is_rejected(self) -> None:
        self.assertIn(self.reason(_png_with_header(0, 5)), {"degenerate_dimensions", "undecodable"})


class ImageEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject(mechanical=["decodable_image"])
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)

    def context(self):
        return load_eval_context(self.project.root, EVAL_ID)

    def run_once(self):
        context = self.context()
        (run_id,) = run_eval(context)
        run_dir = context.eval_dir / "runs" / run_id
        return context, run_id, run_dir

    def test_plan_prices_flat_images_exactly(self) -> None:
        plan = build_plan(self.context())
        self.assertEqual(plan.total_items, 2)
        cost = estimate_plan_cost(plan)
        self.assertEqual(cost.estimated_usd, 0.02)
        self.assertEqual(cost.maximum_usd, 0.04)  # items * (retries + 1) * price
        self.assertTrue(cost.fully_priced)

    def test_end_to_end_run_stores_image_evidence(self) -> None:
        context, run_id, run_dir = self.run_once()
        manifest = load_yaml(run_dir / "run.yml")
        self.assertEqual(manifest["state"], "complete")
        result = load_yaml(run_dir / "items/aurora-lamp/result.yml")
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["outcome"], {"status": "generated", "reason": "ok"})
        image = run_dir / "items/aurora-lamp" / result["output"]["path"]
        self.assertEqual(result["output"]["sha256"], digest_file(image))
        self.assertEqual((result["output"]["width"], result["output"]["height"]), (64, 48))
        (artifact,) = result["artifacts"]
        self.assertEqual(artifact["role"], "task_output")
        provider_response = load_yaml(run_dir / "items/aurora-lamp/provider_response.yml")
        self.assertTrue(provider_response["revised_prompt"].startswith("revised:"))
        self.assertNotIn("b64", str(provider_response))
        report = load_yaml(run_dir / "report.yml")
        self.assertEqual(report["metrics"]["generation_success_rate"], 1.0)
        self.assertEqual(report["metrics"]["mechanical_pass_rate"], 1.0)
        self.assertAlmostEqual(report["totals"]["cost_usd"], 0.02)

    def test_request_carries_common_and_provider_parameters(self) -> None:
        self.run_once()
        request = self.project.provider.requests[0]
        self.assertEqual((request.size, request.output_format, request.count), ("64x48", "png", 1))
        self.assertEqual(request.options, {"reasoning_strength": "low"})
        self.assertIn("a fictional desk lamp", request.prompt)

    def test_corrupt_image_is_terminal_and_still_banks_the_run(self) -> None:
        self.project.set_brief("birch-kettle", "CORRUPT kettle")
        _, _, run_dir = self.run_once()
        manifest = load_yaml(run_dir / "run.yml")
        self.assertEqual(manifest["state"], "complete")
        result = load_yaml(run_dir / "items/birch-kettle/result.yml")
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["outcome"], {"status": "invalid_artifact", "reason": "undecodable"})
        self.assertNotIn("output", result)
        self.assertEqual([a["role"] for a in result["artifacts"]], ["raw_provider_output"])
        report = load_yaml(run_dir / "report.yml")
        self.assertEqual(report["metrics"]["generation_success_rate"], 0.5)
        self.assertEqual(report["metrics"]["invalid_artifact_rate"], 0.5)
        self.assertEqual(report["metrics"]["mechanical_pass_rate"], 0.5)
        self.assertEqual(report["metrics"]["mechanical_pass_rate_given_image"], 1.0)
        self.assertEqual(report["completion"]["outcome_reasons"], {"invalid_artifact:undecodable": 1})
        self.assertEqual(len(build_plan(self.context()).banked_run_ids), 1)

    def test_local_decode_failure_never_triggers_a_paid_retry(self) -> None:
        self.project.set_brief("aurora-lamp", "CORRUPT lamp")
        self.project.set_brief("birch-kettle", "kettle")
        self.run_once()
        self.assertEqual(self.project.provider.calls, 2)  # retries=1 but nothing was retried

    def test_safety_refusal_is_a_measured_outcome(self) -> None:
        self.project.set_brief("aurora-lamp", "REFUSE lamp")
        _, _, run_dir = self.run_once()
        self.assertEqual(load_yaml(run_dir / "run.yml")["state"], "complete")
        result = load_yaml(run_dir / "items/aurora-lamp/result.yml")
        self.assertEqual(result["outcome"], {"status": "safety_filtered", "reason": "safety_filter"})
        self.assertEqual(result["generation"]["cost_usd"], 0.0)
        report = load_yaml(run_dir / "report.yml")
        self.assertEqual(report["metrics"]["safety_filter_rate"], 0.5)
        self.assertEqual(report["metrics"]["generation_success_rate"], 0.5)

    def test_multiple_images_are_rejected_as_invalid(self) -> None:
        self.project.set_brief("aurora-lamp", "TWO lamps")
        _, _, run_dir = self.run_once()
        result = load_yaml(run_dir / "items/aurora-lamp/result.yml")
        self.assertEqual(result["outcome"]["reason"], "unexpected_image_count")

    def test_provider_media_type_claim_is_recorded_but_bytes_win(self) -> None:
        self.project.set_brief("aurora-lamp", "LYING lamp")
        _, _, run_dir = self.run_once()
        result = load_yaml(run_dir / "items/aurora-lamp/result.yml")
        self.assertEqual(result["output"]["media_type"], "image/png")
        provider_response = load_yaml(run_dir / "items/aurora-lamp/provider_response.yml")
        self.assertEqual(provider_response["provider_media_type"], "image/jpeg")
        self.assertTrue(provider_response["media_type_mismatch"])

    def test_transient_failure_leaves_run_partial_and_resume_skips_finished_items(self) -> None:
        provider = self.project.provider
        provider.transient_failures = 2  # first item exhausts retries=1
        context = self.context()
        (run_id,) = run_eval(context)
        run_dir = context.eval_dir / "runs" / run_id
        self.assertEqual(load_yaml(run_dir / "run.yml")["state"], "partial")
        self.assertEqual(load_yaml(run_dir / "items/aurora-lamp/result.yml")["state"], "failed")
        calls_before = provider.calls
        resume_run(context, run_id)
        self.assertEqual(provider.calls - calls_before, 1)  # only the failed item is re-called
        self.assertEqual(load_yaml(run_dir / "run.yml")["state"], "complete")

    def test_tampered_image_is_reported_stale_not_trusted(self) -> None:
        context, _, run_dir = self.run_once()
        result = load_yaml(run_dir / "items/aurora-lamp/result.yml")
        image = run_dir / "items/aurora-lamp" / result["output"]["path"]
        image.write_bytes(png_bytes(color=(1, 1, 1)))
        report = rescore_any(context, run_dir)
        self.assertEqual(report["completion"]["stale_items"], ["aurora-lamp"])
        self.assertEqual(report["metrics"]["mechanical_pass_rate"], 0.5)

    def test_image_code_is_part_of_the_content_fingerprint(self) -> None:
        spec = build_plan(self.context()).specs[0]
        labels = set(spec.content_inventory)
        self.assertTrue(any(label == "image_checks.py" for label in labels))
        self.assertTrue(any(label == "image_generation.py" for label in labels))
        self.assertFalse(any(label == "structured_text_task.py" for label in labels))


class ImageScorerTests(unittest.TestCase):
    def run_with(self, mechanical: list[str], brief: str = "lamp", size: str | None = None):
        project = ImageProject(products={"aurora-lamp": brief}, mechanical=mechanical)
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        context = load_eval_context(project.root, EVAL_ID)
        (run_id,) = run_eval(context)
        run_dir = context.eval_dir / "runs" / run_id
        return load_yaml(run_dir / "items/aurora-lamp/result.yml")["mechanical_scores"]

    def test_min_resolution_is_eval_declared(self) -> None:
        scores = self.run_with(["min_resolution: {width: 128, height: 128}"])
        self.assertFalse(scores["min_resolution"]["pass"])
        scores = self.run_with(["min_resolution: {width: 32, height: 32, prerequisite: true}"])
        self.assertTrue(scores["min_resolution"]["pass"])
        self.assertTrue(scores["min_resolution"]["prerequisite"])

    def test_uniform_image_fails_only_when_the_eval_asks(self) -> None:
        self.assertNotIn("non_uniform", self.run_with([], brief="SOLID lamp"))
        self.assertFalse(self.run_with(["non_uniform"], brief="SOLID lamp")["non_uniform"]["pass"])
        self.assertTrue(self.run_with(["non_uniform"])["non_uniform"]["pass"])

    def test_format_match_and_transparency(self) -> None:
        self.assertTrue(self.run_with(["format_match"])["format_match"]["pass"])
        self.assertFalse(self.run_with(["has_transparency"])["has_transparency"]["pass"])


class ImageLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject()
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)

    def lint_error(self) -> str:
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(self.project.root, EVAL_ID)
        return str(caught.exception)

    def test_text_model_cannot_run_image_task(self) -> None:
        self.project.write_eval(model="muse-spark-1.2")
        self.assertIn("lacks capabilities required by 'image_generation': image_output", self.lint_error())

    def test_unregistered_model_is_rejected_for_images(self) -> None:
        self.project.write_eval(model="mystery-image")
        self.assertIn("is not registered", self.lint_error())

    def test_unknown_scorer_and_bad_parameters(self) -> None:
        self.project.write_eval(["bogus_scorer"])
        self.assertIn("Unknown mechanical scorer: 'bogus_scorer'", self.lint_error())
        self.project.write_eval(["min_resolution: {width: 0}"])
        self.assertIn("width must be a positive integer", self.lint_error())

    def test_provider_only_options_are_rejected_before_any_call(self) -> None:
        text = self.project.eval_path.read_text().replace("reasoning_strength: low", "quality: high")
        self.project.eval_path.write_text(text)
        self.assertIn("does not accept provider option 'quality'", self.lint_error())
        self.assertEqual(self.project.provider.calls, 0)

    def test_text_parameters_are_not_valid_for_images(self) -> None:
        text = self.project.eval_path.read_text().replace("    image:\n", "    max_tokens: 100\n    image:\n")
        self.project.eval_path.write_text(text)
        self.assertIn("parameters.max_tokens is not valid for image_generation", self.lint_error())


if __name__ == "__main__":
    unittest.main()
