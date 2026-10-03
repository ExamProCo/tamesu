# Command-line interface

> **Status:** These commands are implemented for structured-text evals in Tamesu 0.1.
> Image tasks, model judges, human review, and machine-readable CLI output remain planned.

Tamesu's command-line interface is designed around a small workflow:

```text
lint → plan → run → status → rescore → compare → close
```

The examples use `tamesu` as the executable name. Commands operate on files in the
current repository and keep all evaluation evidence inspectable on disk.

## Command summary

| Command | Purpose | Writes files? | May call a paid provider? |
|---|---|---:|---:|
| `lint` | Validate manifests, references, and assets | No | No |
| `list` | Show discovered cases, experiments, and evals | No | No |
| `models` | Show the built-in provider/model registry and pricing | No | No |
| `plan` | Expand an eval and report work, cost, and blockers | No | No |
| `run` | Execute planned run configurations | Yes | Yes |
| `status` | Reconcile the plan with evidence on disk | No | No |
| `resume` | Continue a compatible partial run | Yes | Yes |
| `rescore` | Recompute scores from stored evidence | Yes | No in 0.1 |
| `compare` | Compare compatible completed runs | No | No |
| `leaderboard` | Rebuild an eval's Markdown leaderboard | Yes | No |
| `close` | Verify completion and close an eval | Yes | No |

`rescore` never calls a provider in Tamesu 0.1. It recomputes deterministic scores from
stored text outputs.

## Identifying an eval

Commands that operate on an eval accept its repository-relative identity:

```text
<case>/<experiment>/<eval>
```

For example:

```text
support-ticket-triage/decision-rules/prompt-ablation
```

This resolves to:

```text
cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml
```

Commands that operate on one execution accept its full run ID:

```text
gpt-5-4-mini--decision-rules--rep2--20261002-143012-123456--a81c39f2
```

Human-readable labels help with inspection, but only a full run ID uniquely identifies
stored evidence.

## `tamesu lint`

Validate a case, experiment, eval, dataset, or manifest without executing provider calls.

```sh
tamesu lint cases/support-ticket-triage
```

Linting checks:

- YAML syntax and schema versions;
- required fields, types, IDs, and allowed values;
- referenced datasets, prompts, rubrics, and assets;
- path containment within the repository;
- unique item and arm IDs;
- task-specific dataset requirements;
- run references to declared arms;
- primary metric availability;
- recognized provider/model combinations where possible;
- forbidden secret-like fields.

Credential availability is reported separately from validation. Missing credentials may
block execution, but do not make a reproducible plan structurally invalid.

Lint exits unsuccessfully when it finds an error. Warnings identify conditions that are
valid but deserve attention, such as unknown provider pricing.

## `tamesu list`

Discover evaluation content in the repository.

```sh
tamesu list
```

The listing includes each case, experiment, and eval with its status and relative ID. It
does not inspect providers or make network calls.

The initial implementation may add filters, but unfiltered output remains the portable
default for scripts and local inspection.

## `tamesu models`

List built-in model metadata without requiring a Tamesu project or provider credentials:

```sh
tamesu models
tamesu models --provider anthropic
```

The tab-separated output includes each model's provider, accepted effort values, input
and output token rates, and default output-token cap. Models not in this registry may
still be used with an explicit implemented provider, but their cost is unknown and their
effort value cannot be checked during linting.

## `tamesu plan`

Expand an eval manifest into the concrete work it requires.

```sh
tamesu plan support-ticket-triage/decision-rules/prompt-ablation
```

Planning reports at least:

- model and arm configurations;
- repetitions per configuration;
- selected dataset items;
- total expected generation and judging calls;
- compatible runs already banked;
- partial, failed, and stale runs;
- run slots still owed;
- estimated cost and maximum exposure;
- prices that are unknown;
- credentials required for execution;
- outstanding human-review work.

The basic expansion is:

