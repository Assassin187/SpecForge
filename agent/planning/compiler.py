from __future__ import annotations

from copy import deepcopy
import json
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
    params = _split_params(match.group("params"))
    normalized_params: list[str] = []
    array_parameter_types: dict[int, str] = {}
    for index, param in enumerate(params):
        array = re.fullmatch(
            r"(?P<prefix>.+?[\s*])(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\[[^\]]*\]",
            param,
        )
        normalized_param = f"{array.group('prefix').strip()} *{array.group('name')}" if array else param
        normalized_params.append(normalized_param)
        if array:
            array_parameter_types[index] = str(_parse_param(normalized_param)["TYPE"])
    if normalized_params != params:
        text = (
            f"{match.group('return').strip()} {match.group('name')}"
            f"({', '.join(normalized_params) or 'void'})"
        )
        match = re.match(r"(?P<return>.+?)\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>.*)\)\s*$", text)
        assert match is not None
    if isinstance(raw, dict) and {"RAW", "NAME", "RETURN", "PARAMS"} <= set(raw):
        if str(raw.get("NAME")) != match.group("name"):
            raise ValueError(f"Canonical function {fallback_name!r} has inconsistent signature identity")
        normalized = {**deepcopy(raw), "RAW": text}
        for index, parameter in enumerate(normalized.get("PARAMS", [])):
            if not isinstance(parameter, dict):
                continue
            if "NULLABLE" not in parameter and "nullable" in parameter:
                parameter["NULLABLE"] = parameter.pop("nullable")
            if "OWNERSHIP" not in parameter and "ownership" in parameter:
                parameter["OWNERSHIP"] = parameter.pop("ownership")
            array_type = re.fullmatch(r"(?P<type>.+?)\s*\[[^\]]*\]", str(parameter.get("TYPE", "")).strip())
            if array_type:
                parameter["TYPE"] = f"{array_type.group('type').strip()} *"
            elif index in array_parameter_types:
                parameter["TYPE"] = array_parameter_types[index]
        if any(
            isinstance(parameter, dict) and "(*)" in str(parameter.get("TYPE", "")).replace(" ", "")
            for parameter in normalized.get("PARAMS", [])
        ):
            declarations: list[str] = []
            for parameter in normalized.get("PARAMS", []):
                if not isinstance(parameter, dict):
                    continue
                c_type = str(parameter.get("TYPE", "")).strip()
                name = str(parameter.get("NAME", "param")).strip() or "param"
                callback_declaration = re.sub(r"\(\s*\*\s*\)", f"(*{name})", c_type, count=1)
                declarations.append(
                    callback_declaration if callback_declaration != c_type else f"{c_type} {name}"
                )
            normalized["RAW"] = (
                f"{normalized['RETURN']} {normalized['NAME']}"
                f"({', '.join(declarations) or 'void'})"
            )
        return normalized
    return {
        "RAW": text,
        "NAME": match.group("name"),
        "RETURN": match.group("return").strip(),
        "PARAMS": [_parse_param(item) for item in _split_params(match.group("params"))],
    }


def _lower_signature_type(signature: dict[str, Any], type_name: str) -> None:
    pattern = rf"\b(?:struct\s+|union\s+)?{re.escape(type_name)}\b\s*(?:\*+\s*)?"
    signature["RAW"] = re.sub(pattern, "void *", str(signature.get("RAW", "")))
    signature["RETURN"] = re.sub(pattern, "void *", str(signature.get("RETURN", ""))).strip()
    for parameter in signature.get("PARAMS", []):
        if isinstance(parameter, dict):
            parameter["TYPE"] = re.sub(
                pattern, "void *", str(parameter.get("TYPE", parameter.get("type", "")))
            ).strip()


def _lower_callback_signature_type(type_spec: dict[str, Any], type_name: str) -> None:
    pattern = rf"\b(?:struct\s+|union\s+)?{re.escape(type_name)}\b\s*(?:\*+\s*)?"
    type_spec["CALLBACK_SIGNATURE"] = re.sub(
        pattern, "void *", str(type_spec.get("CALLBACK_SIGNATURE", ""))
    )


def _callback_signature(raw: Any, name: str) -> str:
    if isinstance(raw, dict):
        return_type = str(raw.get("return_type") or raw.get("RETURN") or "void")
        parameters = raw.get("parameters", raw.get("PARAMS", []))
        rendered = []
        for index, parameter in enumerate(parameters if isinstance(parameters, list) else []):
            if not isinstance(parameter, dict):
                continue
            parameter_type = str(parameter.get("c_type") or parameter.get("type") or parameter.get("TYPE") or "void*")
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


def _lowering_diagnostic(
    code: str,
    message: str,
    artifact_id: str,
    *,
    authoritative_stage: str,
    recovery_action: str,
    field_path: str,
    omitted_value: Any = None,
    source_value: Any = None,
) -> dict[str, Any]:
    diagnostic = {
        "level": "error",
        "code": code,
        "message": message,
        "path": artifact_id,
        "owner_layer": "compiler",
        "authoritative_stage": authoritative_stage,
        "recovery_action": recovery_action,
        "field_path": field_path,
    }
    if omitted_value is not None:
        diagnostic["omitted_value"] = omitted_value
    if source_value is not None:
        diagnostic["source_value"] = source_value
    return diagnostic


