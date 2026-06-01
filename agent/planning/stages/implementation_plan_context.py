from __future__ import annotations

from copy import deepcopy
from typing import Any

from .implementation_plan import _capability_refs, _compressed_refs, _field_value, _handler_surfaces, _safe_id, _surface_units, _target_directives, _wire_fields
from .function_inventory_decomposition import select_top_decomposition_hints


PROFILE_FIELDS = (
    "transport_shape",
    "interaction_model",
    "statefulness",
    "routing_intensity",
    "resource_intensity",
    "failure_semantics",
    "timing_model",
)

SYSTEM_TYPE_IDS = [
    "bool",
    "char",
    "double",
    "float",
    "int",
    "long",
    "short",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "socklen_t",
    "socket_t",
    "struct sockaddr",
    "struct sockaddr_in",
    "struct sockaddr_storage",
    "struct epoll_event",
    "void",
]

MQTT_PACKET_TYPE_VALUES = {
    "RESERVED": "0",
    "CONNECT": "1",
    "CONNACK": "2",
    "PUBLISH": "3",
    "SUBSCRIBE": "8",
    "SUBACK": "9",
    "PINGREQ": "12",
    "PINGRESP": "13",
    "DISCONNECT": "14",
}


def normalize_system_type_ref(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("system:"):
        bare = text.split(":", 1)[1].strip()
        return bare if bare in SYSTEM_TYPE_IDS else text
    return text


def _empty_callback_signature() -> dict[str, Any]:
    return {"return_type": "", "params": []}


def _empty_type_lifecycle() -> dict[str, list[str]]:
    return {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []}


def _is_public_type_item(type_item: dict[str, Any]) -> bool:
    return str(type_item.get("visibility", "")) == "public" and str(type_item.get("defined_in", "")) == "public_header"


def _type_ref_aliases(type_item: dict[str, Any]) -> set[str]:
    type_id = str(type_item.get("type_id", "")).strip()
    name = str(type_item.get("name", "")).strip()
    aliases = {type_id, name, name.removeprefix("struct ")}
    if type_id.startswith("type:") and ":" in type_id:
        tail = type_id.rsplit(":", 1)[-1]
        aliases.update({tail, f"type:{tail}"})
    return {alias for alias in aliases if alias}


def _type_lookup(types: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for type_item in types:
        if isinstance(type_item, dict):
            for alias in _type_ref_aliases(type_item):
                result.setdefault(alias, type_item)
    return result


def _is_callback_collection(type_item: dict[str, Any]) -> bool:
    name = str(type_item.get("name", "")).lower()
    if "callbacks" in name or name.endswith("_cb_t"):
        return True
    signature = type_item.get("callback_signature", {}) if isinstance(type_item.get("callback_signature"), dict) else {}
    params = signature.get("params", []) if isinstance(signature.get("params", []), list) else []
    param_names = {str(param.get("name", "")).lower() for param in params if isinstance(param, dict)}
    return bool(param_names & {"on_accept", "on_data", "on_close", "on_timer"})


def _callback_collection_fields(type_item: dict[str, Any]) -> list[dict[str, Any]]:
    signature = type_item.get("callback_signature", {}) if isinstance(type_item.get("callback_signature"), dict) else {}
    fields: list[dict[str, Any]] = []
    for param in signature.get("params", []) if isinstance(signature.get("params", []), list) else []:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name", "")).strip()
        if not name.startswith("on_"):
            continue
        field_type = str(param.get("type") or param.get("type_ref") or "").strip()
        fields.append(
            {
                "field_name": name,
                "field_type": field_type,
                "type_ref": normalize_system_type_ref(param.get("type_ref", "")),
                "required": True,
                "ownership": "BORROWED",
                "lifetime": "valid for runtime lifetime",
                "length_field": "",
                "capacity_field": "",
                "validation_notes": "normalized callback collection field",
            }
        )
    return fields


def _is_payload_type(type_item: dict[str, Any]) -> bool:
    text = f"{type_item.get('name', '')} {type_item.get('purpose', '')} {type_item.get('kind', '')}".lower()
    return "payload" in text or "packet specific" in text or "variant" in text


def _is_packet_container_type(type_item: dict[str, Any]) -> bool:
    text = f"{type_item.get('name', '')} {type_item.get('purpose', '')}".lower()
    field_types = " ".join(str(field.get("field_type", "")) for field in type_item.get("fields", []) if isinstance(field, dict)).lower()
    return "packet" in text and ("container" in text or "decoded" in text or "union" in field_types)


def _is_private_impl_detail_ref(ref: Any) -> bool:
    text = str(ref or "").strip().lower()
    tail = text.rsplit(":", 1)[-1]
    return text.startswith("type:") and any(token in tail for token in ("connection", "parser", "session"))


def _void_boundary_field(field: dict[str, Any], *, note: str) -> None:
    field["field_type"] = "void*"
    field["type_ref"] = "void"
    existing = str(field.get("validation_notes", "")).strip()
    field["validation_notes"] = f"{existing}; {note}" if existing else note


def normalize_type_inventory_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(candidate)
    types = [item for item in result.get("types", []) if isinstance(item, dict)]
    by_ref = _type_lookup(types)
    for type_item in types:
        if not isinstance(type_item, dict):
            continue
        type_item["dependencies"] = [normalize_system_type_ref(dep) for dep in type_item.get("dependencies", [])]
        if _is_public_type_item(type_item) and _is_callback_collection(type_item):
            fields = _callback_collection_fields(type_item)
            if fields:
                type_item["kind"] = "event_struct"
                type_item["fields"] = fields
                type_item["callback_signature"] = _empty_callback_signature()
        if _is_public_type_item(type_item) and _is_packet_container_type(type_item):
            for dependency in type_item.get("dependencies", []):
                dep = by_ref.get(str(dependency))
                if isinstance(dep, dict) and dep.get("module_id") == type_item.get("module_id") and _is_payload_type(dep):
                    dep["visibility"] = "public"
                    dep["defined_in"] = "public_header"
        if _is_public_type_item(type_item) and str(type_item.get("kind", "")) == "callback_type":
            kept_dependencies: list[str] = []
            for dependency in type_item.get("dependencies", []):
                dep = by_ref.get(str(dependency))
                if isinstance(dep, dict) and dep.get("module_id") == type_item.get("module_id") and not _is_public_type_item(dep):
                    continue
                kept_dependencies.append(str(dependency))
            type_item["dependencies"] = kept_dependencies
        if not _is_public_type_item(type_item):
            type_item["dependencies"] = [
                str(dependency)
                for dependency in type_item.get("dependencies", [])
                if by_ref.get(str(dependency)) is not None or not _is_private_impl_detail_ref(dependency)
            ]
        for field in type_item.get("fields", []):
            if isinstance(field, dict):
                if str(field.get("field_type", "")).strip().lower() == "union":
                    field["type_ref"] = ""
                else:
                    field["type_ref"] = normalize_system_type_ref(field.get("type_ref", ""))
                if not _is_public_type_item(type_item) and by_ref.get(str(field.get("type_ref", ""))) is None and _is_private_impl_detail_ref(field.get("type_ref", "")):
                    _void_boundary_field(field, note="normalized private implementation-detail reference to opaque context")
        signature = type_item.get("callback_signature", {})
        if not isinstance(signature, dict):
            signature = {"return_type": "", "params": []}
            type_item["callback_signature"] = signature
        if not isinstance(signature.get("params", []), list):
            signature["params"] = []
        params = signature.get("params", []) if isinstance(signature, dict) else []
        for param in params:
            if isinstance(param, dict):
                param["type_ref"] = normalize_system_type_ref(param.get("type_ref", ""))
                dep = by_ref.get(str(param.get("type_ref", "")))
                if _is_public_type_item(type_item) and isinstance(dep, dict) and dep.get("module_id") == type_item.get("module_id") and not _is_public_type_item(dep):
                    if _is_private_impl_detail_ref(param.get("type_ref", "")):
                        param["type"] = "void*"
                        param["type_ref"] = "void"
                    else:
                        param["type_ref"] = ""
    return result


def _constraints(constraints: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "constraint_id": str(item.get("constraint_id", "")),
            "affected_capabilities": item.get("affected_capabilities", []),
            "obligation": str(item.get("obligation", "")),
            "severity": str(item.get("severity", "")),
            "validation_rule": str(item.get("validation_rule", "")),
        }
        for item in constraints.get("constraints", [])
        if isinstance(item, dict)
    ]


def _selected_modules(selected_architecture: dict[str, Any]) -> list[dict[str, Any]]:
    arch = selected_architecture.get("architecture", {})
    modules = arch.get("modules", []) if isinstance(arch, dict) else []
    return [
        {
            "module_id": str(module.get("module_id", "")),
            "name": str(module.get("name", module.get("module_id", ""))),
            "responsibilities": [str(item) for item in module.get("responsibilities", []) if str(item).strip()],
            "owned_capability_ids": [str(cap) for cap in module.get("owned_capabilities", []) if str(cap).strip()],
            "consumed_capability_ids": [str(cap) for cap in module.get("consumed_capabilities", []) if str(cap).strip()],
            "state_owned": [str(item) for item in module.get("state_owned", []) if str(item).strip()],
            "dependency_hints": [str(item) for item in module.get("dependency_hints", []) if str(item).strip()],
            "support_module": bool(module.get("support_module", False)),
        }
        for module in modules
        if isinstance(module, dict)
    ]


def _protocol_summary(planning_ir: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    target = _target_directives(planning_ir)
    return {
        "name": _field_value(profile.get("protocol_name"), str(planning_ir.get("protocol_name", "protocol"))),
        "target_role": _field_value(profile.get("target_role"), str(target.get("target_role", ""))),
        "minimum_scope": _field_value(profile.get("minimum_scope"), str(target.get("scope", "minimum_v1"))),
        "target_directives": target,
    }


def _required_capabilities(profile: dict[str, Any]) -> list[dict[str, Any]]:
    refs = _capability_refs(profile)
    return [
        {
            "capability_id": capability_id,
            "category": item.get("category"),
            "refs": _compressed_refs(item),
        }
        for capability_id, item in refs.items()
    ]


def _message_summaries(planning_ir: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in _wire_fields(planning_ir):
        message = str(field.get("message", ""))
        if not message or message in seen:
            continue
        seen.add(message)
        result.append({"message": message, "message_id": f"message:{message.lower().replace(' ', '_')}"})
    return result


def _field_summaries(planning_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field_id": item["field_id"],
            "message": item["message"],
            "field": item["field"],
            "field_type": item.get("field_type", ""),
            "access_path_id": item["access_path_id"],
            "access_path": item["access_path"],
        }
        for item in _wire_fields(planning_ir)
    ]


def _protocol_prefix(draft: dict[str, Any]) -> str:
    return str(draft.get("protocol_name", "protocol")).lower().replace("-", "_")


def _module_text(module_artifact: dict[str, Any]) -> str:
    parts: list[str] = [
        str(module_artifact.get("module_id", "")),
        str(module_artifact.get("name", "")),
        str(module_artifact.get("role", "")),
        " ".join(str(item) for item in module_artifact.get("owned_capabilities", [])),
        " ".join(str(item) for item in module_artifact.get("state_owned", [])),
    ]
    for artifact in module_artifact.get("artifacts", []):
        if isinstance(artifact, dict):
            parts.extend([str(artifact.get("name", "")), str(artifact.get("role", ""))])
    return " ".join(parts).lower()


def _message_fields_from_source(planning_ir: dict[str, Any] | None, draft: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    fields: list[dict[str, Any]] = []
    facts = (planning_ir or {}).get("protocol_facts", {}) if isinstance(planning_ir, dict) else {}
    message_model = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    entries = message_model.get("message_or_command_entries", []) if isinstance(message_model.get("message_or_command_entries"), list) else []
    for entry_idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        message = str(entry.get("name") or entry.get("surface_unit") or f"entry_{entry_idx}").strip()
        syntax = str(entry.get("syntax_or_layout", ""))
        entry_summary = str(entry.get("summary", ""))
        for field_idx, field in enumerate(entry.get("fields", []) if isinstance(entry.get("fields"), list) else []):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or f"field_{field_idx}").strip()
            field_summary = str(field.get("summary", ""))
            field_type = str(field.get("type") or field.get("value_type") or field.get("encoding") or field.get("wire_type") or "").strip()
            fields.append(
                {
                    "field_id": str(field.get("fact_id") or f"fact:message_model_message_or_command_entries_{entry_idx}_fields_{field_idx}"),
                    "message": message,
                    "field": field_name,
                    "field_type": field_type or _infer_protocol_field_type(field_name, field_summary, syntax, entry_summary),
                    "field_summary": field_summary,
                    "entry_summary": entry_summary,
                    "syntax_or_layout": syntax,
                    "access_path_id": f"access:{message.lower().replace(' ', '_')}:{field_name.lower().replace(' ', '_')}",
                    "access_path": f"{message.lower().replace(' ', '_')}.{field_name.lower().replace(' ', '_')}",
                }
            )
    if not fields:
        fields = [
            {
                "field_id": str(item.get("field_id", "")),
                "message": str(item.get("message", "")),
                "field": str(item.get("field", "")),
                "field_type": str(item.get("field_type", "")),
                "access_path_id": str(item.get("access_path_id", "")),
                "access_path": str(item.get("access_path", "")),
            }
            for item in draft.get("deterministic_indexes", {}).get("field_index", {}).values()
            if isinstance(item, dict)
        ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        message = str(field.get("message", "")).strip()
        if message:
            grouped.setdefault(message, []).append(field)
    return grouped


def _surface_messages_from_source(planning_ir: dict[str, Any] | None) -> list[dict[str, str]]:
    facts = (planning_ir or {}).get("protocol_facts", {}) if isinstance(planning_ir, dict) else {}
    message_model = facts.get("message_model", {}) if isinstance(facts, dict) and isinstance(facts.get("message_model"), dict) else {}
    surfaces = message_model.get("surface_catalog", []) if isinstance(message_model.get("surface_catalog"), list) else []
    result: list[dict[str, str]] = []
    for item in surfaces:
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            name = str(item.get("name", "")).strip()
            result.append({"name": name, "message_id": f"message:{name.lower().replace(' ', '_')}", "fact_id": str(item.get("fact_id", ""))})
    return result


def _surface_messages_from_draft(draft: dict[str, Any]) -> list[dict[str, str]]:
    surfaces = draft.get("deterministic_indexes", {}).get("surface_unit_index", {})
    result: list[dict[str, str]] = []
    if isinstance(surfaces, dict):
        for name, item in surfaces.items():
            surface_name = str(item.get("name", name) if isinstance(item, dict) else name).strip()
            if surface_name:
                result.append({"name": surface_name, "message_id": f"message:{surface_name.lower().replace(' ', '_')}", "fact_id": ""})
    return result


def _infer_protocol_field_type(field_name: str, field_summary: str = "", syntax: str = "", entry_summary: str = "") -> str:
    name = field_name.lower().replace("-", "_")
    text = f"{name} {field_summary} {syntax} {entry_summary}".lower()
    local_text = f"{name} {field_summary}".lower()
    if name == "packet_type":
        return "mqtt_packet_type_t"
    if name == "remaining_length":
        return "uint32_t"
    if name in {"packet_id", "keep_alive"}:
        return "uint16_t"
    if name in {"client_id", "topic_name", "topic_filter", "protocol_name", "filter"}:
        return "char*"
    if name in {"clean_session", "retain", "dup", "has_packet_id", "session_present"} or " flag" in text or "boolean" in text:
        return "bool"
    if "payload" in name and ("byte" in text or "opaque" in text or "remaining bytes" in text):
        return "uint8_t*"
    if "u16" in local_text or "uint16" in local_text:
        return "uint16_t"
    if "string" in local_text:
        return "char*"
    if "qos" in name or "level" in name or "code" in name or "byte" in text:
        return "uint8_t"
    return "uint8_t"


def _target_field(field: dict[str, Any]) -> dict[str, str]:
    return {
        "field_name": str(field.get("field", "")).lower().replace(" ", "_"),
        "field_type": str(field.get("field_type") or _infer_protocol_field_type(str(field.get("field", "")))),
        "source_field_id": str(field.get("field_id", "")),
    }


def _payload_target_fields(protocol: str, message_key: str, fields: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    field_names = {str(field.get("field", "")).lower().replace(" ", "_") for field in fields}
    payload_fields = [_target_field(field) for field in fields]
    extra_targets: list[dict[str, Any]] = []
    if message_key == "subscribe" and {"topic_filter", "requested_qos"}.issubset(field_names):
        item_name = f"{protocol}_subscribe_topic_t"
        extra_targets.append(
            {
                "target_id": "target:payload:subscribe_topic",
                "target_kind": "payload_struct",
                "suggested_name": item_name,
                "owner_module_id": "",
                "source_message_ids": ["message:subscribe"],
                "source_field_ids": [str(field.get("field_id", "")) for field in fields if str(field.get("field", "")).lower().replace(" ", "_") in {"topic_filter", "requested_qos"}],
                "required_fields": [
                    {"field_name": "filter", "field_type": "char*", "source_field_id": ""},
                    {"field_name": "qos", "field_type": "uint8_t", "source_field_id": ""},
                ],
                "reason": "Repeated SUBSCRIBE topic filters need a stable item struct.",
                "trace_ref_keys": [str(field.get("field_id", "")) for field in fields if str(field.get("field_id", "")).strip()],
            }
        )
        packet_id = next((field for field in payload_fields if field["field_name"] == "packet_id"), {"field_name": "packet_id", "field_type": "uint16_t", "source_field_id": ""})
        payload_fields = [
            packet_id,
            {"field_name": "topics", "field_type": f"{item_name}*", "source_field_id": ""},
            {"field_name": "topic_count", "field_type": "size_t", "source_field_id": ""},
        ]
    if message_key == "publish":
        if any(field["field_name"] == "payload" and field["field_type"] == "uint8_t*" for field in payload_fields) and not any(field["field_name"] == "payload_len" for field in payload_fields):
            payload_fields.append({"field_name": "payload_len", "field_type": "size_t", "source_field_id": ""})
        if not any(field["field_name"] == "dup" for field in payload_fields):
            payload_fields.append({"field_name": "dup", "field_type": "bool", "source_field_id": ""})
        if not any(field["field_name"] == "has_packet_id" for field in payload_fields):
            payload_fields.append({"field_name": "has_packet_id", "field_type": "bool", "source_field_id": ""})
        if not any(field["field_name"] == "packet_id" for field in payload_fields):
            payload_fields.append({"field_name": "packet_id", "field_type": "uint16_t", "source_field_id": ""})
    return payload_fields, extra_targets


def derive_type_generation_targets(draft: dict[str, Any], module_artifact: dict[str, Any], planning_ir: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    module_id = str(module_artifact.get("module_id", ""))
    protocol = _protocol_prefix(draft)
    text = _module_text(module_artifact)
    grouped_fields = _message_fields_from_source(planning_ir, draft)
    artifact_function_names = " ".join(
        str(artifact.get("name", ""))
        for artifact in module_artifact.get("artifacts", [])
        if isinstance(artifact, dict) and str(artifact.get("kind", "")).upper() == "FUNC"
    ).lower()
    module_identity = f"{module_id} {module_artifact.get('name', '')}".lower()
    owned_caps = " ".join(str(item) for item in module_artifact.get("owned_capabilities", [])).lower()
    owns_codec = bool(grouped_fields) and (
        "codec" in module_identity
        or any(word in artifact_function_names for word in ("decode", "decoder", "encode", "encoder", "parse", "parser", "serialize", "serializer"))
        or any(word in owned_caps for word in ("message_decode", "message_encode", "framing"))
    )
    targets: list[dict[str, Any]] = []
    if owns_codec:
        surface_messages = _surface_messages_from_source(planning_ir) or _surface_messages_from_draft(draft)
        enum_messages = surface_messages or [{"name": message, "message_id": f"message:{message.lower().replace(' ', '_')}", "fact_id": ""} for message in grouped_fields]
        enum_names = ["RESERVED"] if protocol == "mqtt" else []
        enum_names.extend(str(item["name"]).upper().replace(" ", "_") for item in enum_messages)
        source_message_ids = [str(item["message_id"]) for item in enum_messages]
        targets.append(
            {
                "target_id": f"target:{module_id}:packet_enum",
                "target_kind": "packet_enum",
                "suggested_name": f"{protocol}_packet_type_t",
                "owner_module_id": module_id,
                "source_message_ids": source_message_ids,
                "source_field_ids": [],
                "required_fields": [
                    {
                        "field_name": enum_name,
                        "field_type": "enum_value",
                        "source_field_id": "",
                        "value": MQTT_PACKET_TYPE_VALUES.get(enum_name, str(index if protocol == "mqtt" else index + 1)),
                    }
                    for index, enum_name in enumerate(dict.fromkeys(enum_names))
                ],
                "reason": "Protocol message facts require a stable packet/control type enum.",
                "trace_ref_keys": source_message_ids,
            }
        )
        variant_fields: list[dict[str, Any]] = []
        for message, fields in grouped_fields.items():
            message_key = message.lower().replace(" ", "_")
            payload_fields, extra_payload_targets = _payload_target_fields(protocol, message_key, fields)
            for extra_target in extra_payload_targets:
                extra_target["target_id"] = f"target:{module_id}:{str(extra_target['target_id']).removeprefix('target:')}"
                extra_target["owner_module_id"] = module_id
                targets.append(extra_target)
            if message_key != "fixed_header":
                variant_fields.append({"field_name": message_key, "field_type": f"{protocol}_{message_key}_payload_t", "source_field_id": ""})
            targets.append(
                {
                    "target_id": f"target:{module_id}:payload:{message_key}",
                    "target_kind": "payload_struct",
                    "suggested_name": f"{protocol}_{message_key}_payload_t",
                    "owner_module_id": module_id,
                    "source_message_ids": [f"message:{message_key}"],
                    "source_field_ids": [str(field.get("field_id", "")) for field in fields if str(field.get("field_id", "")).strip()],
                    "required_fields": payload_fields,
                    "reason": "Message-specific protocol facts should lower into an implementation payload struct.",
                    "trace_ref_keys": [str(field.get("field_id", "")) for field in fields if str(field.get("field_id", "")).strip()],
                }
            )
        packet_fields: list[dict[str, Any]] = [{"field_name": "type", "field_type": f"{protocol}_packet_type_t", "source_field_id": ""}]
        if variant_fields:
            packet_fields.append({"field_name": "v", "field_type": "union", "source_field_id": "", "variants": variant_fields})
        targets.append(
            {
                "target_id": f"target:{module_id}:packet_container",
                "target_kind": "packet_container_struct",
                "suggested_name": f"{protocol}_packet_t",
                "owner_module_id": module_id,
                "source_message_ids": source_message_ids,
                "source_field_ids": [],
                "required_fields": packet_fields,
                "reason": "Decoder and semantic dispatch need one stable packet container type.",
                "trace_ref_keys": source_message_ids,
            }
        )
        if any(word in text for word in ("encode", "encoder", "serializer", "bytes", "buffer")):
            targets.append(
                {
                    "target_id": f"target:{module_id}:encoded_bytes",
                    "target_kind": "owned_buffer",
                    "suggested_name": f"{protocol}_bytes_t",
                    "owner_module_id": module_id,
                    "source_message_ids": source_message_ids,
                    "source_field_ids": [],
                    "required_fields": [
                        {"field_name": "data", "field_type": "uint8_t*", "source_field_id": ""},
                        {"field_name": "len", "field_type": "size_t", "source_field_id": ""},
                    ],
                    "reason": "Encoding results need an owned byte buffer/result type with release semantics.",
                    "trace_ref_keys": source_message_ids,
                }
            )
    if any(word in text for word in ("callback", "event", "timer", "epoll", "accept", "runtime", "server", "listen")):
        callback_fields: list[dict[str, str]] = []
        if any(word in text for word in ("accept", "server", "listen", "connection")):
            callback_fields.append({"field_name": "on_accept", "field_type": f"{protocol}_{module_id}_on_accept_fn", "source_field_id": ""})
        if any(word in text for word in ("data", "read", "receive", "io", "epoll", "connection")):
            callback_fields.append({"field_name": "on_data", "field_type": f"{protocol}_{module_id}_on_data_fn", "source_field_id": ""})
        if any(word in text for word in ("close", "closed", "destroy", "connection")):
            callback_fields.append({"field_name": "on_close", "field_type": f"{protocol}_{module_id}_on_close_fn", "source_field_id": ""})
        if "timer" in text:
            callback_fields.append({"field_name": "on_timer", "field_type": f"{protocol}_{module_id}_on_timer_fn", "source_field_id": ""})
        targets.append(
            {
                "target_id": f"target:{module_id}:callback_boundary",
                "target_kind": "callback_or_event_boundary",
                "suggested_name": f"{protocol}_{module_id}_callbacks_t",
                "owner_module_id": module_id,
                "source_message_ids": [],
                "source_field_ids": [],
                "required_fields": callback_fields,
                "reason": "Runtime/event responsibilities need stable callback or event boundary types.",
                "trace_ref_keys": module_artifact.get("source_fact_ids", []),
            }
        )
    if module_artifact.get("state_owned") or module_artifact.get("owned_capabilities"):
        targets.append(
            {
                "target_id": f"target:{module_id}:private_state",
                "target_kind": "internal_state",
                "suggested_name": f"struct {protocol}_{module_id}",
                "owner_module_id": module_id,
                "source_message_ids": [],
                "source_field_ids": [],
                "required_fields": [],
                "reason": "Resource-owning modules need private implementation state.",
                "trace_ref_keys": module_artifact.get("source_fact_ids", []),
            }
        )
    return targets


def _type_base_name(type_name: str) -> str:
    base = str(type_name).strip().removeprefix("struct ").removesuffix("_t")
    for suffix in ("_fn", "_callback", "_callbacks"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base or str(type_name).strip().replace(" ", "_")


def _has_owned_pointer_field(type_item: dict[str, Any]) -> bool:
    for field in type_item.get("fields", []):
        if not isinstance(field, dict):
            continue
        field_type = str(field.get("field_type", "")).lower()
        if ("*" in field_type or "buffer" in field_type or "string" in field_type) and str(field.get("ownership", "")) in {"OWNED", "TRANSFER"}:
            return True
    return False


def _is_non_function_lifecycle_name(name: Any) -> bool:
    key = _safe_id(str(name))
    return key in {"caller", "external", "application", "app", "user", "callee", "owner", "runtime", "system"} or key.endswith("_caller") or key.endswith("_application")


def _obligation_names(type_item: dict[str, Any], action: str) -> list[str]:
    lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
    if action in {"create", "initialize"}:
        explicit = lifecycle.get("created_by" if action == "create" else "initialized_by", [])
    elif action in {"destroy", "free", "release_owned_data"}:
        explicit = [*lifecycle.get("destroyed_by", []), *lifecycle.get("freed_by", [])]
    else:
        explicit = type_item.get("related_functions", [])
    module_id = str(type_item.get("module_id", ""))
    names = [
        str(name)
        for name in explicit
        if str(name).strip()
        and not _is_non_function_lifecycle_name(name)
        and not (module_id != "broker_app" and "_broker_" in str(name))
    ]
    if names:
        return sorted(set(names))
    base = _type_base_name(str(type_item.get("name", "")))
    if action == "create":
        return [f"{base}_create", f"{base}_init"]
    if action == "initialize":
        return [f"{base}_init", f"{base}_create"]
    if action in {"destroy", "free", "release_owned_data"}:
        return [f"{base}_destroy", f"{base}_free", f"{base}_cleanup"]
    if action == "register_callback":
        return [f"{base}_register", f"{base}_set", f"{base}_adapter"]
    return [f"{base}_{action}"]


def _type_obligation(type_item: dict[str, Any], action: str, *, reason: str, kind: str = "resource_lifecycle", visibility: str = "module_internal") -> dict[str, Any]:
    type_id = str(type_item.get("type_id", ""))
    return {
        "obligation_id": f"obligation:{type_id}:{action}",
        "type_id": type_id,
        "type_name": str(type_item.get("name", "")),
        "action": action,
        "required_function_names": _obligation_names(type_item, action),
        "required_function_kind": kind,
        "visibility_hint": visibility,
        "reason": reason,
        "trace_ref_keys": type_item.get("trace_ref_keys", []),
    }


def derive_type_obligations(draft: dict[str, Any], module_artifact: dict[str, Any]) -> list[dict[str, Any]]:
    module_id = str(module_artifact.get("module_id", ""))
    obligations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for type_item in draft.get("type_inventory", []):
        if not isinstance(type_item, dict) or str(type_item.get("module_id", "")) != module_id:
            continue
        lifecycle = type_item.get("lifecycle", {}) if isinstance(type_item.get("lifecycle"), dict) else {}
        actions: list[tuple[str, str, str, str]] = []
        if lifecycle.get("created_by"):
            actions.append(("create", "Type lifecycle declares creator functions.", "resource_lifecycle", "public" if type_item.get("visibility") == "public" else "module_internal"))
        if lifecycle.get("initialized_by"):
            actions.append(("initialize", "Type lifecycle declares initializer functions.", "resource_lifecycle", "public" if type_item.get("visibility") == "public" else "module_internal"))
        if lifecycle.get("destroyed_by"):
            actions.append(("destroy", "Type lifecycle declares destroy functions.", "resource_lifecycle", "public" if type_item.get("visibility") == "public" else "module_internal"))
        if lifecycle.get("freed_by") or type_item.get("kind") in {"owned_buffer", "result_struct"} or _has_owned_pointer_field(type_item):
            actions.append(("release_owned_data", "Owned pointer/string/buffer fields require a cleanup/free path.", "resource_lifecycle", "public" if type_item.get("visibility") == "public" else "module_internal"))
        if type_item.get("kind") == "callback_type":
            actions.append(("register_callback", "Callback boundary types require registration or adapter functions.", "public_api", "public" if type_item.get("visibility") == "public" else "module_internal"))
        for action, reason, kind, visibility in actions:
            key = (str(type_item.get("type_id", "")), action)
            if key in seen:
                continue
            seen.add(key)
            obligations.append(_type_obligation(type_item, action, reason=reason, kind=kind, visibility=visibility))
    return obligations


def _legal_ids_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    files = draft.get("file_layout", {}).get("files", [])
    return {
        "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
        "capability_ids": list(draft.get("traceability", {}).get("required_capabilities", [])),
        "constraint_ids": list(draft.get("traceability", {}).get("constraint_ids", [])),
        "state_ids": [str(item.get("state_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict)],
        "error_ids": [str(item.get("error_id", "")) for item in draft.get("error_strategy", []) if isinstance(item, dict)],
        "type_ids": sorted(
            {
                str(item.get("type_id", ""))
                for key in ("canonical_types", "type_inventory")
                for item in draft.get(key, [])
                if isinstance(item, dict) and str(item.get("type_id", "")).strip()
            }
        ),
        "system_type_ids": SYSTEM_TYPE_IDS,
        "handler_ids": [str(item.get("handler_id", "")) for item in draft.get("handler_matrix", []) if isinstance(item, dict)],
        "function_ids": [str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)],
        "file_ids": [str(item.get("file_id", "")) for item in files if isinstance(item, dict)],
    }


def _legal_ids_from_inputs(planning_ir: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_ids": [item["module_id"] for item in _selected_modules(selected_architecture)],
        "capability_ids": [item["capability_id"] for item in _required_capabilities(profile)],
        "constraint_ids": [item["constraint_id"] for item in _constraints(constraints)],
        "message_ids": [item["message_id"] for item in _message_summaries(planning_ir)],
        "field_ids": [item["field_id"] for item in _field_summaries(planning_ir)],
    }


def _compressed_trace_refs(profile: dict[str, Any]) -> dict[str, Any]:
    refs = _capability_refs(profile)
    return {
        "profile_fields": {name: _compressed_refs(profile.get(name, {})) for name in PROFILE_FIELDS},
        "capabilities": {capability_id: _compressed_refs(item) for capability_id, item in refs.items()},
    }


def _accepted_summary(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_artifacts": draft.get("module_artifacts", []),
        "canonical_types": draft.get("canonical_types", []),
        "state_design": draft.get("state_design", []),
        "handler_matrix": draft.get("handler_matrix", []),
        "resource_lifecycle": draft.get("resource_lifecycle", []),
        "error_strategy": draft.get("error_strategy", []),
        "function_contracts": [
            {
                "function_id": item.get("function_id"),
                "name": item.get("name"),
                "module_id": item.get("module_id"),
                "file_id": item.get("file_id"),
                "visibility": item.get("visibility"),
                "function_kind": item.get("function_kind"),
                "coder_function_type": item.get("coder_function_type"),
                "api_surface": item.get("api_surface"),
                "grouping_hint": item.get("grouping_hint"),
                "signature": item.get("signature", {}),
                "signature_dependencies": item.get("signature_dependencies", []),
                "service_requirements": item.get("service_requirements", []),
                "capability_ids": item.get("capability_ids", []),
            }
            for item in draft.get("function_contracts", [])
            if isinstance(item, dict)
        ],
    }


def _signature_update_skeleton(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "function_id": item.get("function_id"),
            "signature": item.get("signature", {}),
            "signature_dependencies": item.get("signature_dependencies", []),
            "interface_type_declarations": item.get("interface_type_declarations", []),
            "trace_ref_keys": item.get("traceability", {}).get("decision_ids", []),
            "status": "inferred",
        }
        for item in functions
        if isinstance(item, dict)
    ]


def _behavior_update_skeleton(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "function_id": item.get("function_id"),
            "contract": item.get("behavior_contract", {}),
            "error_behavior": item.get("error_behavior", {}),
            "state_access": item.get("state_access", []),
            "resource_access": item.get("resource_access", []),
            "internal_type_refs": item.get("internal_type_refs", []),
            "service_requirements": [],
            "logic_kind": item.get("logic_kind", ""),
            "forbidden_symbols": item.get("forbidden_symbols", []),
            "trace_ref_keys": item.get("traceability", {}).get("decision_ids", []),
            "status": "inferred",
        }
        for item in functions
        if isinstance(item, dict)
    ]


def _callable_functions(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for function in draft.get("function_contracts", []):
        if not isinstance(function, dict):
            continue
        visibility = str(function.get("visibility", "")).lower()
        same_module = str(function.get("module_id", "")) == module_id
        api_surface = str(function.get("api_surface", ""))
        cross_module_api = visibility == "public" or api_surface == "public"
        if same_module or (visibility not in {"private", "static"} and cross_module_api):
            result.append(
                {
                    "function_id": function.get("function_id"),
                    "name": function.get("name"),
                    "module_id": function.get("module_id"),
                    "visibility": function.get("visibility"),
                    "api_surface": function.get("api_surface"),
                    "function_kind": function.get("function_kind"),
                    "signature": function.get("signature", {}),
                }
            )
    return result


def _module_artifact_for_id(draft: dict[str, Any], module_id: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in draft.get("module_artifacts", [])
            if isinstance(item, dict) and str(item.get("module_id", "")) == module_id
        ),
        {"module_id": module_id, "artifacts": []},
    )


def _module_summary(draft: dict[str, Any], module_id: str) -> dict[str, Any]:
    module = _module_artifact_for_id(draft, module_id)
    return {
        "module_id": module_id,
        "role": str(module.get("role", "")),
        "dependencies": [str(item) for item in module.get("dependencies", []) if str(item).strip()],
        "artifacts": [
            {"kind": item.get("kind"), "name": item.get("name"), "role": item.get("role")}
            for item in module.get("artifacts", [])
            if isinstance(item, dict)
        ],
    }


def _module_canonical_types(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in draft.get("canonical_types", [])
        if isinstance(item, dict) and str(item.get("owner_module_id", item.get("module_id", ""))) == module_id
    ]


def _provider_public_api_summary(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    module = _module_artifact_for_id(draft, module_id)
    provider_modules, _ = _provider_consumer_modules(draft, module)
    provider_ids = {str(item.get("module_id", "")) for item in provider_modules}
    return [
        {
            "function_id": item.get("function_id"),
            "name": item.get("name"),
            "module_id": item.get("module_id"),
            "function_kind": item.get("function_kind"),
            "api_surface": item.get("api_surface"),
            "signature": item.get("signature", {}),
        }
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict)
        and str(item.get("module_id", "")) in provider_ids
        and (bool(item.get("exported")) or str(item.get("api_surface", "")) == "public" or str(item.get("visibility", "")) == "public")
    ]


def _provider_public_types_for_module(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    module = _module_artifact_for_id(draft, module_id)
    provider_modules, _ = _provider_consumer_modules(draft, module)
    return _public_provider_types(draft, provider_modules, None)


def _scoped_type_ids(draft: dict[str, Any], module_id: str, provider_public_types: list[dict[str, Any]]) -> list[str]:
    refs = {
        str(item.get("type_id", ""))
        for item in [*_module_canonical_types(draft, module_id), *_module_type_inventory(draft, module_id)]
        if isinstance(item, dict) and str(item.get("type_id", "")).strip()
    }
    refs.update(
        str(type_item.get("type_id", ""))
        for group in provider_public_types
        for type_item in group.get("types", [])
        if isinstance(type_item, dict) and str(type_item.get("type_id", "")).strip()
    )
    return sorted(refs)


def _scoped_signature_legal_ids(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], provider_public_types: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
        "type_ids": _scoped_type_ids(draft, module_id, provider_public_types),
        "system_type_ids": SYSTEM_TYPE_IDS,
        "function_ids": [str(item.get("function_id", "")) for item in functions if isinstance(item, dict)],
    }


def _module_state_access_policy(draft: dict[str, Any], module_id: str) -> dict[str, Any]:
    own: list[str] = []
    external: list[str] = []
    for item in draft.get("state_design", []):
        if not isinstance(item, dict):
            continue
        state_id = str(item.get("state_id", "")).strip()
        if not state_id:
            continue
        if str(item.get("owner_module_id", "")) == module_id:
            own.append(state_id)
        else:
            external.append(state_id)
    return {"writable_state_ids": own, "read_only_external_state_ids": external}


def _module_resource_refs(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in draft.get("resource_lifecycle", [])
        if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id
    ]


def _scoped_behavior_legal_ids(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], provider_public_api: list[dict[str, Any]]) -> dict[str, Any]:
    state_policy = _module_state_access_policy(draft, module_id)
    provider_public_types = _provider_public_types_for_module(draft, module_id)
    return {
        "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
        "function_ids": [str(item.get("function_id", "")) for item in functions if isinstance(item, dict)],
        "provider_public_function_ids": [str(item.get("function_id", "")) for item in provider_public_api if str(item.get("function_id", "")).strip()],
        "state_ids": [*state_policy["writable_state_ids"], *state_policy["read_only_external_state_ids"]],
        "error_ids": [str(item.get("error_id", "")) for item in draft.get("error_strategy", []) if isinstance(item, dict)],
        "type_ids": _scoped_type_ids(draft, module_id, provider_public_types),
        "system_type_ids": SYSTEM_TYPE_IDS,
    }


def build_core_design_context(
    planning_ir: dict[str, Any],
    profile: dict[str, Any],
    constraints: dict[str, Any],
    selected_architecture: dict[str, Any],
) -> dict[str, Any]:
    surfaces = _surface_units(planning_ir, profile)
    return {
        "schema_version": "core_design_context/v1",
        "protocol": _protocol_summary(planning_ir, profile),
        "profile_summary": {name: _field_value(profile.get(name), "unknown") for name in PROFILE_FIELDS},
        "selected_modules": _selected_modules(selected_architecture),
        "required_capabilities": _required_capabilities(profile),
        "required_surface_units": [
            {"name": item.get("name"), "direction": item.get("direction"), "role": item.get("role"), "refs": _compressed_refs(item)}
            for item in surfaces
        ],
        "handler_requirements": [
            {"surface": item.get("name"), "source_fact_count": len(item.get("source_fact_ids", []))}
            for item in _handler_surfaces(surfaces, str(_target_directives(planning_ir).get("target_role", "")))
        ],
        "message_summaries": _message_summaries(planning_ir),
        "field_summaries": _field_summaries(planning_ir),
        "engineering_constraints": _constraints(constraints),
        "compressed_trace_refs": _compressed_trace_refs(profile),
        "legal_id_universe": _legal_ids_from_inputs(planning_ir, profile, constraints, selected_architecture),
    }


def build_module_artifact_context(draft: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "module_artifacts_context/v1",
        "protocol": {
            "name": str(draft.get("protocol_name", "protocol")),
            "target_role": _field_value(profile.get("target_role"), "target"),
            "minimum_scope": _field_value(profile.get("minimum_scope"), "minimum_v1"),
        },
        "selected_modules": _selected_modules(selected_architecture),
        "core_design_summary": _accepted_summary(draft),
        "required_capabilities": _required_capabilities(profile),
        "engineering_constraints": _constraints(constraints),
        "compressed_trace_refs": _compressed_trace_refs(profile),
        "legal_id_universe": _legal_ids_from_draft(draft)
        | {
            "module_ids": [item["module_id"] for item in _selected_modules(selected_architecture)],
            "capability_ids": [item["capability_id"] for item in _required_capabilities(profile)],
            "constraint_ids": [item["constraint_id"] for item in _constraints(constraints)],
        },
    }


def _module_type_inventory(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in draft.get("type_inventory", [])
        if isinstance(item, dict) and str(item.get("module_id", "")) == module_id
    ]


def _candidate_items(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(candidate.get("types"), list):
        return [item for item in candidate.get("types", []) if isinstance(item, dict)]
    if isinstance(candidate.get("functions"), list):
        return [item for item in candidate.get("functions", []) if isinstance(item, dict)]
    return []


def _public_item_names(items: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in items:
        if (
            str(item.get("visibility", "")).lower() == "public"
            or str(item.get("api_surface", "")).lower() == "public"
            or bool(item.get("exported"))
        ):
            name = str(item.get("name", "")).strip()
            if name:
                result.append(name)
    return result


_REPAIR_RELEVANT_WARNING_CODES = {
    "under_decomposed_inventory",
    "coarse_function_should_split",
    "missing_function_family",
    "missing_dispatch_boundary",
    "missing_cleanup_for_resource_owner",
    "missing_parser_or_serializer_helpers",
}


def _compact_diagnostic_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": str(item.get("level", "error")),
        "code": str(item.get("code", "")),
        "message": str(item.get("message", "")),
        "path": item.get("path"),
    }


def _repair_relevant_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level", "error"))
        code = str(item.get("code", ""))
        if level == "error" or code in _REPAIR_RELEVANT_WARNING_CODES:
            result.append(_compact_diagnostic_item(item))
    return result


def _repair_target_errors(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = [item for item in _repair_relevant_diagnostics(diagnostics) if item.get("level") == "error"]
    if not errors:
        errors = _repair_relevant_diagnostics(diagnostics)
    seen: set[tuple[str, Any]] = set()
    result: list[dict[str, Any]] = []
    for item in errors:
        key = (str(item.get("code", "")), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= 8:
            break
    return result


def _stable_content_to_preserve(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    preserved: list[dict[str, Any]] = []
    if isinstance(candidate.get("types"), list):
        for item in candidate.get("types", []):
            if not isinstance(item, dict):
                continue
            preserved.append(
                {
                    "type_id": str(item.get("type_id", "")),
                    "name": str(item.get("name", "")),
                    "module_id": str(item.get("module_id", "")),
                    "kind": str(item.get("kind", "")),
                    "visibility": str(item.get("visibility", "")),
                    "defined_in": str(item.get("defined_in", "")),
                }
            )
    if isinstance(candidate.get("functions"), list):
        for item in candidate.get("functions", []):
            if not isinstance(item, dict):
                continue
            preserved.append(
                {
                    "function_id": str(item.get("function_id", "")),
                    "name": str(item.get("name", "")),
                    "module_id": str(item.get("module_id", "")),
                    "visibility": str(item.get("visibility", "")),
                    "api_surface": str(item.get("api_surface", "")),
                    "exported": bool(item.get("exported", False)),
                    "public_api_role": str(item.get("public_api_role", "")),
                }
            )
    return [item for item in preserved if item.get("name")]


def build_inventory_failure_context(
    *,
    stage: str,
    module_id: str,
    candidate_attempt: int,
    repair_attempt: int,
    candidate: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    previous_repair_failures: list[dict[str, Any]] | None = None,
    max_retries: int = 2,
    max_repairs: int = 4,
    remaining_retries: int | None = None,
    remaining_repairs: int | None = None,
    failed_patch_summary: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = _candidate_items(candidate)
    repair_relevant = _repair_relevant_diagnostics(diagnostics)
    return {
        "stage": stage,
        "module_id": module_id,
        "candidate_attempt": candidate_attempt,
        "repair_attempt": repair_attempt,
        "current_candidate_summary": {
            "schema_version": str(candidate.get("schema_version", "")),
            "candidate_id": str(candidate.get("candidate_id", "")),
            "module_id": str(candidate.get("module_id", "")),
            "item_count": len(items),
            "item_names": [str(item.get("name", "")) for item in items if str(item.get("name", "")).strip()],
            "public_item_names": _public_item_names(items),
            "assumptions_count": len(candidate.get("assumptions", [])) if isinstance(candidate.get("assumptions"), list) else 0,
            "unresolved_questions_count": len(candidate.get("unresolved_questions", [])) if isinstance(candidate.get("unresolved_questions"), list) else 0,
        },
        "validator_errors": repair_relevant,
        "repair_target_errors": _repair_target_errors(diagnostics),
        "stable_content_to_preserve": _stable_content_to_preserve(candidate),
        "attempt_budget": {
            "max_retries": max_retries,
            "max_repairs": max_repairs,
            "remaining_retries": max_retries if remaining_retries is None else remaining_retries,
            "remaining_repairs": max_repairs if remaining_repairs is None else remaining_repairs,
        },
        "previous_repair_failures": previous_repair_failures or [],
        "failed_patch_summary": failed_patch_summary if failed_patch_summary is not None else previous_repair_failures or [],
        "repair_contract": {
            "mode": "small_patch_only",
            "preserve_valid_content": True,
            "do_not_rewrite_candidate": True,
            "only_fix_listed_validator_errors": True,
        },
    }


def _provider_consumer_modules(draft: dict[str, Any], module_artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    module_id = str(module_artifact.get("module_id", ""))
    modules = [item for item in draft.get("module_artifacts", []) if isinstance(item, dict)]
    providers = {str(dep) for dep in module_artifact.get("dependencies", []) if str(dep).strip()}
    provider_modules = [item for item in modules if str(item.get("module_id", "")) in providers]
    consumers = [
        item
        for item in modules
        if module_id in {str(dep) for dep in item.get("dependencies", []) if str(dep).strip()}
    ]
    return provider_modules, consumers


def _type_seed_kind(name: str, role: str, target_kind: str = "") -> str:
    text = f"{name} {role} {target_kind}".lower()
    if "connection" in text and name.endswith("_t"):
        return "opaque_handle"
    if target_kind == "packet_enum" or "enum" in text or "packet_type" in text:
        return "enum"
    if target_kind == "owned_buffer" or any(word in text for word in ("bytes", "buffer")):
        return "owned_buffer"
    if target_kind == "callback_or_event_boundary" or any(word in text for word in ("callback", "_cb", "_fn", "event")):
        return "callback_type" if "_fn" in text or "callback" in text else "event_struct"
    if any(word in text for word in ("handle", "opaque", "server")):
        return "opaque_handle"
    return "struct"


def _public_seed_item(module_id: str, name: str, kind: str, *, seed_source: str, seed_reason: str, trace_ref_keys: list[str] | None = None) -> dict[str, Any]:
    type_id = f"type:{module_id}:{_safe_id(name)}"
    aliases = sorted({f"type:{_safe_id(name)}"})
    return {
        "type_id": type_id,
        "type_id_aliases": aliases,
        "name": name,
        "module_id": module_id,
        "kind": kind,
        "visibility": "public",
        "defined_in": "public_header",
        "purpose": seed_reason,
        "fields": [],
        "enum_values": [],
        "callback_signature": {"return_type": "", "params": []},
        "ownership_lifetime": "",
        "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
        "related_functions": [],
        "dependencies": [],
        "trace_ref_keys": trace_ref_keys or [],
        "status": "seed",
        "seed_source": seed_source,
        "seed_reason": seed_reason,
    }


def _is_internal_connection_seed(name: str, role: str) -> bool:
    text = f"{name} {role}".lower()
    return "connection" in text and "server" not in text


def _is_internal_connection_public_surface(type_item: dict[str, Any]) -> bool:
    if str(type_item.get("kind", "")) == "opaque_handle":
        return False
    name = str(type_item.get("name", ""))
    text = " ".join(
        [
            name,
            str(type_item.get("purpose", "")),
            str(type_item.get("ownership_lifetime", "")),
            " ".join(str(field.get("field_name", "")) + " " + str(field.get("field_type", "")) for field in type_item.get("fields", []) if isinstance(field, dict)),
        ]
    ).lower()
    if "server" in text:
        return False
    return "connection" in text and any(word in text for word in ("per-connection", "socket", "fd", "buffer", "recv", "send"))


def _artifact_is_public_type_seed(protocol: str, module_id: str, artifact: dict[str, Any]) -> bool:
    name = str(artifact.get("name", "")).strip()
    role = str(artifact.get("role", "")).strip()
    text = f"{module_id} {name} {role}".lower()
    if not name:
        return False
    if _is_internal_connection_seed(name, role):
        return name.endswith("_t") and "private" not in text and "internal" not in text
    module_handle = name == f"{protocol}_{_safe_id(module_id)}_t"
    if module_handle or any(word in text for word in ("public", "opaque", "handle")):
        return True
    if "network" in text or "transport" in text:
        return "server" in text
    if "codec" in text:
        return any(word in text for word in ("packet", "payload", "parser", "decode", "encode", "bytes", "buffer"))
    return name.endswith("_t") and any(word in text for word in ("session", "router", "broker", "topic", "resource", "app"))


def _dedupe_seed_types(types: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in types:
        type_id = str(item.get("type_id", "")).strip()
        key = type_id or f"{item.get('module_id')}:{item.get('name')}"
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def public_type_seed_index(draft: dict[str, Any], planning_ir: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    protocol = _protocol_prefix(draft)
    index: dict[str, list[dict[str, Any]]] = {}
    for module in [item for item in draft.get("module_artifacts", []) if isinstance(item, dict)]:
        module_id = str(module.get("module_id", "")).strip()
        if not module_id:
            continue
        seeds: list[dict[str, Any]] = []
        for artifact in module.get("artifacts", []):
            if not isinstance(artifact, dict) or str(artifact.get("kind", "")).upper() != "TYPE":
                continue
            name = str(artifact.get("name", "")).strip()
            role = str(artifact.get("role", "")).strip()
            if _artifact_is_public_type_seed(protocol, module_id, artifact):
                seeds.append(
                    _public_seed_item(
                        module_id,
                        name,
                        _type_seed_kind(name, role),
                        seed_source="module_artifacts",
                        seed_reason=role or "Public module boundary TYPE artifact.",
                        trace_ref_keys=module.get("source_fact_ids", []),
                    )
                )
        for target in derive_type_generation_targets(draft, module, planning_ir):
            target_kind = str(target.get("target_kind", ""))
            if target_kind not in {"packet_enum", "payload_struct", "packet_container_struct", "owned_buffer", "callback_or_event_boundary"}:
                continue
            name = str(target.get("suggested_name", "")).strip()
            if not name or _is_internal_connection_seed(name, str(target.get("reason", ""))):
                continue
            seeds.append(
                _public_seed_item(
                    module_id,
                    name,
                    _type_seed_kind(name, str(target.get("reason", "")), target_kind),
                    seed_source="type_generation_targets",
                    seed_reason=str(target.get("reason", "")) or f"Derived public {target_kind} boundary.",
                    trace_ref_keys=target.get("trace_ref_keys", []),
                )
            )
        index[module_id] = _dedupe_seed_types(seeds)
    return index


def provider_public_type_seeds_for_module(draft: dict[str, Any], module_artifact: dict[str, Any], planning_ir: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    provider_modules, _ = _provider_consumer_modules(draft, module_artifact)
    seed_index = public_type_seed_index(draft, planning_ir)
    return [
        {"module_id": str(item.get("module_id", "")), "types": seed_index.get(str(item.get("module_id", "")), [])}
        for item in provider_modules
    ]


def _public_provider_types(draft: dict[str, Any], provider_modules: list[dict[str, Any]], planning_ir: dict[str, Any] | None) -> list[dict[str, Any]]:
    seed_groups = public_type_seed_index(draft, planning_ir)
    result: list[dict[str, Any]] = []
    for provider in provider_modules:
        provider_id = str(provider.get("module_id", ""))
        types = [
            type_item
            for type_item in _module_type_inventory(draft, provider_id)
            if str(type_item.get("visibility", "")) == "public"
            and str(type_item.get("defined_in", "")) == "public_header"
            and not _is_internal_connection_public_surface(type_item)
        ]
        types.extend(seed_groups.get(provider_id, []))
        result.append({"module_id": provider_id, "types": _dedupe_seed_types(types)})
    return result


def _legal_ids_with_provider_seeds(draft: dict[str, Any], provider_public_types: list[dict[str, Any]]) -> dict[str, Any]:
    legal_ids = _legal_ids_from_draft(draft)
    seed_type_ids = {
        ref
        for group in provider_public_types
        for type_item in group.get("types", [])
        if isinstance(type_item, dict) and type_item.get("seed_source")
        for ref in [str(type_item.get("type_id", "")), *[str(alias) for alias in type_item.get("type_id_aliases", [])]]
        if ref.strip()
    }
    legal_ids["type_ids"] = sorted(set(legal_ids.get("type_ids", [])) | seed_type_ids)
    return legal_ids


def build_type_inventory_context(draft: dict[str, Any], module_artifact: dict[str, Any], planning_ir: dict[str, Any] | None = None) -> dict[str, Any]:
    module_id = str(module_artifact.get("module_id", ""))
    provider_modules, consumers = _provider_consumer_modules(draft, module_artifact)
    provider_public_types = _public_provider_types(draft, provider_modules, planning_ir)
    from .inventory_planning_space import build_type_planning_space

    type_planning_space = build_type_planning_space(draft, module_artifact, planning_ir)
    return {
        "schema_version": "type_filling_context/v1",
        "type_planning_space": type_planning_space,
        "module_artifact": module_artifact,
        "current_module_artifacts": module_artifact.get("artifacts", []),
        "global_module_artifacts_reference": [
            {"module_id": item.get("module_id"), "role": item.get("role"), "dependencies": item.get("dependencies", []), "artifacts": item.get("artifacts", [])}
            for item in draft.get("module_artifacts", [])
            if isinstance(item, dict)
        ],
        "type_generation_targets": derive_type_generation_targets(draft, module_artifact, planning_ir),
        "current_module_dependencies": module_artifact.get("dependencies", []),
        "provider_module_artifacts": [
            {"module_id": item.get("module_id"), "artifacts": item.get("artifacts", [])}
            for item in provider_modules
        ],
        "provider_public_types": provider_public_types,
        "consumer_module_artifact_dependencies": [
            {"module_id": item.get("module_id"), "artifacts": item.get("artifacts", [])}
            for item in consumers
        ],
        "canonical_types": [
            item
            for item in draft.get("canonical_types", [])
            if isinstance(item, dict) and str(item.get("owner_module_id", "")) in {module_id, *[str(m.get("module_id", "")) for m in provider_modules]}
        ],
        "state_design": [
            item for item in draft.get("state_design", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id
        ],
        "resource_lifecycle": [
            item for item in draft.get("resource_lifecycle", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id
        ],
        "error_strategy": [
            item for item in draft.get("error_strategy", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id
        ],
        "handler_matrix": [
            item for item in draft.get("handler_matrix", []) if isinstance(item, dict) and str(item.get("owner_module_id", "")) == module_id
        ],
        "core_design_summary": _accepted_summary(draft),
        "legal_id_universe": _legal_ids_with_provider_seeds(draft, provider_public_types),
    }


def build_type_inventory_repair_context(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    candidate: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    failure_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = build_type_inventory_context(draft, module_artifact)
    module_id = str(module_artifact.get("module_id", ""))
    context.update(
        {
            "schema_version": "type_inventory_repair_context/v1",
            "current_candidate": candidate,
            "failure_context": failure_context
            or build_inventory_failure_context(
                stage="5.4a_type_inventory",
                module_id=module_id,
                candidate_attempt=1,
                repair_attempt=1,
                candidate=candidate,
                diagnostics=diagnostics,
            ),
            "triggering_diagnostics": [_compact_diagnostic_item(item) for item in diagnostics if isinstance(item, dict)],
            "patch_merge_rules": [
                "added_types are appended to current_candidate.types for this module only.",
                "type_id and name must not duplicate any existing or newly added type.",
                "updated_types may not rename a type or change its module_id/kind.",
                "Repair is a small patch only; never regenerate or rewrite the whole candidate.",
                "Only fix the listed failure_context.validator_errors.",
                "After merge, the result must validate as type_inventory_candidate/v1.",
            ],
        }
    )
    return context


def build_function_inventory_context(draft: dict[str, Any], module_artifact: dict[str, Any]) -> dict[str, Any]:
    module_id = str(module_artifact.get("module_id", ""))
    provider_modules, consumers = _provider_consumer_modules(draft, module_artifact)
    from .inventory_planning_space import build_function_planning_space

    function_planning_space = build_function_planning_space(draft, module_artifact)
    context = {
        "schema_version": "function_annotation_context/v1",
        "function_planning_space": function_planning_space,
        "module_artifact": module_artifact,
        "current_module_artifacts": module_artifact.get("artifacts", []),
        "global_module_artifacts_reference": [
            {"module_id": item.get("module_id"), "role": item.get("role"), "dependencies": item.get("dependencies", []), "artifacts": item.get("artifacts", [])}
            for item in draft.get("module_artifacts", [])
            if isinstance(item, dict)
        ],
        "current_module_type_inventory": _module_type_inventory(draft, module_id),
        "type_obligations": derive_type_obligations(draft, module_artifact),
        "current_module_dependencies": module_artifact.get("dependencies", []),
        "provider_module_artifacts": [
            {
                "module_id": item.get("module_id"),
                "artifacts": item.get("artifacts", []),
                "public_types": [
                    type_item
                    for type_item in _module_type_inventory(draft, str(item.get("module_id", "")))
                    if str(type_item.get("visibility", "")) == "public"
                ],
            }
            for item in provider_modules
        ],
        "consumer_module_artifact_dependencies": [
            {
                "module_id": item.get("module_id"),
                "artifacts": item.get("artifacts", []),
            }
            for item in consumers
        ],
        "global_service_flow_hints": draft.get("service_flow_hints", []),
        "core_design_summary": _accepted_summary(draft),
        "legal_id_universe": _legal_ids_from_draft(draft),
    }
    context["decomposition_context"] = function_planning_space.get("source_context", {}).get("decomposition_context", {})
    if isinstance(context["function_planning_space"].get("source_context"), dict):
        compact_decomposition = dict(context["function_planning_space"]["source_context"].get("decomposition_context", {}))
        compact_decomposition.pop("selected_decomposition_hints", None)
        context["function_planning_space"]["source_context"]["decomposition_context"] = compact_decomposition
    return context


def build_function_inventory_repair_context(
    draft: dict[str, Any],
    module_artifact: dict[str, Any],
    candidate: dict[str, Any],
    coverage_report: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    repair_mode: str,
    failure_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = build_function_inventory_context(draft, module_artifact)
    module_id = str(module_artifact.get("module_id", ""))
    missing_families = [
        {
            "rule_id": rule.get("rule_id", ""),
            "missing_families": rule.get("missing_families", []),
            "coverage_score": rule.get("coverage_score", 0),
        }
        for module_report in coverage_report.get("modules", [])
        if isinstance(module_report, dict)
        for rule in module_report.get("rules", [])
        if isinstance(rule, dict) and rule.get("missing_families")
    ]
    coarse_functions = [
        item
        for item in diagnostics
        if isinstance(item, dict) and item.get("code") == "coarse_function_should_split"
    ]
    context.update(
        {
            "schema_version": "function_inventory_repair_context/v1",
            "repair_mode": repair_mode,
            "current_candidate": candidate,
            "failure_context": failure_context
            or build_inventory_failure_context(
                stage="5.4b_function_inventory",
                module_id=module_id,
                candidate_attempt=1,
                repair_attempt=1,
                candidate=candidate,
                diagnostics=diagnostics,
            ),
            "coverage_report": coverage_report,
            "missing_function_families": missing_families,
            "coarse_functions": coarse_functions,
            "triggering_diagnostics": [_compact_diagnostic_item(item) for item in diagnostics if isinstance(item, dict)],
            "patch_merge_rules": [
                "added_functions are appended to current_candidate.functions for this module only.",
                "function_id and name must not duplicate any existing or newly added function.",
                "updated_functions may only change purpose, grouping_hint, or status.",
                "Do not delete, rename, or change identity/public API fields of existing functions.",
                "Repair is a small patch only; never regenerate or rewrite the whole candidate.",
                "Only fix the listed failure_context.validator_errors or the listed decomposition gaps.",
                "After merge, the result must validate as function_inventory_candidate/v2.",
            ],
        }
    )
    return context


def build_function_signature_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    provider_public_types = _provider_public_types_for_module(draft, module_id)
    return {
        "schema_version": "function_signature_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": functions,
        "required_update_skeleton": _signature_update_skeleton(functions),
        "module_summary": _module_summary(draft, module_id),
        "current_module_type_inventory": _module_type_inventory(draft, module_id),
        "current_module_canonical_types": _module_canonical_types(draft, module_id),
        "provider_public_types": provider_public_types,
        "legal_id_universe": _scoped_signature_legal_ids(draft, module_id, functions, provider_public_types),
    }


def build_function_behavior_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], constraints: dict[str, Any], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    provider_public_api = _provider_public_api_summary(draft, module_id)
    return {
        "schema_version": "function_behavior_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": functions,
        "required_update_skeleton": _behavior_update_skeleton(functions),
        "service_requirement_policy": {
            "external_runtime_examples": ["socket", "accept", "read", "write", "close", "epoll", "malloc", "free", "timer"],
            "cross_module_only": True,
            "provider_own_responsibility": "do_not_emit_service_requirement",
        },
        "module_summary": _module_summary(draft, module_id),
        "module_state_access_policy": _module_state_access_policy(draft, module_id),
        "module_resource_refs": _module_resource_refs(draft, module_id),
        "provider_public_api_summary": provider_public_api,
        "engineering_constraints": _constraints(constraints),
        "legal_id_universe": _scoped_behavior_legal_ids(draft, module_id, functions, provider_public_api) | {"constraint_ids": [item["constraint_id"] for item in _constraints(constraints)]},
    }


def build_wire_access_binding_context(draft: dict[str, Any], planning_ir: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "wire_access_binding_context/v1",
        "codec_and_handler_functions": [
            item
            for item in _accepted_summary(draft)["function_contracts"]
            if item.get("function_kind") in {"parser", "serializer", "handler"}
        ],
        "field_summaries": _field_summaries(planning_ir),
        "state_design": draft.get("state_design", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "legal_id_universe": _legal_ids_from_draft(draft)
        | {
            "message_ids": [item["message_id"] for item in _message_summaries(planning_ir)],
            "field_ids": [item["field_id"] for item in _field_summaries(planning_ir)],
        },
    }


def build_calls_allowed_context(
    draft: dict[str, Any],
    selected_architecture: dict[str, Any],
    module_id: str | None = None,
    functions: list[dict[str, Any]] | None = None,
    *,
    batch_index: int | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    scoped_functions = functions if functions is not None else [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    scoped_ids = {str(item.get("function_id", "")) for item in scoped_functions if isinstance(item, dict)}
    current_module = module_id or ""
    return {
        "schema_version": "calls_allowed_context/v1",
        "module_id": current_module,
        "batch": {"index": batch_index, "size": batch_size} if batch_index is not None else {},
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "service_requirements": [
            {"function_id": item.get("function_id"), "service_requirements": item.get("service_requirements", [])}
            for item in draft.get("function_contracts", [])
            if isinstance(item, dict) and (not scoped_ids or str(item.get("function_id", "")) in scoped_ids)
        ],
        "callable_functions": _callable_functions(draft, current_module) if current_module else [],
        "required_call_update_caller_ids": sorted(scoped_ids),
        "module_artifacts": draft.get("module_artifacts", []),
        "architecture_policy": {"selected_modules": _selected_modules(selected_architecture), "forbidden_cycles": True},
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_file_layout_context(draft: dict[str, Any], planning_ir: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "file_layout_context/v1",
        "module_artifacts": draft.get("module_artifacts", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "target_language": str(_target_directives(planning_ir).get("language", "C")),
        "layout_policy": "source_header_pair",
        "engineering_constraints": _constraints(constraints),
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def _key_flow_module_candidates(draft: dict[str, Any]) -> list[dict[str, Any]]:
    modules = [item for item in draft.get("module_artifacts", []) if isinstance(item, dict)]
    functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict)]
    scored: list[tuple[int, dict[str, Any]]] = []
    for index, module in enumerate(modules):
        module_id = str(module.get("module_id", ""))
        text = " ".join(
            [
                module_id,
                str(module.get("name", "")),
                str(module.get("purpose", "")),
                " ".join(str(cap) for cap in module.get("owned_capabilities", [])),
            ]
        ).lower()
        score = 0
        if any(word in text for word in ("broker", "server", "client", "flow", "app")):
            score += 40
        if "role_composition" in module.get("owned_capabilities", []):
            score += 30
        if "semantic_dispatch" in module.get("owned_capabilities", []):
            score += 20
        if module.get("support_module"):
            score -= 30
        score -= index
        public_functions = [
            function
            for function in functions
            if str(function.get("module_id", "")) == module_id
            and (bool(function.get("exported")) or str(function.get("visibility", "")).lower() == "public")
        ]
        lifecycle_roles = [
            str(function.get("function_id", ""))
            for function in public_functions
            if str(function.get("name", "")).endswith(("_create", "_start", "_run", "_serve", "_destroy"))
        ]
        scored.append(
            (
                score + len(lifecycle_roles) * 5,
                {
                    "module_id": module_id,
                    "purpose": module.get("purpose", ""),
                    "owned_capabilities": module.get("owned_capabilities", []),
                    "existing_public_lifecycle_function_ids": lifecycle_roles,
                },
            )
        )
    return [item for _score, item in sorted(scored, key=lambda pair: pair[0], reverse=True)]


def build_runtime_entrypoint_context(draft: dict[str, Any], planning_ir: dict[str, Any], selected_architecture: dict[str, Any]) -> dict[str, Any]:
    lifecycle_candidates = [
        {
            "function_id": item.get("function_id"),
            "name": item.get("name"),
            "module_id": item.get("module_id"),
            "visibility": item.get("visibility"),
            "exported": item.get("exported"),
            "signature": item.get("signature", {}),
        }
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict)
        and str(item.get("function_kind", "")) in {"resource_lifecycle", "public_api"}
        and str(item.get("name", "")).endswith(("_create", "_start", "_run", "_serve", "_destroy"))
    ]
    return {
        "schema_version": "runtime_entrypoint_context/v1",
        "protocol": _protocol_summary(planning_ir, {}),
        "selected_modules": _selected_modules(selected_architecture),
        "module_artifacts": draft.get("module_artifacts", []),
        "key_flow_module_candidates": _key_flow_module_candidates(draft),
        "existing_lifecycle_candidates": lifecycle_candidates,
        "file_layout": draft.get("file_layout", {}),
        "default_source_path": "main.c",
        "default_entrypoint_signature": "int main(int argc, char** argv)",
        "runtime_entrypoint_policy": {
            "entrypoint_starts_protocol_only": True,
            "protocol_flow_stays_in_key_flow_module": True,
            "source_only_file_allowed": True,
            "archive_file_under_key_flow_module": True,
        },
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_dependency_repair_context(draft: dict[str, Any], dependency_errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "dependency_repair_context/v1",
        "files": draft.get("file_layout", {}).get("files", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "dependency_errors": dependency_errors,
        "allowed_repair_operations": ["remove_call_edge", "adjust_imports_allowed", "lower_visibility", "change_function_file_assignment", "mark_unresolved"],
        "legal_id_universe": _legal_ids_from_draft(draft),
    }
