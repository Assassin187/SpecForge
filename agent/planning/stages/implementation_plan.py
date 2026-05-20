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


def _compressed_refs(item: dict[str, Any]) -> dict[str, Any]:
    source_fact_ids = item.get("source_fact_ids", [])
    target_directive_ids = item.get("target_directive_ids", [])
    evidence_refs = item.get("evidence_refs", [])
    return {
        "source_fact_count": len(source_fact_ids) if isinstance(source_fact_ids, list) else 0,
        "target_directive_count": len(target_directive_ids) if isinstance(target_directive_ids, list) else 0,
        "evidence_refs": [str(ref) for ref in evidence_refs[:5]] if isinstance(evidence_refs, list) else [],
    }


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
    # Compatibility helper for tests and deterministic baselines. The runtime
    # orchestration performs these stages individually and never asks an LLM for
    # a monolithic implementation_plan.
    from .implementation_plan_merger import (
        fallback_calls_allowed,
        fallback_core_design,
        fallback_file_layout,
        fallback_function_behavior,
        fallback_function_inventory,
        fallback_function_signatures,
        fallback_module_contracts,
        fallback_wire_access_binding,
        finalize_dependency_graph,
        merge_calls_allowed,
        merge_core_design,
        merge_file_layout,
        merge_function_behavior,
        merge_function_inventory,
        merge_function_signatures,
        merge_module_contracts,
        merge_wire_access_binding,
        build_plan_skeleton,
    )

    draft = build_plan_skeleton(planning_ir, profile, constraints, selected_architecture)
    draft = merge_core_design(draft, fallback_core_design(draft, planning_ir, constraints, selected_architecture, profile))
    draft = merge_module_contracts(draft, fallback_module_contracts(draft, profile, constraints, selected_architecture))
    for module in list(draft.get("module_contracts", [])):
        draft = merge_function_inventory(draft, fallback_function_inventory(draft, module))
    for module in list(draft.get("module_contracts", [])):
        module_id = str(module.get("module_id", ""))
        draft = merge_function_signatures(draft, fallback_function_signatures(draft, module_id))
    for module in list(draft.get("module_contracts", [])):
        draft = merge_function_behavior(draft, fallback_function_behavior(draft, str(module.get("module_id", ""))))
    draft = merge_wire_access_binding(draft, fallback_wire_access_binding(draft, planning_ir))
    draft = merge_calls_allowed(draft, fallback_calls_allowed(draft))
    draft = merge_file_layout(draft, fallback_file_layout(draft))
    return finalize_dependency_graph(draft)
