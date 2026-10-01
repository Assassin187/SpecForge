"""Post-generation independent evaluation. Never called by an Agent gate."""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

from .documents import digest, hashes, read_json, save_json
from .tools import ToolRuntime


def evaluate_project(project: Path, bundle: Path, evaluator: Path, destination: Path) -> dict:
    """Evaluate a delivery copy, preserving the original generated artifacts."""
    destination.mkdir(parents=True)
    original_hashes = hashes(project)
    work = destination / "project"
    shutil.copytree(project, work)
    harness = destination / "harness"
    harness.mkdir()
    shutil.copyfile(evaluator, harness / "evaluate.py")
    manifest = read_json(bundle / "bundle.json")
    binary = manifest["runtime_contract"]["binary_name"]
    # The binary path crosses from model metadata into a shell command.
    path = Path(binary)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Runtime binary must be a project-relative path")
    runtime = ToolRuntime(work, {"/harness": harness}, destination / "commands", lambda: {"passed": False})
    report = {"passed": False, "evaluator": str(evaluator.resolve()), "evaluator_sha256": digest(evaluator),
              "delivery_hashes": original_hashes, "builds": {}, "phases": {}, "errors": []}
    for phase, target in (("normal", "all"), ("sanitize", "sanitize")):
        build = runtime.command(f"make clean && make {target}", timeout=120)
        report["builds"][phase] = build
        if build["exit_code"] != 0 or build["timed_out"]:
            report["errors"].append(f"{phase} build failed")
            report["phases"][phase] = {"passed": False, "status": "not_executed", "reason": "build_failed"}
            continue
        if phase == "sanitize":
            probe = runtime.command("nm --undefined-only " + shlex.quote("/work/" + binary), timeout=10)
            if "__asan_init" not in probe["output"] or "__ubsan_handle" not in probe["output"]:
                report["errors"].append("Sanitizer binary lacks ASan/UBSan instrumentation")
        command = ("/usr/bin/python3 /harness/evaluate.py --binary " + shlex.quote("/work/" + binary)
                   + f" --out /work/.evaluation/{phase}")
        result = runtime.command(command, timeout=180)
        output = work / ".evaluation" / phase
        if (output / "report.json").is_file():
            phase_report = read_json(output / "report.json")
            shutil.copytree(output, destination / phase)
        else:
            phase_report = {"passed": False, "status": "not_executed", "reason": "missing_report"}
        scenarios = phase_report.get("scenarios", [])
        complete = bool(scenarios) and len(scenarios) == phase_report.get("required_count")
        if (result["exit_code"] != 0 or result["timed_out"] or not phase_report.get("passed")
                or not complete or any(s["status"] != "passed" for s in scenarios)):
            report["errors"].append(f"{phase} independent evaluation failed")
        report["phases"][phase] = phase_report
        if re.search(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer", result["output"]):
            report["errors"].append(f"{phase} sanitizer diagnostics")
    if hashes(project) != original_hashes:
        raise RuntimeError("Independent evaluation changed the original delivery")
    report["passed"] = not report["errors"] and len(report["phases"]) == 2
    save_json(destination / "report.json", report)
    return report


def evaluate_run(run: Path, evaluator: Path) -> dict:
    state = read_json(run / "run.json")
    if state.get("schema_version") != 2 or not state.get("generation_passed"):
        raise ValueError("Independent evaluation requires a completed version-2 generation")
    from .pipeline import project_hashes
    from .specs import validate
    bundle = run / "specs" / f"r{state['spec_revision']:03d}"
    validation = validate(bundle)
    if not validation["passed"]:
        raise ValueError(f"Published Spec changed or is invalid: {validation['errors']}")
    if project_hashes(run / "project") != state["project_hashes"]:
        raise ValueError("Generated delivery changed before independent evaluation")
    state["evaluation_started"] = True
    save_json(run / "run.json", state)
    root = run / "evaluation"
    root.mkdir(exist_ok=True)
    destination = root / f"evaluation_{len(list(root.glob('evaluation_*'))) + 1:03d}"
    report = evaluate_project(run / "project", bundle, evaluator, destination)
    state["evaluation"] = {"passed": report["passed"], "report": str((destination / "report.json").relative_to(run)),
                           "evaluator_sha256": report["evaluator_sha256"]}
    save_json(run / "run.json", state)
    return report
