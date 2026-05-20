from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..artifact_io import safe_slug, write_json
from ..schemas.coder_manifest import SCHEMA_VERSION as CODER_MANIFEST_SCHEMA_VERSION


def _param_for_coder(param: dict[str, Any]) -> dict[str, Any]:
    return {
        "TYPE": str(param.get("type", "void")),
        "NAME": str(param.get("name", "")),
        "NULLABLE": bool(param.get("nullable", False)),
        "OWNERSHIP": str(param.get("ownership", "borrowed")),
    }


def _signature_raw(signature: dict[str, Any]) -> str:
    raw = str(signature.get("raw", "")).strip()
    if raw:
        return raw
    params = signature.get("params", [])
    rendered = ", ".join(f"{item.get('type', 'void')} {item.get('name', '')}".strip() for item in params) if params else "void"
    return f"{signature.get('return_type', 'int')} {signature.get('name', 'unnamed')}({rendered})"


def _interface(function: dict[str, Any], trace_id: str) -> dict[str, Any]:
    signature = function.get("signature", {})
    return {
        "TRACE_ID": trace_id,
        "SIGNATURE": _signature_raw(signature),
        "NAME": str(function.get("name", signature.get("name", ""))),
        "KIND": "FUNCTION",
        "FUNCTION_TYPE": str(function.get("function_kind", "public_api")).upper(),
        "ROLE": str(function.get("purpose", "")),
        "VISIBILITY": str(function.get("visibility", "public")).upper(),
        "CONTRACT": {
            "INPUT": function.get("input_contract", {}),
            "OUTPUT": function.get("output_contract", {}),
            "ERROR": function.get("error_behavior", ""),
        },
    }


def _function_spec(function: dict[str, Any], trace_id: str) -> dict[str, Any]:
    signature = function.get("signature", {})
    body_key = str(function.get("logic_kind") or ("EVENT" if function.get("function_kind") in {"handler", "public_api"} else "LOGIC"))
    contract = function.get("behavior_contract", {}) if isinstance(function.get("behavior_contract"), dict) else {}
    body = {
        "INPUT": str(contract.get("input", "")),
        "ACTION": str(contract.get("action", function.get("purpose", ""))),
        "OUTPUT": str(contract.get("output", "")),
        "INVARIANTS_USED": contract.get("invariants_used", []),
    }
    rely_struct = [
        {"NAME": dep.get("symbol_name", ""), "ROLE": dep.get("reason", "")}
        for dep in function.get("signature_dependencies", [])
        if isinstance(dep, dict)
    ] + [
        {"NAME": item.get("symbol_name", ""), "ROLE": item.get("role", "")}
        for item in function.get("internal_type_refs", [])
        if isinstance(item, dict)
    ]
    rely_func = [
        {"NAME": edge.get("callee_function_id", ""), "KIND": "CALL", "ROLE": edge.get("call_reason", "")}
        for edge in function.get("call_contracts", [])
        if isinstance(edge, dict)
    ]
    return {
        "KIND": "FUNCTION_SPEC",
        "TRACE_ID": trace_id,
        "FUNCTION_TYPE": str(function.get("function_kind", "public_api")).upper(),
        "ROLE": str(function.get("purpose", "")),
        "SIGNATURE": {
            "RAW": _signature_raw(signature),
            "NAME": str(function.get("name", signature.get("name", ""))),
            "RETURN": str(signature.get("return_type", "int")),
            "PARAMS": [_param_for_coder(item) for item in signature.get("params", [])],
        },
        "RELY": {"STRUCT": rely_struct, "FUNC": rely_func, "VAR": []},
        body_key: body,
        "CALL_CONTRACTS": function.get("call_contracts", []),
        "FORBIDDEN_SYMBOLS": function.get("forbidden_symbols", []),
        "TRACEABILITY": function.get("traceability", {}),
        "CAPABILITY_IDS": function.get("capability_ids", []),
        "STATE_ACCESS": function.get("state_access", []),
        "WIRE_MAPPING": function.get("wire_mapping", []),
        "ACCESS_PATHS": function.get("access_paths", []),
        "CALLS_ALLOWED": function.get("calls_allowed", []),
    }


