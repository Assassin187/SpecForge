from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.coder.models import FileSpec, FunctionSpec, ModuleEntry, SpecBundle

from .view_specs import AblationViewContext, UnsupportedViewError


SYSTEM_PROMPT = """You are generating production-quality C code from a local SpecFS protocol view.

Rules:
- Output only the requested C file contents, with no Markdown fences and no commentary.
- Follow the provided public interfaces exactly.
- Treat the provided canonical header content as the source of truth for public structs, enums, typedefs, and function signatures.
- Do not invent public struct fields, enum constants, callback shapes, or public function signatures that are not present in the headers.
- Keep code portable to Linux with C11.
- Include the standard/POSIX headers required for every called function; implicit function declarations are forbidden.
- You may add private helper functions, private structs, and local comments when needed.
- Never change public symbol names or function signatures.
"""


@dataclass(frozen=True)
class ViewPromptBuilder:
    context: AblationViewContext

    def build_source_prompt(
        self,
        bundle: SpecBundle,
        module: ModuleEntry,
        file_spec: FileSpec,
        function_specs: list[FunctionSpec],
        generated_header: str,
        dependency_headers: dict[str, str],
    ) -> list[dict[str, str]]:
        if self.context.view_name == "s1":
            return build_s1_source_prompt(self.context, file_spec, generated_header)
        if self.context.view_name == "s2":
            return build_s2_source_prompt(self.context, file_spec, generated_header)
        raise UnsupportedViewError(f"unsupported ablation view '{self.context.view_name}'")

    def build_main_source_prompt(
        self,
        bundle: SpecBundle,
        module: ModuleEntry,
        file_spec: FileSpec,
        function_specs: list[FunctionSpec],
        generated_header: str,
        dependency_headers: dict[str, str],
    ) -> list[dict[str, str]]:
        return self.build_source_prompt(bundle, module, file_spec, function_specs, generated_header, dependency_headers)

    def build_repair_prompt(
        self,
        bundle: SpecBundle,
        module: ModuleEntry,
        file_spec: FileSpec,
        target_path: str,
        canonical_header: str,
        current_content: str,
        compile_errors: str,
        dependency_headers: dict[str, str],
    ) -> list[dict[str, str]]:
        if self.context.view_name == "s1":
            return build_s1_repair_prompt(
                self.context,
                file_spec,
                target_path,
                canonical_header,
                current_content,
                compile_errors,
            )
        if self.context.view_name == "s2":
            return build_s2_repair_prompt(
                self.context,
                file_spec,
                target_path,
                canonical_header,
                current_content,
                compile_errors,
            )
        raise UnsupportedViewError(f"unsupported ablation view '{self.context.view_name}'")


def _section(title: str, body: str) -> str:
    body = body.rstrip() or "(none)"
    return f"{title}\n{body}"


def _local_specfs_blocks(context: AblationViewContext, file_spec: FileSpec) -> str:
    blocks = context.function_blocks_for_source(file_spec.source_path)
    return "\n\n---\n\n".join(block.strip() for block in blocks)


def _local_headers(context: AblationViewContext, file_spec: FileSpec) -> str:
    headers = context.header_declarations_for_source(file_spec.source_path)
    if not headers:
        return "(none)"
    return "\n\n".join(f"### {path}\n```c\n{content.rstrip()}\n```" for path, content in headers.items())


def _graph_items_by_key(graph: dict[str, Any], collection: str, key: str) -> dict[str, dict[str, Any]]:
    return {
        str(item.get(key, "")): item
        for item in graph.get(collection, [])
        if isinstance(item, dict) and str(item.get(key, "")).strip()
    }


def _function_label(function: dict[str, Any]) -> str:
    name = str(function.get("function_name", "")).strip()
    trace_id = str(function.get("function_trace_id", "")).strip()
    linkage = str(function.get("linkage", "")).strip()
    suffix = f" [{linkage}]" if linkage else ""
    return f"{name}{suffix} <{trace_id}>" if trace_id else f"{name}{suffix}"


def _format_lines(lines: list[str]) -> str:
    return "\n".join(lines) if lines else "- None."


