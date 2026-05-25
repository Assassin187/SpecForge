from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..diagnostics import PlanningDiagnostic


SchemaSpec = dict[str, Any]

STATUS_VALUES = {"supported", "inferred", "assumed", "unresolved"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
VISIBILITY_VALUES = {"public", "internal", "private", "static"}
FUNCTION_KIND_VALUES = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}
CODER_FUNCTION_TYPE_VALUES = {"ALGORITHM", "EVENT", "ENTRYPOINT"}
API_SURFACE_VALUES = {"public", "module_internal", "private_helper", "static_helper"}
TYPE_INVENTORY_KIND_VALUES = {
    "opaque_handle",
    "struct",
    "config_struct",
    "internal_state",
    "enum",
    "callback_type",
    "event_struct",
    "view_struct",
    "owned_buffer",
    "result_struct",
    "bitflag",
    "alias",
}
TYPE_INVENTORY_VISIBILITY_VALUES = {"public", "private", "module_internal"}
TYPE_INVENTORY_DEFINED_IN_VALUES = {"public_header", "internal_header", "source_file"}


def _scalar(name: str, *, enum: set[str] | None = None) -> SchemaSpec:
    spec: SchemaSpec = {"type": name}
    if enum is not None:
        spec["enum"] = sorted(enum)
    return spec


STRING = _scalar("string")
BOOL = _scalar("boolean")
STRING_LIST = {"type": "array", "items": STRING}


def _object(properties: dict[str, SchemaSpec], required: list[str] | None = None) -> SchemaSpec:
    return {"type": "object", "required": required or list(properties), "properties": properties}


def _array(item: SchemaSpec) -> SchemaSpec:
    return {"type": "array", "items": item}


PRODUCER_SCHEMA = _object(
    {
        "stage": STRING,
        "prompt_name": STRING,
        "prompt_version": STRING,
    }
)

ASSUMPTION_SCHEMA = _object(
    {
        "assumption_id": STRING,
        "target_kind": STRING,
        "target_id": STRING,
        "statement": STRING,
        "rationale": STRING,
        "confidence": _scalar("string", enum=CONFIDENCE_VALUES),
        "trace_ref_keys": STRING_LIST,
    }
)

UNRESOLVED_QUESTION_SCHEMA = _object(
    {
        "question_id": STRING,
        "target_kind": STRING,
        "target_id": STRING,
        "question": STRING,
        "unresolved_reason": STRING,
        "blocking": BOOL,
        "trace_ref_keys": STRING_LIST,
    }
)

TRACEABILITY_SCHEMA = _object(
    {
        "trace_ref_keys": STRING_LIST,
        "notes": STRING,
    }
)

STATUS = _scalar("string", enum=STATUS_VALUES)
VISIBILITY = _scalar("string", enum=VISIBILITY_VALUES)

FIELD_SCHEMA = _object(
    {
        "field_name": STRING,
        "field_type": STRING,
        "required": BOOL,
        "source_field_id": STRING,
        "validation_notes": STRING,
    }
)

ENUM_VALUE_SCHEMA = _object(
    {
        "name": STRING,
        "value": STRING,
        "source_field_id": STRING,
    }
)

