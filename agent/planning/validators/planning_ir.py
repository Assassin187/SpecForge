from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.planning_ir import SCHEMA_VERSION


TARGET_FIELDS = {"target_profile", "target_role", "language", "runtime", "deployment_constraints", "scope"}


def validate_planning_ir(artifact: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_planning_ir_schema", f"planning_ir must use {SCHEMA_VERSION}", path))
    protocol_facts = artifact.get("protocol_facts", {})
    if not isinstance(protocol_facts, dict):
        diagnostics.append(PlanningDiagnostic("error", "invalid_protocol_facts", "protocol_facts must be an object", path))
        protocol_facts = {}
    for key in sorted(TARGET_FIELDS):
        if key in protocol_facts:
            diagnostics.append(
                PlanningDiagnostic("error", "target_directive_mixed_into_protocol_facts", f"protocol_facts contains target field '{key}'", path)
            )
    target_directives = artifact.get("target_directives", {})
    if not isinstance(target_directives, dict) or not isinstance(target_directives.get("directives"), dict):
        diagnostics.append(PlanningDiagnostic("error", "missing_target_directives", "target_directives.directives is required", path))

    evidence = artifact.get("evidence", {})
    evidence_ids = set(evidence) if isinstance(evidence, dict) else set()
    normalization = artifact.get("normalization_index", {})
    refs_by_fact = normalization.get("evidence_refs_by_fact_id", {}) if isinstance(normalization, dict) else {}
    if not isinstance(refs_by_fact, dict):
        diagnostics.append(PlanningDiagnostic("error", "invalid_fact_evidence_index", "evidence_refs_by_fact_id must be an object", path))
        return diagnostics
    for fact_id, refs in refs_by_fact.items():
        if not str(fact_id).strip():
            diagnostics.append(PlanningDiagnostic("error", "missing_fact_id", "Indexed fact id is empty", path))
        if not isinstance(refs, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_fact_evidence_refs", f"{fact_id} refs must be a list", path))
            continue
        missing = sorted({str(ref) for ref in refs if str(ref) not in evidence_ids})
        if missing:
            diagnostics.append(
                PlanningDiagnostic("warning", "unknown_fact_evidence_ref", f"{fact_id} references missing evidence: {', '.join(missing)}", path)
            )
    return diagnostics
