# C rendering engine: integration internals

This is the contributor companion to [WALKTHROUGH.md](../../../examples/c-rendering-engine/WALKTHROUGH.md). Read it when modifying the
Inspect backend, scorer, or presentation—not before running the example.

## What the example revealed

One observed Muse 1.2 response put its file marker on the same line as the Markdown fence. The
example scorer's `FILE_BLOCK` expression expects a newline there, so it can report
`no usable FILE blocks` for a response that may contain viable C. This is a measurement bug, not
evidence of model failure.

A more permissive expression would accept either layout:

```python
FILE_BLOCK = re.compile(r"```[a-zA-Z]*[ \t]*\n?\s*//\s*FILE:\s*([\w./-]+)\n(.*?)```", re.S)
```

Changing `inspect/task.py` changes run identity. Existing evidence correctly becomes stale and all
three runs become owed. The example retains the original scorer so its checked-in evidence and
review pack remain compatible.

Observed Muse 1.3 failures—such as a non-constant static array bound or duplicate parameter
name—are ordinary compiler failures. Their logs distinguish model errors from extraction errors.

## Code map

Paths are under `src/tamesu/`. Search by symbol rather than relying on line numbers.

| Concern | Start here |
|---|---|
| Manifest and Inspect validation | `config.py::_validate_eval`, `backends/inspect_backend.py::validate_execution` |
| Undeclared files and image pinning | `inspect_backend.py::_source_like_files`, `image_references` |
| Run matrix and identity | `planner.py::build_plan`, `make_run_spec`, `InspectBackend.identity` |
| Cost ceiling | `pricing.py::estimate_plan_cost`, `InspectBackend.estimate_item_cost` |
| Lifecycle and design | `lifecycle.py::lifecycle_for`, `present_design.py::build_design` |
| Run dispatch | `cli.py::command_run`, `runner.py::run_eval`, `execute_spec` |
| Inspect execution | `backends/inspect_backend.py::execute`, `_run_inspect`, `frozen_samples` |
| Cancellation and resume | `runner.py::execute_spec`, `resume_run`; Inspect pending loop |
| Evidence ingestion | `inspect_backend.py::_ingest`, `_item_result`, `_ingest_artifacts` |
| Artifact safety | `inspect_backend.py::_artifact_rejection` |
| Scorer staging API | `inspect_support.py::Stager`; this example's `inspect/task.py` |
| Review and acceptance | `tasks/artifact_bundle.py`, `review.py`, `acceptance.py` |
| Reports and presentation | `reporting.py`, `presenting.py`, `presenters.py` |
| Digest verification | `inspect_backend.py::verify_logs` |
| Analysis provenance | `analysis.py`, `analyze.py` |
| Transcript exclusion | `packaging.py::_is_inspect_evidence` |

The closest executable specifications are:

- `tests/test_inspect_backend.py`: validation, identity, ingestion, resume, interrupts, packaging,
  and trust;
- `tests/test_inspect_review.py`: review over artifact bundles;
- `tests/test_present_contract.py`: lifecycle, offline rendering, hostile logs, and isolation;
- `tests/test_analyze.py`: analysis generation with a fake provider.

## Known limits

- Inspect cost planning uses the configured maximum because an agent loop is not predictable.
- One item and repetition per model cannot support a meaningful model ranking.
- Review supports one image artifact role for this task.
- Reviewer IDs are asserted strings, not authenticated identities.
- A closed eval whose manifest changes remains closed but reports excluded stale runs.
- Wide presentation tables may scroll on narrow screens.
- Real provider-assisted analysis, packaging, and publishing were not verified in the recorded run.
- The recorded run covered macOS and Docker Desktop, not every supported environment.
- Multiple items, repetitions, higher concurrency, and limits actually tripping remain untested.

## Contributor checklist

When changing this integration, verify:

- lint errors identify the file or field to change;
- progress exposes position, elapsed time, and cost;
- Ctrl-C leaves honest partial evidence and a usable resume command;
- source, logs, image, and score remain traceable;
- mechanical and human judgments remain separate;
- changing prompts, sources, scorer code, or image digest invalidates reuse;
- packages withhold private transcripts;
- low sample counts are described as insufficient for comparison.