CANONICAL_TYPE_SCHEMA = _object(
    {
        "type_id": STRING,
        "name": STRING,
        "kind": _scalar("string", enum={"struct", "enum", "alias", "opaque", "buffer", "scalar"}),
        "owner_module_id": STRING,
        "source_message_ids": STRING_LIST,
        "source_field_ids": STRING_LIST,
        "fields": _array(FIELD_SCHEMA),
        "enum_values": _array(ENUM_VALUE_SCHEMA),
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

STATE_DESIGN_SCHEMA = _object(
    {
        "state_id": STRING,
        "name": STRING,
        "owner_module_id": STRING,
        "state_kind": _scalar("string", enum={"connection", "session", "transaction", "resource", "configuration", "runtime", "unknown"}),
        "lifecycle": STRING_LIST,
        "read_by_module_ids": STRING_LIST,
        "mutated_by_module_ids": STRING_LIST,
        "source_capability_ids": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

HANDLER_MATRIX_SCHEMA = _object(
    {
        "handler_id": STRING,
        "handler_kind": _scalar("string", enum={"message", "event", "command", "timer", "lifecycle", "error"}),
        "capability_id": STRING,
        "owner_module_id": STRING,
        "message_ids": STRING_LIST,
        "trigger": STRING,
        "responsibility": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

RESOURCE_LIFECYCLE_SCHEMA = _object(
    {
        "resource_id": STRING,
        "name": STRING,
        "owner_module_id": STRING,
        "lifecycle_states": STRING_LIST,
        "init_required": BOOL,
        "cleanup_required": BOOL,
        "error_handling": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

ERROR_STRATEGY_SCHEMA = _object(
    {
        "error_id": STRING,
        "error_kind": _scalar("string", enum={"protocol", "transport", "validation", "resource", "timeout", "internal"}),
        "owner_module_id": STRING,
        "related_constraint_ids": STRING_LIST,
        "detection_points": STRING_LIST,
        "recovery_policy": STRING,
        "propagation_policy": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

TEST_PLAN_SEED_SCHEMA = _object(
    {
        "test_id": STRING,
        "purpose": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

MODULE_ARTIFACT_SCHEMA = _object(
    {
        "name": STRING,
        "kind": _scalar("string", enum={"TYPE", "FUNC"}),
        "role": STRING,
    }
)

MODULE_ARTIFACT_ENTRY_SCHEMA = _object(
    {
        "module_id": STRING,
        "name": STRING,
        "role": STRING,
        "dependencies": STRING_LIST,
        "artifacts": _array(MODULE_ARTIFACT_SCHEMA),
        "files": STRING_LIST,
        "doc_ref": STRING_LIST,
    }
)

MODULE_ARTIFACT_CONSISTENCY_RULE_SCHEMA = _object(
    {
        "id": STRING,
        "rule": STRING,
        "doc_ref": STRING_LIST,
    }
)

MODULE_ARTIFACT_FORBIDDEN_SYMBOL_SCHEMA = _object(
    {
        "name": STRING,
        "kind": _scalar("string", enum={"TYPE", "FUNC", "FIELD", "ENUM", "MACRO"}),
        "reason": STRING,
    }
)

FUNCTION_INVENTORY_SCHEMA = _object(
    {
        "function_id": STRING,
        "name": STRING,
        "module_id": STRING,
        "function_kind": _scalar("string", enum=FUNCTION_KIND_VALUES),
        "coder_function_type": _scalar("string", enum=CODER_FUNCTION_TYPE_VALUES),
        "visibility": VISIBILITY,
        "api_surface": _scalar("string", enum=API_SURFACE_VALUES),
        "exported": BOOL,
        "export_reason": STRING,
        "public_api_role": STRING,
        "grouping_hint": STRING,
        "purpose": STRING,
        "capability_ids": STRING_LIST,
        "covers_handler_ids": STRING_LIST,
        "covers_message_ids": STRING_LIST,
        "covers_field_ids": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

TYPE_INVENTORY_FIELD_SCHEMA = _object(
    {
        "field_name": STRING,
        "field_type": STRING,
        "type_ref": STRING,
        "required": BOOL,
        "ownership": _scalar("string", enum={"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"}),
        "lifetime": STRING,
        "length_field": STRING,
        "capacity_field": STRING,
        "validation_notes": STRING,
    }
)

TYPE_INVENTORY_ENUM_VALUE_SCHEMA = _object(
    {
        "name": STRING,
        "value": STRING,
        "role": STRING,
    }
)

CALLBACK_PARAM_SCHEMA = _object(
    {
        "name": STRING,
        "type": STRING,
        "type_ref": STRING,
        "ownership": _scalar("string", enum={"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"}),
    }
)

CALLBACK_SIGNATURE_SCHEMA = _object(
    {
        "return_type": STRING,
        "params": _array(CALLBACK_PARAM_SCHEMA),
    }
)

TYPE_LIFECYCLE_SCHEMA = _object(
    {
        "created_by": STRING_LIST,
        "initialized_by": STRING_LIST,
        "destroyed_by": STRING_LIST,
        "freed_by": STRING_LIST,
    }
)

TYPE_INVENTORY_SCHEMA = _object(
    {
        "type_id": STRING,
        "name": STRING,
        "module_id": STRING,
        "kind": _scalar("string", enum=TYPE_INVENTORY_KIND_VALUES),
        "visibility": _scalar("string", enum=TYPE_INVENTORY_VISIBILITY_VALUES),
        "defined_in": _scalar("string", enum=TYPE_INVENTORY_DEFINED_IN_VALUES),
        "purpose": STRING,
        "fields": _array(TYPE_INVENTORY_FIELD_SCHEMA),
        "enum_values": _array(TYPE_INVENTORY_ENUM_VALUE_SCHEMA),
        "callback_signature": CALLBACK_SIGNATURE_SCHEMA,
        "ownership_lifetime": STRING,
        "lifecycle": TYPE_LIFECYCLE_SCHEMA,
        "related_functions": STRING_LIST,
        "dependencies": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

TYPE_INVENTORY_UPDATE_SCHEMA = _object(
    {
        "type_id": STRING,
        "visibility": _scalar("string", enum=TYPE_INVENTORY_VISIBILITY_VALUES),
        "defined_in": _scalar("string", enum=TYPE_INVENTORY_DEFINED_IN_VALUES),
        "purpose": STRING,
        "fields": _array(TYPE_INVENTORY_FIELD_SCHEMA),
        "enum_values": _array(TYPE_INVENTORY_ENUM_VALUE_SCHEMA),
        "callback_signature": CALLBACK_SIGNATURE_SCHEMA,
        "ownership_lifetime": STRING,
        "lifecycle": TYPE_LIFECYCLE_SCHEMA,
        "related_functions": STRING_LIST,
        "dependencies": STRING_LIST,
        "status": STATUS,
    },
    required=["type_id"],
)

FUNCTION_INVENTORY_UPDATE_SCHEMA = _object(
    {
        "function_id": STRING,
        "purpose": STRING,
        "grouping_hint": STRING,
        "status": STATUS,
    }
)

BATCH_SCHEMA = _object({"index": _scalar("integer"), "size": _scalar("integer")})

SIGNATURE_PARAM_SCHEMA = _object(
    {
        "name": STRING,
        "type": STRING,
        "type_ref": STRING,
        "direction": _scalar("string", enum={"in", "out", "inout", "return", "unknown"}),
        "nullable": BOOL,
        "ownership": _scalar("string", enum={"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"}),
        "passing_mode": _scalar("string", enum={"by_value", "by_pointer", "out_param", "inout_param", "return_value", "unknown"}),
    }
)

FUNCTION_SIGNATURE_SCHEMA = _object(
    {
        "raw": STRING,
        "name": STRING,
        "storage_class": _scalar("string", enum={"static", "extern", "none"}),
        "return_type": STRING,
        "params": _array(SIGNATURE_PARAM_SCHEMA),
    }
)

SIGNATURE_DEPENDENCY_SCHEMA = _object(
    {
        "symbol_name": STRING,
        "symbol_kind": _scalar("string", enum={"type", "opaque_handle", "callback_type", "system_type"}),
        "type_ref": STRING,
        "owner_module_id": STRING,
        "dependency_scope": _scalar("string", enum={"header", "source"}),
        "reason": STRING,
    }
)

INTERFACE_TYPE_DECLARATION_SCHEMA = _object(
    {
        "name": STRING,
        "kind": _scalar("string", enum={"opaque_handle", "callback_typedef", "callback_struct", "forward_decl", "type"}),
        "owner_module_id": STRING,
        "visibility": VISIBILITY,
        "reason": STRING,
    }
)

FUNCTION_SIGNATURE_UPDATE_SCHEMA = _object(
    {
        "function_id": STRING,
        "signature": FUNCTION_SIGNATURE_SCHEMA,
        "signature_dependencies": _array(SIGNATURE_DEPENDENCY_SCHEMA),
        "interface_type_declarations": _array(INTERFACE_TYPE_DECLARATION_SCHEMA),
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

STATE_ACCESS_SCHEMA = _object(
    {
        "state_id": STRING,
        "access_kind": _scalar("string", enum={"read", "write", "read_write"}),
        "required": BOOL,
        "reason": STRING,
    }
)

ERROR_BEHAVIOR_SCHEMA = _object(
    {
        "error_ids": STRING_LIST,
        "propagation": _scalar("string", enum={"return_code", "callback", "close_connection", "log_only", "none", "unknown"}),
        "recovery": _scalar("string", enum={"none", "retry", "cleanup", "reset_state", "close_connection", "unknown"}),
        "return_policy": _scalar("string", enum={"status_code", "boolean", "pointer_null", "void", "out_param", "unknown"}),
    }
)

RESOURCE_ACCESS_SCHEMA = _object(
    {
        "resource_id": STRING,
        "access_kind": _scalar("string", enum={"read", "write", "read_write", "lifecycle"}),
        "required": BOOL,
        "reason": STRING,
    }
)

INTERNAL_TYPE_REF_SCHEMA = _object(
    {
        "symbol_name": STRING,
        "visibility": _scalar("string", enum={"private", "internal", "public"}),
        "role": STRING,
    }
)

SERVICE_REQUIREMENT_SCHEMA = _object(
    {
        "service_requirement_id": STRING,
        "requirement_kind": _scalar("string", enum={"external_runtime_service", "cross_module_service", "owned_responsibility"}),
        "operation": STRING,
        "required_capability_ids": STRING_LIST,
        "expected_inputs": STRING_LIST,
        "expected_output": STRING,
        "failure_policy": _scalar("string", enum={"close_connection", "return_error", "cleanup_and_return", "ignore", "unknown"}),
    }
)

BEHAVIOR_CONTRACT_SCHEMA = _object(
    {
        "input": STRING,
        "action": STRING,
        "output": STRING,
        "preconditions": STRING_LIST,
        "postconditions": STRING_LIST,
        "invariants_used": STRING_LIST,
        "idempotent": BOOL,
        "thread_safety": _scalar("string", enum={"single_thread_only", "reentrant", "requires_external_sync", "unknown"}),
    }
)

EVENT_CONTRACT_SCHEMA = _object(
    {
        "trigger": STRING,
        "precondition": STRING,
        "input": STRING,
        "action": STRING,
        "state_change": STRING,
        "response": STRING,
        "event_type": STRING,
    }
)

FUNCTION_BEHAVIOR_UPDATE_SCHEMA = _object(
    {
        "function_id": STRING,
        "contract": BEHAVIOR_CONTRACT_SCHEMA,
        "event_contract": EVENT_CONTRACT_SCHEMA,
        "error_behavior": ERROR_BEHAVIOR_SCHEMA,
        "state_access": _array(STATE_ACCESS_SCHEMA),
        "resource_access": _array(RESOURCE_ACCESS_SCHEMA),
        "internal_type_refs": _array(INTERNAL_TYPE_REF_SCHEMA),
        "service_requirements": _array(SERVICE_REQUIREMENT_SCHEMA),
        "logic_kind": _scalar("string", enum={"LOGIC", "EVENT"}),
        "forbidden_symbols": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

WIRE_MAPPING_ENTRY_SCHEMA = _object(
    {
        "wire_mapping_id": STRING,
        "function_id": STRING,
        "message_id": STRING,
        "field_id": STRING,
        "direction": _scalar("string", enum={"parse", "serialize", "validate", "handle"}),
        "packet_name": STRING,
        "wire_field": STRING,
        "strategy": _scalar("string", enum={"store_in_field", "parse_and_skip", "reject_if_present"}),
        "target_path": STRING,
        "source_expr": STRING,
        "rule": STRING,
        "mapping_role": STRING,
        "required": BOOL,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

ACCESS_PATH_ENTRY_SCHEMA = _object(
    {
        "access_path_id": STRING,
        "function_id": STRING,
        "field_id": STRING,
        "path": STRING,
        "c_type": STRING,
        "access_kind": _scalar("string", enum={"read", "write", "read_write"}),
        "role": STRING,
        "validity_condition": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

FUNCTION_BINDING_UPDATE_SCHEMA = _object(
    {
        "function_id": STRING,
        "wire_mapping_ids": STRING_LIST,
        "access_path_ids": STRING_LIST,
    }
)

CALL_EDGE_SCHEMA = _object(
    {
        "callee_function_id": STRING,
        "call_kind": _scalar("string", enum={"service_requirement", "parser_delegate", "serializer_delegate", "handler_dispatch", "state_access", "lifecycle", "error_handling", "utility"}),
        "required": BOOL,
        "service_requirement_ids": STRING_LIST,
        "call_reason": STRING,
        "param_bindings": _array(_object({"param_name": STRING, "value_ref": STRING, "ownership": STRING, "nullability": STRING})),
        "return_binding": _object(
            {
                "policy": _scalar("string", enum={"ignore", "branch_on_bool", "return_to_caller", "store_then_cleanup", "out_param"}),
                "target_ref": STRING,
                "cleanup_function_id": STRING,
            }
        ),
        "failure_behavior": _scalar("string", enum={"close_connection", "return_error", "cleanup_and_return", "ignore"}),
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

CALLS_ALLOWED_UPDATE_SCHEMA = _object(
    {
        "caller_function_id": STRING,
        "calls_allowed": _array(CALL_EDGE_SCHEMA),
    }
)

LIFECYCLE_FUNCTION_IDS_SCHEMA = _object(
    {
        "create": STRING,
        "start": STRING,
        "run": STRING,
        "destroy": STRING,
    }
)

STARTUP_STEP_SCHEMA = _object(
    {
        "step": STRING,
        "function_id": STRING,
        "description": STRING,
    }
)

RUNTIME_ENTRYPOINT_SCHEMA = _object(
    {
        "schema_version": _scalar("string", enum={"runtime_entrypoint_candidate/v1"}),
        "candidate_id": STRING,
        "producer": PRODUCER_SCHEMA,
        "key_flow_module_id": STRING,
        "lifecycle_function_ids": LIFECYCLE_FUNCTION_IDS_SCHEMA,
        "source_path": STRING,
        "entrypoint_signature": FUNCTION_SIGNATURE_SCHEMA,
        "startup_sequence": _array(STARTUP_STEP_SCHEMA),
        "assumptions": _array(ASSUMPTION_SCHEMA),
        "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
    }
)

FILE_ITEM_SCHEMA = _object(
    {
        "file_id": STRING,
        "source_path": STRING,
        "header_path": STRING,
        "module_id": STRING,
        "kind": _scalar("string", enum={"source_header_pair"}),
        "responsibility": STRING,
        "exports_function_ids": STRING_LIST,
        "implements_function_ids": STRING_LIST,
        "exports_type_ids": STRING_LIST,
        "imports_allowed": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

FUNCTION_FILE_ASSIGNMENT_SCHEMA = _object(
    {
        "function_id": STRING,
        "implementation_file_id": STRING,
        "declaration_file_id": STRING,
        "visibility": VISIBILITY,
        "reason": STRING,
        "status": STATUS,
    }
)

REPAIR_ACTION_SCHEMAS = {
    "remove_call_edge": _object({"action_kind": _scalar("string", enum={"remove_call_edge"}), "caller_function_id": STRING, "callee_function_id": STRING, "reason": STRING}),
    "adjust_imports_allowed": _object(
        {
            "action_kind": _scalar("string", enum={"adjust_imports_allowed"}),
            "file_id": STRING,
            "add_import_file_ids": STRING_LIST,
            "remove_import_file_ids": STRING_LIST,
            "reason": STRING,
        }
    ),
    "lower_visibility": _object({"action_kind": _scalar("string", enum={"lower_visibility"}), "function_id": STRING, "new_visibility": VISIBILITY, "reason": STRING}),
    "change_function_file_assignment": _object(
        {
            "action_kind": _scalar("string", enum={"change_function_file_assignment"}),
            "function_id": STRING,
            "new_implementation_file_id": STRING,
            "new_declaration_file_id": STRING,
            "reason": STRING,
        }
    ),
    "mark_unresolved": _object({"action_kind": _scalar("string", enum={"mark_unresolved"}), "target_id": STRING, "target_kind": STRING, "reason": STRING}),
}

REPAIR_ACTION_SCHEMA = {"type": "union", "discriminator": "action_kind", "variants": REPAIR_ACTION_SCHEMAS}

SCHEMA_SPECS: dict[str, SchemaSpec] = {
    "core_design_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"core_design_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "canonical_types": _array(CANONICAL_TYPE_SCHEMA),
            "state_design": _array(STATE_DESIGN_SCHEMA),
            "handler_matrix": _array(HANDLER_MATRIX_SCHEMA),
            "resource_lifecycle": _array(RESOURCE_LIFECYCLE_SCHEMA),
            "error_strategy": _array(ERROR_STRATEGY_SCHEMA),
            "test_plan_seed": _array(TEST_PLAN_SEED_SCHEMA),
            "traceability": TRACEABILITY_SCHEMA,
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "module_artifacts_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"module_artifacts_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "modules": _array(MODULE_ARTIFACT_ENTRY_SCHEMA),
            "generation_order": STRING_LIST,
            "consistency_rules": _array(MODULE_ARTIFACT_CONSISTENCY_RULE_SCHEMA),
            "forbidden_symbols": _array(MODULE_ARTIFACT_FORBIDDEN_SYMBOL_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "type_inventory_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"type_inventory_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "types": _array(TYPE_INVENTORY_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "type_inventory_repair_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"type_inventory_repair_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "added_types": _array(TYPE_INVENTORY_SCHEMA),
            "updated_types": _array(TYPE_INVENTORY_UPDATE_SCHEMA),
            "added_assumptions": _array(ASSUMPTION_SCHEMA),
            "added_unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_inventory_candidate/v2": _object(
        {
            "schema_version": _scalar("string", enum={"function_inventory_candidate/v2"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "functions": _array(FUNCTION_INVENTORY_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_inventory_repair_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"function_inventory_repair_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "added_functions": _array(FUNCTION_INVENTORY_SCHEMA),
            "updated_functions": _array(FUNCTION_INVENTORY_UPDATE_SCHEMA),
            "added_assumptions": _array(ASSUMPTION_SCHEMA),
            "added_unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_signature_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"function_signature_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "batch": BATCH_SCHEMA,
            "function_signature_updates": _array(FUNCTION_SIGNATURE_UPDATE_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_behavior_contract_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"function_behavior_contract_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_id": STRING,
            "batch": BATCH_SCHEMA,
            "function_behavior_updates": _array(FUNCTION_BEHAVIOR_UPDATE_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "wire_access_binding_patch/v2": _object(
        {
            "schema_version": _scalar("string", enum={"wire_access_binding_patch/v2"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "wire_mapping_entries": _array(WIRE_MAPPING_ENTRY_SCHEMA),
            "access_path_entries": _array(ACCESS_PATH_ENTRY_SCHEMA),
            "function_binding_updates": _array(FUNCTION_BINDING_UPDATE_SCHEMA),
            "forbidden_symbols": STRING_LIST,
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "calls_allowed_candidate/v2": _object(
        {
            "schema_version": _scalar("string", enum={"calls_allowed_candidate/v2"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "call_updates": _array(CALLS_ALLOWED_UPDATE_SCHEMA),
            "unresolved_service_requirements": STRING_LIST,
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "runtime_entrypoint_candidate/v1": RUNTIME_ENTRYPOINT_SCHEMA,
    "file_layout_candidate/v2": _object(
        {
            "schema_version": _scalar("string", enum={"file_layout_candidate/v2"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "files": _array(FILE_ITEM_SCHEMA),
            "function_file_assignments": _array(FUNCTION_FILE_ASSIGNMENT_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "dependency_repair_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"dependency_repair_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "repair_actions": _array(REPAIR_ACTION_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
}


def output_shape(schema_version: str) -> dict[str, Any]:
    return _shape_for_prompt(SCHEMA_SPECS[schema_version])


def _shape_for_prompt(spec: SchemaSpec) -> Any:
    kind = spec.get("type")
    if kind == "object":
        return {
            "type": "object",
            "required": list(spec.get("required", [])),
            "additionalProperties": False,
            "properties": {key: _shape_for_prompt(child) for key, child in spec.get("properties", {}).items()},
        }
    if kind == "array":
        return {"type": "array", "items": _shape_for_prompt(spec["items"])}
    if kind == "union":
        return {
            "oneOf": [
                _shape_for_prompt(variant)
                for variant in spec.get("variants", {}).values()
            ],
            "discriminator": spec.get("discriminator"),
        }
    result = {"type": kind}
    if "enum" in spec:
        result["enum"] = list(spec["enum"])
    return result


def validate_shape(value: Any, schema_version: str, *, path: str | None = None) -> list[PlanningDiagnostic]:
    if schema_version not in SCHEMA_SPECS:
        return [PlanningDiagnostic("error", "unknown_shape_schema", f"No shape schema registered for {schema_version}", path)]
    diagnostics: list[PlanningDiagnostic] = []
    _validate_node(value, SCHEMA_SPECS[schema_version], path or "$", diagnostics)
    return diagnostics


def _validate_node(value: Any, spec: SchemaSpec, path: str, diagnostics: list[PlanningDiagnostic]) -> None:
    kind = spec.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be an object", path))
            return
        properties = spec.get("properties", {})
        for key in spec.get("required", []):
            if key not in value:
                diagnostics.append(PlanningDiagnostic("error", "missing_required_field", f"{path}.{key} is required", path))
        for key in value:
            if key not in properties:
                diagnostics.append(PlanningDiagnostic("error", "forbidden_extra_field", f"{path}.{key} is not allowed", path))
        for key, child_spec in properties.items():
            if key in value:
                _validate_node(value[key], child_spec, f"{path}.{key}", diagnostics)
        return
    if kind == "array":
        if not isinstance(value, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be an array", path))
            return
        for idx, item in enumerate(value):
            _validate_node(item, spec["items"], f"{path}[{idx}]", diagnostics)
        return
    if kind == "union":
        if not isinstance(value, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be an object", path))
            return
        discriminator = str(spec.get("discriminator", ""))
        action_kind = str(value.get(discriminator, ""))
        variant = spec.get("variants", {}).get(action_kind)
        if variant is None:
            diagnostics.append(PlanningDiagnostic("error", "invalid_enum_value", f"{path}.{discriminator} has invalid value '{action_kind}'", path))
            return
        _validate_node(value, variant, path, diagnostics)
        return
    if kind == "string":
        if not isinstance(value, str):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be a string", path))
            return
        enum = set(spec.get("enum", []))
        if enum and value not in enum:
            code = "invalid_schema_version" if path.endswith(".schema_version") else "invalid_enum_value"
            diagnostics.append(PlanningDiagnostic("error", code, f"{path} has invalid value '{value}'", path))
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be a boolean", path))
        return
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            diagnostics.append(PlanningDiagnostic("error", "invalid_field_type", f"{path} must be an integer", path))
        return
    diagnostics.append(PlanningDiagnostic("error", "unknown_shape_type", f"{path} uses unknown schema type '{kind}'", path))


def copy_schema_spec(schema_version: str) -> SchemaSpec:
    return deepcopy(SCHEMA_SPECS[schema_version])
