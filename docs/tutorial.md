# Tutorial: run your first text eval

> **Status:** This tutorial is runnable with Tamesu 0.1. Its live execution step uses the
> Meta Model API and may incur cost.

This tutorial uses the self-contained
[support-ticket classification project](../examples/support-ticket-classification/). It
compares a basic prompt with a prompt containing explicit decision rules.

The example needs no image dataset, model judge, or human review. Its eight synthetic
tickets and expected answers live directly in one YAML file, so every quality score can be
calculated deterministically.

By the end, you will know how to:

- inspect a Tamesu project;
- validate, plan, and activate an eval;
- run a small probe before the full evaluation;
- inspect and resume stored work;
- compare two experimental arms;
- generate a leaderboard and close the eval.

## How to read Tamesu output

Every execution answers four different questions. Do not treat them as one pass/fail
signal:

| Signal | Question it answers | Example |
|---|---|---|
| Run state | Did the execution satisfy the declared run? | `partial`, `failed`, `complete` |
| Item completion | Did provider calls return usable outputs? | `items=2/2 failed=0` |
| Quality metric | Were those usable outputs correct? | `exact_match_rate=0.5000` |
| Cost | What did this run consume? | `cost_usd=0.001831` |

A provider call can succeed while the model gives the wrong classification. That produces
a completed item with a lower quality score, not a failed item. Conversely, an API error
or unusable response can fail an item before its answer can be scored.

This tutorial shows representative output after each command, explains what it means, and
states the decision to make before continuing. Timestamps, run IDs, latency, cost, and
model scores will vary.

## 1. Install Tamesu

Create a Python 3.12 virtual environment from the Tamesu repository root and install it in
editable mode:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tamesu --help
```

The last command should start with output similar to:

```text
usage: tamesu [-h] [--version] [--config-dir CONFIG_DIR] ...
```

This only confirms that the `tamesu` executable resolves to the installed package. It
does not validate an eval or contact a provider.

Editable installation is appropriate while the framework and examples are developed in
the same repository. A packaged installation method will be documented when Tamesu has a
release process.

## 2. Enter the example project

Move into the example directory:

```sh
cd examples/support-ticket-classification
```

The directory is a standalone Tamesu project with its own `cases/` tree:

```text
support-ticket-classification/
├── .env.example
├── README.md
└── cases/
    └── support-ticket-triage/
        ├── README.md
        ├── case.yml
        ├── datasets/
        │   └── support-tickets-v1/
        │       └── dataset.yml
        └── experiments/
            └── decision-rules/
                ├── README.md
                ├── prompts/
                │   ├── basic-system.md
                │   ├── decision-rules-system.md
                │   └── user.md.j2
                ├── schemas/
                │   └── ticket-classification.schema.json
                └── evals/
                    └── prompt-ablation/
                        └── eval.yml
```

This layout represents one case, one experiment, and one eval:

```text
Case:       support-ticket-triage
Experiment: decision-rules
Eval:       prompt-ablation
```

The eval's command-line ID is therefore:

```text
support-ticket-triage/decision-rules/prompt-ablation
```

## 3. Inspect the dataset

Open:

```text
cases/support-ticket-triage/datasets/support-tickets-v1/dataset.yml
```

Each item contains a stable ID, an inline support message, and the expected structured
answer:

```yaml
- id: duplicate-charge
  input:
    message: >
      I was charged twice for my monthly subscription. Please refund the duplicate
      charge.
  expected:
    category: billing
    priority: high
    requires_human: true
```

There are no external dataset files. The structured-text task renders `input.message` into
the user prompt and compares the model's JSON response with `expected`.

The eight items cover billing, account access, technical problems, cancellation, and an
explicit request for a human. The set is intentionally small: it demonstrates the Tamesu
workflow rather than establishing production accuracy.

## 4. Inspect the experiment

The experiment asks:

> Do explicit routing rules improve exact-match support-ticket classification accuracy?

It contains two arms:

- `basic-prompt` lists the required fields and allowed values.
- `decision-rules` defines category, priority, escalation, and precedence rules.

Both arms use `prompts/user.md.j2`:

```jinja
Classify this support ticket:

