from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tamesu.discovery import resolve_eval_ref
from tamesu.errors import ConfigError


class ResolveEvalRefTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for eval_id in ("case/exp-a/executable", "case/exp-a/single-shot", "case/exp-b/single-shot"):
            case, experiment, name = eval_id.split("/")
            path = self.root / "cases" / case / "experiments" / experiment / "evals" / name / "eval.yml"
            path.parent.mkdir(parents=True)
            path.write_text("name: x\n")

    def test_unique_short_forms_expand_to_the_full_id(self) -> None:
        self.assertEqual(resolve_eval_ref(self.root, "executable"), "case/exp-a/executable")
        self.assertEqual(resolve_eval_ref(self.root, "exp-b/single-shot"), "case/exp-b/single-shot")

    def test_full_ids_pass_through_unchanged(self) -> None:
        self.assertEqual(resolve_eval_ref(self.root, "case/exp-a/executable"), "case/exp-a/executable")

    def test_ambiguous_and_missing_references_fail_with_the_choices(self) -> None:
        with self.assertRaisesRegex(ConfigError, "case/exp-a/single-shot, case/exp-b/single-shot"):
            resolve_eval_ref(self.root, "single-shot")
        with self.assertRaisesRegex(ConfigError, "Eval not found: nope"):
            resolve_eval_ref(self.root, "nope")


if __name__ == "__main__":
    unittest.main()
