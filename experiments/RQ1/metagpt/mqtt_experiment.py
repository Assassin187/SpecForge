#!/usr/bin/env python3
"""One protocol experiment: original MetaGPT generation with passive evidence logging."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
SPECFORGE = BASE.parents[2]
sys.path[:0] = [str(BASE / "MetaGPT"), str(SPECFORGE)]
from specforge.documents import digest, hashes, prepare, read_json, save_json
from specforge.llm import ModelConfig

MODEL = (ModelConfig.from_record(json.loads(os.environ["METAGPT_MODEL_CONFIG"]))
         if "METAGPT_MODEL_CONFIG" in os.environ else ModelConfig())
PRICING = ({"source": "https://developers.openai.com/api/docs/models/gpt-6-luna", "checked_date": "2026-10-12",
            "peak_usd_per_million": {"input_cache_hit": 0.01, "input_cache_miss": 0.1, "output": 0.5},
            "billing_note": "Official standard list-price reference only; gateway billing is unknown.",
            "budget_is_hard_billing_limit": False}
           if MODEL.model == "gpt-6-luna" else
           {"source": "https://api-docs.deepseek.com/quick_start/pricing/", "checked_date": "2026-10-08",
            "peak_usd_per_million": {"input_cache_hit": 0.006, "input_cache_miss": 0.3, "output": 1.2},
            "budget_is_hard_billing_limit": False})


def now():
    return datetime.now(timezone.utc).isoformat()


def summarize_usage(attempts):
    entries = [item for item in attempts if item.get("usage") is not None]
    result = {"requests": len(entries), "http_attempts": len(attempts)}
    for field in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_hit_tokens", "cache_miss_tokens"):
        values = [item["usage"].get(field) for item in entries]
        result[field] = sum(values) if values and all(value is not None for value in values) else None
    result["total_tokens"] = (
        result["input_tokens"] + result["output_tokens"] if entries
        and result["input_tokens"] is not None and result["output_tokens"] is not None else None
    )
    result["elapsed_seconds"] = round(sum(item.get("elapsed_seconds", 0) for item in attempts), 3)
    models = {item.get("model_config", MODEL.record())["model"] for item in attempts}
    if len(models) > 1:
        result["by_model"] = {
            model: summarize_usage([item for item in attempts if item.get("model_config", MODEL.record())["model"] == model])
            for model in sorted(models)
        }
    elif entries and result["input_tokens"] is not None and result["output_tokens"] is not None:
        rates = entries[0].get("pricing", PRICING)["peak_usd_per_million"]
        model = entries[0].get("model_config", MODEL.record())["model"]
        field = "estimated_usd_reference" if model == "gpt-6-luna" else "estimated_usd_upper_bound"
        result[field] = round(
            (result["input_tokens"] * rates["input_cache_miss"] + result["output_tokens"] * rates["output"]) / 1_000_000, 9
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("mqtt", "coap", "http11", "smtp"), default="mqtt")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true", help="Freeze and check input evidence without LLM calls.")
    parser.add_argument("--resume", action="store_true", help="Continue the existing native Team checkpoint; preserve prior evidence.")
    args = parser.parse_args()
    case = args.case
    case_root = SPECFORGE / "cases" / f"{case}_min"
    project_name = {"mqtt": "mqtt_broker", "coap": "coap_server", "http11": "http_server", "smtp": "smtp_server"}[case]
    protocol_name = {"mqtt": "mqtt-v3.1.1-os.pdf", "coap": "rfc7252.txt", "http11": "rfc9110-rfc9112.txt", "smtp": "rfc5321.txt"}[case]
    protocol_label = {"mqtt": "MQTT v3.1.1 standard PDF: complete pdftotext -layout text",
                      "coap": "CoAP RFC 7252: complete original UTF-8 text",
                      "http11": "HTTP/1.1 RFC 9110 and RFC 9112: complete original UTF-8 text",
                      "smtp": "SMTP RFC 5321: complete original UTF-8 text"}[case]
    protocol_end_label = {"mqtt": "MQTT v3.1.1 standard PDF", "coap": "CoAP RFC 7252",
                          "http11": "HTTP/1.1 RFC 9110 and RFC 9112", "smtp": "SMTP RFC 5321"}[case]
    run = args.out.resolve()
    if run.exists() and not args.resume:
        raise SystemExit("Use a new experiment output directory.")
    run.mkdir(parents=True, exist_ok=args.resume)
    for name in ("logs", "snapshots", "reports", "workspace"):
        (run / name).mkdir(exist_ok=args.resume)
    created = now()
    settings = {
        "investment": 3.0, "n_round": 5, "code_review": True, "run_tests": False,
        "implement": True, "project_name": project_name, "inc": False,
        "project_path": "", "reqa_file": "", "max_auto_summarize_code": 0,
        "recover_path": None,
    }
    state = {
        "schema_version": 1, "system": "metagpt", "case": case, "created_at": created,
        "status": "preparing", "fresh": True, "resumed": False, "manual_edits": False,
        "model": MODEL.record(), "native_settings": settings,
        "native_pipeline_completed": False, "generation_passed": False,
        "evaluation_started": False, "evaluation": None, "current_stage": "prepare",
        "jobs": [], "role_events": [], "http_requests": [], "stages": {}, "stop_reason": None,
        "spec_revision": None, "spec_repairs": None, "implementation_repairs": None,
        "not_applicable": {
            "facts_specs_and_spec_revision": "MetaGPT has no SpecForge Facts/Specs publication pipeline.",
            "spec_repairs_and_implementation_repairs": "SpecForge repair counters do not represent native MetaGPT code reviews.",
            "context_resets": "SpecForge checkpoints are not part of the original MetaGPT SOP.",
            "native_qa_execution": "Official generate_repo default run_tests=False; preserve this setting.",
        },
        "recording_policy": "Passive observation; original action results, exceptions, prompts and scheduling are unchanged.",
        "pricing": PRICING,
    }

    continuation = None
    completed_files = {}
    reusable_code = {}
    previous_elapsed = 0
    if args.resume:
        state = read_json(run / "run.json")
        settings = state["native_settings"]
        checkpoint = run / "workspace/storage/team/team.json"
        assert checkpoint.is_file(), "Native generation continuation requires the existing Team checkpoint."
        continuation = run / "reports" / f"generation_continuation_{len(list((run / 'reports').glob('generation_continuation_*'))) + 1:02d}"
        continuation.mkdir()
        shutil.copyfile(run / "run.json", continuation / "run_before_resume.json")
        shutil.copyfile(checkpoint, continuation / "team_before_resume.json")
        shutil.copyfile(__file__, continuation / "generation_entrypoint.py")
        for request in state["http_requests"]:
            request.setdefault("model_config", state["model"])
            request.setdefault("pricing", state["pricing"])
        if state["model"] != MODEL.record():
            state.setdefault("initial_model", state["model"])
            state.setdefault("model_transitions", []).append({
                "at": now(), "from": state["model"], "to": MODEL.record(),
                "before_evidence": str(continuation.relative_to(run)),
            })
            state.update(model=MODEL.record(), pricing=PRICING)
        completed_files = hashes(Path(state["native_project"]))
        if (run / "project").exists():
            shutil.move(run / "project", continuation / "project_before_resume")
        for job in state["jobs"]:
            if job["action"] == "WriteCode" and job["status"] == "completed" and job["filename"] not in completed_files:
                reusable_code[job["filename"]] = read_json(run / "logs" / job["name"] / "result.json")
        previous_elapsed = state.get("generation_elapsed_seconds", 0)
        state.setdefault("generation_continuations", []).append({
            "started_at": now(), "checkpoint": str(checkpoint.relative_to(run)),
            "before_evidence": str(continuation.relative_to(run)),
            "already_saved_files_skipped": list(completed_files),
            "completed_write_code_results_reused": list(reusable_code),
            "new_requirement_published": False,
        })
        state.update(resumed=True, native_pipeline_completed=False)

    def persist():
        state["updated_at"] = now()
        state["usage"] = summarize_usage(state["http_requests"])
        for job in state["jobs"]:
            job["usage"] = summarize_usage([item for item in state["http_requests"] if item.get("job") == job["name"]])
        save_json(run / "run.json", state)

    if not args.resume:
        persist()
        prepared = prepare(run, case_root / "TASK.md", case_root / "REQUIREMENTS.md", case_root / "spec" / protocol_name)
        state["input_hashes"] = prepared["inputs"]
        state["document_hashes"] = prepared["documents"]
        pages = [item["path"] for item in read_json(run / "documents/index.json")["chunks"]]
        protocol_text = ("\f".join((run / "documents" / filename).read_text() for filename in pages)
                         if case == "mqtt" else (run / "inputs/protocol.txt").read_text())
        (run / "documents/protocol_full.txt").write_text(protocol_text)
        state["document_hashes"]["protocol_full.txt"] = digest(run / "documents/protocol_full.txt")
        idea = (
            "[BEGIN TASK.md]\n" + (run / "inputs/TASK.md").read_text() + "\n[END TASK.md]\n\n"
            "[BEGIN REQUIREMENTS.md]\n" + (run / "inputs/REQUIREMENTS.md").read_text() + "\n[END REQUIREMENTS.md]\n\n"
            f"[BEGIN {protocol_label}]\n" + protocol_text
            + f"\n[END {protocol_end_label}]\n"
        )
        (run / "inputs/idea.txt").write_text(idea)
        state["idea"] = {"path": "inputs/idea.txt", "sha256": digest(run / "inputs/idea.txt"),
                         "bytes": len(idea.encode()), "protocol_pages": len(pages) if case == "mqtt" else None,
                         "protocol_chunks": len(pages), "truncated": False,
                         "assembly": "Filename delimiters plus unchanged TASK, REQUIREMENTS and complete protocol text."}
        references = {}
        for ref in sorted((SPECFORGE / "runs/paper").glob(f"round_*/{case}_01/run.json")):
            spec_state = read_json(ref)
            assert spec_state["input_hashes"] == prepared["inputs"], str(ref)
            assert all(digest(ref.parent / "documents" / name) == digest(run / "documents" / name) for name in pages), str(ref)
            references[str(ref.relative_to(SPECFORGE))] = {
                "raw_input_hashes_equal": True, "all_document_chunk_hashes_equal": True,
                "document_chunks": len(pages), "model_config_equal": spec_state["model"] == state["model"],
            }
        assert references, f"No {case.upper()} SpecForge frozen input evidence found."
        state["input_equivalence"] = references
        checkout = BASE / "MetaGPT"
        source_files = sorted((checkout / "metagpt").rglob("*.py"))
        source_files += sorted((checkout / "config").glob("*.yaml"))
        source_files += [checkout / "setup.py", checkout / "requirements.txt"]
        state["framework_hashes"] = {str(path.relative_to(checkout)): digest(path) for path in source_files}
        state["adapter_hashes"] = {name: digest(BASE / name) for name in (
            "run.py", "mqtt_experiment.py", "config/config2.yaml", "requirements.lock", "upstream-requirements.patch"
        )}
        shutil.copyfile(Path(__file__), run / "reports/generation_entrypoint.py")
        state["generation_entrypoint"] = "reports/generation_entrypoint.py"
        state["specforge_recording_source_hashes"] = {name: digest(SPECFORGE / name) for name in (
            "specforge/llm.py", "specforge/agent.py", "specforge/documents.py", "specforge/pipeline.py", "specforge/tools.py",
            "specforge/evaluation.py", f"evaluation/{case}_check.py"
        )}
        state["upstream_commit"] = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        state["upstream_diff"] = subprocess.check_output(["git", "-C", str(checkout), "diff"], text=True)
        state["specforge_commit"] = subprocess.check_output(["git", "-C", str(SPECFORGE), "rev-parse", "HEAD"], text=True).strip()
        state["versions"] = {name: importlib.metadata.version(name) for name in ("metagpt", "openai", "jsonschema", "pydantic", "httpx")}
        state["versions"]["python"] = sys.version
        tool_names = ("gcc", "make", "pdftotext", "bwrap") + (
            ("mosquitto_pub", "mosquitto_sub") if case == "mqtt" else ("coap-client-notls",) if case == "coap" else ()
        )
        for name in tool_names:
            flag = "-v" if name == "pdftotext" else "--help" if name.startswith("mosquitto_") else "--version"
            command = [name] if name == "coap-client-notls" else [name, flag]
            version = subprocess.run(command, text=True, capture_output=True, timeout=10)
            state["versions"][name] = {"path": shutil.which(name), "version": (version.stdout + version.stderr).splitlines()[0]}
        state["stages"]["prepare"] = {"status": "completed", "finished_at": now(), "result": prepared}
        state["status"] = "prepared"
        persist()
        print(f"INPUTS VERIFIED: {case.upper()} complete protocol; {len(pages)} document chunks; "
              f"{len(references)} SpecForge inputs match; model {MODEL.model}, effort {MODEL.reasoning_effort}.", flush=True)
        if args.prepare_only:
            state["stop_reason"] = "prepare_only_zero_llm_requests"
            persist()
            return 0

    idea = (run / "inputs/idea.txt").read_text()

    spec = importlib.util.spec_from_file_location("metagpt_model_setup", BASE / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.config.workspace.path = run / "workspace"
    if args.resume:
        runner.config.update_via_cli(settings["project_path"], settings["project_name"], settings["inc"],
                                     settings["reqa_file"], settings["max_auto_summarize_code"])
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING", backtrace=False, diagnose=False)
    logger.add(run / "logs/framework.log", level="DEBUG", backtrace=False, diagnose=False)
    from openai._base_client import AsyncHttpxClientWrapper
    from metagpt.actions import WriteCode, WriteCodeReview, WriteDesign, WritePRD, WriteTasks
    from metagpt.actions.action_output import ActionOutput
    from metagpt.actions.prepare_documents import PrepareDocuments
    from metagpt.actions.summarize_code import SummarizeCode
    from metagpt.roles.role import Role
    from metagpt.roles.engineer import Engineer
    from metagpt.schema import CodingContext
    from metagpt.context import Context
    from metagpt.team import Team
    from metagpt.software_company import generate_repo
    from metagpt.utils.file_repository import FileRepository
    from pydantic import BaseModel

    role_scope = contextvars.ContextVar("rq1_role", default=None)
    action_scope = contextvars.ContextVar("rq1_action", default=None)
    workspace = run / "workspace" / project_name
    request_started = {}

    async def request_hook(request):
        number = len(state["http_requests"]) + 1
        request.extensions["rq1_request_number"] = number
        record = {"number": number, "started_at": now(), "status": "sent", "role": role_scope.get(),
                  "job": action_scope.get(), "sdk_retry_count": request.headers.get("x-stainless-retry-count"),
                  "path": str(request.url), "request": f"logs/llm/request_{number:04d}.json",
                  "model_config": MODEL.record(), "pricing": PRICING}
        state["http_requests"].append(record)
        request_started[number] = time.monotonic()
        path = run / record["request"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(await request.aread())
        persist()

    async def response_hook(response):
        number = response.request.extensions["rq1_request_number"]
        record = state["http_requests"][number - 1]
        data = await response.aread()
        path = run / f"logs/llm/response_{number:04d}.json"
        path.write_bytes(data)
        try:
            body = json.loads(data)
        except json.JSONDecodeError:
            body = {}
            record["non_json_response"] = True
        record.update(status="received", status_code=response.status_code, finished_at=now(),
                      elapsed_seconds=round(time.monotonic() - request_started[number], 3),
                      response=str(path.relative_to(run)), response_model=body.get("model"),
                      api_error=body.get("error"), request_id=response.headers.get("x-request-id"))
        raw_usage = body.get("usage")
        if raw_usage is not None:
            details = raw_usage.get("completion_tokens_details") or {}
            cache_hit = raw_usage.get("prompt_cache_hit_tokens")
            if cache_hit is None:
                cache_hit = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            cache_miss = raw_usage.get("prompt_cache_miss_tokens")
            if cache_miss is None and cache_hit is not None and raw_usage.get("prompt_tokens") is not None:
                cache_miss = raw_usage["prompt_tokens"] - cache_hit
            record["usage"] = {"input_tokens": raw_usage.get("prompt_tokens"), "output_tokens": raw_usage.get("completion_tokens"),
                               "reasoning_tokens": details.get("reasoning_tokens"), "cache_hit_tokens": cache_hit,
                               "cache_miss_tokens": cache_miss, "raw": raw_usage}
        record["finish_reasons"] = [choice.get("finish_reason") for choice in body.get("choices", [])]
        save_json(run / f"logs/llm/metadata_{number:04d}.json", record)
        persist()
        print(f"LLM {number}: {record['role']} / {record['job']}; {record.get('usage', {}).get('input_tokens')} in, "
              f"{record.get('usage', {}).get('output_tokens')} out; {record['elapsed_seconds']}s", flush=True)

    original_client_kwargs = runner.SpecForgeLLM._make_client_kwargs

    def observed_client_kwargs(self):
        kwargs = original_client_kwargs(self)
        assert "http_client" not in kwargs, "The shared SpecForge model configuration has no proxy."
        kwargs["http_client"] = AsyncHttpxClientWrapper(event_hooks={"request": [request_hook], "response": [response_hook]})
        return kwargs

    runner.SpecForgeLLM._make_client_kwargs = observed_client_kwargs

    if args.resume:
        original_new_code_actions = Engineer._new_code_actions

        async def remaining_code_actions(self):
            await original_new_code_actions(self)
            self.code_todos = [todo for todo in self.code_todos if todo.i_context.filename not in completed_files]
            self.set_todo(self.code_todos[0] if self.code_todos else None)

        Engineer._new_code_actions = remaining_code_actions

    def serialize_output(result):
        if isinstance(result, ActionOutput):
            return {"content": result.content, "instruct_content": result.instruct_content.model_dump(mode="json")}
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        return result

    def observe_action(action_type):
        original = action_type.run

        @functools.wraps(original)
        async def observed(self, *args, **kwargs):
            name = f"{len(state['jobs']) + 1:03d}_{action_type.__name__}"
            context = self.i_context
            filename = context.filename if hasattr(context, "filename") else None
            job = {"name": name, "action": action_type.__name__, "role": role_scope.get(), "filename": filename,
                   "status": "running", "started_at": now(), "usage": {"requests": 0}}
            state["jobs"].append(job)
            token = action_scope.set(name)
            started = time.monotonic()
            state["current_stage"] = action_type.__name__
            persist()
            print(f"ACTION START: {name}" + (f" ({filename})" if filename else ""), flush=True)
            try:
                if action_type is WriteCode and filename in reusable_code:
                    result = CodingContext(**reusable_code.pop(filename))
                    job["reused_completed_native_result"] = True
                else:
                    result = await original(self, *args, **kwargs)
                save_json(run / "logs" / name / "result.json", serialize_output(result))
                job["status"] = "completed"
                return result
            except BaseException as error:
                job.update(status="failed", error=f"{type(error).__name__}: {error}")
                save_json(run / "logs" / name / "error.json", {"error": job["error"], "traceback": traceback.format_exc()})
                raise
            finally:
                job.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
                job["working_hashes"] = {
                    str(path.relative_to(workspace)): digest(path) for path in workspace.rglob("*")
                    if path.is_file() and ".git" not in path.relative_to(workspace).parts
                }
                state["stages"][name] = {"status": job["status"], "started_at": job["started_at"],
                                         "finished_at": job["finished_at"], "elapsed_seconds": job["elapsed_seconds"],
                                         "artifact_hashes": job["working_hashes"]}
                action_scope.reset(token)
                persist()
                print(f"ACTION END: {name}: {job['status']}", flush=True)

        action_type.run = observed

    for action_type in (PrepareDocuments, WritePRD, WriteDesign, WriteTasks, WriteCode, WriteCodeReview, SummarizeCode):
        observe_action(action_type)

    original_role_run = Role.run

    @functools.wraps(original_role_run)
    async def observed_role_run(self, *args, **kwargs):
        label = f"{type(self).__name__}:{self.name}"
        token = role_scope.set(label)
        started = time.monotonic()
        event = {"role": label, "started_at": now(), "status": "running"}
        state["role_events"].append(event)
        try:
            result = await original_role_run(self, *args, **kwargs)
            event["status"] = "completed" if result is not None else "idle"
            if result is not None:
                snapshot = run / "snapshots" / f"{len(state['role_events']):03d}_{type(self).__name__}"
                shutil.copytree(workspace, snapshot, ignore=shutil.ignore_patterns(".git", "__pycache__"))
                event["snapshot"] = str(snapshot.relative_to(run))
                save_json(snapshot / "role_message.json", serialize_output(result))
            return result
        except BaseException as error:
            event.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            event.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
            role_scope.reset(token)
            persist()

    Role.run = observed_role_run

    def observe_file_operation(operation):
        original = getattr(FileRepository, operation)

        @functools.wraps(original)
        async def observed(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            filename = kwargs.get("filename", args[0] if args else None)
            event = {"at": now(), "operation": operation, "role": role_scope.get(), "job": action_scope.get(),
                     "root": str(self.root_path), "filename": str(filename) if filename is not None else None}
            if result is not None:
                event["result"] = serialize_output(result)
            with (run / "logs/file_operations.jsonl").open("a") as log:
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
            return result

        setattr(FileRepository, operation, observed)

    for operation in ("get", "save", "delete"):
        observe_file_operation(operation)

    state.update(status="running", current_stage="native_software_company")
    if not args.resume:
        state["started_at"] = now()
    persist()
    started = time.monotonic()
    try:
        if args.resume:
            context = Context(config=runner.config)
            checkpoint = run / "workspace/storage/team"
            saved_context = read_json(checkpoint / "team.json")["context"]
            # Upstream Context.deserialize validates this field but does not
            # assign the returned manager. Preserve the cumulative budget.
            context.cost_manager = type(context.cost_manager).model_validate_json(saved_context["cost_manager"])
            context.cost_manager.token_costs[MODEL.model] = runner.TOKEN_COSTS[MODEL.model]
            company = Team.deserialize(stg_path=checkpoint, context=context)
            company.env.add_roles(company.env.roles.values())
            for role in company.env.roles.values():
                if role.latest_observed_msg:
                    role.put_message(role.latest_observed_msg)
            assert not company.env.is_idle, "Recovered native pending messages must enter the scheduler."
            native_result = asyncio.run(company.run(n_round=settings["n_round"]))
            repo = context.repo
            state["native_pipeline_completed"] = native_result is not None
        else:
            repo = generate_repo(idea, **settings)
            state["native_pipeline_completed"] = True
        state["native_repo"] = str(repo.workdir)
        source = repo.workdir / repo.src_relative_path
        state["native_project"] = str(source)
        shutil.copytree(source, run / "project")
        state["project_hashes"] = hashes(run / "project")
        state["native_action_counts"] = dict(Counter(job["action"] for job in state["jobs"]))
        state.update(status="generated" if state["native_pipeline_completed"] else "generation_failed",
                     stop_reason="native_checkpoint_resumed" if args.resume and state["native_pipeline_completed"]
                     else "native_framework_exception" if args.resume else "native_generate_repo_returned")
    except BaseException as error:
        state.update(status="generation_failed", stop_reason="native_exception", error=f"{type(error).__name__}: {error}")
        save_json(run / "reports/generation_error.json", {"error": state["error"], "traceback": traceback.format_exc()})
        print(f"GENERATION FAILED: {type(error).__name__}; see reports/generation_error.json", flush=True)
    finally:
        segment_elapsed = round(time.monotonic() - started, 3)
        state.update(generation_elapsed_seconds=round(previous_elapsed + segment_elapsed, 3), generation_finished_at=now())
        if args.resume:
            state["generation_continuations"][-1].update(finished_at=now(), elapsed_seconds=segment_elapsed,
                                                        status=state["status"], usage_after=state["usage"])
        persist()
    print(f"GENERATION FINISHED: {state['status']}; {state['usage']}", flush=True)
    return 0 if state["status"] == "generated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
