from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..artifact_io import safe_slug, write_json
from ..schemas.coder_manifest import SCHEMA_VERSION as CODER_MANIFEST_SCHEMA_VERSION
from .coder_spec_lowering import (
    default_handle_type,
    lower_access_paths_for_coder,
    lower_call_contract_for_coder,
    lower_contract_for_coder,
    lower_doc_ref,
    lower_event_or_logic_for_coder,
    lower_forbidden_symbols_for_coder,
    lower_module_artifacts_for_coder,
    lower_public_symbols_for_coder,
    lower_rely_for_coder,
    lower_signature_for_coder,
    normalize_data_visibility_for_coder,
    normalize_function_type_for_coder,
    normalize_interface_visibility_for_coder,
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


def _source_interface(function: dict[str, Any], trace_id: str) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return {
        "TRACE_ID": trace_id,
        "SIGNATURE": signature_raw(signature),
        "NAME": str(function.get("name", signature.get("name", ""))),
        "KIND": "FUNC",
        "FUNCTION_TYPE": normalize_function_type_for_coder(function),
        "ROLE": str(function.get("purpose", "")) or "Implemented function.",
        "CONTRACT": lower_contract_for_coder(function),
        "VISIBILITY": normalize_interface_visibility_for_coder(function.get("visibility")),
    }


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


def _data_declarations(protocol: str, module_id: str, file_functions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    seen_source = {f"struct {handle_type[:-2]}"}
    for function in file_functions:
        for item in function.get("interface_type_declarations", []) if isinstance(function.get("interface_type_declarations"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in seen_header:
                continue
            seen_header.add(name)
            header_data.append(
                {
                    "NAME": name,
                    "KIND": "TYPE",
                    "VISIBILITY": normalize_data_visibility_for_coder(item.get("visibility")),
                    "ROLE": str(item.get("reason", "")) or "Interface type declaration.",
                }
            )
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

    protocol = str(spec_blueprint.get("protocol_name") or "protocol")
    modules = [item for item in spec_blueprint.get("modules", []) if isinstance(item, dict)]
    files = [item for item in spec_blueprint.get("files", []) if isinstance(item, dict)]
    functions = [item for item in spec_blueprint.get("functions", []) if isinstance(item, dict)]
    access_path_table = [item for item in spec_blueprint.get("access_path_table", []) if isinstance(item, dict)]
    access_by_id = {str(item.get("access_path_id", "")): item for item in access_path_table if str(item.get("access_path_id", "")).strip()}
    functions_by_file: dict[str, list[dict[str, Any]]] = {}
    for function in functions:
        functions_by_file.setdefault(str(function.get("file_id", "")), []).append(function)
    function_index = {str(function.get("function_id", "")): function for function in functions if str(function.get("function_id", "")).strip()}
    file_by_id = {str(item.get("file_id", "")): item for item in files}

    module_entries: list[dict[str, Any]] = []
    all_public_symbols: list[dict[str, str]] = []
    for module in modules:
        module_id = str(module.get("module_id", "module"))
        module_files = [item for item in files if item.get("module_id") == module_id]
        artifacts = lower_module_artifacts_for_coder(module_files, functions_by_file)
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
        "PROTOCOL": {
            "NAME": protocol.upper() if protocol.lower() == "mqtt" else protocol,
            "SPEC_VERSION": str(spec_blueprint.get("protocol_version") or spec_blueprint.get("spec_version") or "unspecified"),
            "ROLES": [str(role).upper() for role in spec_blueprint.get("roles", []) if str(role).strip()] or ["BROKER"],
        },
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

    file_spec_paths: list[str] = []
    function_spec_paths: list[str] = []
    unresolved_lowering: list[dict[str, Any]] = []
    for file_item in files:
        file_id = str(file_item.get("file_id", "file"))
        file_trace_id = str(file_item.get("trace_id", "")).strip() or str(file_id).removeprefix("file:").replace(":", "_")
        module_id = str(file_item.get("module_id", "module"))
        file_functions = functions_by_file.get(file_id, [])
        imported_headers = [
            str(file_by_id[target].get("header_path", ""))
            for target in file_item.get("imports_allowed", [])
            if str(target) in file_by_id and str(file_by_id[target].get("header_path", "")).strip()
        ]
        header_data, source_data = _data_declarations(protocol, module_id, file_functions)
        source_interfaces: list[dict[str, Any]] = []
        header_interfaces: list[dict[str, Any]] = []
        for function in file_functions:
            function_trace_id = _function_trace_id(file_trace_id, function)
            source_interfaces.append(_source_interface(function, function_trace_id))
            if normalize_interface_visibility_for_coder(function.get("visibility")) == "public":
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
        public_symbols = lower_public_symbols_for_coder(file_item, file_functions)
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
        if public_symbols:
            file_spec["PUBLIC_SYMBOLS"] = public_symbols
        file_access = [access_by_id[access_id] for access_id in file_access_ids if access_id in access_by_id]
        lowered_access = lower_access_paths_for_coder(file_access)
        if lowered_access:
            file_spec["ACCESS_PATHS"] = lowered_access
        spec_stem = _normalized_spec_stem(file_item)
        file_spec_path = write_json(spec_root / spec_stem / f"{spec_stem.name}_spec.json", file_spec)
        file_spec_paths.append(str(file_spec_path))

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
