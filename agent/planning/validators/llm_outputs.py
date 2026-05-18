from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..schemas.protocol_profile import ALLOWED_INTENSITY, ALLOWED_STATEFULNESS


REVIEWED_PROFILE_FIELDS = (
    "interaction_model",
    "statefulness",
    "routing_intensity",
    "resource_intensity",
    "failure_semantics",
)

ALLOWED_PROFILE_PATCH_KEYS = {
    "interaction_model",
    "statefulness",
    "routing_intensity",
    "resource_intensity",
    "failure_semantics",
    "capability_additions",
}

ALLOWED_PROFILE_REVIEW_DECISIONS = {"keep", "replace", "uncertain"}
ALLOWED_INTERACTION_MODELS = {
    "request_response",
    "streaming",
    "pubsub",
    "handshake",
    "command_response",
    "event_notification",
    "framed_message",
    "mixed",
    "unknown",
}
ALLOWED_STATEFULNESS_PATCH_VALUES = ALLOWED_STATEFULNESS | {
    "connection_stateful",
    "session_stateful",
    "transaction_stateful",
    "protocol_state_machine",
    "mixed",
}
ALLOWED_INTENSITY_PATCH_VALUES = ALLOWED_INTENSITY | {"none"}
ALLOWED_FAILURE_SEMANTICS_PATCH_VALUES = {
    "simple_error",
    "recoverable_error",
    "fatal_error",
    "mixed_recoverability",
    "timeout_sensitive",
    "retryable",
    "connection_closing",
    "unknown",
    "close_connection_on_protocol_error",
    "reply_with_error",
    "mixed",
}


def _field_value(profile: dict[str, Any], key: str) -> str:
    field = profile.get(key, {})
    if isinstance(field, dict):
        return str(field.get("value", ""))
    return str(field)


