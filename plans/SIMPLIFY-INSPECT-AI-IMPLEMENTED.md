# Implemented simplification

Branch: `simplify-inspect-ai` in [ExamProCo/tamesu](https://github.com/ExamProCo/tamesu).
Scope follows [the plan](SIMPLIFY-INSPECT-AI.md) and [the decisions](SIMPLIFY-INSPECT-AI-DECISIONS.md).

- Inspect AI is required and is the only runner. Built-in text/image compatibility uses Inspect tasks; image endpoints use a ModelAPI adapter.
- Portable archives work without publication metadata and retain results and artifacts. Verification, extraction, viewing, and review work without executing task code.
- Optional human review covers text, code, images, and multiple artifacts, with digest-bound responses and stale-review rejection.
- Activation and closing commands are removed. Evidence determines completion and pending review; historical runs remain visible without banking as new Inspect runs.
- The starter creates three files, with inline dataset/prompts. README and tutorial describe the portable workflow; detailed material is under reference docs.
- Results pages lead with readable findings and put detailed metrics behind an expandable section.

Validation uses deterministic Inspect mock models, including text and image transport, incorrect answers, credentials/budgets, resume, archive round trips, tampering, stale reviews, and offline review. No live paid calls were made.

An offline demo is in `build/simplify-demo`: `view/index.html`, `review-pack/review.html`, a verified archive in `packages`, and an extracted recipient copy. Its model answer is synthetic and human review remains pending.

Limits: image API billing uses estimated `budget_usd` exposure, not Inspect token-priced `cost_limit`. Raw Inspect logs are excluded from archives, so custom scorer replay requires fresh logs. Detailed legacy reference material is retained with migration guidance. Changes are uncommitted; the pre-existing example status edit is preserved.

## Web UI refinement

The live local overview shows run/item progress, six-decimal recorded cost,
answers/checks/review states together, and direct browser review saving and updates.
`/api/state` exposes that state to scripts. Publishing panels stay conditional.
The author-first walkthrough uses browser review as the main path.
Updates poll once per second and cost appears at sample completion, not during
an in-flight provider request. See [the live UI reference](../docs/reference/live-ui.md).
