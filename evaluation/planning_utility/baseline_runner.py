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
from .header_context import HeaderExtraction, extract_header_declarations, fit_header_context
from .prompts import (
    build_nl_plan_messages,
    build_pair_completion_messages,
    build_repair_messages,
    build_source_tree_skeleton_messages,
)
from .requirements import build_allowed_inputs, write_json


METHOD_FS_DIRECT = "fs-direct-coder"
METHOD_NL = "nl-plan-code"
BASELINE_METHODS = (METHOD_FS_DIRECT, METHOD_NL)
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
SKELETON_ALLOWED_KEYS = {"path", "kind", "order"}
SKELETON_ALLOWED_KINDS = {"header", "source", "main"}
PLANNING_ARTIFACT_NAMES = {
    "project_strategy.json",
    "nl_plan.md",
    "module_inventory.json",
    "function_inventory.json",
    "interface_sketch.json",
    "dependency_graph.json",
}
PLANNING_ARTIFACT_PREFIXES = ("type_ownership", "behavior_contract")
MAX_SOURCE_PROMPT_BYTES = 4 * 1024 * 1024
MAX_HEADER_CONTEXT_BYTES = 256 * 1024
JSON_MAX_COMPLETION_TOKENS = 16384
JSON_MAX_RETRIES = 1


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
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object")
    return parsed


def _json_retry_messages(
    messages: list[dict[str, str]],
    reason: str,
    attempt: int,
) -> list[dict[str, str]]:
    return [
        *messages,
        {
            "role": "user",
            "content": (
                f"JSON retry attempt {attempt}. Regenerate the complete requested JSON object from scratch. "
                "The previous response was rejected by deterministic validation. The first character must be '{' "
                "and the last character must be '}'. Return strict standard JSON only, with double-quoted keys and "
                "strings, correctly escaped source content, and no markdown, prose, analysis, comments outside source "
                f"content, or trailing commas. Rejection reason: {reason}"
            ),
        },
    ]


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


def validate_source_tree_skeleton(skeleton: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    extra_top = set(skeleton) - {"files"}
    if extra_top:
        errors.append(f"skeleton top-level keys must only be files, got: {', '.join(sorted(extra_top))}")
    files = skeleton.get("files")
    if not isinstance(files, list) or not files:
        return errors + ["skeleton.files must be a non-empty list"]
    paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"skeleton.files[{index}] must be an object")
            continue
        extra_keys = set(item) - SKELETON_ALLOWED_KEYS
        if extra_keys:
            errors.append(f"skeleton.files[{index}] has forbidden keys: {', '.join(sorted(extra_keys))}")
        try:
            path = _safe_project_path(str(item.get("path", "")))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path == "Makefile":
            errors.append("skeleton must not include Makefile")
            continue
        kind = str(item.get("kind", ""))
        if kind not in SKELETON_ALLOWED_KINDS:
            errors.append(f"unsupported skeleton kind for {path}: {kind}")
        if not isinstance(item.get("order"), int):
            errors.append(f"skeleton order for {path} must be an integer")
        if path in paths:
            errors.append(f"duplicate skeleton file path: {path}")
        paths.append(path)
        if path == "main.c" and kind != "main":
            errors.append("main.c must use kind=main")
        elif path.endswith(".h") and kind != "header":
            errors.append(f"header path must use kind=header: {path}")
        elif path.endswith(".c") and path != "main.c" and kind != "source":
            errors.append(f"source path must use kind=source: {path}")
    groups: dict[str, set[str]] = {}
    for path in paths:
        if path == "main.c":
            continue
        suffix = Path(path).suffix
        stem = path[: -len(suffix)]
        groups.setdefault(stem, set()).add(suffix)
    for stem, suffixes in sorted(groups.items()):
        if suffixes != {".h", ".c"}:
            errors.append(f"skeleton requires a complete h/c pair for {stem}")
    if "main.c" not in paths:
        errors.append("skeleton must include main.c")
    return errors


