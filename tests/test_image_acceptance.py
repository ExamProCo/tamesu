from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from image_fixtures import EVAL_ID, ImageProject

from tamesu.acceptance import MIN_AGREEMENT_SAMPLES, cohens_kappa, run_evidence
from tamesu.cli import command_close, command_status
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import TamesuError
from tamesu.judging import judge_eval
from tamesu.policy import Acceptance
from tamesu.review import export_pack, human_state, import_responses
from tamesu.run_report import rescore_run
from tamesu.runner import run_eval

GOOD, BAD = "aurora-lamp", "birch-kettle"


class Harness:
    """Runs a two-product eval (GOOD passes the fake judge, BAD fails it) and drives evidence."""

    def __init__(self, test: unittest.TestCase, **project_kwargs) -> None:
        products = {GOOD: "a fictional lamp", BAD: "BADJUDGE a fictional kettle"}
        self.project = ImageProject(products=products, **project_kwargs)
        self.project.__enter__()
        test.addCleanup(self.project.__exit__, None, None, None)
        self.context = load_eval_context(self.project.root, EVAL_ID)
        (self.run_id,) = run_eval(self.context)
        self.run_dir = self.context.eval_dir / "runs" / self.run_id
        self.packs = 0

    def evidence(self) -> dict:
        return run_evidence(self.context, self.run_dir)

    def by_item(self) -> dict:
        report = load_yaml(self.run_dir / "report.yml")
        return {entry["item_id"]: entry for entry in report["items"]}

    def review(self, verdicts: dict[str, bool], reviewer: str = "andrew") -> None:
        """Export a pack and import a review passing/failing each item as given."""
        self.packs += 1
        out = self.project.root / f"pack-{self.packs}"
        export_pack(self.context, out, include_reviewed=True)
        key = next(
            load_yaml(p) for p in sorted((self.context.eval_dir / "review" / "keys").glob("*.yml"))
            if load_yaml(p)["pack_id"] == load_yaml(out / "pack.yml")["pack_id"]
        )
        document = yaml.safe_load((out / "responses.yml").read_text())
        document["reviewer"] = reviewer
        for response in document["responses"]:
            item_id = key["items"][response["presentation_id"]]["item_id"]
            passing = verdicts[item_id]
            for index, outcome in enumerate(response["dimensions"].values()):
                if passing or index:
                    outcome.update({"pass": True, "reason_code": None})
                else:
                    outcome.update({"pass": False, "reason_code": "multiple-products"})
        (out / "responses.yml").write_text(yaml.safe_dump(document, sort_keys=False))
        import_responses(self.context, out / "responses.yml")


