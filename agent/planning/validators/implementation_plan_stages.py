from __future__ import annotations

import re
from typing import Any

from ..diagnostics import PlanningDiagnostic, has_errors
from ..schemas.implementation_plan import (
    CALLS_ALLOWED_CANDIDATE_SCHEMA_VERSION,
    CORE_DESIGN_CANDIDATE_SCHEMA_VERSION,
    DEPENDENCY_REPAIR_PATCH_SCHEMA_VERSION,
    FILE_LAYOUT_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_BEHAVIOR_CONTRACT_PATCH_SCHEMA_VERSION,
    FUNCTION_INVENTORY_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_INVENTORY_REPAIR_PATCH_SCHEMA_VERSION,
    FUNCTION_SIGNATURE_PATCH_SCHEMA_VERSION,
    MODULE_ARTIFACTS_CANDIDATE_SCHEMA_VERSION,
    RUNTIME_ENTRYPOINT_CANDIDATE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
    WIRE_ACCESS_BINDING_PATCH_SCHEMA_VERSION,
)
from ..schemas.implementation_plan_candidates import validate_shape
from ..stages.coder_spec_lowering import normalize_type_key
from ..stages.function_inventory_decomposition import select_top_decomposition_hints
from ..stages.implementation_plan import _handler_surfaces, _safe_id, _surface_units, _wire_fields
from ..stages.implementation_plan_context import SYSTEM_TYPE_IDS
from .implementation_plan import validate_implementation_plan


ALLOWED_FUNCTION_KINDS = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}
LIFECYCLE_ROLES = {"runtime_create", "runtime_start", "runtime_run", "runtime_destroy"}
BARE_C_SYMBOL_DENYLIST = {"connect", "read", "write", "close", "send", "publish", "subscribe"}
C_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNCTION_INVENTORY_COVERAGE_REPAIR_THRESHOLD = 0.45
FUNCTION_INVENTORY_COVERAGE_WARNING_THRESHOLD = 0.65

FUNCTION_FAMILY_EXTRA_TERMS = {
    "lifecycle_control": {"init", "create", "start", "run", "stop", "destroy", "shutdown"},
    "event_callback_or_dispatch": {"epoll", "poll", "readable", "writable"},
    "connection_or_endpoint_management": {"socket", "client", "peer", "lookup"},
    "timeout_or_error_cleanup": {"timer", "close", "destroy", "failure"},
    "feed_or_parse_entry": {"decode", "input"},
    "frame_boundary_detection": {"length", "delimiter", "remaining", "partial"},
    "primitive_reader_or_tokenizer": {"field", "header", "option"},
    "validation_or_malformed_input_handling": {"validate", "invalid", "incomplete", "reject"},
    "encode_or_response_entry": {"serialize", "reply"},
    "buffer_size_or_allocation_helper": {"allocate", "growth", "capacity"},
    "handler_lookup_or_switch": {"table", "select"},
    "shared_precondition_check": {"validate", "guard"},
    "lookup_or_get_or_create": {"find", "resolve"},
    "timeout_or_expiry_cleanup": {"timer", "expire"},
    "lookup_or_match": {"find", "search", "resolve"},
    "receive_append_finalize": {"complete"},
    "abort_or_cleanup": {"rollback", "cancel", "free"},
    "callback_registration_or_adapter": {"register", "hook"},
    "centralized_cleanup": {"destroy", "teardown", "release"},
}


def _capability_values(module: dict[str, Any]) -> set[str]:
    return {
        str(cap)
        for key in ("owned_capability_ids", "owned_capabilities")
        for cap in module.get(key, [])
        if str(cap).strip()
    }


def _is_key_flow_module(module: dict[str, Any]) -> bool:
    caps = _capability_values(module)
    return "role_composition" in caps or (
        {"semantic_dispatch", "state_machine"}.issubset(caps)
        and bool(caps & {"connection_termination", "timeout_handling", "protocol_error_policy"})
    )


def _lifecycle_name_matches(action: str, name: str) -> bool:
    if action == "run":
        return name.endswith("_run") or name.endswith("_serve")
    return name.endswith(f"_{action}")


def _is_lifecycle_api(function: dict[str, Any], action: str) -> bool:
    role = str(function.get("public_api_role", ""))
    name = str(function.get("name", ""))
    return (
        role == f"runtime_{action}"
        and str(function.get("function_kind", "")) != "handler"
        and _is_public_function(function)
        and _lifecycle_name_matches(action, name)
    )


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
        "repair_hints": [item.message for item in (errors + warnings)[:8]],
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


def _selected_arch_modules_by_id(selected_architecture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("module_id", "")).strip(): item
        for item in selected_architecture.get("architecture", {}).get("modules", [])
        if isinstance(item, dict) and str(item.get("module_id", "")).strip()
    }


def _module_text(module: dict[str, Any]) -> str:
    return " ".join(
        [
            str(module.get("module_id", "")),
            str(module.get("name", "")),
            str(module.get("role", "")),
            str(module.get("purpose", "")),
            " ".join(str(item) for item in module.get("responsibilities", [])),
            " ".join(str(item) for item in module.get("owned_capabilities", [])),
            " ".join(str(item) for item in module.get("owned_capability_ids", [])),
        ]
    ).lower()


def _has_cycle_edges(edges: list[tuple[str, str]]) -> bool:
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


def _target_role(profile: dict[str, Any]) -> str:
    value = profile.get("target_role", "")
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value).lower()


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


def _module_artifact_ids(module_artifacts: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("module_id", "")) for item in module_artifacts if isinstance(item, dict) and str(item.get("module_id", "")).strip()}


