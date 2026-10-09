# Single-shot Eval

This draft issues one paid call to each of Muse Spark 1.3, 1.2, and 1.1. The substantive
user prompt is identical across all calls and comes from the frozen dataset.

The primary native metric is `generation_failure_rate`; lower is better. Schema validity,
latency, and cost are diagnostics. These metrics cannot establish whether generated C is
correct. Use the experiment-level manual rubric to evaluate buildability, rendering
behavior, and visual quality.

Validate and inspect without making provider calls:

```sh
tamesu lint cases/c-rendering-engine
tamesu plan c-rendering-engine/muse-version-comparison/single-shot
```