class AcceptanceMatrixTests(unittest.TestCase):
    def state(self, harness: Harness, item: str) -> str:
        return harness.by_item()[item]["acceptance"]["state"]

    def test_default_policy_is_mechanical_only(self) -> None:
        harness = Harness(self)
        self.assertEqual({harness.evidence()["counts"]["accepted"]}, {2})

    def test_human_required_waits_then_follows_the_reviewer(self) -> None:
        harness = Harness(self, review=True)
        self.assertEqual((self.state(harness, GOOD), self.state(harness, BAD)), ("pending", "pending"))
        harness.review({GOOD: True, BAD: False})
        self.assertEqual((self.state(harness, GOOD), self.state(harness, BAD)), ("accepted", "rejected"))
        self.assertEqual(harness.by_item()[BAD]["acceptance"]["reasons"], ["human_failed"])

    def test_screen_judge_is_informational_and_cannot_accept_or_reject(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: True})  # human disagrees with the judge on BAD
        items = harness.by_item()
        self.assertEqual(items[BAD]["model_judge"]["verdict"], "fail")
        self.assertEqual(items[BAD]["human"]["verdict"], "pass")
        self.assertEqual(items[BAD]["acceptance"]["state"], "accepted")  # human decides

    def test_gate_judge_must_pass_in_addition_to_human(self) -> None:
        harness = Harness(
            self, review=True, judges=True, judge_role="gate", requires=["mechanical", "model_judge", "human"]
        )
        harness.review({GOOD: True, BAD: True})
        self.assertEqual(self.state(harness, GOOD), "pending")  # judge has not run
        judge_eval(harness.context)
        self.assertEqual(self.state(harness, GOOD), "accepted")
        self.assertEqual(self.state(harness, BAD), "rejected")
        self.assertEqual(harness.by_item()[BAD]["acceptance"]["reasons"], ["model_judge_failed"])

    def test_judge_only_gate_when_declared(self) -> None:
        harness = Harness(self, judges=True, judge_role="gate", requires=["mechanical", "model_judge"])
        judge_eval(harness.context)
        self.assertEqual((self.state(harness, GOOD), self.state(harness, BAD)), ("accepted", "rejected"))

    def test_failed_judge_calls_leave_the_item_pending_not_rejected(self) -> None:
        from tamesu.errors import ProviderError

        harness = Harness(self, judges=True, judge_role="gate", requires=["mechanical", "model_judge"])
        harness.project.provider.judge_failures = [ProviderError("down") for _ in range(2)]
        judge_eval(harness.context)
        self.assertEqual(self.state(harness, GOOD), "pending")
        self.assertEqual(harness.by_item()[GOOD]["model_judge"]["failed_calls"], 1)

    def test_two_reviewers_disagree_reject_policy(self) -> None:
        harness = Harness(self, review=True, required_reviews=2, on_disagreement="reject")
        harness.review({GOOD: True, BAD: False}, reviewer="andrew")
        harness.review({GOOD: False, BAD: False}, reviewer="bea")
        items = harness.by_item()
        self.assertEqual(items[GOOD]["human"]["state"], "disagreement")
        self.assertEqual(items[GOOD]["acceptance"]["state"], "rejected")

    def test_two_reviewers_disagree_adjudicate_needs_a_third(self) -> None:
        harness = Harness(self, review=True, required_reviews=2, on_disagreement="adjudicate")
        harness.review({GOOD: True, BAD: False}, reviewer="andrew")
        harness.review({GOOD: False, BAD: False}, reviewer="bea")
        self.assertEqual(harness.by_item()[GOOD]["human"]["state"], "needs_adjudication")
        self.assertEqual(self.state(harness, GOOD), "pending")
        self.assertEqual(harness.evidence()["review"]["owed_reviews"], 1)
        harness.review({GOOD: True, BAD: False}, reviewer="cleo")
        self.assertEqual(harness.by_item()[GOOD]["human"]["state"], "adjudicated")
        self.assertEqual(self.state(harness, GOOD), "accepted")  # 2 of 3 pass

    def test_human_state_function_directly(self) -> None:
        policy = Acceptance(("human",), 2, "adjudicate", None, "none")
        verdicts = lambda *v: [{"verdict": x} for x in v]
        self.assertEqual(human_state(verdicts("pass"), policy)["owed"], 1)
        self.assertEqual(human_state(verdicts("pass", "pass"), policy)["verdict"], "pass")
        self.assertEqual(human_state(verdicts("fail", "fail"), policy)["verdict"], "fail")
        self.assertIsNone(human_state(verdicts("pass", "fail"), policy)["verdict"])
        self.assertEqual(human_state(verdicts("pass", "fail", "fail"), policy)["verdict"], "fail")

    def test_reviews_do_not_survive_a_tampered_image(self) -> None:
        harness = Harness(self, review=True)
        harness.review({GOOD: True, BAD: True})
        image = next((harness.run_dir / "items" / GOOD).glob("output-*"))
        image.write_bytes(b"tampered")
        rescore_run(harness.context, harness.run_dir)
        item = harness.by_item()[GOOD]
        self.assertEqual(item["acceptance"]["state"], "rejected")
        self.assertEqual(item["acceptance"]["reasons"], ["stale_image"])
        self.assertEqual(harness.evidence()["counts"]["stale_images"], 1)


