from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .models import TargetProfile


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_") or "x"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _signature(return_type: str, name: str, params: list[dict[str, Any]]) -> str:
    raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}".strip() for item in params) or "void"
    return f"{return_type} {name}({raw_params})"


def _visibility(value: Any, *, data_item: bool = False) -> str:
    text = str(value or "public").strip().lower()
    if text not in {"public", "private"}:
        text = "public" if text == "public" else "private" if text == "private" else "public"
    return text.upper() if data_item else text


def _default_contract() -> dict[str, Any]:
    return {
        "PRECONDITION": [{"TEXT": "Caller provides valid arguments according to the signature."}],
        "POSTCONDITION": [{"TEXT": "Function preserves module invariants and applies its documented result or side effect."}],
        "IDEMPOTENT": False,
        "THREAD_SAFETY": "SINGLE_THREAD_ONLY",
    }


def _contract(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and all(key in raw for key in ("PRECONDITION", "POSTCONDITION", "IDEMPOTENT", "THREAD_SAFETY")):
        return raw
    return _default_contract()


def _data_items(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("NAME", "")).strip()
        kind = str(item.get("KIND", "")).strip().upper()
        role = str(item.get("ROLE", "")).strip()
        if not name or kind not in {"TYPE", "VAR", "MACRO", "CONST"} or not role:
            continue
        out = {
            "NAME": name,
            "KIND": kind,
            "VISIBILITY": _visibility(item.get("VISIBILITY", "PUBLIC"), data_item=True),
            "ROLE": role,
        }
        if isinstance(item.get("TYPE_SPEC"), dict):
            out["TYPE_SPEC"] = item["TYPE_SPEC"]
        result.append(out)
    return result


def _header_interfaces(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "SIGNATURE": str(item.get("SIGNATURE", "")),
                "NAME": str(item.get("NAME", "")),
                "KIND": "FUNC",
                "FUNCTION_TYPE": str(item.get("FUNCTION_TYPE", "ALGORITHM")),
                "ROLE": str(item.get("ROLE", "")),
                "VISIBILITY": _visibility(item.get("VISIBILITY", "public")),
            }
        )
    return result


def _source_interfaces(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "TRACE_ID": str(item.get("TRACE_ID", "")),
                "SIGNATURE": str(item.get("SIGNATURE", "")),
                "NAME": str(item.get("NAME", "")),
                "KIND": "FUNC",
                "FUNCTION_TYPE": str(item.get("FUNCTION_TYPE", "ALGORITHM")),
                "ROLE": str(item.get("ROLE", "")),
                "CONTRACT": _contract(item.get("CONTRACT")),
                "VISIBILITY": _visibility(item.get("VISIBILITY", "public")),
            }
        )
    return result


def _call_contracts(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("NAME", "")).strip()
        signature = str(item.get("SIGNATURE", "")).strip()
        if name and signature:
            result.append(dict(item))
    return result


def _rely(raw: Any) -> dict[str, list[dict[str, Any]]]:
    raw = raw if isinstance(raw, dict) else {}
    structs = [
        {"NAME": str(item.get("NAME", "")), "ROLE": str(item.get("ROLE", ""))}
        for item in raw.get("STRUCT", [])
        if isinstance(item, dict) and item.get("NAME") and item.get("ROLE")
    ]
    funcs = [
        {"NAME": str(item.get("NAME", "")), "KIND": str(item.get("KIND") or item.get("DEPENDENCY_KIND") or "FUNC"), "ROLE": str(item.get("ROLE", ""))}
        for item in raw.get("FUNC", [])
        if isinstance(item, dict) and item.get("NAME") and item.get("ROLE")
    ]
    vars_ = [
        {"NAME": str(item.get("NAME", "")), "ROLE": str(item.get("ROLE", ""))}
        for item in raw.get("VAR", [])
        if isinstance(item, dict) and item.get("NAME") and item.get("ROLE")
    ]
    return {"STRUCT": structs, "FUNC": funcs, "VAR": vars_}


