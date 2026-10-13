# Case packages

Tamesu packages are deterministic, self-contained archives for reviewing, rescoring,
rerunning, and forking a case. This document is the version 1 package contract.

## Authored metadata

Every packageable case has `publication.yml` beside `case.yml`. It contains a
lowercase kebab-case package and publisher name, an author-chosen semantic version,
title, summary, authors, license, tags, and one inclusion profile. `publication.yml`
never contains a digest of its own package. The optional `forked_from` block records the
parent publisher, name, version, and content digest.

`full` and `rescorable` publications must include `data_review.acknowledged: true` and a
non-empty note. This is a publisher statement, not a guarantee that the content is free
of personal, customer, or proprietary data.

## Portability

A packageable case is self-contained. Every dataset, output schema, prompt, reference,
and dataset asset named by its manifests must resolve to a regular file below the case
directory. Symlinks and references to shared project files are rejected. Run
`tamesu lint --portable cases/<case>` before publishing.

## Exact profile paths

All paths below are relative to the case directory. Hidden files, `.tamesu/`, `.env`,
symlinks, and files outside the case are never included.

| Profile | Included paths | Excluded paths |
|---|---|---|
| `full` | Every regular, non-hidden case file, including `case.yml`, README files, `datasets/**`, `experiments/**`, runs, item outputs/results, and `logs/**` | `publication.yml` (copied into `package.yml` instead), hidden files, and `package-view.yml` |
| `rescorable` | The same paths as `full`, except logs | Every `logs/` directory and `*.jsonl` log, plus the common exclusions |
| `report-only` | `case.yml`, README files, `prompts/**`, `schemas/**`, `eval.yml`, `analysis.md`, `evaluation-report.md`, `leaderboard.md`, run `run.yml`/`report.yml`, item `result.yml`, and generated `package-view.yml` | `publication.yml` (copied into `package.yml` instead), `datasets/**`, logs, `output.txt`, `output.json`, and all other paths |

A report-only package is view-only. Its generated `package-view.yml` records that
datasets, outputs, and logs were withheld. It can be unpacked and presented, but `run`
and `rescore` refuse it.

## Layout and content identity

The filename is `<publisher>-<name>-<version>.tamesu.tar.gz` and contains:

```text
package.yml
payload/
presentation/       # optional, excluded from content identity
```

For each payload file, compute its lowercase SHA-256 hex digest. Sort paths bytewise by
their UTF-8 representation and write a conceptual inventory line as:

```text
<sha256>  <path>\n
```

The `content_digest` is `sha256:` followed by the SHA-256 of those canonical inventory
bytes. `package.yml` stores the digest, profile, Tamesu version, inventory entries
(`path`, `sha256`, and `size`), and an exact copy of publication metadata. `package.yml`
and `presentation/` are not in the inventory. The archive hash is stored outside the
archive, in its registry entry.

Equal content digests mean equal payload paths and bytes. Publication-only changes can
therefore create a new version with the same content digest. On unpack, Tamesu
materializes `publication.yml` from the exact metadata copy in `package.yml`.

### Worked digest

Suppose the payload has two files:

```text
case.yml       bytes: "a\n"  sha256: 87428fc522803d31065e7bce3cf03fe475096631e5e07bbd6f47d13fb506a4c
README.md      bytes: "b\n"  sha256: 0263829989b6fd954f72baaf2fc64bc2e2f01d692d4de72986ea808f6c4e55f2
```

Bytewise sorting puts `README.md` first. The exact inventory bytes are:

```text
0263829989b6fd954f72baaf2fc64bc2e2f01d692d4de72986ea808f6c4e55f2  README.md\n
87428fc522803d31065e7bce3cf03fe475096631e5e07bbd6f47d13fb506a4c  case.yml\n
```

Their SHA-256 is
`sha256:467a3eb22e53f34839828f4716caa522a2bf15d8350ea4e30ebbfc73d5754a54`.
The implementation exposes
`canonical_inventory()` so this example and external implementations can use exactly
the same algorithm.

## Reproducible archives

Archive entries are bytewise sorted. Files use mode `0644`, directories `0755`, uid and
gid 0, and empty owner/group names. Mtime is 0 unless `SOURCE_DATE_EPOCH` is set. The
gzip header has the same fixed timestamp and no source filename. USTAR is used so no
host PAX metadata or extended attributes appear.

## Dependencies and rendering

The contract adds no required dependency. Archive, hashing, HTML templating, escaping,
and presentation primitives use the Python standard library; YAML continues
to use PyYAML. The built-in Markdown subset escapes raw HTML and removes unsafe URL
schemes. Richer Markdown can be added later as an optional extra without changing the
package format.

## Safety limits

Verification and unpacking reject absolute and parent paths, backslashes, duplicate
entries, symlinks, hard links, devices, and FIFOs. Content is checked before extraction.

| Limit | Default |
|---|---:|
| Files | 10,000 |
| One expanded file | 100 MiB |
| Total expanded bytes | 1 GiB |
| Expanded/compressed ratio | 200:1 |

Packing allow-lists profile files and scans text for authorization headers, signed URLs,
and common credential prefixes. Full-profile JSONL logs have absolute home paths
replaced structurally. Pattern scanning does not detect every secret or sensitive datum.

## Commands

`tamesu pack`, `verify`, `unpack`, and `fork` implement the contract. `verify` validates
entry safety, limits, manifest shape, inventory bytes, and content digest without
extracting. `unpack` refuses to overwrite. `fork` writes a new publication identity and
binds its lineage to the parent's content digest.
