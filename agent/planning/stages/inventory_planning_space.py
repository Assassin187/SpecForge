from __future__ import annotations

from typing import Any

from .function_inventory_decomposition import select_top_decomposition_hints
from .implementation_plan import _safe_id, _wire_fields
from .implementation_plan_context import (
    SYSTEM_TYPE_IDS,
    _field_summaries,
    _legal_ids_from_draft,
    _message_summaries,
    _module_text,
    _protocol_prefix,
    derive_type_generation_targets,
    derive_type_obligations,
    provider_public_type_seeds_for_module,
)


FUNCTION_KINDS = {"public_api", "handler", "parser", "serializer", "validator", "state_machine", "resource_lifecycle", "error_helper", "internal_helper"}
CODEC_FUNCTION_FAMILIES = {
    "parser_helpers",
    "serializer_helpers",
    "parser_context_lifecycle",
    "feed_or_parse_entry",
    "frame_boundary_detection",
    "primitive_reader_or_tokenizer",
    "message_or_command_specific_parser",
    "validation_or_malformed_input_handling",
    "decoded_object_cleanup",
    "encode_or_response_entry",
    "message_or_response_specific_encoder",
    "primitive_writer",
    "status_header_or_option_writer",
    "buffer_size_or_allocation_helper",
    "error_response_helper",
    "encoded_buffer_cleanup",
}

EXAMPLE_FUNCTION_BASELINES = {
    "network": 28,
    "mqtt_codec": 16,
    "protocol_codec": 16,
    "session": 14,
    "router": 20,
    "topic": 14,
    "message_router": 6,
    "broker_app": 12,
    "dispatcher": 8,
    "timer_mgr": 6,
}

RECOMMENDED_FAMILY_PRIORITY = {
    "create_configure_start_run_stop_destroy": 0,
    "lifecycle_control": 1,
    "feed_or_parse_entry": 2,
    "primitive_reader_or_tokenizer": 2,
    "frame_boundary_detection": 2,
    "encode_or_response_entry": 3,
    "primitive_writer": 3,
    "buffer_size_or_allocation_helper": 3,
    "state_transition_handler": 4,
    "connection_io": 4,
    "session_registry": 4,
    "message_or_command_specific_parser": 5,
    "message_or_response_specific_encoder": 6,
    "lookup_or_match": 7,
    "topic_tree_mutation": 7,
    "protocol_error_handling": 8,
    "error_response_helper": 9,
    "encoded_buffer_cleanup": 10,
    "centralized_cleanup": 11,
}


def _trace(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item) for item in value if str(item).strip())
        elif str(value).strip():
            result.append(str(value))
    return sorted(dict.fromkeys(result))


def _decision_ref(kind: str, module_id: str, *parts: Any) -> str:
    tail = ":".join(_safe_id(str(part)) for part in parts if str(part).strip())
    return f"decision:{kind}:{module_id}:{tail or 'inferred'}"


def _type_artifact_kind(name: str, role: str) -> tuple[str, str, str]:
    text = f"{name} {role}".lower()
    if "connection" in text and "server" not in text and any(word in text for word in ("per-connection", "socket", "fd", "buffer")):
        if name.endswith("_t"):
            return "opaque_handle", "public", "public_header"
        return "internal_state", "module_internal", "source_file"
    if any(word in text for word in ("buffer", "payload", "bytes")):
        return "owned_buffer", "public", "public_header"
    if any(word in text for word in ("packet", "container", "decoded message", "decoded protocol")):
        return "struct", "public", "public_header"
    if any(word in text for word in ("callback", "cb", "hook")):
        return "callback_type", "public", "public_header"
    if any(word in text for word in ("enum", "flags", "bitflag")):
        return "enum", "public", "public_header"
    if name.endswith("_t") or any(word in text for word in ("opaque", "handle", "context")):
        return "opaque_handle", "public", "public_header"
    return "struct", "public", "public_header"


def _type_kind_for_target(target_kind: str, suggested_name: str) -> tuple[str, str, str]:
    if target_kind == "packet_enum":
        return "enum", "public", "public_header"
    if target_kind == "owned_buffer":
        return "owned_buffer", "public", "public_header"
    if target_kind == "callback_or_event_boundary":
        return ("callback_type", "public", "public_header") if suggested_name.endswith("_fn") else ("event_struct", "public", "public_header")
    if target_kind == "internal_state":
        return "internal_state", "private", "source_file"
    return "struct", "public", "public_header"


def _type_slot(
    *,
    module_id: str,
    slot_class: str,
    source_kind: str,
    source_id: str,
    name: str,
    kind: str,
    visibility: str,
    defined_in: str,
    source_reason: str,
    trace_ref_keys: list[str] | None = None,
    required_fields: list[dict[str, Any]] | None = None,
    source_refs: list[str] | None = None,
    default_include: bool = True,
) -> dict[str, Any]:
    stable = _safe_id(name.removeprefix("struct ")) or _safe_id(source_id) or slot_class
    return {
        "slot_id": f"slot:type:{module_id}:{slot_class}:{stable}",
        "slot_class": slot_class,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_refs": source_refs or [],
        "type_id": f"type:{module_id}:{stable}",
        "name": name,
        "module_id": module_id,
        "kind": kind,
        "visibility": visibility,
        "defined_in": defined_in,
        "source_reason": source_reason,
        "required_fields": required_fields or [],
        "trace_ref_keys": trace_ref_keys or [],
        "default_include": default_include,
    }


def _dedupe_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for slot in slots:
        key = (str(slot.get("module_id", "")), _safe_id(str(slot.get("name", "")).removeprefix("struct ")))
        existing = seen.get(key)
        if existing is not None:
            if existing.get("kind") == "opaque_handle" or slot.get("kind") == "opaque_handle":
                if existing.get("kind") != "opaque_handle":
                    existing["kind"] = "opaque_handle"
                    existing["visibility"] = "public"
                    existing["defined_in"] = "public_header"
                    existing["required_fields"] = []
                    existing["source_reason"] = str(slot.get("source_reason", "")) or str(existing.get("source_reason", ""))
                existing["trace_ref_keys"] = _trace(existing.get("trace_ref_keys", []), slot.get("trace_ref_keys", []))
                existing["source_refs"] = _trace(existing.get("source_refs", []), slot.get("source_refs", []))
                continue
            if not existing.get("required_fields") and slot.get("required_fields"):
                existing["required_fields"] = slot.get("required_fields", [])
                existing["source_reason"] = str(existing.get("source_reason", "")) or str(slot.get("source_reason", ""))
            existing["trace_ref_keys"] = _trace(existing.get("trace_ref_keys", []), slot.get("trace_ref_keys", []))
            existing["source_refs"] = _trace(existing.get("source_refs", []), slot.get("source_refs", []))
            continue
        seen[key] = slot
        result.append(slot)
    return result


