from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent.coder.generation import render_header
from agent.coder.specs import load_spec_bundle_from_root

from .facts import write_json


def _function_ref(value: Any, functions: list[dict[str, Any]]) -> dict[str, Any] | None:
    reference = str(value or "")
    return next(
        (
            item for item in functions
            if reference in {str(item.get("id", "")), str(item.get("name", ""))}
        ),
        None,
    )


def _dummy_argument(
    parameter: dict[str, Any],
    binding: dict[str, Any] | None,
    callback_bindings: dict[str, dict[str, Any]],
    functions: list[dict[str, Any]],
) -> str:
    c_type = str(parameter.get("TYPE", "int")).strip() or "int"
    if binding and binding.get("source_kind") == "callback_binding":
        relation = callback_bindings.get(str(binding.get("source_ref", "")), {})
        provider = _function_ref(relation.get("provider_function_id"), functions)
        if provider is not None:
            return f"&{provider['name']}"
    return f"({c_type})0"


def run_abi_skeleton_probe(
    plan: dict[str, Any], specs_root: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    include_root = output_dir / "include"
    include_root.mkdir(parents=True)
    functions = [item for item in plan.get("functions", []) if isinstance(item, dict)]
    runtime = plan.get("runtime_entrypoint", {})
    main = _function_ref(runtime.get("main_function"), functions) if isinstance(runtime, dict) else None
    required_services = [
        str(value)
        for key in ("startup_services", "run_services", "cleanup_services")
        for value in (runtime.get(key, []) if isinstance(runtime.get(key), list) else [])
    ] if isinstance(runtime, dict) else []
    contracts = main.get("call_contracts", main.get("CALL_CONTRACTS", [])) if main else []
    contract_callees = {
        str(item.get("callee_function_id") or item.get("NAME"))
        for item in contracts if isinstance(item, dict)
    }
    required_ids = {
        str(service.get("id"))
        for reference in required_services
        if (service := _function_ref(reference, functions)) is not None
    }
    rely = main.get("rely", main.get("RELY", {})) if main else {}
    blockers: list[str] = []
    if required_services and (
        main is None
        or not contracts
        or not isinstance(rely, dict)
        or not rely.get("FUNC")
        or not required_ids <= contract_callees
    ):
        blockers.append("runtime_contract_projection_missing")

    bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
    if bundle.has_errors():
        blockers.append("rendered_header_validation_failed")
    headers: list[str] = []
    for file_spec in bundle.file_specs_by_trace.values():
        if not file_spec.header_path:
            continue
        target = include_root / file_spec.header_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_header(bundle, file_spec), encoding="utf-8")
        headers.append(file_spec.header_path)

    callback_bindings = {
        str(item.get("binding_id")): item
        for item in plan.get("callback_bindings", []) if isinstance(item, dict)
    }
    lines = ["#include <stddef.h>", *[f'#include "{path}"' for path in sorted(headers)], "", "static void specforge_abi_probe(void) {"]
    for index, relation in enumerate(callback_bindings.values()):
        callback = _function_ref(relation.get("provider_function_id"), functions)
        callback_type = next(
            (
                item for item in plan.get("types", [])
                if isinstance(item, dict)
                and str(relation.get("callback_type_id")) in {str(item.get("id")), str(item.get("name"))}
            ),
            None,
        )
        if callback is not None and callback_type is not None:
            lines.append(f"    {callback_type['name']} callback_{index} = &{callback['name']};")
            lines.append(f"    (void)callback_{index};")
    for contract in contracts if isinstance(contracts, list) else []:
        if not isinstance(contract, dict):
            continue
        callee = _function_ref(contract.get("callee_function_id") or contract.get("NAME"), functions)
        if callee is None:
            blockers.append("abi_probe_unknown_callee")
            continue
        signature = callee.get("signature", {})
        arguments = {
            str(item.get("parameter")): item
            for item in contract.get("argument_semantics", []) if isinstance(item, dict)
        }
        params = signature.get("PARAMS", []) if isinstance(signature, dict) else []
        call_args = ", ".join(
            _dummy_argument(parameter, arguments.get(str(parameter.get("NAME"))), callback_bindings, functions)
            for parameter in params if isinstance(parameter, dict)
        )
        lines.append(f"    (void){callee['name']}({call_args});")
    lines.extend(["}", "", "int main(void) { specforge_abi_probe(); return 0; }", ""])
    source = "\n".join(lines)
    source_path = output_dir / "abi_skeleton.c"
    source_path.write_text(source, encoding="utf-8")
    command = [
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
        "-fsyntax-only", "-I", str(include_root), str(source_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False) if not blockers else None
    passed = not blockers and completed is not None and completed.returncode == 0
    result = {
        "kind": "ABI_SKELETON_PROBE",
        "version": 1,
        "passed": passed,
        "blockers": blockers,
        "command": command,
        "returncode": completed.returncode if completed is not None else None,
        "stdout": completed.stdout if completed is not None else "",
        "stderr": completed.stderr if completed is not None else "",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "specs_root": str(specs_root),
    }
    write_json(output_dir / "result.json", result)
    return result
