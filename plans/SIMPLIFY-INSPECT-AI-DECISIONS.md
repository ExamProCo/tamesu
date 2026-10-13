# Direction for a simpler Tamesu

Andrew's decisions following the test report and its seven questions.

## Scope

Tamesu is currently overengineered. Make it a thin documentation, results, and review layer over Inspect AI.

1. **Use Inspect AI as the only test runner.** Remove the native runner rather than maintaining two engines.
2. **Focus on a portable package of experiments and results.** The package should carry the experiment definition and its results so they can be shared and reviewed elsewhere. Publishing and hosted sites are outside the immediate focus. Keep or simplify existing packaging commands according to what this portable workflow needs.
3. **Keep a robust review layer.** Human review must be available as an option. Design the layer to support different review types, including generated images, without making every experiment require human review. Investigate which automated judging and review patterns Inspect AI already supports before adding equivalent machinery to Tamesu.
4. **Remove the required lifecycle.** Drop the draft → active → completed workflow; it currently adds little value.
5. **Reduce the starter scaffold to three files.** Add further files when an experiment needs them.
6. **Simplify the docs provisionally.** Start with one README and one tutorial, with further material in reference documentation. Revisit this if it makes the docs harder to use.

These are direction decisions, not completed implementation work. This repository contains the assessment and evidence, rather than the Tamesu implementation.
