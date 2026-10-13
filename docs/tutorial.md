# An experiment, a portable package, and a review

## Create and run

Install Tamesu with `pip install -e .` from this repository, then run:

```sh
tamesu new eval arithmetic/first/baseline
```

Open `cases/arithmetic/experiments/first/evals/baseline/eval.yml`. It contains the
question, a small dataset, inline prompts, the model configuration, and execution
limits. Change the model/provider to match your credentials if needed. For the
default model, set `META_API_KEY` in your environment or local `.env`.

`task.py` is an Inspect task. Its scorer stores the answer as an artifact and checks
it against the expected answer. Replace the example with inputs and scoring that
represent your actual question. Inspect supports custom and model-graded scorers.

```sh
tamesu plan arithmetic/first/baseline
tamesu run arithmetic/first/baseline
tamesu show arithmetic
```

`plan` shows work and estimated exposure. Cost limits stop work after spending is
observed, so they are not guaranteed billing caps. Run statuses describe what
actually finished. No activation or closing command is needed.

Write findings and limits into the description or add a README when the experiment
needs more narrative. The page shows readable evidence before the detailed metrics.
Its Data (JSON) link exposes the same stored information to scripts.

The local web overview refreshes every second and shows answers/checks together.
Run `show` before `run` in a second terminal to watch item completion and recorded
cost. Costs become visible as items finish.

## Add optional human review

Create `rubric.yml` beside `eval.yml`:

```yaml
schema_version: 1
name: answer-quality
rationale: answer-quality.md
dimensions:
  - id: useful
    question: Does the answer correctly address the question?
    reason_codes: [incorrect, incomplete, other]
acceptance:
  rule: all_dimensions_pass
```

Write `answer-quality.md` explaining the criterion. Add this to the `evaluation`
block, leaving its mechanical scorer in place:

```yaml
human_review:
  rubric: rubric.yml
acceptance:
  requires: [mechanical, human]
```

The starter's `execution.artifacts.review: response` selects the answer for review.
For a custom task with several outputs, declare their roles in `required`/`allowed`
and select them with `review: [response, source, render]`. Text/code appears as text,
images appear inline, and other files can be downloaded. Missing required review
artifacts remain failures. Only experiments declaring human review need a reviewer.

Changing an experiment's acceptance configuration changes its execution identity;
declare required review before running. Rubrics and responses added to an existing
run can still be inspected and reviewed, but that run may no longer bank for the
new configuration.

For local review, open `tamesu show arithmetic --open`, click **Review / update
decisions**, enter your reviewer ID, answer the rubric, and click **Save review**.
Responses are validated and recorded directly; return to the overview to see them.

For a reviewer elsewhere, export a portable blinded pack:

```sh
tamesu review export arithmetic/first/baseline --out review-pack
```

Open `review-pack/review.html`, enter a reviewer ID, answer each rubric dimension,
and download `responses.yml` into the pack directory. The pack hides model and arm
names; keep the generated key files out of the reviewer's folder.

```sh
tamesu review import arithmetic/first/baseline review-pack/responses.yml
tamesu review status arithmetic/first/baseline
```

Human decisions and automated scores remain separate. Modified artifacts or a
changed rubric invalidate older review records rather than silently reusing them.

## Share the complete experiment

```sh
tamesu pack arithmetic
tamesu verify build/packages/local-arithmetic-0.1.0.tamesu.tar.gz
tamesu unpack build/packages/local-arithmetic-0.1.0.tamesu.tar.gz --cases-dir received/cases
cd received
tamesu show arithmetic
```

No `publication.yml` is required. Existing publication metadata and package profiles
remain supported for older archives. The default archive contains authored files,
results, artifacts, recorded reviews, and the rendered page. Raw Inspect transcripts
stay local; retained scores and artifacts support offline review, but custom
Inspect scorers cannot be replayed from an archive lacking their original logs.
Rerunning requires the model credentials, Tamesu/Inspect dependencies, task-specific
dependencies, and any configured sandbox. Opening or reviewing does not.

To review elsewhere, unpack the complete archive and export a fresh blinded review
pack there. Send only the review pack to the blinded reviewer; the complete archive
contains model identities and review keys. Import responses in that unpacked project,
then repack it to carry the new reviews back.

See [migration notes](reference/inspect-only.md) for older manifests and workflows.
