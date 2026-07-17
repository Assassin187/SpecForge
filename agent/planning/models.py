from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str
    path: str | None = None
    owner_layer: str | None = None
    authoritative_stage: str | None = None
    recovery_action: str | None = None


@dataclass(frozen=True)
class NormalizedCharacteristics:
    protocol_name: str
    protocol_slug: str
    spec_version: str
    target_roles: list[str]
    transport_shape: str
    connection_model: str
    interaction_models: list[str]
    role_model: str
    statefulness: str
    framing_model: str
    routing_required: bool
    resource_ownership: list[str]
    timer_requirements: list[str]
    error_semantics: list[str]
    minimum_scope: list[str]
    deferred_features: list[str]
    fact_refs: list[str]


@dataclass(frozen=True)
class EngineeringRule:
    rule_id: str
    name: str
    trigger: str
    constraints: list[str]
    fact_refs: list[str]


@dataclass(frozen=True)
class OpenAssumption:
    assumption_id: str
    missing_or_ambiguous: str
    conservative_assumption: str
    engineering_impact: str
    allowed_in_final_specs: bool
    follow_up: str
    fact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EngineeringDecision:
    decision_id: str
    content: str
    supporting_fact_refs: list[str]
    activated_rule_refs: list[str]
    rationale: str
    affected_artifacts: list[str]


@dataclass(frozen=True)
class PlanningResult:
    output_root: Path
    specs_root: Path | None
    planning_root: Path
    candidate_root: Path | None
    manifest_path: Path
    diagnostics: list[Diagnostic]
    run_status: str

    @property
    def candidate_materialized(self) -> bool:
        return self.specs_root is not None

    @property
    def success(self) -> bool:
        return self.run_status == "completed_with_qualified_specs" and self.candidate_materialized and not any(
            diag.level == "error" and diag.code.startswith("coder_") for diag in self.diagnostics
        )


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
