from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from tamesu.artifacts import write_yaml
from tamesu.backends import BackendRun
from tamesu.backends import inspect_backend as ib
from tamesu.config import load_eval_context
from tamesu.errors import ConfigError, ExecutionError
from tamesu.identity import digest_file
from tamesu.logging import CallLogWriter
from tamesu.packaging import collect_payload
from tamesu.planner import build_plan
from tamesu.runner import resume_run, run_eval
from tamesu.tasks.structured_text import validate_json_schema

INSPECT = importlib.util.find_spec("inspect_ai") is not None
EVAL_ID = "bundle-case/exp/e"
SCHEMAS = Path(__file__).parents[1] / "schemas"

TASK_PY = textwrap.dedent(
    '''
    import os
    from inspect_ai import Task, task
    from inspect_ai.dataset import json_dataset
    from inspect_ai.scorer import Score, accuracy, scorer
    from inspect_ai.solver import generate
    from tamesu.inspect_support import stager


    @task
    def bundle(dataset: str) -> Task:
        return Task(dataset=json_dataset(dataset), solver=[generate()], scorer=check())


    @scorer(metrics=[accuracy()])
    def check():
        async def score(state, target):
            if os.environ.get("BUNDLE_MODE") == "boom" and state.sample_id == "item-b":
                raise RuntimeError("scorer exploded")
            stage = stager(state.sample_id)
            stage.add("report", "out/report.txt", state.output.completion, "text/plain")
            stage.add("secret", "x.txt", "undeclared", "text/plain")
            return Score(value=1.0, explanation="ok")
        return score
    '''
)

EVAL = {
    "schema_version": 1,
    "name": "e",
    "status": "active",
    "question": "Does the bundle round-trip?",
    "description": "Fixture eval for the Inspect backend.",
    "task": "artifact_bundle",
    "dataset": "../../../../datasets/ds/dataset.yml",
    "execution": {
        "backend": "inspect",
        "file": "../../inspect/task.py",
        "task": "bundle",
        "sources": ["../../inspect/task.py"],
        "limits": {"cost_limit": 0.5, "time_limit": 60},
        "artifacts": {"required": ["report"], "allowed": []},
    },
    "defaults": {
        "repetitions": 1,
        "concurrency": 2,
        "timeout_seconds": 60,
        "retries": 0,
        "budget_usd": 5,
        "parameters": {"max_tokens": 200},
    },
    "arms": [
        {
            "id": "plain",
            "description": "One arm.",
            "prompts": {"system": "../../prompts/system.md", "user": "../../prompts/user.md.j2"},
        }
    ],
    "runs": [{"model": "muse-spark-1.2", "provider": "meta", "arms": ["plain"]}],
    "evaluation": {"mechanical": ["check"]},
    "metrics": {"primary": "mean_score:check", "secondary": ["completion_rate", "artifact_complete_rate"]},
}


class Fixture:
    def __init__(self, test: unittest.TestCase) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.case = self.root / "cases" / "bundle-case"
        self.exp = self.case / "experiments" / "exp"
        self.eval_dir = self.exp / "evals" / "e"
        for directory in (self.case / "datasets" / "ds", self.exp / "inspect", self.exp / "prompts", self.eval_dir):
            directory.mkdir(parents=True)
        write_yaml(
            self.case / "case.yml",
            {
                "schema_version": 1,
                "name": "bundle-case",
                "title": "Bundle",
                "description": "d",
                "business_use": "b",
                "current_problem": "p",
                "technical_uncertainty": "t",
            },
        )
        write_yaml(
            self.case / "datasets" / "ds" / "dataset.yml",
            {
                "schema_version": 1,
                "name": "ds",
                "description": "three items",
                "items": [
                    {"id": f"item-{x}", "input": {"prompt": f"say {x}"}, "expected": "", "metadata": {"k": x}}
                    for x in "abc"
                ],
            },
        )
        (self.exp / "inspect" / "task.py").write_text(TASK_PY)
        (self.exp / "prompts" / "system.md").write_text("You are terse.\n")
        (self.exp / "prompts" / "user.md.j2").write_text("{{ item.input.prompt }}\n")
        self.write_eval({})

    def write_eval(self, changes: dict) -> None:
        document = json.loads(json.dumps(EVAL))
        for key, value in changes.items():
            document[key] = value
        write_yaml(self.eval_dir / "eval.yml", document)

    def context(self):
        return load_eval_context(self.root, EVAL_ID)


