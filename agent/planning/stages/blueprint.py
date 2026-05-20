from __future__ import annotations

from typing import Any

from ..schemas.spec_blueprint import SCHEMA_VERSION


def build_spec_blueprint(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    modules = implementation_plan.get("module_contracts", [])
    files = implementation_plan.get("file_layout", {}).get("files", [])
    functions = implementation_plan.get("function_contracts", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_name": implementation_plan.get("protocol_name", "protocol"),
        "roles": [str(implementation_plan.get("target_directives_ref", {}).get("directives", {}).get("target_role", "target")).upper()],
        "source_artifact": "007_implementation_plan.json",
        "modules": [
            {
                "module_id": item.get("module_id"),
                "name": item.get("name", item.get("module_id")),
                "role": item.get("purpose", ""),
                "owned_capabilities": item.get("owned_capabilities", []),
                "dependencies": [],
                "traceability": {
                    "source_fact_ids": item.get("source_fact_ids", []),
                    "decision_ids": item.get("decision_ids", []),
                    "constraint_ids": item.get("constraints", []),
                },
            }
            for item in modules
            if isinstance(item, dict)
        ],
        "files": [
            {
                "file_id": item.get("file_id"),
                "trace_id": f"{implementation_plan.get('protocol_name', 'protocol')}/{item.get('module_id')}/{str(item.get('file_id', '')).replace(':', '_')}",
                "module_id": item.get("module_id"),
                "kind": item.get("kind"),
                "source_path": item.get("source_path", item.get("path")),
                "header_path": item.get("header_path"),
                "responsibility": item.get("responsibility"),
                "exports": item.get("exports", []),
                "exports_type_ids": item.get("exports_type_ids", []),
                "implements": item.get("implements", []),
                "imports_allowed": item.get("imports_allowed", []),
                "traceability": item.get("traceability", {}),
            }
            for item in files
            if isinstance(item, dict)
        ],
        "functions": [
            {
                **item,
                "trace_id": f"{implementation_plan.get('protocol_name', 'protocol')}/"
                f"{str(item.get('file_id', '')).replace('file:', '')}/"
                f"{str(item.get('file_id', '')).replace(':', '_')}/"
                f"{item.get('name')}",
            }
            for item in functions
            if isinstance(item, dict)
        ],
        "wire_mapping_table": implementation_plan.get("wire_mapping_table", []),
        "access_path_table": implementation_plan.get("access_path_table", []),
        "dependency_graph": implementation_plan.get("dependency_graph", {}),
        "resource_lifecycle": implementation_plan.get("resource_lifecycle", []),
        "error_strategy": implementation_plan.get("error_strategy", []),
        "generation_order": [item.get("module_id") for item in modules if isinstance(item, dict)],
    }
