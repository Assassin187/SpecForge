from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..artifact_io import safe_slug, write_json
from ..schemas.coder_manifest import SCHEMA_VERSION as CODER_MANIFEST_SCHEMA_VERSION
from .coder_spec_lowering import (
    _canonical_field_type,
    callback_signature_for_coder,
    canonical_function_symbol,
    canonical_type_symbol,
    default_handle_type,
    extract_c_signature_type_refs,
    is_public_interface_function,
    lower_access_paths_for_coder,
    lower_call_contract_for_coder,
    lower_contract_for_coder,
    lower_canonical_type_to_header_data,
    lower_doc_ref,
    lower_event_or_logic_for_coder,
    lower_forbidden_symbols_for_coder,
    lower_module_artifacts_for_coder,
    lower_planned_module_artifacts_for_coder,
    lower_protocol_meta_for_coder,
    lower_rely_for_coder,
    lower_signature_for_coder,
    merge_coder_artifacts,
    normalize_type_key,
    normalize_data_visibility_for_coder,
    normalize_function_type_for_coder,
    sidecar_payload,
)
from .implementation_plan_context import system_headers_for_type_ref


def _normalized_spec_stem(file_item: dict[str, Any]) -> Path:
    raw_path = str(file_item.get("source_path") or file_item.get("header_path") or "").replace("\\", "/").strip()
    if not raw_path:
        raw_path = str(file_item.get("file_id", "file")).removeprefix("file:")
    while raw_path.startswith("../"):
        raw_path = raw_path[3:]
    while raw_path.startswith("./"):
        raw_path = raw_path[2:]
    stem = Path(raw_path).with_suffix("")
    return Path(*[safe_slug(part) for part in stem.parts if part not in {"", "."}])


def _is_source_only_entrypoint(file_item: dict[str, Any]) -> bool:
    source_path = str(file_item.get("source_path") or file_item.get("path") or "").replace("\\", "/").strip()
    return str(file_item.get("kind", "")) == "source_only_entrypoint" or source_path.endswith("/main.c") or source_path == "main.c"


def _function_trace_id(file_trace_id: str, function: dict[str, Any]) -> str:
    return f"{file_trace_id}/{safe_slug(canonical_function_symbol(function))}"


def _header_interface(function: dict[str, Any]) -> dict[str, Any]:
    signature = lower_signature_for_coder(function)
    return {
        "SIGNATURE": signature["RAW"],
        "NAME": signature["NAME"],
        "KIND": "FUNC",
        "FUNCTION_TYPE": normalize_function_type_for_coder(function),
        "ROLE": str(function.get("purpose", "")) or "Public function.",
        "VISIBILITY": "public",
    }


def _source_interface(function: dict[str, Any], trace_id: str, *, public: bool = False) -> dict[str, Any]:
    signature = lower_signature_for_coder(function)
    return {
        "TRACE_ID": trace_id,
        "SIGNATURE": signature["RAW"],
        "NAME": signature["NAME"],
        "KIND": "FUNC",
        "FUNCTION_TYPE": normalize_function_type_for_coder(function),
        "ROLE": str(function.get("purpose", "")) or "Implemented function.",
        "CONTRACT": lower_contract_for_coder(function),
        "VISIBILITY": "public" if public else "private",
    }


def _public_signature_unresolved(function: dict[str, Any]) -> list[dict[str, Any]]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    lowered = lower_signature_for_coder(function)
    missing = []
    if lowered["NAME"] == "unnamed":
        missing.append("name")
    if not str(signature.get("return_type") or signature.get("RETURN") or "").strip():
        missing.append("return_type")
    for param in signature.get("params", []) if isinstance(signature.get("params"), list) else []:
        if not isinstance(param, dict) or not str(param.get("name", "")).strip() or not str(param.get("type", "")).strip():
            missing.append("params")
            break
    if not missing:
        return []
    return [
        {
            "kind": "public_function_signature",
            "function_id": function.get("function_id", ""),
            "name": function.get("name", ""),
            "missing": sorted(set(missing)),
            "reason": "Public/exported function lacks a lowerable C signature.",
        }
    ]


