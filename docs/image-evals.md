# Image evals

The `image_generation` task generates one image per dataset item, then lets you judge,
review, accept, and promote the results with the same evidence guarantees as
`structured_text`. The task contract names no vendor, endpoint, or billing scheme: the same
dataset, prompt, checks, artifact layout, and report run against any image-capable model.

A runnable, low-cost walkthrough lives in
[`examples/product-reference-images`](../examples/product-reference-images/README.md):
three invented products, two image models, a judge, and a review, for a few cents.

## Why images need more stages

Image quality is not mechanically decidable, so three kinds of evidence arrive by
different routes:

| Evidence | Produced by | When | Cost |
|---|---|---|---|
| Mechanical | Deterministic code (decode, format, size) | Inline at generation | Free |
| Model judge | A separate model shown the image | After generation, repeatable | Paid |
| Human review | A person, outside the process | After generation, asynchronous | Time |

Generation and review are separate stages. A run **banks** when every planned item has a
terminal generation outcome: a usable image, a safety refusal, or an invalid image. Those
last two are measured model outcomes that stay in every denominator; only transport and
framework failures keep a run partial. Banking never waits on a human, and judging or
reviewing never regenerates or overwrites anything.

## The loop

```text
plan → run (generate + mechanical) → [bank]
     → judge (optional, repeatable)
     → review export → (human) → review import
     → rescore / report → close → promote
```

1. **Author** the eval (`task: image_generation`), a rubric, judge prompts, and an
   `acceptance` policy. See the [manifest reference](manifests.md#evalyml).
2. **Plan.** `tamesu plan <eval>` shows item count and cost exposure. Flat-priced models
   give exact numbers; token-priced models report an unknown estimate rather than a guess.
3. **Probe.** `tamesu run <eval> --limit-items 2` makes a small paid probe per model.
   Probes are inspectable but never bank.
4. **Run.** `tamesu run <eval>`. Each item gets `output-0001.<ext>`, `result.yml`, and
   `provider_response.yml`.
5. **Judge (optional).** `tamesu judge <eval>` writes immutable judgments. The judge is
   blinded and answers one yes/no claim per rubric dimension; the verdict is computed.
6. **Review.** `tamesu review export <eval> --out pack/`, review the images (edit
   `responses.yml` by hand, or build a spreadsheet or page around it), then
   `tamesu review import <eval> pack/responses.yml`.
7. **Report.** `tamesu rescore <run-id>` and `tamesu report <eval>` rebuild everything from
   stored files with no provider call. Mechanical, model-judge, and human results appear in
   separate columns; there is no blended score.
8. **Close.** `tamesu close <eval>` refuses while required reviews are missing.
9. **Promote.** `tamesu promote <eval> --to <case>/<new-dataset> --dry-run` shows exactly
   what would be copied into a new dataset version.

## Decisions you make before running

Declare these in the eval before any run; the policy fields are part of the specification
fingerprint, so changing them later makes the earlier run stale.

- **What gates acceptance.** `requires: [mechanical, human]` is the usual choice. A model
  judge with `role: screen` is informational and can never gate. Use `role: gate` only when
  you are prepared to accept on judge evidence.
- **Repetitions and selection.** The default is one repetition with `selection: none`, so
  the first run measures the pipeline rather than hunting for a good-looking image. The real
  cost is review time, so size repetitions to what you will actually review.
- **One size and comparable options per provider**, recorded in `parameters`.
- **Reviews per item.** One reviewer needs no adjudication. With several, choose `reject`
  or `adjudicate` for a split verdict.

## Evidence rules

- **Checksums are the join key.** Judgments and reviews record the image checksum and the
  rubric fingerprint they saw. If the image or rubric changes, those records are reported
  as stale (they stay on disk, they stop counting), and the item is not silently accepted.
- **Judge failures are not generation failures.** A judge error becomes a judgment with
  `status: error`; `result.yml` is untouched, and a later rejudge sits beside it.
- **Judge independence.** A judge from the same model family as the generator may share its
  blind spots or even prefer its style. Treat the judge as a screening instrument: the
  report's judge-vs-human agreement is the check on whether it can be trusted at all, and
  poor agreement is a result, not a bug. Kappa is withheld below ten paired items rather
  than reported misleadingly.
- **Reviewer identity is a free-text ID**, not authentication. This is a lab notebook, not
  an audit system.
- **Blinding.** Review packs use random presentation IDs in shuffled order with the key kept
  outside the pack, and judge prompts can reference only the brief and rubric.
  Tamesu warns when an image carries embedded metadata that might name its generator, but
  it does not rewrite the image: you review the exact stored bytes.

## Metrics

Image evals add these metrics (select them in `metrics.primary` / `metrics.secondary`):

| Metric | Denominator |
|---|---|
| `generation_success_rate` | planned items |
| `safety_filter_rate`, `invalid_artifact_rate` | planned items |
| `generation_failure_rate` | planned items (infrastructure failures only) |
| `mechanical_pass_rate`, `..._given_image` | planned / generated |
| `product_reference_pass_rate` | planned items (end-to-end; failures count) |
| `product_reference_pass_rate_given_image` | generated images |
| `model_judge_pass_rate`, `human_pass_rate` (+ `_given_image`) | planned / judged or reviewed |
| `review_completion_rate` | generated images |
| `cost_per_accepted_usd` | accepted images (generation plus judging) |

`report.yml` also carries an `evidence` section with per-dimension failure rates and top
reason codes (separately for human and judge), agreement statistics, and a cost split.
