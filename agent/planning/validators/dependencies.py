from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.implementation_plan import DEPENDENCY_GRAPH_SCHEMA_VERSION


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
    function_by_id = {str(item.get("function_id", "")): item for item in functions}
    module_by_function = {str(item.get("function_id", "")): str(item.get("module_id", "")) for item in functions}
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
    return diagnostics


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
