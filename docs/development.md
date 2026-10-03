# Development guide

> **Status:** Tamesu 0.1 implements the structured-text vertical slice, six provider
> adapters, and its automated test suite. Image tasks and judging remain roadmap work.

This guide is for contributors implementing Tamesu itself. For authoring evaluation
content, start with [Core concepts](concepts.md) and the
[Manifest reference](manifests.md).

## Technical direction

Tamesu targets Python 3.12 or newer. The current foundation is deliberately small:

- the standard library's `argparse` and dataclasses for the CLI and domain objects;
- [PyYAML](https://pyyaml.org/) for YAML parsing and serialization;
- the standard library's `urllib` for provider HTTP transports;
- `unittest` for the dependency-free test suite.

Provider SDKs should remain optional wherever practical. A user who only lints or compares
local evaluations should not need every provider package installed.

The filesystem contract is more important than the Python implementation. Other tools
must be able to inspect Tamesu repositories without importing the package.

## Local setup

A standard local setup is:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Verify the installation with:

```sh
tamesu --help
python -m unittest discover -s tests -v
```

## Source layout

```text
src/tamesu/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── discovery.py
├── errors.py
├── planner.py
├── pricing.py
├── runner.py
├── identity.py
├── artifacts.py
├── scoring.py
├── reporting.py
├── models.py
├── providers/
│   ├── __init__.py
│   ├── anthropic.py
│   ├── base.py
│   ├── bedrock.py
│   ├── common.py
│   ├── gemini.py
│   ├── http.py
│   ├── meta.py
│   ├── models.py
│   └── openai.py
└── tasks/
    └── structured_text.py
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | Parse commands and options, invoke application services, and format results |
| `config.py` | Load YAML safely and validate typed manifest models |
| `discovery.py` | Find cases, experiments, evals, datasets, and runs inside a repository |
| `errors.py` | Define user-facing configuration, execution, and provider failures |
| `planner.py` | Expand an eval into concrete run specifications and calculate work owed |
| `pricing.py` | Estimate expected cost and maximum priced exposure for a plan |
| `runner.py` | Coordinate item execution, retries, budgets, resume, and terminal state |
| `identity.py` | Canonicalize fingerprint inputs and calculate SHA-256 identities |
| `artifacts.py` | Validate, checksum, quarantine, and atomically write artifacts |
| `scoring.py` | Run deterministic checks and aggregate item and run metrics |
| `reporting.py` | Build reports, comparisons, and leaderboards from stored evidence |
| `models.py` | Shared domain models and normalized result types |
| `providers/` | Translate normalized requests into provider operations |
| `tasks/` | Define task inputs, output validation, prompting, and task metrics |

Keep the CLI thin. Business rules belong in modules that can be exercised without a
terminal process.

## Architectural boundaries

### Provider adapters

A provider adapter owns transport-specific behavior:

- credential discovery;
- supported model and parameter validation;
- request construction;
- response normalization;
- timeout and retryability classification;
- provider request IDs;
- usage and cost calculation.

It must not select dataset items, render task-specific prompts, decide how an output is
scored, or write directly into an eval directory.

Provider adapters should return normalized values and artifact bytes or streams to the
runner. The artifact layer decides how those values are persisted.

### Task adapters

A task adapter owns task-specific behavior:

- dataset item and asset validation;
- prompt rendering for an experimental arm;
- construction of a normalized provider request;
- output validation and media metadata;
- deterministic task checks and metrics;
- declaration of files included in the content fingerprint.

The implemented adapter is `structured_text`; `image_generation` is planned next.

Task adapters must not implement provider authentication, retry policy, or eval run
selection.

### Core orchestration

Planning, execution, scoring, and reporting should depend on provider and task interfaces,
not concrete adapters. The first release should use explicit registries rather than a
dynamic plugin system. Add entry-point loading only when a real external adapter requires
it.

## Non-negotiable invariants

Implementation choices may change, but these behaviors must remain true.

### Evidence is never overwritten

Running an eval creates a new run ID and directory. `--force` means “create another fresh
run,” not “replace this run.” Generation outputs, provider-attempt logs, and original
judgments are immutable evidence.

Derived `report.yml` and `leaderboard.md` files may be replaced only through an explicit
rebuild or rescore operation.

### The manifest is not completion state

`eval.yml` declares work owed. Completion is determined by compatible artifacts on disk.
A filename, directory name, or edited status field is not proof that work succeeded.

### Only compatible complete runs count

A run satisfies a planned repetition only when:

- all selected items have valid terminal artifacts;
- mandatory judging and human review are present;
- its specification fingerprint matches the current expanded plan;
- its content fingerprint matches the current inputs and implementation.

Partial, failed, stale, and extra runs remain visible but do not satisfy an owed slot.

### Paid work is explicit

`lint`, `list`, `plan`, `status`, `compare`, `leaderboard`, and `close` never make paid
provider calls. Planning reports expected calls, known cost, maximum exposure, unknown
prices, and missing credentials before execution.

`rescore` never regenerates outputs. Tamesu 0.1 only performs deterministic local
rescoring; future paid judge support must expose its work and cost before starting.

### Failures remain in the denominator

Provider errors and invalid model outputs are recorded as outcomes. Reporting must not
drop failed items in a way that improves a model's score. Where useful, report both a
conditional score among valid outputs and an end-to-end score that counts generation
failures as failures.

## Loading and validation

Manifest loading should follow a predictable pipeline:

```text
read bytes
  → parse YAML safely
  → validate schema version
  → validate typed fields
  → resolve contained paths
  → apply cross-file rules
  → invoke task/provider validation
```

Use a safe YAML loader. Never construct arbitrary Python objects from repository YAML.

Validation errors should identify:

- the source file;
- the field or item involved;
- the invalid value when it is safe to print;
- the expected constraint;
- a practical correction where one is known.

Collect independent errors when possible so users can fix a manifest in one pass. Stop
early only when later validation would be misleading or unsafe.

Unknown fields are errors in schema version 1, except for mappings explicitly designated
for provider parameters or task metadata.

## Fingerprints

Fingerprints use SHA-256 over canonical JSON. The canonicalizer must recursively sort
mapping keys and normalize values according to their validated models. YAML whitespace,
comments, aliases, and mapping order must not affect identity.

Keep specification and content fingerprints separate:

- The **specification fingerprint** describes the intended measurement.
- The **content fingerprint** describes the exact inputs and machinery used.

Fingerprint construction should be implemented as small, pure functions with fixture-based
tests. Every included field should be deliberate and documented. Avoid hashing entire
directories without a stable, inspectable inventory.

## Artifact writes and logging

YAML, JSON, and Markdown outputs should be written safely:

1. Serialize to a temporary file in the destination directory.
2. Flush and `fsync` the file.
3. Atomically rename it over the derived destination or into its new immutable path.
4. `fsync` the directory when the platform supports it.

Provider-attempt logs are append-only JSONL. Each line must be complete, independently
parseable, and flushed immediately. Concurrent workers must not interleave writes.

Before logging, recursively redact:

- authorization and API-key fields;
- configured secret names;
- signed query parameters;
- credential-bearing URLs;
- provider response fields known to contain secrets.

Large or binary data belongs in artifact files, not YAML or JSONL. Store a relative path,
media type, byte size, and checksum instead.

## Execution and resume

The runner should create and persist the resolved run manifest before the first provider
call. Item execution then advances through explicit stages, such as:

```text
prepare → generate → validate → judge → score → report
```

Resume works at item and stage granularity. Before reusing work, verify fingerprints,
checksums, the saved resolved configuration, and the current validator. Never repeat a
successful generation merely because a later judgment failed.

Retry only failures classified as transient. A valid provider response that violates the
task schema is normally a model outcome, not a transport retry. Record every attempt and
its cost.

Before scheduling work, reject a registered, priced plan when its maximum exposure exceeds
the eval budget. Unknown prices must remain visible rather than being treated as free.
Record actual normalized usage and calculated cost for every successful response.

## Testing strategy

The test suite should favor deterministic local fixtures and avoid live paid calls.

### Unit tests

Cover pure and isolated behavior:

- manifest field and cross-field validation;
- path containment and resolution;
- canonicalization and stable fingerprints;
- run-matrix expansion;
- run classification as banked, partial, stale, failed, or extra;
- retry classification;
- cost aggregation and budget boundaries;
- secret redaction;
- metric denominators and aggregation.

### Contract tests

Every provider adapter should run against recorded or fake responses that exercise:

- successful generation;
- transient and permanent errors;
- rate limiting and timeout behavior;
- malformed or incomplete responses;
- usage and cost normalization;
- request ID preservation;
- secret removal from logs.

Task adapters should have fixtures for valid outputs, corrupt outputs, schema violations,
missing assets, and task-specific edge cases.

### Integration tests

Use temporary repositories to exercise complete local workflows:

```text
lint → plan → fake run → interrupt → resume → rescore → compare → leaderboard → close
```

Important scenarios include:

- process interruption after an artifact write;
- a changed prompt or dataset making prior work stale;
- concurrent item completion;
- a retry that succeeds without duplicating evidence;
- a limited probe that remains unbanked;
- a budget stop that preserves completed items;
- rescoring without a generation call;
- deterministic leaderboard regeneration.

The fake provider should be a first-class test utility with scripted responses, latency,
usage, cost, and failures.

### Optional live tests

Live provider tests must be separately marked, skipped by default, budget-capped, and
excluded from normal continuous integration. They should validate a minimal request rather
than reproduce full evaluation runs.

## Implementation status

The initial vertical slice was built in phases. These portions are complete:

### File contract and read-only commands

- Bootstrap the package and CLI.
- Implement typed manifests and schema-version checks.
- Add repository discovery and path containment.
- Implement `lint`, `list`, and deterministic `plan`.
- Add canonical fingerprints and planning tests.

### Artifact-safe execution

- Implement generated run and item records.
- Add atomic writes, append-only logs, and redaction.
- Build the fake provider and retry classification.
- Implement interruption-safe execution and resume.
- Enforce concurrency and budget ceilings.

### Structured-text task, scoring, and reporting

- Add structured text generation and deterministic mechanical checks.
- Implement `rescore`, `compare`, and `leaderboard`.
- Preserve scoring-source boundaries.

### Provider coverage

- Add first-party OpenAI, Anthropic, Gemini, Grok, Meta, and Bedrock/Nova adapters.
- Normalize usage, cost, request IDs, structured output, and retryable failures.
- Validate known model/provider pairs and effort settings.
- Contract-test request and response serialization without paid calls.

The next phases remain roadmap work:

- Add the image-generation task adapter and media artifact validation.
- Add structured model judgments and blinded human-review artifacts.
- Harden cancellation and live budget tracking.
- Finalize schema version 1 from broader real usage.

Avoid adding a plugin loader, web dashboard, database, distributed scheduler, or hosted
control plane before the first two task adapters demonstrate a concrete need.

## Adding a provider adapter

When adding a provider:

1. Define its normalized capabilities and supported parameters.
2. Implement credential discovery without exposing credential values.
3. Translate normalized requests and responses at the adapter boundary.
4. Classify errors as transient or permanent.
5. Preserve request IDs, usage, price source, and actual or unknown cost.
6. Add fake-response contract tests before any optional live test.
7. Register the adapter explicitly.
8. Document environment variable names and provider-specific limitations.

Do not silently drop unsupported parameters. Reject them during validation with a clear
message.

## Adding a task adapter

When adding a task:

1. Define required dataset asset roles and metadata.
2. Define prompt or request rendering inputs.
3. Specify the normalized provider capability it needs.
4. Validate outputs and declare stored artifact metadata.
5. Add deterministic checks and metric definitions.
6. Declare every file that contributes to the content fingerprint.
7. Add fixture-based unit and integration tests.
8. Register the adapter explicitly.

Task metrics should state their denominator and evidence source. Prefer small atomic
dimensions over one opaque quality score.

## Change checklist

Before considering an implementation change complete:

- run the focused tests and the full local suite;
- lint representative valid and invalid fixture repositories;
- confirm no command unexpectedly contacts a provider;
- inspect new YAML, JSONL, and Markdown artifacts by hand;
- verify logs contain no credentials or signed URLs;
- test interruption or failure paths for write-related changes;
- update schemas and documentation when the file contract changes;
- document fingerprint changes that make existing evidence stale.

For the command behavior contributors are implementing, see the
[Command-line interface](cli.md). The complete design rationale remains in the
[Tamesu implementation plan](../plans/EVALUATION-FRAMEWORK-IMPLEMENTATION.md).
