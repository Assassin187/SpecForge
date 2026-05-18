from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..schemas.implementation_plan import SCHEMA_VERSION
from .dependencies import derive_dependency_graph
from .implementation_plan import _capability_refs, _field_value, _function_signature, _handler_surfaces, _safe_id, _surface_units, _target_directives, _wire_fields


def _constraint_ids(constraints: dict[str, Any]) -> list[str]:
    return [
        str(item.get("constraint_id", "")).strip()
        for item in constraints.get("constraints", [])
        if isinstance(item, dict) and str(item.get("constraint_id", "")).strip()
    ]


def _producer(stage: str, prompt_name: str) -> dict[str, str]:
    return {"stage": stage, "prompt_name": prompt_name, "prompt_version": "deterministic_fallback"}


def _trace(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item) for item in value if str(item).strip())
        elif str(value).strip():
            result.append(str(value))
    return result


def _contract(kind: str, *, type_refs: list[str] | None = None, buffer_refs: list[str] | None = None, notes: str = "") -> dict[str, Any]:
    return {
        "contract_kind": kind,
        "type_refs": type_refs or [],
        "buffer_refs": buffer_refs or [],
        "ownership": "borrowed" if kind in {"typed", "buffer", "opaque"} else "none",
        "nullability": "unknown",
        "validation_required": kind in {"typed", "buffer"},
        "notes": notes,
    }


def _error_behavior(error_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "error_ids": error_ids or [],
        "propagation": "return_code",
        "recovery": "cleanup",
        "return_policy": "status_code",
    }


def _error_behavior_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    return (
        f"propagation={value.get('propagation', 'unknown')}; "
        f"recovery={value.get('recovery', 'unknown')}; "
        f"return_policy={value.get('return_policy', 'unknown')}"
    )


def _default_signature(function: dict[str, Any], module_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    protocol = str(function.get("protocol_name", "protocol"))
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    handle_type = f"{prefix}_t"
    kind = str(function.get("function_kind", "public_api"))
    name = str(function.get("name", "function"))
    params: list[dict[str, Any]]
    return_type = "int"
    if name.endswith("_create"):
        return_type = f"{handle_type}*"
        params = []
    elif name.endswith("_destroy"):
        return_type = "void"
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}]
    elif kind == "parser":
        params = [{"type": "const uint8_t*", "name": "buffer", "nullable": False, "ownership": "borrowed"}, {"type": "size_t", "name": "length", "nullable": False, "ownership": "value"}]
    elif kind == "serializer":
        params = [{"type": "uint8_t*", "name": "buffer", "nullable": False, "ownership": "borrowed"}, {"type": "size_t", "name": "capacity", "nullable": False, "ownership": "value"}]
    elif kind == "handler":
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}, {"type": "const void*", "name": "message", "nullable": False, "ownership": "borrowed"}]
    else:
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "borrowed"}]
    return _function_signature(return_type, name, params), params, return_type


def build_plan_skeleton(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
) -> dict[str, Any]:
    protocol = _safe_id(_field_value(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))))
    modules = selected_architecture.get("architecture", {}).get("modules", [])
    capabilities = _capability_refs(profile)
    wire_fields = _wire_fields(planning_ir)
    surfaces = _surface_units(planning_ir, profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_name": protocol,
        "target_directives_ref": {
            "schema_version": planning_ir.get("target_directives", {}).get("schema_version", "target_directives/v1"),
            "directives": _target_directives(planning_ir),
        },
        "source_artifacts": {
            "planning_ir": "003_planning_ir.json",
            "protocol_profile": "004_protocol_profile.json",
            "engineering_constraints": "005_engineering_constraints.json",
            "selected_architecture": "006_selected_architecture.json",
        },
        "source_artifact_refs": {
            "planning_ir": "003_planning_ir.json",
            "protocol_profile": "004_protocol_profile.json",
            "engineering_constraints": "005_engineering_constraints.json",
            "selected_architecture": "006_selected_architecture.json",
        },
        "id_namespace": {
            "module_ids": [str(item.get("module_id", "")) for item in modules if isinstance(item, dict)],
            "capability_ids": list(capabilities),
            "constraint_ids": _constraint_ids(constraints),
            "field_ids": [str(item.get("field_id", "")) for item in wire_fields],
            "function_id_pattern": "fn:<module_id>:<action>",
            "file_id_pattern": "file:<module_id>",
            "header_file_id_pattern": "header:<module_id>",
        },
        "validation_targets": {
            "capability_coverage": True,
            "handler_coverage": True,
            "wire_field_coverage": True,
            "dependency_derivation_only": True,
            "blueprint_no_new_engineering_semantics": True,
        },
        "module_contracts": [],
        "canonical_types": [],
        "state_design": [],
        "handler_matrix": [],
        "resource_lifecycle": [],
        "error_strategy": [],
        "function_contracts": [],
        "file_layout": {"files": []},
        "wire_mapping_table": [],
        "access_path_table": [],
        "dependency_graph": None,
        "test_plan": [],
        "traceability": {"required_capabilities": list(capabilities), "constraint_ids": _constraint_ids(constraints)},
        "unresolved_questions": planning_ir.get("unresolved_facts", []),
        "deterministic_indexes": {
            "capability_index": capabilities,
            "constraint_index": {item["constraint_id"]: item for item in constraints.get("constraints", []) if isinstance(item, dict) and item.get("constraint_id")},
            "module_index": {str(item.get("module_id", "")): item for item in modules if isinstance(item, dict)},
            "surface_unit_index": {str(item.get("name", "")): item for item in surfaces if isinstance(item, dict) and item.get("name")},
            "message_index": {str(item.get("message", "")): {"message": item.get("message")} for item in wire_fields},
            "field_index": {str(item.get("field_id", "")): item for item in wire_fields if item.get("field_id")},
            "trace_ref_index": {"capabilities": list(capabilities), "constraints": _constraint_ids(constraints)},
        },
        "accepted_stage_artifacts": [],
    }


