from __future__ import annotations

from typing import Any

from .implementation_plan import _capability_refs, _compressed_refs, _field_value, _handler_surfaces, _surface_units, _target_directives, _wire_fields


PROFILE_FIELDS = (
    "transport_shape",
    "interaction_model",
    "statefulness",
    "routing_intensity",
    "resource_intensity",
    "failure_semantics",
    "timing_model",
)

SYSTEM_TYPE_IDS = [
    "bool",
    "char",
    "double",
    "float",
    "int",
    "long",
    "short",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "socklen_t",
    "socket_t",
    "struct sockaddr",
    "struct sockaddr_in",
    "struct sockaddr_storage",
    "struct epoll_event",
    "void",
]


def _constraints(constraints: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "constraint_id": str(item.get("constraint_id", "")),
            "affected_capabilities": item.get("affected_capabilities", []),
            "obligation": str(item.get("obligation", "")),
            "severity": str(item.get("severity", "")),
            "validation_rule": str(item.get("validation_rule", "")),
        }
        for item in constraints.get("constraints", [])
        if isinstance(item, dict)
    ]


def _selected_modules(selected_architecture: dict[str, Any]) -> list[dict[str, Any]]:
    arch = selected_architecture.get("architecture", {})
    modules = arch.get("modules", []) if isinstance(arch, dict) else []
    return [
        {
            "module_id": str(module.get("module_id", "")),
            "name": str(module.get("name", module.get("module_id", ""))),
            "responsibilities": [str(item) for item in module.get("responsibilities", []) if str(item).strip()],
            "owned_capability_ids": [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()],
            "consumed_capability_ids": [str(cap) for cap in module.get("consumed_capabilities", []) if str(cap).strip()],
            "state_owned": [str(item) for item in module.get("state_owned", []) if str(item).strip()],
            "support_module": bool(module.get("support_module", False)),
        }
        for module in modules
        if isinstance(module, dict)
    ]


def _protocol_summary(planning_ir: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    target = _target_directives(planning_ir)
    return {
        "name": _field_value(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))),
        "target_role": _field_value(profile.get("target_role"), str(target.get("target_role", ""))),
        "minimum_scope": _field_value(profile.get("minimum_scope"), str(target.get("scope", "minimum_v1"))),
        "target_directives": target,
    }


def _required_capabilities(profile: dict[str, Any]) -> list[dict[str, Any]]:
    refs = _capability_refs(profile)
    return [
        {
            "capability_id": capability_id,
            "category": item.get("category"),
            "refs": _compressed_refs(item),
        }
        for capability_id, item in refs.items()
    ]


def _message_summaries(planning_ir: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in _wire_fields(planning_ir):
        message = str(field.get("message", ""))
        if not message or message in seen:
            continue
        seen.add(message)
        result.append({"message": message, "message_id": f"message:{message.lower().replace(' ', '_')}"})
    return result


def _field_summaries(planning_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field_id": item["field_id"],
            "message": item["message"],
            "field": item["field"],
            "access_path_id": item["access_path_id"],
            "access_path": item["access_path"],
        }
        for item in _wire_fields(planning_ir)
    ]


