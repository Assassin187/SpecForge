from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.coder.llm_client import FixedQwenClient

from .bounded_repair import BASELINE_REPAIR_METHODS, BoundedCRepairRunner, RepairConfig
from .configs import DEFAULT_OUTPUT_ROOT, PROTOCOLS
from .requirements import write_json


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-") or "repair"


def _source_round(project_dir: Path) -> str:
    for parent in (project_dir, *project_dir.parents):
        match = re.search(r"_round_(\d+)$", parent.name)
        if match:
            return match.group(1)
    return ""


def _repair_group_name(rows: list[dict[str, str]], now: datetime) -> str:
    protocols = sorted({_safe_segment(row["protocol"]) for row in rows})
    protocol_label = protocols[0] if len(protocols) == 1 else "multi_protocol"
    rounds = sorted({_source_round(Path(row["project_dir"]).expanduser()) for row in rows} - {""})
    round_suffix = f"_round_{rounds[0]}" if len(rounds) == 1 else ""
    return f"{now.strftime('%Y%m%d')}_{protocol_label}_after_repair{round_suffix}"


def _fresh_run_root(rows: list[dict[str, str]], now: datetime | None = None) -> Path:
    current = now or datetime.now()
    group_root = DEFAULT_OUTPUT_ROOT / _repair_group_name(rows, current)
    group_root.mkdir(parents=True, exist_ok=True)
    label = current.strftime("%Y%m%d_%H%M%S")
    base = group_root / label
    if not base.exists():
        base.mkdir(parents=True, exist_ok=False)
        return base
    for index in range(2, 1000):
        candidate = group_root / f"{label}_{index}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
    raise RuntimeError(f"could not allocate repair output directory for {label}")


def _copy_project_to_generated_layout(source_project_dir: Path, *, protocol: str, method_dir: str, run_root: Path) -> Path:
    if not source_project_dir.is_dir():
        raise FileNotFoundError(f"project_dir does not exist or is not a directory: {source_project_dir}")
    repair_project_dir = run_root / protocol / method_dir / "coder_out" / protocol
    if repair_project_dir.exists():
        raise FileExistsError(f"repair project copy already exists: {repair_project_dir}")
    repair_project_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_project_dir,
        repair_project_dir,
        ignore=shutil.ignore_patterns(".repair", "_agent_logs"),
    )
    return repair_project_dir


def _run_one(row: dict[str, str], *, api_key_env: str, max_repair_calls: int, dry_run: bool) -> dict[str, Any]:
    source_project_dir = Path(row["project_dir"]).expanduser()
    method = row["method"]
    method_dir = row.get("repair_method_dir") or method
    protocol = row["protocol"]
    binary_name = row["binary_name"]
    argv_contract = row["argv_contract"]
    run_root = Path(row["repair_run_root"]).expanduser()
    project_dir = run_root / protocol / method_dir / "coder_out" / protocol
    output_summary_path = project_dir.parent / "_agent_logs" / "repair_summary.json"
    transport = PROTOCOLS.get(protocol).transport if protocol in PROTOCOLS else ""
    try:
        project_dir = _copy_project_to_generated_layout(source_project_dir, protocol=protocol, method_dir=method_dir, run_root=run_root)
        summary = BoundedCRepairRunner(
            RepairConfig(
                project_dir=project_dir,
                method=method,
                protocol=protocol,
                binary_name=binary_name,
                argv_contract=argv_contract,
                output_summary_path=output_summary_path,
                max_repair_calls=max_repair_calls,
                dry_run=dry_run,
                transport=transport,
            ),
            llm_client=FixedQwenClient(api_key_env),
        ).run()
        summary["source_project_dir"] = str(source_project_dir)
        summary["repair_project_dir"] = str(project_dir)
        summary["repair_run_root"] = str(run_root)
        summary["repair_method_dir"] = method_dir
        write_json(output_summary_path, summary)
        return {
            "status": "ok",
            "summary_path": str(output_summary_path),
            "canonical_summary_path": str(output_summary_path),
            "repair_project_dir": str(project_dir),
            "summary": summary,
        }
    except Exception as exc:  # noqa: BLE001
        summary = {
            "method": method,
            "protocol": protocol,
            "project_dir": str(project_dir),
            "source_project_dir": str(source_project_dir),
            "repair_project_dir": str(project_dir),
            "repair_run_root": str(run_root),
            "repair_method_dir": method_dir,
            "binary_name": binary_name,
            "argv_contract": argv_contract,
            "repair_stop_reason": "runner_error",
            "compile_before_repair": "not_run",
            "link_after_repair": "failed",
            "runtime_start_status": "not_run",
            "planning_dependent_remaining_count": 0,
        }
        write_json(output_summary_path, summary)
        return {
            "status": "error",
            "summary_path": str(output_summary_path),
            "canonical_summary_path": str(output_summary_path),
            "repair_project_dir": str(project_dir),
            "error": f"{type(exc).__name__}: {exc}",
            "summary": summary,
        }


