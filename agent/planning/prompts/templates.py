from __future__ import annotations

import json
from typing import Any

from ..schemas.implementation_plan_candidates import output_shape
from .versions import PROMPT_REGISTRY


def _compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


JSON_ONLY_RULES = (
    "Return one valid minified JSON object only. "
    "Do not use markdown fences. Do not include prose, comments, headings, or analysis. "
    "The first character must be '{' and the last character must be '}'."
)

ID_REFERENCE_RULES = [
    "Every *_id, *_ids, *_ref, and *_refs value must be copied exactly from the context or the legal_id_universe unless the schema defines it as a new candidate-local ID.",
    "Do not convert paths, filenames, display names, descriptions, or natural-language labels into IDs.",
    "Do not cross ID namespaces: type_ids, state_ids, message_ids, field_ids, file_ids, function_ids, module_ids, capability_ids, constraint_ids, and error_ids are not interchangeable.",
    "If a needed referenced ID is absent, record an unresolved_questions item instead of inventing a plausible ID.",
]

ENUM_USAGE_RULES = [
    "Use only enum values shown in output_shape.",
    "For function contracts, contract_kind is the structural carrier category: none, typed, buffer, opaque, callback, or unknown.",
    "Do not use return-result words such as status_code, boolean, void, int, or pointer_null as contract_kind; put those in error_behavior.return_policy when applicable.",
    "Use unknown or unresolved_questions when the enum choice cannot be determined from the context.",
]

STAGE_SEMANTIC_RULES = {
    "core_design_candidate/v1": [
        "handler_matrix[].trigger is a coverage key, not prose. For every handler_requirements[].surface, either one handler_matrix[].trigger must exactly equal that surface or one unresolved_questions[].target_id must exactly equal that surface.",
        "owner_module_id values must come from selected modules.",
        "capability_id and source_capability_ids values must come from required capabilities.",
        "message_ids and source_message_ids must come from legal message IDs; source_field_ids must come from legal field IDs; related_constraint_ids must come from legal constraint IDs.",
        "Do not mention files, functions, calls, imports, dependency graphs, or code in this stage output.",
    ],
    "module_contracts_candidate/v1": [
        "module_id values must come from selected modules.",
        "Every non-support module must own at least one capability; if ownership is unclear, add unresolved_questions instead of leaving ownership empty.",
        "Each capability may have only one primary owner; shared ownership must use the schema's shared_owner_module_ids field.",
        "state_id, error_id, and constraint_id references must come from the accepted core design and engineering constraints.",
        "Do not generate files, functions, calls, access paths, imports, dependency graphs, or code.",
    ],
    "function_inventory_candidate/v2": [
        "Generate only a function inventory; do not include signatures, input/output contracts, state access, wire mappings, calls, file IDs, dependency graphs, or code.",
        "function_id values must be new and unique within this candidate; function names must be unique.",
        "module_id must be the current module or another module explicitly present in module_contracts.",
        "If the module owns decode, encode, dispatch, state-machine, lifecycle, or error-policy responsibilities, represent them with the matching function_kind values or add unresolved_questions.",
        "covers_handler_ids, covers_message_ids, and covers_field_ids may reference only existing handler, message, and field IDs.",
    ],
    "function_signature_patch/v1": [
        "Patch existing function_id values only; never add a new function.",
        "function_signature_updates must include exactly one update for every function in the batch.",
        "signature.name must match the existing function name.",
        "signature.params[].type is the C spelling; type_ref may use only legal_id_universe.type_ids or legal_id_universe.system_type_ids.",
        "C primitive, POSIX, or common network types not present in the type pool must use an empty type_ref.",
        "Never use state_ids, message_ids, field_ids, paths, filenames, or natural-language names as type_ref.",
        "signature_dependencies describe type/header needs only; do not generate imports_allowed, calls_allowed, files, dependency graphs, or code.",
    ],
    "function_behavior_contract_patch/v1": [
        "Patch existing function_id values only; never add a new function and never change signatures.",
        "function_behavior_updates must include exactly one update for every function in the batch.",
        "service_requirements may describe needed operations/capabilities but must not contain callee_function_id.",
        "Use requirement_kind=cross_module_service only when another existing module should provide the operation.",
        "Use requirement_kind=external_runtime_service for socket, epoll, malloc, timer, or OS/runtime operations; do not force those into internal calls.",
        "Do not create service_requirements for a provider function's own owned responsibility.",
        "state_access.access_kind write or read_write is allowed only when the function's module owns that state. Otherwise use read, omit that state access, or add unresolved_questions.",
        "Do not generate calls_allowed, wire mappings, files, dependency graphs, or code.",
    ],
    "wire_access_binding_patch/v2": [
        "direction=parse may bind only parser functions; direction=serialize may bind only serializer functions.",
        "Every target wire field must be covered by a parse or serialize wire_mapping_entry, or by unresolved_questions with that exact field_id as target_id.",
        "Parser and serializer functions must not write state through access paths; their access_path_entries should use read unless the function kind permits mutation.",
        "access path entries describe C access expressions for existing fields; do not invent fields.",
        "Do not add new functions, messages, fields, states, calls, dependency graphs, or code.",
    ],
    "calls_allowed_candidate/v2": [
        "caller_function_id and callee_function_id must come from existing functions.",
        "Do not create self-calls.",
        "Do not call private or static functions across module boundaries.",
        "Resolve only cross_module_service service_requirements into concrete calls where possible; otherwise use unresolved_service_requirements.",
        "External runtime service requirements must not become internal call edges.",
        "If callable_functions is present, callee_function_id must come from that list or from a same-module existing function.",
        "The planned calls_allowed graph must avoid prohibited cycles.",
        "Do not generate imports, file graphs, dependency graphs, new functions, or code.",
    ],
    "file_layout_candidate/v2": [
        "Each files[] item is one source_header_pair FILE_SPEC unit with source_path and header_path.",
        "file_id must use the source path without the .c suffix, prefixed with file:, for example file:mqtt/transport_runtime/transport_runtime.",
        "Do not create separate header or source file items.",
        "Do not add functions; every existing function must have exactly one function_file_assignment.",
        "exports_type_ids may contain only canonical type IDs; never use state IDs, message IDs, field IDs, filenames, paths, or natural-language type names.",
        "imports_allowed may reference only other files[].file_id FILE_SPEC values from this same candidate.",
        "imports_allowed must not reference header IDs, .h files, .c files, paths, or the file's own file_id.",
        "Public functions must have declaration_file_id equal to their FILE_SPEC file_id.",
        "Private or static functions must not have declaration_file_id.",
    ],
    "dependency_repair_patch/v1": [
        "Repair only dependency inputs; never output dependency_graph.",
        "Repair actions may affect only existing function IDs and file IDs.",
        "For adjust_imports_allowed, file_id, add_import_file_ids, and remove_import_file_ids must be copied exactly from legal_id_universe.file_ids or files[].file_id.",
        "A path, header filename, or source filename is not a file_id unless that exact string appears in the allowed file IDs.",
        "Do not add modules, files, functions, capabilities, protocol facts, dependency graphs, or code.",
    ],
}


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


