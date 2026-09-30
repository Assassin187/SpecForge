"""One sequential native tool-call loop shared by the three roles."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from .documents import read_json, save_json
from .llm import LLM, total_usage
from .tools import ToolRuntime


def run_agent(llm: LLM, runtime: ToolRuntime, prefix: str, task: str, response_limit: int,
              log_dir: Path, on_progress=None) -> dict:
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_path = log_dir / "progress.json"
    progress = read_json(progress_path) if progress_path.is_file() else {}
    usage = progress.get("usage_entries", [])
    resets = progress.get("context_resets", 0)
    checkpoint_pending = False
    checkpoint_number = 0
    previous_checkpoint = ""
    messages = [{"role": "system", "content": prefix}, {"role": "user", "content": task}]
    existing_checkpoint = runtime.work / "WORKLOG.md"
    if existing_checkpoint.is_file():
        messages[-1]["content"] += "\nExisting disk checkpoint (the current task takes precedence over earlier phase instructions):\n" + existing_checkpoint.read_text()[:8000]
    start = progress.get("responses", 0)
    if start:
        checkpoint = runtime.work / "WORKLOG.md"
        messages[-1]["content"] += "\nResuming this bounded job from disk. " + (checkpoint.read_text()[:12000] if checkpoint.is_file() else "Re-read existing artifacts.")
    report = {"passed": False, "reason": "response_limit", "responses": start}

    def persist(final=False):
        snapshot = {**report, "reason": report["reason"] if final else "running", "usage": total_usage(usage), "context_resets": resets}
        save_json(progress_path, {**snapshot, "usage_entries": usage})
        if on_progress:
            on_progress(snapshot)

    for number in range(start + 1, response_limit + 1):
        active_tools = runtime.tool_definitions()
        if checkpoint_pending:
            active_tools = [{"type": "function", "function": {"name": "write_file", "description": "Save the requested compact checkpoint only.",
                "parameters": {"type": "object", "additionalProperties": False, "required": ["path", "content"], "properties": {
                    "path": {"type": "string", "const": "/work/WORKLOG.md"}, "content": {"type": "string", "maxLength": 8000}}}}}]
        active_schemas = {t["function"]["name"]: t["function"]["parameters"] for t in active_tools}
        save_json(log_dir / f"request_{number:03d}.json", {"messages": messages, "tools": active_tools, "model_config": llm.config.record()})
        try:
            response = llm.complete(messages, active_tools)
        except Exception as exc:
            # Transport failures are evidence, not a successful model completion.
            report.update(reason="api_error", error=f"{type(exc).__name__}: {exc}")
            save_json(log_dir / f"api_error_{number:03d}.json", {"error": report["error"]})
            break
        save_json(log_dir / f"response_{number:03d}.json", response)
        usage.append(response["usage"])
        report["responses"] = number
        if response["finish_reason"] == "length":
            report["reason"] = "truncated_response"
            persist(final=True)
            break  # Never execute partial/truncated tool calls.
        assistant = response["message"]
        messages.append(assistant)
        calls = assistant.get("tool_calls", [])
        tool_results = []
        for call in calls:
            try:
                arguments = json.loads(call["function"]["arguments"])
                name = call["function"]["name"]
                if name not in active_schemas:
                    raise ValueError("Only the checkpoint write tool is currently available")
                jsonschema.Draft202012Validator(active_schemas[name]).validate(arguments)
                result = runtime.dispatch(call["function"]["name"], arguments)
            except jsonschema.ValidationError as exc:
                result = {"error": f"Invalid parameter {'/'.join(map(str, exc.absolute_path))}: {exc.validator}={exc.validator_value}"}
            except (ValueError, TypeError) as exc:
                result = {"error": f"Invalid tool arguments: {exc}"}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
            tool_results.append({"tool_call_id": call["id"], "name": call["function"]["name"], "result": result})
        save_json(log_dir / f"tools_{number:03d}.json", tool_results)
        persist()
        print(f"  response {number}/{response_limit}: " + (", ".join(c["function"]["name"] for c in calls) or "completion"), flush=True)
        if runtime.gap:
            report.update(reason="spec_gap", gap=runtime.gap)
            break
        if any(t["name"] == "check" and t["result"].get("passed") for t in tool_results):
            gate = runtime.check_callback()
            if gate["passed"]:
                report.update(passed=True, reason="gate_passed")
                break
        if not calls:
            gate = runtime.check_callback()
            save_json(log_dir / f"gate_{number:03d}.json", gate)
            if gate["passed"]:
                report.update(passed=True, reason="gate_passed")
                break
            messages.append({"role": "user", "content": "The deterministic gate failed. Continue fixing the saved artifacts:\n" +
                             json.dumps(gate, ensure_ascii=False)[:8000]})
        if number == response_limit:
            # Artifacts completed by the last permitted response still receive
            # their controller-owned gate; no extra model response is granted.
            gate = runtime.check_callback()
            save_json(log_dir / f"gate_{number:03d}.json", gate)
            if gate["passed"]:
                report.update(passed=True, reason="gate_passed")
                break
        worklog = runtime.work / "WORKLOG.md"
        reset_this_response = False
        if checkpoint_pending and worklog.is_file() and worklog.read_text() != previous_checkpoint:
            checkpoint = worklog.read_text()
            messages = [{"role": "system", "content": prefix},
                        {"role": "user", "content": task + f"\n\n{number}/{response_limit} responses already used; {response_limit-number} remain. " +
                         ("Complete artifacts and check now; avoid further intake. " if response_limit-number <= 8 else "Implement the next concrete action. ") +
                         "Continue from this disk checkpoint. Read only needed artifacts.\n" + checkpoint[:12000]}]
            resets += 1
            checkpoint_pending = False
            previous_checkpoint = checkpoint
            checkpoint_number = number
            reset_this_response = True
        if not reset_this_response and not checkpoint_pending and (number - checkpoint_number >= 8 or (response["usage"].get("input_tokens") or 0) > 32000):
            previous_checkpoint = worklog.read_text() if worklog.is_file() else ""
            messages.append({"role": "user", "content": "Checkpoint now: update /work/WORKLOG.md (at most 4000 characters) with the next concrete action FIRST, then completed work, current issues and relevant file paths. Continue from disk in a fresh context. Use file pointers; do not duplicate Specs or full ABI declarations. Preserve important findings."})
            checkpoint_pending = True
        if number == response_limit - 8:
            messages.append({"role": "user", "content": f"Only 8 model responses remain in this job (limit {response_limit}). Complete and save all required artifacts now, then call check and fix its errors. Avoid additional background reading or scope expansion. Exceeding the limit is a recorded failure."})
    report.update(usage=total_usage(usage), context_resets=resets)
    persist(final=True)
    save_json(log_dir / "result.json", report)
    return report

