from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.engineering_constraints import SCHEMA_VERSION


def validate_constraints(artifact: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_constraints_schema", f"constraints must use {SCHEMA_VERSION}", path))
    seen: set[str] = set()
    constraints = artifact.get("constraints", [])
    if not isinstance(constraints, list):
        diagnostics.append(PlanningDiagnostic("error", "invalid_constraints", "constraints must be an array", path))
        return diagnostics
    for idx, item in enumerate(constraints):
        if not isinstance(item, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_constraint", f"constraints[{idx}] must be an object", path))
            continue
        constraint_id = str(item.get("constraint_id", "")).strip()
        if not constraint_id:
            diagnostics.append(PlanningDiagnostic("error", "missing_constraint_id", f"constraints[{idx}] has no constraint_id", path))
            continue
        if constraint_id in seen:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_constraint_id", f"Duplicate constraint_id '{constraint_id}'", path))
        seen.add(constraint_id)
        for key in ("triggered_by", "affected_capabilities"):
            if not isinstance(item.get(key), list):
                diagnostics.append(PlanningDiagnostic("error", "invalid_constraint_field", f"{constraint_id}.{key} must be an array", path))
        for key in ("obligation", "severity", "rationale", "validation_rule"):
            if not str(item.get(key, "")).strip():
                diagnostics.append(PlanningDiagnostic("error", "missing_constraint_field", f"{constraint_id}.{key} is required", path))
    return diagnostics
