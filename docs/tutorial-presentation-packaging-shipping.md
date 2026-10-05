# Tutorial, Part 2: present, package, and ship a case

> **Status:** This tutorial is runnable with Tamesu 0.1. It uses only stored evidence and
> makes no paid provider calls.

[Part 1](tutorial.md) ended with a completed eval, its run evidence, a leaderboard, and an
evaluation report. This tutorial turns that case into three things:

1. a static case study that a colleague can read in a browser;
2. a deterministic archive that another Tamesu user can verify, unpack, and fork;
3. an inspectable registry entry and static showcase site that can be shipped through a
   reviewed pull request.

The tutorial continues to use the
[support-ticket classification project](../examples/support-ticket-classification/).
No server, database, provider credential, or JavaScript build tool is required.

By the end, you will know how to:

- author publication metadata without putting package digests into source files;
- render evidence as escaped, deterministic HTML;
- check that a case is portable before sharing it;
- choose between `full`, `rescorable`, and `report-only` packages;
- build and verify a byte-reproducible `.tamesu.tar.gz` archive;
- unpack or fork an untrusted package safely;
- publish an inspectable registry entry;
- validate publisher ownership and version rules;
- build and ship the static showcase.

## What is being shipped

Tamesu keeps three layers separate:

| Layer | Purpose | Source of truth? |
|---|---|---:|
| Case files and evidence | The experiment, data, runs, outputs, and reports | Yes |
| Package | A versioned, verifiable selection of those files | Derived from the case |
| Presentation and showcase | HTML for human review and discovery | Derived from package evidence |

Do not edit generated HTML to change a conclusion. Change the source evidence or authored
metadata, regenerate the presentation, and build a new package version.

Checksums prove that package bytes have not changed. They do not prove who authored the
case or that its data is safe to share. Publisher ownership and data review address those
different questions explicitly.

## 1. Start from the completed example

Install Tamesu as described in Part 1, then enter the standalone example project:

```sh
cd examples/support-ticket-classification
```

Confirm that the eval and its derived reports are available:

```sh
EVAL_ID=support-ticket-triage/decision-rules/prompt-ablation
tamesu status "$EVAL_ID"
```

`status` compares the runs the eval plans with the evidence on disk. For the bundled
example it prints:

```text
Planned: 4
Banked: 4
Owed: 0
Partial: 4
Failed: 0
Stale: 0
Extra: 0
partial	muse-spark-1-2--basic-prompt--rep1--20261003-234852-145915--4ba60262	partial
banked	muse-spark-1-2--basic-prompt--rep1--20261003-235104-760286--38d1539d	complete
partial	muse-spark-1-2--basic-prompt--rep2--20261003-234855-334984--1e459f99	partial
banked	muse-spark-1-2--basic-prompt--rep2--20261003-235112-950890--10370f06	complete
partial	muse-spark-1-2--decision-rules--rep1--20261003-234858-264890--a6802bc4	partial
banked	muse-spark-1-2--decision-rules--rep1--20261003-235124-524437--a8c8b9ee	complete
partial	muse-spark-1-2--decision-rules--rep2--20261003-234902-788310--03c1aafd	partial
banked	muse-spark-1-2--decision-rules--rep2--20261003-235130-559270--6849210c	complete
```

How to read it:

- **Planned** is the number of runs the eval requires (models x arms x repetitions).
- **Banked** counts completed, compatible runs. Only these contribute to results.
- **Owed** is planned runs with no banked evidence. It must be `0` for a finished study.
- **Partial**, **Failed**, **Stale**, and **Extra** are runs found on disk but excluded
  from results: interrupted, errored, produced under a changed manifest, or not part of
  the plan.
- Each following line is `state<TAB>run id<TAB>run status`.

Here every planned run is banked (`Planned: 4`, `Banked: 4`, `Owed: 0`). The four
`partial` lines are earlier interrupted attempts of the same four runs; they are kept
as evidence but excluded, and the banked reruns replace them.

```sh
tamesu report "$EVAL_ID"
```

`report` regenerates the evaluation report from banked evidence and prints only its
path:

```text
<project>/cases/support-ticket-triage/experiments/decision-rules/evals/prompt-ablation/evaluation-report.md
```

That file is what you read. Its main sections, abridged:

```markdown
# prompt-ablation evaluation report

- Eval: `support-ticket-triage/decision-rules/prompt-ablation`
- Dataset: `support-tickets-v1` (8 authored items)
- Status: `complete`
- Primary metric: `exact_match_rate`

## What happened

All 4 planned runs are banked as compatible completed evidence.

`muse-spark-1.2 / decision-rules` recorded the highest observed `exact_match_rate`
at 0.7500 (75.00%). The observed spread from `muse-spark-1.2 / basic-prompt` was
0.0625 (6.25 percentage points).

## Results

| Model | Arm | Repetitions | Items | Primary mean | Std. dev. | Failure rate | Cost (USD) |
|---|---|---:|---:|---:|---:|---:|---:|
| muse-spark-1.2 | basic-prompt | 2 | 16 | 0.6875 | 0.0625 | 0.0000 | 0.0139 |
| muse-spark-1.2 | decision-rules | 2 | 16 | 0.7500 | 0.0000 | 0.0000 | 0.0132 |

## Diagnostic metrics
(per arm: category, priority, and requires-human accuracy, invalid-output rate,
median latency, mean cost)

## Evidence coverage
(the same Planned/Banked/Owed/Partial counts that `status` printed)

## Limitations
- The dataset contains 8 authored items.
- The smallest reported row contains 2 repetition(s); dispersion estimates are limited
  by that count.
```

Reading the results: `decision-rules` scored 0.75 against 0.6875 for `basic-prompt`, a
difference of one item in sixteen. The diagnostics show where it came from:
`requires_human_accuracy` rose from 0.75 to 1.00 while `priority_accuracy` fell from
0.8125 to 0.75. With 8 items and 2 repetitions, treat it as an observed difference, not
proof that the rules cause it. The presentation in the next step is built from these
same numbers.

Exact values, timestamps, and run ids will differ if you rerun the evals yourself.

The commands in this tutorial do not call a model. They read existing files and write
derived presentation, package, or registry files.

## 2. Inspect `publication.yml`

Open:

```text
cases/support-ticket-triage/publication.yml
```

The bundled example contains:

```yaml
schema_version: 1
name: support-ticket-triage
publisher: omenking
version: 1.0.0
title: Support Ticket Triage
summary: Compare support-ticket classification prompts using synthetic customer requests.
authors:
  - name: Andrew Brown
license: CC-BY-4.0
tags:
  - classification
  - prompt-engineering
profile: rescorable
data_review:
  acknowledged: true
  note: The dataset contains only synthetic support tickets and no personal data.
```

This file is authored beside `case.yml`. It gives both `present` and `pack` a title,
summary, license, version, profile, and publisher namespace before an archive exists.

The important boundaries are:

- `name` must match `case.name` and both names use lowercase kebab-case;
- `version` is an author-chosen semantic version;
- `license` is required, but your organization chooses which licenses it permits;
- `data_review` is required when datasets or outputs will be included;
- `data_review` is an acknowledgement, not automatic personal-data detection;
- package digests do not belong in this authored file.

For your own case, replace the publisher, authors, license, summary, tags, and review note.
Do not claim the example's `omenking` namespace in a shared registry unless its publisher
record authorizes your GitHub account.

## 3. Bind the conclusion to its evidence

The leaderboard and `evaluation-report.md` are deterministic. An `analysis.md` written by
a person or coding agent is editable commentary and can become stale.

When an analysis exists beside `eval.yml`, its YAML front matter should contain the
current evidence digest:

```yaml
---
evidence_digest: sha256:<digest of current run reports and item results>
---

# Analysis

This is derived interpretation, not evaluation evidence.
```

The `tamesu-eval-analysis` skill computes this digest when it writes an analysis. During
presentation, Tamesu labels a matching file **Analysis, matches the shown evidence**. A
missing or stale digest is labeled **Author commentary, not verified against these
results** and is placed below the deterministic results.

You do not need an `analysis.md` to run or package an eval. The presentation always
computes an observed outcome from compatible stored reports. A human-readable case study
should also include an evidence-bound analysis that answers the technical uncertainty,
states what remains unknown, and names the next experiments. Without it, Tamesu can show
what was measured but cannot safely invent why it happened or what should be tested next.

## 4. Render the static case study

Render the case by its case ID:

```sh
tamesu present support-ticket-triage
```

Expected output resembles:

```text
Rendered case study: <project>/build/present/support-ticket-triage
```

The generated site contains:

```text
build/present/support-ticket-triage/
├── index.html
├── data.json
├── assets/
│   └── style.css
└── experiments/
    └── decision-rules.html
```

