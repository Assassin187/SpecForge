from __future__ import annotations

import json
from typing import Any

from agent.planning.stages.implementation_plan_merger import (
    build_plan_skeleton,
    fallback_calls_allowed,
    fallback_core_design,
    fallback_file_layout,
    fallback_function_behavior,
    fallback_function_signatures,
    fallback_module_artifacts,
    fallback_runtime_entrypoint,
    fallback_wire_access_binding,
    finalize_dependency_graph,
    merge_calls_allowed,
    merge_core_design,
    merge_file_layout,
    merge_function_behavior,
    merge_function_inventory,
    merge_function_signatures,
    merge_module_artifacts,
    merge_runtime_entrypoint,
    merge_type_inventory,
    merge_wire_access_binding,
)
from agent.planning.stages.inventory_planning_space import build_function_planning_space, build_type_planning_space
from agent.planning.stages.inventory_reconciliation import reconcile_function_annotation_candidate, reconcile_type_filling_candidate


def _profile_value(profile: dict[str, Any], key: str, default: str) -> str:
    value = profile.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or default)


def _capability_ids(profile: dict[str, Any]) -> list[str]:
    return [
        str(item.get("capability_id", "")).strip()
        for item in profile.get("required_capabilities", [])
        if isinstance(item, dict) and str(item.get("capability_id", "")).strip()
    ]


def _constraint_ids(constraints: dict[str, Any]) -> list[str]:
    return [
        str(item.get("constraint_id", "")).strip()
        for item in constraints.get("constraints", [])
        if isinstance(item, dict) and str(item.get("constraint_id", "")).strip()
    ]


