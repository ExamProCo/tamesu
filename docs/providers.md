# Providers and models

Tamesu 0.1 supports structured-text generation through six first-party US model
families. Provider adapters normalize structured output, token usage, request IDs, errors,
and estimated cost into the same run-artifact format.

| Manifest provider | Model family | Credential |
|---|---|---|
| `openai` | OpenAI GPT | `OPENAI_API_KEY` |
| `anthropic` | Anthropic Claude | `ANTHROPIC_API_KEY` |
| `gemini` | Google Gemini | `GEMINI_API_KEY` |
| `grok` | xAI Grok | `GROK_API_KEY` |
| `meta` | Meta-hosted models | `META_API_KEY` |
| `bedrock` | Amazon Nova through Bedrock | `AWS_BEARER_TOKEN_BEDROCK` or `BEDROCK_API_KEY` |

Only set credentials for providers you use. Credentials stay in the process environment;
Tamesu does not copy them into plans, logs, requests stored on disk, or run artifacts.

## Discover models

Use the built-in registry as the local source for provider ownership, effort settings,
pricing, and default output-token caps:

```sh
tamesu models
tamesu models --provider openai
```

The registry mirrors the American frontier-model scope used by Shiori. It is deliberately
not an aggregator catalog. A model identifier absent from the registry is still accepted
when `provider` names an implemented adapter, which makes private previews and newly
released models usable without a Tamesu release. Such models have unknown pricing and
receive less parameter validation.

Pricing is an operational estimate, not a billing guarantee. The command prints when the
bundled rates were last reviewed. Check the provider's current first-party pricing before
a material run.

## Configure a run

Provider and model are always declared together:

```yaml
runs:
  - model: claude-sonnet-5
    provider: anthropic
    arms:
      - basic-prompt
    parameters:
      effort: medium
      max_tokens: 512
```

Known model/provider mismatches and unsupported effort values fail linting. Parameters
merge in this order:

```text
defaults.parameters → arm.parameters → run.parameters
```

Portable parameters are:

| Parameter | Meaning |
|---|---|
| `max_tokens` | Maximum generated tokens; alias: `max_output_tokens` |
| `effort` | Provider reasoning/thinking level, validated for registered models |
| `temperature` | Provider sampling temperature |
| `thinking` | Explicit provider thinking configuration where supported |

Additional keys are provider-specific. OpenAI, Grok, and Anthropic pass non-conflicting
keys through to their request payloads. Gemini accepts `generation_config`; Meta accepts
chat-completions fields plus `structured_output`; Bedrock accepts `structured_output`.
For Meta, Tamesu maps portable `max_tokens` to Chat Completions'
`max_completion_tokens`. Muse reasoning and visible output share that allowance, so use a
larger cap or a lower `effort` when a response ends with `finish_reason: length`.

## Structured output behavior

- OpenAI and Grok use Responses API JSON Schema output.
- Anthropic uses Messages API structured output.
- Gemini uses `responseJsonSchema` with JSON response MIME type.
- Meta uses OpenAI-compatible chat-completions response formats.
- Amazon Nova uses a forced Bedrock tool call by default, with prompt-only JSON as an
  explicit fallback.

All adapters return text to the structured-text task, which then parses JSON and applies
the same local schema and exact-match scoring regardless of provider.

## Capabilities

A provider adapter declares identity and credentials; what it can do is a separate
capability, and each registered model lists its capabilities (`text_output`,
`structured_output`, `image_input`, `image_output`). Lint checks the selected model's
capability for the task, so one provider can serve text-only and image-capable models.

| Capability | Used by | Adapters |
|---|---|---|
| text generation | `structured_text` | all |
| image generation | `image_generation` | `meta` (`muse-image-1.0`), `openai` (`gpt-image-2`, `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`) |
| multimodal structured output | model judges | `meta` (`muse-spark-1.1`/`1.2`/`1.3`) |

### Image generation parameters

Common fields live under `parameters.image` (`size` as `WIDTHxHEIGHT`, `output_format` as
`png`/`jpeg`/`webp`, `count`, which must be `1`). Everything provider-specific lives under
`parameters.provider_options`, and each adapter validates its own options at plan time,
before credentials or any paid call:

| Provider | `provider_options` |
|---|---|
| `meta` | `reasoning_strength` (`high`/`low`), `tool_enablement` (`enable_image_search`, `enable_web_search`, `enable_shell` booleans) |
| `openai` | `quality`, `background`, `output_compression` (jpeg/webp), `moderation` |

An option valid for one provider is rejected by the other. Meta image models can use web
search, image search, and shell; an eval that needs fictional output should switch all
three off explicitly. They are not global defaults.

Adapters request inline base64 bytes with `n: 1` and never fetch provider URLs. The decoded
bytes are authoritative: a provider's claimed media type is recorded beside the detected
one if they disagree. Revised prompts and the requested and returned model IDs are
recorded. A provider refusal (an empty result or a moderation/safety `400`) becomes a
`safety_filtered` outcome rather than a failure; a plain `400` stays a non-retryable error.

### Image pricing

Pricing is a strategy chosen per model, not a field every provider shares:

- **flat per image** (`muse-image-1.0`, $0.01 per generated image): plans are exact, and
  failed or filtered requests are billed nothing, so confirmed refusals record `$0`;
- **token billed** (OpenAI `gpt-image-*`, $5 text input, $8 image input, $30 image output
  per million tokens): the recorded cost is exact from reported usage, but a plan cannot
  predict output tokens for a quality and size, so the estimate is reported as unknown;
- **quality/size matrix**: available for models that publish per-image prices.

An unpriced model or parameter combination stays unknown. It is never coerced to zero or
priced with another provider's strategy.

## Cost and budgets

`tamesu plan` reports recorded cost, expected remaining cost, and maximum additional
priced exposure for registered models. The maximum includes configured output-token caps
and all allowed retry attempts. Before execution, Tamesu refuses work when recorded known
cost plus that work's maximum priced exposure exceeds `budget_usd`.

Unknown model pricing is shown explicitly. It is never silently treated as zero, but it
cannot participate in the preflight ceiling. Actual provider-reported token usage is
normalized and the registry rates are used to record per-item and aggregate cost.

## Endpoint overrides

Endpoint overrides exist for integration testing and compatible first-party deployments:

```text
TAMESU_OPENAI_BASE_URL
TAMESU_ANTHROPIC_BASE_URL
TAMESU_GEMINI_BASE_URL
GROK_BASE_URL
META_BASE_URL
TAMESU_BEDROCK_BASE_URL
BEDROCK_REGION
```

Do not use an endpoint override to silently route an eval through a different provider.
Provider identity is part of the run fingerprint and must remain truthful.