Open `index.html` directly in a browser. No web server is required to read it. The case
page shows the authored business use, current problem, technical uncertainty, datasets,
experiment and eval status, evidence coverage, token and cost totals, and package
information. The experiment page shows method details, dataset context, prompts, metric
comparisons, per-item evidence, limitations, and reproduction commands.

Rendering the same inputs twice produces byte-identical output. Tamesu escapes titles,
prompts, outputs, analyses, and other untrusted values; raw model output is displayed as
text. Pages use a restrictive Content Security Policy and load no remote resources.

The machine-readable `data.json` contains the same case-level aggregation used to build
the pages. Scores from different evals remain separate because different evals answer
different questions.

## 5. Check case portability

A project can normally reference a shared file elsewhere in the repository. A package
cannot: copying only the case would break that reference and rewriting it would invalidate
the fingerprints attached to existing evidence.

Run the stricter portability check:

```sh
tamesu lint --portable cases/support-ticket-triage
```

Expected final output:

```text
portable  support-ticket-triage
Validated 1 eval(s).
```

This resolves datasets, schemas, prompts, references, and dataset assets. Every referenced
file must be a regular file below the case directory. If a reference leaves the case,
Tamesu names the manifest field and offending path. Move that dependency into the case
and update the authored reference; do not copy it only at package time.

`pack` runs this same check automatically. Running it separately makes the failure easier
to fix before publication metadata and security checks are involved.

## 6. Choose an inclusion profile

The example uses `profile: rescorable`. Select a profile according to what readers are
allowed to inspect and reproduce:

| Profile | Includes | Reader capability |
|---|---|---|
| `full` | Case files, datasets, prompts, schemas, outputs, results, reports, and logs | Inspect, rescore, and rerun |
| `rescorable` | The same evidence without logs | Inspect and rescore without paying for generation |
| `report-only` | Method files, reports, and result summaries; no datasets, outputs, or logs | Read the method and reported numbers only |

Do not use `report-only` merely to make an otherwise incomplete case look finished. Its
page explicitly says that the reported numbers cannot be reproduced from the package.
When unpacked, it contains `package-view.yml`; `run` and `rescore` refuse that view-only
case because its required data was deliberately withheld.

Profile selection is an allow-list. `.env`, `.tamesu/`, hidden files, symlinks, and paths
outside the case are never included. `publication.yml` is copied into `package.yml`
rather than included in the content-addressed payload, so a metadata-only version change
does not change the payload's content identity.

## 7. Build the package

Build a package using the profile in `publication.yml`:

```sh
tamesu pack support-ticket-triage
```

Expected output has three lines like these:

```text
Packed <project>/build/packages/omenking-support-ticket-triage-1.0.0.tamesu.tar.gz
Content digest: sha256:<payload digest>
Archive SHA-256: sha256:<archive digest>
```

By default, `pack` embeds the generated case study under `presentation/`. The
presentation is convenient for recipients but excluded from content identity. Use
`--without-presentation` only when another system will render the package itself.

During packing Tamesu:

1. validates `publication.yml` and its license;
2. requires the data-review acknowledgement for `full` and `rescorable`;
3. checks portability;
4. applies the exact profile allow-list;
5. scans text for authorization headers, signed URLs, and common credential prefixes;
6. strips absolute home paths from structured full-profile logs;
7. builds the canonical inventory and content digest;
8. writes a normalized tar and gzip stream.

The secret scan is deliberately conservative, but it is not a privacy or compliance
scanner. Review datasets, prompts, outputs, reports, and copied assets yourself before
shipping them.

Set a reusable shell variable for the next commands:

```sh
ARCHIVE=build/packages/omenking-support-ticket-triage-1.0.0.tamesu.tar.gz
```

## 8. Verify identity and reproducibility

Verify the archive without extracting it:

```sh
tamesu verify "$ARCHIVE"
```

Expected output:

```text
Verified build/packages/omenking-support-ticket-triage-1.0.0.tamesu.tar.gz: sha256:<payload digest>
Archive SHA-256: sha256:<archive digest>
```

The two hashes answer different questions:

- `content_digest` identifies sorted payload paths and bytes. Publication-only changes do
  not change it.
- `archive_sha256` identifies the complete compressed file, including `package.yml` and
  an optional presentation. A version or author change therefore changes this hash.

Building the same case twice with the same profile and metadata produces identical
archive bytes, regardless of source file mtimes, modes, uid, or gid. The archive uses a
fixed timestamp by default; reproducible build systems can set `SOURCE_DATE_EPOCH`.

