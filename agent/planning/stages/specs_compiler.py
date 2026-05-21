from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..artifact_io import safe_slug, write_json
from ..schemas.coder_manifest import SCHEMA_VERSION as CODER_MANIFEST_SCHEMA_VERSION
from .coder_spec_lowering import (
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
    lower_protocol_meta_for_coder,
    lower_rely_for_coder,
    lower_signature_for_coder,
    normalize_type_key,
    normalize_data_visibility_for_coder,
    normalize_function_type_for_coder,
    sidecar_payload,
    signature_raw,
)


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


def _function_trace_id(file_trace_id: str, function: dict[str, Any]) -> str:
    return f"{file_trace_id}/{function.get('name', 'function')}"


def _header_interface(function: dict[str, Any]) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return {
        "SIGNATURE": signature_raw(signature),
        "NAME": str(function.get("name", signature.get("name", ""))),
        "KIND": "FUNC",
        "FUNCTION_TYPE": normalize_function_type_for_coder(function),
        "ROLE": str(function.get("purpose", "")) or "Public function.",
        "VISIBILITY": "public",
    }


def _source_interface(function: dict[str, Any], trace_id: str, *, public: bool = False) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return {
        "TRACE_ID": trace_id,
        "SIGNATURE": signature_raw(signature),
        "NAME": str(function.get("name", signature.get("name", ""))),
        "KIND": "FUNC",
        "FUNCTION_TYPE": normalize_function_type_for_coder(function),
        "ROLE": str(function.get("purpose", "")) or "Implemented function.",
        "CONTRACT": lower_contract_for_coder(function),
        "VISIBILITY": "public" if public else "private",
    }


def _public_signature_unresolved(function: dict[str, Any]) -> list[dict[str, Any]]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    missing = [
        key
        for key in ("raw", "name", "return_type")
        if not str(signature.get(key, "")).strip()
    ]
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