def fallback_core_design(draft: dict[str, Any], planning_ir: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    modules = selected_architecture.get("architecture", {}).get("modules", [])
    module_ids = {str(item.get("module_id", "")) for item in modules if isinstance(item, dict)}
    states: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", ""))
        for state in module.get("state_owned", []):
            states.append({"state_id": f"state:{_safe_id(str(state))}", "name": str(state), "owner_module_id": module_id})
    if not states and "semantic_core" in module_ids:
        states.append({"state_id": "state:protocol_session_state", "name": "protocol_session_state", "owner_module_id": "semantic_core"})
    surfaces = _handler_surfaces(_surface_units(planning_ir, profile or {}), str(draft.get("target_directives_ref", {}).get("directives", {}).get("target_role", "")))
    handler_owner = "semantic_core" if "semantic_core" in module_ids else next(iter(module_ids), "")
    return {
        "schema_version": "core_design_candidate/v1",
        "candidate_id": "candidate:core_design:deterministic",
        "producer": _producer("5.2_core_design", "core_design_candidate_prompt"),
        "canonical_types": [
            {
                "type_id": "type:opaque_module_context",
                "name": f"{draft.get('protocol_name', 'protocol')}_module_context",
                "kind": "opaque",
                "owner_module_id": next(iter(module_ids), ""),
                "source_message_ids": [],
                "source_field_ids": [],
                "fields": [],
                "enum_values": [],
                "trace_ref_keys": [],
                "status": "inferred",
            }
        ],
        "state_design": [
            {
                "state_id": state["state_id"],
                "name": state["name"],
                "owner_module_id": state["owner_module_id"],
                "state_kind": "session",
                "lifecycle": ["create", "use", "destroy"],
                "read_by_module_ids": [state["owner_module_id"]],
                "mutated_by_module_ids": [state["owner_module_id"]],
                "source_capability_ids": [],
                "trace_ref_keys": [],
                "status": "inferred",
            }
            for state in states
        ],
        "handler_matrix": [
            {
                "handler_id": f"handler:{_safe_id(str(surface.get('name', 'surface')))}",
                "handler_kind": "message",
                "capability_id": "semantic_dispatch",
                "owner_module_id": handler_owner,
                "message_ids": [],
                "trigger": str(surface.get("name", "")),
                "responsibility": f"Handle target-scope surface unit {surface.get('name')}.",
                "trace_ref_keys": _trace(surface.get("source_fact_ids", [])),
                "status": "inferred",
            }
            for surface in surfaces
        ],
        "resource_lifecycle": [
            {
                "resource_id": f"resource:{module_id}",
                "name": f"{module_id}_resource",
                "owner_module_id": module_id,
                "lifecycle_states": ["created", "active", "destroyed"],
                "init_required": True,
                "cleanup_required": True,
                "error_handling": "Return an error status when initialization or cleanup fails.",
                "trace_ref_keys": [],
                "status": "inferred",
            }
            for module_id in sorted(module_ids)
        ],
        "error_strategy": [
            {
                "error_id": "error:protocol_error",
                "error_kind": "protocol",
                "owner_module_id": handler_owner,
                "related_constraint_ids": _constraint_ids(constraints),
                "detection_points": ["parse", "validate", "handle"],
                "recovery_policy": "Clean up local resources and preserve module-owned state consistency.",
                "propagation_policy": "Return negative status for protocol or runtime errors where applicable.",
                "trace_ref_keys": _constraint_ids(constraints),
                "status": "inferred",
            }
        ],
        "test_plan_seed": [{"test_id": "test:coder_loader_compatibility", "purpose": "Generated specs must load through agent.coder.specs.", "trace_ref_keys": [], "status": "supported"}],
        "traceability": {"trace_ref_keys": _constraint_ids(constraints), "notes": "Deterministic fallback core design."},
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_core_design(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["canonical_types"] = candidate.get("canonical_types", [])
    result["state_design"] = candidate.get("state_design", [])
    result["handler_matrix"] = candidate.get("handler_matrix", [])
    result["resource_lifecycle"] = candidate.get("resource_lifecycle", [])
    result["error_strategy"] = candidate.get("error_strategy", [])
    result["test_plan"] = candidate.get("test_plan_seed", candidate.get("test_plan", []))
    result.setdefault("traceability", {}).update(candidate.get("traceability", {}))
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.2_core_design")
    return result


def fallback_module_contracts(draft: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    capability_refs = _capability_refs(profile)
    modules = selected_architecture.get("architecture", {}).get("modules", [])
    contracts = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = _safe_id(str(module.get("module_id", module.get("name", "module"))))
        owned = [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()]
        source_fact_ids = sorted(
            {
                str(ref)
                for cap in owned
                for ref in capability_refs.get(cap, {}).get("source_fact_ids", [])
                if str(ref).strip()
            }
        )
        contracts.append(
            {
                "module_id": module_id,
                "purpose": "; ".join(str(item) for item in module.get("responsibilities", []) if str(item).strip()) or f"Own {module_id} responsibilities.",
                "owned_capability_ids": owned,
                "consumed_capability_ids": [str(cap) for cap in module.get("consumed_capabilities", [])],
                "public_api_policy": {
                    "exposes_public_api": True,
                    "api_style": "opaque_handle",
                    "visibility_rules": ["Expose public functions in generated headers.", "Keep module-owned state opaque."],
                    "notes": "Deterministic fallback public API policy.",
                },
                "owned_state_ids": [
                    f"state:{_safe_id(str(item))}"
                    for item in module.get("state_owned", [])
                    if str(item).strip()
                ],
                "read_state_ids": [],
                "mutated_state_ids": [
                    f"state:{_safe_id(str(item))}"
                    for item in module.get("state_owned", [])
                    if str(item).strip()
                ],
                "error_responsibility_ids": ["error:protocol_error"],
                "constraint_ids": _constraint_ids(constraints),
                "dependency_policy": "No module dependency graph is emitted by this candidate.",
                "trace_ref_keys": source_fact_ids,
                "status": "inferred",
            }
        )
    return {
        "schema_version": "module_contracts_candidate/v1",
        "candidate_id": "candidate:module_contracts:deterministic",
        "producer": _producer("5.3_module_contracts", "module_contracts_candidate_prompt"),
        "module_contracts": contracts,
        "capability_ownership_claims": [
            {
                "capability_id": cap,
                "primary_owner_module_id": contract["module_id"],
                "shared_owner_module_ids": [],
                "ownership_kind": "primary",
                "reason": "Derived from selected architecture owned_capabilities.",
            }
            for contract in contracts
            for cap in contract["owned_capability_ids"]
        ],
        "state_ownership_claims": [
            {
                "state_id": state_id,
                "owner_module_id": contract["module_id"],
                "read_by_module_ids": [contract["module_id"]],
                "mutated_by_module_ids": [contract["module_id"]],
                "reason": "Derived from selected architecture state ownership.",
            }
            for contract in contracts
            for state_id in contract["owned_state_ids"]
        ],
        "constraint_bindings": [
            {"constraint_id": constraint_id, "module_ids": [contract["module_id"] for contract in contracts], "binding_reason": "Fallback applies global engineering constraints to every module."}
            for constraint_id in _constraint_ids(constraints)
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_module_contracts(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["module_contracts"] = [
        {
            "module_id": item["module_id"],
            "name": item["module_id"],
            "purpose": item["purpose"],
            "owned_capabilities": item["owned_capability_ids"],
            "consumed_capabilities": item["consumed_capability_ids"],
            "support_module": False,
            "public_api_policy": item["public_api_policy"]["notes"],
            "state_owned": item["owned_state_ids"],
            "errors_raised": item["error_responsibility_ids"],
            "constraints": item["constraint_ids"],
            "source_fact_ids": item["trace_ref_keys"],
            "decision_ids": [f"decision:module:{item['module_id']}"],
        }
        for item in candidate.get("module_contracts", [])
        if isinstance(item, dict)
    ]
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.3_module_contracts")
    return result


def _contract_base(*, draft: dict[str, Any], module_id: str, action: str, name: str, function_kind: str, purpose: str, capability_ids: list[str]) -> dict[str, Any]:
    return {
        "function_id": f"fn:{module_id}:{action}",
        "name": name,
        "module_id": module_id,
        "placement_hint": module_id,
        "required_declaration": function_kind in {"public_api", "parser", "serializer", "handler", "resource_lifecycle"},
        "visibility": "public",
        "function_kind": function_kind,
        "purpose": purpose,
        "capability_ids": capability_ids,
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "trace_ref_keys": [f"decision:function:{module_id}:{action}"],
        "status": "inferred",
    }


def fallback_function_inventory(draft: dict[str, Any], module_contract: dict[str, Any]) -> dict[str, Any]:
    protocol = str(draft.get("protocol_name", "protocol"))
    module_id = str(module_contract.get("module_id", "module"))
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    owned = [str(cap) for cap in module_contract.get("owned_capabilities", [])]
    functions = [
        _contract_base(draft=draft, module_id=module_id, action="create", name=f"{prefix}_create", function_kind="resource_lifecycle", purpose=f"Allocate and initialize the {module_id} module context.", capability_ids=owned[:1]),
        _contract_base(draft=draft, module_id=module_id, action="destroy", name=f"{prefix}_destroy", function_kind="resource_lifecycle", purpose=f"Release resources owned by the {module_id} module context.", capability_ids=owned[:1]),
    ]
    if module_id == "transport_runtime":
        for action in ("start", "run_once", "stop"):
            functions.append(_contract_base(draft=draft, module_id=module_id, action=action, name=f"{prefix}_{action}", function_kind="public_api", purpose=f"Perform transport runtime action: {action}.", capability_ids=[cap for cap in owned if cap in {"transport_io", "connection_lifecycle", "connection_buffering"}]))
    elif module_id == "protocol_codec":
        functions.append(_contract_base(draft=draft, module_id=module_id, action="decode_message", name=f"{prefix}_decode_message", function_kind="parser", purpose="Protocol codec decode message entry point.", capability_ids=[cap for cap in owned if cap.startswith("message_") or cap.endswith("framing")]))
        functions.append(_contract_base(draft=draft, module_id=module_id, action="encode_message", name=f"{prefix}_encode_message", function_kind="serializer", purpose="Protocol codec encode message entry point.", capability_ids=[cap for cap in owned if cap.startswith("message_") or cap.endswith("framing")]))
    elif module_id == "semantic_core":
        functions.append(_contract_base(draft=draft, module_id=module_id, action="dispatch_message", name=f"{prefix}_dispatch_message", function_kind="handler", purpose="Dispatch a decoded message to the target-role handler matrix.", capability_ids=[cap for cap in owned if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}]))
        for handler in draft.get("handler_matrix", []):
            if not isinstance(handler, dict):
                continue
            surface = str(handler.get("trigger") or handler.get("handler_id") or "surface")
            surface_id = _safe_id(surface)
            item = _contract_base(draft=draft, module_id=module_id, action=f"handle_{surface_id}", name=f"{prefix}_handle_{surface_id}", function_kind="handler", purpose=f"Handle target-scope surface unit {surface}.", capability_ids=[cap for cap in owned if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}])
            item["covers_handler_ids"] = [str(handler.get("handler_id", ""))]
            functions.append(item)
    elif module_id == "resource_store":
        for action in ("open_session", "close_session", "lookup_resource"):
            functions.append(_contract_base(draft=draft, module_id=module_id, action=action, name=f"{prefix}_{action}", function_kind="state_machine", purpose=f"Resource and session lifecycle helper: {action}.", capability_ids=owned))
    else:
        functions.append(_contract_base(draft=draft, module_id=module_id, action="run", name=f"{prefix}_run", function_kind="public_api", purpose=f"Run the composed target-role boundary for module {module_id}.", capability_ids=owned))
    return {
        "schema_version": "function_inventory_candidate/v1",
        "candidate_id": f"candidate:function_inventory:{module_id}",
        "producer": _producer("5.4a_function_inventory", "function_inventory_candidate_prompt"),
        "functions": functions,
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_function_inventory(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    existing = {str(item.get("function_id")) for item in result.get("function_contracts", []) if isinstance(item, dict)}
    for function in candidate.get("functions", []):
        if isinstance(function, dict) and str(function.get("function_id")) not in existing:
            item = {
                "file_id": "",
                "declared_in": "",
                "signature": {},
                "input_contract": {},
                "output_contract": {},
                "state_access": [],
                "wire_mapping": [],
                "access_paths": [],
                "error_behavior": "",
                "calls_allowed": [],
                "side_effects": [],
                "traceability": {"source_fact_ids": function.get("trace_ref_keys", []), "decision_ids": function.get("trace_ref_keys", [])},
                **function,
            }
            item.pop("placement_hint", None)
            item.pop("required_declaration", None)
            item.pop("trace_ref_keys", None)
            item.pop("status", None)
            result.setdefault("function_contracts", []).append(item)
            existing.add(str(function.get("function_id")))
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4a_function_inventory")
    return result


def fallback_function_details(draft: dict[str, Any], module_id: str) -> dict[str, Any]:
    updates = []
    module_error_ids = [
        str(error.get("error_id"))
        for error in draft.get("error_strategy", [])
        if isinstance(error, dict) and str(error.get("owner_module_id", "")) == module_id
    ] or [str(error.get("error_id")) for error in draft.get("error_strategy", []) if isinstance(error, dict) and error.get("error_id")]
    for function in draft.get("function_contracts", []):
        if not isinstance(function, dict) or str(function.get("module_id")) != module_id:
            continue
        kind = str(function.get("function_kind", "public_api"))
        updates.append(
            {
                "function_id": function.get("function_id"),
                "input_contract": _contract("buffer" if kind in {"parser", "serializer"} else "opaque", notes=f"Inputs required by {kind} function."),
                "output_contract": _contract("typed", notes="Return generated C status or handle value."),
                "state_access": [
                    {"state_id": state.get("state_id"), "access_kind": "read_write", "required": True, "reason": "Function belongs to the state owner module."}
                    for state in draft.get("state_design", [])
                    if isinstance(state, dict) and state.get("owner_module_id") == module_id
                ],
                "error_behavior": _error_behavior(module_error_ids),
                "side_effects": [],
                "preconditions": [],
                "postconditions": [],
                "trace_ref_keys": function.get("traceability", {}).get("decision_ids", []),
                "status": "inferred",
            }
        )
    return {
        "schema_version": "function_contract_detail_patch/v1",
        "patch_id": f"patch:function_details:{module_id}",
        "producer": _producer("5.4b_function_details", "function_contract_detail_patch_prompt"),
        "function_contract_updates": updates,
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_function_details(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    updates = {str(item.get("function_id")): item for item in patch.get("function_contract_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if not update:
            continue
        signature, params, return_type = _default_signature(function, str(function.get("module_id", "")))
        function["signature"] = signature
        function["input_contract"] = {"params": params, "candidate_contract": update["input_contract"]}
        function["output_contract"] = {"return_type": return_type, "candidate_contract": update["output_contract"]}
        function["state_access"] = update["state_access"]
        function["error_behavior"] = _error_behavior_text(update["error_behavior"])
        function["side_effects"] = update["side_effects"]
        function["preconditions"] = update["preconditions"]
        function["postconditions"] = update["postconditions"]
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4b_function_details")
    return result


def fallback_wire_access_binding(draft: dict[str, Any], planning_ir: dict[str, Any]) -> dict[str, Any]:
    wire_fields = _wire_fields(planning_ir)
    parser_ids = [str(item.get("function_id")) for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_kind") == "parser"]
    serializer_ids = [str(item.get("function_id")) for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_kind") == "serializer"]
    parser_id = parser_ids[0] if parser_ids else ""
    serializer_id = serializer_ids[0] if serializer_ids else ""
    states = [item for item in draft.get("state_design", []) if isinstance(item, dict) and item.get("state_id")]
    state_id = str(states[0].get("state_id")) if states else "state:protocol_session_state"
    entries: list[dict[str, Any]] = []
    access: list[dict[str, Any]] = []
    binding_by_function: dict[str, dict[str, Any]] = {}
    for item in wire_fields:
        message_id = f"message:{_safe_id(item['message'])}"
        for direction, function_id in (("parse", parser_id), ("serialize", serializer_id)):
            if not function_id:
                continue
            wire_mapping_id = f"wire:{direction}:{_safe_id(item['message'])}:{_safe_id(item['field'])}"
            entries.append(
                {
                    "wire_mapping_id": wire_mapping_id,
                    "function_id": function_id,
                    "message_id": message_id,
                    "field_id": item["field_id"],
                    "direction": direction,
                    "mapping_role": "codec_field_binding",
                    "required": True,
                    "trace_ref_keys": item["source_fact_ids"],
                    "status": "inferred",
                }
            )
            binding_by_function.setdefault(function_id, {"function_id": function_id, "wire_mapping_ids": [], "access_path_ids": []})["wire_mapping_ids"].append(wire_mapping_id)
        access_path_id = item["access_path_id"]
        access.append(
            {
                "access_path_id": access_path_id,
                "function_id": parser_id or serializer_id,
                "state_id": state_id,
                "access_kind": "read",
                "access_path": item["access_path"],
                "required": True,
                "trace_ref_keys": item["source_fact_ids"],
                "status": "inferred",
            }
        )
        for function_id in (parser_id, serializer_id):
            if function_id:
                binding_by_function.setdefault(function_id, {"function_id": function_id, "wire_mapping_ids": [], "access_path_ids": []})["access_path_ids"].append(access_path_id)
    return {
        "schema_version": "wire_access_binding_patch/v1",
        "patch_id": "patch:wire_access_binding:deterministic",
        "producer": _producer("5.4c_wire_access_binding", "wire_access_binding_patch_prompt"),
        "wire_mapping_entries": entries,
        "access_path_entries": access,
        "function_binding_updates": list(binding_by_function.values()),
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_wire_access_binding(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    entries = [item for item in patch.get("wire_mapping_entries", []) if isinstance(item, dict)]
    access_entries = [item for item in patch.get("access_path_entries", []) if isinstance(item, dict)]
    access_by_id = {str(item.get("access_path_id")): item for item in access_entries}
    by_field: dict[str, dict[str, Any]] = {}
    for entry in entries:
        field_id = str(entry.get("field_id", ""))
        current = by_field.setdefault(
            field_id,
            {
                "mapping_id": f"wire:{field_id}",
                "field_id": field_id,
                "message": str(entry.get("message_id", "")).removeprefix("message:"),
                "field": field_id,
                "parser_function_id": "",
                "serializer_function_id": "",
                "access_path_id": "",
                "source_fact_ids": entry.get("trace_ref_keys", []),
            },
        )
        access_id = next((item.get("access_path_id") for item in access_entries if item.get("function_id") == entry.get("function_id")), "")
        current["access_path_id"] = access_id or current["access_path_id"]
        if entry.get("direction") == "parse":
            current["parser_function_id"] = entry.get("function_id", "")
        elif entry.get("direction") == "serialize":
            current["serializer_function_id"] = entry.get("function_id", "")
    result["wire_mapping_table"] = list(by_field.values())
    result["access_path_table"] = [
        {
            "access_path_id": item["access_path_id"],
            "path": item["access_path"],
            "field_id": next((entry.get("field_id", "") for entry in entries if entry.get("function_id") == item.get("function_id")), ""),
            "owner_module_id": next((state.get("owner_module_id", "") for state in result.get("state_design", []) if state.get("state_id") == item.get("state_id")), ""),
            "source_fact_ids": item.get("trace_ref_keys", []),
        }
        for item in access_entries
    ]
    updates = {str(item.get("function_id")): item for item in patch.get("function_binding_updates", []) if isinstance(item, dict)}
    entry_by_id = {str(item.get("wire_mapping_id")): item for item in entries}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if update:
            function["wire_mapping"] = [
                {
                    "mapping_id": wire_id,
                    "field_id": entry_by_id.get(wire_id, {}).get("field_id", ""),
                    "message": str(entry_by_id.get(wire_id, {}).get("message_id", "")).removeprefix("message:"),
                    "field": entry_by_id.get(wire_id, {}).get("field_id", ""),
                    "direction": entry_by_id.get(wire_id, {}).get("direction", ""),
                    "access_path_id": update.get("access_path_ids", [""])[0] if update.get("access_path_ids") else "",
                }
                for wire_id in update.get("wire_mapping_ids", [])
            ]
            function["access_paths"] = update.get("access_path_ids", [])
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4c_wire_access_binding")
    return result


def fallback_calls_allowed(draft: dict[str, Any]) -> dict[str, Any]:
    functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    by_module_kind: dict[tuple[str, str], list[str]] = {}
    for function in functions:
        by_module_kind.setdefault((str(function.get("module_id", "")), str(function.get("function_kind", ""))), []).append(str(function.get("function_id", "")))
    updates = []
    for function in functions:
        module_id = str(function.get("module_id", ""))
        kind = str(function.get("function_kind", ""))
        helpers = by_module_kind.get((module_id, "state_machine"), []) + by_module_kind.get((module_id, "resource_lifecycle"), [])
        calls: list[str] = []
        if kind == "handler":
            calls = helpers[:3]
        elif kind == "public_api":
            calls = by_module_kind.get((module_id, "parser"), [])[:1] + by_module_kind.get((module_id, "handler"), [])[:1]
        elif kind in {"parser", "serializer"}:
            calls = []
        updates.append(
            {
                "caller_function_id": function.get("function_id"),
                "calls_allowed": [
                    {
                        "callee_function_id": call,
                        "call_reason": "deterministic conservative fallback",
                        "required": False,
                        "call_kind": "utility" if kind not in {"handler", "public_api"} else "handler_dispatch",
                        "trace_ref_keys": [],
                        "status": "inferred",
                    }
                    for call in calls
                    if call and call != function.get("function_id")
                ],
            }
        )
    return {
        "schema_version": "calls_allowed_candidate/v1",
        "candidate_id": "candidate:calls_allowed:deterministic",
        "producer": _producer("5.4d_calls_allowed", "calls_allowed_candidate_prompt"),
        "calls_allowed_updates": updates,
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_calls_allowed(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    updates = {str(item.get("caller_function_id")): item for item in candidate.get("calls_allowed_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if update:
            function["calls_allowed"] = [edge["callee_function_id"] for edge in update.get("calls_allowed", [])]
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4d_calls_allowed")
    return result


def fallback_file_layout(draft: dict[str, Any]) -> dict[str, Any]:
    protocol = str(draft.get("protocol_name", "protocol"))
    files = []
    assignments = []
    for module in draft.get("module_contracts", []):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", "module"))
        file_id = f"file:{module_id}"
        header_file_id = f"header:{module_id}"
        source_path = f"{protocol}/{module_id}/{module_id}.c"
        header_path = f"{protocol}/{module_id}/{module_id}.h"
        module_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict) and str(item.get("module_id")) == module_id]
        public_functions = [str(item.get("function_id")) for item in module_functions if str(item.get("visibility", "public")).lower() == "public"]
        files.extend(
            [
                {
                    "file_id": header_file_id,
                    "path": header_path,
                    "module_id": module_id,
                    "kind": "header",
                    "responsibility": f"Declare the {module_id} public interface.",
                    "exports_function_ids": public_functions,
                    "implements_function_ids": [],
                    "exports_type_ids": [],
                    "imports_allowed": [],
                    "trace_ref_keys": module.get("source_fact_ids", []),
                    "status": "inferred",
                },
                {
                    "file_id": file_id,
                    "path": source_path,
                    "module_id": module_id,
                    "kind": "source",
                    "responsibility": f"Implement the {module_id} module contract.",
                    "exports_function_ids": [],
                    "implements_function_ids": [str(item.get("function_id")) for item in module_functions],
                    "exports_type_ids": [],
                    "imports_allowed": [header_file_id],
                    "trace_ref_keys": module.get("source_fact_ids", []),
                    "status": "inferred",
                },
            ]
        )
        for function in module_functions:
            assignments.append(
                {
                    "function_id": function.get("function_id"),
                    "implementation_file_id": file_id,
                    "declaration_file_id": header_file_id if str(function.get("visibility", "public")).lower() == "public" else "",
                    "visibility": str(function.get("visibility", "public")),
                    "reason": "Deterministic module source/header placement.",
                    "status": "inferred",
                }
            )
    return {
        "schema_version": "file_layout_candidate/v1",
        "candidate_id": "candidate:file_layout:deterministic",
        "producer": _producer("5.5_file_layout", "file_layout_candidate_prompt"),
        "files": files,
        "function_file_assignments": assignments,
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_file_layout(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    source_by_module = {str(item.get("module_id")): item for item in candidate.get("files", []) if isinstance(item, dict) and item.get("kind") == "source"}
    header_by_module = {str(item.get("module_id")): item for item in candidate.get("files", []) if isinstance(item, dict) and item.get("kind") == "header"}
    result["file_layout"] = {
        "files": [
            {
                "file_id": source["file_id"],
                "header_file_id": header_by_module.get(module_id, {}).get("file_id", ""),
                "module_id": module_id,
                "kind": "source_header_pair",
                "path": source["path"],
                "source_path": source["path"],
                "header_path": header_by_module.get(module_id, {}).get("path", ""),
                "responsibility": source["responsibility"],
                "exports": header_by_module.get(module_id, {}).get("exports_function_ids", []),
                "implements": source["implements_function_ids"],
                "imports_allowed": [item for item in source.get("imports_allowed", []) if str(item).startswith("file:")],
                "traceability": {"source_fact_ids": source.get("trace_ref_keys", []), "decision_ids": [f"decision:file:{module_id}"]},
            }
            for module_id, source in source_by_module.items()
        ]
    }
    assignments = {str(item.get("function_id")): item for item in candidate.get("function_file_assignments", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        assignment = assignments.get(str(function.get("function_id")))
        if assignment:
            function["file_id"] = assignment.get("implementation_file_id", "")
            function["declared_in"] = assignment.get("declaration_file_id", "")
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.5_file_layout")
    return result


def apply_dependency_repair_patch(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    remove_edges = {
        (str(item.get("caller_function_id", "")), str(item.get("callee_function_id", "")))
        for item in patch.get("repair_actions", [])
        if isinstance(item, dict)
        and item.get("action_kind") == "remove_call_edge"
    }
    for function in result.get("function_contracts", []):
        caller = str(function.get("function_id", ""))
        function["calls_allowed"] = [callee for callee in function.get("calls_allowed", []) if (caller, str(callee)) not in remove_edges]
    for action in patch.get("repair_actions", []):
        if not isinstance(action, dict):
            continue
        if action.get("action_kind") == "adjust_imports_allowed":
            for file_item in result.get("file_layout", {}).get("files", []):
                if str(file_item.get("file_id", "")) == str(action.get("file_id", "")):
                    current = [str(item) for item in file_item.get("imports_allowed", [])]
                    current = [item for item in current if item not in set(action.get("remove_import_file_ids", []))]
                    for item in action.get("add_import_file_ids", []):
                        if item not in current:
                            current.append(item)
                    file_item["imports_allowed"] = current
        elif action.get("action_kind") == "lower_visibility":
            for function in result.get("function_contracts", []):
                if str(function.get("function_id", "")) == str(action.get("function_id", "")):
                    function["visibility"] = action.get("new_visibility", function.get("visibility", "internal"))
        elif action.get("action_kind") == "change_function_file_assignment":
            for function in result.get("function_contracts", []):
                if str(function.get("function_id", "")) == str(action.get("function_id", "")):
                    function["file_id"] = action.get("new_implementation_file_id", function.get("file_id", ""))
                    function["declared_in"] = action.get("new_declaration_file_id", function.get("declared_in", ""))
        elif action.get("action_kind") == "mark_unresolved":
            result.setdefault("unresolved_questions", []).append({"target_id": action.get("target_id", ""), "question": action.get("reason", ""), "stage": "5.6_dependency_repair"})
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    return result


def fallback_dependency_repair_patch(draft: dict[str, Any], dependency_errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "dependency_repair_patch/v1",
        "patch_id": "patch:dependency_repair:deterministic_empty",
        "producer": _producer("5.6_dependency_repair", "dependency_repair_patch_prompt"),
        "repair_actions": [
            {
                "action_kind": "mark_unresolved",
                "target_id": str(error.get("path") or error.get("code", "dependency_graph")),
                "target_kind": "dependency_error",
                "reason": str(error.get("message", "Dependency validation failed.")),
            }
            for error in dependency_errors
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def apply_deterministic_dependency_fallback(draft: dict[str, Any], dependency_errors: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(draft)
    for function in result.get("function_contracts", []):
        if isinstance(function, dict):
            function["calls_allowed"] = []
    for file_item in result.get("file_layout", {}).get("files", []):
        if isinstance(file_item, dict):
            file_item["imports_allowed"] = []
    result.setdefault("unresolved_questions", []).extend(
        {
            "target_id": str(error.get("path") or error.get("code", "dependency_graph")),
            "question": str(error.get("message", "Dependency validation failed; deterministic fallback removed dependency inputs.")),
            "stage": "5.6_dependency_repair",
        }
        for error in dependency_errors
    )
    return result


def finalize_dependency_graph(draft: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["dependency_graph"] = derive_dependency_graph(result)
    result.pop("accepted_stage_artifacts", None)
    return result
