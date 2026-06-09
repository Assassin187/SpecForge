from __future__ import annotations

from copy import deepcopy
import re
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


def _module_needs_private_state(module_artifact: dict[str, Any]) -> bool:
    text = _module_text(module_artifact)
    if any(word in text for word in ("stateless", "definition", "definitions", "protocol constants", "enum definitions")):
        return False
    runtime_signals = (
        "connection",
        "socket",
        "fd",
        "session",
        "timer",
        "heap",
        "registry",
        "map",
        "store",
        "tree",
        "event loop",
        "runtime context",
        "lifecycle",
        "buffer pool",
        "i/o buffer",
        "io buffer",
        "recv buffer",
        "send buffer",
        "per-connection buffer",
    )
    return any(signal in text for signal in runtime_signals)


def _module_has_context_type_artifact(module_artifact: dict[str, Any], protocol: str) -> bool:
    module_id = _safe_id(str(module_artifact.get("module_id", "")))
    context_names = {module_id, f"{_safe_id(protocol)}_{module_id}"}
    for artifact in module_artifact.get("artifacts", []):
        if not isinstance(artifact, dict) or str(artifact.get("kind", "")).upper() != "TYPE":
            continue
        name = _safe_id(str(artifact.get("name", "")).removeprefix("struct ").removesuffix("_t"))
        if name in context_names:
            return True
    return False


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
    if _module_needs_private_state(module_artifact) and not _module_has_context_type_artifact(module_artifact, protocol):
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


def _public_exportable_type(item: dict[str, Any]) -> dict[str, str]:
    return {
        "type_id": str(item.get("type_id", "")),
        "name": str(item.get("c_symbol") or item.get("c_type_name") or item.get("name", "")).strip(),
        "owner_module_id": str(item.get("owner_module_id", "")),
        "kind": str(item.get("kind", "")),
    }


def _has_c_type_symbol(item: dict[str, Any]) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item.get("c_symbol") or item.get("c_type_name") or item.get("name", "")).strip()))


def _public_exportable_types(draft: dict[str, Any]) -> list[dict[str, str]]:
    return [
        _public_exportable_type(item)
        for item in draft.get("canonical_types", [])
        if isinstance(item, dict)
        and str(item.get("type_id", "")).strip()
        and _has_c_type_symbol(item)
    ]


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


def _clip_strings(values: Any, limit: int = 8) -> list[str]:
    return [str(item) for item in values if str(item).strip()][:limit] if isinstance(values, list) else []


