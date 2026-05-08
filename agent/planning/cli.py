from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..common.llm_client import FixedQwenClient
from .planner import PlanningAgent
from .verifier import verify_output_dir


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-grounded protocol implementation planning agent")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate facts input, target profile, and planning preconditions")
    validate.add_argument("--facts", required=True)
    validate.add_argument("--target-profile", required=True)
    validate.add_argument("--skip-llm-check", action="store_true")

    plan = sub.add_parser("plan", help="Run planning pipeline and compile coder-compatible specs")
    plan.add_argument("--facts", required=True)
    plan.add_argument("--target-profile", required=True)
    plan.add_argument("--output-dir")

    verify = sub.add_parser("verify", help="Verify an existing planning output directory")
    verify.add_argument("--output-dir", required=True)
    return parser


def _print_diagnostics(diags) -> None:
    if not diags:
        print("No diagnostics.")
        return
    for diag in diags:
        path = f" [{diag.path}]" if getattr(diag, "path", None) else ""
        print(f"{diag.level.upper()} {diag.code}{path}: {diag.message}")


def _optional_llm_client() -> FixedQwenClient | None:
    return FixedQwenClient() if os.getenv("ALI_API") else None


def cmd_validate(args: argparse.Namespace) -> int:
    agent = PlanningAgent(
        facts_path=_path(args.facts),
        target_profile_path=_path(args.target_profile),
        llm_client=None if args.skip_llm_check else _optional_llm_client(),
    )
    diagnostics = agent.validate_inputs(skip_llm_check=args.skip_llm_check)
    _print_diagnostics(diagnostics)
    print(
        json.dumps(
            {
                "facts": str(_path(args.facts)),
                "target_profile": str(_path(args.target_profile)),
                "skip_llm_check": args.skip_llm_check,
                "output_dir": str(agent.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(diag.level == "error" for diag in diagnostics) else 0


def cmd_plan(args: argparse.Namespace) -> int:
    agent = PlanningAgent(
        facts_path=_path(args.facts),
        target_profile_path=_path(args.target_profile),
        output_dir=_path(args.output_dir) if args.output_dir else None,
        llm_client=_optional_llm_client(),
    )
    result = agent.plan()
    _print_diagnostics(result.diagnostics)
    print(f"Output: {result.output_dir}")
    print(f"Logs: {result.output_dir / '_agent_logs'}")
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_output_dir(_path(args.output_dir))
    _print_diagnostics(result.diagnostics)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "verify":
        return cmd_verify(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2
