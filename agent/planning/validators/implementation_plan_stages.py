from __future__ import annotations

import re
from typing import Any

from ..diagnostics import PlanningDiagnostic, has_errors
from ..schemas.implementation_plan import (
    CALLS_ALLOWED_CANDIDATE_SCHEMA_VERSION,
    CORE_DESIGN_CANDIDATE_SCHEMA_VERSION,
    DEPENDENCY_REPAIR_PATCH_SCHEMA_VERSION,
    FILE_LAYOUT_CANDIDATE_SCHEMA_VERSION,
    FILE_LAYOUT_OVERRIDE_PATCH_SCHEMA_VERSION,
    FUNCTION_ANNOTATION_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_BEHAVIOR_CONTRACT_PATCH_SCHEMA_VERSION,
    FUNCTION_INVENTORY_CANDIDATE_SCHEMA_VERSION,
    FUNCTION_SIGNATURE_PATCH_SCHEMA_VERSION,
    MODULE_ARTIFACTS_CANDIDATE_SCHEMA_VERSION,
    RUNTIME_ENTRYPOINT_CANDIDATE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TYPE_FILLING_CANDIDATE_SCHEMA_VERSION,
    TYPE_INVENTORY_CANDIDATE_SCHEMA_VERSION,
    VALIDATION_REPORT_SCHEMA_VERSION,
    WIRE_ACCESS_BINDING_PATCH_SCHEMA_VERSION,
)
from ..schemas.implementation_plan_candidates import validate_shape
from ..stages.coder_spec_lowering import canonical_function_symbol, is_anonymous_c_function_pointer_type, lower_canonical_type_to_header_data, normalize_type_key
from ..stages.function_inventory_decomposition import DECOMPOSITION_RULES, select_top_decomposition_hints
from ..stages.implementation_plan import _handler_surfaces, _safe_id, _surface_units, _wire_fields
from ..stages.implementation_plan_context import (
    SYSTEM_TYPE_IDS,
    derive_type_generation_targets,
    derive_type_obligations,
    normalize_system_type_ref,
    provider_public_type_seeds_for_module,
)
from .implementation_plan import validate_implementation_plan


ALLOWED_FUNCTION_KINDS = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}
LIFECYCLE_ROLES = {"runtime_create", "runtime_start", "runtime_run", "runtime_destroy"}
BARE_C_SYMBOL_DENYLIST = {"connect", "read", "write", "close", "send", "publish", "subscribe"}
C_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNCTION_INVENTORY_COVERAGE_REPAIR_THRESHOLD = 0.45
FUNCTION_INVENTORY_COVERAGE_WARNING_THRESHOLD = 0.65
NON_FUNCTION_LIFECYCLE_NAMES = {"caller", "external", "application", "app", "user", "callee", "owner", "runtime", "system"}
ABSTRACT_FUNCTION_FAMILY_NAMES = {
    family
    for rule in DECOMPOSITION_RULES
    for family in rule.expected_function_families
}

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


def _forbidden_public_symbol_names(container: dict[str, Any]) -> set[str]:
    return {
        str(item.get("name") or item.get("NAME") or "").strip()
        for item in container.get("forbidden_symbols", []) if isinstance(item, dict)
        if str(item.get("name") or item.get("NAME") or "").strip()
    } | {
        str(item).strip()
        for item in container.get("forbidden_symbols", []) if not isinstance(item, dict)
        if str(item).strip()
    }


def _public_symbol_policy_diagnostic(name: Any, subject: str, code: str, path: str | None, forbidden_names: set[str] | None = None) -> PlanningDiagnostic | None:
    symbol = str(name or "").strip()
    if not symbol:
        return PlanningDiagnostic("error", code, f"{subject} has empty public C symbol", path)
    if not C_SYMBOL_RE.match(symbol):
        return PlanningDiagnostic("error", code, f"{subject} public C symbol '{symbol}' is not C-friendly", path)
    if symbol in BARE_C_SYMBOL_DENYLIST or (forbidden_names is not None and symbol in forbidden_names):
        return PlanningDiagnostic("error", code, f"{subject} public C symbol '{symbol}' must use a protocol/module-prefixed ABI name", path)
    return None


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


def validation_report(stage: str, diagnostics: list[PlanningDiagnostic], *, quality_diagnostics: list[dict[str, Any]] | None = None, richness_summary: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "quality_diagnostics": quality_diagnostics or [],
        "richness_summary": richness_summary or {},
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
    return {
        str(item.get("type_id", ""))
        for key in ("canonical_types", "type_inventory")
        for item in draft.get(key, [])
        if isinstance(item, dict) and item.get("type_id")
    }


def _canonical_types_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("type_id", "")): item
        for item in draft.get("canonical_types", [])
        if isinstance(item, dict) and str(item.get("type_id", "")).strip()
    }


def _type_inventory_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("type_id", "")): item
        for item in draft.get("type_inventory", [])
        if isinstance(item, dict) and str(item.get("type_id", "")).strip()
    }


def _layout_export_type_diagnostic(type_id: str, draft: dict[str, Any], file_id: str, path: str | None) -> PlanningDiagnostic | None:
    forbidden_names = _forbidden_public_symbol_names(draft)
    canonical = _canonical_types_by_id(draft)
    if type_id in canonical:
        declaration = lower_canonical_type_to_header_data(canonical[type_id])
        if declaration is None:
            return PlanningDiagnostic("error", "layout_exports_unlowerable_type", f"file '{file_id}' exports unlowerable public type '{type_id}'", path)
        policy_diag = _public_symbol_policy_diagnostic(
            declaration.get("NAME", ""),
            f"file '{file_id}' exported type '{type_id}'",
            "layout_exports_forbidden_public_type_name",
            path,
            forbidden_names,
        )
        if policy_diag is not None:
            return policy_diag
        return None
    inventory = _type_inventory_by_id(draft)
    if type_id in inventory:
        item = inventory[type_id]
        public_header = str(item.get("visibility", "")).lower() == "public" and str(item.get("defined_in", "")).lower() == "public_header"
        if public_header:
            return PlanningDiagnostic("error", "layout_exports_noncanonical_type", f"file '{file_id}' exports non-canonical type '{type_id}'", path)
        return PlanningDiagnostic("error", "layout_exports_nonpublic_type", f"file '{file_id}' exports non-public/internal type '{type_id}'", path)
    return PlanningDiagnostic("error", "layout_exports_unknown_type", f"file '{file_id}' exports unknown type '{type_id}'", path)


def _projected_public_symbol_diagnostics(plan: dict[str, Any], path: str | None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    forbidden_names = _forbidden_public_symbol_names(plan)

    for module in plan.get("module_artifacts", []) if isinstance(plan.get("module_artifacts"), list) else []:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", ""))
        for artifact in module.get("artifacts", []) if isinstance(module.get("artifacts"), list) else []:
            if not isinstance(artifact, dict):
                continue
            diag = _public_symbol_policy_diagnostic(
                artifact.get("name", ""),
                f"module '{module_id}' artifact",
                "readiness_forbidden_public_symbol",
                path,
                forbidden_names,
            )
            if diag is not None:
                diagnostics.append(diag)

    for type_item in plan.get("type_inventory", []) if isinstance(plan.get("type_inventory"), list) else []:
        if isinstance(type_item, dict) and _is_public_type(type_item):
            diag = _public_symbol_policy_diagnostic(
                type_item.get("name", ""),
                f"public header type '{type_item.get('type_id')}'",
                "readiness_forbidden_public_symbol",
                path,
                forbidden_names,
            )
            if diag is not None:
                diagnostics.append(diag)

    for function in plan.get("function_contracts", []) if isinstance(plan.get("function_contracts"), list) else []:
        if not isinstance(function, dict):
            continue
        if _is_public_function(function):
            signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
            diag = _public_symbol_policy_diagnostic(
                signature.get("name") or function.get("name", ""),
                f"public function '{function.get('function_id')}'",
                "readiness_forbidden_public_symbol",
                path,
                forbidden_names,
            )
            if diag is not None:
                diagnostics.append(diag)
        for declaration in function.get("interface_type_declarations", []) if isinstance(function.get("interface_type_declarations"), list) else []:
            if isinstance(declaration, dict) and str(declaration.get("visibility", "")).lower() == "public":
                diag = _public_symbol_policy_diagnostic(
                    declaration.get("name", ""),
                    f"public interface type declaration in '{function.get('function_id')}'",
                    "readiness_forbidden_public_symbol",
                    path,
                    forbidden_names,
                )
                if diag is not None:
                    diagnostics.append(diag)

    return diagnostics


def _handler_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("handler_id", "")) for item in draft.get("handler_matrix", []) if isinstance(item, dict) and item.get("handler_id")}


def _function_ids(draft: dict[str, Any]) -> set[str]:
    return {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_id")}


def _field_ids(planning_ir: dict[str, Any]) -> set[str]:
    return {str(item.get("field_id", "")) for item in _wire_fields(planning_ir) if str(item.get("field_id", "")).strip()}


def _message_ids(planning_ir: dict[str, Any]) -> set[str]:
    result = {f"message:{_safe_id(str(item.get('message', '')))}" for item in _wire_fields(planning_ir) if str(item.get("message", "")).strip()}
    facts = planning_ir.get("protocol_facts", {}) if isinstance(planning_ir, dict) else {}
    message_model = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    for key in ("surface_catalog", "message_or_command_entries"):
        entries = message_model.get(key, []) if isinstance(message_model.get(key), list) else []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("name", "")).strip():
                result.add(f"message:{_safe_id(str(entry.get('name', '')))}")
    return result


def _target_surface_ids(planning_ir: dict[str, Any], profile: dict[str, Any]) -> set[str]:
    target_role = str(planning_ir.get("target_directives", {}).get("directives", {}).get("target_role", ""))
    return {str(item.get("name", "")) for item in _handler_surfaces(_surface_units(planning_ir, profile), target_role) if str(item.get("name", "")).strip()}