def _ordered_skeleton_files(skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    files = [item for item in skeleton.get("files", []) if isinstance(item, dict)]
    normalized: list[dict[str, Any]] = []
    for item in files:
        path = _safe_project_path(str(item.get("path", "")))
        normalized.append({"path": path, "kind": item.get("kind"), "order": item.get("order")})
    return sorted(normalized, key=lambda item: (int(item["order"]), item["path"]))


def _skeleton_generation_units(skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    files = _ordered_skeleton_files(skeleton)
    by_path = {item["path"]: item for item in files}
    units: list[dict[str, Any]] = []
    stems = sorted(
        {
            item["path"][: -len(Path(item["path"]).suffix)]
            for item in files
            if item["path"] != "main.c"
        },
        key=lambda stem: min(by_path[f"{stem}{suffix}"]["order"] for suffix in (".h", ".c") if f"{stem}{suffix}" in by_path),
    )
    for stem in stems:
        units.append({"kind": "pair", "paths": [f"{stem}.h", f"{stem}.c"]})
    if "main.c" in by_path:
        units.append({"kind": "main", "paths": ["main.c"]})
    return units


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
    method: str = METHOD_NL

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
        self.generated_pairs: list[dict[str, Any]] = []
        self.generated_files: list[str] = []
        self.expected_files: list[str] = []
        self.current_skeleton: dict[str, Any] | None = None
        self.header_context_cache: dict[str, HeaderExtraction] = {}
        self.header_context_diagnostics: list[dict[str, str]] = []
        self.json_retry_count = 0
        self.json_generation_attempts: list[dict[str, Any]] = []
        self.failed_generation_unit: dict[str, Any] | None = None
        self.timer = StageTimer()

    def run(self) -> BaselineRunResult:
        self._prepare_output_dir()
        summary = self._base_summary()
        manifest_path: Path | None = None
        try:
            self.llm_client.ensure_ready()
            allowed_inputs = self.timer.run("allowed_inputs", self._write_allowed_inputs)
            self._run_generation(allowed_inputs, summary)

            static_errors = self.timer.run("static_check", self._run_static_checks)
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
            if not summary.get("failure_stage"):
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
            "source_tree_skeleton_status": "not_run",
            "pair_completion_status": "not_run",
            "generated_pairs": [],
            "generated_files": [],
            "header_context_diagnostics": [],
            "json_retry_count": 0,
            "json_generation_attempts": [],
            "failed_generation_unit": None,
            "planning_artifact_guard": {"status": "not_run", "findings": []},
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

    def _generate_with_usage(
        self,
        stage: str,
        call_type: str,
        subject: str,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int | None = None,
    ) -> LLMResponse:
        self.logs.write(f"prompt_{stage}_{subject}", _json(messages))
        response = self.llm_client.generate_with_usage(
            LLMRequest(
                messages=messages,
                top_p=0.2,
                temperature=0.2,
                is_stream=True,
                max_completion_tokens=max_completion_tokens,
            )
        )
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse, got {type(response)!r}")
        self._register_usage(stage, call_type, subject, response.usage)
        self.logs.write(f"response_{stage}_{subject}", response.content)
        return response

    def _generate_validated_json(
        self,
        *,
        stage: str,
        subject: str,
        messages: list[dict[str, str]],
        validator,
        unit_paths: list[str],
    ) -> Any:
        rejection_reasons: list[str] = []
        for attempt in range(1, JSON_MAX_RETRIES + 2):
            attempt_messages = messages
            call_type = "generate"
            if attempt > 1:
                self.json_retry_count += 1
                call_type = "json_retry"
                attempt_messages = _json_retry_messages(messages, rejection_reasons[-1], attempt - 1)
            response = self._generate_with_usage(
                stage,
                call_type,
                subject,
                attempt_messages,
                max_completion_tokens=JSON_MAX_COMPLETION_TOKENS,
            )
            try:
                validated = validator(response.content)
            except ValueError as exc:
                reason = " ".join(str(exc).split())[:500]
                rejection_reasons.append(reason)
                if attempt <= JSON_MAX_RETRIES:
                    continue
                record = {
                    "stage": stage,
                    "subject": subject,
                    "attempt_count": attempt,
                    "retry_count": attempt - 1,
                    "status": "failed",
                    "rejection_reasons": rejection_reasons,
                }
                self.json_generation_attempts.append(record)
                self.failed_generation_unit = {
                    "stage": stage,
                    "subject": subject,
                    "paths": list(unit_paths),
                }
                raise ValueError(
                    f"{stage} JSON validation failed after {attempt} attempts for {subject}: {reason}"
                ) from exc
            self.json_generation_attempts.append(
                {
                    "stage": stage,
                    "subject": subject,
                    "attempt_count": attempt,
                    "retry_count": attempt - 1,
                    "status": "passed",
                    "rejection_reasons": rejection_reasons,
                }
            )
            return validated
        raise AssertionError("unreachable JSON generation loop")

    def _generate_nl_plan(self, allowed_inputs: dict[str, Any]) -> str:
        messages = build_nl_plan_messages(protocol=self.config.protocol, allowed_inputs=allowed_inputs)
        response = self._generate_with_usage("nl_plan", "generate", self.config.protocol, messages)
        plan = _strip_fences(response.content)
        (self.output_dir / "nl_plan.md").write_text(plan, encoding="utf-8")
        return plan

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

    def _project_header_declarations(self) -> dict[str, str]:
        extractions: list[tuple[str, HeaderExtraction]] = []
        for path in sorted(self.project_dir.rglob("*.h")):
            relative = path.relative_to(self.project_dir).as_posix()
            if relative not in self.header_context_cache:
                self.header_context_cache[relative] = extract_header_declarations(path, self.project_dir)
            extractions.append((relative, self.header_context_cache[relative]))
        declarations, diagnostics = fit_header_context(extractions, MAX_HEADER_CONTEXT_BYTES)
        self.header_context_diagnostics = diagnostics
        return declarations

    def _run_static_checks(self) -> list[str]:
        errors: list[str] = []
        if self.current_skeleton is not None:
            errors.extend(validate_source_tree_skeleton(self.current_skeleton))
        expected = self.expected_files
        if not expected:
            errors.append("no expected generated files recorded")
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
        if self.method in BASELINE_METHODS:
            planning_artifacts = self._planning_artifact_findings()
            if planning_artifacts:
                errors.append(
                    "planning artifacts found: "
                    + ", ".join(item["path"] for item in planning_artifacts[:3])
                )
        return errors

    def _leakage_findings(self) -> list[dict[str, str]]:
        roots = [
            self.output_dir / "allowed_inputs",
            self.project_dir,
            self.output_dir / "source_tree_skeleton.json",
        ]
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

    def _planning_artifact_findings(self) -> list[dict[str, str]]:
        if self.method not in BASELINE_METHODS or not self.output_dir.exists():
            return []
        findings: list[dict[str, str]] = []
        for path in sorted(self.output_dir.rglob("*")):
            if not path.is_file():
                continue
            name = path.name
            if self.method == METHOD_NL and name == "nl_plan.md":
                continue
            if name in PLANNING_ARTIFACT_NAMES or any(name.startswith(prefix) for prefix in PLANNING_ARTIFACT_PREFIXES):
                findings.append({"path": str(path.relative_to(self.output_dir)), "kind": "planning_artifact"})
        return findings

    def _planning_artifact_guard(self) -> dict[str, Any]:
        findings = self._planning_artifact_findings()
        return {"status": "passed" if not findings else "failed", "findings": findings}

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
        planning_artifact_guard = self._planning_artifact_guard()
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
            "planning_artifact_guard": planning_artifact_guard,
            "generated_pairs": self.generated_pairs,
            "generated_files": self.generated_files,
            "header_context_diagnostics": self.header_context_diagnostics,
            "json_retry_count": self.json_retry_count,
            "json_generation_attempts": self.json_generation_attempts,
            "failed_generation_unit": self.failed_generation_unit,
            "llm_call_usage": self.llm_call_usage,
            "stage_token_usage": self.stage_token_usage,
            "workflow_token_usage": _usage_to_dict(self.workflow_usage),
            "timings": self.timer.timings,
        }
        if self.method == METHOD_NL:
            manifest["nl_plan_guard_status"] = summary.get("nl_plan_guard_status", "not_run")
            manifest["nl_plan_guard"] = summary.get(
                "nl_plan_guard",
                {"status": "not_run", "findings": []},
            )
        return self.logs.write_named("run_manifest.json", _json(manifest) + "\n")

    def _finish(self, summary: dict[str, Any], manifest_path: Path | None) -> BaselineRunResult:
        leakage = self._leakage_findings()
        planning_artifact_guard = self._planning_artifact_guard()
        summary["llm_call_count"] = len(self.llm_call_usage)
        summary["llm_call_usage"] = self.llm_call_usage
        summary["stage_token_usage"] = self.stage_token_usage
        summary["workflow_token_usage"] = _usage_to_dict(self.workflow_usage)
        summary["timings"] = self.timer.timings
        summary["generated_pairs"] = self.generated_pairs
        summary["generated_files"] = self.generated_files
        summary["header_context_diagnostics"] = self.header_context_diagnostics
        summary["json_retry_count"] = self.json_retry_count
        summary["json_generation_attempts"] = self.json_generation_attempts
        summary["failed_generation_unit"] = self.failed_generation_unit
        summary["leakage_guard"] = {"status": "passed" if not leakage else "failed", "findings": leakage}
        summary["planning_artifact_guard"] = planning_artifact_guard
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


class FSDirectCoderRunner(BaselineProjectRunner):
    method = METHOD_FS_DIRECT

    def _run_generation(self, allowed_inputs: dict[str, Any], summary: dict[str, Any]) -> None:
        summary["nl_plan_status"] = "not_applicable"
        summary["strategy_generation_status"] = "not_applicable"
        summary["file_generation_status"] = "not_applicable"
        self._run_skeleton_pair_completion(allowed_inputs, summary)

    def _run_skeleton_pair_completion(
        self,
        allowed_inputs: dict[str, Any],
        summary: dict[str, Any],
        nl_plan: str = "",
    ) -> None:
        try:
            skeleton = self.timer.run(
                "source_tree_skeleton",
                lambda: self._generate_source_tree_skeleton(allowed_inputs, nl_plan),
            )
        except Exception as exc:
            summary["source_tree_skeleton_status"] = "failed"
            self._mark_failure(summary, "source_tree_skeleton", f"{type(exc).__name__}: {exc}")
            raise
        summary["source_tree_skeleton_status"] = "passed"
        try:
            self.timer.run(
                "pair_completion",
                lambda: self._generate_pair_completion(allowed_inputs, skeleton, nl_plan),
            )
        except Exception as exc:
            summary["pair_completion_status"] = "failed"
            self._mark_failure(summary, "pair_completion", f"{type(exc).__name__}: {exc}")
            raise
        summary["pair_completion_status"] = "passed"

    def _generate_source_tree_skeleton(
        self,
        allowed_inputs: dict[str, Any],
        nl_plan: str = "",
    ) -> dict[str, Any]:
        messages = build_source_tree_skeleton_messages(
            protocol=self.config.protocol,
            allowed_inputs=allowed_inputs,
            nl_plan=nl_plan,
        )

        def validate(text: str) -> dict[str, Any]:
            skeleton = _parse_json_response(text)
            errors = validate_source_tree_skeleton(skeleton)
            if errors:
                raise ValueError("; ".join(errors))
            return {"files": _ordered_skeleton_files(skeleton)}

        normalized = self._generate_validated_json(
            stage="source_tree_skeleton",
            subject=self.config.protocol,
            messages=messages,
            validator=validate,
            unit_paths=[],
        )
        self.current_skeleton = normalized
        self.expected_files = [item["path"] for item in normalized["files"]]
        write_json(self.output_dir / "source_tree_skeleton.json", normalized)
        return normalized

    def _generate_pair_completion(
        self,
        allowed_inputs: dict[str, Any],
        skeleton: dict[str, Any],
        nl_plan: str = "",
    ) -> None:
        units = _skeleton_generation_units(skeleton)
        skeleton_paths = [item["path"] for item in _ordered_skeleton_files(skeleton)]
        for unit in units:
            paths = unit["paths"]
            messages = build_pair_completion_messages(
                protocol=self.config.protocol,
                argv_contract=self.config.argv_contract,
                allowed_inputs=allowed_inputs,
                skeleton_paths=skeleton_paths,
                unit=unit,
                generated_files=self.generated_files,
                header_declarations=self._project_header_declarations(),
                nl_plan=nl_plan,
            )
            if _messages_size_bytes(messages) > MAX_SOURCE_PROMPT_BYTES:
                raise RuntimeError(f"pair completion prompt too large for {', '.join(paths)}")
            subject = "__".join(paths)
            generated = self._generate_validated_json(
                stage="pair_completion",
                subject=subject,
                messages=messages,
                validator=lambda text: self._parse_pair_completion_response(text, paths),
                unit_paths=paths,
            )
            for relative_path in paths:
                content = generated[relative_path]
                target = self.project_dir / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                self.logs.write(f"generated_file_{relative_path}", content)
                if relative_path not in self.generated_files:
                    self.generated_files.append(relative_path)
            if unit["kind"] == "pair":
                self.generated_pairs.append({"kind": "pair", "paths": list(paths)})
        sources = [path for path in self.expected_files if path.endswith(".c")]
        (self.project_dir / "Makefile").write_text(_render_makefile(self.config.binary_name, sources), encoding="utf-8")

    @staticmethod
    def _parse_pair_completion_response(text: str, expected_paths: list[str]) -> dict[str, str]:
        parsed = _parse_json_response(text)
        if set(parsed) != {"files"}:
            raise ValueError("pair completion top-level keys must be exactly: files")
        files = parsed.get("files")
        if not isinstance(files, list):
            raise ValueError("pair completion response must contain files list")
        expected = set(expected_paths)
        generated: dict[str, str] = {}
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise ValueError(f"pair completion files[{index}] must be an object")
            if set(item) != {"path", "content"}:
                raise ValueError(f"pair completion files[{index}] keys must be exactly: path, content")
            path = _safe_project_path(str(item.get("path", "")))
            if path not in expected:
                raise ValueError(f"pair completion returned unexpected path: {path}")
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"pair completion returned empty content for {path}")
            if path in generated:
                raise ValueError(f"pair completion returned duplicate path: {path}")
            generated[path] = content
        missing = expected - set(generated)
        if missing:
            raise ValueError(f"pair completion missing files: {', '.join(sorted(missing))}")
        return generated


