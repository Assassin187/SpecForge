from __future__ import annotations

from typing import Any

from ..schemas.implementation_plan import DEPENDENCY_GRAPH_SCHEMA_VERSION


def derive_dependency_graph(plan: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    module_by_file = {str(item.get("file_id", "")): str(item.get("module_id", "")) for item in files}
    file_by_module: dict[str, str] = {}
    for item in files:
        module_id = str(item.get("module_id", ""))
        file_id = str(item.get("file_id", ""))
        if not module_id or not file_id:
            continue
        if module_id not in file_by_module or str(item.get("header_path", "")).strip():
            file_by_module[module_id] = file_id
    state_owner = {str(item.get("state_id", "")): str(item.get("owner_module_id", "")) for item in plan.get("state_design", []) if isinstance(item, dict)}
    known_files = set(module_by_file)
    known_functions = {str(item.get("function_id", "")) for item in functions}
    file_edges: list[dict[str, str]] = []
    module_edges: list[dict[str, str]] = []
    function_edges: list[dict[str, str]] = []

    for file_item in files:
        source_id = str(file_item.get("file_id", ""))
        for target_id in file_item.get("imports_allowed", []):
            target = str(target_id)
            if target not in known_files:
                continue
            edge = {"from": source_id, "to": target, "kind": "imports_allowed"}
            if edge not in file_edges:
                file_edges.append(edge)
            module_edge = {"from": module_by_file.get(source_id, ""), "to": module_by_file.get(target, ""), "kind": "file_import"}
            if module_edge["from"] and module_edge["to"] and module_edge["from"] != module_edge["to"] and module_edge not in module_edges:
                module_edges.append(module_edge)

    for function in functions:
        source_id = str(function.get("function_id", ""))
        source_file = str(function.get("file_id", ""))
        source_module = module_by_file.get(source_file, str(function.get("module_id", "")))
        for dep in function.get("signature_dependencies", []):
            target_module = str(dep.get("owner_module_id", "")) if isinstance(dep, dict) else ""
            target_file = file_by_module.get(target_module, "")
            if source_module and target_module and source_module != target_module:
                module_edge = {"from": source_module, "to": target_module, "kind": "signature_dependency"}
                if module_edge not in module_edges:
                    module_edges.append(module_edge)
            if source_file and target_file and source_file != target_file:
                file_edge = {"from": source_file, "to": target_file, "kind": "signature_dependency"}
                if file_edge not in file_edges:
                    file_edges.append(file_edge)
        for state in function.get("state_access", []):
            target_module = state_owner.get(str(state.get("state_id", ""))) if isinstance(state, dict) else ""
            target_file = file_by_module.get(target_module or "", "")
            if source_module and target_module and source_module != target_module:
                module_edge = {"from": source_module, "to": target_module, "kind": "state_access"}
                if module_edge not in module_edges:
                    module_edges.append(module_edge)
            if source_file and target_file and source_file != target_file:
                file_edge = {"from": source_file, "to": target_file, "kind": "state_access"}
                if file_edge not in file_edges:
                    file_edges.append(file_edge)
        for target_id in function.get("calls_allowed", []):
            target = str(target_id)
            if target not in known_functions:
                continue
            edge = {"from": source_id, "to": target, "kind": "calls_allowed"}
            if edge not in function_edges:
                function_edges.append(edge)
            target_file = next((str(item.get("file_id", "")) for item in functions if item.get("function_id") == target), "")
            if source_file and target_file and source_file != target_file:
                file_edge = {"from": source_file, "to": target_file, "kind": "function_call"}
                if file_edge not in file_edges:
                    file_edges.append(file_edge)
                module_edge = {"from": module_by_file.get(source_file, ""), "to": module_by_file.get(target_file, ""), "kind": "function_call"}
                if module_edge["from"] and module_edge["to"] and module_edge["from"] != module_edge["to"] and module_edge not in module_edges:
                    module_edges.append(module_edge)

    return {
        "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
        "module_edges": module_edges,
        "file_edges": file_edges,
        "function_edges": function_edges,
        "derived_from": {
            "imports_allowed": "implementation_plan.file_layout.files[*].imports_allowed",
            "calls_allowed": "implementation_plan.function_contracts[*].calls_allowed",
            "signature_dependencies": "implementation_plan.function_contracts[*].signature_dependencies",
            "state_access": "implementation_plan.function_contracts[*].state_access",
        },
    }


def build_dependency_validation_report(plan: dict[str, Any], diagnostics: list[Any]) -> dict[str, Any]:
    graph = plan.get("dependency_graph", {})
    return {
        "schema_version": "dependency_validation_report/v1",
        "status": "failed" if diagnostics else "passed",
        "summary": {
            "module_edge_count": len(graph.get("module_edges", [])) if isinstance(graph, dict) else 0,
            "file_edge_count": len(graph.get("file_edges", [])) if isinstance(graph, dict) else 0,
            "function_edge_count": len(graph.get("function_edges", [])) if isinstance(graph, dict) else 0,
            "diagnostic_count": len(diagnostics),
        },
        "diagnostics": [
            {
                "level": getattr(item, "level", "error"),
                "code": getattr(item, "code", "dependency_validation_error"),
                "message": getattr(item, "message", str(item)),
                "path": getattr(item, "path", None),
            }
            for item in diagnostics
        ],
    }
