# Presentation, Packaging, and Showcase: Plan

## Purpose

Tamesu cases already contain experiments, evals, runs, reports, and analyses. They are
good evidence but hard to hand to a colleague: reviewing one means cloning a repository
and reading YAML. This plan covers how to share a case so others can review it, reuse it,
fork it, and find it.

The goal is a website where people publish the latest version of a case study, or a fork
of someone else's, and others can browse and explore it.

It has three parts:

1. **Present**: render a case as a static HTML case study for developer review.
2. **Package**: bundle a case into a versioned, verifiable, forkable archive.
3. **Showcase**: publish packages to a site where people can explore them.

The order of work starts with a **package contract** design step (Step 0) because the
package format is the hardest thing to change once people have published to it.

## Design principles

1. **Keep the file-based model.** Packages are ordinary files. The showcase is built from
   packages, not from a database that becomes the source of truth.
2. **Presentation is derived.** HTML is generated from evidence and never edited by hand.
3. **Identity comes from content.** A package's identity is a digest of its payload, not
   its label.
4. **Integrity is not authorship or privacy.** Checksums prove files were not altered.
   They do not prove who published them, nor that the data is safe to share.
5. **Honest about completeness.** Pages show incomplete samples, unknown costs, and
   limitations. Only complete samples count toward results, as in existing reports.
6. **Static first.** Each stage works as plain files on a static host before any server
   exists.
7. **Minimal dependencies.** The project currently depends only on PyYAML. Every added
   dependency is a decision, listed under "Dependencies" below.

## Step 0: Package contract

Before any code, write `docs/packages.md` and the schemas it describes, and review them.
This step resolves the questions below. Nothing in Parts 1 to 3 is implemented against a
contract that is not settled here.

### Case portability

Today `resolve_contained` (`src/tamesu/config.py`) keeps references inside the **project
root**, not the case directory. A case can therefore reference a shared project-level
file, and a copy of `cases/<case>/` would break.

Decision: **a packageable case must be self-contained.** Every file it references must
live under the case directory. `tamesu pack` enforces this with a new check that resolves
every path in `case.yml`, `dataset.yml`, and `eval.yml` and fails, naming the reference,
when one leaves the case directory. The fix for the author is to move the file into the
case. A dependency-closure copy with path rewriting is rejected because it would change
the content that run fingerprints were computed from.

`tamesu lint --portable` runs the same check without packing.

### Authored source: `publication.yml`

Publication metadata needs an authored home that exists before `pack` and that `present`
can also read. It lives beside `case.yml`:

```yaml
schema_version: 1
name: product-scanning-alignment   # kebab-case package name
publisher: omenking                # stable slug that owns the registry namespace
version: 1.2.0                     # author-chosen semver
title: Product scanning alignment
summary: One sentence for the showcase card.
authors:
  - name: Andrew Brown
license: CC-BY-4.0                 # required
tags: [image, alignment]
profile: rescorable                # full | rescorable | report-only
data_review:                       # required when the profile includes data
  acknowledged: true
  note: Synthetic tickets, no personal data.
forked_from:                       # omitted for originals
  publisher: omenking
  name: product-scanning-alignment
  version: 1.0.0
  content_digest: sha256:...
```

`publication.yml` is authored and never contains digests of its own package. `present`
reads it, so a case page can show title, license, and lineage before anything is packed.

### Inclusion profiles

Independent `runs`, `outputs`, and `datasets` flags allow unusable combinations, such as
outputs with no dataset to rescore them against. Replace them with named profiles:

| Profile | Includes | Lets a reader |
|---|---|---|
| `full` | case files, datasets, prompts, schemas, all runs with outputs and logs | Inspect everything, rescore, and re-run |
| `rescorable` | case files, datasets, prompts, schemas, runs with outputs, no logs | Inspect and rescore without paying for generation |
| `report-only` | case files, prompts, schemas, `report.yml` and result summaries, no datasets or outputs | Read the method and the numbers only |