class MetricsTests(unittest.TestCase):
    def test_denominators_end_to_end_versus_conditional(self) -> None:
        products = {"one": "ok one", "two": "ok two", "three": "REFUSE three", "four": "CORRUPT four"}
        project = ImageProject(products=products, review=True)
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        context = load_eval_context(project.root, EVAL_ID)
        (run_id,) = run_eval(context)
        harness = Harness.__new__(Harness)
        harness.project, harness.context = project, context
        harness.run_dir = context.eval_dir / "runs" / run_id
        harness.packs = 0
        harness.review({"one": True, "two": False})
        metrics = load_yaml(harness.run_dir / "report.yml")["evidence"]["metrics"]
        self.assertEqual(metrics["product_reference_pass_rate"], 0.25)  # 1 accepted / 4 planned
        self.assertAlmostEqual(metrics["product_reference_pass_rate_given_image"], 0.5)  # 1 / 2 generated
        self.assertEqual(metrics["human_pass_rate"], 0.25)
        report = load_yaml(harness.run_dir / "report.yml")
        self.assertEqual(report["metrics"]["generation_success_rate"], 0.5)
        counts = report["evidence"]["counts"]
        self.assertEqual((counts["planned"], counts["generated"], counts["accepted"], counts["rejected"]), (4, 2, 1, 3))

    def test_columns_are_separate_and_costs_are_split(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: False})
        evidence = load_yaml(harness.run_dir / "report.yml")["evidence"]
        self.assertEqual(evidence["columns"]["mechanical"], {"pass": 2, "of_generated": 2})
        self.assertEqual(evidence["columns"]["model_judge"], {"pass": 1, "judged": 2, "of_generated": 2})
        self.assertEqual(evidence["columns"]["human"], {"pass": 1, "reviewed": 2, "of_generated": 2})
        cost = evidence["cost"]
        self.assertEqual((cost["generation_usd"], cost["judge_usd"], cost["total_usd"]), (0.02, 0.004, 0.024))
        self.assertEqual(cost["per_accepted_usd"], 0.024)  # one accepted image
        self.assertEqual(evidence["review"]["completion"], 1.0)

    def test_dimension_failures_are_reported_separately_for_human_and_judge(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: False})
        dims = load_yaml(harness.run_dir / "report.yml")["evidence"]["dimensions"]
        for source in ("human", "model_judge"):
            stats = dims[source]["single-product-composition"]
            self.assertEqual((stats["evaluated"], stats["failed"]), (2, 1))
            self.assertEqual(stats["top_reasons"], {"multiple-products": 1})
            self.assertEqual(dims[source]["fictional-packaging"]["failed"], 0)

    def test_agreement_is_withheld_on_small_samples(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: False})
        agreement = load_yaml(harness.run_dir / "report.yml")["evidence"]["agreement"]
        stats = agreement["single-product-composition"]
        self.assertEqual((stats["n"], stats["raw_agreement"]), (2, 1.0))
        self.assertTrue(stats["insufficient_data"])
        self.assertIsNone(stats["kappa"])

    def test_agreement_with_enough_samples_reports_kappa(self) -> None:
        products = {f"p{i:02d}": ("BADJUDGE x" if i % 2 else "fine") for i in range(MIN_AGREEMENT_SAMPLES)}
        project = ImageProject(products=products, review=True, judges=True, judge_role="screen")
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        context = load_eval_context(project.root, EVAL_ID)
        (run_id,) = run_eval(context)
        judge_eval(context)
        harness = Harness.__new__(Harness)
        harness.project, harness.context, harness.packs = project, context, 0
        harness.run_dir = context.eval_dir / "runs" / run_id
        harness.review({item: not products[item].startswith("BAD") for item in products})  # humans agree
        stats = load_yaml(harness.run_dir / "report.yml")["evidence"]["agreement"]["single-product-composition"]
        self.assertEqual((stats["n"], stats["raw_agreement"], stats["kappa"]), (10, 1.0, 1.0))
        self.assertFalse(stats["insufficient_data"])

    def test_kappa_edge_cases(self) -> None:
        self.assertIsNone(cohens_kappa([]))
        self.assertIsNone(cohens_kappa([(True, True)] * 5))  # constant raters: undefined
        self.assertEqual(cohens_kappa([(True, True), (False, False)]), 1.0)
        self.assertEqual(cohens_kappa([(True, False), (False, True)]), -1.0)