def _module_artifacts(module: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in module.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        raw_kind = str(item.get("KIND", "")).strip().upper()
        name = str(item.get("NAME", "")).strip()
        if raw_kind in {"PUBLIC_TYPE", "TYPE"}:
            kind = "TYPE"
        elif raw_kind in {"PUBLIC_FUNCTION", "FUNC"}:
            kind = "FUNC"
        elif raw_kind in {"VAR", "CONST", "MACRO"}:
            kind = raw_kind
        else:
            continue
        if not name:
            continue
        result.append({"NAME": name, "KIND": kind, "ROLE": str(item.get("ROLE") or f"{kind.lower()} artifact for module {module.get('name', '')}")})
    return result


def _module_entry_from_blueprint(module: dict[str, Any]) -> dict[str, Any]:
    return {
        "NAME": str(module.get("name", "")),
        "ROLE": str(module.get("role", "")),
        "DEPENDENCIES": [str(item) for item in module.get("dependencies", [])],
        "ARTIFACTS": _module_artifacts(module),
        "FILES": [str(item) for item in module.get("files", [])],
        "DOC_REF": [str(item) for item in module.get("evidence_refs", [])],
    }


def _file_spec_from_blueprint(file_item: dict[str, Any]) -> dict[str, Any]:
    header_path = str(file_item.get("header_path", ""))
    source_path = str(file_item.get("source_path", ""))
    spec: dict[str, Any] = {
        "KIND": "FILE_SPEC",
        "FILE": {
            "TRACE_ID": str(file_item.get("trace_id", "")),
            "LANG": str(file_item.get("lang", "C")),
            "ROLE": str(file_item.get("role", "")),
            "DOC_REF": [str(item) for item in file_item.get("evidence_refs", [])],
        },
        "SOURCE": {
            "PATH": source_path,
            "DEPENDENCY": [str(item) for item in file_item.get("source_dependencies", [])],
            "DATA": _data_items(file_item.get("source_data", [])),
            "INTERFACE": _source_interfaces(file_item.get("source_interfaces", [])),
        },
    }
    if header_path:
        spec["HEADER"] = {
            "PATH": header_path,
            "DEPENDENCY": [str(item) for item in file_item.get("header_dependencies", [])],
            "DATA": _data_items(file_item.get("header_data", [])),
            "INTERFACE": _header_interfaces(file_item.get("header_interfaces", [])),
        }
    for key in (
        "PUBLIC_SYMBOLS",
        "ACCESS_PATHS",
        "CALL_CONTRACTS",
        "FORBIDDEN_SYMBOLS",
        "TEST_VECTORS",
    ):
        value = file_item.get(key.lower()) or file_item.get(key)
        if key == "CALL_CONTRACTS":
            value = _call_contracts(value)
        if value:
            spec[key] = value
    return spec


def _function_spec_from_blueprint(function: dict[str, Any]) -> dict[str, Any]:
    signature = dict(function.get("signature", {}))
    if not signature:
        name = str(function.get("name", ""))
        return_type = str(function.get("return_type", "void"))
        params = list(function.get("params", []))
        signature = {"RAW": _signature(return_type, name, params), "NAME": name, "RETURN": return_type, "PARAMS": params}
    function_type = str(function.get("function_type", "ALGORITHM"))
    spec: dict[str, Any] = {
        "KIND": "FUNCTION_SPEC",
        "TRACE_ID": str(function.get("trace_id", "")),
        "FUNCTION_TYPE": function_type,
        "ROLE": str(function.get("role", "")),
        "SIGNATURE": signature,
        "RELY": _rely(function.get("rely", {"STRUCT": [], "FUNC": [], "VAR": []})),
    }
    if function_type == "EVENT":
        spec["EVENT"] = dict(function.get("event") or {})
    elif function_type == "ENTRYPOINT" and function.get("event"):
        spec["EVENT"] = dict(function.get("event") or {})
    else:
        spec["LOGIC"] = dict(function.get("logic") or {})
    optional_key_map = {
        "PUBLIC_SYMBOLS": "public_symbols",
        "ACCESS_PATHS": "access_paths",
        "WIRE_MAPPING": "wire_mapping",
        "CALL_CONTRACTS": "call_contracts",
        "FORBIDDEN_SYMBOLS": "forbidden_symbols",
        "TEST_VECTORS": "test_vectors",
    }
    for spec_key, blueprint_key in optional_key_map.items():
        value = function.get(blueprint_key) or function.get(spec_key)
        if spec_key == "CALL_CONTRACTS":
            value = _call_contracts(value)
        if value:
            spec[spec_key] = value
    return spec


def _module_spec_from_blueprint(blueprint: dict[str, Any], target_profile: TargetProfile) -> dict[str, Any]:
    protocol_name = str(blueprint.get("protocol_name", "protocol"))
    modules = [_module_entry_from_blueprint(item) for item in blueprint.get("modules", [])]
    generation_order = list(blueprint.get("generation_order", [item.get("NAME") for item in modules]))
    return {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": {
            "NAME": protocol_name.upper(),
            "SPEC_VERSION": "planning-generated/v2alpha1",
            "ROLES": [str(target_profile.target_role).upper()],
            "SCOPE": str(target_profile.scope),
        },
        "MODULES": modules,
        "GENERATION_ORDER": generation_order,
        "CONSISTENCY_RULES": [
            {
                "ID": str(item.get("ID") or item.get("NAME") or ""),
                "RULE": str(item.get("RULE") or item.get("DESC") or ""),
                "DOC_REF": [str(ref) for ref in item.get("DOC_REF", [])] if isinstance(item, dict) and isinstance(item.get("DOC_REF", []), list) else [],
            }
            for item in blueprint.get("consistency_rules", [])
            if isinstance(item, dict)
        ],
    }