def _lower_unresolved_type_members(
    spec: dict[str, Any],
    known_types: set[str],
    artifact_id: str,
    diagnostics: list[dict[str, Any]],
    field_path: str = "type_spec",
) -> dict[str, Any]:
    normalized = deepcopy(spec)
    for key in ("FIELDS", "VARIANTS"):
        members = []
        for index, member in enumerate(normalized.get(key, [])):
            if not isinstance(member, dict):
                continue
            refs = set(_CUSTOM_TYPE.findall(str(member.get("TYPE", ""))))
            unresolved = sorted(refs - known_types - _STANDARD_TYPES)
            if unresolved:
                diagnostics.append(
                    _lowering_diagnostic(
                        "semantic_lowering_unresolved_type_member",
                        f"Cannot losslessly emit {artifact_id} member {member.get('NAME', index)!r}; unresolved types: {', '.join(unresolved)}",
                        artifact_id,
                        authoritative_stage="type_and_access_path_design",
                        recovery_action="regenerate_type_partition",
                        field_path=f"{field_path}.{key}[{index}]",
                        source_value=member,
                    )
                )
                # Keep the required member identity in coder-loadable candidate specs.
                # The original planned declaration remains in the blocking diagnostic;
                # plan-to-spec preservation will reject this sentinel lowering.
                member["TYPE"] = "void *"
            if isinstance(member.get("TYPE_SPEC"), dict):
                member["TYPE_SPEC"] = _lower_unresolved_type_members(
                    member["TYPE_SPEC"], known_types, artifact_id, diagnostics, f"{field_path}.{key}[{index}].TYPE_SPEC"
                )
            members.append(member)
        if key in normalized:
            normalized[key] = members
    return normalized


