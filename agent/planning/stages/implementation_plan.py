from __future__ import annotations

import re
from typing import Any

from ..schemas.implementation_plan import SCHEMA_VERSION
from .dependencies import derive_dependency_graph


def _safe_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _field_value(value: Any, default: str = "") -> str:
    if isinstance(value, dict):
        raw = value.get("value", default)
        return str(raw) if raw is not None else default
    if value is None:
        return default
    return str(value)


def _target_directives(ir: dict[str, Any]) -> dict[str, Any]:
    target = ir.get("target_directives", {})
    directives = target.get("directives", {}) if isinstance(target, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(directives, dict):
        for key, value in directives.items():
            if isinstance(value, dict):
                result[key] = value.get("value")
    return result


def _capability_refs(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for item in profile.get("required_capabilities", []):
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id", "")).strip()
        if not cap_id:
            continue
        refs[cap_id] = {
            "category": item.get("category"),
            "source_fact_ids": item.get("source_fact_ids", []),
            "target_directive_ids": item.get("target_directive_ids", []),
            "evidence_refs": item.get("evidence_refs", []),
        }
    return refs


def _surface_units(ir: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for item in profile.get("required_surface_units", []):
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            by_name[str(item["name"])] = {
                "name": str(item["name"]),
                "source_fact_ids": item.get("source_fact_ids", []),
                "evidence_refs": item.get("evidence_refs", []),
            }
    facts = ir.get("protocol_facts", {})
    message = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    surface_catalog = message.get("surface_catalog", []) if isinstance(message.get("surface_catalog"), list) else []
    for item in surface_catalog:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        current = by_name.setdefault(name, {"name": name, "source_fact_ids": [], "evidence_refs": []})
        if item.get("direction") is not None:
            current["direction"] = str(item.get("direction"))
        if item.get("role") is not None:
            current["role"] = str(item.get("role"))
    return list(by_name.values())


def _handler_surfaces(surfaces: list[dict[str, Any]], target_role: str) -> list[dict[str, Any]]:
    role = _safe_id(target_role)
    if not role:
        return surfaces
    result: list[dict[str, Any]] = []
    for surface in surfaces:
        direction = _safe_id(str(surface.get("direction", "")))
        if not direction or direction == "bidirectional" or direction.endswith(f"_to_{role}") or direction == role:
            result.append(surface)
    return result


def _wire_fields(ir: dict[str, Any]) -> list[dict[str, Any]]:
    facts = ir.get("protocol_facts", {})
    message = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    entries = message.get("message_or_command_entries", []) if isinstance(message.get("message_or_command_entries"), list) else []
    field_index = ir.get("normalization_index", {}).get("field_id_by_message_and_name", {})
    result: list[dict[str, Any]] = []
    for entry_idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        message_name = str(entry.get("name") or entry.get("surface_unit") or f"entry_{entry_idx}").strip()
        fields = entry.get("fields", [])
        if not message_name or not isinstance(fields, list):
            continue
        indexed_fields = field_index.get(message_name, {}) if isinstance(field_index, dict) else {}
        for field_idx, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or f"field_{field_idx}").strip()
            field_id = str(field.get("fact_id") or indexed_fields.get(field_name) or f"fact:message_model_message_or_command_entries_{entry_idx}_fields_{field_idx}")
            access_path_id = f"access:{_safe_id(message_name)}:{_safe_id(field_name)}"
            result.append(
                {
                    "field_id": field_id,
                    "message": message_name,
                    "field": field_name,
                    "access_path_id": access_path_id,
                    "access_path": f"{_safe_id(message_name)}.{_safe_id(field_name)}",
                    "source_fact_ids": [field_id],
                }
            )
    return result


def _function_signature(return_type: str, name: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    rendered_params = ", ".join(f"{item['type']} {item['name']}" for item in params) if params else "void"
    return {
        "return_type": return_type,
        "name": name,
        "params": params,
        "raw": f"{return_type} {name}({rendered_params})",
    }


def _contract(
    *,
    function_id: str,
    name: str,
    file_id: str,
    declared_in: str,
    return_type: str,
    params: list[dict[str, Any]],
    purpose: str,
    function_kind: str,
    capability_ids: list[str],
    source_fact_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "function_id": function_id,
        "name": name,
        "file_id": file_id,
        "declared_in": declared_in,
        "visibility": "public",
        "purpose": purpose,
        "function_kind": function_kind,
        "capability_ids": capability_ids,
        "signature": _function_signature(return_type, name, params),
        "input_contract": {"params": params},
        "output_contract": {"return_type": return_type},
        "state_access": [],
        "wire_mapping": [],
        "access_paths": [],
        "error_behavior": "Return negative status for protocol or runtime errors where applicable.",
        "calls_allowed": [],
        "side_effects": [],
        "traceability": {"source_fact_ids": list(source_fact_ids or []), "decision_ids": []},
    }


def _function_contracts_for_file(
    *,
    protocol: str,
    module_id: str,
    file_id: str,
    header_file_id: str,
    owned_capabilities: list[str],
    surfaces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    handle_type = f"{prefix}_t"
    contracts = [
        _contract(
            function_id=f"fn:{file_id}:create",
            name=f"{prefix}_create",
            file_id=file_id,
            declared_in=header_file_id,
            return_type=f"{handle_type}*",
            params=[],
            purpose=f"Allocate and initialize the {module_id} module context.",
            function_kind="resource_lifecycle",
            capability_ids=owned_capabilities[:1],
        ),
        _contract(
            function_id=f"fn:{file_id}:destroy",
            name=f"{prefix}_destroy",
            file_id=file_id,
            declared_in=header_file_id,
            return_type="void",
            params=[{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}],
            purpose=f"Release resources owned by the {module_id} module context.",
            function_kind="resource_lifecycle",
            capability_ids=owned_capabilities[:1],
        ),
    ]
    if module_id == "transport_runtime":
        for action in ("start", "run_once", "stop"):
            contracts.append(
                _contract(
                    function_id=f"fn:{file_id}:{action}",
                    name=f"{prefix}_{action}",
                    file_id=file_id,
                    declared_in=header_file_id,
                    return_type="int",
                    params=[{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}],
                    purpose=f"Perform transport runtime action: {action}.",
                    function_kind="public_api",
                    capability_ids=[cap for cap in owned_capabilities if cap in {"transport_io", "connection_lifecycle", "connection_buffering"}],
                )
            )
    elif module_id == "protocol_codec":
        for action, kind, params in (
            (
                "decode_message",
                "parser",
                [
                    {"type": "const uint8_t*", "name": "buffer", "nullable": False, "ownership": "borrowed"},
                    {"type": "size_t", "name": "length", "nullable": False, "ownership": "value"},
                ],
            ),
            (
                "encode_message",
                "serializer",
                [
                    {"type": "uint8_t*", "name": "buffer", "nullable": False, "ownership": "borrowed"},
                    {"type": "size_t", "name": "capacity", "nullable": False, "ownership": "value"},
                ],
            ),
        ):
            contracts.append(
                _contract(
                    function_id=f"fn:{file_id}:{action}",
                    name=f"{prefix}_{action}",
                    file_id=file_id,
                    declared_in=header_file_id,
                    return_type="int",
                    params=params,
                    purpose=f"Protocol codec {action.replace('_', ' ')} entry point.",
                    function_kind=kind,
                    capability_ids=[cap for cap in owned_capabilities if cap.startswith("message_") or cap.endswith("framing")],
                )
            )
    elif module_id == "semantic_core":
        contracts.append(
            _contract(
                function_id=f"fn:{file_id}:dispatch_message",
                name=f"{prefix}_dispatch_message",
                file_id=file_id,
                declared_in=header_file_id,
                return_type="int",
                params=[
                    {"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"},
                    {"type": "const void*", "name": "message", "nullable": False, "ownership": "borrowed"},
                ],
                purpose="Dispatch a decoded message to the target-role handler matrix.",
                function_kind="handler",
                capability_ids=[cap for cap in owned_capabilities if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}],
            )
        )
        for surface in surfaces:
            surface_id = _safe_id(surface.get("name", "surface"))
            contracts.append(
                _contract(
                    function_id=f"fn:{file_id}:handle_{surface_id}",
                    name=f"{prefix}_handle_{surface_id}",
                    file_id=file_id,
                    declared_in=header_file_id,
                    return_type="int",
                    params=[
                        {"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"},
                        {"type": "const void*", "name": "message", "nullable": False, "ownership": "borrowed"},
                    ],
                    purpose=f"Handle target-scope surface unit {surface.get('name')}.",
                    function_kind="handler",
                    capability_ids=[cap for cap in owned_capabilities if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}],
                    source_fact_ids=[str(item) for item in surface.get("source_fact_ids", [])],
                )
            )
    elif module_id == "resource_store":
        for action in ("open_session", "close_session", "lookup_resource"):
            contracts.append(
                _contract(
                    function_id=f"fn:{file_id}:{action}",
                    name=f"{prefix}_{action}",
                    file_id=file_id,
                    declared_in=header_file_id,
                    return_type="int",
                    params=[{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}],
                    purpose=f"Resource and session lifecycle helper: {action}.",
                    function_kind="state_machine",
                    capability_ids=owned_capabilities,
                )
            )
    else:
        contracts.append(
            _contract(
                function_id=f"fn:{file_id}:run",
                name=f"{prefix}_run",
                file_id=file_id,
                declared_in=header_file_id,
                return_type="int",
                params=[{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}],
                purpose=f"Run the composed target-role boundary for module {module_id}.",
                function_kind="public_api",
                capability_ids=owned_capabilities,
            )
        )
    return contracts


def build_implementation_plan(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
) -> dict[str, Any]:
    protocol = _safe_id(_field_value(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))))
    target = _target_directives(planning_ir)
    arch = selected_architecture.get("architecture", {})
    modules = arch.get("modules", []) if isinstance(arch, dict) else []
    capability_refs = _capability_refs(profile)
    surfaces = _surface_units(planning_ir, profile)
    handler_surfaces = _handler_surfaces(surfaces, str(target.get("target_role", "")))
    constraint_ids = [
        str(item.get("constraint_id", "")).strip()
        for item in constraints.get("constraints", [])
        if isinstance(item, dict) and str(item.get("constraint_id", "")).strip()
    ]

    module_contracts: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = _safe_id(str(module.get("module_id", module.get("name", "module"))))
        owned_capabilities = [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()]
        source_fact_ids: list[str] = []
        for cap in owned_capabilities:
            refs = capability_refs.get(cap, {}) if isinstance(capability_refs.get(cap), dict) else {}
            source_fact_ids.extend(str(item) for item in refs.get("source_fact_ids", []) if str(item).strip())
        source_fact_ids = sorted(set(source_fact_ids))
        module_contracts.append(
            {
                "module_id": module_id,
                "name": module.get("name", module_id),
                "purpose": "; ".join(str(item) for item in module.get("responsibilities", []) if str(item).strip()) or f"Own {module_id} responsibilities.",
                "owned_capabilities": owned_capabilities,
                "consumed_capabilities": [str(cap) for cap in module.get("consumed_capabilities", [])],
                "support_module": bool(module.get("support_module", False)),
                "public_api_policy": "Expose stable public functions in the generated header; keep state opaque.",
                "state_owned": [str(item) for item in module.get("state_owned", [])],
                "errors_raised": ["protocol_error"],
                "constraints": constraint_ids,
                "source_fact_ids": source_fact_ids,
                "decision_ids": [f"decision:module:{module_id}"],
            }
        )
        file_id = f"file:{module_id}"
        header_file_id = f"header:{module_id}"
        source_path = f"{protocol}/{module_id}/{module_id}.c"
        header_path = f"{protocol}/{module_id}/{module_id}.h"
        file_functions = _function_contracts_for_file(
            protocol=protocol,
            module_id=module_id,
            file_id=file_id,
            header_file_id=header_file_id,
            owned_capabilities=owned_capabilities,
            surfaces=handler_surfaces,
        )
        files.append(
            {
                "file_id": file_id,
                "header_file_id": header_file_id,
                "module_id": module_id,
                "kind": "source_header_pair",
                "path": source_path,
                "source_path": source_path,
                "header_path": header_path,
                "responsibility": f"Implement the {module_id} module contract.",
                "exports": [item["name"] for item in file_functions if item["visibility"] == "public"],
                "implements": [item["function_id"] for item in file_functions],
                "imports_allowed": [],
                "traceability": {"source_fact_ids": source_fact_ids, "decision_ids": [f"decision:file:{module_id}"]},
            }
        )
        functions.extend(file_functions)

    wire_fields = _wire_fields(planning_ir)
    parser_ids = [str(item.get("function_id")) for item in functions if item.get("function_kind") == "parser"]
    serializer_ids = [str(item.get("function_id")) for item in functions if item.get("function_kind") == "serializer"]
    parser_id = parser_ids[0] if parser_ids else ""
    serializer_id = serializer_ids[0] if serializer_ids else ""
    wire_mapping_table = [
        {
            "mapping_id": f"wire:{_safe_id(item['message'])}:{_safe_id(item['field'])}",
            "field_id": item["field_id"],
            "message": item["message"],
            "field": item["field"],
            "parser_function_id": parser_id,
            "serializer_function_id": serializer_id,
            "access_path_id": item["access_path_id"],
            "source_fact_ids": item["source_fact_ids"],
        }
        for item in wire_fields
    ]
    access_path_table = [
        {
            "access_path_id": item["access_path_id"],
            "path": item["access_path"],
            "field_id": item["field_id"],
            "owner_module_id": "protocol_codec",
            "source_fact_ids": item["source_fact_ids"],
        }
        for item in wire_fields
    ]
    for function in functions:
        if function.get("function_kind") == "parser":
            function["wire_mapping"] = [
                {
                    "mapping_id": item["mapping_id"],
                    "field_id": item["field_id"],
                    "message": item["message"],
                    "field": item["field"],
                    "direction": "decode",
                    "access_path_id": item["access_path_id"],
                }
                for item in wire_mapping_table
            ]
            function["access_paths"] = [item["access_path_id"] for item in access_path_table]
        elif function.get("function_kind") == "serializer":
            function["wire_mapping"] = [
                {
                    "mapping_id": item["mapping_id"],
                    "field_id": item["field_id"],
                    "message": item["message"],
                    "field": item["field"],
                    "direction": "encode",
                    "access_path_id": item["access_path_id"],
                }
                for item in wire_mapping_table
            ]
            function["access_paths"] = [item["access_path_id"] for item in access_path_table]

    draft_plan = {
        "module_contracts": module_contracts,
        "file_layout": {"files": files},
        "function_contracts": functions,
    }
    dependency_graph = derive_dependency_graph(draft_plan)
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_name": protocol,
        "target_directives_ref": {
            "schema_version": planning_ir.get("target_directives", {}).get("schema_version", "target_directives/v1"),
            "directives": target,
        },
        "source_artifacts": {
            "planning_ir": "003_planning_ir.json",
            "protocol_profile": "004_protocol_profile.json",
            "engineering_constraints": "005_engineering_constraints.json",
            "selected_architecture": "006_selected_architecture.json",
        },
        "module_contracts": module_contracts,
        "file_layout": {"files": files},
        "function_contracts": functions,
        "canonical_types": [
            {
                "type_id": "type:opaque_module_context",
                "name_pattern": f"{protocol}_<module>_t",
                "visibility": "public_opaque",
                "rationale": "Expose opaque handles to keep generated state ownership module-local.",
            }
        ],
        "state_design": {
            "states": [
                {"state_id": "state:connection_registry", "owner_module_id": "transport_runtime"},
                {"state_id": "state:protocol_session_state", "owner_module_id": "semantic_core"},
                {"state_id": "state:resource_index", "owner_module_id": "resource_store"},
            ]
        },
        "handler_matrix": [
            {"surface": surface.get("name"), "handler_module_id": "semantic_core", "source_fact_ids": surface.get("source_fact_ids", [])}
            for surface in handler_surfaces
        ],
        "resource_lifecycle": {"policy": "module_create_destroy_pairs", "owner_modules": [item["module_id"] for item in module_contracts]},
        "error_strategy": {"policy": "negative_status_or_void_destroy", "source": "deterministic_baseline"},
        "wire_mapping_table": wire_mapping_table,
        "access_path_table": access_path_table,
        "dependency_graph": dependency_graph,
        "test_plan": [{"test_id": "test:coder_loader_compatibility", "purpose": "Generated specs must load through agent.coder.specs."}],
        "traceability": {"required_capabilities": list(capability_refs), "constraint_ids": constraint_ids},
        "unresolved_questions": planning_ir.get("unresolved_facts", []),
    }
