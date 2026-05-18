from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..adapters.target_profile import target_directive_id
from ..schemas.protocol_profile import SCHEMA_VERSION


def _value_of(value: Any, default: str = "unknown") -> str:
    if isinstance(value, dict):
        raw = value.get("value", default)
        return str(raw) if raw is not None else default
    if value is None:
        return default
    return str(value)


def _uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _refs(ir: dict[str, Any], prefix: str) -> dict[str, list[str]]:
    normalization = ir.get("normalization_index", {})
    fact_by_path = normalization.get("fact_id_by_path", {}) if isinstance(normalization, dict) else {}
    refs_by_fact = normalization.get("evidence_refs_by_fact_id", {}) if isinstance(normalization, dict) else {}
    source_fact_ids: list[str] = []
    evidence_refs: list[str] = []
    for path, fact_id in fact_by_path.items():
        if path == prefix or str(path).startswith(prefix + "."):
            source_fact_ids.append(str(fact_id))
            refs = refs_by_fact.get(fact_id, [])
            if isinstance(refs, list):
                evidence_refs.extend(str(ref) for ref in refs if str(ref).strip())
    return {
        "source_fact_ids": _uniq(source_fact_ids),
        "evidence_refs": _uniq(evidence_refs),
    }


def _field(value: Any, refs: dict[str, list[str]] | None = None, *, target_directive_ids: list[str] | None = None) -> dict[str, Any]:
    refs = refs or {"source_fact_ids": [], "evidence_refs": []}
    return {
        "value": value,
        "source_fact_ids": list(refs.get("source_fact_ids", [])),
        "target_directive_ids": list(target_directive_ids or []),
        "evidence_refs": list(refs.get("evidence_refs", [])),
        "origin": "deterministic",
        "assumptions": [],
    }


def _capability(capability_id: str, category: str, rationale: str, refs: dict[str, list[str]], *, target_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "category": category,
        "source_fact_ids": list(refs.get("source_fact_ids", [])),
        "target_directive_ids": list(target_ids or []),
        "evidence_refs": list(refs.get("evidence_refs", [])),
        "rationale": rationale,
        "origin": "deterministic",
    }


def _internal_patch_value(key: str, value: Any) -> str:
    raw = str(value)
    aliases = {
        "statefulness": {
            "connection_stateful": "connection_state",
            "session_stateful": "session_state",
            "transaction_stateful": "session_state",
            "protocol_state_machine": "session_state",
            "mixed": "session_state",
        },
        "routing_intensity": {"none": "low"},
        "resource_intensity": {"none": "low"},
        "failure_semantics": {
            "simple_error": "reply_with_error",
            "recoverable_error": "reply_with_error",
            "retryable": "reply_with_error",
            "fatal_error": "close_connection_on_protocol_error",
            "connection_closing": "close_connection_on_protocol_error",
            "mixed_recoverability": "mixed",
            "timeout_sensitive": "mixed",
        },
    }
    return aliases.get(key, {}).get(raw, raw)


