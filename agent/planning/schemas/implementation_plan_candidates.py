from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..diagnostics import PlanningDiagnostic


SchemaSpec = dict[str, Any]

STATUS_VALUES = {"supported", "inferred", "assumed", "unresolved"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
VISIBILITY_VALUES = {"public", "internal", "private", "static"}


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

PUBLIC_API_POLICY_SCHEMA = _object(
    {
        "exposes_public_api": BOOL,
        "api_style": _scalar("string", enum={"opaque_handle", "callback", "procedural", "none", "unknown"}),
        "visibility_rules": STRING_LIST,
        "notes": STRING,
    }
)

MODULE_CONTRACT_SCHEMA = _object(
    {
        "module_id": STRING,
        "purpose": STRING,
        "owned_capability_ids": STRING_LIST,
        "consumed_capability_ids": STRING_LIST,
        "public_api_policy": PUBLIC_API_POLICY_SCHEMA,
        "owned_state_ids": STRING_LIST,
        "read_state_ids": STRING_LIST,
        "mutated_state_ids": STRING_LIST,
        "error_responsibility_ids": STRING_LIST,
        "constraint_ids": STRING_LIST,
        "dependency_policy": STRING,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

CAPABILITY_OWNERSHIP_SCHEMA = _object(
    {
        "capability_id": STRING,
        "primary_owner_module_id": STRING,
        "shared_owner_module_ids": STRING_LIST,
        "ownership_kind": _scalar("string", enum={"primary", "shared"}),
        "reason": STRING,
    }
)

STATE_OWNERSHIP_SCHEMA = _object(
    {
        "state_id": STRING,
        "owner_module_id": STRING,
        "read_by_module_ids": STRING_LIST,
        "mutated_by_module_ids": STRING_LIST,
        "reason": STRING,
    }
)

CONSTRAINT_BINDING_SCHEMA = _object(
    {
        "constraint_id": STRING,
        "module_ids": STRING_LIST,
        "binding_reason": STRING,
    }
)

FUNCTION_INVENTORY_SCHEMA = _object(
    {
        "function_id": STRING,
        "name": STRING,
        "module_id": STRING,
        "placement_hint": STRING,
        "required_declaration": BOOL,
        "visibility": VISIBILITY,
        "function_kind": _scalar(
            "string",
            enum={"public_api", "handler", "parser", "serializer", "state_machine", "resource_lifecycle", "error_helper", "internal_helper", "test_support"},
        ),
        "purpose": STRING,
        "capability_ids": STRING_LIST,
        "covers_handler_ids": STRING_LIST,
        "covers_message_ids": STRING_LIST,
        "covers_field_ids": STRING_LIST,
        "trace_ref_keys": STRING_LIST,
        "status": STATUS,
    }
)

CONTRACT_SCHEMA = _object(
    {
        "contract_kind": _scalar("string", enum={"none", "typed", "buffer", "opaque", "callback", "unknown"}),
        "type_refs": STRING_LIST,
        "buffer_refs": STRING_LIST,
        "ownership": _scalar("string", enum={"borrowed", "owned", "transferred", "value", "none", "unknown"}),
        "nullability": _scalar("string", enum={"nullable", "non_null", "mixed", "not_applicable", "unknown"}),
        "validation_required": BOOL,
        "notes": STRING,
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

FUNCTION_CONTRACT_UPDATE_SCHEMA = _object(
    {
        "function_id": STRING,
        "input_contract": CONTRACT_SCHEMA,
        "output_contract": CONTRACT_SCHEMA,
        "state_access": _array(STATE_ACCESS_SCHEMA),
        "error_behavior": ERROR_BEHAVIOR_SCHEMA,
        "side_effects": STRING_LIST,
        "preconditions": STRING_LIST,
        "postconditions": STRING_LIST,
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
        "state_id": STRING,
        "access_kind": _scalar("string", enum={"read", "write", "read_write"}),
        "access_path": STRING,
        "required": BOOL,
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
        "call_reason": STRING,
        "required": BOOL,
        "call_kind": _scalar("string", enum={"parse_delegate", "serialize_delegate", "state_access", "error_handling", "lifecycle", "handler_dispatch", "utility", "test_only"}),
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

FILE_ITEM_SCHEMA = _object(
    {
        "file_id": STRING,
        "path": STRING,
        "module_id": STRING,
        "kind": _scalar("string", enum={"header", "source", "test", "main"}),
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
    "module_contracts_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"module_contracts_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "module_contracts": _array(MODULE_CONTRACT_SCHEMA),
            "capability_ownership_claims": _array(CAPABILITY_OWNERSHIP_SCHEMA),
            "state_ownership_claims": _array(STATE_OWNERSHIP_SCHEMA),
            "constraint_bindings": _array(CONSTRAINT_BINDING_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_inventory_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"function_inventory_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "functions": _array(FUNCTION_INVENTORY_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "function_contract_detail_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"function_contract_detail_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "function_contract_updates": _array(FUNCTION_CONTRACT_UPDATE_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "wire_access_binding_patch/v1": _object(
        {
            "schema_version": _scalar("string", enum={"wire_access_binding_patch/v1"}),
            "patch_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "wire_mapping_entries": _array(WIRE_MAPPING_ENTRY_SCHEMA),
            "access_path_entries": _array(ACCESS_PATH_ENTRY_SCHEMA),
            "function_binding_updates": _array(FUNCTION_BINDING_UPDATE_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "calls_allowed_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"calls_allowed_candidate/v1"}),
            "candidate_id": STRING,
            "producer": PRODUCER_SCHEMA,
            "calls_allowed_updates": _array(CALLS_ALLOWED_UPDATE_SCHEMA),
            "assumptions": _array(ASSUMPTION_SCHEMA),
            "unresolved_questions": _array(UNRESOLVED_QUESTION_SCHEMA),
        }
    ),
    "file_layout_candidate/v1": _object(
        {
            "schema_version": _scalar("string", enum={"file_layout_candidate/v1"}),
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
    diagnostics.append(PlanningDiagnostic("error", "unknown_shape_type", f"{path} uses unknown schema type '{kind}'", path))


def copy_schema_spec(schema_version: str) -> SchemaSpec:
    return deepcopy(SCHEMA_SPECS[schema_version])
