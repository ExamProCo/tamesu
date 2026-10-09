## Challenge

With each experiment, I have to extend the lowest execution layer to support another way
of running models. So far, this repository has two examples:

- classification with text
- image generation

In another repository, `meta-cases`, I am testing meta-models that generate C code. To
complete that experiment, Tamesu would need to materialize the C project, compile it, run
it and inspect its output.

I need more flexibility at the execution level. The reason I built Tamesu, though, is that
it works from the business use case down to runs. It asks a question and expects technical
uncertainty, a technical journal, a conclusion and next steps. This is what I use in my
bootcamps, and it roughly mirrors Canada's SR&ED process.

As far as I know, Inspect AI does not provide that experiment narrative. Can the two be
combined?

I value Tamesu's logging, but if Inspect provides equivalent execution evidence, it may
not need to be duplicated. The question is where the systems should meet, whether they
should meet at all, or whether Tamesu should keep building its own execution primitives.

---

# Plan: Tamesu above, Inspect AI below

## Decision

Yes, they can be combined, and the boundary is useful:

| Layer | Question | Owner |
|---|---|---|
| Why | What business problem, hypothesis and technical uncertainty are we testing? What did we conclude and what comes next? | **Tamesu** |
| What is owed | What frozen run matrix, budget, fingerprints and acceptance gates define the experiment? | **Tamesu** |
| What an output means | Is this structured text, an image, or an artifact bundle? How is it reviewed and reported? | **Tamesu task** |
| How it executes | How does the model use tools, a sandbox and multiple turns to produce the output? | **Execution backend: native Tamesu or Inspect** |
| Detailed execution evidence | What messages, model calls, tool calls and scorer explanations occurred? | **Inspect log** |
| Judgement | Did the result pass mechanical checks, blinded human review, judge agreement and acceptance gates? | **Tamesu, using ingested evidence** |

Inspect has tasks, datasets, eval sets and metadata, but it does not replace Tamesu's case,
hypothesis, journal, conclusion, banked-work or publication model. Tamesu, in turn, should
not grow its own general agent loop, sandbox and tool runtime.

**Recommendation: keep Tamesu as the experiment system and add Inspect as an optional
execution backend. Do not make `inspect` the public Tamesu task type.** In the current
code, a task owns more than execution: it also defines artifact semantics, mechanical
scores, reports, review and judge support. Calling Inspect a task would conflate these
responsibilities and make later convergence harder.

The first Inspect-backed use case can introduce one reusable semantic task such as
`artifact_bundle`, capable of ingesting declared artifacts and scores. Existing
`structured_text` and `image_generation` tasks stay on the native backend initially.

## Target shape

The hierarchy remains `Case -> Experiment -> Eval -> Run`. A run chooses both a semantic
task and an execution backend.

```text
cases/c-rendering-engine/
├── case.yml
├── datasets/rendering-engine-prompt-v1/dataset.yml
└── experiments/muse-version-comparison/
    ├── README.md                         # uncertainty, journal, conclusion
    ├── inspect/
    │   ├── task.py                       # solver/scorer/sandbox wiring
    │   ├── scorers.py
    │   ├── compose.yaml
    │   ├── Dockerfile
    │   └── requirements.lock
    └── evals/executable/
        └── eval.yml
```

Illustrative manifest shape (the exact schema is a Phase 1 design decision):

```yaml
task: artifact_bundle
execution:
  backend: inspect
  file: ../../inspect/task.py
  task: rendering_engine
  task_args: {scene: lighting-demo}
  sources:                         # explicit identity boundary
    - ../../inspect/task.py
    - ../../inspect/scorers.py
    - ../../inspect/compose.yaml
    - ../../inspect/Dockerfile
    - ../../inspect/requirements.lock
  limits:
    cost_limit: 0.25               # required: finite dollars per sample
    time_limit: 600                # required: wall-clock ceiling for one sample
    token_limit: 32000             # optional aggregate token ceiling for one sample
  artifacts:                       # nested under execution as implemented
    required: [source, compiler-log, render]
    review: render
# max_tokens stays in defaults.parameters (output ceiling per model call)
runs:
  - model: muse-spark-1.3
    provider: meta
    arms: [verbatim-prompt]
  - model: muse-spark-1.2
    provider: meta
    arms: [verbatim-prompt]
```

