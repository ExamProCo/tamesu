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
business_use: >
  Product teams use the images in training material that demonstrates the intended
  scanning workflow.
current_problem: >
  Generated images often show the wrong grip, scanner direction, or barcode alignment.
technical_uncertainty: >
  We do not know which prompting and reference-image strategy most consistently produces
  the required pose and product relationship.
default_task: image_generation
```

### Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `schema_version` | integer | Yes | Must be `1` |
| `name` | string | Yes | Stable kebab-case case ID; must match the directory name |
| `title` | string | Yes | Human-readable case title |
| `description` | string | Yes | Concise description of what the case evaluates |
| `business_use` | string | Yes | How the evaluated capability would be used in practice |
| `current_problem` | string | Yes | Present pain or limitation that motivates the work |
| `technical_uncertainty` | string | Yes | Unknown that the case's experiments are intended to resolve |
| `default_task` | string | No | Task adapter used when an eval does not specify `task` |

These narrative fields are separate so presentations can explain the case without
guessing intent from a generic description or from a README outside the case. The case
manifest does not select models, providers, prompts, or output locations.

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
  judge_budget_usd: 5.00
  parameters:
    image:
      size: 1536x1024
      output_format: png
    provider_options: {}

arms:
  - id: product-only
    description: One product, no scanner reference.
    prompts:
      prompt: ../../prompts/product-only.md

  - id: product-plus-scanner
    description: The same prompt with the scanner described.
    prompts:
      prompt: ../../prompts/product-plus-scanner.md

runs:
  - model: gpt-image-2
    provider: openai
    arms:
      - product-only
      - product-plus-scanner
    parameters:
      provider_options:
        quality: low

  - model: muse-image-1.0
    provider: meta
    arms:
      - product-only
    parameters:
      provider_options:
        reasoning_strength: low
        tool_enablement:
          enable_image_search: false
          enable_web_search: false
          enable_shell: false

evaluation:
  mechanical:
    - decodable_image
    - min_resolution: {width: 1024, height: 768, prerequisite: true}
    - format_match
  model_judges:
    - id: vision-judge-1
      provider: meta
      model: muse-spark-1.3
      rubric: ../../rubrics/visual-alignment.yml
      prompts:
        system: ../../prompts/judge-system.md
        user: ../../prompts/judge-user.md
      repetitions: 2
  human_review:
    rubric: ../../rubrics/visual-alignment.yml
  acceptance:
    requires: [mechanical, human]
    human:
      required_reviews_per_item: 1
      on_disagreement: adjudicate
    model_judge:
      role: screen
    selection: none

metrics:
  primary: product_reference_pass_rate
  secondary:
    - generation_success_rate
    - human_pass_rate_given_image
    - review_completion_rate
    - cost_per_accepted_usd
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

A list of registered deterministic checks for the eval's task. Each entry is a name or a
single-key mapping of name to parameters. Unknown checks and unknown parameters are errors.
Mechanical checks run at generation time and remain visible when they fail.

`structured_text` provides `valid_json`, `schema_valid`, and `exact_match` (no parameters).

`image_generation` always applies mandatory integrity checks (bytes exist, the image
decodes with Pillow, the format is PNG/JPEG/WebP, dimensions are non-degenerate, and
byte/pixel/decompression-bomb limits hold). A failure is a terminal `invalid_artifact`
outcome, not an infrastructure failure. It additionally offers eval-declared checks:

| Check | Parameters | Passes when |
|---|---|---|
| `decodable_image` | none | Always recorded; documents the integrity check |
| `min_resolution` | `width`, `height` | The image is at least that large |
| `format_match` | none | The decoded type equals the requested `output_format` |
| `non_uniform` | `min_stddev` (default `2.0`) | The image is not a single flat colour |
| `has_transparency` | `require` (default `true`) | Transparency presence matches `require` |

Every check also accepts `prerequisite: true`, which excludes failing images from model
judging. A flat-colour image can be the correct answer to a different eval, so content
checks are never global.

#### `model_judges`

Model judges apply a rubric to stored images in a separate, repeatable, paid step
(`tamesu judge`). Only tasks that store reviewable artifacts support them.

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | kebab-case string | Yes | Unique judge ID; part of each judgment's file name |
| `provider` | string | Yes | Judge provider adapter; needs multimodal structured output |
| `model` | string | Yes | Registered model with `image_input` and `structured_output` |
| `rubric` | path | Yes | Machine-readable rubric (see below) |
| `prompts.system`, `prompts.user` | paths | Yes | Judge prompts; see blinding below |
| `repetitions` | integer | No | Judge calls per eligible item; default `1` |
| `parameters` | mapping | No | `effort`, `max_tokens`, and similar |

Declaring any judge requires `defaults.judge_budget_usd`, a ceiling that is tracked
separately from `budget_usd` so rejudging cannot drain the generation budget.

The judge sees the image, the item's `input` (the product brief), and the rubric, and
nothing else. Prompt templates may reference only `item.id`, `item.input.*`, `rubric.name`,
and `rubric.text`; any other reference (arm, model, run, repetition, cost, `expected`) is a
template error. Judges are **not** part of a generation run's identity, so adding or
repeating a judge never un-banks a run.

#### Rubrics

A rubric is a YAML file. It is the single source for the judge's output schema, the review
pack, and the report:

```yaml
schema_version: 1
name: product-reference-quality
rationale: product-reference-quality.md   # optional prose, relative to this file
dimensions:
  - id: single-product-composition
    question: Exactly one complete product, isolated, fully in frame.
    reason_codes: [multiple-products, cropped, wrong-viewpoint]
