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
    "The first character must be '{' and the last character must be '}'. "
    "If you cannot satisfy the task, return a schema-valid JSON object with warnings or unresolved_questions instead of prose."
)

ID_REFERENCE_RULES = [
    "Reference IDs, ID lists, type refs, and dependency refs must be copied exactly from the context or the legal_id_universe unless the field is explicitly listed in local_id_rules.",
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

LOCAL_ID_RULES = {
    "type_filling_candidate/v1": [
        "candidate_id is a new local ID and should use candidate:type_filling:{module_id}.",
        "slot_fillings[].slot_id must be copied exactly from type_planning_space mandatory_type_slots, derived_type_slots, or recommended_type_slots.",
        "Do not assign final required type_id/name/module_id/kind/visibility; those are deterministic slot identity.",
        "optional_type_proposals[].proposal_key is local. optional_type_proposals[].name_hint is only a hint; deterministic reconciliation assigns final identity.",
        "dependencies, fields[].type_ref, and callback_signature.params[].type_ref are references and must use type_planning_space.allowed_type_refs or system types.",
    ],
    "function_annotation_candidate/v1": [
        "candidate_id is a new local ID and should use candidate:function_annotation:{module_id}.",
        "seed_annotations[].seed_id must be copied exactly from function_planning_space required or recommended seeds.",
        "Do not assign final required function_id/name/module_id/kind/visibility; those are deterministic seed identity.",
        "optional_function_proposals[].proposal_key is local. optional_function_proposals[].name_hint is only a hint; deterministic reconciliation assigns final identity.",
        "capability_ids, covers_handler_ids, covers_message_ids, and covers_field_ids are references and must come from function_planning_space.legal_refs.",
    ],
}

STAGE_SEMANTIC_RULES = {
    "core_design_candidate/v1": [
        "canonical_types describe implementation-facing public C type contracts, not raw protocol message labels; keep protocol identity in type_id, source_message_ids, source_field_ids, and trace_ref_keys.",
        "canonical_types[].name must be the C ABI type symbol a coder can place in a public header, preferably protocol/module-prefixed, such as mqtt_connect_payload_t rather than a bare protocol surface name.",
        "handler_matrix[].trigger is a coverage key, not prose. For every handler_requirements[].surface, either one handler_matrix[].trigger must exactly equal that surface or one unresolved_questions[].target_id must exactly equal that surface.",
        "owner_module_id values must come from selected modules.",
        "capability_id and source_capability_ids values must come from required capabilities.",
        "message_ids and source_message_ids must come from legal message IDs; source_field_ids must come from legal field IDs; related_constraint_ids must come from legal constraint IDs.",
        "Do not mention files, functions, calls, imports, dependency graphs, or code in this stage output.",
    ],
    "module_artifacts_candidate/v1": [
        "modules[].module_id values must come from selected modules, and every selected architecture module must have exactly one module artifact entry.",
        "Every non-support module must declare at least one artifact. Artifacts are the module's concrete C-facing TYPE/FUNC seed inventory, not reasoning notes.",
        "artifacts[] may contain only name, kind, and role. Do not output public_api_policy, capability ownership claims, state ownership claims, constraint bindings, or nested reasoning structures.",
        "artifact kind must be TYPE or FUNC. artifact name must be a C-friendly symbol (such as mqtt_connect_t, ftp_connect_t, etc.); prefer a protocol prefix (such as mqtt_**, ftp_**, etc.) and avoid bare names such as connect, read, write, close, send, publish, or subscribe.",
        "For broker/server/client role-composition modules, include lifecycle or app boundary FUNC artifacts such as protocol-prefixed create/run/destroy functions or main.",
        "For network/runtime modules, expose connection/server/callback TYPE artifacts and read/send/flush/close-style FUNC artifacts when applicable.",
        "For codec modules, expose packet/container/buffer TYPE artifacts plus decoder and encoder FUNC artifacts when applicable.",
        "For session, topic/resource, router, and app modules, expose the core module TYPE and the minimum cross-module FUNC artifacts needed by downstream implementation.",
        "Do not concentrate all artifacts in the codec module; cover the implementation boundaries present in selected_modules.",
        "FUNC artifacts seed the next function inventory stage. TYPE artifacts seed canonical type/header/data lowering.",
        "If facts are insufficient, generate a reasonable minimum_v1 artifact and record the assumption; only blocking issues belong in unresolved_questions.",
    ],
    "type_filling_candidate/v1": [
        "Task definition: fill semantic details for the provided deterministic type_planning_space; do not generate a complete type inventory.",
        "Public_header types are coder ABI declarations. Their names must be stable C symbols that identify the protocol/module representation, while protocol message names remain evidence and trace identity.",
        "Preserve every mandatory, derived, and recommended slot identity. Use slot_fillings[].slot_id to attach fields, enum_values, callback_signature, ownership/lifetime, dependencies, assumptions, and unresolved questions.",
        "The LLM is a local semantic proposal generator only. It may not delete, rename, re-module, re-kind, or change visibility for required slots.",
        "optional_type_proposals are allowed only for justified module-local expansion. Treat callback/event/visitor outputs as ABI type boundaries: propose a public callback_type for a single function-pointer role and an event_struct for a callback collection when a module reports events, iterates matches, dispatches timers, or returns items through caller callbacks.",
        "Use only type_planning_space.allowed_type_refs for dependencies and type_ref fields. Never reference provider private types, unrelated module private types, state/message/field ids as type refs, or natural-language type names.",
        "Declare ownership and lifetime for every pointer, string, and buffer field. Owned/resource/container/result types must declare lifecycle cleanup/free intent, but do not generate real function signatures or behavior.",
        "If lifecycle.created_by/initialized_by/destroyed_by/freed_by names are provided, they must be concrete module-owned function names, not event or concept labels such as expiry, timeout, callback, owner, or caller.",
        "Scalar/value ownership means the field value belongs to its containing struct; only owned pointer/string/buffer storage requires cleanup/free lifecycle.",
        "Do not generate function inventory, signature, behavior, wire mapping, calls_allowed, file layout, dependency graph, or code.",
    ],
    "function_annotation_candidate/v1": [
        "Task definition: annotate deterministic function_planning_space seeds and propose only budgeted module-local helpers for concrete implementation boundaries in C code; do not generate a complete function inventory.",
        "Preserve every required seed identity. Use seed_annotations[].seed_id to attach purpose, grouping_hint, trace_ref_keys, and status only.",
        "The LLM may not delete, rename, re-module, re-kind, re-visibility, or re-export any mandatory, obligation, handler, parser, serializer, or recommended seed.",
        "optional_function_proposals must be module-local helpers with expansion_reason, family, source_refs, and legal related capability/message/field/handler refs.",
        "function_planning_space.function_budget is authoritative and is calibrated from specs-example/mqtt_specs FUNCTION_SPEC counts.",
        "The accepted module inventory should stay within function_budget.module_soft_cap unless required seeds already exceed it.",
        "optional_function_proposals length MUST be <= function_planning_space.optional_expansion_policy.max_optional_functions.",
        "Use example_baseline as a density guard, not a reason to collapse responsibilities into configure_runtime/check_preconditions/classify_message-style placeholders.",
        "First annotate recommended_function_families and function_planning_space.source_context.decomposition_context.recommended_concrete_slots_by_rule when they correspond to real implementation actions such as read_u16/read_string/remaining_length, connection_read/send/flush, session_manager_add/get/remove, topic_match/entry_remove_sid, handle_packet, or on_data_cb.",
        "Optional helpers are for gaps after required and recommended seeds: add them only for repeated logic, resource ownership, parser/serializer primitives, registry lookup/mutation, dispatch callbacks, cleanup/error paths, or other coder-critical boundaries.",
        "Do not propose one helper per message, field, state, timer, or error; choose the smallest concrete helper family that removes real implementation complexity.",
        "Rank optional_function_proposals by implementation necessity and engineering role fit; reconciliation keeps earlier proposals first when budget trimming is needed.",
        "When the budget prevents covering an expected concrete boundary, record decomposition_notes, assumptions, or unresolved_questions instead of adding more helpers.",
        "For optional helpers, family may reuse a selected expected/concrete family from decomposition_context, but name_hint and purpose must name a concrete implementation action as a C helper; do not copy abstract family names such as lifecycle_control, lookup_or_match, primitive_reader_or_tokenizer, configure_runtime, or check_preconditions into names or purpose text.",
        "Do not propose cross-module private helper calls or unknown refs. If evidence is missing, record assumptions or unresolved_questions instead of inventing protocol facts.",
        "Do not generate signatures, input/output contracts, behavior, state_access, wire mapping, calls_allowed, file layout, dependency graph, or code.",
    ],
    "function_signature_patch/v1": [
        "Patch existing function_id values only; never add a new function.",
        "function_signature_updates must include exactly one update for every function in the batch.",
        "Start from required_update_skeleton and use only the compact function_signature_context, especially signature_type_table, provider_public_types, module_summary, signature_style_guide, signature_normalization_policy, and legal_id_universe.",
        "Return exactly one function_signature_update for each batch function and no updates for non-batch functions; deterministic normalization will fill missing skeleton entries and discard out-of-batch updates.",
        "signature.name must match the existing function name.",
        "Focus on semantic signature design: return_type, parameter names/types/directions/nullability, public API type exposure, and type dependencies.",
        "Mechanical fields such as storage_class/raw static prefix, dependency_scope normalization, system owner cleanup, and by-value ownership cleanup are canonicalized after output.",
        "signature.raw should be a C function declarator without a trailing semicolon, matching specs-example FUNCTION_SPEC SIGNATURE.RAW style; the normalizer will align storage class details.",
        "Public C symbols must be globally unique. If the existing inventory name is generic or duplicated across modules, preserve signature.name and record an unresolved question instead of inventing an inconsistent raw symbol.",
        "For public lifecycle APIs, use module-owned protocol-prefixed names already present in the inventory and include concrete context/config parameters needed by the role, such as port, callbacks, user context, or the module handle.",
        "For parser/serializer helpers, prefer concrete buffer/cursor/out-param shapes like const uint8_t* + length + position + typed out parameter; avoid generic void* packet/message when a public packet or payload type is available.",
        "For exported=true or api_surface=public functions, design a stable ABI boundary: signature.raw, signature.name, return_type, and every param name/type must lower into a C header without exposing private/internal-only types except through public opaque handles.",
        "Public opaque handles hide layout and must cross the ABI by pointer or be replaced with a concrete public scalar/struct type; do not return or pass an opaque_handle typedef by value.",
        "Public callback/event/visitor parameters must use an existing named public callback_type from signature_type_table or provider_public_types; do not encode them as anonymous C function pointers. If no suitable named callback_type exists, keep the conservative skeleton shape or record an unresolved question for type inventory.",
        "signature.params[].type is the C spelling; type_ref may use only legal_id_universe.type_ids or legal_id_universe.system_type_ids.",
        "For public functions, non-primitive parameter and return types must be canonical_types, named public callback_types, system types, or explicitly public opaque declarations; do not rely on compiler guesses.",
        "C primitive, POSIX, or common network types not present in the type pool must use an empty type_ref.",
        "Never use state_ids, message_ids, field_ids, paths, filenames, or natural-language names as type_ref.",
        "Header-facing public API dependencies should reference only public/exported types or system types; use public opaque handles or unresolved_questions for private implementation types.",
        "signature_dependencies describe type/header needs only; do not generate imports_allowed, calls_allowed, files, dependency graphs, or code.",
        "Keep assumptions and unresolved_questions minimal; prefer empty arrays when validation-safe.",
    ],
    "function_behavior_contract_patch/v1": [
        "Patch existing function_id values only; never add a new function and never change signatures.",
        "function_behavior_updates must include exactly one update for every function in the batch.",
        "Use only the current batch, required_update_skeleton, behavior_quality_policy, module_state_access_policy, module_resource_refs, provider_public_api_summary, engineering_constraints, and legal_id_universe.",
        "For every function, contract.action must be a coder-facing imperative summary of what the C function does, not a restatement of the function name.",
        "For exported=true or api_surface=public functions, contract.input, contract.action, contract.output, thread_safety, invariants_used, and error propagation/return policy must be complete enough for coder-facing SOURCE.INTERFACE and FUNCTION_SPEC lowering.",
        "The current batch function signatures are complete context; use return types and parameters to derive contract.input, contract.output, failure paths, and resource/state effects.",
        "Do not emit behavior updates for non-batch functions.",
        "service_requirements may describe needed operations/capabilities but must not contain callee_function_id.",
        "service_requirements[].required_capability_ids must come from function_behavior_context.service_requirement_policy.allowed_required_capability_ids or the current function capability_ids; do not invent runtime capability IDs such as memory_allocation or heap_allocation.",
        "Use requirement_kind=cross_module_service only when another existing module should provide the operation.",
        "Use requirement_kind=external_runtime_service for socket, epoll, malloc, timer, or OS/runtime operations; do not force those into internal calls.",
        "Do not create service_requirements for a provider function's own owned responsibility.",
        "error_behavior.recovery must not use log_only; use none, retry, cleanup, reset_state, close_connection, or unknown.",
        "service_requirements.failure_policy must not use log_only; use ignore, return_error, cleanup_and_return, close_connection, or unknown.",
        "For handler, EVENT, resource_lifecycle, state_machine, parser, serializer, or non-trivial internal_helper functions, invariants_used should name concrete invariants such as buffer bounds, remaining length, fd/session ownership, subscription index consistency, state transition legality, or cleanup idempotence; if no invariant is knowable, add an unresolved_questions item for that function.",
        "If logic_kind is EVENT, event_contract must fully populate trigger, precondition, input, action, state_change, response, and event_type so the merge/lowering path can preserve coder_function_type=EVENT.",
        "If those EVENT fields are not knowable, use logic_kind LOGIC and keep the event-like intent in contract.action.",
        "state_access.access_kind write or read_write is allowed only when the function's module owns that state. Otherwise use read, omit that state access, or add unresolved_questions.",
        "state_access write or read_write is allowed only for module_state_access_policy.writable_state_ids.",
        "For state owned by another module, use read access only when necessary, or describe the need as a cross_module_service requirement.",
        "Do not restate behavior for non-batch functions and do not repeat full module/global design context.",
        "Keep contract.input, contract.action, and contract.output concise and coder-facing.",
        "Do not generate calls_allowed, wire mappings, files, dependency graphs, or code.",
    ],
    "wire_access_binding_patch/v2": [
        "direction=parse may bind only parser functions; direction=serialize may bind only serializer functions.",
        "Every target wire field must be covered by a direction=parse or direction=serialize wire_mapping_entry, or by unresolved_questions with that exact field_id as target_id.",
        "Parser and serializer functions must not write state through access paths; their access_path_entries[].access_kind must be read.",
        "If no suitable parser or serializer exists for a required field, add an unresolved_questions item whose target_id is the exact field_id.",
        "access path entries describe C access expressions for existing fields; do not invent fields.",
        "Each access_path_entry must include a non-empty path and c_type so it can lower to coder PATH/TYPE/ROLE.",
        "Each wire_mapping_entry must include packet_name, wire_field, strategy, target_path, source_expr, and rule for coder lowering.",
        "forbidden_symbols must stay a string list; when targeting a function, use '<function_id>|<KIND>|<NAME>|<REASON>' so merge can attach it to that function.",
        "Do not add new functions, messages, fields, states, calls, dependency graphs, or code.",
    ],
    "calls_allowed_candidate/v2": [
        "caller_function_id and callee_function_id must come from existing functions.",
        "Do not create self-calls.",
        "Do not call private or static functions across module boundaries.",
        "Plan only cross-module service call edges for service requirements listed in expected_cross_module_service_requirements.",
        "Do not plan same-module helper calls, parser/serializer delegates, lifecycle helpers, cleanup/error-handling helpers, or utility calls; those are generated deterministically before this prompt.",
        "It is acceptable to return call_updates only for callers with cross-module service requirements; deterministic normalization will merge your edges with the baseline and fill missing callers.",
        "For each expected_cross_module_service_requirements item, either bind it to a concrete public/provider call edge or leave it unresolved; deterministic normalization closes any omitted expected service IDs as unresolved.",
        "Every edge you generate must carry at least one service_requirement_id from expected_cross_module_service_requirements.",
        "External runtime service requirements such as socket, epoll, malloc, timer, or OS/runtime operations must not become internal call edges and must not be listed in unresolved_service_requirements.",
        "callee_function_id must come from candidate_provider_functions.provider_function_ids; if no candidate is correct, list the service requirement in unresolved_service_requirements.",
        "The planned calls_allowed graph must avoid prohibited cycles.",
        "Do not bind an outbound delivery requirement to another module's inbound/process handler; if no explicit delivery helper or callback provider exists, list that requirement in unresolved_service_requirements.",
        "Do not generate imports, file graphs, dependency graphs, new functions, or code.",
    ],
    "runtime_entrypoint_candidate/v1": [
        "Plan only the process/runtime entrypoint for starting the deployable target. Do not move protocol flow logic into main.c.",
        "key_flow_module_id must be an existing module that owns the main broker/server/client/application flow boundary.",
        "The entrypoint may name lifecycle function IDs for create, start, run, and destroy. Reuse only existing public lifecycle APIs from the key-flow module; do not point lifecycle_function_ids at message handlers.",
        "source_path should normally be main.c. The runtime entrypoint is logically not a protocol module, but the source file will be archived under key_flow_module_id for coder compatibility.",
        "entrypoint_signature should normally be int main(int argc, char** argv).",
        "startup_sequence should describe parse args, create, start, run, destroy, and status return; it must reference lifecycle IDs where applicable.",
        "Do not generate final dependency_graph, code, protocol handlers, parser/serializer logic, or broker/server/client message processing details.",
    ],
    "file_layout_candidate/v2": [
        "5.5a is the authority for the actual engineering file layout; use module_artifacts[].files only as seed/reference, not as mandatory final FILES.",
        "Each files[] item is one source_header_pair FILE_SPEC unit with source_path and header_path.",
        "file_id must use the source path without the .c suffix, prefixed with file:, for example file:protocol/module/module.",
        "Plan files by real module responsibilities, not by mechanically assigning one source/header pair per module.",
        "Non-trivial modules should split into multiple source_header_pair units when public APIs, runtime callbacks, parser/serializer primitives, resource managers, routing structures, or private helper clusters have different ownership or include needs.",
        "For MQTT-like brokers, prefer role-aware units where applicable: network may split connection and tcp_server; codec may split packet model, decoder, and encoder; session may split session and session_manager; routing may split topic_tree and message_router; broker/app may keep broker lifecycle separate from main/runtime entrypoint.",
        "A small facade-only or support module may remain one source_header_pair, but the rationale must reflect the function clusters rather than module count.",
        "Do not create separate header or source file items.",
        "Do not add functions; every existing function must have exactly one function_file_assignment.",
        "exports_function_ids is the header declaration intent for public API functions; every exported=true, api_surface=public, or visibility=public function must appear in exactly one exports_function_ids list.",
        "exports_type_ids is the public header type ABI surface for this file; export the concrete prefixed representation type that coder code should include, not every semantic/canonical protocol concept covered by the file.",
        "exports_type_ids may contain only IDs copied from file_layout_context.public_exportable_type_ids; never export ordinary type_inventory IDs, internal cursor/result/context/state types, state IDs, message IDs, field IDs, filenames, paths, or natural-language type names.",
        "imports_allowed may reference only other files[].file_id FILE_SPEC values from this same candidate.",
        "imports_allowed must not reference header IDs, .h files, .c files, paths, or the file's own file_id.",
        "Public functions must have declaration_file_id equal to their FILE_SPEC file_id.",
        "Private or static functions must not have declaration_file_id.",
    ],
    "file_layout_override_patch/v1": [
        "This patch may only adjust an existing deterministic file layout baseline.",
        "Prefer keep_baseline=true unless a validator error or mechanical layout warning requires a small change.",
        "file_responsibility_overrides may only reference baseline file_id values and may only change responsibility text or trace_ref_keys.",
        "function_reassignments may only move an existing function to an existing baseline file in the same module.",
        "force_single_unit_module_ids may only name existing module ids when the baseline split is worse than a single cohesive unit.",
        "Do not create new files, paths, file_ids, modules, functions, imports_allowed, dependency graphs, or code.",
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
                "Return one strict JSON object matching architecture_candidates/v1; json.loads(response_text) must succeed. "
                "You may generate module-level design only. Do not generate file paths, functions, call graphs, include graphs, or code. "
                "The capability group hints are non-binding engineering priors. They are not required module names. "
                "You may split, merge, rename, or ignore them if the protocol profile suggests a better architecture. "
                "Prefer protocol-domain module names inferred from protocol.name, target_role, required_surface_units, and profile_summary."
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
                                        "dependency_hints": ["provider module_id"],
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
                    "domain_derivation_instructions": [
                        "First infer protocol-domain nouns from architecture_context.protocol.name, protocol.target_role, required_surface_units, profile_summary, capability evidence, and constraint obligations.",
                        "Use those nouns to name modules before falling back to generic layers. For MQTT broker, natural module names may include broker_app, session, topic, router, protocol_codec, and network. For FTP server, natural module names may include server_app, command_codec, resource, filesystem, and session.",
                        "If statefulness is persistent_state, consider a protocol-domain session/state owner. If routing_intensity or resource_intensity is high, consider protocol-domain routing/resource modules such as topic, router, resource, mailbox, channel, filesystem, or stream where supported by the protocol facts.",
                        "Generic module names such as semantic_core, semantic_state, resource_store, and role_composition are fallback names only when no protocol-domain noun can be derived.",
                        "Assign role_composition to a high-level protocol role module such as broker_app, server_app, or client_app whenever such a role module is derivable.",
                    ],
                    "strategy_guidance": {
                        "capability_clustered": "Cluster modules around cohesive capability ownership, but choose protocol-domain ownership boundaries instead of copying capability-group hint names.",
                        "layered_runtime_codec_semantic": "Keep low-coupling runtime and codec layers where useful, but include a target-role module and protocol-domain state/routing/resource modules when the facts support them.",
                        "minimal_scope": "Prefer the smallest target-scope architecture that still satisfies every capability and constraint; small does not mean generic, so use natural protocol boundaries such as broker/session/topic/router where supported.",
                    }.get(design_strategy, "Generate a coherent module-level architecture for the target scope."),
                    "hard_validation_rules": [
                        "Return strict JSON only: no markdown, no prose, no comments, no trailing comma, and no trailing semicolon.",
                        "The top-level schema_version must be exactly architecture_candidates/v1.",
                        "Return at least one valid candidate and do not add empty placeholder candidates.",
                        "Every required_capability_ids entry must appear in at least one module.owned_capabilities.",
                        "Every module.owned_capabilities entry must come from required_capability_ids.",
                        "A non-support module must own at least one capability.",
                        "Capabilities mentioned in responsibilities, rationale, or consumed_capabilities do not count as covered.",
                        "semantic_dispatch, role_composition, and canonical_type_ownership are real capabilities when present and must be explicitly owned.",
                        "Every module must include dependency_hints to express engineering dependencies; [] is allowed only when the module has no provider dependency.",
                        "module.dependency_hints direction is consumer module -> provider module.",
                        "High-level role modules may depend on lower-level network, protocol_codec, session, topic/resource/router, and data-model modules.",
                        "Lower-level network, codec, data-model, parser, serializer, resource, and state owner modules must not depend on high-level broker/server/client role modules.",
                        "Every dependency_hints entry must reference an existing module_id, must not reference the same module, and the full dependency_hints graph must be a DAG.",
                        "Before returning, check the dependency_hints graph for cycles; if a cycle exists, remove the less certain edge instead of representing mutual collaboration as dependencies.",
                        "module_graph_hints must be []. Architecture still must not output a final dependency_graph; Step 6 derives the final graph deterministically from implementation-plan dependency inputs.",
                        "Capability group hints are non-binding priors, not required module names.",
                        "You may split, merge, rename, or ignore hints.",
                        "The selected architecture will be judged by capability coverage, cohesion, coupling, constraint satisfaction, and acyclicity.",
                        "Before returning, compute required_capability_ids minus the union of module.owned_capabilities; it must be empty.",
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
                "This is a machine API boundary: any natural-language text outside the JSON object is a failed response. "
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
                        "Output exactly one parseable JSON object and no text before or after it.",
                        "Do not wrap the JSON in markdown fences.",
                        "Do not explain your reasoning outside JSON fields.",
                        "The top-level schema_version must be exactly architecture_ranking/v1.",
                        "Score every candidate_id exactly once.",
                        "selected_candidate_id must be one of the candidate IDs.",
                        "Scores must evaluate capability coverage, constraint satisfaction, cohesion, coupling, acyclicity, state ownership clarity, testability, implementation simplicity, and target scope fit.",
                        "Do not output modified architecture candidates.",
                    ],
                    "application_protocol_engineering_experience": [
                        "Rank for implementable C source/header boundaries, not for the fewest modules. A good candidate maps naturally to files such as network connection/server, codec packet/decoder/encoder, session/resource manager, topic/routing, and one broker/server/client app boundary.",
                        "Reward candidates that separate transport/runtime callbacks, codec/data model, session/resource ownership, routing/dispatch, and broker lifecycle when those capabilities exist in the profile.",
                        "Reward clear state/resource ownership: one module owns each mutable state/resource family, lower-level modules do not depend back on broker/app flow modules, and dependency_hints are acyclic consumer -> provider intent.",
                        "Score down minimal_scope or catch-all candidates that compress transport, codec, state/session, routing, and app orchestration into coarse buckets merely to reduce module count.",
                        "Score down candidates whose modules are label buckets without realistic public API, private helper, test seam, or source/header unit cohesion.",
                        "Implementation simplicity means coder-friendly boundaries, small compile units, and testability; it does not mean choosing the candidate closest to five modules.",
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
    payload: dict[str, Any] = {
        "prompt_name": prompt_name,
        "prompt_version": PROMPT_REGISTRY[prompt_name],
        "task": task,
        "output_schema": expected_schema,
        "output_shape": output_shape(expected_schema),
        "forbidden_fields": forbidden_fields,
        "validator_after_output": validator,
    }
    if expected_schema.endswith("_repair_patch/v1"):
        payload["repair_output_contract"] = [
            f"Return only a {expected_schema} patch object.",
            "Do not return a full candidate object.",
            "Do not include candidate_id, full types/functions arrays, markdown, prose, comments, or analysis.",
            "Make the smallest patch that fixes repair_target_errors while preserving stable_content_to_preserve.",
        ]
    payload.update(
        {
            context_key: context,
            "id_reference_rules": ID_REFERENCE_RULES,
            "local_id_rules": LOCAL_ID_RULES.get(expected_schema, []),
            "enum_usage_rules": ENUM_USAGE_RULES,
            "semantic_validation_rules": STAGE_SEMANTIC_RULES.get(expected_schema, []),
            "hard_validation_rules": [
                f"schema_version must be exactly {expected_schema}.",
                "The JSON object must match output_shape exactly.",
                "Return the candidate or patch object itself.",
                "Do not include a complete implementation_plan/v1.",
                "Do not include forbidden fields.",
                "Do not include any fields not shown in output_shape.",
                "Reference IDs must come from the context legal ID universe; only fields listed in local_id_rules may introduce new candidate-local IDs.",
                "If information is insufficient, add unresolved_questions instead of inventing facts.",
            ],
        }
    )
    return [
        {
            "role": "system",
            "content": (
                "You are SpecForge Planning Agent staged implementation-plan assistant. "
                f"{JSON_ONLY_RULES} "
                f"Return JSON matching {expected_schema}. "
                "Return only the current stage candidate or patch. "
                "For repair patch prompts, return only the small patch object and never return a full candidate. "
                "Never return a complete implementation_plan/v1. "
                "Never output code. "
                "Do not invent protocol facts or reference identifiers outside the provided legal ID universe. "
                "Generate only the candidate-local identifiers explicitly allowed by local_id_rules."
            ),
        },
        {
            "role": "user",
            "content": _compact(payload),
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


def module_artifacts_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="module_artifacts_candidate_prompt",
        task="Plan concrete module artifacts from the selected architecture and accepted core design.",
        context_key="module_artifacts_context",
        context=context,
        expected_schema="module_artifacts_candidate/v1",
        forbidden_fields=["public_api_policy", "capability_ownership_claims", "state_ownership_claims", "constraint_bindings", "function_id", "function list", "calls_allowed", "imports_allowed", "code"],
        validator="validate_module_artifacts_candidate",
    )


def type_filling_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="type_filling_candidate_prompt",
        task="Fill deterministic type planning slots from the compact scoped context and propose only justified module-local optional types. Return only type_filling_candidate/v1.",
        context_key="type_filling_context",
        context=context,
        expected_schema="type_filling_candidate/v1",
        forbidden_fields=["type_id", "function_id", "signature", "input_contract", "output_contract", "state_access", "wire_mapping", "access_paths", "calls_allowed", "file_id", "dependency_graph", "code"],
        validator="validate_type_filling_candidate",
    )


def function_annotation_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_annotation_candidate_prompt",
        task=(
            "Annotate deterministic function planning seeds into concrete implementation boundaries and propose only budgeted, necessary module-local helpers. "
            "Respect function_planning_space.function_budget while preferring coder-useful helper slots over abstract placeholder helpers, "
            "and return only function_annotation_candidate/v1."
        ),
        context_key="function_annotation_context",
        context=context,
        expected_schema="function_annotation_candidate/v1",
        forbidden_fields=["function_id", "signature", "input_contract", "output_contract", "state_access", "wire_mapping", "access_paths", "calls_allowed", "dependency_graph", "file_id", "code"],
        validator="validate_function_annotation_candidate",
    )


def function_signature_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_signature_patch_prompt",
        task="Lower the current function batch into coder-quality C signatures close to specs-example style, using only the compact scoped signature context.",
        context_key="function_signature_context",
        context=context,
        expected_schema="function_signature_patch/v1",
        forbidden_fields=["new function_id", "new file_id", "new module_id", "state_access", "wire_mapping", "access_paths", "calls_allowed", "dependency_graph", "code"],
        validator="validate_function_signature_patch",
    )


def function_behavior_contract_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="function_behavior_contract_patch_prompt",
        task="Patch concise behavior contracts, state/resource access, internal type refs, and unresolved service requirements for the current function batch using only the scoped behavior context; do not resolve service requirements into concrete call edges.",
        context_key="function_behavior_context",
        context=context,
        expected_schema="function_behavior_contract_patch/v1",
        forbidden_fields=["new function_id", "signature changes", "wire_mapping", "access_paths", "callee_function_id", "calls_allowed", "dependency_graph", "code"],
        validator="validate_function_behavior_contract_patch",
    )


def wire_access_binding_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="wire_access_binding_patch_prompt",
        task="Bind protocol wire fields to existing parser/serializer coder-lowerable wire mappings and access descriptors; access paths are field descriptors, not state mutation.",
        context_key="wire_access_binding_context",
        context=context,
        expected_schema="wire_access_binding_patch/v2",
        forbidden_fields=["new function", "new message", "new field", "new state", "calls_allowed", "dependency_graph", "code"],
        validator="validate_wire_access_binding_patch",
    )


