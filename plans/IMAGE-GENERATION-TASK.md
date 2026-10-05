# Tamesu: Image-Generation Task Plan

## Purpose

Add a provider-independent `image_generation` task to Tamesu 0.1 so that an
image-producing eval can be planned, run, reviewed, and reported with the same evidence
guarantees as the existing `structured_text` task. The task contract must not mention a
specific vendor, endpoint, response shape, or billing scheme.

The driving use case is `product-scanning-alignment/product-reference-generation` in
`meta-cases`: generate one fictional product-reference image per product brief with a Meta
image model, then decide which outputs are fit to become inputs for later scanner
experiments. Meta Muse Image is the first real use case, not the abstraction boundary.
The first production implementation must also support OpenAI `gpt-image-2` through the
same task and artifact path. (`GPT-2` is a text model; the current image model ID meant in
this plan is `gpt-image-2`.) That experiment cannot create its `eval.yml` until this plan
reaches Phase 3.

This plan is the concrete delta against the current code. The design intent (scoring
separation, blinded human review, denominators) is already stated in
`EVALUATION-FRAMEWORK-IMPLEMENTATION.md`; this document does not restate it, it says what
must change in `src/tamesu` to honor it.

## The core problem

Image quality is not mechanically decidable. A generated image is "usable" only if someone
or something looks at it. So unlike `structured_text`, where `score_output` runs inline and
the item is complete when scoring is done, an image item has **two kinds of evidence that
arrive after generation and by different routes**:

| Evidence | Produced by | When | Cost |
|---|---|---|---|
| Mechanical | Deterministic code (decode, MIME, dimensions) | Inline at generation | Free |
| Model judge | A separate model call that is shown the image | After generation, re-runnable | Paid |
| Human review | A person, outside the process | After generation, asynchronous | Time |

Consequences that shape every decision below:

1. **Generation and review are separate stages with separate completion states.** A run
   banks when every planned item has a terminal generation outcome. A safety refusal or
   invalid returned image is a measured model outcome, not unfinished work; it stays in
   the denominator without blocking the bank. Infrastructure errors remain partial/failed.
   Banking does not wait on a human.
2. **Review evidence is attached to a stored image checksum**, not to a run in progress.
   Rejudging or re-reviewing never regenerates, and never overwrites a prior judgment.
3. **A judge failure must not make a good image look like a failed generation.** Judge
   errors are recorded against the judgment, not the item's generation state.
4. **Model and human verdicts stay visibly separate** and are never blended into one score.
   The eval declares which of them gates acceptance.

## Current state (verified in the tree)

| Seam | Location | What blocks images |
|---|---|---|
| Task allow-list | `config.py:17` `SUPPORTED_TASKS = {"structured_text"}` | `image_generation` fails lint |
| Output schema | `config.py:248` | Requires `output_schema` only for `structured_text`; fine, but no image branch exists |
| Provider contract | `providers/base.py` | Only `generate_text`; no image method |
| Meta provider | `providers/meta.py` | Chat completions only |
| OpenAI provider | `providers/openai.py` | Responses text generation only; no Images API path |
| Model registry | `providers/models.py:ModelSpec` | Describes text effort/token pricing but not model capabilities or image billing |
| Item processing | `runner.py:_process_item` | Hard-wired to `generate_text`, `output.txt`, `score_output` |
| Run completeness | `runner.py:_totals`, `scoring.py:build_report` | Metrics are exact-match / schema-valid only |
| Fingerprint inventory | `planner.py:114` | Hard-codes `tasks/structured_text.py` as the task code that affects identity |
| Models | `models.py:ProviderResponse` | Carries `text`, not bytes |
| Eval schema | `schemas/eval.schema.json` | `model_judges` and `human_review` are untyped (`array` / `object`) |

The good news is the surrounding machinery is already task-agnostic enough: run identity,
atomic writes, resumable per-item `result.yml`, the call log with redaction, budget
checks, and the prompt-metadata hashing all carry over.

## Design

### 1. Task dispatch instead of hard-coding

Introduce a small task interface in `tasks/base.py` and an explicit registry (no plugin
loading, per the framework plan):

