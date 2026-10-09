# Muse Version Comparison

## Experimental variable

Only the Muse Spark model version changes:

- `muse-spark-1.3`
- `muse-spark-1.2`
- `muse-spark-1.1`

All three runs use one prompt arm, one item, one repetition, zero retries, high reasoning
effort, and a 16,000-token output ceiling. The `single-shot` eval remains a draft until
its lint and plan output have been reviewed.

## Interpretation

Native Tamesu scoring answers whether each response was generated and conformed to the
storage schema. It does not answer which implementation is best. Use the frozen manual
rubric for side-by-side review after compiling and running each response in an isolated
environment.

The first comparison should be treated as exploratory evidence. A later executable eval
can freeze a build harness, platform constraints, renderer output format, test scenes, and
image-comparison procedure after the response shapes reveal what can be tested fairly.

## Executable evaluation: technical journal

Entry date: 2026-10-07. Eval: `executable`. Evidence: the three banked runs under
`evals/executable/runs/` (one item, one repetition each).

### Technical uncertainty the single-shot eval left open

`single-shot` stored each model's answer as text and scored only whether it was valid JSON.
It could not say whether any response builds, runs, or draws anything, which is the thing
the business question is about. The uncertainty was whether that can be measured
mechanically, with a fixed prompt and a fixed toolchain, without a person compiling each
response by hand.

### What was tried

Each response is split into files, compiled with `gcc -O2 -o render *.c -lm` in a
network-less, non-root Docker sandbox (toolchain image pinned by digest), run for up to two
minutes, and its `out.ppm` is converted to PNG. The score has five equal steps: exported,
compiled, ran, rendered, and *plausible* (the image is at least 64 px, has at least 32
colors, is at most 90% one color, and has non-trivial, non-extreme brightness). The
thresholds are deliberately loose: they separate "drew nothing useful" from "drew
something", not good from bad. Quality goes to a blinded human review of the PNG alone, so
the reviewer cannot be swayed by which version wrote the program.

### What happened

| Model | Score | Output tokens (reasoning) | Latency | Cost |
|---|---:|---:|---:|---:|
| muse-spark-1.1 | 1.0 (all five steps) | 7,468 (1,788) | 40 s | $0.032 |
| muse-spark-1.2 | 1.0 (all five steps) | 6,800 (1,970) | 39 s | $0.029 |
| muse-spark-1.3 | 0.2 (exported, did not compile) | 9,146 (2,615) | 217 s | $0.039 |

Both passing renders are 800x600 with tens of thousands of distinct colors and no dominant
color above 4.2%. Muse 1.3's single source file failed to compile:
`static double blo[BW*BH*3]` where `BW` and `BH` are not compile-time constants.

### Technical findings

1. **A mechanical build-and-render gate is feasible and cheap.** About $0.03 and under a
   minute per attempt for two of three models, with a compiler log and run log kept as
   evidence, so a zero is always explainable.
2. **Reasoning can consume the whole output budget.** In an earlier scratch run at
   `effort: medium` Muse 1.2 spent 12,025 of its 16,000 output tokens reasoning and returned
   an empty answer (`stop_reason: length`), which scored 0.0. That is a budget artifact, not
   evidence about ability, so this eval uses `effort: low` and records `stop_reason` per
   item. That scratch run is not part of this eval's evidence.
3. **The deterministic checks do not rank the passing models.** Muse 1.1 and 1.2 tie at 1.0,
   and that is expected: ranking them is what the human review is for.
4. **A compile failure is a one-sample observation.** It shows the failure mode exists and
   is detected correctly. It does not show how often Muse 1.3 fails to build.
5. **The harness itself had sharp edges** (documented in
   `docs/execution-backends.md`): a log can report success while every sample errored, Docker's
   file-copy cannot read tmpfs mounts, and an unhandled scorer exception aborts the whole
   task. Each now has a test.

### What this does and does not establish

Established: under this prompt, with `effort: low` and one attempt, Muse 1.1 and 1.2 each
produced a program that built and drew a non-trivial image, and Muse 1.3 produced one that
did not build.

Not established: any ranking of 1.1 against 1.2; any statement about 1.3's general build
rate; image quality or lighting quality (no review has been submitted); and anything about
other prompts or reasoning settings. Sample size is one item and one repetition per model.

### Conclusion (provisional) and next steps

The measurement approach works; the comparison does not yet answer the business question.

1. **Do the blinded review.** Open `evals/executable/review/pack/review.html`, review the
   two images, then run `tamesu review import`. The 1.3 run produced no image and is already
   recorded as a rejection, not a skip.
2. **Estimate build rate before ranking.** Change one factor, `repetitions`, to at least five
   per model, so 1.3's failure and the 1.1/1.2 tie can be told from luck.
3. **Then test repair.** Add one compile-repair turn (a multi-turn solver, which the Inspect
   backend allows) to separate one-shot ability from ability to fix a compile error.
4. **Strengthen the deterministic checks only if review shows they let bad images through**,
   for example by adding a second scene or a determinism check (render twice and compare).
