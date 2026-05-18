from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.protocol_profile import (
    ALLOWED_INTENSITY,
    ALLOWED_STATEFULNESS,
    ALLOWED_TIMING_MODELS,
    ALLOWED_TRANSPORT_SHAPES,
    SCHEMA_VERSION,
)


def _field_value(profile: dict[str, Any], key: str) -> str:
    value = profile.get(key, {})
    if isinstance(value, dict):
        return str(value.get("value", ""))
    return str(value)


def validate_protocol_profile(profile: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if profile.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(PlanningDiagnostic("error", "invalid_profile_schema", f"profile must use {SCHEMA_VERSION}", path))
    enum_checks = {
        "transport_shape": ALLOWED_TRANSPORT_SHAPES,
        "statefulness": ALLOWED_STATEFULNESS,
        "routing_intensity": ALLOWED_INTENSITY,
        "resource_intensity": ALLOWED_INTENSITY,
        "timing_model": ALLOWED_TIMING_MODELS,
    }
    for key, allowed in enum_checks.items():
        value = _field_value(profile, key)
        if value not in allowed:
            diagnostics.append(PlanningDiagnostic("error", "invalid_profile_enum", f"{key} has unsupported value '{value}'", path))
    capabilities = profile.get("required_capabilities", [])
    if not isinstance(capabilities, list) or not capabilities:
        diagnostics.append(PlanningDiagnostic("error", "missing_required_capabilities", "required_capabilities must be non-empty", path))
    else:
        seen: set[str] = set()
        for idx, item in enumerate(capabilities):
            if not isinstance(item, dict):
                diagnostics.append(PlanningDiagnostic("error", "invalid_capability", f"required_capabilities[{idx}] must be an object", path))
                continue
            cap_id = str(item.get("capability_id", "")).strip()
            if not cap_id:
                diagnostics.append(PlanningDiagnostic("error", "missing_capability_id", f"required_capabilities[{idx}] has no capability_id", path))
                continue
            if cap_id in seen:
                diagnostics.append(PlanningDiagnostic("error", "duplicate_capability_id", f"Duplicate capability_id '{cap_id}'", path))
            seen.add(cap_id)
            if not item.get("source_fact_ids") and not item.get("target_directive_ids"):
                diagnostics.append(
                    PlanningDiagnostic("warning", "capability_without_source", f"Capability '{cap_id}' has no source fact or target directive", path)
                )
    return diagnostics