class CloseAndStatusTests(unittest.TestCase):
    def test_close_refuses_until_reviews_exist_then_records_the_gate(self) -> None:
        harness = Harness(self, review=True)
        root = harness.project.root
        with self.assertRaises(TamesuError) as caught:
            command_close(root, EVAL_ID)
        self.assertIn("required evidence is missing", str(caught.exception))
        self.assertIn("2 item(s) pending", str(caught.exception))
        harness.review({GOOD: True, BAD: False})
        self.assertEqual(command_close(root, EVAL_ID), 0)
        closing = load_yaml(harness.context.eval_dir / "closing.yml")
        self.assertFalse(closing["allow_incomplete_review"])
        self.assertFalse(closing["incomplete_evidence"])
        self.assertEqual(closing["runs"][harness.run_id]["accepted"], 1)
        self.assertEqual(load_eval_context(root, EVAL_ID).evaluation["status"], "complete")

    def test_allow_incomplete_review_closes_and_records_the_gap(self) -> None:
        harness = Harness(self, review=True)
        command_close(harness.project.root, EVAL_ID, allow_incomplete_review=True)
        closing = load_yaml(harness.context.eval_dir / "closing.yml")
        self.assertTrue(closing["allow_incomplete_review"])
        self.assertTrue(closing["incomplete_evidence"])
        self.assertEqual(closing["pending_items"], 2)

    def test_mechanical_only_image_eval_closes_without_review(self) -> None:
        harness = Harness(self)
        self.assertEqual(command_close(harness.project.root, EVAL_ID), 0)

    def test_status_reports_owed_work_and_stale_evidence(self) -> None:
        import contextlib
        import io
        import os

        harness = Harness(self, review=True, judges=True, judge_role="screen")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            command_status(harness.project.root, EVAL_ID)
        self.assertIn("awaiting_judgment=2", out.getvalue())
        self.assertIn("awaiting_review=2", out.getvalue())
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: True})
        harness.project.rubric_path.write_text(
            harness.project.rubric_path.read_text().replace("Exactly one", "Only one")
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            command_status(harness.project.root, EVAL_ID)
        text = out.getvalue()
        self.assertIn("stale_evidence=", text)
        self.assertIn("awaiting_review=2", text)  # old reviews no longer count

    def test_rescore_rebuilds_acceptance_with_no_provider_calls(self) -> None:
        harness = Harness(self, review=True)
        harness.review({GOOD: True, BAD: False})
        calls = harness.project.provider.calls
        report = rescore_run(harness.context, harness.run_dir)
        self.assertEqual(harness.project.provider.calls, calls)
        self.assertEqual(report["evidence"]["counts"]["accepted"], 1)


if __name__ == "__main__":
    unittest.main()


class ReportTests(unittest.TestCase):
    def test_evaluation_report_shows_separate_columns_and_audit_links(self) -> None:
        from tamesu.planner import build_plan
        from tamesu.reporting import build_evaluation_report, build_leaderboard

        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: False})
        plan = build_plan(harness.context)
        text = build_evaluation_report(plan).read_text()
        build_leaderboard(plan)
        self.assertIn("| Mechanical | 2 | 2 | 2 |", text)
        self.assertIn("| Model judge | 1 | 2 | 2 |", text)
        self.assertIn("| Human | 1 | 2 | 2 |", text)
        self.assertIn("single-product-composition", text)
        self.assertIn("too few paired items", text)
        self.assertIn("[image](runs/", text)
        self.assertIn("[judgment](runs/", text)
        self.assertIn("[review](runs/", text)
