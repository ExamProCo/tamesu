from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from image_fixtures import EVAL_ID, ImageProject, png_bytes

from tamesu.artifacts import write_yaml_exclusive
from tamesu.config import load_eval_context, load_yaml
from tamesu.errors import ConfigError, ExecutionError
from tamesu.identity import digest_file
from tamesu.review import (
    ReviewImportError,
    _identifying_metadata,
    export_pack,
    import_responses,
    load_reviews,
    review_status,
    split_reviews,
)
from tamesu.rubric import load_rubric
from tamesu.runner import run_eval


class ReviewBase(unittest.TestCase):
    required = 1

    def setUp(self) -> None:
        self.project = ImageProject(review=True, required_reviews=self.required)
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)
        self.context = load_eval_context(self.project.root, EVAL_ID)
        (self.run_id,) = run_eval(self.context)
        self.run_dir = self.context.eval_dir / "runs" / self.run_id
        self.out = self.project.root / "review-pack"

    def export(self, **kwargs):
        out = kwargs.pop("out", self.out)
        return export_pack(self.context, out, **kwargs)

    def fill(self, out: Path | None = None, reviewer: str = "andrew", fail: dict[str, str] | None = None) -> Path:
        """Fill responses.yml: pass everything, except {presentation_id: dimension} -> failing."""
        path = (out or self.out) / "responses.yml"
        document = yaml.safe_load(path.read_text())
        document["reviewer"] = reviewer
        for response in document["responses"]:
            for dimension, outcome in response["dimensions"].items():
                if fail and fail.get(response["presentation_id"]) == dimension:
                    outcome.update({"pass": False, "reason_code": "cropped" if dimension.startswith("single") else "real-brand"})
                else:
                    outcome.update({"pass": True, "reason_code": None})
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        return path

    def item_dir(self, item_id: str = "aurora-lamp") -> Path:
        return self.run_dir / "items" / item_id


