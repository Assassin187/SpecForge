from __future__ import annotations

import re
from typing import Any

from ..artifact_io import safe_slug
from .implementation_plan_context import SYSTEM_TYPE_IDS


CODER_FUNCTION_TYPES = {"ALGORITHM", "EVENT", "ENTRYPOINT"}
EVENT_FIELDS = ("trigger", "precondition", "input", "action", "state_change", "response", "event_type")
ARTIFACT_KINDS = {"TYPE", "FUNC", "VAR", "CONST", "MACRO"}
C_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_C_QUALIFIERS = {"const", "volatile", "restrict", "static", "extern", "signed", "unsigned"}
_SYSTEM_TYPE_KEYS = {str(item).strip().lower() for item in SYSTEM_TYPE_IDS}
_SYSTEM_TYPE_KEYS.update({"unsigned char", "unsigned int", "unsigned short", "unsigned long", "long long", "unsigned long long"})


def normalize_param_ownership_for_coder(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "borrowed": "BORROWED",
        "owned": "OWNED",
        "owned_by_caller": "OWNED_BY_CALLER",
        "owned-by-caller": "OWNED_BY_CALLER",
        "transfer": "TRANSFER",
        "transferred": "TRANSFER",
        "shared": "SHARED",
    }
    return mapping.get(text, str(value).strip() if str(value).strip() in {"BORROWED", "OWNED", "OWNED_BY_CALLER", "TRANSFER", "SHARED", "UNKNOWN"} else "UNKNOWN")


