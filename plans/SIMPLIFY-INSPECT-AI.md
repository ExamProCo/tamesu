# Simplify Tamesu around Inspect AI

Based on Andrew's decisions and the local `/home/andrew/Sites/tamesu` checkout on `inspect-ai`.

Source assessment: [tamesu-inspect-ai-review](https://github.com/danphillips-cloud/tamesu-inspect-ai-review). Its [test report](https://github.com/danphillips-cloud/tamesu-inspect-ai-review/blob/main/README.md), [feature requests](https://github.com/danphillips-cloud/tamesu-inspect-ai-review/blob/main/feature-request.md), and evidence bundle explain the problems motivating this plan. Andrew's subsequent decisions are recorded alongside this plan in `SIMPLIFY-INSPECT-AI-DECISIONS.md` when copied into Tamesu, and in `decisions.md` in the review checkout.

## Intended result

Inspect AI owns execution and automated scoring. Tamesu documents experiments, collects their results into portable packages, presents evidence, and supports optional human review. A package can be opened and reviewed on another machine without running its experiment code.

The existing Inspect integration is the starting point. Its earlier plan to keep the native runner is superseded by `decisions.md`.

## 1. Establish the baseline

- Run the current tests and record existing failures before edits.
- Trace runner selection, lifecycle gates, package creation/opening, and review import.
- Preserve the existing local edit to the support-ticket example's `eval.yml`.
- Define migration behavior for existing manifests and results: old evidence stays readable; new execution uses Inspect only.

## 2. Define the small public workflow and portable package

- Target create → run → package → open → review, with human review optional.
- Build on `src/tamesu/packaging.py` and `docs/packages.md` rather than creating a second archive format.
- Specify the minimum package contents: experiment description, inputs and task sources needed to reproduce it, recorded configuration, results, artifacts, review rubric and responses, and a readable presentation.
- State clearly what has been omitted from a package and whether it can be rerun. Inspect logs are currently excluded; explicitly decide how retained results support review and reproducibility without them.
- Preserve digest verification, safe extraction, and the separation between opening a package and executing its task code.
- Remove publishing-service requirements from the local packaging workflow. Keep unpacking/opening capabilities required for portability; defer hosted sites and publishing workflows.

## 3. Make Inspect the only executor

- Make Inspect a core dependency and remove backend choice from new-user configuration.
- Adapt the existing Inspect integration in `src/tamesu/backends/inspect_backend.py` and `src/tamesu/runner.py`.
- Move structured-text evaluation to Inspect tasks and scorers, preserving meaningful correctness checks rather than relying on valid JSON alone.
- Investigate Inspect's supported image-generation and judging paths before migrating the image example. Identify any required adapter explicitly; do not keep a second native runner as a fallback.
- Delegate retries, concurrency, model invocation, and execution limits to Inspect wherever its public APIs support the needed behavior.
- Retain only the result/artifact conversion necessary for packages and review. Remove the native backend and provider execution code after migrated examples work.

## 4. Generalize optional review

- Replace the current single-image review assumption with review subjects that can include text, code, images, and multiple artifacts.
- Keep rubric-based, optionally blinded human review and portable response export/import.
- Link responses to the reviewed artifact digests and rubric version so changed evidence invalidates old responses.
- Keep automated scores, pending human reviews, and final acceptance distinct. Human review is required only when an experiment explicitly requests it.
- Reuse Inspect's automated judging facilities where supported; Tamesu owns the review presentation and recorded human decisions.
- Start with working text/code and image review examples rather than building a plugin framework for hypothetical formats.

## 5. Remove gates and simplify authoring

- Remove required draft → active → completed transitions from CLI, validation, run, and review flows.
- Preserve factual run statuses and review completion indicators.
- Design a three-file starter around experiment documentation, an Inspect task, and minimal Tamesu configuration; avoid duplicating Inspect configuration. Validate the exact layout with a working example before fixing the scaffold contract.
- Add datasets, prompts, rubrics, or assets as separate files only when needed.
- Simplify the results page: plain-language findings first, technical tables secondary, explain failures, and omit irrelevant publishing panels.
- Rewrite the README and tutorial around the portable workflow; move detailed contracts into reference docs.

## 6. Verify the complete flow

- Use deterministic fixtures or a supported local mock model for routine tests; live model calls are supplemental.
- Exercise a text experiment and an artifact-producing experiment through run, package, extraction into a fresh directory, viewing, optional human review, and response import.
- Check failures, missing scores/artifacts, interrupted runs, stale reviews, archive tampering, and unsafe paths.
- Confirm opening and reviewing a package neither needs model credentials nor imports its executable task code.
- Run the relevant tests after each migration slice and the full suite before completion.

## Implementation order

Deliver the package contract and one complete Inspect-only text workflow first. Then generalize review and migrate artifact/image workflows. Remove obsolete runner code and lifecycle commands once their replacements pass; finish with scaffold and docs cleanup.

This document is a plan, not a record of completed changes.
