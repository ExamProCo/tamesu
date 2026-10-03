from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tamesu.cli import command_close
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ProviderError
from tamesu.models import ProviderResponse
from tamesu.planner import build_plan
from tamesu.reporting import build_leaderboard, comparison_rows, status_summary
from tamesu.runner import resume_run, run_eval
from tamesu.scoring import rescore_run


EVAL_ID = "support-ticket-triage/decision-rules/prompt-ablation"


class FixtureProvider:
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0
        self.failures_remaining = 0

    def credential_error(self) -> None:
        return None

    def generate_text(self, **kwargs: object) -> ProviderResponse:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ProviderError("temporary fixture failure", retryable=True)
        answer = classify(str(kwargs["user_prompt"]))
        return ProviderResponse(
            text=json.dumps(answer),
            request_id=f"fixture-{self.calls}",
            usage={"input_tokens": 20, "output_tokens": 12, "total_tokens": 32},
            cost_usd=0.001,
        )


def classify(prompt: str) -> dict[str, object]:
    prompt = prompt.lower()
    if "charged twice" in prompt:
        return {"category": "billing", "priority": "high", "requires_human": True}
    if "reset email" in prompt:
        return {
            "category": "account-access",
            "priority": "normal",
            "requires_human": False,
        }
    if "account has been stolen" in prompt:
        return {
            "category": "account-access",
            "priority": "urgent",
            "requires_human": True,
        }
    if "csv export" in prompt:
        return {"category": "technical", "priority": "normal", "requires_human": False}
    if "cancel my subscription" in prompt:
        return {
            "category": "cancellation",
            "priority": "normal",
            "requires_human": True,
        }
    if "application crashes" in prompt:
        return {"category": "technical", "priority": "high", "requires_human": False}
    if "annual discount" in prompt:
        return {"category": "billing", "priority": "low", "requires_human": False}
    return {"category": "other", "priority": "normal", "requires_human": True}


class ExampleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        source = Path(__file__).parents[1] / "examples" / "support-ticket-classification"
        self.project = Path(self.temporary.name) / "project"
        shutil.copytree(source, self.project)
        eval_path = (
            self.project
            / "cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml"
        )
        eval_path.write_text(
            eval_path.read_text(encoding="utf-8").replace("status: draft", "status: active"),
            encoding="utf-8",
        )
        self.provider = FixtureProvider()
        self.provider_patch = patch("tamesu.runner.get_provider", return_value=self.provider)
        self.provider_patch.start()

    def tearDown(self) -> None:
        self.provider_patch.stop()
        self.temporary.cleanup()

    def test_example_plan(self) -> None:
        context = load_eval_context(self.project, EVAL_ID)
        plan = build_plan(context)
        self.assertEqual(len(plan.specs), 4)
        self.assertEqual(len(plan.owed_specs), 4)
        self.assertEqual(plan.total_items, 32)

    def test_plan_has_known_cost_exposure(self) -> None:
        from tamesu.pricing import estimate_plan_cost

        plan = build_plan(load_eval_context(self.project, EVAL_ID))
        estimate = estimate_plan_cost(plan)
        self.assertTrue(estimate.fully_priced)
        self.assertGreater(estimate.estimated_usd, 0)
        self.assertGreater(estimate.maximum_usd, estimate.estimated_usd)

    def test_budget_ceiling_blocks_execution_before_calls(self) -> None:
        eval_path = (
            self.project
            / "cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml"
        )
        eval_path.write_text(
            eval_path.read_text(encoding="utf-8").replace(
                "budget_usd: 2.00", "budget_usd: 0.00"
            ),
            encoding="utf-8",
        )
        context = load_eval_context(self.project, EVAL_ID)
        with self.assertRaisesRegex(Exception, "exceeds the.*budget ceiling"):
            run_eval(context, limit_items=1)
        self.assertEqual(self.provider.calls, 0)

    def test_full_example_workflow(self) -> None:
        context = load_eval_context(self.project, EVAL_ID)

        probe_ids = run_eval(context, limit_items=2)
        self.assertEqual(len(probe_ids), 4)
        probe_plan = build_plan(context)
        self.assertEqual(status_summary(probe_plan)["counts"]["partial"], 4)
        self.assertFalse(probe_plan.banked_run_ids)

        run_ids = run_eval(context)
        self.assertEqual(len(run_ids), 4)
        completed_plan = build_plan(context)
        self.assertEqual(len(completed_plan.banked_run_ids), 4)
        self.assertFalse(completed_plan.owed_specs)

        rows = comparison_rows(completed_plan)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["repetitions"] == 2 for row in rows))
        self.assertTrue(all(row["primary_mean"] == 1.0 for row in rows))

        leaderboard_path = build_leaderboard(completed_plan)
        self.assertTrue(leaderboard_path.is_file())
        self.assertIn("basic-prompt", leaderboard_path.read_text(encoding="utf-8"))

        first_run_dir = context.eval_dir / "runs" / run_ids[0]
        report = rescore_run(context, first_run_dir)
        self.assertEqual(report["metrics"]["exact_match_rate"], 1.0)

        self.assertEqual(command_close(self.project, EVAL_ID), 0)
        closed = load_eval_context(self.project, EVAL_ID)
        self.assertEqual(closed.evaluation["status"], "complete")
        self.assertEqual(self.provider.calls, 40)

    def test_resume_retries_a_transient_item(self) -> None:
        eval_path = (
            self.project
            / "cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml"
        )
        eval_path.write_text(
            eval_path.read_text(encoding="utf-8").replace("retries: 1", "retries: 0"),
            encoding="utf-8",
        )
        context = load_eval_context(self.project, EVAL_ID)
        self.provider.failures_remaining = 1
        run_ids = run_eval(context, only="gpt-5.4-mini", limit_items=1)

        first_run_dir = context.eval_dir / "runs" / run_ids[0]
        first_manifest = load_yaml(first_run_dir / "run.yml")
        self.assertEqual(first_manifest["state"], "partial")

        resumed = resume_run(context, run_ids[0])
        self.assertEqual(resumed, run_ids[0])
        result_paths = list((first_run_dir / "items").glob("*/result.yml"))
        self.assertEqual(len(result_paths), 1)
        self.assertEqual(load_yaml(result_paths[0])["attempts"], 2)