def compile_spec_bundle(spec_blueprint: dict[str, Any], output_dir: str | Path) -> tuple[dict[str, Any], Path]:
    root = Path(output_dir)
    spec_root = root / "spec_bundle"
    if spec_root.exists():
        shutil.rmtree(spec_root)
    spec_root.mkdir(parents=True, exist_ok=True)
    protocol = safe_slug(str(spec_blueprint.get("protocol_name", "protocol")))
    modules = [item for item in spec_blueprint.get("modules", []) if isinstance(item, dict)]
    files = [item for item in spec_blueprint.get("files", []) if isinstance(item, dict)]
    functions = [item for item in spec_blueprint.get("functions", []) if isinstance(item, dict)]
    access_path_table = [item for item in spec_blueprint.get("access_path_table", []) if isinstance(item, dict)]
    functions_by_file: dict[str, list[dict[str, Any]]] = {}
    for function in functions:
        functions_by_file.setdefault(str(function.get("file_id", "")), []).append(function)

    module_entries: list[dict[str, Any]] = []
    file_spec_paths: list[str] = []
    function_spec_paths: list[str] = []
    for module in modules:
        module_id = str(module.get("module_id", "module"))
        module_files = [item for item in files if item.get("module_id") == module_id]
        module_entries.append(
            {
                "NAME": module_id,
                "ROLE": module.get("role", ""),
                "DEPENDENCIES": list(module.get("dependencies", [])),
                "ARTIFACTS": [{"KIND": "FILE_SPEC", "PATH": item.get("source_path", "")} for item in module_files],
                "FILES": [item.get("header_path", "") for item in module_files if item.get("header_path")]
                + [item.get("source_path", "") for item in module_files if item.get("source_path")],
                "DOC_REF": module.get("traceability", {}).get("source_fact_ids", []),
            }
        )
    module_spec = {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": {
            "NAME": protocol,
            "SPEC_VERSION": spec_blueprint.get("schema_version", "spec_blueprint/v1"),
            "ROLES": ["target"],
        },
        "MODULES": module_entries,
        "GENERATION_ORDER": [str(item.get("module_id", "")) for item in modules],
        "CONSISTENCY_RULES": [
            {"RULE": "file_function_trace_ids_must_match", "SOURCE": "planning_compiler"},
            {"RULE": "public_functions_declared_in_headers", "SOURCE": "planning_compiler"},
        ],
    }
    module_spec_path = write_json(spec_root / f"{protocol}_module_spec.json", module_spec)

    for file_item in files:
        file_id = str(file_item.get("file_id", "file"))
        file_trace_id = str(file_item.get("trace_id", ""))
        module_id = str(file_item.get("module_id", "module"))
        file_functions = functions_by_file.get(file_id, [])
        handle_type = f"{protocol}_{module_id}_t"
        declarations = [
            {
                "NAME": item.get("name", ""),
                "KIND": "TYPE",
                "VISIBILITY": str(item.get("visibility", "internal")).upper(),
                "ROLE": item.get("reason", ""),
            }
            for function in file_functions
            for item in function.get("interface_type_declarations", [])
            if isinstance(item, dict)
        ]
        private_types = [
            {
                "NAME": item.get("symbol_name", ""),
                "KIND": "TYPE",
                "VISIBILITY": str(item.get("visibility", "private")).upper(),
                "ROLE": item.get("role", ""),
            }
            for function in file_functions
            for item in function.get("internal_type_refs", [])
            if isinstance(item, dict)
        ]
        source_interfaces: list[dict[str, Any]] = []
        header_interfaces: list[dict[str, Any]] = []
        for function in file_functions:
            function_trace_id = f"{file_trace_id}/{function.get('name')}"
            interface = _interface(function, function_trace_id)
            source_interfaces.append(interface)
            if str(function.get("visibility", "public")).lower() == "public":
                header_item = dict(interface)
                header_item.pop("TRACE_ID", None)
                header_interfaces.append(header_item)
            function_spec = _function_spec(function, function_trace_id)
            fn_dir = spec_root / safe_slug(module_id) / "functions"
            fn_path = write_json(fn_dir / f"{safe_slug(str(function.get('name', 'function')))}_function_spec.json", function_spec)
            function_spec_paths.append(str(fn_path))
        file_spec = {
            "KIND": "FILE_SPEC",
            "FILE": {
                "TRACE_ID": file_trace_id,
                "LANG": "C",
                "ROLE": file_item.get("responsibility", ""),
                "DOC_REF": file_item.get("traceability", {}).get("source_fact_ids", []),
            },
            "HEADER": {
                "PATH": file_item.get("header_path", ""),
                "DEPENDENCY": [],
                "DATA": [
                    {
                        "NAME": handle_type,
                        "KIND": "TYPE",
                        "VISIBILITY": "PUBLIC",
                        "ROLE": "Opaque module context handle.",
                    }
                ] + declarations,
                "INTERFACE": header_interfaces,
            },
            "SOURCE": {
                "PATH": file_item.get("source_path", ""),
                "DEPENDENCY": [file_item.get("header_path", "")],
                "DATA": [
                    {
                        "NAME": f"struct {handle_type[:-2]}",
                        "KIND": "TYPE",
                        "VISIBILITY": "PRIVATE",
                        "ROLE": "Private module context storage.",
                        "TYPE_SPEC": {"TYPE_KIND": "STRUCT", "FIELDS": []},
                    }
                ] + private_types,
                "INTERFACE": source_interfaces,
            },
            "PUBLIC_SYMBOLS": [handle_type] + [item.get("NAME", "") for item in header_interfaces],
            "ACCESS_PATHS": [
                {"PATH": item.get("path", ""), "FIELD_ID": item.get("field_id", ""), "SOURCE": "planning_access_path_table"}
                for item in access_path_table
                if module_id == "protocol_codec"
            ],
            "TRACEABILITY": file_item.get("traceability", {}),
        }
        file_spec_path = write_json(spec_root / safe_slug(module_id) / f"{safe_slug(file_id)}_file_spec.json", file_spec)
        file_spec_paths.append(str(file_spec_path))

    manifest = {
        "schema_version": CODER_MANIFEST_SCHEMA_VERSION,
        "protocol_name": protocol,
        "spec_root": str(spec_root),
        "module_spec_path": str(module_spec_path),
        "file_spec_paths": file_spec_paths,
        "function_spec_paths": function_spec_paths,
        "compatibility_target": "agent.coder.specs.load_spec_bundle_from_root",
    }
    manifest_path = write_json(root / "coder_manifest.json", manifest)
    return manifest, manifest_path
