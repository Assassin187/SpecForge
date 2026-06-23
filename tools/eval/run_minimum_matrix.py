#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "agent" / "eval_out" / "minimum_matrix"


@dataclass(frozen=True)
class ProtocolConfig:
    protocol: str
    facts_path: Path
    target_profile_path: Path
    binary_name: str


PROTOCOLS: dict[str, ProtocolConfig] = {
    "mqtt": ProtocolConfig(
        protocol="mqtt",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
        binary_name="mqtt_broker",
    ),
    "coap": ProtocolConfig(
        protocol="coap",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "coap_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_coap.json",
        binary_name="coap_server",
    ),
    "smtp": ProtocolConfig(
        protocol="smtp",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "smtp_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_smtp_min.json",
        binary_name="smtp_server",
    ),
}


def _rel(path: Path | str | None) -> str:
    if path is None:
        return ""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(REPO_ROOT))
    except Exception:  # noqa: BLE001
        return str(p)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_command(cmd: list[str], *, log_dir: Path, name: str) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")
    result = subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        check=False,
    )
    ended_at = datetime.now().isoformat(timespec="seconds")
    (log_dir / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    record = {
        "name": name,
        "command": cmd,
        "returncode": result.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "stdout_path": _rel(log_dir / f"{name}.stdout.txt"),
        "stderr_path": _rel(log_dir / f"{name}.stderr.txt"),
    }
    _write_json(log_dir / f"{name}.command.json", record)
    return {**record, "stdout": result.stdout, "stderr": result.stderr}


def _first_diagnostic(*texts: str) -> str:
    marker_groups = (
        ("error:", " error ", "error ", "fatal", "undefined", "traceback"),
        ("status=failed", "generation failed", "repair stop reason", "failed"),
        ("rejected reason", "warning"),
    )
    for markers in marker_groups:
        for text in texts:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                lower = line.lower().replace("failed=false", "").replace("hit_completion_limit=false", "")
                if any(marker in lower for marker in markers):
                    return line[:500]
    for text in texts:
        stripped = text.strip()
        if stripped:
            return stripped.splitlines()[-1][:500]
    return ""


def _classify_failure(stage: str, diagnostic: str) -> list[str]:
    text = diagnostic.lower()
    categories: list[str] = []
    if "json" in text and ("decode" in text or "parse" in text or "invalid" in text):
        categories.append("llm_json_error")
    if any(token in text for token in ("candidate", "unknown_value_ref", "unbound", "semantic")):
        categories.append("llm_candidate_semantic_error")
    if stage in {"planning_validate", "planning_plan", "planning_verify"}:
        categories.append("planning_stage_failure")
    elif stage == "spec_bundle":
        categories.append("specs_compiler_lowering_failure")
    elif stage == "coder_validate":
        if any(token in text for token in ("header", "rendered", "dummy", "translation unit", "include")):
            categories.append("coder_loader_header_failure")
        else:
            categories.append("coder_loader_failure")
    elif stage == "coder_generate":
        if any(token in text for token in ("header", "rendered", "dummy", "translation unit", "include")):
            categories.append("coder_loader_header_failure")
        categories.append("source_compile_failure")
    elif stage == "smoke":
        categories.append("smoke_behavior_failure")
    elif stage == "input":
        categories.append("input_blocker")
    return sorted(set(categories))


def _facts_summary(path: Path) -> tuple[dict[str, Any], list[str]]:
    facts = _read_json(path)
    meta = facts.get("protocol_meta", {}) if isinstance(facts.get("protocol_meta"), dict) else {}
    minimum = facts.get("minimum_v1", {}) if isinstance(facts.get("minimum_v1"), dict) else {}
    surfaces = []
    raw_surfaces = minimum.get("must_support_surface", [])
    if isinstance(raw_surfaces, list):
        for item in raw_surfaces:
            if isinstance(item, dict):
                surfaces.append(str(item.get("name") or item.get("capability") or item.get("surface_unit") or item.get("summary") or "")[:120])
            else:
                surfaces.append(str(item)[:120])
    open_assumptions = []
    raw_questions = facts.get("open_questions", [])
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if isinstance(item, dict):
                question = str(item.get("question") or item.get("summary") or "").strip()
                if question:
                    open_assumptions.append(question)
    return (
        {
            "source": _rel(path),
            "protocol_name": meta.get("protocol_name"),
            "target_scope": meta.get("target_scope"),
            "minimum_surface": [surface for surface in surfaces if surface],
        },
        open_assumptions,
    )


def _target_decisions(path: Path, config: ProtocolConfig, run_dir: Path) -> list[str]:
    profile = _read_json(path)
    decisions = [
        f"target_role={profile.get('target_role')}",
        f"language={profile.get('language')}",
        f"runtime={profile.get('runtime')}",
        f"scope={profile.get('scope')}",
        f"binary_name={config.binary_name}",
        f"artifact_root={_rel(run_dir)}",
    ]
    constraints = profile.get("deployment_constraints")
    if isinstance(constraints, dict):
        for key, value in sorted(constraints.items()):
            decisions.append(f"deployment_constraints.{key}={value}")
    return decisions


def _copy_existing_planning(source: Path, dest: Path) -> tuple[Path, str]:
    source = source.resolve()
    dest = dest.resolve()
    if source == dest:
        return dest, "already_in_matrix"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return dest, "copied"


def _manifest_compile_status(manifest_path: Path) -> tuple[str, int | None, str]:
    manifest = _read_json(manifest_path)
    if not manifest:
        return "not_run", None, ""
    repair = manifest.get("repair", {}) if isinstance(manifest.get("repair"), dict) else {}
    rounds = repair.get("rounds_attempted")
    compile_success = manifest.get("compile_success")
    if compile_success is True:
        return "passed", int(rounds or 0), ""
    if compile_success is False:
        reason = str(manifest.get("repair_stop_reason") or repair.get("stop_reason") or "")
        return "failed", int(rounds or 0), reason
    return "unknown", int(rounds or 0) if rounds is not None else None, ""


def _planning_report_diagnostic(planning_run: Path) -> str:
    report = _read_json(planning_run / "_validation_reports" / "014_planning_validation_report.json")
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, list):
        return ""
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        if str(item.get("level", "")).lower() != "error":
            continue
        code = str(item.get("code", "")).strip()
        message = str(item.get("message", "")).strip()
        path = str(item.get("path", "")).strip()
        prefix = f"ERROR {code}" if code else "ERROR"
        suffix = f" [{path}]" if path else ""
        return f"{prefix}{suffix}: {message}".strip()
    return ""


