# Execution backends and Inspect AI

A Tamesu **task** decides what an output means: its artifacts, scores, report and review. An
**execution backend** decides how the output is produced.

| Backend | Runs | Use it for | Needs |
|---|---|---|---|
| `native` (default) | Tamesu's per-item provider loop | Text and image generation, one call per item | Provider credentials |
| `inspect` | One [Inspect AI](https://inspect.aisi.org.uk) evaluation per run | Tools, multi-turn solvers, sandboxed code execution (compile, run, render) | `pip install 'tamesu[inspect]'`, Docker for sandboxed tasks |

Tamesu keeps everything above execution: the case, hypothesis, journal, frozen run matrix,
budget, fingerprints, and the report. Inspect's log is supporting evidence. Tamesu ingests
canonical item evidence from it and digest-pins every retained log.

## Declaring an Inspect-backed eval

An Inspect backend needs a task that supports it. The first is `artifact_bundle`: each
item produces named artifacts plus scores from Inspect scorers.

```yaml
task: artifact_bundle
dataset: ../../../../datasets/rendering-engine-prompt-v1/dataset.yml

execution:
  backend: inspect
  file: ../../inspect/task.py          # the Inspect @task module
  task: rendering_engine               # the @task function
  task_args: {}                        # extra keyword arguments (not `dataset`)
  sources:                             # every local file that defines the execution
    - ../../inspect/task.py
    - ../../inspect/compose.yaml
  limits:
    cost_limit: 0.25                   # required: dollars per sample
    time_limit: 600                    # required: seconds per sample
    token_limit: 60000                 # optional aggregate tokens per sample
  artifacts:
    required: [source, compiler-log, run-log, render]
    allowed: [response]
    review: render                     # image role shown to blinded reviewers (see Review)

evaluation:
  mechanical:                          # Inspect scorer names every sample must produce
    - compiles_and_renders:
        pass_at: 1.0                   # value at or above this passes the mechanical gate
  human_review: {rubric: ../../rubrics/rendering-engine-visual.yml}
  acceptance: {requires: [mechanical, human]}
metrics:
  primary: mean_score:compiles_and_renders
  secondary: [render_rate, accepted_rate, human_pass_rate_given_artifact]
```

`lint` checks all of this without importing your Python. It fails when:

- `sources` omits `file`, or omits a Python, YAML, Dockerfile, requirements or shell file
  next to the task. These files are part of the run's identity, so they must be declared;
- a compose `image:` or Dockerfile `FROM` is a mutable tag. Pin it as `name@sha256:<digest>`
  or set `allow_mutable_images: true`;
- `cost_limit` or `time_limit` is missing, or the model has no registered price;
- a provider has no Inspect route. Supported: `meta` (through its OpenAI-compatible
  endpoint, `META_BASE_URL` overrides the default), `openai`, `anthropic`.

## The task contract

Tamesu writes a frozen dataset to `runs/<run-id>/inspect/input.jsonl`: exactly the selected
items, with the Tamesu item ID as the Inspect sample ID and this arm's prompts already
rendered into `system` and `user` messages. It passes the path to your task as a
`dataset` argument.

```python
@task
def rendering_engine(dataset: str) -> Task:
    return Task(dataset=json_dataset(dataset), solver=[generate()], scorer=..., sandbox=...)
```

Everything else comes from `eval.yml`, not from the task file: model, effort, `max_tokens`,
temperature, retries, per-call timeout, concurrency, and the limits. Set only what the
manifest cannot (solver, scorer, sandbox, `message_limit`).

Export artifacts from the scorer while the sandbox is still alive, using the stdlib-only
helper. Scorers never write item evidence directly.

```python
from tamesu.inspect_support import stager

stage = stager(state.sample_id)
stage.add("source", "source/main.c", body, "text/x-c")
stage.add("render", "render.png", png_bytes, "image/png")
```

Scorer exceptions turn the sample into an error, so catch sandbox errors (`PermissionError`,
`TimeoutError`) and convert them into scores. On the Docker sandbox, read binary output with
`exec` (`base64 file`), not `read_file`: `read_file` uses `docker cp`, which cannot see tmpfs
mounts.

## What Tamesu records

Per item, `result.yml` holds the Inspect scorer results unchanged, the validated artifacts
(`items/<id>/artifacts/...` with role, SHA-256, size, media type), `missing_artifacts`
(required roles that never arrived), `rejected_artifacts` (with reasons), token usage, cost
(computed from Tamesu's price registry), the model's `stop_reason`, and any limit that was hit.

Ingestion rejects, per artifact: absolute or traversing paths, backslashes, symlinks, files
outside the staging area, undeclared roles, bad media types, duplicates, files over
`max_bytes` (default 25 MiB), and more than 200 artifacts per item. A rejected artifact does
not fail the item; it appears in `rejected_artifacts`.

State mapping is explicit. A log file existing never means `complete`.

| Inspect | Tamesu item |
|---|---|
| Sample with scores for every listed scorer | `complete` (a hit limit is recorded, not failed) |
| Sample `error` | `failed`, retryable |
| No sample (cancelled or errored first) | `failed`, retryable (`MissingSample`) |
| Sample without a listed scorer | `failed`, permanent (`MissingScore`) |

The Inspect log's own `status` is recorded but never trusted: a log can say `success` while
every sample errored, and a cancelled run exits 0. Unexpected or duplicate sample IDs abort
ingestion as a framework error.

`run.yml` records an `execution` block: backend, Inspect and adapter versions, resolved
model URI, base-URL origin (never credentials), task, limits, and `logs`, one entry per
Inspect attempt with path, SHA-256 and status. The call log gets an `inspect_log` event per
attempt. `rescore` verifies those digests first and refuses stale or tampered logs.

## Review and acceptance

`artifact_bundle` reuses the image review pipeline (blinded pack export, import, acceptance,
`evaluation-report.md`, `close` gate). Declare `execution.artifacts.review: <role>`, where the
role holds one image per item, then add `human_review` and `acceptance` as in an image eval.

Ingestion shapes each result like an image-generation result so the shared code applies:

- `outcome.status` is `generated` when the review artifact exists, has an `image/*` media
  type and decodes; otherwise `invalid_artifact` with a reason (`missing_review_artifact`,
  `review_artifact_not_an_image`, `undecodable_image`). An invalid item is a rejection that
  stays in the denominator, never a skip.
- `output` points at the review image (path, SHA-256, size). Changing the file after the run
  makes it stale and removes it from review.
- Each scorer result gets a `pass` flag from `pass_at` (default 1.0). The mechanical column
  of acceptance is "every listed scorer passed".
- Reviewers see only the image and the dataset item's `input`, never the model, arm or run.

Metrics added when a review artifact is declared: `render_rate`, `mechanical_pass_rate`,
`mechanical_pass_rate_given_artifact`, `accepted_rate`, `accepted_rate_given_artifact`,
`human_pass_rate`, `human_pass_rate_given_artifact`, `review_completion_rate`,
`cost_per_accepted_usd`. Rates without `given_artifact` use all planned items as the
denominator.

Reviewing is limited to a single image role. Model judges are not supported for
`artifact_bundle`, and `promote` (which builds a product dataset from accepted images) only
applies to `image_generation`.

## Budget, limits and resume

`plan` and `run` size exposure from `cost_limit` per sample, one attempt each, because an
agent loop has no per-call estimate. Inspect enforces `cost_limit` using the prices in
Tamesu's registry. `retries` maps to Inspect's model-API retries; whole-sample retries are
not used. A run where every sample errored is `partial`, never `complete`.

`resume` re-runs only items that are not final, in a new Inspect attempt linked to the same
Tamesu run. It refuses to resume if the frozen dataset no longer matches the items and
prompts. Mid-sample checkpoint resume is not used.

## Identity

The specification fingerprint includes the execution block, the resolved model URI, the
base-URL origin, the Inspect version, and a digest of the rendered frozen dataset. The
content fingerprint covers every declared source file. Changing the Inspect version, an
image reference, a limit, a prompt or the task makes earlier runs stale.

## Privacy and packaging

Inspect transcripts contain full rendered prompts, outputs, and the repository's git
remote URL. Tamesu therefore:

- writes them under `runs/<run-id>/inspect/` (ignored by git by default);
- disables raw model-API logging;
- never includes `runs/*/inspect/**` or any `.eval` file in `full` or `rescorable`
  packages. The ingested artifacts and the Tamesu call log still ship.

Read logs with Inspect's `read_eval_log`; the `.eval` format is a compressed archive that
is not meant to be parsed by hand. View them with `inspect view --log-dir <run>/inspect`.

## Trust boundary

Docker isolates only the commands your task runs through Inspect's sandbox API. The Python in
the task file, its solvers and its scorers runs on your machine. An Inspect-backed case is
therefore code, not just data.

- `lint` and `plan` read paths and hashes and never import project Python.
- Cases created by `unpack` or `fork` get a `.untrusted-origin` marker. `tamesu run` and
  `resume` refuse to execute their Inspect tasks until you review the code and pass
  `--trust-code` (or delete the marker).
- Harden the sandbox yourself in compose: non-root user, `network_mode: none`,
  `read_only: true`, memory, CPU and pid limits, no host mounts. Remember `exec` on tmpfs
  if you compile there.

## Not yet supported

- **Model judges** for `artifact_bundle`, and reviewing anything other than one image role
  per item (for example a source file or a video).
- **Cancel from a live Tamesu run** is handled through the log (`cancelled` becomes retryable
  missing samples) but has only been tested against synthetic logs.
- **Tool-calling agents.** The adapter passes any solver through, but only a single-turn
  `generate()` task has been exercised.
- Backends other than `meta`, `openai` and `anthropic` routes, and tasks other than
  `artifact_bundle` on `inspect`. The native task types run only on `native`.
