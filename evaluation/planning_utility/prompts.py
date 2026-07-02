from __future__ import annotations

import json
from typing import Any


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[truncated]"


def _semantic_view(allowed_inputs: dict[str, Any]) -> dict[str, Any]:
    facts_view = allowed_inputs.get("facts_view", {})
    if isinstance(facts_view, dict):
        facts_view = {key: value for key, value in facts_view.items() if key != "minimum_v1"}
    return {
        "facts_view": facts_view,
        "target_profile": allowed_inputs.get("target_profile", {}),
        "minimum_requirements": allowed_inputs.get("minimum_requirements", {}),
        "runtime_contract": allowed_inputs.get("runtime_contract", {}),
    }


def _implementation_view(allowed_inputs: dict[str, Any]) -> dict[str, Any]:
    facts_view = allowed_inputs.get("facts_view", {})
    protocol_meta = facts_view.get("protocol_meta", {}) if isinstance(facts_view, dict) else {}
    schema_version = facts_view.get("schema_version") if isinstance(facts_view, dict) else None
    view: dict[str, Any] = {
        "protocol_meta": protocol_meta,
        "target_profile": allowed_inputs.get("target_profile", {}),
        "minimum_requirements": allowed_inputs.get("minimum_requirements", {}),
        "runtime_contract": allowed_inputs.get("runtime_contract", {}),
    }
    if schema_version:
        view["facts_schema_version"] = schema_version
    return view


def _compact_file_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("path", "kind", "purpose", "interfaces", "depends_on_files", "implementation_notes", "owns")
    return {key: item[key] for key in keys if key in item}


def _file_generation_view(
    allowed_inputs: dict[str, Any],
    strategy: dict[str, Any],
    file_item: dict[str, Any],
) -> dict[str, Any]:
    files = [item for item in strategy.get("files", []) if isinstance(item, dict)]
    dependency_paths = set(file_item.get("depends_on_files") or [])
    return {
        "inputs": _implementation_view(allowed_inputs),
        "entrypoint": strategy.get("entrypoint", {}),
        "build": strategy.get("build", {}),
        "global_contract": strategy.get("global_contract", {}),
        "protocol_rules": strategy.get("protocol_rules", []),
        "shared_ownership": strategy.get("shared_ownership", []),
        "project_files": [_compact_file_item(item) for item in files],
        "target_file": _compact_file_item(file_item),
        "direct_dependencies": [
            _compact_file_item(item) for item in files if str(item.get("path", "")) in dependency_paths
        ],
    }


def _nl_plan_brief(nl_plan: str) -> str:
    lines = nl_plan.strip().splitlines()
    if not lines:
        return ""
    start = None
    for index, line in enumerate(lines):
        normalized = line.strip().lower().replace("-", " ")
        if "code generation brief" in normalized:
            start = index
            break
    if start is None:
        return _clip("\n".join(lines[:60]), 2400)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].lstrip().startswith("#"):
            end = index
            break
    return _clip("\n".join(lines[start:end]), 2400)


def _header_context(header_context: str) -> str:
    stripped = header_context.strip()
    if not stripped:
        return "{}"
    try:
        return _json(json.loads(stripped))
    except json.JSONDecodeError:
        return _clip(stripped, 120000)


def build_nl_plan_messages(
    *,
    protocol: str,
    allowed_inputs: dict[str, Any],
) -> list[dict[str, str]]:
    content = f"""Create a natural-language engineering plan for a minimum {protocol} implementation.

Allowed semantic inputs:
{_json(_semantic_view(allowed_inputs))}

The plan must describe protocol role, modules, data structures, function responsibilities, parsing, state handling, error handling, runtime lifecycle, and tests. Keep it implementation-oriented and do not reference external repositories or local file paths.

End with a section titled `Code-generation brief` containing at most 20 concise bullets that a file-by-file C generator can use without reading the full plan.
"""
    return [
        {"role": "system", "content": "You are an implementation planning assistant for portable C network protocol projects."},
        {"role": "user", "content": content},
    ]


def build_source_tree_skeleton_messages(
    *,
    protocol: str,
    allowed_inputs: dict[str, Any],
) -> list[dict[str, str]]:
    content = f"""Create only a source tree skeleton for a minimum {protocol} C implementation.

Compact allowed inputs:
{_json(_implementation_view(allowed_inputs))}

Return JSON only with exactly this shape:
{{"files":[{{"path":"module/name.h","kind":"header","order":0}},{{"path":"module/name.c","kind":"source","order":1}},{{"path":"main.c","kind":"main","order":2}}]}}

Rules:
- Output source organization metadata only: path, kind, order.
- Use kind=header for .h files, kind=source for non-main .c files, and kind=main for main.c.
- Every non-main .h file must have a same-stem .c file, and every non-main .c file must have a same-stem .h file.
- Include main.c.
- Do not include Makefile.
- Do not include role, description, purpose, modules, functions, types, dependencies, interfaces, behavior, architecture, ownership, contracts, notes, or any planning content.
- Do not include Markdown.
"""
    return [
        {"role": "system", "content": "You list C source tree files without producing any engineering plan."},
        {"role": "user", "content": content},
    ]


