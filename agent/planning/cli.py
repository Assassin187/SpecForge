from __future__ import annotations

import argparse
from pathlib import Path

from .diagnostics import PlanningDiagnostic
from .config import PlanningConfig
from .orchestrator import PlanningAgent, RESUME_STAGE_OPTIONS, STOP_AFTER_STAGE_OPTIONS, compare_output_to_reference, verify_output_dir


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Planning Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate planning inputs and write a preflight manifest")
    validate.add_argument("--facts", required=True)
    validate.add_argument("--target-profile", required=True)
    validate.add_argument("--output-dir")

    plan = sub.add_parser("plan", help="Run LLM-mandatory planning and compile a coder-compatible spec bundle")
    plan.add_argument("--facts", required=True)
    plan.add_argument("--target-profile", required=True)
    plan.add_argument("--output-dir")
    plan.add_argument("--resume-from-stage", choices=RESUME_STAGE_OPTIONS)
    plan.add_argument("--resume-source-dir", help="Planning Agent output directory to inherit artifacts from")
    plan.add_argument("--stop-after-stage", choices=STOP_AFTER_STAGE_OPTIONS)

    verify = sub.add_parser("verify", help="Verify an existing Planning Agent output directory")
    verify.add_argument("--output-dir", required=True)

    compare = sub.add_parser("compare", help="Compare output against a reference coder spec bundle")
    compare.add_argument("--output-dir", required=True)
    compare.add_argument("--reference-spec-root", required=True)
    return parser


def _print_diagnostics(diagnostics: list[PlanningDiagnostic]) -> None:
    if not diagnostics:
        print("No diagnostics.")
        return
    for diag in diagnostics:
        path = f" [{diag.path}]" if diag.path else ""
        print(f"{diag.level.upper()} {diag.code}{path}: {diag.message}")


def cmd_validate(args: argparse.Namespace) -> int:
    agent = PlanningAgent(
        _path(args.facts),
        _path(args.target_profile),
        output_dir=_path(args.output_dir) if args.output_dir else None,
        config=PlanningConfig(),
    )
    result = agent.validate()
    _print_diagnostics(result.diagnostics)
    print(f"Output: {result.output_dir}")
    return 0 if result.success else 1


def cmd_plan(args: argparse.Namespace) -> int:
    agent = PlanningAgent(
        _path(args.facts),
        _path(args.target_profile),
        output_dir=_path(args.output_dir) if args.output_dir else None,
        config=PlanningConfig(),
    )
    result = agent.plan(
        resume_from_stage=args.resume_from_stage,
        resume_source_dir=_path(args.resume_source_dir) if args.resume_source_dir else None,
        stop_after_stage=args.stop_after_stage,
    )
    _print_diagnostics(result.diagnostics)
    print(f"Output: {result.output_dir}")
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_output_dir(_path(args.output_dir))
    _print_diagnostics(result.diagnostics)
    return 0 if result.success else 1


def cmd_compare(args: argparse.Namespace) -> int:
    result = compare_output_to_reference(_path(args.output_dir), _path(args.reference_spec_root))
    _print_diagnostics(result.diagnostics)
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "compare":
        return cmd_compare(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2