def _project_and_binary(coder_out: Path, config: ProtocolConfig) -> tuple[Path, str]:
    manifest = _read_json(coder_out / "_agent_logs" / "run_manifest.json")
    project_dir = manifest.get("project_dir")
    binary = manifest.get("binary_name") or config.binary_name
    if project_dir:
        return Path(project_dir), str(binary)
    return coder_out / config.protocol, str(binary)


def _base_summary(config: ProtocolConfig, protocol_dir: Path) -> dict[str, Any]:
    facts, open_assumptions = _facts_summary(config.facts_path)
    return {
        "protocol": config.protocol,
        "facts_path": _rel(config.facts_path),
        "target_profile_path": _rel(config.target_profile_path),
        "planning_run_dir": "",
        "spec_bundle_path": "",
        "planning_status": "not_run",
        "readiness_status": "not_run",
        "schema_loader_rendered_header_status": "not_run",
        "coder_output_dir": _rel(protocol_dir / "coder_out"),
        "compile_status": "not_run",
        "repair_iterations": None,
        "smoke_status": "not_run",
        "failure_stage": "",
        "failure_categories": [],
        "main_diagnostic": "",
        "artifact_archived": "not_started",
        "protocol_facts": facts,
        "inferred_engineering_decisions": _target_decisions(config.target_profile_path, config, protocol_dir),
        "open_assumptions": open_assumptions,
    }


def _mark_failure(summary: dict[str, Any], stage: str, diagnostic: str) -> None:
    summary["failure_stage"] = stage
    summary["main_diagnostic"] = diagnostic
    summary["failure_categories"] = _classify_failure(stage, diagnostic)


