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


@dataclass(frozen=True)
class BlueprintType:
    name: str
    kind: str
    role: str
    visibility: str
    type_spec: dict[str, Any] | None = None
    evidence_refs: list[str] = field(default_factory=list)
    decision_refs: list[str] = field(default_factory=list)
    profile_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BlueprintFunction:
    trace_id: str
    module: str
    file_trace_id: str
    name: str
    function_type: str
    signature: dict[str, Any]
    role: str
    visibility: str
    rely: dict[str, Any] = field(default_factory=dict)
    logic: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    wire_mapping: list[dict[str, Any]] = field(default_factory=list)
    access_paths: list[dict[str, Any]] = field(default_factory=list)
    call_contracts: list[dict[str, Any]] = field(default_factory=list)
    test_vectors: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    decision_refs: list[str] = field(default_factory=list)
    profile_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BlueprintFile:
    trace_id: str
    module: str
    lang: str
    role: str
    header_path: str
    source_path: str
    header_dependencies: list[str] = field(default_factory=list)
    source_dependencies: list[str] = field(default_factory=list)
    header_data: list[dict[str, Any]] = field(default_factory=list)
    source_data: list[dict[str, Any]] = field(default_factory=list)
    header_interfaces: list[dict[str, Any]] = field(default_factory=list)
    source_interfaces: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    decision_refs: list[str] = field(default_factory=list)
    profile_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BlueprintModule:
    name: str
    role: str
    dependencies: list[str]
    files: list[str]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    decision_refs: list[str] = field(default_factory=list)
    profile_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpecBlueprint:
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
