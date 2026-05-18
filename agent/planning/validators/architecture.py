from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.architecture import ARCHITECTURE_CANDIDATES_SCHEMA_VERSION, SELECTED_ARCHITECTURE_SCHEMA_VERSION


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


def validate_architecture_candidates(
    candidates: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    *,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if candidates.get("schema_version") != ARCHITECTURE_CANDIDATES_SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_candidates_schema", f"architecture candidates must use {ARCHITECTURE_CANDIDATES_SCHEMA_VERSION}", path))
        return diagnostics
    required_caps = _required_capabilities(profile)
    known_constraints = _constraint_ids(constraints)
    items = candidates.get("candidates", [])
    if not isinstance(items, list) or not items:
        diagnostics.append(PlanningDiagnostic("error", "missing_architecture_candidates", "architecture candidates must be a non-empty array", path))
        return diagnostics
    for candidate_idx, candidate in enumerate(items):
        if not isinstance(candidate, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_candidate", f"candidates[{candidate_idx}] must be an object", path))
            continue
        modules = candidate.get("modules", [])
        if not isinstance(modules, list) or not modules:
            diagnostics.append(PlanningDiagnostic("error", "architecture_without_modules", f"Candidate '{candidate.get('candidate_id')}' has no modules", path))
            continue
        module_ids: set[str] = set()
        covered_caps: set[str] = set()
        for module in modules:
            if not isinstance(module, dict):
                diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_module", "architecture module must be object", path))
                continue
            module_id = str(module.get("module_id", "")).strip()
            if not module_id:
                diagnostics.append(PlanningDiagnostic("error", "missing_architecture_module_id", "architecture module missing module_id", path))
            if module_id in module_ids:
                diagnostics.append(PlanningDiagnostic("error", "duplicate_architecture_module_id", f"Duplicate module_id '{module_id}'", path))
            module_ids.add(module_id)
            owned = [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()]
            if not owned and not module.get("support_module"):
                diagnostics.append(PlanningDiagnostic("error", "architecture_module_without_capability", f"Module '{module_id}' owns no capabilities", path))
            for cap in owned:
                if cap not in required_caps:
                    diagnostics.append(PlanningDiagnostic("error", "architecture_unknown_capability", f"Module '{module_id}' owns unknown capability '{cap}'", path))
                covered_caps.add(cap)
            for dep in module.get("dependency_hints", []):
                if str(dep) and str(dep) not in module_ids:
                    # Hints can refer forward; do a second pass below.
                    pass
        for module in modules:
            if isinstance(module, dict):
                for dep in module.get("dependency_hints", []):
                    if str(dep) not in module_ids:
                        diagnostics.append(PlanningDiagnostic("error", "architecture_unknown_dependency_hint", f"Module '{module.get('module_id')}' hints unknown dependency '{dep}'", path))
        for cap in sorted(required_caps - covered_caps):
            diagnostics.append(PlanningDiagnostic("error", "architecture_uncovered_capability", f"Candidate '{candidate.get('candidate_id')}' does not cover capability '{cap}'", path))
        for constraint_id in candidate.get("constraint_ids", []):
            if str(constraint_id) not in known_constraints:
                diagnostics.append(PlanningDiagnostic("error", "architecture_unknown_constraint", f"Candidate references unknown constraint '{constraint_id}'", path))
    return diagnostics


def validate_selected_architecture(
    selected: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    *,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if selected.get("schema_version") != SELECTED_ARCHITECTURE_SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_selected_architecture_schema", f"selected architecture must use {SELECTED_ARCHITECTURE_SCHEMA_VERSION}", path))
        return diagnostics
    architecture = selected.get("architecture", {})
    return diagnostics + validate_architecture_candidates(
        {"schema_version": ARCHITECTURE_CANDIDATES_SCHEMA_VERSION, "candidates": [architecture]},
        profile,
        constraints,
        path=path,
    )

