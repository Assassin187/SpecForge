from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .configs import ProtocolConfig, REPO_ROOT, rel_to_repo
from .requirements import build_allowed_inputs, read_json, write_json


def _run_command(cmd: list[str], *, log_dir: Path, name: str) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        check=False,
    )
    ended_at = datetime.now().isoformat(timespec="seconds")
    stdout_path = log_dir / f"{name}.stdout.txt"
    stderr_path = log_dir / f"{name}.stderr.txt"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    record = {
        "name": name,
        "command": cmd,
        "returncode": result.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "stdout_path": rel_to_repo(stdout_path),
        "stderr_path": rel_to_repo(stderr_path),
    }
    write_json(log_dir / f"{name}.command.json", record)
    return {**record, "stdout": result.stdout, "stderr": result.stderr}


def _first_diagnostic(*texts: str) -> str:
    markers = ("error", "failed", "traceback", "undefined", "fatal")
    for text in texts:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and any(marker in stripped.lower() for marker in markers):
                return stripped[:500]
    for text in texts:
        stripped = text.strip()
        if stripped:
            return stripped.splitlines()[-1][:500]
    return ""


def _manifest_compile_status(path: Path) -> tuple[str, int | None, str]:
    if not path.is_file():
        return "not_run", None, ""
    manifest = read_json(path)
    repair = manifest.get("repair", {}) if isinstance(manifest.get("repair"), dict) else {}
    rounds = repair.get("rounds_attempted")
    if manifest.get("compile_success") is True:
        return "passed", int(rounds or 0), ""
    if manifest.get("compile_success") is False:
        return "failed", int(rounds or 0), str(repair.get("stop_reason") or "")
    return "unknown", int(rounds or 0) if rounds is not None else None, ""