def _render_s2_project_graph(context: AblationViewContext, file_spec: FileSpec) -> str:
    graph = context.project_graph
    if graph is None:
        raise UnsupportedViewError("view 's2' requires a loaded project_graph artifact")

    functions_by_trace = _graph_items_by_key(graph, "functions", "function_trace_id")
    modules_by_name = _graph_items_by_key(graph, "modules", "module_name")

    current_file = next(
        (
            item
            for item in graph.get("files", [])
            if isinstance(item, dict)
            and (item.get("source_path") == file_spec.source_path or item.get("file_trace_id") == file_spec.trace_id)
        ),
        {},
    )
    if not current_file:
        raise UnsupportedViewError(f"S2 project graph has no entry for source file '{file_spec.source_path}'")

    current_position = next(
        (
            item
            for item in graph.get("source_positions", [])
            if isinstance(item, dict)
            and (item.get("source_path") == file_spec.source_path or item.get("file_trace_id") == file_spec.trace_id)
        ),
        {},
    )
    current_module = modules_by_name.get(str(current_file.get("module_name", "")), {})
    current_function_trace_ids = [str(item) for item in current_file.get("contains_function_trace_ids", [])]
    current_functions = [
        _function_label(functions_by_trace[trace_id])
        for trace_id in current_function_trace_ids
        if trace_id in functions_by_trace
    ]
    current_edges = [
        item
        for item in graph.get("file_dependency_edges", [])
        if isinstance(item, dict) and item.get("from_file_trace_id") == current_file.get("file_trace_id")
    ]

    module_order = [
        f"- {index}: {name}"
        for index, name in enumerate(graph.get("module_generation_order", []))
    ]
    module_edges = [
        f"- {edge.get('from_module', '')} -> {edge.get('to_module', '')}"
        for edge in graph.get("module_dependency_edges", [])
        if isinstance(edge, dict)
    ]
    file_lines = []
    for item in sorted(graph.get("files", []), key=lambda value: int(value.get("generation_order", 0)) if isinstance(value, dict) else 0):
        if not isinstance(item, dict):
            continue
        function_names = [
            str(functions_by_trace[trace_id].get("function_name", trace_id))
            for trace_id in item.get("contains_function_trace_ids", [])
            if trace_id in functions_by_trace
        ]
        file_lines.append(
            "- "
            f"{item.get('generation_order', '')}: {item.get('source_path', '')} "
            f"<{item.get('file_trace_id', '')}> module={item.get('module_name', '')} "
            f"header={item.get('header_path', '')} functions={', '.join(function_names) or 'none'}"
        )

    file_edges = [
        "- "
        f"{edge.get('from_file_trace_id', '')} --{edge.get('dependency_kind', '')}:{edge.get('dependency_path', '')}--> "
        f"{edge.get('to_file_trace_id', '') or 'external'}"
        for edge in graph.get("file_dependency_edges", [])
        if isinstance(edge, dict)
    ]

    function_lines: list[str] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for function in graph.get("functions", []):
        if not isinstance(function, dict):
            continue
        by_source.setdefault(str(function.get("source_path", "")), []).append(function)
    for source_path in sorted(by_source):
        labels = [
            _function_label(function)
            for function in sorted(by_source[source_path], key=lambda item: int(item.get("file_function_order", 0)))
        ]
        function_lines.append(f"- {source_path}: {', '.join(labels)}")

    current_edge_lines = [
        "- "
        f"{edge.get('dependency_kind', '')}:{edge.get('dependency_path', '')} -> "
        f"{edge.get('to_file_trace_id', '') or 'external'}"
        for edge in current_edges
    ]
    current_deps = [str(item) for item in current_file.get("dependency_file_trace_ids", [])]
    current_header_deps = [str(item) for item in current_file.get("header_dependency_paths", [])]
    current_source_deps = [str(item) for item in current_file.get("source_dependency_paths", [])]

    return "\n".join(
        [
            "Module Generation Order:",
            _format_lines(module_order),
            "",
            "Module Dependency Edges:",
            _format_lines(module_edges),
            "",
            "Files:",
            _format_lines(file_lines),
            "",
            "File Dependency Edges:",
            _format_lines(file_edges),
            "",
            "Functions:",
            _format_lines(function_lines),
            "",
            "Current Target Position:",
            f"- source_path: {current_file.get('source_path', '')}",
            f"- header_path: {current_file.get('header_path', '')}",
            f"- file_trace_id: {current_file.get('file_trace_id', '')}",
            f"- module_name: {current_file.get('module_name', '')}",
            f"- module_generation_order: {current_position.get('module_generation_order', current_module.get('generation_order', ''))}",
            f"- file_generation_order: {current_file.get('generation_order', '')}",
            f"- dependency_file_trace_ids: {', '.join(current_deps) or 'none'}",
            f"- header_dependency_paths: {', '.join(current_header_deps) or 'none'}",
            f"- source_dependency_paths: {', '.join(current_source_deps) or 'none'}",
            "- current file functions:",
            _format_lines([f"  - {item}" for item in current_functions]),
            "- current file dependency edges:",
            _format_lines([f"  {item}" for item in current_edge_lines]),
        ]
    )


