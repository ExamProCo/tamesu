# Presentation Redesign: Plan

Status: draft for discussion. Supersedes only the "Case study page" part of
[PRESENTATION.md](PRESENTATION.md); packaging and showcase are unchanged.

## Problem

`tamesu present` produces a page that is correct and escaped but not useful to a human
reviewer. The author's own reaction: "I'm glossing over it." Reviewing the generated
`build/present/support-ticket-triage/` for the bundled example shows why.

### Observed defects (bugs, not taste)

1. **Markdown is shown unrendered.** Experiment prose displays literal backticks and
   `- ` list items collapsed into one paragraph ("- `basic-prompt` asks ... - `decision-rules`
   adds ..."). Headings are demoted but the text is otherwise raw.
2. **Per-item evidence is broken.** Each item header reads `application-crash ·  /  · complete`
   (model and arm are empty) and every item ends with `Scores:` followed by `{}`. The
   data the page promises is not reaching the template.
3. **Items are not tied to arms.** Items are listed one after another with no way to see
   "basic-prompt got this wrong, decision-rules got it right".
4. **Results table is a wall of numbers.** 13 columns, unformatted (`1826.5000`,
   `0.0009`), with the primary metric not visually distinguished from diagnostics.
5. **Raw JSON for input, expected, and output** is dumped as one escaped line. Nothing
   highlights which field differed.
6. **Visual design.** Pages follow the OS dark-mode preference with a near-black
   background and small body text; on the case page and experiment page everything
   has equal weight. Tiny text plus no hierarchy is what invites skimming.
7. **Digest and provenance clutter the top of the case page** (a 64-character sha256 in
   the header) while the finding is far below.

### The deeper problem

The page is organized by what Tamesu stores (publication, experiment, eval, run,
item), not by what a reviewer needs to decide. A reviewer arrives with three questions:

1. **What was tested, and what happened?** (one sentence and one chart/table)
2. **Can I trust it?** (sample size, repetitions, failures, what was excluded)
3. **Where exactly did the arms differ?** (the items that explain the number)

Today the answer to 1 is a leaderboard line, 2 is a block of counters, and 3 is
buried in 16 undifferentiated JSON dumps.

## What a good presentation looks like

Principles, in priority order:

1. **Lead with the finding, in words.** The first screen states the question, the
   result, and the caveat, e.g. "Decision rules scored 75% vs 69% exact match (8
   items x 2 runs). The gain is one item in sixteen; too small to call."
2. **Show the comparison, not the table.** One primary-metric chart per eval (a bar or
   dot per arm with repetition values visible) before any table.
3. **Make disagreement the unit of review.** Show items where arms differ, or where any
   arm is wrong, first. Items every arm got right are collapsed.
4. **Show the evidence next to the claim.** Expected vs actual, field by field, with the
   mismatched field highlighted. No raw JSON blob unless expanded.
5. **Honesty about weakness is visible, not footnoted.** Sample size, repetition count,
   excluded partial runs, unpriced runs appear in a "How much to trust this" block
   adjacent to the finding.
6. **Readable by default.** Light theme default with dark as a deliberate option,
   >= 17px body, comfortable line length, real heading hierarchy, tabular numerals,
   rounded display numbers (full precision available on hover or in `data.json`).
7. **Provenance is one click away, not on screen one.** Digests, versions, and
   licenses move to a compact footer/"Package" panel.
8. **Still static, deterministic, escaped, no remote resources.** None of the above
   may weaken the existing safety and reproducibility guarantees.

## Proposed page structure

### Case page (`index.html`)

1. Title, one-line summary, status badge (complete / incomplete).
2. **Findings** block: one card per eval with the question, a plain-language result, an
   inline primary-metric chart, and a trust line ("2 reps x 8 items, 0 failures").