def architecture_candidate_messages(
    architecture_context: dict[str, Any],
    design_strategy: str,
) -> list[dict[str, str]]:
    required_capability_ids = [
        str(item)
        for item in architecture_context.get("required_capability_ids", [])
        if str(item).strip()
    ]
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent architecture candidate generator. "
                f"{JSON_ONLY_RULES} Return JSON matching architecture_candidates/v1. "
                "You may generate module-level design only. Do not generate file paths, functions, call graphs, include graphs, or code. "
                "The capability group hints are non-binding engineering priors. They are not required module names. "
                "You may split, merge, rename, or ignore them if the protocol profile suggests a better architecture."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": "architecture_candidate_prompt",
                    "prompt_version": PROMPT_REGISTRY["architecture_candidate_prompt"],
                    "design_strategy": design_strategy,
                    "required_top_level_keys": ["schema_version", "candidates", "generation_warnings"],
                    "forbidden_top_level_keys": ["output_schema", "expected_response"],
                    "expected_response": {
                        "schema_version": "architecture_candidates/v1",
                        "candidates": [
                            {
                                "candidate_id": "string",
                                "generation_mode": "llm_candidate",
                                "generation_strategy": design_strategy,
                                "generation_request_id": "string",
                                "modules": [
                                    {
                                        "module_id": "string",
                                        "name": "string",
                                        "responsibilities": ["string"],
                                        "owned_capabilities": ["capability_id from required_capabilities"],
                                        "consumed_capabilities": ["capability_id from required_capabilities"],
                                        "state_owned": ["string"],
                                        "dependency_hints": [],
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
                    "architecture_context": architecture_context,
                    "required_capability_ids": required_capability_ids,
                    "non_binding_capability_group_hints": architecture_context.get("non_binding_capability_group_hints", []),
                    "strategy_guidance": {
                        "capability_clustered": "Cluster modules around cohesive capability ownership and make ownership boundaries explicit.",
                        "layered_runtime_codec_semantic": "Prefer runtime, codec, semantic, resource/state, and app-facing layers with low coupling.",
                        "minimal_scope": "Prefer the smallest target-scope architecture that still satisfies every capability and constraint.",
                    }.get(design_strategy, "Generate a coherent module-level architecture for the target scope."),
                    "hard_validation_rules": [
                        "Return the expected_response object itself, not an object containing expected_response or output_schema.",
                        "The top-level schema_version must be exactly architecture_candidates/v1.",
                        "Every required_capability_ids entry must appear in at least one module.owned_capabilities.",
                        "Every module.owned_capabilities entry must come from required_capability_ids.",
                        "A non-support module must own at least one capability.",
                        "Capabilities mentioned in responsibilities, rationale, or consumed_capabilities do not count as covered.",
                        "semantic_dispatch, role_composition, and canonical_type_ownership are real capabilities when present and must be explicitly owned.",
                        "Architecture candidates must not plan dependency relationships; every module.dependency_hints must be [].",
                        "module_graph_hints must be []. Module dependency planning belongs to implementation plan Module Contract Planning via imports_allowed and calls_allowed.",
                        "Capability group hints are non-binding priors, not required module names.",
                        "You may split, merge, rename, or ignore hints.",
                        "The selected architecture will be judged by capability coverage, cohesion, coupling, constraint satisfaction, and acyclicity.",
                        "Before returning, compute required_capability_ids minus the union of module.owned_capabilities; it must be empty.",
                    ],
                    "known_rejection_patterns_to_avoid": [
                        "Describing semantic dispatch in text but omitting semantic_dispatch from owned_capabilities.",
                        "Creating orchestration modules with empty owned_capabilities.",
                        "Moving a capability to consumed_capabilities without any module owning it.",
                        "Filling dependency_hints or module_graph_hints during architecture search.",
                        "Copying hint IDs directly as fixed module names without considering the design strategy.",
                    ],
                }
            ),
        },
    ]


def architecture_ranking_messages(architecture_context: dict[str, Any], architecture_candidates: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent architecture ranking assistant. "
                f"{JSON_ONLY_RULES} Return JSON matching architecture_ranking/v1. "
                "You may score and select only. Do not modify candidates, modules, capabilities, files, functions, dependencies, or code."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": "architecture_ranking_prompt",
                    "prompt_version": PROMPT_REGISTRY["architecture_ranking_prompt"],
                    "required_top_level_keys": ["schema_version", "scores", "selected_candidate_id", "selection_rationale", "ranking_warnings"],
                    "forbidden_top_level_keys": ["output_schema", "expected_response", "candidates"],
                    "expected_response": {
                        "schema_version": "architecture_ranking/v1",
                        "scores": [
                            {
                                "candidate_id": "string",
                                "total_score": 0,
                                "dimension_scores": {
                                    "capability_coverage": 0,
                                    "constraint_satisfaction": 0,
                                    "cohesion": 0,
                                    "coupling": 0,
                                    "acyclicity": 0,
                                    "state_ownership_clarity": 0,
                                    "testability": 0,
                                    "implementation_simplicity": 0,
                                    "target_scope_fit": 0,
                                },
                                "strengths": [],
                                "weaknesses": [],
                                "risks": [],
                            }
                        ],
                        "selected_candidate_id": "string",
                        "selection_rationale": "string",
                        "ranking_warnings": [],
                    },
                    "hard_validation_rules": [
                        "The top-level schema_version must be exactly architecture_ranking/v1.",
                        "Score every candidate_id exactly once.",
                        "selected_candidate_id must be one of the candidate IDs.",
                        "Scores must evaluate capability coverage, constraint satisfaction, cohesion, coupling, acyclicity, state ownership clarity, testability, implementation simplicity, and target scope fit.",
                        "Do not output modified architecture candidates.",
                    ],
                    "architecture_context": architecture_context,
                    "architecture_candidates": architecture_candidates,
                }
            ),
        },
    ]


