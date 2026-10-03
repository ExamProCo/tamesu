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

- `docs/concepts.md` for Case → Experiment → Eval → Run;
- `docs/file-structure.md` for placement and ownership;
- `docs/manifests.md` for the current schema;
- `docs/providers.md` and `docs/configuration.md` for model and credential constraints.

Treat those project-local documents and `tamesu lint` as authoritative over examples in
this skill. If the project has no existing Tamesu pattern or local docs, read
[the structured-text authoring reference](references/structured-text.md).

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

Keep the eval at `status: draft` while authoring. Never place API keys, tokens, customer
secrets, or signed URLs in manifests, prompts, fixtures, or committed `.env` files.

## Validate before activation

Run these read-only checks from the project root:

```sh
tamesu lint <case-or-eval-path>
tamesu plan <case>/<experiment>/<eval>
```

Resolve lint failures. Inspect the expanded models, arms, repetitions, item count, calls,
budget ceiling, estimated cost, maximum exposure, and execution blockers. Summarize these
for the user, including unknown pricing or missing credentials.

Only activate after the user has reviewed or explicitly requested activation:

```sh
tamesu activate <case>/<experiment>/<eval>
```

Activation changes the authored manifest from `draft` to `active`; it does not call a
provider. Do not run `tamesu run` merely because authoring or activation was requested.
Provider execution can spend money and requires separate user intent.

## Interpret and preserve evidence

When execution is requested, prefer a small `--limit-items` probe before a full run unless
the user requests otherwise. Explain run state separately from item completion, quality
metrics, and cost. Probe runs are `partial` by design and do not count as formal evidence.

Do not hand-edit generated `runs/`, `logs/`, `result.yml`, or `report.yml` evidence. Use
`status`, `resume`, `rescore`, `compare`, and `leaderboard` as appropriate. Use `close`
only after all planned work is banked and the result has been reviewed.

At handoff, name the authored files, state what variable each arm changes, report lint and
plan results, and clearly distinguish remaining blockers from optional next steps.
