from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .facts import write_json
from .models import Diagnostic


_PATCH_KINDS = ("module", "file", "type", "function")
_PROVENANCE_KINDS = {"protocol_fact", "engineering_decision", "open_assumption"}
_LIFECYCLE_DESTROY = ("destroy", "free", "deinit", "close", "cleanup", "release", "remove", "stop")
_TYPE_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*_t\b")
_C_STANDARD_TYPES = {
    "bool",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "intptr_t",
    "ptrdiff_t",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "uintptr_t",
}


def _unique(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _name_items(value: Any) -> list[str]:
    names: list[str] = []
    for item in _items(value):
        if isinstance(item, dict):
            name = item.get("NAME") or item.get("name") or item.get("callee")
        else:
            name = item
        if str(name or "").strip():
            names.append(str(name))
    return _unique(names)


def _diag(code: str, message: str, artifact_ids: list[str], **details: Any) -> dict[str, Any]:
    return {
        "level": "error",
        "code": code,
        "message": message,
        "artifact_ids": _unique([item for item in artifact_ids if item]),
        "details": details,
    }


def closure_diagnostics_as_models(diagnostics: list[dict[str, Any]]) -> list[Diagnostic]:
    return [
        Diagnostic(
            str(item.get("level", "error")),
            str(item.get("code", "implementability_error")),
            str(item.get("message", "Implementability closure failed")),
            ",".join(str(value) for value in item.get("artifact_ids", [])) or None,
        )
        for item in diagnostics
    ]


def _index(plan: dict[str, Any], kind: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in plan.get(f"{kind}s", []):
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("id", ""))
        name = str(item.get("name", ""))
        if artifact_id:
            by_id[artifact_id] = item
        if name:
            by_name.setdefault(name, []).append(item)
    return by_id, by_name


def _function_type_refs(function: dict[str, Any]) -> list[str]:
    signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
    text = " ".join(
        [
            str(signature.get("RAW", "")),
            str(signature.get("RETURN", "")),
            *[str(item.get("TYPE", "")) for item in signature.get("PARAMS", []) if isinstance(item, dict)],
        ]
    )
    refs = [item for item in _TYPE_TOKEN.findall(text) if item not in _C_STANDARD_TYPES]
    rely = function.get("rely", {}) if isinstance(function.get("rely"), dict) else {}
    refs.extend(_name_items(rely.get("STRUCT")))
    return _unique(refs)


def _function_calls(function: dict[str, Any]) -> list[str]:
    rely = function.get("rely", {}) if isinstance(function.get("rely"), dict) else {}
    calls = _name_items(rely.get("FUNC"))
    calls.extend(_name_items(function.get("call_contracts")))
    return _unique(calls)


def _type_members(type_item: dict[str, Any]) -> list[str]:
    spec = type_item.get("type_spec", {}) if isinstance(type_item.get("type_spec"), dict) else {}
    refs: list[str] = []
    for key in ("FIELDS", "VARIANTS"):
        for member in spec.get(key, []):
            if isinstance(member, dict):
                refs.extend(item for item in _TYPE_TOKEN.findall(str(member.get("TYPE", ""))) if item not in _C_STANDARD_TYPES)
    refs.extend(item for item in _TYPE_TOKEN.findall(str(spec.get("ALIAS_OF", ""))) if item not in _C_STANDARD_TYPES)
    return _unique(refs)


def _owner_header(item: dict[str, Any], files: dict[str, dict[str, Any]]) -> str | None:
    owner = files.get(str(item.get("file", "")), {})
    value = owner.get("header_path")
    return str(value) if value else None


def complete_deterministic_dependencies(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Complete only dependency edges uniquely implied by existing artifacts."""
    changes: list[dict[str, Any]] = []
    files, _ = _index(plan, "file")
    types, types_by_name = _index(plan, "type")
    functions, functions_by_name = _index(plan, "function")
    del types, functions

    planned_symbols = {
        str(item.get("name", ""))
        for kind in ("types", "functions")
        for item in plan.get(kind, [])
        if isinstance(item, dict) and item.get("name")
    }
    forbidden = plan.get("forbidden_symbols", [])
    if isinstance(forbidden, list):
        retained_forbidden: list[Any] = []
        for item in forbidden:
            name = str(item.get("NAME", "")) if isinstance(item, dict) else str(item)
            if name in planned_symbols:
                changes.append(
                    {
                        "code": "deterministic_forbidden_symbol_reconciliation",
                        "artifact_id": f"symbol:{name}",
                        "field": "forbidden_symbols",
                        "value": name,
                        "reason": "exact symbol is now an explicitly planned artifact",
                        "source_artifact_id": next(
                            str(value.get("id", ""))
                            for kind in ("types", "functions")
                            for value in plan.get(kind, [])
                            if isinstance(value, dict) and value.get("name") == name
                        ),
                    }
                )
            else:
                retained_forbidden.append(item)
        plan["forbidden_symbols"] = retained_forbidden

    for function in plan.get("functions", []):
        if not isinstance(function, dict):
            continue
        rely = function.setdefault("rely", {"STRUCT": [], "FUNC": [], "VAR": []})
        rely_functions = rely.setdefault("FUNC", [])
        contracts = function.setdefault("call_contracts", [])
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            name = str(contract.get("NAME", ""))
            callees = functions_by_name.get(name, [])
            if len(callees) != 1:
                continue
            signature = str(_signature(callees[0]).get("RAW", ""))
            if re.match(r".+?\s*[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", signature) and contract.get("SIGNATURE") != signature:
                contract["SIGNATURE"] = signature
                changes.append(
                    {
                        "code": "deterministic_call_contract_signature_completion",
                        "artifact_id": function.get("id"),
                        "field": "call_contracts.SIGNATURE",
                        "value": signature,
                        "reason": "callee name uniquely resolves to its canonical planned signature",
                        "source_artifact_id": callees[0].get("id"),
                    }
                )
        rely_names = set(_name_items(rely_functions))
        contract_names = set(_name_items(contracts))
        for name in sorted(rely_names - contract_names):
            callees = functions_by_name.get(name, [])
            if len(callees) == 1:
                contracts.append({"NAME": name, "SIGNATURE": str(_signature(callees[0]).get("RAW", ""))})
                changes.append(
                    {
                        "code": "deterministic_call_contract_completion",
                        "artifact_id": function.get("id"),
                        "field": "call_contracts",
                        "value": name,
                        "reason": "RELY.FUNC uniquely resolves to a planned callee signature",
                        "source_artifact_id": callees[0].get("id"),
                    }
                )
        for name in sorted(contract_names - rely_names):
            callees = functions_by_name.get(name, [])
            if len(callees) == 1:
                rely_functions.append({"NAME": name, "KIND": "CALL", "ROLE": str(callees[0].get("role", "planned callee"))})
                changes.append(
                    {
                        "code": "deterministic_rely_completion",
                        "artifact_id": function.get("id"),
                        "field": "rely.FUNC",
                        "value": name,
                        "reason": "CALL_CONTRACTS uniquely identifies an existing callee",
                        "source_artifact_id": callees[0].get("id"),
                    }
                )

    def add_dependency(file_item: dict[str, Any], key: str, dependency: str, reason: str, source_id: str) -> None:
        if not dependency or dependency == file_item.get("header_path"):
            return
        values = file_item.setdefault(key, [])
        if dependency not in values:
            values.append(dependency)
            changes.append(
                {
                    "code": "deterministic_dependency_completion",
                    "artifact_id": file_item.get("id"),
                    "field": key,
                    "value": dependency,
                    "reason": reason,
                    "source_artifact_id": source_id,
                }
            )

    for file_item in files.values():
        own_header = file_item.get("header_path")
        if own_header and file_item.get("source_path"):
            source_dependencies = file_item.setdefault("source_dependencies", [])
            if own_header not in source_dependencies:
                source_dependencies.insert(0, own_header)
                changes.append(
                    {
                        "code": "deterministic_dependency_completion",
                        "artifact_id": file_item.get("id"),
                        "field": "source_dependencies",
                        "value": own_header,
                        "reason": "source includes its public header",
                        "source_artifact_id": file_item.get("id"),
                    }
                )

    for function in plan.get("functions", []):
        if not isinstance(function, dict):
            continue
        caller_file = files.get(str(function.get("file", "")))
        if caller_file is None:
            continue
        for type_name in _function_type_refs(function):
            owners = types_by_name.get(type_name, [])
            if len(owners) != 1:
                continue
            owner_header = _owner_header(owners[0], files)
            if not owner_header or owners[0].get("file") == function.get("file"):
                continue
            add_dependency(caller_file, "source_dependencies", owner_header, f"{function.get('name')} references foreign type {type_name}", str(owners[0].get("id", "")))
            signature = function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}
            if function.get("visibility") == "public" and re.search(rf"\b{re.escape(type_name)}\b", str(signature.get("RAW", ""))):
                add_dependency(caller_file, "header_dependencies", owner_header, f"public signature exposes foreign type {type_name}", str(owners[0].get("id", "")))
        for callee_name in _function_calls(function):
            callees = functions_by_name.get(callee_name, [])
            if len(callees) != 1 or callees[0].get("file") == function.get("file"):
                continue
            owner_header = _owner_header(callees[0], files)
            if owner_header:
                add_dependency(caller_file, "source_dependencies", owner_header, f"{function.get('name')} calls {callee_name}", str(callees[0].get("id", "")))

    for type_item in plan.get("types", []):
        if not isinstance(type_item, dict):
            continue
        owner_file = files.get(str(type_item.get("file", "")))
        if owner_file is None:
            continue
        for type_name in _type_members(type_item):
            owners = types_by_name.get(type_name, [])
            if len(owners) == 1 and owners[0].get("file") != type_item.get("file"):
                owner_header = _owner_header(owners[0], files)
                if owner_header:
                    add_dependency(owner_file, "header_dependencies", owner_header, f"{type_item.get('name')} contains foreign type {type_name}", str(owners[0].get("id", "")))

    header_owner = {str(item.get("header_path")): item for item in files.values() if item.get("header_path")}
    modules, modules_by_name = _index(plan, "module")
    del modules
    for file_item in files.values():
        module_name = str(file_item.get("module", ""))
        module_matches = modules_by_name.get(module_name, [])
        if len(module_matches) != 1:
            continue
        module = module_matches[0]
        for dependency in _unique(_items(file_item.get("header_dependencies")) + _items(file_item.get("source_dependencies"))):
            owner = header_owner.get(str(dependency))
            dependency_module = str(owner.get("module", "")) if owner else ""
            if dependency_module and dependency_module != module_name and dependency_module not in module.setdefault("dependencies", []):
                module["dependencies"].append(dependency_module)
                changes.append(
                    {
                        "code": "deterministic_module_dependency_completion",
                        "artifact_id": module.get("id"),
                        "field": "dependencies",
                        "value": dependency_module,
                        "reason": f"file {file_item.get('id')} includes a header owned by {dependency_module}",
                        "source_artifact_id": file_item.get("id"),
                    }
                )

    ordered: list[dict[str, Any]] = []
    pending = list(plan.get("modules", []))
    while pending:
        ready = [item for item in pending if set(str(dep) for dep in _items(item.get("dependencies"))) <= {str(done.get("name", "")) for done in ordered}]
        if not ready:
            break
        for item in ready:
            ordered.append(item)
            pending.remove(item)
    if not pending and [item.get("id") for item in ordered] != [item.get("id") for item in plan.get("modules", [])]:
        plan["modules"] = ordered
        changes.append(
            {
                "code": "deterministic_generation_order_completion",
                "artifact_id": "protocol:module_order",
                "field": "modules",
                "value": [item.get("name") for item in ordered],
                "reason": "topological order derived from module dependencies",
                "source_artifact_id": "protocol:module_order",
            }
        )
    return changes


def _custom_type_in(value: str, type_names: set[str]) -> list[str]:
    return [name for name in type_names if re.search(rf"\b{re.escape(name)}\b", value)]


def _signature(function: dict[str, Any]) -> dict[str, Any]:
    return function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}


def _explicit_lifecycle_boundary(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and not any(marker in text for marker in ("not explicit", "missing", "unresolved", "unknown", "tied to"))


def _split_c_declarations(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail and tail != "void":
        parts.append(tail)
    return parts


def _normalized_c_type(value: str, *, declaration: bool = False) -> str:
    text = re.sub(r"\[[^\]]*\]", "*", value.strip())
    if declaration:
        match = re.match(r"(.+?)([A-Za-z_][A-Za-z0-9_]*)$", text)
        if match and (" " in match.group(1) or "*" in match.group(1)):
            text = match.group(1)
    return re.sub(r"\s+", "", text)


def _callback_contract(value: str) -> tuple[str, list[str]] | None:
    match = re.match(r"\s*(.*?)\s*\(\s*\*\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*\)\s*\((.*)\)\s*$", value)
    if not match:
        return None
    return (
        _normalized_c_type(match.group(1)),
        [_normalized_c_type(item, declaration=True) for item in _split_c_declarations(match.group(2))],
    )


def _callback_contract_for_param(param_type: str, types_by_name: dict[str, list[dict[str, Any]]]) -> tuple[str, list[str]] | None:
    direct = _callback_contract(param_type)
    if direct is not None:
        return direct
    owners = types_by_name.get(param_type.strip(), [])
    if len(owners) != 1:
        return None
    spec = owners[0].get("type_spec", {}) if isinstance(owners[0].get("type_spec"), dict) else {}
    if str(spec.get("TYPE_KIND", "")).upper() != "CALLBACK":
        return None
    return _callback_contract(str(spec.get("CALLBACK_SIGNATURE", "")))


def _function_matches_callback(function: dict[str, Any], callback: tuple[str, list[str]]) -> bool:
    signature = _signature(function)
    actual_return = _normalized_c_type(str(signature.get("RETURN", "")))
    actual_params = [_normalized_c_type(str(item.get("TYPE", ""))) for item in signature.get("PARAMS", []) if isinstance(item, dict)]
    return actual_return == callback[0] and actual_params == callback[1]


def analyze_implementability(plan: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    files, files_by_name = _index(plan, "file")
    types, types_by_name = _index(plan, "type")
    functions, functions_by_name = _index(plan, "function")
    modules, modules_by_name = _index(plan, "module")
    del files_by_name, modules

    for kind, by_name in (("type", types_by_name), ("function", functions_by_name), ("module", modules_by_name)):
        for name, items in by_name.items():
            if len(items) > 1:
                diagnostics.append(_diag(f"duplicate_{kind}_symbol", f"{kind} symbol {name} has multiple owners", [str(item.get("id", "")) for item in items], symbol=name))

    type_names = set(types_by_name)
    header_owner = {str(item.get("header_path")): item for item in files.values() if item.get("header_path")}
    module_positions = {str(item.get("name", "")): index for index, item in enumerate(plan.get("modules", [])) if isinstance(item, dict)}
    for file_item in files.values():
        source_path = str(file_item.get("source_path") or "")
        if source_path and not file_item.get("header_path") and not (source_path == "main.c" or source_path.endswith("/main.c")):
            diagnostics.append(
                _diag(
                    "file_header_missing",
                    f"Non-main source {source_path} has no planned header path",
                    [str(file_item.get("id", ""))],
                    source_path=source_path,
                )
            )
    for module_items in modules_by_name.values():
        if len(module_items) != 1:
            continue
        module = module_items[0]
        for dependency in _items(module.get("dependencies")):
            dependency = str(dependency)
            if dependency not in modules_by_name:
                diagnostics.append(_diag("unresolved_module_dependency", f"Module {module.get('name')} depends on unknown module {dependency}", [str(module.get("id", ""))], dependency=dependency))
            elif module_positions.get(dependency, -1) > module_positions.get(str(module.get("name", "")), -1):
                diagnostics.append(_diag("module_generation_order_conflict", f"Module {module.get('name')} appears before dependency {dependency}", [str(module.get("id", "")), str(modules_by_name[dependency][0].get("id", ""))], dependency=dependency))
    for function in functions.values():
        owner_file = files.get(str(function.get("file", "")))
        if owner_file is None:
            diagnostics.append(_diag("function_owner_missing", f"Function {function.get('name')} has no resolvable owner file", [str(function.get("id", ""))], owner_file=function.get("file")))
            continue
        for type_name in _function_type_refs(function):
            owners = types_by_name.get(type_name, [])
            if not owners:
                diagnostics.append(_diag("unresolved_type", f"Function {function.get('name')} references unresolved type {type_name}", [str(function.get("id", ""))], symbol=type_name))
                continue
            if len(owners) != 1:
                continue
            type_item = owners[0]
            if type_item.get("file") != function.get("file"):
                if str(type_item.get("visibility", "")).upper() != "PUBLIC":
                    diagnostics.append(_diag("cross_file_private_type", f"Function {function.get('name')} references private type {type_name} owned by another file", [str(function.get("id", "")), str(type_item.get("id", ""))], symbol=type_name))
                owner_header = _owner_header(type_item, files)
                required_field = "header_dependencies" if function.get("visibility") == "public" and type_name in str(_signature(function).get("RAW", "")) else "source_dependencies"
                if owner_header and owner_header not in owner_file.get(required_field, []):
                    diagnostics.append(_diag("foreign_type_dependency_missing", f"{owner_file.get('id')} lacks {owner_header} required by type {type_name}", [str(owner_file.get("id", "")), str(function.get("id", "")), str(type_item.get("id", ""))], dependency=owner_header, field=required_field))
        for callee_name in _function_calls(function):
            callees = functions_by_name.get(callee_name, [])
            if not callees:
                diagnostics.append(_diag("unresolved_callee", f"Function {function.get('name')} calls unresolved function {callee_name}", [str(function.get("id", ""))], symbol=callee_name))
                continue
            if len(callees) != 1:
                continue
            callee = callees[0]
            if callee.get("file") != function.get("file"):
                if callee.get("visibility") != "public":
                    diagnostics.append(_diag("cross_file_private_function", f"Function {function.get('name')} calls private function {callee_name} in another file", [str(function.get("id", "")), str(callee.get("id", ""))], symbol=callee_name))
                owner_header = _owner_header(callee, files)
                if owner_header and owner_header not in owner_file.get("source_dependencies", []):
                    diagnostics.append(_diag("callee_dependency_missing", f"{owner_file.get('id')} lacks {owner_header} required by callee {callee_name}", [str(owner_file.get("id", "")), str(function.get("id", "")), str(callee.get("id", ""))], dependency=owner_header))
        contracts = function.get("call_contracts", [])
        for contract in contracts if isinstance(contracts, list) else []:
            if not isinstance(contract, dict):
                continue
            callees = functions_by_name.get(str(contract.get("NAME", "")), [])
            if len(callees) > 1:
                diagnostics.append(
                    _diag(
                        "call_contract_callee_ambiguous",
                        f"Call contract for {contract.get('NAME')} does not resolve to a unique canonical function",
                        [str(function.get("id", "")), *[str(item.get("id", "")) for item in callees]],
                    )
                )
            elif len(callees) == 1:
                planned_raw = str(_signature(callees[0]).get("RAW", ""))
                declared_raw = str(contract.get("SIGNATURE", ""))
                planned = re.sub(r"\s+", "", planned_raw)
                declared = re.sub(r"\s+", "", declared_raw)
                if not re.match(r".+?\s*[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", planned_raw):
                    diagnostics.append(_diag("canonical_signature_missing", f"Canonical function {contract.get('NAME')} lacks a complete signature", [str(callees[0].get("id", ""))]))
                elif not declared:
                    diagnostics.append(_diag("call_contract_signature_missing", f"Call contract for {contract.get('NAME')} has no canonical signature", [str(function.get("id", "")), str(callees[0].get("id", ""))]))
                elif planned != declared:
                    diagnostics.append(_diag("call_contract_signature_mismatch", f"Call contract for {contract.get('NAME')} does not match its planned signature", [str(function.get("id", "")), str(callees[0].get("id", ""))], declared=contract.get("SIGNATURE"), planned=planned_raw))
        rely = function.get("rely", {}) if isinstance(function.get("rely"), dict) else {}
        rely_names = set(_name_items(rely.get("FUNC")))
        contract_names = set(_name_items(function.get("call_contracts")))
        if rely_names != contract_names:
            diagnostics.append(
                _diag(
                    "call_contract_rely_drift",
                    f"Function {function.get('name')} has mismatched RELY.FUNC and CALL_CONTRACTS names",
                    [str(function.get("id", ""))],
                    rely_only=sorted(rely_names - contract_names),
                    contracts_only=sorted(contract_names - rely_names),
                )
            )

    for type_item in types.values():
        owner_file = files.get(str(type_item.get("file", "")))
        if owner_file is None:
            diagnostics.append(_diag("type_owner_missing", f"Type {type_item.get('name')} has no resolvable owner file", [str(type_item.get("id", ""))], owner_file=type_item.get("file")))
            continue
        for type_name in _type_members(type_item):
            owners = types_by_name.get(type_name, [])
            if not owners:
                diagnostics.append(_diag("unresolved_type", f"Type {type_item.get('name')} contains unresolved type {type_name}", [str(type_item.get("id", ""))], symbol=type_name))
            elif len(owners) == 1 and owners[0].get("file") != type_item.get("file"):
                if str(owners[0].get("visibility", "")).upper() != "PUBLIC":
                    diagnostics.append(_diag("cross_file_private_type", f"Type {type_item.get('name')} contains private foreign type {type_name}", [str(type_item.get("id", "")), str(owners[0].get("id", ""))], symbol=type_name))

    for caller in functions.values():
        caller_calls = set(_function_calls(caller))
        behavior_text = json.dumps({"logic": caller.get("logic"), "event": caller.get("event")}, ensure_ascii=False)
        candidates = [
            function
            for function in functions.values()
            if function.get("id") != caller.get("id")
            and (str(function.get("name", "")) in caller_calls or str(function.get("name", "")) in behavior_text)
        ]
        for callee_name in _function_calls(caller):
            callees = functions_by_name.get(callee_name, [])
            if len(callees) != 1:
                continue
            callback_params: list[tuple[dict[str, Any], tuple[str, list[str]]]] = []
            for param in _signature(callees[0]).get("PARAMS", []):
                if not isinstance(param, dict):
                    continue
                contract = _callback_contract_for_param(str(param.get("TYPE", "")), types_by_name)
                if contract is not None:
                    callback_params.append((param, contract))
            if not callback_params:
                continue
            callback_candidates = [candidate for candidate in candidates if candidate.get("id") != callees[0].get("id")]
            missing: list[str] = []
            compatible_missing: dict[str, list[dict[str, Any]]] = {}
            mismatched: list[str] = []
            expected_contracts: dict[str, dict[str, Any]] = {}
            callback_type_ids: list[str] = []
            for param, contract in callback_params:
                param_name = str(param.get("NAME", "callback"))
                expected_contracts[param_name] = {"RETURN": contract[0], "PARAMS": contract[1]}
                callback_type_ids.extend(
                    str(item.get("id", "")) for item in types_by_name.get(str(param.get("TYPE", "")).strip(), [])
                )
                if any(_function_matches_callback(candidate, contract) for candidate in callback_candidates):
                    continue
                compatible = [
                    candidate
                    for candidate in functions.values()
                    if candidate.get("id") not in {caller.get("id"), callees[0].get("id")}
                    and _function_matches_callback(candidate, contract)
                ]
                mentioned = [candidate for candidate in callback_candidates if str(candidate.get("name", "")) in behavior_text]
                if mentioned:
                    mismatched.extend(str(candidate.get("name", "")) for candidate in mentioned)
                else:
                    missing.append(param_name)
                    compatible_missing[param_name] = compatible
            if missing:
                compatible_functions = _unique([function for values in compatible_missing.values() for function in values])
                diagnostics.append(
                    _diag(
                        "callback_provider_missing",
                        f"Function {caller.get('name')} calls {callee_name} without explicit compatible providers for callback parameters {', '.join(missing)}",
                        [str(caller.get("id", "")), str(callees[0].get("id", "")), *callback_type_ids, *[str(item.get("id", "")) for item in compatible_functions]],
                        callback_params=missing,
                        compatible_candidates={key: [item.get("name") for item in values] for key, values in compatible_missing.items()},
                        expected_contracts={key: expected_contracts[key] for key in missing},
                    )
                )
            if mismatched:
                diagnostics.append(
                    _diag(
                        "callback_signature_mismatch",
                        f"Function {caller.get('name')} binds incompatible callback function(s) {', '.join(_unique(mismatched))} when calling {callee_name}",
                        [str(caller.get("id", "")), str(callees[0].get("id", "")), *callback_type_ids, *[str(functions_by_name[name][0].get("id", "")) for name in _unique(mismatched) if len(functions_by_name.get(name, [])) == 1]],
                        callbacks=_unique(mismatched),
                        expected_contracts=expected_contracts,
                        actual_signatures={name: _signature(functions_by_name[name][0]) for name in _unique(mismatched) if len(functions_by_name.get(name, [])) == 1},
                    )
                )

    for file_item in files.values():
        module_matches = modules_by_name.get(str(file_item.get("module", "")), [])
        if len(module_matches) != 1:
            diagnostics.append(_diag("file_module_missing", f"File {file_item.get('id')} has no resolvable module", [str(file_item.get("id", ""))], module=file_item.get("module")))
            continue
        module = module_matches[0]
        for dependency in _unique(_items(file_item.get("header_dependencies")) + _items(file_item.get("source_dependencies"))):
            owner = header_owner.get(str(dependency))
            if owner is None:
                diagnostics.append(_diag("unresolved_header_dependency", f"File {file_item.get('id')} depends on unknown header {dependency}", [str(file_item.get("id", ""))], dependency=dependency))
            elif owner.get("module") != file_item.get("module") and owner.get("module") not in module.get("dependencies", []):
                diagnostics.append(_diag("module_dependency_conflict", f"Module {module.get('name')} omits dependency {owner.get('module')} required by file {file_item.get('id')}", [str(module.get("id", "")), str(file_item.get("id", ""))], dependency=owner.get("module")))

    cross_file_type_users: dict[str, list[dict[str, Any]]] = {name: [] for name in type_names}
    for function in functions.values():
        for type_name in _function_type_refs(function):
            owner = types_by_name.get(type_name, [{}])[0]
            if owner and owner.get("file") != function.get("file"):
                cross_file_type_users.setdefault(type_name, []).append(function)
        for callee_name in _function_calls(function):
            callees = functions_by_name.get(callee_name, [])
            if len(callees) != 1:
                continue
            signature = _signature(callees[0])
            referenced = _custom_type_in(
                " ".join(
                    [
                        str(signature.get("RETURN", "")),
                        *[str(item.get("TYPE", "")) for item in signature.get("PARAMS", []) if isinstance(item, dict)],
                    ]
                ),
                type_names,
            )
            for type_name in referenced:
                owner = types_by_name.get(type_name, [{}])[0]
                if owner and owner.get("file") != function.get("file"):
                    cross_file_type_users.setdefault(type_name, []).append(function)
    for type_name, users in cross_file_type_users.items():
        owners = types_by_name.get(type_name, [])
        if len(owners) != 1 or not users:
            continue
        type_item = owners[0]
        spec = type_item.get("type_spec", {}) if isinstance(type_item.get("type_spec"), dict) else {}
        if str(spec.get("TYPE_KIND", "")).upper() != "OPAQUE":
            continue
        owner_functions = [function for function in functions.values() if function.get("file") == type_item.get("file")]
        boundaries = type_item.get("opaque_boundaries", {}) if isinstance(type_item.get("opaque_boundaries"), dict) else {}
        constructors = [
            function
            for function in owner_functions
            if type_name in str(_signature(function).get("RETURN", ""))
            and "*" in str(_signature(function).get("RETURN", ""))
        ]
        destructors = [
            function
            for function in owner_functions
            if type_name in _function_type_refs(function)
            and any(word in str(function.get("name", "")).lower() for word in _LIFECYCLE_DESTROY)
        ]
        owner_managed_cleanup = "destroy" in str(type_item.get("ownership_model", "")).lower() and any(
            any(word in str(function.get("name", "")).lower() for word in _LIFECYCLE_DESTROY)
            for function in owner_functions
        )
        if not constructors and not _explicit_lifecycle_boundary(boundaries.get("create")):
            diagnostics.append(_diag("opaque_constructor_missing", f"Opaque type {type_name} is used across files but has no function that returns a constructible instance", [str(type_item.get("id", "")), *[str(item.get("id", "")) for item in users]], symbol=type_name))
        if not destructors and not owner_managed_cleanup and not _explicit_lifecycle_boundary(boundaries.get("destroy")):
            diagnostics.append(_diag("opaque_destructor_missing", f"Opaque type {type_name} is used across files but has no destroy/cleanup service", [str(type_item.get("id", "")), *[str(item.get("id", "")) for item in users]], symbol=type_name))

    for type_name, owners in types_by_name.items():
        if len(owners) != 1:
            continue
        type_item = owners[0]
        spec = type_item.get("type_spec", {}) if isinstance(type_item.get("type_spec"), dict) else {}
        if str(spec.get("TYPE_KIND", "")).upper() == "OPAQUE" or not re.search(r"\bowns?\b|\bowner\b", str(type_item.get("role", "")).lower()):
            continue
        users = [function for function in functions.values() if type_name in _function_type_refs(function)]
        cleanup = [function for function in users if any(word in str(function.get("name", "")).lower() for word in _LIFECYCLE_DESTROY)]
        if users and not cleanup:
            diagnostics.append(_diag("owned_resource_cleanup_missing", f"Owned resource type {type_name} has no explicit cleanup function", [str(type_item.get("id", "")), *[str(item.get("id", "")) for item in users]], symbol=type_name))

    opaque_type_names = {
        name
        for name, owners in types_by_name.items()
        if len(owners) == 1
        and isinstance(owners[0].get("type_spec"), dict)
        and str(owners[0]["type_spec"].get("TYPE_KIND", "")).upper() == "OPAQUE"
    }
    missing_access: dict[tuple[str, str], dict[str, Any]] = {}
    for caller in functions.values():
        calls = [functions_by_name[name][0] for name in _function_calls(caller) if len(functions_by_name.get(name, [])) == 1]
        available = set(_custom_type_in(" ".join(str(item.get("TYPE", "")) for item in _signature(caller).get("PARAMS", []) if isinstance(item, dict)), opaque_type_names))
        for callee in calls:
            available.update(_custom_type_in(str(_signature(callee).get("RETURN", "")), opaque_type_names))
        for callee in calls:
            if callee.get("file") == caller.get("file"):
                continue
            required_text = " ".join(
                str(item.get("TYPE", ""))
                for item in _signature(callee).get("PARAMS", [])
                if isinstance(item, dict) and "(*" not in str(item.get("TYPE", ""))
            )
            for required_type in _custom_type_in(required_text, opaque_type_names):
                if required_type not in available:
                    key = (str(caller.get("id", "")), required_type)
                    entry = missing_access.setdefault(
                        key,
                        {
                            "caller": caller,
                            "type": types_by_name[required_type][0],
                            "callees": [],
                        },
                    )
                    entry["callees"].append(callee)
    for entry in missing_access.values():
        caller = entry["caller"]
        callees = _unique(entry["callees"])
        required_type = str(entry["type"].get("name", ""))
        diagnostics.append(
            _diag(
                "access_service_missing",
                f"Function {caller.get('name')} has no explicit provider/access path for {required_type} required by {', '.join(str(item.get('name', '')) for item in callees)}",
                [str(caller.get("id", "")), str(entry["type"].get("id", "")), *[str(item.get("id", "")) for item in callees]],
                required_type=required_type,
                callees=[item.get("name") for item in callees],
            )
        )

    mains = [function for function in functions.values() if function.get("name") == "main"]
    if not mains:
        diagnostics.append(_diag("runtime_entrypoint_missing", "Executable protocol target has no main function", [str(item.get("id", "")) for item in plan.get("modules", [])], required_symbol="main"))
    elif len(mains) > 1:
        diagnostics.append(_diag("runtime_entrypoint_ambiguous", "Executable protocol target has more than one main function", [str(item.get("id", "")) for item in mains], required_symbol="main"))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in diagnostics:
        marker = json.dumps([item["code"], item["artifact_ids"], item.get("details", {})], sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            deduped.append(item)
    return deduped


def build_semantic_patch_slice(plan: dict[str, Any], context: dict[str, Any], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    related_ids = {str(value) for item in diagnostics for value in item.get("artifact_ids", [])}
    selected: dict[str, list[dict[str, Any]]] = {}
    for kind in _PATCH_KINDS:
        values = [item for item in plan.get(f"{kind}s", []) if isinstance(item, dict) and str(item.get("id", "")) in related_ids]
        selected[f"{kind}s"] = values
    related_files = {str(item.get("file", "")) for kind in ("types", "functions") for item in selected[kind]}
    selected["files"] = _unique(selected["files"] + [item for item in plan.get("files", []) if isinstance(item, dict) and str(item.get("id", "")) in related_files])
    related_files.update(str(item.get("id", "")) for item in selected["files"])
    selected["types"] = _unique(
        selected["types"] + [item for item in plan.get("types", []) if isinstance(item, dict) and str(item.get("file", "")) in related_files]
    )
    selected["functions"] = _unique(
        selected["functions"] + [item for item in plan.get("functions", []) if isinstance(item, dict) and str(item.get("file", "")) in related_files]
    )
    related_modules = {str(item.get("module", "")) for item in selected["files"]}
    selected["modules"] = _unique(selected["modules"] + [item for item in plan.get("modules", []) if isinstance(item, dict) and str(item.get("name", "")) in related_modules])
    if any(item.get("code", "").startswith("runtime_entrypoint") for item in diagnostics):
        selected["modules"] = list(plan.get("modules", []))

    trace_refs: set[str] = set()
    rule_refs: set[str] = set()
    for values in selected.values():
        for item in values:
            trace_refs.update(str(value) for value in _items(item.get("trace_refs")))
            rule_refs.update(str(value) for value in _items(item.get("rule_refs")))
    rule_refs.update(value for value in trace_refs if value.startswith("RULE_"))
    stages = {
        str(item.get("stage_id", "")): item.get("artifact", {})
        for item in plan.get("structured_planning_stages", [])
        if isinstance(item, dict)
    }
    fact_inventory = stages.get("scope_fact_inventory", {}).get("fact_inventory", [])
    normalized_fact_refs = {value.removeprefix("fact:") for value in trace_refs if value.startswith("fact:")}
    relevant_facts = [
        item
        for item in fact_inventory
        if isinstance(item, dict)
        and any(
            str(item.get("fact_ref", "")) == ref
            or str(item.get("fact_ref", "")).startswith(f"{ref}.")
            or ref.startswith(f"{item.get('fact_ref')}.")
            for ref in normalized_fact_refs
        )
    ]
    rules = [item for item in context.get("engineering_rules", []) if not rule_refs or str(item.get("rule_id", "")) in rule_refs]
    decisions = [
        item
        for item in plan.get("engineering_decisions", [])
        if not related_ids or related_ids.intersection(str(value) for value in _items(item.get("affected_artifacts")))
    ]
    compact_fields = {
        "modules": ("id", "name", "role", "dependencies", "trace_refs"),
        "files": ("id", "module", "trace_id", "role", "header_path", "source_path", "header_dependencies", "source_dependencies", "trace_refs"),
        "types": ("id", "file", "name", "visibility", "role", "type_spec", "resource_handle", "ownership_model", "opaque_boundaries", "ownership_fields", "trace_refs"),
        "functions": ("id", "file", "name", "function_type", "visibility", "role", "signature", "rely", "call_contracts", "trace_refs"),
    }
    compact_selected = {
        kind: [{key: item[key] for key in compact_fields[kind] if key in item} for item in values]
        for kind, values in selected.items()
    }
    return {
        "protocol": plan.get("protocol", {}),
        "closure_diagnostics": diagnostics,
        "related_artifacts": compact_selected,
        "relevant_protocol_facts": relevant_facts,
        "activated_generic_engineering_constraints": rules,
        "relevant_architecture": plan.get("architecture", {}),
        "relevant_engineering_decisions": decisions,
        "open_assumptions": context.get("open_assumptions", []),
    }


def validate_semantic_patch(plan: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    operations = patch.get("operations")
    if not isinstance(operations, list) or not operations:
        return ["operations must be a non-empty array"]
    indexes = {kind: _index(plan, kind)[0] for kind in _PATCH_KINDS}
    allowed_changes = {
        "module": {"role", "dependencies", "trace_refs"},
        "file": {"module", "role", "header_path", "source_path", "header_dependencies", "source_dependencies", "trace_refs", "forbidden_symbols", "test_vectors"},
        "type": {
            "file",
            "visibility",
            "role",
            "type_spec",
            "resource_handle",
            "ownership_model",
            "opaque_boundaries",
            "ownership_fields",
            "trace_refs",
            "decision_refs",
            "rule_refs",
        },
        "function": {"file", "name", "function_type", "visibility", "role", "signature", "rely", "call_contracts", "logic", "event", "wire_mapping", "trace_refs", "decision_refs", "rule_refs", "test_vectors"},
    }
    required_add = {
        "module": {"id", "name", "role", "dependencies"},
        "file": {"id", "module", "trace_id", "role", "header_path", "source_path"},
        "type": {"id", "file", "name", "visibility", "role", "type_spec"},
        "function": {"id", "file", "name", "function_type", "visibility", "role", "signature", "rely"},
    }
    seen_adds: set[tuple[str, str]] = set()
    for index, operation in enumerate(operations):
        prefix = f"operations[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{prefix} must be an object")
            continue
        op = operation.get("op")
        kind = operation.get("artifact_kind")
        artifact_id = str(operation.get("artifact_id", ""))
        if op not in {"add", "update"}:
            errors.append(f"{prefix}.op must be add or update; delete is forbidden")
        if kind not in _PATCH_KINDS:
            errors.append(f"{prefix}.artifact_kind is invalid")
            continue
        if not artifact_id:
            errors.append(f"{prefix}.artifact_id is required")
        if not str(operation.get("reason", "")).strip():
            errors.append(f"{prefix}.reason is required")
        provenance = operation.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") not in _PROVENANCE_KINDS:
            errors.append(f"{prefix}.provenance.kind must distinguish protocol_fact, engineering_decision, or open_assumption")
        if not isinstance(operation.get("affected_artifact_ids"), list):
            errors.append(f"{prefix}.affected_artifact_ids must be an array")
        if op == "update":
            if artifact_id not in indexes[kind] and (kind, artifact_id) not in seen_adds:
                errors.append(f"{prefix} cannot update unknown stable ID {artifact_id}")
            changes = operation.get("changes")
            if not isinstance(changes, dict) or not changes:
                errors.append(f"{prefix}.changes must be a non-empty object")
            elif set(changes) - allowed_changes[kind]:
                errors.append(f"{prefix}.changes contains forbidden fields {sorted(set(changes) - allowed_changes[kind])}")
            if kind == "function" and isinstance(changes, dict) and "signature" in changes:
                raw = changes["signature"].get("RAW", "") if isinstance(changes["signature"], dict) else changes["signature"]
                if not re.match(r".+?\s*[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", str(raw)):
                    errors.append(f"{prefix}.changes.signature must be a complete C declaration")
        if op == "add":
            artifact = operation.get("artifact")
            if artifact_id in indexes[kind] or (kind, artifact_id) in seen_adds:
                errors.append(f"{prefix} duplicates stable ID {artifact_id}")
            if not isinstance(artifact, dict):
                errors.append(f"{prefix}.artifact must be an object")
            else:
                if artifact.get("id") != artifact_id:
                    errors.append(f"{prefix}.artifact.id must equal artifact_id")
                missing = required_add[kind] - set(artifact)
                if missing:
                    errors.append(f"{prefix}.artifact misses {sorted(missing)}")
                if set(artifact) - ({"id", "name", "trace_id", "types", "functions"} | allowed_changes[kind]):
                    errors.append(f"{prefix}.artifact contains forbidden fields {sorted(set(artifact) - ({'id', 'name', 'trace_id', 'types', 'functions'} | allowed_changes[kind]))}")
                if kind == "function":
                    raw = artifact.get("signature", {}).get("RAW", "") if isinstance(artifact.get("signature"), dict) else artifact.get("signature", "")
                    if not re.match(r".+?\s*[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", str(raw)):
                        errors.append(f"{prefix}.artifact.signature must be a complete C declaration")
            seen_adds.add((kind, artifact_id))
    return errors


def retain_valid_semantic_operations(plan: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep structurally valid primary operations so a correction can be a small delta."""
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, operation in enumerate(patch.get("operations", [])):
        candidate = {"patch_id": patch.get("patch_id", "primary"), "operations": [*valid, operation]}
        errors = validate_semantic_patch(plan, candidate)
        if errors:
            rejected.append({"operation_index": index, "errors": errors})
        else:
            valid.append(deepcopy(operation))
    return {"patch_id": str(patch.get("patch_id", "primary")), "operations": valid}, rejected


def merge_semantic_patches(primary: dict[str, Any], correction: dict[str, Any]) -> dict[str, Any]:
    """Apply the correction as ordered stable-ID operations after the retained primary operations."""
    return {
        "patch_id": f"{primary.get('patch_id', 'primary')}+{correction.get('patch_id', 'correction')}",
        "operations": [
            *deepcopy(primary.get("operations", [])),
            *deepcopy(correction.get("operations", [])),
        ],
    }


def apply_semantic_patch(plan: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(plan)
    for operation in patch.get("operations", []):
        kind = str(operation["artifact_kind"])
        collection = out.setdefault(f"{kind}s", [])
        artifact_id = str(operation["artifact_id"])
        if operation["op"] == "add":
            artifact = deepcopy(operation["artifact"])
            artifact.setdefault("trace_refs", [])
            if kind == "file":
                artifact.setdefault("header_dependencies", [])
                artifact.setdefault("source_dependencies", [])
                artifact.setdefault("types", [])
                artifact.setdefault("functions", [])
            collection.append(artifact)
            target = artifact
        else:
            target = next(item for item in collection if item.get("id") == artifact_id)
            old_file = target.get("file")
            target.update(deepcopy(operation["changes"]))
            if kind in {"type", "function"} and old_file != target.get("file"):
                list_key = f"{kind}s"
                for file_item in out.get("files", []):
                    if old_file == file_item.get("id") and artifact_id in file_item.get(list_key, []):
                        file_item[list_key].remove(artifact_id)
        if kind in {"type", "function"}:
            list_key = f"{kind}s"
            owner = next((item for item in out.get("files", []) if item.get("id") == target.get("file")), None)
            if owner is not None and artifact_id not in owner.setdefault(list_key, []):
                owner[list_key].append(artifact_id)
    return out


def _correction_input_slice(
    payload: dict[str, Any], previous_patch: dict[str, Any], validation_errors: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    text = "\n".join(validation_errors)
    groups: dict[str, list[str]] = {}
    for error in validation_errors:
        match = re.search(r"(?:residual closure )?([a-z][a-z0-9_]+):", error)
        groups.setdefault(match.group(1) if match else "structural_patch_error", []).append(error)

    related = payload.get("related_artifacts", {})
    selected: dict[str, list[dict[str, Any]]] = {key: [] for key in related}
    selected_file_ids: set[str] = set()
    for key, values in related.items():
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            if any(str(item.get(field, "")) and str(item.get(field)) in text for field in ("id", "name")):
                selected[key].append(item)
                if item.get("file"):
                    selected_file_ids.add(str(item["file"]))
                if key == "files" and item.get("id"):
                    selected_file_ids.add(str(item["id"]))
    for key in ("files", "types", "functions"):
        values = related.get(key, [])
        for item in values if isinstance(values, list) else []:
            if isinstance(item, dict) and (
                str(item.get("id", "")) in selected_file_ids or str(item.get("file", "")) in selected_file_ids
            ) and item not in selected[key]:
                selected[key].append(item)
    if not any(selected.values()):
        selected = deepcopy(related)

    operation_indexes = {int(value) for value in re.findall(r"operations\[(\d+)\]", text)}
    relevant_operations: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for index, operation in enumerate(previous_patch.get("operations", [])):
        operation_fields = operation.get("changes") or operation.get("artifact") or {}
        summary = {
            "operation_index": index,
            "op": operation.get("op"),
            "artifact_kind": operation.get("artifact_kind"),
            "artifact_id": operation.get("artifact_id"),
            "fields": sorted(operation_fields) if isinstance(operation_fields, dict) else [],
        }
        summaries.append(summary)
        artifact_name = str((operation.get("artifact") or {}).get("name", ""))
        if index in operation_indexes or str(operation.get("artifact_id", "")) in text or (artifact_name and artifact_name in text):
            relevant_operations.append(deepcopy(operation))
    compact = {
        key: value
        for key, value in payload.items()
        if key not in {"closure_diagnostics", "related_artifacts"}
    }
    compact["diagnostic_groups"] = groups
    compact["related_artifacts"] = selected
    compact["primary_patch_operation_summary"] = summaries
    return compact, relevant_operations, groups


def request_semantic_patch(
    payload: dict[str, Any],
    *,
    api_key_env: str,
    log_dir: str | Path,
    previous_patch: dict[str, Any] | None = None,
    validation_errors: list[str] | None = None,
    correction_partition: str | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    from agent.common.llm_client import FixedQwenClient, LLMRequest

    correction = previous_patch is not None
    related_functions = {
        str(item.get("name", "")): str(item.get("id", ""))
        for item in payload.get("related_artifacts", {}).get("functions", [])
        if isinstance(item, dict)
    }
    dependency_update_checklist: list[dict[str, str]] = []
    for error in validation_errors or []:
        match = re.search(r"residual closure (access_service_missing|callback_provider_missing): Function ([A-Za-z_][A-Za-z0-9_]*)", error)
        if not match:
            continue
        caller_name = match.group(2)
        item = {
            "diagnostic": match.group(1),
            "caller_name": caller_name,
            "caller_artifact_id": related_functions.get(caller_name, "resolve from previous_patch added functions"),
            "required_operation": "update this caller with complete rely.FUNC and call_contracts containing the compatible provider",
        }
        type_match = re.search(r"provider/access path for ([A-Za-z_][A-Za-z0-9_]*_t)(?: required by|$)", error)
        if type_match:
            required_type = type_match.group(1)
            item["required_provider_return_type"] = required_type
            item["acceptance_rule"] = (
                f"the provider signature RETURN must be {required_type} or a pointer to {required_type}; "
                "a reverse accessor returning another type does not satisfy this diagnostic"
            )
        dependency_update_checklist.append(item)
    input_slice = payload
    relevant_previous_operations: list[dict[str, Any]] = []
    diagnostic_groups: dict[str, list[str]] = {}
    if correction:
        input_slice, relevant_previous_operations, diagnostic_groups = _correction_input_slice(
            payload, previous_patch or {}, validation_errors or []
        )
    task = {
        "task": "Correct only the listed patch validation errors." if correction else "Create one bounded semantic closure patch for the listed implementability diagnostics.",
        "constraints": [
            "Return only a JSON object with patch_id and operations.",
            "Do not modify protocol facts or generate C source code.",
            "Do not delete artifacts or invent protocol behavior.",
            "Use stable artifact IDs. Operations are add or update only.",
            "Every operation requires artifact_kind, artifact_id, reason, provenance, and affected_artifact_ids.",
            "provenance.kind is protocol_fact, engineering_decision, or open_assumption.",
            "Allowed semantic actions: lifecycle/helper/access service/owner/visibility/call/data flow/dependency/runtime main/interface closure only.",
            "Added functions must include complete normalized signature, rely, logic/event behavior, parameter ownership, visibility, file, and trace_refs.",
            "For add operations use only fields listed by normalized_artifact_contracts. owner_file, behavior, definition, and type_category are forbidden aliases; use file, logic/event, and type_spec instead.",
            "Before every add, search all related_artifacts for an existing symbol and stable ID. Update/reuse existing lifecycle or access artifacts; duplicate add operations are invalid.",
            "Resolve diagnostics as a coherent lifecycle and data-flow design; do not copy a protocol-specific reference inventory.",
            "For every access/callback item in required_dependency_update_checklist, include an update operation for that exact caller. Adding a provider without updating the caller RELY.FUNC and call_contracts is invalid.",
        ],
        "diagnostic_resolution_contracts": {
            "access_service_missing": "Add or reuse a provider whose RETURN is the required_type, then add that provider to every affected caller RELY.FUNC and CALL_CONTRACTS so the value is provably available before consumer calls.",
            "callback_signature_mismatch": "Update the named incompatible callback function, not the function that invokes registration/decoding; its RETURN and ordered PARAM types must exactly equal the callback TYPE_SPEC.CALLBACK_SIGNATURE. Never add an extra context/connection parameter: recover context through the declared user_data parameter and explicit access helpers.",
            "callback_provider_missing": "Add every compatible callback function to the registering caller RELY.FUNC and CALL_CONTRACTS, or add compatible callback artifacts. The patch must name each missing callback provider in the caller dependencies; mentioning it only in reason/affected IDs is invalid.",
            "opaque_constructor_missing": "Add a creator/provider returning TYPE* or encode an explicit owner-internal provider boundary supported by existing facts and interfaces.",
            "opaque_destructor_missing": "Add destroy/cleanup and add it to the owning runtime cleanup call chain.",
            "cross_file_private_type": "Move ownership or make the type PUBLIC and ensure owner header dependency closure.",
            "cross_file_private_function": "Move ownership or make the callee public and ensure source dependency closure.",
            "runtime_entrypoint_missing": "Add exactly one int main(...) with explicit create/run/destroy RELY and CALL_CONTRACTS.",
            "file_header_missing": "Update the affected FILE artifact with a dedicated, non-conflicting header_path consistent with its planned source_path; do not reuse a header owned by another FILE artifact.",
        },
        "patch_schema": {
            "patch_id": "stable string",
            "operations": [
                {
                    "op": "add|update",
                    "artifact_kind": "module|file|type|function",
                    "artifact_id": "stable ID",
                    "artifact": "complete artifact for add only",
                    "changes": "allowed changed fields for update only",
                    "reason": "closure reason",
                    "provenance": {"kind": "protocol_fact|engineering_decision|open_assumption", "refs": ["fact/rule/assumption refs"]},
                    "affected_artifact_ids": ["caller/callee/type/resource IDs"],
                }
            ],
        },
        "normalized_artifact_contracts": {
            "type_add": {
                "required": ["id", "file", "name", "visibility", "role", "type_spec"],
                "optional_lifecycle_metadata": ["resource_handle", "ownership_model", "opaque_boundaries", "ownership_fields"],
                "visibility": "PUBLIC|PRIVATE",
                "type_spec_examples": [
                    {"TYPE_KIND": "OPAQUE"},
                    {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": "return_type (*)(parameter_types)"},
                    {"TYPE_KIND": "STRUCT", "FIELDS": [{"NAME": "field", "TYPE": "c_type", "ROLE": "engineering role"}]},
                ],
            },
            "function_add": {
                "required": ["id", "file", "name", "function_type", "visibility", "role", "signature", "rely"],
                "signature": {"RAW": "C declaration", "NAME": "symbol", "RETURN": "C type", "PARAMS": [{"TYPE": "C type", "NAME": "param", "NULLABLE": False, "OWNERSHIP": "BORROWED|OWNED|TRANSFER|SHARED|UNKNOWN"}]},
                "rely": {"STRUCT": [{"NAME": "type", "ROLE": "reason"}], "FUNC": [{"NAME": "callee", "KIND": "CALL", "ROLE": "reason"}], "VAR": []},
                "behavior": "Use logic for ALGORITHM/ENTRYPOINT or event for EVENT.",
            },
            "file_add": {
                "required": ["id", "module", "trace_id", "role", "header_path", "source_path"],
                "dependency_fields": ["header_dependencies", "source_dependencies"],
            },
            "update": "changes may contain only fields explicitly allowed by the target artifact kind; never include id or protocol behavior.",
        },
        "input_slice": input_slice,
    }
    if dependency_update_checklist:
        task["required_dependency_update_checklist"] = dependency_update_checklist
    if correction:
        task["constraints"].append(
            "Return only correction delta operations for the listed diagnostic groups. Do not repeat valid primary operations. Updates may target stable IDs added by the retained primary patch."
        )
        task["diagnostic_groups"] = diagnostic_groups
        task["relevant_primary_operations"] = relevant_previous_operations
        task["patch_validation_errors"] = validation_errors or []
    messages = [
        {
            "role": "system",
            "content": "You are the SpecForge semantic closure repair agent. Return only valid JSON and stay within the bounded patch authority.",
        },
        {"role": "user", "content": json.dumps(task, ensure_ascii=False, separators=(",", ":"))},
    ]
    root = Path(log_dir)
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"02_patch_correction_{correction_partition}" if correction and correction_partition else "02_patch_correction" if correction else "01_semantic_patch"
    for suffix in ("usage.json", "failure.json"):
        path = root / f"{prefix}_{suffix}"
        if path.exists():
            path.unlink()
    write_json(root / f"{prefix}_prompt.json", messages)
    response = FixedQwenClient(api_key_env=api_key_env).generate_with_usage(
        LLMRequest(messages=messages, top_p=0.15, temperature=0.05, is_stream=True, enable_thinking=False, max_completion_tokens=8000)
    )
    (root / f"{prefix}_response.raw.txt").write_text(response.content, encoding="utf-8")
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
        "prompt_characters": len(messages[-1]["content"]),
        "request_count": 1,
    }
    write_json(root / f"{prefix}_usage.json", usage)
    stripped = response.content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].startswith("```"):
            lines.pop()
        stripped = "\n".join(lines).strip()
    try:
        patch = json.loads(stripped)
    except json.JSONDecodeError as exc:
        write_json(
            root / f"{prefix}_failure.json",
            {
                "kind": "json_parse_failure",
                "error": f"{type(exc).__name__}: {exc}",
                "response_characters": len(response.content),
                "usage": usage,
            },
        )
        raise ValueError(f"semantic_patch_json_parse_failure: {exc}") from exc
    if not isinstance(patch, dict):
        write_json(root / f"{prefix}_failure.json", {"kind": "invalid_patch_shape", "error": "response is not a JSON object", "usage": usage})
        raise ValueError("Semantic closure patch must be a JSON object")
    return patch, usage
