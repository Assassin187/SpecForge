"""Descriptive RQ2 results and reviewable evidence; no model-based judging."""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from specforge.documents import read_json, save_json
from spec_views import CONDITIONS
from run import aggregate_usage, normalize_evaluation

CATEGORIES = ("protocol_behavior", "wire_and_framing", "state_and_io",
              "interfaces_and_calls", "resource_lifecycle", "spec_contradiction")
LIMITATIONS = (
    "Generic is converted from Full, not independently generated.",
    "Generic differs in both explicit content and representation; it does not isolate representation alone.",
    "Field ablations retain duplicate semantics in behavior prose and other retained fields.",
    "Independent generations, not individual scenarios, are the experimental samples.",
    "Historical source formation is a shared, previously incurred cost; it is not new experimental spend.",
    "Model response counts exclude unreported SDK transport attempts; missing usage is unknown.",
    "Semantic root causes require human verification; access logs and condition differences alone are associations.")


def _csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _stats(values: list) -> dict:
    values = [v for v in values if v is not None]
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def _artifacts(run: Path, trial: dict) -> tuple[dict, list, list]:
    counts, requested, accesses, evidence = Counter(), Counter(), [], []
    api_errors, queue_errors = 0, 0
    for folder in sorted((run / "trials" / trial["id"] / "logs").glob("*/api")):
        api_errors += len(list(folder.glob("api_error_*.json")))
        for path in sorted(folder.glob("response_*.json")):
            response = read_json(path)
            queue_errors += len(response.get("queue_errors", []))
            tool_path = path.with_name(path.name.replace("response_", "tools_"))
            results = {r["tool_call_id"]: r["result"] for r in read_json(tool_path)} if tool_path.is_file() else {}
            for call in response["message"].get("tool_calls", []):
                name = call["function"]["name"]
                requested[name] += 1
                if call["id"] in results:
                    counts[name] += 1
                execution = ("error" if "error" in results[call["id"]] else "reported") if call["id"] in results else (
                    "not_executed" if response.get("finish_reason") == "length" else "unknown")
                try:
                    args = json.loads(call["function"]["arguments"])
                except ValueError:
                    args = {"invalid_arguments": True}
                entry = {"trial": trial["id"], "condition": trial["condition"], "job": folder.parent.name,
                         "response": path.stem, "tool": name, "arguments": args,
                         "tool_call_id": call["id"], "execution_status": execution,
                         "response_log": str(path.relative_to(run)),
                         "tool_results": str(tool_path.relative_to(run)) if tool_path.is_file() else None,
                         "transform_log": None if trial["condition"] == "direct" else
                                          f"control/{trial['condition']}_transform.json"}
                evidence.append(entry)
                if name in ("read_file", "search", "list_files"):
                    accesses.append({**entry, "path": args.get("path"),
                                     "json_pointers": json.dumps(args.get("json_pointers", []), ensure_ascii=False),
                                     "start_line": args.get("start_line"), "line_count": args.get("line_count"),
                                     "keyword": args.get("keyword")})
    return {"tool_calls": sum(counts.values()), "tool_call_counts": dict(counts),
            "requested_tool_calls": sum(requested.values()),
            "unknown_tool_calls": sum(e["execution_status"] == "unknown" for e in evidence),
            "api_errors": api_errors, "queue_errors": queue_errors,
            "unreported_transport_usage": bool(api_errors or queue_errors)}, accesses, evidence


