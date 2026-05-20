from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic, has_errors
from ..schemas.implementation_plan import (
    CALLS_ALLOWED_CANDIDATE_SCHEMA_VERSION,
    CORE_DESIGN_CANDIDATE_SCHEMA_VERSION,
    DEPENDENCY_REPAIR_PATCH_SCHEMA_VERSION,
    FILE_LAYOUT_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_BEHAVIOR_CONTRACT_PATCH_SCHEMA_VERSION,
    FUNCTION_INVENTORY_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_SIGNATURE_PATCH_SCHEMA_VERSION,
    MODULE_CONTRACTS_CANDIDATE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
    WIRE_ACCESS_BINDING_PATCH_SCHEMA_VERSION,
)
from ..schemas.implementation_plan_candidates import validate_shape
from ..stages.implementation_plan import _handler_surfaces, _safe_id, _surface_units, _wire_fields
from ..stages.implementation_plan_context import SYSTEM_TYPE_IDS
from .implementation_plan import validate_implementation_plan


ALLOWED_FUNCTION_KINDS = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}


def validation_report(stage: str, diagnostics: list[PlanningDiagnostic]) -> dict[str, Any]:
    errors = [item for item in diagnostics if item.level == "error"]
    warnings = [item for item in diagnostics if item.level != "error"]
    return {
        "schema_version": VALIDATION_REPORT_SCHEMA_VERSION,
        "stage": stage,
        "passed": not errors,
        "errors": [
            {"code": item.code, "path": item.path, "message": item.message, "severity": "error", "repairable": True}
            for item in errors
        ],
        "warnings": [
            {"code": item.code, "path": item.path, "message": item.message, "severity": item.level, "repairable": False}
            for item in warnings
        ],
        "repair_hints": [item.message for item in errors[:8]],
    }


def _shape(value: Any, expected: str, *, path: str | None) -> list[PlanningDiagnostic]:
    diagnostics = validate_shape(value, expected, path=path)
    if isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_schema_version", f"stage output must not be a full {SCHEMA_VERSION}", path))
    return diagnostics


def _required_capabilities(profile: dict[str, Any]) -> set[str]:
    return {
        str(item.get("capability_id", "")).strip()
        for item in profile.get("required_capabilities", [])
        if isinstance(item, dict) and str(item.get("capability_id", "")).strip()
    }


def _constraint_ids(constraints: dict[str, Any]) -> set[str]:
    return {
        str(item.get("constraint_id", "")).strip()
        for item in constraints.get("constraints", [])
        if isinstance(item, dict) and str(item.get("constraint_id", "")).strip()
    }


def _module_ids_from_arch(selected_architecture: dict[str, Any]) -> set[str]:
    return {
        str(item.get("module_id", "")).strip()
        for item in selected_architecture.get("architecture", {}).get("modules", [])
        if isinstance(item, dict) and str(item.get("module_id", "")).strip()
    }


def _support_module_ids(selected_architecture: dict[str, Any]) -> set[str]:
    return {
        str(item.get("module_id", "")).strip()
        for item in selected_architecture.get("architecture", {}).get("modules", [])
        if isinstance(item, dict) and item.get("support_module")
    }


def _state_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("state_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict) and item.get("state_id")}


def _error_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("error_id", "")) for item in draft.get("error_strategy", []) if isinstance(item, dict) and item.get("error_id")}


def _type_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("type_id", "")) for item in draft.get("canonical_types", []) if isinstance(item, dict) and item.get("type_id")}


def _handler_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("handler_id", "")) for item in draft.get("handler_matrix", []) if isinstance(item, dict) and item.get("handler_id")}


def _function_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_id")}


def _field_ids(planning_ir: dict[str, Any]) -> set[str]:
    return {str(item.get("field_id", "")) for item in _wire_fields(planning_ir) if str(item.get("field_id", "")).strip()}


def _message_ids(planning_ir: dict[str, Any]) -> set[str]:
    return {f"message:{_safe_id(str(item.get('message', '')))}" for item in _wire_fields(planning_ir) if str(item.get("message", "")).strip()}


def _target_surface_ids(planning_ir: dict[str, Any], profile: dict[str, Any]) -> set[str]:
    target_role = str(planning_ir.get("target_directives", {}).get("directives", {}).get("target_role", ""))
    return {str(item.get("name", "")) for item in _handler_surfaces(_surface_units(planning_ir, profile), target_role) if str(item.get("name", "")).strip()}


