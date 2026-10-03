# Tamesu

![./docs/logo-new.png](./docs/logo.png)

Tamesu is a file-based evaluation framework for reproducible AI experiments. It keeps
evaluation plans, run artifacts, scores, costs, and provenance inspectable on disk—without
requiring a database or hosted service.

> **Status:** Tamesu 0.1 implements structured-text evals with deterministic scoring and
> first-party adapters for OpenAI, Anthropic, Google Gemini, xAI/Grok, Meta, and Amazon
> Nova through Bedrock. Image tasks, model judges, and human review remain planned.

## Why I built Tamesu

I built Tamesu after finding that many evaluation frameworks were difficult to learn,
required hosted services or databases, and hid important behavior behind large abstraction
layers. Tamesu is designed around a few deliberate choices:

- **Files instead of infrastructure.** Plans, outputs, scores, costs, and provenance live
  in ordinary files that can be inspected, diffed, and versioned without a database or
  hosted control plane.
- **A small, hackable codebase.** The implementation should remain understandable enough
  to modify for a particular experiment without first learning an extensive framework.
- **Rich execution logging.** Every run produces a durable, append-only event stream for
  provider attempts, retries, failures, artifacts, usage, and cost.
- **Low-level provider APIs.** Adapters use direct HTTP interfaces and preserve provider
  behavior instead of depending on a deep SDK or orchestration stack.

## Install

Tamesu requires Python 3.12 or newer:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tamesu --help
```

Tamesu organizes evaluation work into four levels:

```text
Case → Experiment → Eval → Run
```

- A **case** defines the broader problem and shared data.
- An **experiment** isolates a hypothesis.
- An **eval** freezes the question and measurement plan.
- A **run** preserves one execution and its evidence.

## Documentation

- [Tutorial: run your first eval](docs/tutorial.md)
- [Core concepts](docs/concepts.md)
- [Repository and file structure](docs/file-structure.md)
- [Manifest reference](docs/manifests.md)
- [Command-line interface](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Providers and models](docs/providers.md)
- [Logging and execution evidence](docs/logging.md)
- [Contributor development guide](docs/development.md)

## Example

- [Support-ticket classification](examples/support-ticket-classification/README.md)

The current design is captured in the
[evaluation framework implementation plan](plans/EVALUATION-FRAMEWORK-IMPLEMENTATION.md).
