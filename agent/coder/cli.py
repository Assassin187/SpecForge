from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .generation import ProjectGenerator
from .llm_client import FixedQwenClient
from .specs import load_spec_bundle
from .verifier import ProjectVerifier


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_ROOT = REPO_ROOT / "specs-example" / "mqtt_specs"
DEFAULT_MODULE_SPEC = DEFAULT_SPEC_ROOT / "mqtt_module_spec.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "agent" / "out"


def _path(value: str) -> Path:
    return Path(value).expanduser()


def default_output_dir() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_ROOT / f"mqtt_broker_{timestamp}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MQTT spec-to-code agent")
    parser.add_argument("--module-spec", default=str(DEFAULT_MODULE_SPEC))
    parser.add_argument("--spec-root", default=str(DEFAULT_SPEC_ROOT))
    parser.add_argument("--output-dir", default=str(default_output_dir()))
    parser.add_argument("--max-repair-rounds", type=int, default=3)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate specs and LLM adapter prerequisites")
    sub.add_parser("generate", help="Generate the MQTT broker project from specs")
    sub.add_parser("verify", help="Verify generated project structure, build, and smoke test")
    return parser


def _print_diagnostics(bundle) -> None:
    if not bundle.diagnostics:
        print("No diagnostics.")
        return
    for diag in bundle.diagnostics:
        prefix = diag.level.upper()
        path = f" [{diag.path}]" if diag.path else ""
        print(f"{prefix} {diag.code}{path}: {diag.message}")


def cmd_validate(args: argparse.Namespace) -> int:
    bundle = load_spec_bundle(_path(args.module_spec), _path(args.spec_root))
    llm = FixedQwenClient()
    _print_diagnostics(bundle)
    try:
        status = llm.self_check()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR llm_self_check: {exc}")
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 1 if bundle.has_errors() else 0


def cmd_generate(args: argparse.Namespace) -> int:
    bundle = load_spec_bundle(_path(args.module_spec), _path(args.spec_root))
    _print_diagnostics(bundle)
    if bundle.has_errors():
        print("Generation aborted because spec validation reported errors.")
        return 1
    generator = ProjectGenerator(
        bundle=bundle,
        llm_client=FixedQwenClient(),
        output_dir=_path(args.output_dir),
        max_repair_rounds=args.max_repair_rounds,
    )
    result = generator.generate()
    print(f"Manifest: {result.manifest_path}")
    print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    bundle = load_spec_bundle(_path(args.module_spec), _path(args.spec_root))
    _print_diagnostics(bundle)
    verifier = ProjectVerifier(bundle, _path(args.output_dir))
    result = verifier.verify()
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
