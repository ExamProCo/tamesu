# Manifest reference

> **Status:** Tamesu 0.1 implements the version 1 case, dataset, eval, run, result, and
> report fields used by structured-text evals. Image and judgment fields remain planned.

Tamesu uses YAML manifests for evaluation plans and YAML records for execution evidence.
Every file is designed to remain readable without the Tamesu CLI.

## File types

| File | Written by | Purpose |
|---|---|---|
| `case.yml` | User | Describes a broad evaluation case |
| `dataset.yml` | User | Declares stable dataset items and their assets |
| `eval.yml` | User | Freezes a question, run matrix, and scoring plan |
| `run.yml` | Tamesu | Records one resolved execution and its provenance |
| `result.yml` | Tamesu | Records the outcome for one dataset item |
| `report.yml` | Tamesu | Summarizes scores and execution results |
| `judge.yml` | Tamesu or reviewer | Preserves a structured judgment for one item |
| `logs/<run-id>.jsonl` | Tamesu | Append-only, versioned run timeline |
| `leaderboard.md` | Tamesu | Provides a compact comparison across compatible runs |
| `evaluation-report.md` | Tamesu | Explains aggregate results, coverage, and limitations |
| `analysis.md` | Human or coding agent | Records a reviewable interpretation of the evidence |

The first three files are authored plans. Run, result, judge, and log files are evidence.
Reports, leaderboards, and analyses are derived views and must not be edited to make an
eval appear complete.

## Common rules

### Schema version

Every YAML manifest or record starts with an integer schema version:

```yaml
schema_version: 1
```

Readers must reject unsupported schema versions rather than guessing how to interpret
them. Adding optional fields does not require a new version; changing the meaning or type
of an existing field does.

### IDs and names

Machine-readable identifiers use lowercase kebab-case:

```text
^[a-z0-9]+(?:-[a-z0-9]+)*$
```

This applies to case, dataset, experiment, eval, arm, metric, and item IDs. Display titles
and descriptions may use normal prose.

IDs are stable. Renaming an item or arm creates a different identity even when its content
is otherwise unchanged.

### Paths

- Paths use forward slashes.
- A relative path is resolved from the manifest containing it.
- A resolved path must remain inside the repository.
- Input files must exist and be readable during linting.
- Stored artifact references are relative and include content checksums.
- URLs are not accepted where a local path is required.

### Unknown fields

Version 1 manifests are strict: unknown fields are validation errors. This catches typos
and prevents configuration from being accepted but ignored. Provider-specific generation
options are the exception and belong under `parameters`.

### Secrets

Credentials, access tokens, authorization headers, signed URLs, and other secrets are
forbidden in manifests. Provider adapters discover credentials from the environment or an
external credential store.

### Checksums

Checksums use an algorithm-prefixed string:

```yaml
sha256: sha256:34f8...c921
```

SHA-256 is the required algorithm for schema version 1.

## `case.yml`

`case.yml` contains stable metadata for one broad problem. It lives at the root of a case
directory.

```yaml
schema_version: 1
name: product-scanning-alignment
title: Product Scanning Alignment
description: >
  Generate plausible images of a person holding a product in one hand and aiming a
  handheld scanner at its barcode with the other.
default_task: image_generation
```

### Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `schema_version` | integer | Yes | Must be `1` |
| `name` | string | Yes | Stable kebab-case case ID; must match the directory name |
| `title` | string | Yes | Human-readable case title |
| `description` | string | Yes | Problem statement and relevant context |
| `default_task` | string | No | Task adapter used when an eval does not specify `task` |

The case manifest does not select models, providers, prompts, or output locations.

## `dataset.yml`

`dataset.yml` defines stable item IDs and maps them to local inputs. It lives inside one
versioned dataset directory.

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

### Top-level fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `schema_version` | integer | Yes | Must be `1` |
| `name` | string | Yes | Stable kebab-case dataset ID; normally includes a version suffix |
| `description` | string | Yes | Human-readable description of the dataset and its scope |
| `shared_assets` | mapping | No | Named asset groups available to every item |
| `items` | list | Yes | Non-empty list of dataset items |

An asset group maps a role to a non-empty list of paths. Roles such as `product`,
`scanner`, `reference`, or `input` are interpreted by the selected task adapter.

