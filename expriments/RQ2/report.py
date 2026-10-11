"""Auditable end-to-end accounting; reporting never creates model clients."""

from __future__ import annotations

import csv
from pathlib import Path

from specforge.documents import read_json, save_json
from specforge.llm import total_usage


def job_evidence(job: dict, api: Path, run: Path, phase: str) -> dict:
    responses = [read_json(p) for p in sorted(api.glob("response_*.json"))]
    attempts = len(list(api.glob("request_*.json")))
    unknown = (attempts > len(responses) or bool(list(api.glob("api_error_*.json")))
               or job.get("usage_unknown", False))
    return {**job, "phase": phase, "api_logs": str(api.relative_to(run)),
            "responses_recorded": len(responses), "requests_recorded": attempts,
            "usage_unknown": unknown, "usage_entries": [r["usage"] for r in responses],
            "truncated_responses": sum(r.get("finish_reason") == "length" for r in responses),
            "queue_errors": sum(len(r.get("queue_errors", [])) for r in responses)}


def aggregate_usage(jobs: list[dict]) -> dict:
    entries = [u for j in jobs for u in j["usage_entries"]]
    unknown = any(j["usage_unknown"] for j in jobs)
    known = total_usage(entries) if entries else {
        **{k: 0 for k in ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens")},
        "requests": 0, "elapsed_seconds": 0}
    known["total_tokens"] = (known["input_tokens"] + known["output_tokens"]
                             if known["input_tokens"] is not None and known["output_tokens"] is not None else None)
    full = dict(known)
    if unknown:
        for field in ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens",
                      "reasoning_tokens", "total_tokens", "elapsed_seconds"):
            full[field] = None
    return {"usage": full, "known_recorded_usage": known, "usage_unknown": unknown,
            "responses_recorded": sum(j["responses_recorded"] for j in jobs),
            "requests_recorded": sum(j["requests_recorded"] for j in jobs),
            "truncated_responses": sum(j["truncated_responses"] for j in jobs),
            "queue_errors": sum(j["queue_errors"] for j in jobs)}


def trial_account(run: Path, trial: dict) -> dict:
    jobs = []
    root = run / "trials" / trial["id"]
    if trial.get("planning_state"):
        planning = read_json(run / trial["planning_state"])
        for job in planning["jobs"]:
            phase = job["name"].split("_", 1)[1]
            if phase == "spec_review" and job.get("spec_revision", 1) > 1:
                phase = "spec_repair_review"
            jobs.append(job_evidence(job, root / "planning/logs" / job["name"] / "api", run, phase))
    for job in trial["jobs"]:
        jobs.append(job_evidence(job, root / "logs" / job["name"] / "api", run, job["role"]))
    phases = {}
    for phase in ("facts", "design", "specs", "spec_review", "spec_repair", "spec_repair_review", "code", "repair"):
        selected = [j for j in jobs if j["phase"] == phase]
        phases[phase] = {**aggregate_usage(selected), "jobs": len(selected),
                         "wall_seconds": sum(j["wall_seconds"] for j in selected) if all("wall_seconds" in j for j in selected) else None,
                         "passed_jobs": sum(bool(j.get("passed")) for j in selected)}
    return {"trial": trial["id"], "condition": trial["condition"], "status": trial["status"],
            "generation_passed": trial["generation_passed"], "stop_reason": trial.get("stop_reason"),
            "first_spec_publication": trial.get("first_spec_publication", {
                "passed": None if trial["condition"] == "D" or trial["status"] in ("pending", "running") else False}),
            "initial_code_job": [{k: j.get(k) for k in ("passed", "reason", "responses", "wall_seconds")}
                                 for j in trial["jobs"] if j["role"] == "code"],
            "initial_evaluation": trial["initial_evaluation"], "final_evaluation": trial["evaluation"],
            "implementation_repairs": trial["implementation_repairs"], "spec_repairs": trial["spec_repairs"],
            "modification_operations": len(trial["modification_events"]), "gate_events": len(trial["gate_events"]),
            "end_to_end_wall_seconds": trial.get("elapsed_seconds"), "accounting": aggregate_usage(jobs),
            "phases": phases, "jobs": jobs,
            "spec_revisions": trial["spec_revisions"], "planning_state": trial.get("planning_state"),
            "evidence": {"trial_root": str(root.relative_to(run)), "error": trial.get("error")}}


