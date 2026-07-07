from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent.coder.generation import render_header
from agent.coder.header_recipes import render_public_declarations
from agent.coder.models import FileSpec, FunctionSpec, SpecBundle
from agent.coder.specs import (
    canonical_signature_for_header,
    canonical_signature_for_source,
    function_specs_for_file,
    load_spec_bundle_from_root,
)

from .configs import DEFAULT_OUTPUT_ROOT, PROTOCOL_CONFIGS, ProtocolConfig, protocol_keys, rel_to_repo


VIEW_SCHEMAS = {
    "s1": {
        "transformer_version": "spec_ablation_s1_transformer/v1",
        "manifest_schema": "spec_ablation_s1_transformation_manifest/v1",
        "execution_manifest_schema": "spec_ablation_s1_execution_manifest/v1",
    },
    "s2": {
        "transformer_version": "spec_ablation_s2_transformer/v1",
        "manifest_schema": "spec_ablation_s2_transformation_manifest/v1",
        "execution_manifest_schema": "spec_ablation_s2_execution_manifest/v1",
    },
    "s3": {
        "transformer_version": "spec_ablation_s3_transformer/v1",
        "manifest_schema": "spec_ablation_s3_transformation_manifest/v1",
        "execution_manifest_schema": "spec_ablation_s3_execution_manifest/v1",
    },
}
PROJECT_GRAPH_SCHEMA = "spec_ablation_s2_project_graph/v1"
S3_INTERFACE_GROUNDING_SCHEMA = "spec_ablation_s3_interface_grounding/v1"

FORBIDDEN_VISIBLE_TERMS: tuple[str, ...] = (
    "PROTOCOL_MODULE_SPEC",
    "FILE_SPEC",
    "FUNCTION_SPEC",
    "ACCESS_PATHS",
    "WIRE_MAPPING",
    "CALL_CONTRACTS",
    "FORBIDDEN_SYMBOLS",
    "TEST_VECTORS",
    "CONSISTENCY_RULES",
    "DOC_REF",
    "TRACE_REFS",
)

S2_PROJECT_GRAPH_FORBIDDEN_TERMS: tuple[str, ...] = (
    *FORBIDDEN_VISIBLE_TERMS,
    "ROLE",
    "ARTIFACTS",
    "DATA",
    "INTERFACE",
    "FUNCTION_TYPE",
    "PARAMS",
    "NULLABLE",
    "OWNERSHIP",
    "PUBLIC_SYMBOLS",
    "SCOPE",
)

S3_INTERFACE_GROUNDING_FORBIDDEN_TERMS: tuple[str, ...] = (
    *FORBIDDEN_VISIBLE_TERMS,
    "LOGIC",
    "EVENT",
)

_LOCAL_DEPENDENCY_CATEGORIES = ("STRUCT", "FUNC", "VAR")


class TransformationError(RuntimeError):
    """Raised when the deterministic S1 projection cannot be produced safely."""


