# Using Tamesu with coding agents

Tamesu includes two portable skills:

```text
skills/tamesu-eval-authoring/SKILL.md
skills/tamesu-eval-analysis/SKILL.md
```

The authoring skill helps create and validate an eval. The analysis skill turns completed
evidence into a cited `analysis.md` without treating the agent's interpretation as new
evidence. Neither depends on a Tamesu-specific integration or MCP server.

## Ask an agent to author an eval

Start the agent in a repository that contains the Tamesu project and ask it to use the
skill explicitly. Include the question you want the eval to answer and the decision the
result should inform. For example:

```text
Use the tamesu-eval-authoring skill to add a structured-text eval for our support-ticket
router. Compare the current prompt with a prompt that includes escalation rules. Use a
small synthetic dataset, deterministic scoring, and leave the eval in draft. Do not run
paid provider calls.
```

The agent should inspect existing cases and the local Tamesu docs, author only the needed
case, dataset, experiment, prompts, schema, and eval files, then run `tamesu lint` and
`tamesu plan`. A useful handoff explains:

- the question and intended decision;
- the single variable changed between arms;
- the dataset and expected-output strategy;
- the primary metric and diagnostic metrics;
- the expanded number of runs and provider calls;
- cost exposure and any execution blockers.

## Activate separately

New evals should remain drafts until their plan has been reviewed. After reviewing the
agent's files and `plan` output, activate the eval without making a provider call:

```sh
tamesu activate <case>/<experiment>/<eval>
```

Then run `tamesu plan` again. `Status: active` permits execution, while
`Execution blockers: none` confirms that credentials and other runtime prerequisites are
available. Activation is not authorization for an agent to incur provider cost; request a
probe or full run explicitly when that is what you want.

## Make the skill discoverable

Keep the files under `skills/` as the repository's canonical copies. For a harness that
uses a project-level skill directory, point it at the relevant canonical file or create
the vendor-supported link to it instead of maintaining a second copy.

If a harness does not discover repository skills automatically, reference the file in
your request:

```text
Read skills/tamesu-eval-authoring/SKILL.md and use it to author this eval: ...
```

This fallback works with coding agents that can read repository files even when they do
not implement automatic skill discovery.

## Ask an agent to analyze an eval

Generate the deterministic report first:

```sh
tamesu report <case>/<experiment>/<eval>
```

Then ask the coding agent to use the analysis skill:

```text
Read skills/tamesu-eval-analysis/SKILL.md and analyze
support-ticket-triage/decision-rules/prompt-ablation. Write analysis.md beside eval.yml.
Do not call providers or change evaluation evidence.
```

The agent should distinguish observed facts from interpretation, limitations, and next
steps. It should cite run IDs or repository-relative evidence paths, account for excluded
partial, failed, stale, or extra runs, and avoid causal claims the experiment did not
establish.

`analysis.md` is a derived view. It can be reviewed, edited, regenerated, or omitted
without changing the eval's metrics or completion state.

## Good request boundaries

Be explicit about the last authorized lifecycle step. Common requests are:

- “Author the eval and leave it in draft.”
- “Author, lint, plan, and activate it, but do not call providers.”
- “Run a two-item probe and explain state, score, failures, and cost.”
- “Run the full active eval, compare the arms, and generate the leaderboard.”

Separating these steps lets an agent help with experimental design without treating file
authoring as permission to spend money.
