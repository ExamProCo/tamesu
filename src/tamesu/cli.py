from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .artifacts import write_text
from .config import load_eval_context, load_eval_context_from_path, load_yaml
from .discovery import (
    discover_eval_paths,
    eval_id_from_path,
    find_project_root,
    find_run_dir,
)
from .errors import TamesuError
from .environment import load_environment
from .planner import build_plan
from .providers import get_provider
from .providers.models import MODELS, PRICING_VERIFIED_AT
from .pricing import estimate_plan_cost, recorded_cost
from .reporting import build_leaderboard, comparison_rows, status_summary
from .runner import resume_run, run_eval
from .scoring import rescore_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tamesu",
        description="File-based evaluation tooling for reproducible AI experiments.",
    )
    parser.add_argument("--version", action="version", version=f"tamesu {__version__}")
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="Load the highest-precedence .env from this directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Validate evaluation files.")
    lint_parser.add_argument("path", nargs="?", default=".")

    subparsers.add_parser("list", help="List cases, experiments, and evals.")

    models_parser = subparsers.add_parser("models", help="List built-in model metadata.")
    models_parser.add_argument("--provider")

    plan_parser = subparsers.add_parser("plan", help="Preview concrete work and blockers.")
    plan_parser.add_argument("eval_id")

    run_parser = subparsers.add_parser("run", help="Execute planned eval work.")
    run_parser.add_argument("eval_id")
    run_parser.add_argument("--only", metavar="MODEL")
    run_parser.add_argument("--limit-items", type=int, metavar="N")
    run_parser.add_argument("--force", action="store_true")

    status_parser = subparsers.add_parser("status", help="Reconcile plan and evidence.")
    status_parser.add_argument("eval_id")

    resume_parser = subparsers.add_parser("resume", help="Continue a partial run.")
    resume_parser.add_argument("run_id")

    rescore_parser = subparsers.add_parser("rescore", help="Rescore stored outputs.")
    rescore_parser.add_argument("run_id")

    compare_parser = subparsers.add_parser("compare", help="Compare compatible runs.")
    compare_parser.add_argument("eval_id")

    leaderboard_parser = subparsers.add_parser(
        "leaderboard", help="Build an eval leaderboard."
    )
    leaderboard_parser.add_argument("eval_id")

    close_parser = subparsers.add_parser("close", help="Close a completed eval.")
    close_parser.add_argument("eval_id")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code = dispatch(args)
    except TamesuError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        print("interrupted; completed artifacts were preserved", file=sys.stderr)
        raise SystemExit(130) from None
    raise SystemExit(exit_code)


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "lint":
        project_root = find_project_root(Path(args.path).resolve())
        load_environment(project_root, config_dir=args.config_dir)
        return command_lint(Path(args.path))
    if args.command == "models":
        load_environment(None, config_dir=args.config_dir)
        return command_models(args.provider)
    project_root = find_project_root()
    load_environment(project_root, config_dir=args.config_dir)
    if args.command == "list":
        return command_list(project_root)
    if args.command == "plan":
        return command_plan(project_root, args.eval_id)
    if args.command == "run":
        return command_run(
            project_root,
            args.eval_id,
            only=args.only,
            limit_items=args.limit_items,
            force=args.force,
        )
    if args.command == "status":
        return command_status(project_root, args.eval_id)
    if args.command == "resume":
        return command_resume(project_root, args.run_id)
    if args.command == "rescore":
        return command_rescore(project_root, args.run_id)
    if args.command == "compare":
        return command_compare(project_root, args.eval_id)
    if args.command == "leaderboard":
        return command_leaderboard(project_root, args.eval_id)
    if args.command == "close":
        return command_close(project_root, args.eval_id)
    raise AssertionError(f"Unhandled command: {args.command}")


def command_lint(path: Path) -> int:
    resolved = path.resolve()
    project_root = find_project_root(resolved)
    if resolved.is_file() and resolved.name == "eval.yml":
        eval_paths = [resolved]
    else:
        eval_paths = [
            candidate
            for candidate in discover_eval_paths(project_root)
            if _is_within(candidate, resolved)
        ]
    if not eval_paths:
        raise TamesuError(f"No eval.yml files found under {resolved}")

    failures: list[str] = []
    for eval_path in eval_paths:
        eval_id = eval_id_from_path(project_root, eval_path)
        try:
            context = load_eval_context_from_path(project_root, eval_path)
            _render_all_prompts(context)
            print(f"ok  {eval_id}")
        except TamesuError as exc:
            failures.append(str(exc))
            print(f"fail  {eval_id}", file=sys.stderr)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Validated {len(eval_paths)} eval(s).")
    return 0


def command_list(project_root: Path) -> int:
    paths = discover_eval_paths(project_root)
    if not paths:
        print("No evals found.")
        return 0
    for path in paths:
        eval_id = eval_id_from_path(project_root, path)
        manifest = load_yaml(path)
        print(f"{eval_id}\t{manifest.get('status', 'unknown')}")
    return 0


