from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ..schemas.implementation_plan import SCHEMA_VERSION
from .coder_spec_lowering import lower_canonical_type_to_header_data, normalize_param_ownership_for_coder, normalize_type_key
from .dependencies import derive_dependency_graph
from .implementation_plan import _capability_refs, _field_value, _function_signature, _handler_surfaces, _safe_id, _surface_units, _target_directives, _wire_fields
from .implementation_plan_context import SYSTEM_TYPE_IDS, normalize_type_inventory_candidate


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


def _nonblocking_questions(items: Any) -> list[Any]:
    result: list[Any] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            question = deepcopy(item)
            question["blocking"] = False
            result.append(question)
        elif str(item).strip():
            result.append({"question": str(item), "blocking": False})
    return result


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


def _lifecycle_name_matches(action: str, name: str) -> bool:
    if action == "run":
        return name.endswith("_run") or name.endswith("_serve")
    return name.endswith(f"_{action}")


def _is_lifecycle_function(function: dict[str, Any], action: str) -> bool:
    return (
        str(function.get("public_api_role", "")) == f"runtime_{action}"
        and str(function.get("function_kind", "")) != "handler"
        and (bool(function.get("exported")) or str(function.get("visibility", "")).lower() == "public" or str(function.get("api_surface", "")).lower() == "public")
        and _lifecycle_name_matches(action, str(function.get("name", "")))
    )


def _default_signature(function: dict[str, Any], module_id: str, protocol: str = "protocol") -> tuple[dict[str, Any], list[dict[str, Any]], str]:
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
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "BORROWED"}]
    elif kind == "parser":
        params = [{"type": "const uint8_t*", "name": "buffer", "nullable": False, "ownership": "BORROWED"}, {"type": "size_t", "name": "length", "nullable": False, "ownership": "UNKNOWN"}]
    elif kind == "serializer":
        params = [{"type": "uint8_t*", "name": "buffer", "nullable": False, "ownership": "BORROWED"}, {"type": "size_t", "name": "capacity", "nullable": False, "ownership": "UNKNOWN"}]
    elif kind == "handler":
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "BORROWED"}, {"type": "const void*", "name": "message", "nullable": False, "ownership": "BORROWED"}]
    else:
        params = [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "BORROWED"}]
    return _function_signature(return_type, name, params), params, return_type


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, list):
            value = next((item for item in value if str(item).strip()), "")
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""


def _role_values(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("value")
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                item = item.get("name") or item.get("value")
            role = str(item).strip().upper() if item is not None else ""
            if role and role not in result:
                result.append(role)
    return result


def _version_from_text(*values: Any) -> str:
    for value in values:
        text = str(value).strip() if value is not None else ""
        if not text:
            continue
        match = re.search(r"\b\d+(?:\.\d+)+(?:[A-Za-z0-9._-]*)?\b", text)
        if match:
            return match.group(0)
    return ""


def _default_port_from_facts(facts: dict[str, Any]) -> int | None:
    if str(facts.get("name", "")).strip().lower() == "default_port":
        match = re.search(r"\b(\d{1,5})\b", str(facts.get("value_or_rule", "")))
        if match and 1 <= int(match.group(1)) <= 65535:
            return int(match.group(1))
    for value in facts.values():
        if isinstance(value, dict):
            nested = _default_port_from_facts(value)
            if nested is not None:
                return nested
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    nested = _default_port_from_facts(item)
                    if nested is not None:
                        return nested
    return None


def _protocol_metadata(planning_ir: dict[str, Any], profile: dict[str, Any], protocol: str) -> dict[str, Any]:
    facts = planning_ir.get("protocol_facts", {}) if isinstance(planning_ir.get("protocol_facts"), dict) else {}
    meta = facts.get("protocol_meta", {}) if isinstance(facts.get("protocol_meta"), dict) else {}
    target = _target_directives(planning_ir)
    message_model = facts.get("message_model", {}) if isinstance(facts.get("message_model"), dict) else {}
    scope = _first_text(
        meta.get("target_scope"),
        meta.get("scope"),
        profile.get("minimum_scope"),
        target.get("scope"),
    )
    version = _first_text(
        meta.get("protocol_version"),
        meta.get("spec_version"),
        meta.get("version"),
        profile.get("protocol_version"),
        profile.get("spec_version"),
        profile.get("version"),
        target.get("protocol_version"),
        target.get("spec_version"),
        target.get("version"),
    ) or _version_from_text(scope, meta.get("target_scope"), message_model.get("summary"))
    roles = _role_values(
        target.get("enabled_roles"),
        target.get("target_role"),
        profile.get("roles"),
        profile.get("target_role"),
        meta.get("roles"),
    )
    result = {
        "name": _first_text(meta.get("protocol_name"), profile.get("protocol_name"), target.get("protocol_name"), protocol),
        "protocol_version": version,
        "roles": roles,
        "scope": scope,
        "source": "planning_ir.protocol_facts.protocol_meta+target_directives",
    }
    default_port = _default_port_from_facts(facts)
    if default_port is not None:
        result["default_port"] = default_port
    return result


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
        "protocol_metadata": _protocol_metadata(planning_ir, profile, protocol),
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
            "file_id_pattern": "file:<source_path_without_.c>",
        },
        "validation_targets": {
            "capability_coverage": True,
            "handler_coverage": True,
            "wire_field_coverage": True,
            "dependency_derivation_only": True,
            "specs_compile_no_new_engineering_semantics": True,
        },
        "module_artifacts": [],
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
        "producer": _producer("5.2a_core_design", "core_design_candidate_prompt"),
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
        "test_plan_seed": [
            {"test_id": "test:coder_loader_compatibility", "purpose": "Generated specs must load through agent.coder.specs.", "trace_ref_keys": [], "status": "supported"},
            {"test_id": "test:runtime_successful_interaction", "purpose": "Start the deployable protocol runtime and complete one successful external protocol interaction.", "trace_ref_keys": [], "status": "supported"},
            {"test_id": "test:runtime_malformed_survival", "purpose": "Send malformed protocol input and verify the runtime remains available for a later valid interaction.", "trace_ref_keys": [], "status": "supported"},
        ],
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
    result.setdefault("accepted_stage_artifacts", []).append("5.2a_core_design")
    return result


def _module_doc_refs(module: dict[str, Any], capability_refs: dict[str, dict[str, Any]]) -> list[str]:
    owned = [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()]
    return sorted(
        {
            str(ref)
            for cap in owned
            for ref in capability_refs.get(cap, {}).get("source_fact_ids", [])
            if str(ref).strip()
        }
    )


def _add_artifact(artifacts: list[dict[str, str]], seen: set[tuple[str, str]], name: str, kind: str, role: str) -> None:
    key = (name, kind)
    if name and key not in seen:
        seen.add(key)
        artifacts.append({"name": name, "kind": kind, "role": role})


def _module_artifacts_for(module: dict[str, Any], protocol: str, target_role: str) -> list[dict[str, str]]:
    module_id = _safe_id(str(module.get("module_id", module.get("name", "module"))))
    text = " ".join(
        [
            module_id,
            str(module.get("name", "")),
            " ".join(str(item) for item in module.get("responsibilities", [])),
            " ".join(str(item) for item in module.get("owned_capabilities", [])),
        ]
    ).lower()
    prefix = _safe_id(protocol)
    artifacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, kind: str, role: str) -> None:
        _add_artifact(artifacts, seen, name, kind, role)

    if any(word in text for word in ("network", "transport", "tcp")):
        add(f"{prefix}_connection_t", "TYPE", "Opaque protocol connection object.")
        add(f"{prefix}_tcp_server_t", "TYPE", "Protocol TCP server runtime object.")
        add(f"{prefix}_tcp_callbacks_t", "TYPE", "Callbacks used by the network runtime.")
        add(f"{prefix}_connection_read", "FUNC", "Read bytes from a protocol connection.")
        add(f"{prefix}_connection_send", "FUNC", "Queue bytes for a protocol connection.")
        add(f"{prefix}_connection_flush", "FUNC", "Flush queued connection bytes.")
        add(f"{prefix}_connection_close", "FUNC", "Close a protocol connection.")
    elif any(word in text for word in ("semantic", "state_machine", "protocol_error_policy")):
        add(f"{prefix}_{module_id}_t", "TYPE", f"Opaque {module_id} semantic module object.")
        add(f"{prefix}_{module_id}_dispatch", "FUNC", "Dispatch decoded protocol semantics.")
    elif any(word in text for word in ("codec", "framing", "decode", "encode", "parser", "serializer")):
        add(f"{prefix}_packet_t", "TYPE", "Decoded protocol packet container.")
        add(f"{prefix}_bytes_t", "TYPE", "Encoded protocol byte buffer.")
        add(f"{prefix}_decoder_feed", "FUNC", "Incrementally decode protocol bytes into packets.")
        add(f"{prefix}_encode_message", "FUNC", "Encode an outbound protocol packet.")
    elif "session" in text:
        add(f"{prefix}_session_t", "TYPE", "Protocol session object.")
        add(f"{prefix}_session_manager_t", "TYPE", "Session lookup and lifecycle registry.")
        add(f"{prefix}_session_mark_connected", "FUNC", "Mark a session as connected.")
        add(f"{prefix}_session_send", "FUNC", "Send protocol bytes through a session.")
        add(f"{prefix}_session_manager_get", "FUNC", "Lookup or create a managed session.")
    elif "topic" in text or "resource" in text:
        add(f"{prefix}_topic_tree_t", "TYPE", "Protocol topic/resource matching table.")
        add(f"{prefix}_topic_match", "FUNC", "Match one topic/resource pattern.")
        add(f"{prefix}_topic_tree_match_subscribers", "FUNC", "Find subscribers matching a topic/resource.")
    elif "router" in text or "dispatch" in text:
        add(f"{prefix}_message_router_t", "TYPE", "Protocol message router object.")
        add(f"{prefix}_message_router_subscribe", "FUNC", "Register a route/subscription.")
        add(f"{prefix}_message_router_publish", "FUNC", "Route or publish an inbound message.")
    elif "role_composition" in text or "app" in text or target_role in {"broker", "server", "client"} and any(word in text for word in ("broker", "server", "client", "role")):
        role = _safe_id(target_role or "app")
        add(f"{prefix}_{role}_t", "TYPE", "Top-level protocol runtime object.")
        add(f"{prefix}_{role}_create", "FUNC", "Create the top-level protocol runtime.")
        add(f"{prefix}_{role}_run", "FUNC", "Run the top-level protocol runtime.")
        add(f"{prefix}_{role}_destroy", "FUNC", "Destroy the top-level protocol runtime.")
        add("main", "FUNC", "Process entrypoint for the generated target.")
    else:
        add(f"{prefix}_{module_id}_t", "TYPE", f"Opaque {module_id} module object.")
        add(f"{prefix}_{module_id}_init", "FUNC", f"Initialize the {module_id} module boundary.")
    return artifacts


def _module_generation_order(modules: list[dict[str, Any]], dependencies_by_module: dict[str, list[str]]) -> list[str]:
    module_ids = [_safe_id(str(module.get("module_id", module.get("name", "module")))) for module in modules if isinstance(module, dict)]
    remaining = set(module_ids)
    result: list[str] = []
    while remaining:
        ready = [
            module_id
            for module_id in module_ids
            if module_id in remaining and all(dep not in remaining for dep in dependencies_by_module.get(module_id, []))
        ]
        if not ready:
            result.extend(module_id for module_id in module_ids if module_id in remaining)
            break
        for module_id in ready:
            remaining.remove(module_id)
            result.append(module_id)
    return result


