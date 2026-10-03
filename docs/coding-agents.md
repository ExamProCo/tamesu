# Using Tamesu with coding agents

Tamesu includes a portable authoring skill that gives coding agents the project model,
safety boundaries, and validation workflow needed to create or revise an eval:

```text
skills/tamesu-eval-authoring/SKILL.md
```

The skill is intentionally a single Markdown file with YAML frontmatter. It does not
depend on a Tamesu-specific agent integration, generated scaffolding, or an MCP server.

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

Keep `skills/tamesu-eval-authoring/SKILL.md` as the repository's canonical copy. For a
harness that uses a project-level skill directory, point it at the canonical file or
create the vendor-supported link to it instead of maintaining a second copy.

If a harness does not discover repository skills automatically, reference the file in
your request:

```text
Read skills/tamesu-eval-authoring/SKILL.md and use it to author this eval: ...
```

This fallback works with coding agents that can read repository files even when they do
not implement automatic skill discovery.

## Good request boundaries

Be explicit about the last authorized lifecycle step. Common requests are:

- “Author the eval and leave it in draft.”
- “Author, lint, plan, and activate it, but do not call providers.”
- “Run a two-item probe and explain state, score, failures, and cost.”
- “Run the full active eval, compare the arms, and generate the leaderboard.”

Separating these steps lets an agent help with experimental design without treating file
authoring as permission to spend money.
