from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ..artifact_io import read_json
from ..diagnostics import PlanningDiagnostic
from ..models import TargetProfile
from ..schemas.planning_ir import TARGET_DIRECTIVES_SCHEMA_VERSION


REQUIRED_TARGET_PROFILE_FIELDS = {
    "target_role",
    "language",
    "runtime",
    "scope",
    "deployment_constraints",
}


def _safe_id_part(value: Any) -> str:
    text = str(value).strip().lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "x"


def target_directive_id(key: str) -> str:
    return f"target:{_safe_id_part(key)}"


def load_target_profile(path: str | Path) -> tuple[TargetProfile | None, list[PlanningDiagnostic]]:
    diagnostics: list[PlanningDiagnostic] = []
    target_path = Path(path)
    try:
        raw = read_json(target_path)
    except Exception as exc:  # noqa: BLE001
        return None, [PlanningDiagnostic("error", "invalid_target_profile_json", str(exc), str(target_path))]

    for key in sorted(REQUIRED_TARGET_PROFILE_FIELDS):
        if key not in raw:
            diagnostics.append(
                PlanningDiagnostic("error", "missing_target_profile_field", f"Missing target profile field '{key}'", str(target_path))
            )

    constraints = raw.get("deployment_constraints", {})
    if not isinstance(constraints, dict):
        diagnostics.append(
            PlanningDiagnostic("error", "invalid_target_profile_field", "deployment_constraints must be an object", str(target_path))
        )
        constraints = {}

    role_aliases_raw = raw.get("role_aliases", {})
    role_aliases: dict[str, str] = {}
    if role_aliases_raw is None:
        role_aliases_raw = {}
    if not isinstance(role_aliases_raw, dict):
        diagnostics.append(
            PlanningDiagnostic("error", "invalid_role_aliases", "role_aliases must be an object when present", str(target_path))
        )
    else:
        for key, value in role_aliases_raw.items():
            source = str(key).strip().lower()
            target = str(value).strip().lower()
            if not source or not target:
                diagnostics.append(
                    PlanningDiagnostic("error", "invalid_role_alias", "role_aliases keys and values must be non-empty", str(target_path))
                )
                continue
            role_aliases[source] = target

    if any(item.level == "error" for item in diagnostics):
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


def build_target_directives(target_profile: TargetProfile) -> dict[str, Any]:
    directives: dict[str, Any] = {}
    for key, value in target_profile.raw.items():
        directives[str(key)] = {
            "directive_id": target_directive_id(str(key)),
            "value": deepcopy(value),
        }
    return {
        "schema_version": TARGET_DIRECTIVES_SCHEMA_VERSION,
        "source": "target_profile.json",
        "directives": directives,
    }