Verification treats an archive as untrusted. It rejects traversal and absolute paths,
duplicate entries, links, devices, FIFOs, unsupported schema versions, inventory
mismatches, dangling included references, excess file sizes/counts, expansion limits,
and excessive compression ratios. It performs these checks before anything is written to
an extraction destination.

## 9. Test the consumer experience

Unpack into a disposable cases directory without overwriting the original case:

```sh
mkdir -p build/review-cases
tamesu unpack "$ARCHIVE" --cases-dir build/review-cases
```

Expected output:

```text
Unpacked case: <project>/build/review-cases/support-ticket-triage
```

`unpack` verifies the archive first and refuses an existing destination. It recreates
`publication.yml` from the exact publication copy in `package.yml`.

Inspect the extracted files and confirm that they match the promised profile. For the
example's `rescorable` package, dataset and output evidence are present while JSONL logs
are absent.

Create a fork in a different disposable directory:

```sh
mkdir -p build/fork-cases
tamesu fork "$ARCHIVE" \
  --cases-dir build/fork-cases \
  --publisher my-team \
  --name support-ticket-triage-fork \
  --version 0.1.0
```

The fork receives a new package identity. Its generated `publication.yml` contains:

```yaml
forked_from:
  publisher: omenking
  name: support-ticket-triage
  version: 1.0.0
  content_digest: sha256:<parent payload digest>
```

Lineage points to content, not merely a mutable label. Before packaging the fork, place it
under the `cases/` directory of a Tamesu project, review its inherited license and data,
and update its title, summary, authors, and version as appropriate.

## 10. Create a publisher record

The static registry ties publisher slugs to GitHub accounts. Authors listed in
`publication.yml` receive credit but do not own the namespace.

Clone the separate showcase repository beside Tamesu and enter it:

```sh
git clone git@github.com:ExamProCo/tamesu-showcase.git /path/to/tamesu-showcase
cd /path/to/tamesu-showcase
ARCHIVE=/absolute/path/to/omenking-support-ticket-triage-1.0.0.tamesu.tar.gz
```

Set `ARCHIVE` to the package built in step 7. The remaining
`registry/` and `build/site` paths belong to this showcase checkout, not to the Tamesu
framework repository.

The live registry already contains published entries, including the bundled example.
Use an ignored scratch registry for the walkthrough so you do not collide with or modify
an immutable live version:

```sh
REGISTRY=build/tutorial-registry
```

For a local walkthrough, create a publisher record matching the example package:

```sh
mkdir -p "$REGISTRY/publishers"
cat > "$REGISTRY/publishers/omenking.yml" <<'YAML'
schema_version: 1
slug: omenking
display_name: Omenking
github_accounts:
  - your-github-login
YAML
```

Replace `your-github-login` with the account used to open the publishing pull request.
In a shared registry, do not add this example record unless you are authorized to manage
the `omenking` namespace. Register your own slug in a separate reviewed pull request,
then change the case's publisher and build a new package.

Keeping publisher registration separate prevents a pull request from granting itself a
namespace and publishing under it in the same change.

## 11. Publish an inspectable registry entry

Add the verified archive to the local registry:

```sh
tamesu publish "$ARCHIVE" --registry "$REGISTRY"
```

Expected output:

```text
Published registry entry: <showcase>/build/tutorial-registry/omenking/support-ticket-triage/1.0.0
```

The entry is deliberately reviewable in Git:

```text
build/tutorial-registry/
├── publishers/
│   └── omenking.yml
└── omenking/
    └── support-ticket-triage/
        └── 1.0.0/
            ├── entry.yml
            └── payload/
```

`entry.yml` records publication metadata, the content digest, the reproducible archive
hash, Tamesu version, and inventory. `payload/` contains ordinary files, so a pull request
shows readable prompt, dataset, report, and evidence changes rather than an opaque binary
tarball.

Published versions are immutable. To publish a change, increase `publication.version`,
pack again, and add the new version. `publish` refuses to replace an existing entry or
add a version that is not greater than the versions it can see locally.

## 12. Validate the registry as CI will

Validate all publisher records, entries, payloads, secrets, and reproducible hashes:

```sh
tamesu site check --registry "$REGISTRY"
```

For the local publisher-ownership check, use the account in the publisher record:

```sh
tamesu site check --registry "$REGISTRY" --author your-github-login
```

Expected output:

```text
Validated 1 registry entry.
```

The showcase repository's workflow performs a pull-request-sized version of this check.
It reads changed registry paths and rejects:

