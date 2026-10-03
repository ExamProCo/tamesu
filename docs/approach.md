# The Tamesu approach: from question to evidence

An evaluation is not just a script that produces a score. It is a structured attempt to
answer a question well enough to make a decision.

The motivation for this approach is described in
[The White Rabbit Problem](white-rabbit-problem.md).

Tamesu therefore starts before model execution. Its workflow makes the reasoning behind
an evaluation visible, freezes the intended measurement before the formal run, and keeps
the resulting evidence connected to the question it was meant to answer.

```text
Decision
   ↓
Real-world question
   ↓
Testable hypothesis
   ↓
Controlled comparison
   ↓
Frozen measurement plan
   ↓
Execution evidence
   ↓
Interpretation
```

## Start with the decision

Before choosing a model or metric, ask:

> What decision could change because of this result?

“Evaluate our support-ticket classifier” is too broad. A more useful starting point is:

> Should we add explicit escalation rules to the production prompt?

That decision leads to a focused question: do the rules improve routing accuracy on the
ticket population we care about, without causing unacceptable failures or cost?

If no plausible result would change a decision, the evaluation probably needs a clearer
purpose.

## Turn the question into one testable hypothesis

State what you expect to change and why:

> Adding explicit escalation rules improves whole-record classification accuracy.

Then isolate that factor. A useful comparison changes the escalation instructions while
holding the dataset, model, output schema, parameters, repetitions, and scoring fixed.
If several important things change at once, the result may show that two bundles differ,
but it cannot explain which change mattered.

This is the role of an **experiment** in Tamesu: it names the hypothesis and makes each
comparison arm explicit.

## Choose evidence that can answer the question

The dataset defines the population represented by the result. It should include the
ordinary cases, boundaries, and failure modes that matter to the decision—not merely
examples that are easy to score.

The output and scoring contract should follow from the question:

- Prefer deterministic checks when correctness can be specified exactly.
- Use diagnostic metrics to explain the primary metric, not replace it with a wall of
  numbers.
- Keep execution failures distinct from wrong but usable model answers.
- Include repetitions when nondeterminism could materially affect the conclusion.
- Set a cost boundary before execution rather than explaining cost afterward.

Tamesu can make this contract explicit and reproducible. It cannot make an unrepresentative
dataset representative or turn a poorly chosen metric into a meaningful one.

## Freeze the measurement before the formal run

An **eval** is the reviewed measurement plan for an experiment. While its status is
`draft`, inspect and revise its dataset, arms, models, parameters, repetitions, metrics,
and budget.

Use `tamesu lint` to validate the contract and `tamesu plan` to see the concrete work,
provider calls, cost exposure, and blockers. When the plan represents the intended test,
`tamesu activate` marks the transition from authoring to execution.

Activation does not make the design scientifically correct, and it does not call a
provider. It creates a visible boundary: this is the plan against which formal evidence
will be judged. If the plan changes later, fingerprints prevent old evidence from silently
counting as though nothing changed.

## Separate a probe from evidence

A small probe answers an engineering question:

> Does this evaluation render prompts, call the provider, parse outputs, score results,
> and record evidence correctly?

It does not answer the research question. Tamesu therefore marks limited runs as
`partial` and excludes them from formal comparison even when every selected item
completed successfully.

Once the plumbing is sound, the full run produces the repetitions and item coverage owed
by the active eval.

## Read the result as evidence, not a verdict

Interpret four signals separately:

| Signal | What it tells you |
|---|---|
| Run state | Whether execution satisfied the declared run |
| Item completion | Whether provider calls produced usable terminal results |
| Quality metrics | How those usable results scored against the contract |
| Cost and timing | What the observed execution consumed |

A complete run can have a poor score. A partial run can have excellent answers for the
items it reached. Neither fact should be hidden inside a single green check mark.

Compare compatible repetitions, inspect diagnostic metrics and failed items, and make a
claim no broader than the dataset and design support. Record important limitations beside
the experiment so another reader can understand not only what happened, but what the
result does and does not justify.

## How the hierarchy supports the approach

The Tamesu hierarchy exists to preserve this chain of reasoning:

| Level | Its methodological role |
|---|---|
| Case | Defines the real-world problem and reusable evidence base |
| Experiment | States the hypothesis and isolates the factor being changed |
| Eval | Freezes one reviewable measurement plan |
| Run | Records one execution and its evidence |

Next, read [Core concepts](concepts.md) for the detailed model or follow the
[text-eval tutorial](tutorial.md) to see the workflow end to end.
