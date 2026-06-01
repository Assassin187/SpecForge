from __future__ import annotations

from copy import deepcopy
from typing import Any

from .function_inventory_decomposition import DECOMPOSITION_RULES
from .implementation_plan import _safe_id
from .implementation_plan_context import SYSTEM_TYPE_IDS, normalize_system_type_ref


TYPE_KIND_VALUES = {
    "opaque_handle",
    "struct",
    "config_struct",
    "internal_state",
    "enum",
    "callback_type",
    "event_struct",
    "view_struct",
    "owned_buffer",
    "result_struct",
    "bitflag",
    "alias",
}
FUNCTION_KIND_VALUES = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}
CODER_FUNCTION_TYPE_VALUES = {"ALGORITHM", "EVENT", "ENTRYPOINT"}
NON_FUNCTION_LIFECYCLE_NAMES = {"caller", "external", "application", "app", "user", "callee", "owner", "runtime", "system"}
ABSTRACT_FUNCTION_FAMILY_NAMES = {
    family
    for rule in DECOMPOSITION_RULES
    for family in rule.expected_function_families
}


def _producer(stage: str, prompt_name: str) -> dict[str, str]:
    return {"stage": stage, "prompt_name": prompt_name, "prompt_version": "deterministic_reconciliation"}


def _empty_callback_signature() -> dict[str, Any]:
    return {"return_type": "", "params": []}


def _empty_type_lifecycle() -> dict[str, list[str]]:
    return {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []}


def _is_non_function_lifecycle_name(name: Any) -> bool:
    key = _safe_id(str(name))
    return key in NON_FUNCTION_LIFECYCLE_NAMES or key.endswith("_caller") or key.endswith("_application")


def _looks_like_abstract_function_family_name(name: Any) -> bool:
    safe_name = _safe_id(str(name))
    return any(safe_name == family or safe_name.endswith(f"_{family}") for family in ABSTRACT_FUNCTION_FAMILY_NAMES)