```python
class Task(Protocol):
    name: str
    code_files: tuple[Path, ...]          # feeds the content fingerprint
    prompt_roles: tuple[str, ...]
    required_capabilities: frozenset[str]
    def validate_eval(self, evaluation, case, eval_dir, project_root) -> list[str]: ...
    def build_request(self, spec, item, prompts, context) -> TaskRequest: ...
    def call_provider(self, provider, spec, request) -> TaskProviderResponse: ...
    def materialize(self, response, item_dir, item) -> GenerationOutcome: ...
    def metrics(self, results) -> dict: ...
```

- `structured_text` is refactored behind this interface with **no behavior change**; its
  existing tests are the regression guard.
- `config.py` replaces the `SUPPORTED_TASKS` set and the inline `if task == "structured_text"`
  branch with `task.validate_eval(...)`.
- Prompt validation is task-owned. `structured_text` continues to require `system` and
  `user`; `image_generation` requires one `prompt` template. Do not invent an image-system
  prompt or concatenate text roles just to fit the old schema.
- `planner.py:114` takes `task.code_files` instead of the hard-coded path, so editing the
  image checks changes the content fingerprint of image runs and only image runs.
- `runner._process_item` keeps the retry loop, attempt logging, and result writing. The
  task builds the request, invokes its required provider capability, and materializes the
  normalized response. The runner logs the returned provider envelope before calling
  `materialize`, so local decode/write failures cannot erase paid-call evidence.

This refactor lands first, alone, so the image work is additive.

### 2. Provider contract: a capability, not a Muse endpoint

Add provider-neutral request and response values to `models.py`:

```python
@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    count: int = 1
    size: str | None = None
    output_format: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    provider_media_type: str | None = None
    revised_prompt: str | None = None

@dataclass(frozen=True)
class ImageGenerationResponse:
    images: tuple[GeneratedImage, ...]
    request_id: str | None
    usage: dict[str, Any]
    cost_usd: float | None
    response_metadata: dict[str, Any] = field(default_factory=dict)
```

Keep the base `Provider` protocol limited to identity and credentials, then define
capability protocols such as `TextGenerationProvider`, `ImageGenerationProvider`, and
`StructuredMultimodalProvider`. `ImageGenerationProvider` supplies
`generate_image(model, request, timeout_seconds)`. Do not add image methods to every
provider class or fill unsupported adapters with stubs. The task sets `count: 1` in v1,
but the response is plural because both real APIs are plural and the contract should not
discard provider output. The task fails a response containing anything other than one
final image before it banks the item.

Common task parameters are `count`, `size`, and `output_format`. All other options are
provider-namespaced in the eval, for example:

```yaml
parameters:
  image:
    size: 1024x1024
    output_format: png
  provider_options:
    reasoning_strength: low     # valid for Muse; rejected by OpenAI
```

Each adapter owns validation and translation of `provider_options`. Tamesu must not grow a
single union of every vendor's parameters. The resolved common parameters and provider
options are both fingerprinted and preserved in `run.yml`.

The byte content is authoritative. `provider_media_type` is only a claim: the task decodes
the bytes, detects their actual media type, and records both if they disagree. Revised
prompts and all non-secret response metadata are preserved because they describe what the
provider actually generated.

Image judging is a separate multimodal structured-output capability, not another image
generation method. In Phase 2 add content parts (`TextPart`, `ImagePart`) to a
`generate_structured` provider call and implement the judge on top of that. This keeps
vision judging reusable for Anthropic, OpenAI, Meta, and future providers instead of
creating a Meta-shaped `judge_image` API.

`ModelSpec` gains explicit capabilities (initially `text_output`, `image_output`,
`image_input`, and `structured_output`) and a pricing strategy. Lint
checks the selected model's capability at plan time. Checking whether a provider object
happens to have a method is insufficient because one provider can serve text-only and
image-capable models.

Provider coverage, in order:

1. **`fake`** (test-only, in `tests/`): deterministic PNGs and scriptable judge verdicts.
   Everything else is built against this first so no phase depends on a paid call.
