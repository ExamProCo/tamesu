# Tamesu: Implementation Plan

## Purpose

Build Tamesu as a small, file-based evaluation framework. It should make
experiments reproducible without requiring a database or hosted service, work for both
text and image-generation tasks, and preserve enough evidence to understand exactly what
was run, scored, and compared.

This design formalizes the working pattern used in Shiori while keeping the framework
domain-independent. Product-scanning alignment is the first end-to-end proving case, not
special logic in the framework.

## Core model

The framework has four levels:

| Level | Meaning | Owns |
|---|---|---|
| Case | A broad real-world problem | Background, shared datasets, assets, and success criteria |
| Experiment | A narrow hypothesis within a case | Experimental arms, task-specific prompts, schemas, and scoring code |
| Eval | A frozen question and sampling plan | Dataset selection, run matrix, repetitions, metrics, budgets, and completion state |
| Run | One execution of one expanded eval specification | Exact configuration, outputs, logs, scores, cost, and provenance |

The distinction between an experiment and an eval is important. An experiment may be
investigated more than once. Each eval is a specific, reviewable measurement plan within
that experiment.

For example:

- Case: can image models produce a believable product-scanning interaction?
- Experiment: does supplying a scanner reference image improve alignment?
- Eval: compare `product-only` and `product-plus-scanner` over 12 products, 3 repetitions,
  with a fixed prompt and rubric.
- Run: one model, arm, parameter set, and repetition executing that eval.

## Design principles

1. **Every run belongs to a question.** Normal runs live under a declared eval. Unplanned
   probes go into a `scratch` eval and cannot appear in a real leaderboard.
2. **The manifest is the plan, not the result.** `eval.yml` states what is owed. Files on
   disk establish what has actually happened.
3. **Never overwrite evidence.** Re-running creates a new run or resumes a compatible
   partial run. It never replaces an existing completed run.
4. **Only complete samples count.** A run with failed or missing items remains useful for
   diagnosis but cannot satisfy a planned repetition.
5. **Separate generation from scoring.** Stored outputs can be rescored after evaluator
   changes without paying to generate them again.
6. **Record identity from content, not names.** A familiar label does not make an old run
   compatible with a changed prompt, dataset, schema, or evaluator.
7. **Prefer deterministic scoring.** Use model judges only for qualities that cannot be
   measured mechanically, and keep model and human judgments visibly separate.
8. **Make cost visible before execution.** Planning reports item count, estimated calls,
   maximum exposure, and any prices that are unknown.
9. **Keep the filesystem understandable.** A person should be able to inspect an eval and
   its evidence without running the framework.

## Target repository layout

```text
tamesu/
  README.md
  pyproject.toml
  .env.example
  .gitignore
  examples/
    support-ticket-classification/
      cases/
  src/tamesu/
    cli.py
    config.py
    discovery.py
    planner.py
    runner.py
    identity.py
    artifacts.py
    logging.py
    scoring.py
    reporting.py
    models.py
    providers/
      base.py
      openai.py
      anthropic.py
      gemini.py
      meta.py
      bedrock.py
    tasks/
      base.py
      image_generation.py
      structured_text.py
  schemas/
    case.schema.json
    dataset.schema.json
    eval.schema.json
    report.schema.json
  tests/
    fixtures/
    test_*.py
  cases/
    product-scanning-alignment/
      README.md
      case.yml
      datasets/
        product-scanning-v1/
          dataset.yml
          products/
          scanner/
      experiments/
        scanner-reference/
          README.md
          prompts/
            system.md
            user.md.j2
          rubrics/
            visual-alignment.yml
          evals/
            reference-ablation/
              eval.yml
              leaderboard.md
              logs/
                <run-id>.jsonl
              runs/
                <run-id>/
                  run.yml
                  report.yml
                  items/
                    <item-id>/
                      result.yml
                      generated.png
                      judge.yml
```

The exact implementation language is not part of the file contract. Python 3.12 is the
recommended first implementation because the provider and image tooling ecosystem is
strong, with Typer for the CLI, Pydantic for manifest validation, PyYAML for YAML, and
pytest for tests. The on-disk contract should remain usable from other languages.

