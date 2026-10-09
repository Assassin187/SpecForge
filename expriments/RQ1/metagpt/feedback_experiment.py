#!/usr/bin/env python3
"""Run native MetaGPT QA on frozen generated protocol projects."""
from __future__ import annotations

import argparse
import ast
import asyncio
import concurrent.futures
import contextvars
import functools
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCES = [
    "mqtt_20261008T075400Z", "coap_20261008T093000Z",
    "http11_20261008T103809Z_continued", "smtp_20261008T103809Z_continued",
    "mqtt_20261008T145033Z", "coap_20261008T145033Z",
    "http11_20261008T145033Z", "smtp_20261008T145033Z",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


def limits():
    tree = ast.parse((ROOT / "specforge/pipeline.py").read_text())
    values = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "LIMITS" for target in node.targets))
    controller = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "do_code")
    rounds = {ast.literal_eval(node.comparators[0]) for node in ast.walk(controller)
              if isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript)
              and isinstance(node.left.slice, ast.Constant) and node.left.slice.value == "implementation_repairs"
              and isinstance(node.comparators[0], ast.Constant)}
    assert rounds == {3}, f"Recheck the current SpecForge repair limit: {rounds}"
    return {"implementation_repair_rounds": rounds.pop(), "responses_per_repair_round": values["repair"],
            "initial_qa_response_limit": values["code"], "sdk_max_retries": 2,
            "build_timeout_seconds": 120, "test_timeout_seconds": 180,
            "source": "specforge/pipeline.py", "source_sha256": hashlib.sha256(
                (ROOT / "specforge/pipeline.py").read_bytes()).hexdigest()}


def prepare(output, sources=SOURCES, round_number=None):
    output.mkdir(parents=True, exist_ok=False)
    settings = limits()
    save(output / "batch.json", {"created_at": now(), "sources": sources, "limits": settings,
                                "fresh_generation": False, "independent_acceptance_executed": False})
    reports = output / "reports"
    reports.mkdir()
    shutil.copyfile(__file__, reports / "feedback_entrypoint.py")
    subprocess.run(["git", "-C", str(BASE / "MetaGPT"), "diff", "--", "metagpt"],
                   stdout=(reports / "c99-adaptation.patch").open("w"), check=True)
    for index, name in enumerate(sources):
        source = BASE / "runs" / name
        original = json.loads((source / "run.json").read_text())
        project_name = original["native_settings"]["project_name"]
        native = Path(original["native_repo"])
        child = output / name
        child.mkdir()
        (child / "logs").mkdir()
        (child / "snapshots").mkdir()
        workspace = child / "workspace" / project_name
        workspace.mkdir(parents=True)
        # Only original generated sources, native design/task docs and raw inputs enter QA.
        shutil.copytree(source / "project", workspace / project_name)
        shutil.copytree(native / "docs", workspace / "docs")
        shutil.copyfile(native / ".dependencies.json", workspace / ".dependencies.json")
        shutil.copytree(source / "inputs", child / "inputs")
        expected = hashes(source / "project")
        assert expected == hashes(workspace / project_name), name
        shutil.copytree(source / "project", child / "before_project")
        save(child / "run.json", {
            "source_run": str(source), "source_hashes": expected,
            "round": round_number if round_number is not None else index // 4 + 1,
            "case": name.split("_")[0], "project_name": project_name, "model": original["model"],
            "limits": settings, "status": "prepared", "created_at": now(), "fresh_generation": False,
            "original_generation_usage": original["usage"], "original_generation_seconds": original.get(
                "generation_active_elapsed_seconds", original.get("generation_elapsed_seconds")),
            "repair_rounds": 0, "jobs": [], "requests": [], "executions": [],
            "isolation": {"independent_acceptance_visible": False, "prior_evaluation_visible": False,
                          "tests_have_isolated_network": True, "original_sources_writable": False},
        })
    return output


