from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .header_recipes import render_public_declarations
from .llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .models import FileSpec, FunctionSpec, ModuleEntry, SpecBundle
from .prompts import build_main_source_prompt, build_repair_prompt, build_source_prompt
from .specs import (
    canonical_signature_for_header,
    function_specs_for_file,
    normalize_repo_path,
    validate_rendered_headers_compile,
)

MAX_REPAIR_DIAGNOSTIC_BYTES = 64 * 1024
MAX_REPAIR_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REPAIR_RESPONSE_BYTES = 1024 * 1024
DEFAULT_MAX_REPAIR_ROUNDS = 3

SourcePromptBuilder = Callable[
    [SpecBundle, ModuleEntry, FileSpec, list[FunctionSpec], str, dict[str, str]],
    list[dict[str, str]],
]
RepairPromptBuilder = Callable[
    [SpecBundle, ModuleEntry, FileSpec, str, str, str, str, dict[str, str]],
    list[dict[str, str]],
]
PromptObserver = Callable[[str, str, list[dict[str, str]]], None]


@dataclass
class GenerationResult:
    success: bool
    manifest_path: Path
    compile_stdout: str
    compile_stderr: str
    repaired_files: list[str]
    repair_stop_reason: str
    repair_blocking_files: list[str]


@dataclass
class RepairOutcome:
    compile_result: subprocess.CompletedProcess[str] | None
    stop_reason: str
    rounds_attempted: int
    blocking_files: list[str]
    rejected_candidates: list[dict[str, str]]
    fingerprint_history: list[dict[str, Any]] = field(default_factory=list)


class GenerationLogger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def write(self, label: str, content: str, suffix: str = ".txt") -> Path:
        self._counter += 1
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "log"
        path = self.root / f"{self._counter:03d}_{safe_label}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def write_named(self, filename: str, content: str) -> Path:
        path = self.root / filename
        path.write_text(content, encoding="utf-8")
        return path


def _bundle_slug(bundle: SpecBundle) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", bundle.protocol.name.lower()).strip("_")
    return slug or "protocol"


def _bundle_binary_name(bundle: SpecBundle) -> str:
    roles = [role.lower() for role in bundle.protocol.roles]
    suffix = "broker" if "broker" in roles else "server" if "server" in roles else "app"
    return f"{_bundle_slug(bundle)}_{suffix}"


def _usage_to_dict(usage: LLMUsage) -> dict[str, int]:
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _add_usage(left: LLMUsage, right: LLMUsage) -> LLMUsage:
    return LLMUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _ensure_semicolon(signature: str) -> str:
    signature = signature.strip()
    return signature if signature.endswith(";") else signature + ";"


def _collect_std_headers(texts: list[str]) -> list[str]:
    merged = "\n".join(texts)
    headers: list[str] = []
    if "bool" in merged:
        headers.append("stdbool.h")
    if "size_t" in merged:
        headers.append("stddef.h")
    if re.search(r"\b(?:u?int(?:8|16|32|64)_t)\b", merged):
        headers.append("stdint.h")
    return headers


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped + ("\n" if not stripped.endswith("\n") else "")
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    result = "\n".join(lines).strip()
    return result + ("\n" if not result.endswith("\n") else "")