def current_architecture_candidates(planning_ir: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    protocol = _profile_value(profile, "protocol_name", planning_ir.get("protocol_name", "protocol")).lower()
    role = _profile_value(profile, "target_role", "target").lower()
    cap_ids = _capability_ids(profile)
    groups = profile.get("capability_groups", {}) if isinstance(profile.get("capability_groups"), dict) else {}
    assigned: set[str] = set()

    def take(group_names: tuple[str, ...] = (), keywords: tuple[str, ...] = ()) -> list[str]:
        wanted = {str(cap) for group in group_names for cap in groups.get(group, [])}
        owned: list[str] = []
        for cap_id in cap_ids:
            if cap_id in assigned:
                continue
            if cap_id in wanted or any(keyword in cap_id for keyword in keywords):
                owned.append(cap_id)
                assigned.add(cap_id)
        return owned

    module_specs = [
        (
            "transport_runtime",
            "Transport runtime",
            "Own socket, connection lifecycle, buffering, and transport callbacks.",
            take(("transport",), ("transport", "connection", "datagram", "peer")),
            [],
            ["listener socket", "connection table", "receive buffer"],
        ),
        (
            "protocol_codec",
            "Protocol codec",
            "Own message decoding, encoding, framing, and canonical wire types.",
            take(("message_framing",), ("decode", "encode", "framing", "canonical")),
            [],
            ["decoder cursor", "encoder scratch"],
        ),
        (
            "semantic_core",
            "Semantic core",
            "Own state-machine, timing, and protocol error semantics.",
            take(("state", "error_policy", "timing"), ("state", "timer", "timeout", "error", "termination", "semantic", "dispatch")),
            ["protocol_codec"],
            ["protocol state", "timer wheel"],
        ),
        (
            "resource_store",
            "Resource store",
            "Own routing resources, indexes, and cleanup policy.",
            take(("routing_resource",), ("routing", "resource", "recovery", "cleanup")),
            ["semantic_core"],
            ["resource index", "routing table"],
        ),
        (
            f"{protocol}_{role}_app",
            f"{protocol.upper()} {role} application",
            "Compose public role behavior and top-level runtime operations.",
            take((), ("role", "composition")),
            ["transport_runtime", "protocol_codec", "semantic_core", "resource_store"],
            ["application runtime"],
        ),
    ]
    remaining = [cap_id for cap_id in cap_ids if cap_id not in assigned]
    if remaining:
        module_specs.append(
            (
                "support_capabilities",
                "Support capabilities",
                "Own capabilities that are outside the standard planning fixture groups.",
                remaining,
                [f"{protocol}_{role}_app"],
                ["support state"],
            )
        )

    modules: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for module_id, name, responsibility, owned, deps, state_owned in module_specs:
        if not owned:
            continue
        valid_deps = [dep for dep in deps if dep in known_ids]
        modules.append(
            {
                "module_id": module_id,
                "name": name,
                "responsibilities": [responsibility],
                "owned_capabilities": owned,
                "consumed_capabilities": [],
                "state_owned": state_owned,
                "dependency_hints": valid_deps,
                "support_module": False,
            }
        )
        known_ids.add(module_id)

    return {
        "schema_version": "architecture_candidates/v1",
        "candidates": [
            {
                "candidate_id": "fixture_current_flow",
                "generation_mode": "deterministic_fixture",
                "generation_strategy": "capability_grouped_current_flow",
                "generation_request_id": "fixture:current_flow",
                "modules": modules,
                "module_graph_hints": [],
                "constraint_ids": _constraint_ids(constraints),
                "design_rationale": "Tests use the current staged planning flow with deterministic capability grouping.",
                "tradeoffs": ["Deterministic fixture favors stable test data over exploratory architecture alternatives."],
            }
        ],
        "generation_warnings": [],
    }


def current_type_inventory_candidate(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    space = build_type_planning_space(draft, module_artifact, planning_ir, profile, constraints)
    return reconcile_type_filling_candidate(space, None)["candidate"]


def current_function_inventory_candidate(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    space = build_function_planning_space(draft, module_artifact, planning_ir, profile, constraints)
    return reconcile_function_annotation_candidate(space, None)["candidate"]


def current_inventory_prompt_candidate(prompt_name: str, messages: list[dict[str, str]]) -> dict[str, Any] | None:
    if prompt_name == "type_filling_candidate_prompt":
        payload = json.loads(messages[1]["content"])
        context = payload["type_filling_context"]
        module_id = str(context.get("module_id") or context.get("module_artifact", {}).get("module_id", ""))
        return {
            "schema_version": "type_filling_candidate/v1",
            "candidate_id": f"candidate:type_filling:{module_id}",
            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
            "module_id": module_id,
            "slot_fillings": [],
            "optional_type_proposals": [],
            "assumptions": [],
            "unresolved_questions": [],
            "expansion_notes": [],
        }
    if prompt_name == "function_annotation_candidate_prompt":
        payload = json.loads(messages[1]["content"])
        context = payload["function_annotation_context"]
        module_id = str(context.get("module_id") or context.get("module_artifact", {}).get("module_id", ""))
        return {
            "schema_version": "function_annotation_candidate/v1",
            "candidate_id": f"candidate:function_annotation:{module_id}",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": module_id,
            "seed_annotations": [],
            "optional_function_proposals": [],
            "assumptions": [],
            "unresolved_questions": [],
            "decomposition_notes": [],
        }
    return None


def current_implementation_plan(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
) -> dict[str, Any]:
    draft = build_plan_skeleton(planning_ir, profile, constraints, selected_architecture)
    draft = merge_core_design(draft, fallback_core_design(draft, planning_ir, constraints, selected_architecture, profile))
    draft = merge_module_artifacts(draft, fallback_module_artifacts(draft, profile, constraints, selected_architecture))
    for module in list(draft.get("module_artifacts", [])):
        draft = merge_type_inventory(draft, current_type_inventory_candidate(draft, module, planning_ir, profile, constraints))
    for module in list(draft.get("module_artifacts", [])):
        draft = merge_function_inventory(draft, current_function_inventory_candidate(draft, module, planning_ir, profile, constraints))
    for module in list(draft.get("module_artifacts", [])):
        module_id = str(module.get("module_id", ""))
        draft = merge_function_signatures(draft, fallback_function_signatures(draft, module_id))
    for module in list(draft.get("module_artifacts", [])):
        module_id = str(module.get("module_id", ""))
        draft = merge_function_behavior(draft, fallback_function_behavior(draft, module_id))
    draft = merge_wire_access_binding(draft, fallback_wire_access_binding(draft, planning_ir))
    draft = merge_calls_allowed(draft, fallback_calls_allowed(draft))
    draft = merge_file_layout(draft, fallback_file_layout(draft))
    draft = merge_runtime_entrypoint(draft, fallback_runtime_entrypoint(draft))
    return finalize_dependency_graph(draft)