The existing required manifest fields (`dataset`, `defaults`, `arms`, `evaluation` and
`metrics`) still apply. The example above only shows the new parts.

## Integration contract

One Tamesu `RunSpec` maps to one Inspect evaluation and one Inspect log. Tamesu remains
the source of truth for the selected item IDs, arm, rendered prompts, repetition, model,
limits and budget.

Before invoking Inspect, the adapter writes a frozen derived dataset beneath the run
directory. It contains exactly the selected Tamesu items, with the Tamesu item ID as the
Inspect sample ID and the already-rendered messages for that arm. The Inspect task may
provide solvers, tools, scorers and sandbox configuration, but it must not silently select
a different dataset. This avoids having two competing sources of truth.

The adapter contract is:

1. Resolve the Tamesu run and write `inspect/input.jsonl` plus its digest.
2. Resolve the Tamesu provider/model to an explicit Inspect model URI. For Muse, first
   try `openai-api/meta/<model>` with the compatible endpoint; use a custom `ModelAPI`
   only if the compatibility gaps are material. Record the URI, base-URL origin (without
   credentials), model args and exact Inspect version in `run.yml`.
3. Run the named Inspect task with the frozen dataset path and limits. Provider credential
   checks, request retries and concurrency must be backend-specific; the existing native
   Tamesu provider preflight cannot be reused unchanged.
4. Have the Inspect scorer copy required sandbox outputs into a per-sample staging area
   beneath `inspect/` and write a declared artifact manifest before the sandbox is
   destroyed. It must not write directly into Tamesu's canonical item directory. A scorer
   can read sandbox files, but returning a `Score` alone does not preserve arbitrary files.
5. Ingest each Inspect sample into Tamesu's canonical item evidence. Require a one-to-one
   match on sample ID and reject missing, duplicate or unexpected samples. Verify artifact
   paths, media types, sizes and SHA-256 digests before copying them into the item directory.
6. Map Inspect status (`success`, `cancelled`, `error`), sample completion/errors, usage,
   limits and scores explicitly to Tamesu run/item states. Do not infer `complete` merely
   because a log file exists.
7. Build the normal Tamesu report and acceptance evidence from the canonical ingested
   results. Inspect scores are recorded with their scorer name and provenance; Tamesu
   must not silently recompute or rename them.

For the C example, each item should end with roles similar to:

```text
items/<item-id>/
├── result.yml
├── source/                 # generated project, safely extracted
├── compiler.txt
├── run.txt
└── render.png              # artifact selected for image review
```

The current image review and judging code is not automatically reusable: it expects the
`image_generation` result shape. Phase 1 must either generalize it to a declared review
artifact role or add a small adapter from `artifact_bundle` results. After that change,
the review workflow can remain unchanged.

## Identity and reproducibility

Hashing only `task.py`, `compose.yaml` and `scorers.py` is insufficient. The Inspect-backed
content fingerprint must include:

- the normal Tamesu case, dataset, prompt, schema and framework inputs;
- the execution block and generated `inspect/input.jsonl`;
- every explicitly declared Inspect source file;
- Dockerfile/build-context inputs and either a built image digest or immutable image
  reference (a mutable image tag is not reproducible);
- the Inspect version, adapter version, resolved model URI, non-secret model args, solver,
  scorer, sandbox and limit configuration.

Do not attempt unreliable transitive Python-import discovery. Require `execution.sources`
and fail lint when a referenced local sandbox/build file is outside the declared identity
boundary. Record the final inventory in `run.yml` as Tamesu does today.

## Logging, privacy and packaging

Keep both logs, with different jobs:

- Tamesu JSONL remains the durable lifecycle/index log. Add an `inspect_log` event with
  the final Inspect log path, SHA-256, Inspect version and status. Keep `run_started`,
  `item_finished`, `artifacts_written`, `framework_error` and `run_finished` semantics.
- The Inspect `.eval` log is detailed supporting evidence and is always read through
  Inspect's Log File API, never by decoding its storage format directly.

Inspect transcripts improve diagnosis and audit because they retain rendered messages,
outputs and tool activity. The trade-off is confidentiality, repository size and a larger
disclosure surface: rendered prompts may contain dataset values or personal/proprietary
content. Disabling raw model-API logging does **not** remove the ordinary conversation
transcript.

Default policy for the first implementation:

- place Inspect logs under `runs/<run-id>/inspect/` and digest them after completion;
- treat a post-ingestion digest mismatch as stale/corrupt evidence;
- disable raw model-API request/response logging unless explicitly requested;
- keep Inspect logs out of version control and all publication profiles by default;
- add an explicit opt-in publication policy later if redaction for `.eval` logs is proven.

This packaging work is part of Phase 1, not out of scope. Today a `full` package includes
all non-hidden files and only scrubs JSONL logs, while a `rescorable` package excludes a
`logs/` directory or `*.jsonl`. With the proposed path, both could accidentally publish an
unscrubbed `.eval` transcript unless `collect_payload()` is changed and tested.

## Budget, limits and resume

Inspect-backed budgeting has several distinct controls which must not be collapsed into
one `limits` value:

| Control | Meaning |
|---|---|
| `max_tokens` | Maximum output tokens for one model generation |
| `token_limit` | Aggregate tokens consumed by the entire sample/agent loop |
| `time_limit` / `working_limit` | Per-sample wall-clock / active-work ceiling |
| `cost_limit` | Per-sample dollar ceiling when model cost data is configured |
| `max_retries` | Model API retries |
| `retry_on_error` | Whole-sample retries |
| `max_samples` | Sample concurrency |

Tamesu should still reject a plan whose conservative maximum exceeds `budget_usd`. When
agent call counts cannot be estimated, require a finite Inspect `cost_limit` or an explicit
budget override rather than treating an unknown maximum as zero. After execution, ingest
Inspect usage and cost; verify Muse's usage fields and pricing during the spike.

Inspect now supports retrying incomplete logs and optional sample checkpointing, so resume
is not necessarily weaker than Tamesu's item-level resume. The integration still needs a
policy: a retried/checkpoint-resumed Inspect evaluation must be linked to the same Tamesu
run, and only the final successful log/artifact set is bankable. Record the retry lineage
and digest every retained log. Do not promise mid-agent resume until the checkpoint path
has been demonstrated with this task.

## Security boundary

Docker isolates commands requested through Inspect's sandbox API; it does not sandbox
the Python in `task.py`, solvers or scorers, which executes in the Inspect host process.
An Inspect-backed case is therefore executable code, not merely a data manifest.

Phase 1 must document and enforce the trust boundary:

- `lint` and `plan` inspect paths and hashes without importing project Python;
- `run` requires trusted local Inspect code (published/unpacked cases are not executed
  without an explicit trust decision);
- the C sandbox runs as non-root with no Docker socket or host mounts, no network, bounded
  CPU/memory/processes/time/output, and only the required writable workspace;
- Tamesu alone promotes staged artifacts into canonical item evidence; ingestion rejects
  absolute paths, traversal, symlinks escaping the workspace, oversized files and
  undeclared artifact roles.

## Trade-offs

| Property | Result |
|---|---|
| Business-down structure, journal and SR&ED-style record | **Kept** |
| Frozen plan, owed/banked work and acceptance gates | **Kept**, once identity and state mapping are implemented |
| Sandboxed code, tools, agents and multi-turn execution | **Gained** |
| Detailed transcripts and log viewer | **Gained**, with a deliberate privacy cost |
| Budget ceiling | **Kept conditionally** through conservative planning plus finite runtime limits |
| Immutable evidence | **Kept** by canonical ingestion and digest-pinning; Inspect logs alone are editable |
| Resume | **Potentially gained**, but checkpoint/retry lineage needs a proven mapping |
| Blinded image review and judge agreement | **Kept after** review-artifact generalization; not automatic today |
| Portable, dependency-light execution | **Kept for native runs; lost for Inspect-backed runs** |
| One log format | **Lost intentionally**: Tamesu is the index, Inspect is detailed evidence |

