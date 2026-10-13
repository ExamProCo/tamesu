"""Deterministic selection shared by plan previews and execution."""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, replace
from typing import Any

from .errors import ExecutionError
from .identity import digest_value
from .models import EvalContext, Plan
from .planner import build_plan, make_run_spec


@dataclass(frozen=True)
class RunOptions:
    model: str | None = None
    arms: tuple[str, ...] = ()
    limit_items: int | None = None
    items: tuple[str, ...] = ()
    sample: str | None = None
    seed: int | None = None
    repetitions: int | None = None
    max_executions: int | None = None
    concurrency: int | None = None
    budget_usd: float | None = None

    def validate(self) -> None:
        for name in ('limit_items', 'repetitions', 'max_executions', 'concurrency'):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 1):
                raise ExecutionError(f"--{name.replace('_', '-')} must be at least 1")
        if self.budget_usd is not None and (not math.isfinite(self.budget_usd) or self.budget_usd <= 0):
            raise ExecutionError('--budget-usd must be a finite positive number')
        if self.items and self.limit_items is not None:
            raise ExecutionError('Use --item or --limit-items, not both')
        if self.sample and (self.limit_items is None or self.items):
            raise ExecutionError('--sample requires --limit-items')
        if self.sample == 'random' and self.seed is None:
            raise ExecutionError('--sample random requires --seed for reproducibility')
        if self.seed is not None and self.sample != 'random':
            raise ExecutionError('--seed requires --sample random')


def selected_plan(context: EvalContext, options: RunOptions) -> Plan:
    options.validate()
    original = build_plan(context)
    all_ids = tuple(item['id'] for item in context.dataset['items'])
    unknown = set(options.items) - set(all_ids)
    if unknown:
        raise ExecutionError('Unknown item IDs: ' + ', '.join(sorted(unknown)))
    known_arms = {spec.arm_id for spec in original.specs}
    if set(options.arms) - known_arms:
        raise ExecutionError('Unknown arms: ' + ', '.join(sorted(set(options.arms) - known_arms)))
    specs = [spec for spec in original.specs if (options.model is None or spec.model == options.model) and (not options.arms or spec.arm_id in options.arms)]
    if not specs:
        raise ExecutionError('No planned run configurations match the requested filters.')
    evaluation = dict(context.evaluation)
    defaults = dict(evaluation['defaults'])
    if options.concurrency is not None:
        defaults['concurrency'] = options.concurrency
    if options.budget_usd is not None:
        defaults['budget_usd'] = options.budget_usd
    evaluation['defaults'] = defaults
    effective = replace(context, evaluation=evaluation)
    ids = all_ids
    if options.items:
        ids = tuple(dict.fromkeys(options.items))
    elif options.limit_items is not None:
        ids = tuple(random.Random(options.seed).sample(list(all_ids), min(options.limit_items, len(all_ids)))) if options.sample == 'random' else all_ids[:options.limit_items]
    if options.repetitions is not None:
        variants = {}
        for spec in specs:
            variants.setdefault((spec.provider, spec.model, spec.arm_id), spec)
        specs = [make_run_spec(effective, provider=spec.provider, model=spec.model, arm_id=spec.arm_id,
                 arm=next(arm for arm in evaluation['arms'] if arm['id'] == spec.arm_id),
                 repetition=rep, item_ids=all_ids, prompts=spec.prompts, parameters=spec.parameters, probe=False)
                 for spec in variants.values() for rep in range(1, options.repetitions + 1)]
    restricted = bool(options.items or options.limit_items is not None or options.max_executions is not None or options.repetitions is not None)
    remaining = options.max_executions
    selected = []
    for spec in specs:
        chosen = ids if remaining is None else ids[:remaining]
        if not chosen:
            break
        fingerprint = spec.specification_fingerprint
        if restricted:
            fingerprint = digest_value({'original_specification_fingerprint': fingerprint, 'item_ids': list(chosen), 'probe': True})
        selected.append(replace(spec, item_ids=chosen, probe=restricted, specification_fingerprint=fingerprint,
                                concurrency=defaults['concurrency'], budget_usd=float(defaults['budget_usd']), selection_options=asdict(options)))
        if remaining is not None:
            remaining -= len(chosen)
    return replace(original, context=effective, specs=tuple(selected), banked_run_ids={} if restricted else original.banked_run_ids)


def options_from_args(args: Any) -> RunOptions:
    return RunOptions(model=args.only, arms=tuple(args.arm), limit_items=args.limit_items, items=tuple(args.item),
                      sample=args.sample, seed=args.seed, repetitions=args.repetitions, max_executions=args.max_executions,
                      concurrency=args.concurrency, budget_usd=args.budget_usd)


def add_selection_arguments(parser: Any) -> None:
    parser.add_argument('--model', '--only', dest='only', metavar='MODEL')
    parser.add_argument('--arm', action='append', default=[])
    parser.add_argument('--limit-items', type=int, metavar='N')
    parser.add_argument('--item', action='append', default=[])
    parser.add_argument('--sample', choices=['random'])
    parser.add_argument('--seed', type=int)
    parser.add_argument('--repetitions', type=int)
    parser.add_argument('--max-executions', type=int)
    parser.add_argument('--concurrency', type=int)
    parser.add_argument('--budget-usd', type=float)
