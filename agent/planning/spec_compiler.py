from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import TargetProfile


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_") or "x"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _public_type_name(protocol_slug: str, module_name: str) -> str:
    return f"{protocol_slug}_{module_name}_t"


def _signature(return_type: str, name: str, params: list[dict[str, str]]) -> str:
    raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}".strip() for item in params) or "void"
    return f"{return_type} {name}({raw_params})"


def compile_spec_bundle(implementation_plan: dict[str, Any], output_dir: str | Path, target_profile: TargetProfile) -> dict[str, Path]:
    out_dir = Path(output_dir)
    spec_root = out_dir / "spec_bundle"
    spec_root.mkdir(parents=True, exist_ok=True)
    protocol_name = str(implementation_plan.get("protocol_name", "protocol"))
    protocol_slug = _slug(protocol_name)
    modules = implementation_plan.get("module_graph", [])

    module_spec = {
        "KIND": "PROTOCOL_MODULE_SPEC",
        "PROTOCOL": {
            "NAME": protocol_name.upper(),
            "SPEC_VERSION": "planning-generated/v1alpha1",
            "ROLES": [str(implementation_plan.get("target_profile", {}).get("target_role", "")).upper()],
        },
        "MODULES": [],
        "CONSISTENCY_RULES": [
            {"NAME": "unique_public_type_owner", "DESC": "Every canonical public type must have one owner only."},
            {"NAME": "acyclic_module_dependencies", "DESC": "Module dependencies must be forward-safe for current coder."},
        ],
    }

    file_paths: list[Path] = []
    for module in modules:
        module_name = str(module.get("name"))
        module_path = str(module.get("path"))
        header_path = f"{module_path}.h"
        source_path = f"{module_path}.c"
        module_spec["MODULES"].append(
            {
                "NAME": module_name,
                "ROLE": str(module.get("role")),
                "DEPENDENCIES": [str(v) for v in module.get("dependencies", [])],
                "FILES": [header_path, source_path],
                "ARTIFACTS": list(module.get("artifacts", [])),
                "DOC_REF": [],
            }
        )
        file_entry = next((item for item in implementation_plan.get("file_plan", []) if item.get("module") == module_name), None)
        functions = [item for item in implementation_plan.get("function_plan", []) if item.get("module") == module_name]
        public_type = file_entry.get("public_type") if isinstance(file_entry, dict) else _public_type_name(protocol_slug, module_name)
        file_trace = f"{protocol_slug}/{module_name}/{module_name}"
        header_dependencies = list(file_entry.get("header_dependencies", [])) if isinstance(file_entry, dict) else []
        source_dependencies = list(file_entry.get("source_dependencies", [])) if isinstance(file_entry, dict) else [header_path]
        header_interfaces = []
        source_interfaces = []
        for function in functions:
            params = list(function.get("params", []))
            signature = _signature(str(function.get("return_type", "void")), str(function.get("name")), params)
            header_interfaces.append(
                {
                    "SIGNATURE": signature,
                    "NAME": str(function.get("name")),
                    "KIND": "FUNC",
                    "FUNCTION_TYPE": str(function.get("function_type", "ALGORITHM")),
                    "ROLE": str(function.get("role")),
                    "VISIBILITY": str(function.get("visibility", "public")),
                }
            )
            source_interfaces.append(
                {
                    "TRACE_ID": str(function.get("trace_id")),
                    "SIGNATURE": signature,
                    "NAME": str(function.get("name")),
                    "KIND": "FUNC",
                    "ROLE": str(function.get("role")),
                    "VISIBILITY": str(function.get("visibility", "public")),
                }
            )
        file_spec = {
            "KIND": "FILE_SPEC",
            "FILE": {
                "TRACE_ID": file_trace,
                "LANG": "C",
                "ROLE": str(module.get("role")),
                "DOC_REF": [],
            },
            "HEADER": {
                "PATH": header_path,
                "DEPENDENCY": header_dependencies,
                "DATA": [{"NAME": public_type, "KIND": "TYPE", "VISIBILITY": "PUBLIC", "ROLE": str(module.get("role"))}],
                "INTERFACE": header_interfaces,
            },
            "SOURCE": {
                "PATH": source_path,
                "DEPENDENCY": source_dependencies,
                "DATA": [{"NAME": f"struct {public_type.rstrip('_t')}", "KIND": "TYPE", "VISIBILITY": "PRIVATE", "ROLE": str(module.get("role"))}],
                "INTERFACE": source_interfaces,
            },
        }
        file_spec_path = spec_root / module_name / f"{module_name}_spec.json"
        _write_json(file_spec_path, file_spec)
        file_paths.append(file_spec_path)
        for function in functions:
            function_spec = {
                "KIND": "FUNCTION_SPEC",
                "TRACE_ID": str(function.get("trace_id")),
                "FUNCTION_TYPE": str(function.get("function_type", "ALGORITHM")),
                "ROLE": str(function.get("role")),
                "SIGNATURE": {
                    "RAW": _signature(str(function.get("return_type", "void")), str(function.get("name")), list(function.get("params", []))),
                    "NAME": str(function.get("name")),
                    "RETURN": str(function.get("return_type", "void")),
                    "PARAMS": list(function.get("params", [])),
                },
                "RELY": dict(function.get("rely", {"STRUCT": [], "FUNC": [], "VAR": []})),
                "LOGIC": {
                    "INPUT": str(function.get("logic", {}).get("input", "")),
                    "ACTION": str(function.get("logic", {}).get("action", "")),
                    "OUTPUT": str(function.get("logic", {}).get("output", "")),
                    "INVARIANTS_USED": list(function.get("logic", {}).get("invariants", [])),
                },
            }
            function_spec_path = spec_root / module_name / f"{function.get('name')}_spec.json"
            _write_json(function_spec_path, function_spec)
            file_paths.append(function_spec_path)

    module_spec_path = spec_root / f"{protocol_slug}_module_spec.json"
    _write_json(module_spec_path, module_spec)
    return {
        "module_spec": module_spec_path,
        "spec_root": spec_root,
    }
