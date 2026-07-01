from __future__ import annotations

import json
from typing import Any


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_nl_plan_messages(
    *,
    protocol: str,
    allowed_inputs: dict[str, Any],
) -> list[dict[str, str]]:
    content = f"""Create a natural-language engineering plan for a minimum {protocol} implementation.

Allowed inputs:
{_json(allowed_inputs)}

The plan must describe protocol role, modules, data structures, function responsibilities, parsing, state handling, error handling, runtime lifecycle, and tests. Keep it implementation-oriented and do not reference external repositories or local file paths.
"""
    return [
        {"role": "system", "content": "You are an implementation planning assistant for portable C network protocol projects."},
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

Allowed inputs:
{_json(allowed_inputs)}
{plan_section}
Return JSON only with this shape:
{{
  "modules": [{{"name": "...", "purpose": "...", "depends_on": ["..."]}}],
  "files": [
    {{"path": "main.c", "kind": "source", "purpose": "...", "interfaces": ["..."]}},
    {{"path": "module/name.h", "kind": "header", "purpose": "...", "interfaces": ["..."]}},
    {{"path": "module/name.c", "kind": "source", "purpose": "...", "interfaces": ["..."]}}
  ],
  "entrypoint": {{"binary": "{binary_name}", "argv": "{argv_contract}"}},
  "build": {{"language": "C11", "target": "{binary_name}"}}
}}

Rules:
- The strategy is only a work plan for source files.
- Include main.c and enough .h/.c files for a multi-file project.
- Use relative paths only.
- Do not include Markdown.
"""
    return [
        {"role": "system", "content": "You design small portable C project work plans from allowed experiment inputs."},
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
    plan_section = f"\nNatural-language plan:\n{nl_plan}\n" if nl_plan else ""
    content = f"""Generate file `{file_item['path']}` for this minimum {protocol} C project.

Allowed inputs:
{_json(allowed_inputs)}
{plan_section}
Project strategy:
{_json(strategy)}

File to generate:
{_json(file_item)}

Headers already generated:
{header_context}

Requirements:
- Output only the complete contents of `{file_item['path']}`.
- Use portable C11 and Linux/POSIX networking APIs as needed.
- Include all required headers; implicit function declarations are forbidden.
- Match the binary and argv contract: {argv_contract}.
- Keep behavior aligned with the minimum requirements.
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

Allowed inputs:
{_json(allowed_inputs)}

Project headers:
{_json(project_headers)}

Compiler errors:
{compile_errors}

Current file content:
{current_content}

Output only the full corrected contents of `{target_path}`. Preserve the runtime contract: {argv_contract}.
"""
    return [
        {"role": "system", "content": "You repair C source files using compiler diagnostics and project headers."},
        {"role": "user", "content": content},
    ]

