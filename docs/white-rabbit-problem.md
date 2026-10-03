# The White Rabbit Problem

> **Close enough to keep chasing. Too uncertain to plan.**

![Alice following the White Rabbit across a large unknown](the-white-rabbit-problem.jpg)

An agent prototype starts working just well enough to feel almost finished. The happy path
works. A few difficult cases look fixable. The next breakthrough always seems one prompt,
tool, or retry strategy away.

A developer might call it “70% complete,” but that is not a measurement. It is a feeling
of proximity.

Then the remaining work begins to expand. One failure needs better planning. Another needs
memory. A third needs recovery logic, another tool, or a different model. Each fix adds
complexity and exposes another edge case. The prototype grows, but feasibility does not
become any clearer.

This is the **White Rabbit Problem**.

## Always one iteration away

The first working demonstrations prove that the agent *can* succeed. They do not show that
it can succeed reliably, at an acceptable cost, across the workload that actually matters.

The apparent final stretch is not necessarily a backlog of known engineering tasks. It
may be an open-ended research problem with no known distance to the finish line.

That uncertainty is easy to ignore because every partial success makes the destination
feel close. The team keeps following:

```text
Promising result
      ↓
Fix the visible failure
      ↓
Discover a deeper failure
      ↓
Add more complexity
      ↓
Feel close enough to try again
```

Eventually, the project runs out of time, money, or organizational patience. The team
still cannot say whether success was days away, months away, or impossible under the
current constraints.

## The real loss is not knowing

An unsuccessful prototype is not necessarily a failure. The larger failure is reaching
the end without evidence another team can use.

Can the organization answer:

- Which parts of the workload are already reliable?
- Which failure modes are blocking feasibility?
- Did the latest intervention improve the system or merely change it?
- What does an attempt—and a successful outcome—cost?
- Is progress holding across representative inputs and repeated runs?
- What result would justify continuing, narrowing the scope, or stopping?

Without those answers, the work cannot be planned or handed off. It remains a collection
of demos, patches, and developer intuition.

## Replace the chase with evidence

Tamesu is designed to turn that open-ended pursuit into a series of bounded experiments.

- Establish a baseline instead of relying on the latest successful demonstration.
- State one hypothesis and change one deliberate factor at a time.
- Decide the dataset, success measure, repetitions, and cost boundary before the formal
  run.
- Preserve outputs, failures, retries, scores, costs, and provenance as inspectable
  evidence.

Tamesu does not calculate how many days remain or guarantee that the workload is feasible.
It makes each next step answerable: did this change move the result, for which cases, and
at what cost?

A positive result can justify another experiment. A result that works only for a narrower
set of cases can support a smaller, honest product claim. A negative result can rule out
an approach before it becomes another permanent layer in the prototype.

The goal is not to make every rabbit catchable. It is to know whether following this one
is still justified—and to leave behind enough evidence for the organization to decide
what happens next.

Read [The Tamesu approach](approach.md) to turn that decision into a controlled evaluation,
or follow the [tutorial](tutorial.md) to see the workflow in practice.