def build_s1_source_prompt(
    context: AblationViewContext,
    file_spec: FileSpec,
    generated_header: str,
) -> list[dict[str, str]]:
    primary_include = (
        f'- Include "{file_spec.header_path}" as the primary project include.'
        if file_spec.header_path
        else "- This target has no paired public header; include the available project headers needed by the local SpecFS blocks."
    )
    content = "\n\n".join(
        [
            f"Generate the full C source file `{file_spec.source_path}`.",
            _section("Canonical header content:", generated_header),
            _section("Available raw header declarations:", _local_headers(context, file_spec)),
            _section("Local SpecFS function blocks for this target:", _local_specfs_blocks(context, file_spec)),
            "Requirements:\n"
            f"{primary_include}\n"
            "- Implement every function described in the local SpecFS blocks for this target.\n"
            "- Match each [GUARANTEE] signature exactly.\n"
            "- Use the [RELY] declarations and the available headers as the allowed C interface context.\n"
            "- Keep public APIs compatible with the canonical headers.\n"
            "- Include required standard/POSIX headers for library and system calls.\n"
            "- Output only the complete C source file content.",
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def build_s1_repair_prompt(
    context: AblationViewContext,
    file_spec: FileSpec,
    target_path: str,
    canonical_header: str,
    current_content: str,
    compile_errors: str,
) -> list[dict[str, str]]:
    if target_path != file_spec.source_path or not target_path.endswith(".c"):
        raise ValueError(f"repair target must be the source path for a .c file: {target_path}")
    content = "\n\n".join(
        [
            f"Repair the source file `{target_path}` so the project compiles.",
            _section("Compiler diagnostics:", compile_errors),
            _section("Canonical header content:", canonical_header),
            _section("Available raw header declarations:", _local_headers(context, file_spec)),
            _section("Local SpecFS function blocks for this target:", _local_specfs_blocks(context, file_spec)),
            _section("Current source file content:", current_content),
            "Requirements:\n"
            "- Output only the full corrected C source file content.\n"
            "- Preserve all public function signatures exactly.\n"
            "- Do not edit headers, Makefile, or any other source file.\n"
            "- Keep behavior aligned with the local SpecFS blocks.",
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def build_s2_source_prompt(
    context: AblationViewContext,
    file_spec: FileSpec,
    generated_header: str,
) -> list[dict[str, str]]:
    primary_include = (
        f'- Include "{file_spec.header_path}" as the primary project include.'
        if file_spec.header_path
        else "- This target has no paired public header; include the available project headers needed by the local SpecFS blocks."
    )
    content = "\n\n".join(
        [
            f"Generate the full C source file `{file_spec.source_path}`.",
            _section("Canonical header content:", generated_header),
            _section("Available raw header declarations:", _local_headers(context, file_spec)),
            _section("S2 project graph context:", _render_s2_project_graph(context, file_spec)),
            _section("Local SpecFS function blocks for this target:", _local_specfs_blocks(context, file_spec)),
            "Requirements:\n"
            f"{primary_include}\n"
            "- Implement every function described in the local SpecFS blocks for this target.\n"
            "- Match each [GUARANTEE] signature exactly.\n"
            "- Use the S2 project graph only for module/file/function structure, dependency visibility, linkage, and generation order.\n"
            "- Treat the local SpecFS blocks and canonical headers as the behavioral and ABI source of truth.\n"
            "- Keep public APIs compatible with the canonical headers.\n"
            "- Include required standard/POSIX headers for library and system calls.\n"
            "- Output only the complete C source file content.",
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def build_s2_repair_prompt(
    context: AblationViewContext,
    file_spec: FileSpec,
    target_path: str,
    canonical_header: str,
    current_content: str,
    compile_errors: str,
) -> list[dict[str, str]]:
    if target_path != file_spec.source_path or not target_path.endswith(".c"):
        raise ValueError(f"repair target must be the source path for a .c file: {target_path}")
    content = "\n\n".join(
        [
            f"Repair the source file `{target_path}` so the project compiles.",
            _section("Compiler diagnostics:", compile_errors),
            _section("Canonical header content:", canonical_header),
            _section("Available raw header declarations:", _local_headers(context, file_spec)),
            _section("S2 project graph context:", _render_s2_project_graph(context, file_spec)),
            _section("Local SpecFS function blocks for this target:", _local_specfs_blocks(context, file_spec)),
            _section("Current source file content:", current_content),
            "Requirements:\n"
            "- Output only the full corrected C source file content.\n"
            "- Preserve all public function signatures exactly.\n"
            "- Do not edit headers, Makefile, or any other source file.\n"
            "- Use the S2 project graph only for module/file/function structure, dependency visibility, linkage, and generation order.\n"
            "- Keep behavior aligned with the local SpecFS blocks and canonical headers.",
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