def _compact_seed(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    for key, limit in (("trace_ref_keys", 8), ("source_refs", 8), ("covers_field_ids", 12)):
        if key in result:
            result[key] = _clip_strings(result.get(key, []), limit)
    return result


def _compact_module_artifact_ref(module: dict[str, Any]) -> dict[str, Any]:
    result = {
        "module_id": module.get("module_id"),
        "role": module.get("role"),
        "dependencies": module.get("dependencies", []),
        "artifacts": [
            {"kind": item.get("kind"), "name": item.get("name"), "role": item.get("role")}
            for item in module.get("artifacts", [])
            if isinstance(item, dict)
        ],
    }
    if isinstance(module.get("public_types"), list):
        result["public_types"] = [_compact_signature_type(item) for item in module["public_types"] if isinstance(item, dict)]
    return result


def compact_type_filling_context(context: dict[str, Any]) -> dict[str, Any]:
    space = context.get("type_planning_space", {}) if isinstance(context.get("type_planning_space"), dict) else {}
    allowed = space.get("allowed_type_refs", {}) if isinstance(space.get("allowed_type_refs"), dict) else {}
    compact_space = {
        "schema_version": space.get("schema_version", "type_planning_space/v1"),
        "module_id": space.get("module_id", ""),
        "mandatory_type_slots": [_compact_seed(item) for item in space.get("mandatory_type_slots", []) if isinstance(item, dict)],
        "derived_type_slots": [_compact_seed(item) for item in space.get("derived_type_slots", []) if isinstance(item, dict)],
        "recommended_type_slots": [_compact_seed(item) for item in space.get("recommended_type_slots", []) if isinstance(item, dict)],
        "allowed_type_refs": {
            "local_slots": allowed.get("local_slots", []),
            "provider_public_types": [_compact_signature_type(item) for item in allowed.get("provider_public_types", []) if isinstance(item, dict)],
            "canonical_public_types": [_compact_signature_type(item) for item in allowed.get("canonical_public_types", []) if isinstance(item, dict)],
            "system_types": allowed.get("system_types", SYSTEM_TYPE_IDS),
        },
        "forbidden_type_refs": space.get("forbidden_type_refs", []),
        "optional_expansion_policy": space.get("optional_expansion_policy", {}),
        "source_context": {
            "module_ownership": space.get("source_context", {}).get("module_ownership", {})
            if isinstance(space.get("source_context"), dict)
            else {}
        },
        "richness_diagnostics": space.get("richness_diagnostics", []),
    }
    result = dict(context)
    result["type_planning_space"] = compact_space
    if isinstance(result.get("module_artifact"), dict):
        result["module_artifact"] = _compact_module_artifact_ref(result["module_artifact"])
    module_ref = result.get("module_artifact") if isinstance(result.get("module_artifact"), dict) else {}
    result["current_module_artifacts"] = module_ref.get("artifacts", [])
    result["global_module_artifacts_reference"] = [
        _compact_module_artifact_ref(item) for item in context.get("global_module_artifacts_reference", []) if isinstance(item, dict)
    ]
    result["provider_module_artifacts"] = [
        _compact_module_artifact_ref(item) for item in context.get("provider_module_artifacts", []) if isinstance(item, dict)
    ]
    result["provider_public_types"] = [
        {
            "module_id": group.get("module_id"),
            "types": [_compact_signature_type(item) for item in group.get("types", []) if isinstance(item, dict)],
        }
        for group in context.get("provider_public_types", [])
        if isinstance(group, dict)
    ]
    result["consumer_module_artifact_dependencies"] = [
        _compact_module_artifact_ref(item) for item in context.get("consumer_module_artifact_dependencies", []) if isinstance(item, dict)
    ]
    result.pop("type_generation_targets", None)
    result.pop("core_design_summary", None)
    return result


def compact_function_annotation_context(context: dict[str, Any]) -> dict[str, Any]:
    space = context.get("function_planning_space", {}) if isinstance(context.get("function_planning_space"), dict) else {}
    source_context = space.get("source_context", {}) if isinstance(space.get("source_context"), dict) else {}
    decomposition = source_context.get("decomposition_context", {}) if isinstance(source_context.get("decomposition_context"), dict) else {}
    compact_space = {
        "schema_version": space.get("schema_version", "function_planning_space/v1"),
        "module_id": space.get("module_id", ""),
        "function_budget": space.get("function_budget", {}),
        "mandatory_function_seeds": [_compact_seed(item) for item in space.get("mandatory_function_seeds", []) if isinstance(item, dict)],
        "obligation_function_seeds": [_compact_seed(item) for item in space.get("obligation_function_seeds", []) if isinstance(item, dict)],
        "handler_function_seeds": [_compact_seed(item) for item in space.get("handler_function_seeds", []) if isinstance(item, dict)],
        "parser_serializer_function_seeds": [_compact_seed(item) for item in space.get("parser_serializer_function_seeds", []) if isinstance(item, dict)],
        "recommended_function_families": [_compact_seed(item) for item in space.get("recommended_function_families", []) if isinstance(item, dict)],
        "legal_refs": space.get("legal_refs", {}),
        "optional_expansion_policy": space.get("optional_expansion_policy", {}),
        "source_context": {
            "target_role": source_context.get("target_role", ""),
            "global_service_flow_hints": source_context.get("global_service_flow_hints", []),
            "wire_field_count": source_context.get("wire_field_count", 0),
            "decomposition_context": {
                "selected_rule_ids": decomposition.get("selected_rule_ids", []),
                "expected_function_families_by_rule": decomposition.get("expected_function_families_by_rule", {}),
                "recommended_concrete_slots_by_rule": decomposition.get("recommended_concrete_slots_by_rule", {}),
                "evidence_summary": decomposition.get("evidence_summary", []),
            },
        },
        "richness_diagnostics": space.get("richness_diagnostics", []),
    }
    result = dict(context)
    result["function_planning_space"] = compact_space
    if isinstance(result.get("module_artifact"), dict):
        result["module_artifact"] = _compact_module_artifact_ref(result["module_artifact"])
    module_ref = result.get("module_artifact") if isinstance(result.get("module_artifact"), dict) else {}
    result["current_module_artifacts"] = module_ref.get("artifacts", [])
    result["global_module_artifacts_reference"] = [
        _compact_module_artifact_ref(item) for item in context.get("global_module_artifacts_reference", []) if isinstance(item, dict)
    ]
    result["provider_module_artifacts"] = [
        _compact_module_artifact_ref(item) for item in context.get("provider_module_artifacts", []) if isinstance(item, dict)
    ]
    result["consumer_module_artifact_dependencies"] = [
        _compact_module_artifact_ref(item) for item in context.get("consumer_module_artifact_dependencies", []) if isinstance(item, dict)
    ]
    result["current_module_type_inventory"] = [_compact_signature_type(item) for item in context.get("current_module_type_inventory", []) if isinstance(item, dict)]
    result["type_obligations"] = [_compact_seed(item) for item in context.get("type_obligations", []) if isinstance(item, dict)]
    result.pop("core_design_summary", None)
    return result


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
            "trace_ref_keys": _clip_strings(item.get("traceability", {}).get("decision_ids", []), 8),
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
                    "purpose": function.get("purpose", ""),
                    "function_kind": function.get("function_kind"),
                    "logic_kind": function.get("logic_kind", ""),
                    "signature": function.get("signature", {}),
                    "behavior_contract": function.get("behavior_contract", {}),
                    "service_requirements": [requirement for requirement in function.get("service_requirements", []) if isinstance(requirement, dict)],
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


def _provider_public_behavior_api_summary(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    return [
        {
            "function_id": item.get("function_id"),
            "name": item.get("name"),
            "module_id": item.get("module_id"),
            "function_kind": item.get("function_kind"),
            "api_surface": item.get("api_surface"),
        }
        for item in _provider_public_api_summary(draft, module_id)
    ]


def _provider_public_types_for_module(draft: dict[str, Any], module_id: str) -> list[dict[str, Any]]:
    module = _module_artifact_for_id(draft, module_id)
    provider_modules, _ = _provider_consumer_modules(draft, module)
    return _public_provider_types(draft, provider_modules, None)


def _compact_signature_function(function: dict[str, Any]) -> dict[str, Any]:
    return {
        "function_id": function.get("function_id"),
        "name": function.get("name"),
        "module_id": function.get("module_id"),
        "function_kind": function.get("function_kind"),
        "coder_function_type": function.get("coder_function_type"),
        "visibility": function.get("visibility"),
        "api_surface": function.get("api_surface"),
        "exported": function.get("exported"),
        "public_api_role": function.get("public_api_role"),
        "purpose": function.get("purpose"),
        "capability_ids": function.get("capability_ids", []),
        "covers_handler_ids": function.get("covers_handler_ids", []),
        "covers_message_ids": function.get("covers_message_ids", []),
        "covers_field_ids": function.get("covers_field_ids", []),
        "trace_ref_keys": function.get("traceability", {}).get("decision_ids", []),
    }


def _compact_signature_type(type_item: dict[str, Any]) -> dict[str, Any]:
    callback = type_item.get("callback_signature", {}) if isinstance(type_item.get("callback_signature"), dict) else {}
    return {
        "type_id": type_item.get("type_id"),
        "name": type_item.get("name"),
        "kind": type_item.get("kind"),
        "module_id": type_item.get("module_id"),
        "visibility": type_item.get("visibility"),
        "defined_in": type_item.get("defined_in"),
        "lifecycle": type_item.get("lifecycle", {}),
        "callback_signature": callback,
    }


def _compact_behavior_function(function: dict[str, Any]) -> dict[str, Any]:
    return {
        **_compact_signature_function(function),
        "signature": function.get("signature", {}),
        "behavior_contract": function.get("behavior_contract", {}),
        "error_behavior": function.get("error_behavior", {}),
        "state_access": function.get("state_access", []),
        "resource_access": function.get("resource_access", []),
        "internal_type_refs": function.get("internal_type_refs", []),
        "logic_kind": function.get("logic_kind", ""),
    }


def _compact_access_type(type_item: dict[str, Any]) -> dict[str, Any]:
    result = _compact_signature_type(type_item)
    result["fields"] = [
        {
            "field_name": field.get("field_name"),
            "field_type": field.get("field_type"),
            "type_ref": field.get("type_ref"),
            "variants": field.get("variants", []),
        }
        for field in type_item.get("fields", [])
        if isinstance(field, dict)
    ]
    result["enum_values"] = [
        {"name": item.get("name"), "value": item.get("value"), "role": item.get("role")}
        for item in type_item.get("enum_values", [])
        if isinstance(item, dict)
    ]
    return result


def _compact_cross_module_call_function(function: dict[str, Any]) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return {
        "function_id": function.get("function_id"),
        "name": function.get("name"),
        "module_id": function.get("module_id"),
        "visibility": function.get("visibility"),
        "api_surface": function.get("api_surface"),
        "purpose": function.get("purpose", ""),
        "function_kind": function.get("function_kind"),
        "signature": {
            "return_type": signature.get("return_type", ""),
            "params": [
                {
                    "name": param.get("name"),
                    "type": param.get("type"),
                    "type_ref": param.get("type_ref"),
                    "direction": param.get("direction", ""),
                    "nullable": param.get("nullable", False),
                }
                for param in signature.get("params", [])
                if isinstance(param, dict)
            ],
        },
        "service_requirements": [
            requirement
            for requirement in function.get("service_requirements", [])
            if isinstance(requirement, dict) and str(requirement.get("requirement_kind", "")) == "cross_module_service"
        ],
    }


def _call_update_skeleton(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"caller_function_id": str(item.get("function_id", "")), "calls_allowed": []}
        for item in functions
        if isinstance(item, dict) and str(item.get("function_id", "")).strip()
    ]


def _is_public_api_function(function: dict[str, Any]) -> bool:
    return (
        bool(function.get("exported"))
        or str(function.get("api_surface", "")).lower() == "public"
        or str(function.get("visibility", "")).lower() == "public"
    )


def _operation_tokens(function: dict[str, Any]) -> set[str]:
    text = " ".join(str(function.get(key, "")) for key in ("function_id", "name", "purpose", "function_kind")).lower()
    tokens = {
        token
        for token in ("close", "cleanup", "free", "destroy", "create", "init", "start", "run", "dispatch", "decode", "encode", "parse", "serialize", "error")
        if token in text
    }
    return tokens


def _call_graph_direction_hints(module_id: str, callers: list[dict[str, Any]], callable_functions: list[dict[str, Any]]) -> list[dict[str, str]]:
    scoped_ids = {str(item.get("function_id", "")) for item in callers if isinstance(item, dict)}
    same_module = [
        item
        for item in [*callers, *callable_functions]
        if isinstance(item, dict)
        and str(item.get("module_id", "")) == module_id
        and str(item.get("function_id", "")).strip()
    ]
    by_id = {str(item.get("function_id", "")): item for item in same_module}
    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for left in by_id.values():
        left_tokens = _operation_tokens(left)
        if not left_tokens:
            continue
        for right in by_id.values():
            if left is right:
                continue
            if not (str(left.get("function_id", "")) in scoped_ids or str(right.get("function_id", "")) in scoped_ids):
                continue
            right_tokens = _operation_tokens(right)
            overlap = left_tokens & right_tokens
            if not overlap or _is_public_api_function(left) == _is_public_api_function(right):
                continue
            public_fn, internal_fn = (left, right) if _is_public_api_function(left) else (right, left)
            key = (str(public_fn.get("function_id", "")), str(internal_fn.get("function_id", "")))
            if key in seen:
                continue
            seen.add(key)
            hints.append(
                {
                    "preferred_caller_id": key[0],
                    "preferred_callee_id": key[1],
                    "forbidden_reverse_reason": f"same-module public/API facade and internal helper share {','.join(sorted(overlap))} responsibility; keep call direction public/API -> internal helper",
                }
            )
            if len(hints) >= 16:
                return hints
    return hints


def _expected_cross_module_service_requirements(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for function in functions:
        if not isinstance(function, dict):
            continue
        for requirement in function.get("service_requirements", []):
            if isinstance(requirement, dict) and str(requirement.get("requirement_kind", "")) == "cross_module_service":
                result.append(
                    {
                        "function_id": function.get("function_id"),
                        "service_requirement_id": requirement.get("service_requirement_id"),
                        "operation": requirement.get("operation", ""),
                        "expected_inputs": requirement.get("expected_inputs", []),
                        "expected_output": requirement.get("expected_output", ""),
                        "failure_policy": requirement.get("failure_policy", ""),
                    }
                )
    return result


def _service_text(value: dict[str, Any]) -> set[str]:
    text = " ".join(str(value.get(key, "")) for key in ("service_requirement_id", "operation", "expected_output")).lower()
    return {item for item in re.split(r"[^a-z0-9]+", text) if len(item) > 2}


def _candidate_provider_functions(requirements: list[dict[str, Any]], callable_functions: list[dict[str, Any]], callers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    caller_module = {str(item.get("function_id", "")): str(item.get("module_id", "")) for item in callers if isinstance(item, dict)}
    public_callables = [
        item
        for item in callable_functions
        if isinstance(item, dict)
        and (
            bool(item.get("exported"))
            or str(item.get("api_surface", "")).lower() == "public"
            or str(item.get("visibility", "")).lower() == "public"
        )
    ]
    result = []
    for requirement in requirements:
        tokens = _service_text(requirement)
        caller_module_id = caller_module.get(str(requirement.get("function_id", "")), "")
        scored: list[tuple[int, str]] = []
        for function in public_callables:
            if str(function.get("module_id", "")) == caller_module_id:
                continue
            text = f"{function.get('name', '')} {function.get('purpose', '')} {function.get('function_kind', '')}".lower()
            score = sum(1 for token in tokens if token in text)
            if score:
                scored.append((score, str(function.get("function_id", ""))))
        provider_ids = [function_id for _, function_id in sorted(scored, reverse=True)[:4]]
        if not provider_ids:
            provider_ids = [str(function.get("function_id", "")) for function in public_callables if str(function.get("module_id", "")) != caller_module_id][:4]
        result.append({"service_requirement_id": requirement.get("service_requirement_id"), "provider_function_ids": provider_ids})
    return result


def _provider_function_summaries(provider_candidates: list[dict[str, Any]], callable_functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    provider_ids = {
        str(function_id)
        for item in provider_candidates
        for function_id in item.get("provider_function_ids", [])
        if str(function_id).strip()
    }
    return [
        _compact_cross_module_call_function(function)
        for function in callable_functions
        if isinstance(function, dict) and str(function.get("function_id", "")) in provider_ids
    ]


def _service_cycle_risk_hints(requirements: list[dict[str, Any]], callable_functions: list[dict[str, Any]], callers: list[dict[str, Any]]) -> list[dict[str, str]]:
    caller_by_id = {str(item.get("function_id", "")): item for item in callers if isinstance(item, dict)}
    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for requirement in requirements:
        caller = caller_by_id.get(str(requirement.get("function_id", "")), {})
        caller_module = str(caller.get("module_id", ""))
        if not caller_module:
            continue
        requirement_tokens = _service_text(requirement)
        for provider in callable_functions:
            if not isinstance(provider, dict) or str(provider.get("module_id", "")) == caller_module:
                continue
            provider_text = f"{provider.get('name', '')} {provider.get('purpose', '')} {provider.get('function_kind', '')}".lower()
            if not any(token in provider_text for token in requirement_tokens):
                continue
            for provider_requirement in provider.get("service_requirements", []):
                if not isinstance(provider_requirement, dict) or str(provider_requirement.get("requirement_kind", "")) != "cross_module_service":
                    continue
                provider_req_text = " ".join(
                    str(provider_requirement.get(key, ""))
                    for key in ("service_requirement_id", "operation", "expected_output")
                ).lower()
                provider_req_text += " " + " ".join(str(item) for item in provider_requirement.get("expected_inputs", []))
                if caller_module not in provider_req_text:
                    continue
                key = (
                    str(requirement.get("service_requirement_id", "")),
                    str(provider.get("function_id", "")),
                    str(provider_requirement.get("service_requirement_id", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                hints.append(
                    {
                        "requester_function_id": str(requirement.get("function_id", "")),
                        "requester_requirement_id": key[0],
                        "candidate_provider_function_id": key[1],
                        "provider_back_requirement_id": key[2],
                        "risk": "candidate provider has a cross-module service requirement back to the requester module; avoid reciprocal route/delivery calls",
                        "preferred_action": "bind requester -> provider only; leave provider delivery requirement unresolved unless an explicit delivery helper or callback provider exists",
                    }
                )
                if len(hints) >= 12:
                    return hints
    return hints


def _signature_type_table(draft: dict[str, Any], module_id: str, provider_public_types: list[dict[str, Any]]) -> dict[str, Any]:
    provider_types = [
        _compact_signature_type(type_item)
        for group in provider_public_types
        for type_item in group.get("types", [])
        if isinstance(type_item, dict)
    ]
    return {
        "current_module_types": [_compact_signature_type(item) for item in _module_type_inventory(draft, module_id)],
        "current_module_canonical_types": [_compact_signature_type(item) for item in _module_canonical_types(draft, module_id)],
        "provider_public_types": provider_types,
    }


def _global_public_symbol_names(draft: dict[str, Any], *, exclude_module_id: str) -> list[str]:
    names = {
        str(item.get("name", "")).strip()
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict)
        and str(item.get("module_id", "")) != exclude_module_id
        and (
            bool(item.get("exported"))
            or str(item.get("api_surface", "")).lower() == "public"
            or str(item.get("visibility", "")).lower() == "public"
        )
        and str(item.get("name", "")).strip()
    }
    return sorted(names)


def _signature_style_guide(protocol: str, module_id: str) -> dict[str, Any]:
    prefix = f"{_safe_id(protocol)}_{_safe_id(module_id)}"
    return {
        "raw_format": "no trailing semicolon; include static only for private source-local helpers",
        "public_symbol_policy": "public names should already be protocol/module-prefixed and globally unique",
        "lifecycle_patterns": [
            f"{prefix}_create(config/context params when needed) -> public opaque handle pointer or status",
            f"{prefix}_start(handle) -> bool/int status when a separate start phase exists",
            f"{prefix}_run(handle) or {prefix}_serve(handle) -> event loop status",
            f"{prefix}_destroy(handle) -> void",
        ],
        "codec_patterns": [
            "decoder feed: const uint8_t* buffer, size_t buffer_len, size_t* consumed, packet callback/out param",
            "primitive reader: const uint8_t* body, size_t body_len, size_t* pos, typed out param",
            "encoder: semantic packet fields or const packet*, owned bytes/result out param",
        ],
        "callback_patterns": [
            "callback registration should carry the module handle when callbacks are instance-scoped",
            "use void* user/context for caller-owned callback state",
        ],
    }


def _public_function_ids(functions: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("function_id", ""))
        for item in functions
        if isinstance(item, dict)
        and (
            bool(item.get("exported"))
            or str(item.get("api_surface", "")).lower() == "public"
            or str(item.get("visibility", "")).lower() == "public"
        )
        and str(item.get("function_id", "")).strip()
    ]


def _signature_normalization_policy(functions: list[dict[str, Any]]) -> dict[str, Any]:
    current_ids = [str(item.get("function_id", "")) for item in functions if isinstance(item, dict) and str(item.get("function_id", "")).strip()]
    public_ids = _public_function_ids(functions)
    return {
        "current_batch_function_ids": current_ids,
        "public_function_ids": public_ids,
        "source_only_function_ids": [function_id for function_id in current_ids if function_id not in set(public_ids)],
        "allowed_dependency_scopes": ["header", "source"],
        "system_type_owner_policy": "leave owner_module_id empty or current module; never system",
        "mechanical_fields_are_normalized": True,
    }


def _allowed_required_capability_ids(draft: dict[str, Any], functions: list[dict[str, Any]]) -> list[str]:
    capabilities = {
        str(item)
        for item in draft.get("traceability", {}).get("required_capabilities", [])
        if str(item).strip()
    }
    if not capabilities:
        capabilities = {
            str(capability)
            for function in functions
            if isinstance(function, dict)
            for capability in function.get("capability_ids", [])
            if str(capability).strip()
        }
    return sorted(capabilities)


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


def _signature_type_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        type_ref = normalize_system_type_ref(value.get("type_ref", ""))
        if type_ref:
            refs.add(type_ref)
        for item in value.values():
            refs.update(_signature_type_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_signature_type_refs(item))
    return refs


def _behavior_type_ids(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]]) -> list[str]:
    type_ids = _signature_type_refs([function.get("signature", {}) for function in functions if isinstance(function, dict)])
    provider_public_types = _provider_public_types_for_module(draft, module_id)
    type_ids.update(
        str(item.get("type_id", ""))
        for item in [*_module_canonical_types(draft, module_id), *_module_type_inventory(draft, module_id)]
        if isinstance(item, dict)
        and str(item.get("type_id", "")).strip()
        and str(item.get("visibility", "")).lower() in {"public", "internal", "module_internal"}
    )
    type_ids.update(
        str(type_item.get("type_id", ""))
        for group in provider_public_types
        for type_item in group.get("types", [])
        if isinstance(type_item, dict) and str(type_item.get("type_id", "")).strip()
    )
    return sorted(type_id for type_id in type_ids if type_id)


def _behavior_constraints(constraints: dict[str, Any], functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    batch_capabilities = {
        str(capability)
        for function in functions
        if isinstance(function, dict)
        for capability in function.get("capability_ids", [])
        if str(capability).strip()
    }
    result = []
    for constraint in _constraints(constraints):
        affected = {str(item) for item in constraint.get("affected_capabilities", []) if str(item).strip()}
        if not affected or affected & batch_capabilities:
            result.append(constraint)
    return result


def _scoped_behavior_legal_ids_for_batch(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], provider_public_api: list[dict[str, Any]]) -> dict[str, Any]:
    state_policy = _module_state_access_policy(draft, module_id)
    return {
        "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
        "function_ids": [str(item.get("function_id", "")) for item in functions if isinstance(item, dict)],
        "provider_public_function_ids": [str(item.get("function_id", "")) for item in provider_public_api if str(item.get("function_id", "")).strip()],
        "state_ids": [*state_policy["writable_state_ids"], *state_policy["read_only_external_state_ids"]],
        "error_ids": [str(item.get("error_id", "")) for item in draft.get("error_strategy", []) if isinstance(item, dict)],
        "type_ids": _behavior_type_ids(draft, module_id, functions),
        "system_type_ids": SYSTEM_TYPE_IDS,
    }


def _slug_identifier(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return text or "unknown"


def normalize_function_behavior_contract_patch(candidate: dict[str, Any], draft: dict[str, Any], constraints: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    result = deepcopy(candidate)
    stats = {"enum_fixes": 0, "state_owner_fixes": 0}
    functions = {str(item.get("function_id", "")): item for item in draft.get("function_contracts", []) if isinstance(item, dict)}
    state_owner = {str(item.get("state_id", "")): str(item.get("owner_module_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict)}
    updates = result.get("function_behavior_updates", [])
    if not isinstance(updates, list):
        return result, stats
    for update in updates:
        if not isinstance(update, dict):
            continue
        error_behavior = update.get("error_behavior")
        if isinstance(error_behavior, dict) and error_behavior.get("recovery") == "log_only":
            error_behavior["recovery"] = "none"
            stats["enum_fixes"] += 1
        service_requirements = update.get("service_requirements")
        if not isinstance(service_requirements, list):
            continue
        for requirement in service_requirements:
            if isinstance(requirement, dict) and requirement.get("failure_policy") == "log_only":
                requirement["failure_policy"] = "ignore"
                stats["enum_fixes"] += 1
        function_id = str(update.get("function_id", ""))
        function = functions.get(function_id, {})
        function_module = str(function.get("module_id", ""))
        state_access = update.get("state_access", [])
        if not isinstance(state_access, list):
            continue
        normalized_state_access = []
        for access in state_access:
            if not isinstance(access, dict):
                normalized_state_access.append(access)
                continue
            state_id = str(access.get("state_id", ""))
            owner = state_owner.get(state_id, "")
            access_kind = str(access.get("access_kind", ""))
            external_write = owner and owner != function_module and access_kind in {"write", "read_write"}
            if not external_write:
                normalized_state_access.append(access)
                continue
            if access_kind == "read_write":
                updated_access = dict(access)
                updated_access["access_kind"] = "read"
                updated_access["reason"] = f"{updated_access.get('reason', '').strip()} Cross-module mutation is requested through a service requirement.".strip()
                normalized_state_access.append(updated_access)
            requirement_id = f"svc:req:{_slug_identifier(function_id)}:state:{_slug_identifier(state_id)}"
            if not any(isinstance(item, dict) and item.get("service_requirement_id") == requirement_id for item in service_requirements):
                service_requirements.append(
                    {
                        "service_requirement_id": requirement_id,
                        "requirement_kind": "cross_module_service",
                        "operation": f"request owner module '{owner}' to mutate state '{state_id}'",
                        "required_capability_ids": function.get("capability_ids", []) if isinstance(function, dict) else [],
                        "expected_inputs": [state_id],
                        "expected_output": "status",
                        "failure_policy": "return_error",
                    }
                )
            stats["state_owner_fixes"] += 1
        update["state_access"] = normalized_state_access
    return result, stats


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


def build_function_signature_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    provider_public_types = _provider_public_types_for_module(draft, module_id)
    protocol = str(draft.get("protocol_name", "protocol"))
    return {
        "schema_version": "function_signature_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": [_compact_signature_function(item) for item in functions if isinstance(item, dict)],
        "required_update_skeleton": _signature_update_skeleton(functions),
        "module_summary": _module_summary(draft, module_id),
        "signature_type_table": _signature_type_table(draft, module_id, provider_public_types),
        "provider_public_types": provider_public_types,
        "global_public_symbol_names": _global_public_symbol_names(draft, exclude_module_id=module_id),
        "signature_style_guide": _signature_style_guide(protocol, module_id),
        "signature_normalization_policy": _signature_normalization_policy(functions),
        "legal_id_universe": _scoped_signature_legal_ids(draft, module_id, functions, provider_public_types),
    }


def build_function_behavior_context(draft: dict[str, Any], module_id: str, functions: list[dict[str, Any]], constraints: dict[str, Any], *, batch_index: int, batch_size: int) -> dict[str, Any]:
    provider_public_api = _provider_public_behavior_api_summary(draft, module_id)
    scoped_constraints = _behavior_constraints(constraints, functions)
    allowed_capabilities = _allowed_required_capability_ids(draft, functions)
    return {
        "schema_version": "function_behavior_context/v1",
        "module_id": module_id,
        "batch": {"index": batch_index, "size": batch_size},
        "functions": [_compact_behavior_function(item) for item in functions if isinstance(item, dict)],
        "required_update_skeleton": _behavior_update_skeleton(functions),
        "service_requirement_policy": {
            "external_runtime_examples": ["socket", "accept", "read", "write", "close", "epoll", "malloc", "free", "timer"],
            "allowed_required_capability_ids": allowed_capabilities,
            "external_runtime_operation_policy": "socket/epoll/malloc/free/timer describe operations, not new capability_ids",
            "cross_module_only": True,
            "provider_own_responsibility": "do_not_emit_service_requirement",
        },
        "behavior_quality_policy": {
            "coder_facing_action_required": True,
            "state_or_resource_effect_required_for_mutating_functions": True,
            "failure_path_required_for_public_or_resource_functions": True,
            "invariant_examples": ["buffer_bounds", "remaining_length_valid", "fd_or_session_ownership", "subscription_index_consistency", "state_transition_legality", "cleanup_idempotence"],
            "event_logic_kind_requires_complete_event_contract": True,
        },
        "module_summary": _module_summary(draft, module_id),
        "module_state_access_policy": _module_state_access_policy(draft, module_id),
        "module_resource_refs": _module_resource_refs(draft, module_id),
        "provider_public_api_summary": provider_public_api,
        "engineering_constraints": scoped_constraints,
        "legal_id_universe": _scoped_behavior_legal_ids_for_batch(draft, module_id, functions, provider_public_api) | {"constraint_ids": [item["constraint_id"] for item in scoped_constraints]},
    }


def build_wire_access_binding_context(draft: dict[str, Any], planning_ir: dict[str, Any]) -> dict[str, Any]:
    codec_and_handler_functions = [
        _compact_behavior_function(item)
        for item in draft.get("function_contracts", [])
        if isinstance(item, dict) and item.get("function_kind") in {"parser", "serializer", "handler"}
    ]
    field_summaries = _field_summaries(planning_ir)
    message_summaries = _message_summaries(planning_ir)
    return {
        "schema_version": "wire_access_binding_context/v1",
        "codec_and_handler_functions": codec_and_handler_functions,
        "field_summaries": field_summaries,
        "wire_binding_policy": {
            "required_field_ids": [item["field_id"] for item in field_summaries],
            "coverage_directions": ["parse", "serialize"],
            "parser_serializer_access_kind": "read",
        },
        "state_design": draft.get("state_design", []),
        "access_target_types": [_compact_access_type(item) for item in draft.get("type_inventory", []) if isinstance(item, dict)],
        "legal_id_universe": {
            "function_ids": [str(item.get("function_id", "")) for item in codec_and_handler_functions if str(item.get("function_id", "")).strip()],
            "state_ids": [str(item.get("state_id", "")) for item in draft.get("state_design", []) if isinstance(item, dict)],
            "type_ids": [str(item.get("type_id", "")) for item in draft.get("type_inventory", []) if isinstance(item, dict)],
            "message_ids": [item["message_id"] for item in message_summaries],
            "field_ids": [item["field_id"] for item in field_summaries],
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
    callable_functions = _callable_functions(draft, current_module) if current_module else []
    expected_requirements = _expected_cross_module_service_requirements(scoped_functions)
    cross_module_caller_ids = {str(item.get("function_id", "")) for item in expected_requirements if str(item.get("function_id", "")).strip()}
    cross_module_callers = [
        item
        for item in scoped_functions
        if isinstance(item, dict) and str(item.get("function_id", "")) in cross_module_caller_ids
    ]
    provider_candidates = _candidate_provider_functions(expected_requirements, callable_functions, scoped_functions)
    provider_summaries = _provider_function_summaries(provider_candidates, callable_functions)
    return {
        "schema_version": "calls_allowed_context/v1",
        "module_id": current_module,
        "batch": {"index": batch_index, "size": batch_size} if batch_index is not None else {},
        "callers": [_compact_cross_module_call_function(item) for item in cross_module_callers],
        "service_requirements": [
            {
                "function_id": item.get("function_id"),
                "cross_module_service_requirements": [
                    requirement
                    for requirement in _compact_cross_module_call_function(item).get("service_requirements", [])
                    if isinstance(requirement, dict) and str(requirement.get("requirement_kind", "cross_module_service")) == "cross_module_service"
                ],
            }
            for item in cross_module_callers
        ],
        "callable_functions": provider_summaries,
        "required_call_update_caller_ids": sorted(scoped_ids),
        "required_call_update_skeleton": _call_update_skeleton(cross_module_callers),
        "expected_cross_module_service_requirements": expected_requirements,
        "candidate_provider_functions": provider_candidates,
        "call_graph_direction_hints": _call_graph_direction_hints(current_module, scoped_functions, callable_functions) if current_module else [],
        "service_cycle_risk_hints": _service_cycle_risk_hints(expected_requirements, callable_functions, scoped_functions),
        "normalization_policy": {
            "baseline_merge": "deterministic same-module calls are generated before this prompt and merged with accepted cross-module service edges",
            "batch_coverage": "normalizer keeps exactly required_call_update_caller_ids and fills missing callers from deterministic baseline",
            "service_requirement_closure": "normalizer keeps resolved current-batch service ids and adds unresolved ids for any expected service requirement left unresolved",
            "invalid_edges": "normalizer drops unknown, self, private cross-module, and out-of-callable-universe callees before validation",
            "cycle_breaking": "aggregate normalization removes cycle edges before final validation while preserving service closure by adding removed unresolved service ids back to unresolved_service_requirements",
        },
        "architecture_policy": {"selected_modules": _selected_modules(selected_architecture), "forbidden_cycles": True},
        "legal_id_universe": {
            "function_ids": sorted(
                {
                    str(item.get("function_id", ""))
                    for item in [*scoped_functions, *callable_functions]
                    if isinstance(item, dict) and str(item.get("function_id", "")).strip()
                }
            ),
            "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
        },
    }


def _function_kind_counts(functions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for function in functions:
        kind = str(function.get("function_kind", "unknown")) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _layout_role_units(module: dict[str, Any], functions: list[dict[str, Any]]) -> list[str]:
    text = _module_text(module)
    kind_text = " ".join(str(function.get("function_kind", "")) for function in functions).lower()
    function_text = " ".join(str(function.get("name", "")) for function in functions).lower()
    combined = f"{text} {kind_text} {function_text}"
    units: list[str] = []
    if any(term in combined for term in ("network", "transport", "socket", "tcp", "epoll", "connection")):
        if "connection" in combined:
            units.append("connection")
        if any(term in combined for term in ("server", "accept", "listen", "epoll", "tcp")):
            units.append("tcp_server")
    if any(term in combined for term in ("codec", "packet", "decode", "encode", "parser", "serializer", "wire", "remaining_length")):
        if any(term in combined for term in ("packet", "payload", "model", "free")):
            units.append("packet")
        if any(term in combined for term in ("decode", "parser", "read", "remaining_length")):
            units.append("decoder")
        if any(term in combined for term in ("encode", "serializer", "write", "put")):
            units.append("encoder")
    if any(term in combined for term in ("session", "client state", "session_manager")):
        units.append("session")
        if any(term in combined for term in ("manager", "registry", "add", "get", "remove")):
            units.append("session_manager")
    if any(term in combined for term in ("topic", "subscription", "routing", "router")):
        if any(term in combined for term in ("topic", "tree", "subscription", "entry")):
            units.append("topic_tree")
        if any(term in combined for term in ("route", "router", "dispatch", "message")):
            units.append("message_router")
    if any(term in combined for term in ("broker", "app", "role_composition", "callback", "on_data", "handle_packet")):
        units.append("broker")
    return sorted(dict.fromkeys(units))


def _layout_function_cluster(function: dict[str, Any]) -> dict[str, Any]:
    service_requirements = [
        {
            "service_requirement_id": requirement.get("service_requirement_id"),
            "requirement_kind": requirement.get("requirement_kind"),
            "operation": requirement.get("operation", ""),
        }
        for requirement in function.get("service_requirements", [])
        if isinstance(requirement, dict)
    ][:4]
    call_contracts = function.get("call_contract_planning", function.get("calls_allowed", []))
    contract = function.get("behavior_contract", {}) if isinstance(function.get("behavior_contract"), dict) else {}
    return {
        "function_id": function.get("function_id"),
        "name": function.get("name"),
        "function_kind": function.get("function_kind"),
        "visibility": function.get("visibility"),
        "api_surface": function.get("api_surface"),
        "exported": bool(function.get("exported")),
        "logic_kind": function.get("logic_kind", ""),
        "grouping_hint": function.get("grouping_hint", ""),
        "action": contract.get("action", ""),
        "service_requirements": service_requirements,
        "call_contract_count": len(call_contracts) if isinstance(call_contracts, list) else 0,
    }


def _file_layout_module_summaries(draft: dict[str, Any]) -> list[dict[str, Any]]:
    functions_by_module: dict[str, list[dict[str, Any]]] = {}
    for function in draft.get("function_contracts", []):
        if isinstance(function, dict):
            functions_by_module.setdefault(str(function.get("module_id", "")), []).append(function)
    summaries: list[dict[str, Any]] = []
    exportable_by_module: dict[str, list[dict[str, str]]] = {}
    for type_item in _public_exportable_types(draft):
        exportable_by_module.setdefault(str(type_item.get("owner_module_id", "")), []).append(type_item)
    for module in draft.get("module_artifacts", []):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("module_id", ""))
        functions = functions_by_module.get(module_id, [])
        public_functions = [function for function in functions if bool(function.get("exported")) or str(function.get("api_surface", "")).lower() == "public" or str(function.get("visibility", "")).lower() == "public"]
        private_functions = [function for function in functions if function not in public_functions]
        decomposition = select_top_decomposition_hints(
            module,
            {
                "provider_module_artifacts": [
                    item
                    for item in draft.get("module_artifacts", [])
                    if isinstance(item, dict) and str(item.get("module_id", "")) in {str(dep) for dep in module.get("dependencies", [])}
                ],
                "consumer_module_artifact_dependencies": [
                    item
                    for item in draft.get("module_artifacts", [])
                    if isinstance(item, dict) and module_id in {str(dep) for dep in item.get("dependencies", [])}
                ],
                "core_design_summary": draft,
            },
            max_hints=2,
        )
        summaries.append(
            {
                "module_id": module_id,
                "role": module.get("role", module.get("purpose", "")),
                "owned_capabilities": module.get("owned_capabilities", []),
                "dependencies": module.get("dependencies", []),
                "function_count": len(functions),
                "function_kind_counts": _function_kind_counts(functions),
                "public_function_ids": [str(function.get("function_id", "")) for function in public_functions],
                "private_function_ids": [str(function.get("function_id", "")) for function in private_functions],
                "recommended_file_units": _layout_role_units(module, functions),
                "public_exportable_types": exportable_by_module.get(module_id, []),
                "selected_decomposition_rules": decomposition.get("selected_rule_ids", []),
                "function_clusters": [_layout_function_cluster(function) for function in functions],
            }
        )
    return summaries


def build_file_layout_context(draft: dict[str, Any], planning_ir: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    exportable_types = _public_exportable_types(draft)
    return {
        "schema_version": "file_layout_context/v1",
        "module_artifacts": draft.get("module_artifacts", []),
        "function_summary": _accepted_summary(draft)["function_contracts"],
        "module_file_layout_summaries": _file_layout_module_summaries(draft),
        "target_language": str(_target_directives(planning_ir).get("language", "C")),
        "layout_policy": "source_header_pair",
        "file_split_policy": {
            "split_by_role_when_non_trivial": True,
            "do_not_add_functions": True,
            "all_existing_functions_exactly_one_assignment": True,
            "public_function_header_export_exactly_one": True,
            "role_unit_examples": ["connection", "tcp_server", "packet", "decoder", "encoder", "session", "session_manager", "topic_tree", "message_router", "broker"],
        },
        "engineering_constraints": _constraints(constraints),
        "public_exportable_type_ids": [str(item["type_id"]) for item in exportable_types],
        "public_exportable_types_by_module": {
            module_id: [item for item in exportable_types if str(item.get("owner_module_id", "")) == module_id]
            for module_id in sorted({str(item.get("owner_module_id", "")) for item in exportable_types if str(item.get("owner_module_id", ""))})
        },
        "legal_id_universe": _legal_ids_from_draft(draft),
    }


def build_file_layout_override_context(draft: dict[str, Any], baseline: dict[str, Any], diagnostics: list[Any]) -> dict[str, Any]:
    files = [item for item in baseline.get("files", []) if isinstance(item, dict)]
    assignments = [item for item in baseline.get("function_file_assignments", []) if isinstance(item, dict)]
    file_ids_by_module: dict[str, list[str]] = {}
    for file_item in files:
        module_id = str(file_item.get("module_id", ""))
        file_id = str(file_item.get("file_id", ""))
        if module_id and file_id:
            file_ids_by_module.setdefault(module_id, []).append(file_id)
    function_ids_by_module: dict[str, list[str]] = {}
    for function in draft.get("function_contracts", []):
        if isinstance(function, dict):
            function_ids_by_module.setdefault(str(function.get("module_id", "")), []).append(str(function.get("function_id", "")))
    return {
        "schema_version": "file_layout_override_context/v1",
        "baseline_files": [
            {
                "file_id": item.get("file_id"),
                "source_path": item.get("source_path"),
                "header_path": item.get("header_path"),
                "module_id": item.get("module_id"),
                "responsibility": item.get("responsibility"),
                "exports_function_ids": item.get("exports_function_ids", []),
                "implements_function_ids": item.get("implements_function_ids", []),
            }
            for item in files
        ],
        "baseline_assignments": [
            {
                "function_id": item.get("function_id"),
                "implementation_file_id": item.get("implementation_file_id"),
                "declaration_file_id": item.get("declaration_file_id"),
                "visibility": item.get("visibility"),
            }
            for item in assignments
        ],
        "module_file_layout_summaries": _file_layout_module_summaries(draft),
        "diagnostics": [
            {
                "level": getattr(item, "level", ""),
                "code": getattr(item, "code", ""),
                "message": getattr(item, "message", ""),
            }
            for item in diagnostics
        ],
        "override_policy": {
            "prefer_keep_baseline": True,
            "allowed_file_ids_by_module": file_ids_by_module,
            "function_ids_by_module": function_ids_by_module,
        },
        "legal_id_universe": {
            "module_ids": [str(item.get("module_id", "")) for item in draft.get("module_artifacts", []) if isinstance(item, dict)],
            "function_ids": [str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)],
            "file_ids": [str(item.get("file_id", "")) for item in files],
        },
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