def run_protocol(
    config: ProtocolConfig,
    *,
    matrix_dir: Path,
    api_key_env: str,
    max_repair_rounds: int,
    skip_planning: bool,
    planning_dirs: dict[str, Path],
) -> dict[str, Any]:
    protocol_dir = matrix_dir / config.protocol
    log_dir = protocol_dir / "logs"
    protocol_dir.mkdir(parents=True, exist_ok=True)
    summary = _base_summary(config, protocol_dir)

    missing = [str(path) for path in (config.facts_path, config.target_profile_path) if not path.exists()]
    if missing:
        summary["planning_status"] = "blocked"
        summary["artifact_archived"] = "partial"
        _mark_failure(summary, "input", f"missing required input(s): {', '.join(missing)}")
        _write_json(protocol_dir / "summary.json", summary)
        return summary

    validate_cmd = [
        sys.executable,
        "-m",
        "agent.planning",
        "validate",
        "--facts",
        str(config.facts_path),
        "--target-profile",
        str(config.target_profile_path),
        "--output-dir",
        str(protocol_dir / "planning_validate"),
    ]
    validate = _run_command(validate_cmd, log_dir=log_dir, name="01_planning_validate")
    if validate["returncode"] != 0:
        summary["planning_status"] = "failed"
        summary["artifact_archived"] = "partial"
        _mark_failure(summary, "planning_validate", _first_diagnostic(validate["stdout"], validate["stderr"]))
        _write_json(protocol_dir / "summary.json", summary)
        return summary

    planning_run = protocol_dir / "planning_run"
    archive_note = "created_in_matrix"
    if skip_planning:
        existing = planning_dirs.get(config.protocol)
        if existing is None:
            summary["planning_status"] = "blocked"
            summary["artifact_archived"] = "partial"
            _mark_failure(summary, "input", f"--skip-planning requires --planning-dir {config.protocol}=PATH")
            _write_json(protocol_dir / "summary.json", summary)
            return summary
        planning_run, archive_note = _copy_existing_planning(existing, planning_run)
    else:
        plan_cmd = [
            sys.executable,
            "-m",
            "agent.planning",
            "plan",
            "--facts",
            str(config.facts_path),
            "--target-profile",
            str(config.target_profile_path),
            "--output-dir",
            str(planning_run),
            "--api-key-env",
            api_key_env,
        ]
        plan = _run_command(plan_cmd, log_dir=log_dir, name="02_planning_plan")
        if plan["returncode"] != 0:
            summary["planning_status"] = "failed"
            summary["planning_run_dir"] = _rel(planning_run)
            summary["artifact_archived"] = "partial"
            diagnostic = _planning_report_diagnostic(planning_run) or _first_diagnostic(plan["stdout"], plan["stderr"])
            _mark_failure(summary, "planning_plan", diagnostic)
            _write_json(protocol_dir / "summary.json", summary)
            return summary

    summary["planning_run_dir"] = _rel(planning_run)
    spec_bundle = planning_run / "spec_bundle"
    summary["spec_bundle_path"] = _rel(spec_bundle) if spec_bundle.exists() else ""

    verify_cmd = [sys.executable, "-m", "agent.planning", "verify", "--output-dir", str(planning_run)]
    verify = _run_command(verify_cmd, log_dir=log_dir, name="03_planning_verify")
    if verify["returncode"] != 0:
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        summary["artifact_archived"] = f"partial:{archive_note}"
        diagnostic = _planning_report_diagnostic(planning_run) or _first_diagnostic(verify["stdout"], verify["stderr"])
        _mark_failure(summary, "planning_verify", diagnostic)
        _write_json(protocol_dir / "summary.json", summary)
        return summary
    summary["planning_status"] = "passed_existing" if skip_planning else "passed"
    summary["readiness_status"] = "passed"

    if not spec_bundle.is_dir():
        summary["artifact_archived"] = f"partial:{archive_note}"
        _mark_failure(summary, "spec_bundle", "planning verify passed but spec_bundle directory is missing")
        _write_json(protocol_dir / "summary.json", summary)
        return summary

    coder_validate_cmd = [
        sys.executable,
        "-m",
        "agent",
        "coder",
        "--spec-root",
        str(spec_bundle),
        "--api-key-env",
        api_key_env,
        "validate",
    ]
    coder_validate = _run_command(coder_validate_cmd, log_dir=log_dir, name="04_coder_validate")
    if coder_validate["returncode"] != 0:
        summary["schema_loader_rendered_header_status"] = "failed"
        summary["artifact_archived"] = f"partial:{archive_note}"
        _mark_failure(summary, "coder_validate", _first_diagnostic(coder_validate["stdout"], coder_validate["stderr"]))
        _write_json(protocol_dir / "summary.json", summary)
        return summary
    summary["schema_loader_rendered_header_status"] = "passed"

    coder_out = protocol_dir / "coder_out"
    generate_cmd = [
        sys.executable,
        "-m",
        "agent",
        "coder",
        "--spec-root",
        str(spec_bundle),
        "--output-dir",
        str(coder_out),
        "--max-repair-rounds",
        str(max_repair_rounds),
        "--api-key-env",
        api_key_env,
        "generate",
    ]
    generate = _run_command(generate_cmd, log_dir=log_dir, name="05_coder_generate")
    compile_status, rounds, manifest_reason = _manifest_compile_status(coder_out / "_agent_logs" / "run_manifest.json")
    summary["compile_status"] = "passed" if generate["returncode"] == 0 and compile_status == "passed" else compile_status
    summary["repair_iterations"] = rounds
    if generate["returncode"] != 0 or summary["compile_status"] != "passed":
        diagnostic = _first_diagnostic(generate["stdout"], generate["stderr"]) or manifest_reason
        summary["artifact_archived"] = f"partial:{archive_note}"
        _mark_failure(summary, "coder_generate", diagnostic)
        _write_json(protocol_dir / "summary.json", summary)
        return summary

    project_dir, binary_name = _project_and_binary(coder_out, config)
    binary_path = project_dir / binary_name
    if not binary_path.is_file():
        summary["smoke_status"] = "failed"
        summary["artifact_archived"] = f"partial:{archive_note}"
        _mark_failure(summary, "smoke", f"binary not found: {binary_path}")
        _write_json(protocol_dir / "summary.json", summary)
        return summary

    smoke_cmd = [
        sys.executable,
        "-m",
        "agent",
        "coder",
        "test",
        "--protocol",
        config.protocol,
        "--project-dir",
        str(project_dir),
        "--binary",
        binary_name,
    ]
    smoke = _run_command(smoke_cmd, log_dir=log_dir, name="06_smoke")
    summary["smoke_status"] = "passed" if smoke["returncode"] == 0 else "failed"
    summary["artifact_archived"] = f"complete:{archive_note}" if smoke["returncode"] == 0 else f"partial:{archive_note}"
    if smoke["returncode"] != 0:
        _mark_failure(summary, "smoke", _first_diagnostic(smoke["stdout"], smoke["stderr"]))

    _write_json(protocol_dir / "summary.json", summary)
    return summary