def _unresolved_targets(candidate: dict[str, Any]) -> set[str]:
    return {
        str(item.get("target_id", ""))
        for item in candidate.get("unresolved_questions", [])
        if isinstance(item, dict) and str(item.get("target_id", "")).strip()
    }


def _module_contract_ids(module_contracts: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("module_id", "")) for item in module_contracts if isinstance(item, dict) and str(item.get("module_id", "")).strip()}


def _function_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("function_id", "")): item for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_id")}


def _file_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("file_id", "")) for item in draft.get("file_layout", {}).get("files", []) if isinstance(item, dict) and item.get("file_id")}


def validate_plan_skeleton(draft: dict[str, Any], selected_architecture: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if draft.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_plan_skeleton_schema", f"implementation_plan draft must use {SCHEMA_VERSION}", path))
    for key in ("source_artifact_refs", "id_namespace", "validation_targets", "deterministic_indexes"):
        if key not in draft:
            diagnostics.append(PlanningDiagnostic("error", "missing_plan_skeleton_key", f"plan skeleton missing {key}", path))
    if draft.get("module_contracts") != [] or draft.get("function_contracts") != []:
        diagnostics.append(PlanningDiagnostic("error", "nonempty_plan_skeleton_design", "plan skeleton must not include module/function design details", path))
    if draft.get("dependency_graph") is not None:
        diagnostics.append(PlanningDiagnostic("error", "nonempty_plan_skeleton_dependency_graph", "plan skeleton dependency_graph must be null", path))
    if set(draft.get("id_namespace", {}).get("module_ids", [])) != _module_ids_from_arch(selected_architecture):
        diagnostics.append(PlanningDiagnostic("error", "skeleton_module_namespace_mismatch", "skeleton module namespace must match selected architecture", path))
    if set(draft.get("id_namespace", {}).get("capability_ids", [])) != _required_capabilities(profile):
        diagnostics.append(PlanningDiagnostic("error", "skeleton_capability_namespace_mismatch", "skeleton capability namespace must match protocol profile", path))
    if set(draft.get("id_namespace", {}).get("constraint_ids", [])) != _constraint_ids(constraints):
        diagnostics.append(PlanningDiagnostic("error", "skeleton_constraint_namespace_mismatch", "skeleton constraint namespace must match engineering constraints", path))
    return diagnostics


def validate_core_design_candidate(candidate: dict[str, Any], planning_ir: dict[str, Any], profile: dict[str, Any], selected_architecture: dict[str, Any], constraints: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, CORE_DESIGN_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_ids_from_arch(selected_architecture)
    capability_ids = _required_capabilities(profile)
    constraint_ids = _constraint_ids(constraints)
    field_ids = _field_ids(planning_ir)
    message_ids = _message_ids(planning_ir)
    seen: dict[str, set[str]] = {"type_id": set(), "state_id": set(), "handler_id": set(), "resource_id": set(), "error_id": set()}
    for key, section in (("type_id", "canonical_types"), ("state_id", "state_design"), ("handler_id", "handler_matrix"), ("resource_id", "resource_lifecycle"), ("error_id", "error_strategy")):
        for item in candidate.get(section, []):
            item_id = str(item.get(key, ""))
            if item_id in seen[key]:
                diagnostics.append(PlanningDiagnostic("error", f"duplicate_{key}", f"duplicate {key} '{item_id}'", path))
            seen[key].add(item_id)
    for item in candidate.get("canonical_types", []):
        if item["owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_type_owner", f"type '{item['type_id']}' owner is not selected", path))
        for field_id in item["source_field_ids"]:
            if field_id and field_id not in field_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_type_field", f"type '{item['type_id']}' references unknown field '{field_id}'", path))
        for message_id in item["source_message_ids"]:
            if message_id and message_id not in message_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_type_message", f"type '{item['type_id']}' references unknown message '{message_id}'", path))
    for state in candidate.get("state_design", []):
        if state["owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_state_owner", f"state owner '{state['owner_module_id']}' is not a selected module", path))
        for module_id in state["read_by_module_ids"] + state["mutated_by_module_ids"]:
            if module_id not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_state_access_module", f"state '{state['state_id']}' references unknown module '{module_id}'", path))
        for cap in state["source_capability_ids"]:
            if cap not in capability_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_state_capability", f"state '{state['state_id']}' references unknown capability '{cap}'", path))
    for handler in candidate.get("handler_matrix", []):
        if handler["owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_handler_owner", f"handler '{handler['handler_id']}' owner is not selected", path))
        if handler["capability_id"] not in capability_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_handler_capability", f"handler references unknown capability '{handler['capability_id']}'", path))
        for message_id in handler["message_ids"]:
            if message_id and message_id not in message_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_handler_message", f"handler '{handler['handler_id']}' references unknown message '{message_id}'", path))
    for item in candidate.get("resource_lifecycle", []):
        if item["owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_resource_owner", f"resource '{item['resource_id']}' owner is not selected", path))
    for item in candidate.get("error_strategy", []):
        if item["owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_error_owner", f"error '{item['error_id']}' owner is not selected", path))
        for constraint_id in item["related_constraint_ids"]:
            if constraint_id not in constraint_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_error_strategy_constraint", f"error strategy references unknown constraint '{constraint_id}'", path))
    covered_surfaces = {str(item.get("trigger", "")) for item in candidate.get("handler_matrix", [])}
    unresolved = _unresolved_targets(candidate)
    for surface in sorted(_target_surface_ids(planning_ir, profile) - covered_surfaces - unresolved):
        diagnostics.append(PlanningDiagnostic("error", "uncovered_target_surface", f"target-scope surface '{surface}' is not in handler_matrix or unresolved_questions", path))
    return diagnostics


