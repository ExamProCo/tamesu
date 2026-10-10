# Make Tamesu easy to use

## The problem

What Tamesu was meant to give is a predictable way to document experiments with a consistent
structure. What it asks for today is far more than that:

- about 25 CLI commands, and a lifecycle (draft, activate, plan, run, review, close) that you
  must step through before the tool is useful;
- 16 authored files for one experiment, most of them required before anything runs;
- about 6,400 lines of documentation, and a walkthrough that needs a fresh copy, YAML edits and
  long paths before you see anything.

Most of that is machinery for guarantees (identity hashing, stale detection, blinding, tamper
checks, packaging, publishing) that matter late, if at all. It sits in front of the one thing a
new user wants: write down an experiment, run it, and look at the page.

## The goal

A new user can document and run an experiment in 15 minutes, reading one page of docs, using
three commands, and spending most of that time looking at the rendered page rather than the CLI.

## The plan

### 1. Three commands carry the main path

| Command | Does |
|---|---|
| `tamesu new <level> <name>` | Scaffold a case, experiment or eval in the tree, with commented example files. |
| `tamesu run [<eval>]` | Validate, then run whatever is owed. No separate `lint`, `plan` or `activate` step. |
| `tamesu show` | Serve the page on localhost, re-render on change. Run it once and leave it open. |

Every other command stays, but moves out of the default `--help` into an "advanced" group:
review, report, compare, close, rescore, resume, analyze, pack, publish, site.

### 2. Keep the tree, write less inside it

The case → experiment → eval tree is the structure Tamesu exists to provide, and it stays
exactly as it is. What shrinks is how much you must write at each level before anything runs:

```
cases/<case>/
  case.yml                          # why this matters (a few lines)
  experiments/<experiment>/
    README.md                       # your journal: method, findings, limits, next steps
    evals/<eval>/
      eval.yml                      # models, prompt, scoring
```

That is the minimum: three authored files, all created by `tamesu new`. Everything else is
added only when you need it:

- `datasets/`: optional; a small dataset can be written inline in `eval.yml`.
- `prompts/`: optional; a short prompt can be inline in `eval.yml`.
- `rubrics/`: only when you want human review.
- `inspect/`, `schemas/`: only for executable or structured-output evals.

`tamesu new` works at every level: `tamesu new case <name>`, `tamesu new experiment <case>/<name>`,
`tamesu new eval <case>/<experiment>/<name>`, each writing commented example files.

### 3. Defaults instead of ceremony

- No draft state by default: a new experiment can run immediately. `status: draft` remains
  available for people who want the gate.
- Lint findings that are about reproducibility (a mutable image tag, an undeclared helper file)
  become warnings shown on the page, not errors that block a run.
- A sensible default cost ceiling, shown before the run, instead of a required field.
- Commands run from any folder inside the project, and eval references are short names
  (done: `tamesu run executable`).

### 4. The page is the interface

`tamesu show` becomes the place you look. It shows what will run and what it costs before you
run, a live state while a run is in progress, and the evidence afterwards. CLI output shrinks
to one line per run plus the URL.

### 5. Keep the guarantees, hide them

Identity hashing, stale detection, digests and lineage keep working, but they are never a step
the user performs. They surface only when something is wrong ("these 3 runs are stale because
the scorer changed; `tamesu run` will redo them").

### 6. One page of docs

- `README.md`: what Tamesu is, install, the three commands, one screenshot of the page.
- `docs/tutorial.md`: one experiment from `new` to page, under 150 lines.
- Everything else moves to `docs/reference/` and is not linked from the main path.
- Example folders keep a short README each; walkthrough, acceptance-test, reliability and
  internals notes move to `docs/reference/` or `plans/`.

### 7. Feature freeze

No new features until the 15-minute test passes. Work in this order, and stop to try it
after each step:

1. `tamesu show` (rename `present --serve`) and the docs trim (step 6). Smallest change, biggest relief.
2. `tamesu run` absorbs lint, plan and activate; draft is opt-in.
3. `tamesu new` scaffolding.
4. Inline dataset and prompt in `eval.yml`, so the minimum tree is three files.
5. Help grouping and warnings-not-errors.

## Success test

Hand the repo to someone who has not seen it. With only the README and the tutorial, they
create an experiment, run it against one model, and explain what the page tells them, in
15 minutes, without asking a question.

## Open decisions

- Which of the advanced features (blinded review, packaging, public showcase) are still wanted
  at all, versus deleted to cut code and docs?
