from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..schemas.implementation_plan import SCHEMA_VERSION
from .coder_spec_lowering import normalize_param_ownership_for_coder
from .dependencies import derive_dependency_graph
from .implementation_plan import _capability_refs, _field_value, _function_signature, _handler_surfaces, _safe_id, _surface_units, _target_directives, _wire_fields
from .implementation_plan_context import SYSTEM_TYPE_IDS


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


LIFECYCLE_ROLES = ("runtime_create", "runtime_start", "runtime_run", "runtime_destroy")


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
            "file_id_pattern": "file:<source_path_without_.c>",
        },
        "validation_targets": {
            "capability_coverage": True,
            "handler_coverage": True,
            "wire_field_coverage": True,
            "dependency_derivation_only": True,
            "blueprint_no_new_engineering_semantics": True,
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
        "producer": _producer("5.3_module_artifacts", "module_artifacts_candidate_prompt"),
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
    result.setdefault("accepted_stage_artifacts", []).append("5.3_module_artifacts")
    return result


def _empty_callback_signature() -> dict[str, Any]:
    return {"return_type": "", "params": []}


def _empty_type_lifecycle() -> dict[str, list[str]]:
    return {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []}


def _type_inventory_item(
    *,
    module_id: str,
    name: str,
    kind: str,
    visibility: str,
    defined_in: str,
    purpose: str,
    related_functions: list[str] | None = None,
    lifecycle: dict[str, list[str]] | None = None,
    trace_ref_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type_id": f"type:{module_id}:{_safe_id(name)}",
        "name": name,
        "module_id": module_id,
        "kind": kind,
        "visibility": visibility,
        "defined_in": defined_in,
        "purpose": purpose,
        "fields": [],
        "enum_values": [],
        "callback_signature": _empty_callback_signature(),
        "ownership_lifetime": "",
        "lifecycle": lifecycle or _empty_type_lifecycle(),
        "related_functions": related_functions or [],
        "dependencies": [],
        "trace_ref_keys": trace_ref_keys or [],
        "status": "inferred",
    }


def _type_artifact_kind(name: str, role: str) -> tuple[str, str, str]:
    text = f"{name} {role}".lower()
    if name.endswith("_t") or any(word in text for word in ("opaque", "handle", "context")):
        return "opaque_handle", "public", "public_header"
    if any(word in text for word in ("buffer", "payload", "bytes")):
        return "owned_buffer", "public", "public_header"
    if any(word in text for word in ("callback", "cb", "hook")):
        return "callback_type", "public", "public_header"
    if any(word in text for word in ("enum", "flags", "bitflag")):
        return "enum", "public", "public_header"
    return "struct", "public", "public_header"


def fallback_type_inventory(draft: dict[str, Any], module_artifact: dict[str, Any]) -> dict[str, Any]:
    module_id = str(module_artifact.get("module_id", "module"))
    protocol = _safe_id(str(draft.get("protocol_name", "protocol")))
    artifact_funcs = [
        str(item.get("name", "")).strip()
        for item in module_artifact.get("artifacts", [])
        if isinstance(item, dict) and str(item.get("kind", "")).upper() == "FUNC" and str(item.get("name", "")).strip()
    ]
    lifecycle = _empty_type_lifecycle()
    lifecycle["created_by"] = [name for name in artifact_funcs if name.endswith(("_create", "_init", "_open"))]
    lifecycle["initialized_by"] = [name for name in artifact_funcs if name.endswith(("_init", "_create", "_start"))]
    lifecycle["destroyed_by"] = [name for name in artifact_funcs if name.endswith(("_destroy", "_close", "_deinit", "_cleanup"))]
    lifecycle["freed_by"] = [name for name in artifact_funcs if name.endswith(("_free", "_destroy", "_close", "_cleanup"))]
    types: list[dict[str, Any]] = []
    for artifact in module_artifact.get("artifacts", []):
        if not isinstance(artifact, dict) or str(artifact.get("kind", "")).upper() != "TYPE":
            continue
        name = str(artifact.get("name", "")).strip()
        if not name:
            continue
        kind, visibility, defined_in = _type_artifact_kind(name, str(artifact.get("role", "")))
        item = _type_inventory_item(
            module_id=module_id,
            name=name,
            kind=kind,
            visibility=visibility,
            defined_in=defined_in,
            purpose=str(artifact.get("role", "")) or f"Type artifact {name}.",
            related_functions=artifact_funcs,
            lifecycle=deepcopy(lifecycle),
            trace_ref_keys=module_artifact.get("source_fact_ids", []),
        )
        if kind in {"owned_buffer", "struct"} and any(word in item["purpose"].lower() for word in ("owned", "allocated", "heap", "payload", "buffer")):
            item["ownership_lifetime"] = "Owned data must be released by the module cleanup/free path."
        types.append(item)
    if not any(item["kind"] == "opaque_handle" for item in types):
        handle = f"{protocol}_{_safe_id(module_id)}_t"
        types.append(
            _type_inventory_item(
                module_id=module_id,
                name=handle,
                kind="opaque_handle",
                visibility="public",
                defined_in="public_header",
                purpose="Opaque module context handle.",
                related_functions=artifact_funcs,
                lifecycle=deepcopy(lifecycle),
                trace_ref_keys=module_artifact.get("source_fact_ids", []),
            )
        )
    if module_artifact.get("state_owned") or module_artifact.get("owned_capabilities"):
        state_name = f"struct {protocol}_{_safe_id(module_id)}"
        state_key = _safe_id(state_name.removeprefix("struct "))
        if not any(_safe_id(str(item["name"]).removeprefix("struct ").removesuffix("_t")) == state_key for item in types):
            types.append(
                _type_inventory_item(
                    module_id=module_id,
                    name=state_name,
                    kind="internal_state",
                    visibility="private",
                    defined_in="source_file",
                    purpose="Private module context storage.",
                    related_functions=artifact_funcs,
                    lifecycle=deepcopy(lifecycle),
                    trace_ref_keys=module_artifact.get("source_fact_ids", []),
                )
            )
    return {
        "schema_version": "type_inventory_candidate/v1",
        "candidate_id": f"candidate:type_inventory:{module_id}",
        "producer": _producer("5.4a_type_inventory", "type_inventory_candidate_prompt"),
        "module_id": module_id,
        "types": types,
        "assumptions": [],
        "unresolved_questions": [],
    }


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
    result.setdefault("accepted_stage_artifacts", []).append("5.4a_type_inventory")
    return result


def apply_type_inventory_repair_patch(candidate: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(candidate)
    by_id = {
        str(type_item.get("type_id", "")): type_item
        for type_item in result.get("types", [])
        if isinstance(type_item, dict)
    }
    for update in patch.get("updated_types", []):
        type_item = by_id.get(str(update.get("type_id", "")))
        if not type_item:
            continue
        for key in ("visibility", "defined_in", "purpose", "fields", "enum_values", "callback_signature", "ownership_lifetime", "lifecycle", "related_functions", "dependencies", "status"):
            if key in update:
                type_item[key] = deepcopy(update[key])
    result.setdefault("types", []).extend(deepcopy(patch.get("added_types", [])))
    result.setdefault("assumptions", []).extend(deepcopy(patch.get("added_assumptions", [])))
    result.setdefault("unresolved_questions", []).extend(deepcopy(patch.get("added_unresolved_questions", [])))
    return result


def _contract_base(
    *,
    draft: dict[str, Any],
    module_id: str,
    action: str,
    name: str,
    function_kind: str,
    purpose: str,
    capability_ids: list[str],
    exported: bool = False,
    public_api_role: str = "",
) -> dict[str, Any]:
    visibility = "public" if exported else "internal"
    return {
        "function_id": f"fn:{module_id}:{action}",
        "name": name,
        "module_id": module_id,
        "function_kind": function_kind,
        "coder_function_type": "ALGORITHM",
        "visibility": visibility,
        "api_surface": "public" if exported else "module_internal",
        "exported": exported,
        "export_reason": "Exported by module artifact plan." if exported else "",
        "public_api_role": public_api_role if exported else "",
        "grouping_hint": module_id,
        "purpose": purpose,
        "capability_ids": capability_ids,
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "trace_ref_keys": [f"decision:function:{module_id}:{action}"],
        "status": "inferred",
    }


def _artifact_function_kind(name: str) -> tuple[str, str, str]:
    if name == "main":
        return "public_api", "ENTRYPOINT", "runtime_entrypoint"
    if name.endswith(("_create", "_destroy", "_start", "_run", "_serve", "_stop")):
        role = "runtime_run" if name.endswith(("_run", "_serve")) else f"runtime_{name.rsplit('_', 1)[-1]}"
        return "resource_lifecycle", "ALGORITHM", role
    if "decode" in name or "decoder" in name:
        return "parser", "ALGORITHM", "decoder"
    if "encode" in name or "encoder" in name:
        return "serializer", "ALGORITHM", "encoder"
    if any(word in name for word in ("route", "publish", "subscribe", "handle", "dispatch")):
        return "handler", "ALGORITHM", "module_boundary_operation"
    return "public_api", "ALGORITHM", "module_boundary_operation"


def fallback_function_inventory(draft: dict[str, Any], module_artifact: dict[str, Any]) -> dict[str, Any]:
    protocol = str(draft.get("protocol_name", "protocol"))
    module_id = str(module_artifact.get("module_id", "module"))
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    owned = [str(cap) for cap in module_artifact.get("owned_capabilities", [])]
    artifact_functions = [
        item
        for item in module_artifact.get("artifacts", [])
        if isinstance(item, dict) and str(item.get("kind", "")).upper() == "FUNC" and str(item.get("name", "")).strip()
    ]
    if artifact_functions:
        functions = []
        for artifact in artifact_functions:
            name = str(artifact.get("name", "")).strip()
            function_kind, coder_function_type, public_api_role = _artifact_function_kind(name)
            action = _safe_id(name.removeprefix(f"{_safe_id(protocol)}_")) or _safe_id(name)
            item = _contract_base(
                draft=draft,
                module_id=module_id,
                action=action,
                name=name,
                function_kind=function_kind,
                purpose=str(artifact.get("role", "")) or f"Implement artifact {name}.",
                capability_ids=owned[:1],
                exported=True,
                public_api_role=public_api_role,
            )
            item["coder_function_type"] = coder_function_type
            functions.append(item)
        return {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": f"candidate:function_inventory:{module_id}",
            "producer": _producer("5.4b_function_inventory", "function_inventory_candidate_prompt"),
            "module_id": module_id,
            "functions": functions,
            "assumptions": [],
            "unresolved_questions": [],
        }
    public_roles: list[str] = []
    exported_caps = owned
    functions = [
        _contract_base(draft=draft, module_id=module_id, action="create", name=f"{prefix}_create", function_kind="resource_lifecycle", purpose=f"Allocate and initialize the {module_id} module context.", capability_ids=owned[:1], exported="runtime_create" in public_roles, public_api_role="runtime_create"),
        _contract_base(draft=draft, module_id=module_id, action="destroy", name=f"{prefix}_destroy", function_kind="resource_lifecycle", purpose=f"Release resources owned by the {module_id} module context.", capability_ids=owned[:1], exported="runtime_destroy" in public_roles, public_api_role="runtime_destroy"),
    ]
    for action in ("start", "run"):
        role = f"runtime_{action}"
        if role in public_roles:
            functions.append(
                _contract_base(
                    draft=draft,
                    module_id=module_id,
                    action=action,
                    name=f"{prefix}_{action}",
                    function_kind="public_api",
                    purpose=f"{action.capitalize()} the {module_id} key flow runtime boundary.",
                    capability_ids=exported_caps,
                    exported=True,
                    public_api_role=role,
                )
            )
    for index, role in enumerate(public_roles or ["module_boundary_operation"]):
        if role in LIFECYCLE_ROLES:
            continue
        action = _safe_id(role) or f"public_{index + 1}"
        functions.append(
            _contract_base(
                draft=draft,
                module_id=module_id,
                action=action,
                name=f"{prefix}_{action}",
                function_kind="public_api",
                purpose=f"Public module API for {role}.",
                capability_ids=exported_caps,
                exported=True,
                public_api_role=role,
            )
        )
    if any(cap in owned for cap in {"message_decode"}) or any("decode" in cap or "framing" in cap for cap in owned):
        functions.append(_contract_base(draft=draft, module_id=module_id, action="decode_message", name=f"{prefix}_decode_message", function_kind="parser", purpose="Decode protocol message data owned by this module.", capability_ids=[cap for cap in owned if "decode" in cap or "framing" in cap]))
    if any(cap in owned for cap in {"message_encode"}) or any("encode" in cap or "framing" in cap for cap in owned):
        functions.append(_contract_base(draft=draft, module_id=module_id, action="encode_message", name=f"{prefix}_encode_message", function_kind="serializer", purpose="Encode protocol message data owned by this module.", capability_ids=[cap for cap in owned if "encode" in cap or "framing" in cap]))
    if any(cap in owned for cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}):
        functions.append(_contract_base(draft=draft, module_id=module_id, action="dispatch", name=f"{prefix}_dispatch", function_kind="handler", purpose="Dispatch protocol semantics owned by this module.", capability_ids=[cap for cap in owned if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}]))
        for handler in draft.get("handler_matrix", []):
            if not isinstance(handler, dict) or str(handler.get("owner_module_id", "")) != module_id:
                continue
            surface = str(handler.get("trigger") or handler.get("handler_id") or "surface")
            surface_id = _safe_id(surface)
            item = _contract_base(draft=draft, module_id=module_id, action=f"handle_{surface_id}", name=f"{prefix}_handle_{surface_id}", function_kind="handler", purpose=f"Handle target-scope surface unit {surface}.", capability_ids=[cap for cap in owned if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}])
            item["covers_handler_ids"] = [str(handler.get("handler_id", ""))]
            functions.append(item)
    return {
        "schema_version": "function_inventory_candidate/v2",
        "candidate_id": f"candidate:function_inventory:{module_id}",
        "producer": _producer("5.4b_function_inventory", "function_inventory_candidate_prompt"),
        "module_id": module_id,
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
    result.setdefault("accepted_stage_artifacts", []).append("5.4b_function_inventory")
    return result


def apply_function_inventory_repair_patch(candidate: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(candidate)
    by_id = {
        str(function.get("function_id", "")): function
        for function in result.get("functions", [])
        if isinstance(function, dict)
    }
    for update in patch.get("updated_functions", []):
        function = by_id.get(str(update.get("function_id", "")))
        if not function:
            continue
        for key in ("purpose", "grouping_hint", "status"):
            if key in update:
                function[key] = update[key]
    result.setdefault("functions", []).extend(deepcopy(patch.get("added_functions", [])))
    result.setdefault("assumptions", []).extend(deepcopy(patch.get("added_assumptions", [])))
    result.setdefault("unresolved_questions", []).extend(deepcopy(patch.get("added_unresolved_questions", [])))
    return result


def _module_functions(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source = functions if functions is not None else draft.get("function_contracts", [])
    return [item for item in source if isinstance(item, dict) and str(item.get("module_id")) == module_id]


def _known_type_refs(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("type_id", "")) for item in draft.get("canonical_types", []) if isinstance(item, dict)} | set(SYSTEM_TYPE_IDS)


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
                and str(requirement.get("requirement_kind", "cross_module_service")) in {"cross_module_service", "external_runtime_service"}
                and str(requirement.get("service_requirement_id", "")).strip()
            ):
                result.append(str(requirement["service_requirement_id"]))
    return sorted(set(result))


def fallback_function_signatures(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]] | None = None, *, batch_index: int = 0, batch_size: int = 0) -> dict[str, Any]:
    updates = []
    for function in _module_functions(draft, module_id, functions):
        signature, params, _return_type = _default_signature(function, module_id, str(draft.get("protocol_name", "protocol")))
        updates.append(
            {
                "function_id": function.get("function_id"),
                "signature": {
                    "raw": signature["raw"],
                    "name": signature["name"],
                    "storage_class": "static" if str(function.get("visibility")) == "static" else "none",
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
        "producer": _producer("5.4c_signature_planning", "function_signature_patch_prompt"),
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size or len(updates)},
        "function_signature_updates": updates,
        "assumptions": [],
        "unresolved_questions": [],
    }


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
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4c_signature_planning")
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
        "producer": _producer("5.4d_behavior_contract", "function_behavior_contract_patch_prompt"),
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
        function["forbidden_symbols"] = update["forbidden_symbols"]
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4d_behavior_contract")
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
        "producer": _producer("5.4e_wire_access_binding", "wire_access_binding_patch_prompt"),
        "wire_mapping_entries": entries,
        "access_path_entries": access,
        "function_binding_updates": list(binding_by_function.values()),
        "forbidden_symbols": [],
        "assumptions": [],
        "unresolved_questions": [],
    }


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
                "source_fact_ids": entry.get("trace_ref_keys", []),
            },
        )
        access_id = access_by_function_field.get((str(entry.get("function_id", "")), field_id), {}).get("access_path_id", "")
        current["access_path_id"] = access_id or current["access_path_id"]
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
                }
                for wire_id in update.get("wire_mapping_ids", [])
            ]
            function["access_paths"] = update.get("access_path_ids", [])
    result.setdefault("unresolved_questions", []).extend(patch.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4e_wire_access_binding")
    return result


def fallback_calls_allowed(draft: dict[str, Any], functions: list[dict[str, Any]] | None = None, *, batch_index: int = 0, batch_size: int = 0) -> dict[str, Any]:
    functions = functions if functions is not None else [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    by_module_kind: dict[tuple[str, str], list[str]] = {}
    all_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    for function in all_functions:
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
                        "call_kind": "utility" if kind not in {"handler", "public_api"} else "handler_dispatch",
                        "required": False,
                        "service_requirement_ids": [],
                        "call_reason": "deterministic conservative fallback",
                        "param_bindings": [],
                        "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
                        "failure_behavior": "ignore",
                        "trace_ref_keys": [],
                        "status": "inferred",
                    }
                    for call in calls
                    if call and call != function.get("function_id")
                ],
            }
        )
    return {
        "schema_version": "calls_allowed_candidate/v2",
        "candidate_id": "candidate:calls_allowed:deterministic",
        "producer": _producer("5.4f_call_planning", "calls_allowed_candidate_prompt"),
        "call_updates": updates,
        "unresolved_service_requirements": _service_requirement_ids(functions),
        "assumptions": [],
        "unresolved_questions": [],
    }


