# Decision-rules experiment

## Hypothesis

Explicit category, priority, and escalation rules improve exact-match classification over
a short prompt that only lists the allowed values.

## Arms

- `basic-prompt` asks for the three required fields and lists their allowed values.
- `decision-rules` adds explicit routing definitions and precedence rules.

Both arms use the same user template, output schema, model settings, dataset, and scorer.
The system prompt is the only intentional difference.

## Interpretation

The primary metric is whole-record exact-match rate. A result counts as correct only when
all three fields match the expected answer.

Secondary field-level metrics help distinguish category, priority, and escalation errors.
Because the dataset is small and synthetic, this example demonstrates the workflow rather
than supporting a production model-selection claim.