The original proposed `runs/<model>/report.yml` shape should become
`runs/<run-id>/report.yml`. A model can have multiple arms, settings, and repetitions, so
the model name alone is not a safe run identity.

## What belongs at each level

### Case

`case.yml` contains stable case metadata:

```yaml
schema_version: 1
name: product-scanning-alignment
title: Product Scanning Alignment
description: >
  Generate plausible images of a person holding a product in one hand and aiming a
  handheld scanner at its barcode with the other.
default_task: image_generation
```

The case directory also owns reusable source assets and versioned datasets. It must not
own provider configuration or the output of a particular experimental arm.

### Experiment

The experiment directory contains the material shared by its evals:

- the hypothesis and experimental arms;
- prompts and structured-output schemas;
- scoring rubric and any task-specific evaluator code;
- instructions for interpreting results and known confounds.

An experiment should change only one primary factor at a time. If both the prompt and the
reference-image strategy change, they should be represented as explicit arms rather than
described as a single model comparison.

### Eval

An eval is a declarative, versioned manifest. It answers these questions before spending
money:

- What question is being answered?
- Which dataset and exact items are included?
- What are the arms and run configurations?
- How many independent repetitions are required?
- Which metrics decide the result?
- Which judge or human review protocol is used?
- What are the concurrency, retry, timeout, and cost limits?
- When is the eval closed to further runs?

### Run

A run is one expanded entry from the eval plan. A run is not merely a provider call; it
usually contains one call per dataset item and may contain generation, judging, and retry
calls. The run owns all evidence needed to reproduce and audit that sample.

## Eval manifest contract

The first schema version should support a small, explicit surface rather than every
provider option. Provider-specific options live under `parameters` and are preserved in
the expanded run manifest.

```yaml
schema_version: 1
name: reference-ablation
status: draft # draft | active | complete
question: >
  Does a scanner reference image improve the geometric plausibility of product scanning?
description: >
  Compare the same model and prompt with and without a scanner reference. Product images,
  output dimensions, repetitions, and judging remain fixed.

task: image_generation
dataset: ../../../../datasets/product-scanning-v1/dataset.yml

defaults:
  repetitions: 3
  concurrency: 4
  timeout_seconds: 300
  retries: 2
  budget_usd: 25.00
  parameters:
    size: 1536x1024
    quality: high

arms:
  - id: product-only
    description: Product views only.
    references: [product]
  - id: product-plus-scanner
    description: Product views and the shared scanner reference.
    references: [product, scanner]

runs:
  - model: gpt-image-2
    provider: openai
    arms: [product-only, product-plus-scanner]
  - model: gemini-image
    provider: gemini
    arms: [product-only, product-plus-scanner]

evaluation:
  mechanical:
    - output_exists
    - decodable_image
    - expected_dimensions
  model_judges:
    - rubric: ../../rubrics/visual-alignment.yml
      model: <judge-model>
      repetitions: 2
      blind: true
  human_review:
    mode: blinded_pairwise
    required: true

metrics:
  primary: scan_alignment_pass_rate
  secondary:
    - barcode_visibility
    - scanner_aim
    - hand_anatomy
    - product_identity
    - overall_plausibility
    - generation_failure_rate
    - median_latency_ms
    - mean_cost_usd
```

Rules enforced by the schema and linter:

- names and IDs use lowercase kebab-case;
- every arm referenced by a run exists;
- `repetitions` is a positive integer;
- the primary metric is produced by the configured evaluator;
- a `complete` eval cannot execute more runs unless reopened by editing the manifest;
- all referenced paths resolve inside the repository;
- secrets and API keys are forbidden in manifests;
- provider/model combinations are validated before execution where possible.

`status: draft` allows linting and planning but no paid execution. `active` permits runs.
`complete` makes the eval a closed record and refuses further paid calls. Reopening it is
an explicit version-controlled change.

## Dataset contract

Datasets must use stable item IDs. File order and sequential numbering are not identities.

