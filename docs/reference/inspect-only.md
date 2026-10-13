# Inspect-only migration

This branch implements [the simplification plan](../../plans/SIMPLIFY-INSPECT-AI.md)
based on [Andrew's decisions](../../plans/SIMPLIFY-INSPECT-AI-DECISIONS.md) and the
[review repository](https://github.com/danphillips-cloud/tamesu-inspect-ai-review).

## Execution

Inspect AI is a core dependency and the only executor. `execution.backend` defaults
to `inspect`; explicit `native` is rejected. Existing `structured_text` manifests
use a built-in Inspect task with the same JSON/schema/exact-match checks and stored
result shape. Existing `image_generation` manifests use an Inspect ModelAPI adapter
for image transport, retaining their mechanical checks and image evidence format.
Custom experiments use `artifact_bundle` with an ordinary Inspect task file.

Inspect owns sample scheduling, model retries, transcripts, and supported configured limits. Built-in image transport records image API billing after generation; use `budget_usd` for estimated image exposure. Inspect token-priced `cost_limit` applies to text/custom tasks, not flat image API billing.
The image API adapter is needed because existing image endpoints do not expose the
chat interface used by text models. It does not restore the removed native runner.

Old `status: draft`, `active`, or `complete` fields are accepted as legacy metadata
but do not gate execution or determine the page's state. The page derives readiness,
partial execution, and pending review from evidence. `activate` and `close` are no
longer CLI commands. Run completion statuses remain meaningful.

New starters inline the dataset and prompt text in `eval.yml`. Larger experiments
can continue using file paths. Portable packages verify references for both layouts.

## Packages and review

`pack` no longer requires publishing metadata. Without it, the archive uses the
local namespace and version `0.1.0`. Existing v1 archives and explicit publication
profiles remain supported. Publishing/site utilities are advanced legacy features;
the documented workflow is local pack, verify, unpack, show, and optional review.

Inspect logs and staging directories stay out of archives. Packages retain canonical
scores and artifacts, so they can be viewed and reviewed offline. They cannot replay
custom scorer code against a withheld Inspect log; rerun the task to produce new logs.

Review subjects can include multiple declared artifact roles. A review of several
files binds the subject manifest and every member's digest. Changing any member or
the rubric makes prior reviews stale. Single-image reviews keep their earlier digest
format for compatibility. Human review is optional and gates acceptance only when
`human` is included in the experiment's acceptance policy.

Complete archives contain model identities and response-import keys. Share the
separately exported blinded pack with reviewers. Never give them the full archive
if blinding is required. A recipient can unpack, review, import, and repack without
model calls. Opening a package does not import its executable Python task.

Older runs remain in the page's evidence section. A `compatible` field in `data.json`
distinguishes them from current banked work; changing engines/configuration does not
turn historical results into new evidence.

Automated model grading should use [Inspect scorers](https://inspect.aisi.org.uk/model-graded.html).
The existing image judge and model-assisted analysis utilities retain their review records and call Inspect model APIs for transport.

## Authored happy path

The primary commands are `case new`, `exp new`, and `eval new`. Experiments have
`experiment.yml` with description, hypothesis, and success criteria. `show` lists cases
and offers authored documentation editing; it also retains live evidence and review.

Custom tasks use `evaluation.scorers` and metric references such as `correctness/accuracy`.
Execution still saves separate resumable repetitions. Tamesu pools the saved sample scores
and asks Inspect to apply the task's repetition reducer and metrics, freezing the results
under the eval's `statistics/` directory. Rendering reads these statistics without importing
task code. It does not average custom metrics from separate repetitions.

Run filters and sampling share one plan with `run --dry-run`. Random probes persist their
seed, selected IDs, and overrides for resume and remain excluded from final comparisons.
