from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.architecture import (
    ARCHITECTURE_CANDIDATES_SCHEMA_VERSION,
    ARCHITECTURE_RANKING_SCHEMA_VERSION,
    SELECTED_ARCHITECTURE_SCHEMA_VERSION,
)


RANKING_DIMENSIONS = {
    "capability_coverage",
    "constraint_satisfaction",
    "cohesion",
    "coupling",
    "acyclicity",
    "state_ownership_clarity",
    "testability",
    "implementation_simplicity",
    "target_scope_fit",
}


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


def _candidate_ids(candidates: dict[str, Any]) -> set[str]:
    return {
        str(item.get("candidate_id", "")).strip()
        for item in candidates.get("candidates", [])
        if isinstance(item, dict) and str(item.get("candidate_id", "")).strip()
    }


def _has_cycle(edges: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in edges.get(node, []):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


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
        dependency_edges: dict[str, list[str]] = {}
        if candidate.get("module_graph_hints"):
            diagnostics.append(PlanningDiagnostic("error", "architecture_module_graph_hints_forbidden", f"Candidate '{candidate.get('candidate_id')}' must leave module_graph_hints empty", path))
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
            dependency_edges[module_id] = [str(dep) for dep in module.get("dependency_hints", []) if str(dep).strip()]
        for module in modules:
            if isinstance(module, dict):
                module_id = str(module.get("module_id", "")).strip()
                for dep in module.get("dependency_hints", []):
                    dep_id = str(dep).strip()
                    if dep_id not in module_ids:
                        diagnostics.append(PlanningDiagnostic("error", "architecture_unknown_dependency_hint", f"Module '{module.get('module_id')}' hints unknown dependency '{dep}'", path))
                    if dep_id and dep_id == module_id:
                        diagnostics.append(PlanningDiagnostic("error", "architecture_self_dependency_hint", f"Module '{module_id}' must not depend on itself", path))
        if _has_cycle(dependency_edges):
            diagnostics.append(PlanningDiagnostic("error", "architecture_dependency_cycle", f"Candidate '{candidate.get('candidate_id')}' has cyclic dependency hints", path))
        for cap in sorted(required_caps - covered_caps):
            diagnostics.append(PlanningDiagnostic("error", "architecture_uncovered_capability", f"Candidate '{candidate.get('candidate_id')}' does not cover capability '{cap}'", path))
        for constraint_id in candidate.get("constraint_ids", []):
            if str(constraint_id) not in known_constraints:
                diagnostics.append(PlanningDiagnostic("error", "architecture_unknown_constraint", f"Candidate references unknown constraint '{constraint_id}'", path))
    return diagnostics


def validate_architecture_ranking(
    ranking: dict[str, Any],
    candidates: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    *,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if ranking.get("schema_version") != ARCHITECTURE_RANKING_SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_ranking_schema", f"architecture ranking must use {ARCHITECTURE_RANKING_SCHEMA_VERSION}", path))
        return diagnostics
    if "candidates" in ranking:
        diagnostics.append(PlanningDiagnostic("error", "ranking_modified_candidates", "architecture ranking must not include candidate content", path))
    valid_candidate_ids = _candidate_ids(candidates)
    scores = ranking.get("scores", [])
    if not isinstance(scores, list) or not scores:
        diagnostics.append(PlanningDiagnostic("error", "missing_architecture_scores", "architecture ranking must include scores", path))
        return diagnostics
    seen: set[str] = set()
    for item in scores:
        if not isinstance(item, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_score", "architecture score must be an object", path))
            continue
        candidate_id = str(item.get("candidate_id", "")).strip()
        if candidate_id not in valid_candidate_ids:
            diagnostics.append(PlanningDiagnostic("error", "unknown_ranked_architecture", f"Ranking references unknown candidate '{candidate_id}'", path))
        if candidate_id in seen:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_architecture_score", f"Duplicate score for candidate '{candidate_id}'", path))
        seen.add(candidate_id)
        dimensions = item.get("dimension_scores", {})
        if not isinstance(dimensions, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_dimension_scores", f"Candidate '{candidate_id}' has invalid dimension_scores", path))
            continue
        missing_dimensions = sorted(RANKING_DIMENSIONS - set(str(key) for key in dimensions))
        if missing_dimensions:
            diagnostics.append(PlanningDiagnostic("error", "missing_architecture_score_dimensions", f"Candidate '{candidate_id}' is missing dimensions {missing_dimensions}", path))
        for key, value in dimensions.items():
            try:
                score = float(value)
            except (TypeError, ValueError):
                diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_score_value", f"Dimension '{key}' is not numeric", path))
                continue
            if score < 0 or score > 10:
                diagnostics.append(PlanningDiagnostic("error", "invalid_architecture_score_range", f"Dimension '{key}' must be between 0 and 10", path))
    missing_scores = sorted(valid_candidate_ids - seen)
    if missing_scores:
        diagnostics.append(PlanningDiagnostic("error", "missing_architecture_candidate_score", f"Ranking omitted candidates {missing_scores}", path))
    selected_id = str(ranking.get("selected_candidate_id", "")).strip()
    if selected_id not in valid_candidate_ids:
        diagnostics.append(PlanningDiagnostic("error", "unknown_selected_architecture", f"Ranking selected unknown candidate '{selected_id}'", path))
    diagnostics.extend(validate_architecture_candidates(candidates, profile, constraints, path=path))
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
