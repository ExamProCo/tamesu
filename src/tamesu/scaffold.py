"""`tamesu new`: write a case, experiment, or eval into the tree from commented templates."""
from __future__ import annotations

import re
from pathlib import Path

from .errors import TamesuError

NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

CASE = """\
name: {case}
title: {title}
description: >
  One or two sentences: what this case studies.
business_use: >
  Who will use the result, and for which decision.
current_problem: >
  What goes wrong today, or what nobody knows yet.
technical_uncertainty: >
  What you cannot answer without running an experiment.
"""

EXPERIMENT = """\
name: {title}
description: >
  What question does this experiment set out to answer?
hypothesis: >
  What do you expect to happen, and why?
success_criteria:
  - Describe the evidence needed for a successful outcome.
"""

def create(project_root: Path, level: str, path: str) -> list[Path]:
    """Create the named level and any missing parents. Never overwrites a file."""
    parts = path.strip("/").split("/")
    expected = {"case": 1, "experiment": 2, "eval": 3}[level]
    if len(parts) != expected:
        shapes = {1: "<case>", 2: "<case>/<experiment>", 3: "<case>/<experiment>/<eval>"}
        raise TamesuError(f"`tamesu new {level}` takes {shapes[expected]}, got {path!r}")
    for part in parts:
        if not NAME.fullmatch(part):
            raise TamesuError(f"Names must be lowercase-kebab-case: {part!r}")
    case, experiment, evaluation = (parts + [None, None])[:3]
    case_dir = project_root / "cases" / case
    files: dict[Path, str] = {case_dir / "case.yml": CASE.format(case=case, title=_title(case)),
        case_dir / "experiments/.keep": "", case_dir / "datasets/.keep": ""}
    if experiment:
        experiment_dir = case_dir / "experiments" / experiment
        files[experiment_dir / "experiment.yml"] = EXPERIMENT.format(title=_title(experiment))
        files[experiment_dir / "evals/.keep"] = ""
    if evaluation:
        eval_dir = experiment_dir / "evals" / evaluation
        files[eval_dir / "eval.yml"] = INSPECT_EVAL.format(eval=evaluation, case=case)
        files[eval_dir / "task.py"] = INSPECT_TASK
    target = {"case": case_dir / "case.yml", "experiment": case_dir / "experiments" / str(experiment),
              "eval": case_dir / "experiments" / str(experiment) / "evals" / str(evaluation)}[level]
    if target.exists():
        raise TamesuError(f"{target.relative_to(project_root)} already exists")
    created = []
    for file, content in files.items():
        if file.exists():
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        created.append(file)
    return created


def _title(name: str) -> str:
    return name.replace("-", " ").capitalize()


INSPECT_EVAL = """\
schema_version: 1
name: {eval}
question: Can the model answer this question correctly?
description: Compare answers using Inspect, then record findings here.
task: artifact_bundle
dataset:
  name: {case}-v1
  description: A small starter dataset; replace it with real examples.
  items:
    - id: example-1
      input: {{question: "What is 2 + 2?"}}
      expected: "4"
execution:
  file: task.py
  task: experiment
  sources: [task.py]
  limits: {{cost_limit: 0.05, time_limit: 120}}
  artifacts:
    required: [response]
    review: response
defaults:
  repetitions: 1
  concurrency: 1
  timeout_seconds: 120
  retries: 0
  budget_usd: 0.50
  parameters: {{max_tokens: 1024}}
arms:
  - id: baseline
    description: Answer directly.
    prompts:
      system: {{text: "Answer with only the answer."}}
      user: {{text: "{{{{ item.input.question }}}}"}}
runs:
  - model: muse-spark-1.2
    provider: meta
    arms: [baseline]
evaluation:
  scorers: [correctness]
metrics:
  primary: correctness/accuracy
"""

INSPECT_TASK = '''from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import generate
from tamesu.inspect_support import stager


@scorer(metrics=[accuracy()])
def correctness():
    async def score(state, target):
        answer = state.output.completion.strip()
        stager(str(state.sample_id)).add("response", "answer.txt", answer, "text/plain")
        return Score(value=1.0 if answer == target.text.strip() else 0.0, answer=answer)
    return score


@task
def experiment(dataset: str):
    return Task(dataset=json_dataset(dataset), solver=generate(), scorer=correctness())
'''
