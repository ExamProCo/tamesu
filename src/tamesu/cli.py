from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .artifacts import write_text, write_yaml
from .config import load_eval_context, load_eval_context_from_path, load_yaml
from .discovery import (
    discover_eval_paths,
    eval_id_from_path,
    find_project_root,
    find_run_dir,
)
from .backends import backend_for
from .errors import ExecutionError, TamesuError
from .environment import load_environment
from .planner import build_plan
from .packaging import (
    fork_archive,
    pack_case,
    resolve_case,
    unpack_archive,
    validate_portable,
    verify_archive,
)
from .presenting import present_case
from .providers import get_provider
from .providers.models import MODELS, PRICING_VERIFIED_AT
from .pricing import estimate_plan_cost, recorded_cost
from .reporting import (
    build_evaluation_report,
    build_leaderboard,
    comparison_rows,
    status_summary,
)
from .runner import resume_run, run_eval
from .run_report import rescore_run


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
    lint_parser.add_argument(
        "--portable", action="store_true", help="Require every reference to stay in the case."
    )

    subparsers.add_parser("list", help="List cases, experiments, and evals.")

    models_parser = subparsers.add_parser("models", help="List built-in model metadata.")
    models_parser.add_argument("--provider")

    plan_parser = subparsers.add_parser("plan", help="Preview concrete work and blockers.")
    plan_parser.add_argument("eval_id")

    activate_parser = subparsers.add_parser(
        "activate", help="Validate and activate a draft eval."
    )
    activate_parser.add_argument("eval_id")

    run_parser = subparsers.add_parser("run", help="Execute planned eval work.")
    run_parser.add_argument("eval_id")
    run_parser.add_argument("--only", metavar="MODEL")
    run_parser.add_argument("--limit-items", type=int, metavar="N")
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument(
        "--trust-code",
        action="store_true",
        help="Allow an unpacked case's Inspect task Python to run on this machine.",
    )

    judge_parser = subparsers.add_parser(
        "judge", help="Run model judges over stored images (paid; repeatable)."
    )
    judge_parser.add_argument("eval_id")
    judge_parser.add_argument("--run", dest="run_id", help="Judge one run (default: banked runs).")
    judge_parser.add_argument("--judge", dest="judge_id", help="Run only this declared judge.")
    judge_parser.add_argument("--limit-items", type=int, help="Judge at most N items per run.")

    promote_parser = subparsers.add_parser(
        "promote", help="Copy accepted images into a new versioned dataset."
    )
    promote_parser.add_argument("eval_id")
    promote_parser.add_argument(
        "--to", required=True, metavar="CASE/DATASET", help="New dataset version, e.g. my-case/products-v2."
    )
    promote_parser.add_argument("--dry-run", action="store_true", help="Show what would be copied.")

    review_parser = subparsers.add_parser(
        "review", help="Export, import, and track blinded human review."
    )
    review_commands = review_parser.add_subparsers(dest="review_command", required=True)
    review_export = review_commands.add_parser("export", help="Write a blinded review pack.")
    review_export.add_argument("eval_id")
    review_export.add_argument("--run", dest="run_id", help="Export one run (default: banked runs).")
    review_export.add_argument("--out", type=Path, required=True, help="Empty directory for the pack.")
    review_export.add_argument("--reviewer", help="Prefill the reviewer ID in responses.yml.")
    review_export.add_argument("--seed", type=int, help="Seed for the shuffle (default: random).")
    review_export.add_argument(
        "--all", dest="include_reviewed", action="store_true", help="Include images already reviewed."
    )
    review_export.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing review pack in --out (only a directory containing pack.yml).",
    )
    review_import = review_commands.add_parser("import", help="Import a completed responses.yml.")
    review_import.add_argument("eval_id")
    review_import.add_argument("responses", type=Path)
    review_status_parser = review_commands.add_parser("status", help="Show review progress.")
    review_status_parser.add_argument("eval_id")
    review_status_parser.add_argument("--run", dest="run_id")

    status_parser = subparsers.add_parser("status", help="Reconcile plan and evidence.")
    status_parser.add_argument("eval_id")

    resume_parser = subparsers.add_parser("resume", help="Continue a partial run.")
    resume_parser.add_argument("run_id")
    resume_parser.add_argument("--trust-code", action="store_true", help="See `run --trust-code`.")

    rescore_parser = subparsers.add_parser("rescore", help="Rescore stored outputs.")
    rescore_parser.add_argument("run_id")

    compare_parser = subparsers.add_parser("compare", help="Compare compatible runs.")
    compare_parser.add_argument("eval_id")

    leaderboard_parser = subparsers.add_parser(
        "leaderboard", help="Build an eval leaderboard."
    )
    leaderboard_parser.add_argument("eval_id")

    report_parser = subparsers.add_parser(
        "report", help="Build a human-readable eval report."
    )
    report_parser.add_argument("eval_id")

    close_parser = subparsers.add_parser("close", help="Close a completed eval.")
    close_parser.add_argument("eval_id")
    close_parser.add_argument(
        "--allow-incomplete-review",
        action="store_true",
        help="Close an image eval although required judgments or reviews are missing "
        "(recorded in closing.yml).",
    )

    present_parser = subparsers.add_parser(
        "present", help="Render a case as a static HTML case study."
    )
    present_parser.add_argument("case")
    present_parser.add_argument("--output", type=Path)
    present_parser.add_argument("--open", action="store_true", help="Open the rendered page in a browser.")
    present_parser.add_argument(
        "--watch", action="store_true", help="Re-render whenever a file in the case changes (Ctrl-C to stop)."
    )
    present_parser.add_argument(
        "--strict", action="store_true", help="Exit nonzero if any eval is invalid (the page is still written)."
    )

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Write analysis.md: a scaffold, or a model-assisted interpretation (calls a model).",
    )
    analyze_parser.add_argument("eval_id")
    analyze_parser.add_argument("--scaffold", action="store_true", help="Create an authored template; no model call.")
    analyze_parser.add_argument("--provider", help="Provider for the model-assisted analysis.")
    analyze_parser.add_argument("--model", help="Model for the model-assisted analysis.")
    analyze_parser.add_argument("--force", action="store_true", help="Replace an analysis a person wrote.")
    analyze_parser.add_argument("--max-cost", type=float, default=0.25, help="Worst-case dollar limit.")

    pack_parser = subparsers.add_parser(
        "pack", help="Build a deterministic, verifiable case package."
    )
    pack_parser.add_argument("case")
    pack_parser.add_argument("--output-dir", type=Path)
    pack_parser.add_argument(
        "--without-presentation", action="store_true", help="Do not embed rendered HTML."
    )

    verify_parser = subparsers.add_parser("verify", help="Verify a .tamesu package.")
    verify_parser.add_argument("archive", type=Path)

    unpack_parser = subparsers.add_parser("unpack", help="Safely unpack a case package.")
    unpack_parser.add_argument("archive", type=Path)
    unpack_parser.add_argument("--cases-dir", type=Path)

    fork_parser = subparsers.add_parser("fork", help="Create a new case from a package.")
    fork_parser.add_argument("archive", type=Path)
    fork_parser.add_argument("--publisher", required=True)
    fork_parser.add_argument("--name")
    fork_parser.add_argument("--version", default="0.1.0")
    fork_parser.add_argument("--cases-dir", type=Path)

    publish_parser = subparsers.add_parser(
        "publish", help="Add a verified package to an inspectable registry."
    )
    publish_parser.add_argument("archive", type=Path)
    publish_parser.add_argument("--registry", type=Path, default=Path("registry"))

    site_parser = subparsers.add_parser("site", help="Validate or build the showcase site.")
    site_commands = site_parser.add_subparsers(dest="site_command", required=True)
    site_build = site_commands.add_parser("build", help="Build the static showcase.")
    site_build.add_argument("--registry", type=Path, default=Path("registry"))
    site_build.add_argument("--output", type=Path)
    site_check = site_commands.add_parser("check", help="Validate registry entries.")
    site_check.add_argument("--registry", type=Path, default=Path("registry"))
    site_check.add_argument("--author")
    site_check.add_argument(
        "--changed-file",
        action="append",
        default=[],
        metavar="STATUS:PATH",
        help="Validate PR ownership/version rules for a changed registry file.",
    )
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
        return command_lint(Path(args.path), portable=args.portable)
    if args.command == "models":
        load_environment(None, config_dir=args.config_dir)
        return command_models(args.provider)
    if args.command == "verify":
        verified = verify_archive(args.archive)
        print(f"Verified {args.archive}: {verified.content_digest}")
        print(f"Archive SHA-256: {verified.archive_sha256}")
        return 0
    if args.command == "publish":
        from .showcasing import publish_archive

        destination = publish_archive(args.archive, args.registry)
        print(f"Published registry entry: {destination}")
        return 0
    if args.command == "site":
        from .showcasing import (
            build_site,
            validate_registry,
            validate_registry_changes,
        )

        if args.site_command == "check":
            if args.changed_file:
                if not args.author:
                    raise TamesuError("--author is required with --changed-file")
                entries = validate_registry_changes(
                    args.registry,
                    author=args.author,
                    changed_files=args.changed_file,
                )
            else:
                entries = validate_registry(args.registry, author=args.author)
            print(f"Validated {len(entries)} registry entr{'y' if len(entries) == 1 else 'ies'}.")
            return 0
        destination = build_site(args.registry, args.output)
        print(f"Built showcase: {destination}")
        return 0
    project_root = find_project_root()
    load_environment(project_root, config_dir=args.config_dir)
    if args.command == "list":
        return command_list(project_root)
    if args.command == "plan":
        return command_plan(project_root, args.eval_id)
    if args.command == "activate":
        return command_activate(project_root, args.eval_id)
    if args.command == "run":
        return command_run(
            project_root,
            args.eval_id,
            only=args.only,
            limit_items=args.limit_items,
            force=args.force,
            trust_code=args.trust_code,
        )
    if args.command == "judge":
        return command_judge(
            project_root,
            args.eval_id,
            run_id=args.run_id,
            judge_id=args.judge_id,
            limit_items=args.limit_items,
        )
    if args.command == "promote":
        return command_promote(project_root, args.eval_id, args.to, dry_run=args.dry_run)
    if args.command == "review":
        return command_review(project_root, args)
    if args.command == "status":
        return command_status(project_root, args.eval_id)
    if args.command == "resume":
        return command_resume(project_root, args.run_id, trust_code=args.trust_code)
    if args.command == "rescore":
        return command_rescore(project_root, args.run_id)
    if args.command == "compare":
        return command_compare(project_root, args.eval_id)
    if args.command == "leaderboard":
        return command_leaderboard(project_root, args.eval_id)
    if args.command == "report":
        return command_report(project_root, args.eval_id)
    if args.command == "close":
        return command_close(
            project_root, args.eval_id, allow_incomplete_review=args.allow_incomplete_review
        )
    if args.command == "present":
        return command_present(project_root, args)
    if args.command == "analyze":
        return command_analyze(project_root, args)
    if args.command == "pack":
        case_dir = resolve_case(project_root, args.case)
        presentation = None
        if not args.without_presentation:
            presentation = present_case(case_dir)
        archive = pack_case(case_dir, args.output_dir, presentation_dir=presentation)
        verified = verify_archive(archive)
        print(f"Packed {archive}")
        print(f"Content digest: {verified.content_digest}")
        print(f"Archive SHA-256: {verified.archive_sha256}")
        return 0
    if args.command == "unpack":
        cases_dir = args.cases_dir or project_root / "cases"
        destination = unpack_archive(args.archive, cases_dir)
        print(f"Unpacked case: {destination}")
        return 0
    if args.command == "fork":
        cases_dir = args.cases_dir or project_root / "cases"
        destination = fork_archive(
            args.archive,
            cases_dir,
            args.publisher,
            name=args.name,
            version=args.version,
        )
        print(f"Created fork: {destination}")
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def command_lint(path: Path, *, portable: bool = False) -> int:
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
    case_dirs: set[Path] = set()
    for eval_path in eval_paths:
        eval_id = eval_id_from_path(project_root, eval_path)
        try:
            context = load_eval_context_from_path(project_root, eval_path)
            case_dirs.add(context.case_dir)
            _render_all_prompts(context)
            print(f"ok  {eval_id}")
        except TamesuError as exc:
            failures.append(str(exc))
            print(f"fail  {eval_id}", file=sys.stderr)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    if portable:
        for case_dir in sorted(case_dirs):
            validate_portable(case_dir)
            print(f"portable  {case_dir.name}")
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
        "output_usd_per_million\tmax_tokens\tcapabilities\timage_pricing"
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
            f"{spec.max_tokens}\t{','.join(sorted(spec.capabilities))}\t"
            f"{_image_pricing_label(spec.image_pricing)}"
        )
    print(f"Pricing verified: {PRICING_VERIFIED_AT}")
    return 0


