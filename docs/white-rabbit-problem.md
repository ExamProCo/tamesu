# The White Rabbit Problem

> An agent workload appears close enough to working that developers keep chasing it, but
> each iteration takes them deeper into complexity without making feasibility any clearer.

![Alice following the White Rabbit across a large unknown](the-white-rabbit-problem.jpg)

The prototype looks 70% complete. The remaining 30% becomes an expanding research
project. New prompts, tools, retries, memory, routing rules, and recovery logic make the
system larger, but not necessarily closer to dependable.

Eventually, the team runs out of runway without knowing whether success was days or
months away. It has no defensible estimate of cost, no clear account of the remaining
failure modes, and little evidence that another organization can use to decide whether to
continue, narrow, pause, or stop the work.

This is the **White Rabbit Problem**.

## Seventy percent is not a distance

“Seventy percent complete” sounds as though the remaining work is smaller than the work
already finished. For agent systems, that is often an illusion.

A prototype may complete seven of ten demonstrations while the other three expose
different missing capabilities: recovering from a bad tool result, maintaining state over
a long trajectory, recognizing an impossible task, or choosing safely under ambiguity.
Fixing one failure can introduce another or increase latency and cost enough to make the
whole workload impractical.

The visible success rate describes observed behavior on a particular sample. It does not
measure the engineering or research distance to a reliable system.

```text
Promising prototype
        ↓
Patch the visible failure
        ↓
Expose a deeper failure mode
        ↓
Add more prompts, tools, state, and retries
        ↓
Increase complexity and cost
        ↓
Still do not know whether the workload is feasible
```

The rabbit always appears one more iteration away.

## Why ordinary iteration makes the problem worse

Fast iteration is useful while discovering a problem, but it is a poor substitute for an
experiment. Without a stable question and measurement plan, each change also changes what
“working” means.

Common warning signs include:

- demonstrations are memorable, but the complete set of attempts is not preserved;
- the prompt, model, tools, dataset, and retry behavior change together;
- failures are patched individually without measuring whether a failure class improved;
- a higher score hides increased latency, retries, or provider cost;
- successful cases are rerun while difficult or failed cases quietly leave the sample;
- nobody can distinguish a regression from model variance or a changed input;
- progress is reported as confidence or percentage complete rather than evidence;
- the team cannot state an exit criterion for the next iteration.

This produces activity without a reliable direction. The system becomes more elaborate
while the central uncertainty—whether it can satisfy the workload within real
constraints—remains unanswered.

## What a team actually needs to learn

The useful question is not simply, “Can the agent succeed?” A prototype has usually
proved that it can succeed sometimes.

The team needs evidence about a bounded workload:

- What success rate and failure profile does the current baseline achieve?
- Which classes of work are reliable, unreliable, or unsupported?
- Which single intervention materially improves the result?
- Does that improvement hold across repeated runs and representative inputs?
- What does an attempt and a successful outcome cost?
- Which failures are retryable, structural, or caused by the evaluation itself?
- What quality, latency, and cost thresholds would make deployment worthwhile?
- What result would justify narrowing or ending the investigation?

These questions do not eliminate uncertainty. They make it possible to decide what the
next unit of uncertainty is worth.

## Tamesu's response

Tamesu turns open-ended iteration into a sequence of reviewable experiments:

1. Define the real-world workload and the decision the evidence must support.
2. Record the current system as a baseline rather than relying on the latest demo.
3. State one hypothesis about what limits performance.
4. Change one deliberate factor between experimental arms.
5. Freeze the dataset, parameters, repetitions, success metrics, and budget before the
   formal run.
6. Preserve every compatible output, failure, retry, score, cost, and provenance record.
7. Compare the result with the baseline and decide whether to continue, narrow, pause, or
   stop.

Its hierarchy preserves the reasoning behind that process:

```text
Case → Experiment → Eval → Run
```

- The **case** defines the workload and real-world problem.
- The **experiment** isolates a hypothesis and the factor being changed.
- The **eval** freezes the measurement plan and cost boundary.
- Each **run** records what happened as inspectable evidence.

The filesystem then separates authored plans, observed evidence, and derived reports.
Fingerprints stop changed prompts, inputs, parameters, schemas, or evaluators from
silently inheriting credit from older runs. Append-only event logs preserve attempts,
retries, failures, usage, costs, and artifacts instead of compressing the investigation
into one score.

| Uncertainty | Evidence Tamesu preserves |
|---|---|
| What were we trying to learn? | The case, experiment, and eval question |
| What changed? | Explicit arms and resolved run configuration |
| Was the comparison still compatible? | Specification and content fingerprints |
| What failed and why? | Item results and append-only execution events |
| What did it consume? | Usage, timing, retries, and recorded cost |
| Can someone else continue the work? | Portable manifests, inputs, outputs, reports, and provenance |

## A failed experiment can be progress

If a controlled change does not improve the primary result, the experiment has still
reduced uncertainty. The team can reject that intervention without folding it into an
ever-growing prototype.

If the system works only for a well-defined subset, the organization can narrow the
product claim. If quality improves but cost exceeds the operating threshold, the team can
investigate cost directly. If several plausible interventions fail to move the baseline,
stopping becomes an evidence-backed decision rather than an exhausted guess.

The goal is not to make every agent workload succeed. The goal is to learn whether and
where it can succeed before the pursuit consumes the available runway.

## What Tamesu cannot promise

Tamesu cannot calculate that success is exactly twelve days away. It cannot prove that an
untested input distribution will behave like the dataset, rescue a weak experimental
design, or make an inherently unreliable workload feasible.

It can replace an unstructured chase with bounded questions. It can show what was tried,
what changed, what improved, what regressed, what failed, and what it cost. That record
gives a developer, research lead, or organization something concrete to carry forward—even
when the correct decision is not to follow the rabbit any farther.

Next, read [The Tamesu approach](approach.md) to turn a product decision into a controlled
evaluation, or follow the [tutorial](tutorial.md) to run that workflow on a small text
example.