class Mocked:
    """Route the Meta provider to Inspect's mock model and satisfy credential checks."""

    def __enter__(self):
        self.patches = [
            patch.dict(ib.ROUTES, {"meta": ib.Route("mockllm", None, None)}),
            patch.dict(os.environ, {"META_API_KEY": "test-key"}),
        ]
        for item in self.patches:
            item.__enter__()
        return self

    def __exit__(self, *exc):
        for item in reversed(self.patches):
            item.__exit__(*exc)


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(self)

    def lint_error(self, **changes) -> str:
        self.fixture.write_eval(changes)
        with self.assertRaises(ConfigError) as caught:
            self.fixture.context()
        return str(caught.exception)

    def test_fixture_is_valid_and_conforms_to_schema(self) -> None:
        context = self.fixture.context()
        schema = json.loads((SCHEMAS / "eval.schema.json").read_text())
        self.assertEqual(validate_json_schema(context.evaluation, schema), [])

    def execution(self, **changes) -> dict:
        block = json.loads(json.dumps(EVAL["execution"]))
        block.update(changes)
        return block

    def test_cost_and_time_limits_are_required(self) -> None:
        message = self.lint_error(execution=self.execution(limits={"token_limit": 10}))
        self.assertIn("limits.cost_limit is required", message)
        self.assertIn("limits.time_limit is required", message)

    def test_undeclared_execution_file_is_rejected(self) -> None:
        (self.fixture.exp / "inspect" / "helper.py").write_text("X = 1\n")
        self.assertIn("sources is missing", self.lint_error())

    def test_staging_and_hidden_files_do_not_need_declaring(self) -> None:
        (self.fixture.exp / "inspect" / "staging").mkdir()
        (self.fixture.exp / "inspect" / "staging" / "leftover.py").write_text("X = 1\n")
        (self.fixture.exp / "inspect" / ".hidden.py").write_text("X = 1\n")
        self.fixture.write_eval({})
        self.fixture.context()

    def test_mutable_image_tag_is_rejected_unless_allowed(self) -> None:
        compose = self.fixture.exp / "inspect" / "compose.yaml"
        compose.write_text("services:\n  default:\n    image: gcc:14\n")
        sources = ["../../inspect/task.py", "../../inspect/compose.yaml"]
        self.assertIn("mutable tag", self.lint_error(execution=self.execution(sources=sources)))
        self.fixture.write_eval({"execution": self.execution(sources=sources, allow_mutable_images=True)})
        self.fixture.context()
        compose.write_text("services:\n  default:\n    image: gcc@sha256:" + "a" * 64 + "\n")
        self.fixture.write_eval({"execution": self.execution(sources=sources)})
        self.fixture.context()

    def test_dockerfile_from_must_be_pinned(self) -> None:
        dockerfile = self.fixture.exp / "inspect" / "Dockerfile"
        dockerfile.write_text("FROM ubuntu:24.04 AS build\nFROM build\n")
        sources = ["../../inspect/task.py", "../../inspect/Dockerfile"]
        message = self.lint_error(execution=self.execution(sources=sources))
        self.assertEqual(message.count("mutable tag"), 1)  # the `FROM build` stage alias is fine

    def test_reserved_dataset_argument_and_unknown_fields(self) -> None:
        self.assertIn("reserved", self.lint_error(execution=self.execution(task_args={"dataset": "x"})))
        self.assertIn("not a recognized field", self.lint_error(execution=self.execution(retries=3)))

    def test_provider_without_inspect_route_is_rejected(self) -> None:
        runs = [{"model": "grok-4.1", "provider": "grok", "arms": ["plain"]}]
        self.assertIn("no Inspect route", self.lint_error(runs=runs))

    def test_unpriced_model_is_rejected_because_cost_limit_needs_prices(self) -> None:
        runs = [{"model": "muse-spark-1.2-contributor", "provider": "meta", "arms": ["plain"]}]
        self.assertIn("needs registered pricing", self.lint_error(runs=runs))

    def test_native_tasks_reject_the_inspect_backend_and_vice_versa(self) -> None:
        message = self.lint_error(task="structured_text")
        self.assertIn("does not support the 'inspect' execution backend", message)
        block = self.execution()
        self.fixture.write_eval({})
        document = yaml.safe_load((self.fixture.eval_dir / "eval.yml").read_text())
        del document["execution"]
        write_yaml(self.fixture.eval_dir / "eval.yml", document)
        with self.assertRaises(ConfigError) as caught:
            self.fixture.context()
        self.assertIn("does not support the 'native' execution backend", str(caught.exception))
        self.assertTrue(block)

    def test_metric_must_name_a_listed_scorer(self) -> None:
        message = self.lint_error(metrics={"primary": "mean_score:other"})
        self.assertIn("names a scorer not listed", message)

    def test_native_eval_cannot_carry_an_execution_block(self) -> None:
        document = yaml.safe_load((self.fixture.eval_dir / "eval.yml").read_text())
        document["execution"] = {"backend": "native"}
        document["task"] = "structured_text"
        document["output_schema"] = "x.json"
        write_yaml(self.fixture.eval_dir / "eval.yml", document)
        with self.assertRaises(ConfigError) as caught:
            self.fixture.context()
        self.assertIn("only valid with backend: inspect", str(caught.exception))


class IdentityAndPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(self)

    def spec(self):
        return build_plan(self.fixture.context()).specs[0]

    def test_declared_source_change_changes_content_identity(self) -> None:
        before = self.spec()
        (self.fixture.exp / "inspect" / "task.py").write_text(TASK_PY + "\n# edited\n")
        after = self.spec()
        self.assertNotEqual(before.content_fingerprint, after.content_fingerprint)
        self.assertEqual(before.specification_fingerprint, after.specification_fingerprint)

    def test_prompt_change_changes_the_frozen_dataset_identity(self) -> None:
        before = self.spec()
        (self.fixture.exp / "prompts" / "user.md.j2").write_text("Please {{ item.input.prompt }}\n")
        after = self.spec()
        self.assertNotEqual(before.specification_fingerprint, after.specification_fingerprint)

    def test_limits_and_model_route_are_part_of_the_specification(self) -> None:
        before = self.spec()
        block = json.loads(json.dumps(EVAL["execution"]))
        block["limits"]["cost_limit"] = 0.6
        self.fixture.write_eval({"execution": block})
        self.assertNotEqual(before.specification_fingerprint, self.spec().specification_fingerprint)

    def test_budget_exposure_is_the_finite_cost_limit_per_item(self) -> None:
        from tamesu.pricing import estimate_plan_cost

        cost = estimate_plan_cost(build_plan(self.fixture.context()))
        self.assertTrue(cost.fully_priced)
        self.assertEqual(cost.maximum_usd, 1.5)  # 3 items x $0.50, one attempt each

    def test_frozen_samples_preserve_item_ids_and_rendered_prompts(self) -> None:
        context = self.fixture.context()
        spec = build_plan(context).specs[0]
        items = {item["id"]: item for item in context.dataset["items"]}
        samples = ib.frozen_samples(context, spec.prompts, [(i, items[i]) for i in spec.item_ids])
        self.assertEqual([s["id"] for s in samples], ["item-a", "item-b", "item-c"])
        self.assertEqual(samples[1]["input"][1], {"role": "user", "content": "say b\n"})
        self.assertEqual(samples[0]["metadata"]["tamesu_item_id"], "item-a")


class ArtifactIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        (self.stage / "files").mkdir(parents=True)
        self.item_dir = self.root / "eval" / "item"
        self.item_dir.mkdir(parents=True)
        self.policy = {"required": ["report"], "allowed": ["extra"], "max_bytes": 100}

    def stage_file(self, name: str, data: bytes = b"hello") -> None:
        path = self.stage / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def manifest(self, *entries: dict) -> None:
        (self.stage / "artifacts.json").write_text(json.dumps(list(entries)))

    def ingest(self):
        return ib._ingest_artifacts(self.stage, self.item_dir, self.root / "eval", self.policy)

    def entry(self, name="a.txt", role="report", media="text/plain") -> dict:
        return {"role": role, "name": name, "media_type": media}

    def test_valid_artifact_is_copied_and_hashed(self) -> None:
        self.stage_file("sub/a.txt")
        self.manifest(self.entry("sub/a.txt"))
        accepted, rejected, missing = self.ingest()
        self.assertEqual((rejected, missing), ([], []))
        copied = self.item_dir / "artifacts" / "sub" / "a.txt"
        self.assertEqual(accepted[0]["sha256"], digest_file(copied))
        self.assertEqual(accepted[0]["path"], "item/artifacts/sub/a.txt")

    def test_unsafe_names_are_rejected_individually(self) -> None:
        self.stage_file("good.txt")
        self.manifest(
            self.entry("../escape.txt"),
            self.entry("/abs.txt"),
            self.entry("back\\slash.txt"),
            self.entry("good.txt"),
        )
        accepted, rejected, _ = self.ingest()
        self.assertEqual([a["path"].rsplit("/", 1)[-1] for a in accepted], ["good.txt"])
        self.assertEqual([r["reason"] for r in rejected], ["unsafe path"] * 3)

    def test_symlinks_and_escapes_are_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        (self.stage / "files" / "link.txt").symlink_to(outside)
        (self.stage / "files" / "linkdir").symlink_to(self.root)
        self.manifest(self.entry("link.txt"), self.entry("linkdir/outside.txt"))
        accepted, rejected, missing = self.ingest()
        self.assertEqual(accepted, [])
        self.assertEqual({r["reason"] for r in rejected}, {"symlink in path"})
        self.assertEqual(missing, ["report"])
        self.assertFalse((self.item_dir / "artifacts").exists())

    def test_oversize_undeclared_role_duplicates_and_bad_media_types(self) -> None:
        self.stage_file("big.txt", b"x" * 101)
        self.stage_file("ok.txt")
        self.manifest(
            self.entry("big.txt"),
            self.entry("ok.txt", role="surprise"),
            self.entry("ok.txt", media="not a type"),
            self.entry("ok.txt"),
            self.entry("ok.txt"),
        )
        accepted, rejected, _ = self.ingest()
        self.assertEqual(len(accepted), 1)
        reasons = [r["reason"] for r in rejected]
        self.assertTrue(reasons[0].startswith("larger than"))
        self.assertIn("is not declared", reasons[1])
        self.assertEqual(reasons[2], "invalid media type")
        self.assertEqual(reasons[3], "duplicate name")

    def test_missing_required_role_is_reported_and_bad_manifest_is_survivable(self) -> None:
        (self.stage / "artifacts.json").write_text("{not json")
        accepted, rejected, missing = self.ingest()
        self.assertEqual((accepted, missing), ([], ["report"]))
        self.assertIn("not valid JSON", rejected[0]["reason"])
        (self.stage / "artifacts.json").write_text('{"a": 1}')
        self.assertIn("must be a list", self.ingest()[1][0]["reason"])

    def test_file_count_is_capped(self) -> None:
        self.policy["max_bytes"] = 10
        self.stage_file("f.txt")
        names = []
        for index in range(ib.MAX_ARTIFACTS_PER_ITEM + 1):
            self.stage_file(f"f{index}.txt")
            names.append(self.entry(f"f{index}.txt"))
        self.manifest(*names)
        accepted, rejected, _ = self.ingest()
        self.assertEqual(len(accepted), ib.MAX_ARTIFACTS_PER_ITEM)
        self.assertEqual(rejected[-1]["reason"], "too many artifacts")


