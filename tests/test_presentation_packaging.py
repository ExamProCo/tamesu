from __future__ import annotations

import gzip
import io
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

from tamesu.artifacts import write_yaml
from tamesu.errors import TamesuError
from tamesu.packaging import (
    _read_archive,
    _write_archive,
    collect_payload,
    fork_archive,
    pack_case,
    unpack_archive,
    validate_portable,
    verify_archive,
)
from tamesu.presenting import build_case_data, evidence_digest, present_case
from tamesu.showcasing import build_site, publish_archive, validate_registry


class PresentationPackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        source = Path(__file__).parents[1] / "examples" / "support-ticket-classification"
        self.project = Path(self.temporary.name) / "project"
        shutil.copytree(source, self.project)
        self.case = self.project / "cases" / "support-ticket-triage"
        self.eval_dir = (
            self.case
            / "experiments/decision-rules/evals/prompt-ablation"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_present_is_deterministic_and_escapes_hostile_values(self) -> None:
        publication = yaml.safe_load((self.case / "publication.yml").read_text())
        publication["title"] = '<script>alert("no")</script>'
        publication["summary"] = "[click](javascript:alert(1))"
        write_yaml(self.case / "publication.yml", publication)
        first = present_case(self.case, self.project / "first")
        first_bytes = self._tree_bytes(first)
        second = present_case(self.case, self.project / "second")
        self.assertEqual(first_bytes, self._tree_bytes(second))
        rendered = (first / "index.html").read_text()
        self.assertNotIn("<script>alert", rendered)
        self.assertNotIn("javascript:", rendered.lower())
        self.assertIn("&lt;script&gt;", rendered)

    def test_present_carries_authored_case_and_dataset_context(self) -> None:
        data = build_case_data(self.case)
        self.assertIn("support team", data["case"]["business_use"])
        self.assertIn("manual triage", data["case"]["current_problem"])
        self.assertIn("explicit category", data["case"]["technical_uncertainty"])
        self.assertEqual(len(data["datasets"]), 1)
        self.assertEqual(data["datasets"][0]["name"], "support-tickets-v1")
        self.assertIn("Eight synthetic support tickets", data["datasets"][0]["description"])
        items = data["experiments"][0]["evals"][0]["items"]
        self.assertEqual(len(items), 32)
        self.assertTrue(all(item["model"] == "muse-spark-1.2" for item in items))
        self.assertTrue(all(item["arm"] for item in items))
        self.assertTrue(all("exact_match" in item["scores"] for item in items))
        evidence_runs = data["experiments"][0]["evals"][0]["evidence_runs"]
        self.assertEqual(len(evidence_runs), 4)
        self.assertTrue(all(len(run["items"]) == 8 for run in evidence_runs))
        self.assertEqual({run["repetition"] for run in evidence_runs}, {1, 2})
        evaluation = data["experiments"][0]["evals"][0]
        self.assertIn("75.00%", evaluation["observed_outcome"]["summary"])
        self.assertIn("68.75%", evaluation["observed_outcome"]["summary"])
        self.assertTrue(evaluation["analysis"]["matches_evidence"])

        destination = present_case(self.case, self.project / "presented")
        rendered = (destination / "index.html").read_text()
        self.assertIn("Business use", rendered)
        self.assertIn("Current problem", rendered)
        self.assertIn("Technical uncertainty", rendered)
        self.assertIn("support-tickets-v1", rendered)
        self.assertIn("Eight synthetic support tickets", rendered)
        self.assertIn("Package information", rendered)
        self.assertIn('<dl class="key-values">', rendered)
        self.assertIn("<dt>Banked runs</dt><dd>4</dd>", rendered)
        self.assertIn("<dt>Runs owed</dt><dd>0</dd>", rendered)
        self.assertIn("<dt>Publisher</dt><dd>omenking</dd>", rendered)
        self.assertIn("<dt>Profile</dt><dd>rescorable</dd>", rendered)
        self.assertIn('class="document-nav"', rendered)
        self.assertIn(
            'href="experiments/decision-rules.html#prompt-ablation-results"',
            rendered,
        )

        experiment = (destination / "experiments" / "decision-rules.html").read_text()
        self.assertIn("Eight synthetic support tickets", experiment)
        self.assertIn('<div class="arm-list">', experiment)
        self.assertIn("Lists the fields and allowed values", experiment)
        self.assertIn("System prompt", experiment)
        self.assertIn("basic-system.md", experiment)
        self.assertIn('href="../index.html#case-overview"', experiment)
        self.assertIn('id="prompt-ablation-arm-basic-prompt"', experiment)
        self.assertIn('class="table-wrap"', experiment)
        self.assertIn('class="run-table"', experiment)
        self.assertEqual(experiment.count('class="run-evidence"'), 4)
        self.assertIn('class="evidence-table"', experiment)
        self.assertIn('class="evidence-row"', experiment)
        self.assertIn("Exact match", experiment)
        self.assertIn("Run evidence", experiment)
        self.assertIn("Observed outcome", experiment)
        self.assertIn("Conclusion and next experiments", experiment)
        self.assertIn("Answer to the technical uncertainty", experiment)
        self.assertIn("What remains uncertain", experiment)
        self.assertIn("Next experiments", experiment)
        question = (
            "Do explicit routing rules improve exact-match support-ticket "
            "classification accuracy?"
        )
        self.assertEqual(experiment.count(question), 1)
        self.assertIn("<h2>Hypothesis</h2>", experiment)
        self.assertNotIn(
            "Explicit category, priority, and escalation rules improve", experiment
        )

    def test_existing_completed_runs_survive_case_narrative_changes(self) -> None:
        data = build_case_data(self.case)
        evaluation = data["experiments"][0]["evals"][0]
        self.assertEqual(evaluation["status"], "closed")  # the example's eval.yml says complete
        self.assertEqual(evaluation["lifecycle"]["label"], "Closed")
        self.assertEqual(evaluation["coverage"]["banked"], 4)
        self.assertEqual(evaluation["coverage"]["owed"], 0)

    def test_case_context_fields_are_required(self) -> None:
        manifest = yaml.safe_load((self.case / "case.yml").read_text())
        del manifest["technical_uncertainty"]
        write_yaml(self.case / "case.yml", manifest)
        with self.assertRaisesRegex(TamesuError, "case.technical_uncertainty"):
            build_case_data(self.case)

    def test_analysis_is_bound_to_current_evidence(self) -> None:
        digest = evidence_digest(self.eval_dir)
        analysis = self.eval_dir / "analysis.md"
        analysis.write_text(f"---\nevidence_digest: {digest}\n---\nObserved result.\n")
        data = build_case_data(self.case)
        shown = data["experiments"][0]["evals"][0]["analysis"]
        self.assertTrue(shown["matches_evidence"])
        analysis.write_text("---\nevidence_digest: sha256:" + "0" * 64 + "\n---\nStale.\n")
        shown = build_case_data(self.case)["experiments"][0]["evals"][0]["analysis"]
        self.assertFalse(shown["matches_evidence"])
        analysis.write_text("No front matter.\n")
        shown = build_case_data(self.case)["experiments"][0]["evals"][0]["analysis"]
        self.assertIn("not verified", shown["label"])

    def test_pack_is_deterministic_and_round_trips(self) -> None:
        first_dir = self.project / "archives-a"
        second_dir = self.project / "archives-b"
        first = pack_case(self.case, first_dir)
        for path in self.case.rglob("*"):
            if path.is_file():
                os.utime(path, (1_700_000_000, 1_700_000_000))
                path.chmod(0o600)
        second = pack_case(self.case, second_dir)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        verified = verify_archive(first)
        destination = unpack_archive(first, self.project / "unpacked")
        repacked = pack_case(destination, self.project / "archives-c")
        self.assertEqual(verified.content_digest, verify_archive(repacked).content_digest)

    def test_publication_only_change_keeps_content_identity(self) -> None:
        first = verify_archive(pack_case(self.case, self.project / "archives-a"))
        publication = yaml.safe_load((self.case / "publication.yml").read_text())
        publication["version"] = "1.0.1"
        publication["summary"] = "A revised showcase summary."
        write_yaml(self.case / "publication.yml", publication)
        second = verify_archive(pack_case(self.case, self.project / "archives-b"))
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.archive_sha256, second.archive_sha256)

    def test_tampering_and_traversal_are_rejected(self) -> None:
        archive = pack_case(self.case, self.project / "archives")
        _, files = _read_archive(archive)
        files["payload/case.yml"] += b"\n# tampered\n"
        tampered = self.project / "tampered.tar.gz"
        _write_archive(tampered, files)
        with self.assertRaisesRegex(TamesuError, "inventory"):
            verify_archive(tampered)

        traversal = self.project / "traversal.tar.gz"
        with traversal.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as tar:
                    info = tarfile.TarInfo("../outside")
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(TamesuError, "Unsafe archive path"):
            verify_archive(traversal)

    def test_profiles_have_exact_security_boundaries(self) -> None:
        full = collect_payload(self.case, "full")
        rescorable = collect_payload(self.case, "rescorable")
        report = collect_payload(self.case, "report-only")
        self.assertTrue(any("/logs/" in f"/{path}" for path in full))
        self.assertFalse(any("/logs/" in f"/{path}" for path in rescorable))
        self.assertTrue(any(path.endswith("output.txt") for path in rescorable))
        self.assertFalse(any(path.startswith("datasets/") for path in report))
        self.assertFalse(any(path.endswith("output.txt") for path in report))
        self.assertIn("package-view.yml", report)

    def test_portability_and_secret_scan_fail_closed(self) -> None:
        eval_path = self.eval_dir / "eval.yml"
        evaluation = yaml.safe_load(eval_path.read_text())
        outside = self.project / "outside.yml"
        outside.write_text("schema_version: 1\n")
        evaluation["dataset"] = str(outside)
        write_yaml(eval_path, evaluation)
        with self.assertRaisesRegex(TamesuError, "leaves the case directory"):
            validate_portable(self.case)

        evaluation["dataset"] = "../../../../datasets/support-tickets-v1/dataset.yml"
        write_yaml(eval_path, evaluation)
        publication = yaml.safe_load((self.case / "publication.yml").read_text())
        publication["summary"] = "credential sk-" + "x" * 30
        write_yaml(self.case / "publication.yml", publication)
        with self.assertRaisesRegex(TamesuError, "Potential secret"):
            pack_case(self.case, self.project / "archives")

    def test_registry_build_reproduces_download(self) -> None:
        archive = pack_case(self.case, self.project / "archives")
        registry = self.project / "registry"
        self._write_publisher(registry, "omenking", "allowed-user")
        publish_archive(archive, registry)
        records = validate_registry(registry)
        self.assertEqual(len(records), 1)
        self.assertEqual(
            len(validate_registry(registry, author="allowed-user")), 1
        )
        with self.assertRaisesRegex(TamesuError, "cannot publish"):
            validate_registry(registry, author="someone-else")
        site = build_site(registry, self.project / "site")
        download = next((site / "downloads").glob("*.tamesu.tar.gz"))
        verified = verify_archive(download)
        self.assertEqual(verified.archive_sha256, records[0]["archive_sha256"])
        self.assertTrue((site / "index.json").is_file())

    def test_showcase_links_fork_lineage_and_builds_comparison(self) -> None:
        parent_archive = pack_case(self.case, self.project / "parent-archive")
        fork = fork_archive(
            parent_archive,
            self.project / "fork-cases",
            "example-team",
            version="1.0.1",
        )
        fork_archive_path = pack_case(fork, self.project / "fork-archive")
        self.assertEqual(
            verify_archive(parent_archive).content_digest,
            verify_archive(fork_archive_path).content_digest,
        )
        registry = self.project / "registry"
        self._write_publisher(registry, "omenking", "omenking-user")
        self._write_publisher(registry, "example-team", "example-user")
        publish_archive(parent_archive, registry)
        publish_archive(fork_archive_path, registry)
        site = build_site(registry, self.project / "site")
        parent_page = (
            site / "omenking/support-ticket-triage/1.0.0/index.html"
        ).read_text()
        fork_dir = site / "example-team/support-ticket-triage/1.0.1"
        self.assertIn("Forks:", parent_page)
        self.assertTrue((fork_dir / "compare.html").is_file())
        self.assertIn("Leaderboard movement", (fork_dir / "compare.html").read_text())

    @staticmethod
    def _tree_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def _write_publisher(registry: Path, slug: str, account: str) -> None:
        publisher_dir = registry / "publishers"
        publisher_dir.mkdir(parents=True, exist_ok=True)
        write_yaml(
            publisher_dir / f"{slug}.yml",
            {
                "schema_version": 1,
                "slug": slug,
                "display_name": slug.replace("-", " ").title(),
                "github_accounts": [account],
            },
        )


if __name__ == "__main__":
    unittest.main()