def _lower_type_spec(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("type_spec"), dict):
        spec = dict(item["type_spec"])
        if str(spec.get("TYPE_KIND", "")).upper() == "CALLBACK":
            name = str(item.get("name") or item.get("type_name") or item.get("symbol") or "callback_t")
            spec["CALLBACK_SIGNATURE"] = _callback_signature(
                spec.get("CALLBACK_SIGNATURE") or item.get("signature") or item.get("c_type"), name
            )
        return _normalize_array_members(spec)
    string_spec = str(item.get("type_spec", "")).upper()
    declared_kinds = {"OPAQUE", "STRUCT", "ENUM", "UNION", "CALLBACK", "ALIAS"}
    kind = string_spec if string_spec in declared_kinds else str(item.get("type_kind") or item.get("kind") or "OPAQUE").upper()
    fields = item.get("fields", [])
    if kind == "ENUM":
        values = item.get("values", item.get("enum_values", fields))
        return {
            "TYPE_KIND": "ENUM",
            "ENUM_VALUES": [
                {
                    "NAME": str(field.get("name", "")),
                    "VALUE": field.get("value"),
                    "ROLE": str(field.get("summary") or field.get("role") or f"{field.get('name', 'enum')} value."),
                    "TRACE_REFS": _unique(
                        [
                            str(ref)
                            for key in ("fact_refs", "trace_refs", "rule_refs", "decision_refs")
                            for ref in _as_list(field.get(key))
                            if str(ref).strip()
                        ]
                    ),
                }
                for field in values
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
        signature = _callback_signature(item.get("signature") or item.get("c_type"), name)
        return {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": signature}
    if kind == "ALIAS":
        return {"TYPE_KIND": "ALIAS", "ALIAS_OF": str(item.get("alias_of", "void*"))}
    if kind == "UNION":
        return _normalize_array_members(
            {
                "TYPE_KIND": "UNION",
                "VARIANTS": [
                    {
                        "NAME": str(field.get("name", "")),
                        "TYPE": str(field.get("c_type") or field.get("type") or "void*"),
                        "ROLE": str(field.get("summary") or field.get("role") or f"{field.get('name', 'variant')} value."),
                    }
                    for field in fields
                    if isinstance(field, dict) and field.get("name")
                ],
            }
        )
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
    if isinstance(value, str) and value.strip():
        return {"INPUT": "", "ACTION": value.strip(), "OUTPUT": "", "INVARIANTS_USED": []}
    raw = value if isinstance(value, dict) else {}
    if set(raw) & {"INPUT", "ACTION", "OUTPUT", "INVARIANTS_USED"}:
        allowed = {"INPUT", "ACTION", "OUTPUT", "INVARIANTS_USED", "PRECONDITION", "POSTCONDITION", "IDEMPOTENT", "THREAD_SAFETY"}
        normalized = {key: deepcopy(raw[key]) for key in allowed if key in raw}
        normalized.setdefault("INPUT", "")
        normalized.setdefault("ACTION", str(role))
        normalized.setdefault("OUTPUT", "")
        normalized.setdefault("INVARIANTS_USED", [])
        return normalized
    return {
        "INPUT": _sentence(raw.get("preconditions")) or "Inputs follow the function signature.",
        "ACTION": _sentence(raw.get("state_changes")) or str(role),
        "OUTPUT": _sentence(raw.get("response_behavior") or raw.get("postconditions")) or "Return according to the signature contract.",
        "INVARIANTS_USED": [str(item) for item in _as_list(raw.get("invariants_used"))],
        "PRECONDITION": _sentence(raw.get("preconditions")),
        "POSTCONDITION": _sentence(raw.get("postconditions")),
    }


def _normalize_event(value: Any, role: str) -> dict[str, Any]:
    if isinstance(value, str) and value.strip():
        return {
            "TRIGGER": "", "PRECONDITION": "", "INPUT": "", "ACTION": value.strip(),
            "STATE_CHANGE": "", "RESPONSE": "", "EVENT_TYPE": "event",
        }
    raw = value if isinstance(value, dict) else {}
    required = {"TRIGGER", "PRECONDITION", "INPUT", "ACTION", "STATE_CHANGE", "RESPONSE", "EVENT_TYPE"}
    if set(raw) & required:
        allowed = {
            "TRIGGER", "PRECONDITION", "INPUT", "ACTION", "STATE_CHANGE", "RESPONSE", "EVENT_TYPE",
            "INVARIANTS_USED", "POSTCONDITION", "IDEMPOTENT", "THREAD_SAFETY",
        }
        normalized = {key: deepcopy(raw[key]) for key in allowed if key in raw}
        for key in required:
            normalized.setdefault(key, "event" if key == "EVENT_TYPE" else "")
        return normalized
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
            input_value = value.get("INPUT", value.get("input"))
            if input_value is None:
                input_value = {k: v for k, v in value.items() if k.startswith("input")}
            expect_value = value.get("EXPECT", value.get("expect"))
            if expect_value is None:
                expect_value = {k: v for k, v in value.items() if k.startswith(("expected", "output"))}
            vector = {
                "NAME": str(value.get("NAME") or value.get("name") or f"{prefix}_{index}"),
                "INPUT": input_value if isinstance(input_value, dict) else {"value": input_value},
                "EXPECT": expect_value if isinstance(expect_value, dict) else {"value": expect_value},
            }
        trace_refs = value.get("TRACE_REFS", value.get("trace_refs"))
        if trace_refs is not None:
            vector["TRACE_REFS"] = [str(item) for item in _as_list(trace_refs) if str(item).strip()]
        vector.setdefault("LEVEL", level)
        vectors.append(vector)
    return vectors


def _normalize_forbidden(values: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for value in _as_list(values):
        if isinstance(value, dict) and (value.get("NAME") or value.get("name")):
            out.append(
                {
                    "NAME": str(value.get("NAME") or value.get("name")),
                    "KIND": str(value.get("KIND", value.get("kind", ""))),
                    "REASON": str(value.get("REASON", value.get("reason", "Forbidden by planning."))),
                }
            )
        elif not isinstance(value, dict) and str(value).strip():
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


def _normalize_wire_mapping(
    value: Any, artifact_id: str, diagnostics: list[dict[str, Any]]
) -> list[dict[str, str]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            diagnostics.append(
                _lowering_diagnostic(
                    "semantic_lowering_unstructured_wire_mapping",
                    f"Cannot losslessly lower unstructured wire mapping for {artifact_id}",
                    artifact_id,
                    authoritative_stage="function_behavior_design",
                    recovery_action="regenerate_behavior_partition_with_structured_wire_mapping",
                    field_path="wire_mapping",
                    omitted_value=value,
                )
            )
            return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if {"PACKET", "WIRE_FIELD", "STRATEGY"} <= set(item):
            allowed = {"PACKET", "WIRE_FIELD", "STRATEGY", "TARGET", "SOURCE", "RULE"}
            out.append({key: str(item[key]) for key in allowed if key in item})
        elif {"packet", "wire_field", "strategy"} <= set(item):
            normalized = {
                "PACKET": str(item["packet"]),
                "WIRE_FIELD": str(item["wire_field"]),
                "STRATEGY": str(item["strategy"]),
            }
            for lower, upper in (("target", "TARGET"), ("source", "SOURCE"), ("rule", "RULE")):
                if lower in item:
                    normalized[upper] = str(item[lower])
            out.append(normalized)
        else:
            diagnostics.append(
                _lowering_diagnostic(
                    "semantic_lowering_incomplete_wire_mapping",
                    f"Wire mapping {index} for {artifact_id} lacks packet/wire_field/strategy",
                    artifact_id,
                    authoritative_stage="function_behavior_design",
                    recovery_action="regenerate_behavior_partition_with_complete_wire_mapping",
                    field_path=f"wire_mapping[{index}]",
                    omitted_value=item,
                )
            )
    return out


def _normalize_access_paths(
    values: Any,
    artifact_id: str,
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for index, value in enumerate(_as_list(values)):
        if not isinstance(value, dict):
            if value is not None:
                diagnostics.append(
                    _lowering_diagnostic(
                        "semantic_lowering_unstructured_access_path",
                        f"Access path {index} for {artifact_id} is not structured",
                        artifact_id,
                        authoritative_stage="type_and_access_path_design",
                        recovery_action="regenerate_access_path_with_path_and_type",
                        field_path=f"access_paths[{index}]",
                        omitted_value=value,
                    )
                )
            continue
        path = str(value.get("PATH") or value.get("path") or "").strip()
        c_type = str(value.get("TYPE") or value.get("type") or value.get("c_type") or "").strip()
        if path and c_type:
            out.append({"PATH": path, "TYPE": c_type, "ROLE": str(value.get("ROLE") or value.get("role") or "")})
        else:
            diagnostics.append(
                _lowering_diagnostic(
                    "semantic_lowering_incomplete_access_path",
                    f"Access path {index} for {artifact_id} lacks PATH or TYPE",
                    artifact_id,
                    authoritative_stage="type_and_access_path_design",
                    recovery_action="regenerate_access_path_with_path_and_type",
                    field_path=f"access_paths[{index}]",
                    omitted_value=value,
                )
            )
    return out


def _normalize_call_contracts(values: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _as_list(values):
        if not isinstance(item, dict):
            continue
        name = str(item.get("NAME") or item.get("callee") or "")
        signature = str(item.get("SIGNATURE") or item.get("signature") or "")
        if name:
            normalized = deepcopy(item)
            for key in ("NAME", "callee", "SIGNATURE", "signature"):
                normalized.pop(key, None)
            out.append({"NAME": name, "SIGNATURE": signature, **normalized})
    return out


def normalize_plan_for_compiler(plan: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(plan.get("implementation_plan", plan))
    lowering_diagnostics = [
        deepcopy(item) for item in raw.get("lowering_diagnostics", []) if isinstance(item, dict)
    ]
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
                "decision_refs": [],
                "rule_refs": [],
                "forbidden_symbols": [],
                "test_vectors": [],
                "access_paths": [],
                "data": [],
                "_raw_ids": [],
            },
        )
        group["_raw_ids"].append(str(item.get("id", "")))
        group["role_parts"].append(str(item.get("role", "")))
        group["trace_refs"].extend(str(ref) for ref in _as_list(item.get("trace_refs")))
        group["decision_refs"].extend(str(ref) for ref in _as_list(item.get("decision_refs")))
        group["rule_refs"].extend(str(ref) for ref in _as_list(item.get("rule_refs")))
        group["forbidden_symbols"].extend(_as_list(item.get("forbidden_symbols")))
        group["test_vectors"].extend(_as_list(item.get("test_vectors")))
        group["access_paths"].extend(_as_list(item.get("access_paths")))
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
                "decision_refs": _unique(group["decision_refs"]),
                "rule_refs": _unique(group["rule_refs"]),
                "forbidden_symbols": _normalize_forbidden(group["forbidden_symbols"]),
                "test_vectors": _normalize_vectors(group["test_vectors"], group["id"], "FUNCTION"),
                "access_paths": _normalize_access_paths(group["access_paths"], group["id"], lowering_diagnostics),
                "planning_access_paths": deepcopy(group["access_paths"]),
                "data": group["data"],
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
            file_id = alias_to_file_id.get(str(item.get("file") or item.get("owner_file") or ""), "")
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
            "planning_access_paths": deepcopy(item.get("planning_access_paths", item.get("access_paths", []))),
            "planning_wire_mapping": deepcopy(item.get("planning_wire_mapping", item.get("wire_mapping"))),
        }
        types.append(normalized)
        type_roles[name] = normalized["role"]
        if file_id in files_by_id:
            files_by_id[file_id]["types"].append(normalized["id"])

    known_types = {item["name"] for item in types}
    public_types = {
        item["name"] for item in types if item["visibility"] == "PUBLIC"
    }
    for type_item in types:
        # Header rendering intentionally omits PRIVATE type declarations. A public
        # definition therefore cannot safely retain even a same-file private type.
        type_item["type_spec"] = _lower_unresolved_type_members(
            type_item["type_spec"],
            public_types if type_item["visibility"] == "PUBLIC" else known_types,
            type_item["id"],
            lowering_diagnostics,
        )

    constants: list[dict[str, Any]] = []
    for item in raw.get("constants_or_macros", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("symbol") or "").strip()
        if not name:
            continue
        owner_ref = str(item.get("file") or item.get("owner_file") or "")
        file_id = alias_to_file_id.get(owner_ref, "")
        raw_kind = str(item.get("kind") or item.get("artifact_kind") or "MACRO").upper()
        kind = raw_kind if raw_kind in {"CONST", "MACRO"} else "MACRO"
        constant = {
            "id": str(item.get("id") or f"{kind.lower()}:{name}"),
            "file": file_id,
            "name": name,
            "kind": kind,
            "visibility": _normalize_visibility(item.get("visibility", "public"), public_true=True),
            "role": str(item.get("role") or f"Planned {kind.lower()} {name}."),
            "trace_refs": _unique([str(ref) for ref in _as_list(item.get("trace_refs", item.get("fact_refs", [])))]),
            "decision_refs": [str(ref) for ref in _as_list(item.get("decision_refs"))],
            "rule_refs": [str(ref) for ref in _as_list(item.get("rule_refs"))],
        }
        if isinstance(item.get("value"), (str, int, bool)):
            constant["value"] = item["value"]
        constants.append(constant)
        if file_id in files_by_id:
            files_by_id[file_id]["data"].append(constant["id"])
        else:
            lowering_diagnostics.append(
                _lowering_diagnostic(
                    "semantic_lowering_constant_owner_missing",
                    f"Constant or macro {name} has no resolvable owner file",
                    constant["id"],
                    authoritative_stage="public_artifact_inventory",
                    recovery_action="regenerate_inventory_constant_owner",
                    field_path="constants_or_macros[].file",
                    omitted_value=owner_ref,
                )
            )

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
        file_id = registry_entry["owner_file_id"] if registry_entry is not None else alias_to_file_id.get(str(item.get("file") or item.get("owner_file") or ""), "")
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
        wire_mapping = _normalize_wire_mapping(
            item.get("wire_mapping", item.get("WIRE_MAPPING", behavior.get("wire_mapping"))),
            fid,
            lowering_diagnostics,
        )
        access_paths = _normalize_access_paths(
            item.get("access_paths", item.get("ACCESS_PATHS", [])), fid, lowering_diagnostics
        )
        known_paths = {
            str(path.get("PATH", ""))
            for path in [*access_paths, *_access_paths(types)]
            if isinstance(path, dict)
        }
        typed_output_targets = {
            str(param.get("NAME"))
            for param in signature.get("PARAMS", [])
            if isinstance(param, dict)
            and "*" in str(param.get("TYPE", ""))
            and str(param.get("ROLE", "")).lower().startswith("output")
            and param.get("NAME")
        }
        for index, mapping in enumerate(wire_mapping):
            target = str(mapping.get("TARGET", "")).strip()
            if (
                mapping.get("STRATEGY") != "store_in_field"
                or not target
                or target in known_paths
                or target in typed_output_targets
            ):
                continue
            access_paths.append(
                {"PATH": target, "TYPE": "void *", "ROLE": "Candidate wire target retained from Stage 7."}
            )
            known_paths.add(target)
            lowering_diagnostics.append(
                _lowering_diagnostic(
                    "semantic_lowering_wire_target_access_path_missing",
                    f"Wire mapping target {target!r} for {fid} has no typed public access path",
                    fid,
                    authoritative_stage="function_behavior_design",
                    recovery_action="regenerate_behavior_partition_with_typed_access_path",
                    field_path=f"wire_mapping[{index}].target",
                    source_value=mapping,
                )
            )
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
            "wire_mapping": wire_mapping,
            "wire_obligation": deepcopy(item.get("wire_obligation", {})),
            "trace_refs": _unique([str(ref) for ref in _as_list(item.get("trace_refs", behavior.get("trace_refs", [])))]),
            "decision_refs": _as_list(item.get("decision_refs")),
            "rule_refs": _as_list(item.get("rule_refs")),
            "test_vectors": _normalize_vectors(vectors, name, "FUNCTION"),
            "access_paths": access_paths,
            "planning_access_paths": deepcopy(item.get("access_paths", item.get("ACCESS_PATHS", []))),
            "forbidden_symbols": _normalize_forbidden(item.get("forbidden_symbols", item.get("FORBIDDEN_SYMBOLS", []))),
        }
        if function_type == "EVENT":
            normalized["event"] = _normalize_event(item.get("event", item.get("EVENT", behavior.get("EVENT"))), normalized["role"])
        else:
            normalized["logic"] = _normalize_logic(item.get("logic", item.get("LOGIC", behavior.get("LOGIC"))), normalized["role"])
        functions.append(normalized)
        function_roles[name] = normalized["role"]
        if file_id in files_by_id:
            files_by_id[file_id]["functions"].append(normalized["id"])

    unknown_signature_types = sorted({
        type_name
        for function in functions
        for type_name in _CUSTOM_TYPE.findall(function["signature"]["RAW"])
        if type_name not in known_types and type_name not in _STANDARD_TYPES
    })
    functions_by_name = {function["name"]: function for function in functions}
    for type_name in unknown_signature_types:
        stem = type_name[:-2]
        create = functions_by_name.get(f"{stem}_create")
        destroy = functions_by_name.get(f"{stem}_destroy")
        if create is None or destroy is None or create["file"] != destroy["file"]:
            continue
        inferred = {
            "id": f"type:lowering/{slug}/{type_name}", "file": create["file"],
            "name": type_name, "kind": "TYPE", "visibility": "PUBLIC",
            "role": f"Opaque owned handle recovered from the {stem}_create/{stem}_destroy ABI pair.",
            "type_spec": {"TYPE_KIND": "OPAQUE"},
            "trace_refs": _unique([*create["trace_refs"], *destroy["trace_refs"]]),
            "decision_refs": [], "rule_refs": [], "resource_handle": True,
            "ownership_model": "owned", "opaque_boundaries": {
                "create": create["id"], "destroy": destroy["id"],
            },
            "ownership_fields": [], "planning_access_paths": [], "planning_wire_mapping": None,
        }
        types.append(inferred)
        known_types.add(type_name)
        public_types.add(type_name)
        type_roles[type_name] = inferred["role"]
        if inferred["file"] in files_by_id:
            files_by_id[inferred["file"]]["types"].append(inferred["id"])
        lowering_diagnostics.append(
            _lowering_diagnostic(
                "semantic_lowering_missing_opaque_handle_materialized",
                f"Materialized unresolved ABI handle {type_name} from its create/destroy pair",
                inferred["id"], authoritative_stage="public_artifact_inventory",
                recovery_action="regenerate_inventory_with_owned_handle_type",
                field_path="types", source_value={"create": create["id"], "destroy": destroy["id"]},
            )
        )

    for function in functions:
        function["rely"]["STRUCT"] = [{"NAME": item["NAME"], "ROLE": type_roles.get(item["NAME"], item["ROLE"])} for item in function["rely"]["STRUCT"]]
        function["rely"]["FUNC"] = [{"NAME": item["NAME"], "KIND": item["KIND"], "ROLE": function_roles.get(item["NAME"], item["ROLE"])} for item in function["rely"]["FUNC"]]
        relied_types = {
            item["NAME"] for item in function["rely"]["STRUCT"] if isinstance(item, dict) and item.get("NAME")
        }
        function["access_paths"] = _unique(
            [
                *function.get("access_paths", []),
                *_access_paths([item for item in types if item["name"] in relied_types]),
            ]
        )

    if registry is not None:
        materialized_type_ids = {item["id"] for item in types}
        missing_abi_types = [
            item
            for item in registry.typed_view({"type", "callback"})
            if item["artifact_id"] not in materialized_type_ids
        ]
        for function in functions:
            for type_item in missing_abi_types:
                type_name = type_item["canonical_name"]
                if not re.search(rf"\b{re.escape(type_name)}\b", function["signature"]["RAW"]):
                    continue
                original = deepcopy(function["signature"])
                _lower_signature_type(function["signature"], type_name)
                lowering_diagnostics.append(
                    _lowering_diagnostic(
                        "semantic_lowering_missing_public_abi_type",
                        f"Function {function['id']} references unmaterialized type {type_name}",
                        function["id"],
                        authoritative_stage="type_and_access_path_design",
                        recovery_action="regenerate_type_partition",
                        field_path="signature",
                        source_value=original,
                    )
                )

    known_headers = {
        str(file_item["header_path"]): file_item
        for file_item in files
        if file_item.get("header_path")
    }
    abi_edges: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for function in functions:
        if function["visibility"] != "public" or function["file"] not in files_by_id:
            continue
        source_header = str(files_by_id[function["file"]].get("header_path") or "")
        for type_item in types:
            owner_header = str(files_by_id.get(type_item["file"], {}).get("header_path") or "")
            if (
                source_header and owner_header and source_header != owner_header
                and re.search(rf"\b{re.escape(type_item['name'])}\b", function["signature"]["RAW"])
            ):
                abi_edges.append((source_header, owner_header, function, type_item))

    callback_abi_edges: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for callback in types:
        callback_spec = callback.get("type_spec", {})
        if callback["visibility"] != "PUBLIC" or callback_spec.get("TYPE_KIND") != "CALLBACK":
            continue
        source_header = str(files_by_id.get(callback["file"], {}).get("header_path") or "")
        signature = str(callback_spec.get("CALLBACK_SIGNATURE", ""))
        for type_item in types:
            owner_header = str(files_by_id.get(type_item["file"], {}).get("header_path") or "")
            if (
                source_header and owner_header and source_header != owner_header
                and re.search(rf"\b{re.escape(type_item['name'])}\b", signature)
            ):
                callback_abi_edges.append((source_header, owner_header, callback, type_item))

    graph: dict[str, set[str]] = {}
    for source_header, owner_header, _, _ in [*abi_edges, *callback_abi_edges]:
        graph.setdefault(source_header, set()).add(owner_header)

    def reaches(start: str, target: str, seen: set[str] | None = None) -> bool:
        if start == target:
            return True
        visited = set() if seen is None else seen
        if start in visited:
            return False
        visited.add(start)
        return any(reaches(value, target, visited) for value in graph.get(start, set()))

    cyclic_edges = [
        edge for edge in abi_edges if reaches(edge[1], edge[0])
    ]
    for source_header, owner_header, function, type_item in cyclic_edges:
        original = deepcopy(function["signature"])
        _lower_signature_type(function["signature"], type_item["name"])
        lowering_diagnostics.append(
            _lowering_diagnostic(
                "semantic_lowering_cyclic_public_abi_type",
                f"Public ABI cycle {source_header} -> {owner_header} requires lowering {type_item['name']} in {function['id']}",
                function["id"],
                authoritative_stage="function_interface_design",
                recovery_action="regenerate_interface_partition_without_public_header_cycle",
                field_path="signature",
                source_value=original,
            )
        )

    cyclic_callback_edges = [
        edge for edge in callback_abi_edges if reaches(edge[1], edge[0])
    ]
    for source_header, owner_header, callback, type_item in cyclic_callback_edges:
        original = str(callback["type_spec"].get("CALLBACK_SIGNATURE", ""))
        _lower_callback_signature_type(callback["type_spec"], type_item["name"])
        lowering_diagnostics.append(
            _lowering_diagnostic(
                "semantic_lowering_cyclic_public_abi_type",
                f"Public ABI cycle {source_header} -> {owner_header} requires lowering {type_item['name']} in {callback['id']}",
                callback["id"],
                authoritative_stage="type_and_access_path_design",
                recovery_action="regenerate_callback_without_public_header_cycle",
                field_path="type_spec.CALLBACK_SIGNATURE",
                source_value=original,
            )
        )

    lowered_signatures = {
        function["name"]: function["signature"]["RAW"] for function in functions
    }
    for function in functions:
        for contract in function.get("call_contracts", []):
            if isinstance(contract, dict) and contract.get("NAME") in lowered_signatures:
                contract["SIGNATURE"] = lowered_signatures[contract["NAME"]]

    for file_item in files:
        project_dependencies = [
            dependency
            for dependency in file_item["header_dependencies"]
            if dependency in known_headers
        ]
        file_item["source_dependencies"] = _unique(
            [*file_item["source_dependencies"], *project_dependencies]
        )
        file_item["header_dependencies"] = [
            dependency
            for dependency in file_item["header_dependencies"]
            if dependency not in known_headers
        ]

    for function in functions:
        if function["visibility"] != "public" or function["file"] not in files_by_id:
            continue
        function_file = files_by_id[function["file"]]
        for type_item in types:
            owner_header = files_by_id.get(type_item["file"], {}).get("header_path")
            if (
                owner_header and owner_header != function_file.get("header_path")
                and re.search(rf"\b{re.escape(type_item['name'])}\b", function["signature"]["RAW"])
            ):
                function_file["header_dependencies"].append(owner_header)
        function_file["header_dependencies"] = _unique(function_file["header_dependencies"])

    for _, owner_header, callback, type_item in callback_abi_edges:
        callback_file = files_by_id.get(callback["file"])
        if (
            callback_file is not None
            and re.search(
                rf"\b{re.escape(type_item['name'])}\b",
                str(callback["type_spec"].get("CALLBACK_SIGNATURE", "")),
            )
        ):
            callback_file["header_dependencies"] = _unique(
                [*callback_file["header_dependencies"], owner_header]
            )

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
        ] + [
            {"NAME": constant["name"], "KIND": constant["kind"], "ROLE": constant["role"]}
            for constant in constants
            if constant["file"] in {file["id"] for file in module_files}
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
                "decision_refs": [str(ref) for ref in _as_list(item.get("decision_refs"))],
                "rule_refs": [str(ref) for ref in _as_list(item.get("rule_refs"))],
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
            "constants_or_macros": constants,
            "functions": functions,
            "consistency_rules": _normalize_rules(raw.get("consistency_rules", [])),
            "forbidden_symbols": _normalize_forbidden(raw.get("forbidden_symbols", [])),
            "test_vectors": _normalize_vectors(raw.get("test_vectors", []), "protocol", "RUNTIME"),
            "lowering_diagnostics": lowering_diagnostics,
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
    type_spec = deepcopy(type_item["type_spec"])
    if type_spec.get("TYPE_KIND") == "ENUM":
        type_spec["ENUM_VALUES"] = [
            {key: value[key] for key in ("NAME", "VALUE", "ROLE") if key in value}
            for value in type_spec.get("ENUM_VALUES", [])
            if isinstance(value, dict)
        ]
    return {
        "NAME": type_item["name"],
        "KIND": type_item["kind"],
        "VISIBILITY": type_item["visibility"],
        "ROLE": type_item["role"],
        "TYPE_SPEC": type_spec,
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


def _public_symbols(
    file_item: dict[str, Any],
    type_items: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    constants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
    for item in constants:
        if item["visibility"] == "PUBLIC":
            symbols.append({"NAME": item["name"], "KIND": item["kind"], "ROLE": item["role"]})
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
        "ACCESS_PATHS": function.get("access_paths", []),
        "FORBIDDEN_SYMBOLS": function.get("forbidden_symbols", []),
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


def _constant_spec(item: dict[str, Any]) -> dict[str, Any]:
    data = {
        "NAME": item["name"],
        "KIND": item["kind"],
        "VISIBILITY": item["visibility"],
        "ROLE": item["role"],
    }
    if "value" in item:
        data["VALUE"] = item["value"]
    return data


def _order_type_items(type_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {str(item.get("name", "")): item for item in type_items if item.get("name")}
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    active: set[str] = set()

    def emit(name: str) -> None:
        if name in emitted or name in active:
            return
        active.add(name)
        item = by_name[name]
        serialized = json.dumps(item.get("type_spec", {}), ensure_ascii=False, sort_keys=True)
        for dependency in by_name:
            if dependency != name and re.search(rf"\b{re.escape(dependency)}\b", serialized):
                emit(dependency)
        active.remove(name)
        emitted.add(name)
        ordered.append(item)

    for item in type_items:
        name = str(item.get("name", ""))
        if name:
            emit(name)
        elif item not in ordered:
            ordered.append(item)
    return ordered


def _file_spec(
    file_item: dict[str, Any],
    type_items: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    constants: list[dict[str, Any]],
) -> dict[str, Any]:
    type_items = _order_type_items(type_items)
    public_constants = [item for item in constants if item["visibility"] == "PUBLIC"]
    private_constants = [item for item in constants if item["visibility"] == "PRIVATE"]
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
            "DATA": [_constant_spec(item) for item in private_constants],
            "INTERFACE": [_source_interface(function) for function in functions],
        },
        "PUBLIC_SYMBOLS": _public_symbols(file_item, type_items, functions, constants),
        "ACCESS_PATHS": _unique(
            [
                *_access_paths(type_items),
                *file_item.get("access_paths", []),
                *[path for function in functions for path in function.get("access_paths", [])],
            ]
        ),
        "CALL_CONTRACTS": [],
        "FORBIDDEN_SYMBOLS": file_item.get("forbidden_symbols", []),
        "TEST_VECTORS": file_item.get("test_vectors", []),
    }
    if file_item.get("header_path"):
        data["HEADER"] = {
            "PATH": file_item["header_path"],
            "DEPENDENCY": file_item["header_dependencies"],
            "DATA": [*[_type_spec(item) for item in type_items], *[_constant_spec(item) for item in public_constants]],
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
    modules = []
    for module in plan["modules"]:
        modules.append(
            {
                "NAME": module["name"],
                "ROLE": module["role"],
                "DEPENDENCIES": list(module["dependencies"]),
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


def _semantic_mapping(plan: dict[str, Any]) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for collection, spec_kind, key_name in (
        ("modules", "PROTOCOL_MODULE_SPEC.MODULES", "name"),
        ("files", "FILE_SPEC", "trace_id"),
        ("types", "FILE_SPEC.HEADER.DATA", "name"),
        ("constants_or_macros", "FILE_SPEC.DATA", "name"),
        ("functions", "FUNCTION_SPEC", "trace_id"),
    ):
        for item in plan.get(collection, []):
            if not isinstance(item, dict):
                continue
            artifacts.append(
                {
                    "plan_id": str(item.get("id", "")),
                    "spec_kind": spec_kind,
                    "spec_key": str(item.get(key_name, "")),
                    "trace_refs": [str(ref) for ref in _as_list(item.get("trace_refs"))],
                    "decision_refs": [str(ref) for ref in _as_list(item.get("decision_refs"))],
                    "rule_refs": [str(ref) for ref in _as_list(item.get("rule_refs"))],
                    "planning_access_paths": deepcopy(item.get("planning_access_paths", [])),
                    "planning_wire_mapping": deepcopy(item.get("planning_wire_mapping")),
                    "planning_type_spec": deepcopy(item.get("type_spec")) if collection == "types" else None,
                }
            )
    return {
        "schema_version": "specforge_planning_semantic_mapping/v1",
        "field_preservation_matrix": [
            {"plan_field": "types[].values", "spec_field": "FILE_SPEC.HEADER.DATA[].TYPE_SPEC.ENUM_VALUES", "mode": "deterministic_lowering"},
            {"plan_field": "types[].c_type|signature", "spec_field": "FILE_SPEC.HEADER.DATA[].TYPE_SPEC.CALLBACK_SIGNATURE", "mode": "deterministic_lowering"},
            {"plan_field": "functions[].logic|LOGIC", "spec_field": "FUNCTION_SPEC.LOGIC", "mode": "deterministic_lowering"},
            {"plan_field": "functions[].event|EVENT", "spec_field": "FUNCTION_SPEC.EVENT", "mode": "deterministic_lowering"},
            {"plan_field": "functions[].wire_mapping|WIRE_MAPPING", "spec_field": "FUNCTION_SPEC.WIRE_MAPPING", "mode": "deterministic_or_explicit_diagnostic"},
            {"plan_field": "functions[].test_vectors", "spec_field": "FUNCTION_SPEC.TEST_VECTORS", "mode": "deterministic_lowering"},
            {"plan_field": "files[].test_vectors", "spec_field": "FILE_SPEC.TEST_VECTORS", "mode": "deterministic_lowering"},
            {"plan_field": "test_vectors", "spec_field": "PROTOCOL_MODULE_SPEC.TEST_VECTORS", "mode": "deterministic_lowering"},
            {"plan_field": "functions[].access_paths|forbidden_symbols", "spec_field": "FUNCTION_SPEC.ACCESS_PATHS|FORBIDDEN_SYMBOLS", "mode": "deterministic_lowering"},
            {"plan_field": "constants_or_macros", "spec_field": "FILE_SPEC.HEADER|SOURCE.DATA", "mode": "deterministic_lowering"},
            {"plan_field": "*.trace_refs|decision_refs|rule_refs", "spec_field": "schema fields plus this sidecar", "mode": "schema_or_sidecar"},
        ],
        "artifacts": artifacts,
        "lowering_diagnostics": plan.get("lowering_diagnostics", []),
        "explicit_lowering_diagnostic_count": len(plan.get("lowering_diagnostics", [])),
    }


def compile_specs(plan: dict[str, Any], specs_root: str | Path, *, clean: bool = True) -> dict[str, Any]:
    normalized_plan = normalize_plan_for_compiler(plan)
    plan.clear()
    plan.update(normalized_plan)

    root = Path(specs_root)
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    types_by_id = _by_id(plan["types"])
    constants_by_id = _by_id(plan.get("constants_or_macros", []))
    functions_by_id = _by_id(plan["functions"])
    written: list[str] = []

    module_path = root / f"{plan['protocol']['slug']}_module_spec.json"
    write_json(module_path, _module_spec(plan))
    written.append(str(module_path))

    for file_item in plan["files"]:
        file_types = [types_by_id[type_id] for type_id in file_item["types"]]
        file_constants = [constants_by_id[item_id] for item_id in file_item.get("data", [])]
        file_functions = [functions_by_id[function_id] for function_id in file_item["functions"]]
        path = _file_spec_path(root, file_item)
        write_json(path, _file_spec(file_item, file_types, file_functions, file_constants))
        written.append(str(path))
        for function in file_functions:
            function_path = _function_spec_path(root, function)
            write_json(function_path, _function_spec(function))
            written.append(str(function_path))

    summary_path = root / "SUMMARY.md"
    summary_path.write_text(_summary(plan), encoding="utf-8")
    written.append(str(summary_path))

    semantic_mapping_path = root / "planning_semantic_mapping.json"
    write_json(semantic_mapping_path, _semantic_mapping(plan))
    written.append(str(semantic_mapping_path))

    return {
        "specs_root": str(root),
        "module_spec": str(module_path),
        "summary": str(summary_path),
        "semantic_mapping": str(semantic_mapping_path),
        "diagnostics": plan.get("lowering_diagnostics", []),
        "written_files": written,
    }