def validate_module_contracts_candidate(candidate: dict[str, Any], selected_architecture: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], core_design: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, MODULE_CONTRACTS_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_ids_from_arch(selected_architecture)
    support_modules = _support_module_ids(selected_architecture)
    capability_ids = _required_capabilities(profile)
    constraint_ids = _constraint_ids(constraints)
    state_ids = _state_ids(core_design)
    error_ids = _error_ids(core_design)
    primary_owner: dict[str, str] = {}
    for module in candidate["module_contracts"]:
        module_id = module["module_id"]
        if module_id not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_module_contract_module", f"module_id '{module_id}' is not selected", path))
        if module_id not in support_modules and not module["owned_capability_ids"]:
            diagnostics.append(PlanningDiagnostic("error", "module_contract_without_capability", f"module '{module_id}' owns no capabilities", path))
        for cap in module["owned_capability_ids"] + module["consumed_capability_ids"]:
            if cap not in capability_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_contract_capability", f"module '{module_id}' references unknown capability '{cap}'", path))
        for cap in module["owned_capability_ids"]:
            previous = primary_owner.setdefault(cap, module_id)
            if previous != module_id:
                diagnostics.append(PlanningDiagnostic("error", "conflicting_capability_owner", f"capability '{cap}' has multiple primary owners", path))
        for state_id in module["owned_state_ids"] + module["read_state_ids"] + module["mutated_state_ids"]:
            if state_id not in state_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_state_ref", f"module '{module_id}' references unknown state '{state_id}'", path))
        for error_id in module["error_responsibility_ids"]:
            if error_id not in error_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_error_ref", f"module '{module_id}' references unknown error '{error_id}'", path))
        for constraint_id in module["constraint_ids"]:
            if constraint_id not in constraint_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_contract_constraint", f"module '{module_id}' references unknown constraint '{constraint_id}'", path))
    for claim in candidate["capability_ownership_claims"]:
        if claim["capability_id"] not in capability_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_capability_ownership_claim", f"ownership claim references unknown capability '{claim['capability_id']}'", path))
        if claim["primary_owner_module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_capability_owner_module", f"ownership claim references unknown module '{claim['primary_owner_module_id']}'", path))
    return diagnostics


