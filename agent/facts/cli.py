from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..common.llm_client import FixedQwenClient
from .extraction import FactsExtractor, default_output_dir, validate_document_inputs
from .verifier import verify_facts_output


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Protocol document to facts agent")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate document inputs and preprocessing prerequisites")
    validate.add_argument("--protocol-name", required=True)
    validate.add_argument("--doc", action="append", required=True)
    validate.add_argument("--skip-llm-check", action="store_true")

    extract = sub.add_parser("extract", help="Extract structured protocol facts from a document set")
    extract.add_argument("--protocol-name", required=True)
    extract.add_argument("--doc", action="append", required=True)
    extract.add_argument("--output-dir")

    verify = sub.add_parser("verify", help="Verify protocol_facts.json completeness for planning")
    verify.add_argument("--output-dir", required=True)
    return parser


def _print_diagnostics(diags) -> None:
    if not diags:
        print("No diagnostics.")
        return
    for diag in diags:
        path = f" [{diag.path}]" if getattr(diag, "path", None) else ""
        print(f"{diag.level.upper()} {diag.code}{path}: {diag.message}")


def cmd_validate(args: argparse.Namespace) -> int:
    context = validate_document_inputs(
        args.protocol_name,
        [_path(path) for path in args.doc],
        llm_client=FixedQwenClient(),
        skip_llm_check=args.skip_llm_check,
    )
    _print_diagnostics(context.diagnostics)
    print(
        json.dumps(
            {
                "protocol_name": context.protocol_name,
                "document_count": len(context.docs),
                "chunk_count": len(context.chunks),
                "skip_llm_check": args.skip_llm_check,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if context.has_errors() else 0


def cmd_extract(args: argparse.Namespace) -> int:
    output_dir = _path(args.output_dir) if args.output_dir else default_output_dir(args.protocol_name)
    extractor = FactsExtractor(
        protocol_name=args.protocol_name,
        doc_paths=[_path(path) for path in args.doc],
        output_dir=output_dir,
        llm_client=FixedQwenClient(),
    )
    result = extractor.extract()
    _print_diagnostics(result.diagnostics)
    print(f"Manifest: {result.manifest_path}")
    print(f"Facts: {result.facts_path}")
    return 0 if result.success else 1


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_facts_output(_path(args.output_dir))
    _print_diagnostics(result.diagnostics)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "extract":
        return cmd_extract(args)
    if args.command == "verify":
        return cmd_verify(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2
