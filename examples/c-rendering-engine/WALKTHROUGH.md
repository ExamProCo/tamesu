# C rendering engine: run the example

A model writes a small C renderer, Inspect AI builds and runs it in Docker, and Tamesu keeps the
source, logs, score, and rendered image. You will spend most of this on the page, not the CLI.

Allow 10 to 15 minutes. The three model calls usually cost $0.10 to $0.15 in total.

## 1. Set up

You need Python 3.12 or newer, Docker, and a `META_API_KEY`. From the Tamesu repository root:

```sh
[ -d .venv ] || python -m venv .venv
.venv/bin/python -m pip install -e '.[inspect]'
[ -f .env ] || cp .env.example .env       # add META_API_KEY to .env
```

The example in the repository already holds evidence from earlier runs. Copy it, without that
evidence, so you start from "nothing has run":

```sh
rm -rf /tmp/c-render-walk && mkdir -p /tmp/c-render-walk
rsync -a --exclude build --exclude runs --exclude logs --exclude review \
  --exclude analysis.md --exclude evaluation-report.md --exclude __pycache__ --exclude staging \
  examples/c-rendering-engine/ /tmp/c-render-walk/
cp .env /tmp/c-render-walk/.env
```

## 2. Open the page

In a terminal you can leave running:

```sh
source .venv/bin/activate && cd /tmp/c-render-walk
tamesu show
```

Open <http://localhost:8000> and go to **Muse Version Comparison**. Before anything has run, the
executable eval already shows what will happen: the prompt each model is sent, the three models,
the scoring rule, the review rubric, the cost limit, and "Nothing has run yet."

The page re-renders whenever a file changes, so keep it open and refresh it after each step.

## 3. Run it

In a second terminal:

```sh
source .venv/bin/activate && cd /tmp/c-render-walk
tamesu run executable
```

Three runs, one per model, each 40 seconds to four minutes. The first run may download a roughly
1.5 GB GCC image.

## 4. Read the result

Refresh the page and expand **Run evidence** for each model. A successful run shows the rendered
image, the score, the source files, compiler and run output, and cost. A failed build shows its
first compiler error instead of an image.

A run marked complete means Tamesu has the evidence, not that the C program was good: read the
score and the image.

The page will say **Awaiting review**. The eval requires a person to judge each image before it is
accepted; [EVIDENCE.md](../../docs/reference/c-rendering-engine/EVIDENCE.md) shows how.

## Clean up

```sh
rm -rf /tmp/c-render-walk
```

## Going further

These are optional and live in [docs/reference/c-rendering-engine](../../docs/reference/c-rendering-engine/):

- [EVIDENCE.md](../../docs/reference/c-rendering-engine/EVIDENCE.md): the files behind the page, blinded review, report, and close.
- [RELIABILITY.md](../../docs/reference/c-rendering-engine/RELIABILITY.md): interrupt a run, stale evidence, tamper detection.
- [INTERNALS.md](../../docs/reference/c-rendering-engine/INTERNALS.md): the scorer, the backend code, and known rough edges.
- [ACCEPTANCE-TEST.md](../../docs/reference/c-rendering-engine/ACCEPTANCE-TEST.md): the full end-to-end test script.
