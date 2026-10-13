# Implement the authored happy path

Source of truth: `docs/happy-path-walkthrough2.md`. This plan compares that document with the current working tree, including the existing Inspect migration. It proposes implementation work; it does not change application behavior.

## Target contract

- Author a case, then an experiment, then evaluations. Business context and hypotheses remain authored documentation.
- Use `tamesu case new`, `tamesu exp new`, and `tamesu eval new` with slash-separated references.
- Store case and experiment documentation in YAML; execute evaluations through an adjacent Inspect `task.py`.
- Use inline or external datasets, arms for variations, `evaluation.scorers`, and Inspect metric references such as `correctness/accuracy`.
- Preview work through `plan`, execute through `run`, inspect progress through `status`, and optionally export `evaluation-report.md` through `report`.
- Serve all cases through `show`, reflect file edits, and allow documentation edits in the browser.
- Keep probe evidence visible but excluded from final comparisons.
- Support authored and model-assisted `analysis.md`. Leave rubric behavior unchanged.

## Resolve the document's inconsistencies

Recommended interpretations for implementation:

1. Keep `experiments/<experiment>/evals/<eval>/`. The document creates `evals/.keep` but later omits `evals/` in two file paths. Discovery, packaging, and existing evidence already depend on this directory.
2. Resolve `{{ root.datasets }}` to the current case's `datasets/` directory. Use `dataset: "{{ root.datasets }}/integer-addition-v1/dataset.yml"` to match the documented dataset directory. The shown flat `integer-addition-v1.yml` reference points to a different file.
3. Treat `case.name` as the human-readable name in the authored example; derive the stable case ID from its directory. Support existing `name: <slug>` plus `title` documents during migration. The authored example must lint without requiring an added `title` or `technical_uncertainty`; still scaffold technical uncertainty because the prose explicitly asks authors to define it.
4. Use `evaluation.scorers` and `correctness/accuracy` in new templates. The earlier template's `mechanical` and `mean_score:correctness` are superseded by the later explicit change.
5. Canonical executable is `tamesu`; `tamseu` and `tasemuse` are prose typos. Fix the mismatched `mkdir`/`cd`, multiplication paths, and section numbering in a later documentation pass.
6. With 100 items, 30 repetitions, and four effort arms, the total is 12,000 executions: 3,000 per arm. Show this in plans and reports; do not silently enforce 3,000 total.
7. Success criteria are prose in this version. Display them with evidence and completion information, but do not claim arbitrary prose criteria were automatically verified. Keep existing explicit acceptance rules for mechanical acceptance.

These choices should be reflected in the walkthrough when implementing the affected phase, preserving the author's commands and intent.

## Phase 1 — Commands and scaffolding

Current gaps: `cli.py` exposes `new case|experiment|eval`; `scaffold.py` writes an experiment README, omits empty-directory placeholders, and enforces a slug as case name. There is no shell completion integration.

Work:

- Add the exact `case new`, `exp new`, and `eval new` command groups. Keep the current `new` command as a compatibility alias; make the authored forms primary in help and examples.
- Generate `case.yml`, `experiments/.keep`, and `datasets/.keep`; generate `experiment.yml` and `evals/.keep`; generate `eval.yml` and `task.py` for an eval.
- Generate experiment fields `name`, `description`, `hypothesis`, and `success_criteria`. Creating an eval with missing parents must create those parents consistently without overwriting authored files.
- Add experiment loading and validation, including incomplete but structurally valid scaffolds. Use directory IDs independently of display names.
- Align templates with the final scorer/metric syntax, zero retries, and clearly marked model/provider placeholders. Planning should explain unresolved placeholders; execution should refuse them.
- Decide naming validation explicitly: the document uses `multi_digit` as well as kebab-case. Prefer accepting both existing kebab-case names and underscore names rather than rewriting the author's identifiers.
- Add shell completion support and document its one-time activation for supported shells. Complete project case, experiment, eval, and arm references without provider calls.

