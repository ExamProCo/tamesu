# My First Experiement

## Scenario

You are an AI Engineer working for the startup building a customer support AI assistant called Orderwisp.

During a pilot, a customer raises concerns about the assistant's ability to perform basic calculations accurately. They worry that incorrect calculations could lead to order errors, customer complaints, and financial losses.

You've been tasked with evaluating whether the AI assistant can reliably perform basic calcuations eg. add numbers together.

Your goal is to design experiments and evaluations that measure the assistant's accuracy across different addition scenarios, analyze the results, and present your findings to the customer, including any identified limitations and recommendations.

> The key point is that Tamesu's documentation layer creates a consistent and easy way to explain evals and they from the business use-case down to the smallest to the individual evaluations that support the conclusions.

## 1. Install Tamesu

Create a python env (optional)
```sh
python3 -m venv .venv
source .venv/bin/activate
```

Install the global python executable directly from GitHub:

```sh
uv tool install git+https://github.com/ExamProCo/tamesu.git
```

Test that it works:
```sh
tamesu --version
```

## 2. Create new folder

We will create a new folder to contain our cases.
Ideally this will be a top level git repo and very likely its main product's repo.

```sh
mkdir -p ~/Sites/orderwisp
cd ~/Sites/orderwisp
```

## 1. Generate Cases, Experiments, and Evaluations

We will generate out template files and fill in as we go.

So it can look like this:

```sh
tamesu case new base-calc
tamesu exp new base-calc/add
tamesu eval new base-calc/add/simple
tamesu eval new base-calc/add/carrying
tamesu eval new base-calc/add/multi_digit
tamesu exp new base-calc/sub
tamesu eval new base-calc/sub/simple
tamesu eval new base-calc/sub/borrowing
tamesu eval new base-calc/sub/multi_digit
tamesu exp new base-calc/mult
tamesu eval new base-calc/mult/simple
tamesu eval new base-calc/mult/double_digit
tamesu eval new base-calc/mult/multi_digit
```

> Consider at this stage I have thought up possible evals without walking the docs. Remembr this as a process error.

### 1.a Generate Case

The first thing we want to create is our case:

```sh
tamesu case new base-calc
```

This will create a single yaml file.
```sh
cases/base-calc/case.yml
cases/base-calc/experiments/.keep
cases/base-calc/datasets/.keep
```

> .keep is a hidden file so that an empty folder can be commited to a repo.

The purpose of our case is to:
- define our business use-case.
- define the problem which is technical or business which is driving us to build this case.
- define the technical uncertainty we have around our case.

Writing out these three things will help us think what we need define as experiments.


```yml
name: Orderwisp - Basic Calculation Accuracy
description: Does Orderwisp accurately handle basic calculations?
business_use: A human support agent will directly ask Orderwisp to perform basic calucations
current_problem: There is a lack of documented evidence to demonstrate to our pilot customer that Orderwisp can reliably perform basic calculations to go beyond pilot into production.
```

> Recommend that you write your own draft, and not have AI generate our your initial case information because it can often scope the case wrong.


`case.yml` explains the purpose. `eval.yml` holds inputs, prompts, model settings,
and acceptance rules. `task.py` is the Inspect task that generates and scores answers.

At anytime we can preview our case in the web-browser

```sh
tamesu show
```

This will serve a simple http-server.
We will be able to see a list of cases.
Click into your case.
If you update your yaml files you will see
them reflected in the page.

Notice you can also update your copy directly in the UI.

### 1.b Generate Experiment

```sh
tamesu exp new base-calc/add
```

```sh
cases/base-calc/experiments/add/experiment.yml
cases/base-calc/experiments/add/evals/.keep
```