def validate_function_inventory_candidate(candidate: dict[str, Any], module_contracts: list[dict[str, Any]], core_design: dict[str, Any], profile: dict[str, Any], planning_ir: dict[str, Any] | None = None, *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, FUNCTION_INVENTORY_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_contract_ids(module_contracts)
    if candidate.get("module_id") not in module_ids and candidate.get("module_id") != "all_modules":
        diagnostics.append(PlanningDiagnostic("error", "unknown_function_inventory_module", f"candidate module_id '{candidate.get('module_id')}' is not a module", path))
    capability_ids = _required_capabilities(profile)
    handler_ids = _handler_ids(core_design)
    message_ids = _message_ids(planning_ir or {})
    field_ids = _field_ids(planning_ir or {})
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    kinds: set[str] = set()
    for function in candidate["functions"]:
        function_id = function["function_id"]
        name = function["name"]
        if function_id in seen_ids:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_function_id", f"duplicate function_id '{function_id}'", path))
        seen_ids.add(function_id)
        if name in seen_names:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_function_name", f"duplicate function name '{name}'", path))
        seen_names.add(name)
        kinds.add(function["function_kind"])
        if function["module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_function_module", f"function '{function_id}' belongs to unknown module", path))
        for cap in function["capability_ids"]:
            if cap not in capability_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_inventory_capability", f"function '{function_id}' references unknown capability '{cap}'", path))
        for handler_id in function["covers_handler_ids"]:
            if handler_id not in handler_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_handler_ref", f"function '{function_id}' references unknown handler '{handler_id}'", path))
        for message_id in function["covers_message_ids"]:
            if message_ids and message_id not in message_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_message_ref", f"function '{function_id}' references unknown message '{message_id}'", path))
        for field_id in function["covers_field_ids"]:
            if field_ids and field_id not in field_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_field_ref", f"function '{function_id}' references unknown field '{field_id}'", path))
    candidate_caps = {cap for function in candidate["functions"] for cap in function["capability_ids"]}
    unresolved = _unresolved_targets(candidate)
    if "message_decode" in candidate_caps and "parser" not in kinds and "message_decode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_parser_function", "message_decode requires a parser entry function or unresolved question", path))
    if "message_encode" in candidate_caps and "serializer" not in kinds and "message_encode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_serializer_function", "message_encode requires a serializer entry function or unresolved question", path))
    if any(cap in candidate_caps for cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}) and core_design.get("handler_matrix") and "handler" not in kinds:
        diagnostics.append(PlanningDiagnostic("error", "missing_handler_function", "handler_matrix requires handler functions", path))
    return diagnostics


def _batch_function_ids(patch: dict[str, Any], key: str) -> set[str]:
    return {str(item.get("function_id", "")) for item in patch.get(key, []) if isinstance(item, dict)}


def _service_requirement_ids(functions: dict[str, dict[str, Any]], caller_ids: set[str] | None = None, *, kinds: set[str] | None = None) -> set[str]:
    result: set[str] = set()
    for function_id, function in functions.items():
        if caller_ids is not None and function_id not in caller_ids:
            continue
        for requirement in function.get("service_requirements", []):
            if not isinstance(requirement, dict):
                continue
            requirement_id = str(requirement.get("service_requirement_id", "")).strip()
            if not requirement_id:
                continue
            requirement_kind = str(requirement.get("requirement_kind", "cross_module_service"))
            if kinds is None or requirement_kind in kinds:
                result.add(requirement_id)
    return result