class ExportTests(ReviewBase):
    def test_export_writes_pack_template_and_key_outside_the_pack(self) -> None:
        result = self.export(seed=7)
        self.assertEqual(result.exported, 2)
        pack = load_yaml(self.out / "pack.yml")
        self.assertEqual(pack["mode"], "absolute")
        self.assertEqual({d["id"] for d in pack["rubric"]["dimensions"]}, {"single-product-composition", "fictional-packaging"})
        self.assertEqual(len(list((self.out / "images").iterdir())), 2)
        responses = load_yaml(self.out / "responses.yml")
        self.assertEqual(responses["pack_id"], result.pack_id)
        self.assertIsNone(responses["responses"][0]["dimensions"]["fictional-packaging"]["pass"])
        self.assertTrue(result.key_path.is_file())
        self.assertFalse(result.key_path.is_relative_to(self.out))
        key = load_yaml(result.key_path)
        self.assertEqual(key["seed"], 7)
        self.assertEqual({v["item_id"] for v in key["items"].values()}, {"aurora-lamp", "birch-kettle"})

    def test_pack_reveals_nothing_about_arm_model_run_or_item_ids(self) -> None:
        self.export()
        forbidden = ["baseline", "fake-image-1", "fake-judge", self.run_id, "aurora-lamp", "birch-kettle", "rep1"]
        for path in self.out.rglob("*"):
            haystack = path.name + (path.read_text() if path.suffix in {".yml", ".yaml", ".html"} else "")
            for needle in forbidden:
                self.assertNotIn(needle, haystack, f"{needle!r} leaked via {path}")

    def test_review_page_blocks_saving_a_fail_without_a_reason(self) -> None:
        self.export()
        page = (self.out / "review.html").read_text()
        self.assertIn("Choose a reason for every fail", page)

    def test_review_page_is_self_contained_and_its_output_imports_unchanged(self) -> None:
        import json

        result = self.export()
        page = (self.out / "review.html").read_text()
        self.assertIn(result.pack_id, page)
        self.assertNotIn("http://", page.replace("<!doctype", ""))  # no external resources
        pack = load_yaml(self.out / "pack.yml")
        self.assertIn(pack["items"][0]["presentation_id"], page)
        # what the page's save button emits: JSON, which is valid YAML
        responses = {
            "schema_version": 1,
            "pack_id": result.pack_id,
            "reviewer": "andrew",
            "responses": [
                {
                    "presentation_id": item["presentation_id"],
                    "dimensions": {
                        d["id"]: {"pass": True, "reason_code": None} for d in pack["rubric"]["dimensions"]
                    },
                    "notes": "",
                }
                for item in pack["items"]
            ],
        }
        path = self.out / "from-page.yml"
        path.write_text(json.dumps(responses, indent=2))
        self.assertEqual(len(import_responses(self.context, path).written), 2)

    def test_same_seed_gives_same_order_and_ids(self) -> None:
        first = self.export(seed=11)
        first_pack = load_yaml(self.out / "pack.yml")["items"]
        second_out = self.project.root / "pack2"
        second = self.export(seed=11, out=second_out)
        second_pack = load_yaml(second_out / "pack.yml")["items"]
        self.assertEqual([i["presentation_id"] for i in first_pack], [i["presentation_id"] for i in second_pack])
        self.assertNotEqual(first.pack_id, "")
        del second

    def test_order_is_shuffled_by_the_seed(self) -> None:
        orders = set()
        for seed in range(12):
            out = self.project.root / f"p{seed}"
            export_pack(self.context, out, seed=seed)
            key = load_yaml(next(iter((self.context.eval_dir / "review" / "keys").glob("*"))).parent / f"{load_yaml(out / 'pack.yml')['pack_id']}.yml")
            orders.add(tuple(key["items"][i["presentation_id"]]["item_id"] for i in load_yaml(out / "pack.yml")["items"]))
        self.assertEqual(len(orders), 2)  # both orders appear across seeds

    def test_pack_images_are_the_exact_stored_bytes(self) -> None:
        self.export()
        key = load_yaml(next((self.context.eval_dir / "review" / "keys").glob("*.yml")))
        for presentation_id, entry in key["items"].items():
            copied = next((self.out / "images").glob(f"{presentation_id}.*"))
            self.assertEqual(digest_file(copied), entry["image_sha256"])

    def test_refuses_non_empty_directory_and_nothing_owed(self) -> None:
        self.out.mkdir()
        (self.out / "x").write_text("x")
        with self.assertRaises(ExecutionError):
            self.export()
        (self.out / "x").unlink()
        self.export()
        self.fill()
        import_responses(self.context, self.out / "responses.yml")
        with self.assertRaises(ExecutionError) as caught:
            self.export(out=self.project.root / "again")
        self.assertIn("No images are owed review", str(caught.exception))
        self.export(out=self.project.root / "again-all", include_reviewed=True)

    def test_force_replaces_an_earlier_pack_but_only_a_pack(self) -> None:
        first = self.export(seed=1)
        (self.out / "stale-note.txt").write_text("old")
        with self.assertRaises(ExecutionError) as caught:
            self.export(seed=2)
        self.assertIn("--force", str(caught.exception))
        second = self.export(seed=2, force=True)
        self.assertNotEqual(first.pack_id, second.pack_id)
        self.assertFalse((self.out / "stale-note.txt").exists())
        self.assertEqual(load_yaml(self.out / "pack.yml")["pack_id"], second.pack_id)
        self.assertTrue(first.key_path.is_file())  # the old key is kept

        not_a_pack = self.project.root / "my-notes"
        not_a_pack.mkdir()
        (not_a_pack / "important.txt").write_text("keep me")
        with self.assertRaises(ExecutionError) as caught:
            self.export(out=not_a_pack, force=True)
        self.assertIn("does not look like a review pack", str(caught.exception))
        self.assertTrue((not_a_pack / "important.txt").exists())

    def test_failed_forced_export_keeps_the_earlier_pack(self) -> None:
        self.export()
        self.fill()
        import_responses(self.context, self.out / "responses.yml")  # nothing is owed any more
        with self.assertRaises(ExecutionError):
            self.export(force=True)
        self.assertTrue((self.out / "pack.yml").is_file())
        self.assertTrue((self.out / "images").is_dir())

    def test_embedded_generator_metadata_is_flagged(self) -> None:
        import io

        info = PngInfo()
        info.add_text("Software", "SomeImageModel 1.0")
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, format="PNG", pnginfo=info)
        self.assertEqual(_identifying_metadata(buffer.getvalue()), {"software"})
        self.assertEqual(_identifying_metadata(png_bytes()), set())


