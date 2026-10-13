---
name: tamesu-eval-authoring
description: Author or revise file-based Tamesu evaluation cases, datasets, experiments, prompts, schemas, and eval manifests. Use when designing an AI eval in a repository that uses Tamesu; do not use for framework implementation or unrelated test suites.
---

# Tamesu eval authoring

Create a reviewable evaluation contract whose intended variable, inputs, outputs, scoring,
cost boundary, and lifecycle are clear before paid execution.

## Start with the repository

Find the Tamesu project root containing `cases/`. Inspect nearby cases before introducing
new conventions. Read the repository's Tamesu documentation when available, especially:

- `docs/reference/concepts.md` for Case → Experiment → Eval → Run;
- `docs/reference/file-structure.md` for placement and ownership;
- `docs/reference/manifests.md` for the current schema;
- `docs/reference/providers.md` and `docs/reference/configuration.md` for model and credential constraints.

Treat those project-local documents and `tamesu lint` as authoritative over examples in
this skill. If the project has no existing Tamesu pattern or local docs, read
[the structured-text authoring reference](references/structured-text.md). For image
generation evals, read `docs/reference/image-evals.md` and follow
[the image-generation authoring reference](references/image-generation.md).

## Author the evaluation contract

Clarify the question and the decision the result should inform. Keep each experiment
focused on one interpretable factor; hold the dataset, model settings, schema, and scoring
fixed across arms unless one of them is intentionally the factor under test.

Reuse an existing case or dataset when it represents the same real-world problem and
population. Otherwise create the smallest valid structure described by the local manifest
reference. Use stable lowercase kebab-case IDs and explicit relative paths. For structured
text, define representative dataset inputs and expected outputs, a strict output schema,
prompt files, deterministic scorers where possible, one primary metric, and useful
diagnostic metrics.

Create the case first, describe the business use and current problem yourself, then
create an experiment with a question, hypothesis, and success criteria. Create evals
only after deciding what evidence the experiment requires:

```sh
tamesu case new <case>
tamesu exp new <case>/<experiment>
tamesu eval new <case>/<experiment>/<eval>
```

Read `case.yml` and `experiment.yml` before proposing evals. Use arms for prompt or model
parameter variations rather than creating a separate eval for each effort level. Use
inline datasets or `dataset: "{{ root.datasets }}/<dataset>/dataset.yml"`; the variable
refers to the current case's datasets directory. Keep evals under `experiments/<exp>/evals/<eval>/`.

For custom Inspect tasks, declare `evaluation.scorers: [correctness]` and
`metrics.primary: correctness/accuracy`. The scorer is an authored Python function in
`task.py`; its metrics and repetition reduction belong to Inspect. Tamesu owns saved
artifacts, costs, completion, comparisons, and review. Describe coverage and success
criteria without claiming that authored prose is automatically verified.

Never place API keys, tokens, customer secrets, or signed URLs in manifests, prompts,
fixtures, or committed `.env` files.

## Validate before execution

```sh
tamesu lint .
tamesu plan <case>/<experiment>/<eval>
tamesu run <case>/<experiment>/<eval> --arm baseline --limit-items 5 --repetitions 1 --dry-run
```

Resolve lint failures and inspect models, arms, unique items, repetitions, actual executions,
probe status, budget, estimated exposure, and execution blockers. No activation or closing
command is required. Authoring intent alone does not request paid provider execution.

For a reproducible probe use `--limit-items N --sample random --seed N`. Select an arm
with repeatable `--arm`, a model with `--model`, or explicit items with repeatable `--item`.
Use `--max-executions` to cap work and `--concurrency` / `--budget-usd` to set invocation limits.

## Interpret and preserve evidence

When execution is requested, prefer a small `--limit-items` probe before a full run unless
the user requests otherwise. Explain run state separately from item completion, quality
metrics, and cost. Probe runs are `partial` by design and do not count as formal evidence.

Do not hand-edit generated `runs/`, `logs/`, `result.yml`, or `report.yml` evidence. Use
`status`, `resume`, `rescore`, `compare`, and `leaderboard` as appropriate. Review conclusions in `analysis.md` after evaluating completion and success criteria.

At handoff, name the authored files, state what variable each arm changes, report lint and
plan results, and clearly distinguish remaining blockers from optional next steps.