def _unresolved_targets(candidate: dict[str, Any]) -> set[str]:
    return {
        str(item.get("target_id", ""))
        for item in candidate.get("unresolved_questions", [])
        if isinstance(item, dict) and str(item.get("target_id", "")).strip()
    }


def _blocking_unresolved_targets(candidate: dict[str, Any]) -> set[str]:
    return {
        str(item.get("target_id", ""))
        for item in candidate.get("unresolved_questions", [])
        if isinstance(item, dict) and bool(item.get("blocking")) and str(item.get("target_id", "")).strip()
    }


def _module_artifact_ids(module_artifacts: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("module_id", "")) for item in module_artifacts if isinstance(item, dict) and str(item.get("module_id", "")).strip()}


def _function_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("function_id", "")): item for item in draft.get("function_contracts", []) if isinstance(item, dict) and item.get("function_id")}


def _module_by_id(module_artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("module_id", "")): item for item in module_artifacts if isinstance(item, dict) and str(item.get("module_id", "")).strip()}


def _is_public_function(function: dict[str, Any]) -> bool:
    return bool(function.get("exported")) or str(function.get("api_surface", "")).lower() == "public" or str(function.get("visibility", "")).lower() == "public"


def _signature_params(function: dict[str, Any]) -> list[dict[str, Any]]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return [
        param
        for param in signature.get("params", [])
        if isinstance(param, dict) and str(param.get("type", "")).strip() and str(param.get("type", "")).strip() != "void"
    ]


def _binding_param_names_match(bindings: Any, callee_params: list[dict[str, Any]]) -> bool:
    if not isinstance(bindings, list) or len(bindings) != len(callee_params):
        return False
    for binding, param in zip(bindings, callee_params):
        if not isinstance(binding, dict):
            return False
        name = str(binding.get("param_name", "")).strip()
        expected = str(param.get("name", "")).strip()
        if name and expected and name != expected:
            return False
    return True


def _is_c_string_literal(value: str) -> bool:
    return bool(re.fullmatch(r'"(?:[^"\\]|\\.)*"', value))


def _is_c_char_literal(value: str) -> bool:
    return bool(re.fullmatch(r"'(?:[^'\\]|\\.)'", value))


def _is_casted_c_literal(value: str) -> bool:
    literal = r'(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)\')'
    cast_type = r"(?:const\s+)?(?:uint8_t|char|void)\s*\*"
    return bool(re.fullmatch(rf"\(\s*{cast_type}\s*\)\s*{literal}", value))


def _allowed_call_value_ref(value_ref: str, caller_param_names: set[str], access_path_values: set[str], local_symbols: set[str], function_symbols: set[str] | None = None) -> bool:
    if not value_ref:
        return True
    known_function_symbols = function_symbols or set()
    if value_ref in caller_param_names or value_ref in access_path_values or value_ref in local_symbols or value_ref in known_function_symbols:
        return True
    if value_ref.startswith(("&", "*")):
        return _allowed_call_value_ref(value_ref[1:].strip(), caller_param_names, access_path_values, local_symbols, known_function_symbols)
    if _is_c_string_literal(value_ref) or _is_c_char_literal(value_ref) or _is_casted_c_literal(value_ref):
        return True
    if value_ref.startswith(("sizeof", "NULL", "true", "false")) or re.fullmatch(r"-?\d+(?:u|U|l|L)*", value_ref):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value_ref):
        return True
    c_expr = r"[A-Za-z_][A-Za-z0-9_]*(?:(?:->|\.)[A-Za-z_][A-Za-z0-9_]*|\[[A-Za-z0-9_+\-*/ ()]+\])*"
    if re.fullmatch(c_expr, value_ref):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\([A-Za-z_][A-Za-z0-9_]*(?:(?:->|\.)[A-Za-z_][A-Za-z0-9_]*)*\)", value_ref):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_>.<+\-*/|&() \[\]]+", value_ref) and re.search(r"(->|\.|\+|-|\*|/|<<|>>|\||&|\(|\[)", value_ref))


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


def _looks_like_abstract_family_function_name(name: str) -> bool:
    safe_name = _safe_id(name)
    return any(safe_name == family or safe_name.endswith(f"_{family}") for family in ABSTRACT_FUNCTION_FAMILY_NAMES)