### Item fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | string | Yes | Stable kebab-case ID, unique within the dataset |
| `input` | mapping | Task-dependent | Inline input values such as text, messages, or variables |
| `assets` | mapping | Task-dependent | Named groups of paths used for this item |
| `expected` | any YAML value | No | Ground-truth value used by deterministic scorers |
| `metadata` | mapping | No | Task-specific scalar or structured metadata |

Text tasks normally use `input`; media tasks normally use `assets`. A task adapter defines
the required shape and may use both. `expected` is optional for generative tasks, but is
required by scorers such as exact match. Metadata must be serializable as YAML. None of
these fields may contain secrets.

### Dataset validation

Linting verifies that:

- the dataset contains at least one item;
- item IDs are unique and valid;
- task-required inline inputs and expected values have the correct shape;
- required asset roles exist for the selected task;
- every referenced asset exists and is readable;
- media types are allowed by the task adapter;
- no resolved path escapes the repository;
- each input can be hashed.

File order does not define identity. A run records selected item IDs and the SHA-256 digest
of every input asset.

## `eval.yml`

`eval.yml` freezes one measurement plan. It lives inside an experiment's `evals/`
directory.

```yaml
schema_version: 1
name: reference-ablation
status: draft
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
    references:
      - product

  - id: product-plus-scanner
    description: Product views and the shared scanner reference.
    references:
      - product
      - scanner

runs:
  - model: gpt-image-2
    provider: openai
    arms:
      - product-only
      - product-plus-scanner

  - model: gemini-image
    provider: gemini
    arms:
      - product-only
      - product-plus-scanner

evaluation:
  mechanical:
    - output_exists
    - decodable_image
    - expected_dimensions
  model_judges:
    - rubric: ../../rubrics/visual-alignment.yml
      model: judge-model
      provider: judge-provider
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

### Identity and status

| Field | Type | Required | Description |
|---|---|---:|---|
| `schema_version` | integer | Yes | Must be `1` |
| `name` | string | Yes | Stable kebab-case eval ID; must match the directory name |
| `status` | string | Yes | One of `draft`, `active`, or `complete` |
| `question` | string | Yes | The specific question the eval will answer |
| `description` | string | Yes | Comparison design, controlled variables, and interpretation |
| `task` | string | Conditional | Registered task adapter; may inherit from `case.yml` |
| `dataset` | path | Yes | Path to one `dataset.yml` |
| `output_schema` | path | Task-dependent | Schema used to validate structured task output |

Status controls execution:

| Status | Meaning | Paid execution allowed? |
|---|---|---:|
| `draft` | The plan may still change | No |
| `active` | The plan is approved for execution | Yes |
| `complete` | The eval is closed to new runs | No |

All statuses allow read-only linting, planning, status, comparison, and reporting. Reopening
a complete eval requires an explicit version-controlled edit.

Use `tamesu activate <case>/<experiment>/<eval>` to validate and move a draft eval to
active without calling a provider. Use `tamesu close` to verify its evidence, move an
active eval to complete, and rebuild its leaderboard.

### `defaults`

| Field | Type | Required | Constraint |
|---|---|---:|---|
| `repetitions` | integer | Yes | At least `1` |
| `concurrency` | integer | Yes | At least `1` |
| `timeout_seconds` | integer | Yes | At least `1` |
| `retries` | integer | Yes | At least `0` |
| `budget_usd` | number | Yes | Greater than or equal to `0` |
| `parameters` | mapping | No | Provider-specific generation parameters |

`budget_usd` is the maximum priced generation exposure for the eval. For registered
models, planning estimates expected cost and maximum exposure from input size, output caps,
and retries; execution refuses a priced plan above the ceiling. Planning calls out unknown
prices instead of treating them as free.

Values inside `parameters` are preserved in the resolved run manifest and included in its
specification fingerprint. A provider adapter validates which parameters it supports.

### `arms`

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | string | Yes | Unique kebab-case arm ID |
| `description` | string | Yes | The factor represented by the arm |
| `prompts` | mapping of paths | Task-dependent | Prompt roles such as `system` and `user` |
| `references` | list of strings | No | Dataset asset roles supplied to the task |
| `parameters` | mapping | No | Parameters that differ intentionally for this arm |

An eval must declare at least one arm. Every arm referenced by `runs` must exist. Differences
between arms should be explicit; a description alone must not alter execution.

Prompt paths resolve from `eval.yml`. The selected task adapter defines required prompt
roles and renders templates from the current dataset item. Prompt sources, rendered
prompts, and the output schema contribute to the content fingerprint.

Parameter precedence is:

```text
defaults.parameters → arm.parameters → run.parameters
```

Later values override earlier values by key. The resolved mapping is stored in `run.yml`.

### `runs`

Each entry defines one model/provider configuration before arms and repetitions are
expanded.

| Field | Type | Required | Description |
|---|---|---:|---|
| `model` | string | Yes | Provider model identifier |
| `provider` | string | Yes | Registered provider adapter |
| `arms` | list of strings | Yes | Non-empty set of arm IDs to execute |
| `parameters` | mapping | No | Provider-specific overrides for this configuration |
| `repetitions` | integer | No | Override for this configuration; at least `1` |

Implemented providers are `openai`, `anthropic`, `gemini`, `grok`, `meta`, and `bedrock`.
Known model/provider mismatches and unsupported effort values are lint errors. An unknown
model ID remains valid with an explicit implemented provider so previews and new releases
can be exercised, but its price is unknown. See [Providers and models](providers.md).

For each run entry, Tamesu expands:

```text
arms × repetitions
```

Dataset items become work units inside each expanded run. They do not create additional
run directories.

### `evaluation`

The evaluation block declares required scorers and reviewers.

#### `mechanical`

A list of registered deterministic checks. Unknown checks are errors. Mechanical checks
run before subjective judging and remain visible when they fail.

#### `model_judges`

Each model judge supports:

| Field | Type | Required | Description |
|---|---|---:|---|
| `rubric` | path | Yes | Structured rubric used by the judge |
| `model` | string | Yes | Judge model identifier |
| `provider` | string | Yes | Judge provider adapter |
| `repetitions` | integer | Yes | Judgments per eligible item; at least `1` |
| `blind` | boolean | Yes | Whether generator identity and arm are withheld |
| `parameters` | mapping | No | Provider-specific judge parameters |

Rubric files and judge settings contribute to the content and specification fingerprints.

#### `human_review`

| Field | Type | Required | Description |
|---|---|---:|---|
| `mode` | string | Yes | Review protocol, initially `blinded_pairwise` or `blinded_single` |
| `required` | boolean | Yes | Whether review is required before completion |

The review protocol may gain task-specific fields before schema version 1 is finalized.
Human judgments remain separate from model judgments in stored evidence and reports.

### `metrics`

| Field | Type | Required | Description |
|---|---|---:|---|
| `primary` | string | Yes | Metric used to answer the eval question |
| `secondary` | list of strings | No | Additional diagnostic, cost, and quality metrics |

Metric IDs must be unique across the primary and secondary fields. The primary metric must
be produced by a configured mechanical scorer, rubric, human-review aggregation, or task
adapter.

### Eval validation

In addition to field validation, linting verifies that:

- the dataset and rubric paths resolve;
- every run references declared arms;
- the selected task can consume the dataset;
- provider/model combinations are recognized where possible;
- the primary metric is produced by the configured evaluation;
- all numeric limits are within their allowed ranges;
- required provider credentials are reported, but never read into the manifest;
- a complete eval cannot schedule new paid work.

Missing credentials do not make a plan invalid. They are reported as execution blockers.

## Generated execution records

The remaining YAML files are written from resolved configuration and observed results.
Their exact schema will be finalized alongside the runner, but the required content is
defined below.

## `run.yml`

`run.yml` is created before provider work starts and finalized when the run reaches a
terminal state.

```yaml
schema_version: 1
run_id: gpt-image-2--product-plus-scanner--rep2--20261002-143012--a81c39f2
label: gpt-image-2--product-plus-scanner--rep2
state: complete
started_at: 2026-10-02T14:30:12Z
finished_at: 2026-10-02T14:36:48Z

