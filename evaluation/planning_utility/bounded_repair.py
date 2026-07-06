from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.coder.generation import GenerationLogger, _add_usage, _strip_fences, _usage_to_dict
from agent.coder.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from agent.coder.protocol_behavior_val import verify_protocol_behavior

from .prompts import build_link_repair_messages, build_runtime_repair_messages, build_source_repair_messages
from .repair_diagnostics import (
    CATEGORIES,
    category_counts,
    create_diagnostic_snapshot,
    iter_project_files,
    relpath,
    validate_source_tree,
)
from .requirements import write_json


BASELINE_REPAIR_METHODS = {"fs-direct-coder", "nl-plan-code"}
MAX_TOTAL_REPAIR_CALLS = 6
KNOWN_PROTOCOLS = {"http", "mqtt", "coap", "smtp"}
FORBIDDEN_ARTIFACT_PARTS = {
    "spec_bundle",
    "gold_specs",
    "specs-example",
    "protocol-example",
    "PROTOCOL_MODULE_SPEC",
    "FILE_SPEC",
    "FUNCTION_SPEC",
    "dependency_graph",
    "wire",
    "validators",
}
STANDARD_INCLUDE_RULES = (
    ("stddef.h", (r"\bsize_t\b", r"\bptrdiff_t\b", r"\bNULL\b")),
    ("stdint.h", (r"\b(?:u?int(?:8|16|32|64)_t)\b",)),
    ("stdbool.h", (r"\bbool\b", r"\btrue\b", r"\bfalse\b")),
    ("sys/types.h", (r"\bssize_t\b", r"\bsocklen_t\b")),
    ("sys/select.h", (r"\bfd_set\b", r"\bselect\s*\(")),
    ("sys/socket.h", (r"\bsocket\s*\(", r"\bbind\s*\(", r"\blisten\s*\(", r"\baccept4?\s*\(", r"\bconnect\s*\(", r"\bsend\s*\(", r"\brecv\s*\(", r"\bsetsockopt\s*\(")),
    ("netinet/in.h", (r"\bsockaddr_in\b", r"\bsockaddr_storage\b", r"\bINADDR_", r"\bhtons\s*\(", r"\bntohs\s*\(")),
    ("arpa/inet.h", (r"\bINET6_ADDRSTRLEN\b", r"\binet_(?:ntop|pton|addr)\s*\(")),
    ("stdlib.h", (r"\bmalloc\s*\(", r"\bcalloc\s*\(", r"\brealloc\s*\(", r"\bfree\s*\(", r"\batoi\s*\(", r"\bstrtol\s*\(")),
    ("string.h", (r"\bmem(?:cpy|move|set|cmp)\s*\(", r"\bstr(?:len|cmp|ncmp|cpy|ncpy|dup)\s*\(")),
    ("unistd.h", (r"\bread\s*\(", r"\bwrite\s*\(", r"\bclose\s*\(", r"\busleep\s*\(")),
    ("errno.h", (r"\berrno\b", r"\bE(?:INTR|AGAIN|WOULDBLOCK|INVAL|PIPE)\b")),
    ("stdio.h", (r"\b(?:a?s?printf|snprintf|fprintf|perror)\s*\(", r"\bFILE\b")),
    ("time.h", (r"\btime_t\b", r"\btime\s*\(")),
    ("pthread.h", (r"\bpthread_",)),
)


@dataclass
class RepairConfig:
    project_dir: Path
    method: str
    protocol: str
    binary_name: str
    argv_contract: str
    output_summary_path: Path | None = None
    max_repair_calls: int = MAX_TOTAL_REPAIR_CALLS
    dry_run: bool = False
    transport: str = ""


@dataclass
class PatchAttempt:
    stage: str
    target: str
    root_cause_id: str
    status: str
    reason: str
    touched_files: list[str]


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _status_from_returncode(returncode: int | None) -> str:
    return "passed" if returncode == 0 else "failed"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _has_include(text: str, header: str) -> bool:
    return re.search(rf'^\s*#\s*include\s+[<"]{re.escape(header)}[>"]', text, re.MULTILINE) is not None


def _needed_includes(text: str) -> list[str]:
    needed: list[str] = []
    without_comments = re.sub(r"/\*.*?\*/|//.*?$", "", text, flags=re.DOTALL | re.MULTILINE)
    for header, patterns in STANDARD_INCLUDE_RULES:
        if not _has_include(text, header) and any(re.search(pattern, without_comments) for pattern in patterns):
            needed.append(header)
    return needed