def command_models(provider: str | None = None) -> int:
    if provider and provider not in {spec.provider for spec in MODELS.values()}:
        raise TamesuError(f"Unknown provider: {provider}")
    print(
        "model\tprovider\tefforts\tinput_usd_per_million\t"
        "output_usd_per_million\tmax_tokens"
    )
    for model, spec in sorted(MODELS.items(), key=lambda item: (item[1].provider, item[0])):
        if provider and spec.provider != provider:
            continue
        efforts = ",".join(spec.efforts) or "-"
        input_rate = (
            "unknown"
            if spec.input_usd_per_million is None
            else f"{spec.input_usd_per_million:g}"
        )
        output_rate = (
            "unknown"
            if spec.output_usd_per_million is None
            else f"{spec.output_usd_per_million:g}"
        )
        print(
            f"{model}\t{spec.provider}\t{efforts}\t{input_rate}\t{output_rate}\t"
            f"{spec.max_tokens}"
        )
    print(f"Pricing verified: {PRICING_VERIFIED_AT}")
    return 0


def command_plan(project_root: Path, eval_id: str) -> int:
    context = load_eval_context(project_root, eval_id)
    plan = build_plan(context)
    models = sorted({spec.model for spec in plan.specs})
    arms = sorted({spec.arm_id for spec in plan.specs})
    calls = sum(len(spec.item_ids) for spec in plan.owed_specs)
    print(f"Eval: {eval_id}")
    print(f"Status: {context.evaluation['status']}")
    print(f"Models: {', '.join(models)}")
    print(f"Arms: {', '.join(arms)}")
    print(f"Planned runs: {len(plan.specs)}")
    print(f"Banked runs: {len(plan.banked_run_ids)}")
    print(f"Runs owed: {len(plan.owed_specs)}")
    print(f"Selected items per run: {len(plan.specs[0].item_ids) if plan.specs else 0}")
    print(f"Generation calls still owed: {calls}")
    print(f"Budget ceiling: ${context.evaluation['defaults']['budget_usd']:.2f}")
    cost = estimate_plan_cost(plan)
    spent = recorded_cost(plan)
    print(f"Recorded known cost: ${spent.known_usd:.6f}")
    if spent.unknown_runs:
        print(f"Runs with unknown recorded cost: {spent.unknown_runs}")
    if cost.fully_priced:
        print(f"Estimated cost: ${cost.estimated_usd:.6f}")
        print(f"Maximum additional exposure: ${cost.maximum_usd:.6f}")
    else:
        print(f"Estimated known cost: ${cost.estimated_usd:.6f}")
        print(f"Maximum known additional exposure: ${cost.maximum_usd:.6f}")
        print(f"Unknown pricing: {', '.join(cost.unknown_models)}")
    if plan.partial_run_ids:
        print(f"Partial runs: {len(plan.partial_run_ids)}")
    if plan.stale_run_ids:
        print(f"Stale runs: {len(plan.stale_run_ids)}")
    blockers: list[str] = []
    for provider_name in sorted({spec.provider for spec in plan.owed_specs}):
        error = get_provider(provider_name).credential_error()
        if error:
            blockers.append(f"{provider_name}: {error}")
    if context.evaluation["status"] != "active":
        blockers.append(f"eval status is {context.evaluation['status']!r}")
    if blockers:
        print("Execution blockers:")
        for blocker in blockers:
            print(f"- {blocker}")
    else:
        print("Execution blockers: none")
    return 0


def command_run(
    project_root: Path,
    eval_id: str,
    *,
    only: str | None,
    limit_items: int | None,
    force: bool,
) -> int:
    context = load_eval_context(project_root, eval_id)
    run_ids = run_eval(
        context,
        only=only,
        limit_items=limit_items,
        force=force,
    )
    if not run_ids:
        print("No work owed. Use --force to create another fresh run.")
        return 0
    states: Counter[str] = Counter()
    failure_messages: Counter[str] = Counter()
    primary = context.evaluation["metrics"]["primary"]
    selected_total = 0
    completed_total = 0
    failed_total = 0
    for run_id in run_ids:
        run_dir = context.eval_dir / "runs" / run_id
        manifest = load_yaml(run_dir / "run.yml")
        state = str(manifest.get("state", "unknown"))
        states[state] += 1
        fields = [state, run_id]
        report_path = run_dir / "report.yml"
        if report_path.is_file():
            report = load_yaml(report_path)
            completion = report.get("completion", {})
            selected = int(completion.get("selected_items", 0))
            completed = int(completion.get("completed_items", 0))
            failed = int(completion.get("failed_items", 0))
            selected_total += selected
            completed_total += completed
            failed_total += failed
            fields.extend((f"items={completed}/{selected}", f"failed={failed}"))
            primary_value = report.get("metrics", {}).get(primary)
            if isinstance(primary_value, (int, float)) and not isinstance(
                primary_value, bool
            ):
                fields.append(f"{primary}={primary_value:.4f}")
            cost = report.get("totals", {}).get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                fields.append(f"cost_usd={cost:.6f}")
            else:
                fields.append("cost_usd=unknown")
        print("\t".join(fields))
        for result_path in (run_dir / "items").glob("*/result.yml"):
            result = load_yaml(result_path)
            if result.get("state") != "failed":
                continue
            message = result.get("error", {}).get("message")
            if isinstance(message, str) and message:
                failure_messages[message] += 1

    if limit_items is not None and selected_total:
        print(
            f"Probe items: {completed_total}/{selected_total} completed; "
            f"{failed_total} failed."
        )
    if states["failed"]:
        print(f"{states['failed']} run(s) failed.", file=sys.stderr)
        for message, count in failure_messages.most_common():
            print(f"- {count} item(s): {message}", file=sys.stderr)
        print(f"Inspect all evidence: tamesu status {eval_id}", file=sys.stderr)
        return 1
    if limit_items is not None:
        print("Probe runs are diagnostic and excluded from formal comparisons.")
        print(f"Next: tamesu run {eval_id}")
        return 0
    incomplete = sum(count for state, count in states.items() if state != "complete")
    if incomplete:
        print(f"{incomplete} run(s) remain incomplete.", file=sys.stderr)
        print(f"Inspect all evidence: tamesu status {eval_id}", file=sys.stderr)
        return 1
    return 0