identity:
  specification_fingerprint: sha256:...
  content_fingerprint: sha256:...

selection:
  item_ids:
    - canned-coffee-01
    - chips-01

resolved:
  provider: openai
  model: gpt-image-2
  arm: product-plus-scanner
  repetition: 2
  timeout_seconds: 300
  retries: 2
  parameters:
    size: 1536x1024
    quality: high

totals:
  attempts: 2
  retries: 0
  duration_ms: 396000
  cost_usd: 0.16
```

The complete record also includes the content hash inventory, Tamesu version, Python
version, platform, executable path, concurrency, budget, prompt source paths, and resolved
parameters.

Run states are `planned`, `running`, `partial`, `failed`, or `complete`. Only a complete
run with matching fingerprints can satisfy a planned repetition.

## `result.yml`

One `result.yml` is stored for each selected dataset item:

```yaml
schema_version: 1
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
  provider_request_id: request-id
  revised_prompt: null
  latency_ms: 18422
  usage: {}
  cost_usd: 0.08

mechanical_scores:
  output_exists: true
  decodable_image: true
  expected_dimensions: true
```

Failed results use the same identity and attempt fields and include a normalized error
with a type, safe message, retryability, and provider request ID when one exists. They are
retained in aggregate denominators.

## `logs/<run-id>.jsonl`

Each run has a schema-versioned, append-only event stream containing lifecycle events,
provider attempts, artifact references, terminal item records, and framework failures.
The complete event contract, durability behavior, ordering rules, and redaction boundary
are defined in [Logging and execution evidence](logging.md) and the
[log-event JSON Schema](../schemas/log-event.schema.json).

The log is execution evidence, but it does not replace `run.yml`, `result.yml`, stored
outputs, or `report.yml`. Provider request bodies and raw prompts are not written to it.

## `judge.yml`

`judge.yml` stores one or more structured judgments without modifying `result.yml`:

```yaml
schema_version: 1
item_id: canned-coffee-01
rubric: visual-alignment
rubric_sha256: sha256:...
blind: true
judgments:
  - judge_type: model
    provider: judge-provider
    model: judge-model
    repetition: 1
    scores:
      barcode_visibility: pass
      scanner_aim: pass
      hand_anatomy: pass
      overall_plausibility: 4
    reason_codes: []
    explanation: The scanner is aimed at the visible barcode.
