"""The short path: `new` scaffolds a valid tree, and omitted fields take their defaults."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tamesu.config import apply_eval_defaults, load_eval_context
from tamesu.errors import TamesuError
from tamesu.scaffold import create


class ScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "cases").mkdir()

    def test_new_eval_creates_a_valid_active_tree(self) -> None:
        created = create(self.root, "eval", "my-case/first/baseline")
        self.assertIn(self.root / "cases/my-case/case.yml", created)
        context = load_eval_context(self.root, "my-case/first/baseline")
        self.assertEqual(context.evaluation["status"], "active")

    def test_new_never_overwrites_and_checks_the_shape(self) -> None:
        create(self.root, "eval", "my-case/first/baseline")
        case_file = self.root / "cases/my-case/case.yml"
        case_file.write_text(case_file.read_text() + "# mine\n")
        create(self.root, "eval", "my-case/first/second")
        self.assertTrue(case_file.read_text().endswith("# mine\n"))
        with self.assertRaisesRegex(TamesuError, "already exists"):
            create(self.root, "eval", "my-case/first/baseline")
        with self.assertRaisesRegex(TamesuError, "<case>/<experiment>"):
            create(self.root, "experiment", "my-case")
        with self.assertRaisesRegex(TamesuError, "kebab-case"):
            create(self.root, "case", "My_Case")


class EvalDefaultsTests(unittest.TestCase):
    def test_inspect_sources_default_to_the_task_and_its_neighbours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir = root / "exp" / "inspect"
            eval_dir = root / "exp" / "evals" / "e"
            task_dir.mkdir(parents=True)
            eval_dir.mkdir(parents=True)
            for name in ("task.py", "compose.yaml", "helper.py", "notes.md"):
                (task_dir / name).write_text("x")
            evaluation = {"execution": {"backend": "inspect", "file": "../../inspect/task.py"}}
            apply_eval_defaults(evaluation, eval_dir, root)
            self.assertEqual(
                evaluation["execution"]["sources"],
                ["../../inspect/task.py", "../../inspect/compose.yaml", "../../inspect/helper.py"],
            )

    def test_written_fields_are_kept(self) -> None:
        evaluation = {"status": "draft", "execution": {"backend": "inspect", "sources": ["a.py"]}}
        apply_eval_defaults(evaluation, Path("."), Path("."))
        self.assertEqual(evaluation["status"], "draft")
        self.assertEqual(evaluation["execution"]["sources"], ["a.py"])


if __name__ == "__main__":
    unittest.main()
