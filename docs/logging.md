# Logging and execution evidence

Tamesu writes one append-only JSONL event stream for every run. The log is the diagnostic
timeline for execution; `run.yml`, item `result.yml` files, stored outputs, and
`report.yml` remain the authoritative state and result records.

The log for a run is stored beside its run directory:

```text
evals/<eval>/
├── logs/
│   └── <run-id>.jsonl
└── runs/
    └── <run-id>/
```

Resume appends to the same file. Tamesu never truncates or rewrites an existing call log.

## Durability and concurrency

Every event is serialized as one compact JSON object followed by a newline. The writer:

- holds an in-process lock so concurrent item workers cannot interleave lines;
- opens the file in append mode;
- flushes the event immediately;
- calls `fsync` before returning.

This protects concurrent threads in one Tamesu process. A single run must not be executed
by multiple operating-system processes at the same time; cross-process log locking is not
implemented.

Events from different items may be interleaved. Within one item, a provider-attempt event
precedes its artifact and terminal-item events.

## Common event envelope

Every line contains:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | Call-log schema; currently `1` |
| `event` | string | Event type described below |
| `at` | timestamp | UTC event time |
| `run_id` | string | Run receiving the event |

Readers must ignore unknown event types and fields from the same schema version. They must
reject an unsupported schema version rather than guessing its meaning.

The machine-readable contract is
[`schemas/log-event.schema.json`](../schemas/log-event.schema.json).

## Event types

### `run_started`

Written after the resolved `run.yml` reaches `running`. It records provider, model, arm,
repetition, selected-item count, and whether this invocation is a resume. A resumed run
therefore has another `run_started` event with `resumed: true`.

### `provider_attempt`

Written once for every provider invocation, successful or failed:

```json
{"schema_version":1,"event":"provider_attempt","at":"2026-10-03T14:30:12Z","run_id":"...","item_id":"duplicate-charge","stage":"generate","attempt":1,"provider":"openai","model":"gpt-5.4-mini","ok":true,"duration_ms":842,"request":{"system_sha256":"sha256:...","user_sha256":"sha256:...","output_schema_sha256":"sha256:...","parameters_sha256":"sha256:..."},"provider_request_id":"resp_123","response_metadata":{"status":"completed"},"usage":{"input_tokens":110,"output_tokens":28},"cost_usd":0.000209}
```

Tamesu logs prompt, schema, and parameter hashes rather than raw prompts or provider
request bodies. The resolved parameter values and prompt source paths are already in
`run.yml`; generated text is stored as an item artifact.

A failed attempt has `ok: false`, normalized `error` fields, an empty `usage` mapping, and
`cost_usd: null` when the provider did not return trustworthy billing information:

```json
{"schema_version":1,"event":"provider_attempt","at":"2026-10-03T14:30:13Z","run_id":"...","item_id":"duplicate-charge","stage":"generate","attempt":1,"provider":"openai","model":"gpt-5.4-mini","ok":false,"duration_ms":301,"request":{"system_sha256":"sha256:...","user_sha256":"sha256:...","output_schema_sha256":"sha256:...","parameters_sha256":"sha256:..."},"provider_request_id":"req_123","response_metadata":{},"usage":{},"cost_usd":null,"error":{"type":"ProviderError","message":"rate limited","retryable":true,"status_code":429}}
```

Unknown failure cost does not mean the provider guaranteed the attempt was free.

### `artifacts_written`

Written after provider output artifacts have been atomically persisted. Each artifact has
a role, eval-relative path, media type, and SHA-256 checksum. Structured-text runs retain
the raw provider text and, when JSON parsing succeeds, the normalized JSON output.

### `item_finished`

Written after an item's `result.yml` has been atomically persisted. It records terminal
state, total attempts, and the result path and checksum. Failed items include their
normalized error.

### `framework_error`

Written when execution machinery outside a provider attempt raises unexpectedly. Tamesu
marks the run failed, or partial when completed item evidence can be resumed, before
returning an execution error to the CLI.

### `run_finished`

Written after the final `run.yml` and, for a normally finalized run, `report.yml` have
been persisted. It records terminal state and totals. Interrupted invocations record a
partial state and `interrupted: true`; a later resume continues in the same stream.

## Redaction and content boundaries

Before an event is serialized, Tamesu recursively redacts:

- authorization, API-key, password, credential, cookie, signature, and token fields;
- values of sensitive environment variables such as provider API keys;
- common inline secret assignments in error text;
- credential and signature query parameters in HTTP(S) URLs.

The replacement marker is `[REDACTED]`. Token-count fields such as `input_tokens` and
`max_tokens` are not treated as credentials.

The same redactor is applied to provider response metadata stored in `result.yml` and to
unexpected error messages. Request headers and API keys are never intentionally added to
events.

Redaction is defense in depth, not a content-classification system. Provider error text
and metadata may still contain proprietary non-secret content. Raw model output is stored
under the run directory and is not scrubbed. Repositories should make an explicit decision
about whether logs and run artifacts belong in version control.

## Relationship to other evidence

| Question | Source |
|---|---|
| What was intended? | `eval.yml` and resolved `run.yml` |
| Which calls occurred and were retried? | `logs/<run-id>.jsonl` |
| What did one item produce? | Item output plus `result.yml` |
| What is the run's terminal state? | `run.yml` |
| How was quality summarized? | `report.yml` |

Logs aid diagnosis and audit. They do not override a checksum mismatch or make an
otherwise incomplete run bankable.