def _function_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("function_id", "")): item for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_id")}


def _module_by_id(module_artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("module_id", "")): item for item in module_artifacts if isinstance(item, dict) and str(item.get("module_id", "")).strip()}


def _is_public_function(function: dict[str, Any]) -> bool:
    return bool(function.get("exported")) or str(function.get("api_surface", "")).lower() == "public" or str(function.get("visibility", "")).lower() == "public"


def _function_text(function: dict[str, Any]) -> str:
    return " ".join(
        [
            str(function.get("name", "")),
            str(function.get("function_kind", "")),
            str(function.get("public_api_role", "")),
            str(function.get("grouping_hint", "")),
            str(function.get("purpose", "")),
            " ".join(str(item) for item in function.get("capability_ids", [])),
        ]
    ).lower()


def _artifact_semantic_text(module: dict[str, Any]) -> str:
    artifacts = module.get("artifacts", [])
    return " ".join(
        [
            str(module.get("role", "")),
            " ".join(str(cap) for cap in module.get("owned_capabilities", [])),
            " ".join(str(cap) for cap in module.get("owned_capability_ids", [])),
            " ".join(str(ref) for ref in module.get("doc_ref", [])),
            " ".join(
                f"{artifact.get('name', '')} {artifact.get('role', '')}"
                for artifact in artifacts
                if isinstance(artifact, dict)
            ),
        ]
    ).lower()


def _has_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _function_family_score(functions: list[dict[str, Any]], family: str) -> tuple[float, list[str]]:
    terms = {item for item in family.split("_") if item and item != "or"} | FUNCTION_FAMILY_EXTRA_TERMS.get(family, set())
    best = 0.0
    matched: list[str] = []
    for function in functions:
        text = _function_text(function)
        hits = {term for term in terms if term in text}
        score = 1.0 if len(hits) >= 2 else 0.5 if hits else 0.0
        if score > 0:
            matched.append(str(function.get("function_id", "")))
        best = max(best, score)
    return best, matched


def _decomposition_classifier_context(module_id: str, module_artifacts: list[dict[str, Any]], core_design: dict[str, Any]) -> dict[str, Any]:
    modules = [item for item in module_artifacts if isinstance(item, dict)]
    module = _module_by_id(modules).get(module_id, {})
    providers = {str(dep) for dep in module.get("dependencies", []) if str(dep).strip()}
    return {
        "provider_module_artifacts": [
            {"module_id": item.get("module_id"), "artifacts": item.get("artifacts", [])}
            for item in modules
            if str(item.get("module_id", "")) in providers
        ],
        "consumer_module_artifact_dependencies": [
            {"module_id": item.get("module_id"), "artifacts": item.get("artifacts", [])}
            for item in modules
            if module_id in {str(dep) for dep in item.get("dependencies", []) if str(dep).strip()}
        ],
        "core_design_summary": core_design,
    }


def function_inventory_decomposition_report(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], core_design: dict[str, Any]) -> dict[str, Any]:
    modules_by_id = _module_by_id(module_artifacts)
    candidate_module_ids = (
        _module_artifact_ids(module_artifacts)
        if candidate.get("module_id") == "all_modules"
        else {str(candidate.get("module_id", ""))}
    )
    module_reports: list[dict[str, Any]] = []
    total_points = 0.0
    total_expected = 0
    for module_id in sorted(candidate_module_ids & set(modules_by_id)):
        module_functions = [
            function
            for function in candidate.get("functions", [])
            if isinstance(function, dict) and str(function.get("module_id", "")) == module_id
        ]
        decomposition = select_top_decomposition_hints(
            modules_by_id[module_id],
            _decomposition_classifier_context(module_id, module_artifacts, core_design),
            max_hints=3,
        )
        rule_reports: list[dict[str, Any]] = []
        module_points = 0.0
        module_expected = 0
        for rule_id in decomposition.get("selected_rule_ids", decomposition.get("detected_rule_ids", [])):
            families = decomposition.get("expected_function_families_by_rule", {}).get(rule_id, [])
            family_reports: list[dict[str, Any]] = []
            rule_points = 0.0
            for family in families:
                score, matched_function_ids = _function_family_score(module_functions, str(family))
                rule_points += score
                family_reports.append(
                    {
                        "family": family,
                        "score": score,
                        "matched_function_ids": matched_function_ids[:5],
                    }
                )
            expected_count = len(families)
            module_points += rule_points
            module_expected += expected_count
            rule_reports.append(
                {
                    "rule_id": rule_id,
                    "expected_families": families,
                    "family_coverage": family_reports,
                    "coverage_score": round(rule_points / expected_count, 3) if expected_count else 1.0,
                    "missing_families": [item["family"] for item in family_reports if item["score"] == 0],
                }
            )
        module_score = round(module_points / module_expected, 3) if module_expected else 1.0
        total_points += module_points
        total_expected += module_expected
        module_reports.append(
            {
                "module_id": module_id,
                "coverage_score": module_score,
                "repair_required": module_score < FUNCTION_INVENTORY_COVERAGE_REPAIR_THRESHOLD,
                "warning": module_score < FUNCTION_INVENTORY_COVERAGE_WARNING_THRESHOLD,
                "selected_rule_ids": decomposition.get("selected_rule_ids", decomposition.get("detected_rule_ids", [])),
                "evidence_summary": decomposition.get("evidence_summary", []),
                "rules": rule_reports,
            }
        )
    score = round(total_points / total_expected, 3) if total_expected else 1.0
    return {
        "schema_version": "function_inventory_decomposition_report/v1",
        "coverage_score": score,
        "repair_required": any(item["repair_required"] for item in module_reports),
        "warning": any(item["warning"] for item in module_reports),
        "repair_threshold": FUNCTION_INVENTORY_COVERAGE_REPAIR_THRESHOLD,
        "warning_threshold": FUNCTION_INVENTORY_COVERAGE_WARNING_THRESHOLD,
        "modules": module_reports,
    }