def _parse_planning_dirs(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--planning-dir must be protocol=PATH, got: {value}")
        protocol, raw_path = value.split("=", 1)
        protocol = protocol.strip()
        if protocol not in PROTOCOLS:
            raise SystemExit(f"Unknown protocol in --planning-dir: {protocol}")
        result[protocol] = Path(raw_path).expanduser()
    return result


def _write_matrix_outputs(matrix_dir: Path, summaries: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": "minimum_protocol_matrix_summary/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "matrix_dir": _rel(matrix_dir),
        "protocols": summaries,
    }
    _write_json(matrix_dir / "matrix_summary.json", payload)

    lines = [
        "# Minimum Protocol Matrix Summary",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- matrix_dir: `{payload['matrix_dir']}`",
        "",
        "| protocol | planning | readiness | loader/header | compile | smoke | failure_stage | diagnostic |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        diagnostic = str(item.get("main_diagnostic") or "").replace("|", "\\|")
        if len(diagnostic) > 180:
            diagnostic = diagnostic[:177] + "..."
        lines.append(
            "| {protocol} | {planning_status} | {readiness_status} | "
            "{schema_loader_rendered_header_status} | {compile_status} | {smoke_status} | "
            "{failure_stage} | {diagnostic} |".format(diagnostic=diagnostic, **item)
        )
    (matrix_dir / "matrix_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MQTT/CoAP/SMTP minimum planning-to-smoke matrix")
    parser.add_argument("--protocol", action="append", choices=sorted(PROTOCOLS), help="Protocol(s) to run; default: all")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--api-key-env", default="ALI_API")
    parser.add_argument("--max-repair-rounds", type=int, default=3)
    parser.add_argument("--skip-planning", action="store_true", help="Reuse --planning-dir outputs instead of invoking planning plan")
    parser.add_argument("--planning-dir", action="append", default=[], help="Existing planning run as protocol=PATH")
    parser.add_argument("--fail-on-protocol-failure", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    protocols = args.protocol or sorted(PROTOCOLS)
    planning_dirs = _parse_planning_dirs(args.planning_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    matrix_dir = Path(args.output_root).expanduser() / timestamp
    matrix_dir.mkdir(parents=True, exist_ok=True)

    summaries = [
        run_protocol(
            PROTOCOLS[protocol],
            matrix_dir=matrix_dir,
            api_key_env=args.api_key_env,
            max_repair_rounds=args.max_repair_rounds,
            skip_planning=args.skip_planning,
            planning_dirs=planning_dirs,
        )
        for protocol in protocols
    ]
    _write_matrix_outputs(matrix_dir, summaries)
    print(f"Matrix summary: {matrix_dir / 'matrix_summary.json'}")
    if args.fail_on_protocol_failure and any(item.get("failure_stage") for item in summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