def validate_function_signature_patch(patch: dict[str, Any], draft: dict[str, Any], expected_function_ids: set[str] | None = None, *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, FUNCTION_SIGNATURE_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    functions = _function_by_id(draft)
    function_ids = set(functions)
    type_ids = _type_ids(draft)
    system_type_ids = set(SYSTEM_TYPE_IDS)
    legal_type_refs = type_ids | system_type_ids
    module_ids = _module_contract_ids(draft.get("module_contracts", []))
    target_ids = _batch_function_ids(patch, "function_signature_updates")
    if expected_function_ids is not None and target_ids != expected_function_ids:
        diagnostics.append(PlanningDiagnostic("error", "signature_batch_coverage_mismatch", "signature patch must update exactly the current batch functions", path))
    seen: set[str] = set()
    for update in patch["function_signature_updates"]:
        function_id = update["function_id"]
        function = functions.get(function_id)
        if function_id in seen:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_signature_update", f"duplicate signature update for '{function_id}'", path))
        seen.add(function_id)
        if function_id not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_function_signature_target", f"patch updates unknown function '{function_id}'", path))
            continue
        signature = update["signature"]
        if signature["name"] != function.get("name"):
            diagnostics.append(PlanningDiagnostic("error", "signature_name_mismatch", f"signature name for '{function_id}' must match inventory name", path))
        if not signature["raw"].strip() or not signature["return_type"].strip():
            diagnostics.append(PlanningDiagnostic("error", "empty_function_signature", f"function '{function_id}' signature is incomplete", path))
        for param in signature["params"]:
            type_ref = str(param.get("type_ref", ""))
            if type_ref.startswith(("state:", "message:", "field:")):
                diagnostics.append(PlanningDiagnostic("error", "invalid_signature_param_type_ref_namespace", f"function '{function_id}' uses non-type namespace as type_ref '{type_ref}'", path))
            elif type_ref and type_ref not in legal_type_refs:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_param_type_ref", f"function '{function_id}' references unknown type_ref '{type_ref}'", path))
        for dep in update["signature_dependencies"]:
            type_ref = str(dep.get("type_ref", ""))
            owner = str(dep.get("owner_module_id", ""))
            if type_ref.startswith(("state:", "message:", "field:")):
                diagnostics.append(PlanningDiagnostic("error", "invalid_signature_dependency_type_namespace", f"function '{function_id}' uses non-type dependency '{type_ref}'", path))
            elif type_ref and dep.get("symbol_kind") != "system_type" and type_ref not in type_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_dependency_type", f"function '{function_id}' references unknown signature dependency '{type_ref}'", path))
            elif type_ref and dep.get("symbol_kind") == "system_type" and type_ref not in system_type_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_system_type", f"function '{function_id}' references unknown system type '{type_ref}'", path))
            if owner and owner not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_dependency_owner", f"function '{function_id}' signature dependency owner '{owner}' is not a module", path))
    return diagnostics


def validate_function_behavior_contract_patch(patch: dict[str, Any], draft: dict[str, Any], constraints: dict[str, Any], expected_function_ids: set[str] | None = None, *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, FUNCTION_BEHAVIOR_CONTRACT_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    functions = _function_by_id(draft)
    function_ids = set(functions)
    state_ids = _state_ids(draft)
    state_owner = {str(item.get("state_id", "")): str(item.get("owner_module_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict)}
    error_ids = _error_ids(draft)
    capability_ids = set(draft.get("traceability", {}).get("required_capabilities", []))
    target_ids = _batch_function_ids(patch, "function_behavior_updates")
    if expected_function_ids is not None and target_ids != expected_function_ids:
        diagnostics.append(PlanningDiagnostic("error", "behavior_batch_coverage_mismatch", "behavior patch must update exactly the current batch functions", path))
    seen: set[str] = set()
    for update in patch["function_behavior_updates"]:
        function_id = update["function_id"]
        function = functions.get(function_id)
        if function_id in seen:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_behavior_update", f"duplicate behavior update for '{function_id}'", path))
        seen.add(function_id)
        if function_id not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_function_behavior_target", f"patch updates unknown function '{function_id}'", path))
            continue
        if "callee_function_id" in str(update.get("service_requirements", [])):
            diagnostics.append(PlanningDiagnostic("error", "behavior_must_not_resolve_calls", f"function '{function_id}' behavior may not include callee_function_id", path))
        for state in update["state_access"]:
            state_id = state["state_id"]
            if state_id not in state_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_state_access", f"function '{function_id}' references unknown state '{state_id}'", path))
            if state["access_kind"] in {"write", "read_write"} and state_id in state_owner and state_owner[state_id] != function.get("module_id"):
                diagnostics.append(PlanningDiagnostic("error", "function_state_write_owner_mismatch", f"function '{function_id}' cannot mutate state '{state_id}' owned by another module", path))
        for error_id in update["error_behavior"]["error_ids"]:
            if error_id not in error_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_error_behavior", f"function '{function_id}' references unknown error '{error_id}'", path))
        for requirement in update["service_requirements"]:
            for cap in requirement["required_capability_ids"]:
                if capability_ids and cap not in capability_ids:
                    diagnostics.append(PlanningDiagnostic("error", "unknown_service_requirement_capability", f"function '{function_id}' service requirement references unknown capability '{cap}'", path))
    for constraint_id in _constraint_ids(constraints):
        if not constraint_id:
            diagnostics.append(PlanningDiagnostic("error", "invalid_function_behavior_constraint_index", "empty constraint_id in engineering constraints", path))
    return diagnostics


def validate_wire_access_binding_patch(patch: dict[str, Any], draft: dict[str, Any], planning_ir: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, WIRE_ACCESS_BINDING_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    functions = _function_by_id(draft)
    function_ids = set(functions)
    field_ids = _field_ids(planning_ir)
    message_ids = _message_ids(planning_ir)
    wire_ids = {entry["wire_mapping_id"] for entry in patch["wire_mapping_entries"]}
    access_ids = {entry["access_path_id"] for entry in patch["access_path_entries"]}
    mapped_fields: set[str] = set()
    for entry in patch["wire_mapping_entries"]:
        function = functions.get(entry["function_id"])
        if entry["function_id"] not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_wire_binding_function", f"wire mapping references unknown function '{entry['function_id']}'", path))
            continue
        if entry["field_id"] not in field_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_wire_field", f"wire mapping references unknown field '{entry['field_id']}'", path))
        if entry["message_id"] not in message_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_wire_message", f"wire mapping references unknown message '{entry['message_id']}'", path))
        if entry["direction"] in {"parse", "serialize"}:
            mapped_fields.add(entry["field_id"])
        if entry["direction"] == "parse" and function.get("function_kind") != "parser":
            diagnostics.append(PlanningDiagnostic("error", "wire_parse_function_kind_mismatch", f"parse mapping uses non-parser function '{entry['function_id']}'", path))
        if entry["direction"] == "serialize" and function.get("function_kind") != "serializer":
            diagnostics.append(PlanningDiagnostic("error", "wire_serialize_function_kind_mismatch", f"serialize mapping uses non-serializer function '{entry['function_id']}'", path))
    for entry in patch["access_path_entries"]:
        function = functions.get(entry["function_id"])
        if entry["function_id"] not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_function", f"access path references unknown function '{entry['function_id']}'", path))
            continue
        if entry["field_id"] not in field_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_field", f"access path references unknown field '{entry['field_id']}'", path))
        if entry["access_kind"] in {"write", "read_write"} and function.get("function_kind") not in {"handler", "state_machine", "resource_lifecycle", "public_api"}:
            diagnostics.append(PlanningDiagnostic("error", "wire_access_kind_conflict", f"function '{entry['function_id']}' may not write state through access path", path))
    for update in patch["function_binding_updates"]:
        if update["function_id"] not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_wire_function_update", f"wire patch updates unknown function '{update['function_id']}'", path))
        for wire_id in update["wire_mapping_ids"]:
            if wire_id not in wire_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_wire_mapping_id", f"function binding references unknown wire mapping '{wire_id}'", path))
        for access_id in update["access_path_ids"]:
            if access_id not in access_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_id", f"function binding references unknown access path '{access_id}'", path))
    unresolved = _unresolved_targets(patch)
    for field_id in sorted(field_ids - mapped_fields - unresolved):
        diagnostics.append(PlanningDiagnostic("error", "uncovered_wire_field", f"wire field '{field_id}' is not covered by parser/serializer or unresolved_questions", path))
    return diagnostics


def validate_calls_allowed_candidate(
    candidate: dict[str, Any],
    draft: dict[str, Any],
    selected_architecture: dict[str, Any] | None = None,
    *,
    expected_caller_ids: set[str] | None = None,
    expected_service_requirement_ids: set[str] | None = None,
    callable_function_ids: set[str] | None = None,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, CALLS_ALLOWED_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    functions = _function_by_id(draft)
    module_ids = _module_ids_from_arch(selected_architecture or {"architecture": {"modules": draft.get("module_contracts", [])}})
    target_ids = {str(update.get("caller_function_id", "")) for update in candidate["call_updates"]}
    if expected_caller_ids is not None and target_ids != expected_caller_ids:
        diagnostics.append(PlanningDiagnostic("error", "calls_allowed_batch_coverage_mismatch", "calls_allowed candidate must update exactly the current batch callers", path))
    service_requirement_ids = expected_service_requirement_ids if expected_service_requirement_ids is not None else _service_requirement_ids(functions, kinds={"cross_module_service", "external_runtime_service"})
    unresolved_service_ids = set(candidate.get("unresolved_service_requirements", []))
    resolved_service_ids: set[str] = set()
    edges: list[tuple[str, str]] = []
    for update in candidate["call_updates"]:
        caller = update["caller_function_id"]
        caller_fn = functions.get(caller)
        if caller not in functions:
            diagnostics.append(PlanningDiagnostic("error", "unknown_call_caller", f"calls_allowed updates unknown caller '{caller}'", path))
            continue
        for edge in update["calls_allowed"]:
            callee = edge["callee_function_id"]
            callee_fn = functions.get(callee)
            if callee not in functions:
                diagnostics.append(PlanningDiagnostic("error", "unknown_call_callee", f"caller '{caller}' references unknown callee '{callee}'", path))
                continue
            if callable_function_ids is not None and callee not in callable_function_ids and callee_fn.get("module_id") != caller_fn.get("module_id"):
                diagnostics.append(PlanningDiagnostic("error", "call_not_in_callable_universe", f"caller '{caller}' may not call '{callee}' in this scoped batch", path))
            if callee == caller:
                diagnostics.append(PlanningDiagnostic("error", "self_call_not_allowed", f"caller '{caller}' may not call itself", path))
            if callee_fn.get("visibility") in {"private", "static"} and callee_fn.get("module_id") != caller_fn.get("module_id"):
                diagnostics.append(PlanningDiagnostic("error", "private_cross_module_call", f"caller '{caller}' cannot call private/static function '{callee}' across modules", path))
            cleanup = str(edge.get("return_binding", {}).get("cleanup_function_id", ""))
            if cleanup and cleanup not in functions:
                diagnostics.append(PlanningDiagnostic("error", "unknown_call_cleanup_function", f"caller '{caller}' references unknown cleanup function '{cleanup}'", path))
            for requirement_id in edge.get("service_requirement_ids", []):
                if requirement_id not in service_requirement_ids:
                    diagnostics.append(PlanningDiagnostic("error", "unknown_call_service_requirement", f"caller '{caller}' references unknown service requirement '{requirement_id}'", path))
                resolved_service_ids.add(requirement_id)
            if caller_fn.get("module_id") not in module_ids or callee_fn.get("module_id") not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "call_unknown_module", f"call edge '{caller}' -> '{callee}' references unknown module", path))
            edges.append((caller, callee))
    for requirement_id in sorted(service_requirement_ids - resolved_service_ids - unresolved_service_ids):
        diagnostics.append(PlanningDiagnostic("error", "unresolved_service_requirement_missing", f"service requirement '{requirement_id}' must be resolved to a call or listed as unresolved", path))
    for requirement_id in sorted(unresolved_service_ids - service_requirement_ids):
        diagnostics.append(PlanningDiagnostic("error", "unknown_unresolved_service_requirement", f"unresolved service requirement '{requirement_id}' is unknown", path))
    if _has_cycle(edges):
        diagnostics.append(PlanningDiagnostic("error", "calls_allowed_cycle", "calls_allowed forms a prohibited cycle", path))
    return diagnostics


def validate_file_layout_candidate(candidate: dict[str, Any], draft: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, FILE_LAYOUT_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_contract_ids(draft.get("module_contracts", []))
    function_ids = _function_ids(draft)
    type_ids = _type_ids(draft)
    file_ids: set[str] = set()
    paths: set[str] = set()
    for file_item in candidate["files"]:
        file_id = file_item["file_id"]
        expected_file_id = f"file:{str(file_item['source_path']).removesuffix('.c')}"
        if file_id != expected_file_id:
            diagnostics.append(PlanningDiagnostic("error", "invalid_file_unit_id", f"file_id '{file_id}' must be '{expected_file_id}'", path))
        if file_item["kind"] != "source_header_pair":
            diagnostics.append(PlanningDiagnostic("error", "invalid_file_layout_kind", f"file '{file_id}' must be a source_header_pair", path))
        if file_id in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_file_id", f"duplicate file_id '{file_id}'", path))
        file_ids.add(file_id)
        for path_key in ("source_path", "header_path"):
            if file_item[path_key] in paths:
                diagnostics.append(PlanningDiagnostic("error", "duplicate_layout_path", f"duplicate path '{file_item[path_key]}'", path))
            paths.add(file_item[path_key])
        if file_item["module_id"] not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_layout_module", f"file '{file_id}' belongs to unknown module", path))
        for function_id in file_item["exports_function_ids"] + file_item["implements_function_ids"]:
            if function_id not in function_ids:
                diagnostics.append(PlanningDiagnostic("error", "layout_references_unknown_function", f"file '{file_id}' references unknown function '{function_id}'", path))
        for type_id in file_item["exports_type_ids"]:
            if type_ids and type_id not in type_ids:
                diagnostics.append(PlanningDiagnostic("error", "layout_exports_unknown_type", f"file '{file_id}' exports unknown type '{type_id}'", path))
    for file_item in candidate["files"]:
        for imported in file_item["imports_allowed"]:
            if imported not in file_ids:
                diagnostics.append(PlanningDiagnostic("error", "layout_imports_unknown_file", f"file '{file_item['file_id']}' imports unknown file '{imported}'", path))
            if imported == file_item["file_id"]:
                diagnostics.append(PlanningDiagnostic("error", "layout_imports_self", f"file '{file_item['file_id']}' must not import itself", path))
            if str(imported).startswith("header:") or str(imported).endswith((".h", ".c")):
                diagnostics.append(PlanningDiagnostic("error", "layout_imports_path_or_header", f"file '{file_item['file_id']}' imports non-FILE_SPEC target '{imported}'", path))
    assigned = {item["function_id"] for item in candidate["function_file_assignments"]}
    for missing in sorted(function_ids - assigned):
        diagnostics.append(PlanningDiagnostic("error", "unassigned_function_file", f"function '{missing}' is not assigned to a file", path))
    for assignment in candidate["function_file_assignments"]:
        function_id = assignment["function_id"]
        if function_id not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_function", f"layout assigns unknown function '{function_id}'", path))
        if assignment["implementation_file_id"] not in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_file", f"layout assigns function to unknown implementation file '{assignment['implementation_file_id']}'", path))
        declaration_file_id = assignment["declaration_file_id"]
        if declaration_file_id and declaration_file_id not in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_file", f"layout assigns function to unknown declaration file '{declaration_file_id}'", path))
        if assignment["visibility"] == "public":
            if not declaration_file_id:
                diagnostics.append(PlanningDiagnostic("error", "public_function_not_declared", f"public function '{function_id}' has no declaration file", path))
            elif declaration_file_id != assignment["implementation_file_id"]:
                diagnostics.append(PlanningDiagnostic("error", "public_function_declared_outside_file_unit", f"public function '{function_id}' must be declared in its FILE_SPEC unit", path))
        if assignment["visibility"] in {"private", "static"} and declaration_file_id:
            diagnostics.append(PlanningDiagnostic("error", "private_function_exposed_in_header", f"private/static function '{function_id}' must not be exposed in a FILE_SPEC header", path))
    return diagnostics


def validate_dependency_repair_patch(patch: dict[str, Any], draft: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, DEPENDENCY_REPAIR_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    function_ids = _function_ids(draft)
    file_ids = _file_ids(draft)
    for action in patch["repair_actions"]:
        kind = action["action_kind"]
        if kind == "remove_call_edge":
            if action["caller_function_id"] not in function_ids or action["callee_function_id"] not in function_ids:
                diagnostics.append(PlanningDiagnostic("error", "repair_unknown_call_edge", "repair patch references unknown call edge function", path))
        elif kind == "adjust_imports_allowed":
            if action["file_id"] not in file_ids:
                diagnostics.append(PlanningDiagnostic("error", "repair_unknown_file", f"repair adjusts unknown file '{action['file_id']}'", path))
            for file_id in action["add_import_file_ids"] + action["remove_import_file_ids"]:
                if file_id not in file_ids:
                    diagnostics.append(PlanningDiagnostic("error", "repair_unknown_import", f"repair references unknown import file '{file_id}'", path))
        elif kind == "lower_visibility" and action["function_id"] not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "repair_unknown_function", f"repair lowers visibility for unknown function '{action['function_id']}'", path))
        elif kind == "change_function_file_assignment":
            if action["function_id"] not in function_ids:
                diagnostics.append(PlanningDiagnostic("error", "repair_unknown_function", f"repair moves unknown function '{action['function_id']}'", path))
            if action["new_implementation_file_id"] not in file_ids:
                diagnostics.append(PlanningDiagnostic("error", "repair_unknown_file", f"repair moves function to unknown file '{action['new_implementation_file_id']}'", path))
            if action["new_declaration_file_id"] and action["new_declaration_file_id"] not in file_ids:
                diagnostics.append(PlanningDiagnostic("error", "repair_unknown_file", f"repair declares function in unknown file '{action['new_declaration_file_id']}'", path))
    return diagnostics


def validate_full_implementation_plan(plan: dict[str, Any], *, profile: dict[str, Any], planning_ir: dict[str, Any], path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = validate_implementation_plan(plan, profile=profile, planning_ir=planning_ir, path=path)
    if plan.get("dependency_graph") is None:
        diagnostics.append(PlanningDiagnostic("error", "missing_final_dependency_graph", "final implementation_plan must include deterministic dependency_graph", path))
    return diagnostics


def stage_passed(diagnostics: list[PlanningDiagnostic]) -> bool:
    return not has_errors(diagnostics)


def _has_cycle(edges: list[tuple[str, str]]) -> bool:
    graph: dict[str, list[str]] = {}
    for source, target in edges:
        if source and target:
            graph.setdefault(source, []).append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in graph.get(node, []):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


validate_core_design = validate_core_design_candidate
validate_module_contracts = validate_module_contracts_candidate
validate_function_inventory = validate_function_inventory_candidate
validate_function_signatures = validate_function_signature_patch
validate_function_behavior_contracts = validate_function_behavior_contract_patch
validate_wire_access_binding = validate_wire_access_binding_patch
validate_calls_allowed = validate_calls_allowed_candidate
validate_file_layout = validate_file_layout_candidate
