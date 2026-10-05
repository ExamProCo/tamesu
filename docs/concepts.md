# Core concepts

This page defines Tamesu's repository and execution model. If you are deciding how to
frame an evaluation, begin with [The Tamesu approach](approach.md).

Tamesu treats an evaluation as a versioned plan backed by inspectable evidence. The
filesystem is the source of truth: manifests describe what should happen, while run
artifacts show what actually happened.

The framework has four levels:

```text
Case → Experiment → Eval → Run
```

| Level | The question it answers | What it owns |
|---|---|---|
| Case | What real-world problem are we investigating? | Background, datasets, shared assets, and success criteria |
| Experiment | What specific hypothesis are we testing? | Arms, prompts, schemas, rubrics, and task-specific scoring |
| Eval | How will we measure this hypothesis? | The frozen sampling plan, run matrix, metrics, budgets, and completion state |
| Run | What happened during one execution? | Resolved configuration, outputs, logs, scores, cost, and provenance |

## Case

A **case** is the broadest unit of work. It provides the stable context and reusable
inputs for a family of experiments.

For example, a case might ask:

> Can image models produce a believable product-scanning interaction?

The case owns the product dataset, scanner reference images, and the criteria that define
a believable result. It does not own provider settings or the output of a particular
model run.

## Experiment

An **experiment** narrows a case to one hypothesis. It defines the factor being changed
and the experimental arms used to compare it.

For example:

> Supplying a scanner reference image improves geometric alignment.

That experiment could have two arms:

- `product-only`
- `product-plus-scanner`

Everything else should remain fixed or be represented explicitly in the experiment. If
both the prompt and reference-image strategy change, they are separate factors—not one
ambiguous comparison.

An experiment may be measured more than once. Its prompts and rubric can be shared by
several evals with different datasets, models, or sample sizes.

## Eval

An **eval** is a specific, reviewable measurement plan for an experiment. It freezes the
question before execution begins.

An eval declares:

- the dataset and selected items;
- the models, providers, and experimental arms;
- the number of repetitions;
- generation and judging parameters;
- primary and secondary metrics;
- retry, concurrency, timeout, and cost limits;
- any required human review;
- whether the eval is a draft, active, or complete.

The eval manifest describes work that is **owed**. It is not a record that the work has
already happened. Tamesu determines actual progress from compatible run artifacts on
disk.

This distinction prevents an edited manifest, an incomplete output directory, or a
successful-looking filename from being mistaken for completed evidence.

## Run

A **run** is one execution of one expanded eval configuration. A run normally processes
all selected dataset items for one model, arm, parameter set, and repetition.

For example:

```text
model:      gpt-image-2
arm:        product-plus-scanner
repetition: 2
items:      all 12 selected products
```

Dataset items are work units inside the run; they are not separate runs. This makes one
repetition a complete sample over the declared dataset.

A run preserves enough evidence to explain and reproduce the execution:

- the fully resolved configuration;
- input, prompt, schema, and evaluator fingerprints;
- per-item outputs and validation results;
- provider attempts and normalized errors;
- scores and judgments;
- timing, usage, and cost;
- framework, runtime, dependency, and source-control provenance.

Runs are immutable evidence. Re-running an eval creates a new run, while rescoring reads
the stored outputs and produces a new derived report without regenerating them.

The run's append-only event stream explains provider attempts, retries, artifacts, and
terminal transitions without copying raw prompts into the log. See
[Logging and execution evidence](logging.md).

## From question to evidence

Using the product-scanning example, the hierarchy looks like this:

```text
Case
└── Product-scanning alignment
    └── Experiment
        └── Scanner-reference ablation
            └── Eval
                └── Compare two arms over 12 products with 3 repetitions
                    └── Run
                        └── One model + one arm + one repetition
```

The planner expands the eval's declared matrix into concrete runs. If the eval contains
two models, two arms, and three repetitions, it owes 12 runs:

```text
2 models × 2 arms × 3 repetitions = 12 runs
```

Planning is read-only and makes no provider calls. Execution creates the evidence needed
to satisfy those planned run slots.

## Plans, evidence, and derived reports

Tamesu separates three kinds of files:

| Kind | Purpose | Examples |
|---|---|---|
| Plan | Declares intended work | Case, dataset, and eval manifests; prompts; rubrics |
| Evidence | Records what occurred | Run manifests, outputs, call logs, judgments |
| Derived view | Summarizes or interprets existing evidence | Reports, comparisons, leaderboards, analyses |

Plans and evidence should be preserved in version control or durable storage. Derived
views can be rebuilt from the evidence and may be replaced by explicit reporting or
rescoring commands.

Generation and scoring remain separate. This allows a stored result to be evaluated with
an improved scorer without paying to generate it again, while retaining the original
output and judgment history.

## Identity and reproducibility

Names make runs readable, but content determines whether they are compatible. Tamesu
uses two identities:

- The **specification fingerprint** describes the intended measurement: the model,
  provider, arm, repetition, selected items, parameters, retry policy, and judge setup.
- The **content fingerprint** describes the material and machinery used: datasets,
  prompt sources, schemas, structured-text scoring and task code, and framework version.

A previous run satisfies an eval slot only when both fingerprints match. A run with the
right label but a changed prompt, dataset, or evaluator is **stale** evidence for the
current eval rather than a compatible result.

## Completion and failure

Run states follow this lifecycle:

```text
planned → running → partial | failed | complete
```

- **Partial** means some valid work can be reused, but the declared sample is incomplete.
- **Failed** means the run cannot safely continue, such as when its saved manifest is
  invalid.
- **Complete** means every selected item has a valid terminal artifact and all mandatory
  judging is present.

Only complete, compatible runs satisfy planned repetitions. Partial and failed runs still
remain visible for diagnosis, including their attempts and costs. Failed items are never
silently removed from metric denominators.

Scratch work belongs in an explicitly marked scratch eval. It can help refine an idea,
but it cannot silently become leaderboard evidence for a preregistered eval.

## Three kinds of scoring

Tamesu keeps scoring sources separate:

1. **Mechanical scoring** checks deterministic facts such as whether an output exists,
   decodes successfully, or has the expected dimensions.
2. **Model judging** evaluates qualities that cannot be measured mechanically using a
   structured, versioned rubric.
3. **Human review** records blinded human decisions when they are required to support a
   conclusion.

These scores may appear beside one another in a report, but they are not silently blended
into one opaque number. Readers should always be able to tell which kind of evidence
supports a result.

## Design principles

The core model follows a few practical rules:

- Every normal run belongs to a declared question.
- Manifests describe the plan; artifacts prove completion.
- Existing evidence is never overwritten by another execution.
- Only complete, compatible samples count toward an eval.
- Generation is stored independently from scoring.
- Identity comes from content, not filenames.
- Deterministic scoring is preferred where possible.
- Cost and missing credentials are visible before paid execution.
- The repository remains understandable without running Tamesu.

For the detailed implementation contract, see the
[evaluation framework implementation plan](../plans/EVALUATION-FRAMEWORK-IMPLEMENTATION.md).
