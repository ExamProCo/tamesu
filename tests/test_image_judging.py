from __future__ import annotations

import unittest

from image_fixtures import EVAL_ID, ImageProject

from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ConfigError, ExecutionError, ProviderError
from tamesu.identity import digest_file
from tamesu.judging import (
    judge_eval,
    judgment_currency,
    load_judgments,
    recorded_judge_cost,
    render_judge_prompts,
    load_judges,
)
from tamesu.pricing import recorded_cost
from tamesu.planner import build_plan
from tamesu.rubric import judge_output_schema, load_rubric, overall_pass, validate_judge_output
from tamesu.runner import run_eval


class RubricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject(judges=True)
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)

    def test_schema_is_derived_from_the_rubric_and_requires_every_dimension(self) -> None:
        rubric = load_rubric(self.project.rubric_path)
        schema = judge_output_schema(rubric)
        self.assertEqual(
            schema["properties"]["dimensions"]["required"],
            ["single-product-composition", "fictional-packaging"],
        )
        reason = schema["properties"]["dimensions"]["properties"]["fictional-packaging"]["properties"]["reason_code"]
        self.assertEqual(reason["enum"], ["none", "real-brand", "readable-text"])

    def test_adding_a_dimension_changes_schema_and_fingerprint(self) -> None:
        before = load_rubric(self.project.rubric_path)
        self.project.rubric_path.write_text(
            self.project.rubric_path.read_text().replace(
                "acceptance:",
                "  - id: front-three-quarter-view\n    question: Viewed from the front three-quarter.\n"
                "    reason_codes: [wrong-viewpoint]\nacceptance:",
            )
        )
        after = load_rubric(self.project.rubric_path)
        self.assertNotEqual(before.sha256, after.sha256)
        self.assertIn("front-three-quarter-view", judge_output_schema(after)["properties"]["dimensions"]["required"])

    def test_verdict_is_computed_and_reason_codes_are_enforced(self) -> None:
        rubric = load_rubric(self.project.rubric_path)
        good = {"pass": True, "reason_code": "none", "explanation": "ok"}
        bad = {"pass": False, "reason_code": "cropped", "explanation": "cut off"}
        dims = {"single-product-composition": good, "fictional-packaging": good}
        self.assertEqual(validate_judge_output({"dimensions": dims}, rubric), [])
        self.assertTrue(overall_pass(dims, rubric))
        dims["single-product-composition"] = bad
        self.assertFalse(overall_pass(dims, rubric))
        wrong_code = {**bad, "reason_code": "real-brand"}  # valid enum elsewhere, wrong dimension
        self.assertTrue(validate_judge_output({"dimensions": {**dims, "single-product-composition": wrong_code}}, rubric))
        failing_without_reason = {**bad, "reason_code": "none"}
        self.assertTrue(
            validate_judge_output({"dimensions": {**dims, "single-product-composition": failing_without_reason}}, rubric)
        )
        passing_with_reason = {**good, "reason_code": "cropped"}
        self.assertTrue(
            validate_judge_output({"dimensions": {**dims, "single-product-composition": passing_with_reason}}, rubric)
        )
        self.assertTrue(validate_judge_output({"dimensions": {"single-product-composition": good}}, rubric))

    def test_invalid_rubric_is_reported_with_every_problem(self) -> None:
        self.project.rubric_path.write_text(
            "schema_version: 1\nname: Bad Name\ndimensions:\n  - id: a\n    question: q\n    reason_codes: [none]\n"
            "acceptance: {rule: vibes}\n"
        )
        with self.assertRaises(ConfigError) as caught:
            load_rubric(self.project.rubric_path)
        message = str(caught.exception)
        self.assertIn("rubric.name", message)
        self.assertIn("reserved code 'none'", message)
        self.assertIn("rubric.acceptance.rule", message)


class JudgingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject(judges=True)
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)
        self.context = load_eval_context(self.project.root, EVAL_ID)
        (self.run_id,) = run_eval(self.context)
        self.run_dir = self.context.eval_dir / "runs" / self.run_id

    def item_dir(self, item_id: str = "aurora-lamp"):
        return self.run_dir / "items" / item_id

    def test_judging_twice_writes_two_records_and_never_overwrites(self) -> None:
        judge_eval(self.context)
        first = {p.name: digest_file(p) for p in (self.item_dir() / "judgments").glob("*.yml")}
        self.assertEqual(len(first), 1)
        judge_eval(self.context)
        second = {p.name: digest_file(p) for p in (self.item_dir() / "judgments").glob("*.yml")}
        self.assertEqual(len(second), 2)
        for name, digest in first.items():
            self.assertEqual(second[name], digest)  # earlier record untouched
        names = sorted(second)
        rubric_sha8 = load_judges(self.context)[0].rubric.sha8
        self.assertEqual(names, [f"vision-judge-1--{rubric_sha8}--0001.yml", f"vision-judge-1--{rubric_sha8}--0002.yml"])

    def test_record_carries_identity_hashes_usage_and_computed_verdict(self) -> None:
        summary = judge_eval(self.context)
        self.assertEqual((summary.judged, summary.ok, summary.failed), (2, 2, 0))
        (record,) = load_judgments(self.item_dir())
        result = load_yaml(self.item_dir() / "result.yml")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["verdict"], "pass")
        self.assertEqual(record["image_sha256"], result["output"]["sha256"])
        self.assertEqual(record["judge"]["model"], "fake-judge-1")
        self.assertTrue(record["rubric"]["sha256"].startswith("sha256:"))
        self.assertEqual(record["usage"]["output_tokens"], 30)
        self.assertEqual(record["cost_usd"], 0.002)
        self.assertIn("system_sha256", record["prompt"])

    def fresh(self, brief: str):
        """A new project whose first item has the given brief *before* generation."""
        project = ImageProject(products={"aurora-lamp": brief}, judges=True)
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        context = load_eval_context(project.root, EVAL_ID)
        (run_id,) = run_eval(context)
        return context, context.eval_dir / "runs" / run_id / "items" / "aurora-lamp"

    def test_failing_dimension_makes_computed_verdict_fail(self) -> None:
        context, item_dir = self.fresh("BADJUDGE lamp")
        judge_eval(context)
        (record,) = load_judgments(item_dir)
        self.assertEqual(record["verdict"], "fail")
        self.assertEqual(record["dimensions"]["single-product-composition"]["reason_code"], "multiple-products")

    def test_judge_never_sees_arm_model_run_repetition_or_cost(self) -> None:
        judge_eval(self.context)
        forbidden = ["baseline", "fake-image-1", self.run_id, "rep1", "0.01", "fake-baseline"]
        for call in self.project.provider.judge_calls:
            visible = call["system"] + call["text"]
            for needle in forbidden:
                self.assertNotIn(needle, visible)
            self.assertEqual(set(vars(call["image"])), {"data", "media_type"})  # bytes + type only

    def test_prompt_templates_cannot_reference_hidden_fields(self) -> None:
        judge = load_judges(self.context)[0]
        item = {"id": "aurora-lamp", "input": {"brief": "x"}, "expected": {"secret": 1}}
        for expression in ("arm.id", "run_id", "model", "item.expected", "cost", "repetition"):
            judge.prompts["user"].write_text("{{ " + expression + " }}")
            with self.assertRaises(ConfigError, msg=expression):
                render_judge_prompts(judge, item)

    def test_judge_outage_leaves_generation_state_untouched(self) -> None:
        before = {
            path: digest_file(path)
            for path in [self.run_dir / "run.yml", *self.run_dir.glob("items/*/result.yml"), *self.run_dir.glob("items/*/output-*")]
        }
        provider = self.project.provider
        provider.judge_failures = [ProviderError("judge down", retryable=False) for _ in range(2)]
        summary = judge_eval(self.context)
        self.assertEqual((summary.failed, summary.ok), (2, 0))
        self.assertEqual({path: digest_file(path) for path in before}, before)
        (record,) = load_judgments(self.item_dir())
        self.assertEqual(record["status"], "error")
        self.assertNotIn("verdict", record)
        self.assertEqual(record["error"]["message"], "judge down")
        # a later rejudge succeeds and sits beside the failure
        judge_eval(self.context)
        statuses = [r["status"] for r in load_judgments(self.item_dir())]
        self.assertEqual(statuses, ["error", "ok"])

    def test_transient_judge_failures_retry(self) -> None:
        self.project.provider.judge_failures = [ProviderError("blip", retryable=True)]
        judge_eval(self.context, limit_items=1)
        (record,) = load_judgments(self.item_dir())
        self.assertEqual((record["status"], record["attempts"]), ("ok", 2))

    def test_unparseable_judge_output_is_recorded_not_trusted(self) -> None:
        context, item_dir = self.fresh("GARBAGE lamp")
        judge_eval(context)
        (record,) = load_judgments(item_dir)
        self.assertEqual(record["status"], "invalid_output")
        self.assertNotIn("verdict", record)
        self.assertEqual(record["raw_output"], "not json at all")

    def test_rubric_change_marks_old_judgments_stale_but_keeps_them(self) -> None:
        judge_eval(self.context)
        (record,) = load_judgments(self.item_dir())
        result = load_yaml(self.item_dir() / "result.yml")
        self.project.rubric_path.write_text(self.project.rubric_path.read_text().replace("Exactly one", "Only one"))
        new_sha = load_rubric(self.project.rubric_path).sha256
        self.assertEqual(
            judgment_currency(record, rubric_sha256=new_sha, image_sha256=result["output"]["sha256"]), "stale_rubric"
        )
        judge_eval(self.context)
        records = load_judgments(self.item_dir())
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["rubric"]["sha256"], records[1]["rubric"]["sha256"])

    def test_tampered_image_is_skipped_not_judged(self) -> None:
        result = load_yaml(self.item_dir() / "result.yml")
        (self.item_dir() / result["output"]["path"]).write_bytes(b"tampered")
        summary = judge_eval(self.context)
        self.assertIn(("aurora-lamp", "stale image"), [(i, r) for _, i, r in summary.skipped])
        self.assertFalse((self.item_dir() / "judgments").exists())

    def test_item_edited_after_generation_is_not_judged_against_a_different_brief(self) -> None:
        self.project.set_brief("aurora-lamp", "a completely different lamp")
        summary = judge_eval(load_eval_context(self.project.root, EVAL_ID), run_id=self.run_id)
        self.assertIn(
            ("aurora-lamp", "dataset item changed since generation"),
            [(item, reason) for _, item, reason in summary.skipped],
        )
        self.assertFalse((self.item_dir() / "judgments").exists())

    def test_judge_calls_are_logged_with_stage_judge(self) -> None:
        judge_eval(self.context)
        events = [
            __import__("json").loads(line)
            for line in (self.context.eval_dir / "logs" / f"{self.run_id}.jsonl").read_text().splitlines()
        ]
        attempts = [e for e in events if e["event"] == "provider_attempt" and e["stage"] == "judge"]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all("image_sha256" in e["request"] for e in attempts))
        self.assertTrue(any(e["event"] == "judge_finished" for e in events))

    def test_judge_budget_is_a_separate_ledger(self) -> None:
        judge_eval(self.context)
        spent, unknown = recorded_judge_cost(self.context.eval_dir)
        self.assertEqual((spent, unknown), (0.004, 0))
        generation_only = recorded_cost(build_plan(self.context)).known_usd
        self.assertEqual(generation_only, 0.02)  # judge spend never counted against generation
        self.project.eval_path.write_text(
            self.project.eval_path.read_text().replace("judge_budget_usd: 1.00", "judge_budget_usd: 0.004")
        )
        with self.assertRaises(ExecutionError) as caught:
            judge_eval(load_eval_context(self.project.root, EVAL_ID))
        self.assertIn("judge budget ceiling", str(caught.exception))

    def test_adding_a_judge_after_banking_does_not_unbank_the_run(self) -> None:
        plain = ImageProject(judges=False)
        plain.__enter__()
        self.addCleanup(plain.__exit__, None, None, None)
        context = load_eval_context(plain.root, EVAL_ID)
        run_eval(context)
        self.assertEqual(len(build_plan(context).banked_run_ids), 1)
        plain.judges = True
        plain.write_eval()
        context = load_eval_context(plain.root, EVAL_ID)
        self.assertEqual(len(build_plan(context).banked_run_ids), 1)  # judges are not run identity


