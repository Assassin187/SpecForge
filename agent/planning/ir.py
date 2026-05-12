from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import PlanningDiagnostic, PlanningIR, TargetProfile


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_target_profile(path: str | Path) -> tuple[TargetProfile | None, list[PlanningDiagnostic]]:
    diagnostics: list[PlanningDiagnostic] = []
    raw = load_json(path)
    required = ["target_role", "language", "runtime", "scope", "deployment_constraints"]
    for key in required:
        if key not in raw:
            diagnostics.append(PlanningDiagnostic("error", "missing_target_profile_field", f"Missing '{key}' in target profile", str(path)))
    constraints = raw.get("deployment_constraints", {})
    if not isinstance(constraints, dict):
        diagnostics.append(PlanningDiagnostic("error", "invalid_target_profile_field", "deployment_constraints must be an object", str(path)))
        constraints = {}
    aliases_raw = raw.get("role_aliases", {})
    role_aliases: dict[str, str] = {}
    if aliases_raw is None:
        aliases_raw = {}
    if not isinstance(aliases_raw, dict):
        diagnostics.append(PlanningDiagnostic("error", "invalid_role_alias", "role_aliases must be an object mapping source role to normalized role", str(path)))
    else:
        for key, value in aliases_raw.items():
            source = str(key).strip().lower()
            target = str(value).strip().lower()
            if not source or not target:
                diagnostics.append(PlanningDiagnostic("error", "invalid_role_alias", "role_aliases keys and values must be non-empty strings", str(path)))
                continue
            role_aliases[source] = target
    if diagnostics:
        return None, diagnostics
    return (
        TargetProfile(
            target_role=str(raw.get("target_role", "")),
            language=str(raw.get("language", "")),
            runtime=str(raw.get("runtime", "")),
            scope=str(raw.get("scope", "")),
            deployment_constraints=constraints,
            role_aliases=role_aliases,
            raw=raw,
        ),
        diagnostics,
    )


def _normalize_roles(facts: dict[str, Any], target_profile: TargetProfile) -> list[str]:
    roles = []
    for item in facts.get("interaction_model", {}).get("roles", []):
        if isinstance(item, dict) and item.get("name"):
            roles.append(str(item["name"]).lower())
    roles.append(target_profile.target_role.lower())
    deduped: list[str] = []
    seen = set()
    for role in roles:
        normalized = target_profile.role_aliases.get(role, role)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def _classify_open_questions(open_questions: list[dict[str, Any]], minimum_v1_surface: set[str]) -> dict[str, list[dict[str, Any]]]:
    result = {"blocking": [], "assumable": [], "deferrable": []}
    for item in open_questions:
        if not isinstance(item, dict):
            continue
        impact = str(item.get("blocking_impact", "low")).lower()
        question_type = str(item.get("question_type", "")).lower()
        text = str(item.get("question", ""))
        if impact == "high" or any(name and name in text for name in minimum_v1_surface):
            result["blocking"].append(item)
        elif question_type == "implementation_policy_needed" or impact == "low":
            result["assumable"].append(item)
        else:
            result["deferrable"].append(item)
    return result


def _build_traceability_index(facts: dict[str, Any]) -> dict[str, list[str]]:
    traceability: dict[str, list[str]] = {}
    for section_name in (
        "transport",
        "interaction_model",
        "message_model",
        "state_model",
        "routing_model",
        "resource_model",
        "error_and_limits",
        "minimum_v1",
    ):
        section = facts.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, dict) and isinstance(value.get("evidence_refs"), list):
                traceability[f"{section_name}.{key}"] = [str(item) for item in value.get("evidence_refs", [])]
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict) and isinstance(item.get("evidence_refs"), list):
                        traceability[f"{section_name}.{key}[{idx}]"] = [str(ref) for ref in item.get("evidence_refs", [])]
    return traceability


def build_planning_ir(facts_path: str | Path, target_profile: TargetProfile) -> tuple[PlanningIR | None, list[PlanningDiagnostic]]:
    diagnostics: list[PlanningDiagnostic] = []
    raw = load_json(facts_path)
    required = [
        "transport",
        "interaction_model",
        "message_model",
        "state_model",
        "routing_model",
        "resource_model",
        "error_and_limits",
        "minimum_v1",
        "evidence_index",
        "open_questions",
    ]
    for key in required:
        if key not in raw:
            diagnostics.append(PlanningDiagnostic("error", "missing_fact_section", f"Missing facts section '{key}'", str(facts_path)))
    if diagnostics:
        return None, diagnostics
    evidence_by_id = {}
    for entry in raw.get("evidence_index", []):
        if isinstance(entry, dict) and entry.get("evidence_id"):
            evidence_by_id[str(entry["evidence_id"])] = entry
    minimum_v1_surface = {
        str(item.get("name"))
        for item in raw.get("minimum_v1", {}).get("must_support_surface", [])
        if isinstance(item, dict) and item.get("name")
    }
    planning_ir = PlanningIR(
        protocol_name=str(raw.get("protocol_meta", {}).get("protocol_name", "protocol")),
        facts_path=Path(facts_path),
        target_profile=target_profile,
        facts=raw,
        normalized_roles=_normalize_roles(raw, target_profile),
        surface_units=list(raw.get("message_model", {}).get("surface_catalog", [])),
        message_entries=list(raw.get("message_model", {}).get("message_or_command_entries", [])),
        state_nodes=list(raw.get("state_model", {}).get("state_nodes", [])),
        transitions=list(raw.get("state_model", {}).get("transitions", [])),
        resource_objects=list(raw.get("resource_model", {}).get("resource_objects", [])),
        error_matrix=list(raw.get("error_and_limits", {}).get("error_matrix", [])),
        minimum_v1=dict(raw.get("minimum_v1", {})),
        evidence_by_id=evidence_by_id,
        open_questions=_classify_open_questions(list(raw.get("open_questions", [])), minimum_v1_surface),
        planning_inputs=dict(raw.get("planning_inputs", {})),
        traceability_index=_build_traceability_index(raw),
    )
    return planning_ir, diagnostics