2. **`meta`** image generation, against Meta's Model API (verified against
   [dev.meta.ai](https://dev.meta.ai/docs/image-generation) on 2026-10-05; re-check before
   Phase 3 because the surface is new and `muse-spark-1.3` already post-dates the registry):

   | Item | Value |
   |---|---|
   | Base URL | `https://api.meta.ai/v1` (already `META_BASE_URL` default in `meta.py`) |
   | Auth | `Authorization: Bearer $META_API_KEY` (same key as text; no new credential) |
   | Generate | `POST /v1/images/generations` (also `/v1/images/edits`, unused here) |
   | Model | `muse-image-1.0` |
   | Request | `model`, `prompt`, `n` (1-10), `size` (`"WxH"`), `response_format` (`b64_json` \| `url`), `output_format` (`webp` default, `png`, `jpeg`); Meta extensions include `reasoning_strength` and `tool_enablement` |
   | Response | `{created, data:[{b64_json \| url}], output_format, usage:{input_tokens, output_tokens}}` |
   | Price | **$0.01 per generated image**, flat; failed generations and safety-filtered images are not billed |
   | Rate limit | 150 requests/min for Muse Image, separate from text; limits are per team |
   | Judge model | `muse-spark-1.3` (or 1.2/1.1): accepts image input; $1.25 in / $4.25 out per M tokens |

   Adapter decisions that follow from this:

   - Use `response_format: b64_json` and `output_format: png`. Bytes come back inline, so
     there is no URL expiry or download failure mode. If `url` is ever used, the adapter
     must fetch and checksum it before returning.
   - Always send `n: 1`. One call per item keeps retry, cost, and the item-to-image mapping
     unambiguous; repetitions are separate calls, not `n > 1`.
   - `reasoning_strength` is a Meta `provider_option`; `size` and `output_format` use the
     common image request fields. All are preserved in the run manifest and fingerprint.
   - Muse pricing is flat per image. Register a flat-image pricing strategy and treat
     generation cost as `images_returned * price`, which makes the cost estimate exact.
     Maximum exposure is `items * (retries + 1) * price`. Add `muse-image-1.0` to the
     registry with a `PRICING_VERIFIED_AT` bump. Billing excludes failures, so
     failed attempts record `cost_usd: 0` only when the provider confirms no image was
     returned; otherwise record unknown, not zero.
   - Safety-filtered output is a terminal model outcome (the image is missing), counted in
     the denominator as a generation failure with a distinct outcome code so the report
     can show how many products tripped the filter. It does not make the run resumable or
     prevent banking; a transport failure still does.
   - `usage.input_tokens/output_tokens` are returned and are recorded as evidence even
     though they do not affect price.
   - Retry classification follows the existing text path: transient HTTP/network errors
     retry; HTTP 400 (bad request) does not.

   Muse Image exposes switches for web search, image search, and shell on both the
   Responses API and the single-shot Images endpoint (`tool_enablement` on Images). The
   first use case requires fictional packaging, so its eval must explicitly disable all
   three. Do not make those flags global task defaults: they are Meta provider options and
   another eval may intentionally test grounded generation.

