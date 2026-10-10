# C rendering engine: reliability exercises

These optional exercises deliberately create failures. They help evaluate Tamesu's recovery and
evidence guarantees, but they are not part of the first-run walkthrough.

Use a disposable copy such as `/tmp/c-render-walk`. For portable in-place edits on macOS and
Linux, define:

```sh
sedi() { if sed --version >/dev/null 2>&1; then sed -i "$@"; else sed -i '' "$@"; fi; }
```

## Validate malformed execution contracts

Each change should produce a focused lint error. Restore the file immediately afterward.

### Missing finite budget

```sh
cp cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml /tmp/e.bak
sedi '/cost_limit/d' cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml
tamesu lint cases/c-rendering-engine
cp /tmp/e.bak cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml
```

### Undeclared execution source

```sh
echo "X=1" > cases/c-rendering-engine/experiments/muse-version-comparison/inspect/helper.py
tamesu lint cases/c-rendering-engine
rm cases/c-rendering-engine/experiments/muse-version-comparison/inspect/helper.py
```

Lint treats every execution or build file beside the task as part of run identity. Do not leave
backup files inside `inspect/`.

### Mutable container image

```sh
cp cases/c-rendering-engine/experiments/muse-version-comparison/inspect/compose.yaml /tmp/c.bak
sedi 's#image: gcc@sha256:[0-9a-f]*#image: gcc:14#' \
  cases/c-rendering-engine/experiments/muse-version-comparison/inspect/compose.yaml
tamesu lint cases/c-rendering-engine
cp /tmp/c.bak cases/c-rendering-engine/experiments/muse-version-comparison/inspect/compose.yaml
```

## Interrupt and resume a run

Do this in a fresh copy with no banked runs:

```sh
tamesu run executable --only muse-spark-1.2
```

After progress starts, wait about 15 seconds and press Ctrl-C. Tamesu should mark the run partial
and print a `tamesu resume <run-id>` command. Check the state and confirm the sandbox was removed:

```sh
tamesu status executable
docker ps | grep inspect
```

Find the run ID, inspect its lineage, and resume it:

```sh
RID=$(ls cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/runs)
cat cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/runs/$RID/inspect/lineage.yml
tamesu resume $RID
cat cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/runs/$RID/inspect/lineage.yml
```

The lineage should retain the cancelled attempt and add the successful attempt. Only unfinished
work is repeated.

## Make existing evidence stale

Changing an input to run identity makes old runs ineligible without deleting them:

```sh
sedi 's/effort: low/effort: medium/' \
  cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml
tamesu status executable
sedi 's/effort: medium/effort: low/' \
  cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/eval.yml
```

With `medium`, status should report the previous runs as stale and the matrix as owed. Restoring
`low` makes compatible runs banked again.

## Detect a changed Inspect log

Choose an existing `.eval` log, back it up, append one byte, and ask Tamesu to rescore its run:

```sh
LOG=$(find cases/c-rendering-engine/experiments/muse-version-comparison/evals/executable/runs \
  -path '*/inspect/attempt-01/*.eval' | head -1)
cp "$LOG" /tmp/log.bak
printf x >> "$LOG"
RID=$(basename "$(dirname "$(dirname "$(dirname "$LOG")")")")
tamesu rescore "$RID"
cp /tmp/log.bak "$LOG"
```

Rescoring should refuse because the digest changed. Tamesu can re-read stored evidence, but it
cannot recompute a score produced by sandboxed scorer code; changing that code requires a new run.