Rules:

- A profile includes exactly the paths in its row and nothing else. The list is the
  contract and is tested.
- `report-only` cannot satisfy `tamesu lint`, because dataset references would dangle.
  Packing writes a `package-view.yml` marking the dataset items as withheld, and readers
  and `present` treat that package as view-only. `rescore` and `run` refuse it with a
  clear message.
- `present` states the profile on the page so a reader knows what they can verify.

### Package layout and identity

The archive is `<publisher>-<name>-<version>.tamesu.tar.gz`:

```text
package.yml          # generated; describes the payload
payload/             # the profile's files, relative to the case root
presentation/        # optional pre-rendered HTML (Part 1), not part of identity
```

Identity is defined without circularity:

1. **Inventory.** Walk `payload/`, sort paths bytewise, and record each as
   `<sha256>  <path>`, one per line, with LF endings. This is the canonical inventory.
2. **`content_digest`** is `sha256` of the canonical inventory. It depends only on payload
   bytes and paths. Version, authors, and other labels do not affect it.
3. **`package.yml`** records `content_digest`, the profile, the Tamesu version, the
   inventory itself, and a copy of the publication metadata. It is excluded from the
   inventory, so there is no self-reference.
4. **`archive_sha256`** is the hash of the final `.tar.gz`. It is stored in the registry
   entry (Part 3), not inside the archive.

Consequences, stated plainly:

- Two packages with equal `content_digest` have identical payloads. They may differ in
  version, authors, or license text. That is intended.
- Changing only publication metadata produces a new version but the same content digest.
  Verification checks both: the payload matches the inventory, and the manifest matches
  the publication metadata.
- `forked_from` references a `content_digest`, so lineage identifies content and not a
  label that can be republished.

### Deterministic archives

Building the same payload twice must produce identical archive bytes:

- Entries sorted bytewise by path.
- uid and gid set to 0, user and group names empty.
- mtime fixed to 0 (or to `SOURCE_DATE_EPOCH` when set).
- Modes normalized to `0644` for files and `0755` for directories.
- gzip header timestamp fixed and no filename recorded.
- No extended attributes or PAX headers carrying host data.

### Security model

Secrets are one risk among several.

**What is not promised.** Pattern scanning for credentials catches only credentials. It
does not detect personal data, customer data, or proprietary text in datasets and
outputs. The plan does not claim sharing "never leaks".

**Authoring-side controls (at `pack`):**

- Allow-list packing. Include only the profile's paths. Never include `.env`,
  `.tamesu/`, or anything outside the case directory.
- Secret scan of manifests, logs, and outputs for authorization headers, signed URLs,
  and known key prefixes. A hit fails the pack.
- Required `data_review.acknowledged: true` for any profile that includes datasets or
  outputs, with a note. It is an explicit statement by the publisher, recorded in the
  package and shown on the page.
- Strip absolute paths and local usernames from logs, working on structured fields.
- Refuse to pack when a license is missing.

**Consumer-side controls (at `verify`, `unpack`, and `site build`).** Packages come from
other people and are untrusted.

- Archive extraction rejects absolute paths, `..` segments, symlinks, hard links, device
  files, FIFOs, and duplicate entries. It never follows links.
- Limits on file count, per-file size, total expanded size, and compression ratio. The
  defaults are set in Step 0 and checked while streaming, before writing to disk.
- Schema versions and manifests are validated before any content is rendered.

**Presentation controls.**

- Every untrusted value (titles, summaries, outputs, prompts, item data) is HTML-escaped
  by default. Sanitizing Markdown alone is not enough.
- Markdown is rendered with raw HTML disabled.
- Links allow only `http`, `https`, and relative URLs. `javascript:`, `data:`, and
  similar schemes are removed.
- Generated pages carry a restrictive `Content-Security-Policy` meta tag, with no inline
  scripts and no remote resources. The few enhancements load from a local `assets/`
  file.