class ImportTests(ReviewBase):
    def test_round_trip_writes_immutable_reviews_with_computed_verdicts(self) -> None:
        self.export()
        pack = load_yaml(self.out / "pack.yml")
        failing = pack["items"][0]["presentation_id"]
        responses = self.fill(fail={failing: "single-product-composition"})
        result = import_responses(self.context, responses)
        self.assertEqual(len(result.written), 2)
        verdicts = {}
        for path in result.written:
            record = load_yaml(path)
            self.assertTrue(path.name.startswith("andrew--"))
            self.assertEqual(record["image_sha256"], digest_file(next(self.run_dir.glob(f"items/{record['item_id']}/output-*"))))
            verdicts[record["item_id"]] = record["verdict"]
        key = load_yaml(next((self.context.eval_dir / "review" / "keys").glob("*.yml")))
        self.assertEqual(sorted(verdicts.values()), ["fail", "pass"])
        self.assertEqual(verdicts[key["items"][failing]["item_id"]], "fail")

    def test_bad_responses_are_rejected_with_zero_partial_writes(self) -> None:
        self.export()
        path = self.fill()
        document = yaml.safe_load(path.read_text())
        good, bad = document["responses"]
        cases = {
            "unknown presentation_id": lambda: {**bad, "presentation_id": "img-deadbeef"},
            "missing dimension": lambda: {**bad, "dimensions": {"fictional-packaging": bad["dimensions"]["fictional-packaging"]}},
            "reason outside enum": lambda: {**bad, "dimensions": {**bad["dimensions"], "fictional-packaging": {"pass": False, "reason_code": "cropped"}}},
            "fail without reason": lambda: {**bad, "dimensions": {**bad["dimensions"], "fictional-packaging": {"pass": False, "reason_code": None}}},
            "pass with reason": lambda: {**bad, "dimensions": {**bad["dimensions"], "fictional-packaging": {"pass": True, "reason_code": "real-brand"}}},
            "non-boolean pass": lambda: {**bad, "dimensions": {**bad["dimensions"], "fictional-packaging": {"pass": "yes", "reason_code": None}}},
            "extra dimension": lambda: {**bad, "dimensions": {**bad["dimensions"], "invented": {"pass": True}}},
            "duplicate presentation_id": lambda: {**good},
        }
        for name, make in cases.items():
            broken = {**document, "responses": [good, make()]}
            path.write_text(yaml.safe_dump(broken, sort_keys=False))
            with self.assertRaises(ReviewImportError, msg=name):
                import_responses(self.context, path)
            self.assertEqual(list(self.run_dir.glob("items/*/reviews/*")), [], msg=name)

    def test_fail_without_a_chosen_reason_says_so_plainly(self) -> None:
        self.export()
        path = self.fill()
        document = yaml.safe_load(path.read_text())
        document["responses"][0]["dimensions"]["fictional-packaging"] = {"pass": False, "reason_code": None}
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(self.context, path)
        message = str(caught.exception)
        self.assertIn("marked fail but no reason was chosen", message)
        self.assertNotIn("None", message)

    def test_unknown_pack_and_missing_reviewer_are_rejected(self) -> None:
        self.export()
        path = self.fill(reviewer="")
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(self.context, path)
        self.assertIn("reviewer must be an ID", str(caught.exception))
        document = yaml.safe_load(path.read_text())
        document["pack_id"] = "pack-nope"
        path.write_text(yaml.safe_dump(document))
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(self.context, path)
        self.assertIn("unknown pack_id", str(caught.exception))

    def test_reviewer_cannot_path_traverse_through_their_id(self) -> None:
        self.export()
        path = self.fill(reviewer="../../evil")
        with self.assertRaises(ReviewImportError):
            import_responses(self.context, path)

    def test_blank_rows_stay_owed_and_do_not_error(self) -> None:
        self.export()
        path = self.fill()
        document = yaml.safe_load(path.read_text())
        for dimension in document["responses"][0]["dimensions"].values():
            dimension.update({"pass": None, "reason_code": None})
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        result = import_responses(self.context, path)
        self.assertEqual((len(result.written), result.skipped_blank), (1, 1))
        self.assertEqual(review_status(self.context)["awaiting_review"], 1)

    def test_partially_blank_row_is_an_error(self) -> None:
        self.export()
        path = self.fill()
        document = yaml.safe_load(path.read_text())
        document["responses"][0]["dimensions"]["fictional-packaging"] = {"pass": None, "reason_code": None}
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        with self.assertRaises(ReviewImportError):
            import_responses(self.context, path)

    def test_re_review_by_the_same_reviewer_adds_a_record_and_latest_wins(self) -> None:
        self.export()
        path = self.fill()
        import_responses(self.context, path)
        pid = load_yaml(path)["responses"][0]["presentation_id"]
        key = load_yaml(next((self.context.eval_dir / "review" / "keys").glob("*.yml")))
        item_id = key["items"][pid]["item_id"]
        self.fill(fail={pid: "fictional-packaging"})
        import_responses(self.context, path)
        records = load_reviews(self.item_dir(item_id))
        self.assertEqual(len(records), 2)
        rubric = load_rubric(self.project.rubric_path)
        current, stale = split_reviews(records, image_sha256=records[0]["image_sha256"], rubric_sha256=rubric.sha256)
        self.assertEqual((len(current), len(stale)), (1, 0))
        self.assertEqual(current[0]["verdict"], "fail")  # latest by timestamp is current

    def test_image_changed_after_export_blocks_import(self) -> None:
        self.export()
        path = self.fill()
        image = next(self.item_dir().glob("output-*"))
        image.write_bytes(png_bytes(color=(5, 5, 5)))
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(self.context, path)
        self.assertIn("stored image changed since export", str(caught.exception))
        self.assertEqual(list(self.run_dir.glob("items/*/reviews/*")), [])

    def test_rubric_changed_after_export_blocks_import(self) -> None:
        self.export()
        path = self.fill()
        self.project.rubric_path.write_text(self.project.rubric_path.read_text().replace("Exactly one", "Only one"))
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(load_eval_context(self.project.root, EVAL_ID), path)
        self.assertIn("rubric changed", str(caught.exception))

    def test_failure_midway_rolls_back_files_already_written(self) -> None:
        self.export()
        path = self.fill()
        calls = {"n": 0}

        def flaky(target, value):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            write_yaml_exclusive(target, value)

        with patch("tamesu.review.write_yaml_exclusive", side_effect=flaky):
            with self.assertRaises(OSError):
                import_responses(self.context, path)
        self.assertEqual(list(self.run_dir.glob("items/*/reviews/*")), [])


