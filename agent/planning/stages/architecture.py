from __future__ import annotations

from typing import Any

from ..schemas.architecture import ARCHITECTURE_CANDIDATES_SCHEMA_VERSION, SELECTED_ARCHITECTURE_SCHEMA_VERSION


def _value_of(value: Any, default: str = "") -> str:
    if isinstance(value, dict):
        raw = value.get("value", default)
        return str(raw) if raw is not None else default
    if value is None:
        return default
    return str(value)


def _capability_ids(profile: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in profile.get("required_capabilities", []):
        if isinstance(item, dict) and str(item.get("capability_id", "")).strip():
            result.append(str(item["capability_id"]).strip())
    return result


def _owned(ids: set[str], *capabilities: str) -> list[str]:
    return [cap for cap in capabilities if cap in ids]


def build_architecture_candidates(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    protocol = _value_of(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))).lower()
    target_role = _value_of(profile.get("target_role"), "target").lower()
    capability_ids = set(_capability_ids(profile))
    constraint_ids = [
        str(item.get("constraint_id", "")).strip()
        for item in constraints.get("constraints", [])
        if isinstance(item, dict) and str(item.get("constraint_id", "")).strip()
    ]
    module_templates = [
        {
            "module_id": "transport_runtime",
            "name": "transport_runtime",
            "responsibility": "Own target runtime transport I/O, connection lifecycle, and buffering boundaries.",
            "owned_capabilities": _owned(
                capability_ids,
                "transport_io",
                "connection_lifecycle",
                "connection_buffering",
                "datagram_io",
                "peer_address_handling",
                "transport_adapter",
            ),
            "state_owned": ["connection_registry"],
        },
        {
            "module_id": "protocol_codec",
            "name": "protocol_codec",
            "responsibility": "Own canonical protocol types, message framing, decoding, and encoding.",
            "owned_capabilities": _owned(
                capability_ids,
                "message_decode",
                "message_encode",
                "incremental_message_framing",
                "datagram_message_framing",
                "canonical_type_ownership",
            ),
            "state_owned": [],
        },
        {
            "module_id": "semantic_core",
            "name": "semantic_core",
            "responsibility": "Dispatch decoded messages to target-role behavior, state transitions, timers, and error policy.",
            "owned_capabilities": _owned(
                capability_ids,
                "semantic_dispatch",
                "state_machine",
                "state_transition_validation",
                "protocol_error_policy",
                "connection_termination",
                "error_response_encoding",
                "timer_source",
                "timeout_handling",
            ),
            "state_owned": ["protocol_session_state"],
        },
        {
            "module_id": "resource_store",
            "name": "resource_store",
            "responsibility": "Own target-role resources, routing indexes, and session/resource lifecycle tables.",
            "owned_capabilities": _owned(
                capability_ids,
                "session_state_ownership",
                "resource_ownership",
                "routing_dispatch",
                "recovery_cleanup_policy",
            ),
            "state_owned": ["resource_index"],
        },
        {
            "module_id": f"{protocol}_{target_role}_app",
            "name": f"{protocol}_{target_role}_app",
            "responsibility": "Compose target-role modules into the public planning boundary expected by the coder agent.",
            "owned_capabilities": _owned(capability_ids, "role_composition"),
            "state_owned": [],
        },
    ]
    modules = [item for item in module_templates if item["owned_capabilities"]]
    covered = {cap for module in modules for cap in module["owned_capabilities"]}
    missing = sorted(capability_ids - covered)
    if missing:
        modules.append(
            {
                "module_id": "support_capabilities",
                "name": "support_capabilities",
                "responsibility": "Own capabilities not covered by the deterministic baseline module map.",
                "owned_capabilities": missing,
                "state_owned": [],
                "support_module": True,
            }
        )
    candidate = {
        "candidate_id": "baseline_modular",
        "generation_mode": "deterministic_baseline",
        "modules": [
            {
                "module_id": module["module_id"],
                "name": module["name"],
                "responsibilities": [module["responsibility"]],
                "owned_capabilities": module["owned_capabilities"],
                "consumed_capabilities": [],
                "state_owned": module["state_owned"],
                "dependency_hints": [],
                "support_module": bool(module.get("support_module", False)),
            }
            for module in modules
        ],
        "module_graph_hints": [],
        "constraint_ids": constraint_ids,
        "design_rationale": "Deterministic baseline partitions transport, codec, semantics, resources, and role composition.",
        "tradeoffs": [
            "Conservative module split favors coder compatibility and traceability over highly optimized decomposition.",
            "Dependency hints are left empty in the baseline; final dependency graph is derived later from imports/calls.",
        ],
    }
    return {
        "schema_version": ARCHITECTURE_CANDIDATES_SCHEMA_VERSION,
        "candidates": [candidate],
        "generation_warnings": [],
    }


def select_architecture(candidates: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    selected = candidates.get("candidates", [{}])[0]
    return {
        "schema_version": SELECTED_ARCHITECTURE_SCHEMA_VERSION,
        "selected_candidate_id": selected.get("candidate_id", ""),
        "architecture": selected,
        "selection_reason": "Selected deterministic baseline because LLM architecture search is not enabled yet.",
        "fallback_used": True,
        "coverage_summary": {
            "required_capability_count": len(_capability_ids(profile)),
            "covered_capability_count": len({cap for module in selected.get("modules", []) for cap in module.get("owned_capabilities", [])}),
        },
    }

