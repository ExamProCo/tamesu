While working with through our cases. I find that I want to check present straight away, I dont want to have to wait until we have to run. 


What is present responsible for because it really is just to generate whatever the current state of the case is to be human readable.

If there is any LLM calls that add additional information like anyalsis than that really is a seperate thing.

---

# Plan: what `present` is, what it shows when, and where analysis belongs

## Decision

`present` is a **deterministic, offline, read-only projection of the files on disk into
human-readable HTML**. It answers one question: *what is the current state of this case?*
It must answer it at **every** point in the lifecycle, from a case with only a `case.yml`
to a closed eval, without waiting for a run.

Anything that needs a model to say more than the files already say (interpretation,
summaries, recommendations) is a **different command with a different output file**.
`present` only renders that output, labelled with where it came from.

## What `present` is responsible for

A contract the code and tests enforce, not a convention:

| # | Property | Meaning |
|---|---|---|
| P1 | Pure projection | Output is a function of the case files (manifests, datasets, prompts, rubrics, run evidence). Same input, same bytes. |
| P2 | Offline | No provider call, no LLM call, no network, no Docker, no import of project Python (`task.py` is never loaded). |
| P3 | Read-only | Writes only under its output directory. Never creates or edits `analysis.md`, reports, evidence or logs. |
| P4 | Total | Renders at every lifecycle state, including broken ones. A problem in one eval is shown on the page, not a reason to render nothing. |
| P5 | Fast | A re-render is cheap enough to run after every edit. It takes 0.26 s today for a three-run case. |
| P6 | Honest about text provenance | Every block of prose is labelled *authored*, *computed*, or *assisted*; see "Separating analysis". |
| P7 | Safe | Everything untrusted (model output, logs, source, prompts) is escaped, size-capped and never executed. |

**Not** its job: running anything, scoring, judging, deciding acceptance, calling a model,
fixing evidence, or interpreting results beyond what a fixed template can state.

## What is true today (measured 2026-10-07)

I ran `present` on scratch copies of the C case at different states.

| State | Result |
|---|---|
| Executable eval is `draft`, no runs | Renders in 0.26 s. The page is mostly empty: a 14-column results table saying "No compatible completed evidence", "No run outputs are included". It does not show what *will* happen. |
| Case with only `case.yml`, no experiments | Renders. |
| An eval with a validation error (missing `metrics`, missing `cost_limit`) | **`present` aborts with the lint error and writes no page at all.** This is the moment the author most wants to see the page. |
| Three banked runs, reviews pending | Page says **"Status: Complete"**. |
| Same | "Observed outcome" says Muse 1.1 and 1.3 "accepted the same share: 0 of 1", omits 1.2, and never mentions that 1.1 scored 1.0 and 1.3 scored 0.2. |
| Same | The 1.3 compile error, the compiler log, the run log and the generated source are all on disk but not on the page. Only "No image (missing_review_artifact)" appears. |
| Same | "Evidence at a glance" shows literal `_..._` and stray `- Cost` paragraphs (the markdown renderer does not handle nested lists or emphasis). |

Root causes found in `src/tamesu/presenting.py`:

- **Status** (line 379) is derived only from run counts: `complete` when nothing is owed and
  banked equals planned. It ignores `eval.status`, pending reviews and stale evidence.
  Nothing else in the codebase defines "complete" the same way.
- **Observed outcome** for image-style evals uses acceptance counts and names only the
  first and last rows (`image_outcome`), so with all items pending it reports a meaningless
  tie.
- **Presentation is keyed on `supports_review`**, a flag that now also covers
  `artifact_bundle`. There is no per-task way to say "show these artifacts".