3. **`openai`** image generation using `POST /v1/images/generations` and
   `gpt-image-2` (verified against the
   [OpenAI image-generation guide](https://developers.openai.com/api/docs/guides/image-generation)
   on 2026-10-05). The adapter sends the common prompt/count/size/output-format fields and
   maps OpenAI-only options such as `quality`, `background`, and `output_compression` from
   `provider_options`. It decodes `data[].b64_json` into the same `GeneratedImage` used by
   Meta. Store a provider-returned revised prompt when present.

   Register both the `gpt-image-2` alias and its available dated snapshot. Evals should
   prefer the snapshot when repeatability across time matters, while the adapter remains
   model-ID agnostic. Always record both the requested and provider-returned model IDs.

   OpenAI image cost is not represented by Meta's flat `usd_per_image`. It can vary by
   model, quality, size, and token usage. The pricing layer therefore needs a small
   strategy interface (token, flat-image, image-matrix/usage), not one optional field on
   `ModelSpec`. A plan reports an unknown estimate when it cannot price an exact parameter
   combination; it must never silently apply the Muse price shape to OpenAI.

4. **Multimodal judges**, beginning with Meta `muse-spark-1.3`, use the generic
   `generate_structured` content-part path. Meta chat completions represents a local image
   as an `image_url` part containing a base64 data URL. Add recorded-fixture tests for that
   serialization. The generation provider and judge provider remain independent choices.

   **Caveat to carry into the report:** with Meta generating and Meta judging, the judge is
   not an independent family, and may share blind spots or even a preference for Muse-style
   output. The framework plan prefers a different family. Keep Meta as chosen, but (a) the
   human review is the deciding evidence for promotion, which section 7 already enforces,
   and (b) the report's judge-vs-human agreement numbers are the check on whether the
   judge can be trusted at all. If agreement is poor, that is a result, not a bug.

The model registry rejects unsupported task/model combinations during lint/plan. Adapter
parameter validation also runs before credentials and before any paid call. Runtime
`ProviderError` remains the fallback for a provider capability that changes remotely.

The implementation is generic enough only when all of these are true:

1. The same dataset, prompt, task code, artifact writer, mechanical checks, and report
   code run against Fake, Meta, and OpenAI.
2. Adding an image model already served by an existing adapter is a registry change, not
   a task or runner change.
3. Adding a new image provider requires one adapter and contract tests; it does not change
   `tasks/image_generation.py`.
4. Provider request/response shapes and provider-only parameters never appear in the task,
   runner, result schema, or report schema.
5. An unpriced provider/model/parameter combination stays explicitly unknown in planning
   and evidence rather than being coerced to zero or to another provider's pricing model.

### 3. Item layout and artifacts

```text
runs/<run-id>/items/<item-id>/
  result.yml            # generation state + mechanical scores (existing shape, extended)
  output-0001.png       # final image; numeric name and extension follow decoded content
  provider_response.yml # revised prompt, request id, raw metadata (redacted)
  judgments/
    <judge-id>--<rubric-sha8>--<seq>.yml     # immutable, one per judge call
  reviews/
    <reviewer-id>--<timestamp>.yml           # immutable, one per human submission
```

`result.yml` gains `artifacts[]` entries with `role: task_output`, `sha256`, `media_type`,
and `width`/`height`. The image checksum is the join key for all later evidence: every
judgment and review records the `image_sha256` it saw, and any record whose checksum no
longer matches the file is reported as stale rather than silently trusted.
`provider_response.yml` contains normalized, redacted metadata only; it must not duplicate
base64 image payloads, signed URLs, authorization data, or the full request body.

Separate execution state from measured outcome. `state` remains `complete`, `partial`, or
`failed` for orchestration; a complete image item also records `outcome.status` as
`generated`, `safety_filtered`, or `invalid_artifact` plus a stable reason code. Only
`generated` has a task-output artifact. This lets a run bank after a provider refusal while
keeping that refusal in every end-to-end denominator.

Mandatory integrity checks are deliberately narrow: bytes exist, the image decodes with
Pillow, the detected type is an allowed raster format, dimensions are non-degenerate, and
decoded bytes/pixels stay within safety limits. Treat Pillow decompression-bomb warnings
as failures and restrict v1 to PNG/JPEG/WebP; SVG and provider URLs are not task artifacts.
Write bytes atomically before writing `result.yml`.

Content-sensitive checks are eval-declared mechanical scorers: minimum resolution,
requested-format match, alpha/background requirements, and non-uniformity. A solid-color
image can be the correct answer to a different eval, so `image_generation` must not reject
it globally. A mandatory integrity failure is a terminal `invalid_artifact` model outcome
and a **generation failure** in the denominator, not an infrastructure `state: failed`.
A declared mechanical scorer failure keeps a valid image as evidence but makes it fail
acceptance; it is excluded from judging only when that scorer is declared a prerequisite.

Log the provider attempt, request ID, usage, known cost, and sanitized metadata immediately
after the provider returns and before decoding or writing the image. If decoding or local
storage then fails, the paid attempt remains visible and is not retried as though no call
happened. A retry after a provider-returned image requires an explicit retryable provider
error, not a local artifact failure.

### 4. Rubric becomes machine-readable

Today the rubric is prose (`rubrics/product-reference-quality.md`). Both the judge and the
review UI need the same atomic dimensions, so add a rubric file format, e.g.
`rubrics/product-reference-quality.yml`:

```yaml
schema_version: 1
name: product-reference-quality
dimensions:
  - id: single-product-composition
    question: Exactly one complete product, isolated, fully in frame, front three-quarter view.
    reason_codes: [multiple-products, cropped, wrong-viewpoint, background-clutter]
  # ... one entry per required dimension in the existing markdown
acceptance:
  rule: all_dimensions_pass
```

The markdown stays as the human-readable rationale and is referenced from the YAML. The
YAML is what is fingerprinted, fed to the judge, and rendered in the review pack, so the
three consumers cannot drift. Add `schemas/rubric.schema.json`.

Keep each dimension a single yes/no claim with reason codes on failure. Do not add a
numeric 1-10 score for the judge; bounded scores from a model judge are the least
trustworthy output it can give and add nothing to the pass rule here.

### 5. Model judge: a separate, repeatable call

Declared in the eval, executed by a separate command, never inline with generation:

```yaml
evaluation:
  mechanical: [decodable_image, min_resolution]
  model_judges:
    - id: vision-judge-1
      provider: anthropic
      model: <judge-model>
      rubric: ../../rubrics/product-reference-quality.yml
      prompts:
        system: ../../prompts/judge-system.md
        user: ../../prompts/judge-user.md.j2
      repetitions: 1
```

Rules:

- **Blinding.** The judge receives the image, the product brief, and the rubric. It never
  receives the arm, generating model, run ID, repetition, or cost. The prompt renderer
  gets an allow-listed context so leaking a field is a template error, not a discipline
  problem.
- **Strict structured output.** Per dimension: `pass` boolean, one `reason_code` from the
  rubric's enum (required when `pass` is false), and a one-sentence explanation. Reuse
  `validate_json_schema`. The schema is generated from the rubric so dimensions cannot be
  forgotten.
- **Overall verdict is computed, not asked for.** `product-reference-pass` is derived from
  the dimension results by the rubric's acceptance rule. The judge is never asked "is this
  good overall."
- **Immutable and cheap to repeat.** Each call writes a new `judgments/*.yml` containing
  judge identity, rubric sha, image sha, prompt hashes, usage and cost. Judge calls go
  through the same logged, budgeted, retrying path as generation, with
  `stage: judge` in `provider_attempt` events.
- **Judge cost has its own budget line** so a rejudge cannot silently drain the generation
  budget (and vice versa).
- **Agreement, not authority.** When `repetitions > 1` or several judges are declared,
  report per-dimension agreement. The judge is a screening instrument; the human decision
  wins on disagreement (see section 7).

### 6. Human review: export, review elsewhere, import

Humans do not review inside the Tamesu process. The framework produces a review pack and
ingests the result, so review can happen in a spreadsheet, a static HTML page, or a hosted
tool without Tamesu depending on any of them.

```sh
tamesu review export <eval-id> [--run <run-id>] --out review-pack/
tamesu review import <eval-id> review-pack/responses.yml
tamesu review status <eval-id>
```

**Export** writes:

- `pack.yml`: manifest with a **presentation ID** per image (random, stable for that
  export), mapped privately to `{run_id, item_id, image_sha256}` in `key.yml` kept
  *outside* the pack so reviewers cannot see arm/model;
- the images, renamed to presentation IDs, order shuffled with a recorded seed;
- the rubric dimensions and reason codes;
- a blank `responses.yml` template, and optionally a self-contained `review.html` that
  renders the images with pass/fail controls per dimension and emits that file. (Optional,
  Phase 5; the YAML round-trip is the contract.)

For this eval the review is **absolute** (accept/reject against the rubric), not pairwise,
because there is one arm. The pack format should still reserve a `comparison` mode for the
later scanner ablation that the framework plan calls for, but do not build it now.

**Import** validates each response against the key and the rubric (unknown presentation
IDs, missing dimensions, or reason codes outside the enum are rejected, nothing partially
written), then writes one immutable `reviews/<reviewer>--<timestamp>.yml` per image. A
reviewer re-reviewing the same image creates a new record; the latest by timestamp is
"current" and earlier ones remain as evidence. Multiple reviewers are supported from day
one because adjudication needs them; the eval declares `required_reviews_per_item`.

Reviewer identity is a free-text ID supplied at export/import, not authentication. Say so
in the docs; this is a lab notebook, not an audit system.

### 7. Acceptance policy: declared before the run

The README's acceptance boundary ("passes the frozen rubric *and* human review", no silent
regeneration, selection rule declared in advance) becomes a validated eval field rather
than a convention:

