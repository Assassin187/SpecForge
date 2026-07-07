from __future__ import annotations

from dataclasses import dataclass

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
    raise UnsupportedViewError("view 's2' is reserved for SpecFS-ProjectGraph but is not implemented yet")


def build_s2_repair_prompt(
    context: AblationViewContext,
    file_spec: FileSpec,
    target_path: str,
    canonical_header: str,
    current_content: str,
    compile_errors: str,
) -> list[dict[str, str]]:
    raise UnsupportedViewError("view 's2' is reserved for SpecFS-ProjectGraph but is not implemented yet")