class PrerequisiteTests(unittest.TestCase):
    def test_images_failing_a_prerequisite_are_not_judged(self) -> None:
        project = ImageProject(
            judges=True, mechanical=["min_resolution: {width: 128, height: 128, prerequisite: true}"]
        )
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        context = load_eval_context(project.root, EVAL_ID)
        run_eval(context)
        summary = judge_eval(context)
        self.assertEqual(summary.judged, 0)
        self.assertEqual({reason for _, _, reason in summary.skipped}, {"failed a prerequisite mechanical check"})
        self.assertEqual(project.provider.judge_calls, [])


class JudgeLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject(judges=True)
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)

    def error(self) -> str:
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(self.project.root, EVAL_ID)
        return str(caught.exception)

    def edit(self, old: str, new: str) -> None:
        self.project.eval_path.write_text(self.project.eval_path.read_text().replace(old, new))

    def test_judge_budget_is_required_when_judges_exist(self) -> None:
        self.edit("  judge_budget_usd: 1.00\n", "")
        self.assertIn("judge_budget_usd is required", self.error())

    def test_text_only_model_cannot_judge_images(self) -> None:
        self.edit("model: fake-judge-1", "model: claude-haiku-4-5")
        self.edit("provider: meta\n      model: claude", "provider: anthropic\n      model: claude")
        self.assertIn("lacks judge capabilities: image_input", self.error())

    def test_missing_prompt_and_rubric_files_are_reported(self) -> None:
        self.edit("judge-user.md", "nope.md")
        self.edit("product-reference-quality.yml", "missing.yml")
        message = self.error()
        self.assertIn("nope.md", message)
        self.assertIn("missing.yml", message)

    def test_unknown_judge_field(self) -> None:
        self.edit("      repetitions: 1\n", "      repetitions: 1\n      arm_hint: baseline\n")
        self.assertIn("model_judges[0].arm_hint is not a recognized field", self.error())


if __name__ == "__main__":
    unittest.main()
