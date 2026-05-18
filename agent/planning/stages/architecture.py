from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..schemas.architecture import (
    ARCHITECTURE_CANDIDATES_SCHEMA_VERSION,
    ARCHITECTURE_RANKING_SCHEMA_VERSION,
    SELECTED_ARCHITECTURE_SCHEMA_VERSION,
)


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


def _candidate_owned_capabilities(candidate: dict[str, Any]) -> set[str]:
    if not isinstance(candidate, dict):
        return set()
    return {
        str(cap)
        for module in candidate.get("modules", [])
        if isinstance(module, dict)
        for cap in module.get("owned_capabilities", [])
        if str(cap).strip()
    }


def _field_refs(field: Any) -> dict[str, Any]:
    if not isinstance(field, dict):
        return {"source_fact_count": 0, "target_directive_count": 0, "evidence_refs": []}
    source_fact_ids = field.get("source_fact_ids", [])
    target_directive_ids = field.get("target_directive_ids", [])
    evidence_refs = field.get("evidence_refs", [])
    return {
        "source_fact_count": len(source_fact_ids) if isinstance(source_fact_ids, list) else 0,
        "target_directive_count": len(target_directive_ids) if isinstance(target_directive_ids, list) else 0,
        "evidence_refs": [str(ref) for ref in evidence_refs[:5]] if isinstance(evidence_refs, list) else [],
    }


def _constraint_context(constraints: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": constraints.get("schema_version"),
        "constraints": [
            {
                "constraint_id": str(item.get("constraint_id", "")),
                "triggered_by": item.get("triggered_by", []),
                "affected_capabilities": item.get("affected_capabilities", []),
                "obligation": str(item.get("obligation", "")),
                "severity": str(item.get("severity", "")),
                "validation_rule": str(item.get("validation_rule", "")),
            }
            for item in constraints.get("constraints", [])
            if isinstance(item, dict)
        ],
    }


def architecture_owner_hints(capability_ids: list[str]) -> list[dict[str, Any]]:
    required = set(capability_ids)
    groups = {
        "transport_runtime_prior": [
            "transport_io",
            "connection_lifecycle",
            "connection_buffering",
            "datagram_io",
            "peer_address_handling",
            "transport_adapter",
        ],
        "codec_framing_prior": [
            "message_decode",
            "message_encode",
            "incremental_message_framing",
            "datagram_message_framing",
            "canonical_type_ownership",
        ],
        "semantic_state_prior": [
            "semantic_dispatch",
            "state_machine",
            "state_transition_validation",
            "protocol_error_policy",
            "connection_termination",
            "error_response_encoding",
            "timer_source",
            "timeout_handling",
        ],
        "resource_routing_prior": [
            "session_state_ownership",
            "resource_ownership",
            "routing_dispatch",
            "recovery_cleanup_policy",
        ],
        "role_composition_prior": ["role_composition"],
    }
    return [
        {
            "hint_id": hint_id,
            "capability_ids": [cap for cap in capabilities if cap in required],
            "binding": "non_binding",
        }
        for hint_id, capabilities in groups.items()
        if any(cap in required for cap in capabilities)
    ]


