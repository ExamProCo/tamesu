# Tutorial: run your first text eval

> **Status:** This tutorial is runnable with Tamesu 0.1. Its live execution step uses the
> OpenAI API and may incur cost.

This tutorial uses the self-contained
[support-ticket classification project](../examples/support-ticket-classification/). It
compares a basic prompt with a prompt containing explicit decision rules.

The example needs no image dataset, model judge, or human review. Its eight synthetic
tickets and expected answers live directly in one YAML file, so every quality score can be
calculated deterministically.

By the end, you will know how to:

- inspect a Tamesu project;
- validate and plan an eval;
- run a small probe before the full evaluation;
- inspect and resume stored work;
- compare two experimental arms;
- generate a leaderboard and close the eval.

## 1. Install Tamesu

Create a Python 3.12 virtual environment from the Tamesu repository root and install it in
editable mode:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tamesu --help
```

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
    max_tokens: 256
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
  - model: gpt-5.4-mini
    provider: openai
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

You should find the `support-ticket-triage` case and its draft `prompt-ablation` eval.

Now lint the full case:

```sh
tamesu lint cases/support-ticket-triage
```

Linting should verify:

- manifest syntax and field types;
- unique case, dataset, item, arm, and eval IDs;
- prompt, schema, and dataset paths;
- the shape of inline inputs and expected answers;
- output-schema validity;
- the model and arm references;
- availability of the configured metrics.

Linting never calls a provider. Missing credentials may be reported as an execution
blocker, but do not make the file structure invalid.

## 7. Preview the work and cost

Define the eval ID once for the remaining commands:

```sh
EVAL_ID=support-ticket-triage/decision-rules/prompt-ablation
```

Expand the plan:

```sh
tamesu plan "$EVAL_ID"
```

This eval has one model, two arms, and two repetitions:

```text
1 model × 2 arms × 2 repetitions = 4 runs
```

Each run contains all eight tickets, producing 32 planned model calls before retries:

```text
4 runs × 8 tickets = 32 calls
```

Before continuing, confirm the reported run count, item count, estimated cost, maximum
exposure, unknown prices, credentials, and work already completed.

Planning is read-only. If the plan is surprising, edit the draft and repeat `lint` and
`plan` before spending anything.

## 8. Configure the provider

The example manifest uses the OpenAI provider and `gpt-5.4-mini`. Set `OPENAI_API_KEY` in
your environment, as documented by `.env.example`; never put a credential in a manifest.

If you want another provider, change `provider` and `model` together to a pair supported
by an implemented adapter. Lint and plan again after the edit because the provider and
model contribute to run identity and cost.

## 9. Activate the eval

After reviewing the question, labels, prompts, sample size, model, and cost, change:

```yaml
status: draft
```

to:

```yaml
status: active
```

Review and commit that change. Activation freezes the measurement plan and permits paid
execution.

Run the safety checks once more:

```sh
tamesu lint cases/support-ticket-triage
tamesu plan "$EVAL_ID"
```

## 10. Run a two-item probe

Exercise both prompt arms on a small subset before running all 32 calls:

```sh
tamesu run "$EVAL_ID" \
  --only gpt-5.4-mini \
  --limit-items 2
```

This checks prompt rendering, structured-output handling, provider transport, JSON Schema
validation, deterministic scoring, logging, and artifact writes.

A limited run remains **partial** even when every selected item succeeds. It does not
satisfy a planned repetition and cannot enter the formal leaderboard.

Inspect the created evidence under the eval directory:

```text
evals/prompt-ablation/
├── logs/
│   └── <run-id>.jsonl
└── runs/
    └── <run-id>/
        ├── run.yml
        └── items/
            └── <item-id>/
                ├── result.yml
                └── output.json
```

For each probed item, check that:

- `output.json` is valid and matches the declared schema;
- `result.yml` records the checksum, attempts, latency, usage, cost, and scores;
- the JSONL log contains one entry per provider attempt;
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

Tamesu creates a separate run directory for each arm and repetition. It never overwrites
the probe or another completed run.

If execution is interrupted, first inspect overall progress:

```sh
tamesu status "$EVAL_ID"
```

Then resume a compatible partial run by its full ID:

```sh
tamesu resume <run-id>
```

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

The small dataset is suitable for learning the workflow, not for claiming that one prompt
is generally superior. A production decision would require more representative items and
an appropriate uncertainty analysis.

## 13. Generate the leaderboard

Build the Markdown summary:

```sh
tamesu leaderboard "$EVAL_ID"
```

This writes `leaderboard.md` beside `eval.yml`. It summarizes each prompt arm across its
completed repetitions and links back to the underlying run reports.

The leaderboard is a derived view. The dataset, run manifests, item outputs, results, and
reports remain the evidence.

## 14. Close the eval

When every repetition and report is complete, close the eval:

```sh
tamesu close "$EVAL_ID"
```

Closing changes the status to `complete` and prevents accidental new paid runs. Review and
commit the updated manifest and leaderboard.

If required work is missing, `close` refuses and explains what remains. Missing or failed
items are never silently excluded to make the result look complete.

## Troubleshooting map

| Symptom | Start here |
|---|---|
| Invalid manifest or missing prompt | `tamesu lint` output |
| Unexpected calls or cost | `tamesu plan` output |
| Missing or stale repetition | `tamesu status` output |
| Provider failure or retry | `logs/<run-id>.jsonl` |
| Invalid JSON or wrong classification | `items/<item-id>/result.yml` |
| Unexpected resolved prompt or parameter | `runs/<run-id>/run.yml` |
| Unexpected aggregate metric | `runs/<run-id>/report.yml` |

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
[CLI reference](cli.md) for full command behavior. Framework contributors should use the
[Contributor development guide](development.md).