- Model outputs are shown in escaped, preformatted blocks, never interpreted.

### Dependencies

| Need | Decision |
|---|---|
| Archive creation and reading | Python standard library (`tarfile`, `gzip`, `hashlib`), with the safe-extraction checks above written in-house |
| HTML templating | Standard library with explicit escaping (`html.escape`, `string.Template`) unless Step 0 finds it unworkable. Jinja2 is **not** currently a dependency, and the existing prompt renderer is a small custom one in `tasks/structured_text.py`. |
| Markdown rendering | One optional dependency, installed with an extra (`tamesu[present]`), with raw HTML disabled. Without it, Markdown is shown escaped in a preformatted block. |
| Charts | Inline SVG generated by our own code |

Step 0 confirms or revises these. The core CLI keeps PyYAML as its only required
dependency.

### Done when (Step 0)

- `docs/packages.md`, `schemas/publication.schema.json`, and `schemas/package.schema.json`
  are written and reviewed.
- The profile table lists exact paths per profile, and the limits have numeric defaults.
- A worked example computes a `content_digest` by hand for the example case.

## Part 1: Case study page

### Output

`tamesu present <case>` writes a self-contained static site to `build/present/<case>/`:

```text
build/present/<case>/
├── index.html            # the case study
├── experiments/<id>.html # one page per experiment, with its evals
├── assets/               # css, copied dataset assets, charts
└── data.json             # machine-readable summary used by the showcase
```

It needs no JavaScript to read.

### Case-level aggregation

The current `status_summary` and `comparison_rows` in `src/tamesu/reporting.py` work on a
single eval. A case page spans many experiments and evals, so Part 1 begins with a small
aggregation layer:

- Discover every experiment and eval in the case using `discovery.py`.
- For each eval, call the existing per-eval functions and keep the result as is.
- Build a case summary: for each experiment, its evals with status (complete, partial,
  not run), the primary metric and leader per eval, and the coverage and cost totals.
- Never merge scores across evals. Different evals are different questions, so the case
  page lists their results side by side and does not average them.

This layer is defined in `data.json` and tested before any HTML is written.

### Page structure

1. **Header**: title, summary, license, publisher, version, `content_digest`, profile,
   Tamesu version, and lineage, all from `publication.yml` and `package.yml` when present.
2. **Overview**: the problem from `case.yml` and `README.md`, and a list of experiments
   with the status of each eval.
3. **Per experiment**: hypothesis, method (dataset, arms, prompts, schemas, metrics,
   repetitions, model matrix), leaderboard, per-metric comparison, per-item drill-down,
   and cost and token totals.
4. **Analysis**: shown as described below.
5. **Limitations and coverage**: incomplete runs, failed items, missing prices, and
   fingerprint mismatches that kept runs from being compared.
6. **Reproduce**: commands, required environment variable names (never values), and
   estimated cost from `tamesu plan`. Omitted for `report-only` packages, which cannot be
   re-run.
7. **Download**: link to the archive and its `archive_sha256` (Part 3).

### Author analysis and evidence binding

`analysis.md` lives beside `eval.yml` and is a derived view that can be edited or stale.
It must never read as the current headline finding when the evidence has moved on.

- `tamesu present` computes the eval's current evidence digest (the inventory digest of its
  run reports and results).
- If `analysis.md` has front matter `evidence_digest: sha256:...` that matches, the page
  labels it **Analysis, matches the shown evidence**.
- Otherwise, including when there is no front matter, the page labels it **Author
  commentary, not verified against these results** and places it below the leaderboard,
  not above it.
- The headline finding on the page is the deterministic leaderboard and
  `evaluation-report.md` summary, never `analysis.md`.
- The analysis skill (`skills/tamesu-eval-analysis`) is updated to write the front matter.

### Implementation

