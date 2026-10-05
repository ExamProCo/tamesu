from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from image_fixtures import FakeImageProvider

from tamesu.cli import command_close, command_lint, command_promote
from tamesu.config import load_eval_context, load_yaml
from tamesu.judging import judge_eval
from tamesu.planner import build_plan
from tamesu.presenting import present_case
from tamesu.pricing import estimate_plan_cost
from tamesu.review import export_pack, import_responses
from tamesu.runner import run_eval

EVAL_ID = "product-reference/baseline/two-model-baseline"
SOURCE = Path(__file__).parents[1] / "examples" / "product-reference-images"


class ExampleFakeProvider(FakeImageProvider):
    def validate_image_parameters(self, model, request):
        return []  # the real adapters' validation is exercised by the lint/plan test below


class ProductReferenceExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        shutil.copytree(
            SOURCE,
            self.project,
            ignore=shutil.ignore_patterns(
                "runs", "logs", "review", "review-pack*", "closing.yml", "leaderboard.md",
                "evaluation-report.md", "analysis.md", "accepted-references-v1", "build", ".env",
            ),
        )
        self.eval_path = (
            self.project / "cases/product-reference/experiments/baseline/evals/two-model-baseline/eval.yml"
        )

    def activate(self) -> None:
        self.eval_path.write_text(self.eval_path.read_text().replace("status: draft", "status: active"))

    def test_example_lints_and_plans_with_the_real_adapters(self) -> None:
        self.assertEqual(command_lint(self.project / "cases/product-reference"), 0)
        plan = build_plan(load_eval_context(self.project, EVAL_ID))
        self.assertEqual((len(plan.specs), plan.total_items), (2, 6))
        cost = estimate_plan_cost(plan)
        self.assertEqual(cost.estimated_usd, 0.03)  # Muse only: 3 * $0.01
        self.assertEqual(cost.unknown_models, ("gpt-image-2",))  # token-billed: no guess

    def test_tutorial_flow_runs_end_to_end_on_a_fake_provider(self) -> None:
        self.activate()
        provider = ExampleFakeProvider()
        with patch("tamesu.runner.get_provider", return_value=provider), patch(
            "tamesu.judging.get_provider", return_value=provider
        ), patch("tamesu.judging.time.sleep"), patch.dict("os.environ", {"META_API_KEY": "x"}):
            context = load_eval_context(self.project, EVAL_ID)
            run_ids = run_eval(context)
            self.assertEqual(len(run_ids), 2)
            self.assertEqual(provider.calls, 6)
            self.assertEqual(judge_eval(context).ok, 6)

            out = self.project / "review-pack"
            export_pack(context, out, reviewer="you")
            document = yaml.safe_load((out / "responses.yml").read_text())
            for response in document["responses"]:
                for outcome in response["dimensions"].values():
                    outcome.update({"pass": True, "reason_code": None})
            (out / "responses.yml").write_text(yaml.safe_dump(document, sort_keys=False))
            self.assertEqual(len(import_responses(context, out / "responses.yml").written), 6)

            self.assertEqual(command_close(self.project, EVAL_ID), 0)
            report = (context.eval_dir / "evaluation-report.md").read_text()
            self.assertIn("| Human | 3 | 3 | 3 |", report)
            closing = load_yaml(context.eval_dir / "closing.yml")
            self.assertFalse(closing["incomplete_evidence"])
            self.assertEqual(
                command_promote(self.project, EVAL_ID, "product-reference/accepted-references-v1", dry_run=True), 0
            )

            # tamesu present: the page must show the images and acceptance, not text-eval columns
            destination = present_case(self.project / "cases/product-reference")
            run_id = run_ids[0]
            copied = sorted((destination / "assets" / "images" / run_id).glob("*.png"))
            self.assertEqual(len(copied), 3)
            page = (destination / "experiments" / "baseline.html").read_text()
            self.assertIn(f"../assets/images/{run_id}/hearthwick-kettle.png", page)
            self.assertIn("Accepted", page)
            self.assertNotIn("Exact matches", page)
            self.assertNotIn("No stored output", page)


if __name__ == "__main__":
    unittest.main()