def _file_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("file_id", "")) for item in draft.get("file_layout", {}).get("files", []) if isinstance(item, dict) and item.get("file_id")}


def validate_plan_skeleton(draft: dict[str, Any], selected_architecture: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if draft.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_plan_skeleton_schema", f"implementation_plan draft must use {SCHEMA_VERSION}", path))
    for key in ("source_artifact_refs", "id_namespace", "validation_targets", "deterministic_indexes"):
        if key not in draft:
            diagnostics.append(PlanningDiagnostic("error", "missing_plan_skeleton_key", f"plan skeleton missing {key}", path))
    if draft.get("module_artifacts") != [] or draft.get("function_contracts") != []:
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


def validate_module_artifacts_candidate(candidate: dict[str, Any], selected_architecture: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], core_design: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, MODULE_ARTIFACTS_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics

    arch_by_id = _selected_arch_modules_by_id(selected_architecture)
    arch_module_ids = set(arch_by_id)
    support_modules = _support_module_ids(selected_architecture)
    modules = [item for item in candidate.get("modules", []) if isinstance(item, dict)]
    if not modules:
        diagnostics.append(PlanningDiagnostic("error", "empty_module_artifacts", "module artifacts candidate must include modules", path))
        return diagnostics

    seen_modules: set[str] = set()
    module_ids: set[str] = set()
    for module in modules:
        module_id = str(module.get("module_id", "")).strip()
        if module_id in seen_modules:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_module_artifact_module", f"module_id '{module_id}' appears more than once", path))
        seen_modules.add(module_id)
        module_ids.add(module_id)
        if module_id not in arch_module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_module_artifacts_module", f"module_id '{module_id}' is not selected", path))
        artifacts = [item for item in module.get("artifacts", []) if isinstance(item, dict)]
        if module_id not in support_modules and not artifacts:
            diagnostics.append(PlanningDiagnostic("error", "module_artifacts_empty_non_support_module", f"module '{module_id}' declares no artifacts", path))
        seen_artifacts: set[str] = set()
        kinds = {str(item.get("kind", "")).upper() for item in artifacts}
        names = [str(item.get("name", "")).strip() for item in artifacts]
        name_text = " ".join(names).lower()
        for artifact in artifacts:
            name = str(artifact.get("name", "")).strip()
            kind = str(artifact.get("kind", "")).upper()
            role = str(artifact.get("role", "")).strip()
            if name in seen_artifacts:
                diagnostics.append(PlanningDiagnostic("error", "duplicate_module_artifact_name", f"module '{module_id}' repeats artifact '{name}'", path))
            seen_artifacts.add(name)
            if kind not in {"TYPE", "FUNC"}:
                diagnostics.append(PlanningDiagnostic("error", "invalid_module_artifact_kind", f"artifact '{name}' in module '{module_id}' uses invalid kind '{kind}'", path))
            if not role:
                diagnostics.append(PlanningDiagnostic("error", "empty_module_artifact_role", f"artifact '{name}' in module '{module_id}' has empty role", path))
            if not C_SYMBOL_RE.match(name):
                diagnostics.append(PlanningDiagnostic("error", "invalid_module_artifact_c_symbol", f"artifact '{name}' in module '{module_id}' is not a C-friendly symbol", path))
            if name in BARE_C_SYMBOL_DENYLIST:
                diagnostics.append(PlanningDiagnostic("error", "forbidden_bare_module_artifact_name", f"artifact '{name}' in module '{module_id}' must use a protocol/module prefix", path))
        text = _module_text(module) + " " + _module_text(arch_by_id.get(module_id, {}))
        if "semantic" not in text and any(word in text for word in ("codec", "framing", "parser", "encoder", "decoder")):
            if "FUNC" not in kinds or "decode" not in name_text or "encod" not in name_text:
                diagnostics.append(PlanningDiagnostic("error", "codec_module_missing_decoder_encoder_artifacts", f"codec module '{module_id}' must include decoder and encoder FUNC artifacts", path))
        if any(word in text for word in ("network", "transport", "tcp")):
            has_network_type = any(word in name_text for word in ("connection", "server", "callback", "_cb", "_fn"))
            has_network_func = any(word in name_text for word in ("read", "send", "flush", "close"))
            if not (has_network_type or has_network_func):
                diagnostics.append(PlanningDiagnostic("error", "network_module_missing_boundary_artifacts", f"network module '{module_id}' must expose connection/server/callback or read/send/close artifacts", path))
        for domain in ("session", "router", "topic", "resource"):
            if domain in text and "TYPE" not in kinds:
                diagnostics.append(PlanningDiagnostic("error", f"{domain}_module_missing_type_artifact", f"{domain} module '{module_id}' must include a TYPE artifact", path))
            if domain in text and "FUNC" not in kinds:
                diagnostics.append(PlanningDiagnostic("error", f"{domain}_module_missing_core_func_artifact", f"{domain} module '{module_id}' must include a core FUNC artifact", path))

    for missing in sorted(arch_module_ids - module_ids):
        diagnostics.append(PlanningDiagnostic("error", "missing_architecture_module_artifacts", f"selected module '{missing}' is missing from module artifacts", path))
    for added in sorted(module_ids - arch_module_ids):
        diagnostics.append(PlanningDiagnostic("error", "added_module_artifacts_module", f"module artifacts added unknown module '{added}'", path))

    dependency_edges: list[tuple[str, str]] = []
    for module in modules:
        module_id = str(module.get("module_id", "")).strip()
        for dep in module.get("dependencies", []):
            dep_id = str(dep).strip()
            if dep_id not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_module_artifact_dependency", f"module '{module_id}' depends on unknown module '{dep_id}'", path))
            if dep_id == module_id:
                diagnostics.append(PlanningDiagnostic("error", "self_module_artifact_dependency", f"module '{module_id}' depends on itself", path))
            dependency_edges.append((dep_id, module_id))
    if _has_cycle_edges(dependency_edges):
        diagnostics.append(PlanningDiagnostic("error", "module_artifacts_dependency_cycle", "module artifact dependencies contain a cycle", path))

    generation_order = [str(item).strip() for item in candidate.get("generation_order", []) if str(item).strip()]
    if set(generation_order) != module_ids or len(generation_order) != len(module_ids):
        diagnostics.append(PlanningDiagnostic("error", "module_artifacts_generation_order_mismatch", "generation_order must contain every module_id exactly once", path))
    order_index = {module_id: index for index, module_id in enumerate(generation_order)}
    for dep_id, module_id in dependency_edges:
        if dep_id in order_index and module_id in order_index and order_index[dep_id] > order_index[module_id]:
            diagnostics.append(PlanningDiagnostic("error", "module_artifacts_generation_order_not_topological", f"generation_order places dependency '{dep_id}' after '{module_id}'", path))

    role_composition_modules = [
        module_id
        for module_id, module in arch_by_id.items()
        if "role_composition" in [str(cap) for cap in module.get("owned_capabilities", [])]
    ]
    if _target_role(profile) == "broker" and role_composition_modules:
        role_modules = [
            module
            for module in modules
            if any(word in _module_text(module) for word in ("broker", "server", "client", "app", "role_composition"))
        ]
        if not role_modules:
            diagnostics.append(PlanningDiagnostic("error", "broker_role_module_missing", "broker target with role_composition must include a broker/server/client/app module", path))
        elif not any(
            artifact.get("kind") == "FUNC"
            and (str(artifact.get("name", "")) == "main" or str(artifact.get("name", "")).endswith(("_create", "_start", "_run", "_serve", "_destroy")))
            for module in role_modules
            for artifact in module.get("artifacts", [])
            if isinstance(artifact, dict)
        ):
            diagnostics.append(PlanningDiagnostic("error", "broker_role_module_missing_lifecycle_artifact", "broker role module must include lifecycle FUNC artifact or main", path))

    return diagnostics


def validate_function_inventory_candidate(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], core_design: dict[str, Any], profile: dict[str, Any], planning_ir: dict[str, Any] | None = None, *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, FUNCTION_INVENTORY_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_artifact_ids(module_artifacts)
    if candidate.get("module_id") not in module_ids and candidate.get("module_id") != "all_modules":
        diagnostics.append(PlanningDiagnostic("error", "unknown_function_inventory_module", f"candidate module_id '{candidate.get('module_id')}' is not a module", path))
    modules_by_id = _module_by_id(module_artifacts)
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
        if bool(function.get("exported")):
            if function.get("api_surface") != "public" or function.get("visibility") != "public":
                diagnostics.append(PlanningDiagnostic("error", "exported_function_not_public", f"function '{function_id}' is exported but not public", path))
            if not str(function.get("public_api_role", "")).strip():
                diagnostics.append(PlanningDiagnostic("error", "exported_function_missing_role", f"function '{function_id}' is exported but has no public_api_role", path))
            if not str(function.get("export_reason", "")).strip():
                diagnostics.append(PlanningDiagnostic("error", "exported_function_missing_reason", f"function '{function_id}' is exported but has no export_reason", path))
        if function.get("visibility") == "static":
            if bool(function.get("exported")):
                diagnostics.append(PlanningDiagnostic("error", "static_function_exported", f"static function '{function_id}' may not be exported", path))
            if function.get("api_surface") == "public":
                diagnostics.append(PlanningDiagnostic("error", "static_function_public_surface", f"static function '{function_id}' may not have public api_surface", path))
        if function.get("api_surface") in {"private_helper", "static_helper"} and function.get("visibility") == "public":
            diagnostics.append(PlanningDiagnostic("error", "private_helper_public_visibility", f"helper function '{function_id}' may not be public", path))
        role = str(function.get("public_api_role", ""))
        if role in LIFECYCLE_ROLES:
            action = role.removeprefix("runtime_")
            if function.get("function_kind") == "handler":
                diagnostics.append(PlanningDiagnostic("error", "lifecycle_role_uses_handler", f"lifecycle role '{role}' may not be assigned to handler function '{function_id}'", path))
            if not _is_public_function(function):
                diagnostics.append(PlanningDiagnostic("error", "lifecycle_function_not_public", f"lifecycle function '{function_id}' must be public/exported", path))
            if not _lifecycle_name_matches(action, str(function.get("name", ""))):
                diagnostics.append(PlanningDiagnostic("error", "lifecycle_function_bad_name", f"lifecycle function '{function_id}' name must end with lifecycle action suffix", path))
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
        if function["coder_function_type"] not in {"ALGORITHM", "EVENT", "ENTRYPOINT"}:
            diagnostics.append(PlanningDiagnostic("error", "invalid_coder_function_type", f"function '{function_id}' has non-coder function type '{function['coder_function_type']}'", path))
    candidate_caps = {cap for function in candidate["functions"] for cap in function["capability_ids"]}
    unresolved = _unresolved_targets(candidate)
    if "message_decode" in candidate_caps and "parser" not in kinds and "message_decode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_parser_function", "message_decode requires a parser entry function or unresolved question", path))
    if "message_encode" in candidate_caps and "serializer" not in kinds and "message_encode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_serializer_function", "message_encode requires a serializer entry function or unresolved question", path))
    if any(cap in candidate_caps for cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}) and core_design.get("handler_matrix") and "handler" not in kinds:
        diagnostics.append(PlanningDiagnostic("error", "missing_handler_function", "handler_matrix requires handler functions", path))
    candidate_module_ids = module_ids if candidate.get("module_id") == "all_modules" else {str(candidate.get("module_id", ""))}
    for module_id in sorted(candidate_module_ids & module_ids):
        expected_artifact_funcs = {
            str(artifact.get("name", "")).strip()
            for artifact in modules_by_id.get(module_id, {}).get("artifacts", [])
            if isinstance(artifact, dict) and str(artifact.get("kind", "")).upper() == "FUNC" and str(artifact.get("name", "")).strip()
        }
        inventory_names = {
            str(function.get("name", "")).strip()
            for function in candidate.get("functions", [])
            if isinstance(function, dict) and str(function.get("module_id", "")) == module_id
        }
        blocking_unresolved = {
            str(item.get("target_id", "")).strip()
            for item in candidate.get("unresolved_questions", [])
            if isinstance(item, dict) and bool(item.get("blocking"))
        }
        for missing_artifact in sorted(expected_artifact_funcs - inventory_names - blocking_unresolved):
            diagnostics.append(PlanningDiagnostic("error", "function_inventory_missing_artifact_function", f"module '{module_id}' FUNC artifact '{missing_artifact}' is missing from function inventory", path))
        module_functions = [
            function
            for function in candidate.get("functions", [])
            if isinstance(function, dict) and str(function.get("module_id", "")) == module_id
        ]
        internal_functions = [function for function in module_functions if not _is_public_function(function)]
        module_text = _artifact_semantic_text(modules_by_id.get(module_id, {}))
        if expected_artifact_funcs and not internal_functions and inventory_names and inventory_names.issubset(expected_artifact_funcs):
            diagnostics.append(
                PlanningDiagnostic(
                    "warning",
                    "under_decomposed_inventory",
                    f"module '{module_id}' function inventory mirrors mandatory FUNC artifacts and lacks internal implementation helpers",
                    path,
                )
            )
        for function in module_functions:
            is_derived_public = _is_public_function(function) and str(function.get("name", "")).strip() not in expected_artifact_funcs
            if is_derived_public and (
                not str(function.get("export_reason", "")).strip()
                or not str(function.get("public_api_role", "")).strip()
            ):
                diagnostics.append(
                    PlanningDiagnostic(
                        "warning",
                        "derived_public_api_without_justification",
                        f"derived public function '{function.get('function_id')}' needs export_reason and public_api_role justification or should be internal",
                        path,
                    )
                )
            purpose = str(function.get("purpose", "")).lower()
            stage_hits = sum(
                1
                for terms in (
                    {"parse", "decode", "read bytes", "frame"},
                    {"validate", "check", "malformed"},
                    {"dispatch", "route", "handler", "classify"},
                    {"state", "session", "transaction", "update"},
                    {"encode", "serialize", "response", "reply"},
                    {"send", "write", "flush"},
                    {"cleanup", "destroy", "free", "close", "rollback", "abort"},
                )
                if _has_any(purpose, terms)
            )
            if stage_hits >= 4:
                diagnostics.append(
                    PlanningDiagnostic(
                        "warning",
                        "coarse_function_should_split",
                        f"function '{function.get('function_id')}' purpose combines too many implementation stages and should be split into clearer function families",
                        path,
                    )
                )
        function_text = " ".join(_function_text(function) for function in module_functions)
        module_or_function_text = f"{module_text} {function_text}"
        owns_parser = _has_any(module_text, {"decode", "decoder", "parse", "parser", "framing", "wire format", "delimiter", "length", "field", "header", "option"})
        owns_serializer = _has_any(module_text, {"encode", "encoder", "serialize", "serializer", "response", "reply", "writer", "ack", "status code"})
        owns_dispatch = _has_any(module_text, {"dispatch", "handler", "state machine", "semantic", "protocol event", "callback", "orchestration"})
        owns_resource = _has_any(module_text, {"resource", "session", "transaction", "connection", "state", "lifecycle", "runtime", "registry", "payload", "buffer", "endpoint"})
        if owns_parser and not any(str(function.get("function_kind", "")) == "parser" for function in module_functions):
            diagnostics.append(PlanningDiagnostic("warning", "missing_function_family", f"module '{module_id}' appears to own parsing/framing but has no parser function family", path))
        if owns_serializer and not any(str(function.get("function_kind", "")) == "serializer" for function in module_functions):
            diagnostics.append(PlanningDiagnostic("warning", "missing_function_family", f"module '{module_id}' appears to own encoding/response generation but has no serializer function family", path))
        if owns_dispatch and not any(str(function.get("function_kind", "")) == "handler" or _has_any(_function_text(function), {"dispatch", "callback", "adapter"}) for function in module_functions):
            diagnostics.append(PlanningDiagnostic("warning", "missing_dispatch_boundary", f"module '{module_id}' appears to own handlers, state-machine, or orchestration but lacks a dispatcher/callback boundary", path))
        if owns_resource and not _has_any(module_or_function_text, {"cleanup", "destroy", "free", "close", "abort", "rollback", "teardown", "release", "failure"}):
            diagnostics.append(PlanningDiagnostic("warning", "missing_cleanup_for_resource_owner", f"module '{module_id}' appears to own resources or state but lacks cleanup/destroy/free/error-path functions", path))
        if owns_parser:
            parser_helpers = [
                function
                for function in module_functions
                if not _is_public_function(function)
                and _has_any(_function_text(function), {"field", "token", "primitive", "incremental", "boundary", "delimiter", "length", "malformed", "incomplete", "partial"})
            ]
            if sum(1 for function in module_functions if str(function.get("function_kind", "")) == "parser") <= 1 and not parser_helpers:
                diagnostics.append(PlanningDiagnostic("warning", "missing_parser_or_serializer_helpers", f"module '{module_id}' appears to own parsing/framing but only has a coarse parser entry without internal helpers", path))
        if owns_serializer:
            serializer_helpers = [
                function
                for function in module_functions
                if not _is_public_function(function)
                and _has_any(_function_text(function), {"field", "token", "primitive", "writer", "buffer", "payload", "status", "reason", "size", "growth"})
            ]
            if sum(1 for function in module_functions if str(function.get("function_kind", "")) == "serializer") <= 1 and not serializer_helpers:
                diagnostics.append(PlanningDiagnostic("warning", "missing_parser_or_serializer_helpers", f"module '{module_id}' appears to own encoding/response generation but only has a coarse serializer entry without internal helpers", path))
    coverage_report = function_inventory_decomposition_report(candidate, module_artifacts, core_design)
    for module_report in coverage_report.get("modules", []):
        if not isinstance(module_report, dict) or not module_report.get("warning"):
            continue
        missing_by_rule = [
            f"{rule.get('rule_id')}: {', '.join(str(item) for item in rule.get('missing_families', [])[:4])}"
            for rule in module_report.get("rules", [])
            if isinstance(rule, dict) and rule.get("missing_families")
        ]
        diagnostics.append(
            PlanningDiagnostic(
                "warning",
                "missing_function_family",
                (
                    f"module '{module_report.get('module_id')}' decomposition coverage score "
                    f"{module_report.get('coverage_score')} is below {FUNCTION_INVENTORY_COVERAGE_WARNING_THRESHOLD}; "
                    f"missing families: {'; '.join(missing_by_rule[:3])}"
                ),
                path,
            )
        )
    return diagnostics