- New module `src/tamesu/presenting.py` next to `reporting.py`, reusing its functions.
- Standard-library templating as decided in Step 0, with escaping on by default.
- `schemas/present-data.schema.json` for `data.json`.
- A `present` section in `docs/cli.md`.

### Done when

- The example case (`examples/support-ticket-classification`) renders to a page a
  reviewer can read without opening YAML.
- Rendering twice from the same inputs gives byte-identical output.
- Tests cover: a snapshot of the example; an `analysis.md` with matching, stale, and
  missing evidence digests; escaping of hostile titles, prompts, and outputs
  (`<script>`, `javascript:` links); and absence of secrets in the output.

## Part 2: Package

### Commands

| Command | Purpose |
|---|---|
| `tamesu pack <case>` | Lint with `--portable`, scan, apply the profile, build the deterministic archive |
| `tamesu verify <archive>` | Safe-read the archive, check limits, schemas, inventory, and `content_digest` |
| `tamesu unpack <archive>` | Extract into `cases/` using safe extraction, refusing to overwrite |
| `tamesu fork <archive>` | Unpack, then create a new `publication.yml` with `forked_from` filled in and a new publisher |

`pack` takes everything it needs from `publication.yml` and refuses to run without one.

### Rules

- The version must increase for the same `publisher` and `name`. The registry enforces
  this in Part 3, and `pack` warns locally when it can see a prior version.
- A fork is a new package under the forker's publisher namespace. It is never an
  overwrite of the original.
- Runs inherited from the original stay valid evidence only while their specification and
  content fingerprints still match. The existing planner already decides this.
- Unpacking a `report-only` package is allowed, but the result is marked view-only.

### Implementation

- New module `src/tamesu/packaging.py`, reusing `identity.py` and `artifacts.py` for
  hashing.
- `docs/packages.md` and updates to `docs/file-structure.md` and `docs/manifests.md`.

### Done when

- Pack, verify, and unpack round-trip the example case, with the same `content_digest`.
- Packing the same case twice, on different machines or different mtimes, gives
  byte-identical archives. A test sets different mtimes, uid, and file modes and compares
  archive bytes.
- Each hostile input fails with a clear error and has a test: a tampered file, a missing
  file, a bad schema version, a planted secret, a traversal path, a symlink, a hard link,
  a device entry, a duplicate entry, an over-limit file count, and a decompression bomb.
- A case with a reference outside its directory is refused with the offending path named.
- Each profile includes exactly its listed paths, and `report-only` refuses `rescore`.

## Part 3: Showcase

### Decision: static registry first

Start without an upload server. The showcase is built from an inspectable registry, with
a generated static site on top. This keeps the no-database principle, costs almost
nothing to host, and gives review for free.

**This delivers a reviewed publishing workflow, not website upload.** The original goal
is a site where people upload their case. The v1 workflow is a validated stand-in for
that, and v2 is the part that delivers it.

### Registry entries

Opaque tarballs committed to Git are hard to review and duplicate data across versions.
So the registry stores an **inspectable entry**, and CI produces the archive:

```text
registry/<publisher>/<name>/<version>/
├── entry.yml             # publication metadata, content_digest, archive_sha256
└── payload/              # the unpacked payload, reviewable as ordinary files
```

- CI runs `tamesu pack` on the entry, builds the deterministic archive, records its
  `archive_sha256` in `entry.yml`, and publishes the archive as a release asset or to
  object storage. Because archives are deterministic, anyone can rebuild one and check the
  hash.
- A pull request therefore shows readable diffs of prompts, datasets, and reports.
- Duplication across versions is accepted for the pilot. If the registry grows large,
  move payloads to object storage addressed by `content_digest` and keep only
  `entry.yml` in Git. That change does not alter the package contract.

### Publisher identity

Checksums prove integrity, not authorship. The registry therefore ties a publisher slug
to an account.

- `registry/publishers/<publisher>.yml` lists the slug, display name, and the GitHub
  accounts allowed to publish under it.