def _csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize_experiment(run: Path, state: dict) -> dict:
    output = run / "summary"
    output.mkdir(exist_ok=True)
    accounts = [trial_account(run, t) for t in state["trials"]]
    result = {"schema_version": 2, "protocol": state["protocol"], "model": state["model"],
              "stage_models": state["stage_models"],
              "generation_finished": state["generation_finished"], "evaluation_finished_at": state.get("evaluation_finished_at"),
              "schedule": state["schedule"], "trials": accounts,
              "interpretation": "One fresh attempt per condition: MQTT case evidence, not statistical significance or stability. "
                                "P-G compares independent specification formation and handoff, including independently chosen designs. "
                                "D has the same code budget but no separate upstream planning budget. "
                                "Scenarios, build modes and snapshots are not independent samples.",
              "accounting_note": "Recorded API usage includes truncated completions and all upstream/repair/review jobs. "
                                 "Unknown consumption after API/interruption failures stays unknown; recorded usage is a subtotal. "
                                 "SDK transport retries are not individually observable. Reasoning tokens are a subset of output. "
                                 "Modification operations are not independent defects; spec references are not verified root causes."}
    save_json(output / "results.json", result)
    rows, stages, scenarios = [], [], []
    markdown = ["# MQTT RQ2 end-to-end results", "", result["interpretation"], "", result["accounting_note"], "",
                f'Single-response output limits: Design {state["stage_models"]["design"]["max_tokens"]} tokens; other jobs {state["model"]["max_tokens"]} tokens.', "",
                "| Condition | Development | Initial acceptance | Final acceptance | Code repairs | Spec repairs | Recorded responses | Total tokens | Seconds |",
                "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for account in sorted(accounts, key=lambda a: a["condition"]):
        usage = account["accounting"]
        row = {"trial": account["trial"], "condition": account["condition"], "status": account["status"],
               "development_passed": None if account["status"] in ("pending", "running") else account["generation_passed"],
               "stop_reason": account["stop_reason"],
               "initial_acceptance_passed": (account["initial_evaluation"] or {}).get("passed"),
               "final_acceptance_passed": (account["final_evaluation"] or {}).get("passed"),
               "implementation_repairs": account["implementation_repairs"], "spec_repairs": account["spec_repairs"],
               "modification_operations": account["modification_operations"], "gate_events": account["gate_events"],
               "requests_recorded": usage["requests_recorded"], "responses_recorded": usage["responses_recorded"],
               "usage_unknown": usage["usage_unknown"], "input_tokens": usage["usage"]["input_tokens"],
               "output_tokens": usage["usage"]["output_tokens"], "total_tokens": usage["usage"]["total_tokens"],
               "recorded_total_tokens": usage["known_recorded_usage"]["total_tokens"],
               "wall_seconds": account["end_to_end_wall_seconds"]}
        rows.append(row)
        flag = lambda v: "pending" if v is None else "passed" if v else "failed"
        tokens = str(row["total_tokens"]) if row["total_tokens"] is not None else "unknown"
        markdown.append(f'| {row["condition"]} | {flag(row["development_passed"])} | {flag(row["initial_acceptance_passed"])} | '
                        f'{flag(row["final_acceptance_passed"])} | {row["implementation_repairs"]} | {row["spec_repairs"]} | '
                        f'{row["responses_recorded"]} | {tokens} | {row["wall_seconds"]} |')
        for phase, values in account["phases"].items():
            stages.append({"trial": account["trial"], "condition": account["condition"], "phase": phase,
                           "jobs": values["jobs"], "passed_jobs": values["passed_jobs"],
                           "responses_recorded": values["responses_recorded"], "usage_unknown": values["usage_unknown"],
                           "input_tokens": values["usage"]["input_tokens"], "output_tokens": values["usage"]["output_tokens"],
                           "total_tokens": values["usage"]["total_tokens"], "recorded_total_tokens": values["known_recorded_usage"]["total_tokens"],
                           "wall_seconds": values["wall_seconds"]})
        for snapshot, evaluation in (("initial", account["initial_evaluation"]), ("final", account["final_evaluation"])):
            if evaluation is None:
                continue
            report = read_json(run / evaluation["report"])
            for mode, phase in report["phases"].items():
                for scenario in phase["scenarios"]:
                    scenarios.append({"trial": account["trial"], "condition": account["condition"], "snapshot": snapshot,
                                      "mode": mode, "scenario": scenario["id"], "status": scenario["status"],
                                      "error": scenario.get("error"), "report": evaluation["report"]})
    _csv(output / "trials.csv", rows, list(rows[0]))
    _csv(output / "stages.csv", stages, list(stages[0]))
    _csv(output / "scenarios.csv", scenarios, ["trial", "condition", "snapshot", "mode", "scenario", "status", "error", "report"])
    markdown += ["", "Initial means the project saved after the initial code job, including compilation and self-testing within that job.",
                 "", "## Evidence", "", "- [Full accounting](results.json)", "- [Trial table](trials.csv)",
                 "- [Stage costs and time](stages.csv)", "- [All scenario outcomes](scenarios.csv)",
                 "- [Frozen design, inputs, budgets and attempt order](../experiment.json)", ""]
    for account in accounts:
        markdown.append(f'- {account["trial"]}: [all artifacts](../{account["evidence"]["trial_root"]}/)')
        for name, evaluation in (("initial", account["initial_evaluation"]), ("final", account["final_evaluation"])):
            if evaluation:
                markdown.append(f'  - [{name} independent acceptance](../{evaluation["report"]})')
    (output / "README.md").write_text("\n".join(markdown) + "\n")
    return state