3. Problem and method (rendered Markdown, collapsed past the first paragraph).
4. Evidence coverage, compact: banked / owed / excluded, with tokens and cost.
5. Package panel: profile, license, publisher, version, digests, reproduce commands.

### Experiment page

1. Hypothesis and arms: a side-by-side comparison of what changed between arms (show
   the prompt diff, since the system prompt is the intended difference).
2. Results: primary metric chart, then a trimmed table (primary + 2-3 diagnostics);
   the full metric set under a disclosure.
3. **Where arms differ**: item cards ordered by disagreement.
4. All items: compact table with pass/fail per arm; each row expands to input,
   expected vs output.
5. Limitations (rendered), reproduction commands.

### Item card (the key unit)

```text
cancellation-request                     basic-prompt  ✗   decision-rules  ✗
"Please cancel my subscription before it renews next week..."

field            expected     basic-prompt    decision-rules
category         cancellation cancellation ✓  cancellation ✓
priority         normal       high         ✗  high         ✗
requires_human   true         false        ✗  true         ✓
```

Field-level comparison is possible only when expected/output are JSON objects; fall
back to side-by-side text otherwise.

## Implementation notes

- Fix defects 1-2 first; they are bugs independent of the redesign. Find why model, arm,
  and scores are empty in `src/tamesu/presenting.py` item rendering (likely a key
  mismatch with the stored item-result shape) and add a regression test.
- Markdown: render a safe subset (headings, paragraphs, lists, inline code, code
  blocks, links, emphasis) with our own escaping renderer, or a vetted library added as
  a dependency. Raw HTML in source Markdown stays escaped. Decision needed, see below.
- Chart: inline SVG generated server-side, deterministic, accessible (title/desc, text
  alternative table). No JavaScript required.
- Add structured fields to `data.json` (per-item per-arm verdicts, field diffs) so the
  page is a pure rendering of it.
- Tests: golden-file tests for the example, plus XSS tests for the new Markdown and
  diff paths.

## Open questions

1. **Markdown renderer:** hand-rolled safe subset vs adding a dependency (e.g.
   `markdown-it-py` with HTML disabled)? Prefer the dependency if packaging allows.
2. **Theme:** light default with `prefers-color-scheme` dark, or a single fixed theme
   for byte-identical output regardless of viewer? (CSS media queries do not affect
   output bytes, so light-default plus dark is fine.)
3. **Field-level diff:** generic for any JSON object, or driven by the eval's declared
   scorer fields? Generic is simpler; scorer-driven is more accurate.
4. **Plain-language finding:** purely templated from numbers (deterministic, can be
   blunt about "within noise"), or taken from a bound `analysis.md` when present? Proposal:
   templated headline always; bound analysis shown as commentary below it.
5. **Significance language:** with 2 repetitions and 8 items, what threshold lets the
   template say "too small to call"? Needs a conservative, documented rule rather than a
   statistical test we cannot justify.
6. **Item volume:** how do we present 500+ items? Proposal: disagreements first, a
   paginated or filterable table, never all expanded.
7. **Multi-eval cases:** keep scores from different evals separate (existing rule);
   do we link them with a summary strip only?

## Order of work

1. Fix the item-evidence bugs (model/arm/scores) with a failing test first.
2. Render Markdown safely.
3. Redesign tokens: light default, type scale, spacing, de-emphasized provenance.
4. Findings block and primary-metric SVG chart.
5. Disagreement-first item cards with field diffs.
6. Update `data.json` schema, `docs/tutorial-presentation-packaging-shipping.md`
   (screenshots/descriptions in step 4), and showcase templates that reuse the renderer.

## Done when

- A reviewer who has never seen the project can say, within a minute and without
  scrolling past one screen, what was tested, which arm won, and how much to trust it.
- Every item shown has model, arm, verdict, and a field-level expected-vs-actual view.
- No raw Markdown syntax is visible anywhere.
- Output remains byte-identical across renders, escapes all untrusted values, and
  loads no remote resources.
