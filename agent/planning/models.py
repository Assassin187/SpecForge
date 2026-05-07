from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlanningDiagnostic:
    level: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class TargetProfile:
    target_role: str
    language: str
    runtime: str
    scope: str
    deployment_constraints: dict[str, Any]
    raw: dict[str, Any]

    @property
    def slug(self) -> str:
        parts = [self.target_role, self.language, self.runtime, self.scope]
        safe_parts = []
        for part in parts:
            text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(part))
            safe_parts.append(text.strip("_") or "x")
        return "__".join(safe_parts)


@dataclass(frozen=True)
class PlanningIR:
    protocol_name: str
    facts_path: Path
    target_profile: TargetProfile
    facts: dict[str, Any]
    normalized_roles: list[str]
    surface_units: list[dict[str, Any]]
    message_entries: list[dict[str, Any]]
    state_nodes: list[dict[str, Any]]
    transitions: list[dict[str, Any]]
    resource_objects: list[dict[str, Any]]
    error_matrix: list[dict[str, Any]]
    minimum_v1: dict[str, Any]
    evidence_by_id: dict[str, dict[str, Any]]
    open_questions: dict[str, list[dict[str, Any]]]
    planning_inputs: dict[str, Any]
    traceability_index: dict[str, list[str]]


@dataclass(frozen=True)
class ProtocolProfile:
    data: dict[str, Any]


@dataclass(frozen=True)
class ExpertRule:
    rule_id: str
    title: str
    description: str
    conditions: dict[str, Any]
    engineering_obligations: list[str]
    recommended_patterns: list[str]
    required_components: list[str]
    spec_impacts: list[str]


@dataclass(frozen=True)
class ExpertActivation:
    rule_id: str
    title: str
    matched: bool
    reasons: list[str]
    engineering_obligations: list[str]
    recommended_patterns: list[str]
    required_components: list[str]
    spec_impacts: list[str]
    evidence_refs: list[str]


@dataclass(frozen=True)
class CandidateArchitecture:
    candidate_id: str
    title: str
    summary: str
    modules: list[dict[str, Any]]
    thread_model: str
    component_relationships: list[str]
    strengths: list[str]
    risks: list[str]
    origin: str


@dataclass(frozen=True)
class ArchitectureScore:
    candidate_id: str
    hard_score: float
    llm_score: float
    total_score: float
    breakdown: dict[str, float]
    reasons: list[str]
    selected: bool


@dataclass(frozen=True)
class DesignDecision:
    decision_id: str
    decision_type: str
    selected_option: str
    rationale: str
    evidence_refs: list[str]
    expert_rule_ids: list[str]
    downstream_spec_impact: list[str]
    origin: str
    source_steps: list[str]


@dataclass(frozen=True)
class ImplementationPlan:
    data: dict[str, Any]


@dataclass
class PlanningResult:
    success: bool
    output_dir: Path
    diagnostics: list[PlanningDiagnostic]
    artifact_paths: dict[str, Path]

    def has_errors(self) -> bool:
        return any(diag.level == "error" for diag in self.diagnostics)


@dataclass
class VerificationResult:
    ok: bool
    diagnostics: list[PlanningDiagnostic]