```yml
name: Basic Addition
description:
  Can we prove that Orderwisp can accurately add numbers together?
  We use a range of Meta Muse Spark 1.3 from minimal to high based on dynamic routing.
  We need to simply test for not a single-turn. An agent going out and collecting documents
  is a differenet experiment, we are assuming that in this experiment the agent has the numbers
  it needs to add together.
hypothesis:
  We expect that such a fundamental task is now solved in modern models
  and that at any effort level it should produce accurate addition.
success_criteria:
  - All calculations must produce the correct answer.
  - Each test case must pass 30 consecutive runs without errors.
  - At least 100 unique calculations must be tested.
  - We must test it against our range of effort from minimal to high.
  - All 12,000 executions (3,000 per effort arm) must produce correct results.
  - All responses must follow the expected output format.
  - No incorrect, fabricated, or invalid answers are acceptable.
```

I would recommend writing the `description` as the question we set out to prove with this experiment.

Our `hypothesis` is our best-guess, it doesn't matter if we are correct or wrong, it matter that we take a guess.

Our `success_critera` is what we need to happen to consider our experiment was a succesfull outcome.

The key thing is `production ready` and so when we rubberducked with an agent what our success_critera should be based on the business use-case it highlighted a large amount of runs.

Earlier in this tutorial I said that we don't want to define our evals until we have our experiment.

We originally identified three addition evals:
- Simple addition — 2 + 2
- Addition with carrying — 8 + 7
- Multi-digit addition — 125 + 378


### 1.c Generate Eval

So we need determine what evals we need. You can use a coding harness now to suggest to evals based on reading your `case.yml` and `evalaution.yml`. This is GPT-6.1-Sol suggested:

- integer-addition	Positive integers, including carrying, zero, and large numbers
- negative-addition	Negative integers and mixed positive/negative values
- decimal-addition	Decimal values and varying precision
- multi-value-addition	Adding three or more numbers
- contextual-addition	Understanding addition requests written in natural language, including irrelevant information

> Use the tamesu agent skill, so that it has full context of tamesu configuration, for example it needs to know that eval's have arms so it doesn't create a ton of evals for eval variation.

So we in theory need to create this many evals.

```sh
tamesu eval new base-calc/add/int
tamesu eval new base-calc/add/neg
tamesu eval new base-calc/add/dec
tamesu eval new base-calc/add/multi-value
tamesu eval new base-calc/add/context
```

> you can autocomplete case/eval names with tab

For now lets just create a single one manually:

```sh
tamesu eval new base-calc/add/int
```


```sh
cases/base-calc/experiments/add/evals/int/eval.yml
cases/base-calc/experiments/add/evals/int/task.py
```

The `task.py` is what will get executed, tamesu is tightly coupled with Inspect AI, we will visit this in a moment buet lets focus on the `eval.yml`

The eval.yml has a lot going on but lets define what it needs a minumum:

- **question** - What you want to find out
- **description** - What the evaluation does
- **dataset** - Test items inline, or a path to dataset.yml
- **defaults** - repetitions, concurrency, timeout_seconds, retries, budget_usd
- **arms** - At least one variant with an id and description
- **runs** - At least one model, provider, and list of arm IDs
- **evaluation** - A mechanical scoring list
- **metrics** - A primary metric

So here is the template eval:

```yml
schema_version: 1
question: "[todo] What question should this eval answer?"
description: "[todo] Describe what you are testing."
task: artifact_bundle
dataset:
  name: examples-v1
  description: "[todo] Describe your test data."
  items:
    - id: example-1
      input:
        question: "[todo] Test input"
      expected: "[todo] Expected answer"
execution:
  file: task.py
  task: experiment
  sources: [task.py]
  limits:
    cost_limit: 0.05
    time_limit: 120
  artifacts:
    required: [response]
    review: response
defaults:
  repetitions: 1
  concurrency: 1
  timeout_seconds: 120
  retries: 0
  budget_usd: 0.50
  parameters:
    max_tokens: 1024
arms:
  - id: baseline
    description: Answer directly.
    prompts:
      system:
        text: "Answer with only the answer."
      user:
        text: "{{ item.input.question }}"
runs:
  - model: "[todo]"
    provider: "[todo]"
    arms: [baseline]
evaluation:
  scorers: [correctness]
metrics:
  primary: correctness/accuracy
```

