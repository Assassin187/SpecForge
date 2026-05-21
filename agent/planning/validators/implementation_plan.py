from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.implementation_plan import SCHEMA_VERSION


def _required_capabilities(profile: dict[str, Any] | None) -> set[str]:
    if not profile:
        return set()
    return {
        str(item.get("capability_id", "")).strip()
        for item in profile.get("required_capabilities", [])
        if isinstance(item, dict) and str(item.get("capability_id", "")).strip()
    }


def _field_ids(planning_ir: dict[str, Any] | None) -> set[str]:
    if not planning_ir:
        return set()
    fields = planning_ir.get("normalization_index", {}).get("field_id_by_message_and_name", {})
    result: set[str] = set()
    if isinstance(fields, dict):
        for field_map in fields.values():
            if isinstance(field_map, dict):
                result.update(str(item) for item in field_map.values() if str(item).strip())
    return result


def _is_runtime_entrypoint_function(function: dict[str, Any]) -> bool:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return str(function.get("coder_function_type", "")).upper() == "ENTRYPOINT" and str(signature.get("name") or function.get("name", "")) == "main"


def _source_is_main(file_item: dict[str, Any]) -> bool:
    source_path = str(file_item.get("source_path") or file_item.get("path") or "").replace("\\", "/")
    return source_path.endswith("/main.c") or source_path == "main.c"


