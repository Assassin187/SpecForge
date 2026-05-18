from __future__ import annotations

import json
from typing import Any

from .versions import PROMPT_REGISTRY


def _compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


JSON_ONLY_RULES = (
    "Return one valid minified JSON object only. "
    "Do not use markdown fences. Do not include prose, comments, headings, or analysis. "
    "The first character must be '{' and the last character must be '}'."
)


def protocol_profile_patch_messages(planning_ir: dict[str, Any], deterministic_profile: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent v2 protocol profile audit assistant. "
                f"{JSON_ONLY_RULES} "
                "Your job is not to generate a full profile. "
                "Your job is to independently audit selected profile fields and propose a patch only when justified. "
                "Do not assume the deterministic profile is correct. "
                "Do not invent protocol facts, modules, files, functions, dependencies, or implementation strategies. "
                "All non-unknown judgments must be supported by existing evidence IDs or facts from the Planning IR."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": "protocol_profile_patch_prompt",
                    "prompt_version": PROMPT_REGISTRY["protocol_profile_patch_prompt"],
                    "task": (
                        "Independently review the deterministic protocol profile. "
                        "For each reviewed field, decide whether to keep the deterministic value, replace it, or mark it uncertain. "
                        "Return a patch only for fields that should be changed. "
                        "An empty patch is allowed only if all reviewed fields are adequately supported and no capability gap is found."
                    ),
                    "required_top_level_keys": [
                        "schema_version",
                        "field_reviews",
                        "capability_gap_review",
                        "patch",
                        "uncertainties",
                        "rationale",
                    ],
                    "forbidden_top_level_keys": ["output_schema", "expected_response"],
                    "hard_validation_rules": [
                        "Return strict JSON only.",
                        "The top-level schema_version must be exactly protocol_profile_patch_candidate/v1.",
                        "Do not return output_schema or expected_response as top-level keys.",
                        "Do not modify protocol_name, target_role, minimum_scope, normalized_roles, question counts, or required_surface_units.",
                        "Only patch interaction_model, statefulness, routing_intensity, resource_intensity, failure_semantics, and capability_additions.",
                        "Every non-empty patch value must be justified by support IDs or explicit Planning IR facts.",
                        "If evidence is weak, use uncertain instead of inventing a stronger classification.",
                        "An empty patch is valid only if field_reviews show keep for all reviewed fields and capability_gap_review has no missing capabilities.",
                    ],
                    "allowed_values": {
                        "interaction_model": [
                            "request_response",
                            "streaming",
                            "pubsub",
                            "handshake",
                            "command_response",
                            "event_notification",
                            "framed_message",
                            "mixed",
                            "unknown",
                        ],
                        "statefulness": [
                            "stateless",
                            "connection_stateful",
                            "session_stateful",
                            "transaction_stateful",
                            "protocol_state_machine",
                            "mixed",
                            "unknown",
                            "connection_state",
                            "session_state",
                            "persistent_state",
                        ],
                        "routing_intensity": ["none", "low", "medium", "high", "unknown"],
                        "resource_intensity": ["none", "low", "medium", "high", "unknown"],
                        "failure_semantics": [
                            "simple_error",
                            "recoverable_error",
                            "fatal_error",
                            "mixed_recoverability",
                            "timeout_sensitive",
                            "retryable",
                            "connection_closing",
                            "unknown",
                            "close_connection_on_protocol_error",
                            "reply_with_error",
                            "mixed",
                        ],
                        "field_review_decision": ["keep", "replace", "uncertain"],
                    },
                    "expected_response_shape": {
                        "schema_version": "protocol_profile_patch_candidate/v1",
                        "field_reviews": {
                            "interaction_model": {
                                "deterministic_value": "string",
                                "llm_value": "string",
                                "decision": "keep | replace | uncertain",
                                "support_ids": [],
                                "reason": "string",
                            },
                            "statefulness": {
                                "deterministic_value": "string",
                                "llm_value": "string",
                                "decision": "keep | replace | uncertain",
                                "support_ids": [],
                                "reason": "string",
                            },
                            "routing_intensity": {
                                "deterministic_value": "string",
                                "llm_value": "string",
                                "decision": "keep | replace | uncertain",
                                "support_ids": [],
                                "reason": "string",
                            },
                            "resource_intensity": {
                                "deterministic_value": "string",
                                "llm_value": "string",
                                "decision": "keep | replace | uncertain",
                                "support_ids": [],
                                "reason": "string",
                            },
                            "failure_semantics": {
                                "deterministic_value": "string",
                                "llm_value": "string",
                                "decision": "keep | replace | uncertain",
                                "support_ids": [],
                                "reason": "string",
                            },
                        },
                        "capability_gap_review": {
                            "missing_capabilities": [],
                            "unsupported_existing_capabilities": [],
                            "reason": "string",
                        },
                        "patch": {
                            "interaction_model": "optional string",
                            "statefulness": "optional string",
                            "routing_intensity": "optional string",
                            "resource_intensity": "optional string",
                            "failure_semantics": "optional string",
                            "capability_additions": [],
                        },
                        "uncertainties": [],
                        "rationale": "string",
                    },
                    "planning_ir": {
                        "protocol_name": planning_ir.get("protocol_name"),
                        "protocol_facts": planning_ir.get("protocol_facts"),
                        "target_directives": planning_ir.get("target_directives"),
                        "evidence_ids": sorted(planning_ir.get("evidence", {}).keys()),
                    },
                    "deterministic_profile": deterministic_profile,
                }
            ),
        },
    ]