def validate_function_inventory_repair_patch(patch: dict[str, Any], candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, FUNCTION_INVENTORY_REPAIR_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_id = str(patch.get("module_id", ""))
    if module_id not in _module_artifact_ids(module_artifacts):
        diagnostics.append(PlanningDiagnostic("error", "repair_unknown_module", f"repair patch module_id '{module_id}' is not a module", path))
    if module_id != str(candidate.get("module_id", "")):
        diagnostics.append(PlanningDiagnostic("error", "repair_module_mismatch", "repair patch module_id must match the current candidate module_id", path))
    existing_ids = {str(function.get("function_id", "")) for function in candidate.get("functions", []) if isinstance(function, dict)}
    existing_names = {str(function.get("name", "")) for function in candidate.get("functions", []) if isinstance(function, dict)}
    added_ids: set[str] = set()
    added_names: set[str] = set()
    for function in patch.get("added_functions", []):
        function_id = str(function.get("function_id", ""))
        name = str(function.get("name", ""))
        if str(function.get("module_id", "")) != module_id:
            diagnostics.append(PlanningDiagnostic("error", "repair_added_function_wrong_module", f"added function '{function_id}' is not in repaired module", path))
        if function_id in existing_ids or function_id in added_ids:
            diagnostics.append(PlanningDiagnostic("error", "repair_duplicate_added_function_id", f"added function_id '{function_id}' conflicts with existing or added function", path))
        if name in existing_names or name in added_names:
            diagnostics.append(PlanningDiagnostic("error", "repair_duplicate_added_function_name", f"added function name '{name}' conflicts with existing or added function", path))
        added_ids.add(function_id)
        added_names.add(name)
    seen_updates: set[str] = set()
    for update in patch.get("updated_functions", []):
        function_id = str(update.get("function_id", ""))
        if function_id not in existing_ids:
            diagnostics.append(PlanningDiagnostic("error", "repair_unknown_update_function", f"updated function '{function_id}' is not in the current candidate", path))
        if function_id in seen_updates:
            diagnostics.append(PlanningDiagnostic("error", "repair_duplicate_update_function", f"duplicate update for function '{function_id}'", path))
        seen_updates.add(function_id)
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
    module_ids = _module_artifact_ids(draft.get("module_artifacts", []))
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
        is_public = _is_public_function(function)
        if signature["name"] != function.get("name"):
            diagnostics.append(PlanningDiagnostic("error", "signature_name_mismatch", f"signature name for '{function_id}' must match inventory name", path))
        if not signature["raw"].strip() or not signature["return_type"].strip():
            diagnostics.append(PlanningDiagnostic("error", "empty_function_signature", f"function '{function_id}' signature is incomplete", path))
        if is_public and signature.get("storage_class") == "static":
            diagnostics.append(PlanningDiagnostic("error", "public_function_static_signature", f"public function '{function_id}' must not have static storage class", path))
        if is_public and (not signature["name"].strip() or not signature["raw"].strip() or not signature["return_type"].strip()):
            diagnostics.append(PlanningDiagnostic("error", "public_function_incomplete_signature", f"public function '{function_id}' lacks a lowerable C signature", path))
        for param in signature["params"]:
            if is_public and (not str(param.get("name", "")).strip() or not str(param.get("type", "")).strip()):
                diagnostics.append(PlanningDiagnostic("error", "public_function_incomplete_param", f"public function '{function_id}' has an incomplete signature parameter", path))
            if param["ownership"] not in {"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"}:
                diagnostics.append(PlanningDiagnostic("error", "invalid_param_ownership", f"function '{function_id}' has non-coder ownership '{param['ownership']}'", path))
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
        if is_public:
            for declaration in update.get("interface_type_declarations", []):
                if str(declaration.get("visibility", "")).lower() in {"private", "internal"}:
                    diagnostics.append(PlanningDiagnostic("error", "public_signature_uses_private_interface_type", f"public function '{function_id}' exposes private/internal type '{declaration.get('name')}'", path))
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
        if _is_public_function(function):
            contract = update.get("contract", {})
            missing_contract = [key for key in ("input", "action", "output", "thread_safety") if not str(contract.get(key, "")).strip()]
            if missing_contract:
                diagnostics.append(PlanningDiagnostic("error", "public_function_incomplete_contract", f"public function '{function_id}' behavior contract is missing: {', '.join(missing_contract)}", path))
            if update["error_behavior"]["propagation"] == "unknown" or update["error_behavior"]["return_policy"] == "unknown":
                diagnostics.append(PlanningDiagnostic("error", "public_function_unknown_error_channel", f"public function '{function_id}' has unknown error propagation or return policy", path))
        if update["logic_kind"] == "EVENT":
            event_contract = update.get("event_contract", {})
            missing = [
                key
                for key in ("trigger", "precondition", "input", "action", "state_change", "response", "event_type")
                if not str(event_contract.get(key, "")).strip()
            ]
            if missing:
                diagnostics.append(PlanningDiagnostic("error", "incomplete_event_contract", f"function '{function_id}' EVENT contract is missing: {', '.join(missing)}", path))
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
        if not entry["packet_name"].strip() or not entry["wire_field"].strip() or not entry["strategy"].strip():
            diagnostics.append(PlanningDiagnostic("error", "incomplete_coder_wire_mapping", f"wire mapping '{entry['wire_mapping_id']}' lacks coder-lowerable packet/field/strategy", path))
    for entry in patch["access_path_entries"]:
        function = functions.get(entry["function_id"])
        if entry["function_id"] not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_function", f"access path references unknown function '{entry['function_id']}'", path))
            continue
        if entry["field_id"] not in field_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_access_path_field", f"access path references unknown field '{entry['field_id']}'", path))
        if not entry["path"].strip() or not entry["c_type"].strip():
            diagnostics.append(PlanningDiagnostic("error", "incomplete_coder_access_path", f"access path '{entry['access_path_id']}' lacks path or c_type", path))
        if str(entry.get("c_type", "")).strip().lower() == "unknown":
            diagnostics.append(PlanningDiagnostic("error", "unknown_coder_access_path_type", f"access path '{entry['access_path_id']}' must not lower TYPE as unknown", path))
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


def validate_runtime_entrypoint_candidate(candidate: dict[str, Any], draft: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, RUNTIME_ENTRYPOINT_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_artifact_ids(draft.get("module_artifacts", []))
    functions = _function_by_id(draft)
    key_module = str(candidate.get("key_flow_module_id", "")).strip()
    if key_module not in module_ids:
        diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_unknown_key_module", f"runtime entrypoint key_flow_module_id '{key_module}' is not a module", path))
    source_path = str(candidate.get("source_path", "")).strip()
    if not source_path.endswith(".c"):
        diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_source_not_c", "runtime entrypoint source_path must be a .c file", path))
    signature = candidate.get("entrypoint_signature", {})
    if signature.get("name") != "main" or signature.get("return_type") != "int" or "argc" not in signature.get("raw", ""):
        diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_signature_not_main", "runtime entrypoint signature should be int main(int argc, char** argv)", path))
    lifecycle = candidate.get("lifecycle_function_ids", {})
    for action in ("create", "start", "run", "destroy"):
        function_id = str(lifecycle.get(action, "")).strip()
        if not function_id:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_missing_lifecycle_id", f"runtime entrypoint missing {action} lifecycle function id", path))
            continue
        known = functions.get(function_id)
        if known and str(known.get("module_id", "")) != key_module:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_lifecycle_wrong_module", f"lifecycle function '{function_id}' is not in key flow module '{key_module}'", path))
        if known and not _is_lifecycle_api(known, action):
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_lifecycle_not_api", f"lifecycle function '{function_id}' is not a public {action} lifecycle API", path))
        if not known and not function_id.startswith(f"fn:{key_module}:"):
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_lifecycle_id_outside_module", f"new lifecycle function '{function_id}' must belong to key flow module '{key_module}'", path))
    sequence_steps = {str(item.get("step", "")) for item in candidate.get("startup_sequence", []) if isinstance(item, dict)}
    for required in ("parse_args", "create", "start", "run", "destroy"):
        if required not in sequence_steps:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_sequence_missing_step", f"startup_sequence missing '{required}' step", path))
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
    module_ids = _module_ids_from_arch(selected_architecture or {"architecture": {"modules": draft.get("module_artifacts", [])}})
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
            if (callee_fn.get("visibility") in {"private", "static"} or callee_fn.get("api_surface") in {"private_helper", "static_helper"}) and callee_fn.get("module_id") != caller_fn.get("module_id"):
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
    module_ids = _module_artifact_ids(draft.get("module_artifacts", []))
    function_ids = _function_ids(draft)
    functions = _function_by_id(draft)
    type_ids = _type_ids(draft)
    file_ids: set[str] = set()
    paths: set[str] = set()
    exports_by_function: dict[str, list[str]] = {}
    implements_by_function: dict[str, list[str]] = {}
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
        for function_id in file_item["exports_function_ids"]:
            exports_by_function.setdefault(function_id, []).append(file_id)
        for function_id in file_item["implements_function_ids"]:
            implements_by_function.setdefault(function_id, []).append(file_id)
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
    assignment_counts: dict[str, int] = {}
    for item in candidate["function_file_assignments"]:
        assignment_counts[item["function_id"]] = assignment_counts.get(item["function_id"], 0) + 1
    assigned = set(assignment_counts)
    for missing in sorted(function_ids - assigned):
        diagnostics.append(PlanningDiagnostic("error", "unassigned_function_file", f"function '{missing}' is not assigned to a file", path))
    for function_id, count in assignment_counts.items():
        if function_id in function_ids and count != 1:
            diagnostics.append(PlanningDiagnostic("error", "function_assignment_count_mismatch", f"function '{function_id}' must have exactly one file assignment, found {count}", path))
    for function_id in sorted(function_ids):
        definitions = implements_by_function.get(function_id, [])
        if len(definitions) != 1:
            diagnostics.append(PlanningDiagnostic("error", "function_definition_count_mismatch", f"function '{function_id}' must have exactly one source definition, found {len(definitions)}", path))
    for assignment in candidate["function_file_assignments"]:
        function_id = assignment["function_id"]
        function = functions.get(function_id, {})
        is_public = _is_public_function(function) or assignment["visibility"] == "public"
        if function_id not in function_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_function", f"layout assigns unknown function '{function_id}'", path))
        if assignment["implementation_file_id"] not in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_file", f"layout assigns function to unknown implementation file '{assignment['implementation_file_id']}'", path))
        declaration_file_id = assignment["declaration_file_id"]
        if declaration_file_id and declaration_file_id not in file_ids:
            diagnostics.append(PlanningDiagnostic("error", "layout_assigns_unknown_file", f"layout assigns function to unknown declaration file '{declaration_file_id}'", path))
        export_count = len(exports_by_function.get(function_id, []))
        if is_public:
            if export_count != 1:
                diagnostics.append(PlanningDiagnostic("error", "public_function_header_export_count_mismatch", f"public function '{function_id}' must appear in exactly one header export list, found {export_count}", path))
            if not declaration_file_id:
                diagnostics.append(PlanningDiagnostic("error", "public_function_not_declared", f"public function '{function_id}' in module '{function.get('module_id', '')}' has no declaration file", path))
            elif declaration_file_id != assignment["implementation_file_id"]:
                diagnostics.append(PlanningDiagnostic("error", "public_function_declared_outside_file_unit", f"public function '{function_id}' must be declared in its FILE_SPEC unit", path))
        else:
            if export_count:
                diagnostics.append(PlanningDiagnostic("error", "private_function_exported_in_header", f"private/static function '{function_id}' must not appear in header exports", path))
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
    blocking = [
        item
        for item in plan.get("unresolved_questions", [])
        if isinstance(item, dict) and bool(item.get("blocking"))
    ]
    if blocking:
        diagnostics.append(PlanningDiagnostic("error", "blocking_unresolved_questions", "final implementation_plan still contains blocking unresolved questions", path))
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    entrypoints = [
        function
        for function in functions
        if str(function.get("coder_function_type", "")).upper() == "ENTRYPOINT"
        and str((function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}).get("name") or function.get("name", "")) == "main"
    ]
    main_files = [
        item
        for item in files
        if str(item.get("source_path") or item.get("path") or "").replace("\\", "/").endswith("main.c")
    ]
    target_role = str(profile.get("target_role", {}).get("value", profile.get("target_role", "")) if isinstance(profile.get("target_role"), dict) else profile.get("target_role", "")).strip()
    if target_role and not (entrypoints and main_files):
        diagnostics.append(PlanningDiagnostic("error", "missing_runtime_entrypoint", f"deployable target role '{target_role}' requires a runtime entrypoint main.c", path))
    key_module_ids = {str(item.get("module_id", "")) for item in main_files if str(item.get("module_id", "")).strip()}
    if entrypoints:
        key_module_ids.update(str(item.get("module_id", "")) for item in entrypoints if str(item.get("module_id", "")).strip())
    for module_id in sorted(key_module_ids):
        module_functions = [item for item in functions if str(item.get("module_id", "")) == module_id]
        public_names = {
            str(item.get("name", ""))
            for item in module_functions
            if bool(item.get("exported")) or str(item.get("visibility", "")).lower() == "public" or str(item.get("api_surface", "")).lower() == "public"
        }
        missing = []
        for suffix in ("_create", "_start", "_destroy"):
            if not any(name.endswith(suffix) for name in public_names):
                missing.append(suffix.removeprefix("_"))
        if not any(name.endswith("_run") or name.endswith("_serve") for name in public_names):
            missing.append("run")
        if missing:
            diagnostics.append(PlanningDiagnostic("error", "runtime_key_flow_missing_lifecycle_api", f"key flow module '{module_id}' lacks public lifecycle API: {', '.join(sorted(missing))}", path))
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
validate_module_artifacts = validate_module_artifacts_candidate
validate_function_inventory = validate_function_inventory_candidate
validate_function_inventory_repair = validate_function_inventory_repair_patch
validate_function_signatures = validate_function_signature_patch
validate_function_behavior_contracts = validate_function_behavior_contract_patch
validate_wire_access_binding = validate_wire_access_binding_patch
validate_calls_allowed = validate_calls_allowed_candidate
validate_runtime_entrypoint = validate_runtime_entrypoint_candidate
validate_file_layout = validate_file_layout_candidate
