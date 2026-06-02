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


def _candidate_dependency_edges(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    modules = candidate.get("modules", []) if isinstance(candidate, dict) else []
    module_ids = {str(module.get("module_id", "")) for module in modules if isinstance(module, dict)}
    edges: list[tuple[str, str]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        consumer = str(module.get("module_id", ""))
        for dependency in module.get("dependency_hints", []):
            provider = str(dependency.get("module_id", dependency)) if isinstance(dependency, dict) else str(dependency)
            if consumer and provider and provider in module_ids and provider != consumer:
                edges.append((consumer, provider))
    return edges


def _has_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for consumer, provider in edges:
        adjacency.setdefault(consumer, []).append(provider)
        adjacency.setdefault(provider, [])
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for next_node in adjacency.get(node, []):
            if visit(next_node):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in nodes)


def _architecture_module_text(module: dict[str, Any]) -> str:
    return " ".join(
        [
            str(module.get("module_id", "")),
            str(module.get("name", "")),
            " ".join(str(item) for item in module.get("responsibilities", [])),
            " ".join(str(item) for item in module.get("owned_capabilities", [])),
            " ".join(str(item) for item in module.get("state_owned", [])),
        ]
    ).lower()


def _engineering_groups_for_module(module: dict[str, Any]) -> set[str]:
    text = _architecture_module_text(module)
    groups: set[str] = set()
    if any(term in text for term in ("transport", "runtime", "network", "socket", "tcp", "udp", "epoll", "connection", "accept", "fd", "io")):
        groups.add("transport_runtime")
    if any(term in text for term in ("codec", "decode", "encode", "parser", "serializer", "framing", "packet", "wire", "canonical_type", "data model", "payload")):
        groups.add("codec_data_model")
    if any(term in text for term in ("session", "state_machine", "state machine", "resource", "registry", "manager", "lifecycle", "keepalive")):
        groups.add("session_resource")
    if any(term in text for term in ("routing", "route", "router", "topic", "subscription", "semantic_dispatch", "dispatch")):
        groups.add("routing_dispatch")
    if any(term in text for term in ("broker", "server", "client", "app", "role_composition", "orchestration", "entrypoint")):
        groups.add("broker_app")
    if any(term in text for term in ("timer", "timeout", "retry", "error", "failure")):
        groups.add("timing_error")
    return groups


def _expected_engineering_groups(required_capabilities: set[str]) -> set[str]:
    text = " ".join(sorted(required_capabilities)).lower()
    groups: set[str] = set()
    if any(term in text for term in ("transport", "runtime", "connection", "io", "socket", "epoll")):
        groups.add("transport_runtime")
    if any(term in text for term in ("decode", "encode", "codec", "framing", "canonical", "message")):
        groups.add("codec_data_model")
    if any(term in text for term in ("session", "state", "resource", "lifecycle")):
        groups.add("session_resource")
    if any(term in text for term in ("routing", "route", "topic", "subscription", "dispatch", "semantic")):
        groups.add("routing_dispatch")
    if any(term in text for term in ("role", "broker", "server", "client", "app")):
        groups.add("broker_app")
    if any(term in text for term in ("timer", "timing", "timeout", "error", "failure")):
        groups.add("timing_error")
    return groups or {"transport_runtime", "codec_data_model", "session_resource", "broker_app"}


def _bounded_score(value: float) -> int:
    return max(0, min(10, int(round(value))))


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
    expected_groups = _expected_engineering_groups(required)
    scored: list[tuple[float, dict[str, Any]]] = []
    dimensions_by_candidate: dict[str, dict[str, int]] = {}
    for candidate in candidates.get("candidates", []):
        modules = candidate.get("modules", []) if isinstance(candidate, dict) else []
        covered = _candidate_owned_capabilities(candidate)
        support_count = sum(1 for module in modules if isinstance(module, dict) and module.get("support_module"))
        module_count = len(modules) if isinstance(modules, list) else 0
        coverage_score = 10 if required <= covered else int(10 * len(required & covered) / max(len(required), 1))
        module_groups = [_engineering_groups_for_module(module) for module in modules if isinstance(module, dict)]
        covered_groups = {group for groups in module_groups for group in groups}
        group_score = 10 * len(expected_groups & covered_groups) / max(len(expected_groups), 1)
        overloaded = sum(1 for groups in module_groups if len(groups) > 2)
        boundary_score = _bounded_score(group_score - overloaded * 1.5 - max(0, len(expected_groups) - module_count) * 1.5)
        state_refs = [
            str(state)
            for module in modules
            if isinstance(module, dict)
            for state in module.get("state_owned", [])
            if str(state).strip()
        ]
        duplicate_state_count = len(state_refs) - len(set(state_refs))
        ownership_score = _bounded_score(10 - duplicate_state_count * 2 - overloaded)
        nodes = {str(module.get("module_id", "")) for module in modules if isinstance(module, dict) and str(module.get("module_id", "")).strip()}
        edges = _candidate_dependency_edges(candidate)
        acyclicity_score = 10 if not _has_cycle(nodes, edges) else 0
        testability_score = _bounded_score(boundary_score - support_count + min(2, module_count // 4))
        target_min = min(8, max(4, len(expected_groups)))
        target_max = min(10, max(target_min + 2, len(expected_groups) + 2))
        if target_min <= module_count <= target_max:
            simplicity_score = 9
        elif module_count < target_min:
            simplicity_score = _bounded_score(9 - (target_min - module_count) * 2)
        else:
            simplicity_score = _bounded_score(9 - (module_count - target_max))
        constraint_score = 8 if required <= covered else 5
        coupling_score = _bounded_score(acyclicity_score - max(0, len(edges) - max(module_count, 1)) * 0.5)
        target_scope_score = _bounded_score((coverage_score + boundary_score + simplicity_score) / 3)
        dimensions = {
            "capability_coverage": coverage_score,
            "constraint_satisfaction": constraint_score,
            "cohesion": boundary_score,
            "coupling": coupling_score,
            "acyclicity": acyclicity_score,
            "state_ownership_clarity": ownership_score,
            "testability": testability_score,
            "implementation_simplicity": simplicity_score,
            "target_scope_fit": target_scope_score,
        }
        total = (
            coverage_score * 4
            + boundary_score * 2
            + ownership_score * 1.5
            + acyclicity_score * 1.5
            + testability_score
            + simplicity_score
            - support_count * 2
        )
        dimensions_by_candidate[str(candidate.get("candidate_id", ""))] = dimensions
        scored.append((total, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[0][1] if scored else {}
    return {
        "schema_version": ARCHITECTURE_RANKING_SCHEMA_VERSION,
        "scores": [
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "total_score": total,
                "dimension_scores": dimensions_by_candidate.get(str(candidate.get("candidate_id", "")), {}),
                "strengths": ["Deterministic fallback ranking based on capability coverage, engineering boundaries, ownership, acyclicity, and testability."],
                "weaknesses": [],
                "risks": [],
            }
            for total, candidate in scored
        ],
        "selected_candidate_id": str(selected.get("candidate_id", "")),
        "selection_rationale": "Deterministic ranking fallback selected the candidate with the clearest implementable engineering boundaries among capability-covering options.",
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