def _legal_ids_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    files = draft.get("file_layout", {}).get("files", [])
    return {
        "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_contracts", []) if isinstance(item, dict)],
        "capability_ids": list(draft.get("traceability", {}).get("required_capabilities", [])),
        "constraint_ids": list(draft.get("traceability", {}).get("constraint_ids", [])),
        "state_ids": [str(item.get("state_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict)],
        "error_ids": [str(item.get("error_id", "")) for item in draft.get("error_strategy", []) if isinstance(item, dict)],
        "type_ids": [str(item.get("type_id", "")) for item in draft.get("canonical_types", []) if isinstance(item, dict)],
        "system_type_ids": SYSTEM_TYPE_IDS,
        "handler_ids": [str(item.get("handler_id", "")) for item in draft.get("handler_matrix", []) if isinstance(item, dict)],
        "function_ids": [str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)],
        "file_ids": [str(item.get("file_id", "")) for item in files if isinstance(item, dict)],
    }


def _legal_ids_from_inputs(planning_ir: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_ids": [item["module_id"] for item in _selected_modules(selected_architecture)],
        "capability_ids": [item["capability_id"] for item in _required_capabilities(profile)],
        "constraint_ids": [item["constraint_id"] for item in _constraints(constraints)],
        "message_ids": [item["message_id"] for item in _message_summaries(planning_ir)],
        "field_ids": [item["field_id"] for item in _field_summaries(planning_ir)],
    }


def _compressed_trace_refs(profile: dict[str, Any]) -> dict[str, Any]:
    refs = _capability_refs(profile)
    return {
        "profile_fields": {name: _compressed_refs(profile.get(name, {})) for name in PROFILE_FIELDS},
        "capabilities": {capability_id: _compressed_refs(item) for capability_id, item in refs.items()},
    }


def _accepted_summary(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_contracts": draft.get("module_contracts", []),
        "canonical_types": draft.get("canonical_types", []),
        "state_design": draft.get("state_design", []),
        "handler_matrix": draft.get("handler_matrix", []),
        "resource_lifecycle": draft.get("resource_lifecycle", []),
        "error_strategy": draft.get("error_strategy", []),
        "function_contracts": [
            {
                "function_id": item.get("function_id"),
                "name": item.get("name"),
                "module_id": item.get("module_id"),
                "file_id": item.get("file_id"),
                "visibility": item.get("visibility"),
                "function_kind": item.get("function_kind"),
                "coder_function_type": item.get("coder_function_type"),
                "api_surface": item.get("api_surface"),
                "grouping_hint": item.get("grouping_hint"),
                "signature": item.get("signature", {}),
                "signature_dependencies": item.get("signature_dependencies", []),
                "service_requirements": item.get("service_requirements", []),
                "capability_ids": item.get("capability_ids", []),
            }
            for item in draft.get("function_contracts", [])
            if isinstance(item, dict)
        ],
    }


def _signature_update_skeleton(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "function_id": item.get("function_id"),
            "signature": item.get("signature", {}),
            "signature_dependencies": item.get("signature_dependencies", []),
            "interface_type_declarations": item.get("interface_type_declarations", []),
            "trace_ref_keys": item.get("traceability", {}).get("decision_ids", []),
            "status": "inferred",
        }
        for item in functions
        if isinstance(item, dict)
    ]


def _behavior_update_skeleton(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "function_id": item.get("function_id"),
            "contract": item.get("behavior_contract", {}),
            "error_behavior": item.get("error_behavior", {}),
            "state_access": item.get("state_access", []),
            "resource_access": item.get("resource_access", []),
            "internal_type_refs": item.get("internal_type_refs", []),
            "service_requirements": [],
            "logic_kind": item.get("logic_kind", ""),
            "forbidden_symbols": item.get("forbidden_symbols", []),
            "trace_ref_keys": item.get("traceability", {}).get("decision_ids", []),
            "status": "inferred",
        }
        for item in functions
        if isinstance(item, dict)
    ]