Likely files: `cli.py`, `scaffold.py`, `config.py`, `discovery.py`, `models.py`, `schemas/`, and packaging metadata if completion needs a dependency.

Done when: a fresh directory can execute the three authored creation commands, producing the agreed tree; duplicate creation never overwrites files; the authored case and experiment examples validate; completion discovers newly created references.

## Phase 2 — Dataset references and config compatibility

Current gaps: external datasets resolve as literal relative paths; `root.datasets` is not expanded. Case validation requires a slug-matching `name`, `title`, and technical uncertainty. Experiment YAML is not part of the current eval context.

Work:

- Expand only supported path variables, beginning with `root.datasets`; report unknown variables clearly and retain existing containment checks.
- Support both inline datasets and the external directory layout. Do not execute arbitrary expressions or general templates to resolve paths.
- Update runtime validation and JSON schemas together for case, experiment, and evaluation contracts.
- Carry experiment documentation into presentation, reporting, and analysis. Documentation edits must not invalidate otherwise unchanged execution evidence.
- Preserve ordinary relative dataset references and existing case files. Test portable packaging and unpacking with the new references.

Done when: switching the addition eval from inline data to the documented external dataset works in lint, plan, execution, presentation, and a package round trip.

## Phase 3 — Inspect scorer and metric ownership

Current gaps: config accepts `evaluation.mechanical`; `artifact_bundle.py` calculates Tamesu score summaries; `reporting.py` averages run metrics across repetitions. The backend currently invokes Inspect with `epochs=1` for each Tamesu repetition. Renaming YAML alone will not satisfy Inspect-owned metrics and repetition handling.

Work:

- Accept `evaluation.scorers` and update all scorer consumers, schemas, identities, and generated templates. Keep legacy `mechanical` readable; reject conflicting simultaneous definitions with a clear diagnostic.
- Treat custom scorer names as declarations of task output, not as built-in scorer factories. Validate declared scorer results against Inspect logs after execution without importing user code during lint.
- Import Inspect scorer/metric results and resolve `scorer/metric` references, including custom metrics. Missing or ambiguous metric references must be visible errors rather than successful reports containing null.
- Design repetition handling before changing execution: use Inspect's repetition and reduction semantics while retaining individual item/repetition evidence, resumability, artifact staging, and completion tracking. Update sample identity/staging if multiple epochs reuse sample IDs.
- Make comparisons consume Inspect-produced statistics; keep Tamesu responsible for completion, costs, artifact acceptance, and presentation. Do not approximate custom metrics by averaging repetition-level values.
- Preserve existing saved reports as historical evidence. Version changed identity/adapter semantics so old and new runs cannot silently become one compatible comparison.

Done when: the authored `task.py` and `correctness/accuracy` eval run through Inspect; reported accuracy matches Inspect logs; a nontrivial custom metric and repeated samples retain Inspect's intended reduction semantics; partial execution can resume without duplicate or overwritten artifacts.

## Phase 4 — One selection plan for previews and execution

Current gaps: `run` supports only `--only`, `--limit-items`, and existing operational flags. Limited items already become probes, but other authored selectors and overrides are absent.

Work:

- Introduce a shared selection/options object used by `plan`, `run`, and `run --dry-run`.
- Add `--model`, repeatable `--arm`, `--limit-items`, repeatable `--item`, `--sample random --seed`, `--repetitions`, `--max-executions`, `--concurrency`, and `--budget-usd`. Retain `--only` as an alias.
- Define deterministic selection order: validate IDs, filter models/arms, select unique dataset items, apply repetition override, then enforce execution cap. Random selection uses a local seeded generator and records selected IDs.
- Define conflicting selectors and reject them clearly. Require a seed for reproducible random sampling, or generate and print/store one if omitted. A cap must never exceed its requested execution count; make any uneven arm/repetition coverage explicit.
- Mark restricted item coverage, reduced repetition coverage, and capped execution plans as probes. A model/arm filter can complete canonical specs for those selected variants; concurrency and budget changes alone do not make evidence a probe. Present that distinction in the preview.
- Persist selection, overrides, seed, and effective limits so resume reproduces the exact plan. Dry-run must make no provider call and create no run evidence.
- Treat `--budget-usd` as the ceiling for the invocation, not a fresh allowance for every arm or repetition. Audit existing preflight estimates and Inspect execution limits so sequential work respects remaining budget; expose unknown costs honestly.
- Show items × models × arms × repetitions, actual executions after capping, probe status, already completed work, and estimated cost in the preview.

