# Tamesu

![./docs/logo.png](./docs/logo.png)

Tamesu is a file-based evaluation framework for reproducible AI experiments. It keeps
evaluation plans, run artifacts, scores, costs, and provenance inspectable on disk—without
requiring a database or hosted service.

> **Status:** Tamesu 0.1 implements structured-text evals with deterministic scoring and
> first-party adapters for OpenAI, Anthropic, Google Gemini, xAI/Grok, Meta, and Amazon
> Nova through Bedrock. Image tasks, model judges, and human review remain planned.

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
- [Providers and models](docs/providers.md)
- [Contributor development guide](docs/development.md)

## Example

- [Support-ticket classification](examples/support-ticket-classification/README.md)

The current design is captured in the
[evaluation framework implementation plan](plans/EVALUATION-FRAMEWORK-IMPLEMENTATION.md).