```yaml
evaluation:
  acceptance:
    requires: [mechanical, human]     # or [mechanical, model_judge] or all three
    human:
      required_reviews_per_item: 1
      on_disagreement: adjudicate     # adjudicate | reject
    model_judge:
      role: screen                    # screen = informational; gate = must pass
    selection: none                   # none | first_pass | declared in eval before execution
```

- `selection` is deliberately restrictive. `none` means every repetition is reported and
  none is "the" image. Any selection rule beyond that must be written into the eval
  before execution; lint refuses an active eval that changes it after a run exists
  (the content fingerprint already enforces this once the field is included in the
  specification fingerprint).
- Lint **rejects an eval whose acceptance requires human review but declares no
  `human_review` block**, and vice versa. A model judge with `role: screen` can never be
  the sole gate for promotion to a downstream dataset. Make that a lint error; it is the
  safeguard behind "judges are not ground truth."
- Disagreement between judge and human is not an error. It is reported (section 9) and the
  acceptance policy says which side decides.

### 8. Eval lifecycle changes

Currently `run -> report -> close`. Image evals add a state between generation and close:

```text
plan -> run (generate + mechanical) -> [bank]
     -> judge (optional, repeatable)
     -> review export -> (human) -> review import
     -> report -> close
```

- A run **banks** when every planned item is terminal: usable image, safety refusal, or
  invalid artifact. Only owed attempts or framework/transport failures keep it partial or
  failed. Judging and review never un-bank it.