def _callable_functions(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for function in draft.get("function_contracts", []):
        if not isinstance(function, dict):
            continue
        visibility = str(function.get("visibility", "")).lower()
        same_module = str(function.get("module_id", "")) == module_id
        api_surface = str(function.get("api_surface", ""))
        cross_module_api = visibility == "public" or api_surface == "public"
        if same_module or (visibility not in {"private", "static"} and cross_module_api):
            result.append(
                {
                    "function_id": function.get("function_id"),
                    "name": function.get("name"),
                    "module_id": function.get("module_id"),
                    "visibility": function.get("visibility"),
                    "api_surface": function.get("api_surface"),
                    "function_kind": function.get("function_kind"),
                    "signature": function.get("signature", {}),
                }
            )
    return result


def build_core_design_context(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
) -> dict[str, Any]:
    surfaces = _surface_units(planning_ir, profile)
    return {
        "schema_version": "core_design_context/v1",
        "protocol": _protocol_summary(planning_ir, profile),
        "profile_summary": {name: _field_value(profile.get(name), "unknown") for name in PROFILE_FIELDS},
        "selected_modules": _selected_modules(selected_architecture),
        "required_capabilities": _required_capabilities(profile),
        "required_surface_units": [
            {"name": item.get("name"), "direction": item.get("direction"), "role": item.get("role"), "refs": _compressed_refs(item)}
            for item in surfaces
        ],
        "handler_requirements": [
            {"surface": item.get("name"), "source_fact_count": len(item.get("source_fact_ids", []))}
            for item in _handler_surfaces(surfaces, str(_target_directives(planning_ir).get("target_role", "")))
        ],
        "message_summaries": _message_summaries(planning_ir),
        "field_summaries": _field_summaries(planning_ir),
        "engineering_constraints": _constraints(constraints),
        "compressed_trace_refs": _compressed_trace_refs(profile),
        "legal_id_universe": _legal_ids_from_inputs(planning_ir, profile, constraints, selected_architecture),
    }


def build_module_contract_context(draft: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "module_contract_context/v1",
        "selected_modules": _selected_modules(selected_architecture),
        "core_design_summary": _accepted_summary(draft),
        "required_capabilities": _required_capabilities(profile),
        "engineering_constraints": _constraints(constraints),
        "compressed_trace_refs": _compressed_trace_refs(profile),
        "legal_id_universe": _legal_ids_from_draft(draft)
        | {
            "module_ids": [item["module_id"] for item in _selected_modules(selected_architecture)],
            "capability_ids": [item["capability_id"] for item in _required_capabilities(profile)],
            "constraint_ids": [item["constraint_id"] for item in _constraints(constraints)],
        },
    }


def build_function_inventory_context(draft: dict[str, Any], module_contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "function_inventory_context/v1",
        "module_contract": module_contract,
        "core_design_summary": _accepted_summary(draft),
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_function_signature_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    return {
        "schema_version": "function_signature_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": functions,
        "required_update_skeleton": _signature_update_skeleton(functions),
        "module_contracts": draft.get("module_contracts", []),
        "core_design_summary": _accepted_summary(draft),
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_function_behavior_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], constraints: dict[str, Any], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    return {
        "schema_version": "function_behavior_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": functions,
        "required_update_skeleton": _behavior_update_skeleton(functions),
        "service_requirement_policy": {
            "external_runtime_examples": ["socket", "accept", "read", "write", "close", "epoll", "malloc", "free", "timer"],
            "cross_module_only": True,
            "provider_own_responsibility": "do_not_emit_service_requirement",
        },
        "module_contracts": draft.get("module_contracts", []),
        "core_design_summary": _accepted_summary(draft),
        "engineering_constraints": _constraints(constraints),
        "legal_id_universe": _legal_ids_from_draft(draft) | {"constraint_ids": [item["constraint_id"] for item in _constraints(constraints)]},
    }


def build_wire_access_binding_context(draft: dict[str, Any], planning_ir: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "wire_access_binding_context/v1",
        "codec_and_handler_functions": [
            item
            for item in _accepted_summary(draft)["function_contracts"]
            if item.get("function_kind") in {"parser", "serializer", "handler"}
        ],
        "field_summaries": _field_summaries(planning_ir),
        "state_design": draft.get("state_design", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "legal_id_universe": _legal_ids_from_draft(draft)
        | {
            "message_ids": [item["message_id"] for item in _message_summaries(planning_ir)],
            "field_ids": [item["field_id"] for item in _field_summaries(planning_ir)],
        },
    }


def build_calls_allowed_context(
    draft: dict[str, Any],
    selected_architecture: dict[str, Any],
    module_id: str | None = None,
    functions: list[dict[str, Any]] | None = None,
    *,
    batch_index: int | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    scoped_functions = functions if functions is not None else [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    scoped_ids = {str(item.get("function_id", "")) for item in scoped_functions if isinstance(item, dict)}
    current_module = module_id or ""
    return {
        "schema_version": "calls_allowed_context/v1",
        "module_id": current_module,
        "batch": {"index": batch_index, "size": batch_size} if batch_index is not None else {},
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "service_requirements": [
            {"function_id": item.get("function_id"), "service_requirements": item.get("service_requirements", [])}
            for item in draft.get("function_contracts", [])
            if isinstance(item, dict) and (not scoped_ids or str(item.get("function_id", "")) in scoped_ids)
        ],
        "callable_functions": _callable_functions(draft, current_module) if current_module else [],
        "required_call_update_caller_ids": sorted(scoped_ids),
        "module_contracts": draft.get("module_contracts", []),
        "architecture_policy": {"selected_modules": _selected_modules(selected_architecture), "forbidden_cycles": True},
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_file_layout_context(draft: dict[str, Any], planning_ir: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "file_layout_context/v1",
        "module_contracts": draft.get("module_contracts", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "target_language": str(_target_directives(planning_ir).get("language", "C")),
        "layout_policy": "source_header_pair",
        "engineering_constraints": _constraints(constraints),
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_dependency_repair_context(draft: dict[str, Any], dependency_errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "dependency_repair_context/v1",
        "files": draft.get("file_layout", {}).get("files", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "dependency_errors": dependency_errors,
        "allowed_repair_operations": ["remove_call_edge", "adjust_imports_allowed", "lower_visibility", "change_function_file_assignment", "mark_unresolved"],
        "legal_id_universe": _legal_ids_from_draft(draft),
    }