{{ item.input.message }}
```

Only the system prompt changes. The dataset, model, output schema, parameters, repetitions,
and scoring stay fixed, so the prompt rules are the experiment's primary variable.

The shared JSON Schema requires exactly three fields:

```json
{
  "category": "billing",
  "priority": "high",
  "requires_human": true
}
```

Additional properties and values outside the declared enums are rejected.

## 5. Inspect the eval manifest

Open:

```text
cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/eval.yml
```

The important sections are:

```yaml
status: draft
task: structured_text
dataset: ../../../../datasets/support-tickets-v1/dataset.yml
output_schema: ../../schemas/ticket-classification.schema.json

defaults:
  repetitions: 2
  concurrency: 2
  timeout_seconds: 60
  retries: 1
  budget_usd: 2.00
  parameters:
    max_tokens: 1024
    effort: minimal
```

Farther down, the manifest maps each arm to its prompts and schedules both arms against
the same model:

```yaml
arms:
  - id: basic-prompt
    prompts:
      system: ../../prompts/basic-system.md
      user: ../../prompts/user.md.j2

  - id: decision-rules
    prompts:
      system: ../../prompts/decision-rules-system.md
      user: ../../prompts/user.md.j2

runs:
  - model: muse-spark-1.2
    provider: meta
    arms:
      - basic-prompt
      - decision-rules
```

The primary metric is `exact_match_rate`. All three returned fields must match the expected
record for an item to pass. Field-level accuracy and invalid-output rate are secondary
metrics that help explain failures.

## 6. Discover and validate the project

List the project contents:

```sh
tamesu list
```

Expected output:

```text
support-ticket-triage/decision-rules/prompt-ablation  draft
```

The first column is the eval ID accepted by later commands. The second is the eval's
workflow status. `draft` means the contract can be inspected but paid execution is still
blocked.

Now lint the full case:

```sh
tamesu lint cases/support-ticket-triage
```

Expected output:

```text
ok  support-ticket-triage/decision-rules/prompt-ablation
Validated 1 eval(s).
```

`ok` means the files form a valid evaluation contract: references resolve, values have
the expected types, and prompts can render. It says nothing about model quality and makes
no provider calls. Do not continue if lint prints `error`.

Linting should verify:

- manifest syntax and field types;
- unique case, dataset, item, arm, and eval IDs;
- prompt, schema, and dataset paths;
- the shape of inline inputs and expected answers;
- output-schema validity;
- the model and arm references;
- availability of the configured metrics.

Linting never calls a provider. Credential availability is an execution concern reported
by `plan`, not a structural lint failure.

## 7. Preview the work and cost

Define the eval ID once for the remaining commands:

```sh
EVAL_ID=support-ticket-triage/decision-rules/prompt-ablation
```

Expand the plan:

```sh
tamesu plan "$EVAL_ID"
```

On a fresh example before activation, expect output shaped like:

```text
Eval: support-ticket-triage/decision-rules/prompt-ablation
Status: draft
Models: muse-spark-1.2
Arms: basic-prompt, decision-rules
Planned runs: 4
Banked runs: 0
Runs owed: 4
Selected items per run: 8
Generation calls still owed: 32
Budget ceiling: $2.00
Recorded known cost: $0.000000
Estimated cost: $0.017782
Maximum additional exposure: $0.304832
Execution blockers:
- meta: META_API_KEY is not set in the effective environment
  (process, project, workspace, global, or --config-dir)
