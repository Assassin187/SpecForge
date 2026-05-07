from __future__ import annotations

import json
from typing import Any

from .models import FileSpec, FunctionSpec, ModuleEntry, SpecBundle
from .specs import canonical_signature_for_source


SYSTEM_PROMPT = """You are generating production-quality C code from structured protocol specifications.

Rules:
- Output only the requested file contents, with no Markdown fences and no commentary.
- Follow the provided public interfaces exactly.
- Treat the provided canonical header content as the only source of truth for public structs, enums, typedefs, and function signatures.
- Never invent public struct fields, enum constants, or callback shapes that are not present in the canonical header.
- Respect the consistency rules strictly.
- Do not reference any external implementation or repository.
- Keep code portable to Linux with C11.
- You may add private helper functions, private structs, and local comments when needed.
- Never change public symbol names or function signatures.
"""


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _machine_constraints(bundle: SpecBundle, file_spec: FileSpec, function_specs: list[FunctionSpec] | None = None) -> str:
    function_specs = function_specs or []
    file_raw = file_spec.raw
    constraints: dict[str, Any] = {
        "MODULE_FORBIDDEN_SYMBOLS": bundle.module_spec_path and _module_forbidden_symbols(bundle),
        "FILE_PUBLIC_SYMBOLS": file_raw.get("PUBLIC_SYMBOLS", []),
        "FILE_ACCESS_PATHS": file_raw.get("ACCESS_PATHS", []),
        "FILE_FORBIDDEN_SYMBOLS": file_raw.get("FORBIDDEN_SYMBOLS", []),
        "FILE_CALL_CONTRACTS": file_raw.get("CALL_CONTRACTS", []),
        "FILE_TEST_VECTORS": file_raw.get("TEST_VECTORS", []),
        "FUNCTIONS": [],
    }
    for spec in function_specs:
        constraints["FUNCTIONS"].append(
            {
                "TRACE_ID": spec.trace_id,
                "ACCESS_PATHS": spec.raw.get("ACCESS_PATHS", []),
                "WIRE_MAPPING": spec.raw.get("WIRE_MAPPING", []),
                "CALL_CONTRACTS": spec.raw.get("CALL_CONTRACTS", []),
                "FORBIDDEN_SYMBOLS": spec.raw.get("FORBIDDEN_SYMBOLS", []),
                "TEST_VECTORS": spec.raw.get("TEST_VECTORS", []),
            }
        )
    return _json(constraints)


def _module_forbidden_symbols(bundle: SpecBundle) -> list[Any]:
    raw = {}
    try:
        raw = json.loads(bundle.module_spec_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return raw.get("FORBIDDEN_SYMBOLS", [])


def _function_summary(file_spec: FileSpec, function_specs: list[FunctionSpec], bundle: SpecBundle) -> str:
    lines: list[str] = []
    source_items = {item.trace_id: item for item in file_spec.source_interfaces}
    for spec in function_specs:
        source_item = source_items.get(spec.trace_id)
        signature = canonical_signature_for_source(bundle, source_item) if source_item else spec.signature.raw
        relied_funcs = ", ".join(func.get("NAME", "") for func in spec.rely.get("FUNC", []) if func.get("NAME")) or "none"
        if spec.function_type == "EVENT":
            action = spec.body.get("ACTION", "")
            invariants = spec.body.get("STATE_CHANGE", "")
        else:
            action = spec.body.get("ACTION", "")
            invariants = "; ".join(spec.body.get("INVARIANTS_USED", []))
        lines.append(
            "\n".join(
                [
                    f"- {signature}",
                    f"  role: {spec.role}",
                    f"  kind: {spec.function_type}",
                    f"  relies on: {relied_funcs}",
                    f"  behavior: {action}",
                    f"  notes: {invariants or 'none'}",
                ]
            )
        )
    return "\n".join(lines)


def _private_interface_summary(file_spec: FileSpec, function_specs: list[FunctionSpec]) -> str:
    covered = {spec.trace_id for spec in function_specs}
    lines = []
    for item in file_spec.source_interfaces:
        if item.trace_id in covered:
            continue
        lines.append(f"- {item.signature}: {item.role}")
    return "\n".join(lines) if lines else "- none"


def build_source_prompt(
    bundle: SpecBundle,
    module: ModuleEntry,
    file_spec: FileSpec,
    function_specs: list[FunctionSpec],
    generated_header: str,
    dependency_headers: dict[str, str],
) -> list[dict[str, str]]:
    content = f"""Generate the full C source file `{file_spec.source_path}`.

Module:
- name: {module.name}
- role: {module.role}
- dependencies: {", ".join(module.dependencies) or "none"}

File:
- trace_id: {file_spec.trace_id}
- role: {file_spec.role}
- source dependencies: {", ".join(file_spec.source_dependencies) or "none"}
- private data items: {", ".join(item.get("NAME", "") for item in file_spec.source_data) or "none"}

Canonical header content:
{generated_header}

Functions to implement:
{_function_summary(file_spec, function_specs, bundle)}

Private helpers/interfaces listed in the file spec without dedicated function specs:
{_private_interface_summary(file_spec, function_specs)}

Machine-readable constraints:
{_machine_constraints(bundle, file_spec, function_specs)}

Consistency rules:
{_json(bundle.consistency_rules)}

Relevant dependency headers:
{_json({key: value for key, value in dependency_headers.items() if key != file_spec.header_path})}

Requirements:
- Include `{file_spec.header_path}` as the primary include.
- Match the canonical signatures exactly.
- Keep all public APIs compatible with the generated header.
- Use only the public struct fields and enum constants that exist in the canonical header.
- Use only the allowed access paths listed in the machine-readable constraints when touching public structs.
- Treat FORBIDDEN_SYMBOLS as hard errors; never emit those names or field paths.
- Do not emit Markdown.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build_repair_prompt(
    bundle: SpecBundle,
    module: ModuleEntry,
    file_spec: FileSpec,
    current_content: str,
    compile_errors: str,
    dependency_headers: dict[str, str],
) -> list[dict[str, str]]:
    repair_function_specs = [
        spec
        for item in file_spec.source_interfaces
        if (spec := bundle.function_specs_by_trace.get(item.trace_id)) is not None
    ]
    content = f"""Repair the file `{file_spec.source_path}` so the project compiles.

Module:
- name: {module.name}
- role: {module.role}

File:
- trace_id: {file_spec.trace_id}
- role: {file_spec.role}
- source dependencies: {", ".join(file_spec.source_dependencies) or "none"}

Consistency rules:
{_json(bundle.consistency_rules)}

Machine-readable constraints:
{_machine_constraints(bundle, file_spec, repair_function_specs)}

Dependency headers:
{_json({key: value for key, value in dependency_headers.items() if key != file_spec.header_path})}

Compiler errors:
{compile_errors}

Current file content:
{current_content}

Before changing code, prefer fixes that replace forbidden or nonexistent access paths with the allowed paths from Machine-readable constraints and Dependency headers. Output the full corrected file contents only, with no Markdown fences.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