@unittest.skipUnless(INSPECT, "inspect-ai is not installed")
class EndToEndWithMockModelTests(unittest.TestCase):
    """Real Inspect evaluation against its mock model: no network, no Docker."""

    def setUp(self) -> None:
        self.fixture = Fixture(self)
        self.addCleanup(os.environ.pop, "BUNDLE_MODE", None)

    def run_dir(self, run_id: str) -> Path:
        return self.fixture.eval_dir / "runs" / run_id

    def events(self, run_id: str) -> list[dict]:
        log = self.fixture.eval_dir / "logs" / f"{run_id}.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()]

    def run_once(self, **kwargs) -> str:
        with Mocked():
            return run_eval(self.fixture.context(), **kwargs)[0]

    def test_run_ingests_scores_artifacts_report_and_digest_pinned_log(self) -> None:
        run_id = self.run_once()
        run_dir = self.run_dir(run_id)
        manifest = yaml.safe_load((run_dir / "run.yml").read_text())
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual(manifest["execution"]["backend"], "inspect")
        self.assertEqual(manifest["execution"]["model_uri"], "mockllm/muse-spark-1.2")
        [entry] = manifest["execution"]["logs"]
        self.assertEqual(entry["samples"], 3)
        self.assertEqual(entry["sha256"], digest_file(self.fixture.eval_dir / entry["path"]))

        for item_id in ("item-a", "item-b", "item-c"):
            result = yaml.safe_load((run_dir / "items" / item_id / "result.yml").read_text())
            self.assertEqual(result["state"], "complete")
            self.assertEqual(result["mechanical_scores"]["check"]["value"], 1.0)
            self.assertEqual([a["role"] for a in result["artifacts"]], ["report"])
            self.assertEqual(result["missing_artifacts"], [])
            self.assertEqual(result["rejected_artifacts"][0]["name"], "x.txt")
            copied = self.fixture.eval_dir / result["artifacts"][0]["path"]
            self.assertEqual(result["artifacts"][0]["sha256"], digest_file(copied))
            self.assertEqual(result["inspect"]["stop_reason"], "stop")

        report = yaml.safe_load((run_dir / "report.yml").read_text())
        self.assertEqual(report["metrics"]["mean_score:check"], 1.0)
        self.assertEqual(report["metrics"]["artifact_complete_rate"], 1.0)
        self.assertEqual(report["completion"], {"selected_items": 3, "completed_items": 3, "failed_items": 0})

        schema = json.loads((SCHEMAS / "log-event.schema.json").read_text())
        events = self.events(run_id)
        for event in events:
            self.assertEqual(validate_json_schema(event, schema), [], event)
        names = [event["event"] for event in events]
        self.assertEqual(names.count("inspect_log"), 1)
        self.assertEqual(names.count("item_finished"), 3)
        self.assertEqual(names[-1], "run_finished")

    def test_item_error_is_a_retryable_failure_and_resume_links_the_retry(self) -> None:
        os.environ["BUNDLE_MODE"] = "boom"
        run_id = self.run_once()
        run_dir = self.run_dir(run_id)
        manifest = yaml.safe_load((run_dir / "run.yml").read_text())
        self.assertEqual(manifest["state"], "partial")
        failed = yaml.safe_load((run_dir / "items" / "item-b" / "result.yml").read_text())
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["error"]["retryable"])
        self.assertIn("scorer exploded", failed["error"]["message"])
        self.assertEqual(manifest["execution"]["logs"][0]["status"], "success")  # log status is not run success

        before = (run_dir / "items" / "item-a" / "result.yml").read_bytes()
        del os.environ["BUNDLE_MODE"]
        with Mocked():
            resume_run(self.fixture.context(), run_id)
        manifest = yaml.safe_load((run_dir / "run.yml").read_text())
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual([entry["attempt"] for entry in manifest["execution"]["logs"]], [1, 2])
        self.assertEqual(manifest["execution"]["logs"][1]["samples"], 1)  # only the owed item re-ran
        self.assertEqual((run_dir / "items" / "item-a" / "result.yml").read_bytes(), before)
        self.assertEqual([e["event"] for e in self.events(run_id)].count("inspect_log"), 2)

    def test_missing_scorer_is_a_permanent_failure(self) -> None:
        self.fixture.write_eval({"evaluation": {"mechanical": ["not_there"]}, "metrics": {"primary": "completion_rate"}})
        run_id = self.run_once()
        manifest = yaml.safe_load((self.run_dir(run_id) / "run.yml").read_text())
        self.assertEqual(manifest["state"], "failed")
        result = yaml.safe_load((self.run_dir(run_id) / "items" / "item-a" / "result.yml").read_text())
        self.assertEqual(result["error"]["type"], "MissingScore")
        self.assertFalse(result["error"]["retryable"])

    def test_tampered_log_is_stale_evidence(self) -> None:
        run_id = self.run_once()
        manifest = yaml.safe_load((self.run_dir(run_id) / "run.yml").read_text())
        log_path = self.fixture.eval_dir / manifest["execution"]["logs"][0]["path"]
        with log_path.open("ab") as stream:
            stream.write(b"tamper")
        from tamesu.run_report import rescore_run

        with self.assertRaises(ExecutionError) as caught:
            rescore_run(self.fixture.context(), self.run_dir(run_id))
        self.assertIn("digest changed", str(caught.exception))

    def test_resume_refuses_a_changed_frozen_dataset(self) -> None:
        os.environ["BUNDLE_MODE"] = "boom"
        run_id = self.run_once()
        del os.environ["BUNDLE_MODE"]
        frozen = self.run_dir(run_id) / "inspect" / "input.jsonl"
        frozen.write_text(frozen.read_text().replace("say b", "say B"))
        with Mocked(), self.assertRaises(ExecutionError) as caught:
            resume_run(self.fixture.context(), run_id)
        self.assertIn("frozen Inspect dataset", str(caught.exception))

    def real_eval_then_swallow(self, *, keep_log: bool):
        """Mimic Inspect on Ctrl-C: it returns no logs (the interrupt is swallowed)."""
        import inspect_ai

        real = inspect_ai.eval

        def wrapper(*args, **kwargs):
            real(*args, **kwargs)
            if not keep_log:
                for log in Path(kwargs["log_dir"]).glob("*.eval"):
                    log.unlink()
            return []

        return patch("inspect_ai.eval", wrapper)

    def test_interrupt_that_returns_no_logs_recovers_the_written_log(self) -> None:
        with self.real_eval_then_swallow(keep_log=True):
            run_id = self.run_once()
        manifest = yaml.safe_load((self.run_dir(run_id) / "run.yml").read_text())
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual(len(manifest["execution"]["logs"]), 1)

    def test_interrupt_before_any_log_leaves_a_resumable_partial_run(self) -> None:
        with self.real_eval_then_swallow(keep_log=False), Mocked():
            with self.assertRaises(KeyboardInterrupt):
                run_eval(self.fixture.context())
        [run_dir] = list((self.fixture.eval_dir / "runs").iterdir())
        manifest = yaml.safe_load((run_dir / "run.yml").read_text())
        self.assertEqual(manifest["state"], "partial")  # not failed: it can resume
        events = self.events(run_dir.name)
        self.assertTrue(events[-1]["interrupted"])
        with Mocked():
            resume_run(self.fixture.context(), run_dir.name)
        self.assertEqual(yaml.safe_load((run_dir / "run.yml").read_text())["state"], "complete")

    def test_probe_runs_a_prefix_of_items(self) -> None:
        run_id = self.run_once(limit_items=2)
        manifest = yaml.safe_load((self.run_dir(run_id) / "run.yml").read_text())
        self.assertTrue(manifest["probe"])
        self.assertEqual(manifest["execution"]["logs"][0]["samples"], 2)