async def worker(output, resume=False):
    state = json.loads((output / "run.json").read_text())
    os.environ["METAGPT_PROJECT_ROOT"] = str(output)
    spec = importlib.util.spec_from_file_location("model_setup", BASE / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.MODEL.record() == state["model"]
    assert not (ROOT / "evaluation").exists()
    assert not (Path(state["source_run"]) / "evaluation").exists()
    assert not (ROOT / "runs/paper").exists()
    state["isolation"]["verified_at"] = now()
    runner.config.workspace.path = output / "workspace"
    runner.config.project_name = state["project_name"]
    from loguru import logger
    logger.remove()
    logger.add(output / "logs/framework.log", level="DEBUG", backtrace=False, diagnose=False)
    from openai._base_client import AsyncHttpxClientWrapper
    from metagpt.actions import DebugError, RunCode, WriteCode, WriteCodeReview, WriteTest
    from metagpt.actions.summarize_code import SummarizeCode
    from metagpt.context import Context
    from metagpt.roles import Engineer, QaEngineer
    from metagpt.schema import Message, RunCodeContext
    from metagpt.team import Team
    from metagpt.utils.git_repository import GitRepository
    from metagpt.utils.project_repo import ProjectRepo

    context = Context(config=runner.config)
    context.git_repo = GitRepository(output / "workspace" / state["project_name"])
    context.repo = ProjectRepo(context.git_repo)
    context.src_workspace = context.git_repo.workdir / state["project_name"]
    context.repo.with_src_path(context.src_workspace)
    context.kwargs.set("c99", True)
    context.kwargs.set("repair_round", 0)
    previous_elapsed = state.get("extra_elapsed_seconds", 0)
    if resume:
        # A route that failed before invoking a repair action did not perform a repair.
        completed_attempts = sum(j["action"] in {"WriteCode", "DebugError"} for j in state["jobs"])
        state.setdefault("continuations", []).append({"at": now(), "prior_status": state["status"],
            "prior_stop_reason": state.get("stop_reason"), "prior_dispatched_repairs": state["repair_rounds"],
            "actual_repair_attempts": completed_attempts,
            "reason": "Continue existing workspace and generated QA tests after correcting native feedback routing."})
        context.kwargs.set("repair_round", completed_attempts)
    # The original requirement remains byte-for-byte identical to its frozen input.
    requirement = context.git_repo.workdir / "docs/requirement.txt"
    assert requirement.read_bytes() == (output / "inputs/idea.txt").read_bytes()
    scope = contextvars.ContextVar("native_action", default=None)
    started_requests = {}

    def persist():
        entries = [r["usage"] for r in state["requests"] if r.get("usage") is not None]
        state["usage"] = {"requests": len(entries), "http_attempts": len(state["requests"])}
        for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            state["usage"][key] = sum(u[key] for u in entries) if entries and all(key in u for u in entries) else None
        state["usage"]["total_tokens"] = sum(u["total_tokens"] for u in entries) if entries else 0
        state["usage"]["reasoning_tokens"] = sum((u.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", 0) for u in entries)
        state["usage"]["unanswered_attempts"] = sum(r.get("usage") is None for r in state["requests"])
        state["repair_rounds"] = context.kwargs.get("repair_round")
        state["updated_at"] = now()
        save(output / "run.json", state)

    class ResponseLimit(BaseException):
        pass

    async def request_hook(request):
        repair_round = context.kwargs.get("repair_round")
        count = sum(r.get("status") == "received" and bool(r.get("usage"))
                    for r in state["requests"] if r["repair_round"] == repair_round)
        cap = state["limits"]["responses_per_repair_round" if repair_round else "initial_qa_response_limit"]
        if count >= cap:
            context.kwargs.set("stop_reason", "model_response_limit")
            raise ResponseLimit(f"Round {repair_round} exhausted {cap} model responses")
        number = len(state["requests"]) + 1
        request.extensions["feedback_number"] = number
        body = json.loads(await request.aread())
        assert body["model"] == state["model"]["model"]
        assert body["max_tokens"] == state["model"]["max_tokens"]
        assert body["reasoning_effort"] == state["model"]["reasoning_effort"]
        assert body["thinking"] == {"type": "enabled"} and not body.get("stream", False)
        path = f"logs/request_{number:03d}.json"
        save(output / path, body)
        state["requests"].append({"number": number, "job": scope.get(), "repair_round": repair_round,
                                  "status": "sent", "started_at": now(), "request": path,
                                  "sdk_retry_count": request.headers.get("x-stainless-retry-count")})
        started_requests[number] = time.monotonic()
        persist()

    async def response_hook(response):
        number = response.request.extensions["feedback_number"]
        data = await response.aread()
        (output / f"logs/response_{number:03d}.json").write_bytes(data)
        body = json.loads(data)
        record = state["requests"][number - 1]
        record.update(status="received", http_status=response.status_code, finished_at=now(),
                      elapsed_seconds=round(time.monotonic() - started_requests[number], 3),
                      usage=body.get("usage"), finish_reasons=[c.get("finish_reason") for c in body.get("choices", [])])
        persist()
        print(f"{state['case']} round {record['repair_round']}: {record['job']}; "
              f"{(record['usage'] or {}).get('total_tokens')} tokens", flush=True)

    original_kwargs = runner.SpecForgeLLM._make_client_kwargs

    def client_kwargs(self):
        kwargs = original_kwargs(self)
        kwargs["max_retries"] = state["limits"]["sdk_max_retries"]
        kwargs["http_client"] = AsyncHttpxClientWrapper(event_hooks={
            "request": [request_hook], "response": [response_hook]})
        return kwargs

    runner.SpecForgeLLM._make_client_kwargs = client_kwargs
    # Retain SDK retries shared with SpecForge without stacking MetaGPT connection retries.
    async def completion_text(self, messages, stream=False, timeout=0):
        response = await self._achat_completion(messages, timeout=timeout)
        return self.get_choice_text(response)
    runner.SpecForgeLLM.acompletion_text = completion_text

    for action in (WriteTest, RunCode, WriteCode, WriteCodeReview, DebugError):
        original = action.run

        @functools.wraps(original)
        async def observed(self, *args, _original=original, _name=action.__name__, **kwargs):
            number = len(state["jobs"]) + 1
            label = f"{number:03d}_{_name}"
            token = scope.set(label)
            started = time.monotonic()
            job = {"name": label, "action": _name, "repair_round": context.kwargs.get("repair_round"),
                   "started_at": now(), "status": "running"}
            state["jobs"].append(job)
            persist()
            try:
                result = await _original(self, *args, **kwargs)
                save(output / "logs" / f"{label}.json", result.model_dump(mode="json")
                     if hasattr(result, "model_dump") else result)
                job["status"] = "completed"
                return result
            except BaseException as error:
                job.update(status="failed", error=f"{type(error).__name__}: {error}")
                raise
            finally:
                job["elapsed_seconds"] = round(time.monotonic() - started, 3)
                if _name == "RunCode" and self.execution:
                    execution = {"job": label, "repair_round": job["repair_round"], **self.execution}
                    state["executions"].append(execution)
                    save(output / "logs" / f"{label}_execution.json", execution)
                if _name in {"WriteCode", "DebugError"}:
                    shutil.copytree(context.src_workspace, output / "snapshots" / label)
                scope.reset(token)
                persist()
        action.run = observed

    qa = QaEngineer(context=context, repair_round_allowed=state["limits"]["implementation_repair_rounds"])
    qa.repair_rounds = context.kwargs.get("repair_round")
    engineer = Engineer(context=context, use_code_review=True)
    team = Team(context=context)
    team.hire([qa, engineer])
    # SpecForge bounds responses rather than dollars; use the same limits above.
    team.invest(float("inf"))
    if resume and (context.git_repo.workdir / "tests/test_c99_project.py").is_file():
        run_context = RunCodeContext(mode="c99", code_filename="Makefile", test_filename="test_c99_project.py",
                                    command=["python3", "tests/test_c99_project.py"],
                                    working_directory=str(context.git_repo.workdir))
        team.env.publish_message(Message(content=run_context.model_dump_json(), cause_by=WriteTest,
                                         send_to="Edward", sent_from="Edward"))
    else:
        team.env.publish_message(Message(content="Continue QA on the existing generated C99 project.",
                                         cause_by=SummarizeCode, send_to="Edward", sent_from="Alex"))
    started = time.monotonic()
    state.update(status="running", started_at=now(), stop_reason=None)
    persist()
    try:
        # Native Team scheduling only; no generation/planning roles and no run_project(idea).
        native_result = await team.run(n_round=12, auto_archive=False)
        failed = [job for job in state["jobs"] if job["status"] == "failed"]
        state["stop_reason"] = context.kwargs.get("stop_reason") or (
            "native_framework_error" if failed or native_result is None else "native_team_idle"
            if team.env.is_idle else "native_scheduler_limit")
        state["status"] = "completed" if not failed and native_result is not None else "failed"
    except BaseException as error:
        state.update(status="failed", stop_reason=context.kwargs.get("stop_reason") or "exception",
                     error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
    finally:
        segment_elapsed = round(time.monotonic() - started, 3)
        state.setdefault("segments", []).append({"finished_at": now(), "elapsed_seconds": segment_elapsed,
                                                "resume": resume, "stop_reason": state["stop_reason"]})
        state["extra_elapsed_seconds"] = round(previous_elapsed + segment_elapsed, 3)
        state["repair_rounds"] = qa.repair_rounds
        executions = state["executions"]
        state["before"] = executions[0] if executions else None
        state["after"] = executions[-1] if executions else None
        state["passed"] = bool(executions and executions[-1]["exit_code"] == 0
                               and not executions[-1]["timed_out"])
        shutil.copytree(context.src_workspace, output / "project")
        state["after_project_hashes"] = hashes(output / "project")
        state["original_copy_unchanged"] = hashes(output / "before_project") == state["source_hashes"]
        persist()
    return 0 if state["status"] == "completed" else 1


def launch(child, resume=False):
    runtime = (BASE / ".venv/bin/python").resolve().parents[1]
    mounts = [Path("/usr"), Path("/lib"), Path("/lib64"), Path("/bin"),
              Path("/etc/alternatives"), BASE / ".venv", runtime, BASE / "MetaGPT", BASE / "config", BASE / "run.py",
              BASE / "feedback_experiment.py", ROOT / "specforge/__init__.py", ROOT / "specforge/llm.py"]
    command = ["bwrap", "--die-with-parent", "--unshare-pid"]
    for path in mounts:
        command += ["--ro-bind", str(path), str(path)]
    command += ["--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
                "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--bind", str(child), str(child), "--chdir", str(child), "--",
                str(BASE / ".venv/bin/python"), str(BASE / "feedback_experiment.py"),
                "--worker", "--out", str(child)]
    if resume:
        command.append("--resume")
    with (child / "logs/worker.log").open("a" if resume else "w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    return child.name, result.returncode


def summarize(output):
    sources = json.loads((output / "batch.json").read_text())["sources"]
    states = [json.loads((output / name / "run.json").read_text()) for name in sources]
    for state in states:
        state["original_source_unchanged"] = hashes(Path(state["source_run"]) / "project") == state["source_hashes"]
        save(output / Path(state["source_run"]).name / "run.json", state)
    summary = {"finished_at": now(), "projects": states,
               "total_requests": sum(s.get("usage", {}).get("requests", 0) for s in states),
               "total_tokens": sum(s.get("usage", {}).get("total_tokens", 0) for s in states),
               "total_project_elapsed_seconds": round(sum(s.get("extra_elapsed_seconds", 0) for s in states), 3),
               "passed_projects": sum(s.get("passed", False) for s in states)}
    save(output / "summary.json", summary)
    rows = ["# MetaGPT 原生 C99 反馈修复实验", "", f"复用 {len(states)} 个已冻结生成项目；反馈阶段未重新生成项目，未执行或访问独立验收。",
            "", "最多 3 轮修复，每轮最多 40 次模型响应；初始 QA 上限 180 次。模型与 SpecForge 相同。",
            "原生每次反馈选择一个文件修复，原生代码审查继续启用；未引入 SpecForge 修复控制器。",
            "构建 120 秒、自测 180 秒上限与 SpecForge 相同；各项目测试使用独立网络命名空间。",
            "", "| 轮次 | 协议 | 修复前构建 | 修复前自测 | 修复后构建 | 修复后自测 | 修复轮数 | 额外秒数 | 模型响应 | Token |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    def phase(execution, kind):
        if not execution:
            return "未执行"
        commands = execution["commands"]
        selected = [c for c in commands if c["phase"] == kind]
        return "未执行" if not selected else "通过" if selected[-1]["exit_code"] == 0 and not selected[-1]["timed_out"] else "失败"
    for s in states:
        rows.append(f"| {s['round']} | {s['case']} | {phase(s.get('before'), 'build')} | "
                    f"{phase(s.get('before'), 'native_tests')} | {phase(s.get('after'), 'build')} | "
                    f"{phase(s.get('after'), 'native_tests')} / QA {phase(s.get('after'), 'qa_tests')} | "
                    f"{s['repair_rounds']} | {s.get('extra_elapsed_seconds', 0)} | "
                    f"{s.get('usage', {}).get('requests', 0)} | {s.get('usage', {}).get('total_tokens', 0)} |")
    rows += ["", f"构建与自测全部通过：{summary['passed_projects']}/{len(states)}。额外模型响应 {summary['total_requests']} 次，"
             f"{summary['total_tokens']:,} Token；各项目额外耗时合计 {summary['total_project_elapsed_seconds']} 秒。",
             "", "这是 MetaGPT 自测结果，不能替代第三方独立协议验收。推理 Token 已包含在输出 Token 中。",
             "原始项目和 before_project 留存并逐字节核验；修复输出、每次原始请求/响应、执行退出码及完整日志均保留。"]
    (output / "RESULTS.md").write_text("\n".join(rows) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Continue saved QA tests and source workspace without regeneration.")
    parser.add_argument("--jobs", type=int, choices=(1, 2, 4), default=4)
    parser.add_argument("--sources", nargs="+", help="Names of frozen generation directories under runs/.")
    parser.add_argument("--round", type=int, choices=(1, 2, 3), dest="round_number")
    args = parser.parse_args()
    output = args.out.resolve()
    if args.worker:
        return asyncio.run(worker(output, resume=args.resume))
    if not args.resume:
        prepare(output, args.sources or SOURCES, args.round_number)
    sources = json.loads((output / "batch.json").read_text())["sources"]
    if args.prepare_only:
        print(f"Prepared {len(sources)} unchanged projects: {output}")
        return 0
    previous_batch_elapsed = 0
    if args.resume:
        previous_summary = json.loads((output / "summary.json").read_text())
        previous_batch_elapsed = previous_summary.get("batch_elapsed_seconds", 0)
        continuation_number = len(list((output / "reports").glob("summary_before_continuation_*.json"))) + 1
        save(output / "reports" / f"summary_before_continuation_{continuation_number:02d}.json", previous_summary)
    started = time.monotonic()
    children = []
    for name in sources:
        child = output / name
        state = json.loads((child / "run.json").read_text())
        if (not args.resume or state["status"] == "failed" or (
            not state.get("passed") and state["repair_rounds"] < state["limits"]["implementation_repair_rounds"]
        )):
            children.append(child)
    if args.resume:
        for child in children:
            archive = child / "logs" / f"before_continuation_{len(list(child.glob('logs/before_continuation_*'))) + 1:02d}"
            archive.mkdir()
            shutil.copyfile(child / "run.json", archive / "run.json")
            if (child / "project").exists():
                shutil.move(child / "project", archive / "project")
        reports = output / "reports"
        shutil.copyfile(__file__, reports / f"feedback_entrypoint_continued_{continuation_number:02d}.py")
        with (reports / f"c99-adaptation-continued_{continuation_number:02d}.patch").open("w") as patch:
            subprocess.run(["git", "-C", str(BASE / "MetaGPT"), "diff", "--", "metagpt"], stdout=patch, check=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name, code in pool.map(lambda child: launch(child, resume=args.resume), children):
            print(f"{name}: process exit {code}", flush=True)
    summary = summarize(output)
    summary["batch_elapsed_seconds"] = round(previous_batch_elapsed + time.monotonic() - started, 3)
    save(output / "summary.json", summary)
    print(f"Finished: {summary['passed_projects']}/{len(sources)} pass; {output / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
