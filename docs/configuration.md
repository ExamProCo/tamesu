# Configuration

Tamesu uses layered environment configuration so credentials can be shared globally,
isolated per project, supplied by a shell or CI system, or explicitly overridden for one
invocation. It does not require a configuration service or store secrets in run artifacts.

## Configuration precedence

The CLI resolves values in this order, from lowest to highest precedence:

| Priority | Source | Typical use |
|---:|---|---|
| 1 | `~/.tamesu/.env` | Personal defaults shared by all Tamesu projects |
| 2 | `<workspace>/.env` | Defaults shared by projects in the enclosing Git workspace |
| 3 | `<workspace>/.tamesu/.env` | Tamesu-specific workspace credentials and endpoints |
| 4 | `<project>/.env` | Conventional project-local environment file |
| 5 | `<project>/.tamesu/.env` | Tamesu-specific project credentials and endpoints |
| 6 | Existing process environment | Shell exports, CI secrets, and container configuration |
| 7 | `--config-dir <directory>/.env` | Explicit per-invocation override |

A higher-priority value replaces the same key from every lower layer. Missing files in the
global, workspace, and project locations are normal. An explicitly selected configuration
directory must exist and contain `.env`; otherwise the command fails before doing work.

The workspace is the nearest current or parent directory containing `.git`. If the
workspace and project are the same directory, each file is loaded only once.

The `TAMESU_CONFIG_DIR` process variable can select an override directory when a command
line flag is inconvenient. `--config-dir` wins when both are present.

## Project discovery

Tamesu identifies the nearest current or parent directory containing `cases/` as the
project root. It also identifies the nearest enclosing Git workspace so a repository-level
configuration can be shared by nested Tamesu projects. A nested project's values override
workspace defaults.

This distinction matters for the bundled example. Its project root is:

```text
examples/support-ticket-classification/
```

The enclosing Tamesu repository is its Git workspace. Therefore any of these provide
`META_API_KEY` to the example:

```text
~/.tamesu/.env
<tamesu-repository>/.env
<tamesu-repository>/.tamesu/.env
examples/support-ticket-classification/.env
examples/support-ticket-classification/.tamesu/.env
```

If the key is already in the Tamesu repository's top-level `.env`, no flag is needed:

```sh
tamesu plan support-ticket-triage/decision-rules/prompt-ablation
```

Use `--config-dir` only to select a different configuration directory explicitly. Global
options must appear before the subcommand.

## Dotenv format

Environment files use a deliberately small dotenv syntax:

```dotenv
# Comments and blank lines are allowed.
META_API_KEY=your-key
BEDROCK_REGION=us-east-1
export GROK_API_KEY="a quoted value"
TAMESU_OPENAI_BASE_URL=https://api.openai.com/v1 # trailing comment
```

Supported forms include unquoted values, single- or double-quoted values, empty values,
optional `export`, and comments. Variable interpolation and command substitution are not
performed. Files are UTF-8.

## Provider credentials

Set only the credentials required by models in the current eval:

| Provider | Environment variable |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` |
| xAI/Grok | `GROK_API_KEY` |
| Meta | `META_API_KEY` |
| Amazon Bedrock | `AWS_BEARER_TOKEN_BEDROCK` or `BEDROCK_API_KEY` |

Provider endpoint and regional overrides are documented in
[Providers and models](providers.md).

## Recommended layouts

Use global configuration when projects share one account:

```text
~/.tamesu/
└── .env
```

Use project configuration when provider usage, billing, or external account tracking must
remain isolated:

```text
my-eval-project/
├── .tamesu/
│   └── .env
├── .gitignore
└── cases/
```

The project `.gitignore` should contain:

```gitignore
.env
.tamesu/
```

Use an explicit override for temporary credentials, a separate billing account, or a
controlled CI environment:

```sh
tamesu --config-dir /secure/configs/customer-a plan <eval-id>
tamesu --config-dir /secure/configs/customer-a run <eval-id>
```

## Security behavior

- Files are read into the current Tamesu process; they are never copied into manifests.
- The CLI never prints credential values.
- Provider request headers are not stored in run logs.
- The logging layer recursively redacts configured credential values and sensitive fields.
- Raw model outputs are evidence and are not scrubbed; do not place secrets in prompts.

Configuration files should be readable only by the intended user. Tamesu does not manage
an operating-system keychain or change file permissions.

## Troubleshooting

Run `tamesu plan <eval-id>` before paid execution. Its execution-blocker section reports
missing credential names without revealing values.

If a key is unexpectedly missing:

1. Confirm which directory is the Tamesu project root—the nearest directory containing
   `cases/`.
2. Confirm the enclosing Git workspace—the nearest parent containing `.git`.
3. Put `.env` in either root, or use `.tamesu/.env` beneath either one.
4. If the file is elsewhere, pass its containing directory with `--config-dir` before the
   subcommand.
5. Confirm the variable name matches the provider table above.
6. Remember that an explicitly selected `.env` overrides even an exported shell value.

When embedding Tamesu as a Python library rather than using the CLI, call
`tamesu.environment.load_environment(project_root)` explicitly. Automatic loading is a
CLI behavior.