```text
run entries × arms × repetitions
```

Dataset items are work units inside each expanded run. For example, two models, two arms,
and three repetitions produce 12 planned runs. If the dataset contains 10 items, those
runs contain 120 generation work units.

`plan` is always read-only. It never calls a generation provider or model judge.

## `tamesu run`

Execute work owed by an active eval.

```sh
tamesu run support-ticket-triage/decision-rules/prompt-ablation
```

Before starting paid work, `run` performs the same validation and expansion as `lint` and
`plan`. It refuses execution when:

- the eval status is `draft` or `complete`;
- a required file is missing or invalid;
- the selected provider credentials are unavailable;
- the requested filters match no planned configuration.

For registered models with known prices, `run` refuses to start when recorded known cost
plus maximum priced exposure—configured output caps, planned retries, and estimated input
usage—exceeds `budget_usd`. Unknown model prices are reported by `plan` and cannot be
enforced; review current provider pricing before running them.

For each selected configuration, the command creates a new run directory and writes its
resolved `run.yml` before making provider calls. Results and call logs are flushed as work
completes so an interrupted execution can be inspected and resumed.

### Select a model

Use `--only` to execute matching planned model configurations without editing the eval:

```sh
tamesu run support-ticket-triage/decision-rules/prompt-ablation \
  --only gpt-5.4-mini
```

Filtering changes which owed work is attempted in this invocation. It does not change the
eval manifest or its completion requirements.

### Limit dataset items

Use `--limit-items` for a small integration probe:

```sh
tamesu run support-ticket-triage/decision-rules/prompt-ablation \
  --only gpt-5.4-mini \
  --limit-items 2
```

A limited run is marked partial and never satisfies a planned repetition, even when every
limited item succeeds. Its artifacts remain useful for diagnosing prompts, providers, and
task adapters.

### Force a fresh run

Use `--force` to execute another fresh sample when compatible evidence already exists:

```sh
tamesu run support-ticket-triage/decision-rules/prompt-ablation \
  --only gpt-5.4-mini \
  --force
```

`--force` creates a new run ID and directory. It never overwrites an existing run or
artifact. Extra runs remain visible but do not silently change the number of repetitions
owed by the eval.

### Interruption and budgets

Every provider attempt is appended to the run's JSONL log. Completed item stages are
preserved when execution is interrupted.

The budget check is a preflight exposure check, not a live provider-side spending limit.
Tamesu records actual cost after each response, but unknown pricing and provider billing
differences still require operator review.

## `tamesu status`

Reconcile an eval's declared plan with evidence currently on disk.

```sh
tamesu status support-ticket-triage/decision-rules/prompt-ablation
```

Status classifies discovered runs as:

- **banked** — complete and compatible with an owed run slot;
- **partial** — compatible reusable work exists, but required items or stages are missing;
- **failed** — the execution reached a non-resumable terminal state;
- **stale** — its specification or content fingerprint does not match the current plan;
- **extra** — valid evidence exists beyond the repetitions currently owed.

The command reports missing run slots, item failures, pending judgments, human-review
requirements, and recorded cost. It does not modify artifacts or call providers.

## `tamesu resume`

Continue one compatible partial run from its last valid item and stage.

```sh
tamesu resume \
  gpt-5-4-mini--decision-rules--rep2--20261002-143012-123456--a81c39f2
```

Before reusing any artifact, `resume` verifies:

- the specification and content fingerprints;
- the run's resolved manifest;
- input and output checksums;
- current artifact validators;
- required stages already recorded as complete.

Only missing or safely retryable work is scheduled. Successful generation is not repeated
merely because judging or reporting is incomplete.

Resume refuses a changed prompt, dataset, asset, schema, rubric, evaluator, parameter set,
or dependency identity and explains the mismatch. Use a fresh `run` when the intended
measurement has changed.

Invalid partial artifacts are moved to a timestamped quarantine name with a reason rather
than being silently deleted.

