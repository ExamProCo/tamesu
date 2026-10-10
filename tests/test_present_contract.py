"""The `present` contract: deterministic, offline, read-only, total, and honest."""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from image_fixtures import EVAL_ID
from test_image_acceptance import BAD, GOOD, Harness
from test_inspect_backend import EVAL, Fixture

from tamesu import lifecycle as lc
from tamesu import present_blocks as pb
from tamesu import presenting
from tamesu.artifacts import write_yaml
from tamesu.config import load_yaml
from tamesu.errors import TamesuError
from tamesu.planner import build_plan
from tamesu.present_watch import snapshot, watch
from tamesu.presenters import present_artifact_bundle
from tamesu.presenting import build_case_data, present_case
from tamesu.reporting import status_summary
from tamesu.tasks import TASKS


def tree_hash(root: Path, skip: tuple[str, ...] = ("build",)) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in skip for part in path.relative_to(root).parts):
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def read_tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class BundleCase(Fixture):
    """A case with an Inspect-backed eval that has not run, plus an optional second eval."""

    def second_eval(self) -> Path:
        target = self.exp / "evals" / "e2"
        shutil.copytree(self.eval_dir, target)
        document = yaml.safe_load((target / "eval.yml").read_text())
        document["name"] = "e2"
        write_yaml(target / "eval.yml", document)
        return target

    def html(self, name: str = "exp") -> str:
        return (present_case(self.case, self.root / "out") / "experiments" / f"{name}.html").read_text()


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BundleCase(self)
        self.context = self.fixture.context()
        self.plan = build_plan(self.context)

    def state(self, counts: dict, *, status: str = "active", pending=(0, 0), closing: dict | None = None):
        context = SimpleNamespace(
            eval_id=self.context.eval_id,
            evaluation={**self.context.evaluation, "status": status},
            eval_dir=self.context.eval_dir,
        )
        full = {"planned": 3, "banked": 0, "owed": 3, "partial": 0, "failed": 0, "stale": 0, "extra": 0, **counts}
        if closing is not None:
            write_yaml(self.context.eval_dir / "closing.yml", closing)
        with patch.object(lc, "pending_evidence", return_value=pending):
            return lc.lifecycle_for(context, self.plan, full)

    def test_every_state_is_derived_from_facts(self) -> None:
        self.assertEqual(self.state({}, status="draft").state, lc.DRAFT)
        self.assertEqual(self.state({}).state, lc.READY)
        self.assertEqual(self.state({"banked": 1, "owed": 2}).state, lc.PARTIAL)
        self.assertEqual(self.state({"partial": 1}).state, lc.PARTIAL)
        self.assertEqual(self.state({"failed": 1, "owed": 3}).state, lc.PARTIAL)
        self.assertEqual(self.state({"banked": 3, "owed": 0}, pending=(2, 2)).state, lc.AWAITING_REVIEW)
        self.assertEqual(self.state({"banked": 3, "owed": 0}).state, lc.EVIDENCE_COMPLETE)
        self.assertEqual(self.state({"banked": 3, "owed": 0}, status="complete").state, lc.CLOSED)

    def test_banked_runs_alone_never_mean_complete(self) -> None:
        awaiting = self.state({"banked": 3, "owed": 0}, pending=(1, 1))
        self.assertNotEqual(awaiting.state, lc.EVIDENCE_COMPLETE)
        self.assertIn("1 item(s) await", awaiting.reason)

    def test_closing_with_a_gap_is_visible_in_the_badge_data(self) -> None:
        closed = self.state({"banked": 3, "owed": 0}, status="complete", pending=(2, 2), closing={"pending_items": 2})
        self.assertEqual(closed.state, lc.CLOSED)
        self.assertIn("closed with 2 item(s) still pending review", closed.modifiers[0])

    def test_stale_and_failed_runs_are_modifiers_not_states(self) -> None:
        state = self.state({"stale": 2, "failed": 1, "banked": 3, "owed": 0})
        self.assertEqual(state.state, lc.PARTIAL)
        self.assertTrue(any("2 stale" in m for m in state.modifiers))

    def test_summarize_and_invalid(self) -> None:
        self.assertEqual(lc.summarize([lc.INVALID, lc.AWAITING_REVIEW, lc.AWAITING_REVIEW]), "1 invalid, 2 awaiting review")
        self.assertEqual(lc.summarize([]), "no evals")
        self.assertEqual(lc.invalid(["a", "b"]).errors, ("a", "b"))


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BundleCase(self)

    def test_renders_identically_twice(self) -> None:
        first = read_tree(present_case(self.fixture.case, self.fixture.root / "a"))
        second = read_tree(present_case(self.fixture.case, self.fixture.root / "b"))
        self.assertEqual(first, second)

    def test_read_only_outside_its_output_and_never_creates_analysis(self) -> None:
        before = tree_hash(self.fixture.root, skip=("out",))
        present_case(self.fixture.case, self.fixture.root / "out")
        self.assertEqual(before, tree_hash(self.fixture.root, skip=("out",)))
        self.assertFalse((self.fixture.eval_dir / "analysis.md").exists())

    def test_offline_and_never_imports_project_python(self) -> None:
        (self.fixture.exp / "inspect" / "task.py").write_text("raise RuntimeError('task imported')\n")

        def boom(*args, **kwargs):
            raise AssertionError("present must not use the network or spawn processes")

        with patch.object(socket, "socket", boom), patch.object(subprocess, "Popen", boom), patch.object(subprocess, "run", boom):
            html = self.fixture.html()
        self.assertIn("Ready to run", html)

    def test_state_is_visible_before_anything_runs(self) -> None:
        html = self.fixture.html()
        for expected in (
            "Ready to run",
            "Run matrix",
            "muse-spark-1.2",
            "Prompts sent",
            "You are terse.",
            "say a",
            "Maximum exposure of the full design",
            "Nothing has run yet, so this eval has no result",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("leader", html.lower())
        data = json.loads((self.fixture.root / "out" / "data.json").read_text())
        design = data["experiments"][0]["evals"][0]["design"]
        self.assertEqual(design["matrix"][0]["owed"], 1)
        self.assertEqual(design["items"]["total"], 3)
        self.assertEqual(design["execution"]["backend"], "inspect")
        self.assertEqual(design["execution"]["limits"], {"cost_limit": 0.5, "time_limit": 60})

    def test_draft_is_labelled_with_how_to_proceed(self) -> None:
        self.fixture.write_eval({"status": "draft"})
        html = self.fixture.html()
        self.assertIn("Draft", html)
        self.assertIn("tamesu activate", html)
        self.assertIn("eval status is &#x27;draft&#x27;", html)

    def test_a_template_error_is_shown_on_the_page_without_the_local_path(self) -> None:
        (self.fixture.exp / "prompts" / "user.md.j2").write_text("{{ item.nope }}\n")
        html = self.fixture.html()
        self.assertIn("Unknown template value", html)
        self.assertIn("item.nope", html)
        self.assertNotIn(str(self.fixture.root), html)  # relative to the project, never absolute
        self.assertIn("prompts/user.md.j2", html)

    def test_an_arm_that_cannot_render_is_reported_in_the_preview(self) -> None:
        from tamesu.present_design import _prompt_preview

        context = self.fixture.context()
        plan = build_plan(context)

        class Broken:
            def render_prompts(self, prompts, item):
                raise ValueError(f"bad template for {item['id']}")

        preview = _prompt_preview(context, plan, Broken())
        self.assertEqual(preview[0]["error"], "ValueError: bad template for item-a")
        self.assertNotIn("prompts", preview[0])

    def test_prompt_preview_is_the_exact_text_for_the_first_item(self) -> None:
        present_case(self.fixture.case, self.fixture.root / "out")
        data = json.loads((self.fixture.root / "out" / "data.json").read_text())
        preview = data["experiments"][0]["evals"][0]["design"]["prompt_preview"][0]
        self.assertEqual(preview["item_id"], "item-a")
        self.assertEqual(preview["prompts"]["user"], "say a\n")

    def test_missing_credentials_are_named_never_valued(self) -> None:
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("META_API_KEY", None)
            present_case(self.fixture.case, self.fixture.root / "out")
        data = json.loads((self.fixture.root / "out" / "data.json").read_text())
        blockers = data["experiments"][0]["evals"][0]["design"]["blockers"]
        self.assertTrue(any("META_API_KEY is not set" in b for b in blockers))

    def test_untrusted_origin_is_a_blocker(self) -> None:
        (self.fixture.case / ".untrusted-origin").write_text("x")
        present_case(self.fixture.case, self.fixture.root / "out")
        blockers = json.loads((self.fixture.root / "out" / "data.json").read_text())["experiments"][0]["evals"][0]["design"]["blockers"]
        self.assertTrue(any("--trust-code" in b for b in blockers))


class ResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BundleCase(self)
        self.fixture.second_eval()
        document = yaml.safe_load((self.fixture.eval_dir / "eval.yml").read_text())
        del document["metrics"]
        document["execution"]["limits"].pop("cost_limit")
        write_yaml(self.fixture.eval_dir / "eval.yml", document)

    def test_an_invalid_eval_becomes_a_card_and_the_rest_still_renders(self) -> None:
        html = self.fixture.html()
        self.assertIn("Invalid", html)
        self.assertIn("Problems to fix", html)
        self.assertIn("limits.cost_limit is required", html)
        self.assertIn("eval.metrics must be a mapping", html)
        self.assertIn("Run matrix", html)  # the other eval has its full design
        index = (self.fixture.root / "out" / "index.html").read_text()
        self.assertIn("1 invalid, 1 ready to run", index)

    def test_strict_writes_the_page_then_fails(self) -> None:
        with self.assertRaises(TamesuError) as caught:
            present_case(self.fixture.case, self.fixture.root / "out", strict=True)
        self.assertIn("1 eval(s) are invalid", str(caught.exception))
        self.assertTrue((self.fixture.root / "out" / "index.html").is_file())

    def test_internal_errors_are_not_blamed_on_the_manifest(self) -> None:
        document = yaml.safe_load((self.fixture.eval_dir / "eval.yml").read_text())
        document["metrics"] = {"primary": "completion_rate"}
        document["execution"]["limits"]["cost_limit"] = 0.5
        write_yaml(self.fixture.eval_dir / "eval.yml", document)
        real = presenting._eval_data

        def broken(context, *args, **kwargs):
            if context.eval_dir.name == "e":
                raise RuntimeError("renderer bug")
            return real(context, *args, **kwargs)

        with patch.object(presenting, "_eval_data", broken):
            html = self.fixture.html()
        self.assertIn("internal error while building this page: RuntimeError: renderer bug", html)
        self.assertIn("Please report it", html)
        self.assertIn("Run matrix", html)

    def test_a_broken_case_yml_still_refuses(self) -> None:
        manifest = yaml.safe_load((self.fixture.case / "case.yml").read_text())
        del manifest["technical_uncertainty"]
        write_yaml(self.fixture.case / "case.yml", manifest)
        with self.assertRaisesRegex(TamesuError, "technical_uncertainty"):
            present_case(self.fixture.case, self.fixture.root / "out")


class HonestyTests(unittest.TestCase):
    def test_no_leader_while_review_is_pending_and_status_says_awaiting(self) -> None:
        harness = Harness(self, review=True)
        data = build_case_data(harness.context.case_dir)["experiments"][0]["evals"][0]
        self.assertEqual(data["status"], lc.AWAITING_REVIEW)
        self.assertIsNone(data["leader"])
        self.assertIn("Review is incomplete", data["observed_outcome"]["summary"])
        harness.review({GOOD: True, BAD: False})
        decided = build_case_data(harness.context.case_dir)["experiments"][0]["evals"][0]
        self.assertEqual(decided["status"], lc.EVIDENCE_COMPLETE)

    def test_three_rows_are_a_range_and_single_samples_are_not_comparisons(self) -> None:
        rows = [
            {"model": m, "arm": "a", "primary_mean": v, "items": 1, "repetitions": 1}
            for m, v in (("x", 0.2), ("y", 1.0), ("z", 1.0))
        ]
        outcome = presenting._observed_outcome(rows, "mean_score")
        self.assertIn("Across 3 rows, mean_score ranges from 0.2000 (x / a) to 1.0000", outcome["summary"])
        self.assertIn("not a comparison", outcome["caveat"])

    def test_status_and_report_use_the_same_state_words(self) -> None:
        import io
        from contextlib import redirect_stdout

        from tamesu.cli import command_status
        from tamesu.reporting import build_evaluation_report

        harness = Harness(self, review=True)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command_status(harness.project.root, EVAL_ID)
        self.assertIn("State: Awaiting review", buffer.getvalue())
        report = build_evaluation_report(build_plan(harness.context)).read_text()
        self.assertIn("- State: **Awaiting review**", report)


class BundlePresenterTests(unittest.TestCase):
    """Model-controlled logs and source are hostile input."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.eval_dir = self.root / "cases" / "c" / "experiments" / "x" / "evals" / "e"
        self.item_dir = self.eval_dir / "runs" / "run-1" / "items" / "item-a"
        (self.item_dir / "artifacts" / "source").mkdir(parents=True)

    def ctx(self, *, compiler: str, include: bool = True, result: dict | None = None):
        (self.item_dir / "artifacts" / "compiler.txt").write_text(compiler)
        (self.item_dir / "artifacts" / "source" / "main.c").write_text("int main(){}")
        base = "runs/run-1/items/item-a/artifacts/"
        document = {
            "state": "complete",
            "mechanical_scores": {"build": {"value": 0.2, "pass": False, "answer": "exported"}},
            "artifacts": [
                {"role": "compiler-log", "path": base + "compiler.txt", "media_type": "text/plain", "size": len(compiler)},
                {"role": "source", "path": base + "source/main.c", "media_type": "text/x-c", "size": 12},
            ],
            "generation": {"usage": {"input_tokens": 1, "output_tokens": 2}},
            "inspect": {"stop_reason": "stop"},
        }
        document.update(result or {})
        return pb.ItemContext(
            eval_dir=self.eval_dir,
            case_dir=self.root / "cases" / "c",
            item_dir=self.item_dir,
            run_id="run-1",
            result=document,
            item={"input": {"prompt": "p"}},
            report_entry={},
            include_text_artifacts=include,
        )

    HOSTILE = "<script>alert(1)</script>\x1b[31mred\x1b[0m\x00 javascript:alert(2)\nmain.c:1: error: <img src=x onerror=y>\n"

    def test_hostile_logs_are_escaped_stripped_and_defanged(self) -> None:
        view = present_artifact_bundle(self.ctx(compiler=self.HOSTILE))
        html = pb.render_item_view(view.as_dict())
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("\x1b", html)
        self.assertNotIn("\x00", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("first error: main.c:1: error:", view.headline)
        row = presenting._item_evidence_html([{"item_id": "item-a", "state": "complete", "view": view.as_dict()}])
        self.assertNotIn("<img src=x", row)  # the headline is data, escaped where it is rendered
        self.assertIn("&lt;img src=x onerror=y&gt;", row)

    def test_huge_logs_are_capped_with_the_middle_omitted(self) -> None:
        text = "\n".join(f"line {i}" for i in range(5000))
        html = pb.render_item_view(present_artifact_bundle(self.ctx(compiler=text)).as_dict())
        self.assertIn("line 0", html)
        self.assertIn("line 4999", html)
        self.assertNotIn("line 2500", html)
        self.assertIn("line(s) omitted", html)

    def test_copied_files_are_text_assets_under_assets_only(self) -> None:
        view = present_artifact_bundle(self.ctx(compiler="x"))
        for entry in view.copy:
            self.assertTrue(entry["target"].startswith("assets/artifacts/run-1/item-a/"))
            self.assertNotIn("..", entry["target"])
            self.assertTrue(entry["target"].endswith(".txt"))
            self.assertTrue(entry.get("max_bytes"))

    def test_unsafe_artifact_names_are_never_copied(self) -> None:
        context = self.ctx(compiler="x")
        context.result["artifacts"].append(
            {"role": "source", "path": "runs/run-1/items/item-a/artifacts/../../../../etc/passwd", "media_type": "text/plain", "size": 1}
        )
        for entry in present_artifact_bundle(context).copy:
            self.assertNotIn("passwd", entry["target"])

    def test_withheld_for_public_sites(self) -> None:
        view = present_artifact_bundle(self.ctx(compiler="secret path /Users/me", include=False))
        html = pb.render_item_view(view.as_dict())
        self.assertNotIn("/Users/me", html)
        self.assertIn("withheld from this rendering", html)
        self.assertEqual([c for c in view.copy if c["target"].endswith(".txt")], [])

    def test_empty_log_is_labelled_not_blank(self) -> None:
        html = pb.render_item_view(present_artifact_bundle(self.ctx(compiler="")).as_dict())
        self.assertIn("empty: the program wrote nothing here", html)

    def test_failed_item_headline_is_the_error(self) -> None:
        view = present_artifact_bundle(self.ctx(compiler="", result={"state": "failed", "error": {"message": "sandbox died"}}))
        self.assertEqual(view.headline, "did not complete: sandbox died")

    def test_copy_assets_refuses_escaping_paths(self) -> None:
        case = self.root / "cases" / "c"
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        data = {"experiments": [{"evals": [{"items": [{"copy": [
            {"source": str(outside), "target": "assets/x.txt"},
            {"source": str(self.item_dir / "artifacts" / "compiler.txt"), "target": "../escape.txt"},
        ]}]}]}]}
        (self.item_dir / "artifacts" / "compiler.txt").write_text("fine")
        out = self.root / "site"
        out.mkdir()
        presenting._copy_assets(case, data, out)
        self.assertFalse((out / "assets" / "x.txt").exists())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_sanitize_text(self) -> None:
        self.assertEqual(pb.sanitize_text("a\x1b[31mb\x1b[0m\x00c\r\nd"), "abc\nd")
        self.assertIn("[secret-redacted]", pb.sanitize_text("api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789"))


class IdentityIsolationTests(unittest.TestCase):
    """Editing how a page looks must never invalidate banked evidence (it once did)."""

    def test_identity_hashed_files_contain_no_presentation_code(self) -> None:
        for name, task in TASKS.items():
            for path in task.code_files():
                text = path.read_text(encoding="utf-8")
                for forbidden in ("present_item", "present_blocks", "ItemView", "presenters"):
                    self.assertNotIn(forbidden, text, f"{path.name} (task {name}) must not hold presentation code")

    def test_presenters_are_not_part_of_any_identity(self) -> None:
        hashed = {p.name for task in TASKS.values() for p in task.code_files()}
        self.assertFalse(hashed & {"presenters.py", "present_blocks.py", "presenting.py", "present_design.py"})


class WatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_snapshot_ignores_build_output_and_inspect_transcripts(self) -> None:
        for relative in (
            "case.yml",
            "build/present/index.html",
            "experiments/x/evals/e/eval.yml",
            "experiments/x/evals/e/runs/r1/inspect/attempt-01/log.eval",
            "experiments/x/evals/e/runs/r1/run.yml",
            "experiments/x/inspect/staging/f.txt",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        names = set(snapshot(self.root))
        self.assertEqual(
            names,
            {"case.yml", "experiments/x/evals/e/eval.yml", "experiments/x/evals/e/runs/r1/run.yml"},
        )

    def test_rerenders_after_a_change_and_survives_a_bad_render(self) -> None:
        (self.root / "case.yml").write_text("a")
        calls = {"n": 0, "sleeps": 0}
        messages: list[str] = []

        def render() -> Path:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("bad edit")
            return self.root / "build"

        def sleep(seconds: float) -> None:
            calls["sleeps"] += 1
            if calls["sleeps"] == 1:  # the author saves a file between polls
                (self.root / "case.yml").write_text("b-changed")

        renders = watch(self.root, render, sleep=sleep, emit=messages.append, max_renders=2)
        self.assertEqual(renders, 2)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(messages[0].startswith("Rendered"))
        self.assertEqual(messages[1], "error: bad edit")


class CliTests(unittest.TestCase):
    def test_strict_and_open_flags(self) -> None:
        from tamesu import cli

        fixture = BundleCase(self)
        fixture.second_eval()
        document = yaml.safe_load((fixture.eval_dir / "eval.yml").read_text())
        del document["metrics"]
        write_yaml(fixture.eval_dir / "eval.yml", document)
        args = Namespace(case="bundle-case", output=fixture.root / "out", open=False, watch=False, serve=False, port=0, strict=True)
        with self.assertRaises(TamesuError):
            cli.command_present(fixture.root, args)
        args.strict = False
        args.open = True
        with patch("webbrowser.open") as opened:
            self.assertEqual(cli.command_present(fixture.root, args), 0)
        self.assertTrue(opened.call_args[0][0].endswith("/out/index.html"))

    def test_serve_returns_the_rendered_page(self) -> None:
        import urllib.request

        from tamesu.present_watch import serve

        fixture = BundleCase(self)
        destination = present_case(fixture.case, fixture.root / "out")
        server = serve(destination, 0)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
            with urllib.request.urlopen(url) as response:
                self.assertEqual(response.read(), (destination / "index.html").read_bytes())
            with self.assertRaises(TamesuError):
                serve(destination, server.server_address[1])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