def fallback_module_artifacts(draft: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    capability_refs = _capability_refs(profile)
    protocol = str(draft.get("protocol_name", "protocol"))
    target_role = _field_value(profile.get("target_role"), str(_target_directives(draft).get("target_role", "target")))
    modules = [module for module in selected_architecture.get("architecture", {}).get("modules", []) if isinstance(module, dict)]
    module_ids = {_safe_id(str(module.get("module_id", module.get("name", "module")))) for module in modules}
    dependencies_by_module: dict[str, list[str]] = {}
    entries: list[dict[str, Any]] = []
    for module in modules:
        module_id = _safe_id(str(module.get("module_id", module.get("name", "module"))))
        dependencies = [
            _safe_id(str(dep))
            for dep in module.get("dependency_hints", [])
            if _safe_id(str(dep)) in module_ids and _safe_id(str(dep)) != module_id
        ]
        dependencies_by_module[module_id] = sorted(set(dependencies))
        entries.append(
            {
                "module_id": module_id,
                "name": str(module.get("name", module_id)) or module_id,
                "role": "; ".join(str(item) for item in module.get("responsibilities", []) if str(item).strip()) or f"Provide the {module_id} module boundary.",
                "dependencies": dependencies_by_module[module_id],
                "artifacts": _module_artifacts_for(module, protocol, target_role),
                "files": [f"{protocol}/{module_id}/{module_id}.h", f"{protocol}/{module_id}/{module_id}.c"],
                "doc_ref": _module_doc_refs(module, capability_refs),
            }
        )
    return {
        "schema_version": "module_artifacts_candidate/v1",
        "candidate_id": "candidate:module_artifacts:deterministic",
        "producer": _producer("5.2b_module_artifacts", "module_artifacts_candidate_prompt"),
        "modules": entries,
        "generation_order": _module_generation_order(modules, dependencies_by_module),
        "consistency_rules": [
            {"id": "module_artifacts_unique_symbols", "rule": "artifact names are unique within each module", "doc_ref": []},
            {"id": "module_artifacts_dependency_order", "rule": "generation_order lists providers before consumers", "doc_ref": []},
        ],
        "forbidden_symbols": [
            {"name": name, "kind": "FUNC", "reason": "Bare C/POSIX-like symbols must use a protocol/module prefix."}
            for name in ("connect", "read", "write", "close", "send", "publish", "subscribe")
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_module_artifacts(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    arch_index = result.get("deterministic_indexes", {}).get("module_index", {})
    result["module_artifacts"] = []
    for item in candidate.get("modules", []):
        if not isinstance(item, dict):
            continue
        module_id = str(item.get("module_id", ""))
        arch_module = arch_index.get(module_id, {}) if isinstance(arch_index, dict) else {}
        result["module_artifacts"].append(
            {
                "module_id": module_id,
                "name": item.get("name", module_id),
                "purpose": item.get("role", ""),
                "role": item.get("role", ""),
                "owned_capabilities": [str(cap) for cap in arch_module.get("owned_capabilities", [])],
                "consumed_capabilities": [str(cap) for cap in arch_module.get("consumed_capabilities", [])],
                "support_module": bool(arch_module.get("support_module", False)),
                "dependencies": [str(dep) for dep in item.get("dependencies", []) if str(dep).strip()],
                "artifacts": deepcopy(item.get("artifacts", [])),
                "files": [str(path) for path in item.get("files", []) if str(path).strip()],
                "doc_ref": [str(ref) for ref in item.get("doc_ref", []) if str(ref).strip()],
                "state_owned": [str(state) for state in arch_module.get("state_owned", [])],
                "errors_raised": ["error:protocol_error"],
                "constraints": list(result.get("traceability", {}).get("constraint_ids", [])),
                "source_fact_ids": [str(ref) for ref in item.get("doc_ref", []) if str(ref).strip()],
                "decision_ids": [f"decision:module:{module_id}"],
            }
        )
    result["module_generation_order"] = [str(item) for item in candidate.get("generation_order", []) if str(item).strip()]
    result["module_consistency_rules"] = deepcopy(candidate.get("consistency_rules", []))
    result["forbidden_symbols"] = deepcopy(candidate.get("forbidden_symbols", []))
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.2b_module_artifacts")
    return result


def _canonical_kind_for_type_inventory(kind: str) -> str:
    if kind in {"enum", "bitflag"}:
        return "enum"
    if kind in {"opaque_handle", "internal_state"}:
        return "opaque"
    if kind in {"owned_buffer"}:
        return "buffer"
    if kind == "alias":
        return "alias"
    return "struct"


def _canonical_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "field_name": str(field.get("field_name", "")),
            "field_type": str(field.get("field_type", "")),
            "required": bool(field.get("required", False)),
            "source_field_id": "",
            "validation_notes": str(field.get("validation_notes") or field.get("lifetime") or ""),
        }
        for field in fields
        if isinstance(field, dict) and str(field.get("field_name", "")).strip()
    ]


def merge_type_inventory(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    candidate = normalize_type_inventory_candidate(candidate)
    existing_type_ids = {str(item.get("type_id", "")) for item in result.get("type_inventory", []) if isinstance(item, dict)}
    existing_canonical_ids = {str(item.get("type_id", "")) for item in result.get("canonical_types", []) if isinstance(item, dict)}
    for type_item in candidate.get("types", []):
        if not isinstance(type_item, dict):
            continue
        type_id = str(type_item.get("type_id", ""))
        if type_id not in existing_type_ids:
            result.setdefault("type_inventory", []).append(deepcopy(type_item))
            existing_type_ids.add(type_id)
        if (
            type_id
            and type_id not in existing_canonical_ids
            and str(type_item.get("visibility", "")) == "public"
            and str(type_item.get("defined_in", "")) == "public_header"
        ):
            result.setdefault("canonical_types", []).append(
                {
                    "type_id": type_id,
                    "name": type_item.get("name", ""),
                    "kind": _canonical_kind_for_type_inventory(str(type_item.get("kind", ""))),
                    "owner_module_id": type_item.get("module_id", ""),
                    "source_message_ids": [],
                    "source_field_ids": [],
                    "fields": _canonical_fields(type_item.get("fields", [])),
                    "enum_values": [
                        {"name": item.get("name", ""), "value": item.get("value", ""), "source_field_id": ""}
                        for item in type_item.get("enum_values", [])
                        if isinstance(item, dict)
                    ],
                    "trace_ref_keys": type_item.get("trace_ref_keys", []),
                    "status": type_item.get("status", "inferred"),
                }
            )
            existing_canonical_ids.add(type_id)
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.3_type_data")
    return result


def _replacement_function_name(functions: list[dict[str, Any]], action: str, type_name: str) -> str:
    suffixes_by_action = {
        "created_by": ("_create", "_init", "_open"),
        "initialized_by": ("_init", "_create", "_start"),
        "destroyed_by": ("_cancel", "_destroy", "_close", "_cleanup", "_deinit", "_free"),
        "freed_by": ("_cancel", "_free", "_destroy", "_cleanup", "_close"),
        "related_functions": (),
    }
    suffixes = suffixes_by_action.get(action, ())
    base = _safe_id(str(type_name).removeprefix("struct ").removesuffix("_t"))
    base_tail = base.removeprefix("mqtt_")
    if action == "related_functions":
        for function in functions:
            name = str(function.get("name", ""))
            if base and base in _safe_id(name):
                return name
        return ""
    for suffix in suffixes:
        candidates = [
            function
            for function in functions
            if str(function.get("name", "")).endswith(suffix) and str(function.get("function_kind", "")) != "handler"
        ]
        preferred = [
            function
            for function in candidates
            if base and (base in _safe_id(str(function.get("name", ""))) or (base_tail and base_tail in _safe_id(str(function.get("name", "")))))
        ]
        for function in [*preferred, *candidates]:
            name = str(function.get("name", ""))
            if name:
                return name
    return ""


def _add_unresolved_type_function_ref(result: dict[str, Any], type_item: dict[str, Any], action: str, ref: str) -> None:
    question_id = "q:type_function_ref:{module}:{type}:{action}:{ref}".format(
        module=_safe_id(str(type_item.get("module_id", ""))),
        type=_safe_id(str(type_item.get("type_id", type_item.get("name", "")))),
        action=_safe_id(action),
        ref=_safe_id(ref),
    )
    existing = {
        str(item.get("question_id", ""))
        for item in result.get("unresolved_questions", [])
        if isinstance(item, dict)
    }
    if question_id in existing:
        return
    result.setdefault("unresolved_questions", []).append(
        {
            "question_id": question_id,
            "target_kind": "function",
            "target_id": ref,
            "question": f"Resolve function reference '{ref}' for type '{type_item.get('type_id', type_item.get('name', ''))}'.",
            "unresolved_reason": "The type lifecycle or related function reference did not match any concrete same-module function inventory entry after 5.4a reconciliation.",
            "blocking": False,
            "trace_ref_keys": [str(type_item.get("type_id", ""))],
        }
    )


def reconcile_type_inventory_function_refs(draft: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["unresolved_questions"] = [
        item
        for item in result.get("unresolved_questions", [])
        if not (isinstance(item, dict) and str(item.get("question_id", "")).startswith("q:type_function_ref:"))
    ]
    functions_by_module: dict[str, list[dict[str, Any]]] = {}
    for function in result.get("function_contracts", []):
        if isinstance(function, dict):
            functions_by_module.setdefault(str(function.get("module_id", "")), []).append(function)
    for type_item in result.get("type_inventory", []):
        if not isinstance(type_item, dict):
            continue
        module_functions = functions_by_module.get(str(type_item.get("module_id", "")), [])
        module_function_names = {
            str(function.get("name", ""))
            for function in module_functions
            if str(function.get("name", "")).strip()
        }
        lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
        for action in ("created_by", "initialized_by", "destroyed_by", "freed_by"):
            repaired: list[str] = []
            replacement = ""
            for name in lifecycle.get(action, []):
                name = str(name)
                if name in module_function_names:
                    repaired.append(name)
                    continue
                replacement = replacement or _replacement_function_name(module_functions, action, str(type_item.get("name", "")))
                if not replacement:
                    _add_unresolved_type_function_ref(result, type_item, action, name)
            if replacement and replacement not in repaired:
                repaired.append(replacement)
            lifecycle[action] = repaired
        if isinstance(type_item.get("lifecycle"), dict):
            type_item["lifecycle"] = lifecycle
        related: list[str] = []
        replacement = ""
        for name in type_item.get("related_functions", []):
            name = str(name)
            if name in module_function_names:
                related.append(name)
                continue
            related_action = "related_functions"
            if name.endswith(("_cleanup", "_destroy", "_free", "_close", "_deinit")):
                related_action = "destroyed_by"
            elif name.endswith(("_init", "_create", "_open", "_start")):
                related_action = "initialized_by"
            replacement = replacement or _replacement_function_name(module_functions, related_action, str(type_item.get("name", ""))) or _replacement_function_name(module_functions, "related_functions", str(type_item.get("name", "")))
            if not replacement:
                _add_unresolved_type_function_ref(result, type_item, "related_functions", name)
        if replacement and replacement not in related:
            related.append(replacement)
        type_item["related_functions"] = related
    return result


def _collect_plan_strings(value: Any, *, skip_keys: set[str] | None = None) -> set[str]:
    skip_keys = skip_keys or set()
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in skip_keys:
                continue
            result.update(_collect_plan_strings(child, skip_keys=skip_keys))
    elif isinstance(value, list):
        for child in value:
            result.update(_collect_plan_strings(child, skip_keys=skip_keys))
    elif isinstance(value, str) and value.strip():
        result.add(value.strip())
        result.add(value.replace("const", "").replace("*", "").strip())
    return result


def _type_aliases(type_item: dict[str, Any]) -> set[str]:
    name = str(type_item.get("name", "")).strip()
    base = name.removeprefix("struct ").removesuffix("_t")
    return {value for value in {str(type_item.get("type_id", "")).strip(), name, name.removeprefix("struct "), base, _safe_id(base)} if value}


def _type_index_by_question_target(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for type_item in types:
        if not isinstance(type_item, dict):
            continue
        for alias in _type_aliases(type_item):
            result.setdefault(alias, type_item)
        type_id = str(type_item.get("type_id", "")).strip()
        module_id = str(type_item.get("module_id", "")).strip()
        base = _safe_id(str(type_item.get("name", "")).removeprefix("struct ").removesuffix("_t"))
        if module_id and base:
            result.setdefault(f"slot:type:{module_id}:derived:{base}", type_item)
            result.setdefault(f"slot:type:{module_id}:mandatory:{base}", type_item)
        if type_id:
            result.setdefault(type_id, type_item)
    return result


def _type_is_referenced(type_item: dict[str, Any], refs: set[str]) -> bool:
    aliases = _type_aliases(type_item)
    return any(alias in refs for alias in aliases)


def _question_resolved_by_functions(question: dict[str, Any], type_item: dict[str, Any], function_names: set[str]) -> bool:
    text = f"{question.get('question', '')} {question.get('unresolved_reason', '')}".lower()
    if not any(word in text for word in ("free", "destroy", "cleanup", "create", "initialize", "init", "lifecycle")):
        return False
    base = _safe_id(str(type_item.get("name", "")).removeprefix("struct ").removesuffix("_t"))
    base_tail = base.removeprefix("mqtt_")
    suffixes = ("_free", "_destroy", "_cleanup", "_deinit", "_close", "_create", "_init", "_open")
    for name in function_names:
        key = _safe_id(name)
        if not key.endswith(suffixes):
            continue
        if base and base in key:
            return True
        if base_tail and base_tail in key:
            return True
    return False


def cleanup_final_unresolved_questions(plan: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(plan)
    types = [item for item in result.get("type_inventory", []) if isinstance(item, dict)]
    refs = _collect_plan_strings(
        {
            "canonical_types": result.get("canonical_types", []),
            "function_contracts": result.get("function_contracts", []),
            "file_layout": result.get("file_layout", {}),
            "wire_mapping_table": result.get("wire_mapping_table", []),
            "access_path_table": result.get("access_path_table", []),
            "dependency_graph": result.get("dependency_graph", {}),
        }
    )
    type_by_target = _type_index_by_question_target(types)
    function_names_by_module: dict[str, set[str]] = {}
    for function in result.get("function_contracts", []):
        if isinstance(function, dict):
            function_names_by_module.setdefault(str(function.get("module_id", "")), set()).add(str(function.get("name", "")))

    kept_questions: list[Any] = []
    stale_private_type_ids: set[str] = set()
    for question in result.get("unresolved_questions", []):
        if not isinstance(question, dict):
            kept_questions.append(question)
            continue
        target = str(question.get("target_id", "")).strip()
        type_item = type_by_target.get(target) or type_by_target.get(_safe_id(target.removeprefix("struct ").removesuffix("_t")))
        if type_item is None:
            kept_questions.append(question)
            continue
        module_functions = function_names_by_module.get(str(type_item.get("module_id", "")), set())
        if _question_resolved_by_functions(question, type_item, module_functions):
            continue
        text = f"{question.get('question', '')} {question.get('unresolved_reason', '')} {' '.join(str(ref) for ref in question.get('trace_ref_keys', []))}".lower()
        private_empty = (
            str(type_item.get("kind", "")) == "internal_state"
            and str(type_item.get("visibility", "")) in {"private", "module_internal"}
            and not [field for field in type_item.get("fields", []) if isinstance(field, dict)]
            and not [dep for dep in type_item.get("dependencies", []) if str(dep).strip()]
            and not [ref for ref in type_item.get("related_functions", []) if str(ref).strip()]
            and not _type_is_referenced(type_item, refs)
        )
        if private_empty and any(word in text for word in ("private_state", "private state", "internal state", "lifecycle")):
            stale_private_type_ids.add(str(type_item.get("type_id", "")))
            continue
        kept_questions.append(question)
    result["unresolved_questions"] = kept_questions
    if stale_private_type_ids:
        result["type_inventory"] = [
            item
            for item in result.get("type_inventory", [])
            if not (isinstance(item, dict) and str(item.get("type_id", "")) in stale_private_type_ids)
        ]
    return result


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
                "call_contracts": [],
                "side_effects": [],
                "signature_dependencies": [],
                "interface_type_declarations": [],
                "resource_access": [],
                "internal_type_refs": [],
                "service_requirements": [],
                "forbidden_symbols": [],
                "logic_kind": "",
                "behavior_contract": {},
                "traceability": {"source_fact_ids": function.get("trace_ref_keys", []), "decision_ids": function.get("trace_ref_keys", [])},
                **function,
            }
            item.pop("trace_ref_keys", None)
            item.pop("status", None)
            result.setdefault("function_contracts", []).append(item)
            existing.add(str(function.get("function_id")))
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4a_function_inventory")
    return result


def _module_functions(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source = functions if functions is not None else draft.get("function_contracts", [])
    return [item for item in source if isinstance(item, dict) and str(item.get("module_id")) == module_id]


def _function_is_public(function: dict[str, Any]) -> bool:
    return bool(function.get("exported")) or str(function.get("api_surface", "")).lower() == "public" or str(function.get("visibility", "")).lower() == "public"


def _source_local_function(function: dict[str, Any]) -> bool:
    return not _function_is_public(function)


def _type_is_public(type_item: dict[str, Any]) -> bool:
    return str(type_item.get("visibility", "")) == "public" and str(type_item.get("defined_in", "")) == "public_header"


def _known_type_refs(draft: dict[str, Any]) -> set[str]:
    return {
        str(item.get("type_id", ""))
        for key in ("canonical_types", "type_inventory")
        for item in draft.get(key, [])
        if isinstance(item, dict)
    } | set(SYSTEM_TYPE_IDS)


def _normalize_type_ref(value: Any, c_type: Any, known: set[str]) -> str:
    ref = str(value or "").strip()
    spelling = str(c_type or "").strip().removeprefix("const ").rstrip("*").strip()
    if ref.startswith(("state:", "message:", "field:", "file:", "func:", "module:")):
        return ""
    if ref in known:
        return ref
    if spelling in known:
        return spelling
    return ""


def _service_requirement_ids(functions: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for function in functions:
        if not isinstance(function, dict):
            continue
        for requirement in function.get("service_requirements", []):
            if (
                isinstance(requirement, dict)
                and str(requirement.get("requirement_kind", "cross_module_service")) == "cross_module_service"
                and str(requirement.get("service_requirement_id", "")).strip()
            ):
                result.append(str(requirement["service_requirement_id"]))
    return sorted(set(result))


def _unique_function_name(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _preferred_lifecycle_repair_name(type_item: dict[str, Any], old_name: str) -> str:
    lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
    type_base = _safe_id(str(type_item.get("name", "")).removeprefix("struct ").removesuffix("_t"))
    if not type_base:
        return ""
    if old_name in [str(item) for item in lifecycle.get("created_by", [])]:
        return f"{type_base}_create"
    if old_name in [str(item) for item in lifecycle.get("initialized_by", [])]:
        return f"{type_base}_init"
    if old_name in [str(item) for item in lifecycle.get("freed_by", [])]:
        return f"{type_base}_free"
    if old_name in [str(item) for item in lifecycle.get("destroyed_by", [])]:
        return f"{type_base}_destroy"
    if old_name in [str(item) for item in type_item.get("related_functions", [])]:
        return f"{type_base}_helper"
    return ""


def _fallback_repair_name(protocol: str, module_id: str, old_name: str) -> str:
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    safe_old = _safe_id(old_name)
    if safe_old.startswith(f"{prefix}_"):
        return safe_old
    protocol_prefix = f"{_safe_id(protocol)}_"
    if safe_old.startswith(protocol_prefix):
        return f"{prefix}_{safe_old[len(protocol_prefix):]}"
    return f"{prefix}_{safe_old}"


def _replace_type_function_refs(type_item: dict[str, Any], old_name: str, new_name: str) -> bool:
    changed = False
    lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
    for action in ("created_by", "initialized_by", "destroyed_by", "freed_by"):
        values = lifecycle.get(action, [])
        if not isinstance(values, list):
            continue
        repaired = [new_name if str(item) == old_name else item for item in values]
        if repaired != values:
            lifecycle[action] = repaired
            changed = True
    if isinstance(type_item.get("lifecycle"), dict):
        type_item["lifecycle"] = lifecycle
    related = type_item.get("related_functions", [])
    if isinstance(related, list):
        repaired = [new_name if str(item) == old_name else item for item in related]
        if repaired != related:
            type_item["related_functions"] = repaired
            changed = True
    return changed


def repair_function_inventory_symbols(draft: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result_draft = deepcopy(draft)
    result_candidate = deepcopy(candidate)
    candidate_functions = [item for item in result_candidate.get("functions", []) if isinstance(item, dict)]
    draft_functions_by_id = {
        str(item.get("function_id", "")): item
        for item in result_draft.get("function_contracts", [])
        if isinstance(item, dict)
    }
    used_names = {str(item.get("name", "")) for item in candidate_functions if str(item.get("name", "")).strip()}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for function in candidate_functions:
        name = str(function.get("name", "")).strip()
        if name:
            by_name.setdefault(name, []).append(function)
    report = {
        "schema_version": "function_symbol_repair_report/v1",
        "stage": "5.4a.1_function_symbol_repair",
        "renamed_functions": [],
        "preserved_public_symbols": [],
        "unrepaired_duplicates": [],
        "reason": "deterministic repair for globally duplicated C-facing function names before signature planning",
    }
    protocol = str(result_draft.get("protocol_name", "protocol"))
    type_inventory = [item for item in result_draft.get("type_inventory", []) if isinstance(item, dict)]
    for name, duplicates in sorted(by_name.items()):
        if len(duplicates) < 2:
            continue
        public = [item for item in duplicates if _function_is_public(item)]
        preserve = public[0] if public else duplicates[0]
        report["preserved_public_symbols"].append(
            {
                "function_id": preserve.get("function_id", ""),
                "name": name,
                "reason": "public/exported API kept" if _function_is_public(preserve) else "first internal symbol kept",
            }
        )
        for function in duplicates:
            if function is preserve:
                continue
            if _function_is_public(function):
                report["unrepaired_duplicates"].append(
                    {
                        "function_id": function.get("function_id", ""),
                        "name": name,
                        "reason": "duplicate public/exported API symbol was not renamed deterministically",
                    }
                )
                continue
            module_id = str(function.get("module_id", ""))
            preferred = ""
            for type_item in type_inventory:
                if str(type_item.get("module_id", "")) != module_id:
                    continue
                preferred = preferred or _preferred_lifecycle_repair_name(type_item, name)
            preferred = preferred or _fallback_repair_name(protocol, module_id, name)
            new_name = _unique_function_name(preferred, used_names)
            function["name"] = new_name
            draft_function = draft_functions_by_id.get(str(function.get("function_id", "")))
            if draft_function is not None:
                draft_function["name"] = new_name
                signature = draft_function.get("signature", {})
                if isinstance(signature, dict) and signature.get("name") == name:
                    signature["name"] = new_name
                    raw = str(signature.get("raw", ""))
                    if raw:
                        signature["raw"] = raw.replace(name, new_name, 1)
            touched_types = []
            for type_item in type_inventory:
                if str(type_item.get("module_id", "")) == module_id and _replace_type_function_refs(type_item, name, new_name):
                    touched_types.append(str(type_item.get("type_id", "")))
            report["renamed_functions"].append(
                {
                    "function_id": function.get("function_id", ""),
                    "old_name": name,
                    "new_name": new_name,
                    "module_id": module_id,
                    "updated_type_refs": touched_types,
                    "reason": "non-public duplicate renamed with owner/type-derived symbol",
                }
            )
    return result_draft, result_candidate, report


def fallback_function_signatures(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]] | None = None, *, batch_index: int = 0, batch_size: int = 0) -> dict[str, Any]:
    updates = []
    for function in _module_functions(draft, module_id, functions):
        signature, params, _return_type = _default_signature(function, module_id, str(draft.get("protocol_name", "protocol")))
        storage_class = "static" if _source_local_function(function) else "none"
        raw = signature["raw"]
        if storage_class == "static" and not raw.startswith("static "):
            raw = f"static {raw}"
        updates.append(
            {
                "function_id": function.get("function_id"),
                "signature": {
                    "raw": raw,
                    "name": signature["name"],
                    "storage_class": storage_class,
                    "return_type": signature["return_type"],
                    "params": [
                        {
                            "name": param["name"],
                            "type": param["type"],
                            "type_ref": "",
                            "direction": "in",
                            "nullable": bool(param.get("nullable", False)),
                            "ownership": normalize_param_ownership_for_coder(param.get("ownership")),
                            "passing_mode": "by_pointer" if "*" in str(param.get("type", "")) else "by_value",
                        }
                        for param in params
                    ],
                },
                "signature_dependencies": [],
                "interface_type_declarations": [],
                "trace_ref_keys": function.get("traceability", {}).get("decision_ids", []),
                "status": "inferred",
            }
        )
    return {
        "schema_version": "function_signature_patch/v1",
        "patch_id": f"patch:function_signatures:{module_id}:{batch_index}",
        "producer": _producer("5.4b_function_signatures", "function_signature_patch_prompt"),
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size or len(updates)},
        "function_signature_updates": updates,
        "assumptions": [],
        "unresolved_questions": [],
    }


def _valid_signature_param(param: dict[str, Any], known_type_refs: set[str]) -> dict[str, Any]:
    c_type = str(param.get("type", "")).strip()
    ownership = normalize_param_ownership_for_coder(param.get("ownership"))
    passing_mode = str(param.get("passing_mode", "")).strip() or ("by_pointer" if "*" in c_type else "by_value")
    return {
        "name": str(param.get("name", "")).strip(),
        "type": c_type,
        "type_ref": _normalize_type_ref(param.get("type_ref", ""), c_type, known_type_refs),
        "direction": str(param.get("direction", "")).strip() or "in",
        "nullable": bool(param.get("nullable", False)),
        "ownership": ownership,
        "passing_mode": passing_mode,
    }


def _raw_with_storage(raw: str, name: str, fallback_raw: str, storage_class: str) -> str:
    raw = raw.strip().rstrip(";")
    if not raw or name not in raw:
        raw = fallback_raw.strip().rstrip(";")
    if storage_class == "static":
        raw = raw.removeprefix("static ").strip()
        return f"static {raw}" if raw else raw
    return raw.removeprefix("static ").strip()


def _type_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("type_id", "")): item
        for key in ("canonical_types", "type_inventory")
        for item in draft.get(key, [])
        if isinstance(item, dict) and str(item.get("type_id", "")).strip()
    }


def _normalize_signature_dependencies(dependencies: Any, draft: dict[str, Any], module_id: str, is_public: bool, known_type_refs: set[str], stats: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(dependencies, list):
        return []
    module_ids = {str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)}
    types = _type_by_id(draft)
    result = []
    for raw_dep in dependencies:
        if not isinstance(raw_dep, dict):
            continue
        symbol_name = str(raw_dep.get("symbol_name", "")).strip()
        type_ref = _normalize_type_ref(raw_dep.get("type_ref", ""), symbol_name, known_type_refs)
        symbol_kind = str(raw_dep.get("symbol_kind", "")).strip()
        if symbol_kind not in {"type", "opaque_handle", "callback_type", "system_type"}:
            symbol_kind = "system_type" if type_ref in SYSTEM_TYPE_IDS or symbol_name in SYSTEM_TYPE_IDS else "type"
            stats["signature_dependency_symbol_kind_normalized"] += 1
        owner = str(raw_dep.get("owner_module_id", "")).strip()
        if symbol_kind == "system_type":
            if owner:
                stats["signature_dependency_owner_normalized"] += 1
            owner = ""
        elif owner not in module_ids:
            owner = str(types.get(type_ref, {}).get("module_id") or module_id)
            stats["signature_dependency_owner_normalized"] += 1
        scope = str(raw_dep.get("dependency_scope", "")).strip()
        if scope not in {"header", "source"}:
            scope = "source"
            stats["signature_dependency_scope_normalized"] += 1
        if not is_public and type_ref in types and not _type_is_public(types[type_ref]) and scope == "header":
            scope = "source"
            stats["private_header_dependency_lowered"] += 1
        result.append(
            {
                "symbol_name": symbol_name,
                "symbol_kind": symbol_kind,
                "type_ref": type_ref,
                "owner_module_id": owner,
                "dependency_scope": scope,
                "reason": str(raw_dep.get("reason", "")).strip(),
            }
        )
    return result


def normalize_function_signature_patch(
    candidate: dict[str, Any],
    draft: dict[str, Any],
    module_id: str,
    functions: list[dict[str, Any]],
    *,
    batch_index: int = 0,
    batch_size: int = 0,
) -> tuple[dict[str, Any], dict[str, int]]:
    stats = {
        "missing_signature_updates_filled": 0,
        "out_of_batch_signature_updates_dropped": 0,
        "signature_storage_normalized": 0,
        "signature_param_ownership_normalized": 0,
        "signature_dependency_scope_normalized": 0,
        "signature_dependency_symbol_kind_normalized": 0,
        "signature_dependency_owner_normalized": 0,
        "private_header_dependency_lowered": 0,
    }
    fallback = fallback_function_signatures(draft, module_id, functions, batch_index=batch_index, batch_size=batch_size)
    expected_ids = [str(item.get("function_id", "")) for item in functions if isinstance(item, dict)]
    functions_by_id = {str(item.get("function_id", "")): item for item in functions if isinstance(item, dict)}
    fallback_updates = {str(item.get("function_id", "")): item for item in fallback.get("function_signature_updates", []) if isinstance(item, dict)}
    candidate_updates = {
        str(item.get("function_id", "")): item
        for item in candidate.get("function_signature_updates", [])
        if isinstance(item, dict) and str(item.get("function_id", "")) in functions_by_id
    }
    stats["out_of_batch_signature_updates_dropped"] = max(0, len(candidate.get("function_signature_updates", []) if isinstance(candidate.get("function_signature_updates"), list) else []) - len(candidate_updates))
    known_type_refs = _known_type_refs(draft)
    updates = []
    for function_id in expected_ids:
        base = deepcopy(fallback_updates[function_id])
        incoming = candidate_updates.get(function_id)
        if incoming is None:
            stats["missing_signature_updates_filled"] += 1
            updates.append(base)
            continue
        function = functions_by_id[function_id]
        is_public = _function_is_public(function)
        base_signature = base["signature"]
        incoming_signature = incoming.get("signature", {}) if isinstance(incoming.get("signature"), dict) else {}
        merged_signature = deepcopy(base_signature)
        merged_signature.update({key: value for key, value in incoming_signature.items() if key in {"raw", "return_type", "params"}})
        merged_signature["name"] = str(function.get("name", ""))
        storage_class = "none" if is_public else "static"
        if merged_signature.get("storage_class") != storage_class or (storage_class == "static" and not str(merged_signature.get("raw", "")).strip().startswith("static ")):
            stats["signature_storage_normalized"] += 1
        merged_signature["storage_class"] = storage_class
        merged_signature["raw"] = _raw_with_storage(str(merged_signature.get("raw", "")), merged_signature["name"], str(base_signature.get("raw", "")), storage_class)
        incoming_params = incoming_signature.get("params", [])
        if not isinstance(incoming_params, list):
            incoming_params = base_signature.get("params", [])
        params = []
        for param in incoming_params:
            if not isinstance(param, dict):
                continue
            before = str(param.get("ownership", ""))
            normalized = _valid_signature_param(param, known_type_refs)
            if before and before != normalized["ownership"] and before not in {"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"}:
                stats["signature_param_ownership_normalized"] += 1
            params.append(normalized)
        merged_signature["params"] = params or base_signature.get("params", [])
        update = {
            "function_id": function_id,
            "signature": merged_signature,
            "signature_dependencies": _normalize_signature_dependencies(incoming.get("signature_dependencies", []), draft, module_id, is_public, known_type_refs, stats),
            "interface_type_declarations": incoming.get("interface_type_declarations", []) if isinstance(incoming.get("interface_type_declarations"), list) else [],
            "trace_ref_keys": incoming.get("trace_ref_keys", base.get("trace_ref_keys", [])) if isinstance(incoming.get("trace_ref_keys", []), list) else base.get("trace_ref_keys", []),
            "status": str(incoming.get("status", base.get("status", "inferred"))) or "inferred",
        }
        updates.append(update)
    result = deepcopy(fallback)
    result.update(
        {
            "patch_id": str(candidate.get("patch_id", fallback.get("patch_id", ""))) or fallback.get("patch_id", ""),
            "producer": candidate.get("producer", fallback.get("producer", {})) if isinstance(candidate.get("producer"), dict) else fallback.get("producer", {}),
            "module_id": module_id,
            "batch": {"index": batch_index, "size": batch_size or len(updates)},
            "function_signature_updates": updates,
            "assumptions": candidate.get("assumptions", []) if isinstance(candidate.get("assumptions"), list) else [],
            "unresolved_questions": candidate.get("unresolved_questions", []) if isinstance(candidate.get("unresolved_questions"), list) else [],
        }
    )
    return result, stats


def merge_function_signatures(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    known_type_refs = _known_type_refs(result)
    updates = {str(item.get("function_id")): item for item in patch.get("function_signature_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if not update:
            continue
        signature = deepcopy(update["signature"])
        signature["return_type"] = signature.get("return_type", "int")
        for param in signature.get("params", []):
            param["type_ref"] = _normalize_type_ref(param.get("type_ref", ""), param.get("type", ""), known_type_refs)
            param["ownership"] = normalize_param_ownership_for_coder(param.get("ownership"))
            param["passing_mode"] = param.get("passing_mode") or ("by_pointer" if "*" in str(param.get("type", "")) else "by_value")
        function["signature"] = signature
        dependencies = deepcopy(update.get("signature_dependencies", []))
        for dep in dependencies:
            dep["type_ref"] = _normalize_type_ref(dep.get("type_ref", ""), dep.get("symbol_name", ""), known_type_refs)
        function["signature_dependencies"] = dependencies
        function["interface_type_declarations"] = update.get("interface_type_declarations", [])
        params = [
            {
                "type": param.get("type", ""),
                "name": param.get("name", ""),
                "nullable": param.get("nullable", False),
                "ownership": normalize_param_ownership_for_coder(param.get("ownership")),
                "passing_mode": param.get("passing_mode", "unknown"),
                "direction": param.get("direction", "in"),
                "type_ref": param.get("type_ref", ""),
            }
            for param in signature.get("params", [])
        ]
        function["input_contract"] = {"params": params}
        function["output_contract"] = {"return_type": signature.get("return_type", "")}
    result.setdefault("unresolved_questions", []).extend(_nonblocking_questions(patch.get("unresolved_questions", [])))
    result.setdefault("accepted_stage_artifacts", []).append("5.4b_function_signatures")
    return result


def fallback_function_behavior(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]] | None = None, *, batch_index: int = 0, batch_size: int = 0) -> dict[str, Any]:
    updates = []
    module_error_ids = [
        str(error.get("error_id"))
        for error in draft.get("error_strategy", [])
        if isinstance(error, dict) and str(error.get("owner_module_id", "")) == module_id
    ] or [str(error.get("error_id")) for error in draft.get("error_strategy", []) if isinstance(error, dict) and error.get("error_id")]
    for function in _module_functions(draft, module_id, functions):
        updates.append(
            {
                "function_id": function.get("function_id"),
                "contract": {
                    "input": "Inputs are the C signature parameters.",
                    "action": str(function.get("purpose", "")),
                    "output": "Result is reflected by return value and documented side effects.",
                    "preconditions": [],
                    "postconditions": [],
                    "invariants_used": [],
                    "idempotent": False,
                    "thread_safety": "single_thread_only",
                },
                "event_contract": {
                    "trigger": "",
                    "precondition": "",
                    "input": "",
                    "action": "",
                    "state_change": "",
                    "response": "",
                    "event_type": "",
                },
                "error_behavior": _error_behavior(module_error_ids),
                "state_access": [
                    {"state_id": state.get("state_id"), "access_kind": "read_write", "required": True, "reason": "Function belongs to the state owner module."}
                    for state in draft.get("state_design", [])
                    if isinstance(state, dict) and state.get("owner_module_id") == module_id
                ],
                "resource_access": [],
                "internal_type_refs": [],
                "service_requirements": [],
                "logic_kind": "LOGIC",
                "forbidden_symbols": [],
                "trace_ref_keys": function.get("traceability", {}).get("decision_ids", []),
                "status": "inferred",
            }
        )
    return {
        "schema_version": "function_behavior_contract_patch/v1",
        "patch_id": f"patch:function_behavior:{module_id}:{batch_index}",
        "producer": _producer("5.4c_behavior_contract", "function_behavior_contract_patch_prompt"),
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size or len(updates)},
        "function_behavior_updates": updates,
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_function_behavior(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    updates = {str(item.get("function_id")): item for item in patch.get("function_behavior_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if not update:
            continue
        contract = update["contract"]
        function["behavior_contract"] = contract
        function["event_contract"] = update.get("event_contract", {})
        function["state_access"] = update["state_access"]
        function["resource_access"] = update["resource_access"]
        function["internal_type_refs"] = update["internal_type_refs"]
        function["service_requirements"] = update["service_requirements"]
        function["error_behavior"] = _error_behavior_text(update["error_behavior"])
        function["preconditions"] = contract["preconditions"]
        function["postconditions"] = contract["postconditions"]
        event_contract = function.get("event_contract", {})
        event_complete = isinstance(event_contract, dict) and all(str(event_contract.get(key, "")).strip() for key in ("trigger", "precondition", "input", "action", "state_change", "response", "event_type"))
        function["logic_kind"] = "EVENT" if update["logic_kind"] == "EVENT" and event_complete else "LOGIC"
        if function["logic_kind"] == "EVENT":
            function["coder_function_type"] = "EVENT"
        function["forbidden_symbols"] = update["forbidden_symbols"]
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4c_behavior_contract")
    return result


def fallback_wire_access_binding(draft: dict[str, Any], planning_ir: dict[str, Any]) -> dict[str, Any]:
    wire_fields = _wire_fields(planning_ir)
    functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    parser_functions = [item for item in functions if item.get("function_kind") == "parser"]
    serializer_functions = [item for item in functions if item.get("function_kind") == "serializer"]
    entries: list[dict[str, Any]] = []
    access: list[dict[str, Any]] = []
    binding_by_function: dict[str, dict[str, Any]] = {}

    def c_type_for_field(item: dict[str, Any]) -> str:
        raw = f"{item.get('field_type', '')} {item.get('field', '')}".lower()
        if any(token in raw for token in ("payload", "binary", "bytes", "octet")):
            return "uint8_t*"
        if any(token in raw for token in ("string", "utf", "text", "topic", "name")):
            return "char*"
        if any(token in raw for token in ("length", "port", "uint16", "short")):
            return "uint16_t"
        if any(token in raw for token in ("uint32", "int32", "long")):
            return "uint32_t"
        if any(token in raw for token in ("bool", "flag", "type", "qos", "byte", "uint8")):
            return "uint8_t"
        return "uint8_t"

    def select_codec(candidates: list[dict[str, Any]], item: dict[str, Any]) -> str:
        if not candidates:
            return ""
        message_id = f"message:{_safe_id(item['message'])}"
        field_id = str(item.get("field_id", ""))
        message_slug = _safe_id(str(item.get("message", "")))
        ranked: list[tuple[int, str]] = []
        for index, function in enumerate(candidates):
            text = " ".join(
                [
                    str(function.get("function_id", "")),
                    str(function.get("name", "")),
                    str(function.get("purpose", "")),
                ]
            ).lower()
            score = 0
            if field_id in function.get("covers_field_ids", []):
                score += 30
            if message_id in function.get("covers_message_ids", []):
                score += 20
            if message_slug and message_slug in text:
                score += 10
            ranked.append((score - index, str(function.get("function_id", ""))))
        ranked.sort(reverse=True)
        return ranked[0][1]

    for item in wire_fields:
        message_id = f"message:{_safe_id(item['message'])}"
        parser_id = select_codec(parser_functions, item)
        serializer_id = select_codec(serializer_functions, item)
        for direction, function_id in (("parse", parser_id), ("serialize", serializer_id)):
            if not function_id:
                continue
            wire_mapping_id = f"wire:{direction}:{_safe_id(item['message'])}:{_safe_id(item['field'])}"
            access_path_id = f"{item['access_path_id']}:{direction}:{_safe_id(function_id)}"
            entries.append(
                {
                    "wire_mapping_id": wire_mapping_id,
                    "function_id": function_id,
                    "message_id": message_id,
                    "field_id": item["field_id"],
                    "direction": direction,
                    "packet_name": str(item["message"]),
                    "wire_field": str(item["field"]),
                    "strategy": "store_in_field",
                    "target_path": str(item["access_path"]),
                    "source_expr": "",
                    "rule": "deterministic wire/access fallback",
                    "mapping_role": "codec_field_binding",
                    "required": True,
                    "trace_ref_keys": item["source_fact_ids"],
                    "status": "inferred",
                }
            )
            binding_by_function.setdefault(function_id, {"function_id": function_id, "wire_mapping_ids": [], "access_path_ids": []})["wire_mapping_ids"].append(wire_mapping_id)
            if function_id:
                access.append(
                    {
                        "access_path_id": access_path_id,
                        "function_id": function_id,
                        "field_id": item["field_id"],
                        "path": item["access_path"],
                        "c_type": c_type_for_field(item),
                        "access_kind": "read",
                        "role": f"{direction} access path for {item['message']}.{item['field']}.",
                        "validity_condition": "",
                        "trace_ref_keys": item["source_fact_ids"],
                        "status": "inferred",
                    }
                )
                binding_by_function.setdefault(function_id, {"function_id": function_id, "wire_mapping_ids": [], "access_path_ids": []})["access_path_ids"].append(access_path_id)
    return {
        "schema_version": "wire_access_binding_patch/v2",
        "patch_id": "patch:wire_access_binding:deterministic",
        "producer": _producer("5.4d_wire_access_binding", "wire_access_binding_patch_prompt"),
        "wire_mapping_entries": entries,
        "access_path_entries": access,
        "function_binding_updates": list(binding_by_function.values()),
        "forbidden_symbols": [],
        "assumptions": [],
        "unresolved_questions": [],
    }


def _wire_entry_value(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key, "")
    return str(value).strip() if value is not None else ""


def _parse_wire_forbidden_symbol(value: Any) -> tuple[str, dict[str, str]] | None:
    text = str(value).strip()
    parts = text.split("|", 3)
    if len(parts) != 4 or not all(part.strip() for part in parts[:3]):
        return None
    function_id, kind, name, reason = (part.strip() for part in parts)
    return function_id, {"NAME": name, "KIND": kind, "REASON": reason or "Forbidden by wire/access binding."}


def _append_unique_forbidden_symbol(function: dict[str, Any], symbol: dict[str, str]) -> None:
    key = (symbol.get("NAME", ""), symbol.get("KIND", ""), symbol.get("REASON", ""))
    existing = {
        (
            str(item.get("NAME") or item.get("name") or ""),
            str(item.get("KIND") or item.get("kind") or ""),
            str(item.get("REASON") or item.get("reason") or ""),
        )
        for item in function.get("forbidden_symbols", [])
        if isinstance(item, dict)
    }
    if key not in existing:
        function.setdefault("forbidden_symbols", []).append(symbol)


def _unresolved_wire_forbidden_symbol(result: dict[str, Any], index: int, raw_value: Any, function_id: str = "") -> None:
    result.setdefault("unresolved_questions", []).append(
        {
            "question_id": f"q:wire_forbidden_symbol:{index}",
            "target_kind": "function",
            "target_id": function_id,
            "question": f"Unable to attach forbidden symbol '{raw_value}' from wire_access_binding_patch to a known function.",
            "unresolved_reason": "Expected '<function_id>|<KIND>|<NAME>|<REASON>' with an existing function_id.",
            "blocking": False,
            "trace_ref_keys": [],
        }
    )


def _canonical_wire_target_path(entry: dict[str, Any], access_entry: dict[str, Any] | None) -> str:
    target_path = _wire_entry_value(entry, "target_path")
    if target_path == "buffer" and str(entry.get("direction", "")) == "serialize":
        return target_path
    access_path = str((access_entry or {}).get("path", "")).strip()
    return access_path or target_path


def merge_wire_access_binding(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    entries = [item for item in patch.get("wire_mapping_entries", []) if isinstance(item, dict)]
    access_entries = [item for item in patch.get("access_path_entries", []) if isinstance(item, dict)]
    functions_by_id = {str(item.get("function_id", "")): item for item in result.get("function_contracts", []) if isinstance(item, dict)}
    access_by_function_field: dict[tuple[str, str], dict[str, Any]] = {}
    for item in access_entries:
        access_by_function_field[(str(item.get("function_id", "")), str(item.get("field_id", "")))] = item
    by_field: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        field_id = str(entry.get("field_id", ""))
        message = str(entry.get("packet_name") or entry.get("message_id", "")).removeprefix("message:")
        current = by_field.setdefault(
            (str(entry.get("message_id", "")), field_id),
            {
                "mapping_id": f"wire:{field_id}",
                "field_id": field_id,
                "message": message,
                "field": str(entry.get("wire_field") or field_id),
                "parser_function_id": "",
                "serializer_function_id": "",
                "access_path_id": "",
                "strategy": _wire_entry_value(entry, "strategy"),
                "target_path": _wire_entry_value(entry, "target_path"),
                "source_expr": _wire_entry_value(entry, "source_expr"),
                "rule": _wire_entry_value(entry, "rule"),
                "mapping_role": _wire_entry_value(entry, "mapping_role"),
                "source_fact_ids": entry.get("trace_ref_keys", []),
            },
        )
        access_entry = access_by_function_field.get((str(entry.get("function_id", "")), field_id), {})
        access_id = access_entry.get("access_path_id", "")
        current["access_path_id"] = access_id or current["access_path_id"]
        for key in ("strategy", "target_path", "source_expr", "rule", "mapping_role"):
            current[key] = current.get(key) or _wire_entry_value(entry, key)
        canonical_target = _canonical_wire_target_path(entry, access_entry)
        if not current.get("target_path") or (current["target_path"] == "buffer" and canonical_target != "buffer") or (access_entry and canonical_target != "buffer"):
            current["target_path"] = canonical_target
        if entry.get("direction") == "parse":
            current["parser_function_id"] = entry.get("function_id", "")
        elif entry.get("direction") == "serialize":
            current["serializer_function_id"] = entry.get("function_id", "")
    result["wire_mapping_table"] = list(by_field.values())
    result["access_path_table"] = [
            {
                "access_path_id": item["access_path_id"],
                "path": item["path"],
                "field_id": item.get("field_id", ""),
                "owner_module_id": str(functions_by_id.get(str(item.get("function_id", "")), {}).get("module_id", "")),
                "c_type": item.get("c_type", ""),
                "role": item.get("role", ""),
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
                    "message": str(entry_by_id.get(wire_id, {}).get("packet_name") or entry_by_id.get(wire_id, {}).get("message_id", "")).removeprefix("message:"),
                    "field": entry_by_id.get(wire_id, {}).get("wire_field") or entry_by_id.get(wire_id, {}).get("field_id", ""),
                    "direction": entry_by_id.get(wire_id, {}).get("direction", ""),
                    "access_path_id": access_by_function_field.get((str(function.get("function_id", "")), str(entry_by_id.get(wire_id, {}).get("field_id", ""))), {}).get("access_path_id", ""),
                    "strategy": _wire_entry_value(entry_by_id.get(wire_id, {}), "strategy"),
                    "target_path": _canonical_wire_target_path(
                        entry_by_id.get(wire_id, {}),
                        access_by_function_field.get((str(function.get("function_id", "")), str(entry_by_id.get(wire_id, {}).get("field_id", ""))), {}),
                    ),
                    "source_expr": _wire_entry_value(entry_by_id.get(wire_id, {}), "source_expr"),
                    "rule": _wire_entry_value(entry_by_id.get(wire_id, {}), "rule"),
                    "mapping_role": _wire_entry_value(entry_by_id.get(wire_id, {}), "mapping_role"),
                }
                for wire_id in update.get("wire_mapping_ids", [])
            ]
            function["access_paths"] = update.get("access_path_ids", [])
    for index, value in enumerate(patch.get("forbidden_symbols", [])):
        parsed = _parse_wire_forbidden_symbol(value)
        if not parsed:
            _unresolved_wire_forbidden_symbol(result, index, value)
            continue
        function_id, symbol = parsed
        function = functions_by_id.get(function_id)
        if not function:
            _unresolved_wire_forbidden_symbol(result, index, value, function_id)
            continue
        _append_unique_forbidden_symbol(function, symbol)
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4d_wire_access_binding")
    return result


def fallback_calls_allowed(draft: dict[str, Any], functions: list[dict[str, Any]] | None = None, *, batch_index: int = 0, batch_size: int = 0) -> dict[str, Any]:
    functions = functions if functions is not None else [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    by_module_kind: dict[tuple[str, str], list[str]] = {}
    all_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    by_id = {str(item.get("function_id", "")): item for item in all_functions}
    for function in all_functions:
        by_module_kind.setdefault((str(function.get("module_id", "")), str(function.get("function_kind", ""))), []).append(str(function.get("function_id", "")))

    def module_functions(module_id: str) -> list[dict[str, Any]]:
        return [item for item in all_functions if str(item.get("module_id", "")) == module_id]

    def named(module_id: str, *needles: str, helper_only: bool = False) -> list[str]:
        result: list[str] = []
        for item in module_functions(module_id):
            helper_like = (
                str(item.get("function_kind", "")) in {"internal_helper", "helper", "utility", "validator", "resource_lifecycle"}
                or str(item.get("api_surface", "")) in {"private_helper", "static_helper"}
            )
            if helper_only and not helper_like:
                continue
            text = f"{item.get('name', '')} {item.get('purpose', '')}".lower()
            if any(needle in text for needle in needles):
                result.append(str(item.get("function_id", "")))
        return result

    def dedupe(values: list[str], caller_id: str, limit: int = 4) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value != caller_id and value not in result:
                result.append(value)
            if len(result) >= limit:
                break
        return result

    def public_boundary(function: dict[str, Any]) -> bool:
        return (
            bool(function.get("exported"))
            or str(function.get("visibility", "")).lower() == "public"
            or str(function.get("api_surface", "")).lower() == "public"
        )

    def lifecycle_like(function: dict[str, Any]) -> bool:
        text = f"{function.get('name', '')} {function.get('purpose', '')}".lower()
        return str(function.get("function_kind", "")) == "error_helper" or (
            public_boundary(function) and any(token in text for token in ("cleanup", "free", "destroy", "close", "release", "deinit", "shutdown"))
        )

    def non_public(function_id: str) -> bool:
        function = by_id.get(function_id, {})
        return not public_boundary(function)

    def cleanup_named(function_id: str) -> bool:
        name = str(by_id.get(function_id, {}).get("name", "")).lower()
        return any(token in name for token in ("cleanup", "free", "destroy", "close", "release", "deinit", "shutdown"))

    def edge_for(caller_kind: str, callee_id: str) -> dict[str, Any]:
        callee_kind = str(by_id.get(callee_id, {}).get("function_kind", ""))
        call_kind = "utility"
        if caller_kind == "parser":
            call_kind = "parser_delegate"
        elif caller_kind == "serializer":
            call_kind = "serializer_delegate"
        elif caller_kind == "handler":
            call_kind = "serializer_delegate" if callee_kind == "serializer" else "handler_dispatch"
        elif callee_kind in {"resource_lifecycle", "lifecycle"}:
            call_kind = "lifecycle"
        if any(word in str(by_id.get(callee_id, {}).get("name", "")).lower() for word in ("cleanup", "free", "destroy", "close", "release", "deinit")):
            call_kind = "error_handling"
        return {
            "callee_function_id": callee_id,
            "call_kind": call_kind,
            "required": False,
            "service_requirement_ids": [],
            "call_reason": "deterministic conservative internal call contract fallback",
            "param_bindings": [],
            "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
            "failure_behavior": "ignore",
            "trace_ref_keys": [],
            "status": "inferred",
        }

    updates = []
    for function in functions:
        module_id = str(function.get("module_id", ""))
        kind = str(function.get("function_kind", ""))
        caller_id = str(function.get("function_id", ""))
        private_helpers = [
            str(item.get("function_id", ""))
            for item in module_functions(module_id)
            if str(item.get("function_kind", "")) in {"internal_helper", "helper", "utility", "validator"}
            or (
                str(item.get("api_surface", "")) in {"private_helper", "static_helper"}
                and str(item.get("function_kind", "")) not in {"parser", "serializer", "handler", "public_api"}
            )
        ]
        cleanup_helpers = named(module_id, "cleanup", "free", "destroy", "close", "release", "deinit", helper_only=True)
        calls: list[str]
        if kind == "handler":
            calls = dedupe(
                by_module_kind.get((module_id, "serializer"), [])
                + by_module_kind.get((module_id, "state_machine"), [])
                + by_module_kind.get((module_id, "resource_lifecycle"), [])
                + private_helpers
                + cleanup_helpers,
                caller_id,
            )
        elif kind == "public_api":
            calls = dedupe(by_module_kind.get((module_id, "parser"), []) + by_module_kind.get((module_id, "serializer"), []) + by_module_kind.get((module_id, "handler"), []), caller_id)
        elif kind == "parser":
            calls = dedupe(named(module_id, "read", "decode", "parse", "validate", "check", helper_only=True) + private_helpers + cleanup_helpers, caller_id)
        elif kind == "serializer":
            calls = dedupe(named(module_id, "write", "encode", "serialize", "emit", "append", helper_only=True) + private_helpers + cleanup_helpers, caller_id)
        elif lifecycle_like(function):
            calls = dedupe([function_id for function_id in cleanup_helpers + private_helpers if non_public(function_id) and cleanup_named(function_id)], caller_id, limit=3)
        else:
            calls = []
        updates.append(
            {
                "caller_function_id": function.get("function_id"),
                "calls_allowed": [edge_for(kind, call) for call in calls],
            }
        )
    return {
        "schema_version": "calls_allowed_candidate/v2",
        "candidate_id": "candidate:calls_allowed:deterministic",
        "producer": _producer("5.4e_call_contracts", "calls_allowed_candidate_prompt"),
        "call_updates": updates,
        "unresolved_service_requirements": _service_requirement_ids(functions),
        "assumptions": [],
        "unresolved_questions": [],
    }


def _valid_call_kind(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"cleanup", "cleanup_and_return"}:
        return "error_handling"
    if text in {"service_requirement", "parser_delegate", "serializer_delegate", "handler_dispatch", "state_access", "lifecycle", "error_handling", "utility"}:
        return text
    return "utility"


def _valid_return_binding(value: Any, functions_by_id: dict[str, dict[str, Any]], stats: dict[str, int]) -> dict[str, str]:
    binding = value if isinstance(value, dict) else {}
    policy = str(binding.get("policy", "ignore"))
    if policy not in {"ignore", "branch_on_bool", "return_to_caller", "store_then_cleanup", "out_param"}:
        policy = "ignore"
        stats["call_return_binding_normalized"] += 1
    cleanup = str(binding.get("cleanup_function_id", "")).strip()
    if cleanup.lower() == "none" or cleanup not in functions_by_id:
        if cleanup:
            stats["call_cleanup_binding_normalized"] += 1
        cleanup = ""
    return {"policy": policy, "target_ref": str(binding.get("target_ref", "")), "cleanup_function_id": cleanup}


def _valid_failure_behavior(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in {"close_connection", "return_error", "cleanup_and_return", "ignore"} else "ignore"


def _signature_params(function: dict[str, Any]) -> list[dict[str, Any]]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return [
        param
        for param in signature.get("params", [])
        if isinstance(param, dict) and str(param.get("type", "")).strip() and str(param.get("type", "")).strip() != "void"
    ]


def _valid_param_bindings(value: Any, callee: dict[str, Any] | None = None) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "param_name": str(item.get("param_name", "")),
                "value_ref": str(item.get("value_ref", "")),
                "ownership": str(item.get("ownership", "")),
                "nullability": str(item.get("nullability", "")),
            }
        )
    if callee is not None and result:
        params = _signature_params(callee)
        if len(result) != len(params):
            return []
        for binding, param in zip(result, params):
            name = str(binding.get("param_name", "")).strip()
            expected = str(param.get("name", "")).strip()
            if name and expected and name != expected:
                return []
    return result


def normalize_calls_allowed_candidate(
    candidate: dict[str, Any],
    draft: dict[str, Any],
    expected_caller_ids: set[str],
    expected_service_requirement_ids: set[str],
    callable_function_ids: set[str],
    fallback: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    stats = {
        "missing_call_updates_filled": 0,
        "out_of_batch_call_updates_dropped": 0,
        "invalid_call_edges_dropped": 0,
        "call_kind_normalized": 0,
        "call_cleanup_binding_normalized": 0,
        "call_return_binding_normalized": 0,
        "service_requirements_closed": 0,
    }
    functions_by_id = {
        str(item.get("function_id", "")): item
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict) and str(item.get("function_id", "")).strip()
    }
    fallback_updates = {
        str(item.get("caller_function_id", "")): deepcopy(item)
        for item in fallback.get("call_updates", [])
        if isinstance(item, dict)
    }
    incoming_updates = candidate.get("call_updates", []) if isinstance(candidate.get("call_updates"), list) else []
    grouped: dict[str, dict[str, Any]] = {}
    for update in incoming_updates:
        if not isinstance(update, dict):
            continue
        caller = str(update.get("caller_function_id", ""))
        if caller not in expected_caller_ids:
            stats["out_of_batch_call_updates_dropped"] += 1
            continue
        grouped[caller] = update
    updates = []
    resolved_service_ids: set[str] = set()
    for caller in sorted(expected_caller_ids):
        base = fallback_updates.get(caller, {"caller_function_id": caller, "calls_allowed": []})
        incoming = grouped.get(caller)
        if incoming is None:
            stats["missing_call_updates_filled"] += 1
            raw_edges = base.get("calls_allowed", [])
        else:
            base_edges = base.get("calls_allowed", []) if isinstance(base.get("calls_allowed", []), list) else []
            incoming_edges = incoming.get("calls_allowed", []) if isinstance(incoming.get("calls_allowed", []), list) else []
            raw_edges = list(base_edges) + list(incoming_edges)
        caller_fn = functions_by_id.get(caller, {})
        edges = []
        seen_edges: set[tuple[str, tuple[str, ...]]] = set()
        raw_edge_items = raw_edges if isinstance(raw_edges, list) else []
        for raw_edge in raw_edge_items:
            if not isinstance(raw_edge, dict):
                stats["invalid_call_edges_dropped"] += 1
                continue
            callee = str(raw_edge.get("callee_function_id", ""))
            callee_fn = functions_by_id.get(callee)
            if not callee_fn or callee == caller:
                stats["invalid_call_edges_dropped"] += 1
                continue
            cross_module = str(callee_fn.get("module_id", "")) != str(caller_fn.get("module_id", ""))
            private_callee = str(callee_fn.get("visibility", "")).lower() in {"private", "static"} or str(callee_fn.get("api_surface", "")).lower() in {"private_helper", "static_helper"}
            if cross_module and (private_callee or callee not in callable_function_ids):
                stats["invalid_call_edges_dropped"] += 1
                continue
            call_kind = _valid_call_kind(raw_edge.get("call_kind"))
            if call_kind != str(raw_edge.get("call_kind", "")):
                stats["call_kind_normalized"] += 1
            service_ids = [
                str(item)
                for item in raw_edge.get("service_requirement_ids", [])
                if str(item) in expected_service_requirement_ids
            ] if isinstance(raw_edge.get("service_requirement_ids", []), list) else []
            if service_ids and not cross_module:
                stats["invalid_call_edges_dropped"] += 1
                continue
            param_bindings = _valid_param_bindings(raw_edge.get("param_bindings", []), callee_fn)
            raw_had_bindings = bool(raw_edge.get("param_bindings")) if isinstance(raw_edge.get("param_bindings", []), list) else False
            if cross_module and service_ids and (raw_had_bindings and not param_bindings or (_signature_params(callee_fn) and not param_bindings)):
                stats["invalid_call_edges_dropped"] += 1
                continue
            edge_key = (callee, tuple(sorted(service_ids)))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            resolved_service_ids.update(service_ids)
            edges.append(
                {
                    "callee_function_id": callee,
                    "call_kind": call_kind,
                    "required": bool(raw_edge.get("required", False)),
                    "service_requirement_ids": service_ids,
                    "call_reason": str(raw_edge.get("call_reason", "")),
                    "param_bindings": param_bindings,
                    "return_binding": _valid_return_binding(raw_edge.get("return_binding", {}), functions_by_id, stats),
                    "failure_behavior": _valid_failure_behavior(raw_edge.get("failure_behavior", "ignore")),
                    "trace_ref_keys": raw_edge.get("trace_ref_keys", []) if isinstance(raw_edge.get("trace_ref_keys", []), list) else [],
                    "status": str(raw_edge.get("status", "inferred")) if str(raw_edge.get("status", "inferred")) in {"supported", "inferred", "assumed", "unresolved"} else "inferred",
                }
            )
        updates.append({"caller_function_id": caller, "calls_allowed": edges})
    candidate_unresolved = {
        str(item)
        for item in candidate.get("unresolved_service_requirements", [])
        if str(item) in expected_service_requirement_ids
    } if isinstance(candidate.get("unresolved_service_requirements", []), list) else set()
    unresolved = sorted((candidate_unresolved | (expected_service_requirement_ids - resolved_service_ids)) - resolved_service_ids)
    stats["service_requirements_closed"] = len(expected_service_requirement_ids - resolved_service_ids - candidate_unresolved)
    return (
        {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": str(candidate.get("candidate_id", fallback.get("candidate_id", ""))) or fallback.get("candidate_id", ""),
            "producer": candidate.get("producer", fallback.get("producer", {})) if isinstance(candidate.get("producer"), dict) else fallback.get("producer", {}),
            "call_updates": updates,
            "unresolved_service_requirements": unresolved,
            "assumptions": candidate.get("assumptions", []) if isinstance(candidate.get("assumptions"), list) else [],
            "unresolved_questions": candidate.get("unresolved_questions", []) if isinstance(candidate.get("unresolved_questions"), list) else [],
        },
        stats,
    )


def _call_edge_records(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for update in candidate.get("call_updates", []):
        if not isinstance(update, dict):
            continue
        caller = str(update.get("caller_function_id", ""))
        for edge in update.get("calls_allowed", []):
            if not isinstance(edge, dict):
                continue
            callee = str(edge.get("callee_function_id", ""))
            if caller and callee:
                records.append({"caller": caller, "callee": callee, "update": update, "edge": edge})
    return records


def _service_requirements_by_id(functions_by_id: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(requirement.get("service_requirement_id", "")): requirement
        for function in functions_by_id.values()
        for requirement in function.get("service_requirements", [])
        if isinstance(requirement, dict) and str(requirement.get("service_requirement_id", "")).strip()
    }


def _call_function_text(function: dict[str, Any]) -> str:
    values = [
        function.get("function_id", ""),
        function.get("name", ""),
        function.get("purpose", ""),
        function.get("function_kind", ""),
    ]
    for key in ("behavior_contract", "event_contract"):
        value = function.get(key, {})
        if isinstance(value, dict):
            values.extend(value.get(field, "") for field in ("input", "action", "output", "response", "state_change", "event_type"))
    return " ".join(str(value) for value in values).lower()


def _call_edge_text(record: dict[str, Any], functions_by_id: dict[str, dict[str, Any]], requirements_by_id: dict[str, dict[str, Any]]) -> str:
    edge = record["edge"]
    values = [
        record.get("caller", ""),
        record.get("callee", ""),
        edge.get("call_kind", ""),
        edge.get("call_reason", ""),
    ]
    for requirement_id in edge.get("service_requirement_ids", []):
        requirement = requirements_by_id.get(str(requirement_id), {})
        values.extend([requirement_id, requirement.get("operation", "")])
        values.extend(requirement.get("expected_inputs", []))
    return " ".join(str(value) for value in values).lower()


def _has_any_token(text: str, tokens: set[str]) -> bool:
    return any(token in text for token in tokens)


def _reverse_cycle_record(record: dict[str, Any], cycle: list[dict[str, Any]]) -> dict[str, Any] | None:
    for other in cycle:
        if other is not record and other.get("caller") == record.get("callee") and other.get("callee") == record.get("caller"):
            return other
    return None


def _find_call_cycle(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for record in _call_edge_records(candidate):
        adjacency.setdefault(record["caller"], []).append(record)
    visited: set[str] = set()
    active: dict[str, int] = {}
    stack: list[dict[str, Any]] = []

    def visit(node: str) -> list[dict[str, Any]]:
        active[node] = len(stack)
        for record in adjacency.get(node, []):
            callee = record["callee"]
            if callee in active:
                return stack[active[callee]:] + [record]
            if callee in visited:
                continue
            stack.append(record)
            cycle = visit(callee)
            if cycle:
                return cycle
            stack.pop()
        active.pop(node, None)
        visited.add(node)
        return []

    for node in sorted(adjacency):
        if node in visited:
            continue
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def _cycle_break_score(record: dict[str, Any], functions_by_id: dict[str, dict[str, Any]], requirements_by_id: dict[str, dict[str, Any]], cycle: list[dict[str, Any]]) -> tuple[int, str, str]:
    caller = functions_by_id.get(record["caller"], {})
    callee = functions_by_id.get(record["callee"], {})
    edge = record["edge"]
    same_module = str(caller.get("module_id", "")) == str(callee.get("module_id", ""))
    caller_local = not _function_is_public(caller)
    callee_public = _function_is_public(callee)
    score = 0
    if same_module and caller_local and callee_public:
        score -= 100
    reverse = _reverse_cycle_record(record, cycle)
    if reverse is not None:
        edge_text = _call_edge_text(record, functions_by_id, requirements_by_id)
        reverse_text = _call_edge_text(reverse, functions_by_id, requirements_by_id)
        callee_text = _call_function_text(callee)
        if (
            _has_any_token(edge_text, {"deliver", "delivery", "callback"})
            and _has_any_token(callee_text, {"process", "handle_in", "inbound", "route", "dispatch"})
            and _has_any_token(reverse_text, {"route", "routing", "dispatch"})
        ):
            score -= 150
    if str(edge.get("status", "")).lower() in {"inferred", "assumed"}:
        score -= 20
    if not edge.get("service_requirement_ids"):
        score -= 10
    if str(edge.get("call_kind", "")) in {"lifecycle", "error_handling", "utility"}:
        score -= 5
    if not bool(edge.get("required", False)):
        score -= 1
    return (score, record["caller"], record["callee"])


def normalize_calls_allowed_aggregate(candidate: dict[str, Any], draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    result = deepcopy(candidate)
    stats = {"aggregate_cycle_edges_removed": 0, "aggregate_removed_service_requirements_closed": 0}
    functions_by_id = {
        str(item.get("function_id", "")): item
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict) and str(item.get("function_id", "")).strip()
    }
    requirements_by_id = _service_requirements_by_id(functions_by_id)
    removed_service_ids: set[str] = set()
    for _ in range(100):
        cycle = _find_call_cycle(result)
        if not cycle:
            break
        remove_record = min(cycle, key=lambda record: _cycle_break_score(record, functions_by_id, requirements_by_id, cycle))
        calls = remove_record["update"].get("calls_allowed", [])
        if isinstance(calls, list) and remove_record["edge"] in calls:
            removed_service_ids.update(str(item) for item in remove_record["edge"].get("service_requirement_ids", []) if str(item).strip())
            calls.remove(remove_record["edge"])
            stats["aggregate_cycle_edges_removed"] += 1
            continue
        break
    if removed_service_ids:
        known_service_ids = set(_service_requirement_ids(list(functions_by_id.values())))
        resolved_service_ids = {
            str(item)
            for record in _call_edge_records(result)
            for item in record["edge"].get("service_requirement_ids", [])
            if str(item).strip()
        }
        unresolved_service_ids = {
            str(item)
            for item in result.get("unresolved_service_requirements", [])
            if str(item).strip()
        }
        closed = sorted((removed_service_ids & known_service_ids) - resolved_service_ids - unresolved_service_ids)
        if closed:
            result["unresolved_service_requirements"] = sorted(unresolved_service_ids | set(closed))
            stats["aggregate_removed_service_requirements_closed"] = len(closed)
    return result, stats


def merge_calls_allowed(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    updates = {str(item.get("caller_function_id")): item for item in candidate.get("call_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if update:
            function["calls_allowed"] = [edge["callee_function_id"] for edge in update.get("calls_allowed", [])]
            function["call_contracts"] = update.get("calls_allowed", [])
    result.setdefault("unresolved_questions", []).extend(_nonblocking_questions(candidate.get("unresolved_questions", [])))
    result.setdefault("accepted_stage_artifacts", []).append("5.4e_call_contracts")
    return result


def _exported_type_file_ids(files: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for file_item in files:
        file_id = str(file_item.get("file_id", ""))
        for type_id in file_item.get("exports_type_ids", []):
            if file_id and str(type_id).strip():
                result.setdefault(str(type_id), file_id)
    return result


def fallback_file_layout(draft: dict[str, Any]) -> dict[str, Any]:
    functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    exportable_by_module: dict[str, list[str]] = {}
    for type_item in draft.get("canonical_types", []):
        if not isinstance(type_item, dict) or lower_canonical_type_to_header_data(type_item) is None:
            continue
        module_id = str(type_item.get("owner_module_id", type_item.get("module_id", "")))
        type_id = str(type_item.get("type_id", ""))
        if module_id and type_id:
            exportable_by_module.setdefault(module_id, []).append(type_id)

    def public(function: dict[str, Any]) -> bool:
        return (
            bool(function.get("exported"))
            or str(function.get("visibility", "")).lower() == "public"
            or str(function.get("api_surface", "")).lower() == "public"
        )

    def function_text(function: dict[str, Any]) -> str:
        return " ".join(
            str(function.get(key, ""))
            for key in ("function_id", "name", "purpose", "function_kind", "grouping_hint", "public_api_role")
        ).lower()

    def module_unit(module_id: str, function: dict[str, Any]) -> str:
        text = function_text(function)
        kind = str(function.get("function_kind", ""))
        if module_id == "network_io":
            if any(token in text for token in ("server", "accept", "listen", "epoll", "runtime", "callback", "on_accept")):
                return "tcp_server"
            return "connection"
        if module_id == "mqtt_codec":
            if kind == "parser" or any(token in text for token in ("decode", "read", "parse", "remaining", "feed")):
                return "decoder"
            if kind == "serializer" or any(token in text for token in ("encode", "write", "serialize")):
                return "encoder"
            return "packet"
        if module_id == "session_manager":
            if "session_manager" in text and "handle_handle" not in text:
                return "session_manager"
            return "session"
        if module_id == "topic_router":
            if any(token in text for token in ("topic_tree", "topic_match", "match_entry", "entry_remove", "runtime")):
                return "topic_tree"
            return "topic_router"
        if module_id == "timer_service":
            if any(token in text for token in ("wheel", "heap", "insert")):
                return "timer_wheel"
            return "timer_service"
        if module_id == "broker_app":
            return "broker"
        if kind in {"parser", "serializer"}:
            return "codec"
        if any(token in text for token in ("tree", "match", "route", "router")):
            return "router"
        if any(token in text for token in ("session", "registry", "manager")):
            return "session"
        return module_id

    files: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    file_by_module_unit: dict[tuple[str, str], dict[str, Any]] = {}
    for module in draft.get("module_artifacts", []):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", "module"))
        module_functions = [item for item in functions if str(item.get("module_id")) == module_id]
        units = sorted({module_unit(module_id, function) for function in module_functions}) or [module_id]
        for unit in units:
            source_path = f"src/{module_id}/{unit}.c"
            file_item = {
                "file_id": f"file:{source_path[:-2]}",
                "source_path": source_path,
                "header_path": f"src/{module_id}/{unit}.h",
                "module_id": module_id,
                "kind": "source_header_pair",
                "responsibility": f"Implement the {unit} role for the {module_id} module.",
                "exports_function_ids": [],
                "implements_function_ids": [],
                "exports_type_ids": [],
                "imports_allowed": [],
                "trace_ref_keys": _trace(module.get("source_fact_ids", []), module.get("owned_capabilities", [])),
                "status": "inferred",
            }
            file_by_module_unit[(module_id, unit)] = file_item
            files.append(file_item)
        for function in module_functions:
            unit = module_unit(module_id, function)
            file_item = file_by_module_unit[(module_id, unit)]
            function_id = str(function.get("function_id", ""))
            file_item["implements_function_ids"].append(function_id)
            if public(function):
                file_item["exports_function_ids"].append(function_id)
            assignments.append(
                {
                    "function_id": function_id,
                    "implementation_file_id": file_item["file_id"],
                    "declaration_file_id": file_item["file_id"] if public(function) else "",
                    "visibility": str(function.get("visibility", "public")),
                    "reason": f"Deterministic role-aware placement in {unit}.",
                    "status": "inferred",
                }
            )
        module_files = [item for item in files if item["module_id"] == module_id]
        if module_files:
            module_files[0]["exports_type_ids"] = sorted(set(exportable_by_module.get(module_id, [])))

    file_by_id = {str(item.get("file_id", "")): item for item in files}
    function_file = {str(item.get("function_id", "")): str(item.get("implementation_file_id", "")) for item in assignments}
    first_file_by_module: dict[str, str] = {}
    for file_item in files:
        first_file_by_module.setdefault(str(file_item.get("module_id", "")), str(file_item.get("file_id", "")))
    type_file = _exported_type_file_ids(files)
    for function in functions:
        source_file = function_file.get(str(function.get("function_id", "")), "")
        source_item = file_by_id.get(source_file)
        if source_item is None:
            continue
        imports: set[str] = set(source_item.get("imports_allowed", []))
        for dependency in function.get("signature_dependencies", []):
            if not isinstance(dependency, dict):
                continue
            owner = str(dependency.get("owner_module_id", "")).strip()
            type_ref = str(dependency.get("type_ref", "")).strip()
            target_file = type_file.get(type_ref, "") or first_file_by_module.get(owner, "")
            if owner and target_file and target_file != source_file:
                imports.add(target_file)
        for edge in function.get("call_contracts", []):
            if not isinstance(edge, dict):
                continue
            target_file = function_file.get(str(edge.get("callee_function_id", "")), "")
            if target_file and target_file != source_file:
                imports.add(target_file)
        source_item["imports_allowed"] = sorted(item for item in imports if item in file_by_id and item != source_file)
    return {
        "schema_version": "file_layout_candidate/v2",
        "candidate_id": "candidate:file_layout:deterministic",
        "producer": _producer("5.5a_file_layout", "file_layout_candidate_prompt"),
        "files": files,
        "function_file_assignments": assignments,
        "assumptions": [],
        "unresolved_questions": [],
    }


def _canonical_type_export_indexes(draft: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    by_key: dict[str, str] = {}
    exportable_ids: set[str] = set()
    for item in draft.get("canonical_types", []):
        if not isinstance(item, dict) or lower_canonical_type_to_header_data(item) is None:
            continue
        type_id = str(item.get("type_id", "")).strip()
        if not type_id:
            continue
        exportable_ids.add(type_id)
        for value in (type_id, item.get("name"), item.get("c_symbol"), item.get("c_type_name")):
            key = normalize_type_key(value)
            if key:
                by_key.setdefault(key, type_id)
    return by_key, exportable_ids


def _inventory_type_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("type_id", "")): item
        for item in draft.get("type_inventory", [])
        if isinstance(item, dict) and str(item.get("type_id", "")).strip()
    }


def _public_inventory_type(item: dict[str, Any]) -> bool:
    return str(item.get("visibility", "")).lower() == "public" and str(item.get("defined_in", "")).lower() == "public_header"


def normalize_file_layout_candidate(candidate: dict[str, Any], draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    result = deepcopy(candidate)
    stats = {
        "layout_export_type_normalized": 0,
        "layout_internal_export_dropped": 0,
        "layout_unknown_export_dropped": 0,
    }
    canonical_by_key, exportable_ids = _canonical_type_export_indexes(draft)
    inventory_by_id = _inventory_type_by_id(draft)
    for file_item in result.get("files", []):
        if not isinstance(file_item, dict):
            continue
        normalized: list[str] = []
        for raw in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            type_id = str(raw).strip()
            replacement = type_id if type_id in exportable_ids else ""
            if not replacement:
                inventory_type = inventory_by_id.get(type_id)
                if inventory_type is not None and not _public_inventory_type(inventory_type):
                    stats["layout_internal_export_dropped"] += 1
                    continue
                if inventory_type is not None:
                    replacement = canonical_by_key.get(normalize_type_key(inventory_type.get("name", "")), "")
                else:
                    replacement = canonical_by_key.get(normalize_type_key(type_id), "")
            if replacement and replacement in exportable_ids:
                if replacement != type_id:
                    stats["layout_export_type_normalized"] += 1
                if replacement not in normalized:
                    normalized.append(replacement)
            else:
                stats["layout_unknown_export_dropped"] += 1
        file_item["exports_type_ids"] = normalized
    return result, stats


def _refresh_file_layout_candidate(candidate: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(candidate)
    files = [item for item in result.get("files", []) if isinstance(item, dict)]
    assignments = [item for item in result.get("function_file_assignments", []) if isinstance(item, dict)]
    file_by_id = {str(item.get("file_id", "")): item for item in files}
    functions_by_id = {
        str(item.get("function_id", "")): item
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict) and str(item.get("function_id", "")).strip()
    }
    for file_item in files:
        file_item["exports_function_ids"] = []
        file_item["implements_function_ids"] = []
        file_item["imports_allowed"] = []
    for assignment in assignments:
        function_id = str(assignment.get("function_id", ""))
        function = functions_by_id.get(function_id, {})
        file_id = str(assignment.get("implementation_file_id", ""))
        file_item = file_by_id.get(file_id)
        if file_item is None:
            continue
        file_item["implements_function_ids"].append(function_id)
        is_public = (
            bool(function.get("exported"))
            or str(function.get("visibility", "")).lower() == "public"
            or str(function.get("api_surface", "")).lower() == "public"
        )
        assignment["declaration_file_id"] = file_id if is_public else ""
        if is_public:
            file_item["exports_function_ids"].append(function_id)
    function_file = {str(item.get("function_id", "")): str(item.get("implementation_file_id", "")) for item in assignments}
    first_file_by_module: dict[str, str] = {}
    for file_item in files:
        first_file_by_module.setdefault(str(file_item.get("module_id", "")), str(file_item.get("file_id", "")))
    type_file = _exported_type_file_ids(files)
    for function_id, function in functions_by_id.items():
        source_file = function_file.get(function_id, "")
        source_item = file_by_id.get(source_file)
        if source_item is None:
            continue
        imports: set[str] = set()
        for dependency in function.get("signature_dependencies", []):
            if not isinstance(dependency, dict):
                continue
            owner = str(dependency.get("owner_module_id", "")).strip()
            type_ref = str(dependency.get("type_ref", "")).strip()
            target_file = type_file.get(type_ref, "") or first_file_by_module.get(owner, "")
            if owner and target_file and target_file != source_file:
                imports.add(target_file)
        for edge in function.get("call_contracts", []):
            if not isinstance(edge, dict):
                continue
            target_file = function_file.get(str(edge.get("callee_function_id", "")), "")
            if target_file and target_file != source_file:
                imports.add(target_file)
        source_item["imports_allowed"] = sorted(item for item in imports if item in file_by_id and item != source_file)
    result["files"] = files
    result["function_file_assignments"] = assignments
    return result


def apply_file_layout_override_patch(baseline: dict[str, Any], patch: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(baseline)
    if patch.get("keep_baseline"):
        result.setdefault("assumptions", []).extend(patch.get("assumptions", []) if isinstance(patch.get("assumptions"), list) else [])
        result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []) if isinstance(patch.get("unresolved_questions"), list) else [])
        return result
    files = [item for item in result.get("files", []) if isinstance(item, dict)]
    assignments = [item for item in result.get("function_file_assignments", []) if isinstance(item, dict)]
    functions_by_id = {
        str(item.get("function_id", "")): item
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict) and str(item.get("function_id", "")).strip()
    }
    file_by_id = {str(item.get("file_id", "")): item for item in files}
    assignment_by_function = {str(item.get("function_id", "")): item for item in assignments}
    forced_modules = patch.get("force_single_unit_module_ids", []) if isinstance(patch.get("force_single_unit_module_ids"), list) else []
    for module_id in forced_modules:
        module_files = [item for item in files if str(item.get("module_id", "")) == str(module_id)]
        if len(module_files) <= 1:
            continue
        primary = module_files[0]
        removed = {str(item.get("file_id", "")) for item in module_files[1:]}
        files = [item for item in files if str(item.get("file_id", "")) not in removed]
        for assignment in assignments:
            if str(assignment.get("implementation_file_id", "")) in removed:
                assignment["implementation_file_id"] = primary["file_id"]
            if str(assignment.get("declaration_file_id", "")) in removed:
                assignment["declaration_file_id"] = primary["file_id"]
        file_by_id = {str(item.get("file_id", "")): item for item in files}
    responsibility_overrides = patch.get("file_responsibility_overrides", []) if isinstance(patch.get("file_responsibility_overrides"), list) else []
    for override in responsibility_overrides:
        if not isinstance(override, dict):
            continue
        file_item = file_by_id.get(str(override.get("file_id", "")))
        if file_item is None:
            continue
        if str(override.get("responsibility", "")).strip():
            file_item["responsibility"] = str(override.get("responsibility", ""))
        if isinstance(override.get("trace_ref_keys"), list):
            file_item["trace_ref_keys"] = [str(item) for item in override.get("trace_ref_keys", []) if str(item).strip()]
    reassignments = patch.get("function_reassignments", []) if isinstance(patch.get("function_reassignments"), list) else []
    for reassignment in reassignments:
        if not isinstance(reassignment, dict):
            continue
        function_id = str(reassignment.get("function_id", ""))
        target_file_id = str(reassignment.get("target_file_id", ""))
        function = functions_by_id.get(function_id)
        target_file = file_by_id.get(target_file_id)
        assignment = assignment_by_function.get(function_id)
        if function is None or target_file is None or assignment is None:
            continue
        if str(function.get("module_id", "")) != str(target_file.get("module_id", "")):
            continue
        assignment["implementation_file_id"] = target_file_id
        assignment["reason"] = str(reassignment.get("reason", "")) or "LLM file layout override within deterministic baseline."
    result["files"] = files
    result["function_file_assignments"] = assignments
    result = _refresh_file_layout_candidate(result, draft)
    result.setdefault("assumptions", []).extend(patch.get("assumptions", []) if isinstance(patch.get("assumptions"), list) else [])
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []) if isinstance(patch.get("unresolved_questions"), list) else [])
    return result


def merge_file_layout(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["file_layout"] = {
        "files": [
            {
                "file_id": item["file_id"],
                "module_id": item["module_id"],
                "kind": "source_header_pair",
                "path": item["source_path"],
                "source_path": item["source_path"],
                "header_path": item["header_path"],
                "responsibility": item["responsibility"],
                "exports": item["exports_function_ids"],
                "exports_type_ids": item["exports_type_ids"],
                "implements": item["implements_function_ids"],
                "imports_allowed": [target for target in item.get("imports_allowed", []) if str(target).startswith("file:") and target != item["file_id"]],
                "traceability": {"source_fact_ids": item.get("trace_ref_keys", []), "decision_ids": [f"decision:file:{item['module_id']}"]},
            }
            for item in candidate.get("files", [])
            if isinstance(item, dict)
        ]
    }
    paths_by_module: dict[str, list[str]] = {}
    for item in result["file_layout"]["files"]:
        module_id = str(item.get("module_id", ""))
        for path in (item.get("header_path", ""), item.get("source_path", "")):
            if module_id and str(path).strip() and str(path) not in paths_by_module.setdefault(module_id, []):
                paths_by_module[module_id].append(str(path))
    for module in result.get("module_artifacts", []):
        if isinstance(module, dict) and str(module.get("module_id", "")) in paths_by_module:
            module["files"] = paths_by_module[str(module.get("module_id", ""))]
    assignments = {str(item.get("function_id")): item for item in candidate.get("function_file_assignments", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        assignment = assignments.get(str(function.get("function_id")))
        if assignment:
            function["file_id"] = assignment.get("implementation_file_id", "")
            function["declared_in"] = assignment.get("declaration_file_id", "")
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.5a_file_layout")
    return result


def _key_flow_module_id(draft: dict[str, Any]) -> str:
    modules = [item for item in draft.get("module_artifacts", []) if isinstance(item, dict)]
    if not modules:
        return ""
    scored: list[tuple[int, str]] = []
    for index, module in enumerate(modules):
        module_id = str(module.get("module_id", ""))
        text = " ".join(
            [
                module_id,
                str(module.get("name", "")),
                str(module.get("purpose", "")),
                " ".join(str(cap) for cap in module.get("owned_capabilities", [])),
            ]
        ).lower()
        score = 0
        if any(word in text for word in ("broker", "server", "client", "flow", "app")):
            score += 40
        if "role_composition" in module.get("owned_capabilities", []):
            score += 30
        if "semantic_dispatch" in module.get("owned_capabilities", []):
            score += 20
        if module.get("support_module"):
            score -= 30
        scored.append((score - index, module_id))
    scored.sort(reverse=True)
    return scored[0][1]


def _primary_file_id(draft: dict[str, Any], module_id: str) -> str:
    for item in draft.get("file_layout", {}).get("files", []):
        if isinstance(item, dict) and str(item.get("module_id", "")) == module_id and str(item.get("header_path", "")).strip():
            return str(item.get("file_id", ""))
    for item in draft.get("file_layout", {}).get("files", []):
        if isinstance(item, dict) and str(item.get("module_id", "")) == module_id:
            return str(item.get("file_id", ""))
    return ""


def _find_lifecycle_function_id(draft: dict[str, Any], module_id: str, action: str) -> str:
    suffixes = [f"_{action}"]
    if action == "run":
        suffixes.append("_serve")
    for function in draft.get("function_contracts", []):
        if not isinstance(function, dict) or str(function.get("module_id", "")) != module_id:
            continue
        name = str(function.get("name", ""))
        if any(name.endswith(suffix) for suffix in suffixes) and _is_lifecycle_function(function, action):
            return str(function.get("function_id", ""))
    return f"fn:{module_id}:{action}"


def fallback_runtime_entrypoint(draft: dict[str, Any]) -> dict[str, Any]:
    module_id = _key_flow_module_id(draft)
    lifecycle = {action: _find_lifecycle_function_id(draft, module_id, action) for action in ("create", "start", "run", "destroy")}
    return {
        "schema_version": "runtime_entrypoint_candidate/v1",
        "candidate_id": "candidate:runtime_entrypoint:deterministic",
        "producer": _producer("5.5b_runtime_entrypoint", "runtime_entrypoint_candidate_prompt"),
        "key_flow_module_id": module_id,
        "lifecycle_function_ids": lifecycle,
        "source_path": "main.c",
        "entrypoint_signature": {
            "raw": "int main(int argc, char** argv)",
            "name": "main",
            "storage_class": "none",
            "return_type": "int",
            "params": [
                {"name": "argc", "type": "int", "type_ref": "", "direction": "in", "nullable": False, "ownership": "BORROWED", "passing_mode": "by_value"},
                {"name": "argv", "type": "char**", "type_ref": "", "direction": "in", "nullable": False, "ownership": "BORROWED", "passing_mode": "by_pointer"},
            ],
        },
        "startup_sequence": [
            {"step": "parse_args", "function_id": "", "description": "Parse an optional listen port from argc/argv."},
            {"step": "create", "function_id": lifecycle["create"], "description": "Create the key flow object."},
            {"step": "start", "function_id": lifecycle["start"], "description": "Start the protocol runtime."},
            {"step": "run", "function_id": lifecycle["run"], "description": "Run the protocol event loop."},
            {"step": "destroy", "function_id": lifecycle["destroy"], "description": "Destroy the key flow object before returning."},
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def _lifecycle_signature(protocol: str, module_id: str, action: str, name: str) -> dict[str, Any]:
    handle_type = f"{_safe_id(protocol)}_{_safe_id(module_id)}_t"
    if action == "create":
        return _function_signature(
            f"{handle_type}*",
            name,
            [{"type": "uint16_t", "name": "port", "nullable": False, "ownership": "BORROWED"}],
        )
    if action == "destroy":
        return _function_signature(
            "void",
            name,
            [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "BORROWED"}],
        )
    return _function_signature(
        "int",
        name,
        [{"type": f"{handle_type}*", "name": "self", "nullable": False, "ownership": "BORROWED"}],
    )


def _runtime_function_base(
    *,
    protocol: str,
    module_id: str,
    function_id: str,
    action: str,
    file_id: str,
    capability_ids: list[str],
) -> dict[str, Any]:
    name = f"{_safe_id(protocol)}_{_safe_id(module_id)}_{action}"
    signature = _lifecycle_signature(protocol, module_id, action, name)
    return {
        "function_id": function_id,
        "name": name,
        "module_id": module_id,
        "file_id": file_id,
        "declared_in": file_id,
        "function_kind": "resource_lifecycle" if action in {"create", "destroy"} else "public_api",
        "coder_function_type": "ALGORITHM",
        "visibility": "public",
        "api_surface": "public",
        "exported": True,
        "export_reason": "Required by runtime entrypoint startup sequence.",
        "public_api_role": f"runtime_{action}",
        "grouping_hint": module_id,
        "purpose": f"{action.capitalize()} the deployable protocol key flow object.",
        "capability_ids": capability_ids,
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "signature": {
            "raw": signature["raw"],
            "name": signature["name"],
            "storage_class": "none",
            "return_type": signature["return_type"],
            "params": [
                {
                    "name": param["name"],
                    "type": param["type"],
                    "type_ref": "",
                    "direction": "in",
                    "nullable": bool(param.get("nullable", False)),
                    "ownership": normalize_param_ownership_for_coder(param.get("ownership")),
                    "passing_mode": "by_pointer" if "*" in str(param.get("type", "")) else "by_value",
                }
                for param in signature["params"]
            ],
        },
        "input_contract": {"params": signature["params"]},
        "output_contract": {"return_type": signature["return_type"]},
        "state_access": [],
        "wire_mapping": [],
        "access_paths": [],
        "error_behavior": "propagation=return_code; recovery=cleanup; return_policy=status_code",
        "calls_allowed": [],
        "call_contracts": [],
        "side_effects": [],
        "signature_dependencies": [],
        "interface_type_declarations": [],
        "resource_access": [],
        "internal_type_refs": [],
        "service_requirements": [],
        "forbidden_symbols": [],
        "logic_kind": "LOGIC",
        "behavior_contract": {
            "input": "The key flow object and runtime parameters.",
            "action": f"{action.capitalize()} the deployable protocol runtime boundary.",
            "output": "Lifecycle status or object pointer.",
            "preconditions": [],
            "postconditions": [],
            "invariants_used": [],
            "idempotent": False,
            "thread_safety": "single_thread_only",
        },
        "traceability": {"source_fact_ids": [], "decision_ids": [f"decision:runtime:{module_id}:{action}"]},
    }


def merge_runtime_entrypoint(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    protocol = str(result.get("protocol_name", "protocol"))
    module_ids = {str(item.get("module_id", "")) for item in result.get("module_artifacts", []) if isinstance(item, dict)}
    module_id = str(candidate.get("key_flow_module_id") or _key_flow_module_id(result))
    if module_id not in module_ids:
        module_id = _key_flow_module_id(result)
    key_file_id = _primary_file_id(result, module_id)
    lifecycle_ids = candidate.get("lifecycle_function_ids", {}) if isinstance(candidate.get("lifecycle_function_ids"), dict) else {}
    module = next((item for item in result.get("module_artifacts", []) if isinstance(item, dict) and str(item.get("module_id", "")) == module_id), {})
    capability_ids = [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()]
    functions_by_id = {str(item.get("function_id", "")): item for item in result.get("function_contracts", []) if isinstance(item, dict)}
    key_file = next((item for item in result.get("file_layout", {}).get("files", []) if isinstance(item, dict) and str(item.get("file_id", "")) == key_file_id), None)

    resolved_lifecycle_ids: dict[str, str] = {}
    for action in ("create", "start", "run", "destroy"):
        requested_id = str(lifecycle_ids.get(action) or "").strip()
        requested_function = functions_by_id.get(requested_id)
        function_id = requested_id if requested_function and _is_lifecycle_function(requested_function, action) else f"fn:{module_id}:{action}"
        resolved_lifecycle_ids[action] = function_id
        if function_id not in functions_by_id:
            function = _runtime_function_base(protocol=protocol, module_id=module_id, function_id=function_id, action=action, file_id=key_file_id, capability_ids=capability_ids)
            result.setdefault("function_contracts", []).append(function)
            functions_by_id[function_id] = function
        else:
            function = functions_by_id[function_id]
            function["module_id"] = module_id
            function["visibility"] = "public"
            function["api_surface"] = "public"
            function["exported"] = True
            function["export_reason"] = function.get("export_reason") or "Required by runtime entrypoint startup sequence."
            function["public_api_role"] = function.get("public_api_role") or f"runtime_{action}"
            function["file_id"] = function.get("file_id") or key_file_id
            function["declared_in"] = function.get("declared_in") or key_file_id
        if key_file is not None:
            for key in ("implements", "exports"):
                key_file.setdefault(key, [])
                if function_id not in key_file[key]:
                    key_file[key].append(function_id)

    source_path = str(candidate.get("source_path") or "main.c").strip() or "main.c"
    if not source_path.endswith(".c"):
        source_path = "main.c"
    file_id = f"file:{source_path.removesuffix('.c')}"
    main_function_id = f"fn:{module_id}:runtime_entrypoint_main"
    signature = candidate.get("entrypoint_signature", {}) if isinstance(candidate.get("entrypoint_signature"), dict) else {}
    if not str(signature.get("raw", "")).strip():
        signature = fallback_runtime_entrypoint(result)["entrypoint_signature"]
    main_function = {
        "function_id": main_function_id,
        "name": str(signature.get("name", "main")) or "main",
        "module_id": module_id,
        "file_id": file_id,
        "declared_in": "",
        "function_kind": "public_api",
        "coder_function_type": "ENTRYPOINT",
        "visibility": "internal",
        "api_surface": "module_internal",
        "exported": False,
        "export_reason": "",
        "public_api_role": "",
        "grouping_hint": "runtime_entrypoint",
        "purpose": "Start the deployable protocol runtime from process arguments.",
        "capability_ids": [],
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "signature": signature,
        "input_contract": {"params": signature.get("params", [])},
        "output_contract": {"return_type": signature.get("return_type", "int")},
        "state_access": [],
        "wire_mapping": [],
        "access_paths": [],
        "error_behavior": "propagation=return_code; recovery=cleanup; return_policy=status_code",
        "calls_allowed": [resolved_lifecycle_ids[action] for action in ("create", "start", "run", "destroy")],
        "call_contracts": [
            {
                "callee_function_id": resolved_lifecycle_ids[action],
                "call_kind": "lifecycle",
                "required": True,
                "service_requirement_ids": [],
                "call_reason": f"Runtime entrypoint {action} step.",
                "param_bindings": [],
                "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
                "failure_behavior": "cleanup_and_return",
                "trace_ref_keys": [],
                "status": "inferred",
            }
            for action in ("create", "start", "run", "destroy")
        ],
        "side_effects": [],
        "signature_dependencies": [],
        "interface_type_declarations": [],
        "resource_access": [],
        "internal_type_refs": [],
        "service_requirements": [],
        "forbidden_symbols": [],
        "logic_kind": "LOGIC",
        "behavior_contract": {
            "input": "argc and argv supplied by the C runtime.",
            "action": "Parse startup arguments, create/start/run/destroy the key flow object, and return process status.",
            "output": "Returns 0 on normal startup/run completion and non-zero on startup failure.",
            "preconditions": ["argc/argv are provided by the C runtime."],
            "postconditions": ["Allocated key flow resources are destroyed before return."],
            "invariants_used": [],
            "idempotent": False,
            "thread_safety": "PROCESS_ENTRYPOINT",
        },
        "traceability": {"source_fact_ids": [], "decision_ids": ["decision:runtime_entrypoint:main"]},
    }
    if main_function_id not in functions_by_id:
        result.setdefault("function_contracts", []).append(main_function)
    else:
        functions_by_id[main_function_id].update(main_function)

    files = result.setdefault("file_layout", {}).setdefault("files", [])
    existing_entry_file = next((item for item in files if isinstance(item, dict) and str(item.get("file_id", "")) == file_id), None)
    entry_file = {
        "file_id": file_id,
        "module_id": module_id,
        "kind": "source_only_entrypoint",
        "path": source_path,
        "source_path": source_path,
        "header_path": "",
        "responsibility": "Source-only process entrypoint that starts the protocol runtime.",
        "exports": [],
        "exports_type_ids": [],
        "implements": [main_function_id],
        "imports_allowed": [key_file_id] if key_file_id and key_file_id != file_id else [],
        "traceability": {"source_fact_ids": [], "decision_ids": ["decision:file:runtime_entrypoint"]},
    }
    if existing_entry_file is None:
        files.append(entry_file)
    else:
        existing_entry_file.update(entry_file)

    unresolved = [
        item
        for item in candidate.get("unresolved_questions", [])
        if not (
            isinstance(item, dict)
            and str(item.get("question_id", "")).strip() == "question:lifecycle_function_identification"
            and str(item.get("target_id", "")).strip() == module_id
        )
    ]
    result.setdefault("unresolved_questions", []).extend(unresolved)
    result.setdefault("accepted_stage_artifacts", []).append("5.5b_runtime_entrypoint")
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
            result.setdefault("unresolved_questions", []).append(
                {
                    "target_id": action.get("target_id", ""),
                    "target_kind": action.get("target_kind", "dependency_error"),
                    "question": action.get("reason", ""),
                    "unresolved_reason": action.get("reason", ""),
                    "stage": "5.6_dependency_repair",
                    "blocking": True,
                    "suggested_repair": "repair dependency inputs instead of accepting an empty dependency graph",
                }
            )
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
    result.setdefault("unresolved_questions", []).extend(
        {
            "target_id": str(error.get("path") or error.get("code", "dependency_graph")),
            "target_kind": "dependency_error",
            "question": str(error.get("message", "Dependency validation failed.")),
            "unresolved_reason": str(error.get("message", "Dependency validation failed.")),
            "stage": "5.6_dependency_repair",
            "blocking": True,
            "dependency_error_code": str(error.get("code", "dependency_validation_error")),
            "suggested_repair": "repair calls_allowed, imports_allowed, signature_dependencies, or call_contracts; do not clear dependency inputs",
        }
        for error in dependency_errors
    )
    return result


def finalize_dependency_graph(draft: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    result["dependency_graph"] = derive_dependency_graph(result)
    result.pop("accepted_stage_artifacts", None)
    return result
