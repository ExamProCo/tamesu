"""Blinded human review, acceptance and reporting for artifact_bundle runs."""
from __future__ import annotations

import importlib.util
import json
import re
import textwrap
import unittest
from pathlib import Path

import yaml
from PIL import Image

from test_inspect_backend import EVAL, EVAL_ID, Fixture, Mocked

from tamesu.artifacts import write_yaml
from tamesu.backends.inspect_backend import _passes
from tamesu.config import load_yaml
from tamesu.errors import ConfigError, ExecutionError
from tamesu.evidence import select_generated_items
from tamesu.promote import plan_promotion
from tamesu.review import export_pack, import_responses, review_status
from tamesu.runner import run_eval

INSPECT = importlib.util.find_spec("inspect_ai") is not None

TASK_PY = textwrap.dedent(
    '''
    import io
    from inspect_ai import Task, task
    from inspect_ai.dataset import json_dataset
    from inspect_ai.scorer import Score, accuracy, scorer
    from inspect_ai.solver import generate
    from PIL import Image
    from tamesu.inspect_support import stager


    @task
    def bundle(dataset: str) -> Task:
        return Task(dataset=json_dataset(dataset), solver=[generate()], scorer=check())


    def png() -> bytes:
        image = Image.new("RGB", (80, 60), (200, 30, 30))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


    @scorer(metrics=[accuracy()])
    def check():
        async def score(state, target):
            stage = stager(state.sample_id)
            if state.sample_id == "item-a":
                stage.add("render", "render.png", png(), "image/png")
            elif state.sample_id == "item-b":
                stage.add("render", "render.png", b"not an image", "image/png")
            # item-c stages no render at all
            return Score(value=1.0)
        return score
    '''
)

RUBRIC = {
    "schema_version": 1,
    "name": "looks-fine",
    "rationale": "looks-fine.md",
    "dimensions": [
        {"id": "looks-fine", "question": "The image looks fine.", "reason_codes": ["bad", "other"]},
        {"id": "has-color", "question": "The image has color.", "reason_codes": ["gray", "other"]},
    ],
    "acceptance": {"rule": "all_dimensions_pass"},
}


class ReviewFixture(Fixture):
    def __init__(self, test: unittest.TestCase, **changes) -> None:
        super().__init__(test)
        (self.exp / "inspect" / "task.py").write_text(TASK_PY)
        (self.exp / "rubrics").mkdir()
        write_yaml(self.exp / "rubrics" / "looks-fine.yml", RUBRIC)
        (self.exp / "rubrics" / "looks-fine.md").write_text("Why these dimensions.\n")
        document = json.loads(json.dumps(EVAL))
        document["execution"]["artifacts"] = {"allowed": ["render"], "review": "render"}
        document["evaluation"] = {
            "mechanical": ["check"],
            "human_review": {"rubric": "../../rubrics/looks-fine.yml"},
            "acceptance": {"requires": ["mechanical", "human"], "human": {"required_reviews_per_item": 1}},
        }
        document["metrics"] = {
            "primary": "accepted_rate",
            "secondary": [
                "accepted_rate_given_artifact",
                "render_rate",
                "mechanical_pass_rate",
                "human_pass_rate_given_artifact",
                "review_completion_rate",
            ],
        }
        document.update(changes)
        write_yaml(self.eval_dir / "eval.yml", document)


