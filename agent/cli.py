from __future__ import annotations

import argparse
import sys

from .coder.cli import main as coder_main
from .facts.cli import main as facts_main
from .planning.cli import main as planning_main


LEGACY_CODER_COMMANDS = {"validate", "generate", "verify"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-agent protocol tooling")
    parser.add_argument("agent", nargs="?", help="Agent name: coder | facts")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    if not raw_argv:
        parser = build_parser()
        parser.print_help()
        return 0

    if raw_argv == ["-h"] or raw_argv == ["--help"]:
        parser = build_parser()
        parser.print_help()
        return 0

    first = raw_argv[0]
    if first in LEGACY_CODER_COMMANDS:
        return coder_main(raw_argv)

    if first == "coder":
        return coder_main(raw_argv[1:])

    if first == "facts":
        return facts_main(raw_argv[1:])

    if first == "planning":
        return planning_main(raw_argv[1:])

    # Preserve the previous "agent defaults to coder" behavior when users pass
    # coder-style global flags directly to the root entrypoint.
    if first.startswith("-"):
        return coder_main(raw_argv)

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    parser.error(f"Unsupported agent: {args.agent}")
    return 2
