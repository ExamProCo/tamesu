# Happy path: author, run, review, and package an experiment

You will create your own experiment, run it through Inspect AI, inspect the
answers, add your human judgment, and package the finished evidence.

The question is: **Can this model answer three arithmetic questions correctly?**
The automated scorer checks exact answers. Human review checks whether each
response is a useful, direct answer. This is a small first run to learn the workflow.

## 1. Create your workspace

In a terminal:

```sh
cd /home/andrew/Sites/tamesu
git branch --show-current
source .venv/bin/activate
tamesu --version
mkdir -p /home/andrew/Sites/tamesu-first-experiment
cd /home/andrew/Sites/tamesu-first-experiment
tamesu new eval arithmetic/first/baseline
```

The branch should be `simplify-inspect-ai`. Use a fresh workspace if you already
created this experiment; `new` does not overwrite it.

The starter creates three files:

```text
cases/arithmetic/case.yml
cases/arithmetic/experiments/first/evals/baseline/eval.yml
cases/arithmetic/experiments/first/evals/baseline/task.py
```

`case.yml` explains the purpose. `eval.yml` holds inputs, prompts, model settings,
and acceptance rules. `task.py` is the Inspect task that generates and scores answers.

## 2. Author the experiment

Open `cases/arithmetic/case.yml` in your editor. Replace the placeholder prose:

```yaml
name: arithmetic
title: Arithmetic answers
description: Test whether a model gives correct, direct arithmetic answers.
business_use: Establish a small baseline before designing a larger evaluation.
current_problem: Fluent answers can still be incorrect.
technical_uncertainty: Can the selected model answer all three questions correctly?
```

Open `cases/arithmetic/experiments/first/evals/baseline/eval.yml`.
Set its `question` and `description`:

```yaml
question: Can the model answer three arithmetic questions correctly?
description: One run with three questions, exact-answer scoring, and human review.
```

Replace its entire `dataset` block with:

```yaml
dataset:
  name: arithmetic-v1
  description: Three small arithmetic questions with known answers.
  items:
    - id: addition
      input: {question: "What is 2 + 2?"}
      expected: "4"
    - id: multiplication
      input: {question: "What is 3 * 7?"}
      expected: "21"
    - id: subtraction
      input: {question: "What is 10 - 6?"}
      expected: "4"
```

Keep the starter's prompts, execution settings, and `task.py` for this first run.
The system prompt asks for only the answer. In `task.py`, the scorer stores
`answer.txt` and compares the trimmed response to the expected answer. `4` passes;
`The answer is 4.` fails this exact-answer rule. That strictness is intentional here.

## 3. Configure human review before running

Beside `eval.yml`, create `rubric.yml`:

```yaml
schema_version: 1
name: answer-quality
rationale: answer-quality.md
dimensions:
  - id: useful
    question: Does the response give a correct, direct answer to the question?
    reason_codes: [incorrect, incomplete, other]
acceptance:
  rule: all_dimensions_pass
```

Create `answer-quality.md` in the same directory:

```text
Pass when the response gives the correct arithmetic result directly.
The expected answers are 4 for addition, 21 for multiplication, and 4 for subtraction.
Fail an incorrect or incomplete answer. Do not pass an answer merely because the automated scorer passed it.
```

Replace the existing `evaluation` block in `eval.yml` with:

```yaml
evaluation:
  mechanical: [correctness]
  human_review:
    rubric: rubric.yml
  acceptance:
    requires: [mechanical, human]
```

The starter already selects the response artifact for review. This experiment now
requires both the automated check and your human decision. Configure these rules
before running so the results belong to the experiment you intend to evaluate.

## 4. Set credentials and inspect the plan

The starter selects `muse-spark-1.2` with provider `meta`. For that configuration,
enter your API key in the terminal:

```sh
read -rsp 'META_API_KEY: ' META_API_KEY
export META_API_KEY
tamesu lint .
tamesu plan arithmetic/first/baseline
```

If you use another supported model/provider, edit the `runs` block and set that
provider's credentials before continuing. `tamesu models` lists built-in metadata.

Expect validation to pass and the plan to show one run containing three items.
Check the selected model and estimated exposure. Running makes paid model calls;
configured cost limits act on observed spending and are not guaranteed billing caps.

## 5. Run the experiment

```sh
tamesu run arithmetic/first/baseline
tamesu status arithmetic/first/baseline
tamesu report arithmetic/first/baseline
```

Inspect generates and scores all three answers. The happy path produces three
completed items with correctness scores of `1.0`; a wrong answer should instead
appear as a failed check. Generation can be finished while human review is pending.

Look under the eval's `runs/` directory for stored results and answer artifacts.
There is no activation or closing step.

## 6. Inspect your results page

In a second terminal:

```sh
source /home/andrew/Sites/tamesu/.venv/bin/activate
cd /home/andrew/Sites/tamesu-first-experiment
tamesu show arithmetic --open
```

If the browser does not open, visit the address printed in the terminal. The live overview shows your question, run status, completed-item counter, and
recorded cost. Answers and automated checks are visible together in a compact
table; human review is still outstanding. It refreshes every second as stored
evidence changes. For the experiment narrative and full evidence, click
**Full results and narrative**.

`show` runs a local server. Press Ctrl+C in that second terminal when finished.

## 7. Review the answers in the web UI

Keep `tamesu show arithmetic --open` running in the second terminal.

1. On the overview, click **Review / update decisions**.
2. Enter your reviewer ID, such as `andrew`.
3. Read each answer and rubric question, then mark it pass or fail.
4. Choose a reason for any failure; add notes where useful.
5. Click **Save review**. The page confirms how many decisions were saved.
6. Click **Back to overview** to see the updated human-review states.

The browser saves directly to your experiment. Untouched items remain pending.
Human decisions remain separate from automated scores. To revise a decision,
open the review again and save under the same reviewer ID; review history is
retained and the latest valid decision is used.

You can also check from the first terminal:

```sh
tamesu review status arithmetic/first/baseline
tamesu report arithmetic/first/baseline
```

Expect no outstanding human review once all three decisions are saved. If both
checks pass, the items meet your acceptance policy. The separate export/import
workflow remains available for a reviewer working elsewhere; see the
[tutorial](tutorial.md).

## 8. Write what you learned

Create `cases/arithmetic/experiments/first/README.md`:

```markdown
# First arithmetic experiment

## Method
One model, one repetition, three arithmetic questions. Exact-answer scoring plus human review.

## Findings
Record how many answers passed each check and describe any failures.

## Limits
Three simple questions do not establish broad mathematical reliability.

## Next experiment
Try a larger dataset with harder questions and compare another prompt or model.
```

Replace the findings instruction with your actual observations. Use **Full results and narrative** to see the narrative alongside the evidence.
The server rebuilds that page when files change; refresh it after editing your findings.

## 9. Package your experiment and results

From the first terminal:

```sh
tamesu pack arithmetic
tamesu verify build/packages/local-arithmetic-0.1.0.tamesu.tar.gz
```

The archive carries your authored files, results, artifacts, reviews, and rendered
page. No publication configuration is needed. Raw Inspect logs stay local.

Optionally check the handoff in a fresh copy:

```sh
tamesu unpack build/packages/local-arithmetic-0.1.0.tamesu.tar.gz --cases-dir received/cases
cd received
tamesu review status arithmetic/first/baseline
tamesu show arithmetic --open
```

The results and reviews should survive the handoff. Viewing does not call a model
or execute the packaged task code.

You have authored, run, inspected, reviewed, documented, and packaged your own experiment.
For file contracts and migration details, see the [reference docs](reference/README.md).