def _image_pricing_label(pricing: Any) -> str:
    from .providers.models import FlatImagePricing, ImageMatrixPricing, ImageTokenPricing

    if pricing is None:
        return "-"
    if isinstance(pricing, FlatImagePricing):
        return f"flat ${pricing.usd_per_image:g}/image"
    if isinstance(pricing, ImageTokenPricing):
        return (
            f"tokens text-in ${pricing.text_input_usd_per_million:g}, image-in "
            f"${pricing.image_input_usd_per_million:g}, image-out "
            f"${pricing.image_output_usd_per_million:g} per M"
        )
    if isinstance(pricing, ImageMatrixPricing):
        return f"matrix ({len(pricing.usd_per_image)} quality/size prices)"
    return "unknown"


def command_plan(project_root: Path, eval_id: str) -> int:
    context = load_eval_context(project_root, eval_id)
    plan = build_plan(context)
    models = sorted({spec.model for spec in plan.specs})
    arms = sorted({spec.arm_id for spec in plan.specs})
    calls = sum(len(spec.item_ids) for spec in plan.owed_specs)
    print(f"Eval: {eval_id}")
    print(f"Status: {context.evaluation['status']}")
    print(f"Execution backend: {backend_for(context.evaluation).name}")
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
    backend = backend_for(context.evaluation)
    if backend.name == "native":
        for provider_name in sorted({spec.provider for spec in plan.owed_specs}):
            error = get_provider(provider_name).credential_error()
            if error:
                blockers.append(f"{provider_name}: {error}")
    else:
        try:
            backend.check_ready(context, list(plan.owed_specs), trust_code=False)
        except ExecutionError as exc:
            blockers.append(str(exc))
    if context.evaluation["status"] != "active":
        blockers.append(f"eval status is {context.evaluation['status']!r}")
    if blockers:
        print("Execution blockers:")
        for blocker in blockers:
            print(f"- {blocker}")
    else:
        print("Execution blockers: none")
    return 0


