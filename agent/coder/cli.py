from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .generation import ProjectGenerator, _bundle_binary_name
from .llm_client import FixedQwenClient
from .specs import load_spec_bundle, load_spec_bundle_from_root
from .verifier import ProjectVerifier


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_ROOT = REPO_ROOT / "specs-example" / "mqtt_specs"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "agent" / "out"


def _path(value: str) -> Path:
    return Path(value).expanduser()


def default_output_dir(bundle=None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = _bundle_binary_name(bundle) if bundle is not None else "mqtt_broker"
    return DEFAULT_OUTPUT_ROOT / f"{prefix}_{timestamp}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Protocol spec-to-code agent")
    parser.add_argument("--spec-root", default=str(DEFAULT_SPEC_ROOT))
    parser.add_argument(
        "--module-spec",
        default=None,
        help="Optional override; by default coder auto-discovers the module spec under --spec-root.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-repair-rounds", type=int, default=3)
    parser.add_argument("--api-key-env", default="ALI_API", help="Environment variable containing the Qwen API key")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate specs and LLM adapter prerequisites")
    sub.add_parser("generate", help="Generate the protocol project from specs")
    sub.add_parser("verify", help="Verify generated project structure, build, and smoke test")
    return parser


def _load_bundle(args: argparse.Namespace):
    spec_root = _path(args.spec_root)
    if args.module_spec:
        return load_spec_bundle(_path(args.module_spec), spec_root)
    return load_spec_bundle_from_root(spec_root)


def _print_diagnostics(bundle) -> None:
    if not bundle.diagnostics:
        print("No diagnostics.")
        return
    for diag in bundle.diagnostics:
        prefix = diag.level.upper()
        path = f" [{diag.path}]" if diag.path else ""
        print(f"{prefix} {diag.code}{path}: {diag.message}")


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        bundle = _load_bundle(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR spec_discovery: {exc}")
        return 1
    llm = FixedQwenClient(args.api_key_env)
    _print_diagnostics(bundle)
    try:
        status = llm.self_check()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR llm_self_check: {exc}")
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 1 if bundle.has_errors() else 0


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        bundle = _load_bundle(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR spec_discovery: {exc}")
        return 1
    _print_diagnostics(bundle)
    if bundle.has_errors():
        print("Generation aborted because spec validation reported errors.")
        return 1
    generator = ProjectGenerator(
        bundle=bundle,
        llm_client=FixedQwenClient(args.api_key_env),
        output_dir=_path(args.output_dir) if args.output_dir else default_output_dir(bundle),
        max_repair_rounds=args.max_repair_rounds,
    )
    result = generator.generate()
    print(f"Manifest: {result.manifest_path}")
    print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    if result.success:
        print("Generation succeeded: final compile passed.")
    else:
        print(f"Repair stop reason: {result.repair_stop_reason}")
        if result.repair_blocking_files:
            print("Repair blocking files:")
            for path in result.repair_blocking_files:
                print(f"- {path}")
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        bundle = _load_bundle(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR spec_discovery: {exc}")
        return 1
    _print_diagnostics(bundle)
    verifier = ProjectVerifier(bundle, _path(args.output_dir) if args.output_dir else default_output_dir(bundle))
    result = verifier.verify()
    for scenario in result.scenarios:
        print(f"{scenario['status'].upper()} {scenario['name']}: {scenario['detail']}")
    for diag in result.diagnostics:
        prefix = diag.level.upper()
        path = f" [{diag.path}]" if diag.path else ""
        print(f"{prefix} {diag.code}{path}: {diag.message}")
    if result.compile_stdout:
        print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "generate":
        return cmd_generate(args)
    if args.command == "verify":
        return cmd_verify(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2