def normalize_data_visibility_for_coder(value: Any) -> str:
    return "PUBLIC" if str(value or "").strip().lower() in {"public", "exported", "external"} else "PRIVATE"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _nested_value(raw: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = raw
    for key in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            value = value.get("value")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _role_values(value: Any) -> list[str]:
    roles: list[str] = []
    if isinstance(value, str):
        roles.append(value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                roles.append(str(item.get("role") or item.get("name") or item.get("value") or ""))
            else:
                roles.append(str(item))
    elif isinstance(value, dict):
        roles.append(str(value.get("role") or value.get("name") or value.get("value") or ""))
    return [role.strip().upper() for role in roles if role.strip()]


def is_c_symbol(name: str) -> bool:
    return bool(C_SYMBOL_RE.fullmatch(name.strip()))


def canonical_c_symbol(value: Any, *, default: str = "unnamed") -> str:
    text = str(value or "").strip()
    if is_c_symbol(text):
        return text
    candidate = safe_slug(text).replace("-", "_").replace(".", "_").replace("/", "_").replace("\\", "_")
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", candidate).strip("_")
    if not candidate:
        candidate = default
    if candidate[0].isdigit():
        candidate = f"fn_{candidate}"
    return candidate if is_c_symbol(candidate) else default


def canonical_function_symbol(function: dict[str, Any]) -> str:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    raw_name = function.get("_coder_symbol") or function.get("name") or signature.get("name")
    if is_c_symbol(str(raw_name or "")):
        return str(raw_name).strip()
    function_id_tail = str(function.get("function_id") or "").rsplit(":", 1)[-1]
    return canonical_c_symbol(function_id_tail or raw_name, default="unnamed")


def is_artifact_name_for_coder(name: str) -> bool:
    text = name.strip()
    if not is_c_symbol(text):
        return False
    lowered = text.lower()
    return not (lowered.startswith(("file:", "func:", "type:")) or "/" in text or "\\" in text or text.endswith((".h", ".c", ".json")))


def normalize_type_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.removeprefix("type:")
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = text.replace("*", " ").replace("&", " ")
    text = re.sub(r"\b(?:const|volatile|restrict|static|extern)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(";")
    for prefix in ("struct ", "enum ", "union "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text.endswith("_t"):
        text = text[:-2]
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_").lower()


def is_builtin_or_system_c_type(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    cleaned = re.sub(r"\[[^\]]*\]", "", text).replace("*", " ").replace("&", " ")
    cleaned = re.sub(r"\b(?:const|volatile|restrict|static|extern)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    if cleaned in {"struct", "enum", "union"}:
        return True
    if cleaned in _SYSTEM_TYPE_KEYS:
        return True
    if cleaned.startswith(("struct ", "enum ", "union ")):
        return cleaned in _SYSTEM_TYPE_KEYS
    tokens = [token for token in cleaned.split() if token not in _C_QUALIFIERS]
    return " ".join(tokens) in _SYSTEM_TYPE_KEYS


def _strip_param_name(param: str) -> str:
    text = param.strip()
    if not text or text == "void":
        return ""
    text = text.split("=", 1)[0].strip()
    text = re.sub(r"\[[^\]]*\]\s*$", "", text).strip()
    match = re.match(r"(.+?)\(\s*\*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*\((.*)\)$", text)
    if match:
        return f"{match.group(1).strip()} (*)({match.group(2).strip()})"
    match = re.match(r"(.+?)([*\s]+)([A-Za-z_][A-Za-z0-9_]*)$", text)
    if match:
        return (match.group(1) + match.group(2)).strip()
    return text


def is_anonymous_c_function_pointer_type(value: Any) -> bool:
    return bool(re.search(r"\(\s*\*\s*\)\s*\(", str(value or "")))


def _function_pointer_parts(value: str) -> tuple[str, str] | None:
    match = re.match(r"^([^()]+?)\(\s*\*\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*\)\s*\((.*)\)$", value.strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _split_c_params(params: str) -> list[str]:
    depth = 0
    start = 0
    parts: list[str] = []
    for index, char in enumerate(params):
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(params[start:index].strip())
            start = index + 1
    parts.append(params[start:].strip())
    return [part for part in parts if part and part != "void"]


def _type_ref_record(raw_type: str) -> dict[str, Any] | None:
    raw = str(raw_type or "").strip()
    if not raw or raw == "void" or is_builtin_or_system_c_type(raw) or is_anonymous_c_function_pointer_type(raw):
        return None
    key = normalize_type_key(raw)
    if not key:
        return None
    return {
        "raw": raw,
        "name": re.sub(r"^(?:const|volatile|restrict)\s+", "", raw).replace("*", "").strip(),
        "key": key,
        "pointer": "*" in raw,
    }


def extract_c_signature_type_refs(signature_or_params: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw_type: Any) -> None:
        text = str(raw_type or "").strip()
        function_pointer = _function_pointer_parts(text)
        if function_pointer is not None:
            return_type, params = function_pointer
            add(return_type)
            for param in _split_c_params(params):
                add(_strip_param_name(param))
            return
        record = _type_ref_record(text)
        if record is None or record["key"] in seen:
            return
        seen.add(record["key"])
        refs.append(record)

    if isinstance(signature_or_params, dict):
        add(signature_or_params.get("return_type") or signature_or_params.get("RETURN"))
        for param in signature_or_params.get("params", []) if isinstance(signature_or_params.get("params"), list) else []:
            if isinstance(param, dict):
                add(param.get("type") or param.get("TYPE"))
        return refs
    if isinstance(signature_or_params, list):
        for param in signature_or_params:
            if isinstance(param, dict):
                add(param.get("type") or param.get("TYPE"))
            else:
                add(_strip_param_name(str(param)))
        return refs

    raw = str(signature_or_params or "").strip().rstrip(";")
    if "(" not in raw or ")" not in raw:
        add(raw)
        return refs
    if _function_pointer_parts(raw) is not None:
        add(raw)
        return refs
    left, rest = raw.split("(", 1)
    params = rest.rsplit(")", 1)[0]
    return_type = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*\s*$", "", left).strip()
    add(return_type)
    for param in _split_c_params(params):
        add(_strip_param_name(param))
    return refs


def canonical_type_symbol(type_item: dict[str, Any]) -> str:
    return str(type_item.get("c_symbol") or type_item.get("c_type_name") or type_item.get("name", "")).strip()


def callback_signature_for_coder(type_item: dict[str, Any], name: str) -> str:
    callback = type_item.get("callback_signature", {}) if isinstance(type_item.get("callback_signature"), dict) else {}
    params = callback.get("params", [])
    rendered_params = "void"
    if isinstance(params, list) and params:
        rendered = []
        for index, param in enumerate(params):
            if not isinstance(param, dict):
                continue
            param_name = canonical_c_symbol(param.get("name"), default=f"param_{index + 1}")
            rendered.append(f"{param.get('type') or 'void'} {param_name}".strip())
        if rendered:
            rendered_params = ", ".join(rendered)
    return f"{callback.get('return_type') or 'void'} (*{name})({rendered_params})"


def _canonical_field_type(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    mapping = {
        "string": "char*",
        "bytes": "uint8_t*",
        "buffer": "uint8_t*",
        "varint": "uint32_t",
        "boolean": "bool",
    }
    return mapping.get(lowered, text or "uint8_t")


def lower_canonical_type_to_header_data(type_item: dict[str, Any]) -> dict[str, Any] | None:
    name = canonical_type_symbol(type_item)
    if not is_c_symbol(name):
        return None
    kind = str(type_item.get("kind", "")).strip().lower()
    declaration: dict[str, Any] = {
        "NAME": name,
        "KIND": "TYPE",
        "VISIBILITY": "PUBLIC",
        "ROLE": str(type_item.get("role") or type_item.get("purpose") or f"Public canonical type {name}."),
    }
    fields = [item for item in type_item.get("fields", []) if isinstance(item, dict)] if isinstance(type_item.get("fields"), list) else []
    enum_values = [item for item in type_item.get("enum_values", []) if isinstance(item, dict)] if isinstance(type_item.get("enum_values"), list) else []
    if kind == "enum":
        declaration["TYPE_SPEC"] = {
            "TYPE_KIND": "ENUM",
            "ENUM_VALUES": [
                {
                    "NAME": str(item.get("name", "")),
                    "VALUE": str(item.get("value", "")),
                    "ROLE": str(item.get("role") or item.get("source_field_id") or "Canonical enum value."),
                }
                for item in enum_values
                if str(item.get("name", "")).strip()
            ],
        }
    elif kind == "struct" and fields:
        declaration["TYPE_SPEC"] = {
            "TYPE_KIND": "STRUCT",
            "FIELDS": [
                {
                    "NAME": str(field.get("field_name", "")),
                    "TYPE": _canonical_field_type(field.get("field_type", "")),
                    "ROLE": str(field.get("validation_notes") or field.get("source_field_id") or "Canonical field."),
                }
                for field in fields
                if str(field.get("field_name", "")).strip()
            ],
        }
    elif kind == "callback_type":
        declaration["TYPE_SPEC"] = {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": callback_signature_for_coder(type_item, name)}
    elif kind == "alias":
        declaration["TYPE_SPEC"] = {"TYPE_KIND": "ALIAS", "ALIAS_OF": str(type_item.get("alias_of") or type_item.get("base_type") or "uint8_t")}
    else:
        declaration["TYPE_SPEC"] = {"TYPE_KIND": "OPAQUE"}
    return declaration


def _exported_function_ids(file_item: dict[str, Any]) -> set[str]:
    return {
        *set(_string_list(file_item.get("exports"))),
        *set(_string_list(file_item.get("exports_function_ids"))),
    }


def is_public_interface_function(function: dict[str, Any], file_item: dict[str, Any], module_item: dict[str, Any] | None = None) -> bool:
    visibility = str(function.get("visibility", "")).strip().lower()
    storage_class = str(function.get("storage_class", "")).strip().lower()
    if visibility in {"private", "internal", "static"} or storage_class == "static":
        return False

    function_id = str(function.get("function_id", "")).strip()
    if bool(function.get("exported")):
        return True
    if visibility in {"public", "exported", "external"}:
        return True
    if str(function.get("api_surface", "")).strip().lower() == "public":
        return True
    if function_id and function_id in _exported_function_ids(file_item):
        return True
    return False


def lower_protocol_meta_for_coder(planning_source: dict[str, Any]) -> dict[str, Any]:
    protocol_metadata = planning_source.get("protocol_metadata", {}) if isinstance(planning_source.get("protocol_metadata"), dict) else {}
    protocol_facts = planning_source.get("protocol_facts", {}) if isinstance(planning_source.get("protocol_facts"), dict) else {}
    fact_meta = protocol_facts.get("protocol_meta", {}) if isinstance(protocol_facts.get("protocol_meta"), dict) else {}
    target_profile = planning_source.get("target_profile", {}) if isinstance(planning_source.get("target_profile"), dict) else {}
    target_directives = planning_source.get("target_directives_ref", {}).get("directives", {}) if isinstance(planning_source.get("target_directives_ref"), dict) else {}

    def directive_value(name: str) -> Any:
        value = target_directives.get(name) if isinstance(target_directives, dict) else None
        if isinstance(value, dict) and ("value" in value or "directive_id" in value):
            return value.get("value")
        return value

    name = _first_text(
        fact_meta.get("protocol_name"),
        protocol_metadata.get("name"),
        protocol_metadata.get("protocol_name"),
        target_profile.get("protocol_name"),
        directive_value("protocol_name"),
        planning_source.get("protocol_name"),
        "UNSPECIFIED_PROTOCOL",
    )
    version = _first_text(
        fact_meta.get("protocol_version"),
        fact_meta.get("spec_version"),
        fact_meta.get("version"),
        protocol_metadata.get("protocol_version"),
        protocol_metadata.get("spec_version"),
        protocol_metadata.get("version"),
        target_profile.get("protocol_version"),
        target_profile.get("spec_version"),
        target_profile.get("version"),
        directive_value("protocol_version"),
        directive_value("spec_version"),
        directive_value("version"),
        "unspecified",
    )
    roles: list[str] = []
    for source in (
        _role_values(target_profile.get("target_role")),
        _role_values(target_profile.get("enabled_roles")),
        _role_values(directive_value("target_role")),
        _role_values(directive_value("enabled_roles")),
        _role_values(planning_source.get("roles")),
        _role_values(protocol_metadata.get("roles")),
        _role_values(fact_meta.get("roles")),
    ):
        if source:
            roles = source
            break
    seen: set[str] = set()
    unique_roles = []
    for role in roles or ["UNSPECIFIED_ROLE"]:
        if role not in seen:
            seen.add(role)
            unique_roles.append(role)
    scope = _first_text(
        protocol_metadata.get("scope"),
        protocol_metadata.get("target_scope"),
        fact_meta.get("target_scope"),
        fact_meta.get("scope"),
        directive_value("scope"),
    )
    result = {"NAME": name, "SPEC_VERSION": version or "unspecified", "ROLES": unique_roles}
    default_port = protocol_metadata.get("default_port")
    if not isinstance(default_port, int):
        default_port = fact_meta.get("default_port")
    if isinstance(default_port, int) and 1 <= default_port <= 65535:
        result["DEFAULT_PORT"] = default_port
    if scope:
        result["SCOPE"] = scope
    return result


def _event_contract(function: dict[str, Any]) -> dict[str, Any]:
    contract = function.get("event_contract")
    return contract if isinstance(contract, dict) else {}


def _has_complete_event_contract(function: dict[str, Any]) -> bool:
    contract = _event_contract(function)
    return all(str(contract.get(field, "")).strip() for field in EVENT_FIELDS)


def normalize_function_type_for_coder(function: dict[str, Any]) -> str:
    requested = str(function.get("coder_function_type") or function.get("function_type") or "").strip().upper()
    if requested == "ENTRYPOINT":
        return "ENTRYPOINT"
    if requested == "EVENT" and _has_complete_event_contract(function):
        return "EVENT"
    return "ALGORITHM"


def _storage_prefix(function: dict[str, Any], signature: dict[str, Any]) -> str:
    storage = str(signature.get("storage_class") or function.get("storage_class") or "").strip().lower()
    raw = str(signature.get("raw") or "").lstrip().lower()
    if storage == "static" or raw.startswith("static "):
        return "static "
    return ""


def _render_signature_params(signature: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    params = signature.get("params", [])
    lowered: list[dict[str, Any]] = []
    if isinstance(params, list):
        for index, param in enumerate(params):
            if not isinstance(param, dict):
                continue
            name = canonical_c_symbol(param.get("name"), default=f"param_{index + 1}")
            lowered.append(
                {
                    "TYPE": str(param.get("type") or "void"),
                    "NAME": name,
                    "NULLABLE": bool(param.get("nullable", False)),
                    "OWNERSHIP": normalize_param_ownership_for_coder(param.get("ownership")),
                }
            )
    def render_param(item: dict[str, Any]) -> str:
        match = re.match(r"(.+?)\(\s*\*\s*\)\s*\((.*)\)$", str(item["TYPE"]).strip())
        if match:
            return f"{match.group(1).strip()} (*{item['NAME']})({match.group(2).strip()})"
        return f"{item['TYPE']} {item['NAME']}".strip()

    rendered = ", ".join(render_param(item) for item in lowered) if lowered else "void"
    return rendered, lowered


def signature_raw(signature: dict[str, Any], *, name: str | None = None, function: dict[str, Any] | None = None) -> str:
    function = function or {}
    symbol = canonical_c_symbol(name or signature.get("name"), default="unnamed")
    rendered, _ = _render_signature_params(signature)
    return f"{_storage_prefix(function, signature)}{signature.get('return_type') or signature.get('RETURN') or 'int'} {symbol}({rendered})"


def lower_signature_for_coder(function: dict[str, Any]) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    name = canonical_function_symbol(function)
    _, params = _render_signature_params(signature)
    return {
        "RAW": signature_raw(signature, name=name, function=function),
        "NAME": name,
        "RETURN": str(signature.get("return_type") or signature.get("RETURN") or "int"),
        "PARAMS": params,
    }


def _text_items(values: Any, default: str) -> list[dict[str, str]]:
    items = values if isinstance(values, list) else [values] if str(values or "").strip() else []
    result = [{"TEXT": str(item)} for item in items if str(item).strip()]
    return result or [{"TEXT": default}]


def lower_contract_for_coder(function: dict[str, Any]) -> dict[str, Any]:
    contract = function.get("behavior_contract", {}) if isinstance(function.get("behavior_contract"), dict) else {}
    error_behavior = str(function.get("error_behavior", "")).strip()
    postconditions = list(contract.get("postconditions", [])) if isinstance(contract.get("postconditions"), list) else []
    if error_behavior:
        postconditions.append(f"Error behavior: {error_behavior}")
    thread_safety = str(contract.get("thread_safety", "single_thread_only")).strip().upper()
    return {
        "PRECONDITION": _text_items(contract.get("preconditions", []), "Inputs must satisfy the function signature contract."),
        "POSTCONDITION": _text_items(postconditions, "Function preserves the documented module invariants and returns according to its signature."),
        "IDEMPOTENT": bool(contract.get("idempotent", False)),
        "THREAD_SAFETY": thread_safety or "SINGLE_THREAD_ONLY",
    }


def lower_event_or_logic_for_coder(function: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    function_type = normalize_function_type_for_coder(function)
    if function_type == "EVENT":
        contract = _event_contract(function)
        return (
            function_type,
            "EVENT",
            {
                "TRIGGER": str(contract.get("trigger", "")),
                "PRECONDITION": str(contract.get("precondition", "")),
                "INPUT": str(contract.get("input", "")),
                "ACTION": str(contract.get("action", "")),
                "STATE_CHANGE": str(contract.get("state_change", "")),
                "RESPONSE": str(contract.get("response", "")),
                "EVENT_TYPE": str(contract.get("event_type", "EVENT")),
            },
        )
    contract = function.get("behavior_contract", {}) if isinstance(function.get("behavior_contract"), dict) else {}
    return (
        function_type,
        "LOGIC",
        {
            "INPUT": str(contract.get("input", "Inputs are the C signature parameters.")),
            "ACTION": str(contract.get("action", function.get("purpose", ""))),
            "OUTPUT": str(contract.get("output", "Result is reflected by return value and documented side effects.")),
            "INVARIANTS_USED": [str(item) for item in contract.get("invariants_used", [])] if isinstance(contract.get("invariants_used"), list) else [],
        },
    )


def lower_doc_ref(item: dict[str, Any]) -> list[str]:
    traceability = item.get("traceability", {}) if isinstance(item.get("traceability"), dict) else {}
    values: list[Any] = []
    values.extend(item.get("trace_ref_keys", []) if isinstance(item.get("trace_ref_keys"), list) else [])
    values.extend(item.get("doc_ref", []) if isinstance(item.get("doc_ref"), list) else [])
    values.extend(traceability.get("source_fact_ids", []) if isinstance(traceability.get("source_fact_ids"), list) else [])
    values.extend(item.get("source_fact_ids", []) if isinstance(item.get("source_fact_ids"), list) else [])
    return sorted({str(value) for value in values if str(value).strip()})


def lower_forbidden_symbols_for_coder(values: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict):
            name = str(item.get("NAME") or item.get("name") or "").strip()
            reason = str(item.get("REASON") or item.get("reason") or "Forbidden by planning constraints.").strip()
            kind = str(item.get("KIND") or item.get("kind") or "").strip()
        else:
            name = str(item).strip()
            reason = "Forbidden by planning constraints."
            kind = ""
        if not name:
            continue
        lowered = {"NAME": name, "REASON": reason or "Forbidden by planning constraints."}
        if kind:
            lowered["KIND"] = kind
        result.append(lowered)
    return result


def lower_access_paths_for_coder(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in entries:
        path = str(item.get("path") or item.get("PATH") or "").strip()
        c_type = str(item.get("c_type") or item.get("TYPE") or "").strip()
        if not path or not c_type:
            continue
        key = (path, c_type)
        if key in seen:
            continue
        seen.add(key)
        lowered = {"PATH": path, "TYPE": c_type}
        role = str(item.get("role") or item.get("ROLE") or "").strip()
        if role:
            lowered["ROLE"] = role
        result.append(lowered)
    return result


def lower_call_contract_for_coder(edge: dict[str, Any], function_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    callee = function_index.get(str(edge.get("callee_function_id", "")))
    if not callee:
        return None
    signature = lower_signature_for_coder(callee)
    params: list[dict[str, Any]] = []
    for binding in edge.get("param_bindings", []) if isinstance(edge.get("param_bindings"), list) else []:
        if not isinstance(binding, dict):
            continue
        lowered = dict(binding)
        value_ref = str(lowered.get("value_ref", "")).strip()
        if value_ref in function_index:
            lowered["value_ref"] = canonical_function_symbol(function_index[value_ref])
        params.append(lowered)
    return {
        "NAME": signature["NAME"],
        "SIGNATURE": signature["RAW"],
        "PARAMS": params,
        "RETURN": str(signature.get("RETURN", "")),
        "FAILURE": str(edge.get("failure_behavior", "")),
    }


def lower_rely_for_coder(function: dict[str, Any], function_index: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    structs = [
        {"NAME": str(dep.get("symbol_name", "")), "ROLE": str(dep.get("reason", ""))}
        for dep in function.get("signature_dependencies", [])
        if isinstance(dep, dict) and str(dep.get("symbol_name", "")).strip()
    ]
    structs.extend(
        {"NAME": str(item.get("symbol_name", "")), "ROLE": str(item.get("role", ""))}
        for item in function.get("internal_type_refs", [])
        if isinstance(item, dict) and str(item.get("symbol_name", "")).strip()
    )
    funcs: list[dict[str, str]] = []
    for edge in function.get("call_contracts", []) if isinstance(function.get("call_contracts"), list) else []:
        if not isinstance(edge, dict):
            continue
        callee = function_index.get(str(edge.get("callee_function_id", "")))
        if not callee:
            continue
        funcs.append({"NAME": canonical_function_symbol(callee), "KIND": "CALL", "ROLE": str(edge.get("call_reason", ""))})
    return {"STRUCT": structs, "FUNC": funcs, "VAR": []}


def lower_module_artifacts_for_coder(file_specs: list[dict[str, Any]]) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, kind: str, role: str) -> None:
        kind = kind.upper()
        if kind not in ARTIFACT_KINDS or not is_artifact_name_for_coder(name):
            return
        key = (name, kind)
        if name and key not in seen:
            seen.add(key)
            artifacts.append({"NAME": name, "KIND": kind, "ROLE": role or "Public module artifact."})

    for file_spec in file_specs:
        header = file_spec.get("HEADER", {}) if isinstance(file_spec.get("HEADER"), dict) else {}
        for item in header.get("INTERFACE", []) if isinstance(header.get("INTERFACE"), list) else []:
            if isinstance(item, dict) and str(item.get("VISIBILITY", "")).lower() == "public":
                add(str(item.get("NAME", "")), "FUNC", str(item.get("ROLE", "")))
        for item in header.get("DATA", []) if isinstance(header.get("DATA"), list) else []:
            if not isinstance(item, dict) or str(item.get("VISIBILITY", "")).upper() != "PUBLIC":
                continue
            add(str(item.get("NAME", "")), str(item.get("KIND", "")), str(item.get("ROLE", "")))
    return artifacts


def lower_planned_module_artifacts_for_coder(artifacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or artifact.get("NAME") or "").strip()
        kind = str(artifact.get("kind") or artifact.get("KIND") or "").upper()
        role = str(artifact.get("role") or artifact.get("ROLE") or "").strip()
        if kind not in ARTIFACT_KINDS or not is_artifact_name_for_coder(name):
            continue
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        result.append({"NAME": name, "KIND": kind, "ROLE": role or "Planned module artifact."})
    return result


def merge_coder_artifacts(*artifact_lists: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artifacts in artifact_lists:
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            name = str(artifact.get("NAME", "")).strip()
            kind = str(artifact.get("KIND", "")).upper()
            if not name or not kind or (name, kind) in seen:
                continue
            seen.add((name, kind))
            result.append({"NAME": name, "KIND": kind, "ROLE": str(artifact.get("ROLE", "")) or "Module artifact."})
    return result


def sidecar_payload(planning_source: dict[str, Any], unresolved_lowering: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    traceability = {"schema_version": "planning_traceability/v1", "items": {}}
    decisions = {"schema_version": "planning_decisions/v1", "items": {}}
    refs = {"schema_version": "planning_ir_refs/v1", "items": {}, "unresolved_lowering": unresolved_lowering}
    for collection in ("modules", "files", "functions"):
        for item in planning_source.get(collection, []) if isinstance(planning_source.get(collection), list) else []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("trace_id") or item.get("function_id") or item.get("file_id") or item.get("module_id") or "")
            if not key:
                continue
            traceability["items"][key] = item.get("traceability", {})
            decisions["items"][key] = {
                "capability_ids": item.get("capability_ids", []),
                "visibility": item.get("visibility", ""),
                "api_surface": item.get("api_surface", ""),
                "exported": item.get("exported", False),
                "public_api_role": item.get("public_api_role", ""),
                "resolved_public_type_roles": item.get("resolved_public_type_roles", {}),
                "exports": item.get("exports", []),
                "state_access": item.get("state_access", []),
                "resource_access": item.get("resource_access", []),
                "calls_allowed": item.get("calls_allowed", []),
                "service_requirements": item.get("service_requirements", []),
            }
            refs["items"][key] = {
                "name": canonical_function_symbol(item) if item.get("function_id") else item.get("name", ""),
                "function_id": item.get("function_id", ""),
                "file_id": item.get("file_id", ""),
                "module_id": item.get("module_id", ""),
                "doc_ref": lower_doc_ref(item),
            }
    return traceability, decisions, refs


def default_handle_type(protocol: str, module_id: str) -> str:
    return f"{safe_slug(protocol)}_{safe_slug(module_id)}_t"