```yaml
schema_version: 1
name: product-scanning-v1
description: Initial product set with one canonical handheld scanner.
shared_assets:
  scanner:
    - scanner/front.png
    - scanner/side.png
items:
  - id: canned-coffee-01
    assets:
      product:
        - products/canned-coffee-01/front.png
        - products/canned-coffee-01/back.png
    metadata:
      package_type: can
      barcode_surface: back
  - id: chips-01
    assets:
      product:
        - products/chips-01/front.png
        - products/chips-01/back.png
    metadata:
      package_type: flexible-bag
      barcode_surface: back
```

Dataset linting verifies unique IDs, readable assets, allowed MIME types, checksums, and
required task fields. A run records the selected IDs and the SHA-256 digest of every input
asset. Editing a dataset later therefore cannot change the meaning of an old run.

Text tasks may store small inputs and deterministic expected values directly in the
dataset instead of referencing assets:

```yaml
items:
  - id: duplicate-charge
    input:
      message: I was charged twice for my subscription.
    expected:
      category: billing
      priority: high
      requires_human: true
```

The selected task adapter defines the allowed shapes of `input`, `assets`, and `expected`.
All inline values and referenced asset hashes contribute to run identity.

## Planning and run expansion

The planner expands each eval into concrete run specifications:

```text
models x arms x repetitions
```

Dataset items are work units inside a run, not separate runs. This keeps one repetition a
complete measurement over the declared dataset.

Each expanded run gets a human-readable label and an immutable ID:

```text
label: gpt-image-2--product-plus-scanner--rep2
run_id: gpt-image-2--product-plus-scanner--rep2--20261002-143012--a81c39f2
```

The timestamp prevents collision and the short fingerprint helps expose configuration
drift. Labels are for reading; fingerprints decide compatibility.

`tamesu plan` prints at least:

- total configurations, repetitions, items, and expected provider calls;
- calls already banked, partial, stale, and still owed;
- estimated and maximum cost, with unknown prices called out;
- models blocked by missing credentials;
- human-review work still required.

Planning never calls a paid provider.

## Run identity and reproducibility

Use two SHA-256 fingerprints, computed from canonical JSON with recursively sorted keys.

### Specification fingerprint

Represents the intended measurement:

- case, experiment, and eval;
- model and provider;
- arm and repetition;
- selected item IDs;
- generation parameters, seed when supported, timeout, and retry policy;
- judge configuration;
- relevant execution mode such as single-shot or staged.

### Content fingerprint

Represents the machinery and material used:

- hashes of datasets and input assets;
- rendered and source prompts;
- output schemas and rubrics;
- task adapter and evaluator source files;
- framework version and dependency lockfile;
- runtime version and Git commit, including whether the tree was dirty.

A finished run banks an eval slot only when both fingerprints match the current expanded
specification. A run with the correct label but different content is `stale`, not banked.
This is a direct lesson from the Shiori framework: matching only a name can accidentally
mix different prompts, datasets, or pipeline revisions into one result.

## Run artifacts

### `run.yml`

Written when the run begins and finalized when execution ends. It contains:

- run ID, label, state, and timestamps;
- fully resolved configuration with defaults expanded;
- selected item IDs;
- both fingerprints and the hash inventory used to derive them;
- framework, provider SDK, runtime, and Git versions;
- aggregate usage, cost, retry, and duration totals.

It does not contain score summaries; those belong in `report.yml`.

### Per-item `result.yml`

```yaml
item_id: canned-coffee-01
state: complete
attempts: 1
output:
  path: generated.png
  sha256: sha256:...
  media_type: image/png
  width: 1536
  height: 1024
generation:
  provider_request_id: ...
  revised_prompt: null
  latency_ms: 18422
  usage: {}
  cost_usd: 0.08
mechanical_scores:
  output_exists: true
  decodable_image: true
  expected_dimensions: true
```

Large binary data must never be embedded in YAML or JSONL. Store it once as an artifact
and refer to it by relative path and checksum.

### `report.yml`

The report is derived from stored item artifacts. It contains:

- completion and failure counts;
- aggregate primary and secondary metrics;
- per-item score summary;
- judge agreement and human-review completion;
- latency, token/image usage, and cost totals;
- confidence intervals when the sample size supports them;
- fingerprints copied from `run.yml`;
- the scorer version and scoring timestamp.

