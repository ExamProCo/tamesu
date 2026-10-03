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

## Structured output behavior

- OpenAI and Grok use Responses API JSON Schema output.
- Anthropic uses Messages API structured output.
- Gemini uses `responseJsonSchema` with JSON response MIME type.
- Meta uses OpenAI-compatible chat-completions response formats.
- Amazon Nova uses a forced Bedrock tool call by default, with prompt-only JSON as an
  explicit fallback.

All adapters return text to the structured-text task, which then parses JSON and applies
the same local schema and exact-match scoring regardless of provider.

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
