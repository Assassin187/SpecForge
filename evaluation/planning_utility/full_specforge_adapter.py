from __future__ import annotations

import hashlib
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
from .no_repair_analysis import analyze_no_repair
from .repair_diagnostics import iter_project_files
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


def _project_and_binary(coder_out: Path, config: ProtocolConfig) -> tuple[Path, str]:
    manifest_path = coder_out / "_agent_logs" / "run_manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        project_dir = manifest.get("project_dir")
        binary = manifest.get("binary_name") or config.binary_name
        if project_dir:
            return Path(project_dir), str(binary)
    return coder_out / config.protocol, config.binary_name


def _spec_inventory(specs_root: Path | None) -> dict[str, int]:
    counts = {"module": 0, "file": 0, "function": 0}
    kinds = {"PROTOCOL_MODULE_SPEC": "module", "FILE_SPEC": "file", "FUNCTION_SPEC": "function"}
    if specs_root is None or not specs_root.is_dir():
        return counts
    for path in specs_root.rglob("*_spec.json"):
        try:
            kind = json.loads(path.read_text(encoding="utf-8")).get("KIND")
        except (OSError, json.JSONDecodeError):
            continue
        if kind in kinds:
            counts[kinds[kind]] += 1
    return counts


def _source_hashes(project_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(project_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in iter_project_files(project_dir)
    }


def _repair_manifest_for_source(coder_original: Path, source_project_dir: Path) -> Path | None:
    matches: list[Path] = []
    for path in coder_original.glob("*_repair_*/_agent_logs/*repair_manifest.json"):
        try:
            manifest = read_json(path)
            if Path(str(manifest.get("source_project_dir", ""))).resolve() == source_project_dir.resolve():
                matches.append(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return max(matches, key=lambda path: path.stat().st_mtime_ns) if matches else None


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
    diagnostic_only: bool = False,
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
        "spec_inventory": {"module": 0, "file": 0, "function": 0},
        "candidate_through_eligible": False,
        "candidate_through_reason": "not_evaluated",
        "diagnostic_only": diagnostic_only,
        "coder_release_eligible": False,
        "coder_release_blockers": ["not_evaluated"],
        "coder_validate_status": "not_run",
        "original_generation_status": "not_run",
        "initial_compile_status": "not_run",
        "repair_status": "not_run",
        "original_source_hashes_preserved": None,
        "coder_loader_passed": None,
        "fatal": False,
        "fatal_reason_code": None,
        "nonfatal_no_specs_count": 0,
        "diagnostic_counts": {"error": 0, "warning": 0},
        "planning_token_usage": {},
        "readiness_status": "not_run",
        "schema_loader_rendered_header_status": "not_run",
        "compile_status": "not_run",
        "sound_build_passed": None,
        "sound_build_diagnostics": [],
        "repair_iterations": None,
        "smoke_status": "not_run_out_of_scope",
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
    summary["manifest_specs_generated"] = bool(plan_manifest.get("specs_generated"))
    summary["coder_loader_passed"] = plan_manifest.get("coder_loader_passed")
    summary["diagnostic_counts"] = plan_manifest.get("diagnostic_counts", summary["diagnostic_counts"])
    summary["planning_token_usage"] = plan_manifest.get("token_accounting", {})
    summary["fatal"] = bool(plan_manifest.get("fatal")) or summary["planning_run_status"] == "failed_internal"
    summary["fatal_reason_code"] = plan_manifest.get("fatal_reason_code") or plan_manifest.get("hard_failure_code")
    specs_root = _path_in_copied_run(
        plan_manifest.get("specs_root") or plan_manifest.get("candidate_specs_root"), planning_run
    )
    if specs_root is not None:
        summary["specs_root"] = rel_to_repo(specs_root)
    inventory = _spec_inventory(specs_root)
    summary["spec_inventory"] = inventory
    summary["specs_generated"] = all(inventory[kind] >= 1 for kind in ("module", "file", "function"))
    summary["candidate_through_eligible"] = summary["specs_generated"]
    summary["candidate_through_reason"] = "physical_nonempty_specs" if summary["specs_generated"] else "missing_or_empty_specs"
    summary["nonfatal_no_specs_count"] = int(not summary["fatal"] and not summary["specs_generated"])

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
    summary["coder_release_eligible"] = bool(validated_manifest.get("coder_release_eligible"))
    summary["coder_release_blockers"] = validated_manifest.get(
        "coder_release_blockers", ["coder_release_manifest_field_missing"]
    )

    stored_diagnostic = _stored_planning_diagnostic(planning_run)
    if not summary["candidate_through_eligible"]:
        summary["planning_status"] = "failed"
        summary["readiness_status"] = "failed"
        stage = "planning_fatal" if summary["fatal"] else "planning_stability"
        reason = summary["fatal_reason_code"] if summary["fatal"] else "planning run produced no physical nonempty specs"
        return fail(stage, stored_diagnostic or str(reason))
    if summary["fatal"]:
        summary["planning_status"] = "fatal_with_candidate"
    elif not summary["qualification_passed"] or validate["returncode"] != 0:
        summary["planning_status"] = "candidate_only"
    else:
        summary["planning_status"] = "passed_existing" if existing_planning_dir is not None else "passed"
    if not summary["coder_release_eligible"] and not diagnostic_only:
        summary["readiness_status"] = "coder_release_blocked"
        blockers = ", ".join(map(str, summary["coder_release_blockers"]))
        return fail("planning_release", stored_diagnostic or blockers)
    summary["readiness_status"] = (
        "coder_release_eligible" if summary["coder_release_eligible"] else "diagnostic_candidate_through"
    )

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
        summary["coder_validate_status"] = "failed"
        summary["schema_loader_rendered_header_status"] = "failed"
        return fail("coder_validate", _first_diagnostic(coder_validate["stdout"], coder_validate["stderr"]))
    summary["coder_validate_status"] = "passed"
    summary["schema_loader_rendered_header_status"] = "passed"

    coder_out = output_dir / "coder_original"
    generate_cmd = [
        sys.executable,
        "-m",
        "agent.coder",
        "--spec-root",
        str(specs_root),
        "--output-dir",
        str(coder_out),
        "--skip-repair",
        "--api-key-env",
        api_key_env,
        "generate",
    ]
    generate = _run_command(generate_cmd, log_dir=log_dir, name="04_coder_generate")
    summary["command_log"].append({key: generate[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    coder_manifest_path = coder_out / "_agent_logs" / "run_manifest.json"
    if generate["returncode"] != 0 or not coder_manifest_path.is_file():
        summary["original_generation_status"] = "failed"
        return fail("coder_generate", _first_diagnostic(generate["stdout"], generate["stderr"]) or "generation manifest is missing")
    coder_manifest = read_json(coder_manifest_path)
    if not coder_manifest.get("generation_success") or not coder_manifest.get("skip_repair"):
        summary["original_generation_status"] = "failed"
        return fail("coder_generate", "coder generation did not preserve the required skip-repair contract")
    summary["original_generation_status"] = "passed"
    summary["coder_manifest_path"] = rel_to_repo(coder_manifest_path)
    summary["llm_call_usage"] = coder_manifest.get("llm_call_usage", [])
    summary["stage_token_usage"] = coder_manifest.get("stage_token_usage", {})
    summary["workflow_token_usage"] = coder_manifest.get("workflow_token_usage", summary["workflow_token_usage"])
    project_dir, binary = _project_and_binary(coder_out, config)
    if not project_dir.is_dir():
        return fail("coder_generate", "generated original project directory is missing")
    before_hashes = _source_hashes(project_dir)
    write_json(coder_out / "_agent_logs" / "pre_repair_source_hashes.json", before_hashes)
    no_repair = analyze_no_repair(project_dir, specs_root, binary)
    write_json(coder_out / "_agent_logs" / "pre_repair_diagnostics.json", no_repair)
    summary["original_project_dir"] = rel_to_repo(project_dir)
    summary["pre_repair_source_hashes_path"] = rel_to_repo(coder_out / "_agent_logs" / "pre_repair_source_hashes.json")
    summary["pre_repair_diagnostics_path"] = rel_to_repo(coder_out / "_agent_logs" / "pre_repair_diagnostics.json")
    summary["initial_compile_status"] = (
        "passed"
        if (no_repair["diagnostic_snapshot"].get("build") or {}).get("returncode") == 0
        else "failed"
    )

    repair_cmd = [
        sys.executable,
        "-m",
        "agent.coder",
        "--spec-root",
        str(specs_root),
        "--max-repair-rounds",
        str(max_repair_rounds),
        "--api-key-env",
        api_key_env,
        "repair",
        "--project-dir",
        str(project_dir),
    ]
    repair = _run_command(repair_cmd, log_dir=log_dir, name="05_coder_repair")
    summary["command_log"].append({key: repair[key] for key in ("name", "command", "returncode", "started_at", "ended_at", "stdout_path", "stderr_path")})
    repair_manifest_path = _repair_manifest_for_source(coder_out, project_dir)
    if repair_manifest_path is None:
        summary["repair_status"] = "failed"
        return fail("coder_repair", _first_diagnostic(repair["stdout"], repair["stderr"]) or "repair manifest is missing")
    repair_manifest = read_json(repair_manifest_path)
    repair_data = repair_manifest.get("repair") or {}
    after_hashes = _source_hashes(project_dir)
    preserved = before_hashes == after_hashes
    write_json(coder_out / "_agent_logs" / "post_repair_original_source_hashes.json", after_hashes)
    summary["repair_manifest_path"] = rel_to_repo(repair_manifest_path)
    summary["repair_project_dir"] = rel_to_repo(Path(str(repair_manifest["project_dir"])))
    summary["source_project_dir"] = rel_to_repo(Path(str(repair_manifest["source_project_dir"])))
    summary["repair_iterations"] = int(repair_data.get("rounds_attempted") or 0)
    summary["repair_stop_reason"] = str(repair_data.get("stop_reason") or "")
    summary["repair_fingerprint_history"] = repair_data.get("fingerprint_history", [])
    summary["repaired_files"] = repair_manifest.get("repaired_files", [])
    summary["blocking_files"] = repair_data.get("blocking_files", [])
    summary["original_source_hashes_preserved"] = preserved
    summary["compile_status"] = "passed" if repair_manifest.get("compile_success") is True else "failed"
    summary["repair_status"] = summary["compile_status"]
    summary["repair_llm_call_usage"] = repair_manifest.get("llm_call_usage", [])
    summary["repair_stage_token_usage"] = repair_manifest.get("stage_token_usage", {})
    summary["repair_workflow_token_usage"] = repair_manifest.get("workflow_token_usage", {})
    if not preserved:
        return fail("original_hash_preservation", "repair changed the original generated source tree")
    repair_project_dir = Path(str(repair_manifest["project_dir"]))
    if not repair_project_dir.is_dir():
        return fail("coder_repair", "repair manifest references a missing project directory")
    post_repair = analyze_no_repair(repair_project_dir, specs_root, binary)
    post_repair_path = coder_out / "_agent_logs" / "post_repair_diagnostics.json"
    write_json(post_repair_path, post_repair)
    sound_build = (post_repair.get("diagnostic_snapshot", {}).get("sound_build") or {})
    summary["post_repair_diagnostics_path"] = rel_to_repo(post_repair_path)
    summary["sound_build_passed"] = sound_build.get("passed") is True
    summary["sound_build_diagnostics"] = sound_build.get("diagnostic_codes", [])
    summary["verification_success"] = summary["sound_build_passed"]
    if repair["returncode"] != 0 or summary["compile_status"] != "passed":
        return fail("coder_repair", _first_diagnostic(repair["stdout"], repair["stderr"]) or summary["repair_stop_reason"])
    write_json(output_dir / "summary.json", summary)
    return summary