- **`analysis.md` is written by `run`, `judge`, `review import` and `close`** (the
  "Evidence at a glance" section is generated and persisted into the author's file).
  `present` itself is clean, but the file the author edits mixes computed facts with prose.

## Lifecycle states `present` must render

One function, `lifecycle(eval)`, derives the state from facts. `present`, `status`, `report`
and `close` all use it, so the word "complete" means one thing.

| State | Facts | Badge |
|---|---|---|
| Invalid | eval fails validation | **Invalid** (with the error list) |
| Draft | valid, `status: draft` | **Draft** |
| Ready | valid, `active`, nothing banked | **Ready to run** |
| Running or partial | some banked, owed, partial or failed | **Partially run** |
| Awaiting review | all banked, required reviews or judgments owed | **Awaiting review** |
| Reviewed | all banked, no evidence owed, not closed | **Evidence complete** |
| Closed | `eval.status: complete` after `close` | **Closed** |

Stale or excluded evidence is shown as a modifier on any state, not a state of its own.
Case and experiment pages summarize their evals (for example "1 invalid, 1 awaiting review").

## What each state shows

The pre-run page is the main gap. Before anything has run it should show the **design**,
the things `plan` prints plus the things nobody prints today:

1. **Question and method** (already shown) and the lifecycle badge with the reason, for
   example "Draft: set `status: active` and run `tamesu activate`".
2. **Run matrix.** A table of model x arm x repetitions with provider, the parameters that
   differ, and the number of items. This replaces the empty results table.
3. **The dataset.** Every item with its input, expected value and metadata, collapsed
   past the first few.
4. **What each arm will actually send.** The rendered system and user prompt for the first
   item of each arm (the exact text a model will see), with a "show all items" disclosure.
   This is the cheapest way to catch a template bug before spending anything.
5. **How it will be judged.** Mechanical scorers (with `pass_at`), the human-review rubric
   dimensions and reason codes, the acceptance policy, and any judges.
6. **How it will execute.** For an Inspect eval: backend, task file and function, declared
   sources, limits (`cost_limit`, `time_limit`), pinned image digests, artifact roles and
   which role is reviewed, and the sandbox hardening visible in the compose file.
7. **Cost and blockers.** Estimated and maximum exposure, budget ceiling, and the same
   blocker list `plan` prints (missing credential *names*, never values; Docker not
   found; untrusted origin). Computed without importing task code or calling anything.
8. **Lint findings.** For an invalid eval, a banner with each error and the file it
   points to. The rest of the page still renders as far as the data allows.

After runs exist the page adds, in this order: the headline outcome, the comparison, the
trust block (sample size, repetitions, failures, excluded runs), item evidence, and the
package panel. The earlier page-structure work in
[PRESENT.md](PRESENT.md) still applies; this plan changes what is shown *when*, not that
layout.

## Honest language rules

These are template rules, so they cannot drift:

- **No leader while evidence is pending.** If any required review or judgment is owed, the
  outcome sentence says so first ("2 of 3 runs await blinded review; mechanical results
  only") and does not name a winner or rank by acceptance.
- **Name every row or none.** Never summarize three rows by naming two. With three or more
  rows, state the range ("scores from 0.2 to 1.0") and let the table carry the rest.
- **Separate columns, never blended.** Mechanical, model-judge and human results appear
  side by side, as they already do in `evaluation-report.md`.
- **A ceiling on claims from one sample.** With one item per run, the template says
  "single item, single repetition: not a comparison", whatever the numbers are.
- **State labels come from `lifecycle()`**, not from run counts.

## Presenting evidence by task, not by flag

Replace the `supports_review` branching in the presenter with a small per-task interface,
the same idea as execution backends:

```text
task.present_item(result, item_dir) -> list[Block]
Block = Text | Table | Image | CodeExcerpt | FileLink | Scores | Badge
```

The presenter owns escaping, size caps and layout; tasks only say *what* to show.

- `structured_text`: expected vs output (existing field diff).
- `image_generation`: image, prompt used, mechanical checks, review verdicts.
- `artifact_bundle`: scores with their explanations; `stop_reason` and any hit limit; the
  render image; **compiler log and run log as capped, escaped excerpts (first and last N
  lines)**; the generated source as links to copied text files with sizes. For a failed
  item, the first error line is the card's headline ("did not compile:
  `storage size of 'blo' isn't constant`"). Execution provenance (backend, Inspect and
  adapter versions, model URI, log digest) goes in the package panel.

Safety: logs and source are model-controlled, so they are escaped, capped (default 200
lines and 64 KiB per excerpt), copied as `text/plain` assets only for declared artifact
roles, and never rendered as HTML or executed. Packaging impact is a decision (see open
questions): `pack` already includes `presentation/`, and compiler output can contain local
paths.

## Separating analysis from presentation

Three kinds of text can appear on a page. Each must say which it is.

| Kind | Produced by | Persisted where | Label on the page |
|---|---|---|---|
| **Authored** | A human | `analysis.md`, READMEs, manifests | "Authored" |
| **Computed** | Fixed templates over evidence, at render time | **Nowhere**: regenerated every render | "Computed from evidence" |
| **Assisted** | A model or agent, through a separate command | `analysis.md` with provenance in front matter | "Model-assisted" with model, date, and whether it matches the current evidence |

Changes:

1. **Stop persisting computed facts into `analysis.md`.** Today the "Evidence at a glance"
   block is generated by `run`/`judge`/`review`/`close` and written into the author's file.
   Compute it inside `present` instead. `analysis.md` then holds only authored or assisted
   prose, and the facts can never be stale. Migration: keep reading old files; when the
   generated section is present, ignore it in favour of the live one.
2. **Stop `run`, `judge` and `review import` creating `analysis.md`.** A scaffold full of
   `TODO`s appears as a side effect today. Provide `tamesu analyze <eval> --scaffold`
   to create it deliberately.
3. **Add a separate `tamesu analyze <eval>`** (the `tamesu-eval-analysis` skill already
   describes the discipline: cite evidence, no causation claims, no scoring). It may call
   a model; it writes `analysis.md` with front matter
   `generated_by: model`, `model`, `prompt_sha256`, `evidence_digest`, `created_at`. It
   never changes evidence and `present` never invokes it.
4. **`present` renders whichever it finds**, with the label above, and shows "stale:
   written against different evidence" when the digest differs. This binding already
   exists (`evidence_digest`); the plan makes the label say who wrote the text.
5. **Model *judging* is not analysis.** `tamesu judge` produces evidence (a column in
   acceptance) and stays where it is. It is not part of `present` and not part of
   `analyze`.

Rule of thumb for any future feature: if it can change the page without a file changing, it
does not belong in `present`.

## Developer loop

The author wants to look at the case constantly, not after a run.

- `tamesu present <case> --open` renders and opens the result.
- `tamesu present <case> --watch` re-renders when a manifest, prompt, rubric or dataset
  file changes (polling, no dependency), printing the render time.
- Output defaults to `build/present/<case>/`, unchanged. Re-render is atomic (already true),
  so a browser refresh never sees a half-written page.

## Robustness

- **Isolate failures per eval.** Validation errors, a missing prompt file or an unreadable
  result become an *Invalid* card with a precise message. Other evals and the case page still
  render. `present --strict` exits nonzero if anything was invalid, for CI.
- **Never import project code or contact services.** Plan building for Inspect evals must
  not require `inspect-ai` to be importable (identity reads the installed version only
  from package metadata, which is already true).
- **Bounded work.** Cap items rendered expanded, log excerpt sizes and asset copies, so a
  500-item or 50 MB-log eval renders in seconds.
- **Missing optional files** (publication, analysis, review key) are normal, never errors.

## Phases

### Phase 0: contract and lifecycle

- Add `lifecycle(eval)` and use it in `present`, `status`, `report`, `close`.
- Write the P1-P7 contract into `docs/` and enforce it with tests (below).
- Fix the three wording defects: status from `lifecycle()`, pending-review outcome text,
  "name every row or none".

*Done when:* the C case with pending reviews reads "Awaiting review", the outcome sentence
names no winner, and a unit test covers every state in the lifecycle table.

### Phase 1: resilient present

- Per-eval error isolation and the *Invalid* card; `--strict`.
- Fix markdown nesting and emphasis in generated text, or stop generating markdown for
  computed facts (the next phase removes it).

*Done when:* the invalid-eval scratch case renders a page listing both errors and still
shows the other eval.

### Phase 2: pre-run design preview

- Run matrix, dataset items, rendered prompts per arm, scoring and review design,
  execution block, cost and blockers.
- `data.json` gains these as structured fields so the page stays a pure rendering of it.

*Done when:* for a draft eval with no runs, a reader can see exactly what each model will
be sent, how it will be scored, what it can cost and what stops it running, without
opening a manifest.

### Phase 3: task presenters and artifact evidence

- Introduce `Block` and `task.present_item`; move text and image presentation onto it with
  byte-identical output for existing examples (golden files).
- `artifact_bundle` presenter: scores, stop reason, limits, render, log excerpts, source
  links, failure headline, provenance panel.

*Done when:* the Muse 1.3 card shows its compiler error without opening a file, and the
existing examples' presented HTML is unchanged byte for byte.

### Phase 4: separate analysis

- Render computed facts live; stop writing them into `analysis.md`; stop auto-scaffolding.
- Add `tamesu analyze` (with `--scaffold`) and the provenance front matter.
- Provenance labels on the page; stale detection stays.

*Done when:* `run`, `judge` and `review import` leave `analysis.md` untouched, `present`
works with no `analysis.md`, and a model-written file is visibly labelled as such.

### Phase 5: developer loop and docs

- `--open`, `--watch`; update the tutorial and `docs/cli.md`; screenshots per state.

## Tests

- **Golden HTML per lifecycle state** for one text, one image and one artifact case.
- **State matrix:** a table-driven test from facts to badge for all seven states.
- **Determinism:** two renders are byte-identical.
- **Read-only:** hash the whole case tree before and after `present`; only `build/` may change.
- **Offline:** patch `socket` and `subprocess` to fail; `present` still renders. Assert
  the task file is never imported.
- **Hostile content:** model output, compiler logs, source and prompts containing
  `<script>`, `javascript:` links, ANSI escapes, NUL bytes, huge lines and path-like names are
  escaped and capped.
- **Wording:** pending evidence never produces a leader; three rows are never summarized by
  two; one sample carries the single-sample caveat.
- **Resilience:** an invalid eval and an unreadable result file each yield an *Invalid*
  card plus the rest of the page.
- **Analysis separation:** `run`, `judge` and `review import` do not create or modify
  `analysis.md`; `present` output does not change when `analysis.md` is removed except
  for the analysis block.

## Open questions

1. **Does `present` show model-assisted analysis at all?** My recommendation: yes,
   clearly labelled and stale-checked, because a reader should not have to open another
   file. The alternative is a link only.
- yes.
2. **Migrating existing `analysis.md` files** that contain a generated "Evidence at a
   glance" section: ignore it silently (my recommendation), or rewrite the files once?
- Ignore
3. **Source and logs on the page and in packages.** Showing generated source and compiler
   output is the most useful evidence for code evals, but `pack` bundles `presentation/` and
   logs can contain local paths. Options: show in local `present` always, strip log
   excerpts from packaged presentations unless the profile is `full`, or scrub paths.
- strip yes, but we ship logs
4. **What does "Closed" require?** Today `close` refuses while evidence is owed unless you
   pass `--allow-incomplete-review`. Should a closed eval with an allowed gap render as
   *Closed with gaps* so the badge cannot hide it?
   - yes
5. **Preview prompts: first item only, or all?** First per arm with a disclosure is cheap;
   all items could be large. Default proposed: first, with the rest collapsed.
- yes default first and collapse rest
6. **`--watch` scope.** Re-render the whole case on any change (simple, fast enough at
   0.26 s), or only the affected experiment?
- yes please
7. **Where do computed facts' wording rules live?** In code templates (simple) or a small
   declarative table so docs and tests share it?
- the latter

## Out of scope

- Any model call from `present`.
- Changing how evidence is stored, scored or reviewed.
- A server or live-updating dashboard; `--watch` rewrites static files only.
- Interactive review inside the page (review stays an exported pack).


---

# Execution status (2026-10-07)

All six phases are implemented and tested (36 + 15 new tests; 309 total). The page was
checked in a browser for three states of the C case: banked and awaiting review, a draft with
no runs, and an invalid eval.

## Decisions I made on the open questions

I used the recommendations in this plan. Reverse any you disagree with.

| # | Question | Decision |
|---|---|---|
| 1 | Show model-assisted analysis on the page? | **Yes**, labelled with model and date, with stale detection. |
| 2 | Migrate old `analysis.md` files? | **Ignore** the old generated section on read; files are not rewritten. |
| 3 | Source and logs on the page and in packages | Shown in local `present`. The public showcase **withholds** them. `pack` is unchanged (its payload already includes the artifacts under the chosen profile). |
| 4 | "Closed with gaps" | Shown as a **note under the Closed badge** ("closed with N item(s) still pending review"), not a separate state. |
| 5 | Prompt preview scope | **First item per arm**, with the item named. |
| 6 | `--watch` scope | Re-render the **whole case** on any change. |
| 7 | Where wording rules live | **In code**, with tests; `reporting.py` owns the shared sentences. |

## What was built, by phase

- **Phase 0:** `lifecycle.py` (seven states from facts) used by `present`, `status` and the
  report; honest-wording rules (no leader while evidence is owed, range for three or more rows,
  a single-sample caveat). Three existing tests encoded the old behaviour and were updated.
- **Phase 1:** per-eval isolation (an *Invalid* card; internal renderer errors are labelled as
  such, not blamed on the manifest), `--strict`, paths in messages made project-relative.
- **Phase 2:** the Design section (`present_design.py`): run matrix, dataset, exact prompts per
  arm or the template error, scoring and rubric, execution, cost, static blockers. It does not
  probe Docker.
- **Phase 3:** `present_blocks.py` (block types, sanitising, caps) and `presenters.py` (per-task
  item views). The artifact-bundle card shows the failure headline, run facts, capped logs,
  source links and render. Files are copied as `.txt` under `assets/` only.
- **Phase 4:** computed facts rendered live (`present_facts.py`); `run`, `judge` and `review
  import` no longer touch `analysis.md`; `close` stamps an existing file only; new
  `tamesu analyze` (`--scaffold` or `--provider/--model`) with provenance front matter and a
  cost limit.
- **Phase 5:** `--open`, `--watch`, `docs/present.md`, updates to `docs/cli.md` and the tutorial.

## A bug this work caused, and the guard against it

`artifact_bundle.py` and `image_generation.py` are part of a run's **identity hash**. Putting
presentation code in them (and, earlier in Phase 1, a `supported_backends` attribute in
`image_generation.py`) silently made banked runs stale: the live C case flipped to "Ready to
run, 3 stale". Fixes:

- presentation lives in `presenters.py`, not in any hashed file;
- `image_generation.py` was restored byte for byte from git, and capabilities
  (`supported_backends`, `supports_promotion`) are resolved by accessors with defaults;
- `tests/test_present_contract.py::IdentityIsolationTests` fails if a hashed task file
  mentions presentation code.

**Check your own evidence:** if you banked image runs before Phase 1, run `tamesu status` on them.
They should be banked again now. If a run is stale for another reason, that is genuine.

## Known gaps

- Run-evidence and results tables overflow on narrow screens (they scroll horizontally).
- `--watch` polls once a second; it is not event-driven.
- No golden-file HTML tests per state; the tests assert on content and structure instead.
- Packaged presentations (`pack`) still include logs and source for the `full` and
  `rescorable` profiles.