- `tamesu status` shows per-run owed work: items awaiting judgment, items awaiting review,
  stale evidence (image checksum mismatch).
- `tamesu close` **refuses** while required reviews are missing, unless an explicit
  `--allow-incomplete-review` is passed and recorded in the closing record (the framework
  plan already requires this).
- `tamesu rescore` for image evals recomputes acceptance and metrics from stored evidence
  without any provider call; `tamesu judge --run <id>` is the only paid post-generation
  command.

### 9. Metrics and report

Replace the exact-match/schema-valid metrics for this task with, all computed from stored
evidence and each labeled with its source:

- `generation_success_rate` (decoded image / planned items; failures stay in the denominator);
- `product_reference_pass_rate` (end-to-end: accepted / planned, generation failures
  counted as failures) and `..._given_image` (conditional);
- per-dimension failure rate and top reason codes, **separately for human and model judge**;
- review completion (`reviewed / required`);
- judge-vs-human agreement per dimension (raw agreement and Cohen's kappa; flag when n is
  too small to mean anything rather than reporting a misleading number);
- cost: generation, judging, total, and cost per accepted image;
- item-level table with links to the image and each judgment/review, so the report can be
  audited without opening YAML.

Report columns stay separate: mechanical, model judge, human. No blended score.
`report.schema.json` gets the new metric keys; `showcasing.py`/`presenting.py` need to be
checked for assumptions about text outputs (they render evidence for presentation) but
are out of scope to extend beyond not crashing on image items.

### 10. Promotion to the downstream dataset

The README's handoff: accepted outputs are copied into a new versioned `product-scanning`
dataset with run ID, checksum, model, prompt fingerprint, and review decision.

```sh
tamesu promote <eval-id> --to product-scanning-alignment/product-scanning-v2 --dry-run
```

- Reads accepted items per the eval's acceptance policy; refuses if the eval is not
  closed or any accepted item has stale evidence.
- Writes **a new dataset version directory, never edits an existing one** (versioned
  datasets are frozen by contract). Each product entry records `source: {eval, run_id,
  item_id}`, `image_sha256`, `generator: {provider, model}`, `prompt_fingerprint`,
  `rubric_sha`, and the human review decisions that accepted it.
- `--dry-run` prints exactly what would be copied and what was excluded and why. Products
  with no accepted image are listed explicitly, not dropped silently. They are a finding
  about the pipeline, and the downstream experiment must decide how to handle them.
- This can ship after Phase 5 without blocking the eval itself. Until then the copy is
  manual and the README's handoff list is the checklist.

## Schema and docs changes

- `schemas/eval.schema.json`: type `evaluation.model_judges[]`, `evaluation.human_review`,
  `evaluation.acceptance`; keep `additionalProperties` strictness as elsewhere.
- `schemas/case.schema.json`: allow `default_task: image_generation`.
- New: `rubric.schema.json`, `judgment.schema.json`, `review.schema.json`; extend
  `log-event.schema.json` with `stage: judge` and image artifact roles; extend
  `report.schema.json`.
- Add `schema_version` bumps only where a *breaking* change is made. These are additive;
  existing `structured_text` evals must lint, plan, and fingerprint identically.
