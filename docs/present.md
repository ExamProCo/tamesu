# What `tamesu present` is, and what it shows when

`present` renders the **current state of a case** as human-readable static HTML. It works at
every point in an eval's life: before anything has run, halfway through, awaiting review,
and closed. Check it as often as you like; a re-render takes a fraction of a second.

```sh
tamesu present support-ticket-triage            # writes build/present/<case>/
tamesu present support-ticket-triage --open     # ...and opens it
tamesu present support-ticket-triage --watch    # re-renders when a case file changes
tamesu present support-ticket-triage --strict   # still writes the page; exits nonzero if any eval is invalid
```

## The contract

| Property | Meaning |
|---|---|
| Pure projection | The output is a function of the case files. The same input gives the same bytes. |
| Offline | No provider call, no model call, no network, no Docker, and project Python (an Inspect `task.py`) is never imported. |
| Read-only | It writes only under its output directory. It never creates or edits `analysis.md`, reports or evidence. |
| Total | A problem in one eval is shown on the page. It is not a reason to render nothing. |
| Fast | Cheap enough to run after every edit. |
| Honest about provenance | Every block of prose says whether it was authored, computed, or model-assisted. |
| Safe | Model output, logs and source are escaped, stripped of terminal escapes, size-capped and never executed. |

`present` does not run, score, judge, decide acceptance, or interpret results beyond what a
fixed template can state. Anything that needs a model to say more is
[`tamesu analyze`](#separating-analysis-from-presentation).

## Where an eval is in its life

One function, `lifecycle()`, derives the state from facts. `present`, `status` and the
evaluation report all use it, so a word means one thing everywhere.

| State | Facts | Badge |
|---|---|---|
| Invalid | the eval does not validate (or cannot be built) | **Invalid**, with the problems listed |
| Draft | valid, `status: draft` | **Draft** |
| Ready | valid, `active`, nothing banked | **Ready to run** |
| Partial | some banked and some owed, partial or failed | **Partially run** |
| Awaiting review | all banked, required reviews or judgments still owed | **Awaiting review** |
| Evidence complete | all banked, nothing owed, not closed | **Evidence complete** |
| Closed | `status: complete` after `tamesu close` | **Closed** |

Stale or excluded runs, failed runs, and a close that allowed a review gap appear as notes
under the badge, not as states of their own.

## What each state shows

Before anything has run, the page shows the **design** (the Design section):

- the run matrix: model x arm x repetitions, with parameters and banked or owed counts;
- every dataset item with its input and expected value;
- the exact prompts each arm sends for the first item, or the template error that stops it;
- how it is judged: mechanical scorers and `pass_at`, the review rubric and its reason codes,
  the acceptance policy, any judges;
- how it executes: for an Inspect eval, the task, declared sources, limits, artifact roles,
  image digests (flagged if a tag is mutable) and model routes;
- cost: budget ceiling, estimated and maximum exposure, and static blockers such as a missing
  credential *name*, a draft status, or an unreviewed package.

`present` does not check Docker or that `inspect-ai` is installed; the page says so.

After runs exist, the design collapses and the page adds the observed outcome, the results
table, **Evidence at a glance**, any analysis, and per-item evidence. Each task decides what an
item shows:

- text: input, expected and output side by side, with the scores;
- images: the image, the input, and mechanical, judge and human evidence in separate columns;
- artifact bundles: scores, run facts (stop reason, tokens, limits hit), a failure headline
  such as "first error: ... isn't constant", capped excerpts of the compiler and run logs,
  links to the generated source, and the review render.

## Honest language

These are template rules, so they cannot drift:

- No leader is named while review or judgment is owed, from a single sample, or before anything
  has run.
- Three or more rows are summarized as a range ("Across 3 rows ... ranges from ... to ...").
  Two of three are never named as if they summarized the rest.
- With one item and one repetition per row, the page says this is not a comparison.
- Mechanical, model-judge and human results stay in separate columns.

## Separating analysis from presentation

Three kinds of text can appear. Each is labelled.

| Kind | Produced by | Stored | Label |
|---|---|---|---|
| Authored | a person | `analysis.md`, READMEs, manifests | "Authored" (draft, verified or unverified) |
| Computed | fixed templates over evidence, at render time | **nowhere** | "Computed from the stored evidence each time this page is built" |
| Model-assisted | `tamesu analyze`, a separate command | `analysis.md` with provenance | "Model-assisted analysis (model), written when; matches the shown evidence" or "STALE" |

`run`, `judge` and `review import` never create or edit `analysis.md`. The "Evidence at a
glance" counts are computed on every render, so they cannot go stale. Older files that still
contain a generated "Evidence at a glance" section are shown without it.

```sh
tamesu analyze <eval> --scaffold                       # an authored template; no model call
tamesu analyze <eval> --provider meta --model muse-spark-1.2   # a model writes the analysis
```

The model-assisted form calls a model and costs money, so you must name the provider and
model. It refuses unpriced models, enforces a worst-case dollar limit (`--max-cost`,
default $0.25), never overwrites an analysis a person wrote without `--force`, and treats the
evidence as data rather than instructions. It records `generated_by: model`, the model, a
hash of the prompt, and the evidence digest in the front matter, so `present` can say who
wrote the text and whether the evidence has changed since. It never changes evidence. Model
*judging* (`tamesu judge`) is evidence, not analysis, and stays separate.

`close` binds an **existing** analysis to the final evidence; it never creates one.

## Bad states

An eval that does not validate becomes an **Invalid** card listing the problems; every
other eval and the case page still render. If Tamesu itself fails while building an eval's
page, the card says it is an internal error, not a problem in your manifest. Paths in these
messages are relative to the project, never your home directory. `--strict` writes the page
and then exits nonzero, for CI.

## Logs and source on the page

Logs and generated source are shown and copied as `.txt` assets (never executed) when you
run `present` locally. The public showcase site withholds them, because compiler output can
contain local paths. Inspect transcripts are never copied.
