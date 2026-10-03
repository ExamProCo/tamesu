# Repository and file structure

> **Status:** The structured-text and provider portions of this layout are implemented in
> Tamesu 0.1. Image tasks remain planned.

Tamesu uses the filesystem as its primary interface and source of truth. Configuration,
inputs, outputs, scores, and provenance are stored as ordinary files so an evaluation can
be inspected without a database or hosted service.

## Repository layout

A complete repository is expected to look like this:

```text
tamesu/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
├── examples/
│   └── support-ticket-classification/
│       └── cases/
├── schemas/
│   ├── case.schema.json
│   ├── dataset.schema.json
│   ├── eval.schema.json
│   ├── log-event.schema.json
│   └── report.schema.json
├── src/
│   └── tamesu/
│       ├── cli.py
│       ├── config.py
│       ├── discovery.py
│       ├── environment.py
│       ├── planner.py
│       ├── pricing.py
│       ├── runner.py
│       ├── identity.py
│       ├── artifacts.py
│       ├── logging.py
│       ├── scoring.py
│       ├── reporting.py
│       ├── models.py
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── common.py
│       │   ├── http.py
│       │   ├── models.py
│       │   ├── openai.py
│       │   ├── anthropic.py
│       │   ├── gemini.py
│       │   ├── meta.py
│       │   └── bedrock.py
│       └── tasks/
│           └── structured_text.py
├── tests/
│   ├── fixtures/
│   └── test_*.py
└── cases/
    └── <case-name>/
        ├── README.md
        ├── case.yml
        ├── datasets/
        └── experiments/
```

The `src/tamesu/` package contains the current implementation. The on-disk case, eval,
and run contracts remain usable without importing Python code.

## Top-level directories

| Path | Purpose |
|---|---|
| `docs/` | User-facing concepts, guides, and reference documentation |
| `examples/` | Self-contained sample Tamesu projects used by tutorials and tests |
| `schemas/` | Machine-readable schemas for manifests and reports |
| `src/tamesu/` | Python CLI and framework implementation |
| `tests/` | Automated tests and self-contained fixtures |
| `cases/` | Evaluation questions, datasets, experiments, and run evidence |
| `plans/` | Design and implementation plans created before the framework code |

Project configuration belongs at the repository root. Secrets do not: `.env.example`
documents expected environment variables, while real credentials remain ignored in
project or enclosing Git-workspace `.env` or `.tamesu/.env` files, in
`~/.tamesu/.env`, or in the process environment. See
[Configuration](configuration.md).

Each directory directly under `examples/` is an independent Tamesu project with its own
`cases/` tree. Examples should use synthetic or redistributable data, stay small enough to
inspect by hand, and state clearly whether their provider calls may incur cost.

## Case layout

Each directory under `cases/` represents one broad problem:

```text
cases/
└── product-scanning-alignment/
    ├── README.md
    ├── case.yml
    ├── datasets/
    │   └── product-scanning-v1/
    │       ├── dataset.yml
    │       ├── products/
    │       └── scanner/
    └── experiments/
        └── scanner-reference/
            ├── README.md
            ├── prompts/
            ├── rubrics/
            └── evals/
```

The case owns stable context and reusable inputs:

- `README.md` explains the real-world problem and its constraints.
- `case.yml` provides machine-readable case metadata.
- `datasets/` contains versioned dataset manifests and source assets.
- `experiments/` contains focused hypotheses tested within the case.

A case must not contain provider credentials or outputs that belong to a particular run.

## Dataset layout

A dataset lives under the case that owns it:

```text
datasets/
└── product-scanning-v1/
    ├── dataset.yml
    ├── products/
    │   ├── canned-coffee-01/
    │   │   ├── front.png
    │   │   └── back.png
    │   └── chips-01/
    │       ├── front.png
    │       └── back.png
    └── scanner/
        ├── front.png
        └── side.png
```

`dataset.yml` assigns stable IDs to items and maps those IDs to assets and metadata.
Directory order, file order, and sequential numbering do not define identity.

Shared assets, such as scanner reference images, are declared separately from item assets.
A run records the selected item IDs and a digest of every input it uses. Editing a dataset
therefore cannot silently change the meaning of an existing run.

## Experiment layout

An experiment contains material shared by all its evals:

```text
experiments/
└── scanner-reference/
    ├── README.md
    ├── prompts/
    │   ├── system.md
    │   └── user.md.j2
    ├── rubrics/
    │   └── visual-alignment.yml
    └── evals/
        └── reference-ablation/
```

- `README.md` records the hypothesis, arms, expected interpretation, and known confounds.
- `prompts/` contains prompt source files and templates.
- `rubrics/` contains versioned scoring definitions.
- `evals/` contains one or more frozen measurement plans.

Task-specific schemas or deterministic evaluator code may also live here when they are
part of the experiment rather than reusable framework behavior. Files that can affect a
result contribute to the run's content fingerprint.

## Eval layout