So using AI coding harness we have it generate our eval:

```yml
schema_version: 1
name: int
question: Can Orderwisp correctly add non-negative integers at every tested effort level?
description: >
  Compare minimal, low, medium, and high effort using the same model,
  prompts, integer-addition inputs, and exact-answer scorer. Repeat each
  calculation 30 times per effort level. Every response must contain only
  the correct integer answer.
task: artifact_bundle
# this dataset is an example its clearly not large enough items.
dataset:
  name: integer-addition-v1
  description: >
    Starter examples covering simple addition, carrying, zero, and larger
    integers. Expand to at least 100 unique calculations before assessing
    the experiment's success criteria.
  items:
    - id: simple
      input: {question: "What is 2 + 2?"}
      expected: "4"
    - id: carrying
      input: {question: "What is 8 + 7?"}
      expected: "15"
    - id: zero
      input: {question: "What is 0 + 42?"}
      expected: "42"
    - id: multi-digit
      input: {question: "What is 125 + 378?"}
      expected: "503"
    - id: large-integers
      input: {question: "What is 999999 + 1?"}
      expected: "1000000"
execution:
  file: task.py
  task: experiment
  sources: [task.py]
  limits:
    cost_limit: 0.05
    time_limit: 120
  artifacts:
    required: [response]
    review: response
defaults:
  repetitions: 30
  concurrency: 1
  timeout_seconds: 120
  retries: 0
  budget_usd: 50.00 # Example ceiling; review the plan before running.
  parameters:
    max_tokens: 1024

arms:
  - id: minimal
    description: Integer addition with minimal reasoning effort.
    parameters: {effort: minimal}
    prompts:
      system:
        text: >
          Add the integers in the question. Return only the integer answer,
          without commas, units, explanation, or additional text.
      user:
        text: "{{ item.input.question }}"
  - id: low
    description: Integer addition with low reasoning effort.
    parameters: {effort: low}
    prompts:
      system:
        text: >
          Add the integers in the question. Return only the integer answer,
          without commas, units, explanation, or additional text.
      user:
        text: "{{ item.input.question }}"
  - id: medium
    description: Integer addition with medium reasoning effort.
    parameters: {effort: medium}
    prompts:
      system:
        text: >
          Add the integers in the question. Return only the integer answer,
          without commas, units, explanation, or additional text.
      user:
        text: "{{ item.input.question }}"
  - id: high
    description: Integer addition with high reasoning effort.
    parameters: {effort: high}
    prompts:
      system:
        text: >
          Add the integers in the question. Return only the integer answer,
          without commas, units, explanation, or additional text.
      user:
        text: "{{ item.input.question }}"

runs:
  - model: muse-spark-1.2
    provider: meta
    arms: [minimal, low, medium, high]
evaluation:
  scorers: [correctness]
metrics:
  primary: correctness/accuracy
```

#### External Dataset

- Notice our dataset is too small, we are suppose to have 100 unique examples, we'll need an external dataset.

```yml
dataset: "{{ root.datasets }}/integer-addition-v1/dataset.yml"
```

The following path `./cases/base-calc/datasets/integer-addition-v1/dataset.yml`:

```yml
schema_version: 1
name: integer-addition-v1
description: 100 unique non-negative integer addition calculations.
items:
  - id: calculation-001
    input:
      question: "What is 2 + 2?"
    expected: "4"
  - id: calculation-002
    input:
      question: "What is 8 + 7?"
    expected: "15"
  # Truncated: add calculations 003 through 099 here.
  - id: calculation-100
    input:
      question: "What is 999999 + 1?"
    expected: "1000000"
```

#### Evaluation and Metrics

Lets take a closer at these these options:

```sh
evaluation:
  scorers: [correctness]
metrics:
  primary: correctness/accuracy
```

