from __future__ import annotations

import contextlib
import hashlib
import io
import unittest
from pathlib import Path

import yaml

from image_fixtures import EVAL_ID, ImageProject
from test_image_acceptance import BAD, GOOD, Harness

from tamesu.cli import command_close, command_promote
from tamesu.config import _validate_dataset, load_eval_context, load_yaml
from tamesu.errors import ExecutionError
from tamesu.identity import digest_file
from tamesu.judging import judge_eval
from tamesu.promote import execute_promotion, plan_promotion
from tamesu.review import export_pack, import_responses
from tamesu.runner import run_eval
from tamesu.tasks.structured_text import validate_json_schema

SCHEMA = Path(__file__).parents[1] / "schemas" / "dataset.schema.json"
CASE = """\
schema_version: 1
name: product-scanning
title: Product Scanning
description: Scanner experiments.
business_use: Scan products.
current_problem: Reference images are scarce.
technical_uncertainty: Whether scanners match references.
default_task: structured_text
"""


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + digest_file(path).encode())
    return digest.hexdigest()


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = Harness(self, review=True, judges=True, judge_role="screen")
        self.root = self.harness.project.root
        case = self.root / "cases" / "product-scanning"
        case.mkdir(parents=True)
        (case / "case.yml").write_text(CASE)
        judge_eval(self.harness.context)
        self.harness.review({GOOD: True, BAD: False})

    def close(self) -> None:
        command_close(self.root, EVAL_ID)

    def context(self):
        return load_eval_context(self.root, EVAL_ID)

    def test_refuses_while_the_eval_is_not_closed(self) -> None:
        with self.assertRaises(ExecutionError) as caught:
            plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        self.assertIn("close it first", str(caught.exception))

    def test_bad_destinations(self) -> None:
        self.close()
        for target, fragment in (
            ("nonsense", "--to must look like"),
            ("missing-case/x-v1", "Destination case does not exist"),
        ):
            with self.assertRaises(ExecutionError) as caught:
                plan_promotion(self.context(), target)
            self.assertIn(fragment, str(caught.exception))

    def test_dry_run_lists_copies_exclusions_and_products_without_an_accepted_image(self) -> None:
        self.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            command_promote(self.root, EVAL_ID, "product-scanning/product-scanning-v2", dry_run=True)
        text = out.getvalue()
        self.assertIn("Would copy 1 image(s)", text)
        self.assertIn(f"no-accepted-image\t{BAD}", text)
        self.assertIn(f"excluded\t{BAD}", text)
        self.assertIn("human_failed", text)
        self.assertFalse((self.root / "cases/product-scanning/datasets").exists())

    def test_promotion_writes_a_valid_new_dataset_with_provenance(self) -> None:
        self.close()
        plan = plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        execute_promotion(self.context(), plan)
        dataset_dir = self.root / "cases/product-scanning/datasets/product-scanning-v2"
        dataset = load_yaml(dataset_dir / "dataset.yml")
        errors: list[str] = []
        _validate_dataset(dataset, errors)
        self.assertEqual(errors, [])
        import json

        self.assertEqual(validate_json_schema(dataset, json.loads(SCHEMA.read_text())), [])
        (entry,) = dataset["items"]
        self.assertEqual(entry["id"], GOOD)
        image = dataset_dir / entry["assets"]["reference_image"][0]
        self.assertEqual(digest_file(image), entry["metadata"]["image_sha256"])
        source_run = self.harness.run_dir
        original = next((source_run / "items" / GOOD).glob("output-*"))
        self.assertEqual(digest_file(image), digest_file(original))  # checksum matches the source run
        meta = entry["metadata"]
        self.assertEqual(meta["source"], {"eval": EVAL_ID, "run_id": self.harness.run_id, "item_id": GOOD})
        self.assertEqual(meta["generator"]["model"], "fake-image-1")
        self.assertTrue(meta["prompt_fingerprint"].startswith("sha256:"))
        self.assertEqual([r["verdict"] for r in meta["review"]], ["pass"])
        self.assertEqual(meta["review"][0]["reviewer"], "andrew")
        promotion = load_yaml(dataset_dir / "promotion.yml")
        self.assertEqual(promotion["products_without_accepted_image"], [BAD])
        self.assertEqual(promotion["promoted"], [GOOD])

    def test_prior_dataset_versions_are_never_touched(self) -> None:
        self.close()
        before = tree_digest(self.root / "cases/product-reference/datasets")
        plan = plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        execute_promotion(self.context(), plan)
        self.assertEqual(tree_digest(self.root / "cases/product-reference/datasets"), before)
        with self.assertRaises(ExecutionError) as caught:
            plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        self.assertIn("frozen", str(caught.exception))

    def test_accepted_image_modified_after_closing_blocks_promotion(self) -> None:
        self.close()
        next((self.harness.run_dir / "items" / GOOD).glob("output-*")).write_bytes(b"tampered")
        with self.assertRaises(ExecutionError) as caught:
            plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        self.assertIn("No accepted images to promote", str(caught.exception))  # stale image is rejected

    def test_failed_copy_leaves_no_partial_dataset_version(self) -> None:
        self.close()
        plan = plan_promotion(self.context(), "product-scanning/product-scanning-v2")
        plan.copies[0][0].unlink()
        with self.assertRaises(OSError):
            execute_promotion(self.context(), plan)
        self.assertEqual(list((self.root / "cases/product-scanning/datasets").iterdir()), [])