```

The final rubric will define allowed dimensions, value ranges, pass rules, and reason
codes. Generator identity, arm, expected winner, and cost are omitted from blinded judge
inputs.

## `report.yml`

`report.yml` is a derived summary and may be replaced only by an explicit reporting or
rescoring operation.

```yaml
schema_version: 1
run_id: gpt-image-2--product-plus-scanner--rep2--20261002-143012--a81c39f2
generated_at: 2026-10-02T14:40:00Z
scorer_version: 0.1.0

identity:
  specification_fingerprint: sha256:...
  content_fingerprint: sha256:...

completion:
  selected_items: 2
  completed_items: 2
  failed_items: 0

metrics:
  scan_alignment_pass_rate: 0.5
  generation_failure_rate: 0.0
  median_latency_ms: 18110
  mean_cost_usd: 0.08

totals:
  cost_usd: 0.16
```

The complete report includes per-item score summaries, conditional and end-to-end rates,
judge agreement, human-review completion, confidence intervals where supported, usage,
latency, and cost.

Reports must preserve evidence-source boundaries. Mechanical, model-judge, and human
metrics may be displayed together but must not be silently combined into one score.

## Canonicalization and fingerprints

Fingerprints are computed from canonical JSON, not YAML text. Before hashing, mappings
are recursively sorted and values are normalized according to their schema. Formatting,
comments, and mapping order therefore do not change identity.

Two SHA-256 fingerprints are stored:

- The **specification fingerprint** covers the intended model, provider, arm, repetition,
  item selection, parameters, retry policy, execution mode, and judge configuration.
- The **content fingerprint** covers the dataset and asset hashes, prompts, schemas,
  rubrics, task and evaluator code, framework version, dependency lockfile, runtime, and
  source-control revision.

Both must match for prior evidence to satisfy a current eval slot.

## Timestamps and numeric values

- Timestamps use RFC 3339 in UTC, represented with a trailing `Z`.
- Durations and latencies are integer milliseconds.
- Currency fields are denominated in US dollars and use the `_usd` suffix.
- Unknown usage or cost is represented explicitly as `null`, not `0`.
- Rates are numbers from `0.0` through `1.0`.

For directory placement and file ownership, see
[Repository and file structure](file-structure.md). For the hierarchy these manifests
represent, see [Core concepts](concepts.md).
