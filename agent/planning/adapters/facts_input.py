from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ..artifact_io import read_json
from ..config import FACTS_INPUT_FORMAT_VERSION, TARGET_PROFILE_FORMAT_VERSION
from ..diagnostics import PlanningDiagnostic
from ..models import TargetProfile
from ..schemas.planning_ir import FACT_SECTION_EXCLUSIONS, REQUIRED_FACT_SECTIONS, SCHEMA_VERSION
from .target_profile import build_target_directives


IMPORTANT_FACT_LISTS = {
    "message_model.surface_catalog",
    "message_model.message_or_command_entries",
    "state_model.state_nodes",
    "state_model.transitions",
    "routing_model.dispatch_keys",
    "resource_model.resource_objects",
    "error_and_limits.error_matrix",
    "minimum_v1.must_support_surface",
}


def _safe_id_part(value: Any) -> str:
    text = str(value).strip().lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "x"


def _fact_id_for_path(path: str) -> str:
    normalized = path.replace("[", ".").replace("]", "")
    return f"fact:{_safe_id_part(normalized)}"


def _path_join(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _looks_like_fact(node: dict[str, Any]) -> bool:
    meaningful_keys = {
        "name",
        "summary",
        "condition",
        "value",
        "value_or_rule",
        "from_state",
        "to_state",
        "surface_unit",
        "actor",
        "kind",
        "scope",
        "capability",
        "syntax_or_layout",
        "required_action",
        "direction",
        "trigger",
        "purpose",
    }
    return bool(meaningful_keys.intersection(node))


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_fact_tree(
    value: Any,
    *,
    path: str,
    evidence_ids: set[str],
    normalization_index: dict[str, Any],
    unresolved: list[dict[str, Any]],
) -> Any:
    if isinstance(value, list):
        return [
            _normalize_fact_tree(
                item,
                path=f"{path}.{idx}",
                evidence_ids=evidence_ids,
                normalization_index=normalization_index,
                unresolved=unresolved,
            )
            for idx, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, child in value.items():
        normalized[str(key)] = _normalize_fact_tree(
            child,
            path=_path_join(path, str(key)),
            evidence_ids=evidence_ids,
            normalization_index=normalization_index,
            unresolved=unresolved,
        )

    refs = _strings(normalized.get("evidence_refs"))
    should_index = bool(refs) or _looks_like_fact(normalized)
    if should_index:
        fact_id = str(normalized.get("fact_id") or _fact_id_for_path(path))
        normalized["fact_id"] = fact_id
        normalization_index.setdefault("fact_id_by_path", {})[path] = fact_id
        normalization_index.setdefault("evidence_refs_by_fact_id", {})[fact_id] = refs

        if not refs:
            unresolved.append({"kind": "missing_evidence", "fact_id": fact_id, "path": path, "severity": "warning"})
        else:
            missing = sorted(ref for ref in refs if ref not in evidence_ids)
            if missing:
                unresolved.append(
                    {
                        "kind": "missing_evidence_reference",
                        "fact_id": fact_id,
                        "path": path,
                        "missing_evidence_refs": missing,
                        "severity": "warning",
                    }
                )
    return normalized


def _build_evidence(raw: dict[str, Any], diagnostics: list[PlanningDiagnostic], path: Path) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    raw_entries = raw.get("evidence_index", [])
    if not isinstance(raw_entries, list):
        diagnostics.append(PlanningDiagnostic("error", "invalid_evidence_index", "evidence_index must be an array", str(path)))
        return evidence
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            diagnostics.append(PlanningDiagnostic("warning", "invalid_evidence_entry", f"evidence_index[{idx}] is not an object", str(path)))
            continue
        evidence_id = str(entry.get("evidence_id", "")).strip()
        if not evidence_id:
            diagnostics.append(PlanningDiagnostic("warning", "missing_evidence_id", f"evidence_index[{idx}] has no evidence_id", str(path)))
            continue
        evidence[evidence_id] = deepcopy(entry)
    return evidence


def _classify_open_questions(raw: Any) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return questions
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        questions.append(
            {
                "unresolved_fact_id": f"unresolved:open_question:{idx}",
                "source": "protocol_facts.open_questions",
                "question": str(item.get("question", "")),
                "question_type": str(item.get("question_type", "")),
                "blocking_impact": str(item.get("blocking_impact", "")),
                "evidence_refs": _strings(item.get("evidence_refs")),
                "raw": deepcopy(item),
            }
        )
    return questions


def validate_raw_facts(raw: dict[str, Any], path: Path) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if raw.get("schema_version") != FACTS_INPUT_FORMAT_VERSION:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "invalid_facts_schema_version",
                f"protocol_facts.json must use schema_version {FACTS_INPUT_FORMAT_VERSION}",
                str(path),
            )
        )
    for key in sorted(REQUIRED_FACT_SECTIONS):
        if key not in raw:
            diagnostics.append(PlanningDiagnostic("error", "missing_fact_section", f"Missing facts section '{key}'", str(path)))

    message_model = raw.get("message_model", {})
    if isinstance(message_model, dict):
        if not isinstance(message_model.get("surface_catalog"), list) or not message_model.get("surface_catalog"):
            diagnostics.append(PlanningDiagnostic("error", "missing_message_surface", "message_model.surface_catalog must be non-empty", str(path)))
        if not isinstance(message_model.get("message_or_command_entries"), list) or not message_model.get("message_or_command_entries"):
            diagnostics.append(
                PlanningDiagnostic("warning", "missing_message_entries", "message_model.message_or_command_entries is empty or missing", str(path))
            )

    transport = raw.get("transport", {})
    if isinstance(transport, dict):
        if not transport.get("network_stack") and not transport.get("connection_model"):
            diagnostics.append(
                PlanningDiagnostic("error", "missing_transport_shape", "transport must provide network_stack or connection_model", str(path))
            )

    if "planning_inputs" not in raw:
        diagnostics.append(
            PlanningDiagnostic("warning", "missing_planning_inputs", "planning_inputs is absent; will continue using semantic categories", str(path))
        )
    return diagnostics