- Inspect owns scoring and statistical metrics, including custom metrics and repetition handling.
- Tamesu owns experiment documentation, comparisons, presentation, completion tracking, costs, and review workflows.

Dev Note: I changed mechanical: to scorers: so it more clear

Lets take a look at the task.py:

```py
from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import generate

from tamesu.inspect_support import stager

@scorer(metrics=[accuracy()])
def correctness():
    async def score(state, target):
        answer = state.output.completion.strip()
        expected = target.text.strip()

        # Store the answer for Tamesu's results page and review.
        stager(str(state.sample_id)).add(
            "response",
            "answer.txt",
            answer,
            "text/plain",
        )

        return Score(
            value=1.0 if answer == expected else 0.0,
            answer=answer,
        )

    return score


@task
def experiment(dataset: str):
    return Task(
        dataset=json_dataset(dataset),
        solver=generate(),
        scorer=correctness(),
    )
```

correctness is a name we chose for our custom Python function. 
It doesn’t point to an Inspect built-in option.
We implement our Inspect AI scorers inside that function.

https://inspect.aisi.org.uk/scoring.html


## Workflow


```sh
tamesu lint . # validate the config and require files
```

These command are all the eval level.
```sh
tamesu plan base-calc/add/int # show what would run
tamesu run base-calc/add/int # actually runs it, consider a limited run first
tamesu status base-calc/add/int # shows the process of a current run
tamesu report base-calc/add/int # summarized saved results, metrics and acceptance outcomes.
```

- run → executes work and saves individual run reports.
- report → combines those results into evaluation-report.md.

When you do `tamesu show` you will already get a report so why do we have a report command?

Report is an optional command and is useful for:
- Pull requests: reviewers can read results alongside changes.
- Automation: CI can attach a readable summary as an artifact.
- Sharing: paste findings into an issue or another document.
- Snapshots: preserve a report in Git and compare it with later results.
You might not use it in your workflow.


## Limited Runs
Before you do a real run you may want to do a limited run.
A run using --limit-items won’t affect the normal report’s comparison metrics. 
Tamesu marks it as a probe and excludes it from the completed runs used for those comparisons.
The UI will show limited runs, but its not part of the final reporting.

Limited runs:

--model NAME | Select a model; clearer than the current --only
--arm NAME | Select an arm; repeatable to select several
--limit-items N | Select N unique dataset items
--item ID | Select specific items; repeatable
--sample random --seed N | Choose a reproducible random subset instead of the first N
--repetitions N | Override repetitions for this run
--max-executions N | Cap total item × model × arm × repetition executions
--concurrency N | Override parallelism
--budget-usd N | Set the run’s budget ceiling
--dry-run | Show the resulting plan without executing 


```sh
tamesu run base-calc/add/int \
  --limit-items 10 \
  --sample random \
  --seed 42 \
  --repetitions 2
```


```sh
tamesu run base-calc/add/int \
  --arm minimal \
  --limit-items 5 \
  --repetitions 1 \
  --dry-run
```
## Anaylze

Once you have completed a run and have a report and you want to provide an anaylsis you can generate the anaylsis.md file.

This will not call AI and will create the template you fill in.
Its a markdown file so you simply edit it.

```sh
tamesu analyze base-calc/add/int --scaffold
```

- Answer to the technical uncertainty - Did the evidence answer your question? Did the success criteria hold? |
- What remains uncertain - Limitations: missing scenarios, dataset coverage, other models, or real customer usage 
- Next experiments - What to test next and why

You can use the anaylze command to generate it:
```sh
tamesu analyze base-calc/add/int \
  --provider PROVIDER \
  --model MODEL
```
Then you can update it manually

You can use the tamesu agent skill with a coding hareness to look and generate it.

You should always review and modify it, AI can assist you but you need to make the conclusions.

## Rubric
We have rubric, but we'll have to counitue this tutorial further.
So for now we dont change how rubric works.