def calls_allowed_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="calls_allowed_candidate_prompt",
        task="Plan only cross-module service call contracts for expected_cross_module_service_requirements; deterministic normalization will merge same-module helper, lifecycle, cleanup, parser, and serializer calls.",
        context_key="calls_allowed_context",
        context=context,
        expected_schema="calls_allowed_candidate/v2",
        forbidden_fields=["new function", "new file", "new module", "imports_allowed", "dependency_graph", "include graph", "code"],
        validator="validate_calls_allowed_candidate",
    )


def runtime_entrypoint_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="runtime_entrypoint_candidate_prompt",
        task="Plan one source-only runtime entrypoint for starting the deployable target. Reuse existing lifecycle APIs when present and keep protocol flow logic in the key flow module.",
        context_key="runtime_entrypoint_context",
        context=context,
        expected_schema="runtime_entrypoint_candidate/v1",
        forbidden_fields=["protocol handler logic", "parser/serializer logic", "new protocol module", "final dependency_graph", "include graph", "code"],
        validator="validate_runtime_entrypoint_candidate",
    )


def file_layout_candidate_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="file_layout_candidate_prompt",
        task="Plan the actual role-aware C source_header_pair file layout, split non-trivial modules by existing function clusters, and assign existing functions to files.",
        context_key="file_layout_context",
        context=context,
        expected_schema="file_layout_candidate/v2",
        forbidden_fields=["new function_id", "new function name", "function implementation details", "function call graph", "final dependency_graph", "code"],
        validator="validate_file_layout_candidate",
    )


def file_layout_override_patch_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return _stage_messages(
        prompt_name="file_layout_override_patch_prompt",
        task="Review the deterministic file layout baseline and return the smallest schema-valid override patch only when needed.",
        context_key="file_layout_override_context",
        context=context,
        expected_schema="file_layout_override_patch/v1",
        forbidden_fields=["new file_id", "new source_path", "new header_path", "new function_id", "imports_allowed", "dependency_graph", "code"],
        validator="validate_file_layout_override_patch",
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