def build_architecture_context(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    required_capability_ids = _capability_ids(profile)
    target_directives = planning_ir.get("target_directives", {}).get("directives", {})
    capability_trace = {
        str(item.get("capability_id")): _field_refs(item)
        for item in profile.get("required_capabilities", [])
        if isinstance(item, dict) and item.get("capability_id")
    }
    field_names = [
        "transport_shape",
        "interaction_model",
        "statefulness",
        "routing_intensity",
        "resource_intensity",
        "failure_semantics",
        "timing_model",
    ]
    return {
        "protocol": {
            "name": _value_of(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))),
            "target_role": _value_of(profile.get("target_role"), "target"),
            "minimum_scope": _value_of(profile.get("minimum_scope"), "minimum_v1"),
            "target_directives": deepcopy(target_directives) if isinstance(target_directives, dict) else {},
        },
        "profile_summary": {
            **{name: _value_of(profile.get(name), "unknown") for name in field_names},
            "normalized_roles": [item.get("role") for item in profile.get("normalized_roles", []) if isinstance(item, dict) and item.get("role")],
            "question_counts": profile.get("question_summary", {}),
        },
        "required_surface_units": [
            {"surface_id": item.get("surface_id"), "name": item.get("name")}
            for item in profile.get("required_surface_units", [])
            if isinstance(item, dict)
        ],
        "required_capability_ids": required_capability_ids,
        "capability_groups": {
            "transport": profile.get("capability_groups", {}).get("transport", []),
            "framing": profile.get("capability_groups", {}).get("message_framing", []),
            "state": profile.get("capability_groups", {}).get("state", []),
            "routing": profile.get("capability_groups", {}).get("routing_resource", []),
            "error": profile.get("capability_groups", {}).get("error_policy", []),
            "timing": profile.get("capability_groups", {}).get("timing", []),
        },
        "non_binding_capability_group_hints": architecture_owner_hints(required_capability_ids),
        "compressed_trace_refs": {
            "profile_fields": {name: _field_refs(profile.get(name)) for name in field_names},
            "capabilities": capability_trace,
        },
        "engineering_constraints": _constraint_context(constraints),
    }


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


def deterministic_architecture_ranking(candidates: dict[str, Any], profile: dict[str, Any], *, warning: str | None = None) -> dict[str, Any]:
    required = set(_capability_ids(profile))
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates.get("candidates", []):
        modules = candidate.get("modules", []) if isinstance(candidate, dict) else []
        covered = _candidate_owned_capabilities(candidate)
        support_count = sum(1 for module in modules if isinstance(module, dict) and module.get("support_module"))
        module_count = len(modules) if isinstance(modules, list) else 0
        coverage_score = 10 if required <= covered else int(10 * len(required & covered) / max(len(required), 1))
        simplicity_score = max(0, 10 - abs(module_count - 5))
        total = coverage_score * 4 + simplicity_score - support_count
        scored.append((total, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[0][1] if scored else {}
    return {
        "schema_version": ARCHITECTURE_RANKING_SCHEMA_VERSION,
        "scores": [
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "total_score": total,
                "dimension_scores": {
                    "capability_coverage": 10 if required <= _candidate_owned_capabilities(candidate) else 0,
                    "constraint_satisfaction": 8,
                    "cohesion": 7,
                    "coupling": 7,
                    "acyclicity": 7,
                    "state_ownership_clarity": 7,
                    "testability": 7,
                    "implementation_simplicity": max(0, 10 - abs(len(candidate.get("modules", [])) - 5)),
                    "target_scope_fit": 7,
                },
                "strengths": ["Deterministic fallback ranking based on coverage and module count."],
                "weaknesses": [],
                "risks": [],
            }
            for total, candidate in scored
        ],
        "selected_candidate_id": str(selected.get("candidate_id", "")),
        "selection_rationale": "Deterministic ranking fallback selected the highest coverage-oriented candidate.",
        "ranking_warnings": [warning] if warning else [],
        "fallback_used": True,
    }


def select_architecture(candidates: dict[str, Any], profile: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    selected_id = str((ranking or {}).get("selected_candidate_id", ""))
    items = candidates.get("candidates", [])
    selected = next((item for item in items if str(item.get("candidate_id", "")) == selected_id), None)
    if selected is None:
        selected = items[0] if items else {}
    return {
        "schema_version": SELECTED_ARCHITECTURE_SCHEMA_VERSION,
        "selected_candidate_id": selected.get("candidate_id", ""),
        "architecture": selected,
        "selection_reason": str((ranking or {}).get("selection_rationale", "Selected first valid architecture candidate.")),
        "fallback_used": bool((ranking or {}).get("fallback_used", False)),
        "coverage_summary": {
            "required_capability_count": len(_capability_ids(profile)),
            "covered_capability_count": len({cap for module in selected.get("modules", []) for cap in module.get("owned_capabilities", [])}),
        },
    }
