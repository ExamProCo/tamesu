# Where Tamesu fits in the evaluation landscape

Tamesu is not the first framework to run locally, use configuration files, record logs,
or preserve reproducible tasks. Those are established capabilities across the evaluation
ecosystem.

Its distinction is the workflow those pieces serve: Tamesu organizes an evaluation as a
reproducible investigation that moves from a real-world question to a controlled
experiment, a frozen measurement plan, and inspectable execution evidence.

## Different tools start from different questions

The categories overlap, and every project continues to evolve. This comparison describes
their center of gravity rather than claiming that a tool can do only one thing.

| Tool or category | Typical starting point | Center of gravity | Choose it when |
|---|---|---|---|
| [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md) | “How does this model perform on these tasks?” | Standardized model benchmarking and reusable task definitions | Benchmark coverage and comparison with established model results are the priority |
| [Promptfoo](https://github.com/promptfoo/promptfoo/blob/main/site/docs/usage/command-line.md) | “Which prompt, model, or configuration performs best?” | Configuration-driven testing across prompt and provider matrices | You want rapid local testing of prompts and configurations |
| [DeepEval](https://deepeval.com/docs/evaluation-local-backend-storage) | “Does this LLM application or agent pass its quality tests?” | Metrics and test cases for LLM application and agent evaluation | You want application-oriented tests built around reusable metrics |
| [Inspect](https://github.com/UKGovernmentBEIS/inspect_ai) | “How should this model or agent capability be evaluated?” | A comprehensive programmable framework for rigorous model and agent evaluations | You need mature task composition, solvers, tools, scorers, and an extensive ecosystem |
| Observability platforms | “What is happening in my production AI system?” | Traces, datasets, dashboards, annotation, monitoring, and team workflows | Production operations and hosted collaboration are central requirements |
| **Tamesu** | “What controlled experiment will answer this real-world question?” | Reproducible AI experiments with file-based plans and inspectable evidence | You want the question, hypothesis, plan, execution, and provenance to travel together in a small repository |

Local execution is not the dividing line. Promptfoo, DeepEval, Inspect, and other tools
can support local workflows. Configuration files and stored logs are not unique either.
Tamesu combines them with an opinionated experimental hierarchy:

```text
Case → Experiment → Eval → Run
```

That hierarchy distinguishes the broader problem from the hypothesis, the measurement
plan from an execution, and authored intent from observed evidence.

## Choose Tamesu when

Tamesu is a good fit when:

- you are investigating a specific hypothesis about AI behavior;
- the comparison should change one deliberate factor at a time;
- the measurement plan should be reviewed before paid execution;
- another person should be able to clone a repository and inspect what was asked, changed,
  run, and observed;
- stale evidence must not silently count after a prompt, dataset, schema, parameter, or
  evaluator changes;
- provider attempts, retries, failures, usage, cost, artifacts, and provenance need a
  durable audit trail;
- a small codebase and direct provider APIs are advantages because the experiment may
  require modification.

The result is closer to a lightweight experimental research system than a general-purpose
LLM testing or observability platform.

## Choose another tool when

Tamesu is intentionally not optimized for every evaluation job. Prefer another tool when
your primary need is:

- running a large catalog of established academic benchmarks;
- production tracing, online monitoring, annotation queues, or hosted team dashboards;
- managing prompts as production application assets;
- a broad library of ready-made RAG, safety, or agent metrics;
- a mature plugin ecosystem for complex multi-turn agent environments;
- an organization-wide evaluation service with database-backed search and collaboration.

Those are legitimate product categories, but pursuing all of them would erase the small,
inspectable boundary that makes Tamesu useful.

## The product boundary

Tamesu should remain focused on designing, running, and preserving reproducible AI
experiments. Its core responsibilities are:

- expressing the question, hypothesis, controlled arms, and measurement plan;
- making work, credentials, and cost exposure visible before execution;
- producing immutable, fingerprinted run evidence;
- keeping generation, scoring, and derived reports distinct;
- making failures and incomplete work visible;
- leaving the complete record understandable without a hosted account.

Features such as production monitoring, prompt management, synthetic-data platforms,
general annotation systems, and hosted collaboration should only enter the project if
real usage shows they support that core workflow without replacing it.

Read [The Tamesu approach](approach.md) for the methodology behind this position and
[Core concepts](concepts.md) for its concrete repository model.
