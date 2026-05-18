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

    module_ids = {str(item.get("module_id", "")) for item in plan.get("module_contracts", []) if isinstance(item, dict)}
    file_ids = {str(item.get("file_id", "")) for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)}
    function_ids = {str(item.get("function_id", "")) for item in plan.get("function_contracts", []) if isinstance(item, dict)}
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
    return diagnostics