@dataclass(frozen=True)
class Declaration:
    name: str
    category: str
    declaration: str
    file_trace_id: str
    header_path: str
    source_path: str


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_semicolon(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped.endswith(";") else f"{stripped};"


def _sanitize_text(value: str) -> str:
    return (
        value.replace("\x00", r"\0")
        .replace("\x01", r"\x01")
        .replace("\x02", r"\x02")
        .replace("\x03", r"\x03")
        .replace("\x04", r"\x04")
        .replace("\x05", r"\x05")
        .replace("\x06", r"\x06")
        .replace("\x07", r"\a")
        .replace("\x08", r"\b")
        .replace("\x0b", r"\v")
        .replace("\x0c", r"\f")
    )


def _trace_parent(trace_id: str) -> str:
    return trace_id.rsplit("/", 1)[0] if "/" in trace_id else ""


def _tree_hash(root: Path, *, pattern: str = "*", exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob(pattern) if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _ordered_files(bundle: SpecBundle) -> list[FileSpec]:
    by_path: dict[str, FileSpec] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        if file_spec.header_path:
            by_path[file_spec.header_path] = file_spec
        if file_spec.source_path:
            by_path[file_spec.source_path] = file_spec

    ordered: list[FileSpec] = []
    seen: set[str] = set()
    for module in bundle.modules_in_order:
        for file_path in module.files:
            file_spec = by_path.get(file_path)
            if file_spec is None or file_spec.trace_id in seen:
                continue
            ordered.append(file_spec)
            seen.add(file_spec.trace_id)

    missing = [item for item in bundle.file_specs_by_trace.values() if item.trace_id not in seen]
    return [*ordered, *sorted(missing, key=lambda item: item.trace_id)]


def _render_single_data_item(file_spec: FileSpec, item: dict[str, Any]) -> str:
    name = str(item.get("NAME", "")).strip()
    if re.match(r"^(?:struct|union|enum)\s+[A-Za-z_][A-Za-z0-9_]*$", name):
        return f"{name};"
    public_item = dict(item)
    public_item["VISIBILITY"] = "PUBLIC"
    single = FileSpec(
        trace_id=file_spec.trace_id,
        role=file_spec.role,
        lang=file_spec.lang,
        header_path=file_spec.header_path,
        source_path=file_spec.source_path,
        header_dependencies=file_spec.header_dependencies,
        header_system_dependencies=file_spec.header_system_dependencies,
        source_dependencies=file_spec.source_dependencies,
        header_data=[public_item],
        source_data=[],
        header_interfaces=[],
        source_interfaces=[],
        spec_path=file_spec.spec_path,
        raw=file_spec.raw,
    )
    rendered = render_public_declarations(single).strip()
    if rendered:
        return rendered
    if str(item.get("KIND", "")).upper() == "TYPE" and name.endswith("_t"):
        return f"typedef struct {name[:-2]} {name};"
    return ""


def _declaration_index(bundle: SpecBundle) -> dict[tuple[str, str], list[Declaration]]:
    index: dict[tuple[str, str], list[Declaration]] = {}

    def add(name: str, category: str, declaration: str, file_spec: FileSpec) -> None:
        name = name.strip()
        declaration = declaration.strip()
        if not name or not declaration:
            return
        key = (category, name)
        item = Declaration(
            name=name,
            category=category,
            declaration=declaration,
            file_trace_id=file_spec.trace_id,
            header_path=file_spec.header_path,
            source_path=file_spec.source_path,
        )
        entries = index.setdefault(key, [])
        if item not in entries:
            entries.append(item)

    for file_spec in bundle.file_specs_by_trace.values():
        for data_item in [*file_spec.header_data, *file_spec.source_data]:
            name = str(data_item.get("NAME", "")).strip()
            declaration = _render_single_data_item(file_spec, data_item)
            kind = str(data_item.get("KIND", "")).upper()
            if kind == "TYPE":
                add(name, "STRUCT", declaration, file_spec)
                add(name, "TYPE", declaration, file_spec)
            elif kind in {"VAR", "CONST", "MACRO"}:
                add(name, "VAR", declaration, file_spec)
                add(name, kind, declaration, file_spec)

        for source_item in file_spec.source_interfaces:
            add(
                source_item.name,
                "FUNC",
                _ensure_semicolon(canonical_signature_for_source(bundle, source_item)),
                file_spec,
            )
        for header_item in file_spec.header_interfaces:
            add(
                header_item.name,
                "FUNC",
                _ensure_semicolon(canonical_signature_for_header(bundle, file_spec, header_item)),
                file_spec,
            )

    for entries in index.values():
        entries.sort(key=lambda item: (item.file_trace_id, item.name, item.declaration))
    return index


def _choose_declaration(
    category: str,
    name: str,
    current_file: FileSpec,
    index: dict[tuple[str, str], list[Declaration]],
) -> tuple[Declaration | None, str]:
    candidates = index.get((category, name), [])
    if not candidates:
        return None, "external_name_only"

    same_parent = [item for item in candidates if item.file_trace_id == current_file.trace_id]
    if len(same_parent) == 1:
        return same_parent[0], "same_parent"
    if len(same_parent) > 1:
        raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: same parent")

    dependency_paths = set(current_file.header_dependencies) | set(current_file.source_dependencies)
    dependency_candidates = [item for item in candidates if item.header_path in dependency_paths]
    if len(dependency_candidates) == 1:
        return dependency_candidates[0], "dependency_provider"
    if len(dependency_candidates) > 1:
        providers = ", ".join(sorted(item.file_trace_id for item in dependency_candidates))
        raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: dependency providers {providers}")

    if len(candidates) == 1:
        return candidates[0], "global_unique"

    providers = ", ".join(sorted(item.file_trace_id for item in candidates))
    raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: {providers}")


def _format_rely(entries: list[dict[str, str]]) -> str:
    if not entries:
        return "None."
    blocks: list[str] = []
    for entry in entries:
        role = entry.get("role", "")
        declaration = entry.get("declaration", "")
        if declaration:
            blocks.append(f"- {entry['category']} `{entry['name']}`\n  role: {role}\n```c\n{declaration}\n```")
        else:
            blocks.append(f"- {entry['category']} `{entry['name']}`\n  role: {role}\n  declaration: external dependency; canonical declaration unavailable.")
    return "\n\n".join(blocks)


def _format_specification(function_spec: FunctionSpec) -> str:
    body = function_spec.body if isinstance(function_spec.body, dict) else {}
    if function_spec.function_type.upper() == "EVENT":
        precondition = "\n".join(
            f"- {label}: {body.get(key, '')}".strip()
            for label, key in (("Trigger", "TRIGGER"), ("Precondition", "PRECONDITION"), ("Input", "INPUT"))
            if str(body.get(key, "")).strip()
        )
        postcondition = "\n".join(
            f"- {label}: {body.get(key, '')}".strip()
            for label, key in (("State Change", "STATE_CHANGE"), ("Response", "RESPONSE"))
            if str(body.get(key, "")).strip()
        )
        invariants = "- None specified."
        action = str(body.get("ACTION", "")).strip()
    else:
        precondition = str(body.get("INPUT", "")).strip()
        postcondition = str(body.get("OUTPUT", "")).strip()
        raw_invariants = body.get("INVARIANTS_USED", [])
        invariants = "\n".join(f"- {item}" for item in raw_invariants if str(item).strip()) if isinstance(raw_invariants, list) else str(raw_invariants).strip()
        action = str(body.get("ACTION", "")).strip()

    def bullet(value: str) -> str:
        value = value.strip()
        if not value:
            return "- None specified."
        return value if value.startswith("- ") else f"- {value}"

    return "\n".join(
        [
            "**Pre-Condition**:",
            bullet(precondition),
            "",
            "**Post-Condition**:",
            bullet(postcondition),
            "",
            "**Invariant**:",
            invariants.strip() or "- None specified.",
            "",
            "**System Algorithm**:",
            bullet(action),
        ]
    )


def _function_projection(
    function_spec: FunctionSpec,
    file_spec: FileSpec,
    index: dict[tuple[str, str], list[Declaration]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    rely_entries: list[dict[str, str]] = []
    resolutions: list[dict[str, str]] = []
    for category in _LOCAL_DEPENDENCY_CATEGORIES:
        for raw_entry in function_spec.rely.get(category, []):
            if not isinstance(raw_entry, dict):
                continue
            name = str(raw_entry.get("NAME", "")).strip()
            if not name:
                continue
            declaration, strategy = _choose_declaration(category, name, file_spec, index)
            rely_entries.append(
                {
                    "category": category,
                    "name": name,
                    "role": str(raw_entry.get("ROLE", "")).strip(),
                    "declaration": declaration.declaration if declaration is not None else "",
                }
            )
            resolutions.append(
                {
                    "category": category,
                    "name": name,
                    "strategy": strategy,
                    "provider_trace_id": declaration.file_trace_id if declaration is not None else "",
                    "provider_header_path": declaration.header_path if declaration is not None else "",
                }
            )

    prompt = f"Implement function `{function_spec.signature.name}`. Responsibility: {function_spec.role}"
    blocks = {
        "PROMPT": prompt,
        "RELY": _format_rely(rely_entries),
        "GUARANTEE": f"```c\n{_ensure_semicolon(function_spec.signature.raw)}\n```",
        "SPECIFICATION": _format_specification(function_spec),
    }
    return blocks, resolutions


def _render_function_spec(blocks: dict[str, str]) -> str:
    ordered = ("PROMPT", "RELY", "GUARANTEE", "SPECIFICATION")
    return "\n\n".join(f"[{name}]\n{_sanitize_text(blocks[name].strip())}" for name in ordered) + "\n"


def _specfs_header(rendered_header: str) -> str:
    lines: list[str] = []
    for line in rendered_header.splitlines():
        stripped = line.strip()
        if stripped in {"#pragma once", "#ifdef __cplusplus", 'extern "C" {', "#endif", "}"}:
            continue
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(line.rstrip())
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _visible_leakage_scan(root: Path) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for path in sorted((root / "specfs_projection").rglob("*")):
        if not path.is_file() or path.suffix not in {".spec", ".header"}:
            continue
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN_VISIBLE_TERMS:
            if term in text:
                violations.append({"path": path.relative_to(root).as_posix(), "term": term})
    return {
        "status": "passed" if not violations else "failed",
        "forbidden_terms": list(FORBIDDEN_VISIBLE_TERMS),
        "violations": violations,
    }


def _s2_project_graph_leakage_scan(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    violations = [{"path": path.name, "term": term} for term in S2_PROJECT_GRAPH_FORBIDDEN_TERMS if term in text]
    return {
        "status": "passed" if not violations else "failed",
        "forbidden_terms": list(S2_PROJECT_GRAPH_FORBIDDEN_TERMS),
        "violations": violations,
    }


def _s3_interface_grounding_leakage_scan(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    violations = [{"path": path.name, "term": term} for term in S3_INTERFACE_GROUNDING_FORBIDDEN_TERMS if term in text]
    return {
        "status": "passed" if not violations else "failed",
        "forbidden_terms": list(S3_INTERFACE_GROUNDING_FORBIDDEN_TERMS),
        "violations": violations,
    }


def _module_file_membership(bundle: SpecBundle) -> dict[str, str]:
    by_path: dict[str, FileSpec] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        if file_spec.header_path:
            by_path[file_spec.header_path] = file_spec
        if file_spec.source_path:
            by_path[file_spec.source_path] = file_spec

    membership: dict[str, str] = {}
    for module in bundle.modules_in_order:
        for file_path in module.files:
            file_spec = by_path.get(file_path)
            if file_spec is None:
                continue
            current = membership.get(file_spec.trace_id)
            if current is not None and current != module.name:
                raise TransformationError(
                    f"File '{file_spec.trace_id}' appears in multiple modules: {current}, {module.name}"
                )
            membership[file_spec.trace_id] = module.name
    return membership


def _function_linkage(function_spec: FunctionSpec, file_spec: FileSpec) -> str:
    source_item = next((item for item in file_spec.source_interfaces if item.trace_id == function_spec.trace_id), None)
    visibility = str(source_item.visibility if source_item is not None else "").strip().lower()
    signature = str(source_item.signature if source_item is not None else function_spec.signature.raw).strip()
    if signature.startswith("static "):
        return "internal_static"
    if visibility == "public":
        return "external"
    if visibility == "private":
        return "internal"
    return visibility or "unspecified"


def _dedupe_ordered(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _s2_project_graph(bundle: SpecBundle, ordered_files: list[FileSpec]) -> dict[str, Any]:
    membership = _module_file_membership(bundle)
    module_order = {module.name: order for order, module in enumerate(bundle.modules_in_order)}
    file_order = {file_spec.trace_id: order for order, file_spec in enumerate(ordered_files)}

    by_path: dict[str, FileSpec] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        if file_spec.header_path:
            by_path[file_spec.header_path] = file_spec
        if file_spec.source_path:
            by_path[file_spec.source_path] = file_spec

    modules: list[dict[str, Any]] = []
    module_dependency_edges: list[dict[str, str]] = []
    for module in bundle.modules_in_order:
        file_trace_ids = _dedupe_ordered(
            by_path[file_path].trace_id for file_path in module.files if file_path in by_path
        )
        modules.append(
            {
                "module_name": module.name,
                "generation_order": module_order[module.name],
                "contains_file_trace_ids": file_trace_ids,
                "dependency_modules": list(module.dependencies),
            }
        )
        module_dependency_edges.extend({"from_module": module.name, "to_module": dep} for dep in module.dependencies)

    files: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    source_positions: list[dict[str, Any]] = []
    file_dependency_edges: list[dict[str, str]] = []
    seen_file_edges: set[tuple[str, str, str]] = set()
    global_function_order = 0

    for order, file_spec in enumerate(ordered_files):
        module_name = membership.get(file_spec.trace_id, "")
        function_specs = function_specs_for_file(bundle, file_spec)
        function_trace_ids = [spec.trace_id for spec in function_specs]
        dependency_trace_ids: list[str] = []

        for dependency_kind, dependencies in (
            ("header", file_spec.header_dependencies),
            ("source", file_spec.source_dependencies),
        ):
            for dependency_path in dependencies:
                target = by_path.get(dependency_path)
                target_trace_id = target.trace_id if target is not None else ""
                if target_trace_id:
                    dependency_trace_ids.append(target_trace_id)
                edge_key = (file_spec.trace_id, dependency_kind, dependency_path)
                if edge_key in seen_file_edges:
                    continue
                seen_file_edges.add(edge_key)
                file_dependency_edges.append(
                    {
                        "from_file_trace_id": file_spec.trace_id,
                        "dependency_kind": dependency_kind,
                        "dependency_path": dependency_path,
                        "to_file_trace_id": target_trace_id,
                    }
                )

        files.append(
            {
                "file_trace_id": file_spec.trace_id,
                "module_name": module_name,
                "generation_order": order,
                "header_path": file_spec.header_path,
                "source_path": file_spec.source_path,
                "contains_function_trace_ids": function_trace_ids,
                "header_dependency_paths": list(file_spec.header_dependencies),
                "source_dependency_paths": list(file_spec.source_dependencies),
                "dependency_file_trace_ids": _dedupe_ordered(dependency_trace_ids),
            }
        )
        source_positions.append(
            {
                "source_path": file_spec.source_path,
                "file_trace_id": file_spec.trace_id,
                "module_name": module_name,
                "module_generation_order": module_order.get(module_name, -1),
                "file_generation_order": order,
                "function_trace_ids": function_trace_ids,
            }
        )

        for function_order, function_spec in enumerate(function_specs):
            functions.append(
                {
                    "function_trace_id": function_spec.trace_id,
                    "function_name": function_spec.signature.name,
                    "module_name": module_name,
                    "file_trace_id": file_spec.trace_id,
                    "source_path": file_spec.source_path,
                    "linkage": _function_linkage(function_spec, file_spec),
                    "file_function_order": function_order,
                    "file_generation_order": file_order[file_spec.trace_id],
                    "global_function_order": global_function_order,
                }
            )
            global_function_order += 1

    return {
        "schema_version": PROJECT_GRAPH_SCHEMA,
        "visibility": "s2_coder_visible_project_graph",
        "module_generation_order": [module.name for module in bundle.modules_in_order],
        "source_files": [file_spec.source_path for file_spec in ordered_files if file_spec.source_path],
        "header_files": [file_spec.header_path for file_spec in ordered_files if file_spec.header_path],
        "modules": modules,
        "module_dependency_edges": module_dependency_edges,
        "files": files,
        "file_dependency_edges": file_dependency_edges,
        "functions": functions,
        "source_positions": source_positions,
    }


def _s3_interface_grounding(bundle: SpecBundle, ordered_files: list[FileSpec]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for file_spec in ordered_files:
        header_type_specs: list[dict[str, Any]] = []
        for item in file_spec.header_data:
            type_spec = item.get("TYPE_SPEC")
            if str(item.get("KIND", "")).upper() != "TYPE" or not isinstance(type_spec, dict):
                continue
            header_type_specs.append(
                {
                    "name": str(item.get("NAME", "")).strip(),
                    "visibility": str(item.get("VISIBILITY", "")).strip(),
                    "role": str(item.get("ROLE", "")).strip(),
                    "type_spec": type_spec,
                    "canonical_declaration": _render_single_data_item(file_spec, item),
                }
            )

        files.append(
            {
                "file_trace_id": file_spec.trace_id,
                "header_path": file_spec.header_path,
                "source_path": file_spec.source_path,
                "header_type_specs": header_type_specs,
                "header_interfaces": [
                    {
                        "name": item.name,
                        "kind": item.kind,
                        "function_type": item.function_type.lower(),
                        "role": item.role,
                        "visibility": item.visibility,
                        "signature": canonical_signature_for_header(bundle, file_spec, item),
                    }
                    for item in file_spec.header_interfaces
                ],
                "source_interfaces": [
                    {
                        "trace_id": item.trace_id,
                        "name": item.name,
                        "kind": item.kind,
                        "role": item.role,
                        "visibility": item.visibility,
                        "signature": canonical_signature_for_source(bundle, item),
                    }
                    for item in file_spec.source_interfaces
                ],
            }
        )

    return {
        "schema_version": S3_INTERFACE_GROUNDING_SCHEMA,
        "visibility": "s3_coder_visible_interface_grounding",
        "files": files,
    }


def _execution_manifest(
    config: ProtocolConfig,
    bundle: SpecBundle,
    ordered_files: list[FileSpec],
    function_artifacts: dict[str, str],
    header_artifacts: dict[str, str],
    view_name: str,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for order, file_spec in enumerate(ordered_files):
        functions = [
            {
                "trace_id": spec.trace_id,
                "name": spec.signature.name,
                "artifact_path": function_artifacts[spec.trace_id],
            }
            for spec in function_specs_for_file(bundle, file_spec)
        ]
        sources.append(
            {
                "order": order,
                "file_trace_id": file_spec.trace_id,
                "source_path": file_spec.source_path,
                "header_path": file_spec.header_path,
                "header_artifact": header_artifacts.get(file_spec.header_path, ""),
                "functions": functions,
            }
        )
    return {
        "schema_version": VIEW_SCHEMAS[view_name]["execution_manifest_schema"],
        "protocol": config.protocol,
        "view": view_name,
        "visibility": "hidden_evaluator_only",
        "note": f"This manifest is evaluator control data and must not be inserted into {view_name.upper()} generation or repair prompts.",
        "sources": sources,
    }


def _loader_validation(bundle: SpecBundle) -> dict[str, Any]:
    diagnostics = [
        {
            "level": item.level,
            "code": item.code,
            "message": item.message,
            "path": item.path or "",
        }
        for item in bundle.diagnostics
    ]
    return {
        "status": "failed" if any(item["level"] == "error" for item in diagnostics) else "passed",
        "diagnostics": diagnostics,
    }


def _build_transformation(config: ProtocolConfig, root: Path, view_name: str) -> dict[str, Any]:
    if view_name not in VIEW_SCHEMAS:
        raise TransformationError(f"Unsupported view '{view_name}'")

    bundle = load_spec_bundle_from_root(config.specs_root, validate_rendered_headers=False)
    loader_validation = _loader_validation(bundle)
    if loader_validation["status"] != "passed":
        codes = ", ".join(item["code"] for item in loader_validation["diagnostics"] if item["level"] == "error")
        raise TransformationError(f"Source oracle failed loader validation: {codes}")

    specfs_root = root / "specfs_projection"
    functions_root = specfs_root / "functions"
    headers_root = specfs_root / "headers"
    ordered_files = _ordered_files(bundle)
    declaration_index = _declaration_index(bundle)

    header_artifacts: dict[str, str] = {}
    header_records: list[dict[str, str]] = []
    for file_spec in ordered_files:
        if not file_spec.header_path:
            continue
        rendered = _specfs_header(render_header(bundle, file_spec))
        artifact = headers_root / f"{file_spec.header_path}.header"
        _write_text(artifact, rendered)
        relative = artifact.relative_to(root).as_posix()
        header_artifacts[file_spec.header_path] = relative
        header_records.append(
            {
                "header_path": file_spec.header_path,
                "artifact_path": relative,
                "sha256": _sha256_text(rendered),
            }
        )

    function_artifacts: dict[str, str] = {}
    function_records: list[dict[str, Any]] = []
    rely_resolutions: list[dict[str, str]] = []
    for file_spec in ordered_files:
        for function_spec in function_specs_for_file(bundle, file_spec):
            blocks, resolutions = _function_projection(function_spec, file_spec, declaration_index)
            content = _render_function_spec(blocks)
            artifact = functions_root / f"{function_spec.trace_id}.spec"
            _write_text(artifact, content)
            relative = artifact.relative_to(root).as_posix()
            function_artifacts[function_spec.trace_id] = relative
            function_records.append(
                {
                    "trace_id": function_spec.trace_id,
                    "name": function_spec.signature.name,
                    "source_path": file_spec.source_path,
                    "artifact_path": relative,
                    "sha256": _sha256_text(content),
                }
            )
            for resolution in resolutions:
                rely_resolutions.append({"trace_id": function_spec.trace_id, **resolution})

    execution_manifest = _execution_manifest(config, bundle, ordered_files, function_artifacts, header_artifacts, view_name)
    _write_json(root / "execution_manifest.json", execution_manifest)

    leakage = _visible_leakage_scan(root)
    if leakage["status"] != "passed":
        raise TransformationError(f"{view_name.upper()} local artifact leakage detected: {leakage['violations']}")

    schema = VIEW_SCHEMAS[view_name]
    manifest = {
        "schema_version": schema["manifest_schema"],
        "transformer_version": schema["transformer_version"],
        "protocol": config.protocol,
        "view": view_name,
        "source_root": rel_to_repo(config.specs_root),
        "output_root": ".",
        "counts": {
            "files": len(bundle.file_specs_by_trace),
            "functions": len(function_records),
            "headers": len(header_records),
        },
        "hashes": {
            "source_specs_tree_sha256": _tree_hash(config.specs_root, pattern="*_spec.json"),
            "output_payload_sha256": "",
        },
        "artifacts": {
            "functions": function_records,
            "headers": header_records,
            "execution_manifest": "execution_manifest.json",
        },
        "validation": {
            "source_loader": loader_validation,
            "leakage_scan": leakage,
        },
        "rely_resolution": rely_resolutions,
    }

    if view_name in {"s2", "s3"}:
        project_graph = _s2_project_graph(bundle, ordered_files)
        project_graph_artifact = specfs_root / "project_graph.json"
        _write_json(project_graph_artifact, project_graph)
        s2_project_graph_leakage = _s2_project_graph_leakage_scan(project_graph_artifact)
        if s2_project_graph_leakage["status"] != "passed":
            raise TransformationError(f"{view_name.upper()} project graph leakage detected: {s2_project_graph_leakage['violations']}")
        manifest["counts"]["project_graphs"] = 1
        manifest["artifacts"]["project_graph"] = {
            "artifact_path": project_graph_artifact.relative_to(root).as_posix(),
            "sha256": _sha256_bytes(project_graph_artifact.read_bytes()),
        }
        manifest["validation"]["s2_project_graph_leakage_scan"] = s2_project_graph_leakage

    if view_name == "s3":
        interface_grounding = _s3_interface_grounding(bundle, ordered_files)
        interface_grounding_artifact = specfs_root / "interface_grounding.json"
        _write_json(interface_grounding_artifact, interface_grounding)
        s3_interface_grounding_leakage = _s3_interface_grounding_leakage_scan(interface_grounding_artifact)
        if s3_interface_grounding_leakage["status"] != "passed":
            raise TransformationError(f"S3 interface grounding leakage detected: {s3_interface_grounding_leakage['violations']}")
        manifest["counts"]["interface_groundings"] = 1
        manifest["artifacts"]["interface_grounding"] = {
            "artifact_path": interface_grounding_artifact.relative_to(root).as_posix(),
            "sha256": _sha256_bytes(interface_grounding_artifact.read_bytes()),
        }
        manifest["validation"]["s3_interface_grounding_leakage_scan"] = s3_interface_grounding_leakage

    manifest["hashes"]["output_payload_sha256"] = _tree_hash(root, exclude={"transformation_manifest.json"})
    manifest["transformation_hash"] = _sha256_text(_canonical_json(manifest))
    _write_json(root / "transformation_manifest.json", manifest)
    return manifest


def _view_keys(values: Iterable[str] | str | None = None) -> list[str]:
    if values is None:
        return list(VIEW_SCHEMAS)
    raw_values = [values] if isinstance(values, str) else list(values)
    selected: list[str] = []
    for value in raw_values:
        if value == "all":
            selected.extend(VIEW_SCHEMAS)
        elif value in VIEW_SCHEMAS:
            selected.append(value)
        else:
            raise TransformationError(f"Unsupported view '{value}'")
    return list(dict.fromkeys(selected))


def _remove_legacy_flat_artifacts(target: Path) -> None:
    for path in (target / "transformation_manifest.json", target / "execution_manifest.json", target / "specfs_projection"):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _view_set_manifest(config: ProtocolConfig, root: Path, generated: dict[str, dict[str, Any]]) -> dict[str, Any]:
    views: dict[str, Any] = {}
    for view_name in VIEW_SCHEMAS:
        manifest = generated.get(view_name)
        manifest_path = root / view_name / "transformation_manifest.json"
        if manifest is None and manifest_path.is_file():
            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        if manifest is None:
            continue
        views[view_name] = {
            "root": view_name,
            "transformation_manifest": f"{view_name}/transformation_manifest.json",
            "transformation_hash": manifest["transformation_hash"],
            "counts": manifest["counts"],
        }
    return {
        "schema_version": "spec_ablation_view_set_manifest/v1",
        "protocol": config.protocol,
        "source_root": rel_to_repo(config.specs_root),
        "views": views,
    }


def transform_protocol(
    protocol: str,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    views: Iterable[str] | str | None = None,
) -> dict[str, Any]:
    if protocol not in PROTOCOL_CONFIGS:
        raise TransformationError(f"Unsupported protocol '{protocol}'")
    target = Path(output_dir)
    selected_views = _view_keys(views)
    if target.exists() and overwrite and set(selected_views) == set(VIEW_SCHEMAS):
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    legacy_flat = any(
        (target / name).exists()
        for name in ("transformation_manifest.json", "execution_manifest.json", "specfs_projection")
    )
    if legacy_flat and not overwrite:
        raise TransformationError(f"Output directory contains legacy flat artifacts: {target}")
    if legacy_flat:
        _remove_legacy_flat_artifacts(target)

    config = PROTOCOL_CONFIGS[protocol]
    generated: dict[str, dict[str, Any]] = {}
    for view_name in selected_views:
        view_root = target / view_name
        if view_root.exists():
            if not overwrite and any(view_root.iterdir()):
                raise TransformationError(f"Output view directory already exists: {view_root}")
            shutil.rmtree(view_root)
        view_root.mkdir(parents=True, exist_ok=True)
        generated[view_name] = _build_transformation(config, view_root, view_name)

    view_set = _view_set_manifest(config, target, generated)
    _write_json(target / "view_set_manifest.json", view_set)
    return view_set


def transform_many(
    protocols: Iterable[str],
    output_root: str | Path,
    *,
    overwrite: bool = False,
    views: Iterable[str] | str | None = None,
) -> list[dict[str, Any]]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    selected: list[str] = []
    for value in protocols:
        selected.extend(protocol_keys(value))
    if not selected:
        selected = protocol_keys("all")
    selected = list(dict.fromkeys(selected))
    return [transform_protocol(protocol, root / protocol, overwrite=overwrite, views=views) for protocol in selected]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministically project Full-SpecForge specs to S1/S2/S3 ablation views.")
    parser.add_argument("--protocol", action="append", default=[], help="Protocol(s) to transform: mqtt/http/coap/smtp/all")
    parser.add_argument("--view", action="append", default=[], help="View(s) to transform: s1/s2/s3/all")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_ROOT), help="Output root; protocol subdirectories are created under it")
    parser.add_argument("--overwrite", action="store_true", help="Replace selected view output directories")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifests = transform_many(args.protocol or ["all"], args.output, overwrite=args.overwrite, views=args.view or ["all"])
    summaries = [
        {
            "protocol": manifest["protocol"],
            "views": manifest["views"],
        }
        for manifest in manifests
    ]
    print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