Because it is derived, `report.yml` may be replaced by an explicit `rescore` command.
Generation outputs, requests, responses, and original judgments are immutable.

### JSONL call log

Each provider attempt writes one line immediately and flushes it to disk:

```json
{"run_id":"...","item_id":"canned-coffee-01","stage":"generate","attempt":1,"at":"...","provider":"openai","model":"gpt-image-2","ok":true,"duration_ms":18422,"request":{},"response_metadata":{},"usage":{},"cost_usd":0.08,"artifacts":[{"path":"runs/.../generated.png","sha256":"sha256:..."}]}
```

Failed calls use the same shape with `ok: false` and a normalized error containing type,
message, retryability, and provider request ID. Logs must redact authorization headers,
API keys, signed URLs, and other configured secret fields.

## Execution lifecycle

```text
lint -> plan -> create run -> execute items -> judge -> score -> report -> compare -> close
```

Run states are:

```text
planned -> running -> partial | failed | complete
```

- `partial` means some valid work is reusable but the full declared sample is incomplete.
- `failed` means no safe continuation is possible, such as an invalid manifest snapshot.
- `complete` means every selected item has a valid terminal artifact and all mandatory
  judging is present.

Resume operates at item and stage granularity. Before reusing an artifact, the runner
checks both run fingerprints, the artifact checksum, and the current validator. Invalid
artifacts are moved to a timestamped quarantine name with a reason instead of deleted.

All YAML, JSON, and report writes use a temporary file in the destination directory,
`fsync`, and atomic rename. JSONL writes are append-only and synchronized so concurrent
workers cannot interleave lines.

`--force` means “create another fresh run.” It never means “overwrite this run.”

## Provider and task boundaries

Keep provider transport separate from task behavior.

### Provider adapter

A provider adapter is responsible for:

- credential discovery;
- translating normalized model parameters;
- making the request and normalizing the response;
- recording raw-enough request/response metadata for diagnosis;
- usage and cost calculation;
- timeout, retryability, and rate-limit classification.

It must not decide how an image is scored or which dataset items belong in a run.

### Task adapter

A task adapter is responsible for:

- validating dataset items and referenced assets;
- rendering prompts for an arm;
- invoking the appropriate provider operation;
- validating and storing output artifacts;
- exposing deterministic checks and task metrics;
- declaring which files contribute to the content fingerprint.

The first two built-in adapters should be `image_generation` and `structured_text`.
Avoid a plugin-loading system in the first release; use an explicit registry of task and
provider adapters. Introduce entry-point plugins only after a real external adapter needs
them.

## Scoring model

The framework must keep three types of evidence separate.

### Mechanical scores

These are deterministic and should run first. For image generation they include output
existence, decoding, MIME type, dimensions, and optional reference-image similarity or
object-detection checks. A mechanical failure may make the item ineligible for subjective
judging, but it remains a reported generation failure rather than disappearing from the
denominator.

### Model-judge scores

The product-scanning rubric should score atomic claims rather than one vague aesthetic
number:

| Dimension | Question |
|---|---|
| Product identity | Is the generated object recognizably the supplied product? |
| Barcode visibility | Is a barcode visible on a plausible product surface? |
| Scanner aim | Does the scanner window point toward the barcode? |
| Grip plausibility | Are product and scanner held in workable orientations? |
| Hand anatomy | Are both hands anatomically plausible and correctly attached? |
| Occlusion | Are scanner, hand, barcode, and product layered consistently? |
| Overall plausibility | Could this frame credibly depict an in-progress scan? |

The judge should return a strict structured response containing a bounded score, pass/fail
decision, reason codes, and a short explanation for each dimension. It must not see the
arm name, model name, expected winner, or generation cost. Prefer a different model family
from the generator and measure inter-judge agreement when multiple judgments are used.

The proposed primary metric is `scan_alignment_pass_rate`, where a pass requires all of:

- barcode visibility passes;
- scanner aim passes;
- grip plausibility passes;
- no severe anatomy failure;
- overall plausibility meets the preregistered threshold.

The exact threshold and weighting live in the rubric before the eval becomes active.

### Human review

