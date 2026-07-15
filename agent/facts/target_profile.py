from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import TargetProfile


TARGET_PROFILE_SCHEMA_VERSION = "target_profile/v2alpha1"
SCOPE_MODES = {"minimal_runnable_subset", "full_protocol"}
CONFORMANCE_MODES = {"intentional_subset", "full"}


class TargetProfileError(ValueError):
    pass


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TargetProfileError(f"{field} must be a JSON object")
    return value


def _require_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TargetProfileError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_projection(data: dict[str, Any]) -> dict[str, Any]:
    deployment = data["deployment_constraints"]
    runtime_contract = data["runtime_contract"]
    return {
        "protocol_name": data["protocol_name"],
        "target_role": data["target_role"],
        "scope_policy": data["scope_policy"],
        "required_capabilities": data["required_capabilities"],
        "feature_constraints": data["feature_constraints"],
        "deployment_constraints": {
            key: deployment[key]
            for key in ("persistence", "tls_mode")
            if key in deployment
        },
        "runtime_contract": {
            key: runtime_contract[key]
            for key in ("transport",)
            if key in runtime_contract
        },
    }


def build_profile_evidence(profile: TargetProfile) -> list[dict[str, str]]:
    directives: list[tuple[str, Any]] = [("target_role", profile.data["target_role"])]
    directives.extend(
        (f"feature_constraints.{key}", value)
        for key, value in sorted(profile.data["feature_constraints"].items())
    )
    directives.extend(
        (f"deployment_constraints.{key}", profile.semantic_projection["deployment_constraints"][key])
        for key in sorted(profile.semantic_projection["deployment_constraints"])
    )
    evidence: list[dict[str, str]] = []
    for field, value in directives:
        suffix = re.sub(r"[^a-z0-9]+", "_", field.casefold()).strip("_")
        evidence_id = f"profile_{suffix}"
        evidence.append(
            {
                "evidence_id": evidence_id,
                "doc_path": str(profile.path),
                "section_hint": field,
                "chunk_id": evidence_id,
                "excerpt": json.dumps({field: value}, ensure_ascii=False, sort_keys=True),
            }
        )
    return evidence


def load_target_profile(path: str | Path, protocol_name: str | None = None) -> TargetProfile:
    profile_path = Path(path).expanduser().resolve()
    try:
        raw = profile_path.read_bytes()
    except OSError as exc:
        raise TargetProfileError(f"cannot read target profile {profile_path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TargetProfileError(f"invalid JSON in target profile {profile_path}: {exc}") from exc

    data = _require_object(data, "target profile")
    if data.get("schema_version") != TARGET_PROFILE_SCHEMA_VERSION:
        raise TargetProfileError(
            f"schema_version must be {TARGET_PROFILE_SCHEMA_VERSION!r}, got {data.get('schema_version')!r}"
        )
    declared_protocol = _require_string(data, "protocol_name")
    _require_string(data, "target_role")
    _require_string(data, "language")
    _require_string(data, "runtime")
    if protocol_name and declared_protocol.casefold() != protocol_name.strip().casefold():
        raise TargetProfileError(
            f"protocol_name mismatch: CLI requested {protocol_name!r}, profile declares {declared_protocol!r}"
        )

    scope_policy = _require_object(data.get("scope_policy"), "scope_policy")
    mode = _require_string(scope_policy, "mode")
    if mode not in SCOPE_MODES:
        raise TargetProfileError(f"scope_policy.mode must be one of {sorted(SCOPE_MODES)}")
    conformance_mode = _require_string(scope_policy, "conformance_mode")
    if conformance_mode not in CONFORMANCE_MODES:
        raise TargetProfileError(
            f"scope_policy.conformance_mode must be one of {sorted(CONFORMANCE_MODES)}"
        )
    if mode == "minimal_runnable_subset" and conformance_mode != "intentional_subset":
        raise TargetProfileError("minimal_runnable_subset requires conformance_mode intentional_subset")
    if mode == "full_protocol" and conformance_mode != "full":
        raise TargetProfileError("full_protocol requires conformance_mode full")

    capabilities = data.get("required_capabilities")
    if not isinstance(capabilities, list) or (mode == "minimal_runnable_subset" and not capabilities):
        raise TargetProfileError("required_capabilities must be a non-empty list for minimal_runnable_subset")
    capability_ids: set[str] = set()
    for index, capability in enumerate(capabilities):
        item = _require_object(capability, f"required_capabilities[{index}]")
        capability_id = _require_string(item, "capability_id")
        _require_string(item, "summary")
        if capability_id in capability_ids:
            raise TargetProfileError(f"duplicate capability_id: {capability_id}")
        capability_ids.add(capability_id)

    for field in ("feature_constraints", "deployment_constraints", "runtime_contract"):
        _require_object(data.get(field), field)

    constraints = data["feature_constraints"] | data["deployment_constraints"]
    for index, capability in enumerate(capabilities):
        requirements = capability.get("requires", {})
        requirements = _require_object(requirements, f"required_capabilities[{index}].requires")
        for constraint, required_value in requirements.items():
            if constraint in constraints and constraints[constraint] != required_value:
                raise TargetProfileError(
                    f"capability {capability['capability_id']!r} requires {constraint}={required_value!r}, "
                    f"but profile constrains it to {constraints[constraint]!r}"
                )

    projection = _semantic_projection(data)
    return TargetProfile(
        path=profile_path,
        data=data,
        sha256=hashlib.sha256(raw).hexdigest(),
        semantic_projection=projection,
        semantic_projection_sha256=_canonical_sha256(projection),
    )