def _type_inventory_by_id(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in draft.get("type_inventory", []):
        if not isinstance(item, dict):
            continue
        type_id = str(item.get("type_id", "")).strip()
        if type_id:
            result[type_id] = item
    return result


def _type_inventory_name_index(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in types:
        if not isinstance(item, dict):
            continue
        for value in (item.get("name"), str(item.get("name", "")).removeprefix("struct ")):
            key = normalize_type_key(value)
            if not key:
                continue
            if key not in result or (_is_public_type(item) and not _is_public_type(result[key])):
                result[key] = item
    return result


def _is_system_type_ref(value: Any) -> bool:
    return normalize_system_type_ref(value) in SYSTEM_TYPE_IDS


def _is_opaque_backing_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    kinds = {str(left.get("kind", "")), str(right.get("kind", ""))}
    if "opaque_handle" not in kinds or not kinds & {"internal_state", "struct"}:
        return False
    backing = right if str(left.get("kind", "")) == "opaque_handle" else left
    return str(backing.get("visibility", "")) in {"private", "module_internal"} and str(backing.get("defined_in", "")) != "public_header"


def _strip_c_type(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\b(const|volatile|struct|enum)\b", " ", text)
    text = text.replace("*", " ")
    text = re.sub(r"\s+", " ", text).strip()
    parts = text.split()
    if len(parts) > 1 and C_SYMBOL_RE.match(parts[-1]):
        return " ".join(parts[:-1])
    return text


def _signature_raw_has_name(raw: str, name: str) -> bool:
    return bool(name) and re.search(rf"\b{re.escape(name)}\s*\(", raw) is not None


def _module_owns_public_lifecycle_name(module_id: str, name: str) -> bool:
    if not any(name.endswith(suffix) for suffix in ("_create", "_start", "_run", "_serve", "_destroy")):
        return True
    normalized_module = _safe_id(module_id)
    normalized_name = _safe_id(name)
    if normalized_name.startswith(f"{normalized_module}_"):
        return True
    if normalized_module == "broker_app" and normalized_name.startswith("broker_app_"):
        return True
    return normalized_module in normalized_name


def _field_has_variant_boundary(field: dict[str, Any]) -> bool:
    return (
        str(field.get("field_type", "")).lower() == "union"
        or "variant" in str(field.get("validation_notes", "")).lower()
        or bool(field.get("variants"))
    )


def _field_ownership_requires_release(field: dict[str, Any]) -> bool:
    field_type = re.sub(r"\s+", "", str(field.get("field_type", "")).lower())
    type_ref = normalize_system_type_ref(field.get("type_ref", ""))
    if type_ref == "void" or field_type in {"void*", "constvoid*"}:
        return False
    release_capable = (
        "*" in field_type
        or "[" in field_type
        or "buffer" in field_type
        or "string" in field_type
        or field_type == "union"
        or bool(field.get("variants"))
    )
    if not release_capable:
        return False
    ownership = re.sub(r"[^A-Z0-9]+", "_", str(field.get("ownership", "")).upper()).strip("_")
    if not ownership or ownership in {"UNKNOWN", "BORROWED", "SHARED", "OWNED_BY_CALLER", "CALLER_OWNED"}:
        return False
    return ownership in {"OWNED", "TRANSFER"} or ownership.startswith("OWNED_BY_") or "TAKES_OWNERSHIP" in ownership


def _type_text_requires_release(type_item: dict[str, Any]) -> bool:
    text = f"{type_item.get('purpose', '')} {type_item.get('ownership_lifetime', '')}".lower()
    if any(marker in text for marker in ("caller_owned", "owned_by_caller", "caller retains", "borrowed", "not owned", "owned scalars", "owned scalar", "owned value", "owned values")):
        return False
    return any(marker in text for marker in (" owned", "owns ", "takes ownership", "must free", "cleanup", "release"))


def _type_requires_release_path(type_item: dict[str, Any]) -> bool:
    kind = str(type_item.get("kind", ""))
    if kind == "owned_buffer":
        return True
    if kind == "result_struct" and _type_text_requires_release(type_item):
        return True
    fields = [field for field in type_item.get("fields", []) if isinstance(field, dict)]
    if any(_field_ownership_requires_release(field) for field in fields):
        return True
    if any(_field_has_variant_boundary(field) for field in fields) and _type_text_requires_release(type_item):
        return True
    if kind == "internal_state":
        return _type_text_requires_release(type_item) and any(_field_has_variant_boundary(field) or _field_ownership_requires_release(field) for field in fields)
    return False


def _has_release_path(type_item: dict[str, Any]) -> bool:
    lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
    return bool(lifecycle.get("freed_by") or lifecycle.get("destroyed_by"))


def _protocol_alias_matches(actual_key: str, expected_key: str) -> bool:
    if actual_key == expected_key:
        return True
    if expected_key.startswith("protocol_"):
        suffix = expected_key.removeprefix("protocol_")
        return actual_key == suffix or actual_key.endswith(f"_{suffix}")
    if actual_key.startswith("protocol_"):
        suffix = actual_key.removeprefix("protocol_")
        return expected_key == suffix or expected_key.endswith(f"_{suffix}")
    return False


def _field_type_matches_expected(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    return _protocol_alias_matches(normalize_type_key(actual), normalize_type_key(expected))


def _packet_container_score(item: dict[str, Any], suggested_key: str) -> int:
    if item.get("kind") not in {"struct", "view_struct", "result_struct"}:
        return 0
    fields = [field for field in item.get("fields", []) if isinstance(field, dict)]
    if not fields:
        return 0
    name = str(item.get("name", ""))
    text = f"{name} {item.get('purpose', '')}".lower()
    has_variant = any(_field_has_variant_boundary(field) for field in fields)
    has_container_semantics = any(marker in text for marker in ("container", "decoded", "unified"))
    if not has_variant and (not has_container_semantics or len(fields) <= 1):
        return 0
    score = 0
    if normalize_type_key(name) == suggested_key:
        score += 16
    if has_variant:
        score += 8
    if has_container_semantics:
        score += 4
    if "packet" in name.lower():
        score += 1
    return score


def _find_packet_container(types: list[dict[str, Any]], suggested_name: str) -> dict[str, Any] | None:
    suggested_key = normalize_type_key(suggested_name)
    ranked = [(_packet_container_score(item, suggested_key), item) for item in types]
    ranked = [(score, item) for score, item in ranked if score > 0]
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def _find_payload_struct(types: list[dict[str, Any]], suggested_name: str, target: dict[str, Any]) -> dict[str, Any] | None:
    message = ""
    source_messages = target.get("source_message_ids", [])
    if source_messages:
        message = str(source_messages[0]).removeprefix("message:").replace("_", "")
    suggested_key = normalize_type_key(suggested_name)
    message_matches: list[dict[str, Any]] = []
    for item in types:
        if str(item.get("kind", "")) not in {"struct", "view_struct", "result_struct"}:
            continue
        name_key = normalize_type_key(item.get("name", ""))
        if _protocol_alias_matches(name_key, suggested_key):
            return item
        if message and message in name_key.replace("_", "") and "payload" in name_key:
            message_matches.append(item)
    return message_matches[0] if message_matches else None


def _payload_fields_cover_target(type_item: dict[str, Any], target: dict[str, Any]) -> bool:
    fields = {normalize_type_key(field.get("field_name", "")): str(field.get("field_type", "")) for field in type_item.get("fields", []) if isinstance(field, dict)}
    for required in target.get("required_fields", []):
        if not isinstance(required, dict):
            continue
        name = normalize_type_key(required.get("field_name", ""))
        expected = str(required.get("field_type", ""))
        actual = fields.get(name)
        if not actual:
            return False
        if expected != "enum_value" and not _field_type_matches_expected(actual, expected):
            return False
    return True


def _packet_enum_covers_target(types: list[dict[str, Any]], suggested_name: str, target: dict[str, Any]) -> bool:
    required = {normalize_type_key(field.get("field_name", "")) for field in target.get("required_fields", []) if isinstance(field, dict)}
    if not required:
        return True
    for item in types:
        if str(item.get("kind", "")) != "enum":
            continue
        if normalize_type_key(item.get("name", "")) != normalize_type_key(suggested_name) and "packet" not in str(item.get("name", "")).lower():
            continue
        values = {normalize_type_key(value.get("name", "")).replace("mqtt_pkt_", "").replace("mqtt_packet_type_", "") for value in item.get("enum_values", []) if isinstance(value, dict)}
        values |= {normalize_type_key(value.get("role", "")) for value in item.get("enum_values", []) if isinstance(value, dict)}
        if required.issubset(values):
            return True
    return False


def _callback_boundary_covers_target(types: list[dict[str, Any]], suggested_name: str, target: dict[str, Any]) -> bool:
    required = {normalize_type_key(field.get("field_name", "")) for field in target.get("required_fields", []) if isinstance(field, dict)}
    if not required:
        return any(item.get("kind") in {"callback_type", "event_struct"} for item in types)
    for item in types:
        if normalize_type_key(item.get("name", "")) != normalize_type_key(suggested_name) and "callback" not in str(item.get("name", "")).lower():
            continue
        field_names = {normalize_type_key(field.get("field_name", "")) for field in item.get("fields", []) if isinstance(field, dict)}
        if required.issubset(field_names):
            return True
    return False


def _has_owned_result_buffer(types: list[dict[str, Any]], suggested_name: str) -> bool:
    suggested_key = normalize_type_key(suggested_name)
    for item in types:
        if normalize_type_key(item.get("name", "")) == suggested_key:
            return True
        if item.get("kind") in {"owned_buffer", "result_struct"}:
            return True
        field_names = {str(field.get("field_name", "")) for field in item.get("fields", []) if isinstance(field, dict)}
        if {"data", "len"}.issubset(field_names) or {"data", "length"}.issubset(field_names):
            return True
    return False


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


def _module_classification_text(module: dict[str, Any], arch_module: dict[str, Any]) -> str:
    return " ".join(
        [
            str(module.get("module_id", "")),
            str(module.get("name", "")),
            str(module.get("role", "")),
            str(module.get("purpose", "")),
            str(arch_module.get("module_id", "")),
            str(arch_module.get("name", "")),
            str(arch_module.get("role", "")),
            str(arch_module.get("purpose", "")),
            " ".join(_capability_values(module)),
            " ".join(_capability_values(arch_module)),
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
        policy_diag = _public_symbol_policy_diagnostic(
            item.get("name", ""),
            f"canonical type '{item.get('type_id')}'",
            "forbidden_bare_canonical_type_name",
            path,
        )
        if policy_diag is not None:
            diagnostics.append(policy_diag)
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
        classification_text = _module_classification_text(module, arch_by_id.get(module_id, {}))
        if "semantic" not in classification_text and any(word in classification_text for word in ("codec", "framing", "parser", "encoder", "decoder", "message_decode", "message_encode")):
            if "FUNC" not in kinds or "decode" not in name_text or "encod" not in name_text:
                diagnostics.append(PlanningDiagnostic("error", "codec_module_missing_decoder_encoder_artifacts", f"codec module '{module_id}' must include decoder and encoder FUNC artifacts", path))
        if any(word in classification_text for word in ("network", "transport", "tcp")):
            has_network_type = any(word in name_text for word in ("connection", "server", "callback", "_cb", "_fn"))
            has_network_func = any(word in name_text for word in ("read", "send", "flush", "close"))
            if not (has_network_type or has_network_func):
                diagnostics.append(PlanningDiagnostic("error", "network_module_missing_boundary_artifacts", f"network module '{module_id}' must expose connection/server/callback or read/send/close artifacts", path))
        for domain in ("session", "router", "topic", "resource"):
            if domain in classification_text and "TYPE" not in kinds:
                diagnostics.append(PlanningDiagnostic("error", f"{domain}_module_missing_type_artifact", f"{domain} module '{module_id}' must include a TYPE artifact", path))
            if domain in classification_text and "FUNC" not in kinds:
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


def _type_candidate_modules(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]]) -> set[str]:
    return _module_artifact_ids(module_artifacts) if candidate.get("module_id") == "all_modules" else {str(candidate.get("module_id", ""))}


def _type_text(module: dict[str, Any], core_design: dict[str, Any]) -> str:
    module_id = str(module.get("module_id", ""))
    local_handlers = [item for item in core_design.get("handler_matrix", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id]
    local_resources = [item for item in core_design.get("resource_lifecycle", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id]
    return " ".join(
        [
            _artifact_semantic_text(module),
            " ".join(str(item.get("trigger", "")) + " " + str(item.get("responsibility", "")) for item in local_handlers),
            " ".join(str(item.get("name", "")) for item in local_resources),
        ]
    ).lower()


def _is_public_type(type_item: dict[str, Any]) -> bool:
    return str(type_item.get("visibility", "")) == "public" and str(type_item.get("defined_in", "")) == "public_header"


def _is_public_opaque_type(type_item: dict[str, Any] | None, *, canonical: bool = False) -> bool:
    if not isinstance(type_item, dict):
        return False
    kind = str(type_item.get("kind", "")).lower()
    if canonical:
        return kind == "opaque"
    return kind == "opaque_handle" and _is_public_type(type_item)


def _type_is_by_value(raw_type: Any) -> bool:
    text = str(raw_type or "").strip()
    return bool(text) and "*" not in text and not is_anonymous_c_function_pointer_type(text)


def _type_item_by_ref_or_name(
    *,
    type_ref: Any,
    raw_type: Any,
    type_inventory: dict[str, dict[str, Any]],
    type_inventory_by_name: dict[str, dict[str, Any]],
    canonical_types: dict[str, dict[str, Any]],
    canonical_types_by_name: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, bool]:
    ref = str(type_ref or "").strip()
    if ref in type_inventory:
        return type_inventory[ref], False
    if ref in canonical_types:
        return canonical_types[ref], True
    key = normalize_type_key(_strip_c_type(raw_type))
    if key in type_inventory_by_name:
        return type_inventory_by_name[key], False
    if key in canonical_types_by_name:
        return canonical_types_by_name[key], True
    return None, False


def _canonical_type_name_index(canonical_types: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in canonical_types.values():
        for value in (item.get("name"), item.get("c_symbol"), item.get("c_type_name")):
            key = normalize_type_key(value)
            if key:
                result.setdefault(key, item)
    return result


def _add_type_reference(index_by_id: dict[str, dict[str, Any]], index_by_name: dict[str, dict[str, Any]], type_item: dict[str, Any]) -> None:
    type_id = str(type_item.get("type_id", "")).strip()
    values = [type_id, str(type_item.get("name", "")).strip(), str(type_item.get("name", "")).strip().removeprefix("struct ")]
    values.extend(str(alias).strip() for alias in type_item.get("type_id_aliases", []) if str(alias).strip())
    if type_id.startswith("type:") and ":" in type_id:
        tail = type_id.rsplit(":", 1)[-1]
        values.extend([tail, f"type:{tail}"])
    for value in values:
        if not value:
            continue
        index_by_id.setdefault(value, type_item)
        key = normalize_type_key(value)
        if key:
            index_by_name.setdefault(key, type_item)


def _canonical_provider_public_type_ref(type_item: dict[str, Any]) -> dict[str, Any]:
    result = dict(type_item)
    owner = str(result.get("module_id") or result.get("owner_module_id") or "").strip()
    if owner:
        result["module_id"] = owner
    if owner and "visibility" not in result and "defined_in" not in result:
        result["visibility"] = "public"
        result["defined_in"] = "public_header"
    if not str(result.get("name", "")).strip():
        result["name"] = str(result.get("c_symbol") or result.get("c_type_name") or result.get("type_id") or "").strip()
    return result


def _provider_public_type_reference_indexes(core_design: dict[str, Any], module_artifacts: list[dict[str, Any]], module_id: str, planning_ir: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    modules_by_id = _module_by_id(module_artifacts)
    module = modules_by_id.get(module_id, {})
    provider_ids = {str(dep).strip() for dep in module.get("dependencies", []) if str(dep).strip()}
    draft = dict(core_design or {})
    draft.setdefault("module_artifacts", module_artifacts)
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for type_item in draft.get("type_inventory", []):
        if isinstance(type_item, dict) and str(type_item.get("module_id", "")) in provider_ids and _is_public_type(type_item):
            _add_type_reference(by_id, by_name, type_item)
    for group in provider_public_type_seeds_for_module(draft, module, planning_ir):
        for type_item in group.get("types", []):
            if isinstance(type_item, dict) and _is_public_type(type_item):
                _add_type_reference(by_id, by_name, type_item)
    for type_item in draft.get("canonical_types", []):
        if not isinstance(type_item, dict):
            continue
        owner = str(type_item.get("module_id") or type_item.get("owner_module_id") or "").strip()
        ref_item = _canonical_provider_public_type_ref(type_item)
        if owner in provider_ids and _is_public_type(ref_item):
            _add_type_reference(by_id, by_name, ref_item)
    return by_id, by_name


def _resolve_type_reference(
    type_ref: Any,
    field_type: Any,
    local_by_id: dict[str, dict[str, Any]],
    local_by_name: dict[str, dict[str, Any]],
    provider_by_id: dict[str, dict[str, Any]],
    provider_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for raw in (type_ref, _strip_c_type(field_type)):
        normalized = normalize_system_type_ref(raw)
        if not str(normalized).strip():
            continue
        target = local_by_id.get(str(normalized)) or provider_by_id.get(str(normalized))
        if target is not None:
            return target
        keys = [normalize_type_key(normalized)]
        if str(normalized).startswith("type:") and ":" in str(normalized):
            keys.append(normalize_type_key(str(normalized).rsplit(":", 1)[-1]))
        for key in keys:
            target = local_by_name.get(key) or provider_by_name.get(key)
            if target is not None:
                return target
    return None


def validate_type_inventory_candidate(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], core_design: dict[str, Any], profile: dict[str, Any] | None = None, planning_ir: dict[str, Any] | None = None, *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, TYPE_INVENTORY_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_artifact_ids(module_artifacts)
    if candidate.get("module_id") not in module_ids and candidate.get("module_id") != "all_modules":
        diagnostics.append(PlanningDiagnostic("error", "unknown_type_inventory_module", f"candidate module_id '{candidate.get('module_id')}' is not a module", path))
    modules_by_id = _module_by_id(module_artifacts)
    candidate_module_ids = _type_candidate_modules(candidate, module_artifacts)
    types = [item for item in candidate.get("types", []) if isinstance(item, dict)]
    by_name = _type_inventory_name_index(types)
    provider_indexes = {
        module_id: _provider_public_type_reference_indexes(core_design, module_artifacts, module_id, planning_ir)
        for module_id in candidate_module_ids
        if module_id in module_ids
    }
    seen_names: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    seen_type_ids: set[str] = set()
    for type_item in types:
        type_id = str(type_item.get("type_id", ""))
        if type_id:
            by_id.setdefault(type_id, type_item)
    for type_item in types:
        type_id = str(type_item.get("type_id", ""))
        name = str(type_item.get("name", ""))
        module_id = str(type_item.get("module_id", ""))
        if type_id in seen_type_ids:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_type_inventory_type_id", f"duplicate type_id '{type_id}'", path))
        if type_id:
            seen_type_ids.add(type_id)
        name_key = (module_id, normalize_type_key(name))
        existing_same_name = seen_names.get(name_key, [])
        if len(existing_same_name) >= 2 or (existing_same_name and not any(_is_opaque_backing_pair(type_item, existing) for existing in existing_same_name)):
            diagnostics.append(PlanningDiagnostic("error", "duplicate_type_inventory_name", f"duplicate type name '{name}' in module '{module_id}'", path))
        seen_names.setdefault(name_key, []).append(type_item)
        if module_id not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_type_inventory_owner", f"type '{type_id}' belongs to unknown module", path))
        if candidate.get("module_id") != "all_modules" and module_id != candidate.get("module_id"):
            diagnostics.append(PlanningDiagnostic("error", "type_inventory_wrong_module", f"type '{type_id}' is outside current module", path))
        if _is_public_type(type_item):
            policy_diag = _public_symbol_policy_diagnostic(
                name,
                f"public header type '{type_id}'",
                "forbidden_bare_public_type_name",
                path,
            )
            if policy_diag is not None:
                diagnostics.append(policy_diag)
        if _is_public_type(type_item) and type_item.get("kind") == "internal_state":
            diagnostics.append(PlanningDiagnostic("error", "public_header_exposes_internal_state", f"public header exposes internal state type '{name}'", path))
        provider_by_id, provider_by_name = provider_indexes.get(module_id, ({}, {}))
        for dependency in type_item.get("dependencies", []):
            dependency_ref = normalize_system_type_ref(dependency)
            if _is_system_type_ref(dependency_ref):
                continue
            dep = _resolve_type_reference(dependency_ref, "", by_id, by_name, provider_by_id, provider_by_name)
            if dep is None:
                diagnostics.append(PlanningDiagnostic("error", "unknown_type_ref", f"type '{type_id}' depends on unknown type '{dependency}'", path))
                continue
            if dep.get("module_id") != module_id and not _is_public_type(dep):
                diagnostics.append(PlanningDiagnostic("error", "cross_module_private_type_dependency", f"type '{type_id}' depends on private type '{dependency}' from another module", path))
            if _is_public_type(type_item) and not _is_public_type(dep):
                diagnostics.append(PlanningDiagnostic("error", "public_type_field_uses_private_type", f"public type '{type_id}' depends on private/internal type '{dependency}'", path))
        for field in type_item.get("fields", []):
            if not isinstance(field, dict):
                continue
            field_type = str(field.get("field_type", ""))
            type_ref = normalize_system_type_ref(field.get("type_ref", ""))
            target = _resolve_type_reference(type_ref, field_type, by_id, by_name, provider_by_id, provider_by_name)
            if type_ref and not _is_system_type_ref(type_ref) and target is None:
                diagnostics.append(PlanningDiagnostic("error", "unknown_type_ref", f"field '{type_item['type_id']}.{field.get('field_name')}' references unknown type '{type_ref}'", path))
            if _is_public_type(type_item) and target is not None and not _is_public_type(target):
                diagnostics.append(PlanningDiagnostic("error", "public_type_field_uses_private_type", f"public type '{type_id}' field '{field.get('field_name')}' references private/internal type", path))
            lower = field_type.lower()
            is_pointer_like = "*" in field_type or "char*" in lower or "buffer" in lower or "string" in lower or "uint8_t*" in lower
            if is_pointer_like and (not str(field.get("ownership", "")).strip() or field.get("ownership") == "UNKNOWN" or not str(field.get("lifetime", "")).strip()):
                diagnostics.append(PlanningDiagnostic("error", "pointer_field_missing_ownership", f"field '{type_item['type_id']}.{field.get('field_name')}' lacks ownership/lifetime", path))
        if type_item.get("kind") == "owned_buffer":
            field_names = {str(field.get("field_name", "")) for field in type_item.get("fields", []) if isinstance(field, dict)}
            has_size_fields = any(str(field.get("length_field", "")).strip() for field in type_item.get("fields", []) if isinstance(field, dict)) or bool(field_names & {"len", "length", "size", "capacity", "cap"})
            if not has_size_fields:
                diagnostics.append(PlanningDiagnostic("error", "buffer_type_missing_size_fields", f"owned buffer type '{type_id}' lacks length/capacity fields", path))
        if _type_requires_release_path(type_item) and not _has_release_path(type_item):
            diagnostics.append(PlanningDiagnostic("error", "owned_type_missing_release_path", f"owned type '{type_id}' has no free/destroy path", path))
        for cb_param in type_item.get("callback_signature", {}).get("params", []):
            type_ref = normalize_system_type_ref(cb_param.get("type_ref", ""))
            target = _resolve_type_reference(type_ref, "", by_id, by_name, provider_by_id, provider_by_name)
            if type_ref and not _is_system_type_ref(type_ref) and target is None:
                diagnostics.append(PlanningDiagnostic("error", "unknown_type_ref", f"callback type '{type_id}' references unknown type '{type_ref}'", path))
            if _is_public_type(type_item) and target is not None and not _is_public_type(target):
                diagnostics.append(PlanningDiagnostic("error", "public_callback_param_uses_private_type", f"public callback type '{type_id}' references private/internal type '{type_ref}'", path))
    unresolved = _unresolved_targets(candidate)
    for module_id in sorted(candidate_module_ids & module_ids):
        module = modules_by_id.get(module_id, {})
        module_types = [item for item in types if str(item.get("module_id", "")) == module_id]
        names = {normalize_type_key(item.get("name", "")) for item in module_types}
        for artifact in module.get("artifacts", []):
            if isinstance(artifact, dict) and str(artifact.get("kind", "")).upper() == "TYPE":
                name = str(artifact.get("name", "")).strip()
                if name and normalize_type_key(name) not in names and name not in unresolved:
                    diagnostics.append(PlanningDiagnostic("error", "type_inventory_missing_artifact_type", f"module '{module_id}' TYPE artifact '{name}' is missing from type inventory", path))
        owns_state = bool(module.get("state_owned")) or bool(module.get("owned_capabilities")) or any(str(item.get("owner_module_id", "")) == module_id for item in core_design.get("resource_lifecycle", []))
        if owns_state and not any(item.get("kind") in {"opaque_handle", "internal_state"} for item in module_types):
            diagnostics.append(PlanningDiagnostic("error", "state_owner_missing_handle_or_state_type", f"module '{module_id}' owns state/resources but lacks opaque handle or internal state type", path))
        text = _type_text(module, core_design)
        if "config" in text and not any(item.get("kind") == "config_struct" for item in module_types):
            diagnostics.append(PlanningDiagnostic("warning", "config_module_missing_config_struct", f"module '{module_id}' appears to need configuration but lacks config_struct", path))
        if any(word in text for word in ("callback", "event", "timer", "epoll")) and not any(item.get("kind") in {"callback_type", "event_struct"} for item in module_types):
            diagnostics.append(PlanningDiagnostic("warning", "event_callback_type_missing", f"module '{module_id}' appears to expose callbacks/events but lacks callback/event type", path))
        missing_trace = [str(item.get("type_id", "")) for item in module_types if not [ref for ref in item.get("trace_ref_keys", []) if str(ref).strip()]]
        if missing_trace:
            diagnostics.append(PlanningDiagnostic("warning", "type_inventory_missing_trace_refs", f"module '{module_id}' has {len(missing_trace)} type inventory entries without trace_ref_keys", path))
        for target in derive_type_generation_targets(core_design, module, planning_ir):
            target_kind = str(target.get("target_kind", ""))
            suggested_name = str(target.get("suggested_name", ""))
            target_id = str(target.get("target_id", ""))
            if target_kind == "packet_enum" and not (
                _packet_enum_covers_target(module_types, suggested_name, target)
            ):
                diagnostics.append(PlanningDiagnostic("error", "missing_packet_enum_type", f"module '{module_id}' lacks protocol packet enum target '{target_id}'", path))
            elif target_kind == "payload_struct":
                payload_type = _find_payload_struct(module_types, suggested_name, target)
                if payload_type is None:
                    diagnostics.append(PlanningDiagnostic("error", "missing_payload_struct_type", f"module '{module_id}' lacks payload struct target '{target_id}'", path))
                elif not _payload_fields_cover_target(payload_type, target):
                    diagnostics.append(PlanningDiagnostic("error", "payload_struct_field_mismatch", f"module '{module_id}' payload struct target '{target_id}' lacks required concrete fields", path))
            elif target_kind == "packet_container_struct":
                packet_type = _find_packet_container(module_types, suggested_name)
                if packet_type is None:
                    diagnostics.append(PlanningDiagnostic("error", "missing_packet_container_type", f"module '{module_id}' lacks packet/container struct target '{target_id}'", path))
                else:
                    if any(isinstance(field, dict) and field.get("variants") for field in target.get("required_fields", [])) and not _has_release_path(packet_type):
                        diagnostics.append(PlanningDiagnostic("error", "packet_container_missing_release_path", f"module '{module_id}' packet/container target '{target_id}' lacks cleanup/free lifecycle", path))
            elif target_kind == "owned_buffer" and not _has_owned_result_buffer(module_types, suggested_name):
                diagnostics.append(PlanningDiagnostic("error", "missing_result_buffer_type", f"module '{module_id}' lacks owned result/buffer target '{target_id}'", path))
            elif target_kind == "callback_or_event_boundary" and not _callback_boundary_covers_target(module_types, suggested_name, target):
                diagnostics.append(PlanningDiagnostic("error", "missing_callback_boundary_type", f"module '{module_id}' lacks callback/event boundary target '{target_id}'", path))
        concept_counts: dict[str, int] = {}
        packet_stems: set[str] = set()
        payload_stems: set[str] = set()
        for item in module_types:
            key = str(item.get("kind", ""))
            if key in {"config_struct", "result_struct", "internal_state"}:
                concept_counts[key] = concept_counts.get(key, 0) + 1
            name_key = normalize_type_key(str(item.get("name", "")).removeprefix("struct ").removesuffix("_t"))
            concept_stem = name_key.replace("packet", "").replace("payload", "").strip("_")
            if "packet" in name_key:
                packet_stems.add(concept_stem)
            if "payload" in name_key:
                payload_stems.add(concept_stem)
        for key, count in concept_counts.items():
            if count > 1:
                diagnostics.append(PlanningDiagnostic("warning", "duplicate_concept_type", f"module '{module_id}' has {count} {key} types", path))
        overlap = packet_stems & payload_stems
        if overlap:
            diagnostics.append(PlanningDiagnostic("warning", "duplicate_packet_payload_concept", f"module '{module_id}' has overlapping packet/payload concepts: {', '.join(sorted(overlap)[:4])}", path))
    return diagnostics


def validate_type_filling_candidate(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, TYPE_FILLING_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_artifact_ids(module_artifacts)
    module_id = str(candidate.get("module_id", ""))
    if module_id not in module_ids:
        diagnostics.append(PlanningDiagnostic("error", "unknown_type_filling_module", f"candidate module_id '{module_id}' is not a module", path))
    seen_slots: set[str] = set()
    for filling in candidate.get("slot_fillings", []):
        slot_id = str(filling.get("slot_id", ""))
        if slot_id in seen_slots:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_type_slot_filling", f"duplicate slot filling '{slot_id}'", path))
        seen_slots.add(slot_id)
        for dependency in filling.get("dependencies", []):
            if str(dependency).startswith(("state:", "message:", "field:", "handler:", "func:", "file:", "module:")):
                diagnostics.append(PlanningDiagnostic("error", "invalid_type_filling_ref_namespace", f"slot '{slot_id}' uses non-type dependency '{dependency}'", path))
    seen_proposals: set[str] = set()
    for proposal in candidate.get("optional_type_proposals", []):
        key = str(proposal.get("proposal_key", ""))
        if key in seen_proposals:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_optional_type_proposal", f"duplicate optional type proposal '{key}'", path))
        seen_proposals.add(key)
        if not str(proposal.get("expansion_reason", "")).strip():
            diagnostics.append(PlanningDiagnostic("error", "optional_type_missing_expansion_reason", f"optional type proposal '{key}' lacks expansion_reason", path))
        if not [ref for ref in proposal.get("source_refs", []) if str(ref).strip()]:
            diagnostics.append(PlanningDiagnostic("error", "optional_type_missing_source_refs", f"optional type proposal '{key}' lacks source_refs", path))
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
        if not _is_public_function(function) and _looks_like_abstract_family_function_name(str(name)):
            diagnostics.append(
                PlanningDiagnostic(
                    "warning",
                    "abstract_function_family_name",
                    f"internal function '{function_id}' uses an abstract decomposition family name; prefer a concrete implementation helper name",
                    path,
                )
            )
        if _safe_id(str(name)) in NON_FUNCTION_LIFECYCLE_NAMES:
            diagnostics.append(
                PlanningDiagnostic(
                    "warning",
                    "non_function_lifecycle_name_in_inventory",
                    f"function inventory contains lifecycle actor name '{name}' as a function; use a concrete module-owned function name",
                    path,
                )
            )
    candidate_caps = {cap for function in candidate["functions"] for cap in function["capability_ids"]}
    unresolved = _unresolved_targets(candidate)
    if "message_decode" in candidate_caps and "parser" not in kinds and "message_decode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_parser_function", "message_decode requires a parser entry function or unresolved question", path))
    if "message_encode" in candidate_caps and "serializer" not in kinds and "message_encode" not in unresolved:
        diagnostics.append(PlanningDiagnostic("error", "missing_serializer_function", "message_encode requires a serializer entry function or unresolved question", path))
    candidate_module_ids = module_ids if candidate.get("module_id") == "all_modules" else {str(candidate.get("module_id", ""))}
    all_function_names = {
        str(function.get("name", "")).strip()
        for function in candidate.get("functions", [])
        if isinstance(function, dict) and str(function.get("name", "")).strip()
    } | {
        str(function.get("name", "")).strip()
        for function in core_design.get("function_contracts", [])
        if isinstance(function, dict) and str(function.get("name", "")).strip()
    }
    all_function_ids = {
        str(function.get("function_id", "")).strip()
        for function in candidate.get("functions", [])
        if isinstance(function, dict) and str(function.get("function_id", "")).strip()
    } | {
        str(function.get("function_id", "")).strip()
        for function in core_design.get("function_contracts", [])
        if isinstance(function, dict) and str(function.get("function_id", "")).strip()
    }
    for assumption in candidate.get("assumptions", []):
        if not isinstance(assumption, dict):
            continue
        target_id = str(assumption.get("target_id", "")).strip()
        if target_id.startswith("fn:") and target_id not in all_function_ids:
            diagnostics.append(
                PlanningDiagnostic(
                    "warning",
                    "assumption_function_reference_unresolved",
                    f"assumption '{assumption.get('assumption_id', '')}' references unknown function '{target_id}'",
                    path,
                )
            )
    blocking_unresolved = _blocking_unresolved_targets(candidate)
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
        missing_trace = [str(function.get("function_id", "")) for function in module_functions if not [ref for ref in function.get("trace_ref_keys", []) if str(ref).strip()]]
        if missing_trace:
            diagnostics.append(PlanningDiagnostic("warning", "function_inventory_missing_trace_refs", f"module '{module_id}' has {len(missing_trace)} function inventory entries without trace_ref_keys", path))
        owned_handler_ids = {
            str(handler.get("handler_id", ""))
            for handler in core_design.get("handler_matrix", [])
            if isinstance(handler, dict) and str(handler.get("owner_module_id", "")) == module_id and str(handler.get("handler_id", "")).strip()
        }
        covered_handler_ids = {
            str(handler_id)
            for function in module_functions
            if isinstance(function, dict) and str(function.get("function_kind", "")) == "handler"
            for handler_id in function.get("covers_handler_ids", [])
            if str(handler_id).strip()
        }
        if owned_handler_ids and not owned_handler_ids.issubset(covered_handler_ids):
            diagnostics.append(PlanningDiagnostic("error", "missing_handler_function", "handler owner module must cover its handler_matrix entries", path))
        module = modules_by_id.get(module_id, {})
        for obligation in derive_type_obligations(core_design, module):
            required_names = [str(name) for name in obligation.get("required_function_names", []) if str(name).strip()]
            if not any(name in inventory_names or name in all_function_names for name in required_names):
                unresolved_ids = {str(obligation.get("obligation_id", "")), str(obligation.get("type_id", "")), str(obligation.get("type_name", ""))}
                if not (blocking_unresolved & unresolved_ids):
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "type_obligation_uncovered",
                            f"module '{module_id}' does not cover type obligation '{obligation.get('obligation_id')}' with one of: {', '.join(required_names)}",
                            path,
                        )
                    )
        if candidate.get("module_id") == "all_modules" or core_design.get("function_contracts"):
            for type_item in core_design.get("type_inventory", []):
                if not isinstance(type_item, dict) or str(type_item.get("module_id", "")) != module_id:
                    continue
                refs = set(str(name) for name in type_item.get("related_functions", []) if str(name).strip())
                lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
                for names in lifecycle.values():
                    refs.update(str(name) for name in names if str(name).strip())
                for ref in sorted(refs - all_function_names):
                    if ref not in blocking_unresolved:
                        diagnostics.append(PlanningDiagnostic("error", "type_function_reference_unresolved", f"type '{type_item.get('type_id')}' references unknown function '{ref}'", path))
        internal_functions = [function for function in module_functions if not _is_public_function(function)]
        module_text = _artifact_semantic_text(module)
        provider_ids = {str(dep) for dep in module.get("dependencies", []) if str(dep).strip()}
        provider_text = " ".join(_artifact_semantic_text(modules_by_id.get(dep, {})) for dep in provider_ids).lower()
        has_codec_provider = any(term in provider_text or any(term in dep.lower() for dep in provider_ids) for term in ("codec", "parser", "encoder", "decoder", "message_decode", "message_encode", "wire format", "packet"))
        is_transport_only = _has_any(module_text, {"network", "transport", "socket", "tcp", "udp", "epoll", "connection", "read", "write", "send", "flush", "close"}) and not _has_any(module_text, {"message_decode", "message_encode"})
        boundary_codec_helpers = [
            function.get("function_id", "")
            for function in module_functions
            if str(function.get("function_kind", "")) in {"parser", "serializer"}
            and _has_any(_function_text(function), {"decode message", "encode message", "wire field", "packet", "protocol bytes"})
        ]
        if is_transport_only and has_codec_provider and boundary_codec_helpers:
            diagnostics.append(PlanningDiagnostic("warning", "wire_format_boundary_in_transport_module", f"transport module '{module_id}' appears to include codec helpers despite a provider codec boundary", path))
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
        owns_serializer = _has_any(module_text, {"encode", "encoder", "serialize", "serializer", "response", "reply", "writer", "status code"})
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


def validate_function_annotation_candidate(candidate: dict[str, Any], module_artifacts: list[dict[str, Any]], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(candidate, FUNCTION_ANNOTATION_CANDIDATE_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    module_ids = _module_artifact_ids(module_artifacts)
    module_id = str(candidate.get("module_id", ""))
    if module_id not in module_ids:
        diagnostics.append(PlanningDiagnostic("error", "unknown_function_annotation_module", f"candidate module_id '{module_id}' is not a module", path))
    seen_seeds: set[str] = set()
    for annotation in candidate.get("seed_annotations", []):
        seed_id = str(annotation.get("seed_id", ""))
        if seed_id in seen_seeds:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_function_seed_annotation", f"duplicate seed annotation '{seed_id}'", path))
        seen_seeds.add(seed_id)
    seen_proposals: set[str] = set()
    for proposal in candidate.get("optional_function_proposals", []):
        key = str(proposal.get("proposal_key", ""))
        if key in seen_proposals:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_optional_function_proposal", f"duplicate optional function proposal '{key}'", path))
        seen_proposals.add(key)
        if not str(proposal.get("expansion_reason", "")).strip():
            diagnostics.append(PlanningDiagnostic("error", "optional_function_missing_expansion_reason", f"optional function proposal '{key}' lacks expansion_reason", path))
        if not [ref for ref in proposal.get("source_refs", []) if str(ref).strip()]:
            diagnostics.append(PlanningDiagnostic("error", "optional_function_missing_source_refs", f"optional function proposal '{key}' lacks source_refs", path))
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
    type_inventory = _type_inventory_by_id(draft)
    type_inventory_by_name = _type_inventory_name_index(list(type_inventory.values()))
    canonical_types = _canonical_types_by_id(draft)
    canonical_types_by_name = _canonical_type_name_index(canonical_types)
    module_ids = _module_artifact_ids(draft.get("module_artifacts", []))
    target_ids = _batch_function_ids(patch, "function_signature_updates")
    if expected_function_ids is not None and target_ids != expected_function_ids:
        diagnostics.append(PlanningDiagnostic("error", "signature_batch_coverage_mismatch", "signature patch must update exactly the current batch functions", path))
    seen: set[str] = set()
    public_signature_names: dict[str, list[str]] = {}
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
        raw = str(signature.get("raw", "")).strip()
        storage_class = str(signature.get("storage_class", ""))
        if raw.endswith(";"):
            diagnostics.append(PlanningDiagnostic("warning", "signature_raw_trailing_semicolon", f"function '{function_id}' raw signature should omit the trailing semicolon for coder specs style", path))
        if storage_class == "static" and raw and not raw.startswith("static "):
            diagnostics.append(PlanningDiagnostic("error", "static_signature_raw_missing_static", f"static function '{function_id}' raw signature must include static storage class", path))
        if storage_class != "static" and raw.startswith("static "):
            diagnostics.append(PlanningDiagnostic("error", "nonstatic_signature_raw_has_static", f"non-static function '{function_id}' raw signature must not include static storage class", path))
        if raw and not _signature_raw_has_name(raw, str(signature.get("name", ""))):
            diagnostics.append(PlanningDiagnostic("warning", "signature_raw_name_mismatch", f"function '{function_id}' raw signature does not spell the signature name", path))
        if is_public and signature.get("storage_class") == "static":
            diagnostics.append(PlanningDiagnostic("error", "public_function_static_signature", f"public function '{function_id}' must not have static storage class", path))
        if is_public and (not signature["name"].strip() or not signature["raw"].strip() or not signature["return_type"].strip()):
            diagnostics.append(PlanningDiagnostic("error", "public_function_incomplete_signature", f"public function '{function_id}' lacks a lowerable C signature", path))
        if is_public:
            public_signature_names.setdefault(str(signature.get("name", "")), []).append(function_id)
            module_id = str(function.get("module_id", ""))
            name = str(signature.get("name", ""))
            if not _module_owns_public_lifecycle_name(module_id, name):
                diagnostics.append(PlanningDiagnostic("warning", "public_lifecycle_name_crosses_module_boundary", f"public lifecycle function '{name}' does not appear owned by module '{module_id}'", path))
            return_type_item, return_is_canonical = _type_item_by_ref_or_name(
                type_ref="",
                raw_type=signature.get("return_type", ""),
                type_inventory=type_inventory,
                type_inventory_by_name=type_inventory_by_name,
                canonical_types=canonical_types,
                canonical_types_by_name=canonical_types_by_name,
            )
            if _type_is_by_value(signature.get("return_type", "")) and _is_public_opaque_type(return_type_item, canonical=return_is_canonical):
                diagnostics.append(PlanningDiagnostic("error", "public_signature_returns_opaque_by_value", f"public function '{function_id}' returns opaque public type '{signature.get('return_type')}' by value", path))
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
            if is_public:
                raw_param_type = str(param.get("type", ""))
                if not type_ref and is_anonymous_c_function_pointer_type(raw_param_type):
                    diagnostics.append(PlanningDiagnostic("error", "public_signature_uses_anonymous_callback_pointer", f"public function '{function_id}' exposes an anonymous callback pointer parameter '{param.get('name')}'", path))
                inv_type = type_inventory.get(type_ref) if type_ref else None
                if inv_type is None and raw_param_type.strip().startswith("struct "):
                    inv_type = type_inventory_by_name.get(normalize_type_key(_strip_c_type(raw_param_type)))
                if inv_type is not None and not _is_public_type(inv_type):
                    diagnostics.append(PlanningDiagnostic("error", "public_signature_uses_private_type", f"public function '{function_id}' exposes private/internal type '{inv_type.get('name')}'", path))
                param_type_item, param_is_canonical = _type_item_by_ref_or_name(
                    type_ref=type_ref,
                    raw_type=raw_param_type,
                    type_inventory=type_inventory,
                    type_inventory_by_name=type_inventory_by_name,
                    canonical_types=canonical_types,
                    canonical_types_by_name=canonical_types_by_name,
                )
                if _type_is_by_value(raw_param_type) and _is_public_opaque_type(param_type_item, canonical=param_is_canonical):
                    diagnostics.append(PlanningDiagnostic("error", "public_signature_uses_opaque_by_value", f"public function '{function_id}' exposes opaque public type '{raw_param_type}' by value", path))
        for dep in update["signature_dependencies"]:
            type_ref = str(dep.get("type_ref", ""))
            owner = str(dep.get("owner_module_id", ""))
            if type_ref.startswith(("state:", "message:", "field:")):
                diagnostics.append(PlanningDiagnostic("error", "invalid_signature_dependency_type_namespace", f"function '{function_id}' uses non-type dependency '{type_ref}'", path))
            elif type_ref and dep.get("symbol_kind") != "system_type" and type_ref not in type_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_dependency_type", f"function '{function_id}' references unknown signature dependency '{type_ref}'", path))
            elif type_ref and dep.get("symbol_kind") == "system_type" and type_ref not in system_type_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_system_type", f"function '{function_id}' references unknown system type '{type_ref}'", path))
            elif type_ref and type_ref in type_inventory and not _is_public_type(type_inventory[type_ref]) and dep.get("dependency_scope") == "header":
                diagnostics.append(PlanningDiagnostic("error", "unknown_or_unexported_signature_type", f"function '{function_id}' header dependency references unexported type '{type_ref}'", path))
            if owner and owner not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_signature_dependency_owner", f"function '{function_id}' signature dependency owner '{owner}' is not a module", path))
        if is_public:
            for declaration in update.get("interface_type_declarations", []):
                if str(declaration.get("visibility", "")).lower() in {"private", "internal"}:
                    diagnostics.append(PlanningDiagnostic("error", "public_signature_uses_private_interface_type", f"public function '{function_id}' exposes private/internal type '{declaration.get('name')}'", path))
    for name, ids in sorted(public_signature_names.items()):
        if name and len(ids) > 1:
            diagnostics.append(PlanningDiagnostic("warning", "duplicate_public_signature_name", f"public C symbol '{name}' appears in multiple signature updates: {', '.join(ids)}", path))
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
    unresolved_targets = _unresolved_targets(patch)
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
            contract = update.get("contract", {})
            invariants = [item for item in contract.get("invariants_used", []) if str(item).strip()] if isinstance(contract, dict) else []
            if not invariants and function_id not in unresolved_targets:
                diagnostics.append(PlanningDiagnostic("warning", "behavior_missing_invariants", f"EVENT function '{function_id}' should declare coder-facing invariants or an unresolved question", path))
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
    access_by_function_field = {
        (str(entry.get("function_id", "")), str(entry.get("field_id", ""))): entry
        for entry in patch["access_path_entries"]
        if isinstance(entry, dict)
    }
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
        access_entry = access_by_function_field.get((entry["function_id"], entry["field_id"]))
        target_path = str(entry.get("target_path", "")).strip()
        access_path = str((access_entry or {}).get("path", "")).strip()
        if access_path and target_path and target_path != "buffer" and target_path != access_path:
            diagnostics.append(PlanningDiagnostic("error", "wire_mapping_target_path_not_canonical", f"wire mapping '{entry['wire_mapping_id']}' target_path must match access path '{access_path}'", path))
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
    for field_id in sorted(field_ids - mapped_fields):
        diagnostics.append(PlanningDiagnostic("error", "uncovered_wire_field", f"wire field '{field_id}' is not covered by parser/serializer mapping", path))
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
        if known is None:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_unknown_lifecycle_function", f"runtime entrypoint {action} lifecycle function '{function_id}' does not exist", path))
            continue
        if known and str(known.get("module_id", "")) != key_module:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_lifecycle_wrong_module", f"lifecycle function '{function_id}' is not in key flow module '{key_module}'", path))
        if known and not _is_lifecycle_api(known, action):
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_lifecycle_not_api", f"lifecycle function '{function_id}' is not a public {action} lifecycle API", path))
    sequence_steps = {str(item.get("step", "")) for item in candidate.get("startup_sequence", []) if isinstance(item, dict)}
    for required in ("parse_args", "create", "start", "run", "destroy"):
        if required not in sequence_steps:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_sequence_missing_step", f"startup_sequence missing '{required}' step", path))
    lifecycle_values = {str(value).strip() for value in lifecycle.values() if str(value).strip()}
    for step in candidate.get("startup_sequence", []) if isinstance(candidate.get("startup_sequence", []), list) else []:
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("step", "")).strip()
        function_id = str(step.get("function_id", "")).strip()
        if step_name in {"create", "start", "run", "destroy"} and function_id != str(lifecycle.get(step_name, "")).strip():
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_sequence_lifecycle_mismatch", f"startup_sequence step '{step_name}' must reference lifecycle_function_ids.{step_name}", path))
        elif function_id and function_id not in lifecycle_values:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_non_lifecycle_call", f"startup_sequence references non-lifecycle function '{function_id}'", path))
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
    service_requirement_ids = expected_service_requirement_ids if expected_service_requirement_ids is not None else _service_requirement_ids(functions, kinds={"cross_module_service"})
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
            if edge.get("service_requirement_ids") and caller_fn.get("module_id") == callee_fn.get("module_id"):
                diagnostics.append(PlanningDiagnostic("error", "cross_module_service_bound_to_same_module_call", f"caller '{caller}' cannot resolve cross-module service through same-module callee '{callee}'", path))
            if caller_fn.get("module_id") not in module_ids or callee_fn.get("module_id") not in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "call_unknown_module", f"call edge '{caller}' -> '{callee}' references unknown module", path))
            bindings = edge.get("param_bindings", [])
            callee_params = _signature_params(callee_fn)
            if isinstance(bindings, list) and bindings:
                if len(bindings) != len(callee_params):
                    diagnostics.append(PlanningDiagnostic("error", "call_contract_param_count_mismatch", f"caller '{caller}' binds {len(bindings)} values for callee '{callee}' with {len(callee_params)} params", path))
                elif not _binding_param_names_match(bindings, callee_params):
                    diagnostics.append(PlanningDiagnostic("error", "call_contract_param_name_mismatch", f"caller '{caller}' binds parameter names that do not match callee '{callee}' signature", path))
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
            diag = _layout_export_type_diagnostic(str(type_id), draft, file_id, path)
            if diag is not None:
                diagnostics.append(diag)
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
    files_by_module: dict[str, list[dict[str, Any]]] = {}
    functions_by_module: dict[str, list[dict[str, Any]]] = {}
    for file_item in candidate["files"]:
        files_by_module.setdefault(str(file_item.get("module_id", "")), []).append(file_item)
    for function in functions.values():
        functions_by_module.setdefault(str(function.get("module_id", "")), []).append(function)
    for module_id, module_functions in sorted(functions_by_module.items()):
        module_files = files_by_module.get(module_id, [])
        kinds = {str(function.get("function_kind", "")) for function in module_functions if str(function.get("function_kind", "")).strip()}
        public_count = sum(1 for function in module_functions if _is_public_function(function))
        private_count = len(module_functions) - public_count
        non_trivial = len(module_functions) >= 6 and (len(kinds) >= 3 or (public_count > 0 and private_count > 0))
        if non_trivial and len(module_files) == 1:
            diagnostics.append(
                PlanningDiagnostic(
                    "warning",
                    "mechanical_single_file_module_layout",
                    f"non-trivial module '{module_id}' has {len(module_functions)} functions across {len(kinds)} function kinds but only one source_header_pair",
                    path,
                )
            )
    return diagnostics


def validate_file_layout_override_patch(patch: dict[str, Any], baseline: dict[str, Any], draft: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics = _shape(patch, FILE_LAYOUT_OVERRIDE_PATCH_SCHEMA_VERSION, path=path)
    if has_errors(diagnostics):
        return diagnostics
    files = {str(item.get("file_id", "")): item for item in baseline.get("files", []) if isinstance(item, dict)}
    module_ids = _module_artifact_ids(draft.get("module_artifacts", []))
    functions = _function_by_id(draft)
    for module_id in patch.get("force_single_unit_module_ids", []):
        if module_id not in module_ids:
            diagnostics.append(PlanningDiagnostic("error", "override_unknown_module", f"file layout override references unknown module '{module_id}'", path))
    for override in patch.get("file_responsibility_overrides", []):
        file_id = str(override.get("file_id", ""))
        if file_id not in files:
            diagnostics.append(PlanningDiagnostic("error", "override_unknown_file", f"file layout override references unknown file '{file_id}'", path))
    for reassignment in patch.get("function_reassignments", []):
        function_id = str(reassignment.get("function_id", ""))
        target_file_id = str(reassignment.get("target_file_id", ""))
        function = functions.get(function_id)
        target_file = files.get(target_file_id)
        if function is None:
            diagnostics.append(PlanningDiagnostic("error", "override_unknown_function", f"file layout override references unknown function '{function_id}'", path))
            continue
        if target_file is None:
            diagnostics.append(PlanningDiagnostic("error", "override_unknown_target_file", f"file layout override targets unknown file '{target_file_id}'", path))
            continue
        if str(function.get("module_id", "")) != str(target_file.get("module_id", "")):
            diagnostics.append(PlanningDiagnostic("error", "override_cross_module_reassignment", f"function '{function_id}' may not move to file '{target_file_id}' in another module", path))
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
    metadata = plan.get("protocol_metadata", {}) if isinstance(plan.get("protocol_metadata"), dict) else {}
    if not str(metadata.get("name") or plan.get("protocol_name") or "").strip():
        diagnostics.append(PlanningDiagnostic("error", "readiness_missing_protocol_name", "final implementation_plan must include protocol metadata name", path))
    if not str(metadata.get("protocol_version") or metadata.get("spec_version") or metadata.get("version") or "").strip():
        diagnostics.append(PlanningDiagnostic("error", "readiness_missing_protocol_version", "final implementation_plan must include protocol version/spec_version metadata", path))
    roles = metadata.get("roles", [])
    if not ([item for item in roles if str(item).strip()] if isinstance(roles, list) else str(roles).strip()):
        diagnostics.append(PlanningDiagnostic("error", "readiness_missing_protocol_roles", "final implementation_plan must include target protocol role metadata", path))
    if plan.get("dependency_graph") is None:
        diagnostics.append(PlanningDiagnostic("error", "missing_final_dependency_graph", "final implementation_plan must include deterministic dependency_graph", path))
    blocking = [
        item
        for item in plan.get("unresolved_questions", [])
        if isinstance(item, dict) and bool(item.get("blocking"))
    ]
    if blocking:
        diagnostics.append(PlanningDiagnostic("error", "blocking_unresolved_questions", "final implementation_plan still contains blocking unresolved questions", path))
    diagnostics.extend(_projected_public_symbol_diagnostics(plan, path))
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    for file_item in files:
        file_id = str(file_item.get("file_id", ""))
        for type_id in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            diag = _layout_export_type_diagnostic(str(type_id), plan, file_id, path)
            if diag is not None:
                diagnostics.append(diag)
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    functions_by_id = {str(item.get("function_id", "")): item for item in functions if str(item.get("function_id", "")).strip()}
    type_inventory = _type_inventory_by_id(plan)
    type_inventory_by_name = _type_inventory_name_index(list(type_inventory.values()))
    canonical_types = _canonical_types_by_id(plan)
    canonical_types_by_name = _canonical_type_name_index(canonical_types)
    access_paths_by_id = {
        str(item.get("access_path_id", "")): str(item.get("path", "")).strip()
        for item in plan.get("access_path_table", [])
        if isinstance(item, dict) and str(item.get("access_path_id", "")).strip()
    }
    access_path_values = {value for value in access_paths_by_id.values() if value}
    for mapping in plan.get("wire_mapping_table", []) if isinstance(plan.get("wire_mapping_table"), list) else []:
        if not isinstance(mapping, dict):
            continue
        access_id = str(mapping.get("access_path_id", "")).strip()
        target_path = str(mapping.get("target_path", "")).strip()
        if access_id and access_id not in access_paths_by_id:
            diagnostics.append(PlanningDiagnostic("error", "readiness_wire_mapping_unknown_access_id", f"wire mapping '{mapping.get('mapping_id')}' references unknown access path '{access_id}'", path))
        if target_path and target_path != "buffer" and access_path_values and target_path not in access_path_values:
            diagnostics.append(PlanningDiagnostic("error", "readiness_wire_mapping_target_not_accessible", f"wire mapping '{mapping.get('mapping_id')}' target_path '{target_path}' is not an ACCESS_PATHS path", path))

    for function in functions:
        if not _is_public_function(function):
            continue
        function_id = str(function.get("function_id", ""))
        signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
        return_type_item, return_is_canonical = _type_item_by_ref_or_name(
            type_ref="",
            raw_type=signature.get("return_type", ""),
            type_inventory=type_inventory,
            type_inventory_by_name=type_inventory_by_name,
            canonical_types=canonical_types,
            canonical_types_by_name=canonical_types_by_name,
        )
        if _type_is_by_value(signature.get("return_type", "")) and _is_public_opaque_type(return_type_item, canonical=return_is_canonical):
            diagnostics.append(PlanningDiagnostic("error", "readiness_public_signature_opaque_by_value", f"public function '{function_id}' returns opaque public type '{signature.get('return_type')}' by value", path))
        for param in signature.get("params", []) if isinstance(signature.get("params"), list) else []:
            if not isinstance(param, dict):
                continue
            raw_param_type = str(param.get("type", ""))
            param_type_item, param_is_canonical = _type_item_by_ref_or_name(
                type_ref=param.get("type_ref", ""),
                raw_type=raw_param_type,
                type_inventory=type_inventory,
                type_inventory_by_name=type_inventory_by_name,
                canonical_types=canonical_types,
                canonical_types_by_name=canonical_types_by_name,
            )
            if _type_is_by_value(raw_param_type) and _is_public_opaque_type(param_type_item, canonical=param_is_canonical):
                diagnostics.append(PlanningDiagnostic("error", "readiness_public_signature_opaque_by_value", f"public function '{function_id}' exposes opaque public type '{raw_param_type}' by value", path))

    for caller in functions:
        function_symbols = set(functions_by_id)
        for function in functions:
            signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
            for value in (function.get("name"), signature.get("name"), canonical_function_symbol(function)):
                text = str(value or "").strip()
                if text:
                    function_symbols.add(text)
        caller_id = str(caller.get("function_id", ""))
        caller_param_names = {
            str(param.get("name", ""))
            for param in _signature_params(caller)
            if str(param.get("name", "")).strip()
        }
        call_contracts = caller.get("call_contracts", []) if isinstance(caller.get("call_contracts"), list) else []
        local_symbols = {
            str(edge.get("return_binding", {}).get("target_ref", "")).strip()
            for edge in call_contracts
            if isinstance(edge, dict) and isinstance(edge.get("return_binding"), dict) and str(edge.get("return_binding", {}).get("target_ref", "")).strip()
        }
        for edge in call_contracts:
            if not isinstance(edge, dict):
                continue
            callee_id = str(edge.get("callee_function_id", "")).strip()
            callee = functions_by_id.get(callee_id)
            if callee is None:
                diagnostics.append(PlanningDiagnostic("error", "readiness_call_contract_unknown_callee", f"function '{caller_id}' call contract references unknown callee '{callee_id}'", path))
                continue
            if str(caller.get("module_id", "")) != str(callee.get("module_id", "")) and not _is_public_function(callee):
                diagnostics.append(PlanningDiagnostic("error", "readiness_call_contract_private_cross_module", f"function '{caller_id}' cannot call non-public callee '{callee_id}' across modules", path))
            bindings = edge.get("param_bindings", [])
            callee_params = _signature_params(callee)
            if isinstance(bindings, list) and bindings and len(bindings) != len(callee_params):
                diagnostics.append(PlanningDiagnostic("error", "readiness_call_contract_param_count_mismatch", f"call contract '{caller_id}' -> '{callee_id}' has {len(bindings)} param bindings for {len(callee_params)} callee params", path))
            elif isinstance(bindings, list) and bindings and not _binding_param_names_match(bindings, callee_params):
                diagnostics.append(PlanningDiagnostic("error", "readiness_call_contract_param_name_mismatch", f"call contract '{caller_id}' -> '{callee_id}' binds parameter names that do not match the callee signature", path))
            for binding in bindings if isinstance(bindings, list) else []:
                if not isinstance(binding, dict):
                    continue
                value_ref = str(binding.get("value_ref", "")).strip()
                if not _allowed_call_value_ref(value_ref, caller_param_names, access_path_values, local_symbols, function_symbols):
                    diagnostics.append(PlanningDiagnostic("error", "readiness_call_contract_unknown_param_binding", f"call contract '{caller_id}' -> '{callee_id}' binds unknown value_ref '{value_ref}'", path))

    wire_facing = [
        function
        for function in functions
        if function.get("wire_mapping") or str(function.get("function_kind", "")) in {"parser", "serializer"}
    ]
    has_test_seed = bool(plan.get("test_plan"))
    has_function_vectors = any(function.get("test_vectors") for function in wire_facing)
    if wire_facing and not (has_test_seed or has_function_vectors):
        diagnostics.append(PlanningDiagnostic("error", "readiness_missing_codec_test_vectors", "wire-facing parser/serializer functions require TEST_VECTORS or planning test_plan seeds", path))
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
    file_by_id = {str(item.get("file_id", "")): item for item in files if str(item.get("file_id", "")).strip()}
    target_role = str(profile.get("target_role", {}).get("value", profile.get("target_role", "")) if isinstance(profile.get("target_role"), dict) else profile.get("target_role", "")).strip()
    if target_role and not (entrypoints and main_files):
        diagnostics.append(PlanningDiagnostic("error", "missing_runtime_entrypoint", f"deployable target role '{target_role}' requires a runtime entrypoint main.c", path))
    for entrypoint in entrypoints:
        entrypoint_id = str(entrypoint.get("function_id", ""))
        entry_file_id = str(entrypoint.get("file_id", "")).strip()
        entry_file = file_by_id.get(entry_file_id, {})
        source_imports = {
            str(item).strip()
            for item in entry_file.get("imports_allowed", [])
            if str(item).strip()
        } if isinstance(entry_file.get("imports_allowed", []), list) else set()
        lifecycle_refs = {
            str(item).strip()
            for item in entrypoint.get("calls_allowed", [])
            if str(item).strip()
        } if isinstance(entrypoint.get("calls_allowed", []), list) else set()
        for contract in entrypoint.get("call_contracts", []) if isinstance(entrypoint.get("call_contracts", []), list) else []:
            if isinstance(contract, dict) and str(contract.get("callee_function_id", "")).strip():
                lifecycle_refs.add(str(contract["callee_function_id"]).strip())
        if not lifecycle_refs:
            diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_unknown_lifecycle_function", f"runtime entrypoint '{entrypoint_id}' does not call lifecycle functions", path))
        for callee_id in sorted(lifecycle_refs):
            callee = functions_by_id.get(callee_id)
            if callee is None:
                diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_unknown_lifecycle_function", f"runtime entrypoint '{entrypoint_id}' references unknown lifecycle function '{callee_id}'", path))
                continue
            action = str(callee.get("public_api_role", "")).removeprefix("runtime_")
            if action not in {"create", "start", "run", "destroy"} or not _is_lifecycle_api(callee, action):
                diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_non_lifecycle_call", f"runtime entrypoint '{entrypoint_id}' may only call public lifecycle APIs, not '{callee_id}'", path))
                continue
            callee_file_id = str(callee.get("declared_in") or callee.get("file_id") or "").strip()
            if callee_file_id and callee_file_id != entry_file_id and callee_file_id not in source_imports:
                diagnostics.append(PlanningDiagnostic("error", "runtime_entrypoint_missing_source_dependency", f"runtime entrypoint '{entrypoint_id}' must import lifecycle file '{callee_file_id}' for '{callee_id}'", path))
    if target_role.lower() in {"broker", "server"}:
        runtime_tests = [
            item
            for item in plan.get("test_plan", [])
            if isinstance(item, dict) and str(item.get("level", "runtime")).strip().lower() == "runtime"
        ]
        purposes = [str(item.get("purpose", "")).lower() for item in runtime_tests]
        if not any(any(word in purpose for word in ("success", "valid", "interaction", "publish", "request")) for purpose in purposes):
            diagnostics.append(PlanningDiagnostic("error", "readiness_missing_runtime_success_test", f"deployable target role '{target_role}' requires a successful runtime interaction test", path))
        if not any(any(word in purpose for word in ("malformed", "invalid", "error", "unknown", "not found")) for purpose in purposes):
            diagnostics.append(PlanningDiagnostic("error", "readiness_missing_runtime_error_test", f"deployable target role '{target_role}' requires a malformed/error runtime test", path))
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
validate_type_inventory = validate_type_inventory_candidate
validate_function_inventory = validate_function_inventory_candidate
validate_function_signatures = validate_function_signature_patch
validate_function_behavior_contracts = validate_function_behavior_contract_patch
validate_wire_access_binding = validate_wire_access_binding_patch
validate_calls_allowed = validate_calls_allowed_candidate
validate_runtime_entrypoint = validate_runtime_entrypoint_candidate
validate_file_layout = validate_file_layout_candidate