def _capability_ids(profile: dict[str, Any]) -> list[str]:
    return [
        str(item.get("capability_id"))
        for item in profile.get("required_capabilities", [])
        if isinstance(item, dict) and item.get("capability_id")
    ]


def _architecture_owner_hints(capability_ids: list[str]) -> dict[str, list[str]]:
    required = set(capability_ids)
    hints = {
        "transport_runtime": [
            "transport_io",
            "connection_lifecycle",
            "connection_buffering",
            "datagram_io",
            "peer_address_handling",
            "transport_adapter",
        ],
        "protocol_codec": [
            "message_decode",
            "message_encode",
            "incremental_message_framing",
            "datagram_message_framing",
            "canonical_type_ownership",
        ],
        "semantic_core": [
            "semantic_dispatch",
            "state_machine",
            "state_transition_validation",
            "protocol_error_policy",
            "connection_termination",
            "error_response_encoding",
            "timer_source",
            "timeout_handling",
        ],
        "resource_store": [
            "session_state_ownership",
            "resource_ownership",
            "routing_dispatch",
            "recovery_cleanup_policy",
        ],
        "target_role_app": ["role_composition"],
    }
    return {module: [cap for cap in caps if cap in required] for module, caps in hints.items() if any(cap in required for cap in caps)}


def architecture_candidate_messages(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, str]]:
    required_capability_ids = _capability_ids(profile)
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent architecture candidate generator. "
                f"{JSON_ONLY_RULES} Return JSON matching architecture_candidates/v1. "
                "You may generate module-level design only. Do not generate file paths, functions, call graphs, include graphs, or code."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": "architecture_candidate_prompt",
                    "prompt_version": PROMPT_REGISTRY["architecture_candidate_prompt"],
                    "required_top_level_keys": ["schema_version", "candidates", "generation_warnings"],
                    "forbidden_top_level_keys": ["output_schema", "expected_response"],
                    "expected_response": {
                        "schema_version": "architecture_candidates/v1",
                        "candidates": [
                            {
                                "candidate_id": "string",
                                "generation_mode": "llm_candidate",
                                "modules": [
                                    {
                                        "module_id": "string",
                                        "name": "string",
                                        "responsibilities": ["string"],
                                        "owned_capabilities": ["capability_id from required_capabilities"],
                                        "consumed_capabilities": ["capability_id from required_capabilities"],
                                        "state_owned": ["string"],
                                        "dependency_hints": ["module_id"],
                                        "support_module": False,
                                    }
                                ],
                                "module_graph_hints": [],
                                "constraint_ids": ["constraint_id"],
                                "design_rationale": "string",
                                "tradeoffs": ["string"],
                            }
                        ],
                        "generation_warnings": [],
                    },
                    "planning_ir_summary": {
                        "protocol_name": planning_ir.get("protocol_name"),
                        "target_directives": planning_ir.get("target_directives"),
                    },
                    "required_capability_ids": required_capability_ids,
                    "capability_owner_hints": _architecture_owner_hints(required_capability_ids),
                    "hard_validation_rules": [
                        "Return the expected_response object itself, not an object containing expected_response or output_schema.",
                        "The top-level schema_version must be exactly architecture_candidates/v1.",
                        "Every required_capability_ids entry must appear in at least one module.owned_capabilities.",
                        "Every module.owned_capabilities entry must come from required_capability_ids.",
                        "A non-support module must own at least one capability.",
                        "Capabilities mentioned in responsibilities, rationale, or consumed_capabilities do not count as covered.",
                        "semantic_dispatch, role_composition, and canonical_type_ownership are real capabilities when present and must be explicitly owned.",
                        "Before returning, compute required_capability_ids minus the union of module.owned_capabilities; it must be empty.",
                    ],
                    "known_rejection_patterns_to_avoid": [
                        "Describing semantic dispatch in text but omitting semantic_dispatch from owned_capabilities.",
                        "Creating orchestration modules with empty owned_capabilities.",
                        "Moving a capability to consumed_capabilities without any module owning it.",
                    ],
                    "protocol_profile": profile,
                    "engineering_constraints": constraints,
                }
            ),
        },
    ]


def implementation_plan_candidate_messages(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
    deterministic_plan: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent implementation plan candidate assistant. "
                f"{JSON_ONLY_RULES} Return JSON matching implementation_plan/v1. "
                "Implementation Plan is the final stage where engineering semantics may be introduced. "
                "Do not output code. Do not introduce protocol facts not present in the input. "
                "Every module must come from selected_architecture; every file/function must be internally consistent."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": "function_contract_prompt",
                    "prompt_version": PROMPT_REGISTRY["function_contract_prompt"],
                    "planning_ir": planning_ir,
                    "protocol_profile": profile,
                    "engineering_constraints": constraints,
                    "selected_architecture": selected_architecture,
                    "deterministic_baseline_plan": deterministic_plan,
                    "allowed_response": "Return a complete implementation_plan/v1 JSON object. Use compact JSON. If no changes are needed, return the deterministic baseline plan as compact JSON.",
                }
            ),
        },
    ]