def _merge_slot_details(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> None:
    by_key = {
        (str(slot.get("module_id", "")), _safe_id(str(slot.get("name", "")).removeprefix("struct "))): slot
        for slot in primary
        if isinstance(slot, dict)
    }
    for slot in secondary:
        if not isinstance(slot, dict):
            continue
        key = (str(slot.get("module_id", "")), _safe_id(str(slot.get("name", "")).removeprefix("struct ")))
        existing = by_key.get(key)
        if existing is None:
            continue
        if existing.get("kind") == "opaque_handle" or slot.get("kind") == "opaque_handle":
            if existing.get("kind") != "opaque_handle":
                existing["kind"] = "opaque_handle"
                existing["visibility"] = "public"
                existing["defined_in"] = "public_header"
                existing["required_fields"] = []
                existing["source_reason"] = str(slot.get("source_reason", "")) or str(existing.get("source_reason", ""))
            existing["trace_ref_keys"] = _trace(existing.get("trace_ref_keys", []), slot.get("trace_ref_keys", []))
            existing["source_refs"] = _trace(existing.get("source_refs", []), slot.get("source_refs", []))
            continue
        if slot.get("required_fields"):
            existing["required_fields"] = slot.get("required_fields", [])
        existing["trace_ref_keys"] = _trace(existing.get("trace_ref_keys", []), slot.get("trace_ref_keys", []))
        existing["source_refs"] = _trace(existing.get("source_refs", []), slot.get("source_refs", []))


def _provider_public_types(draft: dict[str, Any], module_artifact: dict[str, Any], planning_ir: dict[str, Any] | None) -> list[dict[str, Any]]:
    module_deps = {str(dep) for dep in module_artifact.get("dependencies", []) if str(dep).strip()}
    accepted = [
        type_item
        for type_item in draft.get("type_inventory", [])
        if isinstance(type_item, dict)
        and str(type_item.get("module_id", "")) in module_deps
        and str(type_item.get("visibility", "")) == "public"
        and str(type_item.get("defined_in", "")) == "public_header"
    ]
    seeded = [
        type_item
        for group in provider_public_type_seeds_for_module(draft, module_artifact, planning_ir)
        for type_item in group.get("types", [])
        if isinstance(type_item, dict)
    ]
    by_id: dict[str, dict[str, Any]] = {}
    for item in [*accepted, *seeded]:
        type_id = str(item.get("type_id", "")).strip()
        if type_id:
            by_id.setdefault(type_id, item)
    return list(by_id.values())


def _provider_state_fields(provider_types: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for type_item in provider_types:
        name = str(type_item.get("name", "")).strip()
        type_id = str(type_item.get("type_id", "")).strip()
        if not name or not type_id:
            continue
        if str(type_item.get("kind", "")) not in {"opaque_handle", "struct", "owned_buffer", "result_struct"}:
            continue
        base = _safe_id(name.removeprefix("struct ").removesuffix("_t"))
        field_name = base
        for prefix in ("mqtt_", "protocol_"):
            if field_name.startswith(prefix):
                field_name = field_name[len(prefix) :]
        field_name = field_name or base
        if field_name in seen_names:
            continue
        seen_names.add(field_name)
        pointer = "*" if str(type_item.get("kind", "")) == "opaque_handle" and "*" not in name else ""
        fields.append(
            {
                "field_name": field_name,
                "field_type": f"{name}{pointer}",
                "type_ref": type_id,
                "required": True,
                "ownership": "BORROWED",
                "lifetime": "valid while provider module owns the referenced object",
                "length_field": "",
                "capacity_field": "",
                "validation_notes": "derived from dependency provider public type",
            }
        )
        if len(fields) >= 4:
            break
    return fields


def _add_internal_state_dependency_fields(slots: list[dict[str, Any]], provider_types: list[dict[str, Any]]) -> None:
    fields = _provider_state_fields(provider_types)
    if not fields:
        return
    for slot in slots:
        if slot.get("kind") == "internal_state" and not slot.get("required_fields"):
            slot["required_fields"] = fields
            slot["source_reason"] = f"{slot.get('source_reason', '')} Stores references to provider public boundaries.".strip()


def build_type_planning_space(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module_id = str(module_artifact.get("module_id", ""))
    protocol = _protocol_prefix(draft)
    mandatory: list[dict[str, Any]] = []
    for artifact in module_artifact.get("artifacts", []):
        if not isinstance(artifact, dict) or str(artifact.get("kind", "")).upper() != "TYPE":
            continue
        name = str(artifact.get("name", "")).strip()
        if not name:
            continue
        kind, visibility, defined_in = _type_artifact_kind(name, str(artifact.get("role", "")))
        mandatory.append(
            _type_slot(
                module_id=module_id,
                slot_class="mandatory",
                source_kind="module_artifact",
                source_id=name,
                name=name,
                kind=kind,
                visibility=visibility,
                defined_in=defined_in,
                source_reason=str(artifact.get("role", "")) or f"Mandatory TYPE artifact {name}.",
                trace_ref_keys=_trace(module_artifact.get("source_fact_ids", []), artifact.get("doc_ref", [])),
                source_refs=[name],
            )
        )

    derived: list[dict[str, Any]] = []
    for target in derive_type_generation_targets(draft, module_artifact, planning_ir):
        name = str(target.get("suggested_name", "")).strip()
        if not name:
            continue
        target_kind = str(target.get("target_kind", ""))
        kind, visibility, defined_in = _type_kind_for_target(target_kind, name)
        if target_kind == "callback_or_event_boundary":
            for field in target.get("required_fields", []):
                if not isinstance(field, dict):
                    continue
                callback_name = str(field.get("field_type", "")).strip()
                if not callback_name:
                    continue
                derived.append(
                    _type_slot(
                        module_id=module_id,
                        slot_class="derived",
                        source_kind="callback_event_boundary",
                        source_id=f"{target.get('target_id', '')}:{callback_name}",
                        name=callback_name,
                        kind="callback_type",
                        visibility="public",
                        defined_in="public_header",
                        source_reason=f"Callback function type for {field.get('field_name', 'event')}.",
                        trace_ref_keys=_trace(target.get("trace_ref_keys", []), _decision_ref("type_slot", module_id, "callback", callback_name)),
                        source_refs=_trace(target.get("target_id", ""), field.get("field_name", "")),
                    )
                )
        derived.append(
            _type_slot(
                module_id=module_id,
                slot_class="derived",
                source_kind="type_generation_target",
                source_id=str(target.get("target_id", "")),
                name=name,
                kind=kind,
                visibility=visibility,
                defined_in=defined_in,
                source_reason=str(target.get("reason", "")) or f"Derived {target_kind} target.",
                required_fields=[field for field in target.get("required_fields", []) if isinstance(field, dict)],
                trace_ref_keys=_trace(
                    target.get("trace_ref_keys", []),
                    target.get("source_message_ids", []),
                    target.get("source_field_ids", []),
                    _decision_ref("type_slot", module_id, target_kind, target.get("target_id", "")),
                ),
                source_refs=_trace(target.get("target_id", ""), target.get("source_message_ids", []), target.get("source_field_ids", [])),
            )
        )

    if (module_artifact.get("state_owned") or module_artifact.get("owned_capabilities")) and not any(slot.get("kind") == "internal_state" for slot in [*mandatory, *derived]):
        derived.append(
            _type_slot(
                module_id=module_id,
                slot_class="derived",
                source_kind="module_state_ownership",
                source_id=f"state:{module_id}:private",
                name=f"struct {protocol}_{_safe_id(module_id)}",
                kind="internal_state",
                visibility="private",
                defined_in="source_file",
                source_reason="State/resource ownership requires private implementation storage.",
                trace_ref_keys=_trace(module_artifact.get("source_fact_ids", []), _decision_ref("type_slot", module_id, "private_state")),
                source_refs=[f"module:{module_id}"],
            )
        )
    if not any(slot.get("kind") == "opaque_handle" for slot in [*mandatory, *derived]):
        derived.append(
            _type_slot(
                module_id=module_id,
                slot_class="derived",
                source_kind="module_boundary",
                source_id=f"module:{module_id}:handle",
                name=f"{protocol}_{_safe_id(module_id)}_t",
                kind="opaque_handle",
                visibility="public",
                defined_in="public_header",
                source_reason="Opaque module context handle for downstream signature/file-layout compatibility.",
                trace_ref_keys=_trace(module_artifact.get("source_fact_ids", []), _decision_ref("type_slot", module_id, "module_handle")),
                source_refs=[f"module:{module_id}"],
            )
        )
    _merge_slot_details(mandatory, derived)

    text = _module_text(module_artifact)
    recommended: list[dict[str, Any]] = []
    if any(word in text for word in ("parse", "decode", "framing", "field", "header", "remaining length")):
        recommended.extend(
            [
                _type_slot(
                    module_id=module_id,
                    slot_class="recommended",
                    source_kind="coder_compatibility",
                    source_id=f"recommended:{module_id}:parser_cursor",
                    name=f"{protocol}_{_safe_id(module_id)}_cursor_t",
                    kind="view_struct",
                    visibility="module_internal",
                    defined_in="internal_header",
                    source_reason="Parser-heavy modules benefit from an explicit cursor/view helper type.",
                    trace_ref_keys=[_decision_ref("type_slot", module_id, "recommended", "parser_cursor")],
                    source_refs=["parser_serializer_need"],
                ),
                _type_slot(
                    module_id=module_id,
                    slot_class="recommended",
                    source_kind="coder_compatibility",
                    source_id=f"recommended:{module_id}:decode_result",
                    name=f"{protocol}_{_safe_id(module_id)}_decode_result_t",
                    kind="result_struct",
                    visibility="module_internal",
                    defined_in="internal_header",
                    source_reason="Decoder results need a stable success/error/consumed-byte boundary.",
                    trace_ref_keys=[_decision_ref("type_slot", module_id, "recommended", "decode_result")],
                    source_refs=["parser_serializer_need", "error_strategy"],
                ),
            ]
        )
    if any(word in text for word in ("encode", "serialize", "writer", "buffer", "response")):
        recommended.append(
            _type_slot(
                module_id=module_id,
                slot_class="recommended",
                source_kind="coder_compatibility",
                source_id=f"recommended:{module_id}:encode_buffer",
                name=f"{protocol}_{_safe_id(module_id)}_encode_buffer_t",
                kind="owned_buffer",
                visibility="module_internal",
                defined_in="internal_header",
                source_reason="Serializer modules need an owned encode buffer helper.",
                trace_ref_keys=[_decision_ref("type_slot", module_id, "recommended", "encode_buffer")],
                source_refs=["parser_serializer_need"],
            )
        )
    if any(word in text for word in ("dispatch", "handler", "state machine", "validation")):
        recommended.append(
            _type_slot(
                module_id=module_id,
                slot_class="recommended",
                source_kind="coder_compatibility",
                source_id=f"recommended:{module_id}:dispatch_context",
                name=f"{protocol}_{_safe_id(module_id)}_dispatch_context_t",
                kind="view_struct",
                visibility="module_internal",
                defined_in="internal_header",
                source_reason="Handler and state transition code needs a stable dispatch context.",
                trace_ref_keys=[_decision_ref("type_slot", module_id, "recommended", "dispatch_context")],
                source_refs=["handler_boundary", "state_resource_ownership"],
            )
        )

    provider_types = _provider_public_types(draft, module_artifact, planning_ir)
    _add_internal_state_dependency_fields(derived, provider_types)
    canonical_public = [
        item
        for item in draft.get("canonical_types", [])
        if isinstance(item, dict) and str(item.get("owner_module_id", "")) in {module_id, *[str(dep) for dep in module_artifact.get("dependencies", [])]}
    ]
    local_slots = _dedupe_slots([*mandatory, *derived, *recommended])
    space = {
        "schema_version": "type_planning_space/v1",
        "module_id": module_id,
        "mandatory_type_slots": _dedupe_slots(mandatory),
        "derived_type_slots": _dedupe_slots(derived),
        "recommended_type_slots": _dedupe_slots(recommended),
        "allowed_type_refs": {
            "local_slots": [
                {
                    "type_id": slot.get("type_id"),
                    "name": slot.get("name"),
                    "slot_id": slot.get("slot_id"),
                    "module_id": slot.get("module_id"),
                    "kind": slot.get("kind"),
                    "visibility": "public" if str(slot.get("visibility", "")) == "public_header_when_needed" else slot.get("visibility"),
                    "defined_in": "public_header" if str(slot.get("visibility", "")) == "public_header_when_needed" else slot.get("defined_in"),
                }
                for slot in local_slots
            ],
            "provider_public_types": provider_types,
            "canonical_public_types": canonical_public,
            "system_types": SYSTEM_TYPE_IDS,
        },
        "forbidden_type_refs": [
            {"namespace": namespace, "reason": "Not a type reference namespace."}
            for namespace in ("state:", "message:", "field:", "handler:", "func:", "file:", "module:")
        ],
        "optional_expansion_policy": {
            "allowed": True,
            "allowed_visibility": ["private", "module_internal", "public_header_when_needed"],
            "required_source_bindings": [
                "message structure",
                "field group",
                "handler boundary",
                "resource lifecycle",
                "error strategy",
                "parser/serializer need",
                "callback/event boundary",
                "coder compatibility",
                "explicit assumption",
            ],
            "deterministic_identity": True,
        },
        "source_context": {
            "target_role": str((profile or {}).get("target_role", "")),
            "protocol_profile": profile or {},
            "engineering_constraints": constraints or {},
            "module_ownership": {
                "owned_capabilities": module_artifact.get("owned_capabilities", []),
                "state_owned": module_artifact.get("state_owned", []),
                "dependencies": module_artifact.get("dependencies", []),
            },
        },
        "richness_diagnostics": [],
    }
    space["richness_diagnostics"] = type_inventory_richness_diagnostics(space)
    return space


def _artifact_function_kind(name: str) -> tuple[str, str, str]:
    if name == "main":
        return "public_api", "ENTRYPOINT", "runtime_entrypoint"
    if name.endswith(("_create", "_destroy", "_start", "_run", "_serve", "_stop")):
        role = "runtime_run" if name.endswith(("_run", "_serve")) else f"runtime_{name.rsplit('_', 1)[-1]}"
        return "resource_lifecycle", "ALGORITHM", role
    if "decode" in name or "decoder" in name or "parse" in name:
        return "parser", "ALGORITHM", "decoder"
    if "encode" in name or "encoder" in name or "serialize" in name:
        return "serializer", "ALGORITHM", "encoder"
    if any(word in name for word in ("route", "publish", "subscribe", "handle", "dispatch")):
        return "handler", "ALGORITHM", "module_boundary_operation"
    return "public_api", "ALGORITHM", "module_boundary_operation"


def _function_seed(
    *,
    module_id: str,
    seed_class: str,
    source_kind: str,
    source_id: str,
    name: str,
    function_kind: str,
    purpose: str,
    capability_ids: list[str] | None = None,
    covers_handler_ids: list[str] | None = None,
    covers_message_ids: list[str] | None = None,
    covers_field_ids: list[str] | None = None,
    coder_function_type: str = "ALGORITHM",
    exported: bool = False,
    public_api_role: str = "",
    visibility: str | None = None,
    api_surface: str | None = None,
    trace_ref_keys: list[str] | None = None,
    family: str = "",
) -> dict[str, Any]:
    action = _safe_id(name.rsplit(":", 1)[-1].removeprefix(f"{module_id}_").removeprefix("fn_")) or _safe_id(source_id) or seed_class
    if name and "_" in name:
        action = _safe_id(name)
    function_kind = function_kind if function_kind in FUNCTION_KINDS else "internal_helper"
    return {
        "seed_id": f"seed:function:{module_id}:{seed_class}:{_safe_id(source_id) or action}",
        "seed_class": seed_class,
        "source_kind": source_kind,
        "source_id": source_id,
        "function_id": f"fn:{module_id}:{action}",
        "name": name,
        "module_id": module_id,
        "function_kind": function_kind,
        "coder_function_type": coder_function_type,
        "visibility": visibility or ("public" if exported else "internal"),
        "api_surface": api_surface or ("public" if exported else "module_internal"),
        "exported": exported,
        "export_reason": f"Exported by {source_kind}." if exported else "",
        "public_api_role": public_api_role if exported else "",
        "grouping_hint": family or seed_class,
        "purpose": purpose,
        "capability_ids": capability_ids or [],
        "covers_handler_ids": covers_handler_ids or [],
        "covers_message_ids": covers_message_ids or [],
        "covers_field_ids": covers_field_ids or [],
        "trace_ref_keys": trace_ref_keys or [],
        "status": "inferred",
        "family": family,
    }


def _dedupe_function_seeds(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for seed in seeds:
        function_id = str(seed.get("function_id", ""))
        name = str(seed.get("name", ""))
        existing = by_id.get(function_id) or by_name.get(name)
        if existing is not None:
            for key in ("capability_ids", "covers_handler_ids", "covers_message_ids", "covers_field_ids", "trace_ref_keys"):
                existing[key] = _trace(existing.get(key, []), seed.get(key, []))
            continue
        by_id[function_id] = seed
        by_name[name] = seed
        result.append(seed)
    return result


def _message_ids(planning_ir: dict[str, Any] | None) -> list[str]:
    if not planning_ir:
        return []
    return [str(item.get("message_id", "")) for item in _message_summaries(planning_ir) if str(item.get("message_id", "")).strip()]


def _field_ids(planning_ir: dict[str, Any] | None) -> list[str]:
    if not planning_ir:
        return []
    ids = [str(item.get("field_id", "")) for item in _field_summaries(planning_ir) if str(item.get("field_id", "")).strip()]
    if ids:
        return ids
    fields = planning_ir.get("normalization_index", {}).get("field_id_by_message_and_name", {})
    result: list[str] = []
    if isinstance(fields, dict):
        for field_map in fields.values():
            if isinstance(field_map, dict):
                result.extend(str(item) for item in field_map.values() if str(item).strip())
    return sorted(dict.fromkeys(result))


def _field_ids_by_message(planning_ir: dict[str, Any] | None) -> dict[str, list[str]]:
    if not planning_ir:
        return {}
    by_message: dict[str, list[str]] = {}
    for item in _field_summaries(planning_ir):
        message = str(item.get("message", "")).strip()
        field_id = str(item.get("field_id", "")).strip()
        if message and field_id:
            by_message.setdefault(f"message:{_safe_id(message)}", []).append(field_id)
    index = planning_ir.get("normalization_index", {}).get("field_id_by_message_and_name", {})
    if isinstance(index, dict):
        for message, field_map in index.items():
            if not isinstance(field_map, dict):
                continue
            by_message.setdefault(f"message:{_safe_id(str(message))}", [])
            by_message[f"message:{_safe_id(str(message))}"].extend(str(item) for item in field_map.values() if str(item).strip())
    return {message_id: sorted(dict.fromkeys(fields)) for message_id, fields in by_message.items()}


def _codec_boundary_text(module: dict[str, Any]) -> str:
    artifacts = " ".join(
        f"{artifact.get('name', '')} {artifact.get('role', '')}"
        for artifact in module.get("artifacts", [])
        if isinstance(artifact, dict)
    )
    return f"{_module_text(module)} {artifacts}".lower()


def _module_has_codec_provider(draft: dict[str, Any], module_artifact: dict[str, Any]) -> bool:
    deps = {str(dep) for dep in module_artifact.get("dependencies", []) if str(dep).strip()}
    if any(any(term in dep.lower() for term in ("codec", "parser", "encoder", "decoder", "protocol")) for dep in deps):
        return True
    codec_terms = {"codec", "parser", "encoder", "decoder", "message_decode", "message_encode", "wire format", "packet"}
    for provider in draft.get("module_artifacts", []):
        if not isinstance(provider, dict) or str(provider.get("module_id", "")) not in deps:
            continue
        if any(term in _codec_boundary_text(provider) for term in codec_terms):
            return True
    return False


def _is_transport_only_module(module_artifact: dict[str, Any], owned_caps: list[str]) -> bool:
    text = _codec_boundary_text(module_artifact)
    owns_wire_format = bool({"message_decode", "message_encode"} & set(owned_caps))
    is_transport = any(term in text for term in ("network", "transport", "socket", "tcp", "udp", "epoll", "connection", "read", "write", "send", "flush", "close"))
    return is_transport and not owns_wire_format


def _profile_text(profile: dict[str, Any] | None, key: str) -> str:
    value = (profile or {}).get(key, "")
    if isinstance(value, dict):
        value = value.get("value", value.get("label", ""))
    return str(value).lower()


def _example_baseline_for_module(module_id: str, module_artifact: dict[str, Any]) -> dict[str, Any]:
    safe_module = _safe_id(module_id)
    if safe_module in EXAMPLE_FUNCTION_BASELINES:
        baseline = EXAMPLE_FUNCTION_BASELINES[safe_module]
        source = f"specs-example/mqtt_specs:{safe_module}"
    else:
        text = _codec_boundary_text(module_artifact)
        matched_key = next((key for key in EXAMPLE_FUNCTION_BASELINES if key in text), "")
        baseline = EXAMPLE_FUNCTION_BASELINES.get(matched_key, 8)
        source = f"specs-example/mqtt_specs:{matched_key or 'median_module'}"
    return {"baseline": baseline, "source": source}


def _complexity_delta(
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    owned_caps: list[str],
) -> int:
    message_count = len(_message_ids(planning_ir))
    field_count = len(_field_ids(planning_ir))
    delta = 0
    if message_count >= 8:
        delta += 1
    if field_count >= 25:
        delta += 1
    if any(word in _profile_text(profile, "statefulness") for word in ("session", "transaction", "state_machine", "mixed")):
        delta += 1
    if any(word in _profile_text(profile, "routing_intensity") for word in ("medium", "high")) or "routing" in " ".join(owned_caps).lower():
        delta += 1
    if any(word in _profile_text(profile, "timing_model") for word in ("timer", "timeout", "retransmission", "keep_alive")):
        delta += 1
    if _is_transport_only_module(module_artifact, owned_caps) and "message_decode" not in owned_caps and "message_encode" not in owned_caps:
        delta -= 1
    return max(-2, min(4, delta))


def _message_specific_seed_limit(planning_ir: dict[str, Any] | None, profile: dict[str, Any] | None) -> int:
    message_count = len(_message_ids(planning_ir))
    field_count = len(_field_ids(planning_ir))
    complexity = 0
    if message_count >= 6:
        complexity += 1
    if field_count >= 25:
        complexity += 1
    if any(word in _profile_text(profile, "statefulness") for word in ("session", "transaction", "state_machine", "mixed")):
        complexity += 1
    return 6 if complexity >= 3 else 4 if complexity >= 1 else 2


def _function_budget(
    module_id: str,
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    owned_caps: list[str],
    required_seed_count: int,
) -> dict[str, Any]:
    example = _example_baseline_for_module(module_id, module_artifact)
    delta = _complexity_delta(module_artifact, planning_ir, profile, owned_caps)
    module_soft_cap = max(required_seed_count, int(example["baseline"]) + delta)
    return {
        "basis": "specs-example/mqtt_specs",
        "example_baseline": example,
        "complexity_delta": delta,
        "module_soft_cap": module_soft_cap,
        "required_seed_count": required_seed_count,
    }


def _recommended_priority(seed: dict[str, Any]) -> tuple[int, int, str]:
    family = str(seed.get("family", ""))
    source_priority = 0 if str(seed.get("source_kind", "")) == "engineering_role_helper" else 1
    return (RECOMMENDED_FAMILY_PRIORITY.get(family, 50), source_priority, str(seed.get("name", "")))


def _family_kind(family: str) -> str:
    text = family.lower()
    if any(word in text for word in ("parse", "reader", "token", "frame", "decode")):
        return "parser"
    if any(word in text for word in ("encode", "writer", "response")):
        return "serializer"
    if any(word in text for word in ("validate", "malformed", "error", "precondition")):
        return "validator"
    if any(word in text for word in ("state", "transition", "transaction")):
        return "state_machine"
    if any(word in text for word in ("cleanup", "destroy", "lifecycle", "free", "teardown", "shutdown")):
        return "resource_lifecycle"
    if any(word in text for word in ("dispatch", "handler", "lookup_or_switch", "classification")):
        return "handler"
    return "internal_helper"


def _module_function_prefix(protocol: str, module_id: str) -> str:
    safe_module = _safe_id(module_id)
    if safe_module.startswith(f"{protocol}_"):
        return safe_module
    return f"{protocol}_{safe_module}" if safe_module else protocol


def _c_symbol(value: str) -> bool:
    if not value or not (value[0].isalpha() or value[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in value)


def _obligation_function_name(protocol: str, module_id: str, obligation: dict[str, Any], raw_name: str) -> str:
    name = raw_name.strip()
    if _c_symbol(name) and ":" not in name:
        return name
    type_name = _safe_id(str(obligation.get("type_name", "")))
    reason = " ".join([str(obligation.get("obligation_id", "")), str(obligation.get("reason", "")), name]).lower()
    action = "destroy" if any(word in reason for word in ("destroy", "free", "cleanup", "release")) else "create"
    if type_name.endswith("_t"):
        type_name = type_name[:-2]
    if type_name.startswith(f"{protocol}_"):
        return f"{type_name}_{action}"
    return f"{_module_function_prefix(protocol, module_id)}_{action}"


def _role_specific_helper_seed_specs(module_id: str, prefix: str, module_artifact: dict[str, Any], owned_caps: list[str], *, owns_decode: bool, owns_encode: bool, guarded_by_codec_provider: bool) -> list[dict[str, Any]]:
    text = _module_text(module_artifact)
    specs: list[tuple[str, str, str, str, str]] = []
    if owns_decode and not guarded_by_codec_provider:
        specs.extend(
            [
                ("read_u16", "parser", "primitive_reader_or_tokenizer", "Read a two-byte network-order integer from the decoder cursor.", "codec_primitive_reader"),
                ("read_string", "parser", "primitive_reader_or_tokenizer", "Read an MQTT-style length-prefixed UTF-8 string from the decoder cursor.", "codec_string_reader"),
                ("try_parse_remaining_length", "parser", "frame_boundary_detection", "Parse the variable-length Remaining Length field and detect incomplete or malformed frames.", "codec_remaining_length_reader"),
            ]
        )
    if owns_encode and not guarded_by_codec_provider:
        specs.extend(
            [
                ("put_u16", "serializer", "primitive_writer", "Write a two-byte network-order integer into the encoder buffer.", "codec_primitive_writer"),
                ("remaining_length_bytes", "serializer", "buffer_size_or_allocation_helper", "Compute the MQTT Remaining Length encoded byte sequence size.", "codec_remaining_length_writer"),
            ]
        )
    if _is_transport_only_module(module_artifact, owned_caps):
        specs.extend(
            [
                ("connection_read", "internal_helper", "connection_io", "Read available bytes from a connection into its receive buffer.", "network_connection_read"),
                ("connection_send", "internal_helper", "connection_io", "Queue or write outbound bytes for a connection.", "network_connection_send"),
                ("connection_flush", "internal_helper", "connection_io", "Flush pending outbound bytes while preserving partial-write state.", "network_connection_flush"),
            ]
        )
    if any(term in text for term in ("session", "session_manager", "client registry", "state_machine")):
        specs.extend(
            [
                ("session_manager_add", "internal_helper", "session_registry", "Add or replace a session entry in the session manager.", "session_manager_add"),
                ("session_manager_get", "internal_helper", "session_registry", "Look up an active session entry by client identifier.", "session_manager_get"),
                ("session_manager_remove", "internal_helper", "session_registry", "Remove a session entry and preserve cleanup ownership.", "session_manager_remove"),
            ]
        )
    if any(term in text for term in ("topic", "subscription", "routing", "router", "message_router")):
        specs.extend(
            [
                ("topic_match", "internal_helper", "lookup_or_match", "Match a topic name against a subscription filter.", "topic_match"),
                ("entry_remove_sid", "internal_helper", "topic_tree_mutation", "Remove a subscriber/session identifier from a topic tree entry.", "topic_entry_remove_sid"),
            ]
        )
    if any(term in text for term in ("broker", "server", "client", "app", "role_composition", "semantic_dispatch", "callback")):
        specs.extend(
            [
                ("handle_packet", "handler", "handler_helpers", "Dispatch a decoded packet to the broker/session/routing operation for its packet type.", "broker_handle_packet"),
                ("on_data_cb", "handler", "handler_helpers", "Adapt a network data callback into packet decode and broker dispatch.", "broker_on_data_cb"),
            ]
        )
    return [
        _function_seed(
            module_id=module_id,
            seed_class="recommended",
            source_kind="engineering_role_helper",
            source_id=f"role_helper:{module_id}:{source_id}",
            name=f"{prefix}_{suffix}",
            function_kind=kind,
            purpose=purpose,
            capability_ids=owned_caps[:1],
            trace_ref_keys=[_decision_ref("function_slot", module_id, "role_helper", source_id)],
            family=family,
        )
        for suffix, kind, family, purpose, source_id in specs
    ]


def build_function_planning_space(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    planning_ir: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module_id = str(module_artifact.get("module_id", ""))
    protocol = _protocol_prefix(draft)
    prefix = _module_function_prefix(protocol, module_id)
    owned_caps = [str(cap) for cap in module_artifact.get("owned_capabilities", []) if str(cap).strip()]
    mandatory: list[dict[str, Any]] = []
    for artifact in module_artifact.get("artifacts", []):
        if not isinstance(artifact, dict) or str(artifact.get("kind", "")).upper() != "FUNC":
            continue
        name = str(artifact.get("name", "")).strip()
        if not name:
            continue
        kind, coder_type, public_role = _artifact_function_kind(name)
        mandatory.append(
            _function_seed(
                module_id=module_id,
                seed_class="mandatory",
                source_kind="module_artifact",
                source_id=name,
                name=name,
                function_kind=kind,
                coder_function_type=coder_type,
                purpose=str(artifact.get("role", "")) or f"Mandatory FUNC artifact {name}.",
                capability_ids=owned_caps[:1],
                exported=True,
                public_api_role=public_role,
                trace_ref_keys=_trace(module_artifact.get("source_fact_ids", []), artifact.get("doc_ref", []), _decision_ref("function_slot", module_id, "module_artifact", name)),
            )
        )

    obligations: list[dict[str, Any]] = []
    for obligation in derive_type_obligations(draft, module_artifact):
        names = [str(name) for name in obligation.get("required_function_names", []) if str(name).strip()]
        if not names:
            continue
        exported = str(obligation.get("visibility_hint", "")) == "public"
        kind = str(obligation.get("required_function_kind", "resource_lifecycle")) or "resource_lifecycle"
        obligations.append(
            _function_seed(
                module_id=module_id,
                seed_class="obligation",
                source_kind="type_obligation",
                source_id=str(obligation.get("obligation_id", "")),
                name=_obligation_function_name(protocol, module_id, obligation, names[0]),
                function_kind=kind,
                purpose=f"Satisfy type obligation {obligation.get('obligation_id')}: {obligation.get('reason')}",
                capability_ids=owned_caps[:1],
                exported=exported,
                public_api_role="module_boundary_operation" if exported else "",
                trace_ref_keys=_trace(obligation.get("trace_ref_keys", []), obligation.get("obligation_id", "")),
                family="resource_lifecycle_helpers",
            )
        )

    handlers: list[dict[str, Any]] = []
    handler_caps = [cap for cap in owned_caps if cap in {"semantic_dispatch", "state_machine", "protocol_error_policy"}]
    for handler in draft.get("handler_matrix", []):
        if not isinstance(handler, dict) or str(handler.get("owner_module_id", "")) != module_id:
            continue
        handler_id = str(handler.get("handler_id", "")).strip()
        if not handler_id:
            continue
        surface = str(handler.get("trigger") or handler_id)
        handler_suffix = _safe_id(handler_id.removeprefix("handler:")) or _safe_id(surface)
        name = f"{prefix}_handle_{handler_suffix}"
        handlers.append(
            _function_seed(
                module_id=module_id,
                seed_class="handler",
                source_kind="handler_matrix",
                source_id=handler_id,
                name=name,
                function_kind="handler",
                purpose=f"Handle protocol surface {surface}.",
                capability_ids=handler_caps,
                covers_handler_ids=[handler_id],
                trace_ref_keys=_trace(handler.get("trace_ref_keys", []), handler_id),
                family="handler_helpers",
            )
        )

    text = _module_text(module_artifact)
    parser_serializer: list[dict[str, Any]] = []
    decode_evidence = any(word in text for word in ("decode", "decoder", "parse", "parser", "framing"))
    encode_evidence = any(word in text for word in ("encode", "encoder", "serialize", "serializer", "response"))
    guarded_by_codec_provider = _is_transport_only_module(module_artifact, owned_caps) and _module_has_codec_provider(draft, module_artifact)
    owns_decode = "message_decode" in owned_caps or (decode_evidence and not guarded_by_codec_provider)
    owns_encode = "message_encode" in owned_caps or (encode_evidence and not guarded_by_codec_provider)
    message_ids = _message_ids(planning_ir)
    message_seed_limit = _message_specific_seed_limit(planning_ir, profile)
    message_specific_ids = message_ids[:message_seed_limit]
    field_ids = _field_ids(planning_ir)
    fields_by_message = _field_ids_by_message(planning_ir)
    if owns_decode:
        parser_serializer.append(
            _function_seed(
                module_id=module_id,
                seed_class="parser_serializer",
                source_kind="message_decode_capability",
                source_id=f"capability:{module_id}:message_decode",
                name=f"{prefix}_decode_message",
                function_kind="parser",
                purpose="Decode protocol bytes into accepted packet/message inventory types.",
                capability_ids=[cap for cap in owned_caps if "decode" in cap or "framing" in cap],
                covers_message_ids=message_ids,
                covers_field_ids=field_ids[:20],
                trace_ref_keys=_trace(message_ids, field_ids[:20], _decision_ref("function_slot", module_id, "message_decode", "entry")),
                family="parser_helpers",
            )
        )
        for message_id in message_specific_ids:
            tail = _safe_id(message_id.removeprefix("message:"))
            covered_fields = fields_by_message.get(message_id, [])
            parser_serializer.append(
                _function_seed(
                    module_id=module_id,
                    seed_class="parser_serializer",
                    source_kind="message_decode_capability",
                    source_id=f"{message_id}:decode",
                    name=f"{prefix}_decode_{tail}",
                    function_kind="parser",
                    purpose=f"Decode the {message_id} wire fields into the accepted packet/message model.",
                    capability_ids=[cap for cap in owned_caps if "decode" in cap or "framing" in cap],
                    covers_message_ids=[message_id],
                    covers_field_ids=covered_fields,
                    trace_ref_keys=_trace(message_id, covered_fields, _decision_ref("function_slot", module_id, "decode", tail)),
                    family="message_or_command_specific_parser",
                )
            )
        if field_ids:
            parser_serializer.append(
                _function_seed(
                    module_id=module_id,
                    seed_class="parser_serializer",
                    source_kind="wire_field_reader",
                    source_id=f"field_reader:{module_id}",
                    name=f"{prefix}_read_wire_field",
                    function_kind="parser",
                    purpose="Read primitive wire fields from the protocol input cursor.",
                    capability_ids=[cap for cap in owned_caps if "decode" in cap or "framing" in cap],
                    covers_field_ids=field_ids[:20],
                    trace_ref_keys=_trace(field_ids[:20], _decision_ref("function_slot", module_id, "primitive_reader")),
                    family="primitive_reader_or_tokenizer",
                )
            )
    if owns_encode:
        parser_serializer.append(
            _function_seed(
                module_id=module_id,
                seed_class="parser_serializer",
                source_kind="message_encode_capability",
                source_id=f"capability:{module_id}:message_encode",
                name=f"{prefix}_encode_message",
                function_kind="serializer",
                purpose="Encode accepted packet/message inventory types into protocol bytes.",
                capability_ids=[cap for cap in owned_caps if "encode" in cap or "framing" in cap],
                covers_message_ids=message_ids,
                covers_field_ids=field_ids[:20],
                trace_ref_keys=_trace(message_ids, field_ids[:20], _decision_ref("function_slot", module_id, "message_encode", "entry")),
                family="serializer_helpers",
            )
        )
        for message_id in message_specific_ids:
            tail = _safe_id(message_id.removeprefix("message:"))
            covered_fields = fields_by_message.get(message_id, [])
            parser_serializer.append(
                _function_seed(
                    module_id=module_id,
                    seed_class="parser_serializer",
                    source_kind="message_encode_capability",
                    source_id=f"{message_id}:encode",
                    name=f"{prefix}_encode_{tail}",
                    function_kind="serializer",
                    purpose=f"Encode the {message_id} packet/message model into wire bytes.",
                    capability_ids=[cap for cap in owned_caps if "encode" in cap or "framing" in cap],
                    covers_message_ids=[message_id],
                    covers_field_ids=covered_fields,
                    trace_ref_keys=_trace(message_id, covered_fields, _decision_ref("function_slot", module_id, "encode", tail)),
                    family="message_or_response_specific_encoder",
                )
            )
        if field_ids:
            parser_serializer.append(
                _function_seed(
                    module_id=module_id,
                    seed_class="parser_serializer",
                    source_kind="wire_field_writer",
                    source_id=f"field_writer:{module_id}",
                    name=f"{prefix}_write_wire_field",
                    function_kind="serializer",
                    purpose="Write primitive wire fields into the protocol output buffer.",
                    capability_ids=[cap for cap in owned_caps if "encode" in cap or "framing" in cap],
                    covers_field_ids=field_ids[:20],
                    trace_ref_keys=_trace(field_ids[:20], _decision_ref("function_slot", module_id, "primitive_writer")),
                    family="primitive_writer",
                )
            )
        parser_serializer.append(
            _function_seed(
                module_id=module_id,
                seed_class="parser_serializer",
                source_kind="encoded_buffer_cleanup",
                source_id=f"encoded_buffer:{module_id}:cleanup",
                name=f"{prefix}_free_encoded_buffer",
                function_kind="resource_lifecycle",
                purpose="Release owned encoded output buffers produced by serializers.",
                capability_ids=[cap for cap in owned_caps if "encode" in cap or "framing" in cap],
                trace_ref_keys=[_decision_ref("function_slot", module_id, "encoded_buffer_cleanup")],
                family="encoded_buffer_cleanup",
            )
        )

    mandatory = _dedupe_function_seeds(mandatory)
    obligations = _dedupe_function_seeds(obligations)
    handlers = _dedupe_function_seeds(handlers)
    parser_serializer = _dedupe_function_seeds(parser_serializer)
    required_seed_count = len(mandatory) + len(obligations) + len(handlers) + len(parser_serializer)
    function_budget = _function_budget(module_id, module_artifact, planning_ir, profile, owned_caps, required_seed_count)
    seed_slack = max(0, int(function_budget["module_soft_cap"]) - required_seed_count)
    reserved_optional_slots = 1 if seed_slack > 0 else 0
    max_recommended_seed_count = max(0, seed_slack - reserved_optional_slots)

    decomposition = select_top_decomposition_hints(
        module_artifact,
        {
            "provider_module_artifacts": [
                item
                for item in draft.get("module_artifacts", [])
                if isinstance(item, dict) and str(item.get("module_id", "")) in {str(dep) for dep in module_artifact.get("dependencies", [])}
            ],
            "consumer_module_artifact_dependencies": [
                item
                for item in draft.get("module_artifacts", [])
                if isinstance(item, dict) and module_id in {str(dep) for dep in item.get("dependencies", [])}
            ],
            "core_design_summary": draft,
        },
        max_hints=3,
    )
    recommended: list[dict[str, Any]] = []
    for rule_id in decomposition.get("selected_rule_ids", []):
        slots = decomposition.get("recommended_concrete_slots_by_rule", {}).get(rule_id, [])
        if not slots:
            slots = [
                {"family": family, "suffix": _safe_id(str(family)), "purpose": f"Cover {family} responsibility.", "function_kind": ""}
                for family in decomposition.get("expected_function_families_by_rule", {}).get(rule_id, [])[:3]
            ]
        for slot in slots[:7]:
            if not isinstance(slot, dict):
                continue
            family_text = str(slot.get("family", "")).strip()
            suffix = _safe_id(str(slot.get("suffix", ""))) or _safe_id(family_text)
            if not family_text or not suffix:
                continue
            if guarded_by_codec_provider and family_text in CODEC_FUNCTION_FAMILIES:
                continue
            purpose = str(slot.get("purpose", "")).strip() or f"Cover {family_text} responsibility."
            recommended.append(
                _function_seed(
                    module_id=module_id,
                    seed_class="recommended",
                    source_kind="decomposition_hint",
                    source_id=f"{rule_id}:{family_text}:{suffix}",
                    name=f"{prefix}_{suffix}",
                    function_kind=str(slot.get("function_kind", "")).strip() or _family_kind(family_text),
                    purpose=f"{purpose} Covers {family_text} responsibility from decomposition rule {rule_id}.",
                    capability_ids=owned_caps[:1],
                    trace_ref_keys=[f"decision:function_slot:{module_id}:{rule_id}:{family_text}:{suffix}"],
                    family=family_text,
                )
            )
    recommended.extend(
        _role_specific_helper_seed_specs(
            module_id,
            prefix,
            module_artifact,
            owned_caps,
            owns_decode=owns_decode,
            owns_encode=owns_encode,
            guarded_by_codec_provider=guarded_by_codec_provider,
        )
    )

    recommended = sorted(_dedupe_function_seeds(recommended), key=_recommended_priority)[:max_recommended_seed_count]
    function_budget["max_recommended_seed_count"] = max_recommended_seed_count
    function_budget["accepted_recommended_seed_count"] = len(recommended)
    function_budget["message_specific_seed_limit"] = message_seed_limit
    max_optional_functions = min(1, max(0, int(function_budget["module_soft_cap"]) - required_seed_count - len(recommended)))

    generic_optional_families = {
        "parser_helpers",
        "serializer_helpers",
        "validation_helpers",
        "handler_helpers",
        "dispatch_helpers",
        "state_transition_helpers",
        "resource_lifecycle_helpers",
        "error_helpers",
        "buffer_helpers",
        "protocol_event_helpers",
    }
    if guarded_by_codec_provider:
        generic_optional_families -= CODEC_FUNCTION_FAMILIES
    selected_families = {
        str(family)
        for rule_id in decomposition.get("selected_rule_ids", [])
        for family in decomposition.get("expected_function_families_by_rule", {}).get(rule_id, [])
        if str(family).strip() and not (guarded_by_codec_provider and str(family) in CODEC_FUNCTION_FAMILIES)
    }
    concrete_families = {
        str(slot.get("family", ""))
        for rule_id in decomposition.get("selected_rule_ids", [])
        for slot in decomposition.get("recommended_concrete_slots_by_rule", {}).get(rule_id, [])
        if isinstance(slot, dict)
        and str(slot.get("family", "")).strip()
        and not (guarded_by_codec_provider and str(slot.get("family", "")) in CODEC_FUNCTION_FAMILIES)
    }
    allowed_optional_families = sorted(generic_optional_families | selected_families | concrete_families)

    legal = _legal_ids_from_draft(draft)
    legal["local_capability_ids"] = sorted(dict.fromkeys(owned_caps))
    legal["message_ids"] = message_ids
    legal["field_ids"] = field_ids
    space = {
        "schema_version": "function_planning_space/v1",
        "module_id": module_id,
        "function_budget": function_budget,
        "mandatory_function_seeds": mandatory,
        "obligation_function_seeds": obligations,
        "handler_function_seeds": handlers,
        "parser_serializer_function_seeds": parser_serializer,
        "recommended_function_families": recommended,
        "legal_refs": legal,
        "optional_expansion_policy": {
            "allowed": True,
            "module_local_only": True,
            "must_reference_known_ids": True,
            "max_optional_functions": max_optional_functions,
            "allowed_families": [
                *allowed_optional_families,
            ],
            "family_aliases": {"handler_lookup_or_switch": "lookup_or_match"},
            "deterministic_identity": True,
        },
        "source_context": {
            "target_role": str((profile or {}).get("target_role", "")),
            "protocol_profile": profile or {},
            "engineering_constraints": constraints or {},
            "global_service_flow_hints": draft.get("service_flow_hints", []),
            "decomposition_context": decomposition,
            "wire_field_count": len(_wire_fields(planning_ir or {})),
        },
        "richness_diagnostics": [],
    }
    space["richness_diagnostics"] = function_inventory_richness_diagnostics(space)
    return space


def type_inventory_richness_diagnostics(space: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    slots = [*space.get("mandatory_type_slots", []), *space.get("derived_type_slots", []), *space.get("recommended_type_slots", [])]
    kinds = {str(slot.get("kind", "")) for slot in slots if isinstance(slot, dict)}
    module_id = str(space.get("module_id", ""))
    if not ({"opaque_handle", "internal_state"} & kinds):
        diagnostics.append({"level": "warning", "code": "low_type_implementation_richness", "message": f"module '{module_id}' lacks handle/internal_state planning slots"})
    if any(slot.get("source_kind") == "type_generation_target" for slot in slots) and not ({"enum", "struct", "owned_buffer"} & kinds):
        diagnostics.append({"level": "warning", "code": "coarse_type_decomposition", "message": f"module '{module_id}' has protocol targets but few concrete protocol type slots"})
    packet_stems: set[str] = set()
    payload_stems: set[str] = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        name = _safe_id(str(slot.get("name", "")).removeprefix("struct ").removesuffix("_t"))
        stem = name.replace("packet", "").replace("payload", "").strip("_")
        if "packet" in name:
            packet_stems.add(stem)
        if "payload" in name:
            payload_stems.add(stem)
    overlap = packet_stems & payload_stems
    if overlap:
        diagnostics.append({"level": "warning", "code": "duplicate_packet_payload_concept", "message": f"module '{module_id}' has packet/payload concepts with overlapping stems: {', '.join(sorted(overlap)[:4])}"})
    return diagnostics


def function_inventory_richness_diagnostics(space: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    module_id = str(space.get("module_id", ""))
    seeds = [
        *space.get("mandatory_function_seeds", []),
        *space.get("obligation_function_seeds", []),
        *space.get("handler_function_seeds", []),
        *space.get("parser_serializer_function_seeds", []),
        *space.get("recommended_function_families", []),
    ]
    kinds = {str(seed.get("function_kind", "")) for seed in seeds if isinstance(seed, dict)}
    if space.get("parser_serializer_function_seeds") and not ({"parser", "serializer"} & kinds):
        diagnostics.append({"level": "warning", "code": "missing_parser_or_serializer_helpers", "message": f"module '{module_id}' has codec capability evidence but no parser/serializer seed"})
    if len(space.get("recommended_function_families", [])) < 2:
        diagnostics.append({"level": "warning", "code": "low_helper_richness", "message": f"module '{module_id}' has few recommended helper family seeds"})
    return diagnostics
