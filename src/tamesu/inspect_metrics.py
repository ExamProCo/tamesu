"""Freeze Inspect's statistics while task metric registrations are available.

Rendering reads these artifacts and never imports an authored task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import write_yaml
from .config import load_yaml
from .errors import ExecutionError
from .identity import digest_value


def metric_values(results: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for score in (getattr(results, 'scores', None) or []):
        for name, metric in score.metrics.items():
            key = f'{score.name}/{name}'
            if key in values:
                raise ExecutionError(f'Ambiguous Inspect metric {key}; choose one reducer in the task')
            values[key] = metric.value
    return values


def aggregate_path(eval_dir: Path, run_ids: list[str]) -> Path:
    return eval_dir / 'statistics' / (digest_value(sorted(run_ids)).split(':', 1)[1] + '.yml')


def freeze_statistics(context: Any, run_ids: list[str]) -> dict[str, Any]:
    from inspect_ai.log import read_eval_log
    from inspect_ai.scorer import SampleScore
    from inspect_ai.scorer._metric import metric_create
    from inspect_ai.scorer._reducer import create_reducers
    from inspect_ai._eval.task.results import ScorerInfo, eval_results
    from .backends.inspect_backend import verify_logs

    scores = []
    reference = None
    total = 0
    for run_id in sorted(run_ids):
        run_dir = context.eval_dir / 'runs' / run_id
        problems = verify_logs(run_dir, context.eval_dir)
        if problems:
            raise ExecutionError('; '.join(problems))
        lineage = load_yaml(run_dir / 'inspect/lineage.yml')['logs']
        latest = {}
        for entry in lineage:
            log = read_eval_log(str(context.eval_dir / entry['path']))
            reference = log
            for sample in log.samples or []:
                latest[str(sample.id)] = sample
        total += len(load_yaml(run_dir / 'run.yml')['selection']['item_ids'])
        for sample in latest.values():
            if sample.scores:
                scores.append({name: SampleScore(score=score, sample_id=sample.id, sample_metadata=sample.metadata)
                               for name, score in sample.scores.items()})
    if reference is None:
        return {}

    def create_metrics(definitions: Any) -> Any:
        if isinstance(definitions, dict):
            return {key: create_metrics(value) for key, value in definitions.items()}
        return [create_metrics(value) if isinstance(value, dict) else metric_create(value.name, **(value.options or {}))
                for value in (definitions or [])]

    scorers = [ScorerInfo(name=scorer.name.split('/')[-1], metrics=create_metrics(scorer.metrics), params=scorer.options or {})
               for scorer in (reference.eval.scorers or [])]
    results, _ = eval_results(samples=total, scores=scores,
                             reducers=create_reducers(reference.eval.config.epochs_reducer), scorers=scorers, metrics=None)
    values = metric_values(results)
    requested = [context.evaluation['metrics']['primary'], *context.evaluation['metrics'].get('secondary', [])]
    missing = [key for key in requested if '/' in key and key not in values]
    if missing:
        raise ExecutionError('Inspect did not produce requested metrics: ' + ', '.join(missing))
    write_yaml(aggregate_path(context.eval_dir, run_ids), {'schema_version': 1, 'run_ids': sorted(run_ids), 'metrics': values})
    return values
