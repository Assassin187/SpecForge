from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.coder.generation import DEFAULT_MAX_REPAIR_ROUNDS, ProjectGenerator, _bundle_binary_name
from agent.coder.llm_client import FixedQwenClient
from agent.coder.verifier import ProjectVerifier

from .configs import REPO_ROOT
from .view_prompts import ViewPromptBuilder
from .view_specs import (
    AblationViewContext,
    UnsupportedViewError,
    ViewSpecError,
    load_ablation_view_context,
    prompt_forbidden_terms,
    visible_boundary_name,
)


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "evaluation" / "spec_ablation" / "out"


class PromptLeakageError(RuntimeError):
    """Raised when a view prompt crosses its allowed visibility boundary."""


def _path(value: str) -> Path:
    return Path(value).expanduser()


@dataclass
class PromptAudit:
    output_dir: Path
    view_name: str
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.output_dir / "_view_logs" / "prompt_audit.json"

    def __call__(self, stage: str, subject: str, messages: list[dict[str, str]]) -> None:
        payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        violations = [term for term in prompt_forbidden_terms(self.view_name) if term in payload]
        record = {
            "view": self.view_name,
            "stage": stage,
            "subject": subject,
            "message_count": len(messages),
            "request_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "request_bytes": len(payload.encode("utf-8")),
            "visible_boundary": visible_boundary_name(self.view_name),
            "leakage_scan": {
                "status": "failed" if violations else "passed",
                "violations": violations,
            },
        }
        self.records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if violations:
            raise PromptLeakageError(f"prompt leakage detected for {stage}:{subject}: {violations}")


def default_output_dir(context: AblationViewContext, round_number: int = 1) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    protocol = str(context.transformation_manifest.get("protocol") or _bundle_binary_name(context.source_bundle))
    return DEFAULT_OUTPUT_ROOT / f"{timestamp}_{protocol}_{context.view_name.upper()}_round_{round_number}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate code from SpecFS ablation specification views")
    parser.add_argument("--view", default="s1", help="Ablation view to use: s1 or s2")
    parser.add_argument("--view-root", required=True, help="Path to transformed view artifacts or its specfs_projection directory")
    parser.add_argument("--full-spec-root", default=None, help="Optional source oracle override for deterministic assembly")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--round-number", type=int, default=1, help="Experiment round number used in the default output directory name")
    parser.add_argument("--api-key-env", default="ALI_API", help="Environment variable containing the Qwen API key")
    parser.add_argument("--max-repair-rounds", type=int, default=DEFAULT_MAX_REPAIR_ROUNDS)
    parser.add_argument(
        "--skip-repair",
        action="store_true",
        help="Stop after code generation without building the project, repairing, or running behavior checks.",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate view artifacts and the internal source oracle")
    sub.add_parser("generate", help="Generate a protocol project from the ablation view")
    repair = sub.add_parser("repair", help="Compile and repair an existing protocol project")
    repair.add_argument(
        "--project-dir",
        required=True,
        type=_path,
        help="Existing protocol project directory containing a Makefile",
    )
    sub.add_parser("verify", help="Verify generated project structure, compile, and behavior")
    return parser


def _load_context(args: argparse.Namespace) -> AblationViewContext:
    return load_ablation_view_context(args.view_root, view_name=args.view, full_spec_root=args.full_spec_root)


def _print_context_summary(context: AblationViewContext) -> None:
    payload = {
        "view": context.view_name,
        "view_root": str(context.view_root),
        "protocol": context.transformation_manifest.get("protocol", context.source_bundle.protocol.name),
        "counts": context.transformation_manifest.get("counts", {}),
        "source_root": str(context.source_bundle.spec_root),
        "transformation_hash": context.transformation_manifest.get("transformation_hash", ""),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _generator(context: AblationViewContext, output_dir: Path, args: argparse.Namespace) -> ProjectGenerator:
    builder = ViewPromptBuilder(context)
    audit = PromptAudit(output_dir=output_dir, view_name=context.view_name)
    return ProjectGenerator(
        bundle=context.source_bundle,
        llm_client=FixedQwenClient(args.api_key_env),
        output_dir=output_dir,
        max_repair_rounds=args.max_repair_rounds,
        skip_repair=args.skip_repair,
        source_prompt_builder=builder.build_source_prompt,
        main_source_prompt_builder=builder.build_main_source_prompt,
        repair_prompt_builder=builder.build_repair_prompt,
        prompt_observer=audit,
    )


def cmd_validate(args: argparse.Namespace) -> int:
    context = _load_context(args)
    _print_context_summary(context)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    context = _load_context(args)
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else default_output_dir(context, args.round_number)
    result = _generator(context, output_dir, args).generate()
    print(f"Manifest: {result.manifest_path}")
    if result.compile_stdout:
        print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    if args.skip_repair:
        print("Code generation completed; project build, repair, and behavior checks were skipped.")
    elif result.success:
        print("Generation succeeded: final compile passed.")
    else:
        print(f"Repair stop reason: {result.repair_stop_reason}")
        for path in result.repair_blocking_files:
            print(f"- {path}")
    return 0 if result.success else 1


def cmd_repair(args: argparse.Namespace) -> int:
    context = _load_context(args)
    project_dir = Path(args.project_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else project_dir.parent
    result = _generator(context, output_dir, args).repair_existing(project_dir)
    print(f"Manifest: {result.manifest_path}")
    if result.compile_stdout:
        print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    context = _load_context(args)
    if not args.output_dir:
        raise ViewSpecError("--output-dir is required for verify")
    result = ProjectVerifier(context.source_bundle, Path(args.output_dir).expanduser()).verify()
    for scenario in result.scenarios:
        print(f"{scenario['status'].upper()} {scenario['name']}: {scenario['detail']}")
    for diag in result.diagnostics:
        path = f" [{diag.path}]" if diag.path else ""
        print(f"{diag.level.upper()} {diag.code}{path}: {diag.message}")
    if result.compile_stdout:
        print(result.compile_stdout)
    if result.compile_stderr:
        print(result.compile_stderr)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "generate":
            return cmd_generate(args)
        if args.command == "repair":
            return cmd_repair(args)
        if args.command == "verify":
            return cmd_verify(args)
    except UnsupportedViewError as exc:
        print(f"ERROR unsupported_reserved_view: {exc}")
        return 2
    except (ViewSpecError, PromptLeakageError) as exc:
        print(f"ERROR view_generation: {exc}")
        return 1
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