def _scenario_counts_from_stdout(stdout: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for line in stdout.splitlines():
        status = line.split(":", 1)[0].strip().lower()
        if status.startswith("passed"):
            counts["passed"] += 1
        elif status.startswith("failed"):
            counts["failed"] += 1
        elif status.startswith("skipped"):
            counts["skipped"] += 1
    return counts


def _project_and_binary(coder_out: Path, config: ProtocolConfig) -> tuple[Path, str]:
    manifest_path = coder_out / "_agent_logs" / "run_manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        project_dir = manifest.get("project_dir")
        binary = manifest.get("binary_name") or config.binary_name
        if project_dir:
            return Path(project_dir), str(binary)
    return coder_out / config.protocol, config.binary_name


def _copy_existing_planning(source: Path, dest: Path) -> tuple[Path, str]:
    source = source.resolve()
    dest = dest.resolve()
    if source == dest:
        return dest, "already_in_matrix"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    return dest, "copied"


def _path_in_copied_run(value: Any, run_dir: Path) -> Path | None:
    if not value:
        return None
    raw = Path(str(value))
    try:
        raw.resolve().relative_to(run_dir.resolve())
        return raw
    except ValueError:
        pass
    if "_planning" in raw.parts:
        index = raw.parts.index("_planning")
        return run_dir.joinpath(*raw.parts[index:])
    return run_dir / raw.name


def _rewrite_copied_manifest_paths(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "_planning" / "run_manifest.json"
    manifest = read_json(manifest_path)
    for field in (
        "candidate_root",
        "candidate_specs_root",
        "specs_root",
        "module_spec",
        "summary",
        "semantic_mapping",
        "implementability_report",
    ):
        mapped = _path_in_copied_run(manifest.get(field), run_dir)
        if mapped is not None:
            manifest[field] = str(mapped)
    manifest["written_files"] = [
        str(mapped)
        for value in manifest.get("written_files", [])
        if (mapped := _path_in_copied_run(value, run_dir)) is not None
    ]
    write_json(manifest_path, manifest)
    return manifest


def _stored_planning_diagnostic(run_dir: Path) -> str:
    path = run_dir / "_planning" / "diagnostics.json"
    if not path.is_file():
        return ""
    diagnostics = json.loads(path.read_text(encoding="utf-8"))
    for item in diagnostics if isinstance(diagnostics, list) else []:
        if isinstance(item, dict) and item.get("level") == "error":
            return f"{item.get('code', 'planning_error')}: {item.get('message', '')}"[:500]
    return ""


def run_full_specforge(
    config: ProtocolConfig,
    output_dir: Path,
    *,
    api_key_env: str,
    max_repair_rounds: int,
    existing_planning_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    runtime_contract = {
        "binary_name": config.binary_name,
        "argv_contract": config.argv_contract,
        "transport": config.transport,
    }
    allowed_inputs = build_allowed_inputs(config.facts_path, config.target_profile_path, runtime_contract)
    allowed_root = output_dir / "allowed_inputs"
    write_json(allowed_root / "input_hashes.json", allowed_inputs["input_hashes"])
    write_json(allowed_root / "runtime_contract.json", runtime_contract)
    summary = {
        "schema_version": "planning_utility_full_specforge_summary/v1",
        "method": "full-specforge",
        "protocol": config.protocol,
        "facts_path": rel_to_repo(config.facts_path),
        "target_profile_path": rel_to_repo(config.target_profile_path),
        "target_profile_sha256": allowed_inputs["input_hashes"]["target_profile_sha256"],
        "target_profile_visible_to_planner": False,
        "binary_name": config.binary_name,
        "argv_contract": config.argv_contract,
        "planning_status": "not_run",
        "planning_run_status": "not_run",
        "planning_validation_passed": None,
        "qualification_passed": None,
        "specs_generated": False,
        "specs_root": "",
        "coder_loader_passed": None,
        "fatal": False,
        "fatal_reason_code": None,
        "nonfatal_no_specs_count": 0,
        "diagnostic_counts": {"error": 0, "warning": 0},
        "planning_token_usage": {},
        "readiness_status": "not_run",
        "schema_loader_rendered_header_status": "not_run",
        "compile_status": "not_run",
        "repair_iterations": None,
        "smoke_status": "not_run",
        "verification_success": None,
        "scenario_counts": {"passed": 0, "failed": 0, "skipped": 0},
        "llm_call_usage": [],
        "stage_token_usage": {},
        "workflow_token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "failure_stage": "",
        "failure_categories": [],
        "main_diagnostic": "",
        "command_log": [],
        "output_dir": str(output_dir),
    }

    def fail(stage: str, diagnostic: str) -> dict[str, Any]:
        summary["failure_stage"] = stage
        summary["main_diagnostic"] = diagnostic
        summary["failure_categories"] = [f"{stage}_failure"]
        write_json(output_dir / "summary.json", summary)
        return summary

    planning_run = output_dir / "planning_run"
    archive_note = "created"
    if existing_planning_dir is not None:
        planning_run, archive_note = _copy_existing_planning(existing_planning_dir, planning_run)
        _rewrite_copied_manifest_paths(planning_run)
    else:
        plan_cmd = [
            sys.executable,
            "-m",
            "agent.planning",
            "plan",
            "--facts",
            str(config.facts_path),
            "--out",
            str(planning_run),
            "--api-key-env",
            api_key_env,
        ]
        plan = _run_command(plan_cmd, log_dir=log_dir, name="01_planning_plan")
        summary["command_log"].append({key: plan[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})

    summary["planning_run_dir"] = rel_to_repo(planning_run)
    summary["planning_archive_note"] = archive_note
    manifest_path = planning_run / "_planning" / "run_manifest.json"
    if not manifest_path.is_file():
        summary["planning_status"] = "failed"
        diagnostic = "planning manifest is missing"
        if existing_planning_dir is None:
            diagnostic = _first_diagnostic(plan["stdout"], plan["stderr"]) or diagnostic
        return fail("planning_plan", diagnostic)
    plan_manifest = read_json(manifest_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, log_dir / "plan_time_run_manifest.json")
    diagnostics_path = planning_run / "_planning" / "diagnostics.json"
    if diagnostics_path.is_file():
        shutil.copy2(diagnostics_path, log_dir / "plan_time_diagnostics.json")

    summary["planning_manifest_path"] = rel_to_repo(manifest_path)
    summary["planning_run_status"] = str(plan_manifest.get("run_status", "unknown"))
    summary["planning_validation_passed"] = plan_manifest.get("planning_validation_passed")
    summary["qualification_passed"] = bool(plan_manifest.get("qualification_passed"))
    summary["specs_generated"] = bool(plan_manifest.get("specs_generated"))
    summary["coder_loader_passed"] = plan_manifest.get("coder_loader_passed")
    summary["diagnostic_counts"] = plan_manifest.get("diagnostic_counts", summary["diagnostic_counts"])
    summary["planning_token_usage"] = plan_manifest.get("token_accounting", {})
    summary["fatal"] = bool(plan_manifest.get("fatal")) or summary["planning_run_status"] == "failed_internal"
    summary["fatal_reason_code"] = plan_manifest.get("fatal_reason_code") or plan_manifest.get("hard_failure_code")
    summary["nonfatal_no_specs_count"] = int(not summary["fatal"] and not summary["specs_generated"])

    specs_root = _path_in_copied_run(plan_manifest.get("specs_root"), planning_run)
    if specs_root is not None:
        summary["specs_root"] = rel_to_repo(specs_root)

    with tempfile.TemporaryDirectory(prefix="specforge_planning_validate_") as validate_tmp:
        validation_run = Path(validate_tmp) / "planning_run"
        shutil.copytree(planning_run, validation_run)
        _rewrite_copied_manifest_paths(validation_run)
        validate_cmd = [
            sys.executable,
            "-m",
            "agent.planning",
            "validate",
            "--run-dir",
            str(validation_run),
        ]
        validate = _run_command(validate_cmd, log_dir=log_dir, name="02_planning_validate")
        validated_manifest_path = validation_run / "_planning" / "run_manifest.json"
        validated_manifest = read_json(validated_manifest_path) if validated_manifest_path.is_file() else plan_manifest
        if validated_manifest_path.is_file():
            shutil.copy2(validated_manifest_path, log_dir / "validated_run_manifest.json")
        validated_diagnostics = validation_run / "_planning" / "diagnostics.json"
        if validated_diagnostics.is_file():
            shutil.copy2(validated_diagnostics, log_dir / "validated_diagnostics.json")
    summary["command_log"].append({key: validate[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    summary["planning_validation_passed"] = validated_manifest.get("planning_validation_passed")
    summary["qualification_passed"] = bool(validated_manifest.get("qualification_passed"))
    summary["coder_loader_passed"] = validated_manifest.get("coder_loader_passed")
    summary["diagnostic_counts"] = validated_manifest.get("diagnostic_counts", summary["diagnostic_counts"])

    stored_diagnostic = _stored_planning_diagnostic(planning_run)
    if summary["fatal"]:
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        return fail("planning_fatal", stored_diagnostic or str(summary["fatal_reason_code"] or "fatal planning failure"))
    if summary["nonfatal_no_specs_count"]:
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        return fail("planning_stability", stored_diagnostic or "non-fatal planning run produced no specs")
    if validate["returncode"] != 0:
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        return fail("planning_validate", _first_diagnostic(validate["stdout"], validate["stderr"]) or stored_diagnostic)
    if not summary["qualification_passed"]:
        summary["planning_status"] = "candidate_only"
        summary["readiness_status"] = "failed"
        return fail("planning_qualification", stored_diagnostic or "qualification_passed=false")
    if specs_root is None or not specs_root.is_dir():
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        return fail("specs_root", "qualified planning manifest references a missing specs_root")
    summary["planning_status"] = "passed_existing" if existing_planning_dir is not None else "passed"
    summary["readiness_status"] = "passed"

    coder_validate_cmd = [
        sys.executable,
        "-m",
        "agent.coder",
        "--spec-root",
        str(specs_root),
        "--output-dir",
        str(output_dir / "coder_validate"),
        "--api-key-env",
        api_key_env,
        "validate",
    ]
    coder_validate = _run_command(coder_validate_cmd, log_dir=log_dir, name="03_coder_validate")
    summary["command_log"].append({key: coder_validate[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    if coder_validate["returncode"] != 0:
        summary["schema_loader_rendered_header_status"] = "failed"
        return fail("coder_validate", _first_diagnostic(coder_validate["stdout"], coder_validate["stderr"]))
    summary["schema_loader_rendered_header_status"] = "passed"

    coder_out = output_dir / "coder_out"
    generate_cmd = [
        sys.executable,
        "-m",
        "agent.coder",
        "--spec-root",
        str(specs_root),
        "--output-dir",
        str(coder_out),
        "--max-repair-rounds",
        str(max_repair_rounds),
        "--api-key-env",
        api_key_env,
        "generate",
    ]
    generate = _run_command(generate_cmd, log_dir=log_dir, name="04_coder_generate")
    summary["command_log"].append({key: generate[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    coder_manifest_path = coder_out / "_agent_logs" / "run_manifest.json"
    compile_status, rounds, manifest_reason = _manifest_compile_status(coder_manifest_path)
    if coder_manifest_path.is_file():
        coder_manifest = read_json(coder_manifest_path)
        summary["coder_manifest_path"] = rel_to_repo(coder_manifest_path)
        summary["llm_call_usage"] = coder_manifest.get("llm_call_usage", [])
        summary["stage_token_usage"] = coder_manifest.get("stage_token_usage", {})
        summary["workflow_token_usage"] = coder_manifest.get("workflow_token_usage", summary["workflow_token_usage"])
        summary["repair_stop_reason"] = (coder_manifest.get("repair") or {}).get("stop_reason", "")
        summary["repaired_files"] = coder_manifest.get("repaired_files", [])
        summary["blocking_files"] = (coder_manifest.get("repair") or {}).get("blocking_files", [])
    summary["compile_status"] = "passed" if generate["returncode"] == 0 and compile_status == "passed" else compile_status
    summary["repair_iterations"] = rounds
    if generate["returncode"] != 0 or summary["compile_status"] != "passed":
        return fail("coder_generate", _first_diagnostic(generate["stdout"], generate["stderr"]) or manifest_reason)

    project_dir, binary = _project_and_binary(coder_out, config)
    smoke_cmd = [
        sys.executable,
        "-m",
        "agent.coder",
        "test",
        "--protocol",
        config.protocol,
        "--project-dir",
        str(project_dir),
        "--binary",
        binary,
    ]
    smoke = _run_command(smoke_cmd, log_dir=log_dir, name="05_smoke")
    summary["command_log"].append({key: smoke[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    summary["smoke_status"] = "passed" if smoke["returncode"] == 0 else "failed"
    summary["verification_success"] = smoke["returncode"] == 0
    summary["scenario_counts"] = _scenario_counts_from_stdout(smoke["stdout"])
    if smoke["returncode"] != 0:
        return fail("smoke", _first_diagnostic(smoke["stdout"], smoke["stderr"]))
    write_json(output_dir / "summary.json", summary)
    return summary
