from __future__ import annotations

from typing import Any

from ..artifact_io import safe_slug


CODER_FUNCTION_TYPES = {"ALGORITHM", "EVENT", "ENTRYPOINT"}
EVENT_FIELDS = ("trigger", "precondition", "input", "action", "state_change", "response", "event_type")


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


def normalize_interface_visibility_for_coder(value: Any) -> str:
    return "public" if str(value or "").strip().lower() in {"public", "exported", "external"} else "private"


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


def signature_raw(signature: dict[str, Any]) -> str:
    raw = str(signature.get("raw", "")).strip()
    if raw:
        return raw
    params = signature.get("params", [])
    rendered = ", ".join(f"{item.get('type', 'void')} {item.get('name', '')}".strip() for item in params) if isinstance(params, list) and params else "void"
    return f"{signature.get('return_type', 'int')} {signature.get('name', 'unnamed')}({rendered})"


def lower_signature_for_coder(function: dict[str, Any]) -> dict[str, Any]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    return {
        "RAW": signature_raw(signature),
        "NAME": str(function.get("name") or signature.get("name") or "unnamed"),
        "RETURN": str(signature.get("return_type") or signature.get("RETURN") or "int"),
        "PARAMS": [
            {
                "TYPE": str(param.get("type", "void")),
                "NAME": str(param.get("name", "")),
                "NULLABLE": bool(param.get("nullable", False)),
                "OWNERSHIP": normalize_param_ownership_for_coder(param.get("ownership")),
            }
            for param in signature.get("params", [])
            if isinstance(param, dict)
        ],
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


def lower_public_symbols_for_coder(file_item: dict[str, Any], functions: list[dict[str, Any]]) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(symbol: dict[str, str]) -> None:
        key = (symbol["NAME"], symbol["KIND"])
        if symbol["NAME"] and key not in seen:
            seen.add(key)
            symbols.append(symbol)

    for function in functions:
        if normalize_interface_visibility_for_coder(function.get("visibility")) != "public":
            continue
        signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
        add({"NAME": str(function.get("name", "")), "KIND": "FUNC", "SIGNATURE": signature_raw(signature), "ROLE": str(function.get("purpose", ""))})
    for type_id in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
        name = str(type_id).split(":")[-1]
        add({"NAME": name, "KIND": "TYPE", "ROLE": "Exported type from planning file layout."})
    return symbols


def lower_call_contract_for_coder(edge: dict[str, Any], function_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    callee = function_index.get(str(edge.get("callee_function_id", "")))
    if not callee:
        return None
    signature = callee.get("signature", {}) if isinstance(callee.get("signature"), dict) else {}
    return {
        "NAME": str(callee.get("name", signature.get("name", ""))),
        "SIGNATURE": signature_raw(signature),
        "PARAMS": edge.get("param_bindings", []) if isinstance(edge.get("param_bindings"), list) else [],
        "RETURN": str(signature.get("return_type", "")),
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
        funcs.append({"NAME": str(callee.get("name", "")), "KIND": "CALL", "ROLE": str(edge.get("call_reason", ""))})
    return {"STRUCT": structs, "FUNC": funcs, "VAR": []}


def lower_module_artifacts_for_coder(files: list[dict[str, Any]], functions_by_file: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, kind: str, role: str) -> None:
        key = (name, kind)
        if name and key not in seen:
            seen.add(key)
            artifacts.append({"NAME": name, "KIND": kind, "ROLE": role or "Public module artifact."})

    for file_item in files:
        for function in functions_by_file.get(str(file_item.get("file_id", "")), []):
            if normalize_interface_visibility_for_coder(function.get("visibility")) == "public":
                add(str(function.get("name", "")), "FUNC", str(function.get("purpose", "")))
        for type_id in file_item.get("exports_type_ids", []) if isinstance(file_item.get("exports_type_ids"), list) else []:
            add(str(type_id).split(":")[-1], "TYPE", "Exported type from planning file layout.")
    return artifacts


def sidecar_payload(spec_blueprint: dict[str, Any], unresolved_lowering: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    traceability = {"schema_version": "planning_traceability/v1", "items": {}}
    decisions = {"schema_version": "planning_decisions/v1", "items": {}}
    refs = {"schema_version": "planning_ir_refs/v1", "items": {}, "unresolved_lowering": unresolved_lowering}
    for collection in ("modules", "files", "functions"):
        for item in spec_blueprint.get(collection, []) if isinstance(spec_blueprint.get(collection), list) else []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("trace_id") or item.get("function_id") or item.get("file_id") or item.get("module_id") or "")
            if not key:
                continue
            traceability["items"][key] = item.get("traceability", {})
            decisions["items"][key] = {
                "capability_ids": item.get("capability_ids", []),
                "state_access": item.get("state_access", []),
                "resource_access": item.get("resource_access", []),
                "calls_allowed": item.get("calls_allowed", []),
                "service_requirements": item.get("service_requirements", []),
            }
            refs["items"][key] = {
                "function_id": item.get("function_id", ""),
                "file_id": item.get("file_id", ""),
                "module_id": item.get("module_id", ""),
                "doc_ref": lower_doc_ref(item),
            }
    return traceability, decisions, refs


def default_handle_type(protocol: str, module_id: str) -> str:
    return f"{safe_slug(protocol)}_{safe_slug(module_id)}_t"