- Docs: `docs/manifests.md` (new fields), `docs/file-structure.md` (item layout),
  `docs/cli.md` (judge, review, promote), `docs/providers.md` (image + judge capabilities),
  and a short `docs/image-evals.md` walking the generate, judge, review, close loop.
  Update the `tamesu-eval-authoring` skill with the image eval shape once stable.

## Phases

Each phase ends with a verifiable exit criterion and is independently mergeable.

### Phase 0: Task interface refactor
- Extract the `Task` interface; move `structured_text` behind it.
- Fingerprint inventory driven by `task.code_files`.
- **Exit:** full existing test suite passes unchanged; an existing example eval produces a
  byte-identical specification fingerprint before and after.

### Phase 1: Image generation vertical slice (fake provider)
- `image_generation` task, `ImageGenerationRequest`/`ImageGenerationResponse`,
  `generate_image` on the image-generation capability protocol,
  `fake` image provider, image artifact storage, mechanical checks, new `result.yml` shape.
- Lint/plan/run/resume for an image eval; plan-time capability errors.
- **Exit:** a tiny two-item image eval runs end to end on the fake provider; resume skips
  completed items; a corrupt-image item is terminal `invalid_artifact`, is reported as a
  failure in the denominator, and does not leave the run partial.

### Phase 2: Rubric and model judge
- Rubric YAML + schema; generated judge output schema; generic multimodal
  `generate_structured` on the fake provider and Meta `muse-spark-1.3`; `tamesu judge`;
  immutable judgments; blinded prompt context; separate judge budget.
- **Exit:** judging twice yields two records and no overwrites; the judge prompt provably
  contains no arm/model/run fields; a judge outage leaves generation state untouched.

### Phase 3: Real image providers and the first eval
- Implement Meta `generate_image` (`muse-image-1.0`, base64 output, `n: 1`), flat-image
  pricing, tool-disable options, and a safety-filter outcome class.
- Implement OpenAI `generate_image` (`gpt-image-2`, base64 output, `n: 1`),
  quality/size-aware cost estimation, and the same safety-filter outcome class.
- Add recorded response fixtures and adapter contract tests for both providers. The same
  fake-provider eval must plan for either real model by changing only the `runs` entry and
  provider-specific options.
- Author `product-reference-generation/evals/meta-baseline/eval.yml` in `meta-cases`.
- **Exit:** `tamesu plan` reports item count and cost exposure for both real models; one
  `--limit-items 2` probe per provider produces inspectable images with the same artifact
  and result schema and a complete log. (Probes never bank, per the framework plan.)

### Phase 4: Human review loop
- `review export/import/status`, key file, shuffled blinded pack, immutable reviews,
  multi-reviewer support, stale-evidence detection.
- **Exit:** export, hand-edit a response file, import; bad responses are rejected
  atomically; a reviewer cannot learn arm/model from the pack contents.

### Phase 5: Acceptance, report, close
- `evaluation.acceptance` validation (including the "judge alone cannot gate" lint),
  image metrics and agreement stats, report/leaderboard changes, `close` review gate.
- Optional self-contained `review.html`.
- **Exit:** `close` refuses with reviews missing and succeeds with them; report shows
  mechanical, judge, and human columns separately with correct end-to-end and conditional
  rates.

### Phase 6: Promotion
- `tamesu promote` with `--dry-run`, new dataset version, provenance fields.
- **Exit:** promoted dataset validates against `dataset.schema.json`, every image's
  checksum matches its source run, and nothing in the prior dataset version changed.

## Testing

All of this is testable without a paid call:

1. Task registry: structured_text parity (fingerprints, outputs) before/after Phase 0.
2. Image integrity and scorers: valid PNG/JPEG/WebP, truncated file, wrong MIME,
   zero-size, decompression bomb, byte/pixel limits, plus opt-in uniformity and minimum
   resolution failures.
3. Checksum join: modify `output-0001.png` after judging; judgment and review report stale.
4. Judge blinding: render with a context containing arm/model; assert they are absent from
   the final prompt and that referencing them is a template error.
5. Judge schema derivation from rubric: adding a dimension changes the schema and the
   rubric fingerprint, invalidating prior judgments' currency (not their existence).
6. Immutability: judging/reviewing repeatedly never rewrites an existing record.
7. Review import: unknown presentation IDs, missing dimensions, bad reason codes rejected
   with zero partial writes; duplicate submission by one reviewer creates a new record.