def summarize_experiment(run: Path, state: dict) -> dict:
    output = run / "summary"
    output.mkdir(exist_ok=True)
    trial_rows, scenario_rows, stage_rows, access_rows, evidence, attribution = [], [], [], [], [], []
    costs = []
    history = read_json(run / "control/historical_cost.json")
    protocol = {"mqtt": "MQTT", "coap": "CoAP", "smtp": "SMTP"}[state["protocol"]]
    revision = Path(state["source"]).name
    scenario_count = len(state["expected_scenarios"])
    for trial in state["trials"]:
        metrics, accesses, calls = _artifacts(run, trial)
        access_rows.extend(accesses)
        evidence.extend(calls)
        jobs = trial["jobs"]
        usage = aggregate_usage(jobs)
        evaluation = (normalize_evaluation(read_json(run / trial["evaluation"]["report"]), state["expected_scenarios"])
                      if trial.get("evaluation") else None)
        initial = (normalize_evaluation(read_json(run / trial["initial_evaluation"]["report"]), state["expected_scenarios"])
                   if trial.get("initial_evaluation") else None)
        evaluated = evaluation is not None
        gate_events = trial.get("gate_events", [])
        final_gate = gate_events[-1] if gate_events else {}
        row = {"trial": trial["id"], "condition": trial["condition"], "repeat": trial["repeat"],
               "status": trial["status"], "stop_reason": trial.get("stop_reason"),
               "generation_passed": trial["generation_passed"], "evaluated": evaluated,
               "independent_passed": bool(evaluation and evaluation["passed"]),
               "initial_submitted": trial.get("initial_snapshot") is not None,
               "initial_responses": (trial.get("initial_snapshot") or {}).get("responses"),
               "initial_seconds": (trial.get("initial_snapshot") or {}).get("elapsed_seconds"),
               "initial_evaluated": initial is not None,
               "initial_independent_passed": bool(initial and initial["passed"]),
               "initial_evaluation_seconds": initial.get("wall_seconds") if initial else None,
               "evaluation_seconds": evaluation.get("wall_seconds") if evaluation else None,
               "repair_operations": len(trial.get("repair_events", [])),
               "repair_source_operations": sum(any(f["category"] == "source" for f in e["files"])
                                               for e in trial.get("repair_events", [])),
               "repair_test_operations": sum(any(f["category"] == "test" for f in e["files"])
                                             for e in trial.get("repair_events", [])),
               "repair_build_operations": sum(any(f["category"] == "build" for f in e["files"])
                                              for e in trial.get("repair_events", [])),
               "repair_responses": len({(e["job"], e["response"]) for e in trial.get("repair_events", [])}),
               "failed_development_gate_events": sum(not e["passed"] for e in gate_events),
               "implementation_repairs": trial["implementation_repairs"],
               "responses": sum(j.get("responses", 0) for j in jobs),
               "context_resets": sum(j.get("context_resets", 0) for j in jobs),
               "wall_seconds": trial.get("elapsed_seconds"),
               "first_build_responses": trial.get("first_normal_build", {}).get("responses"),
               "first_build_seconds": trial.get("first_normal_build", {}).get("elapsed_seconds"),
               "total_tokens": usage["total_tokens"], "input_tokens": usage["input_tokens"],
               "output_tokens": usage["output_tokens"], "cache_hit_tokens": usage["cache_hit_tokens"],
               "cache_miss_tokens": usage["cache_miss_tokens"], "reasoning_tokens": usage["reasoning_tokens"],
               "model_wait_seconds": usage["elapsed_seconds"], **metrics}
        for phase in ("normal", "sanitize"):
            phase_report = evaluation["phases"][phase] if evaluation else {}
            row[phase + "_passed"] = sum(s["status"] == "passed" for s in phase_report.get("scenarios", []))
            row[phase + "_build_passed"] = bool(
                evaluation and evaluation.get("builds", {}).get(phase, {}).get("exit_code") == 0 and
                not evaluation["builds"][phase].get("timed_out"))
            row[phase + "_development_build_passed"] = bool(
                final_gate.get("builds", {}).get(phase, {}).get("exit_code") == 0 and
                not final_gate["builds"][phase].get("timed_out"))
            initial_phase = initial["phases"][phase] if initial else {}
            row["initial_" + phase + "_passed"] = sum(s["status"] == "passed" for s in initial_phase.get("scenarios", []))
            initial_build = initial.get("builds", {}).get(phase, {}) if initial else {}
            row["initial_" + phase + "_build_passed"] = (
                initial_build.get("exit_code") == 0 and not initial_build.get("timed_out", True)) if initial else None
            row["initial_" + phase + "_behavior_status"] = (
                "passed" if initial_phase.get("passed") else
                "failed" if any(s["status"] != "not_executed" for s in initial_phase.get("scenarios", [])) else
                "not_executed" if initial else "not_evaluated")
            for version, report, key in (("initial", initial, "initial_evaluation"), ("final", evaluation, "evaluation")):
                scenarios = report["phases"][phase].get("scenarios", []) if report else []
                for scenario_id in state["expected_scenarios"]:
                    scenario = next((s for s in scenarios if s["id"] == scenario_id), {})
                    scenario_row = {"trial": trial["id"], "condition": trial["condition"], "repeat": trial["repeat"],
                                    "version": version, "phase": phase, "scenario": scenario_id,
                                    "status": scenario.get("status", "not_executed"),
                                    "error": scenario.get("error", "" if report else "not_evaluated"),
                                    "elapsed_seconds": scenario.get("elapsed_seconds"),
                                    "report": trial[key]["report"] if report else ""}
                    scenario_rows.append(scenario_row)
                    if report and scenario_row["status"] != "passed":
                        attribution.append({
                            "id": f"{trial['id']}:" + ("initial:" if version == "initial" else "") + f"{phase}:{scenario_id}",
                            "trial": trial["id"], "condition": trial["condition"], "scenario": scenario_id,
                            "observed_failure": scenario_row["error"], "review_status": "unreviewed",
                            "candidate_category": "", "root_cause": "", "spec_refs": "",
                            "evidence_refs": scenario_row["report"], "notes": ""})
        trial_rows.append(row)
        for job in jobs:
            stage = {"trial": trial["id"], "condition": trial["condition"], "stage": job["name"],
                     "passed": job["passed"], "reason": job["reason"], "responses": job.get("responses", 0),
                     "wall_seconds": job.get("wall_seconds"), **aggregate_usage([job])}
            stage_rows.append(stage)
            costs.append({**stage, "ledger": "new_codegen", "amortization_reuses": 1})
        if not jobs:
            stage_rows.append({"trial": trial["id"], "condition": trial["condition"], "stage": "code",
                               "passed": False, "reason": trial.get("stop_reason", "not_started")})
        for event_index, event in enumerate(gate_events):
            for error_index, error in enumerate(event["errors"]):
                attribution.append({"id": f"{trial['id']}:gate:{event_index}:{error_index}",
                                    "trial": trial["id"], "condition": trial["condition"], "scenario": "development",
                                    "observed_failure": error, "review_status": "unreviewed", "candidate_category": "",
                                    "root_cause": "", "spec_refs": "", "evidence_refs":
                                    f"trials/{trial['id']}/{event['report']}", "notes": ""})
        if trial.get("spec_gap"):
            gap = trial["spec_gap"]
            attribution.append({"id": trial["id"] + ":spec_gap", "trial": trial["id"],
                                "condition": trial["condition"], "scenario": "spec_gap",
                                "observed_failure": gap["problem"], "review_status": "unreviewed",
                                "candidate_category": "", "root_cause": "",
                                "spec_refs": json.dumps(gap["spec_refs"], ensure_ascii=False),
                                "evidence_refs": gap["evidence"], "notes": ""})
        costs.append({"ledger": "new_evaluation", "trial": trial["id"], "condition": trial["condition"],
                      "stage": "independent_evaluation", "total_tokens": 0,
                      "wall_seconds": evaluation.get("wall_seconds") if evaluation else None})
        costs.append({"ledger": "new_evaluation", "trial": trial["id"], "condition": trial["condition"],
                      "stage": "initial_independent_evaluation", "total_tokens": 0,
                      "wall_seconds": initial.get("wall_seconds") if initial else None})

    groups = {}
    selected = state.get("conditions", list(CONDITIONS))
    for condition in selected:
        rows = [r for r in trial_rows if r["condition"] == condition]
        evaluated = sum(r["evaluated"] for r in rows)
        groups[condition] = {
            "planned_attempts": len(rows), "evaluated_attempts": evaluated,
            "generation_passed": sum(r["generation_passed"] for r in rows),
            "initial_submitted": sum(r["initial_submitted"] for r in rows),
            "initial_independent_passed": sum(r["initial_independent_passed"] for r in rows),
            "initial_independent_pass_rate": sum(r["initial_independent_passed"] for r in rows) / len(rows)
                                             if any(r["initial_evaluated"] for r in rows) else None,
            "repair_operations": _stats([r["repair_operations"] for r in rows if r["status"] != "pending"]),
            "independent_passed": sum(r["independent_passed"] for r in rows),
            "independent_pass_rate": sum(r["independent_passed"] for r in rows) / len(rows) if evaluated else None,
            "normal_scenario_pass_rate": sum(r["normal_passed"] for r in rows) / (len(rows) * len(state["expected_scenarios"])) if evaluated else None,
            "sanitize_scenario_pass_rate": sum(r["sanitize_passed"] for r in rows) / (len(rows) * len(state["expected_scenarios"])) if evaluated else None,
            "independent_normal_build_passed": sum(r["normal_build_passed"] for r in rows),
            "independent_sanitize_build_passed": sum(r["sanitize_build_passed"] for r in rows),
            "development_normal_build_passed": sum(r["normal_development_build_passed"] for r in rows),
            "development_sanitize_build_passed": sum(r["sanitize_development_build_passed"] for r in rows),
            "total_tokens": _stats([r["total_tokens"] for r in rows]),
            "wall_seconds": _stats([r["wall_seconds"] for r in rows]),
            "implementation_repairs": _stats([r["implementation_repairs"] for r in rows if r["status"] != "pending"]),
            "responses": _stats([r["responses"] for r in rows if r["status"] != "pending"])}
    contrasts = []
    meanings = {"direct": "complete intermediate representation, planning and public interfaces",
                "generic": "joint effect of explicit enhancements and representation",
                "full_no_vectors": "conditional contribution of explicit test vectors",
                "full_no_contracts": "conditional contribution of explicit wire/call contracts"}
    for baseline, meaning in meanings.items():
        if "full" not in groups or baseline not in groups:
            continue
        full_rate, base_rate = groups["full"]["independent_pass_rate"], groups[baseline]["independent_pass_rate"]
        contrasts.append({"treatment": "full", "baseline": baseline, "interpretation": meaning,
                          "independent_pass_rate_difference": full_rate - base_rate
                          if full_rate is not None and base_rate is not None else None})
    h = history["source_formation"]
    costs.append({"ledger": "historical_shared_source", "stage": revision + "_formation", **h,
                  "wall_seconds": history["source_formation_wall_seconds"], "amortization_reuses": 1})
    amortized = {}
    for reuses in (1, 3):
        amortized[str(reuses)] = {k: v / reuses if isinstance(v, (int, float)) else v
                                  for k, v in h.items()}
    for condition in selected:
        if condition == "direct":
            continue
        rows = [r for r in trial_rows if r["condition"] == condition]
        for reuses in (1, 3):
            downstream = _stats([r["total_tokens"] for r in rows])["mean"]
            source_tokens = amortized[str(reuses)]["total_tokens"]
            costs.append({"ledger": "modeled_source_reuse", "condition": condition, "stage": "per_project",
                          "amortization_reuses": reuses, "source_tokens_per_project": source_tokens,
                          "total_tokens": source_tokens + downstream
                          if source_tokens is not None and downstream is not None else None})
    costs.append({"ledger": "new_preparation", "stage": "views_and_preflight", "total_tokens": 0,
                  "wall_seconds": state["prepare_elapsed_seconds"]})
    for condition, view in state["views"].items():
        costs.append({"ledger": "preparation_detail", "condition": condition, "stage": "input_view",
                      "total_tokens": 0, "wall_seconds": view["elapsed_seconds"]})
    feedback = "with" if history["coding_feedback"]["requests"] else "without"
    limitations = [f"{protocol} only; one frozen {revision} Spec {feedback} historical coding feedback; {state['repetitions']} generations per condition.",
                   *LIMITATIONS]
    limitations.append("Initial drafts prohibit commands and existing code revisions until a complete neutral delivery is frozen; this differs from the earlier interleaved pilot.")
    limitations.append("Repair counts are observed file revision operations after draft freezing, not verified distinct defects; one operation can touch multiple categories.")
    if state.get("execution", {}).get("workers", 1) > 1:
        limitations.append("Parallel trials share API and host resources; their timing includes concurrent resource contention.")
    summary = {"schema_version": 1, "protocol": state["protocol"], "source": state["source"],
               "expected_scenarios": state["expected_scenarios"],
               "trials": trial_rows, "conditions": groups, "contrasts": contrasts,
               "execution": state.get("execution"),
               "historical_cost": history, "source_amortization": amortized, "limitations": limitations,
               "attribution_categories": list(CATEGORIES), "experiment_complete":
               all(t.get("evaluation") is not None and t.get("initial_evaluation") is not None for t in state["trials"])}
    save_json(output / "results.json", summary)
    _csv(output / "trials.csv", [k for k in trial_rows[0] if k != "tool_call_counts"], trial_rows)
    _csv(output / "scenarios.csv", list(scenario_rows[0]), scenario_rows)
    _csv(output / "stages.csv", ["trial", "condition", "stage", "passed", "reason", "responses", "requests",
                                "input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens",
                                "reasoning_tokens", "elapsed_seconds", "wall_seconds"], stage_rows)
    _csv(output / "costs.csv", ["ledger", "trial", "condition", "stage", "amortization_reuses",
                               "source_tokens_per_project", "input_tokens", "output_tokens", "total_tokens",
                               "cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens", "requests",
                               "elapsed_seconds", "wall_seconds"], costs)
    _csv(output / "spec_access.csv", ["trial", "condition", "job", "response", "tool", "path", "json_pointers",
                                     "start_line", "line_count", "keyword", "tool_call_id", "execution_status",
                                     "response_log", "tool_results",
                                     "transform_log"], access_rows)
    with (output / "evidence.jsonl").open("w", encoding="utf-8") as handle:
        for entry in evidence:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        for trial in state["trials"]:
            for event in trial.get("repair_events", []):
                handle.write(json.dumps({"trial": trial["id"], "kind": "repair_operation", **event}) + "\n")
            for event in trial.get("gate_events", []):
                handle.write(json.dumps({"trial": trial["id"], "kind": "development_gate", **event}) + "\n")
    fields = ["id", "trial", "condition", "scenario", "observed_failure", "review_status",
              "candidate_category", "root_cause", "spec_refs", "evidence_refs", "notes"]
    existing = {}
    if (output / "attribution.csv").exists():
        with (output / "attribution.csv").open(newline="", encoding="utf-8") as handle:
            existing = {row["id"]: row for row in csv.DictReader(handle)}
    for row in attribution:
        for key in ("review_status", "candidate_category", "root_cause", "spec_refs", "evidence_refs", "notes"):
            if row["id"] in existing:
                row[key] = existing[row["id"]][key]
    _csv(output / "attribution.csv", fields, attribution)
    lines = [f"# {protocol} RQ2 results", "",
             "Complete" if summary["experiment_complete"] else "Incomplete: planned attempts remain visible; this is not a finished comparison.",
             "", "| Condition | Generation | Independent complete | Normal scenarios | Sanitizer scenarios |",
             "| --- | --- | --- | --- | --- |"]
    for condition, group in groups.items():
        rows = [r for r in trial_rows if r["condition"] == condition]
        denominator = group["planned_attempts"]
        lines.append(f"| {condition} | {group['generation_passed']}/{denominator} | "
                     f"{group['independent_passed']}/{denominator} | "
                     f"{sum(r['normal_passed'] for r in rows)}/{denominator * len(state['expected_scenarios'])} | "
                     f"{sum(r['sanitize_passed'] for r in rows)}/{denominator * len(state['expected_scenarios'])} |")
    lines += ["", f"Initial and final metrics (N/S = normal/sanitizer; behavior is passed scenarios out of {scenario_count}):", "",
              "| Trial | Initial build N/S | Initial behavior N/S | Repair operations (source/test/build) | Final complete | Total tokens | Responses | Seconds |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in trial_rows:
        builds = "/".join("pending" if row["initial_" + p + "_build_passed"] is None else
                          "pass" if row["initial_" + p + "_build_passed"] else "fail" for p in ("normal", "sanitize"))
        behavior = "/".join(str(row["initial_" + p + "_passed"]) + f"/{scenario_count} (" +
                            row["initial_" + p + "_behavior_status"] + ")" for p in ("normal", "sanitize"))
        lines.append(f"| {row['trial']} | {builds} | {behavior} | {row['repair_operations']} "
                     f"({row['repair_source_operations']}/{row['repair_test_operations']}/{row['repair_build_operations']}) | "
                     f"{row['independent_passed']} | {row['total_tokens']} | {row['responses']} | {row['wall_seconds']} |")
    lines += ["", "Initial = first complete neutral delivery, frozen before any commands or source revisions. "
              "Both initial and final independent evaluations run only after all generation finishes; no acceptance feedback reaches Coder.",
              "Repair operations include edits, rewrites and shell mutations observed by source hashes; repeated fixes may concern one defect. "
              "Category counts can overlap when one operation modifies several kinds of file.",
              "", f"Primary endpoint: all {scenario_count} frozen scenarios must execute and pass in both modes, with clean builds and instrumentation.",
              "", "## Cost accounting", "",
              f"Historical {revision} formation: {h['total_tokens']} tokens, counted once as a shared source.",
              "New generation, evaluation and preparation are separate ledgers in costs.csv.",
              "Modeled reuse rows are estimates, not additional actual expenditure. No currency conversion is performed.",
              "", "## Mechanism evidence", "",
              "Use spec_access.csv and evidence.jsonl with controller transformation logs and native request/command reports.",
              "Fill attribution.csv only after inspecting evidence. Keep unverified causes unreviewed.",
              "Categories: " + ", ".join(CATEGORIES) + ".", "",
              "## Limits", ""] + ["- " + text for text in limitations]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return state
