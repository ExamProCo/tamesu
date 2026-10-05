# Product reference images example

A small Tamesu project for trying the [image eval workflow](../../docs/image-evals.md):
generate, judge, review, close, and promote. It uses three invented products, so nothing
here involves a real brand.

## What this example is testing

A later experiment needs clean reference images of products that do not exist. This eval
asks two things:

1. What share of generated images are usable references: exactly one complete product, the
   product we asked for, and no real branding?
2. Does a model judge agree with a person about which ones are usable?

Two image models receive the same prompt, size, and format: Meta's `muse-image-1.0` and
OpenAI's `gpt-image-2`. Each makes one image per product (6 images). An image is accepted
only if it passes the mechanical checks **and** your review. The model judge is a screen:
it is reported beside your verdicts and never decides.

## Setup

```sh
cd examples/product-reference-images
cp .env.example .env     # then fill in both keys
```

```dotenv
META_API_KEY=your-key
OPENAI_API_KEY=your-key
```

Meta also provides the judge (`muse-spark-1.3`). Because the judge shares a model family with
one of the generators, treat it as a screening instrument and check the report's
judge-vs-human agreement before trusting it. With three products that agreement is
reported as "too few paired items"; it only means something at about ten or more.

## Walkthrough

```sh
tamesu lint cases/product-reference
tamesu plan product-reference/baseline/two-model-baseline
```

`plan` shows 6 generation calls. Muse is flat-priced ($0.01 per image), so its exposure is
exact. `gpt-image-2` is billed by tokens, so its estimate is shown as *unknown* and its
cost is recorded exactly after each call. The `budget_usd` ceiling cannot be enforced for
it ahead of time; expect cents.

```sh
tamesu activate product-reference/baseline/two-model-baseline
tamesu run product-reference/baseline/two-model-baseline --limit-items 1   # probe: 1 image per model
```

Open the images under
`cases/product-reference/experiments/baseline/evals/two-model-baseline/runs/*/items/*/`.
Probes are diagnostic and never bank. When they look sane:

```sh
tamesu run product-reference/baseline/two-model-baseline
tamesu judge product-reference/baseline/two-model-baseline
```

`run` banks a run even if an image was refused or unreadable: those outcomes stay in the
denominator as measured results. `judge` writes an immutable record per image under
`judgments/`; run it again and you get a second record, never an overwrite.

Now review the images yourself, blinded to model and judge:

```sh
tamesu review export product-reference/baseline/two-model-baseline --out review-pack --reviewer you
open review-pack/review.html        # or edit review-pack/responses.yml by hand
tamesu review import product-reference/baseline/two-model-baseline review-pack/responses.yml
tamesu review status product-reference/baseline/two-model-baseline
```

The page's **Download responses.yml** button saves the file; put it in `review-pack/` and
import it. Leave an image untouched to review it later. Then:

```sh
tamesu status  product-reference/baseline/two-model-baseline   # owed work and stale evidence
tamesu report  product-reference/baseline/two-model-baseline
tamesu close   product-reference/baseline/two-model-baseline
tamesu promote product-reference/baseline/two-model-baseline \
  --to product-reference/accepted-references-v1 --dry-run
```

`close` refuses while reviews are missing (`--allow-incomplete-review` waives it and is
recorded in `closing.yml`). Open `evaluation-report.md`: mechanical, model-judge, and human
results are separate columns, with per-dimension failures and an item table that links to
each image, judgment, and review. Drop `--dry-run` to create the new dataset version.

## Things to try

- Change a rubric sentence, run `tamesu status`: earlier judgments and reviews become
  *stale* instead of silently counting.
- Edit an image file, then `tamesu rescore <run-id>`: the checksum mismatch is reported.
- Run `tamesu judge` twice and compare the two records.
- Fail an image the judge passed (or the reverse) and compare the two columns in the report.

## Cost

About $0.03 for Muse images, a few cents for `gpt-image-2` at `quality: low`, and a small
judge cost bounded by `judge_budget_usd: 0.50`.

Generated runs, logs, review packs, closing records, and promoted datasets are ignored in
this example so trying the tutorial adds nothing disposable to Git. Generated images can be
large; in a real project track them with Git LFS or DVC.