def _trace_file_spec_path(spec_root: Path, trace_id: str, fallback_dir: str, suffix_name: str) -> Path:
    parts = [part for part in trace_id.split("/") if part]
    if len(parts) >= 2:
        path_parts = parts[1:]
        if not path_parts:
            path_parts = [suffix_name]
        return spec_root.joinpath(*path_parts, f"{suffix_name}_spec.json")
    return spec_root / fallback_dir / f"{suffix_name}_spec.json"


def _trace_function_spec_path(spec_root: Path, trace_id: str, fallback_dir: str, suffix_name: str) -> Path:
    parts = [part for part in trace_id.split("/") if part]
    if len(parts) >= 3:
        path_parts = parts[1:-1]
        return spec_root.joinpath(*path_parts, f"{suffix_name}_spec.json")
    if len(parts) == 2:
        return spec_root / parts[1] / f"{suffix_name}_spec.json"
    return spec_root / fallback_dir / f"{suffix_name}_spec.json"


def _spec_path_for_file(spec_root: Path, file_item: dict[str, Any], file_spec: dict[str, Any]) -> Path:
    trace_id = str(file_spec.get("FILE", {}).get("TRACE_ID", "file"))
    basename = trace_id.split("/")[-1] if trace_id else "file"
    return _trace_file_spec_path(spec_root, trace_id, _slug(trace_id), basename)


def _spec_path_for_function(spec_root: Path, function_item: dict[str, Any], function_spec: dict[str, Any]) -> Path:
    trace_id = str(function_spec.get("TRACE_ID", "function"))
    parts = [part for part in trace_id.split("/") if part]
    basename = parts[-1] if parts else "function"
    parent = parts[-2] if len(parts) >= 2 else ""
    suffix_name = f"{basename}_function" if basename == parent else basename
    return _trace_function_spec_path(spec_root, trace_id, "functions", suffix_name)


def compile_spec_bundle(spec_blueprint: dict[str, Any], output_dir: str | Path, target_profile: TargetProfile) -> dict[str, Path]:
    out_dir = Path(output_dir)
    spec_root = out_dir / "spec_bundle"
    if spec_root.exists():
        shutil.rmtree(spec_root)
    spec_root.mkdir(parents=True, exist_ok=True)

    module_spec = _module_spec_from_blueprint(spec_blueprint, target_profile)
    protocol_slug = _slug(spec_blueprint.get("protocol_name", module_spec.get("PROTOCOL", {}).get("NAME", "protocol")))
    module_spec_path = spec_root / f"{protocol_slug}_module_spec.json"
    _write_json(module_spec_path, module_spec)

    for file_item in spec_blueprint.get("files", []):
        file_spec = _file_spec_from_blueprint(file_item)
        _write_json(_spec_path_for_file(spec_root, file_item, file_spec), file_spec)

    for function in spec_blueprint.get("functions", []):
        function_spec = _function_spec_from_blueprint(function)
        _write_json(_spec_path_for_function(spec_root, function, function_spec), function_spec)

    return {
        "module_spec": module_spec_path,
        "spec_root": spec_root,
    }