def validate_implementation_plan(
    plan: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    planning_ir: dict[str, Any] | None = None,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if plan.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_implementation_plan_schema", f"implementation_plan must use {SCHEMA_VERSION}", path))
    modules = plan.get("module_contracts", [])
    files = plan.get("file_layout", {}).get("files", [])
    functions = plan.get("function_contracts", [])
    required_caps = _required_capabilities(profile)
    module_ids = {str(item.get("module_id", "")) for item in modules if isinstance(item, dict)}
    file_ids = {str(item.get("file_id", "")) for item in files if isinstance(item, dict)}
    function_ids = {str(item.get("function_id", "")) for item in functions if isinstance(item, dict)}
    paths: set[str] = set()
    covered_caps: set[str] = set()
    files_by_module: dict[str, list[dict[str, Any]]] = {}
    functions_by_module: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        if not isinstance(module, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_module_contract", "module_contracts item must be object", path))
            continue
        module_id = str(module.get("module_id", "")).strip()
        if not module_id:
            diagnostics.append(PlanningDiagnostic("error", "missing_module_id", "module contract missing module_id", path))
        if not module.get("owned_capabilities") and not module.get("support_module"):
            diagnostics.append(PlanningDiagnostic("error", "module_without_capability", f"Module '{module_id}' owns no capabilities", path))
        for cap in module.get("owned_capabilities", []):
            cap_id = str(cap)
            covered_caps.add(cap_id)
            if required_caps and cap_id not in required_caps:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_capability", f"Module '{module_id}' owns unknown capability '{cap_id}'", path))
    for cap_id in sorted(required_caps - covered_caps):
        diagnostics.append(PlanningDiagnostic("error", "uncovered_required_capability", f"Required capability '{cap_id}' is not owned by any module", path))
    for file_item in files:
        if not isinstance(file_item, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_file_layout", "file_layout item must be object", path))
            continue
        module_id = str(file_item.get("module_id", "")).strip()
        files_by_module.setdefault(module_id, []).append(file_item)
        if module_id not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_file_module", f"File belongs to unknown module '{module_id}'", path))
        source_only_entrypoint = str(file_item.get("kind", "")) == "source_only_entrypoint" or _source_is_main(file_item)
        for key in ("source_path", "header_path"):
            value = str(file_item.get(key, "")).strip()
            if key == "header_path" and source_only_entrypoint and not value:
                continue
            if not value:
                diagnostics.append(PlanningDiagnostic("error", "missing_file_path", f"File '{file_item.get('file_id')}' missing {key}", path))
                continue
            if value in paths:
                diagnostics.append(PlanningDiagnostic("error", "duplicate_file_path", f"Duplicate file path '{value}'", path))
            paths.add(value)
        imports_allowed = file_item.get("imports_allowed", [])
        if not isinstance(imports_allowed, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_imports_allowed", f"File '{file_item.get('file_id')}' imports_allowed must be an array", path))
        else:
            for target_file in imports_allowed:
                if str(target_file) not in file_ids:
                    diagnostics.append(PlanningDiagnostic("error", "unknown_imports_allowed_file", f"File '{file_item.get('file_id')}' imports unknown file '{target_file}'", path))
    for function in functions:
        if not isinstance(function, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_function_contract", "function_contracts item must be object", path))
            continue
        functions_by_module.setdefault(str(function.get("module_id", "")), []).append(function)
        file_id = str(function.get("file_id", "")).strip()
        if file_id not in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_function_file", f"Function belongs to unknown file '{file_id}'", path))
        if str(function.get("visibility", "")).lower() == "public" and not str(function.get("declared_in", "")).strip() and not _is_runtime_entrypoint_function(function):
            diagnostics.append(PlanningDiagnostic("error", "public_function_not_declared", f"Public function '{function.get('name')}' has no declared_in", path))
        signature = function.get("signature", {})
        if not isinstance(signature, dict) or not signature.get("raw"):
            diagnostics.append(PlanningDiagnostic("error", "missing_function_signature", f"Function '{function.get('name')}' has no signature", path))
        for cap in function.get("capability_ids", []):
            cap_id = str(cap)
            if required_caps and cap_id not in required_caps:
                diagnostics.append(PlanningDiagnostic("error", "unknown_function_capability", f"Function '{function.get('name')}' references unknown capability '{cap_id}'", path))
        calls_allowed = function.get("calls_allowed", [])
        if not isinstance(calls_allowed, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_calls_allowed", f"Function '{function.get('name')}' calls_allowed must be an array", path))
        else:
            for target_function in calls_allowed:
                if str(target_function) not in function_ids:
                    diagnostics.append(PlanningDiagnostic("error", "unknown_calls_allowed_function", f"Function '{function.get('name')}' calls unknown function '{target_function}'", path))
    handler_functions = [item for item in functions if isinstance(item, dict) and item.get("function_kind") == "handler"]
    for item in plan.get("handler_matrix", []):
        if not isinstance(item, dict):
            continue
        surface = str(item.get("surface", "")).strip().lower()
        if surface and not any(surface in str(function.get("name", "")).lower() or surface in str(function.get("purpose", "")).lower() for function in handler_functions):
            diagnostics.append(PlanningDiagnostic("error", "uncovered_handler_surface", f"No handler function covers surface '{surface}'", path))

    known_field_ids = _field_ids(planning_ir)
    mapping_field_ids = {
        str(item.get("field_id", ""))
        for item in plan.get("wire_mapping_table", [])
        if isinstance(item, dict) and str(item.get("field_id", "")).strip()
    }
    if known_field_ids:
        for field_id in sorted(known_field_ids - mapping_field_ids):
            diagnostics.append(PlanningDiagnostic("error", "uncovered_wire_field", f"Wire field '{field_id}' is not present in wire_mapping_table", path))
    parser_or_serializer_mapped = {
        str(mapping.get("field_id", ""))
        for function in functions
        if isinstance(function, dict) and function.get("function_kind") in {"parser", "serializer"}
        for mapping in function.get("wire_mapping", [])
        if isinstance(mapping, dict) and str(mapping.get("field_id", "")).strip()
    }
    for field_id in sorted(mapping_field_ids - parser_or_serializer_mapped):
        diagnostics.append(PlanningDiagnostic("error", "wire_field_without_codec_function", f"Wire field '{field_id}' is not covered by parser/serializer function mapping", path))

    access_path_ids = {
        str(item.get("access_path_id", ""))
        for item in plan.get("access_path_table", [])
        if isinstance(item, dict) and str(item.get("access_path_id", "")).strip()
    }
    for item in plan.get("wire_mapping_table", []):
        if isinstance(item, dict) and str(item.get("access_path_id", "")) not in access_path_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_wire_access_path", f"Wire mapping '{item.get('mapping_id')}' references unknown access path", path))
    for item in plan.get("access_path_table", []):
        if isinstance(item, dict) and str(item.get("c_type", "")).strip().lower() == "unknown":
            diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_type", f"Access path '{item.get('access_path_id')}' has TYPE unknown", path))
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", ""))
        if "role_composition" in {str(cap) for cap in module.get("owned_capabilities", [])} and not files_by_module.get(module_id) and not functions_by_module.get(module_id):
            diagnostics.append(PlanningDiagnostic("error", "empty_role_composition_module", f"role_composition module '{module_id}' has no files or functions", path))
    return diagnostics
