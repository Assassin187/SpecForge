"""Read-only accounting of manually confirmed consistency incidents.

python expriments/RQ2/analysis/analyze.py --out expriments/RQ2/analysis/results
Additional trials can be included with --run PATH; unannotated trials have
unknown consistency counts, never zero. No model, build or evaluator is run.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CATEGORIES = ("interface_call", "implementation_test", "inherited_spec")
USAGE_FIELDS = ("input_tokens", "output_tokens", "requests", "elapsed_seconds")


def read_json(path):
    return json.loads(path.read_text())


def usage_sum(records):
    result = {key: sum(record.get(key, 0) for record in records) for key in USAGE_FIELDS}
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def coder_jobs(state):
    # spec_repair is a Planner job, despite also ending in "_repair".
    return [job for job in state["jobs"] if job["name"].split("_", 1)[1] in ("code", "repair")]


def measure_run(run, annotation):
    state = read_json(run / "run.json")
    jobs = coder_jobs(state)
    events = annotation.get("events", []) if annotation else []
    counts = Counter(event["category"] for event in events)
    corrections = sorted({ref for event in events if event["category"] != "inherited_spec"
                          for ref in event["coder_corrections"]})
    job_names = {job["name"] for job in jobs}
    for event in events:
        if event["category"] not in CATEGORIES:
            raise ValueError(f"Unknown incident category: {event['id']}")
        for ref in event["evidence"]:
            if not (run / ref).is_file():
                raise FileNotFoundError(run / ref)
    correction_usage = []
    for ref in corrections:
        job, response = ref.split("/")
        if job not in job_names:
            raise ValueError(f"Correction is not a Coder response: {ref}")
        correction_usage.append(read_json(run / "logs" / job / "api" / f"response_{response}.json")["usage"])
    code_usage = usage_sum([job["usage"] for job in jobs])
    correction_usage = usage_sum(correction_usage)
    assessed = bool(jobs and annotation)
    evaluation = state.get("evaluation")
    phases = {}
    if evaluation:
        report = read_json(run / evaluation["report"])
        for name, phase in report["phases"].items():
            scenarios = phase.get("scenarios", [])
            phases[name] = {"passed": phase["passed"], "required": phase["required_count"],
                            "passed_scenarios": sum(s["status"] == "passed" for s in scenarios)}
    result = {
        "run": str(run.relative_to(ROOT)),
        "system": "NoSpec" if state.get("pipeline") == "nospec-specforge" else "SpecForge",
        "protocol": run.name.removesuffix("_01"),
        "model": state["model"]["model"],
        "reasoning_effort": state["model"].get("reasoning_effort"),
        "input_hashes": state["input_hashes"],
        "framework_hashes": state["framework_hashes"],
        "entered_code": bool(jobs),
        "audit_status": "observed_lower_bound" if assessed else "not_entered_code" if not jobs else "unreviewed",
        "generation_passed": state["generation_passed"],
        "evaluation_passed": evaluation["passed"] if evaluation else None,
        "evaluation_phases": phases,
        "implementation_repairs": state["implementation_repairs"],
        "spec_repairs": state.get("spec_repairs", 0),
        "coder_responses": sum(job["responses"] for job in jobs),
        "coder_usage": code_usage,
        "whole_generation_usage": state["usage"],
        **{key: counts[key] if assessed else None for key in CATEGORIES},
        "implementation_incidents": counts["interface_call"] + counts["implementation_test"] if assessed else None,
        "all_incidents": len(events) if assessed else None,
        "coder_correction_responses": len(corrections) if assessed else None,
        "coder_correction_usage": correction_usage if assessed else None,
        "coder_correction_response_share": len(corrections) / sum(j["responses"] for j in jobs) if assessed else None,
        "coder_correction_token_share": correction_usage["total_tokens"] / code_usage["total_tokens"] if assessed else None,
        "events": events,
        "repair_windows": [],
    }
    if assessed:
        for window in annotation.get("repair_windows", []):
            records = [read_json(run / "logs" / window["job"] / "api" / f"response_{n:03}.json")["usage"]
                       for n in range(window["first_response"], window["last_response"] + 1)]
            usage = usage_sum(records)
            result["repair_windows"].append({**window, "usage": usage,
                                           "token_share": usage["total_tokens"] / code_usage["total_tokens"]})
    return result


def analyze(annotations, extra_runs):
    trials = {item["run"]: item for item in annotations["runs"]}
    paths = {ROOT / path for path in trials}
    paths.update(Path(path).resolve() for path in extra_runs)
    rows = [measure_run(path, trials.get(str(path.relative_to(ROOT)))) for path in sorted(paths)]
    groups = []
    for system, protocol in sorted({(row["system"], row["protocol"]) for row in rows}):
        selected = [row for row in rows if (row["system"], row["protocol"]) == (system, protocol)]
        reviewed = [row for row in selected if row["audit_status"] == "observed_lower_bound"]
        groups.append({"system": system, "protocol": protocol, "trials": len(selected),
                       "entered_code": sum(row["entered_code"] for row in selected), "reviewed": len(reviewed),
                       "generation_passed": sum(row["generation_passed"] for row in selected),
                       "evaluation_executed": sum(row["evaluation_passed"] is not None for row in selected),
                       "evaluation_passed": sum(row["evaluation_passed"] is True for row in selected),
                       "runs_with_implementation_incidents": sum(row["implementation_incidents"] > 0 for row in reviewed),
                       **{key: sum(row[key] for row in reviewed) if reviewed else None
                          for key in (*CATEGORIES, "implementation_incidents", "all_incidents")}})
    return {"definition": annotations["definition"], "runs": rows, "groups": groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run", action="append", default=[])
    args = parser.parse_args()
    annotations = read_json(HERE / "annotations.json")
    result = analyze(annotations, args.run)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    fields = ("run", "system", "protocol", "model", "audit_status", "entered_code", "generation_passed",
              "evaluation_passed", "coder_responses", "implementation_repairs", "spec_repairs", *CATEGORIES,
              "implementation_incidents", "all_incidents", "coder_correction_responses", "coder_correction_response_share",
              "coder_correction_token_share")
    with (args.out / "metrics.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["runs"])
    incident_fields = ("run", "id", "category", "conflict", "coder_corrections", "evidence")
    with (args.out / "incidents.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=incident_fields)
        writer.writeheader()
        for row in result["runs"]:
            for event in row["events"]:
                writer.writerow({"run": row["run"], **{key: " | ".join(event[key]) if isinstance(event[key], list) else event[key]
                                                      for key in incident_fields[1:]}})
    print(json.dumps({"runs": len(result["runs"]), "confirmed_incidents": sum(row["all_incidents"] or 0 for row in result["runs"]),
                      "output": str(args.out.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
