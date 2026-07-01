from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.coder.generation import (
    GenerationLogger,
    _add_usage,
    _compact_compile_diagnostics,
    _compile_project,
    _extract_project_error_files,
    _messages_size_bytes,
    _strip_fences,
    _usage_to_dict,
    _validate_repair_candidate,
)
from agent.coder.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from agent.coder.protocol_behavior_val import verify_protocol_behavior

from .configs import ProtocolConfig, rel_to_repo
from .prompts import build_file_messages, build_nl_plan_messages, build_repair_messages, build_strategy_messages
from .requirements import build_allowed_inputs, write_json


METHOD_DIRECT = "direct-code-agent"
METHOD_NL = "nl-plan-code"
BASELINE_METHODS = (METHOD_DIRECT, METHOD_NL)
FORBIDDEN_LEAK_TERMS = (
    "PROTOCOL_MODULE_SPEC",
    "FILE_SPEC",
    "FUNCTION_SPEC",
    "dependency_graph",
    "wire/access binding",
    "gold_specs",
    "spec_bundle",
    "specs-example",
)
MAX_SOURCE_PROMPT_BYTES = 4 * 1024 * 1024
MAX_HEADER_CONTEXT_BYTES = 256 * 1024


@dataclass
class BaselineRunResult:
    success: bool
    summary_path: Path
    manifest_path: Path | None
    summary: dict[str, Any]


@dataclass
class StageTimer:
    timings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def run(self, name: str, fn):
        started_at = datetime.now().isoformat(timespec="seconds")
        start = time.perf_counter()
        try:
            return fn()
        finally:
            elapsed = time.perf_counter() - start
            self.timings[name] = {
                "started_at": started_at,
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_seconds": round(elapsed, 3),
            }


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_json_response(text: str) -> dict[str, Any]:
    stripped = _strip_fences(text).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def _safe_project_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("empty project path")
    candidate = Path(normalized)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"unsafe project path: {path}")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    allowed = {".c", ".h"}
    if normalized != "Makefile" and Path(normalized).suffix not in allowed:
        raise ValueError(f"unsupported project file extension: {path}")
    return normalized


