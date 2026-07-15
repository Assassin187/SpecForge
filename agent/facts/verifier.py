from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from .models import FactDiagnostic, VerificationResult


REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "protocol_meta",
    "transport",
    "interaction_model",
    "message_model",
    "state_model",
    "routing_model",
    "resource_model",
    "error_and_limits",
    "minimum_v1",
    "planning_inputs",
    "open_questions",
    "evidence_index",
}

REQUIRED_PLANNING_ITEMS = {
    "传输层与运行模型",
    "报文模型与编解码范围",
    "状态对象设计",
    "路由/调度设计",
    "安全与边界限制",
    "存储或资源抽象",
    "最小可运行功能集",
    "测试与验证方式",
}

REQUIRED_CATEGORY_KEYS = {
    "protocol_meta": {"protocol_name", "source_documents", "document_count", "fact_source_type", "target_scope"},
    "transport": {"channels", "connection_model", "network_stack", "runtime_implications"},
    "interaction_model": {"core_flows", "interaction_units", "roles", "style"},
    "message_model": {"field_constraints", "framing", "message_or_command_entries", "surface_catalog"},
    "state_model": {"invariants", "state_nodes", "timers_and_constants", "transitions"},
    "routing_model": {"dispatch_keys", "dispatch_targets", "matching_rules"},
    "resource_model": {"lifecycle_rules", "persistence_scope", "resource_objects"},
    "error_and_limits": {"error_matrix", "limits", "security"},
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _leaf_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _is_open_question_object_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    required = {"category", "question_type", "question", "blocking_impact", "suggested_followup"}
    for item in value:
        if not isinstance(item, dict):
            return False
        if not required.issubset(item.keys()):
            return False
    return True


def _collect_nested_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_collect_nested_dicts(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_nested_dicts(item))
    return found


def _looks_like_fact_item(node: dict[str, Any]) -> bool:
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
    }
    return any(key in node for key in meaningful_keys)


