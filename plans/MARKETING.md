Is there a geniue need for my AI evaluation framework or did I just reivent the wheel?
https://github.com/ExamProCo/tamesu
ALl I was seeing was generic AI evaluation frameworks that are often hosted or tied to a cloud offering with complex setup like databases..

Yes — there is a genuine need here. But I would **change how you describe what Tamesu is solving**, because “simple local AI eval framework” alone is increasingly crowded.

After looking through the repo and comparing it with the current eval ecosystem, I think you have **reinvented several individual pieces, but not necessarily the combination or workflow**.

The strongest part of Tamesu is actually this:

> **Case → Experiment → Eval → Run**

That is more interesting to me than “file-based.”

Tamesu explicitly separates the real-world question, the hypothesis, the frozen measurement plan, and an individual execution. It then treats the run artifacts as immutable evidence, distinguishes plans from evidence from derived reports, fingerprints the inputs/evaluators, separates generation from scoring, and preserves provenance. [GitHub](https://github.com/ExamProCo/tamesu/blob/main/docs/concepts.md)

That maps unusually well to the kind of research work you've been describing.

### Where you *did* reinvent the wheel

Several existing frameworks can already run without some giant hosted platform.

DeepEval explicitly calls itself local-first. It can now store runs as plain JSON or SQLite without using Confident AI. [DeepEval](https://deepeval.com/docs/evaluation-local-backend-storage?utm_source=chatgpt.com)

Promptfoo is fundamentally CLI/config driven and can run locally as well. [GitHub](https://github.com/promptfoo/promptfoo/blob/main/site/docs/usage/command-line.md?plain=1\&utm_source=chatgpt.com)

OpenAI Evals records local JSONL event logs and defines evals through YAML/JSONL. [GitHub](https://github.com/openai/evals/blob/main/docs/build-eval.md?utm_source=chatgpt.com)

EleutherAI's `lm-evaluation-harness` uses shareable YAML configurations specifically for reproducibility. [GitHub](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md?utm_source=chatgpt.com)

And Inspect is a serious local evaluation framework from the UK AI Security Institute with model grading, tools, multi-turn evaluation and hundreds of prebuilt evals. [GitHub](https://github.com/UKGovernmentBEIS/inspect_ai?utm_source=chatgpt.com)

So I would **not** make this the main pitch:

> Unlike other frameworks, Tamesu runs locally and doesn't need a database.

That's too easily challenged now.

### Where I think Tamesu has something

Most eval frameworks think primarily in terms of something like:

**dataset → tests/tasks → models → scores**

Your model is closer to:

**research question → controlled experiment → preregistered measurement → execution evidence → conclusion**

That distinction matters.

Look at what you've actually designed into Tamesu. An experiment identifies the factor you're changing and explicitly warns against changing multiple factors in an ambiguous comparison. An eval freezes the measurement plan before execution. A run preserves the exact resolved configuration and enough evidence to reconstruct what happened. A previous run only counts when specification and content fingerprints still match. [GitHub](https://github.com/ExamProCo/tamesu/blob/main/docs/concepts.md)

That's much closer to a **lightweight experimental research system** than a conventional LLM test runner.

And your logging model reinforces that. Every run has an append-only event stream containing provider attempts, retries, errors, costs and artifacts, while the actual source files remain inspectable. [GitHub](https://github.com/ExamProCo/tamesu/blob/main/docs/logging.md)

That has value for something like your Pokémon experiment because you can ask:

> "Does decomposition strategy B make this agent better?"

instead of:

> "How well does GPT-X score on my Pokemon benchmark?"

Those sound superficially similar, but they're not.

The first requires experimental discipline.

---

I think your actual competitive landscape looks roughly like this:

| Framework | Center of gravity |
|---|---|
| lm-eval-harness | Benchmarking **models** |
| OpenAI Evals | Defining/running **model evals** |
| Promptfoo | Testing **prompts/configurations** |
| DeepEval | Testing **LLM applications/agents** |
| Inspect | Rigorous **model/agent evaluations** |
| **Tamesu** | Conducting reproducible **AI experiments** |

That final description is where I would push it.

There's still overlap—especially with Inspect. Inspect is probably the project I'd study most carefully because it is substantially closer philosophically to what you're doing than the SaaS observability platforms. But Inspect is also a much larger Python framework with an extensive ecosystem and more than 200 packaged evaluations. [GitHub](https://github.com/UKGovernmentBEIS/inspect_ai?utm_source=chatgpt.com)

Your "small enough to understand and hack" constraint is therefore legitimate rather than cosmetic.

### And there's another important difference

You aren't trying to build another LangSmith.

Your README explicitly says that ordinary files are the source of truth and that the repository should remain understandable without even running Tamesu. [GitHub](https://github.com/ExamProCo/tamesu)

I think that's a **very useful design constraint for community research**.

Imagine you publish:

```text
cases/
  pokemon-agent-navigation/
    case.yml
    datasets/
    experiments/
      decomposition-strategy/
        experiment.yml
        evals/
          eval-001/
            eval.yml
            runs/
```

Someone doesn't need your dashboard account.

They don't need your PostgreSQL instance.

They don't need your LangSmith project.

They clone the repo and can inspect **what you asked, what you changed, what you ran, and what happened.**

That's particularly suited to the ExamPro Community AI Research Group idea.

---

There is one danger, though.

If Tamesu gradually accumulates:

- traces
- dashboards
- RAG metrics
- dozens of judge types
- synthetic dataset generation
- production monitoring
- hosted collaboration
- annotation queues
- prompt management
- OpenTelemetry
- CI management

then you *will* just rebuild DeepEval/Promptfoo/Inspect badly.

I would resist that extremely hard.

Keep the product boundary something like:

> **Tamesu is a small, file-based framework for designing, running, and preserving reproducible AI experiments.**

And perhaps make the differentiator even stronger:

> **Tamesu treats an AI evaluation as an experiment with inspectable evidence, not as a dashboard metric.**

That immediately explains why it exists.

The irony is that your best feature may not be any particular piece of Python code you've written. It may be the **opinionated experimental methodology** embedded into the filesystem and CLI.

That's much harder to dismiss as "yet another eval framework."

So: **you did reinvent some wheels. But I don't think you reinvented the car.**

You discovered that the current tooling tends to optimize around *running evals*, while the problem you've actually been wrestling with is **organizing reproducible investigations into uncertain AI behavior**.

Tamesu makes considerably more sense when framed around that problem.