## Alternatives considered

- **Keep building execution primitives.** The next task requires sandboxing, compilation,
  process capture and artifact extraction, followed eventually by agent loops. This grows
  an execution framework inside Tamesu.
- **Move entirely to Inspect.** This loses Tamesu's hypothesis/journal, owed-work identity,
  acceptance and publication model while adding no capability at the business layer.
- **Run Inspect separately and link it by hand.** This breaks banked-work, fingerprints,
  budget and evidence ingestion—the main value Tamesu adds.
- **Make `inspect` a Tamesu task.** This is expedient but mixes execution with artifact and
  reporting semantics. It is acceptable only as throwaway spike code, not the public API.

## Phases

### Phase 0: Spike (time-boxed, no core changes)

In `examples/c-rendering-engine`, build a plain Inspect task that uses the existing item
and prompts, asks each Muse model for a project, compiles it in a Docker sandbox, runs it,
and renders an image. Pin the Inspect version used by the spike.

Answer with executable evidence:

1. Does `openai-api/meta/<model>` preserve Muse tool calls, usage, request IDs and the
   `effort` parameter? If not, is the Anthropic-compatible path better, or is a custom
   `ModelAPI` justified?
2. Can Tamesu read successful, cancelled, errored and partially completed samples through
   the public Log File API, including usage, limits, scores and retry information?
3. What explicit scorer/store convention reliably exports C source, compiler output and
   a binary PNG before sandbox cleanup?
4. What happens on Ctrl-C during generation, compilation and scoring? Can retry and
   checkpoint resume be associated safely with one Tamesu run?
5. Exactly which rendered prompts, outputs, environment details and raw API payloads are
   present with default logging and with raw model-API logging disabled?
6. Can the Docker sandbox compile and render with networking disabled and the proposed
   CPU, memory, process, time and output limits?

Exit criteria:

- all three Muse versions run from one frozen Tamesu-derived sample each;
- Inspect sample IDs match Tamesu item IDs one-to-one;
- source, compiler log and PNG are exported and independently hashed;
- a compile failure and an interrupted run are correctly observable through the Log API;
- usage/cost and limit semantics are recorded, or their absence is explicitly understood;
- transcripts are visible in `inspect view`, with their disclosure contents documented.

If the artifact-export or provider compatibility contract fails, stop and reassess before
changing Tamesu core.

### Phase 0 results (run 2026-10-06, inspect-ai 0.3.277)

The spike lives in `examples/c-rendering-engine/experiments/muse-version-comparison/inspect/`
(`task.py`, `compose.yaml`). It is deliberately plain Inspect with no Tamesu code. Scratch
output goes to `staging/`, which is git-ignored.

**Verdict: the approach is viable. Proceed to Phase 1 with the adjustments below.**

| Exit criterion | Result |
|---|---|
| Muse reachable through `openai-api/meta/<model>` | **Yes.** Needs `META_API_KEY` and `META_BASE_URL=https://api.meta.ai/v1` in the environment, `pip install openai`, and `--reasoning-effort` for `effort`. Usage, including reasoning tokens, is returned. `total_cost` is `None`, so cost still comes from `pricing.py`. Tool calls were not exercised, the spike is single-turn |
| Three Muse versions run | **Two of three.** 1.2 and 1.1 compiled, ran and rendered (score 1.0, 800x600 raytraced scene). 1.3 returned 503 `service_overloaded` on every attempt and exhausted its retries (410 s) |
| Source, compiler log and PNG exported | **Yes**, after two fixes (below). PPM was converted to PNG on the host with Pillow |
| Compile failure and interrupt observable | **Yes.** Compile failure scored 0.25, infinite loop scored 0.5 (sandbox timeout caught), no-files and path traversal scored 0. Ctrl-C produced log `status: cancelled`, exit code 0, and the container was removed |
| Usage/cost and limits | Usage per sample is in `sample.model_usage`. Cost is not provided (see above). Not yet tested: `token_limit`, `cost_limit` and checkpoint resume |
| Transcripts and disclosure | Documented below. `inspect view` was not opened |
| Sandbox restrictions | Verified from inside a compiled program: non-root (uid 1000), network blocked, root filesystem read-only, pids capped at about 124 of 128, a 2 GB allocation OOM-killed (exit 137) |