def command_status(project_root: Path, eval_id: str) -> int:
    plan = build_plan(load_eval_context(project_root, eval_id))
    summary = status_summary(plan)
    counts = summary["counts"]
    for key in ("planned", "banked", "owed", "partial", "failed", "stale", "extra"):
        print(f"{key.capitalize()}: {counts[key]}")
    for run in summary["runs"]:
        print(f"{run['classification']}\t{run['run_id']}\t{run['state']}")
    return 0


def command_resume(project_root: Path, run_id: str) -> int:
    run_dir = find_run_dir(project_root, run_id)
    manifest = load_yaml(run_dir / "run.yml")
    context = load_eval_context(project_root, manifest["eval_id"])
    print(resume_run(context, run_id))
    return 0


def command_rescore(project_root: Path, run_id: str) -> int:
    run_dir = find_run_dir(project_root, run_id)
    manifest = load_yaml(run_dir / "run.yml")
    context = load_eval_context(project_root, manifest["eval_id"])
    report = rescore_run(context, run_dir)
    print(f"Rescored {run_id}: {context.evaluation['metrics']['primary']}="
          f"{report['metrics'].get(context.evaluation['metrics']['primary'])}")
    return 0


def command_compare(project_root: Path, eval_id: str) -> int:
    plan = build_plan(load_eval_context(project_root, eval_id))
    rows = comparison_rows(plan)
    if not rows:
        print("No compatible completed runs to compare.")
        summary = status_summary(plan)
        counts = summary["counts"]
        excluded = [
            f"{counts[key]} {key}"
            for key in ("partial", "failed", "stale")
            if counts[key]
        ]
        if excluded:
            print(f"Excluded evidence: {', '.join(excluded)}.")
        if counts["owed"]:
            print(f"Planned runs still owed: {counts['owed']}.")
            print(f"Next: tamesu run {eval_id}")
        print(f"Inspect: tamesu status {eval_id}")
        return 0
    print("model\tarm\trepetitions\titems\tprimary_mean\tstdev\tfailure_rate\tcost_usd")
    for row in rows:
        print(
            "{model}\t{arm}\t{repetitions}\t{items}\t{primary_mean}\t{primary_stdev}\t"
            "{generation_failure_rate}\t{total_cost_usd}".format(**row)
        )
    return 0


def command_leaderboard(project_root: Path, eval_id: str) -> int:
    plan = build_plan(load_eval_context(project_root, eval_id))
    print(build_leaderboard(plan))
    return 0


def command_close(project_root: Path, eval_id: str) -> int:
    context = load_eval_context(project_root, eval_id)
    if context.evaluation["status"] == "complete":
        print(f"{eval_id} is already complete.")
        return 0
    plan = build_plan(context)
    if plan.owed_specs:
        raise TamesuError(
            f"Cannot close {eval_id}: {len(plan.owed_specs)} planned run(s) are still owed."
        )
    for run_id in plan.banked_run_ids.values():
        if not (context.eval_dir / "runs" / run_id / "report.yml").is_file():
            raise TamesuError(f"Cannot close {eval_id}: run {run_id} has no report.yml.")

    eval_path = context.eval_dir / "eval.yml"
    source = eval_path.read_text(encoding="utf-8")
    updated, replacements = re.subn(
        r"(?m)^status:\s*(draft|active|complete)\s*$", "status: complete", source, count=1
    )
    if replacements != 1:
        raise TamesuError(f"Could not update status in {eval_path}")
    write_text(eval_path, updated)
    closed_context = load_eval_context(project_root, eval_id)
    build_leaderboard(build_plan(closed_context))
    print(f"Closed {eval_id}.")
    return 0


def _render_all_prompts(context: Any) -> None:
    from .planner import build_plan, items_by_id
    from .tasks.structured_text import render_prompts

    item_map = items_by_id(context)
    for spec in build_plan(context).specs:
        for item_id in spec.item_ids:
            render_prompts(spec.prompts, item_map[item_id])


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    main()