def _function_spec(function: dict[str, Any], trace_id: str, function_index: dict[str, dict[str, Any]], access_by_id: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        access = access_by_id.get(str(item.get("access_path_id", "")), {})
        packet = str(item.get("message", "")).strip()
        wire_field = str(item.get("field", "") or item.get("field_id", "")).strip()
        if not packet or not wire_field:
            continue
        mapping = {
            "PACKET": packet,
            "WIRE_FIELD": wire_field,
            "STRATEGY": "store_in_field" if access.get("path") else "parse_and_skip",
        }
        if access.get("path"):
            mapping["TARGET"] = str(access["path"])
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
    return spec, unresolved


def _data_declarations(
    protocol: str,
    module_id: str,
    file_item: dict[str, Any],
    module_item: dict[str, Any],
    file_functions: list[dict[str, Any]],
    canonical_type_index: dict[str, dict[str, Any]],
    *,
    include_module_public_roles: bool,
    unresolved: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    handle_type = default_handle_type(protocol, module_id)
    header_data = [
        {
            "NAME": handle_type,
            "KIND": "TYPE",
            "VISIBILITY": "PUBLIC",
            "ROLE": "Opaque module context handle.",
        }
    ]
    source_data = [
        {
            "NAME": f"struct {handle_type[:-2]}",
            "KIND": "TYPE",
            "VISIBILITY": "PRIVATE",
            "ROLE": "Private module context storage.",
            "TYPE_SPEC": {"TYPE_KIND": "STRUCT", "FIELDS": []},
        }
    ]
    seen_header = {handle_type}
    seen_header_keys = {normalize_type_key(handle_type)}
    header_type_specs_by_key: dict[str, dict[str, Any]] = {normalize_type_key(handle_type): {"TYPE_KIND": "OPAQUE"}}
    seen_source = {f"struct {handle_type[:-2]}"}

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

    expected_roles = []
    if include_module_public_roles:
        policy = module_item.get("public_api_policy", {}) if isinstance(module_item.get("public_api_policy"), dict) else {}
        expected_roles.extend(str(role) for role in policy.get("expected_public_type_roles", []) if str(role).strip())
    expected_roles.extend(str(role) for role in file_item.get("exports_type_ids", []) if str(role).strip())
    for role in expected_roles:
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
            seen_header.add(name)
            seen_header_keys.add(key)
            declaration = {
                "NAME": name,
                "KIND": "TYPE",
                "VISIBILITY": normalize_data_visibility_for_coder(item.get("visibility")),
                "ROLE": str(item.get("reason", "")) or "Interface type declaration.",
            }
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
            if str(type_item.get("owner_module_id", module_id)) == module_id:
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
    return header_data, source_data


def compile_spec_bundle(spec_blueprint: dict[str, Any], output_dir: str | Path) -> tuple[dict[str, Any], Path]:
    root = Path(output_dir)
    spec_root = root / "spec_bundle"
    if spec_root.exists():
        shutil.rmtree(spec_root)
    spec_root.mkdir(parents=True, exist_ok=True)

    protocol_meta = lower_protocol_meta_for_coder(spec_blueprint)
    protocol = str(protocol_meta["NAME"])
    modules = [item for item in spec_blueprint.get("modules", []) if isinstance(item, dict)]
    files = [item for item in spec_blueprint.get("files", []) if isinstance(item, dict)]
    functions = [item for item in spec_blueprint.get("functions", []) if isinstance(item, dict)]
    access_path_table = [item for item in spec_blueprint.get("access_path_table", []) if isinstance(item, dict)]
    canonical_types = [item for item in spec_blueprint.get("canonical_types", []) if isinstance(item, dict)]
    canonical_type_index = _canonical_type_index(canonical_types)
    access_by_id = {str(item.get("access_path_id", "")): item for item in access_path_table if str(item.get("access_path_id", "")).strip()}
    functions_by_file: dict[str, list[dict[str, Any]]] = {}
    for function in functions:
        functions_by_file.setdefault(str(function.get("file_id", "")), []).append(function)
    function_index = {str(function.get("function_id", "")): function for function in functions if str(function.get("function_id", "")).strip()}
    file_by_id = {str(item.get("file_id", "")): item for item in files}
    module_by_id = {str(item.get("module_id", "")): item for item in modules if str(item.get("module_id", "")).strip()}
    primary_file_by_module: dict[str, str] = {}
    for item in files:
        module_id = str(item.get("module_id", ""))
        file_id = str(item.get("file_id", ""))
        if module_id and file_id:
            primary_file_by_module.setdefault(module_id, file_id)

    file_spec_paths: list[str] = []
    function_spec_paths: list[str] = []
    file_specs_by_file_id: dict[str, dict[str, Any]] = {}
    unresolved_lowering: list[dict[str, Any]] = []
    for file_item in files:
        file_id = str(file_item.get("file_id", "file"))
        file_trace_id = str(file_item.get("trace_id", "")).strip() or str(file_id).removeprefix("file:").replace(":", "_")
        module_id = str(file_item.get("module_id", "module"))
        module_item = module_by_id.get(module_id, {})
        file_functions = functions_by_file.get(file_id, [])
        imported_headers = [
            str(file_by_id[target].get("header_path", ""))
            for target in file_item.get("imports_allowed", [])
            if str(target) in file_by_id and str(file_by_id[target].get("header_path", "")).strip()
        ]
        header_data, source_data = _data_declarations(
            protocol,
            module_id,
            file_item,
            module_item,
            file_functions,
            canonical_type_index,
            include_module_public_roles=primary_file_by_module.get(module_id) == file_id,
            unresolved=unresolved_lowering,
        )
        source_interfaces: list[dict[str, Any]] = []
        header_interfaces: list[dict[str, Any]] = []
        for function in file_functions:
            function_trace_id = _function_trace_id(file_trace_id, function)
            is_public = is_public_interface_function(function, file_item, module_item)
            if is_public:
                unresolved_lowering.extend(_public_signature_unresolved(function))
            source_interfaces.append(_source_interface(function, function_trace_id, public=is_public))
            if is_public:
                header_interfaces.append(_header_interface(function))
            function_spec, unresolved = _function_spec(function, function_trace_id, function_index, access_by_id)
            unresolved_lowering.extend(unresolved)
            spec_dir = spec_root / _normalized_spec_stem(file_item)
            fn_path = write_json(spec_dir / f"{safe_slug(str(function.get('name', 'function')))}_spec.json", function_spec)
            function_spec_paths.append(str(fn_path))

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
            "HEADER": {
                "PATH": str(file_item.get("header_path", "")),
                "DEPENDENCY": sorted({header for header in imported_headers if header != file_item.get("header_path", "")}),
                "DATA": header_data,
                "INTERFACE": header_interfaces,
            },
            "SOURCE": {
                "PATH": str(file_item.get("source_path", "")),
                "DEPENDENCY": [
                    dep
                    for dep in [str(file_item.get("header_path", "")), *sorted(set(imported_headers))]
                    if dep
                ],
                "DATA": source_data,
                "INTERFACE": source_interfaces,
            },
        }
        public_symbols = lower_module_artifacts_for_coder([file_spec])
        if public_symbols:
            file_spec["PUBLIC_SYMBOLS"] = public_symbols
        file_access = [access_by_id[access_id] for access_id in file_access_ids if access_id in access_by_id]
        lowered_access = lower_access_paths_for_coder(file_access)
        if lowered_access:
            file_spec["ACCESS_PATHS"] = lowered_access
        spec_stem = _normalized_spec_stem(file_item)
        file_spec_path = write_json(spec_root / spec_stem / f"{spec_stem.name}_spec.json", file_spec)
        file_spec_paths.append(str(file_spec_path))
        file_specs_by_file_id[file_id] = file_spec

    module_entries: list[dict[str, Any]] = []
    all_public_symbols: list[dict[str, str]] = []
    for module in modules:
        module_id = str(module.get("module_id", "module"))
        module_files = [item for item in files if item.get("module_id") == module_id]
        module_file_specs = [file_specs_by_file_id[str(item.get("file_id", ""))] for item in module_files if str(item.get("file_id", "")) in file_specs_by_file_id]
        artifacts = lower_module_artifacts_for_coder(module_file_specs)
        all_public_symbols.extend({"NAME": item["NAME"], "KIND": item["KIND"], "ROLE": item["ROLE"]} for item in artifacts)
        module_entries.append(
            {
                "NAME": module_id,
                "ROLE": str(module.get("role", "")) or "Planning module.",
                "DEPENDENCIES": [str(dep) for dep in module.get("dependencies", []) if str(dep).strip()],
                "ARTIFACTS": artifacts,
                "FILES": [
                    str(path)
                    for item in module_files
                    for path in (item.get("header_path", ""), item.get("source_path", ""))
                    if str(path).strip()
                ],
                "DOC_REF": lower_doc_ref(module),
            }
        )
    module_spec = {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": protocol_meta,
        "MODULES": module_entries,
        "GENERATION_ORDER": [str(item) for item in spec_blueprint.get("generation_order", []) if str(item).strip()]
        or [str(item.get("module_id", "")) for item in modules if str(item.get("module_id", "")).strip()],
        "CONSISTENCY_RULES": [
            {"ID": "C1", "RULE": "file_function_trace_ids_must_match", "DOC_REF": []},
            {"ID": "C2", "RULE": "public_functions_declared_in_headers", "DOC_REF": []},
        ],
    }
    if all_public_symbols:
        seen: set[tuple[str, str]] = set()
        module_spec["PUBLIC_SYMBOLS"] = []
        for item in all_public_symbols:
            key = (item["NAME"], item["KIND"])
            if key not in seen:
                seen.add(key)
                module_spec["PUBLIC_SYMBOLS"].append(item)
    module_spec_path = write_json(spec_root / f"{safe_slug(protocol)}_module_spec.json", module_spec)

    traceability_sidecar, decisions_sidecar, refs_sidecar = sidecar_payload(spec_blueprint, unresolved_lowering)
    write_json(spec_root / "planning_traceability.json", traceability_sidecar)
    write_json(spec_root / "planning_decisions.json", decisions_sidecar)
    write_json(spec_root / "planning_ir_refs.json", refs_sidecar)

    manifest = {
        "schema_version": CODER_MANIFEST_SCHEMA_VERSION,
        "protocol_name": protocol,
        "spec_root": str(spec_root),
        "module_spec_path": str(module_spec_path),
        "file_spec_paths": file_spec_paths,
        "function_spec_paths": function_spec_paths,
        "sidecar_paths": [
            str(spec_root / "planning_traceability.json"),
            str(spec_root / "planning_decisions.json"),
            str(spec_root / "planning_ir_refs.json"),
        ],
        "compatibility_target": "specs_schema_then_agent.coder.specs.load_spec_bundle_from_root",
    }
    manifest_path = write_json(root / "coder_manifest.json", manifest)
    return manifest, manifest_path