An eval owns its manifest and the evidence created from it:

```text
evals/
└── reference-ablation/
    ├── eval.yml
    ├── leaderboard.md
    ├── logs/
    │   └── <run-id>.jsonl
    └── runs/
        └── <run-id>/
            ├── run.yml
            ├── report.yml
            └── items/
                └── <item-id>/
                    ├── result.yml
                    ├── generated.png
                    └── judge.yml
```

### `eval.yml`

The eval manifest declares the question and the work owed. It selects the dataset,
experimental arms, models, repetitions, metrics, judges, and operating limits.

The manifest is authored configuration. It must not be rewritten to make an incomplete
run appear complete.

### `leaderboard.md`

The leaderboard is a generated view across compatible reports. It is convenient for
review, but is not a source of truth and can be rebuilt.

### `logs/`

Each run receives an append-only, schema-versioned JSONL event stream. It records run
start and finish, every provider attempt, persisted artifact references, terminal item
records, and unexpected framework failures. Prompt and schema content is represented by
hashes rather than copied into the log.

Events are flushed and synced immediately, and concurrent item workers share one locked
writer. Sensitive keys, configured credential values, inline secrets, and signed URL
parameters are recursively redacted. See [Logging and execution evidence](logging.md) for
the event schema, durability boundary, and content policy.

### `runs/`

Every execution has its own immutable run directory. A model name is not a sufficient
directory key because the same model can appear in multiple arms, parameter sets, and
repetitions.

A run ID combines a readable label, timestamp, and short fingerprint:

```text
gpt-image-2--product-plus-scanner--rep2--20261002-143012--a81c39f2
```

Creating a fresh run always creates a new directory. Existing completed evidence is never
overwritten.

## Run contents

### `run.yml`

`run.yml` is written when execution begins and finalized when it ends. It contains:

- the run ID, label, state, and timestamps;
- defaults expanded into a fully resolved configuration;
- selected dataset item IDs;
- specification and content fingerprints;
- the hash inventory used to compute those fingerprints;
- framework and Python runtime information;
- aggregate usage, cost, retry, and duration totals.

Score summaries belong in `report.yml`, not `run.yml`.

### `items/<item-id>/`

Each selected dataset item gets its own directory. It contains the generated output or
structured response, a `result.yml` record, and any judgments associated with that item.

`result.yml` records the artifact's relative path, media type, checksum, validation state,
provider request ID, attempts, latency, usage, cost, and mechanical scores.

Large outputs are stored once as standalone files. Binary data must not be embedded in
YAML or JSONL.

### `report.yml`

The report is derived from stored artifacts. It contains completion counts, aggregate and
per-item metrics, judgment summaries, human-review status, latency, usage, cost, and the
scorer version.

An explicit rescore operation may replace a report. It must not replace generation
outputs, original call records, or immutable judgments.

## Authored, recorded, and generated files

| File type | Examples | May normal tooling replace it? |
|---|---|---|
| Authored plan | `case.yml`, `dataset.yml`, `eval.yml`, prompts, rubrics | Only through an intentional source edit |
| Recorded evidence | `run.yml`, item outputs, `result.yml`, call logs, judgments | No; resume may append missing compatible work |
| Derived output | `report.yml`, `leaderboard.md` | Yes, through an explicit rebuild or rescore |

Partial and failed artifacts remain in place for inspection. Tamesu does not silently
delete them.

## Path and naming rules

- Case, experiment, eval, arm, and item IDs use lowercase kebab-case.
- References in manifests use repository-relative or manifest-relative paths.
- Referenced paths must resolve inside the repository.
- Run directories use generated run IDs; they are not named by model alone.
- Stored artifact references use relative paths and include checksums.
- Secrets and machine-specific absolute paths are forbidden in manifests.
- Temporary writes are created beside their destination and atomically renamed.

These rules keep a repository portable while preventing manifests from reading or writing
unrelated files outside the evaluation workspace.

## Version-control policy

Commit the files needed to understand and reproduce an evaluation:

- manifests, prompts, rubrics, schemas, and evaluator code;
- small source datasets and assets when licensing permits;
- completed run metadata, reports, and leaderboards;
- human-review records and other evidence needed to support a conclusion.

Do not commit:

- credentials or populated environment files;
- caches, temporary files, or interrupted writes;
- provider SDK caches;
- large generated artifacts when the repository uses external artifact storage.

When generated artifacts are stored externally, their metadata must retain a stable
location, media type, size, and content checksum. A report without its referenced evidence
is not a self-contained evaluation record.

## Ownership at a glance

When deciding where a new file belongs, place it at the narrowest level that can reuse it:

```text
Used across experiments?       → Case
Used across evals?             → Experiment
Defines one measurement plan?  → Eval
Created by one execution?      → Run
Created for one dataset item?  → Run item
```

For the meaning of each level, see [Core concepts](concepts.md). Manifest fields will be
documented in the [Manifest reference](manifests.md).