def _insert_includes(text: str, includes: list[str], *, is_header: bool) -> str:
    if not includes:
        return text
    include_lines = [f"#include <{header}>" for header in includes]
    lines = text.splitlines()
    insert_at = 0
    if is_header:
        for index, line in enumerate(lines[:8]):
            if line.strip().startswith("#define ") or line.strip() == "#pragma once":
                insert_at = index + 1
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            insert_at = index + 1
    new_lines = lines[:insert_at] + include_lines + lines[insert_at:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def _ensure_header_guard(text: str, relative_path: str) -> tuple[str, bool]:
    if "#pragma once" in text or re.search(r"^\s*#\s*ifndef\s+\w+", text, re.MULTILINE):
        return text, False
    macro = re.sub(r"[^A-Za-z0-9]", "_", relative_path.upper()).strip("_") + "_"
    wrapped = f"#ifndef {macro}\n#define {macro}\n{text.rstrip()}\n#endif /* {macro} */\n"
    return wrapped, True


def _add_cflags(text: str, flags: list[str]) -> tuple[str, bool]:
    missing = [flag for flag in flags if flag not in text]
    if not missing:
        return text, False
    pattern = re.compile(r"^(CFLAGS\s*(?:\?=|:=|=)\s*)(.*)$", re.MULTILINE)
    match = pattern.search(text)
    if match:
        current = match.group(2)
        updated = current
        if "-std=c11" in updated and "-std=gnu11" in missing:
            updated = updated.replace("-std=c11", "-std=gnu11")
            missing = [flag for flag in missing if flag != "-std=gnu11"]
        updated = " ".join([updated, *missing]).strip()
        return text[: match.start(2)] + updated + text[match.end(2) :], True
    return f"CFLAGS ?= {' '.join(missing)}\n{text}", True


def _add_sources(text: str, sources: list[str]) -> tuple[str, bool]:
    missing = [source for source in sources if source not in text]
    if not missing:
        return text, False
    return text.rstrip() + "\n\nSRCS += " + " ".join(missing) + "\n", True


def _normalize_diff_path(path: str) -> str:
    path = path.strip()
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def _parse_diff_paths(diff_text: str) -> list[str]:
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw == "/dev/null":
                paths.append(raw)
                continue
            path = _normalize_diff_path(raw)
            if path and path not in paths:
                paths.append(path)
    return [path for path in paths if path != "/dev/null"]


class BoundedCRepairRunner:
    def __init__(self, config: RepairConfig, *, llm_client: FixedQwenClient | Any) -> None:
        if config.method not in BASELINE_REPAIR_METHODS:
            raise ValueError(f"bounded generic repair only supports {sorted(BASELINE_REPAIR_METHODS)}, got {config.method}")
        self.config = config
        self.llm_client = llm_client
        self.max_repair_calls = max(0, min(int(config.max_repair_calls), MAX_TOTAL_REPAIR_CALLS))
        self.artifact_dir = (config.output_summary_path.parent if config.output_summary_path else config.project_dir / ".repair")
        self.summary_path = config.output_summary_path or self.artifact_dir / "repair_summary.json"
        self.log = GenerationLogger(self.artifact_dir / "logs")
        self.workflow_usage = LLMUsage(0, 0, 0)
        self.stage_token_usage: dict[str, dict[str, int]] = {}
        self.llm_call_usage: list[dict[str, Any]] = []
        self.llm_repair_calls = 0
        self.modified_files: set[str] = set()
        self.patch_attempts: list[PatchAttempt] = []
        self.repair_events: list[dict[str, Any]] = []
        self.leakage_guard: dict[str, Any] = {"status": "passed", "findings": [], "skipped_forbidden_artifacts": []}
        self.semantic_risk_count = 0
        self.generated_stub_count = 0
        self.wrapper_count = 0
        self.duplicate_symbol_repairs = 0
        self.signature_alignment_repairs = 0
        self.portability_repairs = 0
        self.deterministic_patch_count = 0
        self.llm_patch_count = 0
        self._temp_root: tempfile.TemporaryDirectory[str] | None = None
        self.work_dir = config.project_dir

    def run(self) -> dict[str, Any]:
        started_at = datetime.now().isoformat(timespec="seconds")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        if self.config.dry_run:
            self._temp_root = tempfile.TemporaryDirectory(prefix="planning_utility_repair_")
            self.work_dir = Path(self._temp_root.name) / self.config.project_dir.name
            shutil.copytree(self.config.project_dir, self.work_dir, ignore=shutil.ignore_patterns(".repair"))
        try:
            summary = self._run_inner(started_at)
            write_json(self.summary_path, summary)
            return summary
        finally:
            if self._temp_root is not None:
                self._temp_root.cleanup()

    def _run_inner(self, started_at: str) -> dict[str, Any]:
        incomplete = validate_source_tree(self.work_dir, self.config.binary_name)
        if incomplete:
            if self.work_dir.exists():
                self._has_forbidden_access()
            summary = self._incomplete_summary(started_at, incomplete)
            self._write_minimal_artifacts(incomplete)
            return summary

        before = self._snapshot("before")
        self._write_initial_artifacts(before)
        summary = self._base_summary(started_at, before)
        if self._has_forbidden_access():
            summary["leakage_guard"] = self.leakage_guard
            summary["repair_stop_reason"] = "leakage_guard_failed"
            return self._finalize(summary, before, before, "not_run")

        deterministic_changes = self._apply_deterministic_repairs(before)
        self.deterministic_patch_count = len(deterministic_changes)
        after_det = self._snapshot("after_deterministic")
        write_json(self.artifact_dir / "repair_diagnostics_after_deterministic.json", self._diagnostics_payload(after_det))
        summary["deterministic_repairs"] = deterministic_changes
        summary["compile_after_deterministic"] = _status_from_returncode(after_det["build"]["returncode"])

        current = after_det
        stop_reason = "compile_succeeded" if current["build"]["returncode"] == 0 else "no_generic_repair_target"
        if current["build"]["returncode"] != 0 and self.max_repair_calls > 0:
            current, stop_reason = self._run_source_header_llm_repairs(current)

        summary["compile_after_llm"] = _status_from_returncode(current["build"]["returncode"])
        if current["build"]["returncode"] != 0 and self._source_checks_pass(current) and self.llm_repair_calls < self.max_repair_calls:
            current, stop_reason = self._run_link_repairs(current, stop_reason)

        link_status = _status_from_returncode(current["build"]["returncode"])
        summary["link_after_repair"] = link_status
        runtime_status = "not_run"
        smoke_status = "not_run"
        verification_success: bool | None = None
        verification: dict[str, Any] = {"scenarios": [], "error": None}
        if current["build"]["returncode"] == 0:
            runtime = self._probe_runtime_start()
            runtime_status = runtime["status"]
            write_json(self.artifact_dir / "runtime_start_probe.json", runtime)
            if runtime_status == "failed" and self.llm_repair_calls < self.max_repair_calls:
                current, stop_reason = self._run_runtime_repair(current, runtime)
                runtime = self._probe_runtime_start()
                runtime_status = runtime["status"]
                write_json(self.artifact_dir / "runtime_start_probe_after_repair.json", runtime)
            if runtime_status in {"passed", "skipped"}:
                ok, scenarios, error = verify_protocol_behavior(self.config.protocol, self.work_dir, self.config.binary_name)
                verification_success = ok
                smoke_status = "passed" if ok else "failed"
                verification = {"scenarios": scenarios, "error": error}
                write_json(self.artifact_dir / "behavior_verification.json", verification)
                if not ok:
                    self._record_remaining_c5(current, scenarios, error)
            if stop_reason == "compile_succeeded":
                stop_reason = "completed" if smoke_status != "failed" else "planning_dependent_remaining"
        else:
            planning_remaining = sum(1 for item in current["root_causes"] if item.get("planning_dependent"))
            stop_reason = "planning_dependent_remaining" if planning_remaining else stop_reason

        if self.config.dry_run:
            stop_reason = "dry_run_complete" if stop_reason in {"completed", "compile_succeeded"} else stop_reason

        return self._finalize(summary, before, current, stop_reason, runtime_status, smoke_status, verification_success, verification)

    def _base_summary(self, started_at: str, before: dict[str, Any]) -> dict[str, Any]:
        counts = before["category_counts"]
        errors = before["error_counts"]
        return {
            "schema_version": "planning_utility_bounded_generic_c_repair/v1",
            "started_at": started_at,
            "method": self.config.method,
            "protocol": self.config.protocol,
            "project_dir": str(self.config.project_dir),
            "work_dir": str(self.work_dir),
            "dry_run": self.config.dry_run,
            "binary_name": self.config.binary_name,
            "argv_contract": self.config.argv_contract,
            "max_repair_calls": self.max_repair_calls,
            "compile_before_repair": _status_from_returncode(before["build"]["returncode"]),
            "compile_after_deterministic": "not_run",
            "compile_after_llm": "not_run",
            "link_after_repair": "not_run",
            "runtime_start_status": "not_run",
            "deterministic_patch_count": 0,
            "llm_patch_count": 0,
            "llm_repair_calls": 0,
            "modified_files": [],
            "repaired_files": [],
            "blocking_files": [],
            "repair_stop_reason": "",
            "smoke_status": "not_run",
            "verification_success": None,
            "verification": {"scenarios": [], "error": None},
            "planning_dependent_remaining_count": 0,
            "semantic_risk_count": 0,
            "generated_stub_count": 0,
            "wrapper_count": 0,
            "duplicate_symbol_repairs": 0,
            "signature_alignment_repairs": 0,
            "portability_repairs": 0,
            "repair_events": [],
            "patch_attempts": [],
            "llm_call_usage": [],
            "stage_token_usage": {},
            "workflow_token_usage": _usage_to_dict(LLMUsage(0, 0, 0)),
            "leakage_guard": self.leakage_guard,
            **{f"{category}_before_count": counts[category] for category in CATEGORIES},
            **{f"{category}_fixed_count": 0 for category in CATEGORIES},
            **{f"{category}_remaining_count": 0 for category in CATEGORIES},
            "header_compile_errors_before": errors["header_compile_errors"],
            "source_errors_before": errors["source_errors"],
            "link_errors_before": errors["link_errors"],
            "header_compile_errors_after": 0,
            "source_errors_after": 0,
            "link_errors_after": 0,
        }

    def _incomplete_summary(self, started_at: str, reasons: list[str]) -> dict[str, Any]:
        summary = {
            "schema_version": "planning_utility_bounded_generic_c_repair/v1",
            "started_at": started_at,
            "method": self.config.method,
            "protocol": self.config.protocol,
            "project_dir": str(self.config.project_dir),
            "work_dir": str(self.work_dir),
            "dry_run": self.config.dry_run,
            "binary_name": self.config.binary_name,
            "argv_contract": self.config.argv_contract,
            "max_repair_calls": self.max_repair_calls,
            "compile_before_repair": "not_run",
            "compile_after_deterministic": "not_run",
            "compile_after_llm": "not_run",
            "link_after_repair": "not_run",
            "runtime_start_status": "not_run",
            "deterministic_patch_count": 0,
            "llm_patch_count": 0,
            "llm_repair_calls": 0,
            "modified_files": [],
            "repaired_files": [],
            "blocking_files": [],
            "repair_stop_reason": "incomplete_source_tree",
            "incomplete_source_tree_reasons": reasons,
            "smoke_status": "not_run",
            "verification_success": None,
            "planning_dependent_remaining_count": 0,
            "semantic_risk_count": 0,
            "generated_stub_count": 0,
            "wrapper_count": 0,
            "duplicate_symbol_repairs": 0,
            "signature_alignment_repairs": 0,
            "portability_repairs": 0,
            "repair_events": [],
            "patch_attempts": [],
            "llm_call_usage": [],
            "stage_token_usage": {},
            "workflow_token_usage": _usage_to_dict(LLMUsage(0, 0, 0)),
            "leakage_guard": self.leakage_guard,
            "header_compile_errors_before": 0,
            "source_errors_before": 0,
            "link_errors_before": 0,
            "header_compile_errors_after": 0,
            "source_errors_after": 0,
            "link_errors_after": 0,
        }
        for category in CATEGORIES:
            summary[f"{category}_before_count"] = 1 if category == "C1" else 0
            summary[f"{category}_fixed_count"] = 0
            summary[f"{category}_remaining_count"] = 1 if category == "C1" else 0
        return summary

    def _write_minimal_artifacts(self, reasons: list[str]) -> None:
        causes = [
            {
                "id": "incomplete_source_tree",
                "category": "C1",
                "phase": "preflight",
                "path": "",
                "symbol": "",
                "message": ", ".join(reasons),
                "planning_dependent": False,
            }
        ]
        write_json(self.artifact_dir / "repair_diagnostics.json", {"root_causes": causes})
        write_json(self.artifact_dir / "repair_file_index.json", {"files": []})
        write_json(self.artifact_dir / "repair_summary_before.json", {"root_causes": causes, "category_counts": category_counts(causes)})

    def _snapshot(self, label: str) -> dict[str, Any]:
        snapshot = create_diagnostic_snapshot(self.work_dir, self.config.binary_name)
        self.log.write(f"{label}_build_stdout", str(snapshot["build"].get("stdout", "")))
        self.log.write(f"{label}_build_stderr", str(snapshot["build"].get("stderr", "")))
        return snapshot

    @staticmethod
    def _diagnostics_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "build": {key: value for key, value in snapshot["build"].items() if key not in {"stdout", "stderr"}},
            "root_causes": snapshot["root_causes"],
            "category_counts": snapshot["category_counts"],
            "error_counts": snapshot["error_counts"],
        }

    def _write_initial_artifacts(self, before: dict[str, Any]) -> None:
        write_json(self.artifact_dir / "repair_diagnostics.json", self._diagnostics_payload(before))
        write_json(self.artifact_dir / "repair_file_index.json", before["file_index"])
        write_json(
            self.artifact_dir / "repair_summary_before.json",
            {
                "compile_before_repair": _status_from_returncode(before["build"]["returncode"]),
                "root_causes": before["root_causes"],
                "category_counts": before["category_counts"],
                "error_counts": before["error_counts"],
            },
        )

    def _has_forbidden_access(self) -> bool:
        findings: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        root = self.work_dir.resolve(strict=False)
        for path in sorted(self.work_dir.rglob("*")):
            if any(part in FORBIDDEN_ARTIFACT_PARTS for part in path.parts):
                skipped.append({"path": str(path), "reason": "forbidden_artifact_skipped"})
        for path in iter_project_files(self.work_dir):
            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(root)
            except Exception:  # noqa: BLE001
                findings.append({"path": str(path), "reason": "symlink_or_path_escapes_project"})
        self.leakage_guard = {
            "status": "passed" if not findings else "failed",
            "findings": findings,
            "skipped_forbidden_artifacts": skipped,
        }
        write_json(self.artifact_dir / "repair_access_audit.json", self.leakage_guard)
        if findings:
            return True
        return False

    def _apply_deterministic_repairs(self, snapshot: dict[str, Any]) -> list[dict[str, str]]:
        changes: list[dict[str, str]] = []
        for path in iter_project_files(self.work_dir):
            if path.suffix not in {".h", ".c"}:
                continue
            relative = relpath(path, self.work_dir)
            text = _read(path)
            updated = text
            if path.suffix == ".h":
                updated, guarded = _ensure_header_guard(updated, relative)
                if guarded:
                    changes.append({"path": relative, "kind": "header_guard", "detail": "added include guard"})
            includes = _needed_includes(updated)
            if includes:
                updated = _insert_includes(updated, includes, is_header=path.suffix == ".h")
                changes.append({"path": relative, "kind": "include", "detail": ",".join(includes)})
            if updated != text:
                _write(path, updated)
                self.modified_files.add(relative)

        makefile = self.work_dir / "Makefile"
        if makefile.is_file():
            before = _read(makefile)
            text = before
            all_sources = sorted(relpath(path, self.work_dir) for path in iter_project_files(self.work_dir) if path.suffix == ".c")
            text, source_changed = _add_sources(text, all_sources)
            include_dirs = sorted({Path(relpath(path.parent, self.work_dir)).as_posix() for path in iter_project_files(self.work_dir) if path.suffix == ".h" and path.parent != self.work_dir})
            include_flags = [f"-I{item}" for item in include_dirs if item and f"-I{item}" not in text]
            source_text = "\n".join(_read(path) for path in iter_project_files(self.work_dir) if path.suffix in {".h", ".c"})
            feature_flags = []
            if "accept4" in source_text or "asprintf" in source_text:
                feature_flags.extend(["-D_GNU_SOURCE", "-std=gnu11"])
            text, flags_changed = _add_cflags(text, [*include_flags, *feature_flags])
            if source_changed:
                changes.append({"path": "Makefile", "kind": "makefile_sources", "detail": "added missing .c sources"})
            if flags_changed:
                changes.append({"path": "Makefile", "kind": "makefile_flags", "detail": "added include paths or feature macros"})
                self.portability_repairs += 1 if feature_flags else 0
            if text != before:
                _write(makefile, text)
                self.modified_files.add("Makefile")
        return changes

    @staticmethod
    def _source_checks_pass(snapshot: dict[str, Any]) -> bool:
        return all(int(item.get("returncode", 0)) == 0 for item in snapshot.get("source_checks", []))

    def _run_source_header_llm_repairs(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
        current = snapshot
        no_progress: dict[str, int] = {}
        for _ in range(3):
            if current["build"]["returncode"] == 0 or self.llm_repair_calls >= self.max_repair_calls:
                break
            cause = self._select_root_cause(current, categories={"C2", "C3"}, phases={"compile", "source", "header"})
            if cause is None:
                return current, "no_generic_repair_target"
            before_score = self._repairable_score(current)
            target = cause.get("path") or self._symbol_owner_path(current, str(cause.get("symbol", "")))
            if not target or not (self.work_dir / target).is_file():
                return current, "no_repairable_sources"
            allow_pair = self._is_pair_repair_allowed(target, cause)
            messages = build_source_repair_messages(
                argv_contract=self.config.argv_contract,
                target_path=target,
                target_content=_read(self.work_dir / target),
                related_headers=self._related_headers(current, target, str(cause.get("symbol", ""))),
                related_snippets=self._related_snippets(current, str(cause.get("symbol", ""))),
                diagnostics=[cause],
                classification={"category": cause.get("category"), "planning_dependent": cause.get("planning_dependent")},
                allow_pair=allow_pair,
            )
            diff_text = self._call_llm("repair_c2_c3", target, messages)
            ok, reason, touched = self._validate_diff(diff_text, target, allow_pair=allow_pair, allow_stub=False)
            if not ok:
                self._record_patch("repair_c2_c3", target, str(cause["id"]), "rejected", reason, touched)
                if reason == "patch_format_or_scope_rejected":
                    return current, "patch_apply_failed"
                continue
            backup = self._backup(touched)
            applied, apply_reason = self._apply_diff(diff_text, touched)
            if not applied:
                self._restore(backup)
                self._record_patch("repair_c2_c3", target, str(cause["id"]), "rejected", apply_reason, touched)
                return current, "patch_apply_failed"
            new_snapshot = self._snapshot(f"after_llm_{self.llm_repair_calls}")
            if new_snapshot["build"]["returncode"] == 0 or self._repairable_score(new_snapshot) < before_score:
                self.llm_patch_count += 1
                self.modified_files.update(touched)
                if cause.get("category") == "C3":
                    self.signature_alignment_repairs += 1
                current = new_snapshot
                self._record_patch("repair_c2_c3", target, str(cause["id"]), "accepted", "root_cause_reduced", touched)
                continue
            self._restore(backup)
            current = self._snapshot(f"after_llm_revert_{self.llm_repair_calls}")
            no_progress[str(cause["id"])] = no_progress.get(str(cause["id"]), 0) + 1
            self._record_patch("repair_c2_c3", target, str(cause["id"]), "rejected", "no_root_cause_reduction", touched)
            if no_progress[str(cause["id"])] >= 2:
                return current, "repeated_root_cause_no_progress"
        if current["build"]["returncode"] == 0:
            return current, "compile_succeeded"
        if self.llm_repair_calls >= self.max_repair_calls:
            return current, "max_repair_calls_exhausted"
        planning = sum(1 for item in current["root_causes"] if item.get("category") in {"C3", "C4", "C5"} and item.get("planning_dependent"))
        return current, "planning_dependent_remaining" if planning else "no_generic_repair_target"

    def _run_link_repairs(self, snapshot: dict[str, Any], stop_reason: str) -> tuple[dict[str, Any], str]:
        current = self._repair_duplicate_definitions(snapshot)
        if current["build"]["returncode"] == 0:
            return current, "compile_succeeded"
        for _ in range(2):
            if current["build"]["returncode"] == 0 or self.llm_repair_calls >= self.max_repair_calls:
                break
            cause = self._select_root_cause(current, categories={"C3", "C4"}, phases={"link"})
            if cause is None or cause.get("planning_dependent"):
                return current, "planning_dependent_remaining"
            target = self._symbol_owner_path(current, str(cause.get("symbol", ""))) or "main.c"
            if not (self.work_dir / target).is_file():
                return current, "no_repairable_sources"
            messages = build_link_repair_messages(
                argv_contract=self.config.argv_contract,
                target_path=target,
                target_content=_read(self.work_dir / target),
                file_index=current["file_index"],
                diagnostics=[cause],
            )
            diff_text = self._call_llm("repair_link", target, messages)
            ok, reason, touched = self._validate_diff(diff_text, target, allow_pair=True, allow_stub=True)
            if not ok:
                self._record_patch("repair_link", target, str(cause["id"]), "rejected", reason, touched)
                return current, "patch_apply_failed"
            backup = self._backup(touched)
            applied, apply_reason = self._apply_diff(diff_text, touched)
            if not applied:
                self._restore(backup)
                self._record_patch("repair_link", target, str(cause["id"]), "rejected", apply_reason, touched)
                return current, "patch_apply_failed"
            new_snapshot = self._snapshot(f"after_link_llm_{self.llm_repair_calls}")
            if new_snapshot["build"]["returncode"] == 0 or len(new_snapshot["root_causes"]) < len(current["root_causes"]):
                self.llm_patch_count += 1
                self.modified_files.update(touched)
                current = new_snapshot
                self._record_patch("repair_link", target, str(cause["id"]), "accepted", "link_root_cause_reduced", touched)
                continue
            self._restore(backup)
            current = self._snapshot(f"after_link_revert_{self.llm_repair_calls}")
            self._record_patch("repair_link", target, str(cause["id"]), "rejected", "no_root_cause_reduction", touched)
        if current["build"]["returncode"] == 0:
            return current, "compile_succeeded"
        if self.llm_repair_calls >= self.max_repair_calls:
            return current, "max_repair_calls_exhausted"
        return current, stop_reason

    def _repair_duplicate_definitions(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        changed = False
        definitions = snapshot["file_index"].get("duplicate_definition", {})
        declarations = snapshot["file_index"].get("function_declarations", {})
        for symbol, defs in definitions.items():
            extern_defs = [item for item in defs if item.get("storage") == "extern"]
            if len(extern_defs) < 2 or declarations.get(symbol):
                continue
            owners = [item for item in extern_defs if Path(item["path"]).stem in symbol]
            if len(owners) != 1:
                continue
            owner_path = owners[0]["path"]
            for item in extern_defs:
                path = item["path"]
                if path == owner_path:
                    continue
                file_path = self.work_dir / path
                text = _read(file_path)
                pattern = re.compile(rf"(?m)^(\s*)(?!static\b)((?:[A-Za-z_][\w\s\*\(\),]*?\s+)+{re.escape(symbol)}\s*\()")
                updated, count = pattern.subn(r"\1static \2", text, count=1)
                if count:
                    _write(file_path, updated)
                    self.modified_files.add(path)
                    self.duplicate_symbol_repairs += 1
                    changed = True
        return self._snapshot("after_duplicate_symbol_repair") if changed else snapshot

    def _run_runtime_repair(self, snapshot: dict[str, Any], runtime: dict[str, Any]) -> tuple[dict[str, Any], str]:
        target = "main.c" if (self.work_dir / "main.c").is_file() else ""
        if not target:
            return snapshot, "runtime_repair_not_applicable"
        messages = build_runtime_repair_messages(
            argv_contract=self.config.argv_contract,
            target_path=target,
            target_content=_read(self.work_dir / target),
            runtime_diagnostics=runtime,
        )
        diff_text = self._call_llm("repair_runtime", target, messages)
        ok, reason, touched = self._validate_diff(diff_text, target, allow_pair=False, allow_stub=False)
        if not ok:
            self._record_patch("repair_runtime", target, "runtime_start", "rejected", reason, touched)
            return snapshot, "runtime_repair_not_applicable"
        backup = self._backup(touched)
        applied, apply_reason = self._apply_diff(diff_text, touched)
        if not applied:
            self._restore(backup)
            self._record_patch("repair_runtime", target, "runtime_start", "rejected", apply_reason, touched)
            return snapshot, "patch_apply_failed"
        new_snapshot = self._snapshot("after_runtime_llm")
        if new_snapshot["build"]["returncode"] == 0:
            self.llm_patch_count += 1
            self.modified_files.update(touched)
            self._record_patch("repair_runtime", target, "runtime_start", "accepted", "runtime_patch_compiles", touched)
            return new_snapshot, "compile_succeeded"
        self._restore(backup)
        self._record_patch("repair_runtime", target, "runtime_start", "rejected", "runtime_patch_broke_build", touched)
        return snapshot, "runtime_repair_not_applicable"

    def _select_root_cause(self, snapshot: dict[str, Any], *, categories: set[str], phases: set[str]) -> dict[str, Any] | None:
        for cause in snapshot["root_causes"]:
            if cause.get("category") in categories and cause.get("phase") in phases and not cause.get("planning_dependent"):
                return cause
        return None

    def _repairable_score(self, snapshot: dict[str, Any]) -> int:
        return sum(1 for item in snapshot["root_causes"] if item.get("category") in {"C1", "C2", "C3"})

    def _symbol_owner_path(self, snapshot: dict[str, Any], symbol: str) -> str:
        if not symbol:
            return ""
        defs = snapshot["file_index"].get("function_definitions", {}).get(symbol, [])
        if len(defs) == 1:
            return str(defs[0]["path"])
        return ""

    def _is_pair_repair_allowed(self, target: str, cause: dict[str, Any]) -> bool:
        if cause.get("category") != "C3":
            return False
        path = Path(target)
        if path.suffix not in {".h", ".c"}:
            return False
        pair = self.work_dir / path.with_suffix(".c" if path.suffix == ".h" else ".h")
        return pair.is_file()

    def _related_headers(self, snapshot: dict[str, Any], target: str, symbol: str) -> dict[str, str]:
        candidates: list[str] = []
        target_path = self.work_dir / target
        if target_path.is_file():
            text = _read(target_path)
            for include in re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.MULTILINE):
                if include.endswith(".h") and (self.work_dir / include).is_file():
                    candidates.append(include)
        for decl in snapshot["file_index"].get("function_declarations", {}).get(symbol, []):
            path = decl.get("path")
            if path and path.endswith(".h"):
                candidates.append(path)
        if Path(target).suffix == ".c":
            pair = str(Path(target).with_suffix(".h"))
            if (self.work_dir / pair).is_file():
                candidates.insert(0, pair)
        headers: dict[str, str] = {}
        for path in candidates:
            if path not in headers and (self.work_dir / path).is_file():
                headers[path] = _read(self.work_dir / path)[:12000]
            if len(headers) >= 3:
                break
        return headers

    def _related_snippets(self, snapshot: dict[str, Any], symbol: str) -> dict[str, str]:
        if not symbol:
            return {}
        snippets: dict[str, str] = {}
        for file_info in snapshot["file_index"].get("files", []):
            path = str(file_info.get("path", ""))
            if not path or not (self.work_dir / path).is_file():
                continue
            lines = _read(self.work_dir / path).splitlines()
            selected: list[str] = []
            for index, line in enumerate(lines):
                if symbol in line:
                    start = max(0, index - 3)
                    end = min(len(lines), index + 4)
                    selected.extend(f"{number + 1}: {lines[number]}" for number in range(start, end))
            if selected:
                snippets[path] = "\n".join(selected[:80])
            if len(snippets) >= 4:
                break
        return snippets

    def _call_llm(self, stage: str, subject: str, messages: list[dict[str, str]]) -> str:
        if self.llm_repair_calls == 0:
            self.llm_client.ensure_ready()
        self.llm_repair_calls += 1
        self.log.write(f"prompt_{stage}_{self.llm_repair_calls}_{subject}", _json(messages))
        response = self.llm_client.generate_with_usage(
            LLMRequest(messages=messages, top_p=0.2, temperature=0.2, is_stream=True)
        )
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse, got {type(response)!r}")
        self.workflow_usage = _add_usage(self.workflow_usage, response.usage)
        usage_dict = _usage_to_dict(response.usage)
        self.llm_call_usage.append({"stage": stage, "call_type": "repair", "subject": subject, "usage": usage_dict})
        existing = self.stage_token_usage.get(stage, _usage_to_dict(LLMUsage(0, 0, 0)))
        self.stage_token_usage[stage] = _usage_to_dict(_add_usage(LLMUsage(**existing), response.usage))
        self.log.write(f"response_{stage}_{self.llm_repair_calls}_{subject}", response.content)
        return _strip_fences(response.content)

    def _validate_diff(self, diff_text: str, target: str, *, allow_pair: bool, allow_stub: bool) -> tuple[bool, str, list[str]]:
        if not diff_text.strip().startswith("--- ") or "\n+++ " not in diff_text:
            return False, "patch_format_or_scope_rejected", []
        touched = _parse_diff_paths(diff_text)
        if not touched:
            return False, "patch_format_or_scope_rejected", []
        allowed = {target}
        if allow_pair and Path(target).suffix in {".h", ".c"}:
            pair = Path(target).with_suffix(".c" if Path(target).suffix == ".h" else ".h").as_posix()
            allowed.add(pair)
        if len(touched) > (2 if allow_pair else 1) or any(path not in allowed for path in touched):
            return False, "patch_touches_disallowed_files", touched
        for path in touched:
            suffix = Path(path).suffix
            if suffix not in {".h", ".c"} and path != "Makefile":
                return False, "patch_touches_disallowed_files", touched
            if any(part in {"tests", ".repair", "_agent_logs"} for part in Path(path).parts):
                return False, "patch_touches_disallowed_files", touched
            try:
                (self.work_dir / path).resolve(strict=False).relative_to(self.work_dir.resolve(strict=False))
            except Exception:  # noqa: BLE001
                return False, "patch_touches_disallowed_files", touched
        added_lines = [line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")]
        risky = [line for line in added_lines if re.search(r"\b(parser|serializer|handler|router|broker|codec)\b", line, re.IGNORECASE)]
        if risky:
            self.semantic_risk_count += 1
        if risky and len(added_lines) > 80:
            return False, "large_semantic_rewrite_rejected", touched
        if not allow_stub and any("stub" in line.lower() or "TODO" in line for line in added_lines):
            self.generated_stub_count += 1
            return False, "stub_rejected", touched
        if allow_stub and any("stub" in line.lower() for line in added_lines):
            self.generated_stub_count += 1
        return True, "", touched

    def _backup(self, paths: list[str]) -> dict[str, str | None]:
        backup: dict[str, str | None] = {}
        for path in paths:
            target = self.work_dir / path
            backup[path] = _read(target) if target.is_file() else None
        return backup

    def _restore(self, backup: dict[str, str | None]) -> None:
        for path, content in backup.items():
            target = self.work_dir / path
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write(target, content)

    def _apply_diff(self, diff_text: str, touched: list[str]) -> tuple[bool, str]:
        self.log.write(f"candidate_diff_{self.llm_repair_calls}", diff_text, suffix=".diff")
        strip_level = "1" if any(line.startswith("--- a/") or line.startswith("+++ b/") for line in diff_text.splitlines()) else "0"
        result = subprocess.run(
            ["patch", f"-p{strip_level}", "--forward", "--batch"],
            cwd=self.work_dir,
            input=diff_text,
            text=True,
            capture_output=True,
            check=False,
        )
        self.log.write(f"patch_stdout_{self.llm_repair_calls}", result.stdout)
        self.log.write(f"patch_stderr_{self.llm_repair_calls}", result.stderr)
        if result.returncode != 0:
            return False, "patch_apply_failed"
        if not touched:
            return False, "patch_apply_failed"
        return True, ""

    def _record_patch(self, stage: str, target: str, root_cause_id: str, status: str, reason: str, touched: list[str]) -> None:
        self.patch_attempts.append(PatchAttempt(stage, target, root_cause_id, status, reason, touched))

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _runtime_args(self, port: int, tmpdir: str) -> list[str]:
        tokens = self.config.argv_contract.split()
        if not tokens:
            return [f"./{self.config.binary_name}", str(port)]
        args: list[str] = []
        for index, token in enumerate(tokens):
            if index == 0:
                args.append(f"./{self.config.binary_name}")
            elif token == "<port>":
                args.append(str(port))
            elif token.startswith("<") and token.endswith(">"):
                args.append(tmpdir)
            else:
                args.append(token)
        return args

    def _probe_runtime_start(self) -> dict[str, Any]:
        if self.config.protocol not in KNOWN_PROTOCOLS:
            return {"status": "skipped", "reason": "no_generic_runtime_probe_for_protocol"}
        binary = self.work_dir / self.config.binary_name
        if not binary.exists():
            return {"status": "failed", "reason": "binary_missing"}
        port = self._free_port()
        with tempfile.TemporaryDirectory(prefix="repair_runtime_") as tmp:
            args = self._runtime_args(port, tmp)
            proc = subprocess.Popen(args, cwd=self.work_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.time() + 2.0
                connected = False
                while time.time() < deadline:
                    if proc.poll() is not None:
                        stdout, stderr = proc.communicate(timeout=1)
                        return {
                            "status": "failed",
                            "reason": "process_exited_immediately",
                            "returncode": proc.returncode,
                            "stdout": stdout.decode("utf-8", errors="ignore")[-2000:],
                            "stderr": stderr.decode("utf-8", errors="ignore")[-2000:],
                        }
                    if self.config.protocol in {"http", "mqtt", "smtp"}:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                connected = True
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        time.sleep(0.4)
                        connected = True
                        break
                return {"status": "passed" if connected else "failed", "reason": "" if connected else "port_not_listening", "argv": args}
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    def _record_remaining_c5(self, snapshot: dict[str, Any], scenarios: list[dict[str, str]], error: str | None) -> None:
        detail = error or "; ".join(str(item.get("detail") or item.get("name")) for item in scenarios if item.get("status") == "failed")
        if not detail:
            detail = "protocol behavior smoke failure"
        snapshot["root_causes"].append(
            {
                "id": "runtime_smoke_c5",
                "category": "C5",
                "phase": "smoke",
                "path": "",
                "symbol": "",
                "message": detail[:500],
                "planning_dependent": True,
            }
        )
        snapshot["category_counts"] = category_counts(snapshot["root_causes"])

    def _finalize(
        self,
        summary: dict[str, Any],
        before: dict[str, Any],
        final: dict[str, Any],
        stop_reason: str,
        runtime_status: str = "not_run",
        smoke_status: str = "not_run",
        verification_success: bool | None = None,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        before_counts = before["category_counts"]
        final_counts = final["category_counts"]
        for category in CATEGORIES:
            summary[f"{category}_remaining_count"] = final_counts[category]
            summary[f"{category}_fixed_count"] = max(0, before_counts[category] - final_counts[category])
        errors = final["error_counts"]
        summary.update(
            {
                "deterministic_patch_count": self.deterministic_patch_count,
                "llm_patch_count": self.llm_patch_count,
                "llm_repair_calls": self.llm_repair_calls,
                "modified_files": sorted(self.modified_files),
                "repaired_files": sorted(self.modified_files),
                "blocking_files": sorted({item.get("path", "") for item in final["root_causes"] if item.get("path")}),
                "repair_stop_reason": stop_reason,
                "runtime_start_status": runtime_status,
                "smoke_status": smoke_status,
                "verification_success": verification_success,
                "verification": verification or {"scenarios": [], "error": None},
                "planning_dependent_remaining_count": sum(1 for item in final["root_causes"] if item.get("planning_dependent")),
                "semantic_risk_count": self.semantic_risk_count,
                "generated_stub_count": self.generated_stub_count,
                "wrapper_count": self.wrapper_count,
                "duplicate_symbol_repairs": self.duplicate_symbol_repairs,
                "signature_alignment_repairs": self.signature_alignment_repairs,
                "portability_repairs": self.portability_repairs,
                "patch_attempts": [item.__dict__ for item in self.patch_attempts],
                "repair_events": self.repair_events,
                "llm_call_usage": self.llm_call_usage,
                "stage_token_usage": self.stage_token_usage,
                "workflow_token_usage": _usage_to_dict(self.workflow_usage),
                "leakage_guard": self.leakage_guard,
                "header_compile_errors_after": errors["header_compile_errors"],
                "source_errors_after": errors["source_errors"],
                "link_errors_after": errors["link_errors"],
                "final_root_causes": final["root_causes"],
                "ended_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if final["build"]["returncode"] == 0:
            summary["compile_after_llm"] = "passed"
            summary["link_after_repair"] = "passed"
        write_json(self.artifact_dir / "repair_diagnostics_final.json", self._diagnostics_payload(final))
        return summary
