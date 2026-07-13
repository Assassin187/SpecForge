from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Protocol

from .amendment import REQUIRED_REQUEST_FIELDS
from .facts import select_fact_slice
from .registry import CanonicalPlanningRegistry, RegistryInvariantError


class PromptStage(Protocol):
    stage_id: str
    title: str
    purpose: str
    required_output: dict[str, Any]
    engineering_focus: list[str]


_PLANNING_SYSTEM_PROMPT = (
    "You are the SpecForge planning agent. Return only valid JSON. "
    "Never modify protocol facts, never generate C source code, and never use a fixed protocol template."
)


def _stage_artifact(previous_artifacts: list[dict[str, Any]], stage_id: str) -> dict[str, Any]:
    for item in reversed(previous_artifacts):
        if item.get("stage_id") == stage_id and isinstance(item.get("artifact"), dict):
            return item["artifact"]
    return {}


def _stable_artifact_key(item: dict[str, Any], kind: str) -> str:
    if kind == "function":
        return str(item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name") or "")
    return str(item.get("type_id") or item.get("id") or item.get("type_name") or item.get("symbol") or item.get("name") or "")


def _stage_artifact_boundary(
    stage_id: str,
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry | None = None,
) -> dict[str, Any]:
    inventory = _stage_artifact(previous_artifacts, "public_artifact_inventory")
    interfaces = _stage_artifact(previous_artifacts, "function_interface_design")
    layout = _stage_artifact(previous_artifacts, "module_file_plan")
    type_ids = [
        _stable_artifact_key(item, "type")
        for item in inventory.get("types", [])
        if isinstance(item, dict) and _stable_artifact_key(item, "type")
    ]
    inventory_function_ids = [
        str(item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name"))
        for item in inventory.get("functions", [])
        if isinstance(item, dict) and (item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name"))
    ]
    canonical_function_ids = [
        _stable_artifact_key(item, "function")
        for item in interfaces.get("function_interfaces", [])
        if isinstance(item, dict) and _stable_artifact_key(item, "function")
    ]
    if registry is not None:
        type_ids = [item["artifact_id"] for item in registry.typed_view({"type", "callback"})]
        inventory_function_ids = [item["artifact_id"] for item in registry.typed_view({"function"})]
        canonical_function_ids = inventory_function_ids
    contracts: dict[str, dict[str, Any]] = {
        "module_file_plan": {
            "mode": "canonical_inventory",
            "rule": "Return the closed module/file inventory. Every non-main source requires a unique dedicated header path.",
        },
        "public_artifact_inventory": {
            "mode": "canonical_inventory",
            "visibility_schema": {"type": "string", "enum": ["public", "private"]},
            "artifact_kind_rule": "Use kind=callback only for callbacks; representation such as opaque/struct/enum belongs to Stage 5 definition_overlay, not visibility.",
            "rule": "Return the closed type/function identity inventory, including callback and runtime entrypoint identities. Do not forbid any symbol present in this inventory.",
        },
        "type_and_access_path_design": {
            "mode": "typed_delta",
            "join_key": "type_id",
            "allowed_stable_ids": type_ids,
            "type_id_schema": {"type": "string", "enum": type_ids},
            "allowed_overlay_fields": [
                "type_spec",
                "type_kind",
                "fields",
                "values",
                "signature",
                "c_type",
                "role",
                "ownership_semantics",
                "ownership_model",
                "ownership_fields",
                "opaque_boundaries",
                "resource_handle",
                "trace_refs",
                "fact_refs",
                "access_paths",
                "wire_mapping",
            ],
            "forbidden_identity_fields": ["id", "type_name", "symbol", "name", "owner_file", "file", "visibility", "artifact_kind", "kind"],
            "rule": "Return only {type_id, definition_overlay}. Report an artifact_request instead of adding identity.",
        },
        "function_interface_design": {
            "mode": "canonical_definition",
            "join_key": "function_id",
            "allowed_stable_ids": inventory_function_ids,
            "required_identity_fields": ["function_id", "owner_file", "trace_id", "function_type", "visibility", "signature"],
            "rule": "Define only these function IDs, once each, with a complete C declaration; a function name alone is not a signature.",
        },
        "function_behavior_design": {
            "mode": "overlay_only",
            "join_key": "function_id",
            "allowed_stable_ids": canonical_function_ids,
            "allowed_fields": ["function_id", "trace_id", "LOGIC", "EVENT", "wire_mapping", "trace_refs"],
        },
        "function_call_contract_closure": {
            "mode": "typed_call_edges",
            "join_key": "caller_function_id|callee_function_id",
            "function_id_schema": {"type": "string", "enum": canonical_function_ids},
            "required_edge_fields": [
                "caller_function_id",
                "callee_function_id",
                "call_purpose",
                "condition",
                "argument_semantics",
                "result_usage",
            ],
            "forbidden_fields": ["NAME", "SIGNATURE", "owner", "owner_file", "visibility", "RELY"],
            "rule": "Return typed call intent only. The registry/compiler derives names, signatures, RELY.FUNC, CALL_CONTRACTS, and dependencies.",
        },
        "function_test_vector_design": {
            "mode": "overlay_only",
            "join_key": "function_id",
            "allowed_stable_ids": canonical_function_ids,
            "allowed_fields": ["function_test_vectors", "file_test_vectors", "runtime_test_vectors", "test_vector_diagnostics"],
        },
        "dependency_closure": {
            "mode": "non_derivable_choices_only",
            "allowed_fields": ["ordering_choices", "architecture_choices", "artifact_requests", "dependency_diagnostics"],
            "choice_item_schema": {
                "required_fields": ["reason", "provenance", "affected_artifact_ids"],
                "reason": "non-empty explanation of why the relation is not uniquely derivable",
                "provenance": {"kind": "string", "refs": "non-empty array"},
                "affected_artifact_ids": "array of existing registry artifact IDs; use [] only when no artifact is affected",
            },
            "forbidden_inventory_fields": ["modules", "files", "functions", "types", "call_graph", "generation_order"],
            "rule": "Ownership, call, callback, foreign-type, include, module dependency, and uniquely determined generation-order edges are compiler-derived. Emit only choices that cannot be uniquely derived, with reason and provenance.",
        },
    }
    return contracts.get(stage_id, {"mode": "fact_or_architecture_analysis"})


_PROMPT_STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "scope_fact_inventory": (),
    "architecture_boundaries": ("scope_fact_inventory",),
    "module_file_plan": ("scope_fact_inventory", "architecture_boundaries"),
    "public_artifact_inventory": ("scope_fact_inventory", "architecture_boundaries", "module_file_plan"),
    "type_and_access_path_design": ("architecture_boundaries", "module_file_plan", "public_artifact_inventory"),
    "function_interface_design": ("module_file_plan", "public_artifact_inventory", "type_and_access_path_design"),
    "function_behavior_design": (
        "architecture_boundaries",
        "module_file_plan",
        "public_artifact_inventory",
        "type_and_access_path_design",
        "function_interface_design",
    ),
    "function_call_contract_closure": (
        "architecture_boundaries",
        "module_file_plan",
        "public_artifact_inventory",
        "type_and_access_path_design",
        "function_interface_design",
        "function_behavior_design",
    ),
    "function_test_vector_design": (
        "module_file_plan",
        "public_artifact_inventory",
        "type_and_access_path_design",
        "function_interface_design",
        "function_behavior_design",
        "function_call_contract_closure",
    ),
}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _prompt_input_slice(
    stage: PromptStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    partition: dict[str, Any] | None,
    registry: CanonicalPlanningRegistry | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if stage.stage_id in {"scope_fact_inventory", "architecture_boundaries"}:
        facts = context["facts"]
    else:
        facts = None
    projected = _project_previous_artifacts(stage.stage_id, previous_artifacts, partition, registry)
    if facts is None:
        refs = ["fact:protocol_meta", "fact:minimum_v1", *_collect_prompt_refs(projected)]
        refs.extend(_collect_prompt_refs(context.get("open_assumptions", [])))
        facts = select_fact_slice(context["facts"], list(dict.fromkeys(refs)))
    registry_catalog = _prompt_registry_catalog(registry) if stage.stage_id in {
        "function_call_contract_closure",
        "dependency_closure",
    } else []
    return facts, projected, registry_catalog


def _project_previous_artifacts(
    stage_id: str,
    previous_artifacts: list[dict[str, Any]],
    partition: dict[str, Any] | None,
    registry: CanonicalPlanningRegistry | None,
) -> list[dict[str, Any]]:
    if stage_id == "dependency_closure":
        diagnostics = dependency_ambiguities(previous_artifacts, {})["upstream_diagnostics"]
        return [{"stage_id": "dependency_ambiguity_input", "artifact": {"upstream_diagnostics": diagnostics}}]
    dependencies = _PROMPT_STAGE_DEPENDENCIES.get(stage_id, ())
    selected = [
        {"stage_id": item.get("stage_id"), "artifact": deepcopy(item.get("artifact", {}))}
        for item in previous_artifacts
        if item.get("stage_id") in dependencies
    ]
    if stage_id == "type_and_access_path_design" and partition is not None:
        _filter_type_partition_context(selected, partition or {}, registry)
    elif stage_id == "function_behavior_design" and partition is not None:
        _filter_function_partition_context(selected, partition or {}, registry, include_all_interfaces=False)
    elif stage_id == "function_call_contract_closure" and partition is not None:
        _filter_function_partition_context(selected, partition or {}, registry, include_all_interfaces=True)
    return selected


def _filter_type_partition_context(
    artifacts: list[dict[str, Any]], partition: dict[str, Any], registry: CanonicalPlanningRegistry | None
) -> None:
    type_ids = set(map(str, partition.get("type_ids", [])))
    owner_file_id = str(partition.get("owner_file_id", ""))
    for item in artifacts:
        artifact = item["artifact"]
        if item["stage_id"] == "module_file_plan":
            artifact["files"] = [
                value for value in artifact.get("files", [])
                if _registry_item_matches(value, owner_file_id, "file", registry)
            ]
            file_modules = {str(value.get("module", "")) for value in artifact["files"]}
            artifact["modules"] = [value for value in artifact.get("modules", []) if str(value.get("name", "")) in file_modules]
        elif item["stage_id"] == "public_artifact_inventory":
            artifact["types"] = [
                value for value in artifact.get("types", [])
                if _registry_item_id(value, "type", registry) in type_ids
            ]
            artifact["functions"] = []
            artifact.pop("public_symbol_table", None)


def _filter_function_partition_context(
    artifacts: list[dict[str, Any]],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry | None,
    *,
    include_all_interfaces: bool,
) -> None:
    function_ids = set(map(str, partition.get("caller_function_ids", [])))
    for value in partition.get("functions", []):
        artifact_id = _registry_item_id(value, "function", registry)
        if artifact_id:
            function_ids.add(artifact_id)
    owner_file_id = str(partition.get("owner_file_id") or partition.get("owner_file") or "")
    module_id = str(partition.get("owner_module_id") or partition.get("module") or "")
    for item in artifacts:
        artifact = item["artifact"]
        if item["stage_id"] == "architecture_boundaries":
            artifact_keys = {"ownership_decisions", "cross_module_services", "architecture_diagnostics"}
            item["artifact"] = {key: value for key, value in artifact.items() if key in artifact_keys}
        elif item["stage_id"] == "module_file_plan":
            files = [
                value for value in artifact.get("files", [])
                if (
                    _registry_item_matches(value, owner_file_id, "file", registry)
                    if owner_file_id
                    else not module_id or _module_reference_matches(value.get("module"), module_id, registry)
                )
            ]
            modules = [
                value for value in artifact.get("modules", [])
                if not module_id or _module_reference_matches(value.get("id") or value.get("name"), module_id, registry)
            ]
            item["artifact"] = {"files": files, "modules": modules}
        elif item["stage_id"] == "public_artifact_inventory":
            item["artifact"] = {
                "functions": [
                    value for value in artifact.get("functions", [])
                    if include_all_interfaces or _registry_item_id(value, "function", registry) in function_ids
                ],
                "types": artifact.get("types", []),
                "constants_or_macros": artifact.get("constants_or_macros", []),
                "lifecycle_matrix": artifact.get("lifecycle_matrix", {}),
                "runtime_entrypoint": artifact.get("runtime_entrypoint", {}),
            }
        elif item["stage_id"] == "function_interface_design":
            item["artifact"] = {
                "function_interfaces": [
                    value for value in artifact.get("function_interfaces", [])
                    if include_all_interfaces or _registry_item_id(value, "function", registry) in function_ids
                ]
            }
        elif item["stage_id"] == "function_behavior_design":
            item["artifact"] = {
                "function_behaviors": [
                    value for value in artifact.get("function_behaviors", [])
                    if _registry_item_id(value, "function", registry) in function_ids
                ],
                "wire_mappings": [
                    value for value in artifact.get("wire_mappings", [])
                    if _registry_item_id(value, "function", registry) in function_ids
                ],
                "behavior_diagnostics": artifact.get("behavior_diagnostics", []),
            }
        elif item["stage_id"] == "type_and_access_path_design" and module_id:
            item["artifact"] = {
                "type_definition_overlays": artifact.get("type_definition_overlays", []),
                "type_dependency_notes": artifact.get("type_dependency_notes", []),
            }


def _registry_item_id(
    item: Any, kind: str, registry: CanonicalPlanningRegistry | None
) -> str:
    if not isinstance(item, dict):
        return ""
    reference = item.get(f"{kind}_id") or item.get("id") or item.get("symbol") or item.get("name")
    if not reference:
        return ""
    if registry is None:
        return str(reference)
    try:
        expected_kinds = {"type", "callback"} if kind == "type" else {kind}
        return str(registry.resolve(str(reference), expected_kinds=expected_kinds)["artifact_id"])
    except (RegistryInvariantError, ValueError):
        return str(reference)


def _registry_item_matches(
    item: Any, artifact_id: str, kind: str, registry: CanonicalPlanningRegistry | None
) -> bool:
    return bool(artifact_id) and _registry_item_id(item, kind, registry) == artifact_id


def _module_reference_matches(
    reference: Any, module_id: str, registry: CanonicalPlanningRegistry | None
) -> bool:
    if not reference:
        return False
    if registry is None:
        return str(reference) == module_id
    try:
        left = registry.resolve(str(reference), expected_kinds={"module"})["artifact_id"]
        right = registry.resolve(module_id, expected_kinds={"module"})["artifact_id"]
        return left == right
    except ValueError:
        return str(reference) == module_id


def _collect_prompt_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"trace_refs", "fact_refs", "supporting_fact_refs"} and isinstance(child, list):
                refs.extend(str(item) for item in child if str(item).startswith("fact:"))
            else:
                refs.extend(_collect_prompt_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_prompt_refs(child))
    return refs


def _prompt_registry_catalog(registry: CanonicalPlanningRegistry | None) -> list[dict[str, Any]]:
    if registry is None:
        return []
    fields = ("artifact_id", "artifact_kind", "owner_module_id", "owner_file_id", "visibility", "status", "semantic_role")
    kinds = {"module", "file", "type", "callback", "function", "constant", "test"}
    return [{key: item.get(key) for key in fields if item.get(key) is not None} for item in registry.typed_view(kinds)]


def dependency_ambiguities(
    previous_artifacts: list[dict[str, Any]], context: dict[str, Any]
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    for item in previous_artifacts:
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        for key, value in artifact.items():
            if "diagnostic" not in key or not isinstance(value, list):
                continue
            diagnostics.extend(
                {"source_stage": item.get("stage_id"), "diagnostic_kind": key, "value": entry}
                for entry in value
            )
    return {
        "open_assumptions": deepcopy(context.get("open_assumptions", [])),
        "upstream_diagnostics": diagnostics,
    }


def build_stage_prompt(
    stage: PromptStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]] | None = None,
    partition: dict[str, Any] | None = None,
    registry: CanonicalPlanningRegistry | None = None,
) -> str:
    facts, projected_artifacts, registry_catalog = _prompt_input_slice(
        stage, context, previous_artifacts or [], partition, registry
    )
    prompt = {
        "stage": {
            "id": stage.stage_id,
            "title": stage.title,
            "purpose": stage.purpose,
            "engineering_focus": stage.engineering_focus,
            "required_output": stage.required_output,
            "artifact_boundary": _stage_artifact_boundary(stage.stage_id, previous_artifacts or [], registry),
        },
        "global_contract": {
            "input_facts_are_read_only": True,
            "do_not_invent_protocol_behavior": True,
            "do_not_emit_c_source_code": True,
            "do_not_use_reference_specs_as_instance_content": True,
            "trace_every_decision_to_fact_rule_or_assumption": True,
            "implementability_gate": {
                "all_type_function_and_callee_refs_resolve": True,
                "private_symbols_never_cross_files": True,
                "foreign_public_signature_types_are_visible": True,
                "function_rely_calls_and_file_header_dependencies_close": True,
                "opaque_and_owned_resources_have_create_use_destroy_paths": True,
                "cross_module_data_has_explicit_provider_or_access_service": True,
                "module_dependencies_match_signatures_and_calls": True,
                "callbacks_match_exact_signatures_and_user_data_has_typed_access_services": True,
                "executable_target_has_exactly_one_main_with_cleanup_chain": True,
                "coder_must_not_invent_unplanned_symbols": True,
            },
            "controlled_inventory_amendment": {
                "artifact_request_required_fields": sorted(REQUIRED_REQUEST_FIELDS),
                "optional_fields": ["proposed_name"],
                "provenance_schema": {"kind": "string", "refs": "non-empty array"},
                "rule": "Use only for a genuinely missing artifact. Remove any currently invalid typed reference; a request is not an immediately bindable artifact.",
            },
            "final_specs_dialect": {
                "module_spec": "PROTOCOL_MODULE_SPEC with PROTOCOL, MODULES, GENERATION_ORDER, CONSISTENCY_RULES",
                "file_spec": "FILE_SPEC with FILE, optional HEADER, SOURCE, PUBLIC_SYMBOLS, ACCESS_PATHS, CALL_CONTRACTS",
                "function_spec": "FUNCTION_SPEC with TRACE_ID, FUNCTION_TYPE, ROLE, SIGNATURE, RELY, LOGIC or EVENT",
            },
        },
        "facts": facts,
        "normalized_characteristics": context["characteristics"],
        "activated_engineering_rules": context["engineering_rules"],
        "open_assumptions": context["open_assumptions"],
        "previous_stage_artifacts": projected_artifacts,
    }
    if registry_catalog:
        prompt["registry_catalog"] = registry_catalog
    if partition is not None:
        prompt["current_partition"] = partition
        if stage.stage_id == "type_and_access_path_design":
            prompt["stage"]["artifact_boundary"]["type_id_schema"] = {
                "type": "string",
                "enum": partition.get("type_ids", []),
            }
            prompt["partition_contract"] = {
                "define_every_type_id_in_partition_once": True,
                "do_not_emit_identity_owner_visibility_or_artifact_kind": True,
                "use_artifact_request_for_missing_identity": True,
                "return_partition_artifact_only": True,
            }
        elif stage.stage_id == "function_call_contract_closure":
            prompt["stage"]["artifact_boundary"]["caller_function_id_schema"] = {
                "type": "string",
                "enum": partition.get("caller_function_ids", []),
            }
            prompt["partition_contract"] = {
                "only_emit_edges_for_partition_callers": True,
                "callee_must_use_function_registry_enum": True,
                "type_payload_field_and_parameter_access_are_not_call_edges": True,
                "never_fabricate_a_function_id_from_a_type_id": True,
                "remove_invalid_edge_before_artifact_request": True,
                "do_not_emit_names_signatures_owners_visibility_or_rely": True,
                "return_partition_artifact_only": True,
            }
        else:
            prompt["partition_contract"] = {
                "only_generate_behavior_for_functions_in_current_partition": True,
                "do_not_add_or_rename_functions": True,
                "do_not_generate_test_vectors": True,
                "return_partition_artifact_only": True,
            }
    return compact_json(prompt)


def build_json_repair_prompt(stage: PromptStage, raw_response: str, parse_error: json.JSONDecodeError) -> str:
    return json.dumps(
        {
            "stage_id": stage.stage_id,
            "task": "Repair this response into syntactically valid JSON only.",
            "constraints": [
                "Do not add, remove, reinterpret, or improve design content.",
                "Do not invent missing fields.",
                "Convert JSON-invalid numeric forms such as 0x48 into valid decimal integers.",
                "Remove comments and trailing commas if present.",
                "Return only the repaired JSON object.",
            ],
            "parse_error": {
                "message": str(parse_error),
                "line": parse_error.lineno,
                "column": parse_error.colno,
                "position": parse_error.pos,
            },
            "raw_response": raw_response,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_stage_messages(
    stage: PromptStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    *,
    partition: dict[str, Any] | None = None,
    registry: CanonicalPlanningRegistry | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _PLANNING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_stage_prompt(
                stage,
                context,
                previous_artifacts,
                partition=partition,
                registry=registry,
            ),
        },
    ]


def build_json_repair_messages(
    stage: PromptStage, raw_response: str, parse_error: json.JSONDecodeError
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You repair invalid JSON. Return only valid JSON. Do not add, remove, reinterpret, or improve design content. "
                "Only fix syntax such as hex literals, quotes, commas, comments, or trailing commas."
            ),
        },
        {"role": "user", "content": build_json_repair_prompt(stage, raw_response, parse_error)},
    ]


