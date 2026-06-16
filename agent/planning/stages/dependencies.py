from __future__ import annotations

import json
import re
from pathlib import Path
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
        },
    }


def build_dependency_validation_report(plan: dict[str, Any], diagnostics: list[Any]) -> dict[str, Any]:
    graph = plan.get("dependency_graph", {})
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    enriched = [_enrich_diagnostic(item, plan) for item in diagnostics]
    return {
        "schema_version": "dependency_validation_report/v1",
        "status": "failed" if diagnostics else "passed",
        "summary": {
            "module_edge_count": len(graph.get("module_edges", [])) if isinstance(graph, dict) else 0,
            "file_edge_count": len(graph.get("file_edges", [])) if isinstance(graph, dict) else 0,
            "function_edge_count": len(graph.get("function_edges", [])) if isinstance(graph, dict) else 0,
            "calls_allowed_count": sum(len(item.get("calls_allowed", [])) for item in functions if isinstance(item.get("calls_allowed", []), list)),
            "call_contract_count": sum(len(item.get("call_contracts", [])) for item in functions if isinstance(item.get("call_contracts", []), list)),
            "signature_dependency_count": sum(len(item.get("signature_dependencies", [])) for item in functions if isinstance(item.get("signature_dependencies", []), list)),
            "imports_allowed_count": sum(len(item.get("imports_allowed", [])) for item in files if isinstance(item.get("imports_allowed", []), list)),
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
        "errors": [item for item in enriched if item["level"] == "error"],
        "warnings": [item for item in enriched if item["level"] == "warning"],
        "dependency_sources": _dependency_sources(plan),
        "call_edge_sources": _call_edge_sources(plan),
        "header_dep_sources": [],
        "source_dep_sources": [],
    }


def attach_coder_dependency_sources(report: dict[str, Any], spec_root: str | Path) -> dict[str, Any]:
    header_sources: list[dict[str, Any]] = []
    source_sources: list[dict[str, Any]] = []
    root = Path(spec_root)
    for path in sorted(root.rglob("*_spec.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("KIND") != "FILE_SPEC":
            continue
        file_meta = raw.get("FILE", {}) if isinstance(raw.get("FILE"), dict) else {}
        header = raw.get("HEADER", {}) if isinstance(raw.get("HEADER"), dict) else {}
        source = raw.get("SOURCE", {}) if isinstance(raw.get("SOURCE"), dict) else {}
        trace_id = str(file_meta.get("TRACE_ID", "")).strip()
        header_path = str(header.get("PATH", "")).strip()
        source_path = str(source.get("PATH", "")).strip()
        for dependency in header.get("DEPENDENCY", []) if isinstance(header.get("DEPENDENCY", []), list) else []:
            dep = str(dependency).strip()
            if dep:
                header_sources.append(
                    {
                        "file_trace_id": trace_id,
                        "header_path": header_path,
                        "dependency_header": dep,
                        "source_fields": [f"spec_bundle.file_specs[trace_id={trace_id}].HEADER.DEPENDENCY"],
                        "reason": "declared coder-facing public header dependency",
                    }
                )
        for dependency in source.get("DEPENDENCY", []) if isinstance(source.get("DEPENDENCY", []), list) else []:
            dep = str(dependency).strip()
            if dep:
                source_sources.append(
                    {
                        "file_trace_id": trace_id,
                        "source_path": source_path,
                        "dependency_header": dep,
                        "source_fields": [f"spec_bundle.file_specs[trace_id={trace_id}].SOURCE.DEPENDENCY"],
                        "reason": "declared coder-facing source include dependency",
                    }
                )
    updated = dict(report)
    updated["header_dep_sources"] = header_sources
    updated["source_dep_sources"] = source_sources
    return updated


def _enrich_diagnostic(item: Any, plan: dict[str, Any]) -> dict[str, Any]:
    code = str(getattr(item, "code", "dependency_validation_error"))
    message = str(getattr(item, "message", str(item)))
    entity_kind, entity_id = _diagnostic_entity(code, message)
    return {
        "level": str(getattr(item, "level", "error")),
        "code": code,
        "stage": "5.7_spec_readiness/dependency_validation",
        "message": message,
        "path": getattr(item, "path", None),
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "source_fields": _diagnostic_source_fields(code, message, entity_kind, entity_id, plan),
        "suggested_repair": _suggested_repair(code),
    }


def _diagnostic_entity(code: str, message: str) -> tuple[str, str]:
    match = re.search(r"function '([^']+)'", message, re.IGNORECASE)
    if match:
        return "function", match.group(1)
    if "function" in code or "function_edges" in message:
        edge = re.search(r"'([^']+)'\s*->\s*'([^']+)'", message)
        if edge:
            return "dependency_edge", f"{edge.group(1)}->{edge.group(2)}"
        return "dependency_edge", ""
    if "module" in code:
        match = re.search(r"module '([^']+)'", message, re.IGNORECASE)
        return "module", match.group(1) if match else ""
    if "signature" in code or "type" in code:
        match = re.search(r"function '([^']+)'", message, re.IGNORECASE)
        if match:
            return "function", match.group(1)
        return "type", ""
    if "file" in message:
        match = re.search(r"file '([^']+)'", message, re.IGNORECASE)
        return "file", match.group(1) if match else ""
    return "unknown", ""


def _diagnostic_source_fields(code: str, message: str, entity_kind: str, entity_id: str, plan: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    if code in {"dependency_call_contracts_without_calls_allowed", "dependency_call_contract_unknown_callee", "dependency_call_contract_not_allowed"}:
        fields.extend(_function_fields(entity_id, ["call_contracts", "calls_allowed"]))
    elif code in {"dependency_graph_missing_function_edges", "dependency_graph_missing_function_edge", "dependency_graph_unexplained_function_edge"}:
        fields.append("dependency_graph.function_edges")
        if entity_kind == "dependency_edge" and "->" in entity_id:
            caller, _ = entity_id.split("->", 1)
            fields.extend(_function_fields(caller, ["calls_allowed", "call_contracts"]))
    elif code == "dependency_unknown_module_dependency":
        fields.append(f"module_artifacts[module_id={entity_id}].dependencies" if entity_id else "module_artifacts[*].dependencies")
    elif code in {"dependency_signature_missing_import", "dependency_signature_unresolved_provider_file", "dependency_unknown_signature_dependency_owner"}:
        function_id = entity_id if entity_kind == "function" else _extract_quoted_after(message, "function")
        fields.extend(_function_fields(function_id, ["signature_dependencies"]))
        source_file, target_file = _extract_import_files(message)
        if source_file:
            fields.append(f"file_layout.files[file_id={source_file}].imports_allowed")
        if target_file:
            fields.append(f"file_layout.files[file_id={target_file}].exports_type_ids")
    elif code.startswith("unknown_dependency_") or code in {"self_dependency_edge", "private_dependency_edge", "dependency_cycle"}:
        fields.append(_dependency_graph_field(message))
    if not fields:
        fields.append("dependency_graph")
    return sorted(dict.fromkeys(fields))


def _function_fields(function_id: str, names: list[str]) -> list[str]:
    if not function_id:
        return [f"function_contracts[*].{name}" for name in names]
    return [f"function_contracts[function_id={function_id}].{name}" for name in names]


def _extract_quoted_after(message: str, label: str) -> str:
    match = re.search(rf"{label} '([^']+)'", message, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_import_files(message: str) -> tuple[str, str]:
    match = re.search(r"requires file '([^']+)' to import '([^']+)'", message)
    return (match.group(1), match.group(2)) if match else ("", "")


def _dependency_graph_field(message: str) -> str:
    for key in ("module_edges", "file_edges", "function_edges"):
        if key in message:
            return f"dependency_graph.{key}"
    return "dependency_graph"


def _suggested_repair(code: str) -> str:
    if code.startswith("dependency_call_contract"):
        return "align call_contracts with calls_allowed and known callee functions"
    if code.startswith("dependency_graph_missing") or code.startswith("dependency_graph_unexplained"):
        return "regenerate dependency_graph.function_edges from calls_allowed and call_contracts"
    if code == "dependency_unknown_module_dependency":
        return "remove the unknown module dependency or add the provider module artifact"
    if code.startswith("dependency_signature"):
        return "add the provider file to imports_allowed or repair the signature dependency provider type"
    if code.startswith("unknown_dependency"):
        return "repair dependency_graph edge endpoints so they reference known module/file/function ids"
    if code == "dependency_cycle":
        return "break compile-order dependency cycles without deleting source-only call edges"
    return "repair calls_allowed, imports_allowed, signature_dependencies, or dependency_graph; do not clear dependency inputs"


def _call_edge_sources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    graph = plan.get("dependency_graph", {}) if isinstance(plan.get("dependency_graph"), dict) else {}
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    expected: dict[tuple[str, str], set[str]] = {}
    for function in functions:
        caller = str(function.get("function_id", "")).strip()
        for callee in function.get("calls_allowed", []) if isinstance(function.get("calls_allowed", []), list) else []:
            edge = (caller, str(callee).strip())
            if edge[0] and edge[1]:
                expected.setdefault(edge, set()).add("function_contracts[*].calls_allowed")
        for contract in function.get("call_contracts", []) if isinstance(function.get("call_contracts", []), list) else []:
            if not isinstance(contract, dict):
                continue
            edge = (caller, str(contract.get("callee_function_id", "")).strip())
            if edge[0] and edge[1]:
                expected.setdefault(edge, set()).add("function_contracts[*].call_contracts")
    graph_edges = {
        (str(edge.get("from", "")).strip(), str(edge.get("to", "")).strip())
        for edge in graph.get("function_edges", [])
        if isinstance(edge, dict) and str(edge.get("from", "")).strip() and str(edge.get("to", "")).strip()
    }
    rows: list[dict[str, Any]] = []
    for caller, callee in sorted(set(expected) | graph_edges):
        source_fields = [
            field.replace("[*]", f"[function_id={caller}]")
            for field in sorted(expected.get((caller, callee), set()))
        ]
        has_graph_edge = (caller, callee) in graph_edges
        if has_graph_edge:
            source_fields.append("dependency_graph.function_edges")
        if has_graph_edge and (caller, callee) in expected:
            status = "explained"
        elif (caller, callee) in expected:
            status = "missing_graph_edge"
        else:
            status = "unexplained_graph_edge"
        rows.append(
            {
                "caller_function_id": caller,
                "callee_function_id": callee,
                "source_fields": sorted(dict.fromkeys(source_fields)),
                "status": status,
            }
        )
    return rows


def _dependency_sources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    graph = plan.get("dependency_graph", {}) if isinstance(plan.get("dependency_graph"), dict) else {}
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    module_by_file = {str(item.get("file_id", "")): str(item.get("module_id", "")) for item in files}
    function_by_id = {str(item.get("function_id", "")): item for item in functions}
    rows: list[dict[str, Any]] = []
    for edge_scope in ("module_edges", "file_edges", "function_edges"):
        for edge in graph.get(edge_scope, []) if isinstance(graph.get(edge_scope, []), list) else []:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from", "")).strip()
            target = str(edge.get("to", "")).strip()
            kind = str(edge.get("kind", "")).strip()
            rows.append(
                {
                    "edge_scope": edge_scope,
                    "from": source,
                    "to": target,
                    "kind": kind,
                    "source_fields": _edge_source_fields(edge_scope, source, target, kind, files, functions, module_by_file, function_by_id),
                }
            )
    return rows


def _edge_source_fields(
    edge_scope: str,
    source: str,
    target: str,
    kind: str,
    files: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    module_by_file: dict[str, str],
    function_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    fields: list[str] = []
    if edge_scope == "file_edges" and kind in {"imports_allowed", "file_import"}:
        fields.append(f"file_layout.files[file_id={source}].imports_allowed")
    elif edge_scope == "module_edges" and kind == "file_import":
        for file_item in files:
            file_id = str(file_item.get("file_id", "")).strip()
            if module_by_file.get(file_id) == source and any(module_by_file.get(str(dep).strip()) == target for dep in file_item.get("imports_allowed", []) if isinstance(file_item.get("imports_allowed", []), list)):
                fields.append(f"file_layout.files[file_id={file_id}].imports_allowed")
    elif edge_scope == "function_edges":
        fields.extend(_function_fields(source, ["calls_allowed", "call_contracts"]))
    elif kind == "function_call":
        for function in functions:
            function_id = str(function.get("function_id", "")).strip()
            function_file = str(function.get("file_id", "")).strip()
            if edge_scope == "file_edges" and function_file != source:
                continue
            if edge_scope == "module_edges" and str(function.get("module_id", "")).strip() != source:
                continue
            if any(str(item).strip() in function_by_id and _edge_target_matches(function_by_id[str(item).strip()], edge_scope, target, module_by_file) for item in function.get("calls_allowed", []) if isinstance(function.get("calls_allowed", []), list)):
                fields.extend(_function_fields(function_id, ["calls_allowed", "call_contracts"]))
    elif kind == "signature_dependency":
        for function in functions:
            function_file = str(function.get("file_id", "")).strip()
            if edge_scope == "file_edges" and function_file != source:
                continue
            if edge_scope == "module_edges" and str(function.get("module_id", "")).strip() != source:
                continue
            for dep in function.get("signature_dependencies", []) if isinstance(function.get("signature_dependencies", []), list) else []:
                if not isinstance(dep, dict):
                    continue
                owner = str(dep.get("owner_module_id", "")).strip()
                if edge_scope == "module_edges" and owner == target:
                    fields.extend(_function_fields(str(function.get("function_id", "")).strip(), ["signature_dependencies"]))
                elif edge_scope == "file_edges":
                    fields.extend(_function_fields(str(function.get("function_id", "")).strip(), ["signature_dependencies"]))
    if not fields:
        fields.append(f"dependency_graph.{edge_scope}")
    return sorted(dict.fromkeys(fields))


def _edge_target_matches(function: dict[str, Any], edge_scope: str, target: str, module_by_file: dict[str, str]) -> bool:
    if edge_scope == "module_edges":
        return str(function.get("module_id", "")).strip() == target
    if edge_scope == "file_edges":
        return str(function.get("file_id", "")).strip() == target
    if edge_scope == "function_edges":
        return str(function.get("function_id", "")).strip() == target
    return False
