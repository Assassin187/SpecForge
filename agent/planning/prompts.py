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
    type_design = _stage_artifact(previous_artifacts, "type_and_access_path_design")
    behavior_design = _stage_artifact(previous_artifacts, "function_behavior_design")
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
    required_wire_function_ids: list[str] = []
    if registry is not None:
        for coverage in inventory.get("implementation_coverage_matrix", []):
            if not isinstance(coverage, dict) or coverage.get("obligation_id") != "foundation:codec":
                continue
            for reference in coverage.get("artifact_ids", []):
                try:
                    entry = registry.resolve(reference)
                except (RegistryInvariantError, ValueError):
                    continue
                if entry["artifact_kind"] == "function":
                    required_wire_function_ids.append(entry["artifact_id"])
    required_wire_function_ids = sorted(set(required_wire_function_ids))
    interface_catalog = [
        {
            "function_id": _stable_artifact_key(item, "function"),
            "visibility": item.get("visibility"),
            "signature": deepcopy(item.get("signature")),
        }
        for item in interfaces.get("function_interfaces", [])
        if isinstance(item, dict) and _stable_artifact_key(item, "function")
    ]
    caller_field_catalog: list[dict[str, Any]] = []
    access_provider_refs: list[str] = []
    for overlay in type_design.get("type_definition_overlays", []):
        if not isinstance(overlay, dict) or not isinstance(overlay.get("definition_overlay"), dict):
            continue
        definition = overlay["definition_overlay"]
        type_spec = definition.get("type_spec") if isinstance(definition.get("type_spec"), dict) else {}
        fields = definition.get("fields", type_spec.get("FIELDS", []))
        caller_field_catalog.append(
            {
                "type_id": overlay.get("type_id"),
                "fields": [
                    {
                        "name": field.get("name", field.get("NAME")),
                        "type": field.get("c_type", field.get("type", field.get("TYPE"))),
                    }
                    for field in fields if isinstance(field, dict)
                    and field.get("name", field.get("NAME"))
                    and field.get("c_type", field.get("type", field.get("TYPE")))
                ],
            }
        )
        access_paths = definition.get("access_paths", [])
        ownership_fields = definition.get("ownership_fields", [])
        for value in [
            *(access_paths if isinstance(access_paths, list) else []),
            *(ownership_fields if isinstance(ownership_fields, list) else []),
        ]:
            reference = (
                value.get("path") or value.get("PATH") or value.get("name") or value.get("NAME")
                if isinstance(value, dict) else value
            )
            if str(reference or "").strip():
                access_provider_refs.append(str(reference))
    for behavior in behavior_design.get("function_behaviors", []):
        if not isinstance(behavior, dict):
            continue
        mappings = behavior.get("wire_mapping", behavior.get("WIRE_MAPPING", []))
        for mapping in mappings if isinstance(mappings, list) else []:
            if isinstance(mapping, dict) and str(mapping.get("target", mapping.get("TARGET", ""))).strip():
                access_provider_refs.append(str(mapping.get("target", mapping.get("TARGET"))))
    contracts: dict[str, dict[str, Any]] = {
        "module_file_plan": {
            "mode": "canonical_inventory",
            "rule": "Return the closed module/file inventory. Every non-main source requires a unique dedicated header path.",
        },
        "public_artifact_inventory": {
            "mode": "canonical_inventory",
            "visibility_schema": {"type": "string", "enum": ["public", "private"]},
            "artifact_kind_rule": "Use kind=callback only for callbacks; representation such as opaque/struct/enum belongs to Stage 5 definition_overlay, not visibility.",
            "constant_value_rule": "Every constant/macro requires an explicit value plus fact_refs or trace_refs. Normative numeric values may not be supported only by decisions, assumptions, or model knowledge.",
            "implementation_coverage_matrix_schema": "array of {obligation_id,artifact_ids,fact_refs,rule_refs,decision_refs}; never return an object keyed by obligation_id",
            "test_obligations_schema": "array of {obligation_id,fact_refs,rule_refs,decision_refs}; never return bare obligation-id strings",
            "runtime_cleanup_rule": "cleanup_services must be non-empty and include top-level destroy functions from lifecycle_matrix; nested per-connection cleanup remains in lifecycle_matrix and Stage 8 failure cleanup.",
            "runtime_context_rule": "When callbacks or handlers need process-owned registry/router state, define one opaque runtime context type in the orchestrator module and use existing top-level startup/cleanup identities as its direct create/destroy pair. The context must aggregate those long-lived handles, be carried explicitly through runtime services and callback user_data, and declare one minimal public typed accessor identity for each child handle required by another service. Include those accessors in coverage; never rely on an unregistered global singleton or expose imaginary struct fields.",
            "runtime_configuration_rule": "Every startup parameter must have a provider planned at Stage 4. If protocol facts ground a numeric value, define a traced constant. If they do not, never invent a default constant: make main/runtime configuration accept external input using standard C types so Stage 6 can expose argc/argv or an equivalent registered provider and Stage 7 can validate/parse a typed local value.",
            "lifecycle_abi_rule": "lifecycle_matrix contains only direct owned-handle lifecycles: type_id must be an exact types[].symbol, create_function must construct/return type_id, and destroy_function must accept that same handle type. Never use void*, add/remove, subscribe/unsubscribe, lookup, or another mutation as a lifecycle identity. Omit a resource that has no direct pair instead of fabricating one; keep its mutation behavior in obligation coverage and tests. Prefer existing explicit create/destroy identities for registry and router containers.",
            "visibility_rule": "A function invoked or registered from another file/module, including callback providers, must be public. Mark private only helpers guaranteed to stay in their owner file.",
            "callback_coverage_rule": "For each callback included in foundation:callback_provider, cover its consumer/registration function and the exact function that implements the callback ABI; do not substitute the provider's upstream caller or a buffer-feed helper. The provider role must explicitly say implements/provides the exact callback symbol, and every provider input needed without globals must be represented by the callback.",
            "callback_ownership_rule": "A callback typedef belongs to the header of the function that consumes/registers it, not the provider header. This keeps the consumer interface self-contained and prevents consumer/provider public-header cycles.",
            "rule": "Return a closed type/constant/function identity inventory and cover every required implementation and test obligation. runtime_entrypoint and lifecycle_matrix must reference only identities in that inventory. Do not forbid any inventory symbol.",
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
                "enum_value_refs",
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
            "opaque_schema": {
                "type_spec.TYPE_KIND|type_kind": "OPAQUE",
                "ownership_model": "non-empty string",
                "opaque_boundaries": {"create": "existing create function ID/name", "destroy": "existing destroy function ID/name"},
            },
            "enum_schema": "Every ENUM value needs NAME/name, VALUE/value, non-empty ROLE/role, and its own fact/trace/rule/decision refs or enum_value_refs[name].",
            "callback_schema": "A CALLBACK signature must match the exact Stage 4 provider role and contain every fact-grounded input that provider needs without hidden globals. A per-connection packet dispatcher must receive, in one explicit stable order, runtime/user_data context, the registered connection handle, packet discriminant, and decoded packet payload. When naming the high-level runtime type would create a lower-layer header cycle, use void* user_data consistently in the callback, consumer, and provider ABI; otherwise use the registered typed handle. Do not add user_data without an inventory runtime service that explicitly carries the context. access_paths must name the exact provider function ID, not a header path.",
            "runtime_context_schema": "For an inventory runtime context, emit an OPAQUE type with ownership_model and opaque_boundaries bound to its exact startup/create and cleanup/destroy identities. Its access_paths must name the exact registered typed accessor services for child handles plus the services that carry the context, not a header path, imaginary field, or global variable.",
            "wire_representation_rule": "A decoded packet STRUCT must materialize every fact-grounded field later used by state, routing, response, or error decisions. When facts name a bit-derived semantic value used by handlers (for example clean_session derived from connect_flags), expose that exact typed semantic field instead of retaining only an untyped raw flags byte. Repeated wire items must preserve aligned per-item values. If Stage 4 did not register an item type, use parallel arrays of standard/registered element types plus one shared count (for example char** topic_filters and uint8_t* requested_qos); never invent a new *_t identity in Stage 5. Do not drop a parsed field merely because the minimum implementation later normalizes or rejects it.",
            "rule": "Return only {type_id, definition_overlay}. Report an artifact_request instead of adding identity.",
        },
        "function_interface_design": {
            "mode": "canonical_definition",
            "join_key": "function_id",
            "allowed_stable_ids": inventory_function_ids,
            "required_identity_fields": ["function_id", "owner_file", "trace_id", "function_type", "visibility", "signature"],
            "signature_schema": {
                "RAW": "complete C declaration ending in )",
                "NAME": "exact canonical function name",
                "RETURN": "standard C type or registered custom type",
                "PARAMS": "array of {NAME,TYPE,ROLE,NULLABLE,OWNERSHIP}; every parameter requires uppercase NULLABLE boolean and non-empty uppercase OWNERSHIP; every custom type must exist in registry_catalog",
            },
            "public_abi_rule": "Public signatures may use only public registered types/callbacks or standard C types. Never invent a *_t name or struct/union tag.",
            "layering_rule": "A lower-level transport public ABI must not depend on higher-level session/router/broker handle types. Use registered callbacks plus void* user_data, or another already-grounded acyclic boundary.",
            "callback_abi_rule": "A callback consumer parameter must use the registered callback type, and the selected provider function RETURN/PARAMS must exactly match that callback definition. Do not add or drop user_data on only one side.",
            "callback_consumer_forwarding_rule": "A callback consumer must accept an explicit source parameter for every callback custom-handle context it cannot derive from decoded bytes, including the registered connection handle and runtime user_data. It is invalid for the callback/provider ABI to require a handle that the consumer signature cannot supply.",
            "runtime_context_abi_rule": "If Stage 4 defines a process-owned runtime context, its create/startup function returns context*, its cleanup function accepts context*, main passes it to the run service, and every handler needing registry/router state receives context* directly or through the exact callback user_data ABI. Do not use hidden globals to satisfy owned-state calls.",
            "runtime_configuration_abi_rule": "For each startup configuration parameter, use an exact registered constant only when its value is fact-grounded. Otherwise main must expose standard configuration inputs such as argc/argv and Stage 7 must derive a validated typed local value. Propagate that value through the exact startup/run service that owns the lower-level operation: if run starts the server, run must accept the typed port and main must not also start the same server. Never reference an undeclared default constant.",
            "wire_obligation_schema": {
                "required": "boolean",
                "targets": "non-empty coverage of required_wire_mapping_targets; copy requirement_id, packet, wire_field, optional rule, and fact_refs exactly; the same grounded target may be assigned to multiple functions that directly decode or encode it",
                "fact_refs|rule_refs|decision_refs": "non-empty provenance array",
            },
            "required_wire_function_ids": required_wire_function_ids,
            "rule": "Define only these function IDs, once each, with a complete C declaration; a function name alone is not a signature. Functions selected by Stage 4 codec coverage require a grounded wire_obligation; a decoder/parser receives all input targets, while each encoder/serializer receives the shared fixed_header targets plus only relevant packet fields. Never invent a response target ID when response-specific fields are absent. Dispatchers consume decoded values and must not receive duplicate wire targets unless they directly decode or encode bytes.",
        },
        "function_behavior_design": {
            "mode": "overlay_only",
            "join_key": "function_id",
            "allowed_stable_ids": canonical_function_ids,
            "allowed_fields": ["function_id", "trace_id", "LOGIC", "EVENT", "wire_mapping", "trace_refs"],
            "runtime_configuration_behavior_rule": "When main exposes argc/argv because no fact-grounded startup constant exists, its LOGIC must parse and validate the required primitive configuration into a named typed local value, define invalid/missing-input exit behavior, and pass that local through the single startup/run service that owns the lower-level operation in Stage 8. Do not duplicate the lower-level start call in both main and its run service; never silently reintroduce an unregistered default.",
            "wire_mapping_schema": {
                "shape": "non-empty array; one flat object per required target, never a nested packet-to-field map",
                "required": ["packet", "wire_field", "strategy"],
                "optional": ["target", "source", "rule"],
                "strategy": ["store_in_field", "parse_and_skip", "reject_if_present"],
                "grounding": "Copy packet, wire_field, and rule exactly from current_partition.wire_targets_by_function.",
            },
        },
        "function_call_contract_closure": {
            "mode": "typed_call_edges",
            "join_key": "caller_function_id|callee_function_id",
            "function_id_schema": {"type": "string", "enum": canonical_function_ids},
            "function_signature_catalog": interface_catalog,
            "caller_field_catalog": caller_field_catalog,
            "allowed_access_provider_refs": list(dict.fromkeys(access_provider_refs)),
            "required_edge_fields": [
                "caller_function_id",
                "callee_function_id",
                "call_purpose",
                "condition",
                "argument_semantics",
                "result_usage",
                "trace_refs",
            ],
            "argument_binding_schema": {
                "required": ["parameter", "source_kind", "source_ref", "source_type"],
                "source_kind": ["caller_param", "caller_field", "local_value", "prior_result", "constant", "literal", "access_path", "owned_state", "callback_binding"],
                "source_rules": {
                    "caller_param": "source_ref is an exact parameter NAME from the caller signature",
                    "caller_field": "source_ref is param->field or param.field, where param is a typed caller parameter and field/type exists exactly in its registered Stage 5 STRUCT",
                    "local_value": "source_ref is a simple caller-local C identifier, &identifier, or &identifier[index] created for this call, with source_type exactly equal to the callee parameter type",
                    "prior_result": "source_ref is the exact canonical function ID of an earlier edge from the same caller; never a field, local name, or prose result",
                    "constant": "source_ref is an exact registered constant ID or alias",
                    "literal": "source_ref is a concrete primitive C literal matching source_type, never prose",
                    "access_path|owned_state": "source_ref must appear exactly in allowed_access_provider_refs; a plausible global name is not a provider",
                    "callback_binding": "source_ref is an exact binding_id emitted in this partition",
                },
            },
            "condition_schema": {"expression": "non-empty string", "reachable": True},
            "result_binding_schema": {"usage": ["ignored", "checked", "stored", "returned", "passed"], "target": "string"},
            "callback_binding_rule": "Use callback_bindings for provider/type/consumer association. A callback binding is not a direct call edge. Operations executed inside a registered callback belong to the provider function's partition; never emit them as direct calls from the registration/consumer function by pretending callback-local parameters are consumer parameters.",
            "callback_binding_schema": {
                "required_only": [
                    "binding_id", "owner_function_id", "consumer_function_id", "consumer_parameter",
                    "callback_type_id", "provider_function_id", "user_data_source", "trace_refs",
                ],
                "provider_rule": "provider_function_id ABI must exactly implement callback_type_id; consumer_parameter must have that callback type.",
            },
            "runtime_flow_schema": {
                "main_function_id": "canonical main function ID",
                "success_sequence": "ordered unique Stage 4 top-level startup, run, and cleanup function IDs",
                "failure_cleanup": [
                    {
                        "after_function_id": "fallible top-level startup or run function ID",
                        "cleanup_function_ids": ["reachable cleanup/destroy function IDs"],
                    }
                ],
                "trace_refs": ["fact/rule/decision refs"],
            },
            "runtime_flow_rule": "Only the partition containing main emits runtime_flow. It must cover the Stage 4 runtime_entrypoint and must have one typed direct call edge from main to every service in success_sequence. Nested lifecycle services remain reachable through their owning top-level service and are not duplicated as main calls.",
            "forbidden_fields": ["NAME", "SIGNATURE", "owner", "owner_file", "visibility", "RELY"],
            "relation_rule": "call_edges contain only calls that execute. Keep lifecycle pairing in lifecycle_matrix and state prerequisites in Stage 7 PRECONDITION/INVARIANTS; never add placeholder edges for those relations.",
            "edge_minimization_rule": "An empty call_edges array is valid when no direct call can be fully proven. Never make a library/codec function call upward into its consumer merely to connect the graph.",
            "rule": "Return only fully bindable typed call intent. The registry/compiler derives names, signatures, RELY.FUNC, CALL_CONTRACTS, and dependencies.",
        },
        "function_test_vector_design": {
            "mode": "overlay_only",
            "join_key": "function_id",
            "allowed_stable_ids": canonical_function_ids,
            "function_id_schema": {"type": "string", "enum": canonical_function_ids},
            "file_id_schema": {
                "type": "string",
                "enum": [item["artifact_id"] for item in registry.typed_view({"file"})] if registry is not None else [],
            },
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
        refs.extend(_collect_prompt_refs(partition or {}))
        if stage.stage_id == "function_interface_design":
            refs.extend(_collect_prompt_refs(context.get("required_wire_mapping_targets", [])))
        refs.extend(_collect_prompt_refs(context.get("open_assumptions", [])))
        facts = select_fact_slice(context["facts"], list(dict.fromkeys(refs)))
    registry_catalog = _prompt_registry_catalog(registry) if stage.stage_id in {
        "function_interface_design", "function_call_contract_closure",
        "function_test_vector_design", "dependency_closure",
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
                refs.extend(
                    str(item) if str(item).startswith("fact:") else f"fact:{item}"
                    for item in child
                    if str(item).strip()
                )
            else:
                refs.extend(_collect_prompt_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_prompt_refs(child))
    return refs


def _prompt_registry_catalog(registry: CanonicalPlanningRegistry | None) -> list[dict[str, Any]]:
    if registry is None:
        return []
    fields = (
        "artifact_id", "artifact_kind", "canonical_name", "owner_module_id",
        "owner_file_id", "visibility", "status", "semantic_role",
    )
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
                "provenance_schema": {"kind": "string", "refs": "non-empty array"},
                "rule": "Use only for a genuinely missing type, callback, or function. Missing constants require Stage 4 regeneration because a later request cannot supply a grounded value. Remove invalid typed references; a request is not immediately bindable.",
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
        "input_policy": {
            "target_profile_visible_to_planner": bool(context.get("target_profile_visible_to_planner", False)),
            "target_directives": context.get("planner_visible_target_directives", []),
        },
        "previous_stage_artifacts": projected_artifacts,
    }
    if stage.stage_id == "public_artifact_inventory":
        prompt["required_implementation_obligations"] = context.get("required_implementation_obligations", [])
    if stage.stage_id == "function_interface_design":
        prompt["required_wire_mapping_targets"] = context.get("required_wire_mapping_targets", [])
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
                "main_function_id": partition.get("main_function_id", ""),
                "required_callback_bindings": partition.get("required_callback_bindings", []),
                "emit_each_required_callback_binding_and_one_consuming_call_edge": True,
                "emit_runtime_flow_exactly_once_if_main_function_id_is_nonempty": bool(
                    partition.get("main_function_id")
                ),
                "return_partition_artifact_only": True,
            }
        else:
            prompt["partition_contract"] = {
                "only_generate_behavior_for_functions_in_current_partition": True,
                "wire_function_ids_require_complete_structured_wire_mapping": partition.get("wire_function_ids", []),
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
            "required_implementation_obligations",
        )
        if key in original
    }
    stage4_inventory_recovery = stage.stage_id == "public_artifact_inventory"
    stage6_whole_stage_recovery = (
        stage.stage_id == "function_interface_design" and partition.get("partition_id") == "whole_stage"
    )
    stage4_targets = [
        item for item in local_contract.get("required_implementation_obligations", [])
        if isinstance(item, dict) and str(item.get("obligation_id", "")) in validation_error
    ] if stage4_inventory_recovery else []
    stage4_target = stage4_targets[0] if stage4_targets else {}
    stage4_callback_audit = (
        " In the same correction, audit foundation:callback_provider even when another Stage 4 error was reported first: "
        "its artifact_ids must include one exact callback, one function whose role explicitly says it accepts/registers/consumes "
        "that callback, and one function whose role explicitly says it implements/provides that callback. The callback owner_file "
        "must equal the consumer function owner_file."
        if stage4_inventory_recovery else ""
    )
    recovery_guidance = {
        "stage4_obligation_kind_missing": (
            "Return the complete inventory unchanged except for the required repair. Satisfy every obligation listed in the "
            "validation error by "
            "adding exact existing typed IDs to its implementation_coverage_matrix.artifact_ids; do not delete or reclassify "
            "any type, callback, function, constant, public_symbol_table entry, lifecycle entry, or other obligation coverage. "
            "Add a new grounded identity only when the complete inventory truly has too few artifacts of the required kind."
            + stage4_callback_audit
        ),
        "stage4_lifecycle_destroy_not_in_cleanup": (
            "Keep every lifecycle entry. Set runtime_entrypoint.cleanup_services to a non-empty list containing existing "
            "top-level destroy functions from lifecycle_matrix, especially process-owned manager/router cleanup. Do not add a "
            "new cleanup identity when an existing registered destroy function provides the required cleanup."
        ),
        "stage4_lifecycle_identity_invalid": (
            "Audit every lifecycle entry. type_id, create_function, and destroy_function must be exact existing inventory IDs. "
            "Remove entries using void/void* or a missing identity; if no direct pair exists, omit that resource rather than "
            "inventing an identity. Preserve at least one valid direct owned-handle lifecycle and all unrelated inventory."
        ),
        "stage4_lifecycle_mutator_misclassified": (
            "Audit every lifecycle entry, not only the reported index. Remove or replace every add/remove, subscribe/unsubscribe, "
            "lookup, match, mark, update, feed, or flush function used as create/destroy. Use only an existing direct constructor "
            "and destructor for the exact type_id; omit resources without such a pair and preserve their functions in coverage."
        ),
        "stage4_callback_role_coverage_missing": (
            "In foundation:callback_provider include the exact callback identity, its consumer/registration function, and its ABI "
            "provider function. Their roles must explicitly name callback consumption/registration and callback implementation/provision. "
            "Do not relabel an unrelated protocol handler as the consumer: replace it with an existing transport startup, decoder, or "
            "registration function that truly accepts the callback. Move the callback owner_file to that consumer owner_file. Also repair "
            "every outstanding_kind_deficits and outstanding_role_deficits item included in the validation error in this same complete "
            "artifact. In particular, add the corresponding registered handle type for every typed _get_<handle> accessor, and satisfy "
            "the explicit runtime and session create/destroy/lookup/access roles rather than only renaming a role."
        ),
        "stage4_callback_owner_consumer_mismatch": (
            "Move the callback identity to the exact consumer function's owner file and keep that consumer plus the ABI provider in "
            "foundation:callback_provider; do not place the callback typedef in the higher-level provider header."
        ),
        "stage4_role_coverage_missing": (
            "Repair every listed role deficit in the same complete Stage 4 artifact. foundation:runtime_services and "
            "runtime_entrypoint must reference distinct grounded startup/create, run/serve/listen/accept/poll loop, and "
            "cleanup/destroy services; a queue/flush helper, main, or constructor is not a run service. "
            "foundation:session_access must include manager/session types plus explicit create, destroy, lookup/get/find, and "
            "mutation/access functions. Reuse an exact existing identity only when its role truly matches; otherwise add one "
            "grounded identity in the existing owner file. Every typed _get_<handle> accessor must have a corresponding registered "
            "<handle> type; add the grounded engineering type at Stage 4 or remove the unsupported accessor. Also repair every "
            "outstanding_kind_deficits item. For foundation:transport_accept_callback add a transport-owned accept callback type, "
            "the startup/registration consumer that accepts it, and an upper-layer provider whose role explicitly implements it. "
            "For foundation:transport_output add a connection send/queue/write service grounded by the outbound-buffer facts."
        ),
        "stage5_opaque_boundary_missing": (
            "Keep the type_id and complete definition_overlay. Set a non-empty ownership_model and set opaque_boundaries "
            "to an object with exactly create and destroy, each referencing an existing lifecycle function."
        ),
        "stage5_enum_grounding_missing": (
            "Keep every enum value. Add a non-empty role and fact/trace/rule/decision refs to each value, or add "
            "enum_value_refs keyed by each exact enum value name; use only refs supplied by facts/constants."
        ),
        "stage7_wire_mapping_invalid": (
            "Replace nested or prose mappings with a non-empty flat array of {packet,wire_field,strategy,target?,source?,rule?}; "
            "copy every required target packet/wire_field exactly and use only store_in_field, parse_and_skip, or reject_if_present. "
            "For an encoder use store_in_field with a non-empty signature parameter or grounded primitive source; do not substitute a different response field. "
            "Never return wire_mapping:[]: for a required wire function emit all assigned mappings; for a non-required function remove the wire_mapping key entirely."
        ),
        "stage7_wire_obligation_unfulfilled": (
            "Do not remove or leave empty the required mapping. In the named function_behavior, add wire_mapping as one non-empty "
            "flat item for every entry in current_partition.wire_targets_by_function for that exact function ID; preserve all other function behaviors."
        ),
        "stage7_wire_target_access_path_missing": (
            "Keep every function behavior and every required wire field. A decoder store_in_field target must be an exact Stage 5 "
            "STRUCT field such as type_name.field or the exact name of its typed pointer parameter whose ROLE begins with output; "
            "an encoder store_in_field must instead use a grounded signature parameter, "
            "registered constant, or primitive expression in source. If no typed decoder field exists and the function intentionally "
            "consumes the value without storing it, use parse_and_skip; do not invent a field, simple variable target, or void* access path."
        ),
        "stage8_callback_binding_shape": (
            "Return each callback binding with exactly the eight fields in callback_binding_schema; remove every extra field."
        ),
        "stage8_argument_provider_missing": (
            "caller_param source_ref must be an exact caller parameter NAME. If no exact caller_param, prior_result, constant, "
            "caller_field, local_value, access_path, owned_state, callback_binding, or grounded primitive literal exists, remove the entire false call edge. "
            "For one element selected while iterating a repeated caller field, bind a simple current-element local_value (for example topic_filter), not field[index]. "
            "For a child handle inside an OPAQUE runtime context, never use context->field; first call its exact registered typed "
            "accessor with the context, then bind the child handle as prior_result of that accessor."
        ),
        "stage8_argument_binding_coverage": (
            "Emit exactly one argument_semantics item for every canonical callee parameter. Each parameter name must match the "
            "callee signature and use an allowed source_kind with an exact caller param, prior result function ID, constant ID, "
            "typed access path, owned state, callback binding, or grounded primitive literal. Remove a false edge instead of "
            "leaving any callee parameter unbound. Never repair coverage by deleting just one parameter. For a callback parameter, "
            "emit the exact callback_binding linking this owner, consumer, callback type, and ABI-matching provider, then bind it "
            "with source_kind=callback_binding; a provider function ID is not a prior_result."
        ),
        "stage8_argument_binding_type": (
            "Use the exact callee parameter TYPE from function_signature_catalog and an exact provider of that type. Do not cast, "
            "rename, or substitute void*. Remove the entire call edge when no exact typed provider exists."
        ),
        "stage8_argument_source_kind_invalid": (
            "artifact_request is never an argument source_kind. Use an exact registered constant when one exists; otherwise use "
            "source_kind=literal with the concrete primitive value supplied by facts (such as a default port), or remove the false edge."
        ),
        "unknown_artifact_id": (
            "Replace the unknown reference with an exact ID from the supplied registry. Never invent a constant ID. For a primitive "
            "configuration value explicitly grounded by facts or an engineering decision, use source_kind=literal and its concrete "
            "C literal; a numeric value itself is a literal, never a constant reference. When main exposes argc/argv, bind the validated "
            "configuration as a typed local_value named for the callee parameter. Otherwise use an existing typed provider or remove the ungrounded edge."
        ),
        "stage8_callback_provider_abi_mismatch": (
            "A provider RETURN/PARAMS must exactly match the callback type. Stage 8 cannot repair Stage 5/6 ABI; remove the invalid "
            "callback binding and every call edge that consumes it, while retaining independently valid runtime_flow entries."
        ),
        "stage8_callback_consumer_abi_mismatch": (
            "The consumer parameter must use the exact registered callback typedef, not an inline function-pointer type with a "
            "different ABI. Stage 8 cannot repair Stage 5/6 ABI; remove the invalid callback binding and all argument bindings that "
            "reference it. Never model operations performed inside that callback as direct consumer-function calls."
        ),
        "stage8_callback_binding_missing": (
            "If the caller signature already has a parameter with the exact callback type, bind it with source_kind=caller_param "
            "and that exact parameter NAME. Otherwise use an exact callback_binding owned by this caller, or remove the edge."
        ),
        "stage8_required_callback_binding_missing": (
            "For every item in current_partition.required_callback_bindings, emit one callback_bindings item with the exact "
            "callback_type_id, consumer_function_id, and provider_function_id, then emit one real call edge from an allowed "
            "partition caller to that consumer and bind the consumer callback parameter with source_kind=callback_binding. "
            "When one consumer accepts multiple required callbacks, bind all of them on the same call edge. Do not replace "
            "asynchronous callback registration with a direct consumer-to-provider call."
        ),
        "stage8_result_usage_mismatch": (
            "Match result_usage to the exact callee RETURN. A void result must use usage=ignored and target=''. A non-void result "
            "must use checked, stored, returned, or passed with a concrete non-empty caller-local target; if the result has no real "
            "consumer, remove that false call edge instead of marking a non-void result ignored. Preserve all unrelated edges."
        ),
        "stage8_runtime_flow_partition_escape": (
            "This partition does not own main. Remove the runtime_flow key entirely; do not return an empty runtime_flow object."
        ),
        "stage8_runtime_success_sequence_invalid": (
            "Keep runtime_flow with exactly its four required fields. Set success_sequence to a non-empty ordered list of unique "
            "registered service function IDs and remove main_function_id from that list; main_function_id belongs only in its "
            "dedicated field. Preserve valid failure_cleanup and trace_refs."
        ),
        "stage8_runtime_direct_edge_missing": (
            "Preserve runtime_flow and add one real typed call edge from main_function_id to every missing function in its "
            "success_sequence. Bind each parameter from an exact main parameter, local value, earlier constructor result, "
            "registered constant, or typed access path; use checked/stored result usage and feed constructor results into later "
            "run/cleanup calls. Do not claim a runtime service in success_sequence without a coder-facing direct call contract."
        ),
        "stage8_cross_file_private_callee": (
            "A private callee cannot be invoked across files. Remove the invalid direct edge; do not change visibility in this "
            "Stage 8 overlay. The authoritative recovery is Stage 4 regeneration or a registered callback boundary."
        ),
        "stage6_private_type_leak": (
            "A public function signature may use only public registered types/callbacks or standard C types. Do not hide a "
            "private typedef by spelling its struct tag. Keep every registered function identity and repair all affected signatures. "
            "Every custom *_t name or struct/union tag must be an exact registered type/callback from registry_catalog; when no "
            "message struct is registered, use grounded primitive parameters or byte spans instead of inventing a type. Every RAW "
            "declaration must be syntactically complete and agree with NAME, RETURN, and PARAMS."
        ),
        "stage6_parameter_contract_missing": (
            "Keep every registered function and its RAW/NAME/RETURN identity. For every PARAMS item use uppercase NAME, TYPE, "
            "ROLE, NULLABLE, and OWNERSHIP; NULLABLE is boolean and OWNERSHIP is a non-empty ownership mode. Remove lowercase "
            "nullable/ownership keys."
        ),
        "stage6_callback_owner_consumer_mismatch": (
            "Stage 6 cannot move a Stage 4 callback identity. Preserve all interfaces and report the mismatch; the authoritative "
            "repair is to regenerate Stage 4 with the callback owner_file equal to the exact consumer/registration function's "
            "owner_file, never the provider function's header."
        ),
        "stage6_callback_consumer_context_missing": (
            "Keep every interface and the exact callback/provider ABI. Add the missing registered custom-handle parameter to the "
            "callback consumer signature so it can forward that value when invoking the callback; preserve callback, user_data, "
            "buffer, length, and output parameters. Do not use a hidden global or remove the provider input."
        ),
        "stage6_wire_target_coverage": (
            "Keep all function interfaces. Copy every missing required_wire_mapping_targets item exactly into the decoder/parser "
            "targets so the complete input catalog is covered. Every required encoder/serializer must also have a non-empty target "
            "list: reuse the exact shared fixed_header targets, then add only packet-specific targets it truly encodes. Targets may "
            "be shared across functions. Keep required=true and provenance; do not remove valid targets or invent requirement IDs."
        ),
        "stage6_wire_obligation_missing": (
            "Keep every registered function interface. The named function is in required_wire_function_ids or already declares "
            "required=true; give it a non-empty wire_obligation.targets array copied exactly from required_wire_mapping_targets, "
            "with provenance on the obligation or target. Do not remove another required function's obligation."
        ),
        "stage6_wire_targets_missing": (
            "Keep the complete interface and required=true. Copy exact targets only from required_wire_mapping_targets. A decoder/parser "
            "must cover all input targets; an encoder with no response-specific target must reuse the exact shared fixed_header targets. "
            "Never leave targets empty and never invent a response requirement ID."
        ),
        "stage6_wire_target_unknown": (
            "Remove every invented target and preserve the complete interface inventory. Replace it only with exact entries copied from "
            "required_wire_mapping_targets. For a response encoder whose response-specific fields are absent, reuse the exact grounded "
            "fixed_header targets rather than synthesizing packet-specific IDs."
        ),
        "stage6_encoder_wire_source_missing": (
            "Keep every interface and the decoder's complete input coverage. For each encoder, remove only packet-specific targets "
            "whose wire_field is absent from every typed input STRUCT in that encoder signature; retain the shared fixed_header "
            "targets and fields present in its input STRUCT. Do not rename fields, add fake parameters, or invent target IDs."
        ),
        "unknown_artifact_id": (
            "Use only exact registry enum IDs. In Stage 5, replace an unknown nested/member type with registered or standard C "
            "types; for aligned repeated scalar fields use parallel arrays plus a shared count, or emit a valid artifact_request "
            "instead of defining a new *_t inline. In Stage 8, a prior_result source_ref must be the canonical function ID producing "
            "the value; a local variable or field name is not a function ID. Remove an edge that cannot be bound."
        ),
        "artifact_request_invalid_fields": (
            "Remove the artifact request unless a genuinely missing identity is unavoidable. If unavoidable, use exactly requested_kind, "
            "semantic_role, requested_owner, required_by, reason, provenance, preferred_visibility, and proposed_name; remove all other keys."
        ),
    }.get(diagnostic_code, "")
    if stage4_inventory_recovery and diagnostic_code == "unknown_artifact_id":
        recovery_guidance = (
            "Audit implementation_coverage_matrix, lifecycle_matrix, runtime_entrypoint, public_symbol_table, and forbidden_symbols "
            "for the named unknown ID. If it consistently denotes a required owned handle with grounded existing create/destroy "
            "functions, add that missing type identity once in types with the existing manager/container owner_file and obligation "
            "provenance. Otherwise remove every unsupported reference. Return the complete inventory and do not alter unrelated IDs."
        )
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
                "stage4_may_add_grounded_inventory_identities": stage4_inventory_recovery,
                "stage4_must_return_the_complete_corrected_inventory": stage4_inventory_recovery,
                "stage6_must_return_every_registered_function_interface": stage6_whole_stage_recovery,
            },
            "stage4_inventory_recovery_target": {
                "obligation": stage4_target,
                "required_action": (
                    "First reuse exact existing typed IDs and preserve the complete inventory. Only if the required kind is "
                    "truly absent, add each missing identity to types/functions with an existing owner_file and grounded "
                    "provenance; callbacks must be types[].kind=callback. Update implementation_coverage_matrix and "
                    "public_symbol_table, and return all unchanged inventory entries as well."
                ) if stage4_target else "",
            },
            "stage4_inventory_recovery_targets": stage4_targets,
            "diagnostic_specific_recovery_guidance": recovery_guidance,
            "whole_partition_correction_rule": "Audit every edge and binding against the full contract in this one attempt. Return fewer or zero call_edges when any provider cannot be proven; do not stop after repairing only the first reported error.",
            "invalid_partition_artifact": artifact,
            "original_partition_contract": local_contract,
        }
    )
    if stage4_inventory_recovery:
        recovery_policy = (
            "Stage 4 owns the canonical identity inventory: return the complete corrected Stage 4 artifact, not a patch. "
            "You may add a missing type, callback, constant, or function identity only when required by a supplied "
            "implementation obligation and supported by its fact/rule/decision refs; keep it in an existing owner file. "
            "Cover every required_kind_counts entry and every required test obligation. Audit every lifecycle entry: type_id "
            "must be an exact type identity and create/destroy must be its direct handle pair; remove void* and mutation-based "
            "entries. Preserve all unrelated identities and coverage rows. Do not use ArtifactRequest. "
        )
    elif stage6_whole_stage_recovery:
        recovery_policy = (
            "Return the complete corrected Stage 6 artifact, not a subset or patch: include exactly one function_interface "
            "for every allowed registered function ID plus complete header_interfaces, source_interfaces, and interface_diagnostics. "
            "Do not add identity inline. Use ArtifactRequest when a genuinely missing artifact is required. "
        )
    else:
        recovery_policy = (
            "Do not add identity inline. Use ArtifactRequest when a genuinely missing artifact is required. "
        )
    return [
        {
            "role": "system",
            "content": (
                f"You perform one {required_recovery} recovery for a {validation_layer} validation failure. "
                "Return only valid JSON. Preserve supported semantic decisions and obey the applicable registry enums. "
                f"{recovery_policy}"
                "Remove every invalid typed reference before returning; never keep an invalid edge beside "
                "an ArtifactRequest. A type, payload, field, or direct parameter access is not a function call and must not "
                "be emitted as a call edge. This is the only correction attempt."
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
