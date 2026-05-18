from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifact_io import ArtifactStore, input_record
from ..config import PlanningConfig
from ..diagnostics import PlanningDiagnostic, diagnostics_to_dict
from ..schemas.planning_run_manifest import SCHEMA_VERSION


LLM_PARTICIPATION_MATRIX = [
    {"stage": "Preflight", "llm_mode": "none", "role": "", "is_final": False, "fallback": "stop on invalid required inputs"},
    {"stage": "Facts Input Adapter / Canonical Planning IR", "llm_mode": "none", "role": "", "is_final": False, "fallback": "deterministic normalizer"},
    {"stage": "Protocol Profile", "llm_mode": "patch_generator", "role": "structured summarizer", "is_final": False, "fallback": "retry 3 then fail"},
    {"stage": "Engineering Constraint Activation", "llm_mode": "none", "role": "", "is_final": False, "fallback": "deterministic constraints"},
    {"stage": "Architecture Search", "llm_mode": "candidate_generator", "role": "architecture planner", "is_final": False, "fallback": "retry 3 then fail"},
    {"stage": "Architecture Ranking", "llm_mode": "ranker", "role": "architecture ranker", "is_final": False, "fallback": "rank valid LLM candidates"},
    {"stage": "Plan Skeleton", "llm_mode": "none", "role": "", "is_final": False, "fallback": "stop on invalid skeleton"},
    {"stage": "Module Contract Planning", "llm_mode": "patch_generator", "role": "module contract planner", "is_final": False, "fallback": "retry 3 then fail"},
    {"stage": "File Layout Planning", "llm_mode": "candidate_generator", "role": "file layout planner", "is_final": False, "fallback": "retry 3 then fail"},
    {"stage": "Function Contract Planning", "llm_mode": "primary_planner", "role": "function contract planner", "is_final": False, "fallback": "retry 3 then fail"},
    {"stage": "Dependency Derivation & Repair", "llm_mode": "repair_assistant", "role": "dependency repair assistant", "is_final": False, "fallback": "rule-derived graph; future LLM repair must retry 3 then fail"},
    {"stage": "Spec Blueprint Lowering", "llm_mode": "none", "role": "", "is_final": False, "fallback": "stop on lowering errors"},
    {"stage": "Coder-Compatible Specs Compilation", "llm_mode": "none", "role": "", "is_final": False, "fallback": "stop on compile errors"},
    {"stage": "Planning Validation Report", "llm_mode": "advisory", "role": "validator explanation assistant", "is_final": False, "fallback": "machine report; advisory text must not change pass/fail"},
]


def build_manifest(
    *,
    facts_path: Path,
    target_profile_path: Path,
    store: ArtifactStore,
    config: PlanningConfig,
    status: str,
    diagnostics: list[PlanningDiagnostic],
    artifact_paths: dict[str, Path] | None = None,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_paths = artifact_paths or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "inputs": {
            "facts": input_record(facts_path),
            "target_profile": input_record(target_profile_path),
        },
        "validated_input_paths": {
            "facts": str(facts_path) if facts_path.exists() else None,
            "target_profile": str(target_profile_path) if target_profile_path.exists() else None,
        },
        "compatibility": {
            "facts_input_format_version": config.facts_input_format_version,
            "target_profile_format_version": config.target_profile_format_version,
            "coder_output_format_version": config.coder_output_format_version,
        },
        "llm": {
            "enabled": True,
            "required": True,
            "prompt_version": config.prompt_version,
            "participation_matrix": LLM_PARTICIPATION_MATRIX,
        },
        "output_dir": str(store.output_dir),
        "artifacts": {key: str(value) for key, value in artifact_paths.items()},
        "diagnostics": diagnostics_to_dict(diagnostics),
        "failure": failure,
    }


def validate_input_paths(facts_path: Path, target_profile_path: Path) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if not facts_path.exists():
        diagnostics.append(PlanningDiagnostic("error", "missing_facts_path", "protocol_facts.json path does not exist", str(facts_path)))
    if not target_profile_path.exists():
        diagnostics.append(
            PlanningDiagnostic("error", "missing_target_profile_path", "target_profile.json path does not exist", str(target_profile_path))
        )
    return diagnostics