def _stage_messages(
    *,
    prompt_name: str,
    task: str,
    context_key: str,
    context: dict[str, Any],
    expected_schema: str,
    forbidden_fields: list[str],
    validator: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent staged implementation-plan assistant. "
                f"{JSON_ONLY_RULES} "
                f"Return JSON matching {expected_schema}. "
                "Return only the current stage candidate or patch. "
                "Never return a complete implementation_plan/v1. "
                "Never output code. "
                "Do not invent protocol facts or identifiers outside the provided legal ID universe."
            ),
        },
        {
            "role": "user",
            "content": _compact(
                {
                    "prompt_name": prompt_name,
                    "prompt_version": PROMPT_REGISTRY[prompt_name],
                    "task": task,
                    "output_schema": expected_schema,
                    "output_shape": output_shape(expected_schema),
                    "forbidden_fields": forbidden_fields,
                    "validator_after_output": validator,
                    context_key: context,
                    "id_reference_rules": ID_REFERENCE_RULES,
                    "enum_usage_rules": ENUM_USAGE_RULES,
                    "semantic_validation_rules": STAGE_SEMANTIC_RULES.get(expected_schema, []),
                    "hard_validation_rules": [
                        f"schema_version must be exactly {expected_schema}.",
                        "The JSON object must match output_shape exactly.",
                        "Return the candidate or patch object itself.",
                        "Do not include a complete implementation_plan/v1.",
                        "Do not include forbidden fields.",
                        "Do not include any fields not shown in output_shape.",
                        "Use only IDs present in the context legal ID universe.",
                        "If information is insufficient, add unresolved_questions instead of inventing facts.",
                    ],
                }
            ),
        },
    ]


