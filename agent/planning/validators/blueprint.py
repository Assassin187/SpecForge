from __future__ import annotations

import json
from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.spec_blueprint import SCHEMA_VERSION


def validate_spec_blueprint(blueprint: dict[str, Any], implementation_plan: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if blueprint.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_spec_blueprint_schema", f"spec_blueprint must use {SCHEMA_VERSION}", path))
    plan_modules = {str(item.get("module_id", "")) for item in implementation_plan.get("module_artifacts", []) if isinstance(item, dict)}
    plan_files = {str(item.get("file_id", "")) for item in implementation_plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)}
    plan_functions = {str(item.get("function_id", "")) for item in implementation_plan.get("function_contracts", []) if isinstance(item, dict)}
    blueprint_modules = {str(item.get("module_id", "")) for item in blueprint.get("modules", []) if isinstance(item, dict)}
    blueprint_files = {str(item.get("file_id", "")) for item in blueprint.get("files", []) if isinstance(item, dict)}
    blueprint_functions = {str(item.get("function_id", "")) for item in blueprint.get("functions", []) if isinstance(item, dict)}
    for missing in sorted(plan_modules - blueprint_modules):
        diagnostics.append(PlanningDiagnostic("error", "blueprint_missing_module", f"Blueprint omitted plan module '{missing}'", path))
    for added in sorted(blueprint_modules - plan_modules):
        diagnostics.append(PlanningDiagnostic("error", "blueprint_added_module", f"Blueprint added module '{added}'", path))
    for added in sorted(blueprint_files - plan_files):
        diagnostics.append(PlanningDiagnostic("error", "blueprint_added_file", f"Blueprint added file '{added}'", path))
    for added in sorted(blueprint_functions - plan_functions):
        diagnostics.append(PlanningDiagnostic("error", "blueprint_added_function", f"Blueprint added function '{added}'", path))
    for key in ("wire_mapping_table", "access_path_table", "dependency_graph"):
        if _stable(blueprint.get(key)) != _stable(implementation_plan.get(key)):
            diagnostics.append(PlanningDiagnostic("error", f"blueprint_changed_{key}", f"Blueprint changed implementation_plan.{key}", path))
    for key in ("resource_lifecycle", "error_strategy"):
        if key in blueprint and _stable(blueprint.get(key)) != _stable(implementation_plan.get(key)):
            diagnostics.append(PlanningDiagnostic("error", f"blueprint_changed_{key}", f"Blueprint changed implementation_plan.{key}", path))
    return diagnostics


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