- a package whose payload or inventory fails verification;
- a secret-pattern hit;
- missing data-review acknowledgement;
- changes to an already published version;
- a new version lower than or equal to an existing version;
- publication under a namespace that does not list the pull-request author;
- adding a publisher record and publishing under it in the same change.

The full registry is still validated and rebuilt, so unchanged entries cannot silently
become invalid.

## 13. Build and inspect the showcase

Build the static site:

```sh
tamesu site build --registry "$REGISTRY" --output build/site
```

Expected output:

```text
Built showcase: <project>/build/site
```

The generated site contains:

```text
build/site/
├── index.html
├── index.json
├── assets/
├── downloads/
│   └── omenking-support-ticket-triage-1.0.0.tamesu.tar.gz
├── publishers/
│   └── omenking/
└── omenking/
    └── support-ticket-triage/
        └── 1.0.0/
            ├── index.html
            ├── data.json
            └── experiments/
```

Open `build/site/index.html` directly, or serve it locally if you want to exercise search:

```sh
python -m http.server 8000 --directory build/site
```

Then visit `http://localhost:8000/`. The home page searches titles, summaries, tags,
publishers, and models. Case pages expose version, profile, content and archive hashes,
the trust statement, and package download. Publisher pages list their packages.

When the registry contains a fork, parent and child pages link to each other by the
parent's content digest. A fork or later version also gets a comparison page showing
changed prompts, datasets, schemas, other payload paths, and stored leaderboard movement.

Stop the local server with `Ctrl-C` when finished.

## 14. Ship the static output

The v1 shipping workflow is intentionally static and reviewed:

1. Register a publisher slug in its own pull request.
2. Run `lint --portable`, `pack`, and `verify` locally.
3. From a clean showcase checkout, run `publish --registry registry` to create the live
   inspectable registry entry.
4. Commit the publisher record or entry payload, not a hand-edited generated website.
5. Open a pull request under an authorized GitHub account.
6. Let the showcase workflow validate the change and build `build/site` as an artifact.
7. On `main`, let the showcase repository deploy that artifact to GitHub Pages.

Because the site is derived from the registry, rebuild it instead of editing pages by
hand. Because package archives are deterministic, anyone can rebuild a registry entry and
confirm its `archive_sha256` before hosting or downloading it.

The live registry, CI workflow, and Pages deployment are maintained in
[`ExamProCo/tamesu-showcase`](https://github.com/ExamProCo/tamesu-showcase). The
authenticated browser upload endpoint described as a future v2 is not implemented.
The reviewed registry pull request is the current publishing boundary. Tamesu also does
not host eval execution, provider credentials, comments, ratings, or a database-backed
catalog.

## Troubleshooting map

| Symptom | Start here |
|---|---|
| `publication.yml` is missing or invalid | Compare it with step 2 and `schemas/publication.schema.json` |
| A reference leaves the case | `tamesu lint --portable` and the named manifest field |
| Pack reports a possible secret | Inspect the named file; remove the value rather than bypassing the scan |
| Two versions unexpectedly have the same content digest | Check whether only publication metadata changed; this is intentional |
| Archive verification fails | Treat the archive as altered or malformed; rebuild it from reviewed source |
| Unpack refuses the destination | Choose an empty cases directory or a new fork name; unpack never overwrites |
| A report-only case cannot run or rescore | Obtain a `full` or `rescorable` package containing the withheld evidence |
| Registry ownership fails | Check the publisher slug, GitHub login, and separately reviewed publisher record |
| Registry version fails | Increase semantic version; published versions are immutable |
| Site search does not work from a local file | Serve `build/site` with the local HTTP command in step 13 |

## Final review checklist

Before opening a publishing pull request, confirm:

- the case question and experiment method are understandable without opening YAML;
- only complete, compatible evidence contributes to reported comparisons;
- incomplete, failed, stale, and unpriced evidence is visible;
- the publication profile matches what the payload actually permits a reader to verify;
- the license permits redistribution of every included file;
- data review covers datasets, prompts, outputs, reports, and binary assets;
- `analysis.md`, if present, is bound to the current evidence digest;
- `lint --portable`, `pack`, `verify`, and `site check` all succeed;
- the publisher record authorizes the pull-request author;
- the version is new and the parent digest is correct for a fork;
- the generated case page, package download, lineage, and comparison page work locally.

For the normative profile rules, digest algorithm, and archive limits, read the
[Case package contract](packages.md). For every command option, read the
[CLI reference](cli.md).