@unittest.skipUnless(INSPECT, "inspect-ai is not installed")
class ReviewOverBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ReviewFixture(self)
        mocked = Mocked()  # stays active so plans built later fingerprint the same route
        mocked.__enter__()
        self.addCleanup(mocked.__exit__, None, None, None)
        self.context = self.fixture.context()
        (self.run_id,) = run_eval(self.context)
        self.run_dir = self.fixture.eval_dir / "runs" / self.run_id

    def result(self, item_id: str) -> dict:
        return load_yaml(self.run_dir / "items" / item_id / "result.yml")

    def report(self) -> dict:
        return load_yaml(self.run_dir / "report.yml")

    def review(self, verdict: bool, reviewer: str = "andrew") -> None:
        out = self.fixture.root / f"pack-{len(list(self.fixture.root.glob('pack-*')))}"
        export_pack(self.context, out, include_reviewed=True)
        pack = yaml.safe_load((out / "pack.yml").read_text())
        key = next(
            load_yaml(p)
            for p in (self.context.eval_dir / "review" / "keys").glob("*.yml")
            if load_yaml(p)["pack_id"] == pack["pack_id"]
        )
        document = yaml.safe_load((out / "responses.yml").read_text())
        document["reviewer"] = reviewer
        for response in document["responses"]:
            assert key["items"][response["presentation_id"]]["item_id"] == "item-a"
            for index, outcome in enumerate(response["dimensions"].values()):
                if verdict or index:
                    outcome.update({"pass": True, "reason_code": None})
                else:
                    outcome.update({"pass": False, "reason_code": "bad"})
        (out / "responses.yml").write_text(yaml.safe_dump(document, sort_keys=False))
        import_responses(self.context, out / "responses.yml")

    def test_results_carry_the_shape_shared_review_code_reads(self) -> None:
        good = self.result("item-a")
        self.assertEqual(good["outcome"], {"status": "generated", "reason": "ok"})
        self.assertEqual(good["output"]["path"], "artifacts/render.png")
        self.assertEqual((good["output"]["width"], good["output"]["height"]), (80, 60))
        self.assertTrue((self.run_dir / "items" / "item-a" / good["output"]["path"]).is_file())
        self.assertIn("input_sha256", good["prompt"])
        self.assertEqual(self.result("item-b")["outcome"]["reason"], "undecodable_image")
        self.assertEqual(self.result("item-c")["outcome"]["reason"], "missing_review_artifact")
        self.assertNotIn("output", self.result("item-b"))

    def test_scores_record_a_pass_flag(self) -> None:
        score = self.result("item-a")["mechanical_scores"]["check"]
        self.assertEqual((score["value"], score["pass"]), (1.0, True))

    def test_only_decodable_current_images_are_offered_for_review(self) -> None:
        selection = select_generated_items(self.context)
        self.assertEqual([entry.item_id for entry in selection.items], ["item-a"])
        reasons = {item: reason for _, item, reason in selection.skipped}
        self.assertEqual(reasons, {"item-b": "no generated image", "item-c": "no generated image"})

    def test_report_keeps_missing_renders_in_the_denominator_and_waits_for_review(self) -> None:
        metrics = self.report()["metrics"]
        self.assertEqual(metrics["render_rate"], 1 / 3)
        self.assertEqual(metrics["mechanical_pass_rate"], 1 / 3)
        self.assertEqual(metrics["accepted_rate"], 0.0)
        self.assertIsNone(metrics["human_pass_rate_given_artifact"])
        counts = self.report()["evidence"]["counts"]
        self.assertEqual((counts["planned"], counts["generated"], counts["pending"]), (3, 1, 1))
        self.assertEqual(counts["rejected"], 2)  # no render is a rejection, not a skip
        status = review_status(self.context)
        self.assertEqual((status["reviewed"], status["awaiting_review"]), (0, 1))

    def test_a_passing_review_accepts_the_item_and_rebuilds_the_report(self) -> None:
        self.review(True)
        metrics = self.report()["metrics"]
        self.assertEqual(metrics["accepted_rate"], 1 / 3)
        self.assertEqual(metrics["accepted_rate_given_artifact"], 1.0)
        self.assertEqual(metrics["human_pass_rate_given_artifact"], 1.0)
        self.assertEqual(metrics["review_completion_rate"], 1.0)
        item = next(i for i in self.report()["items"] if i["item_id"] == "item-a")
        self.assertEqual(item["evidence"]["acceptance"]["state"], "accepted")

    def test_a_failing_review_rejects_with_the_reason(self) -> None:
        self.review(False)
        item = next(i for i in self.report()["items"] if i["item_id"] == "item-a")
        self.assertEqual(item["evidence"]["acceptance"], {"state": "rejected", "reasons": ["human_failed"], "awaiting": []})
        self.assertEqual(self.report()["metrics"]["accepted_rate"], 0.0)

    def test_reviewers_never_see_the_model_or_arm(self) -> None:
        out = self.fixture.root / "blind"
        export_pack(self.context, out)
        text = "".join(p.read_text() for p in (out / "pack.yml", out / "review.html"))
        for secret in ("muse-spark", self.run_id, "mockllm"):
            self.assertNotIn(secret, text)
        self.assertIsNone(re.search(r"\bplain\b", text), "arm id leaked")

    def test_changing_the_image_after_the_run_makes_it_stale(self) -> None:
        path = self.run_dir / "items" / "item-a" / "artifacts" / "render.png"
        Image.new("RGB", (10, 10), (0, 0, 255)).save(path)
        selection = select_generated_items(self.context)
        self.assertEqual(selection.items, [])
        self.assertIn("stale image", {reason for _, _, reason in selection.skipped})

    def test_artifact_bundle_results_cannot_be_promoted_as_a_product_dataset(self) -> None:
        with self.assertRaises(ExecutionError) as caught:
            plan_promotion(self.context, "somewhere")
        self.assertIn("no accepted-image output", str(caught.exception))


class LintTests(unittest.TestCase):
    def lint_error(self, **changes) -> str:
        fixture = ReviewFixture(self)
        self.addCleanup(lambda: None)
        document = yaml.safe_load((fixture.eval_dir / "eval.yml").read_text())
        for key, value in changes.items():
            if key == "artifacts":
                document["execution"]["artifacts"] = value
            elif key == "evaluation":
                document["evaluation"].update(value)
            else:
                document[key] = value
        write_yaml(fixture.eval_dir / "eval.yml", document)
        with self.assertRaises(ConfigError) as caught:
            fixture.context()
        return str(caught.exception)

    def test_review_needs_a_declared_review_role(self) -> None:
        self.assertIn("need eval.execution.artifacts.review", self.lint_error(artifacts={"allowed": ["render"]}))

    def test_review_role_must_be_declared(self) -> None:
        message = self.lint_error(artifacts={"allowed": ["other"], "review": "render"})
        self.assertIn("review must be one of the required or allowed roles", message)

    def test_model_judges_remain_unsupported(self) -> None:
        message = self.lint_error(evaluation={"model_judges": [{"id": "j"}]})
        self.assertIn("model_judges are not supported", message)

    def test_pass_at_must_be_a_positive_number(self) -> None:
        message = self.lint_error(evaluation={"mechanical": [{"check": {"pass_at": 0}}]})
        self.assertIn("pass_at must be a positive number", message)
        message = self.lint_error(evaluation={"mechanical": [{"check": {"other": 1}}]})
        self.assertIn("is not a recognized parameter", message)

    def test_valid_review_fixture_lints(self) -> None:
        ReviewFixture(self).context()


class PassThresholdTests(unittest.TestCase):
    def test_threshold_semantics(self) -> None:
        self.assertTrue(_passes(1.0, 1.0))
        self.assertFalse(_passes(0.99, 1.0))
        self.assertTrue(_passes(0.8, 0.8))
        self.assertTrue(_passes(True, 1.0))
        self.assertFalse(_passes(False, 0.1))
        self.assertTrue(_passes("C", 1.0))
        self.assertFalse(_passes("I", 1.0))


if __name__ == "__main__":
    unittest.main()