## `tamesu rescore`

Recompute a run's scores and report from stored outputs.

```sh
tamesu rescore \
  gpt-5-4-mini--decision-rules--rep2--20261002-143012-123456--a81c39f2
```

Rescoring:

- reads existing generated or structured outputs;
- reruns configured mechanical checks;
- applies the selected rubric and scorer version;
- preserves original generation artifacts and call logs;
- records new judgments separately from previous judgments;
- atomically replaces the derived `report.yml` after success.

Generation calls are forbidden during rescoring. If new model judgments would incur cost,
the command must identify their count and exposure before making those calls.

## `tamesu compare`

Compare compatible completed runs for one eval.

```sh
tamesu compare support-ticket-triage/decision-rules/prompt-ablation
```

Comparison output includes:

- complete repetitions and total evaluated items;
- mean and dispersion for the primary metric;
- secondary quality metrics;
- generation failure rate;
- latency, usage, and cost;
- judge agreement and human preference where available;
- paired wins, losses, and ties for matched ablation arms;
- confidence intervals when the sample size supports them.

Stale, partial, failed, and scratch runs are identified but excluded from primary
comparisons. `compare` reads stored reports and never calls a provider.

## `tamesu leaderboard`

Generate or rebuild the Markdown leaderboard for an eval.

```sh
tamesu leaderboard \
  support-ticket-triage/decision-rules/prompt-ablation
```

The command writes `leaderboard.md` beside `eval.yml`. The file contains the eval question,
dataset version, primary metric, completion state, one row per model/arm configuration,
links to run reports, and its generation and scorer versions.

The leaderboard is a derived view, not evidence. Rebuilding it does not modify run
artifacts or make provider calls.

## `tamesu close`

Verify that an eval has met its declared completion requirements and close it to new runs.

```sh
tamesu close support-ticket-triage/decision-rules/prompt-ablation
```

Closing requires:

- every planned repetition to have a compatible complete run;
- every selected item to have a terminal result;
- all mandatory scoring and judgments to exist;
- every compatible run to have a current report.

When validation succeeds, the command changes the eval status to `complete` and rebuilds
the leaderboard. Those changes should be reviewed and committed like any other plan
change.

If completion requirements are intentionally waived, the reason must first be recorded in
the eval manifest using the final schema's explicit closure field. `close` never silently
waives missing evidence.

## Typical workflow

Create and inspect the plan:

```sh
tamesu lint cases/support-ticket-triage
tamesu plan support-ticket-triage/decision-rules/prompt-ablation
```

After reviewing the plan and changing `status: draft` to `status: active`, execute it:

```sh
tamesu run support-ticket-triage/decision-rules/prompt-ablation
tamesu status support-ticket-triage/decision-rules/prompt-ablation
```

Resume interrupted work if necessary:

```sh
tamesu resume <run-id>
```

Review the results and close the record:

```sh
tamesu compare support-ticket-triage/decision-rules/prompt-ablation
tamesu leaderboard support-ticket-triage/decision-rules/prompt-ablation
tamesu close support-ticket-triage/decision-rules/prompt-ablation
```

## Automation expectations

The CLI is intended to work in local shells and continuous integration:

- commands do not prompt when standard input is not interactive;
- validation failures return a non-zero exit status;
- human-readable output goes to standard output;
- errors and actionable diagnostics go to standard error;
- files are written atomically except append-only call logs;
- paid work is never triggered by `lint`, `list`, `plan`, `status`, `compare`,
  `leaderboard`, or `close`;
- interrupt signals stop new scheduling and preserve completed work.

Successful commands return `0`, lint validation failures return `1`, user-facing command
errors return `2`, and an interrupted command returns `130`. Machine-readable output is
not implemented in Tamesu 0.1.

For the files these commands read and write, see
[Repository and file structure](file-structure.md). For configuration fields, see the
[Manifest reference](manifests.md).
