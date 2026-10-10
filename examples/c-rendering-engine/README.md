# One-shot C Rendering Engine

## Question

What does each of Muse Spark 1.3, 1.2, and 1.1 produce when given the same one-shot
request to build a C software renderer, and which output is the strongest candidate for
executable follow-up evaluation?

## Design

The `muse-version-comparison/single-shot` eval contains one dataset item and one shared
prompt arm. It varies only the model version. Each run uses the same system prompt,
verbatim user prompt, reasoning effort, output-token ceiling, timeout, and structured
response envelope.

One repetition and zero retries are deliberate: each model gets exactly one provider
request, matching the single-shot condition. Tamesu's schema constraint wraps the answer
in a `response` field so the complete implementation can be stored as one artifact.

## Evaluation boundary

The installed Tamesu version can validate and store text but cannot materialize a project,
compile C, render an image, or conduct human review for a structured-text task. Its native
metrics therefore cover response generation, JSON/schema validity, latency, and cost—not
implementation quality.

After running, extract each `response` into an isolated directory and assess it with the
[manual review rubric](cases/c-rendering-engine/experiments/muse-version-comparison/rubrics/rendering-engine-quality.md).
Do not interpret schema validity as evidence that the renderer builds or satisfies the
prompt.

## Workflow

Run these read-only checks from the repository root:

```sh
tamesu lint cases/c-rendering-engine
tamesu plan single-shot
```

After reviewing the plan, activate it. Activation does not call the provider:

```sh
tamesu activate single-shot
```

The paid run makes three calls: one to each Muse Spark version.

```sh
tamesu run single-shot
```

Do not use `--limit-items 1` as a probe here: there is already only one item per run, so
that would duplicate the full paid call pattern without banking formal evidence.

> **Run the executable example:** start with [WALKTHROUGH.md](WALKTHROUGH.md). Deeper guides live in
> [docs/reference/c-rendering-engine](../../docs/reference/c-rendering-engine/).

## Executable evaluation (Inspect AI backend)

The `muse-version-comparison/executable` eval compiles and runs each model's response in
a network-less Docker sandbox through Inspect AI, then ingests the source, compiler log,
run log and rendered PNG as Tamesu evidence.

**Deterministic score** (five equal steps): exported, compiled, ran, rendered (decodable
image), plausible (not blank, flat or near-uniform). All five must pass for the mechanical
gate.

**Blinded human review** of the PNG alone, against
`rubrics/rendering-engine-visual.yml`. An item is accepted only when the mechanical gate
and the reviewer both pass it.

```sh
pip install 'tamesu[inspect]'     # needs Docker running
tamesu lint cases/c-rendering-engine
tamesu plan executable
tamesu activate executable
tamesu run executable
tamesu review export executable --out review/pack
# review in review/pack/review.html, then:
tamesu review import executable review/pack/responses.yml
tamesu report executable
```

Run these from this directory (it holds `cases/`). See
[Execution backends and Inspect AI](../../docs/execution-backends.md) and the technical
journal in the experiment README. Reasoning models can spend the whole `max_tokens` budget
thinking and return an empty answer, so the eval uses `effort: low`; check each item's
`stop_reason` before blaming the model.
