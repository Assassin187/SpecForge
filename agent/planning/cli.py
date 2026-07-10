from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_planning, validate_existing_run
from .planner import planning_resume_points, planning_stage_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SpecForge planning agent")
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="Generate protocol specs from protocol facts")
    plan.add_argument("--facts", required=True, help="Path to protocol_facts.json")
    plan.add_argument("--out", required=True, help="Output directory for planning artifacts and specs")
    plan.add_argument("--planner-mode", choices=["llm"], default="llm", help="Structured planner provider")
    plan.add_argument("--api-key-env", default="ALI_API", help="API key environment variable for --planner-mode llm")
    plan.add_argument("--no-coder-validate", action="store_true", help="Skip coder loader/header validation")
    plan.add_argument("--resume-from", choices=planning_resume_points(), help="Reuse previous stage logs and resume from this stage or compile_specs")

    validate = subparsers.add_parser("validate", help="Re-run planning checks for an existing run directory")
    validate.add_argument("--run-dir", required=True, help="Directory containing _planning/run_manifest.json")
    validate.add_argument("--no-coder-validate", action="store_true", help="Skip coder loader/header validation")

    subparsers.add_parser("stages", help="Print the Structured Planning stage decomposition")

    return parser


def _print_result(result) -> None:
    errors = [diag for diag in result.diagnostics if diag.level == "error"]
    warnings = [diag for diag in result.diagnostics if diag.level == "warning"]
    print(f"specs_root: {result.specs_root}")
    print(f"planning_manifest: {result.manifest_path}")
    print(f"diagnostics: {len(errors)} error(s), {len(warnings)} warning(s)")
    for diag in errors[:20]:
        location = f" ({diag.path})" if diag.path else ""
        print(f"ERROR {diag.code}: {diag.message}{location}")
    for diag in warnings[:10]:
        location = f" ({diag.path})" if diag.path else ""
        print(f"WARNING {diag.code}: {diag.message}{location}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = run_planning(
            Path(args.facts),
            Path(args.out),
            planner_mode=args.planner_mode,
            api_key_env=args.api_key_env,
            coder_validate=not args.no_coder_validate,
            resume_from=args.resume_from,
        )
        _print_result(result)
        return 0 if result.success else 1
    if args.command == "validate":
        result = validate_existing_run(Path(args.run_dir), coder_validate=not args.no_coder_validate)
        _print_result(result)
        return 0 if result.success else 1
    if args.command == "stages":
        for stage in planning_stage_catalog():
            print(f"{stage['stage_id']}: {stage['title']}")
            print(f"  {stage['purpose']}")
        return 0
    parser.print_help()
    return 0