def command_activate(project_root: Path, eval_id: str) -> int:
    context = load_eval_context(project_root, eval_id)
    status = context.evaluation["status"]
    if status == "active":
        print(f"{eval_id} is already active.")
        return 0
    if status == "complete":
        raise TamesuError(
            f"Cannot activate {eval_id}: the eval is complete. Create a new eval or "
            "make an explicit reviewed manifest edit."
        )

    # Activation is the boundary between authoring and paid execution. Render every
    # prompt before crossing it so errors hidden in an unselected item cannot be
    # activated. This performs no provider calls and does not require credentials.
    _render_all_prompts(context)

    eval_path = context.eval_dir / "eval.yml"
    source = eval_path.read_text(encoding="utf-8")
    updated, replacements = re.subn(
        r"(?m)^status:\s*draft\s*$", "status: active", source, count=1
    )
    if replacements != 1:
        raise TamesuError(f"Could not update draft status in {eval_path}")
    write_text(eval_path, updated)
    load_eval_context(project_root, eval_id)
    print(f"Activated {eval_id}.")
    return 0


def command_run(
    project_root: Path,
    eval_id: str,
    *,
    only: str | None,
    limit_items: int | None,
    force: bool,
    trust_code: bool = False,
) -> int:
    context = load_eval_context(project_root, eval_id)
    run_ids = run_eval(
        context,
        only=only,
        limit_items=limit_items,
        force=force,
        trust_code=trust_code,
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
        for run_id in run_ids:
            if load_yaml(context.eval_dir / "runs" / run_id / "run.yml").get("state") == "partial":
                print(f"Resume without repeating finished work: tamesu resume {run_id}", file=sys.stderr)
        print(f"Inspect all evidence: tamesu status {eval_id}", file=sys.stderr)
        return 1
    return 0


def command_judge(
    project_root: Path,
    eval_id: str,
    *,
    run_id: str | None,
    judge_id: str | None,
    limit_items: int | None,
) -> int:
    from .judging import judge_eval

    context = load_eval_context(project_root, eval_id)
    summary = judge_eval(context, run_id=run_id, judge_id=judge_id, limit_items=limit_items)
    print(
        f"Judged {summary.judged} call(s): {summary.ok} ok, {summary.failed} failed; "
        f"cost_usd={summary.cost_usd:.6f}"
        + (f" (+{summary.unknown_costs} unknown)" if summary.unknown_costs else "")
    )
    for run, item, reason in summary.skipped:
        print(f"skipped\t{run}\t{item}\t{reason}")
    if summary.failed:
        print(
            f"{summary.failed} judgment(s) did not produce a verdict; "
            "generation results are unchanged. Re-run tamesu judge to try again.",
            file=sys.stderr,
        )
        return 1
    return 0


def command_promote(project_root: Path, eval_id: str, to: str, *, dry_run: bool) -> int:
    from .promote import execute_promotion, plan_promotion

    context = load_eval_context(project_root, eval_id)
    plan = plan_promotion(context, to)
    verb = "Would copy" if dry_run else "Copying"
    print(f"{verb} {len(plan.copies)} image(s) into {plan.destination.relative_to(context.project_root)}:")
    for source, target in plan.copies:
        print(f"  {source.relative_to(context.project_root)} -> images/{target.name}")
    for item, run, reason in plan.excluded:
        print(f"excluded\t{item}\t{run}\t{reason}")
    for product in plan.unpromoted_products:
        print(f"no-accepted-image\t{product}")
    if dry_run:
        print("Dry run: nothing was written.")
        return 0
    execute_promotion(context, plan)
    print(f"Created dataset {plan.dataset['name']} with {len(plan.dataset['items'])} item(s).")
    return 0


def command_review(project_root: Path, args: argparse.Namespace) -> int:
    from .review import export_pack, import_responses, review_status

    context = load_eval_context(project_root, args.eval_id)
    if args.review_command == "export":
        result = export_pack(
            context,
            args.out,
            run_id=args.run_id,
            reviewer=args.reviewer,
            seed=args.seed,
            include_reviewed=args.include_reviewed,
            force=args.force,
        )
        print(f"Exported {result.exported} image(s) to {result.out_dir} ({result.pack_id}).")
        print(f"Key (keep it away from reviewers): {result.key_path}")
        for run, item, reason in result.skipped:
            print(f"skipped\t{run}\t{item}\t{reason}")
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    if args.review_command == "import":
        result = import_responses(context, args.responses)
        print(
            f"Imported {len(result.written)} review(s) from {result.reviewer} for {result.pack_id}"
            + (f"; {result.skipped_blank} blank row(s) left owed." if result.skipped_blank else ".")
        )
        return 0
    status = review_status(context, args.run_id)
    print(f"Required reviews per item: {status['required_reviews_per_item']}")
    print(f"Reviewed: {status['reviewed']}  Awaiting review: {status['awaiting_review']}")
    if status["stale_records"]:
        print(f"Stale review records (image or rubric changed): {status['stale_records']}")
    for item in status["items"]:
        print(f"{item['run_id']}\t{item['item_id']}\treviews={item['reviews']}\towed={item['owed']}")
    for run, item, reason in status["skipped"]:
        print(f"skipped\t{run}\t{item}\t{reason}")
    return 0


def command_present(project_root: Path, args: argparse.Namespace) -> int:
    case_dir = resolve_case(project_root, args.case)

    def render() -> Path:
        return present_case(case_dir, args.output, strict=args.strict and not args.watch)

    if args.watch:
        from .present_watch import watch

        destination = render()
        if args.open:
            _open_in_browser(destination)
        print(f"Watching {case_dir} (Ctrl-C to stop)")
        try:
            watch(case_dir, render)
        except KeyboardInterrupt:
            print("stopped")
        return 0
    destination = render()
    print(f"Rendered case study: {destination}")
    if args.open:
        _open_in_browser(destination)
    return 0


def _open_in_browser(destination: Path) -> None:
    import webbrowser

    webbrowser.open((destination / "index.html").as_uri())


def command_analyze(project_root: Path, args: argparse.Namespace) -> int:
    from . import analyze as analyze_module

    context = load_eval_context(project_root, args.eval_id)
    if args.scaffold:
        print(f"Created {analyze_module.scaffold(context)}")
        return 0
    if not args.provider or not args.model:
        raise TamesuError(
            "Model-assisted analysis needs --provider and --model (it calls a model and costs money). "
            "Use --scaffold for an authored template with no model call."
        )
    path, cost = analyze_module.analyze(
        context, provider_name=args.provider, model=args.model, force=args.force, max_cost_usd=args.max_cost
    )
    spent = f"${cost:.4f}" if isinstance(cost, (int, float)) else "unknown cost"
    print(f"Wrote {path} (model-assisted, {spent}). `tamesu present` labels it as such.")
    return 0


def command_status(project_root: Path, eval_id: str) -> int:
    context = load_eval_context(project_root, eval_id)
    plan = build_plan(context)
    summary = status_summary(plan)
    counts = summary["counts"]
    from .lifecycle import lifecycle_for

    state = lifecycle_for(context, plan, counts)
    print(f"State: {state.label} — {state.reason}")
    for modifier in state.modifiers:
        print(f"  {modifier}")
    for key in ("planned", "banked", "owed", "partial", "failed", "stale", "extra"):
        print(f"{key.capitalize()}: {counts[key]}")
    for run in summary["runs"]:
        print(f"{run['classification']}\t{run['run_id']}\t{run['state']}")
    _print_image_owed_work(plan)
    return 0


def _print_image_owed_work(plan: Any) -> None:
    """Owed post-generation work and stale evidence, for image evals only."""
    from .tasks import task_for

    context = plan.context
    if not task_for(context.evaluation, context.case).supports_review:
        return
    from .acceptance import run_evidence

    for run_id in sorted(plan.banked_run_ids.values()):
        evidence = run_evidence(context, context.eval_dir / "runs" / run_id)
        counts, columns = evidence["counts"], evidence["columns"]
        parts = [
            f"accepted={counts['accepted']}",
            f"rejected={counts['rejected']}",
            f"pending={counts['pending']}",
        ]
        if context.evaluation["evaluation"].get("model_judges"):
            judge = columns["model_judge"]
            parts.append(f"awaiting_judgment={judge['of_generated'] - judge['judged']}")
        if context.evaluation["evaluation"].get("human_review"):
            parts.append(f"awaiting_review={evidence['review']['owed_reviews']}")
        stale = counts["stale_images"] + counts["stale_judgments"] + counts["stale_reviews"]
        if stale:
            parts.append(
                f"stale_evidence={stale} (images={counts['stale_images']}, "
                f"judgments={counts['stale_judgments']}, reviews={counts['stale_reviews']})"
            )
        print(f"evidence\t{run_id}\t" + " ".join(parts))


def command_resume(project_root: Path, run_id: str, *, trust_code: bool = False) -> int:
    run_dir = find_run_dir(project_root, run_id)
    manifest = load_yaml(run_dir / "run.yml")
    context = load_eval_context(project_root, manifest["eval_id"])
    print(resume_run(context, run_id, trust_code=trust_code))
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


def command_report(project_root: Path, eval_id: str) -> int:
    plan = build_plan(load_eval_context(project_root, eval_id))
    print(build_evaluation_report(plan))
    return 0


def command_close(
    project_root: Path, eval_id: str, *, allow_incomplete_review: bool = False
) -> int:
    context = load_eval_context(project_root, eval_id)
    if context.evaluation["status"] == "complete":
        print(f"{eval_id} is already complete.")
        return 0
    plan = build_plan(context)
    if plan.owed_specs:
        raise TamesuError(
            f"Cannot close {eval_id}: {len(plan.owed_specs)} planned run(s) are still owed."
        )
    closing = _close_review_gate(context, plan, allow_incomplete_review)
    for run_id in plan.banked_run_ids.values():
        if not (context.eval_dir / "runs" / run_id / "report.yml").is_file():
            raise TamesuError(f"Cannot close {eval_id}: run {run_id} has no report.yml.")
    if closing is not None:
        write_yaml(context.eval_dir / "closing.yml", closing)

    eval_path = context.eval_dir / "eval.yml"
    source = eval_path.read_text(encoding="utf-8")
    updated, replacements = re.subn(
        r"(?m)^status:\s*(draft|active|complete)\s*$", "status: complete", source, count=1
    )
    if replacements != 1:
        raise TamesuError(f"Could not update status in {eval_path}")
    write_text(eval_path, updated)
    closed_context = load_eval_context(project_root, eval_id)
    from .analysis import stamp_existing

    stamp_existing(closed_context)  # bind an existing analysis to the final evidence; never create one
    closed_plan = build_plan(closed_context)
    build_leaderboard(closed_plan)
    build_evaluation_report(closed_plan)
    print(f"Closed {eval_id}.")
    return 0


def _close_review_gate(context: Any, plan: Any, allow_incomplete: bool) -> dict[str, Any] | None:
    """For image evals: refuse to close while required judgments or reviews are missing."""
    from .tasks import task_for

    if not task_for(context.evaluation, context.case).supports_review:
        return None
    from .acceptance import run_evidence
    from datetime import UTC, datetime

    runs: dict[str, Any] = {}
    owed = 0
    for run_id in sorted(plan.banked_run_ids.values()):
        run_dir = context.eval_dir / "runs" / run_id
        evidence = run_evidence(context, run_dir)
        counts = evidence["counts"]
        stale = counts["stale_images"] + counts["stale_judgments"] + counts["stale_reviews"]
        runs[run_id] = {
            "accepted": counts["accepted"],
            "rejected": counts["rejected"],
            "pending": counts["pending"],
            "owed_reviews": evidence["review"]["owed_reviews"],
            "stale_evidence_records": stale,
        }
        owed += counts["pending"]
    if owed and not allow_incomplete:
        lines = [
            f"- {run_id}: {info['pending']} item(s) pending "
            f"({info['owed_reviews']} review(s) owed)"
            for run_id, info in runs.items()
            if info["pending"]
        ]
        raise TamesuError(
            f"Cannot close {context.eval_id}: required evidence is missing.\n"
            + "\n".join(lines)
            + "\nImport the missing reviews or judgments, or pass --allow-incomplete-review "
            "to close anyway (the gap is recorded in closing.yml)."
        )
    return {
        "schema_version": 1,
        "eval_id": context.eval_id,
        "closed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "allow_incomplete_review": bool(allow_incomplete),
        "incomplete_evidence": bool(owed),
        "pending_items": owed,
        "runs": runs,
    }


def _render_all_prompts(context: Any) -> None:
    from .planner import build_plan, items_by_id
    from .tasks import task_for

    task = task_for(context.evaluation, context.case)
    item_map = items_by_id(context)
    for spec in build_plan(context).specs:
        for item_id in spec.item_ids:
            task.render_prompts(spec.prompts, item_map[item_id])


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    main()