def _known_support_ids(planning_ir: dict[str, Any] | None, profile: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    if isinstance(planning_ir, dict):
        evidence = planning_ir.get("evidence", {})
        if isinstance(evidence, dict):
            known.update(str(key) for key in evidence)
        normalization = planning_ir.get("normalization_index", {})
        if isinstance(normalization, dict):
            fact_by_path = normalization.get("fact_id_by_path", {})
            if isinstance(fact_by_path, dict):
                known.update(str(value) for value in fact_by_path.values())
        directives = planning_ir.get("target_directives", {}).get("directives", {})
        if isinstance(directives, dict):
            for item in directives.values():
                if isinstance(item, dict) and item.get("directive_id"):
                    known.add(str(item["directive_id"]))
    for value in profile.values():
        if isinstance(value, dict):
            for key in ("source_fact_ids", "target_directive_ids", "evidence_refs"):
                refs = value.get(key, [])
                if isinstance(refs, list):
                    known.update(str(ref) for ref in refs)
    for item in profile.get("required_capabilities", []):
        if isinstance(item, dict):
            for key in ("source_fact_ids", "target_directive_ids", "evidence_refs"):
                refs = item.get(key, [])
                if isinstance(refs, list):
                    known.update(str(ref) for ref in refs)
    return {item for item in known if item}


def _allowed_patch_values(field: str) -> set[str] | None:
    if field == "interaction_model":
        return ALLOWED_INTERACTION_MODELS
    if field == "statefulness":
        return ALLOWED_STATEFULNESS_PATCH_VALUES
    if field in {"routing_intensity", "resource_intensity"}:
        return ALLOWED_INTENSITY_PATCH_VALUES
    if field == "failure_semantics":
        return ALLOWED_FAILURE_SEMANTICS_PATCH_VALUES
    return None


def _missing_capability_ids(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    result: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            cap_id = str(item.get("capability_id", "")).strip()
        else:
            cap_id = str(item).strip()
        if cap_id:
            result.add(cap_id)
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def validate_protocol_profile_patch_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    *,
    planning_ir: dict[str, Any] | None = None,
    path: str | None = None,
) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if candidate.get("schema_version") != "protocol_profile_patch_candidate/v1":
        diagnostics.append(PlanningDiagnostic("error", "invalid_profile_patch_schema", "profile patch must use protocol_profile_patch_candidate/v1", path))
        return diagnostics
    for forbidden_key in ("output_schema", "expected_response"):
        if forbidden_key in candidate:
            diagnostics.append(PlanningDiagnostic("error", "forbidden_profile_patch_key", f"LLM may not return '{forbidden_key}'", path))
    for required_key in ("field_reviews", "capability_gap_review", "patch", "uncertainties", "rationale"):
        if required_key not in candidate:
            diagnostics.append(PlanningDiagnostic("error", "missing_profile_audit_key", f"profile patch candidate must include '{required_key}'", path))
    patch = candidate.get("patch", {})
    if not isinstance(patch, dict):
        diagnostics.append(PlanningDiagnostic("error", "invalid_profile_patch", "patch must be an object", path))
        return diagnostics
    for key in patch:
        if key not in ALLOWED_PROFILE_PATCH_KEYS:
            diagnostics.append(PlanningDiagnostic("error", "forbidden_profile_patch_key", f"LLM may not patch '{key}'", path))
    for key in REVIEWED_PROFILE_FIELDS:
        allowed = _allowed_patch_values(key)
        if key in patch and allowed is not None and str(patch[key]) not in allowed:
            diagnostics.append(PlanningDiagnostic("error", "invalid_profile_patch_enum", f"Invalid {key} '{patch[key]}'", path))
    known_support_ids = _known_support_ids(planning_ir, profile)
    additions = patch.get("capability_additions", [])
    if additions and not isinstance(additions, list):
        diagnostics.append(PlanningDiagnostic("error", "invalid_capability_additions", "capability_additions must be an array", path))
    for item in additions if isinstance(additions, list) else []:
        if not isinstance(item, dict) or not str(item.get("capability_id", "")).strip():
            diagnostics.append(PlanningDiagnostic("error", "invalid_capability_addition", "capability addition must include capability_id", path))
            continue
        refs = _string_list(item.get("source_fact_ids", [])) + _string_list(item.get("target_directive_ids", [])) + _string_list(item.get("evidence_refs", []))
        if not refs:
            diagnostics.append(PlanningDiagnostic("error", "unsupported_capability_addition", "capability addition must cite source facts, target directives, or evidence", path))
        unknown_refs = [ref for ref in refs if ref not in known_support_ids]
        if unknown_refs:
            diagnostics.append(PlanningDiagnostic("error", "unknown_capability_addition_support", f"capability addition cites unknown support IDs: {unknown_refs}", path))

    field_reviews = candidate.get("field_reviews", {})
    if isinstance(field_reviews, dict):
        for field in REVIEWED_PROFILE_FIELDS:
            review = field_reviews.get(field)
            if not isinstance(review, dict):
                diagnostics.append(PlanningDiagnostic("error", "missing_profile_field_review", f"field_reviews.{field} must be an object", path))
                continue
            deterministic_value = str(review.get("deterministic_value", ""))
            if deterministic_value != _field_value(profile, field):
                diagnostics.append(PlanningDiagnostic("error", "profile_review_value_mismatch", f"field_reviews.{field}.deterministic_value does not match profile", path))
            decision = str(review.get("decision", ""))
            if decision not in ALLOWED_PROFILE_REVIEW_DECISIONS:
                diagnostics.append(PlanningDiagnostic("error", "invalid_profile_review_decision", f"field_reviews.{field}.decision is invalid", path))
            llm_value = str(review.get("llm_value", ""))
            allowed = _allowed_patch_values(field)
            if decision != "keep" and allowed is not None and llm_value not in allowed:
                diagnostics.append(PlanningDiagnostic("error", "invalid_profile_review_value", f"field_reviews.{field}.llm_value is invalid", path))
            support_ids = review.get("support_ids", [])
            if not isinstance(support_ids, list):
                diagnostics.append(PlanningDiagnostic("error", "invalid_profile_review_support", f"field_reviews.{field}.support_ids must be an array", path))
                support_ids = []
            unsupported_ids = [str(item) for item in support_ids if str(item) not in known_support_ids]
            if unsupported_ids:
                diagnostics.append(PlanningDiagnostic("error", "unknown_profile_review_support", f"field_reviews.{field} cites unknown support IDs: {unsupported_ids}", path))
            if llm_value != "unknown" and not support_ids:
                diagnostics.append(PlanningDiagnostic("error", "unsupported_profile_review", f"field_reviews.{field} must cite support IDs for non-unknown judgment", path))
            if decision == "keep" and field in patch:
                diagnostics.append(PlanningDiagnostic("error", "unneeded_profile_patch", f"field_reviews.{field} says keep but patch modifies it", path))
            if decision == "replace" and str(patch.get(field, "")) != llm_value:
                diagnostics.append(PlanningDiagnostic("error", "missing_profile_patch", f"field_reviews.{field} says replace but patch does not match llm_value", path))
            if decision == "uncertain" and str(patch.get(field, "")) != "unknown":
                diagnostics.append(PlanningDiagnostic("error", "missing_uncertain_profile_patch", f"field_reviews.{field} says uncertain but patch does not set unknown", path))
    else:
        diagnostics.append(PlanningDiagnostic("error", "invalid_profile_field_reviews", "field_reviews must be an object", path))

    gap_review = candidate.get("capability_gap_review", {})
    missing_capability_ids: set[str] = set()
    if isinstance(gap_review, dict):
        missing_capability_ids = _missing_capability_ids(gap_review.get("missing_capabilities", []))
        unsupported = gap_review.get("unsupported_existing_capabilities", [])
        if not isinstance(unsupported, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_unsupported_capability_review", "unsupported_existing_capabilities must be an array", path))
    else:
        diagnostics.append(PlanningDiagnostic("error", "invalid_capability_gap_review", "capability_gap_review must be an object", path))
    addition_ids = {str(item.get("capability_id", "")).strip() for item in additions if isinstance(item, dict)}
    if missing_capability_ids and not missing_capability_ids.issubset(addition_ids):
        diagnostics.append(PlanningDiagnostic("error", "missing_capability_gap_patch", "missing capabilities must be covered by capability_additions", path))

    nonempty_patch = any(key != "capability_additions" for key in patch) or bool(additions)
    if not nonempty_patch and isinstance(field_reviews, dict):
        decisions = [str(field_reviews.get(field, {}).get("decision", "")) for field in REVIEWED_PROFILE_FIELDS]
        if any(decision != "keep" for decision in decisions) or missing_capability_ids:
            diagnostics.append(PlanningDiagnostic("error", "invalid_empty_profile_patch", "empty patch requires keep decisions and no missing capabilities", path))
    return diagnostics