- eval status is 'draft'
```

Read this as a preflight decision:

- `Planned runs: 4` is the frozen measurement design.
- `Banked runs: 0` means no compatible completed evidence exists yet.
- `Runs owed: 4` means all four planned repetitions still need execution.
- `Generation calls still owed: 32` is the paid work remaining before retries.
- `Estimated cost` is a likely amount, while `Maximum additional exposure` assumes every
  call reaches its configured output cap and uses every allowed retry.
- The blocker list explains why execution is not yet allowed. At this point both the
  credential and draft status are expected.

If Tamesu inherited a valid key from another configuration layer, the credential blocker
will already be absent; the draft-status blocker should still be present.

If prior evidence exists, recorded cost and run counts will differ. The important go/no-go
checks are that the expanded work matches your intention, maximum exposure fits the
budget, and there are no blockers.

This eval has one model, two arms, and two repetitions:

```text
1 model × 2 arms × 2 repetitions = 4 runs
```

Each run contains all eight tickets, producing 32 planned model calls before retries:

```text
4 runs × 8 tickets = 32 calls
```

Planning is read-only. If the plan is surprising, return the eval to `draft`, correct the
contract, and repeat `lint` and `plan` before spending anything.

## 8. Configure the provider

The example manifest uses the Meta provider and `muse-spark-1.2`. Set `META_API_KEY` in
the process environment, the example project or enclosing Git workspace's `.env` or
`.tamesu/.env`, or the global `~/.tamesu/.env`. Never put a credential in a manifest. See
[Configuration](configuration.md) for precedence and explicit overrides.

After setting the key, run the plan again:

```sh
tamesu plan "$EVAL_ID"
```

The credential blocker should disappear, leaving only `eval status is 'draft'`. If the
plan still names `META_API_KEY`, Tamesu has not found the credential in the effective
configuration. Stop there and use the
[configuration troubleshooting steps](configuration.md#troubleshooting); do not diagnose
credentials by repeatedly running paid commands.

Muse Spark spends output tokens on private reasoning before producing visible JSON. The
example uses `effort: minimal` and leaves enough output budget for both stages; a cap that
is too small can produce an empty response with `finish_reason: length`.

If you want another provider, change `provider` and `model` together to a pair supported
by an implemented adapter. Lint and plan again after the edit because the provider and
model contribute to run identity and cost.

## 9. Activate the eval

After reviewing the manifest, lint result, expanded work, and cost exposure, activate the
draft:

```sh
tamesu activate "$EVAL_ID"
```

Expected output:

```text
Activated support-ticket-triage/decision-rules/prompt-ablation.
```

Activation validates the full contract and atomically changes `status: draft` to
`status: active`. It does not contact Meta or incur cost. Review and commit that manifest
change if the project keeps eval plans in version control.

Preview once more:

```sh
tamesu plan "$EVAL_ID"
```

Proceed only when lint reports `Validated 1 eval(s).`, the plan says `Status: active`, and
the plan ends with `Execution blockers: none`.

## 10. Run a two-item probe

Exercise both prompt arms on a small subset before running all 32 calls:

```sh
tamesu run "$EVAL_ID" \
  --only muse-spark-1.2 \
  --limit-items 2
```

A successful probe prints one line per arm and repetition, followed by a summary. This is
representative output from the bundled example:

```text
partial  muse-spark-1-2--basic-prompt--rep1--<timestamp>--<hash>  items=2/2  failed=0  exact_match_rate=1.0000  cost_usd=0.002135
partial  muse-spark-1-2--basic-prompt--rep2--<timestamp>--<hash>  items=2/2  failed=0  exact_match_rate=1.0000  cost_usd=0.001799
partial  muse-spark-1-2--decision-rules--rep1--<timestamp>--<hash>  items=2/2  failed=0  exact_match_rate=0.5000  cost_usd=0.001831
partial  muse-spark-1-2--decision-rules--rep2--<timestamp>--<hash>  items=2/2  failed=0  exact_match_rate=0.5000  cost_usd=0.001921
Probe items: 8/8 completed; 0 failed.
Probe runs are diagnostic and excluded from formal comparisons.
Next: tamesu run support-ticket-triage/decision-rules/prompt-ablation
```

Interpret one line from left to right:

- `partial` is the run state. Here it means the command intentionally selected only two
  of the eight declared items, so the run cannot satisfy a formal repetition.
- The run ID identifies the model, arm, repetition, timestamp, and fingerprint suffix.
- `items=2/2` means both selected items produced terminal results.
- `failed=0` means neither item encountered a provider, rendering, or artifact failure.
- `exact_match_rate=0.5000` means one of the two usable model answers matched the entire
  expected record. This is a quality result, not an execution failure.
- `cost_usd` is the recorded cost for that run based on returned token usage.

In this particular probe, the basic prompt classified both selected tickets correctly in
both repetitions. The decision-rules prompt missed one ticket's priority in both
repetitions. That is worth inspecting, but two selected tickets are too few to conclude
that the basic prompt is generally better.

This checks prompt rendering, structured-output handling, provider transport, JSON Schema
validation, deterministic scoring, logging, and artifact writes.

A limited run remains **partial** even when every selected item succeeds. It does not
satisfy a planned repetition and cannot enter the formal leaderboard.

Use the probe as a go/no-go check:

- Continue when all selected items completed, structured output is valid, costs are
  plausible, and any wrong answers look like genuine model behavior.
- Stop and inspect evidence when `failed` is nonzero, a run state is `failed`, output is
  invalid, or cost is surprising.
- Do not optimize the prompt based only on this two-item sample; that would turn the probe
  into an accidental test-set tuning loop.

Inspect the created evidence under the eval directory:

```text
evals/prompt-ablation/
├── logs/
│   └── <run-id>.jsonl
└── runs/
    └── <run-id>/
        ├── run.yml
        ├── report.yml
        └── items/
            └── <item-id>/
                ├── result.yml
                └── output.json
