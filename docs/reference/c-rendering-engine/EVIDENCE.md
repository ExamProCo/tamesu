# C rendering engine: evidence, review, and report

Complete [WALKTHROUGH.md](../../../examples/c-rendering-engine/WALKTHROUGH.md) first. This guide starts after all three executable runs
have finished and explains how to inspect and finish the evaluation.

Run every command from the clean example copy at `/tmp/c-render-walk` with the virtual environment
active.

## 1. Inspect what Tamesu stored

```sh
find cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable \
  -path '*/inspect/staging' -prune -o -type f -print | sort | head -50
```

For a run under `evals/executable/runs/<run-id>/`, the useful files are:

1. `run.yml`: resolved configuration, fingerprints, model route, Inspect version, and log digest.
2. `inspect/input.jsonl`: the frozen, fully rendered prompt given to Inspect.
3. `items/*/result.yml`: score, outcome, output, missing artifacts, usage, and stop reason.
4. `items/*/artifacts/`: generated C source, compiler log, run log, and—when successful—the PNG.
5. `inspect/lineage.yml`: attempts made for this run.

The eval-level `logs/<run-id>.jsonl` is the lifecycle index. Inspect's `.eval` file contains the
full transcript and scorer explanation. To use Inspect's viewer:

```sh
inspect view --log-dir cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/runs/<run-id>/inspect
```

Inspect transcripts can contain full prompts and repository metadata. Tamesu therefore excludes
them from packages and publication.

## 2. Review the images without model labels

```sh
tamesu review export executable \
  --out cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/review/pack
tamesu review status executable
```

Items without an image are skipped. Open `review/pack/review.html`. It shows the prompt and image
but hides the model, arm, and run. The private mapping is stored separately under `review/keys/`.

Enter a reviewer ID, mark the five dimensions, and download the result as
`review/pack/responses.yml`. A failed dimension requires one of the rubric's reason codes. Import
the response:

```sh
tamesu review import executable \
  cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/review/pack/responses.yml
tamesu review status executable
tamesu status executable
```

Acceptance requires both the mechanical gate and human review. The signals are preserved
separately rather than averaged. A second reviewer adds a record; changing an image invalidates
reviews attached to its old digest.

## 3. Generate and read the report

```sh
tamesu report executable
tamesu compare executable
tamesu leaderboard executable
tamesu present c-rendering-engine --open
```

Read the generated `evaluation-report.md`. Check its state and outcome sentence before interpreting
the table. This example has only one item and repetition per model, so it demonstrates the evidence
workflow; it does not establish a reliable model ranking.

## 4. Analyze and close

Create an analysis template without making another model call:

```sh
tamesu analyze executable --scaffold
```

Edit
`cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/analysis.md` and
replace its TODOs with conclusions supported by the shown evidence. Alternatively, a paid model
call can draft it. Tamesu refuses costs above `--max-cost`
(default $0.25) and will not overwrite a file unless `--force` is given:

```sh
tamesu analyze executable \
  --provider meta --model muse-spark-1.2 --force
```

Close only after required reviews and analysis are complete:

```sh
tamesu close executable
tamesu status executable
```

Closing binds the analysis to the final evidence digest. The report and presentation then show the
eval as **Closed**.