class IngestionGuardTests(unittest.TestCase):
    """Log shapes that real runs rarely produce, fed to the ingester directly."""

    def setUp(self) -> None:
        self.fixture = Fixture(self)
        self.context = self.fixture.context()
        self.spec = build_plan(self.context).specs[0]
        self.run_dir = self.fixture.eval_dir / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        self.log = CallLogWriter(self.fixture.eval_dir / "logs" / "r1.jsonl")
        self.run = BackendRun(
            context=self.context,
            spec=self.spec,
            run_id="r1",
            run_dir=self.run_dir,
            log=self.log,
            selected=[],
            progress=SimpleNamespace(item_done=lambda *args: None),
        )

    def fake_log(self, ids: list[str], status: str = "success"):
        samples = [SimpleNamespace(id=i, error=None, scores={}, model_usage={}, total_time=1, limit=None, output=None) for i in ids]
        return SimpleNamespace(status=status, samples=samples)

    def ingest(self, log, pending=("item-a", "item-b", "item-c")):
        ib.InspectBackend()._ingest(self.run, log, list(pending), self.run_dir / "stage", "log.eval")

    def test_unexpected_sample_is_a_framework_error(self) -> None:
        with self.assertRaises(ExecutionError) as caught:
            self.ingest(self.fake_log(["item-a", "intruder"]))
        self.assertIn("unexpected sample 'intruder'", str(caught.exception))

    def test_duplicate_sample_is_a_framework_error(self) -> None:
        with self.assertRaises(ExecutionError) as caught:
            self.ingest(self.fake_log(["item-a", "item-a"]))
        self.assertIn("duplicate sample", str(caught.exception))

    def test_cancelled_log_leaves_unstarted_items_retryable(self) -> None:
        self.ingest(self.fake_log([], status="cancelled"))
        for item_id in ("item-a", "item-b", "item-c"):
            result = yaml.safe_load((self.run_dir / "items" / item_id / "result.yml").read_text())
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error"]["type"], "MissingSample")
            self.assertTrue(result["error"]["retryable"])
            self.assertEqual(result["inspect"]["log_status"], "cancelled")
        self.assertEqual(len(self.run.results), 3)

    def test_sample_error_is_never_complete_even_when_log_status_is_success(self) -> None:
        sample = self.fake_log(["item-a"]).samples[0]
        sample.error = SimpleNamespace(message="RetryError(InternalServerError)")
        self.ingest(SimpleNamespace(status="success", samples=[sample]), pending=("item-a",))
        result = yaml.safe_load((self.run_dir / "items" / "item-a" / "result.yml").read_text())
        self.assertEqual((result["state"], result["error"]["type"]), ("failed", "SampleError"))


class PackagingAndTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture(self)

    def seed_run_evidence(self) -> None:
        run = self.fixture.eval_dir / "runs" / "r1"
        for relative in (
            "run.yml",
            "inspect/attempt-01/2026.eval",
            "inspect/input.jsonl",
            "inspect/staging/attempt-01/item/files/x.txt",
            "inspect/lineage.yml",
            "items/item-a/result.yml",
            "items/item-a/artifacts/out.txt",
        ):
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("data")
        (self.fixture.eval_dir / "stray.eval").write_text("transcript")
        (self.fixture.eval_dir / "logs").mkdir()
        (self.fixture.eval_dir / "logs" / "r1.jsonl").write_text('{"event":"run_started"}\n')

    def test_full_and_rescorable_packages_never_contain_inspect_transcripts(self) -> None:
        self.seed_run_evidence()
        for profile in ("full", "rescorable"):
            payload = collect_payload(self.fixture.case, profile)
            leaked = [p for p in payload if "/inspect/" in p or p.endswith(".eval")]
            # the authored task source under experiments/exp/inspect is kept; run evidence is not
            self.assertEqual([p for p in leaked if "/runs/" in p or p.endswith(".eval")], [], profile)
            self.assertIn("experiments/exp/inspect/task.py", payload)
            self.assertTrue(any(p.endswith("items/item-a/artifacts/out.txt") for p in payload))

    def test_unpacked_cases_require_explicit_trust(self) -> None:
        (self.fixture.case / ".untrusted-origin").write_text("unpacked")
        context = self.fixture.context()
        specs = list(build_plan(context).specs)
        with Mocked():
            with self.assertRaises(ExecutionError) as caught:
                ib.InspectBackend().check_ready(context, specs, trust_code=False)
            self.assertIn("--trust-code", str(caught.exception))
            if INSPECT:
                ib.InspectBackend().check_ready(context, specs, trust_code=True)

    def test_unpack_marks_the_case_untrusted(self) -> None:
        from tamesu.packaging import pack_case, unpack_archive

        write_yaml(
            self.fixture.case / "publication.yml",
            {
                "schema_version": 1,
                "name": "bundle-case",
                "publisher": "tests",
                "version": "0.1.0",
                "title": "Bundle",
                "summary": "Fixture package.",
                "authors": [{"name": "Test Author"}],
                "license": "CC-BY-4.0",
                "tags": ["fixture"],
                "profile": "rescorable",
                "data_review": {"acknowledged": True, "note": "Synthetic fixture data."},
            },
        )
        archive = pack_case(self.fixture.case, self.fixture.root / "dist")
        destination = unpack_archive(archive, self.fixture.root / "unpacked")
        self.assertTrue((destination / ".untrusted-origin").is_file())
        self.assertNotIn(".untrusted-origin", collect_payload(destination, "full"))


if __name__ == "__main__":
    unittest.main()