class StatusTests(ReviewBase):
    required = 2

    def test_required_reviews_per_item_is_honored_across_reviewers(self) -> None:
        self.export()
        import_responses(self.context, self.fill(reviewer="andrew"))
        status = review_status(self.context)
        self.assertEqual((status["reviewed"], status["awaiting_review"]), (0, 2))
        self.assertEqual({i["owed"] for i in status["items"]}, {1})
        second = self.project.root / "pack-b"
        self.export(out=second)
        import_responses(self.context, self.fill(out=second, reviewer="bea"))
        status = review_status(self.context)
        self.assertEqual((status["reviewed"], status["awaiting_review"]), (2, 0))

    def test_stale_records_are_reported_not_counted(self) -> None:
        self.export()
        import_responses(self.context, self.fill())
        self.project.rubric_path.write_text(self.project.rubric_path.read_text().replace("Exactly one", "Only one"))
        status = review_status(load_eval_context(self.project.root, EVAL_ID))
        self.assertEqual(status["stale_records"], 2)
        self.assertEqual(status["awaiting_review"], 2)


class AcceptanceLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ImageProject(review=True, judges=True)
        self.project.__enter__()
        self.addCleanup(self.project.__exit__, None, None, None)

    def error(self) -> str:
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(self.project.root, EVAL_ID)
        return str(caught.exception)

    def edit(self, old: str, new: str) -> None:
        self.project.eval_path.write_text(self.project.eval_path.read_text().replace(old, new))

    def test_valid_review_eval_lints(self) -> None:
        load_eval_context(self.project.root, EVAL_ID)

    def test_requires_human_without_a_human_review_block(self) -> None:
        self.edit("  human_review:\n    rubric: ../../rubrics/product-reference-quality.yml\n", "")
        self.assertIn("requires human review but eval.evaluation.human_review is not declared", self.error())

    def test_human_review_block_without_requiring_human(self) -> None:
        self.edit("requires: [mechanical, human]", "requires: [mechanical]")
        self.assertIn("does not include 'human'", self.error())

    def test_screen_judge_cannot_be_a_gate_or_sole_gate(self) -> None:
        self.edit("requires: [mechanical, human]", "requires: [mechanical, model_judge]")
        self.edit("      on_disagreement: adjudicate\n", "      on_disagreement: adjudicate\n    model_judge:\n      role: screen\n")
        message = self.error()
        self.assertIn("role must be 'gate' to appear in requires", message)

    def test_gate_role_must_be_required(self) -> None:
        self.edit("      on_disagreement: adjudicate\n", "      on_disagreement: adjudicate\n    model_judge:\n      role: gate\n")
        self.assertIn("role is 'gate' but model_judge is not in requires", self.error())

    def test_requires_model_judge_with_no_judges(self) -> None:
        plain = ImageProject()
        plain.__enter__()
        self.addCleanup(plain.__exit__, None, None, None)
        plain.eval_path.write_text(
            plain.eval_path.read_text().replace(
                "metrics:", "  acceptance:\n    requires: [model_judge]\n    model_judge: {role: gate}\nmetrics:"
            ).replace("    []\n", "    []\n")
        )
        with self.assertRaises(ConfigError) as caught:
            load_eval_context(plain.root, EVAL_ID)
        self.assertIn("no model_judges are declared", str(caught.exception))

    def test_bad_values(self) -> None:
        self.edit("on_disagreement: adjudicate", "on_disagreement: shrug")
        self.edit("selection: none", "selection: best_looking")
        message = self.error()
        self.assertIn("on_disagreement must be one of", message)
        self.assertIn("selection must be one of", message)

    def test_structured_text_evals_reject_review_blocks(self) -> None:
        # the task flag, not the YAML shape, decides support
        from tamesu.tasks import get_task

        self.assertFalse(get_task("structured_text").supports_review)
        self.assertTrue(get_task("image_generation").supports_review)

    def test_acceptance_is_part_of_run_identity_and_judges_are_not(self) -> None:
        from tamesu.planner import build_plan

        before = build_plan(load_eval_context(self.project.root, EVAL_ID)).specs[0].specification_fingerprint
        self.edit("      required_reviews_per_item: 1", "      required_reviews_per_item: 3")
        after = build_plan(load_eval_context(self.project.root, EVAL_ID)).specs[0].specification_fingerprint
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()


class OtherReasonTests(unittest.TestCase):
    def test_other_requires_an_explanation_in_the_notes(self) -> None:
        project = ImageProject(review=True)
        project.__enter__()
        self.addCleanup(project.__exit__, None, None, None)
        project.rubric_path.write_text(
            project.rubric_path.read_text().replace(
                "reason_codes: [multiple-products, cropped]", "reason_codes: [multiple-products, cropped, other]"
            )
        )
        context = load_eval_context(project.root, EVAL_ID)
        run_eval(context)
        out = project.root / "pack"
        export_pack(context, out)
        path = out / "responses.yml"
        document = yaml.safe_load(path.read_text())
        document["reviewer"] = "andrew"
        for response in document["responses"]:
            for outcome in response["dimensions"].values():
                outcome.update({"pass": True, "reason_code": None})
        document["responses"][0]["dimensions"]["single-product-composition"] = {"pass": False, "reason_code": "other"}
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        with self.assertRaises(ReviewImportError) as caught:
            import_responses(context, path)
        self.assertIn("'other' on single-product-composition needs an explanation", str(caught.exception))
        document["responses"][0]["notes"] = "image is a collage, not a photograph"
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        self.assertEqual(len(import_responses(context, path).written), 2)
