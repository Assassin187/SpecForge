from __future__ import annotations

from typing import Any

from .models import PlanningIR, ProtocolProfile


def _value_of(value: Any, default: str = "unknown") -> str:
    if isinstance(value, dict):
        raw = value.get("value", default)
        return str(raw) if raw is not None else default
    return str(value) if value is not None else default


def analyze_protocol_profile(planning_ir: PlanningIR) -> ProtocolProfile:
    facts = planning_ir.facts
    transport = facts.get("transport", {})
    interaction = facts.get("interaction_model", {})
    routing = facts.get("routing_model", {})
    resource = facts.get("resource_model", {})
    errors = facts.get("error_and_limits", {})
    minimum_v1 = facts.get("minimum_v1", {})
    state_model = facts.get("state_model", {})

    network_stack = _value_of(transport.get("network_stack"))
    connection_model = _value_of(transport.get("connection_model"))
    style = _value_of(interaction.get("style"))
    has_timers = bool(state_model.get("timers_and_constants"))
    persistence = bool(resource.get("persistence_scope")) or any(
        "persist" in str(item.get("summary", "")).lower() for item in planning_ir.resource_objects if isinstance(item, dict)
    )

    if network_stack in {"tcp", "tls", "stream"} or connection_model in {"connection_oriented", "control_plus_data"}:
        transport_shape = "stream"
    elif network_stack in {"udp", "datagram"} or connection_model == "connectionless":
        transport_shape = "datagram"
    else:
        transport_shape = "mixed"

    if persistence:
        statefulness = "persistent_state"
    elif planning_ir.state_nodes or planning_ir.transitions:
        statefulness = "session_state"
    elif connection_model == "connection_oriented":
        statefulness = "connection_state"
    else:
        statefulness = "stateless"

    routing_intensity = "high" if len(routing.get("dispatch_keys", [])) >= 2 else "medium" if routing.get("dispatch_keys") else "low"
    resource_intensity = "high" if len(planning_ir.resource_objects) >= 2 else "medium" if planning_ir.resource_objects else "low"

    error_actions = " ".join(str(item.get("required_action", "")) for item in planning_ir.error_matrix if isinstance(item, dict)).lower()
    if "close" in error_actions:
        failure_semantics = "close_connection_on_protocol_error"
    elif "reply" in error_actions or "ack" in error_actions:
        failure_semantics = "reply_with_error"
    else:
        failure_semantics = "mixed"

    profile = {
        "protocol_name": planning_ir.protocol_name,
        "target_role": planning_ir.target_profile.target_role,
        "transport_shape": transport_shape,
        "interaction_model": style,
        "statefulness": statefulness,
        "routing_intensity": routing_intensity,
        "resource_intensity": resource_intensity,
        "failure_semantics": failure_semantics,
        "timing_model": "timer_driven" if has_timers else "event_driven",
        "minimum_scope": planning_ir.target_profile.scope or ("minimum_v1" if minimum_v1 else "unknown"),
        "normalized_roles": planning_ir.normalized_roles,
        "blocking_questions": len(planning_ir.open_questions.get("blocking", [])),
        "assumable_questions": len(planning_ir.open_questions.get("assumable", [])),
        "deferrable_questions": len(planning_ir.open_questions.get("deferrable", [])),
        "required_surface_units": [item.get("name") for item in minimum_v1.get("must_support_surface", []) if isinstance(item, dict) and item.get("name")],
        "required_components": [
            "network_runtime" if transport_shape == "stream" else "transport_adapter",
            "protocol_codec",
            "handler_dispatch",
        ],
    }
    return ProtocolProfile(profile)