Done when: both authored limited-run examples behave as written; dry-run and execution select identical work; seeded selections repeat; caps and ceilings hold across multiple specs; probes never enter normal comparison metrics.

## Phase 5 — Project browser and authored editing

Current gaps: `show` requires a named case when several cases exist. `live.py` serves live evidence and review actions, but not case/experiment/eval documentation editing.

Work:

- Make bare `tamesu show` serve a project index listing cases, with links through experiments to evaluations. Preserve `show <case>` as a shortcut.
- Render case business context, experiment hypothesis and success criteria, eval question, arms, results, and analysis even before execution. Reflect file changes automatically.
- Add editing for authored copy in case/experiment/eval YAML, preserving unrelated config and formatting where practical. Validate edits, save atomically, and detect changes made on disk since the editor loaded the file.
- Build on the current localhost server's request protections and explicit allowed fields/paths. Keep editing limited to authored content; preserve existing review workflows.
- Label probes in the live state and page; provide their answers and costs while excluding them from the final comparisons.
- Compute the displayed report from the same evidence model as `report`; viewing a case should not require generating a Markdown report first.

Done when: two newly scaffolded cases can be browsed with bare `show`; disk edits refresh; browser copy edits persist and re-render; invalid or conflicting saves return actionable feedback; displayed results agree with the Markdown report.

## Phase 6 — Report, analysis, skill, and walkthrough

Already present: `report` writes `evaluation-report.md`; `analyze --scaffold` and provider/model analysis exist with overwrite protection and the three requested analysis headings. Reuse these paths.

Work:

- Include case purpose, experiment hypothesis/criteria, scope, completion, Inspect metrics, acceptance outcomes, costs, and evidence references consistently in reports and analysis inputs.
- Keep incomplete, stale, and probe evidence distinguishable. Do not equate completed execution with correct answers or production readiness.
- Ensure scaffold generation makes no model call, preserves authored work, and analysis is displayed in the browser. Model assistance remains explicitly requested with provider/model flags.
- Update the existing authoring and analysis skills to teach the new commands, arms, dataset variable, Inspect scorers/metrics, and evidence limits. Avoid having the skill create unnecessary evals for arm variations.
- Align README, CLI reference, schemas, examples, and the walkthrough with the final contract. Fix tutorial inconsistencies listed above; leave rubric behavior intact.

Done when: a user can finish the walkthrough, optionally export a report, scaffold or generate analysis, edit it, and see the same evidence and conclusions in the browser.

## Validation and delivery order

Implement phases 1–2 first, phase 3 next, then phase 4, phase 5, and phase 6. Each phase should be independently reviewable with its relevant docs and schema changes.

Use targeted contract/integration tests for the authored commands and YAML rather than only unit tests that reproduce implementation details. Add one fresh-project walkthrough fixture with 100 unique integer calculations and four effort arms. Verify a planned 12,000 executions without actually issuing those paid calls. Execute a small deterministic local Inspect fixture with known answers to exercise scoring, artifacts, probe exclusion, resume, report, and analysis scaffolding. Test HTTP browsing/editing with multiple cases and concurrent file changes. Run the existing Inspect, identity, reporting/presentation, analysis, review, and portability coverage affected by each phase.

The current working tree already contains substantial in-progress changes. Implement against that state and preserve those changes; do not reset to the committed version or conflate existing modifications with this plan.

Final acceptance: the corrected walkthrough's commands and final YAML can be followed verbatim in a fresh project, with no hidden field additions, manual directory repair, alternate command spellings, or Tamesu substitutions for Inspect scoring statistics.
