from __future__ import annotations

from copy import deepcopy
import re
import shutil
from pathlib import Path
from typing import Any

from .facts import write_json
from .registry import CanonicalPlanningRegistry


_CUSTOM_TYPE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*_t\b")
_STANDARD_TYPES = {
    "bool", "int8_t", "int16_t", "int32_t", "int64_t", "intptr_t", "ptrdiff_t",
    "size_t", "ssize_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "uintptr_t",
}


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _stage_artifacts(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for item in plan.get("structured_planning_stages", []):
        if isinstance(item, dict) and isinstance(item.get("artifact"), dict):
            artifacts[str(item.get("stage_id", ""))] = item["artifact"]
    return artifacts


def _clean_trace(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_./-]+", "_", value.strip().strip("/")).strip("_/") or "artifact"


def _strip_c_suffix(path: str) -> str:
    return re.sub(r"\.(c|h)$", "", path.strip())


def _trace_from_path(protocol_slug: str, path: str) -> str:
    clean = _clean_trace(_strip_c_suffix(path))
    if not clean.startswith(f"{protocol_slug}/"):
        clean = f"{protocol_slug}/{clean}"
    return clean


def _companion_source_path(header_path: str) -> str:
    if header_path.endswith(".h"):
        return f"{header_path[:-2]}.c"
    return f"{header_path}.c"


def _companion_header_path(source_path: str) -> str:
    return str(Path(source_path).with_suffix(".h"))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _unique(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out


def _normalize_visibility(value: Any, *, public_true: bool = False) -> str:
    if value is True:
        return "PUBLIC" if public_true else "public"
    if value is False:
        return "PRIVATE" if public_true else "private"
    text = str(value or "").lower()
    if text in {"public", "external", "api"}:
        return "PUBLIC" if public_true else "public"
    return "PRIVATE" if public_true else "private"


def _split_params(params: str) -> list[str]:
    out: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(params):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            out.append(params[start:index].strip())
            start = index + 1
    tail = params[start:].strip()
    if tail and tail != "void":
        out.append(tail)
    return out


def _parse_param(raw: str) -> dict[str, Any]:
    callback = re.search(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", raw)
    if callback:
        name = callback.group(1)
        c_type = raw.replace(name, "").replace("(*)", "(*)").strip()
    else:
        match = re.match(r"(?P<type>.+?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?$", raw)
        if match:
            name = match.group("name")
            c_type = match.group("type").strip()
        else:
            name = raw.rsplit(" ", 1)[-1].strip("* ")
            c_type = raw[: -len(name)].strip() if name else raw
    return {
        "TYPE": c_type or raw,
        "NAME": name or "param",
        "NULLABLE": "*" in raw,
        "OWNERSHIP": "BORROWED",
    }


def _signature(raw: Any, fallback_name: str) -> dict[str, Any]:
    text = str(raw.get("RAW", "")) if isinstance(raw, dict) else str(raw or "")
    text = text.strip().removesuffix(";").strip()
    match = re.match(r"(?P<return>.+?)\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>.*)\)\s*$", text)
    if not match:
        raise ValueError(f"Canonical function {fallback_name!r} is missing a complete C signature")
    if isinstance(raw, dict) and {"RAW", "NAME", "RETURN", "PARAMS"} <= set(raw):
        if str(raw.get("NAME")) != match.group("name"):
            raise ValueError(f"Canonical function {fallback_name!r} has inconsistent signature identity")
        return raw
    return {
        "RAW": text,
        "NAME": match.group("name"),
        "RETURN": match.group("return").strip(),
        "PARAMS": [_parse_param(item) for item in _split_params(match.group("params"))],
    }


def _callback_signature(raw: Any, name: str) -> str:
    if isinstance(raw, dict):
        return_type = str(raw.get("return_type") or raw.get("RETURN") or "void")
        parameters = raw.get("parameters", raw.get("PARAMS", []))
        rendered = []
        for index, parameter in enumerate(parameters if isinstance(parameters, list) else []):
            if not isinstance(parameter, dict):
                continue
            parameter_type = str(parameter.get("type") or parameter.get("TYPE") or "void*")
            parameter_name = str(parameter.get("name") or parameter.get("NAME") or f"arg{index}")
            rendered.append(f"{parameter_type} {parameter_name}")
        return f"{return_type} (*{name})({', '.join(rendered) or 'void'})"
    signature = str(raw or "void (*)(void)").strip()
    if signature.startswith("typedef "):
        signature = signature[len("typedef ") :].strip()
    signature = signature.removesuffix(";").strip()
    return re.sub(r"\(\s*\*\s*\)", f"(*{name})", signature, count=1)


def _normalize_array_members(spec: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(spec)
    for key in ("FIELDS", "VARIANTS"):
        for member in normalized.get(key, []):
            if not isinstance(member, dict):
                continue
            match = re.fullmatch(r"([^\[\]]+?)\s*\[([^\[\]]+)\]", str(member.get("TYPE", "")).strip())
            if match and "ARRAY_LEN" not in member:
                member["TYPE"] = match.group(1).strip()
                member["ARRAY_LEN"] = match.group(2).strip()
            if isinstance(member.get("TYPE_SPEC"), dict):
                member["TYPE_SPEC"] = _normalize_array_members(member["TYPE_SPEC"])
    return normalized


def _omit_unresolved_type_members(spec: dict[str, Any], known_types: set[str]) -> dict[str, Any]:
    normalized = deepcopy(spec)
    for key in ("FIELDS", "VARIANTS"):
        members = []
        for member in normalized.get(key, []):
            if not isinstance(member, dict):
                continue
            refs = set(_CUSTOM_TYPE.findall(str(member.get("TYPE", ""))))
            if refs - known_types - _STANDARD_TYPES:
                continue
            if isinstance(member.get("TYPE_SPEC"), dict):
                member["TYPE_SPEC"] = _omit_unresolved_type_members(member["TYPE_SPEC"], known_types)
            members.append(member)
        if key in normalized:
            normalized[key] = members
    return normalized


def _lower_type_spec(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("type_spec"), dict):
        spec = dict(item["type_spec"])
        if str(spec.get("TYPE_KIND", "")).upper() == "CALLBACK":
            name = str(item.get("name") or item.get("type_name") or item.get("symbol") or "callback_t")
            spec["CALLBACK_SIGNATURE"] = _callback_signature(spec.get("CALLBACK_SIGNATURE"), name)
        return _normalize_array_members(spec)
    kind = str(item.get("type_kind") or item.get("kind") or "OPAQUE").upper()
    fields = item.get("fields", [])
    if kind == "ENUM":
        return {
            "TYPE_KIND": "ENUM",
            "ENUM_VALUES": [
                {
                    "NAME": str(field.get("name", "")),
                    "VALUE": field.get("value"),
                    "ROLE": str(field.get("summary") or field.get("role") or f"{field.get('name', 'enum')} value."),
                }
                for field in fields
                if isinstance(field, dict) and field.get("name")
            ],
        }
    if kind == "STRUCT":
        return _normalize_array_members({
            "TYPE_KIND": "STRUCT",
            "FIELDS": [
                {
                    "NAME": str(field.get("name", "")),
                    "TYPE": str(field.get("c_type") or field.get("type") or "void*"),
                    "ROLE": str(field.get("summary") or field.get("role") or f"{field.get('name', 'field')} value."),
                }
                for field in fields
                if isinstance(field, dict) and field.get("name")
            ],
        })
    if kind == "CALLBACK":
        name = str(item.get("name") or item.get("type_name") or item.get("symbol") or "callback_t")
        signature = _callback_signature(item.get("c_type") or item.get("signature"), name)
        return {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": signature}
    if kind == "ALIAS":
        return {"TYPE_KIND": "ALIAS", "ALIAS_OF": str(item.get("alias_of", "void*"))}
    if kind == "UNION":
        return {"TYPE_KIND": "UNION"}
    return {"TYPE_KIND": "OPAQUE"}


def _string_items(values: Any, *, kind: str = "") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for value in _as_list(values):
        if isinstance(value, dict) and value.get("NAME"):
            item = {"NAME": str(value["NAME"]), "ROLE": str(value.get("ROLE", value.get("role", "planned dependency")))}
            if kind == "FUNC":
                item["KIND"] = str(value.get("KIND", "CALL"))
            out.append(item)
        elif str(value).strip():
            item = {"NAME": str(value), "ROLE": "planned dependency"}
            if kind == "FUNC":
                item["KIND"] = "CALL"
            out.append(item)
    return out


def _normalize_rely(value: Any) -> dict[str, list[dict[str, str]]]:
    raw = value if isinstance(value, dict) else {}
    return {
        "STRUCT": _string_items(raw.get("STRUCT", raw.get("RELY.STRUCT", []))),
        "FUNC": _string_items(raw.get("FUNC", raw.get("RELY.FUNC", [])), kind="FUNC"),
        "VAR": _string_items(raw.get("VAR", raw.get("RELY.VAR", []))),
    }


def _sentence(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if str(item).strip())
    return str(value or "")


def _normalize_logic(value: Any, role: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    if {"INPUT", "ACTION", "OUTPUT", "INVARIANTS_USED"} <= set(raw):
        return {
            "INPUT": str(raw.get("INPUT", "")),
            "ACTION": str(raw.get("ACTION", "")),
            "OUTPUT": str(raw.get("OUTPUT", "")),
            "INVARIANTS_USED": [str(item) for item in _as_list(raw.get("INVARIANTS_USED"))],
        }
    return {
        "INPUT": _sentence(raw.get("preconditions")) or "Inputs follow the function signature.",
        "ACTION": _sentence(raw.get("state_changes")) or str(role),
        "OUTPUT": _sentence(raw.get("response_behavior") or raw.get("postconditions")) or "Return according to the signature contract.",
        "INVARIANTS_USED": [str(item) for item in _as_list(raw.get("invariants_used"))],
        "PRECONDITION": _sentence(raw.get("preconditions")),
        "POSTCONDITION": _sentence(raw.get("postconditions")),
    }


def _normalize_event(value: Any, role: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    if {"TRIGGER", "PRECONDITION", "INPUT", "ACTION", "STATE_CHANGE", "RESPONSE", "EVENT_TYPE"} <= set(raw):
        return {key: raw[key] for key in ("TRIGGER", "PRECONDITION", "INPUT", "ACTION", "STATE_CHANGE", "RESPONSE", "EVENT_TYPE")}
    return {
        "TRIGGER": str(raw.get("trigger") or role),
        "PRECONDITION": _sentence(raw.get("preconditions")),
        "INPUT": _sentence(raw.get("input")) or "Event input follows the callback signature.",
        "ACTION": _sentence(raw.get("postconditions")) or str(role),
        "STATE_CHANGE": _sentence(raw.get("state_changes")),
        "RESPONSE": _sentence(raw.get("response_behavior")),
        "EVENT_TYPE": "callback",
        "INVARIANTS_USED": [str(item) for item in _as_list(raw.get("invariants_used"))],
    }


def _normalize_vectors(values: Any, prefix: str, level: str) -> list[dict[str, Any]]:
    vectors: list[dict[str, Any]] = []
    for index, value in enumerate(_as_list(values), 1):
        if not isinstance(value, dict):
            continue
        if {"NAME", "INPUT", "EXPECT"} <= set(value):
            vector = dict(value)
        else:
            vector = {
                "NAME": str(value.get("NAME") or value.get("name") or f"{prefix}_{index}"),
                "INPUT": value.get("INPUT") or value.get("input") or {k: v for k, v in value.items() if k.startswith("input")},
                "EXPECT": value.get("EXPECT") or value.get("expect") or {k: v for k, v in value.items() if k.startswith(("expected", "output"))},
            }
        vector.setdefault("LEVEL", level)
        vectors.append(vector)
    return vectors


def _normalize_forbidden(values: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for value in _as_list(values):
        if isinstance(value, dict) and value.get("NAME"):
            out.append({"NAME": str(value["NAME"]), "KIND": str(value.get("KIND", "")), "REASON": str(value.get("REASON", value.get("reason", "Forbidden by planning.")))})
        elif str(value).strip():
            out.append({"NAME": str(value), "KIND": "", "REASON": "Forbidden by planning."})
    return out


def _normalize_rules(values: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, value in enumerate(_as_list(values), 1):
        if isinstance(value, dict) and value.get("ID"):
            out.append({"ID": str(value["ID"]), "RULE": str(value.get("RULE", value.get("rule", ""))), "DOC_REF": [str(item) for item in _as_list(value.get("DOC_REF", value.get("doc_ref", [])))]})
        elif str(value).strip():
            out.append({"ID": f"PC{index}", "RULE": str(value), "DOC_REF": []})
    return out


def _normalize_wire_mapping(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if {"PACKET", "WIRE_FIELD", "STRATEGY"} <= set(item):
            out.append({key: str(item[key]) for key in ("PACKET", "WIRE_FIELD", "STRATEGY")})
        else:
            out.append({"PACKET": str(item.get("packet", "packet")), "WIRE_FIELD": str(item.get("wire_field", "wire")), "STRATEGY": "store_in_field", "RULE": str(item)})
    return out


def _normalize_call_contracts(values: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in _as_list(values):
        if not isinstance(item, dict):
            continue
        name = str(item.get("NAME") or item.get("callee") or "")
        signature = str(item.get("SIGNATURE") or item.get("signature") or "")
        if name:
            out.append({"NAME": name, "SIGNATURE": signature})
    return out


def normalize_plan_for_compiler(plan: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(plan.get("implementation_plan", plan))
    snapshot = raw.get("canonical_registry_snapshot")
    registry = CanonicalPlanningRegistry.from_snapshot(snapshot) if isinstance(snapshot, dict) else None
    stages = _stage_artifacts(raw)
    protocol = raw.get("protocol", {})
    slug = str(protocol.get("slug") or protocol.get("name", "protocol")).lower()

    dep_files = {str(item.get("id")): item for item in stages.get("dependency_closure", {}).get("files", []) if isinstance(item, dict)}
    file_groups: dict[tuple[str, str], dict[str, Any]] = {}
    alias_to_file_id: dict[str, str] = {}
    for item in raw.get("files", []):
        if not isinstance(item, dict):
            continue
        module = str(item.get("module", ""))
        path = str(item.get("source_path") or item.get("header_path") or item.get("id", "file"))
        registry_entry = registry.resolve(item.get("id") or path, expected_kinds={"file"}) if registry else None
        if registry_entry is not None:
            module = registry.resolve(registry_entry["owner_module_id"], expected_kinds={"module"})["canonical_name"]
        key = (module, _strip_c_suffix(path))
        group = file_groups.setdefault(
            key,
            {
                "id": registry_entry["artifact_id"] if registry_entry is not None else f"file:{_trace_from_path(slug, path)}",
                "module": module,
                "trace_id": registry_entry["canonical_name"] if registry_entry is not None else _trace_from_path(slug, path),
                "role_parts": [],
                "header_path": None,
                "source_path": None,
                "header_dependencies": [],
                "source_dependencies": [],
                "types": [],
                "functions": [],
                "trace_refs": [],
                "forbidden_symbols": [],
                "test_vectors": [],
                "_raw_ids": [],
            },
        )
        group["_raw_ids"].append(str(item.get("id", "")))
        group["role_parts"].append(str(item.get("role", "")))
        group["trace_refs"].extend(str(ref) for ref in _as_list(item.get("trace_refs")))
        if item.get("header_path"):
            group["header_path"] = str(item["header_path"])
        if item.get("source_path"):
            group["source_path"] = str(item["source_path"])
        intent = item.get("dependency_intent", {}) if isinstance(item.get("dependency_intent"), dict) else {}
        closure = dep_files.get(str(item.get("id")), {})
        group["header_dependencies"].extend(_as_list(item.get("header_dependencies")))
        group["source_dependencies"].extend(_as_list(item.get("source_dependencies")))
        if item.get("header_path"):
            group["header_dependencies"].extend(_as_list(intent.get("public_includes")) + _as_list(closure.get("header_dependencies")))
        if item.get("source_path"):
            group["source_dependencies"].extend(_as_list(intent.get("public_includes")) + _as_list(closure.get("header_dependencies")))
        group["source_dependencies"].extend(_as_list(intent.get("private_includes")) + _as_list(closure.get("source_dependencies")))

    shared_header_owners: dict[str, str] = {}
    groups_by_header: dict[str, list[dict[str, Any]]] = {}
    for group in file_groups.values():
        if group["header_path"]:
            groups_by_header.setdefault(group["header_path"], []).append(group)
    for header_path, groups in groups_by_header.items():
        if len(groups) < 2:
            continue
        owner = next(
            (group for group in groups if _strip_c_suffix(str(group.get("source_path") or "")) == _strip_c_suffix(header_path)),
            groups[0],
        )
        shared_header_owners[header_path] = owner["id"]
        for group in groups:
            if group is not owner and group.get("source_path"):
                group["header_path"] = _companion_header_path(group["source_path"])

    header_aliases: dict[str, str] = {}
    for group in file_groups.values():
        if group["header_path"]:
            registry_aliases: list[str] = []
            if registry is not None:
                registry_aliases = registry.resolve(group["id"], expected_kinds={"file"}).get("aliases", [])
            for alias in [
                group["header_path"],
                Path(group["header_path"]).name,
                *group["_raw_ids"],
                *registry_aliases,
            ]:
                if str(alias) not in shared_header_owners or shared_header_owners[str(alias)] == group["id"]:
                    header_aliases.setdefault(str(alias), group["header_path"])
        for alias in [group["source_path"], Path(str(group["source_path"])).name if group["source_path"] else "", group["header_path"], Path(str(group["header_path"])).name if group["header_path"] else "", *group["_raw_ids"]]:
            if alias:
                alias_to_file_id[str(alias)] = group["id"]

    files: list[dict[str, Any]] = []
    for group in file_groups.values():
        if not group["source_path"] and group["header_path"]:
            group["source_path"] = _companion_source_path(group["header_path"])
        if group["header_path"] and group["source_path"]:
            group["source_dependencies"].append(group["header_path"])
        group["header_dependencies"] = _unique(
            [
                header_aliases.get(str(dep), str(dep))
                for dep in group["header_dependencies"]
                if str(dep).strip() and header_aliases.get(str(dep), str(dep)) != group["header_path"]
            ]
        )
        group["source_dependencies"] = _unique([header_aliases.get(str(dep), str(dep)) for dep in group["source_dependencies"] if str(dep).strip()])
        files.append(
            {
                "id": group["id"],
                "module": group["module"],
                "trace_id": group["trace_id"],
                "role": "; ".join(part for part in _unique(group["role_parts"]) if part) or "Generated protocol file specification.",
                "language": "C",
                "header_path": group["header_path"],
                "source_path": group["source_path"],
                "header_dependencies": group["header_dependencies"],
                "source_dependencies": group["source_dependencies"],
                "types": group["types"],
                "functions": group["functions"],
                "trace_refs": _unique(group["trace_refs"]),
                "forbidden_symbols": _normalize_forbidden(group["forbidden_symbols"]),
                "test_vectors": _normalize_vectors(group["test_vectors"], group["id"], "FUNCTION"),
            }
        )

    files_by_id = {item["id"]: item for item in files}
    types: list[dict[str, Any]] = []
    type_roles: dict[str, str] = {}
    for item in raw.get("types", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("type_name") or item.get("symbol") or "")
        registry_entry = registry.resolve(item.get("id") or name, expected_kinds={"type", "callback"}) if registry else None
        if registry_entry is not None:
            name = registry_entry["canonical_name"]
            file_id = registry_entry["owner_file_id"]
        else:
            file_id = alias_to_file_id.get(str(item.get("file") or item.get("owner_file") or ""), next(iter(files_by_id), ""))
        normalized = {
            "id": registry_entry["artifact_id"] if registry_entry is not None else str(item.get("id") or f"type:{name}"),
            "file": file_id,
            "name": name,
            "kind": "TYPE",
            "visibility": _normalize_visibility(
                registry_entry["visibility"] if registry_entry is not None else item.get("visibility", item.get("public_visibility", bool(item.get("public_access_path")))),
                public_true=True,
            ),
            "role": str(item.get("role") or item.get("ownership_semantics") or f"{name} protocol data type."),
            "type_spec": _lower_type_spec(item),
            "trace_refs": _unique([str(ref) for ref in _as_list(item.get("trace_refs", item.get("source_facts", [])))]),
            "decision_refs": _as_list(item.get("decision_refs")),
            "rule_refs": _as_list(item.get("rule_refs")),
            "resource_handle": bool(item.get("resource_handle")),
            "ownership_model": str(item.get("ownership_model", "")),
            "opaque_boundaries": item.get("opaque_boundaries", {}) if isinstance(item.get("opaque_boundaries"), dict) else {},
            "ownership_fields": _as_list(item.get("ownership_fields")),
        }
        types.append(normalized)
        type_roles[name] = normalized["role"]
        if file_id in files_by_id:
            files_by_id[file_id]["types"].append(normalized["id"])

    known_types = {item["name"] for item in types}
    for type_item in types:
        type_item["type_spec"] = _omit_unresolved_type_members(type_item["type_spec"], known_types)

    behavior_stage = stages.get("function_behavior_design", {})
    behaviors = {str(item.get("function_id")): item for item in behavior_stage.get("function_behaviors", []) if isinstance(item, dict)}
    closure = stages.get("function_call_contract_closure", {})
    closure_rely = closure.get("rely_by_function", {}) if isinstance(closure.get("rely_by_function"), dict) else {}
    call_contracts: dict[str, list[dict[str, Any]]] = {}
    for item in closure.get("call_contracts", []):
        if isinstance(item, dict) and item.get("caller"):
            call_contracts.setdefault(str(item["caller"]), []).append(
                {
                    "NAME": str(item.get("NAME", item.get("callee", ""))),
                    "SIGNATURE": str(item.get("SIGNATURE", item.get("signature", ""))),
                }
            )
    test_stage = stages.get("function_test_vector_design", {})
    stage_vectors = test_stage.get("function_test_vectors", {}) if isinstance(test_stage.get("function_test_vectors"), dict) else {}

    functions: list[dict[str, Any]] = []
    function_roles: dict[str, str] = {}
    for item in raw.get("functions", []):
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or item.get("function_id") or item.get("name") or "")
        registry_entry = registry.resolve(fid, expected_kinds={"function"}) if registry else None
        if registry_entry is not None:
            fid = registry_entry["artifact_id"]
        behavior = behaviors.get(fid) or behaviors.get(str(item.get("function_id", ""))) or {}
        signature = _signature(item.get("signature"), fid)
        name = registry_entry["canonical_name"] if registry_entry is not None else str(item.get("name") or signature["NAME"] or fid)
        if registry_entry is not None and signature["NAME"] != name:
            raise ValueError(f"Registry function {fid!r} conflicts with signature name {signature['NAME']!r}")
        file_id = registry_entry["owner_file_id"] if registry_entry is not None else alias_to_file_id.get(str(item.get("file") or item.get("owner_file") or ""), next(iter(files_by_id), ""))
        file_trace = files_by_id.get(file_id, {}).get("trace_id", f"{slug}/{name}")
        raw_type = str(item.get("function_type", "")).upper()
        is_event = raw_type == "EVENT" or "EVENT" in item or "EVENT" in behavior
        function_type = "ENTRYPOINT" if raw_type in {"ENTRYPOINT", "ENTRY_POINT"} or name == "main" else "EVENT" if is_event else "ALGORITHM"
        rely = item.get("rely")
        if not isinstance(rely, dict):
            rely = item.get("RELY")
        if not isinstance(rely, dict):
            rely = closure_rely.get(fid, closure_rely.get(name, {}))
        direct_contracts = item.get("call_contracts")
        if not isinstance(direct_contracts, list):
            direct_contracts = item.get("CALL_CONTRACTS")
        if not isinstance(direct_contracts, list):
            direct_contracts = call_contracts.get(fid, call_contracts.get(name, []))
        vectors = item.get("test_vectors", item.get("TEST_VECTORS", stage_vectors.get(fid, stage_vectors.get(name, []))))
        normalized = {
            "id": registry_entry["artifact_id"] if registry_entry is not None else str(item.get("id") or f"function:{file_trace}/{name}"),
            "file": file_id,
            "trace_id": f"{file_trace}/{name}",
            "name": name,
            "function_type": function_type,
            "visibility": _normalize_visibility(registry_entry["visibility"] if registry_entry is not None else item.get("visibility")),
            "role": str(item.get("role") or f"{name} function."),
            "signature": signature,
            "rely": _normalize_rely(rely),
            "call_contracts": _normalize_call_contracts(direct_contracts),
            "wire_mapping": _normalize_wire_mapping(item.get("wire_mapping", item.get("WIRE_MAPPING", behavior.get("wire_mapping")))),
            "trace_refs": _unique([str(ref) for ref in _as_list(item.get("trace_refs", behavior.get("trace_refs", [])))]),
            "decision_refs": _as_list(item.get("decision_refs")),
            "rule_refs": _as_list(item.get("rule_refs")),
            "test_vectors": _normalize_vectors(vectors, name, "FUNCTION"),
        }
        if function_type == "EVENT":
            normalized["event"] = _normalize_event(item.get("event", item.get("EVENT", behavior.get("EVENT"))), normalized["role"])
        else:
            normalized["logic"] = _normalize_logic(item.get("logic", item.get("LOGIC", behavior.get("LOGIC"))), normalized["role"])
        functions.append(normalized)
        function_roles[name] = normalized["role"]
        if file_id in files_by_id:
            files_by_id[file_id]["functions"].append(normalized["id"])

    for function in functions:
        function["rely"]["STRUCT"] = [{"NAME": item["NAME"], "ROLE": type_roles.get(item["NAME"], item["ROLE"])} for item in function["rely"]["STRUCT"]]
        function["rely"]["FUNC"] = [{"NAME": item["NAME"], "KIND": item["KIND"], "ROLE": function_roles.get(item["NAME"], item["ROLE"])} for item in function["rely"]["FUNC"]]

        if function["visibility"] != "public" or function["file"] not in files_by_id:
            continue
        function_file = files_by_id[function["file"]]
        for type_item in types:
            owner_file = files_by_id.get(type_item["file"], {})
            owner_header = owner_file.get("header_path")
            if (
                owner_header
                and owner_header != function_file.get("header_path")
                and re.search(rf"\b{re.escape(type_item['name'])}\b", function["signature"]["RAW"])
            ):
                function_file["header_dependencies"].append(owner_header)
        function_file["header_dependencies"] = _unique(function_file["header_dependencies"])

    for function in functions:
        owner = files_by_id.get(function["file"])
        if function["function_type"] == "ENTRYPOINT" and owner and not owner.get("header_path"):
            source = Path(str(owner.get("source_path") or "main.c"))
            owner["source_path"] = str(source.with_name("main.c"))

    modules: list[dict[str, Any]] = []
    for item in raw.get("modules", []):
        if not isinstance(item, dict):
            continue
        registry_entry = registry.resolve(item.get("id") or item.get("name"), expected_kinds={"module"}) if registry else None
        module_name = registry_entry["canonical_name"] if registry_entry is not None else item.get("name")
        module_files = [file for file in files if file["module"] == module_name]
        artifacts = [
            {"NAME": type_item["name"], "KIND": "TYPE", "ROLE": type_item["role"]}
            for type_item in types
            if type_item["file"] in {file["id"] for file in module_files}
        ] + [
            {"NAME": function["name"], "KIND": "FUNC", "ROLE": function["role"]}
            for function in functions
            if function["file"] in {file["id"] for file in module_files}
        ]
        modules.append(
            {
                "id": registry_entry["artifact_id"] if registry_entry is not None else str(item.get("id") or f"module:{item.get('name')}"),
                "name": str(module_name or item.get("id")),
                "role": str(item.get("role", "")),
                "dependencies": [str(dep) for dep in _as_list(item.get("dependencies"))],
                "files": [path for file in module_files for path in (file.get("header_path"), file.get("source_path")) if path],
                "artifacts": artifacts,
                "trace_refs": [str(ref) for ref in _as_list(item.get("trace_refs"))],
            }
        )

    out = dict(raw)
    out.update(
        {
            "schema_version": raw.get("schema_version", "specforge_planning_ir_v1"),
            "protocol": protocol,
            "modules": modules,
            "files": files,
            "types": types,
            "functions": functions,
            "consistency_rules": _normalize_rules(raw.get("consistency_rules", [])),
            "forbidden_symbols": _normalize_forbidden(raw.get("forbidden_symbols", [])),
            "test_vectors": _normalize_vectors(raw.get("test_vectors", []), "protocol", "RUNTIME"),
        }
    )
    return out


def _spec_rel_dir(trace_id: str) -> Path:
    parts = trace_id.split("/")
    if len(parts) <= 2:
        return Path(parts[-1])
    return Path(*parts[1:])


def _file_spec_path(specs_root: Path, file_item: dict[str, Any]) -> Path:
    rel = _spec_rel_dir(file_item["trace_id"])
    return specs_root / rel / f"{rel.name}_spec.json"


def _function_spec_path(specs_root: Path, function: dict[str, Any]) -> Path:
    parent = function["trace_id"].rsplit("/", 1)[0]
    rel = _spec_rel_dir(parent)
    filename = f"{function['name']}_spec.json"
    if filename == f"{rel.name}_spec.json":
        filename = f"{function['name']}_function_spec.json"
    return specs_root / rel / filename


def _doc_refs(item: dict[str, Any]) -> list[str]:
    refs = item.get("trace_refs", [])
    return [str(ref) for ref in refs if str(ref).strip()]


def _type_spec(type_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "NAME": type_item["name"],
        "KIND": type_item["kind"],
        "VISIBILITY": type_item["visibility"],
        "ROLE": type_item["role"],
        "TYPE_SPEC": type_item["type_spec"],
    }


def _header_interface(function: dict[str, Any]) -> dict[str, Any]:
    return {
        "SIGNATURE": function["signature"]["RAW"],
        "NAME": function["name"],
        "KIND": "FUNC",
        "FUNCTION_TYPE": function["function_type"],
        "ROLE": function["role"],
        "VISIBILITY": function["visibility"],
    }


def _source_interface(function: dict[str, Any]) -> dict[str, Any]:
    return {
        "TRACE_ID": function["trace_id"],
        "SIGNATURE": function["signature"]["RAW"],
        "NAME": function["name"],
        "KIND": "FUNC",
        "FUNCTION_TYPE": function["function_type"],
        "ROLE": function["role"],
        "VISIBILITY": function["visibility"],
    }


def _public_symbols(file_item: dict[str, Any], type_items: list[dict[str, Any]], functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for item in type_items:
        if item["visibility"] == "PUBLIC":
            symbols.append({"NAME": item["name"], "KIND": item["kind"], "ROLE": item["role"]})
            type_spec = item.get("type_spec", {})
            if isinstance(type_spec, dict) and type_spec.get("TYPE_KIND") == "ENUM":
                for enum_item in type_spec.get("ENUM_VALUES", []):
                    if isinstance(enum_item, dict) and enum_item.get("NAME"):
                        symbols.append({"NAME": enum_item["NAME"], "KIND": "ENUM", "ROLE": enum_item.get("ROLE", "")})
    for function in functions:
        if function["visibility"] == "public":
            symbols.append({"NAME": function["name"], "KIND": "FUNC", "SIGNATURE": function["signature"]["RAW"], "ROLE": function["role"]})
    return symbols


def _access_paths(type_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []

    def visit(prefix: str, spec: dict[str, Any]) -> None:
        kind = str(spec.get("TYPE_KIND", "")).upper()
        key = "FIELDS" if kind == "STRUCT" else "VARIANTS" if kind == "UNION" else ""
        for member in spec.get(key, []) if key else []:
            if not isinstance(member, dict):
                continue
            name = str(member.get("NAME", "")).strip()
            c_type = str(member.get("TYPE", "")).strip()
            if not name or not c_type:
                continue
            path = f"{prefix}.{name}"
            paths.append({"PATH": path, "TYPE": c_type, "ROLE": str(member.get("ROLE", ""))})
            nested = member.get("TYPE_SPEC")
            if isinstance(nested, dict):
                visit(path, nested)

    for item in type_items:
        spec = item.get("type_spec", {})
        if isinstance(spec, dict):
            visit(item["name"], spec)
    return paths


def _function_spec(function: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "KIND": "FUNCTION_SPEC",
        "TRACE_ID": function["trace_id"],
        "FUNCTION_TYPE": function["function_type"],
        "ROLE": function["role"],
        "SIGNATURE": function["signature"],
        "RELY": function["rely"],
        "PUBLIC_SYMBOLS": [{"NAME": function["name"], "KIND": "FUNC", "SIGNATURE": function["signature"]["RAW"], "ROLE": function["role"]}]
        if function["visibility"] == "public"
        else [],
        "ACCESS_PATHS": [],
        "FORBIDDEN_SYMBOLS": [],
        "TEST_VECTORS": function.get("test_vectors", []),
    }
    if function.get("call_contracts"):
        data["CALL_CONTRACTS"] = function["call_contracts"]
    if function.get("wire_mapping"):
        data["WIRE_MAPPING"] = function["wire_mapping"]
    if function["function_type"] == "EVENT":
        data["EVENT"] = function["event"]
    else:
        data["LOGIC"] = function["logic"]
    return data


def _file_spec(file_item: dict[str, Any], type_items: list[dict[str, Any]], functions: list[dict[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "KIND": "FILE_SPEC",
        "FILE": {
            "TRACE_ID": file_item["trace_id"],
            "LANG": file_item["language"],
            "ROLE": file_item["role"],
            "DOC_REF": _doc_refs(file_item),
        },
        "SOURCE": {
            "PATH": file_item["source_path"],
            "DEPENDENCY": file_item["source_dependencies"],
            "DATA": [],
            "INTERFACE": [_source_interface(function) for function in functions],
        },
        "PUBLIC_SYMBOLS": _public_symbols(file_item, type_items, functions),
        "ACCESS_PATHS": _access_paths(type_items),
        "CALL_CONTRACTS": [],
        "FORBIDDEN_SYMBOLS": file_item.get("forbidden_symbols", []),
        "TEST_VECTORS": file_item.get("test_vectors", []),
    }
    if file_item.get("header_path"):
        data["HEADER"] = {
            "PATH": file_item["header_path"],
            "DEPENDENCY": file_item["header_dependencies"],
            "DATA": [_type_spec(item) for item in type_items],
            "INTERFACE": [_header_interface(function) for function in functions if function["visibility"] == "public"],
        }
    return data


def _module_spec(plan: dict[str, Any]) -> dict[str, Any]:
    protocol = plan["protocol"]
    protocol_block: dict[str, Any] = {
        "NAME": protocol["name"],
        "SPEC_VERSION": protocol["spec_version"],
        "ROLES": protocol["roles"],
        "SCOPE": protocol.get("scope", ""),
    }
    if protocol.get("default_port"):
        protocol_block["DEFAULT_PORT"] = protocol["default_port"]
    generation_order = [module["name"] for module in plan["modules"]]
    positions = {name: index for index, name in enumerate(generation_order)}
    modules = []
    for module in plan["modules"]:
        modules.append(
            {
                "NAME": module["name"],
                "ROLE": module["role"],
                "DEPENDENCIES": [
                    dependency
                    for dependency in module["dependencies"]
                    if dependency in positions and positions[dependency] < positions[module["name"]]
                ],
                "ARTIFACTS": module.get("artifacts", []),
                "FILES": module["files"],
                "DOC_REF": _doc_refs(module),
            }
        )
    return {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": protocol_block,
        "MODULES": modules,
        "GENERATION_ORDER": generation_order,
        "CONSISTENCY_RULES": plan.get("consistency_rules", []),
        "FORBIDDEN_SYMBOLS": plan.get("forbidden_symbols", []),
        "TEST_VECTORS": plan.get("test_vectors", []),
    }


def _summary(plan: dict[str, Any]) -> str:
    protocol = plan["protocol"]
    lines = [
        f"# {protocol['name']} Protocol Specs",
        "",
        "## Metadata",
        "",
        f"- Protocol: {protocol['name']}",
        f"- Spec version: {protocol['spec_version']}",
        f"- Roles: {', '.join(protocol['roles'])}",
        f"- Scope: {protocol.get('scope', '')}",
        "",
        "## Modules",
        "",
    ]
    for module in plan["modules"]:
        lines.append(f"- `{module['name']}`: {module['role']}")
    lines.extend(["", "## Generation Order", "", ", ".join(f"`{module['name']}`" for module in plan["modules"]), ""])
    lines.extend(["## Consistency Rules", ""])
    for rule in plan.get("consistency_rules", []):
        lines.append(f"- `{rule['ID']}`: {rule['RULE']}")
    lines.extend(["", "## Forbidden Symbols", ""])
    for item in plan.get("forbidden_symbols", []):
        lines.append(f"- `{item['NAME']}`: {item['REASON']}")
    lines.extend(["", "## Key Call Chains", ""])
    lines.append("- `main` -> application runtime create/run/destroy")
    lines.append("- application runtime -> codec/session/routing/transport services according to active modules")
    lines.extend(["", "## Test Vectors", ""])
    for vector in plan.get("test_vectors", []):
        lines.append(f"- `{vector['NAME']}`")
    lines.append("")
    return "\n".join(lines)


def compile_specs(plan: dict[str, Any], specs_root: str | Path, *, clean: bool = True) -> dict[str, Any]:
    normalized_plan = normalize_plan_for_compiler(plan)
    plan.clear()
    plan.update(normalized_plan)

    root = Path(specs_root)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    types_by_id = _by_id(plan["types"])
    functions_by_id = _by_id(plan["functions"])
    written: list[str] = []

    module_path = root / f"{plan['protocol']['slug']}_module_spec.json"
    write_json(module_path, _module_spec(plan))
    written.append(str(module_path))

    for file_item in plan["files"]:
        file_types = [types_by_id[type_id] for type_id in file_item["types"]]
        file_functions = [functions_by_id[function_id] for function_id in file_item["functions"]]
        path = _file_spec_path(root, file_item)
        write_json(path, _file_spec(file_item, file_types, file_functions))
        written.append(str(path))
        for function in file_functions:
            function_path = _function_spec_path(root, function)
            write_json(function_path, _function_spec(function))
            written.append(str(function_path))

    summary_path = root / "SUMMARY.md"
    summary_path.write_text(_summary(plan), encoding="utf-8")
    written.append(str(summary_path))

    return {
        "specs_root": str(root),
        "module_spec": str(module_path),
        "summary": str(summary_path),
        "written_files": written,
    }