def build_strategy_messages(
    *,
    protocol: str,
    binary_name: str,
    argv_contract: str,
    allowed_inputs: dict[str, Any],
    nl_plan: str,
) -> list[dict[str, str]]:
    plan_section = f"\nNatural-language plan:\n{nl_plan}\n" if nl_plan else ""
    content = f"""Create a lightweight project strategy for a minimum {protocol} C project.

Allowed semantic inputs:
{_json(_semantic_view(allowed_inputs))}
{plan_section}
Return JSON only with this minimum shape. You may add only lightweight project-planning fields useful for file generation:
{{
  "modules": [{{"name": "...", "purpose": "...", "depends_on": ["..."]}}],
  "files": [
    {{"path": "main.c", "kind": "source", "purpose": "...", "interfaces": ["..."], "depends_on_files": ["..."], "implementation_notes": ["..."]}},
    {{"path": "module/name.h", "kind": "header", "purpose": "...", "interfaces": ["..."], "depends_on_files": ["..."], "implementation_notes": ["..."]}},
    {{"path": "module/name.c", "kind": "source", "purpose": "...", "interfaces": ["..."], "depends_on_files": ["..."], "implementation_notes": ["..."]}}
  ],
  "entrypoint": {{"binary": "{binary_name}", "argv": "{argv_contract}"}},
  "build": {{"language": "C11", "target": "{binary_name}"}},
  "global_contract": {{"runtime": "...", "argv": "{argv_contract}", "transport": "..."}},
  "protocol_rules": ["..."],
  "shared_ownership": [{{"symbol": "...", "owner": "module/name.h", "included_by": ["..."]}}]
}}

Rules:
- The strategy is only a work plan for source files.
- Include main.c and enough .h/.c files for a multi-file project.
- Use relative paths only.
- Make this JSON self-contained for later code generation: include protocol constants, state invariants, parser/encoder rules, and ownership of shared structs/enums/constants.
- Assign every shared enum, struct, typedef, macro, and constant to exactly one owner header; other files must include that header instead of redefining it.
- Do not include Markdown.
"""
    return [
        {"role": "system", "content": "You design small portable C project work plans from allowed experiment inputs."},
        {"role": "user", "content": content},
    ]


def build_pair_completion_messages(
    *,
    protocol: str,
    argv_contract: str,
    allowed_inputs: dict[str, Any],
    skeleton_paths: list[str],
    unit: dict[str, Any],
    generated_files: list[str],
    header_declarations: dict[str, str],
) -> list[dict[str, str]]:
    context = {
        "inputs": _implementation_view(allowed_inputs),
        "runtime_contract": {"argv": argv_contract},
        "source_tree_skeleton_paths": skeleton_paths,
        "generation_unit": unit,
        "generated_files": generated_files,
        "existing_header_public_declarations": header_declarations,
    }
    content = f"""Complete the requested C source generation unit for a minimum {protocol} implementation.

Compact implementation context:
{_json(context)}

Return JSON only with this exact envelope:
{{"files":[{{"path":"...","content":"complete file content"}}]}}

Rules:
- Return exactly the files listed in generation_unit.paths and no other files.
- Directly output the specified .h/.c/main.c contents inside the JSON content strings.
- Do not output a plan, design explanation, module responsibilities, interface list, function inventory, type inventory, call graph, include graph, or architecture notes.
- Reuse public declarations already present in existing_header_public_declarations.
- Do not redefine an existing public enum, struct, typedef, macro, or function prototype.
- Header files should contain include guards, required includes, public declarations, constants, and prototypes only.
- Source files should include their header when present and implement only the requested files.
- Use portable C11 and Linux/POSIX networking APIs as needed.
- Keep behavior aligned with the minimum requirements and runtime contract: {argv_contract}.
- Do not include Markdown fences or commentary outside the JSON.
"""
    return [
        {"role": "system", "content": "You complete C header/source files directly from compact protocol inputs and existing declarations."},
        {"role": "user", "content": content},
    ]


def build_file_messages(
    *,
    protocol: str,
    argv_contract: str,
    allowed_inputs: dict[str, Any],
    strategy: dict[str, Any],
    file_item: dict[str, Any],
    header_context: str,
    nl_plan: str,
) -> list[dict[str, str]]:
    plan_brief = _nl_plan_brief(nl_plan)
    plan_section = f"\nNL code-generation brief:\n{plan_brief}\n" if plan_brief else ""
    content = f"""Generate file `{file_item['path']}` for this minimum {protocol} C project.

Compact implementation context:
{_json(_file_generation_view(allowed_inputs, strategy, file_item))}
{plan_section}
Headers already generated. Treat these declarations as authoritative:
{_header_context(header_context)}

Requirements:
- Output only the complete contents of `{file_item['path']}`.
- Use portable C11 and Linux/POSIX networking APIs as needed.
- Include all required headers; implicit function declarations are forbidden.
- Match the binary and argv contract: {argv_contract}.
- Keep behavior aligned with the minimum requirements.
- Follow shared_ownership exactly: define each shared enum/type/constant only in its owner header; include that header everywhere else.
- Header files should contain only include guards, required includes, public typedefs/struct declarations, constants, and prototypes.
- Source files should implement only the target file responsibilities and must not duplicate public declarations from headers.
- Keep comments short and only where they clarify non-obvious protocol logic.
- Do not include Markdown fences or commentary.
"""
    return [
        {"role": "system", "content": "You generate production-quality portable C source and header files."},
        {"role": "user", "content": content},
    ]


def build_repair_messages(
    *,
    argv_contract: str,
    allowed_inputs: dict[str, Any],
    target_path: str,
    project_headers: dict[str, str],
    current_content: str,
    compile_errors: str,
) -> list[dict[str, str]]:
    content = f"""Repair source file `{target_path}` so the project compiles.

Compact implementation context:
{_json(_implementation_view(allowed_inputs))}

Project headers. Treat these declarations as authoritative:
{_json(project_headers)}

Compiler errors:
{compile_errors}

Current file content:
{current_content}

Output only the full corrected contents of `{target_path}`.
Preserve the runtime contract: {argv_contract}.
Do not redefine shared enum/type/constant declarations that already exist in project headers; include the owner header and adjust this source file only.
"""
    return [
        {"role": "system", "content": "You repair C source files using compiler diagnostics and project headers."},
        {"role": "user", "content": content},
    ]
