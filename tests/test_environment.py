from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tamesu.environment import load_environment, parse_dotenv
from tamesu.errors import ConfigError


class EnvironmentTests(unittest.TestCase):
    def test_layer_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            override = root / "override"
            (home / ".tamesu").mkdir(parents=True)
            (project / ".tamesu").mkdir(parents=True)
            override.mkdir()
            (home / ".tamesu/.env").write_text(
                "META_API_KEY=global\nGLOBAL_ONLY=yes\n", encoding="utf-8"
            )
            (project / ".env").write_text(
                "META_API_KEY=project-root\nROOT_ONLY=yes\n", encoding="utf-8"
            )
            (project / ".tamesu/.env").write_text(
                "META_API_KEY=project-tamesu\nPROJECT_ONLY=yes\n", encoding="utf-8"
            )
            (override / ".env").write_text(
                "META_API_KEY=override\nOVERRIDE_ONLY=yes\n", encoding="utf-8"
            )
            environment = {"META_API_KEY": "process", "PROCESS_ONLY": "yes"}

            result = load_environment(
                project,
                config_dir=override,
                environ=environment,
                home=home,
            )

            self.assertEqual(environment["META_API_KEY"], "override")
            self.assertEqual(environment["GLOBAL_ONLY"], "yes")
            self.assertEqual(environment["ROOT_ONLY"], "yes")
            self.assertEqual(environment["PROJECT_ONLY"], "yes")
            self.assertEqual(environment["PROCESS_ONLY"], "yes")
            self.assertEqual(environment["OVERRIDE_ONLY"], "yes")
            self.assertEqual(len(result.sources), 4)
            self.assertIn("META_API_KEY", result.loaded_keys)

    def test_process_environment_overrides_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            project.mkdir()
            (project / ".env").write_text("META_API_KEY=file\n", encoding="utf-8")
            environment = {"META_API_KEY": "process"}

            load_environment(project, environ=environment, home=home)

            self.assertEqual(environment["META_API_KEY"], "process")

    def test_nested_project_inherits_git_workspace_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            workspace = root / "workspace"
            project = workspace / "examples" / "nested-project"
            home.mkdir()
            (workspace / ".git").mkdir(parents=True)
            project.mkdir(parents=True)
            (workspace / ".env").write_text(
                "META_API_KEY=workspace\nWORKSPACE_ONLY=yes\n", encoding="utf-8"
            )

            environment: dict[str, str] = {}
            load_environment(project, environ=environment, home=home)

            self.assertEqual(environment["META_API_KEY"], "workspace")
            self.assertEqual(environment["WORKSPACE_ONLY"], "yes")

            (project / ".env").write_text("META_API_KEY=project\n", encoding="utf-8")
            environment = {}
            load_environment(project, environ=environment, home=home)
            self.assertEqual(environment["META_API_KEY"], "project")

    def test_dotenv_quotes_exports_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(
                "export PLAIN=value # comment\n"
                "SINGLE='value with spaces'\n"
                'DOUBLE="line\\nvalue"\n'
                "EMPTY=\n"
                "URL=https://example.test/path#fragment\n",
                encoding="utf-8",
            )

            parsed = parse_dotenv(path)

            self.assertEqual(parsed["PLAIN"], "value")
            self.assertEqual(parsed["SINGLE"], "value with spaces")
            self.assertEqual(parsed["DOUBLE"], "line\nvalue")
            self.assertEqual(parsed["EMPTY"], "")
            self.assertEqual(parsed["URL"], "https://example.test/path#fragment")

    def test_explicit_directory_requires_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            override = root / "override"
            override.mkdir()
            with self.assertRaisesRegex(ConfigError, "has no .env file"):
                load_environment(None, config_dir=override, environ={}, home=root)


if __name__ == "__main__":
    unittest.main()