def build_protocol_profile(planning_ir: dict[str, Any]) -> dict[str, Any]:
    facts = planning_ir.get("protocol_facts", {})
    target = planning_ir.get("target_directives", {}).get("directives", {})
    transport = facts.get("transport", {}) if isinstance(facts.get("transport"), dict) else {}
    interaction = facts.get("interaction_model", {}) if isinstance(facts.get("interaction_model"), dict) else {}
    message = facts.get("message_model", {}) if isinstance(facts.get("message_model"), dict) else {}
    state = facts.get("state_model", {}) if isinstance(facts.get("state_model"), dict) else {}
    routing = facts.get("routing_model", {}) if isinstance(facts.get("routing_model"), dict) else {}
    resource = facts.get("resource_model", {}) if isinstance(facts.get("resource_model"), dict) else {}
    errors = facts.get("error_and_limits", {}) if isinstance(facts.get("error_and_limits"), dict) else {}
    minimum_v1 = facts.get("minimum_v1", {}) if isinstance(facts.get("minimum_v1"), dict) else {}

    network_stack = _value_of(transport.get("network_stack"))
    connection_model = _value_of(transport.get("connection_model"))
    if network_stack in {"tcp", "tls", "stream"} or connection_model in {"connection_oriented", "control_plus_data"}:
        transport_shape = "stream"
    elif network_stack in {"udp", "datagram"} or connection_model == "connectionless":
        transport_shape = "datagram"
    elif network_stack == "unknown" and connection_model == "unknown":
        transport_shape = "unknown"
    else:
        transport_shape = "mixed"

    state_nodes = state.get("state_nodes", []) if isinstance(state.get("state_nodes"), list) else []
    transitions = state.get("transitions", []) if isinstance(state.get("transitions"), list) else []
    resources = resource.get("resource_objects", []) if isinstance(resource.get("resource_objects"), list) else []
    persistence = bool(resource.get("persistence_scope"))
    if persistence:
        statefulness = "persistent_state"
    elif state_nodes or transitions:
        statefulness = "session_state"
    elif connection_model == "connection_oriented":
        statefulness = "connection_state"
    else:
        statefulness = "stateless"

    dispatch_keys = routing.get("dispatch_keys", []) if isinstance(routing.get("dispatch_keys"), list) else []
    routing_intensity = "high" if len(dispatch_keys) >= 2 else "medium" if dispatch_keys else "low"
    resource_intensity = "high" if len(resources) >= 2 else "medium" if resources else "low"
    error_matrix = errors.get("error_matrix", []) if isinstance(errors.get("error_matrix"), list) else []
    error_text = " ".join(str(item.get("required_action", "")) for item in error_matrix if isinstance(item, dict)).lower()
    if "close" in error_text:
        failure_semantics = "close_connection_on_protocol_error"
    elif "reply" in error_text or "response" in error_text or "ack" in error_text:
        failure_semantics = "reply_with_error"
    elif error_matrix:
        failure_semantics = "mixed"
    else:
        failure_semantics = "unknown"
    timing_model = "timer_driven" if state.get("timers_and_constants") else "event_driven"

    transport_refs = _refs(planning_ir, "transport")
    message_refs = _refs(planning_ir, "message_model")
    state_refs = _refs(planning_ir, "state_model")
    routing_refs = _refs(planning_ir, "routing_model")
    resource_refs = _refs(planning_ir, "resource_model")
    error_refs = _refs(planning_ir, "error_and_limits")
    surface_refs = _refs(planning_ir, "minimum_v1.must_support_surface")
    timing_refs = _refs(planning_ir, "state_model.timers_and_constants")
    role_directive = target_directive_id("target_role")
    scope_directive = target_directive_id("scope")
    language_directive = target_directive_id("language")
    runtime_directive = target_directive_id("runtime")

    transport_caps = ["transport_io"]
    if transport_shape == "stream":
        transport_caps.extend(["connection_lifecycle", "connection_buffering"])
    elif transport_shape == "datagram":
        transport_caps.extend(["datagram_io", "peer_address_handling"])
    else:
        transport_caps.append("transport_adapter")

    message_caps = ["message_decode"]
    surface_catalog = message.get("surface_catalog", []) if isinstance(message.get("surface_catalog"), list) else []
    entries = message.get("message_or_command_entries", []) if isinstance(message.get("message_or_command_entries"), list) else []
    if surface_catalog or entries:
        message_caps.append("message_encode")
    if transport_shape == "stream":
        message_caps.append("incremental_message_framing")
    if transport_shape == "datagram":
        message_caps.append("datagram_message_framing")

    state_caps: list[str] = []
    if state_nodes or transitions:
        state_caps.extend(["state_machine", "state_transition_validation"])
    if statefulness in {"connection_state", "session_state", "persistent_state"}:
        state_caps.append("session_state_ownership")
    if statefulness == "persistent_state":
        state_caps.append("recovery_cleanup_policy")

    routing_caps: list[str] = []
    if dispatch_keys:
        routing_caps.append("routing_dispatch")
    if resources:
        routing_caps.append("resource_ownership")

    error_caps = ["protocol_error_policy"]
    if failure_semantics == "close_connection_on_protocol_error":
        error_caps.append("connection_termination")
    elif failure_semantics == "reply_with_error":
        error_caps.append("error_response_encoding")

    timing_caps = ["timer_source", "timeout_handling"] if timing_model == "timer_driven" else []
    required_ids = _uniq(
        transport_caps
        + message_caps
        + state_caps
        + routing_caps
        + error_caps
        + timing_caps
        + ["semantic_dispatch", "role_composition", "canonical_type_ownership"]
    )
    capability_records: list[dict[str, Any]] = []
    for cap in transport_caps:
        capability_records.append(_capability(cap, "transport", f"Required by transport shape {transport_shape}", transport_refs))
    for cap in message_caps:
        capability_records.append(_capability(cap, "message_framing", "Required for target-scope protocol messages", message_refs or surface_refs))
    for cap in state_caps:
        capability_records.append(_capability(cap, "state", f"Required by statefulness {statefulness}", state_refs))
    for cap in routing_caps:
        capability_records.append(_capability(cap, "routing_resource", "Required by routing/resource model", routing_refs if cap == "routing_dispatch" else resource_refs))
    for cap in error_caps:
        capability_records.append(_capability(cap, "error_policy", f"Required by failure semantics {failure_semantics}", error_refs))
    for cap in timing_caps:
        capability_records.append(_capability(cap, "timing", "Required by timer facts", timing_refs))
    capability_records.append(_capability("semantic_dispatch", "semantics", "Required to map decoded surface units to behavior", surface_refs))
    capability_records.append(
        _capability("role_composition", "target_boundary", "Required to compose the requested target role", {"source_fact_ids": [], "evidence_refs": []}, target_ids=[role_directive, scope_directive])
    )
    capability_records.append(
        _capability("canonical_type_ownership", "types", "Required for coder-facing public type ownership", surface_refs, target_ids=[language_directive])
    )
    by_id = {str(item["capability_id"]): item for item in capability_records}
    capability_records = [by_id[item] for item in required_ids if item in by_id]

    role_value = target.get("target_role", {}).get("value", "") if isinstance(target, dict) else ""
    language_value = target.get("language", {}).get("value", "") if isinstance(target, dict) else ""
    runtime_value = target.get("runtime", {}).get("value", "") if isinstance(target, dict) else ""
    scope_value = target.get("scope", {}).get("value", "") if isinstance(target, dict) else ""
    roles = interaction.get("roles", []) if isinstance(interaction.get("roles"), list) else []
    normalized_roles = _uniq([str(item.get("name", "")).lower() for item in roles if isinstance(item, dict)] + [str(role_value).lower()])

    profile = {
        "schema_version": SCHEMA_VERSION,
        "protocol_name": _field(planning_ir.get("protocol_name", "protocol"), _refs(planning_ir, "protocol_meta")),
        "target_role": _field(role_value, target_directive_ids=[role_directive]),
        "transport_shape": _field(transport_shape, transport_refs),
        "interaction_model": _field(_value_of(interaction.get("style")), _refs(planning_ir, "interaction_model.style")),
        "statefulness": _field(statefulness, state_refs),
        "routing_intensity": _field(routing_intensity, routing_refs),
        "resource_intensity": _field(resource_intensity, resource_refs),
        "failure_semantics": _field(failure_semantics, error_refs),
        "timing_model": _field(timing_model, timing_refs),
        "minimum_scope": _field(scope_value or "minimum_v1", target_directive_ids=[scope_directive]),
        "implementation_boundary": _field(
            {
                "target_role": role_value,
                "language": language_value,
                "runtime": runtime_value,
                "scope": scope_value,
            },
            target_directive_ids=[role_directive, language_directive, runtime_directive, scope_directive],
        ),
        "normalized_roles": [{"role": role, "origin": "deterministic"} for role in normalized_roles if role],
        "question_summary": {
            "blocking": sum(1 for item in planning_ir.get("unresolved_facts", []) if item.get("blocking_impact") == "high"),
            "assumable": sum(1 for item in planning_ir.get("unresolved_facts", []) if item.get("blocking_impact") in {"low", "medium"}),
            "deferrable": 0,
        },
        "required_surface_units": [
            {
                "surface_id": str(item.get("name", "")),
                "name": str(item.get("name", "")),
                "source_fact_ids": [_refs(planning_ir, f"minimum_v1.must_support_surface.{idx}").get("source_fact_ids", [""])[0]]
                if _refs(planning_ir, f"minimum_v1.must_support_surface.{idx}").get("source_fact_ids")
                else [],
                "evidence_refs": _refs(planning_ir, f"minimum_v1.must_support_surface.{idx}").get("evidence_refs", []),
                "origin": "deterministic",
            }
            for idx, item in enumerate(minimum_v1.get("must_support_surface", []) if isinstance(minimum_v1.get("must_support_surface"), list) else [])
            if isinstance(item, dict) and item.get("name")
        ],
        "capability_groups": {
            "transport": _uniq(transport_caps),
            "message_framing": _uniq(message_caps),
            "state": _uniq(state_caps),
            "routing_resource": _uniq(routing_caps),
            "error_policy": _uniq(error_caps),
            "timing": _uniq(timing_caps),
        },
        "required_capabilities": capability_records,
        "profile_generation_warnings": [],
    }
    return profile