8. Acceptance matrix: each `requires` combination x judge/human agree/disagree x
   `on_disagreement`.
9. Denominators: generation failure counts in end-to-end rate and not in conditional rate.
10. Lint: human-required acceptance with no `human_review`; judge-only gating; acceptance
    edited after a run exists.
11. Resume mid-judging and mid-review-import.
12. Provider contract: the same request produces the same normalized result shape from
    recorded Meta and OpenAI fixtures; provider-only parameters are rejected by the wrong
    adapter before a paid call.
13. Pricing: flat-image, token, and quality/size estimates; unsupported combinations are
    reported as unknown rather than zero.
14. Attempt accounting: an image returned before a decode/write failure retains request,
    usage, and cost evidence and does not trigger a paid retry.
15. End-to-end: two-item eval on the fake provider through generate, judge, review, close,
    promote(dry-run).

## Risks and mitigations

- **Judge treated as truth.** Mitigated structurally: separate stage, separate column,
  lint refuses judge-only promotion, agreement is reported.
- **Review bias via unblinded packs.** Presentation IDs plus a key held outside the pack;
  tested.
- **Silent cherry-picking** (the README's main worry). `selection` is declared and
  fingerprinted; failures and rejected images stay in the report; promote lists excluded
  products.
- **Stale evidence after an image is touched.** Checksum on every record; staleness is a
  reported state, not an assumption.
- **Rubric drift** between judge, review UI, and prose. One YAML is the source; markdown
  is rationale only.
- **Image storage bloat.** Add generated images to the Git LFS/DVC guidance already in
  the framework plan before the first real run; do not commit them by accident.
- **Provider revised prompts.** Some image APIs rewrite prompts. Store the revised prompt
  in `provider_response.yml` and surface it in the report; the prompt fingerprint alone
  would otherwise overstate what was tested.
- **Provider abstraction leaks.** Contract tests run the same logical request through
  fake, Meta, and OpenAI fixtures. Vendor options remain under `provider_options`; task
  code may not import a concrete provider module.
- **Incorrect cost assumptions.** Pricing is a strategy selected by `ModelSpec`; unknown
  usage or an unpriced size/quality combination remains unknown and blocks an automatic
  budget claim.
- **Over-building the UI.** The YAML round-trip is the contract; the HTML page is optional
  and last.

## Decisions

Settled:

1. **Meta image API and pricing.** Resolved above: `muse-image-1.0`,
   `POST https://api.meta.ai/v1/images/generations`, $0.01 per generated image,
   150 req/min. A 50-product baseline at one repetition is about $0.50 for generation.
2. **OpenAI image support.** Use `gpt-image-2` through the Images API. It is a second
   production adapter and genericity test, not a second task. New GPT Image model IDs can
   be added through `ModelSpec` when their option set fits the same adapter.
3. **Judge model.** Meta Muse Spark (`muse-spark-1.3`, same API and key). Independence
   caveat recorded in section 2 of the provider coverage.
4. **Human review surface.** YAML round-trip first; the HTML review page stays optional
   and last (Phase 5).
5. **Reviewers.** One reviewer to start: `required_reviews_per_item: 1`, so adjudication
   is not needed for the first run. The pack format and import already support several
   reviewers, so adding one later requires no format change.

Still to be declared in the eval before execution (per the README):

6. **Repetitions per product.** Default: one repetition, `selection: none`, so the first
   run measures the pipeline rather than hunting for a good-looking image. Image cost is
   low enough that more repetitions are cheap; the real cost is human review time, so size
   repetitions to what you are willing to review.
7. **Image size/aspect and provider options.** Use one common size for every product;
   select comparable quality/reasoning settings separately for each provider and record
   them. They need values chosen from what the later scanner experiment wants as a
   reference image.
8. **Model-judge role.** `screen` (informational) vs `gate`. Recommendation: `screen`,
   with human review required for acceptance.

## Out of scope

- Pairwise/comparative review (reserve the pack field; build for the scanner ablation).
- Reference-image conditioning and scanner-aware scoring (belongs to the scanner
  experiment; the task interface should not preclude `arm.references`, which already
  exists in the arm schema).
- Automated similarity or object-detection checks beyond decode/size/uniformity.
- Authenticated reviewer identity.
- Plugin-loaded tasks or providers.
