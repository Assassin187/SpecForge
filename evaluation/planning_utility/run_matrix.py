from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.coder.llm_client import FixedQwenClient

from .baseline_runner import BASELINE_METHODS, FSDirectCoderRunner, NLPlanCodeRunner
from .configs import DEFAULT_OUTPUT_ROOT, PROTOCOLS, ProtocolConfig, rel_to_repo
from .full_specforge_adapter import run_full_specforge
from .requirements import write_json


METHOD_FULL = "full-specforge"
METHODS = (*BASELINE_METHODS, METHOD_FULL)


def _parse_planning_dirs(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--full-planning-dir must be protocol=PATH, got: {value}")
        protocol, raw_path = value.split("=", 1)
        if protocol not in PROTOCOLS:
            raise SystemExit(f"Unknown protocol in --full-planning-dir: {protocol}")
        result[protocol] = Path(raw_path).expanduser()
    return result


def _run_method(
    method: str,
    config: ProtocolConfig,
    method_dir: Path,
    *,
    api_key_env: str,
    max_repair_rounds: int,
    max_repair_calls: int,
    full_planning_dirs: dict[str, Path],
) -> dict[str, Any]:
    if method == "fs-direct-coder":
        runner = FSDirectCoderRunner(
            config,
            method_dir,
            llm_client=FixedQwenClient(api_key_env),
            max_repair_rounds=max_repair_rounds,
            max_repair_calls=max_repair_calls,
        )
        return runner.run().summary
    if method == "nl-plan-code":
        runner = NLPlanCodeRunner(
            config,
            method_dir,
            llm_client=FixedQwenClient(api_key_env),
            max_repair_rounds=max_repair_rounds,
            max_repair_calls=max_repair_calls,
        )
        return runner.run().summary
    if method == METHOD_FULL:
        return run_full_specforge(
            config,
            method_dir,
            api_key_env=api_key_env,
            max_repair_rounds=max_repair_rounds,
            existing_planning_dir=full_planning_dirs.get(config.protocol),
        )
    raise ValueError(f"Unsupported method: {method}")


def _write_matrix_outputs(matrix_dir: Path, summaries: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": "planning_utility_matrix_summary/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "matrix_dir": rel_to_repo(matrix_dir),
        "runs": summaries,
    }
    write_json(matrix_dir / "matrix_summary.json", payload)
    lines = [
        "# Planning Utility Matrix Summary",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- matrix_dir: `{payload['matrix_dir']}`",
        "",
        "| method | protocol | planning | structure | codegen | static | compile | smoke | failure_stage | diagnostic |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        diagnostic = str(item.get("main_diagnostic") or "").replace("|", "\\|")
        if len(diagnostic) > 180:
            diagnostic = diagnostic[:177] + "..."
        structure = _first_status(item, "source_tree_skeleton_status", "strategy_generation_status", "readiness_status")
        codegen = _first_status(item, "pair_completion_status", "file_generation_status")
        lines.append(
            "| {method} | {protocol} | {planning} | {structure} | {codegen} | {static} | {compile} | {smoke} | {failure} | {diag} |".format(
                method=item.get("method", ""),
                protocol=item.get("protocol", ""),
                planning=item.get("planning_status", ""),
                structure=structure,
                codegen=codegen,
                static=item.get("static_check_status", item.get("schema_loader_rendered_header_status", "")),
                compile=item.get("compile_status", ""),
                smoke=item.get("smoke_status", ""),
                failure=item.get("failure_stage", ""),
                diag=diagnostic,
            )
        )
    (matrix_dir / "matrix_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _first_status(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value and value not in {"not_applicable", "not_run"}:
            return str(value)
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run planning-utility baseline matrix")
    parser.add_argument("--protocol", action="append", choices=sorted(PROTOCOLS), help="Protocol(s) to run; default: all")
    parser.add_argument("--method", action="append", choices=METHODS, help="Method(s) to run; default: all")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--api-key-env", default="ALI_API")
    parser.add_argument("--max-repair-rounds", type=int, default=3, help="Repair rounds for full-specforge only")
    parser.add_argument("--max-repair-calls", type=int, default=6, help="Bounded generic C repair calls for fs-direct-coder and nl-plan-code")
    parser.add_argument("--full-planning-dir", action="append", default=[], help="Existing Full SpecForge planning run as protocol=PATH")
    parser.add_argument("--fail-on-method-failure", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    protocols = args.protocol or sorted(PROTOCOLS)
    methods = args.method or list(METHODS)
    full_planning_dirs = _parse_planning_dirs(args.full_planning_dir)
    matrix_dir = Path(args.output_root).expanduser() / datetime.now().strftime("%Y%m%d_%H%M%S")
    matrix_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for protocol in protocols:
        for method in methods:
            summaries.append(
                _run_method(
                    method,
                    PROTOCOLS[protocol],
                    matrix_dir / protocol / method,
                    api_key_env=args.api_key_env,
                    max_repair_rounds=args.max_repair_rounds,
                    max_repair_calls=args.max_repair_calls,
                    full_planning_dirs=full_planning_dirs,
                )
            )
    _write_matrix_outputs(matrix_dir, summaries)
    print(f"Matrix summary: {matrix_dir / 'matrix_summary.json'}")
    if args.fail_on_method_failure and any(item.get("failure_stage") for item in summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