def apply_protocol_profile_patch_candidate(profile: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    patched = deepcopy(profile)
    patch = candidate.get("patch", {}) if isinstance(candidate, dict) else {}
    if not isinstance(patch, dict):
        return patched
    for key in ("interaction_model", "statefulness", "routing_intensity", "resource_intensity", "failure_semantics"):
        if key in patch and isinstance(patched.get(key), dict):
            original = str(patch[key])
            internal_value = _internal_patch_value(key, original)
            patched[key]["value"] = internal_value
            patched[key]["origin"] = "llm_patch_validated"
            note = "Value patched by validated LLM profile patch candidate."
            if internal_value != original:
                note = f"LLM audit value '{original}' normalized to internal profile value '{internal_value}'."
            patched[key].setdefault("assumptions", []).append(note)
    additions = patch.get("capability_additions", [])
    if isinstance(additions, list):
        existing = {str(item.get("capability_id")) for item in patched.get("required_capabilities", []) if isinstance(item, dict)}
        for item in additions:
            if not isinstance(item, dict):
                continue
            cap_id = str(item.get("capability_id", "")).strip()
            if not cap_id or cap_id in existing:
                continue
            record = {
                "capability_id": cap_id,
                "category": str(item.get("category", "llm_suggested")),
                "source_fact_ids": [str(value) for value in item.get("source_fact_ids", [])],
                "target_directive_ids": [str(value) for value in item.get("target_directive_ids", [])],
                "evidence_refs": [str(value) for value in item.get("evidence_refs", [])],
                "rationale": str(item.get("rationale", "Validated LLM capability addition.")),
                "origin": "llm_patch_validated",
            }
            patched.setdefault("required_capabilities", []).append(record)
            existing.add(cap_id)
    return patched