def _function_definition_count(content: str, name: str) -> int:
    pattern = re.compile(
        rf"(?m)^\s*(?:[A-Za-z_][A-Za-z0-9_]*[\s*]+)+{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    return len(pattern.findall(content))


def _source_integrity_issues(
    content: str, source_path: str, function_specs: list[FunctionSpec]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for function in function_specs:
        count = _function_definition_count(content, function.signature.name)
        if count != 1:
            issues.append(
                {
                    "code": "specified_function_definition_count",
                    "source_path": source_path,
                    "function": function.signature.name,
                    "expected": 1,
                    "actual": count,
                }
            )
    if re.search(r"(?mi)^\s*(?:TODO|FIXME)\b|\bnot\s+implemented\b", content):
        issues.append({"code": "placeholder_source", "source_path": source_path})
    return issues


def render_header(bundle: SpecBundle, file_spec: FileSpec) -> str:
    public_declarations = render_public_declarations(file_spec)
    signature_block = [canonical_signature_for_header(bundle, file_spec, item) for item in file_spec.header_interfaces]
    std_headers = _collect_std_headers([public_declarations, *signature_block])

    lines: list[str] = ["#pragma once", ""]
    for dependency in file_spec.header_dependencies:
        lines.append(f'#include "{dependency}"')
    if file_spec.header_dependencies:
        lines.append("")
    for header in file_spec.header_system_dependencies:
        lines.append(f"#include <{header}>")
    if file_spec.header_system_dependencies:
        lines.append("")
    for header in std_headers:
        if header not in file_spec.header_system_dependencies:
            lines.append(f"#include <{header}>")
    if std_headers:
        lines.append("")
    lines.extend(
        [
            "#ifdef __cplusplus",
            'extern "C" {',
            "#endif",
            "",
        ]
    )
    if public_declarations:
        lines.append(public_declarations.rstrip())
        lines.append("")
    for signature in signature_block:
        lines.append(_ensure_semicolon(signature))
    lines.append("")
    lines.extend(
        [
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
        ]
    )
    return "\n".join(lines)


def render_makefile(bundle: SpecBundle) -> str:
    broker_srcs: list[str] = []
    for module in bundle.modules_in_order:
        for file_path in module.files:
            normalized = normalize_repo_path(file_path)
            if normalized.endswith(".c"):
                broker_srcs.append(normalized)
    main_candidates = [normalize_repo_path(path) for module in bundle.modules_in_order for path in module.files if path.endswith("/main.c")]
    for main_path in reversed(main_candidates):
        if main_path not in broker_srcs:
            broker_srcs.insert(0, main_path)
    unique_srcs: list[str] = []
    seen = set()
    for src in broker_srcs:
        if src not in seen:
            seen.add(src)
            unique_srcs.append(src)
    separator = " \\\n\t"
    body = separator.join(unique_srcs)
    binary_name = _bundle_binary_name(bundle)
    return f"""CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror=implicit-function-declaration -pedantic -D_POSIX_C_SOURCE=200809L -I.
LDFLAGS ?=

BROKER_SRCS = \\
\t{body}

TARGET ?= {binary_name}

all: $(TARGET)

$(TARGET): $(BROKER_SRCS)
\t$(CC) $(CFLAGS) -o $@ $(BROKER_SRCS) $(LDFLAGS)

clean:
\trm -f $(TARGET)

.PHONY: all clean
"""


def _compile_project(output_dir: Path, binary_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", binary_name],
        cwd=output_dir,
        text=True,
        capture_output=True,
        check=False,
    )


_DIAGNOSTIC_RE = re.compile(r"^(.+\.(?:c|h)):\d+:\d+: ((?:fatal )?error|warning|note):")
_LINKER_SOURCE_DIAGNOSTIC_RE = re.compile(
    r"(?:^|[;:\s])(?P<path>[^;:\s]+\.c):\([^)]*\):\s*"
    r"(?P<kind>multiple definition of|undefined reference to)"
)
_LINKER_ROOT_RE = re.compile(
    r"(?P<kind>multiple definition of|undefined reference to)\s+[`'‘]"
    r"(?P<symbol>[^`'’]+)[`'’]"
)


def _project_relative_path(raw_path: str, project_dir: Path) -> str | None:
    raw_path = raw_path.strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        absolute = path.resolve(strict=False) if path.is_absolute() else (project_dir / path).resolve(strict=False)
        relative = absolute.relative_to(project_dir.resolve(strict=False))
    except ValueError:
        return None
    return normalize_repo_path(relative.as_posix())


def _extract_project_error_files(stdout: str, stderr: str, project_dir: Path) -> list[str]:
    combined = f"{stdout}\n{stderr}"
    found: list[str] = []
    for line in combined.splitlines():
        match = _DIAGNOSTIC_RE.match(line)
        raw_paths = (
            [match.group(1)]
            if match and "error" in match.group(2)
            else [item.group("path") for item in _LINKER_SOURCE_DIAGNOSTIC_RE.finditer(line)]
        )
        for raw_path in raw_paths:
            relative = _project_relative_path(raw_path, project_dir)
            if relative and relative not in found:
                found.append(relative)
    return found


def _linker_error_roots(stdout: str, stderr: str) -> set[str]:
    return {
        f"{match.group('kind').replace(' ', '_')}:{match.group('symbol')}"
        for match in _LINKER_ROOT_RE.finditer(f"{stdout}\n{stderr}")
    }


def _compile_error_fingerprints(stdout: str, stderr: str, project_dir: Path) -> set[str]:
    fingerprints = set(_linker_error_roots(stdout, stderr))
    for path, level, block in _diagnostic_blocks(stdout, stderr, project_dir):
        if "error" not in level:
            continue
        first_line = block.splitlines()[0]
        message = first_line.split(": error:", 1)[-1]
        message = re.sub(r"\b0x[0-9A-Fa-f]+\b", "<address>", message)
        message = re.sub(r"\s+", " ", message).strip()
        fingerprints.add(f"compile:{path}:{message}")
    return fingerprints


def _canonical_function_symbols(bundle: SpecBundle) -> set[str]:
    symbols = {
        interface.name
        for file_spec in bundle.file_specs_by_trace.values()
        for interface in [*file_spec.header_interfaces, *file_spec.source_interfaces]
        if interface.name
    }
    symbols.update(
        function.signature.name
        for function in bundle.function_specs_by_trace.values()
        if function.signature.name
    )
    return symbols


def _unknown_external_symbols(stdout: str, stderr: str, bundle: SpecBundle) -> set[str]:
    combined = f"{stdout}\n{stderr}"
    referenced = {
        match.group("symbol")
        for match in _LINKER_ROOT_RE.finditer(combined)
        if match.group("kind") == "undefined reference to"
    }
    referenced.update(
        re.findall(r"implicit declaration of function\s+[`'‘]([A-Za-z_][A-Za-z0-9_]*)[`'’]", combined)
    )
    return referenced - _canonical_function_symbols(bundle)


def _classify_repair_targets(
    stdout: str,
    stderr: str,
    project_dir: Path,
    bundle: SpecBundle,
) -> tuple[list[str], list[str]]:
    repairable: list[str] = []
    blocking_headers: list[str] = []
    for relative in _extract_project_error_files(stdout, stderr, project_dir):
        if relative.endswith(".h"):
            if relative not in blocking_headers:
                blocking_headers.append(relative)
            continue
        file_spec = bundle.file_specs_by_source_path.get(relative)
        if file_spec is not None and relative.endswith(".c") and relative not in repairable:
            repairable.append(relative)
    return repairable, blocking_headers


def _diagnostic_blocks(stdout: str, stderr: str, project_dir: Path) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    current_path: str | None = None
    current_level = ""
    current_lines: list[str] = []
    pending_linker_lines: list[str] = []

    def flush() -> None:
        nonlocal current_path, current_level, current_lines
        if current_path and current_lines:
            blocks.append((current_path, current_level, "\n".join(current_lines)))
        current_path = None
        current_level = ""
        current_lines = []

    for line in f"{stdout}\n{stderr}".splitlines():
        match = _DIAGNOSTIC_RE.match(line)
        if match:
            flush()
            pending_linker_lines = []
            relative = _project_relative_path(match.group(1), project_dir)
            if relative is None:
                continue
            current_path = relative
            current_level = match.group(2)
            current_lines = [line]
        elif linker_match := _LINKER_SOURCE_DIAGNOSTIC_RE.search(line):
            flush()
            relative = _project_relative_path(linker_match.group("path"), project_dir)
            if relative is None:
                pending_linker_lines = []
                continue
            current_path = relative
            current_level = "error"
            current_lines = [*pending_linker_lines, line]
            pending_linker_lines = []
        elif line.startswith("/usr/bin/ld:"):
            flush()
            pending_linker_lines = [line]
        elif current_path:
            current_lines.append(line)
    flush()
    return blocks


def _compact_compile_diagnostics(
    stdout: str,
    stderr: str,
    target_path: str,
    project_dir: Path,
    max_bytes: int = MAX_REPAIR_DIAGNOSTIC_BYTES,
) -> str:
    blocks = _diagnostic_blocks(stdout, stderr, project_dir)
    selected = [block for path, _, block in blocks if path == target_path]
    if not selected:
        selected = [block for _, level, block in blocks if "error" in level]
    if not selected:
        selected = [block for _, _, block in blocks]

    retained: list[str] = []
    seen: set[str] = set()
    used = 0
    omitted = 0
    for block in selected:
        if block in seen:
            omitted += 1
            continue
        seen.add(block)
        encoded_len = len((block + "\n\n").encode("utf-8"))
        if retained and used + encoded_len > max_bytes:
            omitted += 1
            continue
        if not retained and encoded_len > max_bytes:
            data = block.encode("utf-8")[:max_bytes]
            retained.append(data.decode("utf-8", errors="ignore"))
            omitted += 1
            used = max_bytes
            continue
        retained.append(block)
        used += encoded_len

    text = "\n\n".join(retained)
    if omitted:
        suffix = f"\n\n[diagnostics truncated: retained <= {max_bytes} bytes, omitted {omitted} diagnostic blocks]"
        available = max_bytes - len(suffix.encode("utf-8"))
        if len(text.encode("utf-8")) > available:
            text = text.encode("utf-8")[:max(0, available)].decode("utf-8", errors="ignore")
        text += suffix
    return text


def _messages_size_bytes(messages: list[dict[str, str]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def _validate_repair_candidate(target_path: str, current_content: str, candidate: str) -> tuple[bool, str]:
    if not candidate.strip():
        return False, "empty_response"
    if len(candidate.encode("utf-8")) > MAX_REPAIR_RESPONSE_BYTES:
        return False, "response_too_large"
    if candidate == current_content:
        return False, "unchanged_response"
    if candidate.lstrip().startswith("#pragma once"):
        return False, "header_like_response"
    include_pattern = re.compile(r'^\s*#\s*include\s+[<"]' + re.escape(normalize_repo_path(target_path)) + r'[>"]', re.MULTILINE)
    if include_pattern.search(candidate):
        return False, "self_include_response"
    return True, ""


class ProjectGenerator:
    def __init__(
        self,
        bundle: SpecBundle,
        llm_client: FixedQwenClient,
        output_dir: str | Path,
        max_repair_rounds: int = DEFAULT_MAX_REPAIR_ROUNDS,
        skip_repair: bool = False,
        source_prompt_builder: SourcePromptBuilder | None = None,
        main_source_prompt_builder: SourcePromptBuilder | None = None,
        repair_prompt_builder: RepairPromptBuilder | None = None,
        prompt_observer: PromptObserver | None = None,
    ) -> None:
        self.bundle = bundle
        self.llm_client = llm_client
        self.output_dir = Path(output_dir)
        self.project_dir = self.output_dir / _bundle_slug(bundle)
        self.max_repair_rounds = max_repair_rounds
        self.skip_repair = skip_repair
        self.source_prompt_builder = source_prompt_builder if source_prompt_builder is not None else build_source_prompt
        self.main_source_prompt_builder = (
            main_source_prompt_builder if main_source_prompt_builder is not None else build_main_source_prompt
        )
        self.repair_prompt_builder = repair_prompt_builder if repair_prompt_builder is not None else build_repair_prompt
        self.prompt_observer = prompt_observer
        self.logs = GenerationLogger(self.output_dir / "_agent_logs")
        self._module_by_file = self._build_module_file_index()
        self.workflow_usage = LLMUsage(0, 0, 0)
        self.llm_call_usage: list[dict[str, Any]] = []
        self.stage_token_usage: dict[str, dict[str, int]] = {}

    def _build_module_file_index(self) -> dict[str, ModuleEntry]:
        mapping: dict[str, ModuleEntry] = {}
        for module in self.bundle.modules_in_order:
            for file_path in module.files:
                mapping[normalize_repo_path(file_path)] = module
        return mapping

    def _llm_metadata(self) -> dict[str, str]:
        return {
            "model": str(getattr(self.llm_client, "model", "unknown")),
            "base_url": str(getattr(self.llm_client, "base_url", "")),
        }

    def _write_file(self, relative_path: str, content: str) -> Path:
        target = self.project_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _dependency_headers(self, dependencies: list[str]) -> dict[str, str]:
        contents: dict[str, str] = {}
        seen: set[str] = set()

        def visit(dependency: str) -> None:
            dependency = normalize_repo_path(dependency)
            if not dependency or dependency in seen:
                return
            seen.add(dependency)
            path = self.project_dir / dependency
            if not path.exists():
                return
            text = path.read_text(encoding="utf-8")
            contents[dependency] = text
            for match in re.finditer(r'^\s*#\s*include\s+"([^"]+)"', text, flags=re.MULTILINE):
                visit(match.group(1))

        for dependency in dependencies:
            visit(dependency)
        return contents

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

    def _generate_with_usage(self, stage: str, call_type: str, subject: str, request: LLMRequest) -> LLMResponse:
        response = self.llm_client.generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage, got {type(response)!r}")
        self._register_usage(stage, call_type, subject, response.usage)
        return response

    def _module_for_spec(self, file_spec: FileSpec) -> ModuleEntry:
        module = self._module_by_file.get(file_spec.source_path) or self._module_by_file.get(file_spec.header_path)
        if module is None:
            raise RuntimeError(f"Unable to determine module for file spec {file_spec.trace_id}")
        return module

    def prepare_output_dir(self) -> None:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.project_dir = self.output_dir / _bundle_slug(self.bundle)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.logs = GenerationLogger(self.output_dir / "_agent_logs")
        self.workflow_usage = LLMUsage(0, 0, 0)
        self.llm_call_usage = []
        self.stage_token_usage = {}

    def generate(self) -> GenerationResult:
        self.llm_client.ensure_ready()
        self.prepare_output_dir()
        validate_rendered_headers_compile(self.bundle, self.logs.root / "rendered_header_checks")
        if self.bundle.has_errors():
            manifest = {
                **self._llm_metadata(),
                "llm_client": "agent.coder.llm_client.chat_with_llm",
                "output_dir": str(self.output_dir),
                "project_dir": str(self.project_dir),
                "log_dir": str(self.logs.root),
                "binary_name": _bundle_binary_name(self.bundle),
                "max_repair_rounds": self.max_repair_rounds,
                "skip_repair": self.skip_repair,
                "generation_order": self.bundle.generation_order,
                "modules": [module.name for module in self.bundle.modules_in_order],
                "repaired_files": [],
                "generation_success": False,
                "compile_run": False,
                "compile_success": False,
                "verification_run": False,
                "verification_success": None,
                "verification": {"scenarios": [], "diagnostics": []},
                "repair": {
                    "stop_reason": "spec_validation_failed",
                    "rounds_attempted": 0,
                    "blocking_files": [],
                    "rejected_candidates": [],
                },
                "llm_call_usage": [],
                "stage_token_usage": {},
                "workflow_token_usage": _usage_to_dict(LLMUsage(0, 0, 0)),
                "diagnostics": [diag.__dict__ for diag in self.bundle.diagnostics],
            }
            manifest_path = self.logs.write_named("run_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            return GenerationResult(
                success=False,
                manifest_path=manifest_path,
                compile_stdout="",
                compile_stderr="",
                repaired_files=[],
                repair_stop_reason="spec_validation_failed",
                repair_blocking_files=[],
            )

        generated_headers: dict[str, str] = {}
        source_integrity_records: list[dict[str, Any]] = []
        integrity_retry_used = False
        for module in self.bundle.modules_in_order:
            print(f"[agent.generate] module={module.name} start", flush=True)
            header_paths = [path for path in module.files if path.endswith(".h")]
            source_paths = [path for path in module.files if path.endswith(".c")]

            for header_path in header_paths:
                file_spec = self.bundle.file_specs_by_header_path.get(header_path)
                if file_spec is None:
                    continue
                print(f"[agent.generate] header={header_path} start", flush=True)
                header_content = render_header(self.bundle, file_spec)
                self._write_file(header_path, header_content)
                generated_headers[header_path] = header_content
                print(f"[agent.generate] header={header_path} done", flush=True)

            for source_path in source_paths:
                file_spec = self.bundle.file_specs_by_source_path.get(source_path)
                if file_spec is None:
                    continue
                print(f"[agent.generate] source={source_path} start", flush=True)
                module_entry = self._module_for_spec(file_spec)
                function_specs = function_specs_for_file(self.bundle, file_spec)
                header_content = ""
                if file_spec.header_path:
                    header_content = generated_headers.get(file_spec.header_path, "")
                    if not header_content:
                        print(f"[agent.generate] source={source_path} header_missing_generate={file_spec.header_path}", flush=True)
                        header_content = render_header(self.bundle, file_spec)
                        self._write_file(file_spec.header_path, header_content)
                        generated_headers[file_spec.header_path] = header_content
                        print(f"[agent.generate] source={source_path} header_generated={file_spec.header_path}", flush=True)
                dependency_headers = self._dependency_headers(file_spec.source_dependencies)
                print(
                    f"[agent.generate] source={source_path} prompt_build functions={len(function_specs)} deps={len(dependency_headers)}",
                    flush=True,
                )
                prompt_builder = (
                    self.main_source_prompt_builder
                    if Path(source_path).name == "main.c"
                    else self.source_prompt_builder
                )
                messages = prompt_builder(
                    self.bundle,
                    module_entry,
                    file_spec,
                    function_specs,
                    header_content,
                    dependency_headers,
                )
                if self.prompt_observer is not None:
                    self.prompt_observer("source_generation", source_path, messages)
                print(f"[agent.generate] source={source_path} prompt_built messages={len(messages)}", flush=True)
                self.logs.write(f"prompt_{source_path}", json.dumps(messages, ensure_ascii=False, indent=2))
                print(f"[agent.generate] source={source_path} prompt_logged", flush=True)
                print(f"[agent.generate] source={source_path} llm_request_start", flush=True)
                response = self._generate_with_usage(
                    "source_generation",
                    "generate",
                    source_path,
                    LLMRequest(messages=messages, top_p=0.2, temperature=0.2, is_stream=True),
                )
                print(
                    f"[agent.generate] source={source_path} llm_response_done chars={len(response.content)} tokens={response.usage.total_tokens}",
                    flush=True,
                )
                content = _strip_fences(response.content)
                issues = _source_integrity_issues(content, source_path, function_specs)
                retried = False
                if issues and not integrity_retry_used:
                    integrity_retry_used = True
                    retried = True
                    self.logs.write(f"source_integrity_first_draft_{source_path}", content)
                    correction_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "Regenerate the complete source file. Fix these source-integrity violations "
                                "without changing specified signatures: "
                                + json.dumps(issues, ensure_ascii=False)
                            ),
                        },
                    ]
                    if self.prompt_observer is not None:
                        self.prompt_observer("source_integrity_regeneration", source_path, correction_messages)
                    retry = self._generate_with_usage(
                        "source_integrity_regeneration",
                        "regenerate",
                        source_path,
                        LLMRequest(messages=correction_messages, top_p=0.2, temperature=0.2, is_stream=True),
                    )
                    content = _strip_fences(retry.content)
                    issues = _source_integrity_issues(content, source_path, function_specs)
                source_integrity_records.append(
                    {"source_path": source_path, "retried": retried, "issues": issues}
                )
                self._write_file(source_path, content)
                print(f"[agent.generate] source={source_path} file_written", flush=True)

        makefile_content = render_makefile(self.bundle)
        print("[agent.generate] write=Makefile", flush=True)
        self._write_file("Makefile", makefile_content)

        repaired_files: list[str] = []
        binary_name = _bundle_binary_name(self.bundle)
        owner_by_function = {
            interface.name: file_spec.source_path
            for file_spec in self.bundle.file_specs_by_trace.values()
            for interface in file_spec.source_interfaces
            if interface.trace_id in self.bundle.function_specs_by_trace
        }
        for source_path in sorted(self.project_dir.rglob("*.c")):
            relative = normalize_repo_path(source_path.relative_to(self.project_dir).as_posix())
            content = source_path.read_text(encoding="utf-8")
            for function_name, owner in owner_by_function.items():
                if relative != owner and _function_definition_count(content, function_name):
                    source_integrity_records.append(
                        {
                            "source_path": relative,
                            "retried": False,
                            "issues": [{
                                "code": "specified_function_wrong_source",
                                "function": function_name,
                                "owner_source": owner,
                            }],
                        }
                    )
        source_integrity_passed = not any(item["issues"] for item in source_integrity_records)
        if not source_integrity_passed:
            print("[agent.generate] compile=blocked_by_source_integrity", flush=True)
            repair_outcome = RepairOutcome(None, "source_integrity_failed", 0, [], [])
        elif self.skip_repair:
            print("[agent.generate] compile=skipped", flush=True)
            repair_outcome = RepairOutcome(None, "skipped_by_cli", 0, [], [])
        else:
            print("[agent.generate] compile=initial", flush=True)
            repair_outcome = self._repair_until_compiles(repaired_files, binary_name)
        compile_result = repair_outcome.compile_result
        compile_run = compile_result is not None
        success = source_integrity_passed and (not compile_run or compile_result.returncode == 0)
        verification_run = False
        verification_success: bool | None = None
        verification_scenarios: list[dict[str, str]] = []
        verification_diagnostics: list[dict[str, Any]] = []
        if compile_result is not None and compile_result.returncode == 0:
            from .verifier import ProjectVerifier

            behavior_result = ProjectVerifier(self.bundle, self.output_dir).verify_behavior()
            verification_run = True
            verification_success = behavior_result.ok
            verification_scenarios = behavior_result.scenarios
            verification_diagnostics = [diag.__dict__ for diag in behavior_result.diagnostics]
            for scenario in verification_scenarios:
                print(
                    f"[agent.generate] behavior={scenario['name']} status={scenario['status']} detail={scenario['detail']}",
                    flush=True,
                )
            self.logs.write_named(
                "behavior_verification.json",
                json.dumps(
                    {
                        "success": verification_success,
                        "scenarios": verification_scenarios,
                        "diagnostics": verification_diagnostics,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        manifest = {
            **self._llm_metadata(),
            "llm_client": "agent.coder.llm_client.chat_with_llm",
            "output_dir": str(self.output_dir),
            "project_dir": str(self.project_dir),
            "log_dir": str(self.logs.root),
            "binary_name": binary_name,
            "max_repair_rounds": self.max_repair_rounds,
            "skip_repair": self.skip_repair,
            "generation_order": self.bundle.generation_order,
            "modules": [module.name for module in self.bundle.modules_in_order],
            "repaired_files": repaired_files,
            "generation_success": success,
            "source_integrity": {
                "passed": source_integrity_passed,
                "retry_used": integrity_retry_used,
                "records": source_integrity_records,
            },
            "compile_run": compile_run,
            "compile_success": compile_result.returncode == 0 if compile_result is not None else None,
            "verification_run": verification_run,
            "verification_success": verification_success,
            "verification": {
                "scenarios": verification_scenarios,
                "diagnostics": verification_diagnostics,
            },
            "repair": {
                "stop_reason": repair_outcome.stop_reason,
                "rounds_attempted": repair_outcome.rounds_attempted,
                "blocking_files": repair_outcome.blocking_files,
                "rejected_candidates": repair_outcome.rejected_candidates,
                "fingerprint_history": repair_outcome.fingerprint_history,
            },
            "llm_call_usage": self.llm_call_usage,
            "stage_token_usage": self.stage_token_usage,
            "workflow_token_usage": _usage_to_dict(self.workflow_usage),
            "diagnostics": [diag.__dict__ for diag in self.bundle.diagnostics],
        }
        manifest_path = self.logs.write_named("run_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return GenerationResult(
            success=success,
            manifest_path=manifest_path,
            compile_stdout=compile_result.stdout if compile_result is not None else "",
            compile_stderr=compile_result.stderr if compile_result is not None else "",
            repaired_files=repaired_files,
            repair_stop_reason=repair_outcome.stop_reason,
            repair_blocking_files=repair_outcome.blocking_files,
        )

    def repair_existing(self, project_dir: str | Path) -> GenerationResult:
        source_project_dir = Path(project_dir).resolve()
        if not source_project_dir.is_dir():
            raise FileNotFoundError(f"Existing protocol project not found: {source_project_dir}")
        if self.bundle.has_errors():
            raise RuntimeError("Repair aborted because spec validation reported errors")

        self.llm_client.ensure_ready()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        repair_output_base = source_project_dir.parent / f"{source_project_dir.name}_repair_{timestamp}"
        self.output_dir = repair_output_base
        suffix = 2
        while self.output_dir.exists():
            self.output_dir = Path(f"{repair_output_base}_{suffix}")
            suffix += 1
        self.project_dir = self.output_dir / source_project_dir.name
        self.output_dir.mkdir(parents=True)
        shutil.copytree(source_project_dir, self.project_dir)
        self.logs = GenerationLogger(self.output_dir / "_agent_logs")
        self.workflow_usage = LLMUsage(0, 0, 0)
        self.llm_call_usage = []
        self.stage_token_usage = {}

        repaired_files: list[str] = []
        binary_name = _bundle_binary_name(self.bundle)
        print(f"[agent.repair] project={self.project_dir} compile=initial", flush=True)
        outcome = self._repair_until_compiles(repaired_files, binary_name)
        compile_result = outcome.compile_result
        if compile_result is None:
            raise RuntimeError("Repair finished without a compile result")
        success = compile_result.returncode == 0
        manifest = {
            "mode": "repair_existing",
            **self._llm_metadata(),
            "spec_root": str(self.bundle.spec_root),
            "source_project_dir": str(source_project_dir),
            "project_dir": str(self.project_dir),
            "log_dir": str(self.logs.root),
            "binary_name": binary_name,
            "max_repair_rounds": self.max_repair_rounds,
            "repaired_files": repaired_files,
            "compile_success": success,
            "repair": {
                "stop_reason": outcome.stop_reason,
                "rounds_attempted": outcome.rounds_attempted,
                "blocking_files": outcome.blocking_files,
                "rejected_candidates": outcome.rejected_candidates,
                "fingerprint_history": outcome.fingerprint_history,
            },
            "llm_call_usage": self.llm_call_usage,
            "stage_token_usage": self.stage_token_usage,
            "workflow_token_usage": _usage_to_dict(self.workflow_usage),
            "diagnostics": [diag.__dict__ for diag in self.bundle.diagnostics],
        }
        manifest_path = self.logs.write(
            "repair_manifest", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", ".json"
        )
        return GenerationResult(
            success=success,
            manifest_path=manifest_path,
            compile_stdout=compile_result.stdout,
            compile_stderr=compile_result.stderr,
            repaired_files=repaired_files,
            repair_stop_reason=outcome.stop_reason,
            repair_blocking_files=outcome.blocking_files,
        )

    def _repair_until_compiles(self, repaired_files: list[str], binary_name: str) -> RepairOutcome:
        last_result = _compile_project(self.project_dir, binary_name)
        self.logs.write("compile_stdout_0", last_result.stdout)
        self.logs.write("compile_stderr_0", last_result.stderr)
        if last_result.returncode == 0:
            return RepairOutcome(last_result, "compile_succeeded", 0, [], [])

        rejected_candidates: list[dict[str, str]] = []
        fingerprint_history: list[dict[str, Any]] = []

        for round_idx in range(1, self.max_repair_rounds + 1):
            repairable_sources, blocking_headers = _classify_repair_targets(
                last_result.stdout,
                last_result.stderr,
                self.project_dir,
                self.bundle,
            )
            if blocking_headers:
                print(f"[agent.repair] blocked=headers files={','.join(blocking_headers)}", flush=True)
                return RepairOutcome(last_result, "deterministic_header_compile_error", round_idx - 1, blocking_headers, rejected_candidates, fingerprint_history)
            if not repairable_sources:
                reason = (
                    "unmapped_linker_diagnostics"
                    if _linker_error_roots(last_result.stdout, last_result.stderr)
                    else "no_repairable_sources"
                )
                return RepairOutcome(last_result, reason, round_idx - 1, [], rejected_candidates, fingerprint_history)
            unknown_symbols = _unknown_external_symbols(last_result.stdout, last_result.stderr, self.bundle)
            if unknown_symbols:
                blocking = sorted(unknown_symbols)
                rejected_candidates.extend(
                    {"path": "", "reason": f"spec_contract_gap:{symbol}"}
                    for symbol in blocking
                )
                return RepairOutcome(
                    last_result,
                    "spec_contract_blocked",
                    round_idx - 1,
                    blocking,
                    rejected_candidates,
                    fingerprint_history,
                )

            print(f"[agent.repair] round={round_idx} files={','.join(repairable_sources)}", flush=True)
            changed = False
            original_contents: dict[str, str] = {}
            for relative_path in repairable_sources:
                file_spec = self.bundle.file_specs_by_source_path[relative_path]
                print(f"[agent.repair] file={relative_path} start", flush=True)
                module = self._module_for_spec(file_spec)
                current_path = self.project_dir / relative_path
                if not current_path.exists():
                    print(f"[agent.repair] file={relative_path} missing_skip", flush=True)
                    continue
                current_content = current_path.read_text(encoding="utf-8")
                dependency_headers = self._dependency_headers(file_spec.source_dependencies or file_spec.header_dependencies)
                canonical_header = ""
                if file_spec.header_path:
                    header_path = self.project_dir / file_spec.header_path
                    if header_path.exists():
                        canonical_header = header_path.read_text(encoding="utf-8")
                compact_errors = _compact_compile_diagnostics(
                    last_result.stdout,
                    last_result.stderr,
                    relative_path,
                    self.project_dir,
                )
                print(
                    f"[agent.repair] file={relative_path} prompt_build deps={len(dependency_headers)}",
                    flush=True,
                )
                messages = self.repair_prompt_builder(
                    self.bundle,
                    module,
                    file_spec,
                    relative_path,
                    canonical_header,
                    current_content,
                    compact_errors,
                    dependency_headers,
                )
                if self.prompt_observer is not None:
                    self.prompt_observer("repair", relative_path, messages)
                request_size = _messages_size_bytes(messages)
                if request_size > MAX_REPAIR_REQUEST_BYTES:
                    self.logs.write(
                        f"repair_prompt_rejected_size_{round_idx}_{relative_path}",
                        json.dumps(
                            {
                                "target_path": relative_path,
                                "request_bytes": request_size,
                                "max_request_bytes": MAX_REPAIR_REQUEST_BYTES,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    return RepairOutcome(last_result, "repair_request_too_large", round_idx - 1, [], rejected_candidates, fingerprint_history)
                print(f"[agent.repair] file={relative_path} prompt_built messages={len(messages)}", flush=True)
                self.logs.write(f"repair_prompt_{round_idx}_{relative_path}", json.dumps(messages, ensure_ascii=False, indent=2))
                print(f"[agent.repair] file={relative_path} prompt_logged", flush=True)
                print(f"[agent.repair] file={relative_path} llm_request_start", flush=True)
                try:
                    response = self._generate_with_usage(
                        "repair",
                        f"repair_round_{round_idx}",
                        relative_path,
                        LLMRequest(messages=messages, top_p=0.2, temperature=0.2, is_stream=True),
                    )
                except Exception as exc:  # noqa: BLE001
                    self.logs.write(
                        f"repair_llm_error_{round_idx}_{relative_path}",
                        f"{type(exc).__name__}: {exc}\n",
                    )
                    return RepairOutcome(last_result, "repair_llm_error", round_idx - 1, [], rejected_candidates, fingerprint_history)
                print(
                    f"[agent.repair] file={relative_path} llm_response_done chars={len(response.content)} tokens={response.usage.total_tokens}",
                    flush=True,
                )
                candidate = _strip_fences(response.content)
                candidate_ok, reject_reason = _validate_repair_candidate(relative_path, current_content, candidate)
                if not candidate_ok:
                    rejected_candidates.append({"path": relative_path, "reason": reject_reason})
                    print(f"[agent.repair] file={relative_path} rejected={reject_reason}", flush=True)
                    continue
                original_contents[relative_path] = current_content
                current_path.write_text(candidate, encoding="utf-8")
                print(f"[agent.repair] file={relative_path} file_written", flush=True)
                changed = True
            if not changed:
                return RepairOutcome(last_result, "no_repair_progress", round_idx, [], rejected_candidates, fingerprint_history)
            previous_result = last_result
            last_result = _compile_project(self.project_dir, binary_name)
            print(f"[agent.repair] compile=round_{round_idx} returncode={last_result.returncode}", flush=True)
            self.logs.write(f"compile_stdout_{round_idx}", last_result.stdout)
            self.logs.write(f"compile_stderr_{round_idx}", last_result.stderr)
            previous_fingerprints = _compile_error_fingerprints(
                previous_result.stdout, previous_result.stderr, self.project_dir
            )
            current_fingerprints = _compile_error_fingerprints(
                last_result.stdout, last_result.stderr, self.project_dir
            )
            accepted = last_result.returncode == 0 or current_fingerprints < previous_fingerprints
            fingerprint_history.append(
                {
                    "round": round_idx,
                    "before": sorted(previous_fingerprints),
                    "after": sorted(current_fingerprints),
                    "accepted": accepted,
                }
            )
            if not accepted:
                added_roots = current_fingerprints - previous_fingerprints
                reason = (
                    "new_compile_roots:" + ",".join(sorted(added_roots))
                    if added_roots
                    else "compile_roots_not_reduced"
                )
                for relative_path, original_content in original_contents.items():
                    (self.project_dir / relative_path).write_text(original_content, encoding="utf-8")
                    rejected_candidates.append({"path": relative_path, "reason": reason})
                self.logs.write(
                    f"repair_candidate_rejected_{round_idx}",
                    json.dumps({"reason": reason, "files": sorted(original_contents)}, indent=2),
                    ".json",
                )
                print(f"[agent.repair] round={round_idx} rejected={reason}", flush=True)
                return RepairOutcome(
                    previous_result,
                    "repair_stagnated",
                    round_idx,
                    [],
                    rejected_candidates,
                    fingerprint_history,
                )
            for relative_path in original_contents:
                if relative_path not in repaired_files:
                    repaired_files.append(relative_path)
            if last_result.returncode == 0:
                return RepairOutcome(last_result, "compile_succeeded", round_idx, [], rejected_candidates, fingerprint_history)
        return RepairOutcome(last_result, "max_rounds_exhausted", self.max_repair_rounds, [], rejected_candidates, fingerprint_history)