```

Start with `report.yml`; it is the run-level answer to “how did this sample perform?” A
decision-rules probe from the example contains:

```yaml
completion:
  selected_items: 2
  completed_items: 2
  failed_items: 0
metrics:
  exact_match_rate: 0.5
  category_accuracy: 1.0
  priority_accuracy: 0.5
  requires_human_accuracy: 1.0
  invalid_output_rate: 0.0
  median_latency_ms: 1505.5
  mean_cost_usd: 0.0009155
totals:
  cost_usd: 0.001831
```

This says execution was healthy—two outputs completed and neither failed—but only one
entire classification was correct. Category and escalation were correct for both items;
priority was correct for only one. That is why the exact-match rate is 0.5.

Next open the failing item's `result.yml` to localize the mismatch:

```yaml
item_id: password-reset
state: complete
mechanical_scores:
  valid_json: true
  schema_valid: true
  exact_match: false
  field_matches:
    category: true
    priority: false
    requires_human: true
```

`state: complete` and `exact_match: false` are not contradictory. The model returned a
usable, schema-valid answer, but the answer was wrong: it emitted priority `high` while
the dataset expected `normal`. This distinction lets you diagnose prompt quality without
mislabeling model mistakes as infrastructure failures.

For each probed item, check that:

- `output.json` is valid and matches the declared schema;
- `result.yml` records the checksum, attempts, latency, usage, cost, and scores;
- the JSONL log contains one entry per provider attempt;
- lifecycle, artifact, and item-terminal events surround those attempt entries;
- no credential appears in any stored file;
- a wrong classification is recorded as a model outcome, not a transport error.

If the probe exposes a problem with the plan, return the eval to `draft`, make the change,
and repeat review and activation. The probe remains diagnostic evidence, not formal eval
evidence.

## 11. Run the complete eval

Execute the work still owed:

```sh
tamesu run "$EVAL_ID"
```

Unlike the probe, each successful full run is terminally `complete` and covers all eight
items:

```text
complete  muse-spark-1-2--basic-prompt--rep1--<timestamp>--<hash>  items=8/8  failed=0  exact_match_rate=<score>  cost_usd=<cost>
complete  muse-spark-1-2--basic-prompt--rep2--<timestamp>--<hash>  items=8/8  failed=0  exact_match_rate=<score>  cost_usd=<cost>
complete  muse-spark-1-2--decision-rules--rep1--<timestamp>--<hash>  items=8/8  failed=0  exact_match_rate=<score>  cost_usd=<cost>
complete  muse-spark-1-2--decision-rules--rep2--<timestamp>--<hash>  items=8/8  failed=0  exact_match_rate=<score>  cost_usd=<cost>
```

`complete` means the run covered its entire declared sample and has the required terminal
artifacts. It does not mean the model answered every item correctly; read the primary
metric for quality. A complete run with `exact_match_rate=0.7500`, for example, executed
successfully and classified six of eight tickets exactly.

Tamesu creates a separate run directory for each arm and repetition. It never overwrites
the probe or another completed run.

If execution is interrupted, first inspect overall progress:

```sh
tamesu status "$EVAL_ID"
```

After four successful full runs, while retaining the four earlier probes, the count
section should look like:

```text
Planned: 4
Banked: 4
Owed: 0
Partial: 4
Failed: 0
Stale: 0
Extra: 0
```

These are reconciliation classifications, not all run states:

- `Planned: 4` is the number of slots declared by the manifest.
- `Banked: 4` means four complete runs match those slots and their fingerprints.
- `Owed: 0` means the formal evaluation has all required evidence.
- `Partial: 4` refers to the retained two-item probes; they remain visible but do not
  count against the plan.
- `Failed` is non-resumable execution evidence, `Stale` no longer matches the current
  plan, and `Extra` is compatible evidence beyond what the plan requires.

The command then lists each discovered run. A line beginning with `banked` is formal
evidence; one beginning with `partial` is diagnostic or resumable evidence. The decision
gate before comparison is `Banked == Planned` and `Owed == 0`.

If a full run was interrupted or exhausted a retryable failure, resume that compatible
partial run by its full ID:

```sh
tamesu resume <run-id>
```

Use the exact ID from the interrupted full run's `partial` status line. A successful
resume prints the same run ID because it continues that evidence stream rather than
creating a replacement. Run `status` again afterward and verify that the expected slot
became banked. Resuming a deliberately limited probe can complete its selected work, but
it remains a probe and never becomes banked.

Resume verifies the saved configuration, fingerprints, and artifact checksums. Completed
items are reused; only missing or safely retryable stages are scheduled.

If a prompt, dataset item, expected answer, schema, parameter, or implementation input has
changed, the old run is stale and cannot satisfy the revised plan. Start a fresh run after
reviewing the change.

## 12. Read the results

When all four runs are complete, compare them:

```sh
tamesu compare "$EVAL_ID"
```

The command prints one aggregate row per model and arm. The values below illustrate the
shape; your model scores and costs are the result of your own run:

```text
model            arm             repetitions  items  primary_mean  stdev   failure_rate  cost_usd
muse-spark-1.2   basic-prompt    2            16     <mean>        <sd>    0.0           <cost>
muse-spark-1.2   decision-rules  2            16     <mean>        <sd>    0.0           <cost>
```

Read the columns as follows:

- `repetitions=2` confirms both planned repetitions contributed to the row.
- `items=16` is eight tickets across each of those two repetitions.
- `primary_mean` is the average `exact_match_rate`; higher is better for this eval.
- `stdev` shows variation between repetitions. A large value means the result was
  unstable across repeated runs.
- `failure_rate` is provider/task execution failure, not model inaccuracy. A wrong but
  valid classification lowers exact match instead.
- `cost_usd` is the sum of recorded costs for the contributing runs.

The first comparison question is not “which number is larger?” Check completeness and
failure rate first. Then compare primary means, examine variation, and use field-level
metrics in each `report.yml` to explain the difference. For example, equal category
accuracy but lower priority accuracy localizes the regression to the priority rules.

Review:

- whole-record exact-match rate;
- category accuracy;
- priority accuracy;
- `requires_human` accuracy;
- invalid-output rate;
- variation across repetitions;
- latency and cost by arm.

No model judge is involved. Tamesu derives every quality metric by comparing parsed output
with the expected record in `dataset.yml`.

If the command says `No compatible completed runs to compare`, read the lines beneath it.
They report excluded partial, failed, or stale evidence and the number of planned runs
still owed. A successful probe alone is not enough for formal comparison.

The small dataset is suitable for learning the workflow, not for claiming that one prompt
is generally superior. A production decision would require more representative items and
an appropriate uncertainty analysis.

### Turn the table into a bounded conclusion

In one run of this example, the comparison looked like this:

```text
muse-spark-1.2  basic-prompt    2  16  0.6875  0.0625  0.0  0.0139
muse-spark-1.2  decision-rules  2  16  0.7500  0.0000  0.0  0.0132
```

The decision-rules arm recorded an exact-match mean 0.0625 higher than the basic prompt.
Across 16 scored item observations per arm, that difference corresponds to one additional
whole-record match. Both repetitions of the decision-rules arm scored 0.7500, while the
basic arm varied between repetitions.

The diagnostic metrics made the tradeoff clearer: the decision-rules arm was better at
the `requires_human` decision but slightly worse on priority. There were no invalid JSON
outputs or execution failures, and cost was similar.

A defensible reading is:

> On this eight-item synthetic dataset, the decision-rules prompt had a higher observed
> exact-match rate, mainly alongside better escalation decisions. The sample is too small
> to claim that it is generally better, and its priority regression deserves another
> targeted experiment.

That conclusion separates what was observed from what remains uncertain. It does not say
the rules caused the difference, that the prompt is production-ready, or that the same
result will hold on real support traffic.

## 13. Generate the leaderboard and report

Build the Markdown summary:

```sh
tamesu leaderboard "$EVAL_ID"
```

Expected output is the path written by the command:

```text
<project>/cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/leaderboard.md
```

Open that file. Its table should contain the same model/arm aggregates as `compare`, while
the run links below the table provide the audit trail back to each `report.yml`. If a row
is missing, return to `status`; the leaderboard intentionally excludes non-banked runs.

This writes `leaderboard.md` beside `eval.yml`. It summarizes each prompt arm across its
completed repetitions and links back to the underlying run reports.

The leaderboard is a derived view. The dataset, run manifests, item outputs, results, and
reports remain the evidence.

Now generate the human-readable evaluation report:

```sh
tamesu report "$EVAL_ID"
```

Expected output is another written path:

```text
<project>/cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/evaluation-report.md
```

Open that file after the leaderboard. Its `What happened` section states whether the
planned evidence is complete and summarizes the observed primary-metric difference. The
diagnostic sections show where each arm performed differently. `Evidence coverage` lists
anything excluded, while `Limitations` prevents the aggregate table from being read as a
broader claim than the dataset supports.

The report is generated from stored facts and uses no AI model. It will not explain why
the difference occurred or recommend a product decision. For that deeper synthesis, use
the `tamesu-eval-analysis` coding-agent skill after reviewing the deterministic report.

## 14. Close the eval

When every repetition and report is complete, close the eval:

```sh
tamesu close "$EVAL_ID"
```

Expected output:

```text
Closed support-ticket-triage/decision-rules/prompt-ablation.
```

Confirm that `eval.yml` now says `status: complete` and that `leaderboard.md` and
`evaluation-report.md` were rebuilt. At this point `run` refuses new paid work unless you
deliberately create a new eval or reopen the lifecycle through an explicit reviewed edit.

Closing changes the status to `complete` and prevents accidental new paid runs. Review and
commit the updated manifest and leaderboard.

If required work is missing, `close` refuses and explains what remains. Missing or failed
items are never silently excluded to make the result look complete.

## Troubleshooting map

| Symptom | Start here |
|---|---|
| Invalid manifest or missing prompt | `tamesu lint` output |
| Unexpected calls or cost | `tamesu plan` output |
| Probe says `partial` but every item completed | The probe explanation in step 10 |
| Missing or stale repetition | `tamesu status` output |
| Provider failure or retry | `logs/<run-id>.jsonl` |
| Invalid JSON or wrong classification | `items/<item-id>/result.yml` |
| Unexpected resolved prompt or parameter | `runs/<run-id>/run.yml` |
| Unexpected aggregate metric | `runs/<run-id>/report.yml` |
| Unsure what the leaderboard means | `evaluation-report.md` |

## Make the example your own

A safe first modification is to copy the entire example project and change only one part
of the experiment:

1. Add representative synthetic tickets and expected answers to `dataset.yml`.
2. Adjust the allowed labels in the JSON Schema if your routing taxonomy differs.
3. Update both prompts to use the same taxonomy.
4. State one prompt hypothesis in the experiment README.
5. Keep the model, parameters, dataset, schema, and scorer fixed across arms.
6. Run `lint` and `plan` after every contract change.

Use the [Manifest reference](manifests.md) for field details and the
[CLI reference](cli.md) for full command behavior. Use
[Logging and execution evidence](logging.md) to interpret a run timeline. Framework
contributors should use the [Contributor development guide](development.md).