def _type_field(
    name: str,
    field_type: str,
    *,
    type_ref: str = "",
    ownership: str = "BORROWED",
    lifetime: str = "valid while parent type is valid",
    length_field: str = "",
    capacity_field: str = "",
    validation_notes: str = "",
    variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    field = {
        "field_name": name,
        "field_type": field_type,
        "type_ref": type_ref,
        "required": True,
        "ownership": ownership,
        "lifetime": lifetime,
        "length_field": length_field,
        "capacity_field": capacity_field,
        "validation_notes": validation_notes,
    }
    if variants:
        field["variants"] = variants
    return field


def _system_type_ref(field_type: str) -> str:
    raw = str(field_type).replace("const", "").replace("*", "").strip()
    return raw if raw in SYSTEM_TYPE_IDS else ""


def _type_ref_for_field(field_type: str, allowed_refs: set[str], name_aliases: dict[str, str]) -> str:
    raw = _system_type_ref(field_type)
    if raw:
        return raw
    clean = str(field_type).replace("const", "").replace("*", "").strip()
    alias = name_aliases.get(_safe_id(clean.removeprefix("struct ")))
    if alias:
        return alias
    return clean if clean in allowed_refs else ""


def _is_public_type_ref_meta(type_item: dict[str, Any] | None) -> bool:
    if not type_item:
        return False
    if "visibility" not in type_item and "defined_in" not in type_item and "owner_module_id" in type_item:
        return True
    return str(type_item.get("visibility", "")) == "public" and str(type_item.get("defined_in", "")) == "public_header"


def _pointer_like(c_type: Any) -> bool:
    text = str(c_type)
    lower = text.lower()
    return "*" in text or "buffer" in lower or "string" in lower


def _release_capable_field(field: dict[str, Any]) -> bool:
    field_type = str(field.get("field_type", ""))
    lower = field_type.lower()
    return "*" in field_type or "[" in field_type or "buffer" in lower or "string" in lower or lower.strip() == "union" or bool(field.get("variants"))


def _field_owns_releasable_data(field: dict[str, Any]) -> bool:
    ownership = str(field.get("ownership", "")).strip().upper()
    if ownership in {"BORROWED", "SHARED", "OWNED_BY_CALLER", "CALLER_OWNED"}:
        return False
    return _release_capable_field(field) and ownership in {"OWNED", "TRANSFER", "UNKNOWN"}


def _normalize_field_ownership(field: dict[str, Any]) -> None:
    ownership = str(field.get("ownership", "")).strip().upper()
    if ownership in {"OWNED", "TRANSFER"} and not _release_capable_field(field):
        field["ownership"] = "BORROWED"
        if not str(field.get("lifetime", "")).strip() or str(field.get("lifetime", "")).lower() == "packet-scoped":
            field["lifetime"] = "stored inline in parent value"


def _merge_variants(default_variants: Any, filling_variants: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for raw in [*(default_variants if isinstance(default_variants, list) else []), *(filling_variants if isinstance(filling_variants, list) else [])]:
        if not isinstance(raw, dict):
            continue
        name = _safe_id(str(raw.get("field_name", "variant"))) or "variant"
        current = by_name.get(name, {})
        item = {**current, **deepcopy(raw), "field_name": name}
        by_name[name] = item
    for item in by_name.values():
        merged.append(item)
    return merged


def _merge_type_fields(default_fields: list[dict[str, Any]], filling_fields: Any) -> list[dict[str, Any]]:
    if not isinstance(filling_fields, list) or not filling_fields:
        return deepcopy(default_fields)
    filling_by_name = {
        _safe_id(str(field.get("field_name", ""))): field
        for field in filling_fields
        if isinstance(field, dict) and str(field.get("field_name", "")).strip()
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    semantic_keys = {"ownership", "lifetime", "length_field", "capacity_field", "validation_notes"}
    for default in default_fields:
        item = deepcopy(default)
        key = _safe_id(str(item.get("field_name", "")))
        filling = filling_by_name.get(key)
        if filling:
            for semantic_key in semantic_keys:
                if str(filling.get(semantic_key, "")).strip():
                    item[semantic_key] = deepcopy(filling[semantic_key])
            if not str(item.get("type_ref", "")).strip() and str(filling.get("type_ref", "")).strip():
                item["type_ref"] = deepcopy(filling["type_ref"])
            variants = _merge_variants(item.get("variants", []), filling.get("variants", []))
            if variants:
                item["variants"] = variants
        merged.append(item)
        seen.add(key)
    for filling in filling_fields:
        if not isinstance(filling, dict):
            continue
        key = _safe_id(str(filling.get("field_name", "")))
        if key and key not in seen:
            merged.append(deepcopy(filling))
            seen.add(key)
    return merged


def _merge_type_items(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(existing)
    item["fields"] = _merge_type_fields(
        [field for field in item.get("fields", []) if isinstance(field, dict)],
        [field for field in incoming.get("fields", []) if isinstance(field, dict)],
    )
    if not item.get("enum_values") and incoming.get("enum_values"):
        item["enum_values"] = deepcopy(incoming.get("enum_values", []))
    elif incoming.get("enum_values"):
        seen_values = {str(value.get("name", "")) for value in item.get("enum_values", []) if isinstance(value, dict)}
        for value in incoming.get("enum_values", []):
            if isinstance(value, dict) and str(value.get("name", "")) not in seen_values:
                item.setdefault("enum_values", []).append(deepcopy(value))
                seen_values.add(str(value.get("name", "")))
    for key in ("dependencies", "trace_ref_keys", "related_functions"):
        item[key] = sorted(set([str(ref) for ref in item.get(key, []) if str(ref).strip()] + [str(ref) for ref in incoming.get(key, []) if str(ref).strip()]))
    for key in ("created_by", "initialized_by", "destroyed_by", "freed_by"):
        item.setdefault("lifecycle", _empty_type_lifecycle())
        item["lifecycle"][key] = sorted(set([str(ref) for ref in item["lifecycle"].get(key, []) if str(ref).strip()] + [str(ref) for ref in incoming.get("lifecycle", {}).get(key, []) if str(ref).strip()]))
    if not str(item.get("ownership_lifetime", "")).strip() and str(incoming.get("ownership_lifetime", "")).strip():
        item["ownership_lifetime"] = str(incoming.get("ownership_lifetime", ""))
    if str(item.get("status", "")) in {"", "inferred"} and str(incoming.get("status", "")).strip():
        item["status"] = str(incoming.get("status", "inferred"))
    item["lifecycle"] = _lifecycle_for_type(item)
    return item


def _slot_default_fields(slot: dict[str, Any], allowed_refs: set[str], name_aliases: dict[str, str]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for raw in slot.get("required_fields", []):
        if not isinstance(raw, dict):
            continue
        field_type = str(raw.get("field_type") or "uint8_t")
        field_name = _safe_id(str(raw.get("field_name", "field"))) or "field"
        if field_type == "enum_value":
            continue
        variants = [
            {
                "field_name": _safe_id(str(variant.get("field_name", "variant"))) or "variant",
                "field_type": str(variant.get("field_type", "")),
                "source_field_id": str(variant.get("source_field_id", "")),
            }
            for variant in raw.get("variants", [])
            if isinstance(variant, dict)
        ]
        fields.append(
            _type_field(
                field_name,
                field_type,
                type_ref=str(raw.get("type_ref", "")).strip()
                or ("" if field_type == "union" else _type_ref_for_field(field_type, allowed_refs, name_aliases)),
                ownership=str(raw.get("ownership", "")).strip().upper()
                or ("OWNED" if (_pointer_like(field_type) or field_type == "union" or variants) else "BORROWED"),
                lifetime=str(raw.get("lifetime", "")).strip()
                or ("owned by parent until cleanup" if (_pointer_like(field_type) or field_type == "union" or variants) else "valid while parent type is valid"),
                length_field=str(raw.get("length_field", "")).strip()
                or (f"{field_name}_len" if field_type == "uint8_t*" and not field_name.endswith("_len") else ""),
                capacity_field=str(raw.get("capacity_field", "")).strip(),
                validation_notes=str(raw.get("validation_notes", "")).strip() or str(raw.get("source_field_id", "")),
                variants=variants,
            )
        )
    return fields


def _slot_default_enum_values(slot: dict[str, Any]) -> list[dict[str, Any]]:
    if slot.get("kind") != "enum":
        return []
    base = _safe_id(str(slot.get("name", "enum")).removesuffix("_t")).upper()
    values: list[dict[str, Any]] = []
    for index, raw in enumerate(slot.get("required_fields", [])):
        if not isinstance(raw, dict) or str(raw.get("field_type", "")) != "enum_value":
            continue
        role = _safe_id(str(raw.get("field_name", f"value_{index}"))).upper()
        values.append({"name": f"{base}_{role}", "value": str(raw.get("value", index)), "role": str(raw.get("field_name", ""))})
    return values


def _lifecycle_for_type(type_item: dict[str, Any]) -> dict[str, list[str]]:
    lifecycle = deepcopy(type_item.get("lifecycle", {})) if isinstance(type_item.get("lifecycle"), dict) else _empty_type_lifecycle()
    for key in ("created_by", "initialized_by", "destroyed_by", "freed_by"):
        lifecycle.setdefault(key, [])
        lifecycle[key] = [str(item) for item in lifecycle[key] if str(item).strip() and not _is_non_function_lifecycle_name(item)]
    text = f"{type_item.get('name', '')} {type_item.get('kind', '')} {type_item.get('purpose', '')}".lower()
    fields = type_item.get("fields", [])
    owns_data = str(type_item.get("kind", "")) in {"owned_buffer", "result_struct"} or any(isinstance(field, dict) and _field_owns_releasable_data(field) for field in fields)
    if owns_data:
        base = _safe_id(str(type_item.get("name", "type")).removeprefix("struct ").removesuffix("_t"))

        def local_release_name(name: str) -> bool:
            function_key = _safe_id(name)
            base_tail = base.removeprefix("mqtt_")
            module_key = _safe_id(str(type_item.get("module_id", "")))
            module_parts = [part for part in module_key.split("_") if len(part) >= 4]
            return (
                base in function_key
                or (base_tail and base_tail in function_key)
                or (module_key and module_key in function_key)
                or any(part in function_key for part in module_parts)
            )

        lifecycle["freed_by"] = [name for name in lifecycle.get("freed_by", []) if local_release_name(name)]
        lifecycle["destroyed_by"] = [name for name in lifecycle.get("destroyed_by", []) if local_release_name(name)]
        if not lifecycle.get("freed_by") and not lifecycle.get("destroyed_by"):
            lifecycle["freed_by"] = [f"{base}_free"]
    if str(type_item.get("kind", "")) == "internal_state" and "owned" in text and not lifecycle.get("destroyed_by"):
        base = _safe_id(str(type_item.get("name", "type")).removeprefix("struct ").removesuffix("_t"))
        lifecycle["destroyed_by"] = [f"{base}_destroy"]
    return lifecycle


def _allowed_type_refs(space: dict[str, Any]) -> tuple[set[str], dict[str, str], dict[str, dict[str, Any]]]:
    refs: set[str] = set(SYSTEM_TYPE_IDS)
    aliases: dict[str, str] = {}
    local_meta: dict[str, dict[str, Any]] = {}
    allowed = space.get("allowed_type_refs", {})
    for key in ("local_slots", "provider_public_types", "canonical_public_types"):
        for item in allowed.get(key, []):
            if not isinstance(item, dict):
                continue
            type_id = str(item.get("type_id", "")).strip()
            name = str(item.get("name", "")).strip()
            if type_id:
                refs.add(type_id)
                local_meta[type_id] = item
            if name:
                refs.add(name)
                aliases[_safe_id(name.removeprefix("struct "))] = type_id or name
                local_meta[name] = item
    return refs, aliases, local_meta


def _sanitize_type_refs(type_item: dict[str, Any], allowed_refs: set[str], name_aliases: dict[str, str], local_meta: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    item = deepcopy(type_item)
    diagnostics: list[dict[str, Any]] = []

    def normalize(ref: Any, c_type: Any = "") -> str:
        raw = normalize_system_type_ref(ref)
        if raw.startswith(("state:", "message:", "field:", "handler:", "func:", "file:", "module:")):
            return ""
        if raw in allowed_refs:
            return raw
        alias = name_aliases.get(_safe_id(str(raw).removeprefix("struct ")))
        if alias:
            return alias
        return _type_ref_for_field(str(c_type), allowed_refs, name_aliases)

    is_public = str(item.get("visibility", "")) == "public" and str(item.get("defined_in", "")) == "public_header"

    def note(existing: Any, message: str) -> str:
        text = str(existing or "").strip()
        return f"{text}; {message}" if text else message

    def normalize_surface_ref(ref: Any, c_type: Any, *, surface: str, owner_name: Any, fallback_to_void: bool) -> str:
        normalized = normalize(ref, c_type)
        if not normalized:
            if fallback_to_void and _pointer_like(c_type):
                diagnostics.append({"level": "warning", "code": "unknown_type_ref_normalized", "message": f"normalized unknown {surface} ref in {item.get('type_id')}"})
                return "void"
            return ""
        target = local_meta.get(normalized)
        if is_public and target is not None and not _is_public_type_ref_meta(target):
            diagnostics.append({"level": "warning", "code": "public_private_type_ref_normalized", "message": f"normalized private {surface} ref in {item.get('type_id')}"})
            return "void" if fallback_to_void else ""
        if target is None and normalized not in SYSTEM_TYPE_IDS and normalized.startswith("type:"):
            diagnostics.append({"level": "warning", "code": "unknown_type_ref_normalized", "message": f"dropped unknown {surface} ref '{normalized}' in {owner_name}"})
            return "void" if fallback_to_void and _pointer_like(c_type) else ""
        return normalized

    dependencies: list[str] = []
    for dependency in item.get("dependencies", []):
        normalized = normalize_surface_ref(dependency, "", surface="dependency", owner_name=item.get("type_id"), fallback_to_void=False)
        if normalized and normalized != "void":
            dependencies.append(normalized)
    for field in item.get("fields", []):
        if not isinstance(field, dict):
            continue
        normalized = normalize_surface_ref(field.get("type_ref", ""), field.get("field_type", ""), surface="field", owner_name=f"{item.get('type_id')}.{field.get('field_name')}", fallback_to_void=True)
        if normalized == "void" and not _system_type_ref(str(field.get("field_type", ""))):
            field["field_type"] = "void*"
            field["type_ref"] = "void"
            field["validation_notes"] = note(field.get("validation_notes", ""), "type reference normalized to opaque context")
        else:
            field["type_ref"] = normalized
        if field["type_ref"] and field["type_ref"] != "void":
            dependencies.append(field["type_ref"])
        if _pointer_like(field.get("field_type", "")) and (not str(field.get("ownership", "")).strip() or field.get("ownership") == "UNKNOWN"):
            field["ownership"] = "BORROWED"
        if _pointer_like(field.get("field_type", "")) and not str(field.get("lifetime", "")).strip():
            field["lifetime"] = "valid while parent type is valid"
        _normalize_field_ownership(field)
    signature = item.get("callback_signature")
    if not isinstance(signature, dict):
        signature = _empty_callback_signature()
        item["callback_signature"] = signature
    params = signature.get("params", [])
    if not isinstance(params, list):
        params = []
        signature["params"] = params
    for param in params:
        if not isinstance(param, dict):
            continue
        normalized = normalize_surface_ref(param.get("type_ref", ""), param.get("type", ""), surface="callback parameter", owner_name=f"{item.get('type_id')}.{param.get('name')}", fallback_to_void=True)
        if normalized == "void" and not _system_type_ref(str(param.get("type", ""))):
            param["type"] = "void*"
            param["type_ref"] = "void"
        else:
            param["type_ref"] = normalized
        if param.get("type_ref") and param.get("type_ref") != "void":
            dependencies.append(str(param["type_ref"]))
        if _pointer_like(param.get("type", "")) and (not str(param.get("ownership", "")).strip() or param.get("ownership") == "UNKNOWN"):
            param["ownership"] = "BORROWED"
    item["dependencies"] = sorted(dict.fromkeys(dep for dep in dependencies if dep and dep not in SYSTEM_TYPE_IDS))
    return item, diagnostics


def _type_from_slot(slot: dict[str, Any], filling: dict[str, Any] | None, allowed_refs: set[str], name_aliases: dict[str, str], local_meta: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    item = {
        "type_id": str(slot.get("type_id", "")),
        "name": str(slot.get("name", "")),
        "module_id": str(slot.get("module_id", "")),
        "kind": str(slot.get("kind", "struct")),
        "visibility": "public" if str(slot.get("visibility", "")) == "public_header_when_needed" else str(slot.get("visibility", "module_internal")),
        "defined_in": "public_header" if str(slot.get("visibility", "")) == "public_header_when_needed" else str(slot.get("defined_in", "internal_header")),
        "purpose": str(slot.get("source_reason", "")),
        "fields": _slot_default_fields(slot, allowed_refs, name_aliases),
        "enum_values": _slot_default_enum_values(slot),
        "callback_signature": _empty_callback_signature(),
        "ownership_lifetime": "",
        "lifecycle": _empty_type_lifecycle(),
        "related_functions": [],
        "dependencies": [],
        "trace_ref_keys": [str(ref) for ref in slot.get("trace_ref_keys", []) if str(ref).strip()],
        "status": "inferred",
    }
    if item["kind"] == "owned_buffer" and not item["fields"]:
        item["fields"] = [
            _type_field("data", "uint8_t*", type_ref="uint8_t", ownership="OWNED", lifetime="owned until buffer free", length_field="len", capacity_field="cap"),
            _type_field("len", "size_t", type_ref="size_t"),
            _type_field("cap", "size_t", type_ref="size_t"),
        ]
        item["ownership_lifetime"] = "Owned byte buffer; release through the generated free path."
    if item["kind"] == "callback_type" and not item["callback_signature"].get("return_type"):
        item["callback_signature"] = {
            "return_type": "void",
            "params": [
                {"name": "user", "type": "void*", "type_ref": "void", "ownership": "BORROWED"},
                {"name": "context", "type": "void*", "type_ref": "void", "ownership": "BORROWED"},
            ],
        }
    if filling:
        if str(filling.get("semantic_purpose", "")).strip():
            item["purpose"] = str(filling.get("semantic_purpose", "")).strip()
        if isinstance(filling.get("fields"), list) and filling.get("fields"):
            item["fields"] = _merge_type_fields(item["fields"], filling["fields"])
        for key in ("enum_values", "dependencies"):
            if isinstance(filling.get(key), list) and filling.get(key):
                item[key] = deepcopy(filling[key])
        if isinstance(filling.get("callback_signature"), dict) and (filling.get("callback_signature", {}).get("return_type") or filling.get("callback_signature", {}).get("params")):
            item["callback_signature"] = deepcopy(filling["callback_signature"])
        if str(filling.get("ownership_lifetime", "")).strip():
            item["ownership_lifetime"] = str(filling.get("ownership_lifetime", "")).strip()
        if isinstance(filling.get("lifecycle"), dict):
            item["lifecycle"] = deepcopy(filling["lifecycle"])
        item["trace_ref_keys"] = sorted(set(item["trace_ref_keys"] + [str(ref) for ref in filling.get("trace_ref_keys", []) if str(ref).strip()]))
        if str(filling.get("status", "")).strip():
            item["status"] = str(filling.get("status", "inferred"))
    item["lifecycle"] = _lifecycle_for_type(item)
    item, diagnostics = _sanitize_type_refs(item, allowed_refs, name_aliases, local_meta)
    return item, diagnostics


def _lifecycle_obligations(types: list[dict[str, Any]]) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    for item in types:
        lifecycle = item.get("lifecycle", {}) if isinstance(item.get("lifecycle"), dict) else {}
        for action, names in (
            ("create", lifecycle.get("created_by", [])),
            ("initialize", lifecycle.get("initialized_by", [])),
            ("destroy", lifecycle.get("destroyed_by", [])),
            ("release_owned_data", lifecycle.get("freed_by", [])),
        ):
            filtered_names = [str(name) for name in names if str(name).strip() and not _is_non_function_lifecycle_name(name)]
            if filtered_names:
                obligations.append(
                    {
                        "obligation_id": f"obligation:{item.get('type_id')}:{action}",
                        "type_id": item.get("type_id", ""),
                        "type_name": item.get("name", ""),
                        "action": action,
                        "required_function_names": filtered_names,
                        "source_reason": f"type lifecycle declares {action}",
                        "trace_ref_keys": item.get("trace_ref_keys", []),
                    }
                )
    return obligations


def reconcile_type_filling_candidate(space: dict[str, Any], filling_candidate: dict[str, Any] | None) -> dict[str, Any]:
    filling_candidate = filling_candidate or {}
    allowed_refs, name_aliases, local_meta = _allowed_type_refs(space)
    fillings = {
        str(item.get("slot_id", "")): item
        for item in filling_candidate.get("slot_fillings", [])
        if isinstance(item, dict) and str(item.get("slot_id", "")).strip()
    }
    diagnostics: list[dict[str, Any]] = [dict(item) for item in space.get("richness_diagnostics", []) if isinstance(item, dict)]
    types: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    index_by_id: dict[str, int] = {}
    index_by_name: dict[str, int] = {}
    for slot in [*space.get("mandatory_type_slots", []), *space.get("derived_type_slots", []), *space.get("recommended_type_slots", [])]:
        if not isinstance(slot, dict):
            continue
        type_item, item_diags = _type_from_slot(slot, fillings.get(str(slot.get("slot_id", ""))), allowed_refs, name_aliases, local_meta)
        key = _safe_id(str(type_item.get("name", "")).removeprefix("struct "))
        if type_item["type_id"] in seen_ids or key in seen_names:
            existing_index = index_by_id.get(str(type_item["type_id"]), index_by_name.get(key))
            if existing_index is not None:
                types[existing_index] = _merge_type_items(types[existing_index], type_item)
            diagnostics.append({"level": "warning", "code": "duplicate_concept_type", "message": f"merged duplicate type slot '{type_item.get('name')}'"})
            diagnostics.extend(item_diags)
            continue
        seen_ids.add(type_item["type_id"])
        seen_names.add(key)
        index_by_id[str(type_item["type_id"])] = len(types)
        index_by_name[key] = len(types)
        types.append(type_item)
        diagnostics.extend(item_diags)
    accepted_optional: list[dict[str, Any]] = []
    rejected_optional: list[dict[str, Any]] = []
    for proposal in filling_candidate.get("optional_type_proposals", []):
        if not isinstance(proposal, dict):
            continue
        reason = str(proposal.get("expansion_reason", "")).strip()
        name = str(proposal.get("name_hint", "")).strip()
        source_refs = [str(ref) for ref in proposal.get("source_refs", []) if str(ref).strip()]
        if not reason or not name or not source_refs:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "missing name_hint, expansion_reason, or source_refs"})
            continue
        kind = str(proposal.get("kind", "struct"))
        if kind not in TYPE_KIND_VALUES:
            kind = "struct"
        visibility = str(proposal.get("visibility", "module_internal"))
        defined_in = str(proposal.get("defined_in", "internal_header"))
        if visibility == "public_header_when_needed":
            visibility, defined_in = "public", "public_header"
        type_id = f"type:{space.get('module_id')}:{_safe_id(name)}"
        key = _safe_id(name.removeprefix("struct "))
        if type_id in seen_ids or key in seen_names:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "duplicate optional type id/name"})
            continue
        item = {
            "type_id": type_id,
            "name": name,
            "module_id": str(space.get("module_id", "")),
            "kind": kind,
            "visibility": visibility if visibility in {"public", "private", "module_internal"} else "module_internal",
            "defined_in": defined_in if defined_in in {"public_header", "internal_header", "source_file"} else "internal_header",
            "purpose": str(proposal.get("semantic_purpose", "")) or reason,
            "fields": deepcopy(proposal.get("fields", [])),
            "enum_values": deepcopy(proposal.get("enum_values", [])),
            "callback_signature": deepcopy(proposal.get("callback_signature", _empty_callback_signature())),
            "ownership_lifetime": str(proposal.get("ownership_lifetime", "")),
            "lifecycle": deepcopy(proposal.get("lifecycle", _empty_type_lifecycle())),
            "related_functions": [],
            "dependencies": deepcopy(proposal.get("dependencies", [])),
            "trace_ref_keys": sorted(set(source_refs + [str(ref) for ref in proposal.get("trace_ref_keys", []) if str(ref).strip()])),
            "status": str(proposal.get("status", "inferred")),
        }
        item["lifecycle"] = _lifecycle_for_type(item)
        item, item_diags = _sanitize_type_refs(item, allowed_refs | {type_id, name}, name_aliases | {key: type_id}, local_meta | {type_id: item})
        types.append(item)
        seen_ids.add(type_id)
        seen_names.add(key)
        accepted_optional.append({"proposal_key": proposal.get("proposal_key", ""), "type_id": type_id, "name": name})
        diagnostics.extend(item_diags)
    candidate = {
        "schema_version": "type_inventory_candidate/v1",
        "candidate_id": f"candidate:type_inventory:{space.get('module_id')}",
        "producer": _producer("5.4a_type_inventory", "type_filling_candidate_prompt"),
        "module_id": str(space.get("module_id", "")),
        "types": types,
        "assumptions": deepcopy(filling_candidate.get("assumptions", [])),
        "unresolved_questions": deepcopy(filling_candidate.get("unresolved_questions", [])),
    }
    return {
        "candidate": candidate,
        "reconciliation_report": {
            "schema_version": "type_reconciliation_report/v1",
            "module_id": space.get("module_id", ""),
            "required_slot_count": len(space.get("mandatory_type_slots", [])) + len(space.get("derived_type_slots", [])),
            "recommended_slot_count": len(space.get("recommended_type_slots", [])),
            "accepted_optional_types": accepted_optional,
            "rejected_optional_types": rejected_optional,
            "diagnostic_count": len(diagnostics),
        },
        "diagnostics": diagnostics,
        "type_obligations": {"schema_version": "type_obligations/v1", "module_id": space.get("module_id", ""), "obligations": _lifecycle_obligations(types)},
    }


def _legal_refs(space: dict[str, Any]) -> dict[str, set[str]]:
    refs = space.get("legal_refs", {})
    return {key: {str(item) for item in refs.get(key, []) if str(item).strip()} for key in ("capability_ids", "local_capability_ids", "handler_ids", "message_ids", "field_ids", "type_ids")}


def _function_from_seed(seed: dict[str, Any], annotation: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {key: deepcopy(seed.get(key)) for key in (
        "function_id",
        "name",
        "module_id",
        "function_kind",
        "coder_function_type",
        "visibility",
        "api_surface",
        "exported",
        "export_reason",
        "public_api_role",
        "grouping_hint",
        "purpose",
        "capability_ids",
        "covers_handler_ids",
        "covers_message_ids",
        "covers_field_ids",
        "trace_ref_keys",
        "status",
    )}
    if annotation:
        if str(annotation.get("purpose", "")).strip():
            item["purpose"] = str(annotation.get("purpose", "")).strip()
        if str(annotation.get("grouping_hint", "")).strip():
            item["grouping_hint"] = str(annotation.get("grouping_hint", "")).strip()
        item["trace_ref_keys"] = sorted(set([str(ref) for ref in item.get("trace_ref_keys", []) if str(ref).strip()] + [str(ref) for ref in annotation.get("trace_ref_keys", []) if str(ref).strip()]))
        if str(annotation.get("status", "")).strip():
            item["status"] = str(annotation.get("status", "inferred"))
    return item


def _sanitize_function_refs(function: dict[str, Any], legal: dict[str, set[str]]) -> dict[str, Any]:
    item = deepcopy(function)
    for key, legal_key in (
        ("capability_ids", "capability_ids"),
        ("covers_handler_ids", "handler_ids"),
        ("covers_message_ids", "message_ids"),
        ("covers_field_ids", "field_ids"),
    ):
        allowed = legal.get(legal_key, set())
        if allowed:
            item[key] = [str(ref) for ref in item.get(key, []) if str(ref) in allowed]
        else:
            item[key] = [str(ref) for ref in item.get(key, []) if str(ref).strip()]
    return item


def reconcile_function_annotation_candidate(space: dict[str, Any], annotation_candidate: dict[str, Any] | None) -> dict[str, Any]:
    annotation_candidate = annotation_candidate or {}
    annotations = {
        str(item.get("seed_id", "")): item
        for item in annotation_candidate.get("seed_annotations", [])
        if isinstance(item, dict) and str(item.get("seed_id", "")).strip()
    }
    legal = _legal_refs(space)
    diagnostics: list[dict[str, Any]] = [dict(item) for item in space.get("richness_diagnostics", []) if isinstance(item, dict)]
    functions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    seed_groups = (
        "mandatory_function_seeds",
        "obligation_function_seeds",
        "handler_function_seeds",
        "parser_serializer_function_seeds",
        "recommended_function_families",
    )
    for group in seed_groups:
        for seed in space.get(group, []):
            if not isinstance(seed, dict):
                continue
            function = _sanitize_function_refs(_function_from_seed(seed, annotations.get(str(seed.get("seed_id", "")))), legal)
            if str(function.get("function_id", "")) in seen_ids or str(function.get("name", "")) in seen_names:
                diagnostics.append({"level": "warning", "code": "duplicate_function_seed_merged", "message": f"merged duplicate function seed '{function.get('name')}'"})
                continue
            seen_ids.add(str(function.get("function_id", "")))
            seen_names.add(str(function.get("name", "")))
            functions.append(function)
    accepted_optional: list[dict[str, Any]] = []
    rejected_optional: list[dict[str, Any]] = []
    policy = space.get("optional_expansion_policy", {}) if isinstance(space.get("optional_expansion_policy"), dict) else {}
    allowed_families = set(policy.get("allowed_families", []))
    family_aliases = {str(key): str(value) for key, value in policy.get("family_aliases", {}).items()} if isinstance(policy.get("family_aliases"), dict) else {}
    max_optional_functions = int(policy.get("max_optional_functions", len(annotation_candidate.get("optional_function_proposals", []))) or 0)
    for proposal in annotation_candidate.get("optional_function_proposals", []):
        if not isinstance(proposal, dict):
            continue
        if len(accepted_optional) >= max_optional_functions:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "function_budget_exceeded"})
            continue
        name = str(proposal.get("name_hint", "")).strip()
        reason = str(proposal.get("expansion_reason", "")).strip()
        family = str(proposal.get("family", "")).strip()
        family = family_aliases.get(family, family)
        source_refs = [str(ref) for ref in proposal.get("source_refs", []) if str(ref).strip()]
        if not name or not reason or not source_refs:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "missing name_hint, expansion_reason, or source_refs"})
            continue
        if _looks_like_abstract_function_family_name(name):
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "name_hint copies an abstract decomposition family; use a concrete helper name"})
            continue
        if allowed_families and family and family not in allowed_families:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": f"unknown optional function family '{family}'"})
            continue
        function_id = f"fn:{space.get('module_id')}:{_safe_id(name)}"
        if function_id in seen_ids or name in seen_names:
            rejected_optional.append({"proposal_key": proposal.get("proposal_key", ""), "reason": "duplicate optional function id/name"})
            continue
        function_kind = str(proposal.get("function_kind", "internal_helper"))
        coder_type = str(proposal.get("coder_function_type", "ALGORITHM"))
        proposal_capability_ids = [str(ref) for ref in proposal.get("capability_ids", []) if str(ref).strip()]
        local_capability_ids = legal.get("local_capability_ids", set())
        accepted_capability_ids = [ref for ref in proposal_capability_ids if ref in local_capability_ids]
        dropped_capability_ids = sorted(set(proposal_capability_ids) - set(accepted_capability_ids))
        if dropped_capability_ids:
            diagnostics.append(
                {
                    "level": "warning",
                    "code": "optional_function_provider_capability_refs_removed",
                    "message": f"optional function proposal '{proposal.get('proposal_key', '')}' referenced non-local capabilities: {', '.join(dropped_capability_ids)}",
                }
            )
        item = {
            "function_id": function_id,
            "name": name,
            "module_id": str(space.get("module_id", "")),
            "function_kind": function_kind if function_kind in FUNCTION_KIND_VALUES else "internal_helper",
            "coder_function_type": coder_type if coder_type in CODER_FUNCTION_TYPE_VALUES else "ALGORITHM",
            "visibility": "internal",
            "api_surface": "module_internal",
            "exported": False,
            "export_reason": "",
            "public_api_role": "",
            "grouping_hint": family or str(proposal.get("grouping_hint", "optional_helper")),
            "purpose": str(proposal.get("purpose", "")) or reason,
            "capability_ids": accepted_capability_ids,
            "covers_handler_ids": deepcopy(proposal.get("covers_handler_ids", [])),
            "covers_message_ids": deepcopy(proposal.get("covers_message_ids", [])),
            "covers_field_ids": deepcopy(proposal.get("covers_field_ids", [])),
            "trace_ref_keys": sorted(set(source_refs + dropped_capability_ids + [str(ref) for ref in proposal.get("trace_ref_keys", []) if str(ref).strip()])),
            "status": str(proposal.get("status", "inferred")),
        }
        item = _sanitize_function_refs(item, legal)
        functions.append(item)
        seen_ids.add(function_id)
        seen_names.add(name)
        accepted_optional.append({"proposal_key": proposal.get("proposal_key", ""), "function_id": function_id, "name": name, "family": family})
    candidate = {
        "schema_version": "function_inventory_candidate/v2",
        "candidate_id": f"candidate:function_inventory:{space.get('module_id')}",
        "producer": _producer("5.4b_function_inventory", "function_annotation_candidate_prompt"),
        "module_id": str(space.get("module_id", "")),
        "functions": functions,
        "assumptions": deepcopy(annotation_candidate.get("assumptions", [])),
        "unresolved_questions": deepcopy(annotation_candidate.get("unresolved_questions", [])),
    }
    return {
        "candidate": candidate,
        "reconciliation_report": {
            "schema_version": "function_reconciliation_report/v1",
            "module_id": space.get("module_id", ""),
            "required_seed_count": sum(len(space.get(group, [])) for group in seed_groups if group != "recommended_function_families"),
            "recommended_seed_count": len(space.get("recommended_function_families", [])),
            "accepted_optional_functions": accepted_optional,
            "rejected_optional_functions": rejected_optional,
            "diagnostic_count": len(diagnostics),
        },
        "diagnostics": diagnostics,
    }
