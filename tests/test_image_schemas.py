from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from image_fixtures import EVAL_ID, ImageProject
from test_image_acceptance import BAD, GOOD, Harness

from tamesu.judging import judge_eval
from tamesu.tasks.structured_text import validate_json_schema

SCHEMAS = Path(__file__).parents[1] / "schemas"


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


class StoredEvidenceMatchesSchemas(unittest.TestCase):
    """Coarse structural check (the in-repo validator ignores pattern/oneOf/const)."""

    def test_every_schema_is_valid_json(self) -> None:
        for path in SCHEMAS.glob("*.json"):
            json.loads(path.read_text())

    def test_eval_rubric_judgment_review_and_report_conform(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        harness.review({GOOD: True, BAD: False})
        eval_doc = yaml.safe_load(harness.project.eval_path.read_text())
        self.assertEqual(validate_json_schema(eval_doc, schema("eval.schema.json")), [])
        rubric = yaml.safe_load(harness.project.rubric_path.read_text())
        self.assertEqual(validate_json_schema(rubric, schema("rubric.schema.json")), [])
        judgment = yaml.safe_load(next(harness.run_dir.glob("items/*/judgments/*.yml")).read_text())
        self.assertEqual(validate_json_schema(judgment, schema("judgment.schema.json")), [])
        review = yaml.safe_load(next(harness.run_dir.glob("items/*/reviews/*.yml")).read_text())
        self.assertEqual(validate_json_schema(review, schema("review.schema.json")), [])
        report = yaml.safe_load((harness.run_dir / "report.yml").read_text())
        self.assertEqual(validate_json_schema(report, schema("report.schema.json")), [])

    def test_log_events_conform_including_judge_stage(self) -> None:
        harness = Harness(self, review=True, judges=True, judge_role="screen")
        judge_eval(harness.context)
        log = harness.context.eval_dir / "logs" / f"{harness.run_id}.jsonl"
        for line in log.read_text().splitlines():
            self.assertEqual(validate_json_schema(json.loads(line), schema("log-event.schema.json")), [], line)


if __name__ == "__main__":
    unittest.main()
