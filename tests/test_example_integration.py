from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tamesu.cli import command_close, command_compare, command_run
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ExecutionError, ProviderError
from tamesu.models import ProviderResponse
from tamesu.planner import build_plan
from tamesu.reporting import build_leaderboard, comparison_rows, status_summary
from tamesu.runner import resume_run, run_eval
from tamesu.scoring import rescore_run


EVAL_ID = "support-ticket-triage/decision-rules/prompt-ablation"


class FixtureProvider:
    name = "meta"

    def __init__(self) -> None:
        self.calls = 0
        self.failures_remaining = 0
        self.unexpected_failures_remaining = 0
        self.response_metadata: dict[str, object] = {}

    def credential_error(self) -> None:
        return None

    def generate_text(self, **kwargs: object) -> ProviderResponse:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ProviderError("temporary fixture failure", retryable=True)
        if self.unexpected_failures_remaining:
            self.unexpected_failures_remaining -= 1
            raise RuntimeError(f"unexpected fixture failure {os.environ.get('META_API_KEY', '')}")
        answer = classify(str(kwargs["user_prompt"]))
        return ProviderResponse(
            text=json.dumps(answer),
            request_id=f"fixture-{self.calls}",
            usage={"input_tokens": 20, "output_tokens": 12, "total_tokens": 32},
            cost_usd=0.001,
            response_metadata=self.response_metadata,
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
        shutil.copytree(
            source,
            self.project,
            ignore=shutil.ignore_patterns("runs", "logs", "leaderboard.md"),
        )
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

    def test_failed_probes_are_visible_in_cli_status_and_run_output(self) -> None:
        self.provider.unexpected_failures_remaining = 4
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = command_run(
                self.project,
                EVAL_ID,
                only="muse-spark-1.2",
                limit_items=1,
                force=False,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue().count("failed\t"), 4)
        self.assertIn("4 run(s) failed", stderr.getvalue())

        plan = build_plan(load_eval_context(self.project, EVAL_ID))
        summary = status_summary(plan)
        self.assertEqual(summary["counts"]["failed"], 4)
        self.assertEqual(summary["counts"]["partial"], 0)

        compare_output = StringIO()
        with redirect_stdout(compare_output):
            self.assertEqual(command_compare(self.project, EVAL_ID), 0)
        self.assertIn("Excluded evidence: 4 failed", compare_output.getvalue())
        self.assertIn("Planned runs still owed: 4", compare_output.getvalue())

    def test_successful_probe_output_includes_completion_score_and_cost(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = command_run(
                self.project,
                EVAL_ID,
                only="muse-spark-1.2",
                limit_items=2,
                force=False,
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(output.count("partial\t"), 4)
        self.assertEqual(output.count("items=2/2\tfailed=0"), 4)
        self.assertEqual(output.count("exact_match_rate=1.0000"), 4)
        self.assertEqual(output.count("cost_usd=0.002000"), 4)
        self.assertIn("Probe items: 8/8 completed; 0 failed.", output)
        self.assertIn("excluded from formal comparisons", output)

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
        run_ids = run_eval(context, only="muse-spark-1.2", limit_items=1)

        first_run_dir = context.eval_dir / "runs" / run_ids[0]
        first_manifest = load_yaml(first_run_dir / "run.yml")
        self.assertEqual(first_manifest["state"], "partial")

        resumed = resume_run(context, run_ids[0])
        self.assertEqual(resumed, run_ids[0])
        result_paths = list((first_run_dir / "items").glob("*/result.yml"))
        self.assertEqual(len(result_paths), 1)
        self.assertEqual(load_yaml(result_paths[0])["attempts"], 2)

        log_path = context.eval_dir / "logs" / f"{run_ids[0]}.jsonl"
        events = [
            json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        attempts = [event for event in events if event["event"] == "provider_attempt"]
        self.assertEqual([event["attempt"] for event in attempts], [1, 2])
        self.assertEqual([event["ok"] for event in attempts], [False, True])
        self.assertEqual(attempts[0]["usage"], {})
        self.assertIsNone(attempts[0]["cost_usd"])
        starts = [event for event in events if event["event"] == "run_started"]
        self.assertEqual([event["resumed"] for event in starts], [False, True])
        self.assertTrue(any(event["event"] == "artifacts_written" for event in events))

    @patch.dict(os.environ, {"META_API_KEY": "sk-fixture-secret"})
    def test_logs_and_results_redact_secrets(self) -> None:
        context = load_eval_context(self.project, EVAL_ID)
        self.provider.response_metadata = {
            "authorization": "Bearer sk-fixture-secret",
            "debug": "key=sk-fixture-secret",
        }
        run_ids = run_eval(context, only="muse-spark-1.2", limit_items=1)
        run_id = run_ids[0]
        log_text = (context.eval_dir / "logs" / f"{run_id}.jsonl").read_text(
            encoding="utf-8"
        )
        result_text = next(
            (context.eval_dir / "runs" / run_id / "items").glob("*/result.yml")
        ).read_text(encoding="utf-8")
        self.assertNotIn("sk-fixture-secret", log_text)
        self.assertNotIn("sk-fixture-secret", result_text)
        self.assertIn("[REDACTED]", log_text)
        self.assertIn("[REDACTED]", result_text)

    @patch.dict(os.environ, {"META_API_KEY": "sk-unexpected-secret"})
    def test_unexpected_provider_exception_is_logged_and_terminal(self) -> None:
        context = load_eval_context(self.project, EVAL_ID)
        self.provider.unexpected_failures_remaining = 1
        run_ids = run_eval(context, only="muse-spark-1.2", limit_items=1)
        run_id = run_ids[0]
        run_dir = context.eval_dir / "runs" / run_id
        manifest = load_yaml(run_dir / "run.yml")
        result_path = next((run_dir / "items").glob("*/result.yml"))
        result = load_yaml(result_path)
        log_text = (context.eval_dir / "logs" / f"{run_id}.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertEqual(manifest["state"], "failed")
        self.assertEqual(result["state"], "failed")
        self.assertIn("unexpected RuntimeError", result["error"]["message"])
        self.assertNotIn("sk-unexpected-secret", log_text)
        self.assertNotIn("sk-unexpected-secret", result_path.read_text(encoding="utf-8"))

    def test_finalization_failure_leaves_resumable_logged_run(self) -> None:
        context = load_eval_context(self.project, EVAL_ID)
        with patch("tamesu.runner.build_report", side_effect=RuntimeError("report failed")):
            with self.assertRaisesRegex(ExecutionError, "Could not finalize run"):
                run_eval(context, only="muse-spark-1.2", limit_items=1)

        run_dirs = list((context.eval_dir / "runs").iterdir())
        self.assertEqual(len(run_dirs), 1)
        manifest = load_yaml(run_dirs[0] / "run.yml")
        self.assertEqual(manifest["state"], "partial")
        log_path = context.eval_dir / "logs" / f"{run_dirs[0].name}.jsonl"
        events = [
            json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(events[-2]["event"], "framework_error")
        self.assertEqual(events[-1]["event"], "run_finished")
        self.assertEqual(events[-1]["state"], "partial")
