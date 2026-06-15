from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.implementation_plan import DEPENDENCY_GRAPH_SCHEMA_VERSION
from ..stages.implementation_plan_context import SYSTEM_TYPE_IDS, normalize_system_type_ref


def validate_dependency_graph(plan: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    graph = plan.get("dependency_graph", {})
    if not isinstance(graph, dict) or graph.get("schema_version") != DEPENDENCY_GRAPH_SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_dependency_graph_schema", f"dependency_graph must use {DEPENDENCY_GRAPH_SCHEMA_VERSION}", path))
        return diagnostics

    module_ids = {str(item.get("module_id", "")) for item in plan.get("module_artifacts", []) if isinstance(item, dict)}
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    file_ids = {str(item.get("file_id", "")) for item in files}
    function_ids = {str(item.get("function_id", "")) for item in functions}
    file_by_id = {str(item.get("file_id", "")): item for item in files}
    function_by_id = {str(item.get("function_id", "")): item for item in functions}
    module_by_function = {str(item.get("function_id", "")): str(item.get("module_id", "")) for item in functions}
    file_by_function = {str(item.get("function_id", "")): str(item.get("file_id", "")) for item in functions}
    edge_sets = {
        "module_edges": module_ids,
        "file_edges": file_ids,
        "function_edges": function_ids,
    }
    for key, known_ids in edge_sets.items():
        edges = graph.get(key, [])
        if not isinstance(edges, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_dependency_edges", f"{key} must be an array", path))
            continue
        for idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                diagnostics.append(PlanningDiagnostic("error", "invalid_dependency_edge", f"{key}[{idx}] must be an object", path))
                continue
            source = str(edge.get("from", ""))
            target = str(edge.get("to", ""))
            if source not in known_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_source", f"{key}[{idx}] source '{source}' does not exist", path))
            if target not in known_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_target", f"{key}[{idx}] target '{target}' does not exist", path))
            if source == target:
                diagnostics.append(PlanningDiagnostic("error", "self_dependency_edge", f"{key}[{idx}] may not point to itself", path))
            if key == "function_edges" and source in function_by_id and target in function_by_id:
                callee = function_by_id[target]
                visibility = str(callee.get("visibility", "public")).lower()
                if visibility in {"private", "static"} and module_by_function.get(source) != module_by_function.get(target):
                    diagnostics.append(PlanningDiagnostic("error", "private_dependency_edge", f"Function '{source}' may not depend on private/static function '{target}' across modules", path))
    for key in ("module_edges", "file_edges", "function_edges"):
        edges = graph.get(key, [])
        if isinstance(edges, list) and _has_cycle([(str(edge.get("from", "")), str(edge.get("to", ""))) for edge in edges if isinstance(edge, dict)]):
            diagnostics.append(PlanningDiagnostic("error", "dependency_cycle", f"{key} contains a cycle", path))
    diagnostics.extend(
        _cross_layer_dependency_diagnostics(
            plan,
            graph,
            module_ids=module_ids,
            file_by_id=file_by_id,
            function_ids=function_ids,
            function_by_id=function_by_id,
            file_by_function=file_by_function,
            path=path,
        )
    )
    return diagnostics


def _cross_layer_dependency_diagnostics(
    plan: dict[str, Any],
    graph: dict[str, Any],
    *,
    module_ids: set[str],
    file_by_id: dict[str, dict[str, Any]],
    function_ids: set[str],
    function_by_id: dict[str, dict[str, Any]],
    file_by_function: dict[str, str],
    path: str | None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    expected_call_edges: set[tuple[str, str]] = set()
    for function in function_by_id.values():
        caller = str(function.get("function_id", "")).strip()
        calls_allowed = function.get("calls_allowed", [])
        allowed = {str(item).strip() for item in calls_allowed if str(item).strip()} if isinstance(calls_allowed, list) else set()
        call_contracts = function.get("call_contracts", []) if isinstance(function.get("call_contracts"), list) else []
        if call_contracts and not allowed:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "dependency_call_contracts_without_calls_allowed",
                    f"function '{caller}' has call_contracts but no calls_allowed entries",
                    path,
                )
            )
        for callee in sorted(allowed):
            if callee in function_ids and callee != caller:
                expected_call_edges.add((caller, callee))
        for edge in call_contracts:
            if not isinstance(edge, dict):
                continue
            callee = str(edge.get("callee_function_id", "")).strip()
            if callee not in function_ids:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_call_contract_unknown_callee",
                        f"function '{caller}' call contract references unknown callee '{callee}'",
                        path,
                    )
                )
                continue
            expected_call_edges.add((caller, callee))
            if callee not in allowed:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_call_contract_not_allowed",
                        f"function '{caller}' call contract callee '{callee}' is not listed in calls_allowed",
                        path,
                    )
                )
    graph_call_edges = {
        (str(edge.get("from", "")).strip(), str(edge.get("to", "")).strip())
        for edge in graph.get("function_edges", [])
        if isinstance(edge, dict) and str(edge.get("from", "")).strip() and str(edge.get("to", "")).strip()
    }
    if expected_call_edges and not graph_call_edges:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "dependency_graph_missing_function_edges",
                "dependency_graph.function_edges is empty despite calls_allowed or call_contracts",
                path,
            )
        )
    elif expected_call_edges:
        for caller, callee in sorted(expected_call_edges - graph_call_edges):
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "dependency_graph_missing_function_edge",
                    f"dependency_graph.function_edges omits call edge '{caller}' -> '{callee}'",
                    path,
                )
            )
    for caller, callee in sorted(graph_call_edges - expected_call_edges):
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "dependency_graph_unexplained_function_edge",
                f"dependency_graph.function_edges contains '{caller}' -> '{callee}' without calls_allowed or call_contracts source",
                path,
            )
        )

    for module in plan.get("module_artifacts", []) if isinstance(plan.get("module_artifacts"), list) else []:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", "")).strip()
        for dependency in module.get("dependencies", []) if isinstance(module.get("dependencies", []), list) else []:
            dependency_id = str(dependency).strip()
            if dependency_id and dependency_id not in module_ids:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_unknown_module_dependency",
                        f"module '{module_id}' depends on unknown module '{dependency_id}'",
                        path,
                    )
                )

    type_file_by_ref: dict[str, str] = {}
    for file_id, file_item in file_by_id.items():
        for type_ref in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            ref = str(type_ref).strip()
            if ref:
                type_file_by_ref.setdefault(ref, file_id)
    for function in function_by_id.values():
        caller = str(function.get("function_id", "")).strip()
        source_file = file_by_function.get(caller, "")
        source_item = file_by_id.get(source_file, {})
        imports_allowed = {
            str(item).strip()
            for item in source_item.get("imports_allowed", [])
            if str(item).strip()
        } if isinstance(source_item.get("imports_allowed", []), list) else set()
        for dependency in function.get("signature_dependencies", []) if isinstance(function.get("signature_dependencies"), list) else []:
            if not isinstance(dependency, dict) or _is_system_signature_dependency(dependency):
                continue
            owner = str(dependency.get("owner_module_id", "")).strip()
            type_ref = str(dependency.get("type_ref", "")).strip()
            if not owner:
                continue
            if owner not in module_ids:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_unknown_signature_dependency_owner",
                        f"function '{caller}' signature dependency owner '{owner}' is not a module",
                        path,
                    )
                )
                continue
            target_file = type_file_by_ref.get(type_ref, "")
            if not target_file:
                if owner == str(source_item.get("module_id", "")).strip():
                    continue
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_signature_unresolved_provider_file",
                        f"function '{caller}' signature dependency '{type_ref or dependency.get('symbol_name', '')}' has no provider file",
                        path,
                    )
                )
                continue
            if source_file and target_file != source_file and target_file not in imports_allowed:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "dependency_signature_missing_import",
                        f"function '{caller}' signature dependency '{type_ref or dependency.get('symbol_name', '')}' requires file '{source_file}' to import '{target_file}'",
                        path,
                    )
                )
    return diagnostics


def _is_system_signature_dependency(dependency: dict[str, Any]) -> bool:
    if str(dependency.get("symbol_kind", "")).strip() == "system_type":
        return True
    type_ref = normalize_system_type_ref(dependency.get("type_ref", ""))
    symbol_name = normalize_system_type_ref(dependency.get("symbol_name", ""))
    return type_ref in SYSTEM_TYPE_IDS or symbol_name in SYSTEM_TYPE_IDS


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