def validate_project_strategy(strategy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    files = strategy.get("files")
    if not isinstance(files, list) or not files:
        return ["strategy.files must be a non-empty list"]
    paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"strategy.files[{index}] must be an object")
            continue
        raw_path = str(item.get("path", ""))
        try:
            path = _safe_project_path(raw_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path == "Makefile":
            continue
        if path in paths:
            errors.append(f"duplicate project file path: {path}")
        paths.append(path)
    if not any(path.endswith(".c") for path in paths):
        errors.append("strategy must include at least one C source file")
    if "main.c" not in paths:
        errors.append("strategy must include main.c")
    return errors


def _ordered_strategy_files(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    files = [item for item in strategy.get("files", []) if isinstance(item, dict)]
    normalized: list[dict[str, Any]] = []
    for item in files:
        path = _safe_project_path(str(item.get("path", "")))
        if path == "Makefile":
            continue
        normalized.append({**item, "path": path})
    return sorted(normalized, key=lambda item: (0 if item["path"].endswith(".h") else 2 if item["path"] == "main.c" else 1, item["path"]))


def _render_makefile(binary_name: str, source_paths: list[str]) -> str:
    unique_sources = []
    seen = set()
    for source in source_paths:
        if source not in seen:
            unique_sources.append(source)
            seen.add(source)
    body = " \\\n\t".join(unique_sources)
    return f"""CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror=implicit-function-declaration -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

SRCS = \\
\t{body}

TARGET ?= {binary_name}

all: $(TARGET)

$(TARGET): $(SRCS)
\t$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

clean:
\trm -f $(TARGET)

.PHONY: all clean
"""


def _count_scenarios(scenarios: list[dict[str, str]]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for item in scenarios:
        status = str(item.get("status", ""))
        if status in counts:
            counts[status] += 1
    return counts


class BaselineProjectRunner:
    method: str = METHOD_DIRECT

    def __init__(
        self,
        config: ProtocolConfig,
        output_dir: Path,
        *,
        llm_client: FixedQwenClient,
        max_repair_rounds: int = 3,
    ) -> None:
        self.config = config
        self.output_dir = output_dir
        self.coder_out = output_dir / "coder_out"
        self.project_dir = self.coder_out / config.protocol
        self.logs = GenerationLogger(self.coder_out / "_agent_logs")
        self.llm_client = llm_client
        self.max_repair_rounds = max_repair_rounds
        self.workflow_usage = LLMUsage(0, 0, 0)
        self.stage_token_usage: dict[str, dict[str, int]] = {}
        self.llm_call_usage: list[dict[str, Any]] = []
        self.repaired_files: list[str] = []
        self.timer = StageTimer()

    def run(self) -> BaselineRunResult:
        self._prepare_output_dir()
        summary = self._base_summary()
        manifest_path: Path | None = None
        try:
            self.llm_client.ensure_ready()
            allowed_inputs = self.timer.run("allowed_inputs", self._write_allowed_inputs)
            nl_plan = ""
            if self.method == METHOD_NL:
                nl_plan = self.timer.run("nl_plan", lambda: self._generate_nl_plan(allowed_inputs))
                summary["nl_plan_status"] = "passed"
            else:
                summary["nl_plan_status"] = "not_applicable"

            strategy = self.timer.run("strategy_generation", lambda: self._generate_strategy(allowed_inputs, nl_plan))
            summary["strategy_generation_status"] = "passed"
            self.timer.run("file_generation", lambda: self._generate_files(allowed_inputs, strategy, nl_plan))
            summary["file_generation_status"] = "passed"

            static_errors = self.timer.run("static_check", lambda: self._run_static_checks(strategy))
            if static_errors:
                summary["static_check_status"] = "failed"
                self._mark_failure(summary, "static_check", "; ".join(static_errors[:3]))
                manifest_path = self._write_manifest(summary, None, "static_check_failed", [], [])
                return self._finish(summary, manifest_path)
            summary["static_check_status"] = "passed"

            compile_result, repair_stop_reason, rounds, blocking_files, rejected_candidates = self.timer.run(
                "compile_repair",
                self._compile_and_repair,
            )
            summary["compile_status"] = "passed" if compile_result.returncode == 0 else "failed"
            summary["compile_returncode"] = compile_result.returncode
            summary["repair_iterations"] = rounds
            summary["repair_stop_reason"] = repair_stop_reason
            summary["repaired_files"] = self.repaired_files
            summary["blocking_files"] = blocking_files
            summary["rejected_candidates"] = rejected_candidates

            verification_success: bool | None = None
            scenarios: list[dict[str, str]] = []
            verification_error: str | None = None
            if compile_result.returncode == 0:
                verification_success, scenarios, verification_error = self.timer.run("smoke", self._run_behavior)
                summary["smoke_status"] = "passed" if verification_success else "failed"
                summary["verification_success"] = verification_success
                summary["scenario_counts"] = _count_scenarios(scenarios)
                summary["verification"] = {"scenarios": scenarios, "error": verification_error}
                if not verification_success:
                    self._mark_failure(summary, "smoke", verification_error or self._first_failed_scenario(scenarios))
            else:
                summary["smoke_status"] = "not_run"
                summary["verification_success"] = None
                summary["scenario_counts"] = {"passed": 0, "failed": 0, "skipped": 0}
                summary["verification"] = {"scenarios": [], "error": None}
                self._mark_failure(summary, "compile", repair_stop_reason)

            manifest_path = self._write_manifest(
                summary,
                compile_result,
                repair_stop_reason,
                blocking_files,
                rejected_candidates,
            )
            return self._finish(summary, manifest_path)
        except Exception as exc:  # noqa: BLE001
            self._mark_failure(summary, "runner", f"{type(exc).__name__}: {exc}")
            manifest_path = self._write_manifest(summary, None, "runner_error", [], [])
            return self._finish(summary, manifest_path)

    def _prepare_output_dir(self) -> None:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.logs = GenerationLogger(self.coder_out / "_agent_logs")

    def _runtime_contract(self) -> dict[str, Any]:
        return {
            "binary_name": self.config.binary_name,
            "argv_contract": self.config.argv_contract,
            "transport": self.config.transport,
        }

    def _base_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "planning_utility_baseline_summary/v1",
            "method": self.method,
            "protocol": self.config.protocol,
            "facts_path": rel_to_repo(self.config.facts_path),
            "target_profile_path": rel_to_repo(self.config.target_profile_path),
            "binary_name": self.config.binary_name,
            "argv_contract": self.config.argv_contract,
            "planning_status": "not_applicable",
            "strategy_generation_status": "not_run",
            "nl_plan_status": "not_run",
            "file_generation_status": "not_run",
            "static_check_status": "not_run",
            "compile_status": "not_run",
            "compile_returncode": None,
            "repair_iterations": None,
            "repair_stop_reason": "",
            "repaired_files": [],
            "blocking_files": [],
            "smoke_status": "not_run",
            "verification_success": None,
            "scenario_counts": {"passed": 0, "failed": 0, "skipped": 0},
            "failure_stage": "",
            "failure_categories": [],
            "main_diagnostic": "",
            "llm_call_count": 0,
            "llm_call_usage": [],
            "stage_token_usage": {},
            "workflow_token_usage": _usage_to_dict(LLMUsage(0, 0, 0)),
            "timings": {},
            "leakage_guard": {"status": "not_run", "findings": []},
            "output_dir": str(self.output_dir),
            "project_dir": str(self.project_dir),
            "log_dir": str(self.logs.root),
        }

    def _write_allowed_inputs(self) -> dict[str, Any]:
        allowed = build_allowed_inputs(self.config.facts_path, self.config.target_profile_path, self._runtime_contract())
        root = self.output_dir / "allowed_inputs"
        write_json(root / "facts_view.json", allowed["facts_view"])
        write_json(root / "target_profile.json", allowed["target_profile"])
        write_json(root / "minimum_requirements.json", allowed["minimum_requirements"])
        write_json(root / "runtime_contract.json", allowed["runtime_contract"])
        write_json(root / "input_hashes.json", allowed["input_hashes"])
        return allowed

    def _register_usage(self, stage: str, call_type: str, subject: str, usage: LLMUsage) -> None:
        self.workflow_usage = _add_usage(self.workflow_usage, usage)
        self.llm_call_usage.append(
            {
                "stage": stage,
                "call_type": call_type,
                "subject": subject,
                "usage": _usage_to_dict(usage),
            }
        )
        existing = self.stage_token_usage.get(stage, _usage_to_dict(LLMUsage(0, 0, 0)))
        self.stage_token_usage[stage] = _usage_to_dict(_add_usage(LLMUsage(**existing), usage))

    def _generate_with_usage(self, stage: str, call_type: str, subject: str, messages: list[dict[str, str]]) -> LLMResponse:
        self.logs.write(f"prompt_{stage}_{subject}", _json(messages))
        response = self.llm_client.generate_with_usage(
            LLMRequest(messages=messages, top_p=0.2, temperature=0.2, is_stream=True)
        )
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse, got {type(response)!r}")
        self._register_usage(stage, call_type, subject, response.usage)
        self.logs.write(f"response_{stage}_{subject}", response.content)
        return response

    def _generate_nl_plan(self, allowed_inputs: dict[str, Any]) -> str:
        messages = build_nl_plan_messages(protocol=self.config.protocol, allowed_inputs=allowed_inputs)
        response = self._generate_with_usage("nl_plan", "generate", self.config.protocol, messages)
        plan = _strip_fences(response.content)
        (self.output_dir / "nl_plan.md").write_text(plan, encoding="utf-8")
        return plan

    def _generate_strategy(self, allowed_inputs: dict[str, Any], nl_plan: str) -> dict[str, Any]:
        messages = build_strategy_messages(
            protocol=self.config.protocol,
            binary_name=self.config.binary_name,
            argv_contract=self.config.argv_contract,
            allowed_inputs=allowed_inputs,
            nl_plan=nl_plan,
        )
        response = self._generate_with_usage("strategy", "generate", self.config.protocol, messages)
        strategy = _parse_json_response(response.content)
        errors = validate_project_strategy(strategy)
        if errors:
            raise ValueError("; ".join(errors))
        write_json(self.output_dir / "project_strategy.json", strategy)
        return strategy

    def _generate_files(self, allowed_inputs: dict[str, Any], strategy: dict[str, Any], nl_plan: str) -> None:
        headers: dict[str, str] = {}
        for item in _ordered_strategy_files(strategy):
            path = item["path"]
            header_context = _json(headers)
            if len(header_context.encode("utf-8")) > MAX_HEADER_CONTEXT_BYTES:
                header_context = header_context.encode("utf-8")[:MAX_HEADER_CONTEXT_BYTES].decode("utf-8", errors="ignore")
            messages = build_file_messages(
                protocol=self.config.protocol,
                argv_contract=self.config.argv_contract,
                allowed_inputs=allowed_inputs,
                strategy=strategy,
                file_item=item,
                header_context=header_context,
                nl_plan=nl_plan,
            )
            if _messages_size_bytes(messages) > MAX_SOURCE_PROMPT_BYTES:
                raise RuntimeError(f"source generation prompt too large for {path}")
            response = self._generate_with_usage("file_generation", "generate", path, messages)
            content = _strip_fences(response.content)
            target = self.project_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if path.endswith(".h"):
                headers[path] = content
            self.logs.write(f"generated_file_{path}", content)
        sources = [item["path"] for item in _ordered_strategy_files(strategy) if item["path"].endswith(".c")]
        (self.project_dir / "Makefile").write_text(_render_makefile(self.config.binary_name, sources), encoding="utf-8")

    def _project_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        used = 0
        for path in sorted(self.project_dir.rglob("*.h")):
            relative = path.relative_to(self.project_dir).as_posix()
            content = path.read_text(encoding="utf-8", errors="ignore")
            encoded = content.encode("utf-8")
            if used + len(encoded) > MAX_HEADER_CONTEXT_BYTES:
                break
            headers[relative] = content
            used += len(encoded)
        return headers

    def _run_static_checks(self, strategy: dict[str, Any]) -> list[str]:
        errors = validate_project_strategy(strategy)
        expected = [item["path"] for item in _ordered_strategy_files(strategy)]
        for path in expected:
            if not (self.project_dir / path).is_file():
                errors.append(f"missing generated file: {path}")
        makefile = self.project_dir / "Makefile"
        if not makefile.is_file():
            errors.append("missing Makefile")
        else:
            text = makefile.read_text(encoding="utf-8", errors="ignore")
            if self.config.binary_name not in text:
                errors.append(f"Makefile does not mention target {self.config.binary_name}")
            dry = subprocess.run(
                ["make", "-n", self.config.binary_name],
                cwd=self.project_dir,
                text=True,
                capture_output=True,
                check=False,
            )
            self.logs.write("make_dry_run_stdout", dry.stdout)
            self.logs.write("make_dry_run_stderr", dry.stderr)
            if dry.returncode != 0:
                errors.append("make -n failed")
        leakage = self._leakage_findings()
        if leakage:
            errors.append(f"forbidden leakage terms found: {', '.join(item['term'] for item in leakage[:3])}")
        return errors

    def _leakage_findings(self) -> list[dict[str, str]]:
        roots = [self.output_dir / "allowed_inputs", self.project_dir, self.output_dir / "project_strategy.json"]
        if self.method == METHOD_NL:
            roots.append(self.output_dir / "nl_plan.md")
        findings: list[dict[str, str]] = []
        for root in roots:
            paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
            for path in paths:
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for term in FORBIDDEN_LEAK_TERMS:
                    if term in text:
                        findings.append({"path": str(path), "term": term})
        return findings

    def _compile_and_repair(self) -> tuple[subprocess.CompletedProcess[str], str, int, list[str], list[dict[str, str]]]:
        allowed_inputs = build_allowed_inputs(self.config.facts_path, self.config.target_profile_path, self._runtime_contract())
        last_result = _compile_project(self.project_dir, self.config.binary_name)
        self.logs.write("compile_stdout_0", last_result.stdout)
        self.logs.write("compile_stderr_0", last_result.stderr)
        if last_result.returncode == 0:
            return last_result, "compile_succeeded", 0, [], []

        rejected: list[dict[str, str]] = []
        for round_idx in range(1, self.max_repair_rounds + 1):
            error_files = _extract_project_error_files(last_result.stdout, last_result.stderr, self.project_dir)
            blocking_headers = [path for path in error_files if path.endswith(".h")]
            if blocking_headers:
                return last_result, "header_compile_error", round_idx - 1, blocking_headers, rejected
            repairable = [path for path in error_files if path.endswith(".c") and (self.project_dir / path).is_file()]
            if not repairable:
                return last_result, "no_repairable_sources", round_idx - 1, [], rejected
            changed = False
            for relative_path in repairable:
                current_path = self.project_dir / relative_path
                current_content = current_path.read_text(encoding="utf-8", errors="ignore")
                diagnostics = _compact_compile_diagnostics(
                    last_result.stdout,
                    last_result.stderr,
                    relative_path,
                    self.project_dir,
                )
                messages = build_repair_messages(
                    argv_contract=self.config.argv_contract,
                    allowed_inputs=allowed_inputs,
                    target_path=relative_path,
                    project_headers=self._project_headers(),
                    current_content=current_content,
                    compile_errors=diagnostics,
                )
                request_size = _messages_size_bytes(messages)
                if request_size > MAX_SOURCE_PROMPT_BYTES:
                    return last_result, "repair_request_too_large", round_idx - 1, [], rejected
                response = self._generate_with_usage("repair", f"repair_round_{round_idx}", relative_path, messages)
                candidate = _strip_fences(response.content)
                ok, reason = _validate_repair_candidate(relative_path, current_content, candidate)
                if not ok:
                    rejected.append({"path": relative_path, "reason": reason})
                    continue
                current_path.write_text(candidate, encoding="utf-8")
                self.logs.write(f"repaired_file_{round_idx}_{relative_path}", candidate)
                if relative_path not in self.repaired_files:
                    self.repaired_files.append(relative_path)
                changed = True
            if not changed:
                return last_result, "no_repair_progress", round_idx, [], rejected
            last_result = _compile_project(self.project_dir, self.config.binary_name)
            self.logs.write(f"compile_stdout_{round_idx}", last_result.stdout)
            self.logs.write(f"compile_stderr_{round_idx}", last_result.stderr)
            if last_result.returncode == 0:
                return last_result, "compile_succeeded", round_idx, [], rejected
        return last_result, "max_rounds_exhausted", self.max_repair_rounds, [], rejected

    def _run_behavior(self) -> tuple[bool, list[dict[str, str]], str | None]:
        ok, scenarios, error = verify_protocol_behavior(self.config.protocol, self.project_dir, self.config.binary_name)
        write_json(
            self.logs.root / "behavior_verification.json",
            {"success": ok, "scenarios": scenarios, "error": error},
        )
        return ok, scenarios, error

    def _write_manifest(
        self,
        summary: dict[str, Any],
        compile_result: subprocess.CompletedProcess[str] | None,
        repair_stop_reason: str,
        blocking_files: list[str],
        rejected_candidates: list[dict[str, str]],
    ) -> Path:
        leakage = self._leakage_findings()
        manifest = {
            "schema_version": "planning_utility_baseline_run_manifest/v1",
            "method": self.method,
            "model": "qwen3-max-2026-01-23",
            "llm_client": "agent.coder.llm_client.chat_with_llm",
            "output_dir": str(self.output_dir),
            "project_dir": str(self.project_dir),
            "log_dir": str(self.logs.root),
            "binary_name": self.config.binary_name,
            "argv_contract": self.config.argv_contract,
            "max_repair_rounds": self.max_repair_rounds,
            "generation_success": compile_result is not None and compile_result.returncode == 0,
            "compile_success": compile_result is not None and compile_result.returncode == 0,
            "compile_returncode": compile_result.returncode if compile_result is not None else None,
            "verification_run": summary.get("smoke_status") in {"passed", "failed"},
            "verification_success": summary.get("verification_success"),
            "verification": summary.get("verification", {"scenarios": [], "error": None}),
            "repaired_files": self.repaired_files,
            "repair": {
                "stop_reason": repair_stop_reason,
                "rounds_attempted": summary.get("repair_iterations") or 0,
                "blocking_files": blocking_files,
                "rejected_candidates": rejected_candidates,
            },
            "leakage_guard": {"status": "passed" if not leakage else "failed", "findings": leakage},
            "llm_call_usage": self.llm_call_usage,
            "stage_token_usage": self.stage_token_usage,
            "workflow_token_usage": _usage_to_dict(self.workflow_usage),
            "timings": self.timer.timings,
        }
        return self.logs.write_named("run_manifest.json", _json(manifest) + "\n")

    def _finish(self, summary: dict[str, Any], manifest_path: Path | None) -> BaselineRunResult:
        leakage = self._leakage_findings()
        summary["llm_call_count"] = len(self.llm_call_usage)
        summary["llm_call_usage"] = self.llm_call_usage
        summary["stage_token_usage"] = self.stage_token_usage
        summary["workflow_token_usage"] = _usage_to_dict(self.workflow_usage)
        summary["timings"] = self.timer.timings
        summary["leakage_guard"] = {"status": "passed" if not leakage else "failed", "findings": leakage}
        if manifest_path is not None:
            summary["manifest_path"] = rel_to_repo(manifest_path)
        summary_path = self.output_dir / "summary.json"
        write_json(summary_path, summary)
        return BaselineRunResult(
            success=not summary.get("failure_stage") and summary.get("compile_status") == "passed",
            summary_path=summary_path,
            manifest_path=manifest_path,
            summary=summary,
        )

    @staticmethod
    def _first_failed_scenario(scenarios: list[dict[str, str]]) -> str:
        for item in scenarios:
            if item.get("status") == "failed":
                return str(item.get("detail") or item.get("name") or "behavior verification failed")
        return "behavior verification failed"

    @staticmethod
    def _mark_failure(summary: dict[str, Any], stage: str, diagnostic: str) -> None:
        summary["failure_stage"] = stage
        summary["main_diagnostic"] = diagnostic
        categories: list[str] = []
        text = diagnostic.lower()
        if stage == "static_check":
            categories.append("baseline_static_check_failure")
        if stage == "compile":
            categories.append("source_compile_failure")
        if stage == "smoke":
            categories.append("smoke_behavior_failure")
        if "json" in text:
            categories.append("llm_json_error")
        if not categories:
            categories.append("runner_failure")
        summary["failure_categories"] = sorted(set(categories))


class DirectCodeAgentRunner(BaselineProjectRunner):
    method = METHOD_DIRECT


class NLPlanCodeRunner(BaselineProjectRunner):
    method = METHOD_NL
