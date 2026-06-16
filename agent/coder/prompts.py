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
- Treat applicable TEST_VECTORS as hard behavioral requirements and reason through them before emitting code.
- Do not reference any external implementation or repository.
- Keep code portable to Linux with C11.
- Include the standard/POSIX headers required for every called function; implicit function declarations are forbidden.
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
        "MODULE_TEST_VECTORS": _module_test_vectors(bundle),
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


def _module_test_vectors(bundle: SpecBundle) -> list[Any]:
    try:
        raw = json.loads(bundle.module_spec_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return raw.get("TEST_VECTORS", [])


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


def _callee_return_info(bundle: SpecBundle, file_spec: FileSpec, function_specs: list[FunctionSpec]) -> str:
    """Extract return-value semantics for every function in RELY.FUNC across all
    function_specs.  This gives the LLM the information it needs to write correct
    caller-side return-value checks (e.g. ``ret < 0`` vs ``ret != 0``).
    """
    # Collect all callee names referenced by any function_spec
    callee_names: set[str] = set()
    for spec in function_specs:
        for func in spec.rely.get("FUNC", []):
            name = func.get("NAME", "")
            if name:
                callee_names.add(name)

    if not callee_names:
        return "- (none)"

    # Build name -> FunctionSpec lookup
    callee_specs_by_name: dict[str, FunctionSpec] = {}
    for spec in bundle.function_specs_by_trace.values():
        name = spec.signature.name
        if name:
            callee_specs_by_name[name] = spec

    lines: list[str] = []
    for callee_name in sorted(callee_names):
        callee_spec = callee_specs_by_name.get(callee_name)
        if callee_spec is None:
            continue

        parts: list[str] = [f"- {callee_spec.signature.raw}"]

        # Return type
        return_type = callee_spec.signature.return_type
        if return_type and return_type != "void":
            parts.append(f"  return type: {return_type}")

        # ROLE always provides useful context about the callee's behavior.
        role = callee_spec.role
        if role:
            parts.append(f"  role: {role}")

        # ACTION describes the callee's behavior, often including return-value
        # encodings (e.g. "EAGAIN returns -2, peer closed returns 0")
        action = callee_spec.body.get("ACTION", "")
        if action:
            parts.append(f"  behavior: {action}")

        # If the callee returns -2 for EAGAIN, data was buffered — caller
        # MUST still process the buffer after a -2 return.
        if action and "-2" in action and "EAGAIN" in action:
            parts.append("  IMPORTANT: -2 means data was read into the buffer but no more is available; "
                         "caller MUST proceed to pop lines from the buffer after a -2 return")

        # TEST_VECTORS: return_positive / return hints
        for tv in callee_spec.raw.get("TEST_VECTORS", []):
            if not isinstance(tv, dict):
                continue
            expect = tv.get("EXPECT", {})
            if not isinstance(expect, dict):
                continue
            if expect.get("return_positive") is True:
                parts.append("  on success: returns a positive value (>0) — caller MUST check ret < 0 for errors")
            if expect.get("return_negative") is True:
                parts.append("  on error: returns a negative value (<0)")
            if "return" in expect:
                tv_name = tv.get("NAME", "")
                tv_context = f" (test: {tv_name})" if tv_name else ""
                parts.append(f"  test vector{tv_context}: expects return={expect['return']}")

        # LOGIC.OUTPUT may describe contract
        output_desc = callee_spec.body.get("OUTPUT", "")
        if output_desc:
            parts.append(f"  output: {output_desc}")

        # If we have more than just the signature line, keep it
        if len(parts) > 1:
            lines.extend(parts)

    return "\n".join(lines) if lines else "- (no return-value contracts available for callees)"


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
    primary_include_requirement = (
        f"- Include `{file_spec.header_path}` as the primary include."
        if file_spec.header_path
        else "- This is a source-only file; include the headers listed in source dependencies as needed."
    )
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

Callee return-value contracts (how to interpret return values of functions you call):
{_callee_return_info(bundle, file_spec, function_specs)}

Private helpers/interfaces listed in the file spec without dedicated function specs:
{_private_interface_summary(file_spec, function_specs)}

Machine-readable constraints:
{_machine_constraints(bundle, file_spec, function_specs)}

Consistency rules:
{_json(bundle.consistency_rules)}

Relevant dependency headers:
{_json({key: value for key, value in dependency_headers.items() if key != file_spec.header_path})}

Requirements:
{primary_include_requirement}
- Match the canonical signatures exactly.
- Keep all public APIs compatible with the generated header.
- Use only the public struct fields and enum constants that exist in the canonical header.
- Use only the allowed access paths listed in the machine-readable constraints when touching public structs.
- Treat FORBIDDEN_SYMBOLS as hard errors; never emit those names or field paths.
- Satisfy every applicable MODULE_TEST_VECTORS, FILE_TEST_VECTORS, and function TEST_VECTORS entry.
- Include the standard/POSIX headers required for every called function; implicit function declarations are forbidden.
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
    target_path: str,
    canonical_header: str,
    current_content: str,
    compile_errors: str,
    dependency_headers: dict[str, str],
) -> list[dict[str, str]]:
    if target_path != file_spec.source_path or not target_path.endswith(".c"):
        raise ValueError(f"repair target must be the source path for a .c file: {target_path}")
    repair_function_specs = [
        spec
        for item in file_spec.source_interfaces
        if (spec := bundle.function_specs_by_trace.get(item.trace_id)) is not None
    ]
    content = f"""Repair the source file `{target_path}` so the project compiles.

Module:
- name: {module.name}
- role: {module.role}

File:
- trace_id: {file_spec.trace_id}
- role: {file_spec.role}
- source dependencies: {", ".join(file_spec.source_dependencies) or "none"}

Canonical header content:
{canonical_header}

Consistency rules:
{_json(bundle.consistency_rules)}

Machine-readable constraints:
{_machine_constraints(bundle, file_spec, repair_function_specs)}

Callee return-value contracts:
{_callee_return_info(bundle, file_spec, repair_function_specs)}

Dependency headers:
{_json({key: value for key, value in dependency_headers.items() if key != file_spec.header_path})}

Compiler errors:
{compile_errors}

Current file content:
{current_content}

Before changing code, prefer fixes that replace forbidden or nonexistent access paths with the allowed paths from Machine-readable constraints and Dependency headers. Preserve every applicable TEST_VECTORS behavior and include all headers required for called functions. Output the full corrected file contents only, with no Markdown fences.
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