def core_design_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="core_design_candidate_prompt",
        task="Plan only the core implementation design matrix before files, functions, or dependencies exist.",
        context_key="core_design_context",
        context=context,
        expected_schema="core_design_candidate/v1",
        forbidden_fields=["file_id", "file path", "function_id", "function name", "calls_allowed", "imports_allowed", "dependency_graph", "code"],
        validator="validate_core_design_candidate",
    )


def module_contracts_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="module_contracts_candidate_prompt",
        task="Plan module contracts from the selected architecture and accepted core design.",
        context_key="module_contract_context",
        context=context,
        expected_schema="module_contracts_candidate/v1",
        forbidden_fields=["file layout", "file_id", "file path", "function_id", "function list", "calls_allowed", "imports_allowed", "dependency_graph", "access paths", "code"],
        validator="validate_module_contracts_candidate",
    )


def function_inventory_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_inventory_candidate_prompt",
        task="Plan the function inventory for one module. Do not fill signatures or detailed contracts.",
        context_key="function_inventory_context",
        context=context,
        expected_schema="function_inventory_candidate/v2",
        forbidden_fields=["signature", "input_contract", "output_contract", "state_access", "wire_mapping", "access_paths", "calls_allowed", "dependency_graph", "code"],
        validator="validate_function_inventory_candidate",
    )


def function_signature_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_signature_patch_prompt",
        task="Patch complete C signatures and signature_dependencies for the current function batch.",
        context_key="function_signature_context",
        context=context,
        expected_schema="function_signature_patch/v1",
        forbidden_fields=["new function_id", "new file_id", "new module_id", "state_access", "wire_mapping", "access_paths", "calls_allowed", "dependency_graph", "code"],
        validator="validate_function_signature_patch",
    )


def function_behavior_contract_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_behavior_contract_patch_prompt",
        task="Patch behavior contracts, state/resource access, internal type refs, and service requirements for the current function batch. Do not generate call edges.",
        context_key="function_behavior_context",
        context=context,
        expected_schema="function_behavior_contract_patch/v1",
        forbidden_fields=["new function_id", "signature changes", "wire_mapping", "access_paths", "callee_function_id", "calls_allowed", "dependency_graph", "code"],
        validator="validate_function_behavior_contract_patch",
    )


def wire_access_binding_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="wire_access_binding_patch_prompt",
        task="Bind existing functions to protocol wire fields and access paths.",
        context_key="wire_access_binding_context",
        context=context,
        expected_schema="wire_access_binding_patch/v2",
        forbidden_fields=["new function", "new message", "new field", "new state", "calls_allowed", "dependency_graph", "code"],
        validator="validate_wire_access_binding_patch",
    )


def calls_allowed_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="calls_allowed_candidate_prompt",
        task="Resolve service requirements into concrete calls_allowed edges after signatures, behavior contracts, and wire binding are stable.",
        context_key="calls_allowed_context",
        context=context,
        expected_schema="calls_allowed_candidate/v2",
        forbidden_fields=["new function", "new file", "new module", "imports_allowed", "dependency_graph", "include graph", "code"],
        validator="validate_calls_allowed_candidate",
    )


def file_layout_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="file_layout_candidate_prompt",
        task="Plan C source_header_pair file layout and assign existing functions to files.",
        context_key="file_layout_context",
        context=context,
        expected_schema="file_layout_candidate/v2",
        forbidden_fields=["new function_id", "new function name", "function implementation details", "function call graph", "final dependency_graph", "code"],
        validator="validate_file_layout_candidate",
    )


def dependency_repair_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="dependency_repair_patch_prompt",
        task="Repair invalid dependency inputs only. Do not generate the dependency graph.",
        context_key="dependency_repair_context",
        context=context,
        expected_schema="dependency_repair_patch/v1",
        forbidden_fields=["final dependency_graph", "new module", "new file", "new function", "new capability", "new protocol fact", "code"],
        validator="validate_dependency_repair_patch",
    )