**Findings that change the plan**

1. **Log `status` is not run success.** With `fail_on_error=False` (needed so one sample's
   error does not abort the run), the 1.3 log reported `status: success` while its only
   sample had `error: RetryError(... InternalServerError)` and no scores. The ingester must
   judge from per-sample `error`, `limit` and scores, never from log status. Cancel is
   `status: cancelled`; the Python call and CLI still exit 0, so exit code is no signal
   either. Phase 1's state mapping and its tests must cover all three.
2. **Without `fail_on_error=False` one scorer exception killed the task**: "no samples
   completed". Any exception in scorer code, such as a `PermissionError` from `exec`,
   aborts everything. Scorers must catch sandbox errors and convert them into scores.
3. **`SandboxExecutionError` types.** `exec` raises `PermissionError` (exit 126) and
   `SandboxTimeoutError` (a `TimeoutError`) instead of returning a failed result. Both need
   handling.
4. **`read_file` does not work on tmpfs.** On the Docker sandbox it uses `docker cp`, which
   cannot see tmpfs mounts, so the hardened `/work` tmpfs returned "No such file".
   Artifact export works by `exec`-ing `base64` and decoding on the host. The artifact
   contract should specify that mechanism, with a size cap, not `read_file`.
5. **tmpfs needs `exec`**, otherwise compiled binaries fail with "Permission denied".
6. **The base URL is not recorded.** `eval.model_base_url` was `None` even though
   `META_BASE_URL` was set. To record the origin in `run.yml` as the plan requires, the
   adapter must pass `base_url` explicitly as a model argument.
7. **Disclosure.** No API keys appear in the log. The full system and user prompts and the
   complete completion do (default logging; no `api_call` raw payloads). The log also
   records the git remote URL and commit (`origin=git@github.com:...`, `dirty=True`),
   so a published `.eval` would leak the remote. This supports the keep-out-of-packages
   default.
8. **`.eval` files are zstd-compressed zips** that Python's `zipfile` cannot read. Reading
   only through `read_eval_log` is mandatory, as the plan says.
9. **Provider flakiness is real.** One of three Muse models was unavailable for ten minutes
   and another timed out in a first probe. Retry policy (`--max-retries`) and an overall
   timeout need to be explicit in the eval, and a run where every sample errors must be
   `failed` or `partial`, never `complete`.
10. **Muse spent most tokens reasoning**: 1.1 used 10.6k output tokens, 5.1k of them
    reasoning, against the 16k ceiling. With a low `max_tokens` the content comes back
    `null` with `finish_reason: length`. `max_tokens` for these evals must leave room for
    reasoning.

**Left for Phase 1 to prove**: `token_limit`/`cost_limit` behaviour, checkpoint resume with
the same Tamesu run, `inspect view`, and tool-calling agents. Muse 1.3 should be re-run when
the backend recovers.

### Phase 1: Execution backend and evidence contract

- Add an execution-backend protocol with `native` as the default and `inspect` optional.
  Keep the existing per-item runner behind the native backend; do not add an optional
  `execute_run` method to the semantic Task protocol.
- Add and validate the `execution` block and reusable `artifact_bundle` task. Define the
  canonical artifact manifest, score mapping and review-artifact role.
- Add backend-specific model resolution, credential checks, cost planning, limits,
  cancellation and run-state mapping.
