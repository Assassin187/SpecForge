from __future__ import annotations

import re
from typing import Any

def _safe_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _field_value(value: Any, default: str = "") -> str:
    if isinstance(value, dict):
        raw = value.get("value", default)
        return str(raw) if raw is not None else default
    if value is None:
        return default
    return str(value)


def _target_directives(ir: dict[str, Any]) -> dict[str, Any]:
    target = ir.get("target_directives", {})
    if not isinstance(target, dict) or "directives" not in target:
        target = ir.get("target_directives_ref", {})
    directives = target.get("directives", {}) if isinstance(target, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(directives, dict):
        for key, value in directives.items():
            if isinstance(value, dict) and ("value" in value or "directive_id" in value):
                result[key] = value.get("value")
            else:
                result[key] = value
    return result


def _capability_refs(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for item in profile.get("required_capabilities", []):
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id", "")).strip()
        if not cap_id:
            continue
        refs[cap_id] = {
            "category": item.get("category"),
            "source_fact_ids": item.get("source_fact_ids", []),
            "target_directive_ids": item.get("target_directive_ids", []),
            "evidence_refs": item.get("evidence_refs", []),
        }
    return refs


def _compressed_refs(item: dict[str, Any]) -> dict[str, Any]:
    source_fact_ids = item.get("source_fact_ids", [])
    target_directive_ids = item.get("target_directive_ids", [])
    evidence_refs = item.get("evidence_refs", [])
    return {
        "source_fact_count": len(source_fact_ids) if isinstance(source_fact_ids, list) else 0,
        "target_directive_count": len(target_directive_ids) if isinstance(target_directive_ids, list) else 0,
        "evidence_refs": [str(ref) for ref in evidence_refs[:5]] if isinstance(evidence_refs, list) else [],
    }


def _surface_units(ir: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for item in profile.get("required_surface_units", []):
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            by_name[str(item["name"])] = {
                "name": str(item["name"]),
                "source_fact_ids": item.get("source_fact_ids", []),
                "evidence_refs": item.get("evidence_refs", []),
            }
    facts = ir.get("protocol_facts", {})
    message = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    surface_catalog = message.get("surface_catalog", []) if isinstance(message.get("surface_catalog"), list) else []
    for item in surface_catalog:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        current = by_name.setdefault(name, {"name": name, "source_fact_ids": [], "evidence_refs": []})
        if item.get("direction") is not None:
            current["direction"] = str(item.get("direction"))
        if item.get("role") is not None:
            current["role"] = str(item.get("role"))
    return list(by_name.values())


def _handler_surfaces(surfaces: list[dict[str, Any]], target_role: str) -> list[dict[str, Any]]:
    role = _safe_id(target_role)
    if not role:
        return surfaces
    result: list[dict[str, Any]] = []
    for surface in surfaces:
        direction = _safe_id(str(surface.get("direction", "")))
        if not direction or direction == "bidirectional" or direction.endswith(f"_to_{role}") or direction == role:
            result.append(surface)
    return result


def _wire_fields(ir: dict[str, Any]) -> list[dict[str, Any]]:
    facts = ir.get("protocol_facts", {})
    message = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    entries = message.get("message_or_command_entries", []) if isinstance(message.get("message_or_command_entries"), list) else []
    field_index = ir.get("normalization_index", {}).get("field_id_by_message_and_name", {})
    result: list[dict[str, Any]] = []
    for entry_idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        message_name = str(entry.get("name") or entry.get("surface_unit") or f"entry_{entry_idx}").strip()
        fields = entry.get("fields", [])
        if not message_name or not isinstance(fields, list):
            continue
        indexed_fields = field_index.get(message_name, {}) if isinstance(field_index, dict) else {}
        for field_idx, field in enumerate(fields):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or f"field_{field_idx}").strip()
            field_id = str(field.get("fact_id") or indexed_fields.get(field_name) or f"fact:message_model_message_or_command_entries_{entry_idx}_fields_{field_idx}")
            access_path_id = f"access:{_safe_id(message_name)}:{_safe_id(field_name)}"
            field_type = str(field.get("type") or field.get("value_type") or field.get("encoding") or field.get("wire_type") or "").strip()
            result.append(
                {
                    "field_id": field_id,
                    "message": message_name,
                    "field": field_name,
                    "field_type": field_type,
                    "access_path_id": access_path_id,
                    "access_path": f"{_safe_id(message_name)}.{_safe_id(field_name)}",
                    "source_fact_ids": [field_id],
                }
            )
    return result


def _function_signature(return_type: str, name: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    rendered_params = ", ".join(f"{item['type']} {item['name']}" for item in params) if params else "void"
    return {
        "return_type": return_type,
        "name": name,
        "params": params,
        "raw": f"{return_type} {name}({rendered_params})",
    }