def build_planning_ir(facts_path: str | Path, target_profile: TargetProfile) -> tuple[dict[str, Any] | None, list[PlanningDiagnostic]]:
    path = Path(facts_path)
    diagnostics: list[PlanningDiagnostic] = []
    try:
        raw = read_json(path)
    except Exception as exc:  # noqa: BLE001
        return None, [PlanningDiagnostic("error", "invalid_facts_json", str(exc), str(path))]

    diagnostics.extend(validate_raw_facts(raw, path))
    evidence = _build_evidence(raw, diagnostics, path)
    if any(item.level == "error" for item in diagnostics):
        return None, diagnostics

    normalization_index: dict[str, Any] = {
        "fact_id_by_path": {},
        "evidence_refs_by_fact_id": {},
    }
    unresolved: list[dict[str, Any]] = []
    protocol_facts: dict[str, Any] = {}
    evidence_ids = set(evidence)
    for key, value in raw.items():
        if key in FACT_SECTION_EXCLUSIONS:
            continue
        protocol_facts[key] = _normalize_fact_tree(
            deepcopy(value),
            path=str(key),
            evidence_ids=evidence_ids,
            normalization_index=normalization_index,
            unresolved=unresolved,
        )

    message_model = raw.get("message_model", {}) if isinstance(raw.get("message_model"), dict) else {}
    field_id_by_message_and_name: dict[str, dict[str, str]] = {}
    entries = message_model.get("message_or_command_entries", [])
    if isinstance(entries, list):
        for entry_idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("name") or entry.get("surface_unit") or f"entry_{entry_idx}").strip()
            fields = entry.get("fields", [])
            if not isinstance(fields, list):
                continue
            field_map: dict[str, str] = {}
            for field_idx, field in enumerate(fields):
                if not isinstance(field, dict):
                    continue
                field_name = str(field.get("name", "")).strip()
                if field_name:
                    field_map[field_name] = _fact_id_for_path(f"message_model.message_or_command_entries.{entry_idx}.fields.{field_idx}")
            if field_map:
                field_id_by_message_and_name[entry_name] = field_map
    normalization_index["field_id_by_message_and_name"] = field_id_by_message_and_name

    for list_path in IMPORTANT_FACT_LISTS:
        section, _, key = list_path.rpartition(".")
        cursor: Any = raw
        for part in section.split("."):
            cursor = cursor.get(part, {}) if isinstance(cursor, dict) else {}
        if not isinstance(cursor, dict) or not cursor.get(key):
            unresolved.append({"kind": "missing_or_empty_fact_list", "path": list_path, "severity": "warning"})

    protocol_name = str(raw.get("protocol_meta", {}).get("protocol_name", "protocol"))
    target_directives = build_target_directives(target_profile)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "input_compatibility": {
            "facts_agent_format": FACTS_INPUT_FORMAT_VERSION,
            "target_profile_format": TARGET_PROFILE_FORMAT_VERSION,
        },
        "protocol_name": protocol_name,
        "source_artifacts": {
            "protocol_facts": str(path),
            "target_profile": "target_profile.json",
        },
        "protocol_facts": protocol_facts,
        "target_directives": target_directives,
        "normalization_index": normalization_index,
        "evidence": evidence,
        "unresolved_facts": [*unresolved, *_classify_open_questions(raw.get("open_questions", []))],
    }
    return artifact, diagnostics