For the first image eval, human review is required before making a model-selection claim.
Present outputs in randomized, blinded order. Pairwise comparison is preferred for the
reference-image ablation because the question is comparative. Review data is stored as an
immutable artifact and aggregated separately from model-judge scores.

Do not silently blend mechanical, model, and human scores into one number. The leaderboard
may show them together, but each column must identify its evidence source.

## Failure, retry, and denominators

- Retry only errors classified as transient. Schema failures and invalid images are model
  outcomes unless the provider response was truncated or corrupt in transport.
- Record every failed attempt and its cost.
- Cap retries per item and total run cost.
- Stop scheduling new work once the budget ceiling is reached; allow in-flight calls to
  finish and report their maximum exposure.
- Report failed items explicitly. Never exclude them in a way that makes a fragile model
  appear better.
- A run banks only after every item reaches the required terminal state and all required
  judgments exist.

For metrics based on valid generated images, show both the conditional score and the
end-to-end rate. For example, report `alignment_pass_rate_given_image` alongside
`alignment_pass_rate`, where the latter counts generation failures as failures.

## CLI contract

The initial CLI should be small and composable:

```sh
tamesu lint cases/product-scanning-alignment
tamesu list
tamesu plan product-scanning-alignment/scanner-reference/reference-ablation
tamesu run product-scanning-alignment/scanner-reference/reference-ablation
tamesu run ... --only gpt-image-2 --limit-items 2
tamesu status product-scanning-alignment/scanner-reference/reference-ablation
tamesu resume <run-id>
tamesu rescore <run-id>
tamesu compare product-scanning-alignment/scanner-reference/reference-ablation
tamesu leaderboard product-scanning-alignment/scanner-reference/reference-ablation
tamesu close product-scanning-alignment/scanner-reference/reference-ablation
```

Behavioral requirements:

- `lint`, `list`, `plan`, `status`, and `compare` never make paid calls;
- `run --limit-items` creates a partial probe and never banks a planned sample;
- `run --only` filters the plan without rewriting it;
- `resume` refuses any spec or content fingerprint mismatch and explains what changed;
- `rescore` reads stored outputs and judgments only;
- `close` refuses if required samples or human reviews are missing unless an explicit
  reason is recorded in the manifest.

## Leaderboard and comparison

`leaderboard.md` is a generated, reviewable projection of reports, not a source of truth.
It should include:

- eval question, dataset version, primary metric, and completion state;
- one row per model/arm configuration;
- number of complete repetitions and total items;
- mean and dispersion for the primary score;
- failure rate, mean cost, and median latency;
- human preference result when available;
- links to each run report;
- generation timestamp and scorer version.

For repeated runs, compare distributions rather than only the best sample. For paired
ablation arms, compare the same dataset items and report wins, losses, ties, and bootstrap
confidence intervals. Avoid declaring a winner when intervals are too wide or judge
agreement is poor.

## Storage and version-control policy

Track in Git:

- case, dataset, experiment, and eval manifests;
- prompts, schemas, rubrics, and evaluator code;
- small source fixtures;
- summarized reports and generated leaderboards when they are part of a recorded finding.

Never track:

- `.env` or credentials;
- temporary files;
- unredacted provider payloads containing secrets.

Generated images and large reference assets should be handled by Git LFS, DVC, or an
object store once volume requires it. The local MVP may keep them on disk and ignore them
in Git, but every report must retain relative artifact paths plus SHA-256 checksums. Moving
to remote storage later should change the storage backend, not the run format.

Raw logs may contain prompts or proprietary source material. Make log tracking an explicit
repository policy and redact secrets before the write, not as a later cleanup step.

## Test strategy

CI must never make paid model calls. Use fake providers with recorded responses.

Required test groups:

1. Manifest and path validation, including traversal rejection.
2. Deterministic plan expansion and label generation.
3. Canonical hashing and fingerprint drift detection.
4. Atomic writes and append-safe concurrent logging.
5. Complete, partial, stale, quarantined, and resumable run classification.
6. Retry and budget-stop behavior with recorded failed attempts.
7. Image artifact validation and checksum verification.
8. Deterministic metric aggregation and denominators.
9. Rescoring without provider calls.
10. End-to-end execution of a tiny two-item eval through fake generator and judge adapters.