- Implement the frozen derived dataset, one-to-one sample validation, artifact ingestion,
  report generation and digest-pinned `inspect_log` event.
- Extend fingerprints and provenance as described above.
- Exclude `.eval` transcripts from Git and publication profiles by default; add packaging,
  secret/privacy and tamper tests.
- Add interrupted/error/duplicate/missing-sample tests and sandbox path/size/symlink tests.
- Make `inspect-ai` a version-bounded optional extra (`pip install tamesu[inspect]`).

Streaming sample completion into Tamesu's stderr progress display is desirable but not a
Phase 1 acceptance criterion unless the public Inspect API exposes a reliable callback.
Correct final state and evidence are more important than duplicating Inspect's live UI.

### Phase 1 status (implemented 2026-10-07)

Implemented and tested (238 tests pass, 35 of them new in `tests/test_inspect_backend.py`):

- **Backend protocol** (`src/tamesu/backends/`). `native` wraps the existing per-item loop
  unchanged; `inspect` is optional (`pip install 'tamesu[inspect]'`). The semantic Task
  protocol gained only `supported_backends`; there is no `execute_run` hook on it.
- **`artifact_bundle` task** and the `execution` manifest block, with schema, lint rules
  (declared sources, pinned images, finite `cost_limit`/`time_limit`, priced models, Inspect
  route), and task/backend compatibility checks.
- **Frozen derived dataset**, one-to-one sample IDs, rendered prompts; unexpected or
  duplicate samples abort ingestion.
- **Artifact staging and ingestion** (`tamesu.inspect_support.stager`): per-artifact
  rejection of traversal, symlinks, undeclared roles, oversize and duplicates.
- **State mapping** from sample error, missing sample, missing scorer; log status is
  recorded but never trusted (Phase 0 finding 1).
- **Identity**: execution block, model URI, base-URL origin, Inspect and adapter versions,
  image references, frozen-dataset digest and every declared source are fingerprinted.
- **Digest-pinned logs** in `run.yml` and an `inspect_log` call-log event; `rescore`
  refuses tampered logs.
- **Budget**: exposure is `cost_limit` per sample with one attempt; Inspect enforces it
  with Tamesu's prices.
- **Resume**: re-runs only non-final items in a new linked attempt; refuses a changed
  frozen dataset.
- **Packaging/privacy**: `runs/*/inspect/**` and `.eval` never enter packages; git ignores
  them; packaging now validates `execution.file` and `sources` references and no longer
  assumes every eval has an `output_schema`.
- **Trust**: `unpack`/`fork` mark cases `.untrusted-origin`; `run`/`resume` need
  `--trust-code`.
- **Example**: `examples/c-rendering-engine/.../evals/executable` with the compose image
  pinned by digest.

Verified live through `tamesu run` (scratch project copy, real Muse API and Docker):
Muse 1.2 and 1.1 compiled, ran and rendered an 800x600 image, scored 1.0, with source,
compiler log, run log and PNG ingested and hashed. Muse 1.3 failed every sample with
`RetryError(InternalServerError)` after 5m55s and was recorded as a `partial` run with a
retryable failed item, not `complete`. Changing `effort` correctly made the earlier run
stale.

**Not done, deliberately** (also listed in `docs/execution-backends.md`):

1. **Review-artifact generalization.** `artifact_bundle` has `supports_review = False`:
   the image review, judge and acceptance code reads the `image_generation` result shape.
   The `artifacts.review` role is reserved but unused. This is the next slice.
2. **Live cancel.** The cancelled-log mapping is tested with synthetic logs only; a real
   Ctrl-C through `tamesu run` was not exercised.
3. **Agents and tool calls**, `token_limit`/`cost_limit` actually tripping, and checkpoint
   resume were not exercised against a live model.
4. **Progress lines** for Inspect samples beyond one line per finished item.
5. **Example layout.** `examples/c-rendering-engine/` has no `cases/` wrapper, so it cannot
   run in place the way the other examples do; testing used a scratch copy laid out as
   `cases/c-rendering-engine/`.