def merge_calls_allowed(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(draft)
    updates = {str(item.get("caller_function_id")): item for item in candidate.get("call_updates", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        update = updates.get(str(function.get("function_id")))
        if update:
            function["calls_allowed"] = [edge["callee_function_id"] for edge in update.get("calls_allowed", [])]
            function["call_contracts"] = update.get("calls_allowed", [])
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.4f_call_planning")
    return result


def fallback_file_layout(draft: dict[str, Any]) -> dict[str, Any]:
    protocol = str(draft.get("protocol_name", "protocol"))
    files = []
    assignments = []
    for module in draft.get("module_artifacts", []):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", "module"))
        source_path = f"{protocol}/{module_id}/{module_id}.c"
        file_id = f"file:{source_path[:-2]}"
        header_path = f"{protocol}/{module_id}/{module_id}.h"
        module_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict) and str(item.get("module_id")) == module_id]
        public_functions = [str(item.get("function_id")) for item in module_functions if str(item.get("visibility", "public")).lower() == "public"]
        imports_allowed = [
            f"file:{protocol}/{dep.get('owner_module_id')}/{dep.get('owner_module_id')}"
            for function in module_functions
            for dep in function.get("signature_dependencies", [])
            if isinstance(dep, dict) and dep.get("owner_module_id") and dep.get("owner_module_id") != module_id
        ]
        imports_allowed = sorted({item for item in imports_allowed if item})
        files.append(
            {
                "file_id": file_id,
                "source_path": source_path,
                "header_path": header_path,
                "module_id": module_id,
                "kind": "source_header_pair",
                "responsibility": f"Implement and declare the {module_id} module contract.",
                "exports_function_ids": public_functions,
                "implements_function_ids": [str(item.get("function_id")) for item in module_functions],
                "exports_type_ids": [],
                "imports_allowed": imports_allowed,
                "trace_ref_keys": module.get("source_fact_ids", []),
                "status": "inferred",
            }
        )
        for function in module_functions:
            visibility = str(function.get("visibility", "public")).lower()
            assignments.append(
                {
                    "function_id": function.get("function_id"),
                    "implementation_file_id": file_id,
                    "declaration_file_id": file_id if visibility == "public" else "",
                    "visibility": str(function.get("visibility", "public")),
                    "reason": "Deterministic module source/header placement.",
                    "status": "inferred",
                }
            )
    return {
        "schema_version": "file_layout_candidate/v2",
        "candidate_id": "candidate:file_layout:deterministic",
        "producer": _producer("5.5_file_layout", "file_layout_candidate_prompt"),
        "files": files,
        "function_file_assignments": assignments,
        "assumptions": [],
        "unresolved_questions": [],
    }


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
    assignments = {str(item.get("function_id")): item for item in candidate.get("function_file_assignments", []) if isinstance(item, dict)}
    for function in result.get("function_contracts", []):
        assignment = assignments.get(str(function.get("function_id")))
        if assignment:
            function["file_id"] = assignment.get("implementation_file_id", "")
            function["declared_in"] = assignment.get("declaration_file_id", "")
    result.setdefault("unresolved_questions", []).extend(candidate.get("unresolved_questions", []))
    result.setdefault("accepted_stage_artifacts", []).append("5.5_file_layout")
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
        if any(name.endswith(suffix) for suffix in suffixes):
            return str(function.get("function_id", ""))
    return f"fn:{module_id}:{action}"


def fallback_runtime_entrypoint(draft: dict[str, Any]) -> dict[str, Any]:
    module_id = _key_flow_module_id(draft)
    lifecycle = {action: _find_lifecycle_function_id(draft, module_id, action) for action in ("create", "start", "run", "destroy")}
    return {
        "schema_version": "runtime_entrypoint_candidate/v1",
        "candidate_id": "candidate:runtime_entrypoint:deterministic",
        "producer": _producer("5.4g_runtime_entrypoint_candidate", "runtime_entrypoint_candidate_prompt"),
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
    result.setdefault("accepted_stage_artifacts", []).append("5.4g_runtime_entrypoint_candidate")
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
