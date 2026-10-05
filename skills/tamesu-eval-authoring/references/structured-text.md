# Structured-text authoring reference

Use this reference only when a project has no existing Tamesu case to copy and no local
manifest documentation. Replace every `<placeholder>` before linting.

## Minimal layout

```text
cases/<case>/
├── case.yml
├── datasets/<dataset>/dataset.yml
└── experiments/<experiment>/
    ├── prompts/system.md
    ├── prompts/user.md.j2
    ├── schemas/output.schema.json
    └── evals/<eval>/eval.yml
```

Paths in `eval.yml` resolve from that file. The dataset is four levels above its eval
directory; prompts and schemas are two levels above it.

## Authored files

`case.yml`:

```yaml
schema_version: 1
name: <case>
title: <human-readable title>
description: <concise description of what the case evaluates>
business_use: <how the evaluated capability would be used in practice>
current_problem: <present pain or limitation that motivates the work>
technical_uncertainty: <unknown that the experiments are intended to resolve>
default_task: structured_text
```

`dataset.yml`:

```yaml
schema_version: 1
name: <dataset>
description: <population and version represented by these items>
items:
  - id: <stable-item-id>
    input:
      text: <representative input>
    expected:
      label: <expected-label>
```

`prompts/user.md.j2` may interpolate scalar item values with expressions such as:

```text
Classify this input:

{{ item.input.text }}
```

Tamesu's template syntax is deliberately small. It supports dotted lookup of scalar
values; it does not support loops, filters, functions, or arbitrary Jinja expressions.

`schemas/output.schema.json` should constrain the stored expected shape. A minimal label
schema is:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["label"],
  "properties": {
    "label": {
      "type": "string",
      "enum": ["<allowed-label-a>", "<allowed-label-b>"]
    }
  }
}
```

`eval.yml`:

```yaml
schema_version: 1
name: <eval>
status: draft
question: <specific question answered by the comparison>
description: <changed factor, controlled variables, and intended interpretation>

task: structured_text
dataset: ../../../../datasets/<dataset>/dataset.yml
output_schema: ../../schemas/output.schema.json

defaults:
  repetitions: 2
  concurrency: 2
  timeout_seconds: 60
  retries: 1
  budget_usd: 2.00
  parameters:
    max_tokens: 512

arms:
  - id: baseline
    description: <baseline condition>
    prompts:
      system: ../../prompts/system.md
      user: ../../prompts/user.md.j2

runs:
  - model: <model-from-tamesu-models>
    provider: <matching-provider>
    arms:
      - baseline

evaluation:
  mechanical:
    - valid_json
    - schema_valid
    - exact_match

metrics:
  primary: exact_match_rate
  secondary:
    - invalid_output_rate
    - generation_failure_rate
    - median_latency_ms
    - mean_cost_usd
```

Use `tamesu models` to choose a built-in model/provider pair and see accepted effort
values and known pricing. Add a second arm only when it represents a deliberate comparison;
each arm requires its own prompt mapping even when it reuses shared prompt files.

## Final checks

- IDs are lowercase kebab-case and match their directory names.
- Every item has an `input` mapping and `expected` output.
- Expected outputs conform to the JSON Schema.
- Each arm states what condition it represents.
- The primary metric answers the declared question.
- The budget covers the plan's maximum priced exposure.
- Secrets remain in supported environment configuration, never authored files.