def _canonical_type_index(canonical_types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in canonical_types:
        if not isinstance(item, dict):
            continue
        for value in (item.get("type_id"), item.get("name"), item.get("c_symbol"), item.get("c_type_name")):
            key = normalize_type_key(value)
            if key:
                result.setdefault(key, item)
    return result


def _resolve_canonical_type(value: Any, canonical_type_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return canonical_type_index.get(normalize_type_key(value))


def _add_unresolved(unresolved: list[dict[str, Any]], *, kind: str, module_id: str, type_name: str, reason: str, function_id: str = "", file_id: str = "") -> None:
    unresolved.append(
        {
            "kind": kind,
            "module_id": module_id,
            "function_id": function_id,
            "file_id": file_id,
            "type_name": type_name,
            "reason": reason,
        }
    )


def _plan_modules(implementation_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in implementation_plan.get("module_artifacts", []) if isinstance(item, dict)]


def _plan_files(implementation_plan: dict[str, Any]) -> list[dict[str, Any]]:
    file_layout = implementation_plan.get("file_layout", {}) if isinstance(implementation_plan.get("file_layout"), dict) else {}
    return [item for item in file_layout.get("files", []) if isinstance(item, dict)]


def _plan_functions(implementation_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in implementation_plan.get("function_contracts", []) if isinstance(item, dict)]


def _module_dependencies_from_plan(implementation_plan: dict[str, Any], module_ids: list[str]) -> dict[str, set[str]]:
    known = set(module_ids)
    deps: dict[str, set[str]] = {module_id: set() for module_id in module_ids}
    for module in implementation_plan.get("module_artifacts", []) if isinstance(implementation_plan.get("module_artifacts"), list) else []:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", "")).strip()
        if module_id not in known:
            continue
        deps.setdefault(module_id, set()).update(str(dep).strip() for dep in module.get("dependencies", []) if str(dep).strip() in known and str(dep).strip() != module_id)
    graph = implementation_plan.get("dependency_graph", {})
    compile_order_edge_kinds = {"signature_dependency", "header_dependency", "public_header_dependency"}
    edges = graph.get("module_edges", []) if isinstance(graph, dict) else []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("kind", "")).strip() not in compile_order_edge_kinds:
            continue
        source = str(edge.get("from", "")).strip()
        target = str(edge.get("to", "")).strip()
        if source in known and target in known and source != target:
            deps.setdefault(source, set()).add(target)
    return deps


def _type_ref_keys(type_item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for value in (type_item.get("type_id"), type_item.get("name"), type_item.get("c_symbol"), type_item.get("c_type_name")):
        key = normalize_type_key(value)
        if key:
            keys.add(key)
        text = str(value or "").strip()
        if text.startswith("type:") and ":" in text:
            tail_key = normalize_type_key(text.rsplit(":", 1)[-1])
            if tail_key:
                keys.add(tail_key)
    return keys


def _all_type_index(canonical_types: list[dict[str, Any]], type_inventory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for type_item in [*canonical_types, *type_inventory]:
        if not isinstance(type_item, dict):
            continue
        for key in _type_ref_keys(type_item):
            result.setdefault(key, type_item)
    return result


def _register_provider(provider_by_type_key: dict[str, str | None], key: str, header_path: str) -> None:
    if not key or not header_path:
        return
    existing = provider_by_type_key.get(key)
    if existing is None and key in provider_by_type_key:
        return
    if existing and existing != header_path:
        provider_by_type_key[key] = None
        return
    provider_by_type_key[key] = header_path


def _module_public_type_owner_headers(files: list[dict[str, Any]]) -> dict[str, str]:
    headers_by_module: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, file_item in enumerate(files):
        module_id = str(file_item.get("module_id", "")).strip()
        header_path = str(file_item.get("header_path", "")).strip()
        if module_id and header_path and not _is_source_only_entrypoint(file_item):
            headers_by_module.setdefault(module_id, []).append((index, file_item))

    result: dict[str, str] = {}
    for module_id, indexed_files in headers_by_module.items():
        if len(indexed_files) == 1:
            result[module_id] = str(indexed_files[0][1].get("header_path", "")).strip()
            continue

        def score(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            index, file_item = item
            header_path = str(file_item.get("header_path", "")).strip()
            basename_match = int(Path(header_path).stem == module_id)
            export_count = len(file_item.get("exports_type_ids", [])) if isinstance(file_item.get("exports_type_ids"), list) else 0
            return basename_match, export_count, -index

        result[module_id] = str(max(indexed_files, key=score)[1].get("header_path", "")).strip()
    return result


def _public_type_export_targets(
    canonical_types: list[dict[str, Any]],
    type_inventory: list[dict[str, Any]],
) -> tuple[dict[str, tuple[str, str]], dict[str, set[str]]]:
    by_key: dict[str, tuple[str, str]] = {}
    by_module: dict[str, set[str]] = {}

    def add(type_item: dict[str, Any], type_id: str, module_id: str) -> None:
        if not type_id or not module_id:
            return
        by_module.setdefault(module_id, set()).add(type_id)
        for key in _type_ref_keys(type_item) | {normalize_type_key(type_id)}:
            if key:
                by_key.setdefault(key, (type_id, module_id))

    for type_item in canonical_types:
        if not isinstance(type_item, dict) or lower_canonical_type_to_header_data(type_item) is None:
            continue
        add(
            type_item,
            str(type_item.get("type_id") or canonical_type_symbol(type_item)).strip(),
            str(type_item.get("owner_module_id") or type_item.get("module_id") or "").strip(),
        )
    for type_item in type_inventory:
        if not isinstance(type_item, dict):
            continue
        if str(type_item.get("visibility", "")).strip().lower() != "public":
            continue
        if str(type_item.get("defined_in", "")).strip().lower() != "public_header":
            continue
        add(
            type_item,
            str(type_item.get("type_id") or type_item.get("name") or "").strip(),
            str(type_item.get("module_id") or type_item.get("owner_module_id") or "").strip(),
        )
    return by_key, by_module


def _normalize_public_type_owner_files(
    files: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]],
    type_inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for file_item in files:
        copied = dict(file_item)
        if isinstance(file_item.get("exports_type_ids"), list):
            copied["exports_type_ids"] = [str(item) for item in file_item.get("exports_type_ids", []) if str(item).strip()]
        result.append(copied)

    owner_header_by_module = _module_public_type_owner_headers(result)
    header_counts: dict[str, int] = {}
    for file_item in result:
        module_id = str(file_item.get("module_id", "")).strip()
        if module_id and str(file_item.get("header_path", "")).strip() and not _is_source_only_entrypoint(file_item):
            header_counts[module_id] = header_counts.get(module_id, 0) + 1
    multi_header_modules = {module_id for module_id, count in header_counts.items() if count > 1}
    if not multi_header_modules:
        return result

    type_by_key, type_ids_by_module = _public_type_export_targets(canonical_types, type_inventory)
    for file_item in result:
        module_id = str(file_item.get("module_id", "")).strip()
        if module_id not in multi_header_modules:
            continue
        kept: list[str] = []
        for raw in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            target = type_by_key.get(normalize_type_key(raw))
            if target and target[1] == module_id:
                continue
            kept.append(str(raw))
        file_item["exports_type_ids"] = kept

    for module_id in sorted(multi_header_modules):
        owner_header = owner_header_by_module.get(module_id, "")
        owner = next((item for item in result if str(item.get("module_id", "")).strip() == module_id and str(item.get("header_path", "")).strip() == owner_header), None)
        if owner is None:
            continue
        exports = [str(item) for item in owner.get("exports_type_ids", []) if str(item).strip()] if isinstance(owner.get("exports_type_ids"), list) else []
        export_keys = {normalize_type_key(item) for item in exports}
        for type_id in sorted(type_ids_by_module.get(module_id, set())):
            key = normalize_type_key(type_id)
            if key and key not in export_keys:
                exports.append(type_id)
                export_keys.add(key)
        owner["exports_type_ids"] = exports
    return result


def _header_provider_index(
    files: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]],
    type_inventory: list[dict[str, Any]],
) -> dict[str, str | None]:
    provider_by_type_key: dict[str, str | None] = {}
    type_index = _all_type_index(canonical_types, type_inventory)
    public_headers_by_module: dict[str, list[str]] = {}
    for file_item in files:
        module_id = str(file_item.get("module_id", "")).strip()
        header_path = str(file_item.get("header_path", "")).strip()
        if module_id and header_path and not _is_source_only_entrypoint(file_item):
            public_headers_by_module.setdefault(module_id, []).append(header_path)
        for type_ref in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            key = normalize_type_key(type_ref)
            if key:
                _register_provider(provider_by_type_key, key, header_path)
            type_item = type_index.get(key, {}) if key else {}
            for alias in _type_ref_keys(type_item):
                _register_provider(provider_by_type_key, alias, header_path)

    for type_item in type_inventory:
        if not isinstance(type_item, dict):
            continue
        if str(type_item.get("visibility", "")).strip().lower() != "public":
            continue
        if str(type_item.get("defined_in", "")).strip().lower() != "public_header":
            continue
        module_id = str(type_item.get("module_id") or type_item.get("owner_module_id") or "").strip()
        module_headers = sorted(set(public_headers_by_module.get(module_id, [])))
        if len(module_headers) != 1:
            continue
        for key in _type_ref_keys(type_item):
            _register_provider(provider_by_type_key, key, module_headers[0])
    return provider_by_type_key


def _type_refs_from_type_spec(type_spec: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    kind = str(type_spec.get("TYPE_KIND", "")).upper()
    if kind in {"STRUCT", "UNION"}:
        member_key = "FIELDS" if kind == "STRUCT" else "VARIANTS"
        for member in type_spec.get(member_key, []) if isinstance(type_spec.get(member_key, []), list) else []:
            if not isinstance(member, dict):
                continue
            refs.extend(extract_c_signature_type_refs(member.get("TYPE", "")))
            nested = member.get("TYPE_SPEC")
            if isinstance(nested, dict):
                refs.extend(_type_refs_from_type_spec(nested))
    elif kind == "ALIAS":
        refs.extend(extract_c_signature_type_refs(type_spec.get("ALIAS_OF", "")))
    elif kind == "CALLBACK":
        refs.extend(extract_c_signature_type_refs(type_spec.get("CALLBACK_SIGNATURE", "")))
    return refs


def _type_refs_from_data(data_items: list[dict[str, Any]], *, public_only: bool) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in data_items:
        if not isinstance(item, dict):
            continue
        if public_only and str(item.get("VISIBILITY", "")).upper() != "PUBLIC":
            continue
        type_spec = item.get("TYPE_SPEC")
        if isinstance(type_spec, dict):
            refs.extend(_type_refs_from_type_spec(type_spec))
    return refs


def _system_headers_from_c_type(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    headers = system_headers_for_type_ref(text)
    function_pointer = text if "(*)" in text or "(*" in text else ""
    if function_pointer and "(" in function_pointer and ")" in function_pointer:
        for part in function_pointer.replace("(", " ").replace(")", " ").replace(",", " ").split():
            for header in system_headers_for_type_ref(part):
                if header not in headers:
                    headers.append(header)
    return headers


def _system_headers_from_type_spec(type_spec: dict[str, Any]) -> list[str]:
    headers: list[str] = []

    def add(value: Any) -> None:
        for header in _system_headers_from_c_type(value):
            if header not in headers:
                headers.append(header)

    kind = str(type_spec.get("TYPE_KIND", "")).upper()
    if kind in {"STRUCT", "UNION"}:
        member_key = "FIELDS" if kind == "STRUCT" else "VARIANTS"
        for member in type_spec.get(member_key, []) if isinstance(type_spec.get(member_key, []), list) else []:
            if not isinstance(member, dict):
                continue
            add(member.get("TYPE", ""))
            nested = member.get("TYPE_SPEC")
            if isinstance(nested, dict):
                for header in _system_headers_from_type_spec(nested):
                    if header not in headers:
                        headers.append(header)
    elif kind == "ALIAS":
        add(type_spec.get("ALIAS_OF", ""))
    elif kind == "CALLBACK":
        add(type_spec.get("CALLBACK_SIGNATURE", ""))
    return headers


def _public_header_system_dependencies(header_data: list[dict[str, Any]], header_interfaces: list[dict[str, Any]]) -> list[str]:
    headers: list[str] = []

    def add_all(values: list[str]) -> None:
        for header in values:
            if header not in headers:
                headers.append(header)

    for item in header_data:
        if not isinstance(item, dict) or str(item.get("VISIBILITY", "")).upper() != "PUBLIC":
            continue
        type_spec = item.get("TYPE_SPEC")
        if isinstance(type_spec, dict):
            add_all(_system_headers_from_type_spec(type_spec))
    for interface in header_interfaces:
        if isinstance(interface, dict) and str(interface.get("VISIBILITY", "")).lower() == "public":
            add_all(_system_headers_from_c_type(interface.get("SIGNATURE", "")))
    return sorted(headers)


def _declared_type_keys(data_items: list[dict[str, Any]]) -> set[str]:
    return {
        key
        for item in data_items
        if isinstance(item, dict) and str(item.get("KIND", "")).upper() == "TYPE"
        for key in (normalize_type_key(item.get("NAME", "")),)
        if key
    }


def _sort_header_data_by_local_type_dependencies(header_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    local_keys = {
        normalize_type_key(item.get("NAME", "")): index
        for index, item in enumerate(header_data)
        if isinstance(item, dict) and str(item.get("KIND", "")).upper() == "TYPE" and normalize_type_key(item.get("NAME", ""))
    }
    dependencies: dict[int, set[int]] = {index: set() for index in range(len(header_data))}
    for index, item in enumerate(header_data):
        if not isinstance(item, dict):
            continue
        self_key = normalize_type_key(item.get("NAME", ""))
        type_spec = item.get("TYPE_SPEC")
        refs = _type_refs_from_type_spec(type_spec) if isinstance(type_spec, dict) else []
        for ref in refs:
            key = str(ref.get("key", "")).strip()
            dep_index = local_keys.get(key)
            if dep_index is not None and key != self_key and dep_index != index:
                dependencies[index].add(dep_index)

    ordered: list[int] = []
    temporary: set[int] = set()
    permanent: set[int] = set()

    def visit(index: int) -> bool:
        if index in permanent:
            return True
        if index in temporary:
            return False
        temporary.add(index)
        for dependency in sorted(dependencies.get(index, set())):
            if not visit(dependency):
                return False
        temporary.remove(index)
        permanent.add(index)
        ordered.append(index)
        return True

    for index in range(len(header_data)):
        if not visit(index):
            return header_data
    return [header_data[index] for index in ordered]


def _resolve_type_ref_headers(
    refs: list[dict[str, Any]],
    *,
    provider_by_type_key: dict[str, str | None],
    local_type_keys: set[str],
    current_header: str,
    unresolved: list[dict[str, Any]],
    unresolved_kind: str,
    module_id: str,
    file_id: str,
    reason: str,
) -> set[str]:
    headers: set[str] = set()
    for ref in refs:
        key = str(ref.get("key", "")).strip()
        if not key or key in local_type_keys:
            continue
        provider = provider_by_type_key.get(key)
        if provider:
            if provider != current_header:
                headers.add(provider)
            continue
        _add_unresolved(
            unresolved,
            kind=unresolved_kind,
            module_id=module_id,
            file_id=file_id,
            type_name=str(ref.get("raw") or ref.get("name") or key),
            reason=reason,
        )
    return headers


def _public_header_dependency_headers(
    file_item: dict[str, Any],
    header_data: list[dict[str, Any]],
    header_interfaces: list[dict[str, Any]],
    provider_by_type_key: dict[str, str | None],
    unresolved: list[dict[str, Any]],
) -> list[str]:
    refs: list[dict[str, Any]] = []
    refs.extend(_type_refs_from_data(header_data, public_only=True))
    for interface in header_interfaces:
        if isinstance(interface, dict) and str(interface.get("VISIBILITY", "")).lower() == "public":
            refs.extend(extract_c_signature_type_refs(interface.get("SIGNATURE", "")))
    return sorted(
        _resolve_type_ref_headers(
            refs,
            provider_by_type_key=provider_by_type_key,
            local_type_keys=_declared_type_keys(header_data),
            current_header=str(file_item.get("header_path", "")).strip(),
            unresolved=unresolved,
            unresolved_kind="public_header_dependency_provider",
            module_id=str(file_item.get("module_id", "")),
            file_id=str(file_item.get("file_id", "")),
            reason="Public header references an external non-system type but no provider header could be resolved.",
        )
    )


def _callee_function_ids(function: dict[str, Any]) -> set[str]:
    ids = {str(item).strip() for item in function.get("calls_allowed", []) if str(item).strip()} if isinstance(function.get("calls_allowed", []), list) else set()
    for edge in function.get("call_contracts", []) if isinstance(function.get("call_contracts"), list) else []:
        if isinstance(edge, dict) and str(edge.get("callee_function_id", "")).strip():
            ids.add(str(edge["callee_function_id"]).strip())
    return ids


def _source_dependency_headers(
    file_item: dict[str, Any],
    file_functions: list[dict[str, Any]],
    source_data: list[dict[str, Any]],
    source_interfaces: list[dict[str, Any]],
    file_by_id: dict[str, dict[str, Any]],
    function_index: dict[str, dict[str, Any]],
    provider_by_type_key: dict[str, str | None],
) -> list[str]:
    deps: set[str] = set()
    self_header = str(file_item.get("header_path", "")).strip()
    if self_header:
        deps.add(self_header)
    for target_id in file_item.get("imports_allowed", []) if isinstance(file_item.get("imports_allowed", []), list) else []:
        target = file_by_id.get(str(target_id))
        header_path = str(target.get("header_path", "")).strip() if isinstance(target, dict) else ""
        if header_path:
            deps.add(header_path)
    for function in file_functions:
        for callee_id in _callee_function_ids(function):
            callee = function_index.get(callee_id)
            callee_file = file_by_id.get(str(callee.get("file_id", ""))) if isinstance(callee, dict) else None
            header_path = str(callee_file.get("header_path", "")).strip() if isinstance(callee_file, dict) else ""
            if header_path:
                deps.add(header_path)
        refs: list[dict[str, Any]] = []
        signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
        refs.extend(extract_c_signature_type_refs(signature))
        for dependency in function.get("signature_dependencies", []) if isinstance(function.get("signature_dependencies", []), list) else []:
            if not isinstance(dependency, dict):
                continue
            refs.extend(extract_c_signature_type_refs(dependency.get("symbol_name") or dependency.get("type_ref") or ""))
        for ref in refs:
            key = str(ref.get("key", "")).strip()
            provider = provider_by_type_key.get(key)
            if provider:
                deps.add(provider)
    local_type_keys = _declared_type_keys(source_data)
    for interface in source_interfaces:
        if isinstance(interface, dict):
            for ref in extract_c_signature_type_refs(interface.get("SIGNATURE", "")):
                key = str(ref.get("key", "")).strip()
                provider = provider_by_type_key.get(key)
                if provider and key not in local_type_keys:
                    deps.add(provider)
    for ref in _type_refs_from_data(source_data, public_only=False):
        provider = provider_by_type_key.get(str(ref.get("key", "")).strip())
        if provider:
            deps.add(provider)
    return sorted(dep for dep in deps if dep)


def _compiler_sidecar_source(
    implementation_plan: dict[str, Any],
    modules: list[dict[str, Any]],
    files: list[dict[str, Any]],
    functions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        **implementation_plan,
        "modules": modules,
        "files": files,
        "functions": functions,
    }


def _file_trace_id(protocol: str, file_item: dict[str, Any]) -> str:
    module_id = safe_slug(str(file_item.get("module_id") or "module"))
    file_id = safe_slug(str(file_item.get("file_id") or "file"))
    return f"{safe_slug(protocol)}/{module_id}/{file_id}"


def _implemented_ids(file_item: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    values.extend(file_item.get("implements", []) if isinstance(file_item.get("implements"), list) else [])
    values.extend(file_item.get("implements_function_ids", []) if isinstance(file_item.get("implements_function_ids"), list) else [])
    return {str(item).strip() for item in values if str(item).strip()}


def _attach_canonical_symbols(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: set[str] = set()
    lowered: list[dict[str, Any]] = []
    for function in functions:
        item = dict(function)
        base = canonical_function_symbol(item)
        symbol = base
        if symbol in used:
            module_slug = safe_slug(str(item.get("module_id") or "module")).replace("-", "_")
            symbol = f"{module_slug}_{base}" if module_slug else base
        counter = 2
        while symbol in used:
            symbol = f"{base}_{counter}"
            counter += 1
        item["_coder_symbol"] = symbol
        used.add(symbol)
        lowered.append(item)
    return lowered


def _topological_generation_order(module_ids: list[str], deps_by_module: dict[str, set[str]]) -> list[str]:
    ordered: list[str] = []
    permanent: set[str] = set()
    temporary: set[str] = set()

    def visit(module_id: str) -> bool:
        if module_id in permanent:
            return True
        if module_id in temporary:
            return False
        temporary.add(module_id)
        for dependency in sorted(deps_by_module.get(module_id, set())):
            if dependency in deps_by_module and not visit(dependency):
                return False
        temporary.remove(module_id)
        permanent.add(module_id)
        ordered.append(module_id)
        return True

    for module_id in module_ids:
        if not visit(module_id):
            return module_ids
    return ordered


def _function_spec(
    function: dict[str, Any],
    trace_id: str,
    function_index: dict[str, dict[str, Any]],
    access_by_id: dict[str, dict[str, Any]],
    access_by_field: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    function_type, body_key, body = lower_event_or_logic_for_coder(function)
    call_contracts: list[dict[str, Any]] = []
    for edge in function.get("call_contracts", []) if isinstance(function.get("call_contracts"), list) else []:
        if not isinstance(edge, dict):
            continue
        lowered = lower_call_contract_for_coder(edge, function_index)
        if lowered is None:
            unresolved.append(
                {
                    "kind": "call_contract",
                    "function_id": function.get("function_id", ""),
                    "callee_function_id": edge.get("callee_function_id", ""),
                    "reason": "Unable to resolve planning function id to C symbol/signature.",
                }
            )
            continue
        call_contracts.append(lowered)

    access_entries = [
        access_by_id[item]
        for item in function.get("access_paths", [])
        if isinstance(item, str) and item in access_by_id
    ]
    wire_mappings: list[dict[str, str]] = []
    for item in function.get("wire_mapping", []) if isinstance(function.get("wire_mapping"), list) else []:
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("field_id", "")).strip()
        access = access_by_id.get(str(item.get("access_path_id", "")), {}) or access_by_field.get(field_id, {})
        packet = str(item.get("message", "")).strip()
        wire_field = str(item.get("field", "") or item.get("wire_field", "") or item.get("field_id", "")).strip()
        if not packet or not wire_field:
            continue
        access_path = str(access.get("path") or "").strip()
        target_path = str(item.get("target_path") or "").strip()
        target = access_path if access_path and (not target_path or target_path == "buffer" or str(item.get("direction", "")).strip() == "serialize") else target_path or access_path
        strategy = str(item.get("strategy") or "").strip()
        mapping = {
            "PACKET": packet,
            "WIRE_FIELD": wire_field,
            "STRATEGY": strategy or ("store_in_field" if access.get("path") else "parse_and_skip"),
        }
        if target:
            mapping["TARGET"] = target
        if str(item.get("source_expr", "")).strip():
            mapping["SOURCE"] = str(item.get("source_expr", "")).strip()
        if str(item.get("rule", "")).strip():
            mapping["RULE"] = str(item.get("rule", "")).strip()
        wire_mappings.append(mapping)

    spec = {
        "KIND": "FUNCTION_SPEC",
        "TRACE_ID": trace_id,
        "FUNCTION_TYPE": function_type,
        "ROLE": str(function.get("purpose", "")) or "Function behavior generated from planning IR.",
        "SIGNATURE": lower_signature_for_coder(function),
        "RELY": lower_rely_for_coder(function, function_index),
        body_key: body,
    }
    forbidden = lower_forbidden_symbols_for_coder(function.get("forbidden_symbols", []))
    if forbidden:
        spec["FORBIDDEN_SYMBOLS"] = forbidden
    if call_contracts:
        spec["CALL_CONTRACTS"] = call_contracts
    lowered_access = lower_access_paths_for_coder(access_entries)
    if lowered_access:
        spec["ACCESS_PATHS"] = lowered_access
    if wire_mappings:
        spec["WIRE_MAPPING"] = wire_mappings
        test_vectors = [
            {
                "NAME": f"smoke_{safe_slug(mapping.get('PACKET', 'packet'))}_{safe_slug(mapping.get('WIRE_FIELD', 'field'))}",
                "INPUT": {
                    "packet": mapping.get("PACKET", ""),
                    "wire_field": mapping.get("WIRE_FIELD", ""),
                    "strategy": mapping.get("STRATEGY", ""),
                },
                "EXPECT": {
                    "target": mapping.get("TARGET", ""),
                    "rule": mapping.get("RULE", ""),
                    "coder_action": "exercise_wire_mapping",
                },
            }
            for mapping in wire_mappings[:4]
        ]
        if test_vectors:
            spec["TEST_VECTORS"] = test_vectors
    return spec, unresolved


def _derived_forbidden_symbols(canonical_types: list[dict[str, Any]]) -> list[dict[str, str]]:
    forbidden: list[dict[str, str]] = []
    seen: set[str] = set()
    for type_item in canonical_types:
        if not isinstance(type_item, dict):
            continue
        fields = [field for field in type_item.get("fields", []) if isinstance(field, dict)]
        field_names = {str(field.get("field_name", "")).strip() for field in fields}
        if "v" not in field_names or "data" in field_names:
            continue
        symbol = canonical_type_symbol(type_item)
        if not symbol:
            continue
        name = f"{symbol}.data"
        if name in seen:
            continue
        seen.add(name)
        forbidden.append(
            {
                "NAME": name,
                "KIND": "FIELD",
                "REASON": f"{symbol} exposes canonical fields {', '.join(sorted(field_names))}; data is not a public field.",
            }
        )
    return forbidden


def _data_declarations(
    protocol: str,
    module_id: str,
    file_item: dict[str, Any],
    module_item: dict[str, Any],
    file_functions: list[dict[str, Any]],
    canonical_type_index: dict[str, dict[str, Any]],
    module_type_inventory: list[dict[str, Any]],
    *,
    unresolved: list[dict[str, Any]],
    allow_unlisted_public_inventory: bool,
    declare_public_header_types: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    handle_type = default_handle_type(protocol, module_id)
    header_data = (
        [
            {
                "NAME": handle_type,
                "KIND": "TYPE",
                "VISIBILITY": "PUBLIC",
                "ROLE": "Opaque module context handle.",
            }
        ]
        if declare_public_header_types
        else []
    )
    source_data = [
        {
            "NAME": f"struct {handle_type[:-2]}",
            "KIND": "TYPE",
            "VISIBILITY": "PRIVATE",
            "ROLE": "Private module context storage.",
            "TYPE_SPEC": {"TYPE_KIND": "STRUCT", "FIELDS": []},
        }
    ]
    seen_header = {handle_type} if declare_public_header_types else set()
    seen_header_keys = {normalize_type_key(handle_type)} if declare_public_header_types else set()
    header_type_specs_by_key: dict[str, dict[str, Any]] = {normalize_type_key(handle_type): {"TYPE_KIND": "OPAQUE"}} if declare_public_header_types else {}
    seen_source = {f"struct {handle_type[:-2]}"}
    exported_type_keys = {
        normalize_type_key(value)
        for value in file_item.get("exports_type_ids", [])
        if normalize_type_key(value)
    }

    def add_inventory_type(type_item: dict[str, Any]) -> None:
        name = str(type_item.get("name", "")).strip()
        if not name:
            return
        target_public = str(type_item.get("visibility", "")) == "public" and str(type_item.get("defined_in", "")) == "public_header"
        if target_public and not declare_public_header_types:
            return
        type_keys = _type_ref_keys(type_item)
        if target_public and not allow_unlisted_public_inventory and not (type_keys & exported_type_keys):
            return
        target = header_data if target_public else source_data
        seen = seen_header if target_public else seen_source
        seen_keys = seen_header_keys if target_public else {normalize_type_key(item) for item in seen_source}
        key = normalize_type_key(name)
        if name in seen or key in seen_keys:
            return
        declaration = {
            "NAME": name,
            "KIND": "TYPE",
            "VISIBILITY": normalize_data_visibility_for_coder(type_item.get("visibility")),
            "ROLE": str(type_item.get("purpose", "")) or "Planned module type.",
        }
        kind = str(type_item.get("kind", "opaque_handle"))
        fields = [field for field in type_item.get("fields", []) if isinstance(field, dict)]
        enum_values = [item for item in type_item.get("enum_values", []) if isinstance(item, dict)]
        if kind in {"struct", "config_struct", "event_struct", "view_struct", "result_struct", "owned_buffer", "internal_state"} and fields:
            declaration["TYPE_SPEC"] = {
                "TYPE_KIND": "STRUCT",
                "FIELDS": [
                    {
                        "NAME": str(field.get("field_name", "")),
                        "TYPE": _canonical_field_type(field.get("field_type", "")),
                        "ROLE": str(field.get("validation_notes") or field.get("lifetime") or "Planned type field."),
                    }
                    for field in fields
                    if str(field.get("field_name", "")).strip()
                ],
            }
        elif kind in {"enum", "bitflag"}:
            declaration["TYPE_SPEC"] = {
                "TYPE_KIND": "ENUM",
                "ENUM_VALUES": [
                    {"NAME": str(item.get("name", "")), "VALUE": str(item.get("value", "")), "ROLE": str(item.get("role", ""))}
                    for item in enum_values
                    if str(item.get("name", "")).strip()
                ],
            }
        elif kind == "callback_type":
            declaration["TYPE_SPEC"] = {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": callback_signature_for_coder(type_item, name)}
        elif kind == "alias":
            declaration["TYPE_SPEC"] = {"TYPE_KIND": "ALIAS", "ALIAS_OF": str(type_item.get("ownership_lifetime") or "uint8_t")}
        elif target_public:
            declaration["TYPE_SPEC"] = {"TYPE_KIND": "OPAQUE"}
        target.append(declaration)
        seen.add(name)
        if target_public:
            type_spec = declaration.get("TYPE_SPEC", {"TYPE_KIND": "OPAQUE"})
            for type_key in type_keys | {key}:
                if type_key:
                    seen_header_keys.add(type_key)
                    header_type_specs_by_key[type_key] = type_spec

    for type_item in module_type_inventory:
        add_inventory_type(type_item)

    def add_public_type(type_item: dict[str, Any], *, role: str = "", ref: dict[str, Any] | None = None) -> None:
        symbol = canonical_type_symbol(type_item)
        key = normalize_type_key(symbol)
        if key in seen_header_keys:
            return
        declaration = lower_canonical_type_to_header_data(type_item)
        if declaration is None:
            _add_unresolved(unresolved, kind="public_type_symbol", module_id=module_id, file_id=str(file_item.get("file_id", "")), type_name=symbol, reason="Resolved public canonical type does not have a valid C symbol.")
            return
        type_spec = declaration.get("TYPE_SPEC", {}) if isinstance(declaration.get("TYPE_SPEC"), dict) else {}
        if ref and not bool(ref.get("pointer")) and type_spec.get("TYPE_KIND") == "OPAQUE":
            _add_unresolved(
                unresolved,
                kind="public_type_by_value_opaque",
                module_id=module_id,
                file_id=str(file_item.get("file_id", "")),
                function_id=str(ref.get("function_id", "")),
                type_name=str(ref.get("raw", symbol)),
                reason="Public function signature uses an opaque/unresolved public type by value.",
            )
        header_data.append(declaration)
        seen_header.add(str(declaration["NAME"]))
        seen_header_keys.add(key)
        header_type_specs_by_key[key] = type_spec
        if role:
            module_item.setdefault("resolved_public_type_roles", {})[role] = str(declaration["NAME"])

    def current_file_exports_type(type_item: dict[str, Any]) -> bool:
        return allow_unlisted_public_inventory or bool(_type_ref_keys(type_item) & exported_type_keys)

    expected_roles = []
    if declare_public_header_types:
        expected_roles.extend(str(role) for role in file_item.get("exports_type_ids", []) if str(role).strip())
    for role in expected_roles:
        if normalize_type_key(role) in seen_header_keys:
            module_item.setdefault("resolved_public_type_roles", {})[role] = role
            continue
        type_item = _resolve_canonical_type(role, canonical_type_index)
        if type_item is None:
            _add_unresolved(unresolved, kind="public_type_role", module_id=module_id, file_id=str(file_item.get("file_id", "")), type_name=role, reason="Expected public type role could not be resolved to a canonical type.")
            continue
        if str(type_item.get("owner_module_id", module_id)) == module_id:
            add_public_type(type_item, role=role)
        else:
            module_item.setdefault("resolved_public_type_roles", {})[role] = canonical_type_symbol(type_item)

    for function in file_functions:
        for item in function.get("interface_type_declarations", []) if isinstance(function.get("interface_type_declarations"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            key = normalize_type_key(name)
            if not name or name in seen_header or key in seen_header_keys:
                continue
            declaration = {
                "NAME": name,
                "KIND": "TYPE",
                "VISIBILITY": normalize_data_visibility_for_coder(item.get("visibility")),
                "ROLE": str(item.get("reason", "")) or "Interface type declaration.",
            }
            if declaration["VISIBILITY"] == "PUBLIC" and not declare_public_header_types:
                continue
            seen_header.add(name)
            seen_header_keys.add(key)
            if declaration["VISIBILITY"] == "PUBLIC":
                declaration["TYPE_SPEC"] = {"TYPE_KIND": "OPAQUE"}
                header_type_specs_by_key[key] = declaration["TYPE_SPEC"]
            header_data.append(declaration)

    for function in file_functions:
        if not is_public_interface_function(function, file_item, module_item):
            continue
        signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
        for ref in extract_c_signature_type_refs(signature):
            if ref["key"] in seen_header_keys:
                if not bool(ref.get("pointer")) and header_type_specs_by_key.get(ref["key"], {}).get("TYPE_KIND") == "OPAQUE":
                    _add_unresolved(
                        unresolved,
                        kind="public_type_by_value_opaque",
                        module_id=module_id,
                        file_id=str(file_item.get("file_id", "")),
                        function_id=str(function.get("function_id", "")),
                        type_name=str(ref.get("raw", "")),
                        reason="Public function signature uses an opaque/unresolved public type by value.",
                    )
                continue
            type_item = _resolve_canonical_type(ref.get("raw"), canonical_type_index) or _resolve_canonical_type(ref.get("name"), canonical_type_index)
            if type_item is None:
                _add_unresolved(
                    unresolved,
                    kind="public_signature_type",
                    module_id=module_id,
                    file_id=str(file_item.get("file_id", "")),
                    function_id=str(function.get("function_id", "")),
                    type_name=str(ref.get("raw", "")),
                    reason="Public function signature references a non-primitive type that is not declared in HEADER.DATA or canonical_types.",
                )
                continue
            if str(type_item.get("owner_module_id", module_id)) == module_id and current_file_exports_type(type_item):
                ref_with_function = dict(ref)
                ref_with_function["function_id"] = function.get("function_id", "")
                add_public_type(type_item, ref=ref_with_function)

    for function in file_functions:
        for item in function.get("internal_type_refs", []) if isinstance(function.get("internal_type_refs"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("symbol_name", "")).strip()
            if not name or name in seen_source:
                continue
            seen_source.add(name)
            source_data.append(
                {
                    "NAME": name,
                    "KIND": "TYPE",
                    "VISIBILITY": normalize_data_visibility_for_coder(item.get("visibility")),
                    "ROLE": str(item.get("role", "")) or "Internal type reference.",
                }
            )
    return _sort_header_data_by_local_type_dependencies(header_data), source_data


def compile_spec_bundle(implementation_plan: dict[str, Any], output_dir: str | Path) -> tuple[dict[str, Any], Path]:
    root = Path(output_dir)
    spec_root = root / "spec_bundle"
    if spec_root.exists():
        shutil.rmtree(spec_root)
    spec_root.mkdir(parents=True, exist_ok=True)

    protocol_meta = lower_protocol_meta_for_coder(implementation_plan)
    protocol = str(protocol_meta["NAME"])
    modules = _plan_modules(implementation_plan)
    functions = _attach_canonical_symbols(_plan_functions(implementation_plan))
    access_path_table = [item for item in implementation_plan.get("access_path_table", []) if isinstance(item, dict)]
    canonical_types = [item for item in implementation_plan.get("canonical_types", []) if isinstance(item, dict)]
    type_inventory = [item for item in implementation_plan.get("type_inventory", []) if isinstance(item, dict)]
    files = _normalize_public_type_owner_files(_plan_files(implementation_plan), canonical_types, type_inventory)
    canonical_type_index = _canonical_type_index(canonical_types)
    access_by_id = {str(item.get("access_path_id", "")): item for item in access_path_table if str(item.get("access_path_id", "")).strip()}
    access_by_field = {str(item.get("field_id", "")): item for item in access_path_table if str(item.get("field_id", "")).strip()}
    provider_by_type_key = _header_provider_index(files, canonical_types, type_inventory)
    module_header_counts: dict[str, int] = {}
    for item in files:
        if str(item.get("header_path", "")).strip() and not _is_source_only_entrypoint(item):
            module_header_counts[str(item.get("module_id", ""))] = module_header_counts.get(str(item.get("module_id", "")), 0) + 1
    owner_header_by_module = _module_public_type_owner_headers(files)
    functions_by_file: dict[str, list[dict[str, Any]]] = {}
    for function in functions:
        functions_by_file.setdefault(str(function.get("file_id", "")), []).append(function)
    function_index = {str(function.get("function_id", "")): function for function in functions if str(function.get("function_id", "")).strip()}
    file_by_id = {str(item.get("file_id", "")): item for item in files}
    module_by_id = {str(item.get("module_id", "")): item for item in modules if str(item.get("module_id", "")).strip()}
    module_ids = [str(item.get("module_id", "")) for item in modules if str(item.get("module_id", "")).strip()]
    deps_by_module = _module_dependencies_from_plan(implementation_plan, module_ids)
    generation_order = _topological_generation_order(module_ids, deps_by_module)
    file_spec_paths: list[str] = []
    function_spec_paths: list[str] = []
    file_specs_by_file_id: dict[str, dict[str, Any]] = {}
    unresolved_lowering: list[dict[str, Any]] = []
    for file_item in files:
        file_id = str(file_item.get("file_id", "file"))
        file_trace_id = _file_trace_id(protocol, file_item)
        module_id = str(file_item.get("module_id", "module"))
        module_item = module_by_id.get(module_id, {})
        implemented_ids = _implemented_ids(file_item)
        file_functions = [function for function in functions_by_file.get(file_id, []) if not implemented_ids or str(function.get("function_id", "")) in implemented_ids]
        source_only = _is_source_only_entrypoint(file_item)
        current_header = str(file_item.get("header_path", "")).strip()
        owner_header = owner_header_by_module.get(module_id, current_header)
        declare_public_header_types = not owner_header or current_header == owner_header
        if source_only:
            header_data, source_data = [], []
        else:
            header_data, source_data = _data_declarations(
                protocol,
                module_id,
                file_item,
                module_item,
                file_functions,
                canonical_type_index,
                [item for item in type_inventory if str(item.get("module_id", "")) == module_id],
                unresolved=unresolved_lowering,
                allow_unlisted_public_inventory=module_header_counts.get(module_id, 0) <= 1,
                declare_public_header_types=declare_public_header_types,
            )
        source_interfaces: list[dict[str, Any]] = []
        header_interfaces: list[dict[str, Any]] = []
        spec_stem = _normalized_spec_stem(file_item)
        for function in file_functions:
            function_trace_id = _function_trace_id(file_trace_id, function)
            is_public = is_public_interface_function(function, file_item, module_item)
            if is_public:
                unresolved_lowering.extend(_public_signature_unresolved(function))
            source_interfaces.append(_source_interface(function, function_trace_id, public=is_public))
            if is_public:
                header_interfaces.append(_header_interface(function))
            function_spec, unresolved = _function_spec(function, function_trace_id, function_index, access_by_id, access_by_field)
            unresolved_lowering.extend(unresolved)
            spec_dir = spec_root / spec_stem
            fn_slug = safe_slug(canonical_function_symbol(function))
            fn_filename = f"{fn_slug}_function_spec.json" if fn_slug == spec_stem.name else f"{fn_slug}_spec.json"
            fn_path = write_json(spec_dir / fn_filename, function_spec)
            function_spec_paths.append(str(fn_path))

        source_dependencies = _source_dependency_headers(
            file_item,
            file_functions,
            source_data,
            source_interfaces,
            file_by_id,
            function_index,
            provider_by_type_key,
        )
        file_access_ids = {
            access_id
            for function in file_functions
            for access_id in function.get("access_paths", [])
            if isinstance(access_id, str)
        }
        file_spec = {
            "KIND": "FILE_SPEC",
            "FILE": {
                "TRACE_ID": file_trace_id,
                "LANG": "C",
                "ROLE": str(file_item.get("responsibility", "")) or "Generated C source/header unit.",
                "DOC_REF": lower_doc_ref(file_item),
            },
            "SOURCE": {
                "PATH": str(file_item.get("source_path", "")),
                "DEPENDENCY": source_dependencies,
                "DATA": source_data,
                "INTERFACE": source_interfaces,
            },
        }
        if not source_only:
            header_dependencies = _public_header_dependency_headers(
                file_item,
                header_data,
                header_interfaces,
                provider_by_type_key,
                unresolved_lowering,
            )
            if header_interfaces and owner_header and current_header and owner_header != current_header:
                header_dependencies = sorted(set(header_dependencies) | {owner_header})
            file_spec["HEADER"] = {
                "PATH": str(file_item.get("header_path", "")),
                "DEPENDENCY": header_dependencies,
                "SYSTEM_DEPENDENCY": _public_header_system_dependencies(header_data, header_interfaces),
                "DATA": header_data,
                "INTERFACE": header_interfaces,
            }
        public_symbols = lower_module_artifacts_for_coder([file_spec])
        if public_symbols:
            file_spec["PUBLIC_SYMBOLS"] = public_symbols
        file_access = [access_by_id[access_id] for access_id in file_access_ids if access_id in access_by_id]
        lowered_access = lower_access_paths_for_coder(file_access)
        if lowered_access:
            file_spec["ACCESS_PATHS"] = lowered_access
        file_spec_path = write_json(spec_root / spec_stem / f"{spec_stem.name}_spec.json", file_spec)
        file_spec_paths.append(str(file_spec_path))
        file_specs_by_file_id[file_id] = file_spec

    module_entries: list[dict[str, Any]] = []
    all_public_symbols: list[dict[str, str]] = []
    for module in modules:
        module_id = str(module.get("module_id", "module"))
        module_files = [item for item in files if item.get("module_id") == module_id]
        module_file_specs = [file_specs_by_file_id[str(item.get("file_id", ""))] for item in module_files if str(item.get("file_id", "")) in file_specs_by_file_id]
        planned_artifacts = lower_planned_module_artifacts_for_coder(module.get("artifacts", []))
        artifacts = merge_coder_artifacts(planned_artifacts, lower_module_artifacts_for_coder(module_file_specs))
        all_public_symbols.extend({"NAME": item["NAME"], "KIND": item["KIND"], "ROLE": item["ROLE"]} for item in artifacts)
        planned_files = [str(path) for path in module.get("files", []) if str(path).strip()]
        lowered_files = [
            str(path)
            for item in module_files
            for path in (item.get("header_path", ""), item.get("source_path", ""))
            if str(path).strip()
        ]
        module_entries.append(
            {
                "NAME": module_id,
                "ROLE": str(module.get("role", "")) or "Planning module.",
                "DEPENDENCIES": sorted(set(str(dep) for dep in module.get("dependencies", []) if str(dep).strip()) | deps_by_module.get(module_id, set())),
                "ARTIFACTS": artifacts,
                "FILES": lowered_files or planned_files,
                "DOC_REF": lower_doc_ref(module),
            }
        )
    module_spec = {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": protocol_meta,
        "MODULES": module_entries,
        "GENERATION_ORDER": generation_order
        or [str(item) for item in implementation_plan.get("module_generation_order", []) if str(item).strip()]
        or module_ids,
        "CONSISTENCY_RULES": [
            {"ID": "C1", "RULE": "file_function_trace_ids_must_match", "DOC_REF": []},
            {"ID": "C2", "RULE": "public_functions_declared_in_headers", "DOC_REF": []},
            *[
                {"ID": str(item.get("id", "")), "RULE": str(item.get("rule", "")), "DOC_REF": lower_doc_ref(item)}
                for item in implementation_plan.get("module_consistency_rules", [])
                if isinstance(item, dict) and str(item.get("id", "")).strip() and str(item.get("rule", "")).strip()
            ],
        ],
    }
    forbidden = lower_forbidden_symbols_for_coder([*implementation_plan.get("forbidden_symbols", []), *_derived_forbidden_symbols(canonical_types)])
    if forbidden:
        module_spec["FORBIDDEN_SYMBOLS"] = forbidden
    module_test_vectors = [
        {
            "NAME": safe_slug(item.get("test_id") or item.get("name") or f"test_{index}"),
            "INPUT": {"scenario": str(item.get("purpose", "")), "trace_ref_keys": item.get("trace_ref_keys", []) if isinstance(item.get("trace_ref_keys"), list) else []},
            "EXPECT": {"status": str(item.get("status", "inferred")), "coder_action": "preserve_protocol_behavior"},
            "LEVEL": str(item.get("level", "RUNTIME")).upper(),
            "TRACE_REFS": item.get("trace_ref_keys", []) if isinstance(item.get("trace_ref_keys"), list) else [],
        }
        for index, item in enumerate(implementation_plan.get("test_plan", []))
        if isinstance(item, dict) and str(item.get("purpose", "")).strip()
    ]
    if module_test_vectors:
        module_spec["TEST_VECTORS"] = module_test_vectors
    if all_public_symbols:
        seen: set[tuple[str, str]] = set()
        module_spec["PUBLIC_SYMBOLS"] = []
        for item in all_public_symbols:
            key = (item["NAME"], item["KIND"])
            if key not in seen:
                seen.add(key)
                module_spec["PUBLIC_SYMBOLS"].append(item)
    module_spec_path = write_json(spec_root / f"{safe_slug(protocol)}_module_spec.json", module_spec)

    traceability_sidecar, decisions_sidecar, refs_sidecar = sidecar_payload(_compiler_sidecar_source(implementation_plan, modules, files, functions), unresolved_lowering)
    sidecar_paths = [
        root / "planning_traceability.json",
        root / "planning_decisions.json",
        root / "planning_ir_refs.json",
    ]
    write_json(sidecar_paths[0], traceability_sidecar)
    write_json(sidecar_paths[1], decisions_sidecar)
    write_json(sidecar_paths[2], refs_sidecar)

    manifest = {
        "schema_version": CODER_MANIFEST_SCHEMA_VERSION,
        "protocol_name": protocol,
        "spec_root": str(spec_root),
        "module_spec_path": str(module_spec_path),
        "file_spec_paths": file_spec_paths,
        "function_spec_paths": function_spec_paths,
        "sidecar_paths": [str(path) for path in sidecar_paths],
        "compatibility_target": "specs_schema_then_agent.coder.specs.load_spec_bundle_from_root",
    }
    manifest_path = write_json(root / "coder_manifest.json", manifest)
    return manifest, manifest_path