acceptance:
  rule: all_dimensions_pass
```

Each dimension is one yes/no claim. A failing dimension carries one reason code from its
list; a passing one carries `none` (a reserved word, not allowed as a reason code). The
overall verdict is computed from the dimensions and is never requested from a judge.
Editing a rubric changes its fingerprint, which marks earlier judgments and reviews stale
(they stay on disk but stop counting). See `schemas/rubric.schema.json`.

#### `human_review`

| Field | Type | Required | Description |
|---|---|---:|---|
| `rubric` | path | Yes | The rubric reviewers answer |

Humans review outside the process: `tamesu review export` writes a blinded pack and
`tamesu review import` ingests the completed responses. Reviewer identity is a free-text
ID, not authentication. Human and model verdicts stay separate in stored evidence and
reports.

#### `acceptance`

Declares, before any run, what makes an image acceptable.

| Field | Type | Description |
|---|---|---|
| `requires` | list | Any of `mechanical`, `model_judge`, `human`; all listed sources must pass |
| `human.required_reviews_per_item` | integer | Current reviews needed per image; default `1` |
| `human.on_disagreement` | `adjudicate` or `reject` | `reject` fails a split verdict; `adjudicate` asks for one more review and decides by majority of an odd count |
| `model_judge.role` | `screen` or `gate` | `screen` is informational; `gate` must appear in `requires` |
| `selection` | `none` or `first_pass` | `none` reports every repetition; `first_pass` promotes the first accepted one per product |

Lint rules: `human` in `requires` needs a `human_review` block and vice versa; a `screen`
judge can never be in `requires` (it can never be the sole gate); `gate` needs
`model_judge` in `requires`. An eval without an `acceptance` block requires only
`mechanical`. `mechanical` and `acceptance` are part of a run's specification fingerprint,
so changing them after a run exists leaves that run stale.

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

## `publication.yml` and `package.yml`

`publication.yml` is authored beside `case.yml` and controls how a case is presented and
packaged. `package.yml` is generated inside a `.tamesu.tar.gz` archive. It copies the
publication metadata and adds the selected profile, producing Tamesu version, sorted
payload inventory, and content digest. Publication metadata never includes its own
package digest, avoiding circular identity.

The normative fields, inclusion rules, canonicalization algorithm, and consumer safety
limits are documented in [Case packages](packages.md). Machine-readable definitions are
in `schemas/publication.schema.json` and `schemas/package.schema.json`.