def _load_batch(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    required = {"project_dir", "method", "protocol", "binary_name", "argv_contract"}
    for index, row in enumerate(rows, start=2):
        missing = sorted(key for key in required if not row.get(key))
        if missing:
            raise SystemExit(f"{path}:{index}: missing required columns: {', '.join(missing)}")
        if row["method"] not in BASELINE_REPAIR_METHODS:
            raise SystemExit(f"{path}:{index}: unsupported method: {row['method']}")
    return rows


def _prepare_repair_rows(rows: list[dict[str, str]], run_root: Path) -> list[dict[str, str]]:
    used_dirs: set[tuple[str, str]] = set()
    prepared_rows: list[dict[str, str]] = []
    for row in rows:
        prepared = dict(row)
        protocol = row["protocol"]
        base_method_dir = _safe_segment(row["method"])
        method_dir = base_method_dir
        index = 2
        while (protocol, method_dir) in used_dirs:
            method_dir = f"{base_method_dir}_{index:02d}"
            index += 1
        used_dirs.add((protocol, method_dir))
        prepared["repair_run_root"] = str(run_root)
        prepared["repair_method_dir"] = method_dir
        prepared.pop("output_summary_path", None)
        prepared_rows.append(prepared)
    return prepared_rows


def _write_batch_outputs(batch_summary_path: Path, results: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": "planning_utility_batch_bounded_repair/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runs": results,
    }
    write_json(batch_summary_path, payload)
    md_path = batch_summary_path.with_suffix(".md")
    lines = [
        "# Batch Bounded Generic C Repair Summary",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- project_count: `{len(results)}`",
        "",
        "| status | method | protocol | compile_before | link_after | runtime_start | C1/C2 fixed | C3/C4/C5 remaining | planning-dependent | stop_reason | repair_project | summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        summary = item.get("summary", {})
        fixed_c12 = int(summary.get("C1_fixed_count", 0) or 0) + int(summary.get("C2_fixed_count", 0) or 0)
        remaining_c345 = (
            int(summary.get("C3_remaining_count", 0) or 0)
            + int(summary.get("C4_remaining_count", 0) or 0)
            + int(summary.get("C5_remaining_count", 0) or 0)
        )
        lines.append(
            "| {status} | {method} | {protocol} | {before} | {link} | {runtime} | {fixed} | {remaining} | {planning} | {reason} | {repair_project} | {path} |".format(
                status=item.get("status", ""),
                method=summary.get("method", ""),
                protocol=summary.get("protocol", ""),
                before=summary.get("compile_before_repair", ""),
                link=summary.get("link_after_repair", ""),
                runtime=summary.get("runtime_start_status", ""),
                fixed=fixed_c12,
                remaining=remaining_c345,
                planning=summary.get("planning_dependent_remaining_count", 0),
                reason=str(summary.get("repair_stop_reason", "")).replace("|", "\\|"),
                repair_project=str(item.get("repair_project_dir", "")).replace("|", "\\|"),
                path=item.get("summary_path", ""),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded generic C repair for planning-utility baselines")
    parser.add_argument("--project-dir", help="Existing coder_out/<protocol> project directory; copied to planning_utility/out before repair")
    parser.add_argument("--batch-input", help="CSV with project_dir,method,protocol,binary_name,argv_contract")
    parser.add_argument("--method", choices=sorted(BASELINE_REPAIR_METHODS))
    parser.add_argument("--protocol", choices=sorted(PROTOCOLS))
    parser.add_argument("--binary-name")
    parser.add_argument("--argv-contract")
    parser.add_argument("--output-summary-path", help="Deprecated; repair summaries are written inside the copied repair run folder")
    parser.add_argument("--max-repair-calls", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jobs", type=int, default=1, help="Batch parallelism, capped at 4")
    parser.add_argument("--api-key-env", default="ALI_API")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.project_dir) == bool(args.batch_input):
        raise SystemExit("Exactly one of --project-dir or --batch-input is required")
    if args.project_dir:
        missing = [name for name in ("method", "protocol", "binary_name", "argv_contract") if not getattr(args, name.replace("-", "_"), None)]
        if missing:
            raise SystemExit(f"Single-project repair requires: {', '.join('--' + name for name in missing)}")
        row = {
            "project_dir": args.project_dir,
            "method": args.method,
            "protocol": args.protocol,
            "binary_name": args.binary_name,
            "argv_contract": args.argv_contract,
        }
        run_root = _fresh_run_root([row])
        result = _run_one(
            {
                **row,
                "repair_run_root": str(run_root),
                "repair_method_dir": args.method,
            },
            api_key_env=args.api_key_env,
            max_repair_calls=args.max_repair_calls,
            dry_run=args.dry_run,
        )
        print(
            json.dumps(
                {
                    "summary_path": result["summary_path"],
                    "canonical_summary_path": result.get("canonical_summary_path"),
                    "repair_project_dir": result.get("repair_project_dir"),
                    "status": result["status"],
                },
                ensure_ascii=False,
            )
        )
        summary = result["summary"]
        if result["status"] != "ok":
            return 2
        return 0 if summary.get("link_after_repair") == "passed" and summary.get("runtime_start_status") in {"passed", "skipped", "not_run"} else 1

    rows = _load_batch(Path(args.batch_input).expanduser())
    jobs = max(1, min(int(args.jobs), 4))
    run_root = _fresh_run_root(rows)
    prepared_rows = _prepare_repair_rows(rows, run_root)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [
            executor.submit(
                _run_one,
                row,
                api_key_env=args.api_key_env,
                max_repair_calls=args.max_repair_calls,
                dry_run=args.dry_run,
            )
            for row in prepared_rows
        ]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: (item.get("summary", {}).get("protocol", ""), item.get("summary", {}).get("method", ""), item.get("summary", {}).get("project_dir", "")))
    batch_summary_path = run_root / "batch_repair_summary.json"
    _write_batch_outputs(batch_summary_path, results)
    print(f"Batch repair summary: {batch_summary_path}")
    if any(item.get("status") != "ok" for item in results):
        return 2
    return 0 if all(item.get("summary", {}).get("link_after_repair") == "passed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