class NLPlanCodeRunner(FSDirectCoderRunner):
    method = METHOD_NL

    def _base_summary(self) -> dict[str, Any]:
        summary = super()._base_summary()
        summary["nl_plan_guard_status"] = "not_run"
        summary["nl_plan_guard"] = {"status": "not_run", "findings": []}
        return summary

    def _run_generation(self, allowed_inputs: dict[str, Any], summary: dict[str, Any]) -> None:
        summary["strategy_generation_status"] = "not_applicable"
        summary["file_generation_status"] = "not_applicable"
        nl_plan = self.timer.run("nl_plan", lambda: self._generate_nl_plan(allowed_inputs))
        summary["nl_plan_status"] = "passed"
        guard = self._nl_plan_guard()
        summary["nl_plan_guard_status"] = guard["status"]
        summary["nl_plan_guard"] = guard
        if guard["status"] != "passed":
            raise ValueError("nl_plan.md was not generated as a non-empty natural-language plan")
        self._run_skeleton_pair_completion(allowed_inputs, summary, nl_plan)

    def _nl_plan_guard(self) -> dict[str, Any]:
        path = self.output_dir / "nl_plan.md"
        if not path.is_file():
            return {"status": "failed", "findings": [{"path": "nl_plan.md", "reason": "missing"}]}
        if not path.read_text(encoding="utf-8", errors="ignore").strip():
            return {
                "status": "failed",
                "findings": [{"path": "nl_plan.md", "reason": "empty"}],
            }
        return {"status": "passed", "findings": []}