Findings during implementation:

- Packaging crashed on any eval without `output_schema` (found by the new package test).
- Tests patch `tamesu.runner.get_provider`; the native backend resolves providers through
  that name so they keep working.
- Changing `effort` or `max_tokens` changes the specification, so earlier runs become stale
  (intended, but it matters when tuning reasoning budgets).

### Phase 2: Executable C rendering evaluation

- Freeze the compiler/toolchain image by digest.
- Score ordered partial-credit outcomes: project exported, compiles, runs, produces a
  decodable render, meets deterministic comparisons, and passes qualitative review.
- Keep deterministic compile/run/render results as Inspect-origin mechanical evidence;
  feed only the declared rendered PNG into blinded Tamesu review/judging.
- Write the experiment journal explaining what the original one-shot text eval could not
  establish and what executable evidence changed.

### Phase 2 status (implemented 2026-10-07)

Delivered:

- **Review-artifact adapter** (the Phase 1 gap). `artifact_bundle` results are shaped like
  image-generation results (`outcome`, `output`, `prompt.input_sha256`, scorer `pass`
  flags) so the existing blinded review, acceptance, report and close-gate code runs
  unchanged. `supports_review` is now true for the task; `supports_promotion` is a new flag,
  true only for `image_generation`, so `promote` still refuses bundles. Model judges stay
  unsupported.
- **Deterministic checks**: five equal steps (exported, compiled, ran, rendered,
  *plausible*) with image statistics recorded in the score metadata. `pass_at` per scorer
  decides the mechanical gate.
- **Review feeds on the PNG only**, with a five-dimension visual rubric
  (`rubrics/rendering-engine-visual.yml`) and an acceptance policy of mechanical and human.
- **Toolchain image pinned by digest** (done in Phase 1).
- **Example restructured** to `examples/c-rendering-engine/cases/c-rendering-engine/` so it runs in
  place like the other examples.
- **Evidence banked** for all three models via `tamesu run`, a blinded review pack exported
  at `evals/executable/review/pack`, and `report` and `analysis.md` generated.
- **Journal** written in the experiment README (uncertainty, method, observations, findings,
  limits, provisional conclusion, next steps).
- 15 new tests (`tests/test_inspect_review.py`); 253 pass.

Results (one item, one repetition each): Muse 1.1 and 1.2 scored 1.0 (800x600 image, all
five steps); Muse 1.3 scored 0.2 (a compile error: `static double blo[BW*BH*3]` with
non-constant bounds).

**Owed by you** (cannot be done by the tooling):

1. The blinded human review of the two images. Until it is imported, `accepted_rate` is 0.0
   by design (pending items count as not accepted) and the eval cannot be closed without
   `--allow-incomplete-review`.
2. `analysis.md` still has its `TODO` interpretation sections. They are the author's.
3. A decision on repetitions: with one sample per model the comparison cannot support a
   ranking (see the journal's next steps).

Not done: model judges, reviewing non-image artifacts, a reference-image or determinism
comparison, repeated runs, and the `qualitative review` step being scored inside Inspect
(it is deliberately a Tamesu human-review column, never blended into the Inspect score).

### Phase 3: Decide on convergence

Only after Phase 2, assess whether `structured_text` and `image_generation` should also
gain an Inspect backend. Compare maintenance cost, evidence parity, performance, resume,
privacy and provider fidelity. Do not migrate working native paths merely to have one
executor.

## Decisions already made

- Muse exposes OpenAI- and Anthropic-compatible protocols, so Phase 0 should try those
  before a custom provider.
- Docker is acceptable for the bootcamp audience.
- The SR&ED-style journal does not depend on Tamesu's call-log field names.

## Not in scope

- Replacing Tamesu's case, experiment, manifest, journal or publication model.
- Replacing existing native tasks during the spike or first integration.
- Treating Inspect's dataset as the experiment source of truth.
- Publishing raw Inspect transcripts by default.
- Automatically trusting executable Inspect code received in a package.