- CI rejects a pull request that adds or changes `registry/<publisher>/...` unless the
  pull request author is listed for that publisher.
- A new publisher slug is added through its own reviewed pull request.
- `authors` in `publication.yml` is credit text and has no ownership meaning.

### Publishing flow (v1)

1. Author runs `tamesu pack` locally to check the case, then copies the payload into
   `registry/<publisher>/<name>/<version>/` (a `tamesu publish` helper does this).
2. Author opens a pull request.
3. CI checks publisher ownership, version monotonicity, `verify`, the secret scan, the
   `data_review` acknowledgement, and renders the presentation.
4. A maintainer reviews and merges. The site and archives rebuild.

**Upload flow (v2):** a small authenticated endpoint that performs the same checks and
opens or commits the registry entry. Decide on it after v1 sees real use. It is the step
that delivers the "upload on a website" goal.

### Site

Generated from `registry/` by `tamesu site build`:

- **Home**: search and tag filters over cards (title, summary, publisher, tags, models,
  date), using a generated `index.json`.
- **Case page**: the Part 1 presentation, version picker, download link, `content_digest`,
  `archive_sha256`, and profile.
- **Lineage**: forks of this case and its parent, from `forked_from` content digests.
- **Compare** (later): two versions, or a fork and its parent, showing changed prompts,
  datasets, and leaderboard movement.
- **Publisher page**: their packages.

### Trust statement shown on pages

Results are claims about evidence that ships in the package. For `full` and `rescorable`
profiles a reader can run `tamesu rescore` to check scores, and with `full` can re-run to
check outputs. For `report-only` the page says that the outputs are not included and the
numbers cannot be reproduced from the package.

### Done when

- A registry containing the example case and one fork of it builds a site that shows
  lineage.
- CI rejects each of: a package failing `verify`, a secret hit, a missing `data_review`
  acknowledgement, a version that does not increase, and a publisher the pull request
  author is not listed for.
- The archive built by CI from a registry entry has the `archive_sha256` in `entry.yml`
  when rebuilt locally.
- A reviewer who has never used Tamesu can find a case, read its results, see how it was
  measured, and download it, without installing anything.

## Order of work

| Step | Work | Depends on |
|---|---|---|
| 0 | Package contract: `docs/packages.md`, schemas, profile paths, limits, dependency decisions | none |
| 1 | `lint --portable`, case-level aggregation, `tamesu present`, snapshot and escaping tests | step 0 |
| 2 | `publication.yml`, `pack` and `verify` with deterministic archives and the hostile-input tests | step 0 |
| 3 | `unpack` and `fork`, lineage fields | step 2 |
| 4 | Registry layout, publisher ownership, CI checks, `site build` home and case pages | steps 1 and 2 |
| 5 | Lineage and compare views | steps 3 and 4 |
| 6 | Decide on the upload endpoint from real usage | step 4 in use |

Steps 1 and 2 can proceed in parallel once Step 0 is approved. Steps 0 to 4 validate the
publishing workflow end to end. Website upload is not delivered until step 6.

## Open questions

- **Hosting and domain** for the showcase, and whether it is public or internal at first.
- **Allowed licenses.** The field is required, but the permitted values are for the
  maintainers to choose.
- **Dataset redistribution.** `report-only` covers data that cannot be shared but loses
  drill-down. Decide whether to allow a synthetic or redacted substitute dataset.
- **Image cases.** Image tasks are still planned. Images are ordinary assets in the
  contract, but the size limits need to be revisited once image evals exist.
- **Registry reviewers**, and what the review bar is, including how data review is
  judged.
- **Numeric limits** for file count, file size, expanded size, and compression ratio,
  settled in Step 0.

## Out of scope

- Hosted execution of evals from the website.
- Accounts, comments, ratings, or other social features.
- A database-backed registry.
- Showing scores on the site that a reader has no means to verify.
- A guarantee that shared data is free of personal or proprietary content. The plan
  provides an acknowledgement and review, not detection.
