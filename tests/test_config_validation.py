from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tamesu.config import load_eval_context
from tamesu.errors import ConfigError


EVAL_ID = "support-ticket-triage/decision-rules/prompt-ablation"


class ModelValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        source = Path(__file__).parents[1] / "examples" / "support-ticket-classification"
        self.project = Path(self.temporary.name) / "project"
        shutil.copytree(source, self.project)
        self.eval_path = (
            self.project
            / "cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def replace(self, old: str, new: str) -> None:
        source = self.eval_path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        self.eval_path.write_text(source.replace(old, new), encoding="utf-8")

    def test_known_model_provider_mismatch_fails(self) -> None:
        self.replace("provider: meta", "provider: anthropic")
        with self.assertRaisesRegex(ConfigError, "belongs to provider 'meta'"):
            load_eval_context(self.project, EVAL_ID)

    def test_invalid_known_effort_fails(self) -> None:
        self.replace("effort: minimal", "effort: impossible")
        with self.assertRaisesRegex(ConfigError, "does not accept effort 'impossible'"):
            load_eval_context(self.project, EVAL_ID)

    def test_unknown_model_with_explicit_provider_is_allowed(self) -> None:
        self.replace("model: muse-spark-1.2", "model: private-preview-model")
        context = load_eval_context(self.project, EVAL_ID)
        self.assertEqual(context.evaluation["runs"][0]["model"], "private-preview-model")


if __name__ == "__main__":
    unittest.main()
