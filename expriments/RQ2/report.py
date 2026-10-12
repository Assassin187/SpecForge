"""Read existing artifacts only; never run a model, gate, build or evaluator."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from specforge.documents import hashes, read_json, save_json

TOKEN_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cache_hit_tokens", "cache_miss_tokens")


def optional(path: Path) -> dict:
    return read_json(path) if path.is_file() else {}


def sum_usage(entries: list[dict]) -> dict:
    result = {k: sum(e[k] for e in entries) if all(e.get(k) is not None for e in entries) else None for k in TOKEN_FIELDS}
    result["requests"] = sum(e.get("requests", 0) for e in entries)
    result["tokens"] = (result["input_tokens"] + result["output_tokens"]
                        if result["input_tokens"] is not None and result["output_tokens"] is not None else None)
    return result


def raw_usage(path: Path) -> dict:
    value = read_json(path).get("response", {}).get("usage") or {}
    details = value.get("completion_tokens_details") or {}
    cache = value.get("prompt_cache_hit_tokens")
    if cache is None:
        cache = (value.get("prompt_tokens_details") or {}).get("cached_tokens")
    miss = value.get("prompt_cache_miss_tokens")
    if miss is None and cache is not None and value.get("prompt_tokens") is not None:
        miss = value["prompt_tokens"] - cache
    return {"input_tokens": value.get("prompt_tokens"), "output_tokens": value.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"), "cache_hit_tokens": cache, "cache_miss_tokens": miss,
            "requests": 1}


def run_usage(run: Path) -> dict:
    state = optional(run / "run.json")
    paths = sorted((run / "logs").glob("*/api/response_*.json"))
    entries = [raw_usage(path) for path in paths]
    total = sum_usage(entries)
    errors = sorted((run / "logs").glob("*/api/api_error_*.json"))
    unreturned = [p for p in (run / "logs").glob("*/api/request_*.json")
                  if not p.with_name(p.name.replace("request_", "response_")).is_file()]
    # A request without a returned usage is unknown, even when its preceding completions are known.
    if errors or unreturned:
        total = {**total, **{k: None for k in TOKEN_FIELDS}, "tokens": None}
    crosschecks = []
    for job in state.get("jobs", []):
        job_paths = [p for p in paths if p.parent.parent.name == job["name"]]
        actual = sum_usage([raw_usage(p) for p in job_paths])
        recorded = job.get("usage", {})
        differences = {k: {"raw": actual[k], "native": recorded.get(k)} for k in (*TOKEN_FIELDS, "requests")
                       if actual[k] != recorded.get(k)}
        crosschecks.append({"job": job["name"], "raw": actual, "native": recorded,
                            "result": {k: job[k] for k in ("passed", "reason", "responses", "error", "gap") if k in job},
                            "matches": not differences, "differences": differences})
    return {**total, "response_count": len(paths), "api_error_count": len(errors), "unreturned_request_count": len(unreturned),
            "known_completion_subtotal": sum_usage(entries), "native": state.get("usage"), "jobs": crosschecks}


def changed(before: dict, after: dict) -> list[str]:
    return sorted(p for p in set(before) | set(after) if p != "WORKLOG.md" and before.get(p) != after.get(p))


def audit_materials(experiment: Path, variant: str, initial: dict):
    destination = experiment / "audit" / variant
    destination.mkdir(parents=True, exist_ok=True)
    source = experiment / "variants" / variant / "run"
    if initial.get("code", {}).get("passed"):
        for name in ("specs", "project"):
            if not (destination / name).exists():
                shutil.copytree(source / "snapshots/initial" / name, destination / name)
        build = experiment / "evaluation" / variant / "build0"
        if build.exists() and not (destination / "build_diagnostics").exists():
            shutil.copytree(build, destination / "build_diagnostics", ignore=shutil.ignore_patterns("project"))
    checklist = destination / "defects.csv"
    if not checklist.exists():
        with checklist.open("w", newline="") as output:
            csv.writer(output).writerow(["root_cause_id", "category", "spec_evidence", "code_evidence", "build_evidence",
                                       "merged_diagnostics", "local_syntax_only", "reviewer", "status"])
    commands = []
    for request in sorted((source / "logs").glob("*/api/request_*.json")):
        response = request.with_name(request.name.replace("request_", "response_"))
        if not response.is_file():
            continue
        calls = read_json(response).get("message", {}).get("tool_calls", [])
        for call in calls:
            if call["function"]["name"] == "run_command":
                commands.append({"request": str(request.relative_to(experiment)), "response": str(response.relative_to(experiment)),
                                 "arguments": call["function"]["arguments"],
                                 "tool_result": str(response.with_name(response.name.replace("response_", "tools_")).relative_to(experiment))})
    save_json(destination / "command_audit.json", {"status": "pending", "commands": commands,
                                                  "note": "Inspect command logs, outputs and exit codes manually; keywords do not establish equivalent checking."})


def collect(experiment: Path) -> dict:
    registration = read_json(experiment / "experiment.json")
    common = run_usage(experiment / "common/run")
    common_result = optional(experiment / "common/result.json")
    common_state = optional(experiment / "common/run/run.json")
    rows = []
    for variant in ("V0", "V1", "V2"):
        root = experiment / "variants" / variant
        run = root / "run"
        state = optional(run / "run.json")
        result = optional(root / "result.json")
        initial = optional(run / "snapshots/initial/metadata.json")
        final = optional(run / "snapshots/final/metadata.json")
        evaluation0 = optional(experiment / "evaluation" / variant / "initial/result.json")
        evaluation = optional(experiment / "evaluation" / variant / "final/result.json")
        branch = run_usage(run)
        failed_stages = {name: stage.get("result") for name, stage in state.get("stages", {}).items() if stage.get("status") == "failed"}
        comparison = sum_usage([common, branch])
        code = bool(initial.get("code", {}).get("passed"))
        revisions = []
        modified = set()
        for directory in sorted((run / "snapshots/precode").glob("precode_round_*")):
            before = optional(directory / "before.json")
            after = optional(directory / "result.json")
            names = changed(before.get("hashes", {}), after.get("hashes", {}))
            modified.update(names)
            revisions.append({"round": directory.name, "modified_files": names, "result": after.get("job"), "gate": after.get("gate")})
        if state.get("spec_repairs"):
            before = run / "snapshots/before_spec_repair/specs"
            if before.exists() and final.get("spec_hashes"):
                modified.update(changed(hashes(before), final["spec_hashes"]))
        scenario_counts = {phase: sum(s.get("status") == "passed" for s in evaluation.get("phases", {}).get(phase, {}).get("scenarios", []))
                           for phase in ("normal", "sanitize")}
        rows.append({"variant": variant, "N": 1, "Code": int(code), "Build0": int(code and bool(evaluation0.get("build0"))),
                     "Defects": "pending" if code else "NA", "Accept": int(bool(evaluation.get("passed"))),
                     "Tokens_M": comparison["tokens"] / 1e6 if comparison["tokens"] is not None else None,
                     "status": registration["attempts"][variant]["status"], "failure_reason": None if result.get("passed") else result.get("reason", "not_finished"),
                     "failed_stages": failed_stages, "accept_errors": evaluation.get("errors", [evaluation["reason"]] if "reason" in evaluation else []),
                     "initial_missing_reason": None if initial else optional(run / "snapshots/initial_missing.json").get("reason", result.get("initial_missing", "initial snapshot missing")),
                     "final_missing_reason": None if final else result.get("final_missing", "final snapshot missing"),
                     "branch_usage": branch, "comparison_usage": comparison,
                     "method_seconds": common_result.get("elapsed_seconds", 0) + result.get("elapsed_seconds", 0),
                     "evaluation_seconds": sum(optional(experiment / "evaluation" / variant / name / "result.json").get("elapsed_seconds", 0) for name in ("initial", "final")),
                     "implementation_repairs": state.get("implementation_repairs", 0), "spec_repairs": state.get("spec_repairs", 0),
                     "modified_file_count": len(modified), "modified_files": sorted(modified), "revisions": revisions,
                     "scenario_pass_counts": scenario_counts, "required_scenarios_per_mode": 16,
                     "initial_result": initial.get("result"), "final_result": final.get("result"),
                     "evaluation_status": evaluation.get("status", "not_executed")})
    pairs = {}
    by_variant = {r["variant"]: r for r in rows}
    for comparison, other in (("V2-V1", "V1"), ("V2-V0", "V0")):
        pairs[comparison] = {key: by_variant["V2"][key] - by_variant[other][key]
                             if isinstance(by_variant["V2"][key], (int, float)) and isinstance(by_variant[other][key], (int, float)) else None
                             for key in ("Code", "Build0", "Defects", "Accept", "Tokens_M", "method_seconds")}
    return {"protocol": "MQTT", "paired_blocks": 1, "registered_attempts": 3, "order": registration["order"],
            "variant_execution": registration.get("variant_execution"),
            "common_result": common_result, "common_usage": common, "rows": rows, "paired_differences": pairs,
            "common_failed_stages": {name: stage.get("result") for name, stage in common_state.get("stages", {}).items() if stage.get("status") == "failed"},
            "actual_batch_usage": sum_usage([common, *(r["branch_usage"] for r in rows)]),
            "freeze_control_error": registration.get("freeze_control_error"),
            "native_unchanged": registration.get("native_unchanged"), "evaluation_finished": registration.get("evaluation_finished", False)}


def write_report(experiment: Path) -> dict:
    report = collect(experiment)
    save_json(experiment / "summary.json", report)
    columns = ["variant", "N", "Code", "Build0", "Defects", "Accept", "Tokens_M", "status", "failure_reason",
               "method_seconds", "evaluation_seconds", "implementation_repairs", "spec_repairs", "modified_file_count"]
    with (experiment / "summary.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["rows"])
    lines = ["# MQTT RQ2 pilot", "", f"Paired blocks: 1; registered attempts: 3; submission order: {' / '.join(report['order'])}.",
             f"Variant execution: {report['variant_execution'] or 'not recorded'}.", "",
             "| Variant | Code | Build₀ | Defects | Accept | Tokens (M) | Status / reason |",
             "|---|---:|---:|---|---:|---:|---|"]
    for row in report["rows"]:
        tokens = f"{row['Tokens_M']:.6f}" if row["Tokens_M"] is not None else "unknown"
        lines.append(f"| {row['variant']} | {row['Code']}/1 | {row['Build0']}/1 | {row['Defects']} | {row['Accept']}/1 | {tokens} | {row['status']} / {row['failure_reason'] or 'completed'} |")
        audit_materials(experiment, row["variant"], optional(experiment / "variants" / row["variant"] / "run/snapshots/initial/metadata.json"))
    batch = report["actual_batch_usage"]["tokens"]
    if report["freeze_control_error"]:
        lines += ["", "Frozen control violation; this block cannot be used for a controlled comparison: " + report["freeze_control_error"]["error"]]
    lines += ["", "Each row includes the entire common generation cost plus that branch. Actual batch tokens: " + (str(batch) if batch is not None else "unknown") + ".",
              "Reasoning tokens are included in output, never added again; cache usage is recorded separately in summary.json.", "",
              "Paired differences (V2−V1 primary, V2−V0 secondary):", "", "```json", json.dumps(report["paired_differences"], ensure_ascii=False, indent=2), "```", "",
              "Defects remain pending until independent manual root-cause audit; no complete initial project means NA.",
              "Missing or unexecuted independent scenarios count as failures. One paired block validates the implementation and does not support statistical conclusions.", "",
              "Full gate/probe evidence: variants/<variant>/run/reports/rq2_gates. Autonomous commands and outcomes: common/run/logs and variants/<variant>/run/logs.",
              "Manual audit materials: audit/<variant> (root-cause table, initial Specs/code and build diagnostics; preserve human entries)."]
    (experiment / "summary.md").write_text("\n".join(lines) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    args = parser.parse_args()
    write_report(args.experiment.resolve())
    print(args.experiment.resolve() / "summary.md")


if __name__ == "__main__":
    main()
