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


def build_nl_plan_messages(
    *,
    protocol: str,
    allowed_inputs: dict[str, Any],
) -> list[dict[str, str]]:
    content = f"""Create an implementation-oriented natural-language engineering plan for a minimum runnable {protocol} implementation.

Allowed semantic inputs:
{_json(_semantic_view(allowed_inputs))}

Your task is to plan a real C protocol implementation that can be generated, compiled, launched with the required runtime contract, and exercised by minimum behavior tests. The plan should translate the allowed protocol facts and target constraints into concrete engineering guidance for a pair-wise C header/source generator.

Focus on what the implementation must actually do:
- the protocol role and expected runtime behavior;
- the major source files or components needed to implement that role;
- the key runtime data that must be represented;
- how incoming bytes/messages are parsed and validated;
- how protocol state is initialized, updated, and released;
- how requests, commands, packets, or protocol events are handled;
- how responses or errors are produced;
- how resource ownership, buffers, sockets, sessions, files, timers, or queues should be managed when relevant;
- how the process starts, runs, handles failures, and exits;
- which minimum tests the generated implementation must satisfy.

Use only the allowed semantic inputs. Do not introduce unsupported protocol features. If a detail is missing but necessary for a runnable minimum implementation, state a minimal implementation assumption explicitly and keep it conservative.

The output must be a natural-language engineering plan, not a structured specification. Use prose, headings, and concise bullets. Do not emit JSON, YAML, Markdown tables, project_strategy.json, module inventories, function inventories, type ownership tables, dependency graphs, FILE_SPEC, FUNCTION_SPEC, or similar structured protocol specifications. Do not provide exact C function prototypes or complete struct/enum definitions.

Make the plan specific enough that a pair-wise C header/source generator can use it to produce consistent C files, but keep the guidance at the natural-language design level rather than as machine-readable specs.

End with a section titled `Code-generation brief` containing at most 20 concise bullets. These bullets should summarize the most important implementation decisions, shared concepts, runtime behavior, parsing rules, state rules, error handling rules, and minimum test obligations needed by the pair-wise C header/source generator.
"""
    return [
        {"role": "system", "content": "You are an implementation planning assistant for portable C network protocol projects."},
        {"role": "user", "content": content},
    ]


def build_source_tree_skeleton_messages(
    *,
    protocol: str,
    allowed_inputs: dict[str, Any],
    nl_plan: str = "",
) -> list[dict[str, str]]:
    plan_brief = _nl_plan_brief(nl_plan)
    plan_section = f"\nNatural-language plan brief:\n{plan_brief}\n" if plan_brief else ""
    content = f"""Create only a source tree skeleton for a minimum {protocol} C implementation.

Compact allowed inputs:
{_json(_implementation_view(allowed_inputs))}{plan_section}

Return JSON only with exactly this shape:
{{"files":[{{"path":"module/name.h","kind":"header","order":0}},{{"path":"module/name.c","kind":"source","order":1}},{{"path":"main.c","kind":"main","order":2}}]}}

Rules:
- Return one strict standard JSON object. The first character must be `{{` and the last character must be `}}`.
- Use double-quoted keys and strings with no Markdown fences, leading/trailing prose, comments, or trailing commas.
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


def build_pair_completion_messages(
    *,
    protocol: str,
    argv_contract: str,
    allowed_inputs: dict[str, Any],
    skeleton_paths: list[str],
    unit: dict[str, Any],
    generated_files: list[str],
    header_declarations: dict[str, str],
    nl_plan: str = "",
) -> list[dict[str, str]]:
    context = {
        "inputs": _implementation_view(allowed_inputs),
        "runtime_contract": {"argv": argv_contract},
        "source_tree_skeleton_paths": skeleton_paths,
        "generation_unit": unit,
        "generated_files": generated_files,
        "existing_header_public_declarations": header_declarations,
    }
    plan_brief = _nl_plan_brief(nl_plan)
    if plan_brief:
        context["nl_plan_brief"] = plan_brief
    response_shape = {
        "files": [
            {"path": path, "content": f"<complete contents of {path}>"}
            for path in unit.get("paths", [])
        ]
    }
    content = f"""Complete the requested C source generation unit for a minimum {protocol} implementation.

Compact implementation context:
{_json(context)}

Return JSON only with this exact path/order envelope, replacing each placeholder with complete source content:
{_json(response_shape)}

Rules:
- Return one strict standard JSON object. The first character must be `{{` and the last character must be `}}`.
- Use double-quoted keys and strings, escape every newline, quote, backslash, tab, and control character inside source content, and do not use trailing commas.
- Return exactly the files listed in generation_unit.paths and no other files.
- Directly output the specified .h/.c/main.c contents inside the JSON content strings.
- Do not output a plan, design explanation, module responsibilities, interface list, function inventory, type inventory, call graph, include graph, or architecture notes.
- Do not put design deliberation, uncertainty, interface-gap discussion, TODO text, or self-analysis inside generated source comments. Resolve ambiguity with the smallest implementation compatible with existing declarations.
- Finish every requested file and close every JSON string, array, and object before ending the response.
- Reuse public declarations already present in existing_header_public_declarations.
- Do not redefine an existing public enum, struct, typedef, macro, or function prototype.
- Header files should contain include guards, required includes, public declarations, constants, and prototypes only.
- Source files should include their header when present and implement only the requested files.
- Use portable C11 and Linux/POSIX networking APIs as needed.
- Keep behavior aligned with the minimum requirements and runtime contract: {argv_contract}.
- Do not include Markdown fences or commentary outside the JSON.
"""
    return [
        {"role": "system", "content": "You return strict JSON containing complete C files, without analysis or prose."},
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
