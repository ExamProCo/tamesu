# Support-ticket classification example

This is a small, text-only Tamesu project for the
[first eval tutorial](../../docs/tutorial.md). It asks whether explicit decision rules
improve a model's ability to classify synthetic support tickets.

The example is intentionally self-contained:

- ticket text and expected answers are stored inline in `dataset.yml`;
- both prompt arms are plain Markdown files;
- outputs are validated against a JSON Schema;
- all quality metrics are deterministic;
- no model judge, human review, or binary asset is required.

After installing Tamesu from the repository root, start with:

```sh
cd examples/support-ticket-classification
tamesu lint cases/support-ticket-triage
tamesu plan support-ticket-triage/decision-rules/prompt-ablation
```

The example uses an OpenAI model in `eval.yml`. Replace the provider and model together if
you want to exercise another implemented provider adapter.
