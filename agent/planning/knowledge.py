from __future__ import annotations

from typing import Any

from .facts import fact_id, infer_spec_version, protocol_name, slugify
from .models import EngineeringRule, NormalizedCharacteristics, OpenAssumption


def _get_value(raw: Any, default: str = "unknown") -> str:
    if isinstance(raw, dict) and raw.get("value") is not None:
        return str(raw.get("value")).strip().lower() or default
    if isinstance(raw, str):
        return raw.strip().lower() or default
    return default


def _names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                out.append(name)
        elif str(item).strip():
            out.append(str(item).strip())
    return out


def _surface_names(facts: dict[str, Any]) -> list[str]:
    minimum = facts.get("minimum_v1", {})
    if isinstance(minimum, dict):
        names = _names(minimum.get("must_support_surface", []))
        if names:
            return names
    message_model = facts.get("message_model", {})
    if isinstance(message_model, dict):
        return _names(message_model.get("surface_catalog", []))
    return []


def _target_roles(facts: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    interaction = facts.get("interaction_model", {})
    if isinstance(interaction, dict):
        for item in interaction.get("roles", []) if isinstance(interaction.get("roles", []), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            summary = str(item.get("summary", "")).lower()
            target_markers = ("generated target", "target is", "target should", "generated target is")
            if name and any(marker in summary for marker in target_markers):
                roles.append(name)
        if not roles:
            roles = _names(interaction.get("roles", []))
    return [role.upper() for role in roles] or ["IMPLEMENTATION"]


def normalize_characteristics(facts: dict[str, Any]) -> NormalizedCharacteristics:
    name = protocol_name(facts)
    slug = slugify(name)
    transport = facts.get("transport", {}) if isinstance(facts.get("transport", {}), dict) else {}
    interaction = facts.get("interaction_model", {}) if isinstance(facts.get("interaction_model", {}), dict) else {}
    message = facts.get("message_model", {}) if isinstance(facts.get("message_model", {}), dict) else {}
    state = facts.get("state_model", {}) if isinstance(facts.get("state_model", {}), dict) else {}
    routing = facts.get("routing_model", {}) if isinstance(facts.get("routing_model", {}), dict) else {}
    resources = facts.get("resource_model", {}) if isinstance(facts.get("resource_model", {}), dict) else {}
    errors = facts.get("error_and_limits", {}) if isinstance(facts.get("error_and_limits", {}), dict) else {}
    minimum = facts.get("minimum_v1", {}) if isinstance(facts.get("minimum_v1", {}), dict) else {}

    network_stack = _get_value(transport.get("network_stack"))
    if network_stack in {"tcp", "tls", "stream"}:
        transport_shape = "stream"
    elif network_stack in {"udp", "dtls", "datagram"}:
        transport_shape = "datagram"
    else:
        transport_shape = "unknown"

    style = _get_value(interaction.get("style"))
    interaction_models: list[str] = []
    surface_names = [name.lower() for name in _surface_names(facts)]
    if style != "unknown":
        interaction_models.append(style)
    if {"publish", "subscribe"} & set(surface_names) or routing.get("matching_rules"):
        interaction_models.append("publish_subscribe")
    if any("response" == str(item.get("kind", "")).lower() for item in message.get("surface_catalog", []) if isinstance(item, dict)):
        interaction_models.append("request_response")
    interaction_models = list(dict.fromkeys(interaction_models)) or ["unknown"]

    role_names = [role.lower() for role in _names(interaction.get("roles", []))]
    if {"client", "server"} <= set(role_names) or {"client", "broker"} <= set(role_names):
        role_model = "client_server"
    elif len(role_names) > 1:
        role_model = "multi_role"
    else:
        role_model = role_names[0] if role_names else "unknown"

    statefulness = "stateful" if state.get("state_nodes") or resources.get("resource_objects") else "stateless"
    framing_model = _get_value(message.get("framing"), "unknown")
    routing_required = bool(routing.get("dispatch_keys") or routing.get("dispatch_targets") or routing.get("matching_rules"))
    resource_ownership = _names(resources.get("resource_objects", []))
    timer_requirements = _names(state.get("timers_and_constants", []))
    error_semantics = [
        str(item.get("condition", item.get("summary", ""))).strip()
        for item in errors.get("error_matrix", []) if isinstance(item, dict)
    ]
    minimum_scope = _surface_names(facts) + _names(minimum.get("must_support_state_behaviors", []))
    deferred_features = _names(minimum.get("may_defer_features", []))

    return NormalizedCharacteristics(
        protocol_name=name.upper(),
        protocol_slug=slug,
        spec_version=infer_spec_version(facts),
        target_roles=_target_roles(facts),
        transport_shape=transport_shape,
        connection_model=_get_value(transport.get("connection_model")),
        interaction_models=interaction_models,
        role_model=role_model,
        statefulness=statefulness,
        framing_model=framing_model,
        routing_required=routing_required,
        resource_ownership=resource_ownership,
        timer_requirements=timer_requirements,
        error_semantics=error_semantics,
        minimum_scope=minimum_scope,
        deferred_features=deferred_features,
        fact_refs=[
            fact_id("protocol_meta"),
            fact_id("transport"),
            fact_id("interaction_model"),
            fact_id("message_model"),
            fact_id("state_model"),
            fact_id("routing_model"),
            fact_id("resource_model"),
            fact_id("error_and_limits"),
            fact_id("minimum_v1"),
        ],
    )


def activate_engineering_rules(characteristics: NormalizedCharacteristics) -> list[EngineeringRule]:
    rules: list[EngineeringRule] = []

    def add(rule_id: str, name: str, trigger: str, constraints: list[str], fact_refs: list[str]) -> None:
        rules.append(EngineeringRule(rule_id, name, trigger, constraints, fact_refs))

    if characteristics.transport_shape == "stream":
        add(
            "RULE_STREAM_TRANSPORT",
            "Stream transport runtime",
            "transport_shape == stream",
            [
                "Maintain connection lifecycle and peer disconnect handling.",
                "Support partial reads/writes through receive buffering and outbound queues.",
                "Perform incremental framing above the byte stream.",
            ],
            [fact_id("transport")],
        )
    if characteristics.transport_shape == "datagram":
        add(
            "RULE_DATAGRAM_TRANSPORT",
            "Datagram transport runtime",
            "transport_shape == datagram",
            [
                "Preserve per-datagram message boundaries.",
                "Validate each datagram independently.",
                "Enable retransmission or timers only when facts require them.",
            ],
            [fact_id("transport")],
        )
    if characteristics.connection_model == "connection_oriented":
        add(
            "RULE_CONNECTION_ORIENTED",
            "Connection-oriented lifecycle",
            "connection_model == connection_oriented",
            [
                "Represent accepted peers as connection resources.",
                "Clean up connection-owned resources on close or protocol termination.",
            ],
            [fact_id("transport.connection_model")],
        )
    if "publish_subscribe" in characteristics.interaction_models:
        add(
            "RULE_PUBLISH_SUBSCRIBE",
            "Publish-subscribe ownership",
            "interaction model includes publish_subscribe",
            [
                "Separate subscription ownership from message dispatch.",
                "Route publications through topic/filter matching.",
                "Remove subscriber state during session cleanup.",
            ],
            [fact_id("interaction_model"), fact_id("routing_model")],
        )
    if "request_response" in characteristics.interaction_models:
        add(
            "RULE_REQUEST_RESPONSE",
            "Request-response handling",
            "surface catalog contains responses",
            [
                "Keep request dispatch separate from response construction.",
                "Use correlation or timeout state only when facts require it.",
            ],
            [fact_id("interaction_model"), fact_id("message_model.surface_catalog")],
        )
    if characteristics.statefulness == "stateful":
        add(
            "RULE_STATEFUL_SESSION",
            "Stateful session management",
            "state model or resource model is non-empty",
            [
                "Make state ownership explicit.",
                "Reject invalid state transitions according to facts.",
                "Clean abnormal termination state deterministically.",
            ],
            [fact_id("state_model"), fact_id("resource_model")],
        )
    if characteristics.framing_model in {"binary", "text", "line", "record"}:
        add(
            "RULE_FRAMING_CODEC",
            "Framing and codec separation",
            "framing model is explicit",
            [
                "Keep wire parsing and serialization separate from transport IO.",
                "Expose structured protocol units to upper layers.",
                "Preserve partial-frame behavior when transport requires buffering.",
            ],
            [fact_id("message_model.framing")],
        )
    if characteristics.routing_required:
        add(
            "RULE_ROUTING",
            "Routing and matching",
            "routing model has dispatch or matching requirements",
            [
                "Centralize dispatch keys and matching rules.",
                "Keep routing data structures independent from wire codec details.",
            ],
            [fact_id("routing_model")],
        )
    if characteristics.timer_requirements:
        add(
            "RULE_TIMERS",
            "Timer policy",
            "facts mention timers or protocol constants",
            [
                "Store timer-related facts in state objects.",
                "Activate enforcement only when facts require runtime timers.",
            ],
            [fact_id("state_model.timers_and_constants")],
        )
    if characteristics.error_semantics:
        add(
            "RULE_ERROR_TERMINATION",
            "Error and termination handling",
            "facts contain error matrix entries",
            [
                "Map protocol errors to explicit actions.",
                "Separate recoverable unsupported-scope behavior from connection termination.",
            ],
            [fact_id("error_and_limits.error_matrix")],
        )
    add(
        "RULE_MINIMUM_SCOPE",
        "Minimum implementation scope",
        "minimum_v1 is present",
        [
            "Only include surfaces and behaviors in the declared minimum scope.",
            "Record deferred features as out of scope instead of silently implementing them.",
        ],
        [fact_id("minimum_v1")],
    )
    return rules


def extract_open_assumptions(facts: dict[str, Any], characteristics: NormalizedCharacteristics) -> list[OpenAssumption]:
    assumptions: list[OpenAssumption] = []
    for idx, question in enumerate(facts.get("open_questions", []) if isinstance(facts.get("open_questions", []), list) else [], 1):
        if not isinstance(question, dict):
            continue
        assumptions.append(
            OpenAssumption(
                assumption_id=f"ASM{idx:03d}",
                missing_or_ambiguous=str(question.get("question", "")).strip(),
                conservative_assumption=str(question.get("suggested_followup", "")).strip()
                or "Keep the behavior explicit as an implementation policy item.",
                engineering_impact=str(question.get("blocking_impact", "unknown")).strip(),
                allowed_in_final_specs=str(question.get("blocking_impact", "")).lower() != "high",
                follow_up=str(question.get("suggested_followup", "")).strip(),
                fact_refs=[fact_id("open_questions")],
            )
        )
    if characteristics.transport_shape == "unknown":
        assumptions.append(
            OpenAssumption(
                assumption_id=f"ASM{len(assumptions) + 1:03d}",
                missing_or_ambiguous="Transport shape is not explicit in protocol facts.",
                conservative_assumption="Generate no transport-specific runtime behavior until facts clarify stream or datagram semantics.",
                engineering_impact="Transport runtime module may remain abstract.",
                allowed_in_final_specs=False,
                follow_up="Add transport facts before coder generation.",
                fact_refs=[fact_id("transport")],
            )
        )
    return assumptions