def verify_facts_output(output_dir: str | Path) -> VerificationResult:
    out_dir = Path(output_dir)
    diagnostics: list[FactDiagnostic] = []
    facts_path = out_dir / "protocol_facts.json"
    if not facts_path.exists():
        diagnostics.append(FactDiagnostic("error", "missing_facts_json", "Missing protocol_facts.json", str(facts_path)))
        return VerificationResult(False, diagnostics)

    try:
        raw = _load_json(facts_path)
    except Exception as exc:  # noqa: BLE001
        diagnostics.append(FactDiagnostic("error", "invalid_json", str(exc), str(facts_path)))
        return VerificationResult(False, diagnostics)

    if raw.get("schema_version") != "protocol_facts/v2alpha1":
        diagnostics.append(
            FactDiagnostic("error", "invalid_schema_version", "schema_version must be 'protocol_facts/v2alpha1'", str(facts_path))
        )

    for key in sorted(REQUIRED_TOP_LEVEL_KEYS):
        if key not in raw:
            diagnostics.append(FactDiagnostic("error", "missing_top_level_key", f"Missing top-level key '{key}'", str(facts_path)))

    for key, expected_keys in REQUIRED_CATEGORY_KEYS.items():
        value = raw.get(key)
        if isinstance(value, dict) and set(value) != expected_keys:
            diagnostics.append(
                FactDiagnostic(
                    "error",
                    "schema_error",
                    f"{key} keys must be exactly {sorted(expected_keys)}, got {sorted(value)}",
                    str(facts_path),
                )
            )

    for category in (
        "transport",
        "interaction_model",
        "message_model",
        "state_model",
        "routing_model",
        "resource_model",
        "error_and_limits",
        "minimum_v1",
    ):
        value = raw.get(category)
        if not isinstance(value, dict) or not value:
            diagnostics.append(FactDiagnostic("error", "missing_semantic_category", f"Category '{category}' is empty", str(facts_path)))

    planning_inputs = raw.get("planning_inputs", {})
    checklist = planning_inputs.get("planning_checklist", [])
    missing_items = REQUIRED_PLANNING_ITEMS.difference(checklist if isinstance(checklist, list) else [])
    for item in sorted(missing_items):
        diagnostics.append(FactDiagnostic("error", "missing_planning_item", f"Missing planning checklist item '{item}'", str(facts_path)))

    message_model = raw.get("message_model", {})
    surface_catalog = message_model.get("surface_catalog", [])
    entries = message_model.get("message_or_command_entries", [])
    if not isinstance(surface_catalog, list) or not surface_catalog:
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "message_model.surface_catalog must be non-empty", str(facts_path)))
    if not isinstance(entries, list) or not entries:
        diagnostics.append(
            FactDiagnostic("error", "planning_insufficiency", "message_model.message_or_command_entries must be non-empty", str(facts_path))
        )

    interaction_model = raw.get("interaction_model", {})
    core_flows = interaction_model.get("core_flows", [])
    known_surface_names = {
        item.get("name")
        for item in surface_catalog
        if isinstance(item, dict) and item.get("name")
    }
    if isinstance(core_flows, list):
        for flow in core_flows:
            if not isinstance(flow, dict):
                diagnostics.append(FactDiagnostic("error", "schema_error", "interaction_model.core_flows items must be objects", str(facts_path)))
                continue
            for surface_name in flow.get("surface_units", []):
                if surface_name not in known_surface_names:
                    diagnostics.append(
                        FactDiagnostic(
                            "error",
                            "coverage_gap",
                            f"interaction flow references unknown surface unit '{surface_name}'",
                            str(facts_path),
                        )
                    )

    state_model = raw.get("state_model", {})
    if not isinstance(state_model.get("state_nodes"), list) or not state_model.get("state_nodes"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "state_model.state_nodes must be non-empty", str(facts_path)))
    if not isinstance(state_model.get("transitions"), list) or not state_model.get("transitions"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "state_model.transitions must be non-empty", str(facts_path)))

    routing_model = raw.get("routing_model", {})
    if not isinstance(routing_model.get("dispatch_keys"), list) or not routing_model.get("dispatch_keys"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "routing_model.dispatch_keys must be non-empty", str(facts_path)))

    resource_model = raw.get("resource_model", {})
    if not isinstance(resource_model.get("resource_objects"), list) or not resource_model.get("resource_objects"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "resource_model.resource_objects must be non-empty", str(facts_path)))
    if not isinstance(resource_model.get("lifecycle_rules"), list) or not resource_model.get("lifecycle_rules"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "resource_model.lifecycle_rules must be non-empty", str(facts_path)))

    error_and_limits = raw.get("error_and_limits", {})
    if not isinstance(error_and_limits.get("error_matrix"), list) or not error_and_limits.get("error_matrix"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "error_and_limits.error_matrix must be non-empty", str(facts_path)))

    limits = error_and_limits.get("limits", {})
    if not isinstance(limits, dict):
        diagnostics.append(FactDiagnostic("error", "schema_error", "error_and_limits.limits must be an object", str(facts_path)))
    else:
        for field in ("fixed_protocol_constants", "defaults", "recommended_values", "hard_bounds", "configurable_bounds"):
            if field not in limits or not isinstance(limits.get(field), list):
                diagnostics.append(FactDiagnostic("error", "schema_error", f"limits.{field} must be a list", str(facts_path)))

    minimum_v1 = raw.get("minimum_v1", {})
    for field in (
        "must_support_surface",
        "must_support_state_behaviors",
        "must_support_error_paths",
        "must_support_limits",
        "may_defer_features",
        "implementation_assumptions",
    ):
        if not isinstance(minimum_v1.get(field), list):
            diagnostics.append(FactDiagnostic("error", "schema_error", f"minimum_v1.{field} must be a list", str(facts_path)))
    if not minimum_v1.get("must_support_surface"):
        diagnostics.append(FactDiagnostic("error", "planning_insufficiency", "minimum_v1.must_support_surface must be non-empty", str(facts_path)))

    open_questions = raw.get("open_questions", [])
    if not _is_open_question_object_list(open_questions):
        diagnostics.append(FactDiagnostic("error", "schema_error", "open_questions must be a list of question objects", str(facts_path)))

    evidence_index = raw.get("evidence_index", [])
    evidence_ids: set[str] = set()
    if not isinstance(evidence_index, list) or not evidence_index:
        diagnostics.append(FactDiagnostic("error", "missing_evidence", "evidence_index must be non-empty", str(facts_path)))
    else:
        for entry in evidence_index:
            if not isinstance(entry, dict):
                diagnostics.append(FactDiagnostic("error", "invalid_evidence_entry", "Evidence entry must be an object", str(facts_path)))
                continue
            evidence_id = entry.get("evidence_id")
            if not evidence_id:
                diagnostics.append(FactDiagnostic("error", "missing_evidence_id", "Evidence entry missing evidence_id", str(facts_path)))
                continue
            if evidence_id in evidence_ids:
                diagnostics.append(FactDiagnostic("error", "duplicate_evidence_id", f"Duplicate evidence_id '{evidence_id}'", str(facts_path)))
            evidence_ids.add(evidence_id)
            for field in ("doc_path", "section_hint", "chunk_id", "excerpt"):
                if not entry.get(field):
                    diagnostics.append(
                        FactDiagnostic("error", "invalid_evidence_entry", f"Evidence '{evidence_id}' missing '{field}'", str(facts_path))
                    )

    all_fact_nodes = []
    for category in (
        "transport",
        "interaction_model",
        "message_model",
        "state_model",
        "routing_model",
        "resource_model",
        "error_and_limits",
        "minimum_v1",
    ):
        all_fact_nodes.extend(_collect_nested_dicts(raw.get(category)))

    for node in all_fact_nodes:
        if not _looks_like_fact_item(node):
            continue
        refs = node.get("evidence_refs", [])
        if not isinstance(refs, list) or not refs:
            diagnostics.append(FactDiagnostic("error", "traceability_gap", "Fact item missing evidence_refs", str(facts_path)))
            continue
        for ref in refs:
            if ref not in evidence_ids:
                diagnostics.append(FactDiagnostic("error", "traceability_gap", f"Unknown evidence ref '{ref}'", str(facts_path)))

    for category in ("transport", "interaction_model", "message_model", "state_model", "routing_model", "resource_model", "error_and_limits"):
        for node in _collect_nested_dicts(raw.get(category)):
            if not _looks_like_fact_item(node):
                continue
            refs = node.get("evidence_refs", [])
            if isinstance(refs, list) and refs and all(str(ref).startswith("profile_") for ref in refs):
                diagnostics.append(
                    FactDiagnostic("error", "profile_evidence_misuse", f"Normative {category} fact uses only profile evidence", str(facts_path))
                )

    must_support_surface = minimum_v1.get("must_support_surface", [])
    for item in must_support_surface if isinstance(must_support_surface, list) else []:
        if not isinstance(item, dict):
            diagnostics.append(FactDiagnostic("error", "schema_error", "minimum_v1.must_support_surface items must be objects", str(facts_path)))
            continue
        name = item.get("name")
        if name and name not in known_surface_names:
            diagnostics.append(
                FactDiagnostic(
                    "error",
                    "planning_insufficiency",
                    f"minimum_v1.must_support_surface item '{name}' does not map to message_model.surface_catalog",
                    str(facts_path),
                )
            )

    manifest_path = out_dir / "run_manifest.json"
    if not manifest_path.exists():
        diagnostics.append(FactDiagnostic("error", "missing_manifest", "Missing run_manifest.json", str(manifest_path)))
    else:
        try:
            manifest = _load_json(manifest_path)
            profile_meta = manifest.get("target_profile", {})
            profile_path = Path(profile_meta.get("path", ""))
            if not profile_path.exists() or hashlib.sha256(profile_path.read_bytes()).hexdigest() != profile_meta.get("sha256"):
                diagnostics.append(FactDiagnostic("error", "profile_hash_mismatch", "Target profile path/hash mismatch", str(manifest_path)))
            scope_meta = manifest.get("scope_resolution", {})
            scope_path = Path(scope_meta.get("path", ""))
            if not scope_path.exists() or hashlib.sha256(scope_path.read_bytes()).hexdigest() != scope_meta.get("sha256"):
                diagnostics.append(FactDiagnostic("error", "scope_hash_mismatch", "Scope resolution path/hash mismatch", str(manifest_path)))
            else:
                scope = _load_json(scope_path)
                if scope.get("profile_sha256") != profile_meta.get("sha256"):
                    diagnostics.append(FactDiagnostic("error", "scope_profile_mismatch", "Scope resolution used a different profile", str(scope_path)))
                if scope.get("closure_status") != "complete" or scope.get("unresolved_capabilities"):
                    diagnostics.append(FactDiagnostic("error", "scope_incomplete", "Capability/dependency closure is incomplete", str(scope_path)))
                included = set(scope.get("included_surface", []))
                minimum_names = {item.get("name") for item in must_support_surface if isinstance(item, dict)}
                if not included.issubset(minimum_names):
                    diagnostics.append(FactDiagnostic("error", "scope_coverage_gap", f"minimum_v1 misses included surfaces: {sorted(included - minimum_names)}", str(facts_path)))
                excluded = set(scope.get("excluded_surface", []))
                leaked = minimum_names & excluded
                if leaked:
                    diagnostics.append(FactDiagnostic("error", "excluded_surface_leakage", f"minimum_v1 includes excluded surfaces: {sorted(leaked)}", str(facts_path)))
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(FactDiagnostic("error", "invalid_manifest", str(exc), str(manifest_path)))

    return VerificationResult(not any(diag.level == "error" for diag in diagnostics), diagnostics)
