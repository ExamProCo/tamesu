# Tamesu

![./docs/logo-new.png](./docs/logo-new.png)

Tamesu is a small, file-based framework for designing, running, and preserving
reproducible AI experiments.

> **Treat an AI evaluation as an experiment with inspectable evidence—not as a dashboard
> metric.**

Many evaluation workflows begin with a dataset, a task, or a list of models. Tamesu begins
with a real-world question, isolates the factor being tested, freezes a measurement plan,
and preserves what happened as evidence in ordinary files.

I built it to address what I call
[the White Rabbit Problem](docs/white-rabbit-problem.md): an agent workload can appear
close to working while each iteration makes its feasibility less clear.

> **Status:** Tamesu 0.1 implements structured-text evals with deterministic scoring and
> first-party adapters for OpenAI, Anthropic, Google Gemini, xAI/Grok, Meta, and Amazon
> Nova through Bedrock. Cases can be rendered as static studies, packaged as reproducible
> archives, and published through an inspectable static registry. Image tasks, model
> judges, and human review remain planned.

## Why Tamesu exists

The ecosystem already has capable local test runners, benchmark harnesses, and full
evaluation platforms. Tamesu is for a narrower problem: organizing reproducible
investigations into uncertain AI behavior.

It is designed around a few deliberate choices:

- **Experiments instead of disconnected scores.** The structure makes the question,
  hypothesis, controlled comparison, measurement plan, and resulting evidence explicit.
- **Plans are separate from evidence.** Manifests declare what is owed; only complete,
  compatible run artifacts demonstrate what happened.
- **Files instead of infrastructure.** Plans, outputs, scores, costs, and provenance live
  in ordinary files that can be inspected, diffed, and versioned without a database or
  hosted control plane.
- **A small, hackable codebase.** The implementation should remain understandable enough
  to modify for a particular experiment without first learning an extensive framework.
- **Rich execution logging.** Every run produces a durable, append-only event stream for
  provider attempts, retries, failures, artifacts, usage, and cost.
- **Low-level provider APIs.** Adapters use direct HTTP interfaces and preserve provider
  behavior instead of depending on a deep SDK or orchestration stack.

Tamesu is not trying to become a hosted observability platform, prompt-management suite,
or catalog of hundreds of benchmarks. See [Where Tamesu fits](docs/landscape.md) for the
tradeoffs.

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

- [The White Rabbit Problem](docs/white-rabbit-problem.md)
- [The Tamesu approach: from question to evidence](docs/approach.md)
- [Core concepts](docs/concepts.md)
- [Where Tamesu fits in the evaluation landscape](docs/landscape.md)
- [Tutorial, Part 1: run your first eval](docs/tutorial.md)
- [Tutorial, Part 2: present, package, and ship a case](docs/tutorial-presentation-packaging-shipping.md)
- [Repository and file structure](docs/file-structure.md)
- [Manifest reference](docs/manifests.md)
- [Command-line interface](docs/cli.md)
- [Presentation and case package contract](docs/packages.md)
- [Tamesu Showcase registry](https://github.com/ExamProCo/tamesu-showcase)
- [Configuration](docs/configuration.md)
- [Using Tamesu with coding agents](docs/coding-agents.md)
- [Providers and models](docs/providers.md)
- [Logging and execution evidence](docs/logging.md)
- [Contributor development guide](docs/development.md)

## Example

- [Support-ticket classification](examples/support-ticket-classification/README.md)