def build_local_correction_messages(
    stage: PromptStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry | None,
    artifact: dict[str, Any],
    validation_error: str,
    *,
    validation_layer: str,
    diagnostic_code: str,
    required_recovery: str,
) -> list[dict[str, str]]:
    original = json.loads(
        build_stage_prompt(
            stage,
            context,
            previous_artifacts,
            partition=None if partition.get("partition_id") == "whole_stage" else partition,
            registry=registry,
        )
    )
    local_contract = {
        key: original[key]
        for key in (
            "stage",
            "facts",
            "normalized_characteristics",
            "activated_engineering_rules",
            "open_assumptions",
            "previous_stage_artifacts",
            "registry_catalog",
            "current_partition",
            "partition_contract",
        )
        if key in original
    }
    content = compact_json(
        {
            "validation_error": validation_error,
            "validation_layer": validation_layer,
            "diagnostic_code": diagnostic_code,
            "required_recovery": required_recovery,
            "binding_recovery_invariants": {
                "all_typed_references_must_use_exact_registry_enum_ids": True,
                "remove_invalid_edges_instead_of_relabeling_types_as_functions": True,
                "type_payload_field_and_parameter_access_are_not_call_edges": True,
                "artifact_request_does_not_make_an_invalid_edge_bindable_in_this_response": True,
            },
            "invalid_partition_artifact": artifact,
            "original_partition_contract": local_contract,
        }
    )
    return [
        {
            "role": "system",
            "content": (
                f"You perform one {required_recovery} recovery for a {validation_layer} validation failure. "
                "Return only valid JSON. Preserve supported semantic decisions, obey the registry enums, and do not add "
                "identity inline. Remove every invalid typed reference before returning; never keep an invalid edge beside "
                "an ArtifactRequest. A type, payload, field, or direct parameter access is not a function call and must not "
                "be emitted as a call edge. Use ArtifactRequest when a genuinely missing artifact is required. This is the "
                "only correction attempt."
            ),
        },
        {"role": "user", "content": content},
    ]


def build_amendment_messages(
    stage: PromptStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
    *,
    artifact_id: str,
    required_by: str,
    semantic_role: str,
    contract: dict[str, Any],
) -> list[dict[str, str]]:
    prompt = json.loads(build_stage_prompt(stage, context, previous_artifacts, registry=registry))
    prompt["current_amendment_partition"] = {
        "artifact_id": artifact_id,
        "required_by": required_by,
        "semantic_role": semantic_role,
    }
    prompt["amendment_partition_contract"] = contract
    return [
        {
            "role": "system",
            "content": (
                "Generate one bounded inventory-amendment partition. Return only valid JSON. "
                "Define only the supplied canonical artifact ID and do not change any existing artifact."
            ),
        },
        {"role": "user", "content": compact_json(prompt)},
    ]