Include golden fixtures for `run.yml`, `result.yml`, `report.yml`, and JSONL so accidental
file-format changes are visible in review. Schema changes require a `schema_version`
migration and backward-compatibility tests.

## Implementation phases

### Phase 1: File contract and read-only commands

- Create the package and CLI.
- Define Pydantic models and JSON Schemas for cases, datasets, evals, and reports.
- Implement repository discovery, safe path resolution, linting, plan expansion, and
  identity fingerprints.
- Add `list`, `lint`, `plan`, and `status` with fixture-based tests.

Exit criterion: the product-scanning eval expands deterministically and reports its exact
item/call count without contacting a provider.

### Phase 2: Artifact-safe execution

- Implement the artifact store, atomic writes, JSONL call logging, and secret redaction.
- Add the provider and task interfaces plus fake providers.
- Implement bounded concurrency, timeout, retries, budget enforcement, and item-level
  resume.
- Add `run` and `resume`.

Exit criterion: an interrupted fake-provider run resumes without repeating successful
items, overwriting artifacts, or banking an incomplete sample.

### Phase 3: Image-generation vertical slice

- Implement one real image provider adapter.
- Implement dataset/reference loading and prompt rendering.
- Store generated images with metadata and checksums.
- Add mechanical image validation.
- Run a two-product scratch smoke test with a strict budget.

Exit criterion: one real run produces inspectable images, a complete log, a run manifest,
and deterministic mechanical scores.

### Phase 4: Judging and reporting

- Implement the visual rubric schema and blinded model-judge path.
- Add human-review import/export using stable item and presentation IDs.
- Implement scoring, `rescore`, `compare`, and `leaderboard`.
- Add paired arm analysis and confidence intervals.

Exit criterion: the reference-image ablation can be executed and compared end to end, and
all aggregate numbers can be recomputed from stored artifacts.

### Phase 5: Hardening and second task

- Add a second provider and validate parameter normalization.
- Add `structured_text` to prove the abstractions are not image-specific.
- Add schema migrations, clearer diagnostics, and optional external artifact storage.
- Document how to create a new case, experiment, evaluator, task, and provider.

Exit criterion: a new evaluation type can be added without changing planner, identity,
artifact, or reporting fundamentals.

## Deliberate non-goals for the first release

- Hosted dashboards, authentication, or multi-user coordination.
- A database as the source of truth.
- Distributed workers or queues.
- Automatic prompt optimization.
- A universal provider abstraction covering every vendor feature.
- A generic plugin marketplace.
- Treating a model judge as unquestioned ground truth.

The first release succeeds if it makes a local evaluation honest, resumable, inspectable,
and difficult to mix up—not if it automates every possible evaluation workflow.

## First framework acceptance test

The product-scanning case is ready to validate the framework when:

1. The dataset contains at least two package shapes and front/back views with barcode
   location metadata.
2. The experiment has `product-only` and `product-plus-scanner` arms differing only in
   the scanner reference.
3. `tamesu plan` shows the complete call and cost exposure before execution.
4. A killed run resumes without regenerating completed items.
5. Changing a prompt makes prior matching-label runs stale.
6. Mechanical checks, blinded judge output, and human review remain separately visible.
7. `rescore` changes reports without making generation calls.
8. A generated leaderboard links every aggregate claim back to run and item evidence.

Passing this test establishes the reusable framework. Further experiments—multiple
product views, spatial guide images, scanner pose controls, prompt strategies, and model
comparisons—then become new evals or experiments rather than one-off scripts.

## Bootstrap from the current Yakin draft

When creating the separate repository:

1. Copy the current case README to `cases/product-scanning-alignment/README.md` and add the
   small `case.yml` manifest described above.
2. Turn the current case-level `eval.yml` into the first experiment README or the
   `reference-ablation/eval.yml`; its `question` and `description` are useful starting
   material, but it still needs explicit arms, a dataset, metrics, and a run matrix.
3. Put product and scanner source images into the versioned dataset rather than beside a
   particular run.
4. Start with the fake-provider acceptance test, then a two-item `scratch` run, before
   activating the paid eval.
