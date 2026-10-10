"""`tamesu new`: write a case, experiment, or eval into the tree from commented templates."""
from __future__ import annotations

import re
from pathlib import Path

from .errors import TamesuError

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

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

EXPERIMENT_README = """\
# {title}

## Uncertainty

What this experiment is trying to find out, and why it is not obvious.

## Method

What is compared, what is held fixed, and how results are scored.

## Findings

Fill this in after the runs. Link to the page from `tamesu show`.

## Limits

What these results do not show.

## Next steps
"""

EVAL = """\
name: {eval}
question: >
  The one question this eval answers.
description: >
  What is compared, and what is held fixed.

task: structured_text
dataset: ../../../../datasets/{case}-v1/dataset.yml
output_schema: ../../schemas/answer.schema.json

defaults:
  repetitions: 1
  concurrency: 1
  timeout_seconds: 120
  retries: 1
  budget_usd: 0.50          # the run refuses to start if its estimate exceeds this
  parameters:
    max_tokens: 1024

arms:                       # each arm is one way of prompting; add more to compare prompts
  - id: baseline
    description: The prompt as written.
    prompts:
      system: ../../prompts/system.md
      user: ../../prompts/user.md.j2

runs:                       # each entry is a model; add more to compare models
  - model: muse-spark-1.2
    provider: meta
    arms: [baseline]

evaluation:
  mechanical:
    - valid_json
    - schema_valid

metrics:
  primary: invalid_output_rate
"""

DATASET = """\
name: {case}-v1
description: The inputs every model and arm receives.
items:
  - id: example-1
    input:
      question: Replace this with a real input.
    expected:
      answer: The answer you expect, if there is one.
"""

SYSTEM_PROMPT = """\
Answer the question. Return one JSON object with an `answer` field and nothing else.
"""

USER_PROMPT = """\
{{{{ item.input.question }}}}
"""

SCHEMA = """\
{{
  "type": "object",
  "required": ["answer"],
  "properties": {{"answer": {{"type": "string"}}}},
  "additionalProperties": false
}}
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
    files: dict[Path, str] = {case_dir / "case.yml": CASE.format(case=case, title=_title(case))}
    if experiment:
        experiment_dir = case_dir / "experiments" / experiment
        files[experiment_dir / "README.md"] = EXPERIMENT_README.format(title=_title(experiment))
    if evaluation:
        files.update(
            {
                experiment_dir / "evals" / evaluation / "eval.yml": EVAL.format(eval=evaluation, case=case),
                case_dir / "datasets" / f"{case}-v1" / "dataset.yml": DATASET.format(case=case),
                experiment_dir / "prompts" / "system.md": SYSTEM_PROMPT,
                experiment_dir / "prompts" / "user.md.j2": USER_PROMPT.format(),
                experiment_dir / "schemas" / "answer.schema.json": SCHEMA.format(),
            }
        )
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
