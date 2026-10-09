# C rendering engine: an end-to-end walkthrough

Purpose: step through one real case from the first file you read to a closed eval, so you can
judge the **experience** of Tamesu and know **which code to open** at each step. Every command
and output below was run on a clean copy on 2026-10-07 (macOS, Docker Desktop, Muse API).
Model output varies from run to run, so your numbers will differ; the shapes will not.

**Budget:** about 15 to 25 minutes and roughly **$0.10 to $0.15** of Muse calls. The ceiling the
tool will quote you ($0.75) is a safety limit, not the expected spend.

Contents: [0 Setup](#0-setup) · [1 Read the experiment](#1-read-the-experiment) ·
[2 Validate, then break it](#2-validate-then-break-it) · [3 Before anything runs](#3-before-anything-runs) ·
[4 First run, Ctrl-C, resume](#4-first-run-ctrl-c-resume) · [5 Remaining runs and the evidence](#5-the-remaining-runs-and-the-evidence) ·
[6 Review](#6-blinded-review) · [7 Reports, stale, tamper](#7-reports-stale-evidence-tampering) ·
[8 Analysis and close](#8-analysis-and-close) · [9 What the evidence will tell you](#9-what-the-evidence-will-tell-you) ·
[10 Code map](#10-code-map) · [11 Evaluation checklist](#11-evaluation-checklist) ·
[12 Rough edges and what is not verified](#12-rough-edges-and-what-is-not-verified)

---

## 0. Setup

You need: Python 3.12 with this repo installed, Docker running, and a `META_API_KEY`.

```sh
cd <repo>                                   # the tamesu repository
.venv/bin/pip install -e '.[inspect]'       # tamesu plus inspect-ai and openai
.venv/bin/tamesu --version
docker info >/dev/null && echo "docker ok"
```

Work in a **fresh copy** so you start from "nothing has run". The example directory in the repo
already holds the evidence from earlier runs.

```sh
WALK=/tmp/c-render-walk
rm -rf $WALK && mkdir -p $WALK
rsync -a --exclude build --exclude runs --exclude logs --exclude review \
  --exclude analysis.md --exclude evaluation-report.md --exclude __pycache__ --exclude staging \
  examples/c-rendering-engine/ $WALK/
cp .env $WALK/.env                          # a copy outside the repo has no workspace .env to inherit
cd $WALK
alias tamesu=<repo>/.venv/bin/tamesu        # or put .venv/bin on PATH
```

Ship state: both evals say `status: active`. Make the executable one a draft so you can feel
the draft-to-active step:

```sh
F=cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml
sed -i '' 's/^status: active/status: draft/' $F        # macOS; on Linux drop the ''
```

The first run pulls the sandbox image (about 1.5 GB):
`gcc@sha256:9188ac751ca24431dc43dbd142a223c98ea74f01d2858e84d30ba342a0d67844`. To avoid a surprise
pause during step 4, run `docker pull gcc@sha256:9188ac751ca24431dc43dbd142a223c98ea74f01d2858e84d30ba342a0d67844` now.

Everything below uses these two shorthands:

```sh
E=c-rendering-engine/muse-version-comparison/executable
D=cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable
```

---

## 1. Read the experiment

Read these in order. This is the "business use down to runs" structure the tool is built around.

| # | File (under `cases/c-rendering-engine/`) | Ask yourself |
|---|---|---|
| 1 | `case.yml` | Do business use, current problem and technical uncertainty make you want an experiment? |
| 2 | `experiments/muse-version-comparison/README.md` | Is the journal (uncertainty, method, findings, limits, next steps) something you would show a bootcamp or an SR&ED reviewer? |
| 3 | `evals/single-shot/eval.yml` | What could the text-only eval *not* tell you? (It only scores "valid JSON".) |
| 4 | `evals/executable/eval.yml` | Can you tell, from this one file, what will run, what it can cost, how it is scored, and who reviews it? |
| 5 | `datasets/rendering-engine-prompt-v1/dataset.yml` and `prompts/` | What exactly is a model sent? (`system-files.md` plus `user.md.j2`.) |
| 6 | `inspect/task.py`, `inspect/compose.yaml` | The only Python you write for an Inspect eval. Is the contract (dataset in, artifacts out, limits from the manifest) clear? |
| 7 | `rubrics/rendering-engine-visual.yml` | Are five yes/no dimensions with fixed failure codes reviewable by someone who never sees the code? |

Things to notice in `evals/executable/eval.yml`: the `execution:` block lists every file that
defines the run (`sources`), the finite `limits`, and which artifact role is reviewed; the
mechanical scorer has `pass_at: 1.0`; `acceptance` requires both a mechanical pass and a human.

---

## 2. Validate, then break it

```sh
tamesu list
tamesu lint cases/c-rendering-engine
```

Expected: both evals listed (`executable  draft`, `single-shot  active`) and `Validated 2 eval(s).`

Now break it three ways. Each should fail with a precise message. Restore after each.

```sh
# 1. a budget that is not finite
cp $D/eval.yml /tmp/e.bak && sed -i '' '/cost_limit/d' $D/eval.yml && tamesu lint cases/c-rendering-engine; cp /tmp/e.bak $D/eval.yml
#   - eval.execution.limits.cost_limit is required (budget must be finite)

# 2. an execution file the manifest does not declare (it would silently escape the run's identity)
X=cases/c-rendering-engine/experiments/muse-version-comparison
echo "X=1" > $X/inspect/helper.py && tamesu lint cases/c-rendering-engine; rm $X/inspect/helper.py
#   - eval.execution.sources is missing .../inspect/helper.py (an execution or build file next to the task must be declared for identity)

# 3. a mutable container tag (not reproducible)
cp $X/inspect/compose.yaml /tmp/c.bak && sed -i '' 's#image: gcc@sha256:[0-9a-f]*#image: gcc:14#' $X/inspect/compose.yaml
tamesu lint cases/c-rendering-engine; cp /tmp/c.bak $X/inspect/compose.yaml
#   - compose.yaml: image 'gcc:14' is a mutable tag; pin it as name@sha256:<digest> or set eval.execution.allow_mutable_images: true
```

Do not leave stray backup files inside `inspect/`: lint treats any file next to the task as
undeclared source (this is deliberate).

**Judge:** did the messages tell you what to fix without opening the code? Is it right that
`lint` never imports your Python?

---

## 3. Before anything runs

This is the part you asked for: look at the case *now*.

```sh
tamesu present c-rendering-engine --open
```

Open the **Muse Version Comparison** page. Expected for the executable eval:

- badge **Draft**, reason "Check it with `tamesu plan ...`, then `tamesu activate ...`. A draft cannot run.";
- an expanded **Design** section: run matrix (3 models, 0 banked / 1 owed each), the dataset item,
  **Prompts sent** (expand System and User: this is exactly what a model receives), the five
  rubric dimensions, acceptance policy, execution details (backend, Inspect version, sources,
  limits, the image digest marked *pinned*, model routes), and cost and blockers;
- "Observed outcome: Nothing has run yet, so this eval has no result";
- no leader anywhere.

Try the watch loop while you read:

```sh
tamesu present c-rendering-engine --watch      # leave running in a second terminal
```

Edit `prompts/system-files.md`, save, and refresh the browser. Expect a line like
`Rendered ... in 0.18s` per save. Ctrl-C to stop. (A bad edit prints `error: ...` and keeps watching.)

Then the CLI view of the same facts:

```sh
tamesu plan $E
tamesu run $E               # expect: error: Eval status must be active for execution; found 'draft'.
tamesu status $E | head -3  # State: Draft - Check it with `tamesu plan ...`
```

`plan` should end with `Execution blockers:` listing `eval status is 'draft'`. Note the cost
lines: **Estimated cost: $0.75** equals the maximum. For an Inspect eval the tool cannot predict
an agent loop, so it prices exposure at `cost_limit` ($0.25) x 3 runs. Real spend was $0.03 to $0.05 per run, so the quoted figure is roughly 5 to 8 times what you will pay.

```sh
tamesu activate $E          # Activated ...
tamesu plan $E | tail -3    # Execution blockers: none
```

---

## 4. First run, Ctrl-C, resume

Run one model, and interrupt it on purpose to see what the tool does to your evidence.

```sh
tamesu run $E --only muse-spark-1.2
```

Expected progress (to stderr; you are never waiting blind):

```
[run 1/1 muse-spark-1-2--file-blocks--rep1 (1 item(s), concurrency 1)] started
```

**Wait about 15 seconds, then press Ctrl-C.** Expected (observed):

```
  [1/1] c-rendering-engine-lighting-demo failed  elapsed 17s  cost $0.0000 failed=1
[run 1/1 ...] partial in 17s
1 run(s) remain incomplete.
Resume without repeating finished work: tamesu resume muse-spark-1-2--file-blocks--rep1--<timestamp>--b86bbdfa
```

```sh
tamesu status $E | head -8        # State: Partially run ... Partial: 1  Failed: 0
docker ps | grep inspect          # nothing: the sandbox container was removed
RID=$(ls $D/runs)                 # the partial run's id
cat $D/runs/$RID/inspect/lineage.yml
```

The lineage shows attempt 1 with `status: cancelled`. Now finish it:

```sh
tamesu resume $RID
cat $D/runs/$RID/inspect/lineage.yml     # attempt 1 cancelled, attempt 2 success, both digest-pinned
```

Expected: the item re-runs once (about 40 to 80 seconds, about $0.03), the run becomes `complete`,
and the call log `$D/logs/$RID.jsonl` reads `run_started, inspect_log (cancelled), item_finished
(failed), run_finished (partial), run_started, inspect_log (success), artifacts_written,
item_finished (complete), run_finished (complete)`.

**Judge:** was it clear what state your evidence was in after Ctrl-C? Did you ever feel you
might have lost work or paid twice? (A run that is cancelled before Inspect writes any log also
ends `partial`, not `failed`; that path is covered by a test.)

---

## 5. The remaining runs and the evidence

```sh
tamesu run $E
```

Only the two **owed** models run (1.2 is already banked). Muse 1.3 is slow and can take 2 to 4
minutes. Observed: 1.1 scored `1.0000`, 1.3 scored `0.2000`.

Now walk the evidence on disk:

```sh
find $D -path '*/inspect/staging' -prune -o -type f -print | sort | sed "s#$D/##" | head -50
```

Open, in this order:

1. `$D/runs/<run>/run.yml`: resolved config, fingerprints, and the `execution:` block (model
   route, Inspect version, **log path and SHA-256**).
2. `$D/runs/<run>/inspect/input.jsonl`: the **frozen dataset** Inspect was given. One sample per
   item, prompts already rendered.
3. `$D/runs/<run>/items/*/result.yml`: scores (`value`, `pass`, `answer`), `outcome`, `output`,
   `missing_artifacts`, token usage, `stop_reason`.
4. `$D/runs/<run>/items/*/artifacts/`: `source/*.c`, `compiler.txt`, `run.txt`, `render.png`. Open
   `render.png` for 1.1.
5. `$D/logs/<run>.jsonl`: the lifecycle index. Find the `inspect_log` event and its `sha256`.

For the full transcript (every message, the scorer's explanation), use Inspect's own viewer:

```sh
<repo>/.venv/bin/inspect view --log-dir $D/runs/<run>/inspect
```

(I did not launch the viewer myself while writing this guide; it is the one UI I could not check.)
These transcripts contain the full prompt and your git remote URL, which is why Tamesu keeps
`runs/*/inspect/` out of git and out of packages.

Now the page with evidence:

```sh
tamesu present c-rendering-engine --open
```

Expected on the executable eval: badge **Awaiting review**; outcome "Review is incomplete: 1 of 3
item(s) await required review or judgment ... this is not a comparison"; **no leader**; a computed
"Evidence at a glance" list; and under **Run evidence**, each run is a collapsed row. Expand them:

- Muse 1.3: red headline `compiles_and_renders scored 0.2 (passed: exported) - first error: ...`,
  the compiler-log excerpt, links to `source/*.c`, "No image (missing_review_artifact)";
- Muse 1.1: the rendered image, scores, run facts, the (empty) compiler log labelled as empty,
  the run log, and a provenance line (backend, model route, **log digest, "not published"**).

**Judge:** can you see *why* each run scored what it did without opening a file? Is anything on the
page you would not want to publish (the public showcase withholds logs and source; a local page does not)?

---

## 6. Blinded review

```sh
tamesu review export $E --out $D/review/pack
tamesu review status $E
```

Only runs that produced an image are offered; the others print `skipped ... no generated image`.
Open `$D/review/pack/review.html` in a browser. You see the image and the prompt, **never the
model, arm or run**; the mapping lives in `$D/review/keys/` (keep it away from reviewers).

Fill in your reviewer id, mark each of the five dimensions pass or fail (a fail needs a reason
code), click **Download responses.yml**, save it as `$D/review/pack/responses.yml`, then:

```sh
tamesu review import $E $D/review/pack/responses.yml
tamesu review status $E        # Reviewed: 1  Awaiting review: 0 (counts differ with your images)
tamesu status $E | head -2     # State: Evidence complete   (or still Awaiting review if images remain)
tamesu present c-rendering-engine
```

Reload the page: the item's acceptance is now Accepted or Rejected, and "Review is incomplete"
is replaced by the counts. Importing a second review by another reviewer adds a record (the latest
per reviewer counts); changing an image invalidates reviews for it.

**Judge:** was the review page enough to decide without the code? Was the rubric fair for a
single render? (In my test pass I failed `free-of-artifacts` with `speckle-or-noise`, and the item
was rejected with `human_failed`.)

---

## 7. Reports, stale evidence, tampering

```sh
tamesu report $E          # writes $D/evaluation-report.md and prints its path
tamesu compare $E         # model, arm, repetitions, items, primary_mean, cost
tamesu leaderboard $E
```

Read `evaluation-report.md`. Note the **State** line, the honest outcome sentence, and that
mechanical, model-judge and human columns are never blended.

**Stale evidence.** Change one factor and the tool refuses to reuse old runs:

```sh
sed -i '' 's/effort: low/effort: medium/' $D/eval.yml
tamesu status $E | head -6        # Banked: 0  Owed: 3  Stale: 3
sed -i '' 's/effort: medium/effort: low/' $D/eval.yml     # undo: they are banked again
```

**Tamper detection.** The Inspect log is digest-pinned:

```sh
LOG=$(ls $D/runs/*/inspect/attempt-01/*.eval | head -1)
cp $LOG /tmp/log.bak && printf x >> $LOG
tamesu rescore $(basename $(dirname $(dirname $(dirname $LOG))))   # error: Inspect log evidence is stale or corrupt: ... digest changed
cp /tmp/log.bak $LOG
```

`rescore` re-reads stored evidence only. It cannot recompute Inspect scores (by design: the score
came from code that ran in the sandbox), so a scorer change means re-running.

---

## 8. Analysis and close

```sh
tamesu close $E            # if any review is still owed it refuses and lists exactly what is missing
tamesu analyze $E --scaffold         # authored template; no model call
tamesu analyze $E                    # error: needs --provider and --model (it costs money)
```

Edit `$D/analysis.md` and answer the TODOs in your words, then reload `tamesu present`. The
label should read "Draft analysis: unfinished" until the TODOs are gone. Optional, **a paid call (my estimate
is a few cents; the command refuses anything over `--max-cost`, default $0.25)**: let a model write it
instead. It refuses to overwrite yours without `--force`:

```sh
tamesu analyze $E --provider meta --model muse-spark-1.2 --force
```

Present then labels it **Model-assisted analysis (muse-spark-1.2), written ...; matches the shown
evidence**, and flips to **STALE** if you change evidence afterwards.

Close once review is done:

```sh
tamesu close $E            # Closed ...   close binds an existing analysis.md to the final evidence
tamesu status $E | head -2 # State: Closed
```

Notice: the evaluation report and the page now say **Closed**, `run` and `resume` no longer apply,
and your analysis carries an `evidence_digest`.

---

## 9. What the evidence will tell you

Do not skim this part. It is the actual result of using the tool, and it changes how to read the
comparison.

**Variance is large.** Same prompt, same settings, different outcomes:

| Model | Earlier runs | This walkthrough |
|---|---|---|
| muse-spark-1.1 | 1.0 | 1.0 |
| muse-spark-1.2 | 1.0 (twice) | **0.0** (after resume) |
| muse-spark-1.3 | 0.2 (compile error) | 0.2 (a **different** compile error) |

That is the argument for repetitions, and the page says so ("not a comparison").

**Muse 1.2's 0.0 is a measurement bug, not a model failure.** Open its `response.txt`:

```sh
grep -n "FILE:" $D/runs/*b86bbdfa/items/*/artifacts/response.txt | head -3
```

The model wrote ` ```// FILE: render.c ` (fence and marker on one line). `inspect/task.py`'s
`FILE_BLOCK` regex requires a newline between them, so the scorer reported `no usable FILE blocks`
and a likely-fine program was scored zero. This is exactly what a human review of mechanical
evidence is for, and it is a real limit of "deterministic" scoring.

**Exercise: fix the scorer and watch identity work.** In `inspect/task.py` change

```python
FILE_BLOCK = re.compile(r"```[a-zA-Z]*\n\s*//\s*FILE:\s*([\w./-]+)\n(.*?)```", re.S)
# to
FILE_BLOCK = re.compile(r"```[a-zA-Z]*[ \t]*\n?\s*//\s*FILE:\s*([\w./-]+)\n(.*?)```", re.S)
```

(I checked it still matches the standard format and now matches both same-line forms.) Then:

```sh
tamesu lint cases/c-rendering-engine     # still ok
tamesu status $E | head -8               # Banked: 0  Stale: 3 ... the scorer is part of every run's identity
tamesu plan $E | grep owed               # Runs owed: 3
```

All earlier runs are now stale, correctly: they were scored by different code. Re-run to bank
new evidence (`tamesu run $E`). I did **not** change this in the repo's example, because it would
silently invalidate your existing banked runs and review pack.

**Muse 1.3's two failures** (`static double blo[BW*BH*3]` non-constant bounds; then a duplicate
parameter name `t`) are plausible C mistakes, not harness errors: the compiler log is on the page.

---

## 10. Code map

Line numbers are as of 2026-10-07; use the function name to find them again
(`grep -n "def name" file`). Paths are under `src/tamesu/`. Read in this order.

| Step in this guide | What happens | Read |
|---|---|---|
| 2 lint | Manifest validation, including the Inspect rules | `config.py::_validate_eval` (the `execution` block is validated by `backends/inspect_backend.py::validate_execution`, line 135) |
| 2 lint | Why an undeclared file or a mutable tag fails | `inspect_backend.py::validate_execution`, `_source_like_files`, `image_references` |
| 3 plan | The frozen run matrix and the **identity hash** | `planner.py::build_plan`, `make_run_spec` (line 82); `backends/inspect_backend.py::InspectBackend.identity` |
| 3 plan | Cost exposure for an agent loop | `pricing.py::estimate_plan_cost`; `inspect_backend.py::estimate_item_cost` |
| 3 present | The lifecycle states | `lifecycle.py::lifecycle_for` (line 82) |
| 3 present | The Design section's data | `present_design.py::build_design` (line 27) |
| 4 run | Dispatch, budget check, per-run loop, progress lines | `cli.py::command_run` (551) → `runner.py::run_eval` (28) → `execute_spec` (66) |
| 4 run | The backend seam | `backends/__init__.py::BackendRun` (18), `backends/native.py` |
| 4 run | **Everything Inspect-specific** | `backends/inspect_backend.py::execute` (396): freeze dataset (`frozen_samples`, 262), call Inspect (`_run_inspect`, 469), digest the log, then ingest |
| 4 Ctrl-C | Recovering a cancelled log; partial not failed | `_run_inspect` (the `if not logs:` branch); `runner.py::execute_spec` (`except KeyboardInterrupt`) |
| 4 resume | Re-running only unfinished items, linking attempts | `runner.py::resume_run` (324); `execute` (the `pending` loop and `lineage.yml`) |
| 5 evidence | Turning Inspect samples into Tamesu evidence | `inspect_backend.py::_ingest` (550), `_item_result` (588) |
| 5 evidence | Artifact safety (traversal, symlinks, size, roles) | `_ingest_artifacts` (731), `_artifact_rejection` (779) |
| 5 evidence | How your scorer hands files over | `inspect_support.py::Stager` (18); your `inspect/task.py` |
| 5 evidence | The shape review code reads | `tasks/artifact_bundle.py::adapt_ingested_result` (150) |
| 5 evidence | Scores to metrics and the report | `tasks/artifact_bundle.py::build_report` (195) |
| 5 present | Building the page's data | `presenting.py::build_case_data` (215), `_eval_data` (460) |
| 5 present | The artifact card (headline, logs, links) | `presenters.py::present_artifact_bundle` (77) |
| 5 present | Escaping and size caps | `present_blocks.py::cap_excerpt` (39), `sanitize_text`, `render_item_view` |
| 5 present | A broken eval becomes a card | `presenting.py::_invalid_eval` (408) |
| 6 review | Pack export/import, blinding | `review.py::export_pack`, `import_responses`; `evidence.py::select_generated_items` |
| 6 review | Acceptance (mechanical + human, never blended) | `acceptance.py::item_evidence`, `build_image_evidence` |
| 7 report | Honest outcome sentences | `reporting.py::image_outcome` (382), `_observed_result`, `single_sample`, `metric_range_sentence` |
| 7 tamper | Digest verification | `inspect_backend.py::verify_logs` (824); `artifact_bundle.py::rescore_run` |
| 8 analyze | Scaffold, stamp, provenance | `analysis.py::scaffold_analysis`, `stamp_existing`; `analyze.py::analyze` (84) |
| (privacy) | Why transcripts never ship | `packaging.py::_is_inspect_evidence` (230) |
| (watch) | Polling re-render | `present_watch.py::watch` (29) |

**The tests are the executable specification.** Read them next to the code:

| File | What it pins down |
|---|---|
| `tests/test_inspect_backend.py` | Lint rules, identity, ingestion, state mapping, resume, interrupt recovery, packaging, trust |
| `tests/test_inspect_review.py` | The review adapter over a bundle |
| `tests/test_present_contract.py` | Lifecycle table, determinism, read-only, offline, invalid cards, hostile logs, identity isolation |
| `tests/test_analyze.py` | The model-assisted command with a fake provider |

---

## 11. Evaluation checklist

Score each honestly; the notes are where Tamesu is weakest or strongest.

**Orientation**
- [ ] Could a new person read `case.yml` and the experiment README and say what is being tested and why?
- [ ] Did the lifecycle badge ever disagree with what you knew was true?

**Feedback while working**
- [ ] During `run`, did you always know how far along it was and what it had cost?
- [ ] After Ctrl-C, did you trust the state of your evidence? Did `resume` do what it said?
- [ ] Did `present` give you something useful *before* the first run?

**Evidence and trust**
- [ ] For each score, could you find the log, the code and the artifact that produced it?
- [ ] Did you believe the "not a comparison" caveats? Would you have over-read the table without them?
- [ ] Did the tamper and stale checks behave the way you want evidence to behave?

**Judgement**
- [ ] Is the blinded review page sufficient? What did you want that it did not show?
- [ ] Is "deterministic score then human review" the right split for code-generation, given the format-miss finding?

**Cost of the abstraction**
- [ ] How many files did one experiment need? Which could you not explain?
- [ ] Was anything about the Inspect seam (`task.py` contract, staging, `sources`) harder than writing your own sandbox code would have been?

---

## 12. Rough edges and what is not verified

**Known rough edges you will probably hit**

- "Estimated cost" for an Inspect eval equals its maximum (a `cost_limit` ceiling), roughly 5 to 8 times the real spend.
- One item and one repetition per run: no ranking is possible, and the tool says so.
- The scorer's FILE-block regex is brittle (section 9). It is user code, so Tamesu cannot fix it for you.
- A **Closed** eval whose manifest you later change still says Closed; the badge shows a "stale run(s) excluded" note instead of re-opening it.
- Wide tables scroll sideways on narrow screens.
- Model judges are not supported for this task type, and review works on exactly one image role.
- Both the review step and `analyze` are free-text trust exercises: reviewer identity is an ID you type, not authentication.

**Not verified by me**

- `inspect view` (I did not launch the viewer).
- `tamesu analyze --provider ... --model ...` against the real API. It is tested with a fake provider only, so treat the first real call as a test of it.
- `tamesu pack`, `publish` and `site build` for this case (it has no `publication.yml`), including how the public site withholds logs and source.
- Linux or Windows; everything above ran on macOS with Docker Desktop.
- More than one item, more than one repetition, concurrency above 1, tool-calling agents, or `cost_limit` and `token_limit` actually tripping.

**Cleanup**

```sh
rm -rf /tmp/c-render-walk
docker image ls gcc            # keep or remove the pulled sandbox image
```
