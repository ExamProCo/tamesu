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

The example uses Meta's `muse-spark-1.2` model in `eval.yml`. Replace the provider and
model together if you want to exercise another implemented provider adapter.
Muse is a reasoning model, so this simple classifier uses minimal reasoning and reserves
enough output tokens for both private reasoning and the visible JSON response.

Provide the example's credential in `.env` or `.tamesu/.env` here, in the enclosing Git
workspace, in the shell, or through Tamesu's global or explicit configuration layers. For
this manifest:

```dotenv
META_API_KEY=your-key
```

The included `.env.example` can be copied to `.env` and filled in locally.
When running this bundled example, a `.env` at the Tamesu repository root is inherited
automatically.

See the [configuration guide](../../docs/configuration.md) for precedence and alternatives.

Generated runs, call logs, and leaderboards are ignored in this example so trying the
tutorial does not add disposable evidence to Git. In a real Tamesu project, choose an
artifact-retention policy appropriate for the sensitivity and reproducibility needs of
the evaluation.
