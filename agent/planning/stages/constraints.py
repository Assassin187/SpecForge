from __future__ import annotations

from typing import Any

from ..schemas.engineering_constraints import SCHEMA_VERSION


def _value(profile: dict[str, Any], key: str, default: str = "unknown") -> str:
    field = profile.get(key, {})
    if isinstance(field, dict):
        return str(field.get("value", default))
    return str(field or default)


def _capability_ids(profile: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in profile.get("required_capabilities", []):
        if isinstance(item, dict) and item.get("capability_id"):
            result.add(str(item["capability_id"]))
    return result


def _constraint(
    *,
    constraint_id: str,
    triggered_by: list[str],
    affected_capabilities: list[str],
    obligation: str,
    severity: str,
    rationale: str,
    validation_rule: str,
) -> dict[str, Any]:
    return {
        "constraint_id": constraint_id,
        "triggered_by": triggered_by,
        "affected_capabilities": affected_capabilities,
        "obligation": obligation,
        "severity": severity,
        "rationale": rationale,
        "validation_rule": validation_rule,
    }


def activate_constraints(protocol_profile: dict[str, Any]) -> dict[str, Any]:
    capabilities = _capability_ids(protocol_profile)
    constraints: list[dict[str, Any]] = []
    if _value(protocol_profile, "transport_shape") == "stream":
        constraints.append(
            _constraint(
                constraint_id="stream_requires_incremental_decode",
                triggered_by=["protocol_profile.transport_shape"],
                affected_capabilities=["connection_buffering", "incremental_message_framing"],
                obligation="Stream transports must define buffering and incremental message boundary handling.",
                severity="high",
                rationale="Byte-stream protocols can fragment or coalesce protocol messages.",
                validation_rule="function contracts must include parser/framing coverage for target-scope wire fields",
            )
        )
    if _value(protocol_profile, "timing_model") == "timer_driven":
        constraints.append(
            _constraint(
                constraint_id="timer_manager_required",
                triggered_by=["protocol_profile.timing_model"],
                affected_capabilities=["timer_source", "timeout_handling"],
                obligation="Timer-governed behavior must have explicit timeout handling ownership.",
                severity="medium",
                rationale="Timer facts affect state transitions and resource lifecycle.",
                validation_rule="implementation_plan must assign timer capabilities to at least one module contract",
            )
        )
    if _value(protocol_profile, "routing_intensity") in {"medium", "high"} and _value(protocol_profile, "resource_intensity") in {"medium", "high"}:
        constraints.append(
            _constraint(
                constraint_id="routing_resource_split",
                triggered_by=["protocol_profile.routing_intensity", "protocol_profile.resource_intensity"],
                affected_capabilities=["routing_dispatch", "resource_ownership"],
                obligation="Routing behavior and resource ownership must be explicitly assigned and not lost in a generic app module.",
                severity="medium",
                rationale="Routing/resource-heavy protocols need clear state and dispatch boundaries for downstream code generation.",
                validation_rule="selected architecture must cover routing/resource capabilities without ownership conflicts",
            )
        )
    if "recovery_cleanup_policy" in capabilities:
        constraints.append(
            _constraint(
                constraint_id="persistent_session_store",
                triggered_by=["protocol_profile.required_capabilities.recovery_cleanup_policy"],
                affected_capabilities=["session_state_ownership", "recovery_cleanup_policy"],
                obligation="Persistent state must define ownership, recovery, and cleanup responsibilities.",
                severity="medium",
                rationale="Persistent state cannot be safely generated without lifecycle contracts.",
                validation_rule="implementation_plan.resource_lifecycle must reference persistent state owner",
            )
        )
    if _value(protocol_profile, "failure_semantics") == "close_connection_on_protocol_error":
        constraints.append(
            _constraint(
                constraint_id="connection_close_error_policy",
                triggered_by=["protocol_profile.failure_semantics"],
                affected_capabilities=["protocol_error_policy", "connection_termination"],
                obligation="Protocol errors that close connections must share a terminal error path.",
                severity="high",
                rationale="Close-on-error semantics affect handlers, transport, and recovery behavior.",
                validation_rule="function contracts must include error behavior for malformed target-scope messages",
            )
        )
    constraints.append(
        _constraint(
            constraint_id="canonical_ownership_required",
            triggered_by=["always"],
            affected_capabilities=["canonical_type_ownership"],
            obligation="Each public type and shared state object must have one canonical owner.",
            severity="high",
            rationale="Coder-compatible specs must avoid duplicate public type definitions.",
            validation_rule="compiled specs must not duplicate public type owners",
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "constraints": constraints,
        "constraint_count": len(constraints),
    }