class SelectionTests(unittest.TestCase):
    def promote(self, selection: str):
        project = ImageProject(
            products={GOOD: "a fictional lamp"}, review=True, selection=selection, repetitions=2
        )
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        case = project.root / "cases" / "product-scanning"
        case.mkdir(parents=True)
        (case / "case.yml").write_text(CASE)
        context = load_eval_context(project.root, EVAL_ID)
        run_ids = run_eval(context)
        self.assertEqual(len(run_ids), 2)
        out = project.root / "pack"
        export_pack(context, out)
        document = yaml.safe_load((out / "responses.yml").read_text())
        document["reviewer"] = "andrew"
        for response in document["responses"]:
            for outcome in response["dimensions"].values():
                outcome.update({"pass": True, "reason_code": None})
        (out / "responses.yml").write_text(yaml.safe_dump(document, sort_keys=False))
        import_responses(context, out / "responses.yml")
        command_close(project.root, EVAL_ID)
        return plan_promotion(load_eval_context(project.root, EVAL_ID), "product-scanning/product-scanning-v2")

    def test_selection_none_promotes_every_accepted_repetition(self) -> None:
        plan = self.promote("none")
        self.assertEqual(len(plan.promoted), 2)
        self.assertTrue(all(entry.startswith(f"{GOOD}-") and entry.endswith(("rep1", "rep2")) for entry in plan.promoted))

    def test_first_pass_promotes_one_and_reports_the_rest(self) -> None:
        plan = self.promote("first_pass")
        self.assertEqual(plan.promoted, [GOOD])
        self.assertEqual(len(plan.excluded), 1)
        self.assertIn("not selected", plan.excluded[0][2])


class EndToEndTests(unittest.TestCase):
    def test_generate_judge_review_close_promote_dry_run(self) -> None:
        project = ImageProject(review=True, judges=True, judge_role="screen")
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        case = project.root / "cases" / "product-scanning"
        case.mkdir(parents=True)
        (case / "case.yml").write_text(CASE)
        context = load_eval_context(project.root, EVAL_ID)
        (run_id,) = run_eval(context)
        self.assertEqual(judge_eval(context).ok, 2)
        out = project.root / "pack"
        export_pack(context, out)
        document = yaml.safe_load((out / "responses.yml").read_text())
        document["reviewer"] = "andrew"
        for response in document["responses"]:
            for outcome in response["dimensions"].values():
                outcome.update({"pass": True, "reason_code": None})
        (out / "responses.yml").write_text(yaml.safe_dump(document, sort_keys=False))
        import_responses(context, out / "responses.yml")
        self.assertEqual(command_close(project.root, EVAL_ID), 0)
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            command_promote(project.root, EVAL_ID, "product-scanning/product-scanning-v2", dry_run=True)
        self.assertIn("Would copy 2 image(s)", text.getvalue())
        self.assertFalse((project.root / "cases/product-scanning/datasets").exists())
        self.assertTrue(run_id)


if __name__ == "__main__":
    unittest.main()
