from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .models import (
    Diagnostic,
    FileSpec,
    FunctionSignature,
    FunctionSpec,
    HeaderInterface,
    ModuleEntry,
    ProtocolMeta,
    SourceInterface,
    SpecBundle,
)


def normalize_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("../"):
        normalized = normalized[3:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_system_header(path: str) -> str:
    return path.strip().removeprefix("<").removesuffix(">")


def normalize_signature(signature: str) -> str:
    return re.sub(r"\s+", " ", signature.strip())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _require_object(raw: dict[str, Any], key: str, path: Path, diags: list[Diagnostic]) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        diags.append(Diagnostic("error", "invalid_shape", f"Expected object at key '{key}'", str(path)))
        return {}
    return value


def _require_list(raw: dict[str, Any], key: str, path: Path, diags: list[Diagnostic]) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        diags.append(Diagnostic("error", "invalid_shape", f"Expected array at key '{key}'", str(path)))
        return []
    return value


def _parse_header_interface(raw: dict[str, Any], path: Path, diags: list[Diagnostic]) -> HeaderInterface:
    required = ["SIGNATURE", "NAME", "KIND", "FUNCTION_TYPE", "ROLE", "VISIBILITY"]
    for key in required:
        if key not in raw:
            diags.append(Diagnostic("error", "missing_field", f"Missing '{key}' in header interface", str(path)))
    return HeaderInterface(
        signature=str(raw.get("SIGNATURE", "")),
        name=str(raw.get("NAME", "")),
        kind=str(raw.get("KIND", "")),
        function_type=str(raw.get("FUNCTION_TYPE", "")),
        role=str(raw.get("ROLE", "")),
        visibility=str(raw.get("VISIBILITY", "")),
    )


def _parse_source_interface(raw: dict[str, Any], path: Path, diags: list[Diagnostic]) -> SourceInterface:
    required = ["TRACE_ID", "SIGNATURE", "NAME", "KIND", "ROLE", "VISIBILITY"]
    for key in required:
        if key not in raw:
            diags.append(Diagnostic("error", "missing_field", f"Missing '{key}' in source interface", str(path)))
    return SourceInterface(
        trace_id=str(raw.get("TRACE_ID", "")),
        signature=str(raw.get("SIGNATURE", "")),
        name=str(raw.get("NAME", "")),
        kind=str(raw.get("KIND", "")),
        role=str(raw.get("ROLE", "")),
        visibility=str(raw.get("VISIBILITY", "")),
    )


def _parse_file_spec(path: Path, raw: dict[str, Any], diags: list[Diagnostic]) -> FileSpec:
    file_meta = _require_object(raw, "FILE", path, diags)
    header_raw = raw.get("HEADER")
    if header_raw is None:
        header: dict[str, Any] = {}
    elif isinstance(header_raw, dict):
        header = header_raw
    else:
        diags.append(Diagnostic("error", "invalid_shape", "Expected object at key 'HEADER'", str(path)))
        header = {}
    source = _require_object(raw, "SOURCE", path, diags)
    header_dependency = header.get("DEPENDENCY", [])
    header_system_dependency = header.get("SYSTEM_DEPENDENCY", [])
    header_data = header.get("DATA", [])
    header_interface = header.get("INTERFACE", [])
    return FileSpec(
        trace_id=str(file_meta.get("TRACE_ID", "")),
        role=str(file_meta.get("ROLE", "")),
        lang=str(file_meta.get("LANG", "")),
        header_path=normalize_repo_path(str(header.get("PATH", ""))),
        source_path=normalize_repo_path(str(source.get("PATH", ""))),
        header_dependencies=[normalize_repo_path(str(item)) for item in header_dependency] if isinstance(header_dependency, list) else [],
        header_system_dependencies=[normalize_system_header(str(item)) for item in header_system_dependency] if isinstance(header_system_dependency, list) else [],
        source_dependencies=[normalize_repo_path(str(item)) for item in _require_list(source, "DEPENDENCY", path, diags)],
        header_data=header_data if isinstance(header_data, list) else [],
        source_data=_require_list(source, "DATA", path, diags),
        header_interfaces=[_parse_header_interface(item, path, diags) for item in header_interface] if isinstance(header_interface, list) else [],
        source_interfaces=[_parse_source_interface(item, path, diags) for item in _require_list(source, "INTERFACE", path, diags)],
        spec_path=path,
        raw=raw,
    )


def _parse_function_spec(path: Path, raw: dict[str, Any], diags: list[Diagnostic]) -> FunctionSpec:
    signature = _require_object(raw, "SIGNATURE", path, diags)
    return FunctionSpec(
        trace_id=str(raw.get("TRACE_ID", "")),
        function_type=str(raw.get("FUNCTION_TYPE", "")),
        role=str(raw.get("ROLE", "")),
        signature=FunctionSignature(
            raw=str(signature.get("RAW", "")),
            name=str(signature.get("NAME", "")),
            return_type=str(signature.get("RETURN", "")),
            params=_require_list(signature, "PARAMS", path, diags),
        ),
        rely=_require_object(raw, "RELY", path, diags),
        body=raw.get("LOGIC") if "LOGIC" in raw else raw.get("EVENT", {}),
        source_path=path,
        raw=raw,
    )


def _parse_module_spec(path: Path, raw: dict[str, Any], diags: list[Diagnostic]) -> tuple[ProtocolMeta, list[ModuleEntry], list[str], list[dict[str, Any]]]:
    protocol_raw = _require_object(raw, "PROTOCOL", path, diags)
    protocol = ProtocolMeta(
        name=str(protocol_raw.get("NAME", "")),
        spec_version=str(protocol_raw.get("SPEC_VERSION", "")),
        roles=[str(role) for role in protocol_raw.get("ROLES", []) if str(role).strip()],
        default_port=protocol_raw.get("DEFAULT_PORT") if isinstance(protocol_raw.get("DEFAULT_PORT"), int) else None,
    )
    modules_raw = _require_list(raw, "MODULES", path, diags)
    modules = [
        ModuleEntry(
            name=str(item.get("NAME", "")),
            role=str(item.get("ROLE", "")),
            dependencies=[str(dep) for dep in item.get("DEPENDENCIES", [])],
            files=[normalize_repo_path(str(file_path)) for file_path in item.get("FILES", [])],
            artifacts=item.get("ARTIFACTS", []),
            doc_ref=item.get("DOC_REF", []),
            raw=item,
        )
        for item in modules_raw
    ]
    generation_order = [str(name) for name in raw.get("GENERATION_ORDER", [])]
    return protocol, modules, generation_order, raw.get("CONSISTENCY_RULES", [])


def _modules_in_generation_order(modules: list[ModuleEntry], generation_order: list[str], path: Path, diags: list[Diagnostic]) -> list[ModuleEntry]:
    by_name = {module.name: module for module in modules}
    if len(by_name) != len(modules):
        return modules
    if not generation_order:
        diags.append(Diagnostic("warning", "missing_generation_order", "GENERATION_ORDER is empty; falling back to MODULES order", str(path)))
        return modules

    ordered: list[ModuleEntry] = []
    seen: set[str] = set()
    for name in generation_order:
        if name in seen:
            diags.append(Diagnostic("error", "duplicate_generation_order_entry", f"GENERATION_ORDER repeats module '{name}'", str(path)))
            continue
        seen.add(name)
        module = by_name.get(name)
        if module is None:
            diags.append(Diagnostic("error", "unknown_generation_order_module", f"GENERATION_ORDER references unknown module '{name}'", str(path)))
            continue
        ordered.append(module)

    missing = [module.name for module in modules if module.name not in seen]
    for name in missing:
        diags.append(Diagnostic("error", "missing_generation_order_module", f"GENERATION_ORDER omits module '{name}'", str(path)))

    return ordered if not missing and len(ordered) == len(modules) else modules


def _trace_parent(trace_id: str) -> str:
    return trace_id.rsplit("/", 1)[0] if "/" in trace_id else ""


def _iter_type_paths(type_name: str, type_spec: dict[str, Any], prefix: str | None = None) -> set[str]:
    base = prefix or type_name
    paths: set[str] = set()
    kind = str(type_spec.get("TYPE_KIND", "")).upper()
    member_key = "FIELDS" if kind == "STRUCT" else "VARIANTS" if kind == "UNION" else ""
    members = type_spec.get(member_key, [])
    if not isinstance(members, list):
        return paths
    for member in members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("NAME", "")).strip()
        if not name:
            continue
        member_path = f"{base}.{name}"
        paths.add(member_path)
        nested = member.get("TYPE_SPEC")
        if isinstance(nested, dict):
            paths.update(_iter_type_paths(type_name, nested, member_path))
    return paths


def _iter_type_members(type_spec: dict[str, Any]) -> list[dict[str, Any]]:
    kind = str(type_spec.get("TYPE_KIND", "")).upper()
    member_key = "FIELDS" if kind == "STRUCT" else "VARIANTS" if kind == "UNION" else ""
    members = type_spec.get(member_key, [])
    if not isinstance(members, list):
        return []
    out: list[dict[str, Any]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        out.append(member)
        nested = member.get("TYPE_SPEC")
        if isinstance(nested, dict):
            out.extend(_iter_type_members(nested))
    return out


def _public_symbols_and_paths(bundle: SpecBundle) -> tuple[set[str], set[str]]:
    symbols: set[str] = set()
    paths: set[str] = set()
    for file_spec in bundle.file_specs_by_trace.values():
        for item in file_spec.header_data:
            name = str(item.get("NAME", "")).strip()
            if name:
                symbols.add(name)
            type_spec = item.get("TYPE_SPEC")
            if isinstance(type_spec, dict) and name:
                paths.update(_iter_type_paths(name, type_spec))
                for enum_item in type_spec.get("ENUM_VALUES", []) if isinstance(type_spec.get("ENUM_VALUES"), list) else []:
                    if isinstance(enum_item, dict) and enum_item.get("NAME"):
                        symbols.add(str(enum_item["NAME"]))
        for interface in file_spec.header_interfaces:
            if interface.name:
                symbols.add(interface.name)
        for symbol in file_spec.raw.get("PUBLIC_SYMBOLS", []) if isinstance(file_spec.raw.get("PUBLIC_SYMBOLS"), list) else []:
            if isinstance(symbol, dict) and symbol.get("NAME"):
                symbols.add(str(symbol["NAME"]))
            elif isinstance(symbol, str):
                symbols.add(symbol)
        for path_item in file_spec.raw.get("ACCESS_PATHS", []) if isinstance(file_spec.raw.get("ACCESS_PATHS"), list) else []:
            if isinstance(path_item, dict) and path_item.get("PATH"):
                paths.add(str(path_item["PATH"]))
            elif isinstance(path_item, str):
                paths.add(path_item)
    return symbols, paths


def _canonical_access_path(path: str) -> str:
    value = path.strip()
    for prefix in ("out->", "pkt->", "p->"):
        if value.startswith(prefix):
            return "mqtt_packet_t." + value[len(prefix) :]
    return value


def _validate_module_order(bundle: SpecBundle) -> None:
    positions = {module.name: idx for idx, module in enumerate(bundle.modules_in_order)}
    declared = set(positions)
    order_names = [module.name for module in bundle.modules_in_order]
    if bundle.module_spec_path:
        seen = set(order_names)
        if len(seen) != len(order_names):
            bundle.diagnostics.append(Diagnostic("error", "duplicate_module", "Duplicate module names in generation order", str(bundle.module_spec_path)))
    for module in bundle.modules_in_order:
        for dep in module.dependencies:
            if dep not in declared:
                bundle.diagnostics.append(Diagnostic("error", "unknown_module_dependency", f"Module '{module.name}' depends on unknown module '{dep}'", str(bundle.module_spec_path)))
                continue
            if positions[dep] > positions[module.name]:
                bundle.diagnostics.append(Diagnostic("error", "generation_order_violation", f"Module '{module.name}' appears before dependency '{dep}'", str(bundle.module_spec_path)))


def _validate_module_file_coverage(bundle: SpecBundle) -> None:
    known_paths = set(bundle.file_specs_by_header_path) | set(bundle.file_specs_by_source_path)
    for module in bundle.modules_in_order:
        for file_path in module.files:
            if file_path not in known_paths:
                bundle.diagnostics.append(Diagnostic("error", "layout_module_file_unknown", f"Module '{module.name}' references '{file_path}' with no matching file spec", str(bundle.module_spec_path)))


def _validate_file_specs(bundle: SpecBundle) -> None:
    known_headers = set(bundle.file_specs_by_header_path)
    all_source_by_name = {
        interface.name: interface
        for file_spec in bundle.file_specs_by_trace.values()
        for interface in file_spec.source_interfaces
    }
    for trace_id, file_spec in bundle.file_specs_by_trace.items():
        if not trace_id:
            bundle.diagnostics.append(Diagnostic("error", "missing_trace_id", "File spec TRACE_ID is empty", str(file_spec.spec_path)))
        if not file_spec.header_path and not file_spec.source_path:
            bundle.diagnostics.append(Diagnostic("error", "missing_path", f"File spec '{trace_id}' is missing both header and source path", str(file_spec.spec_path)))
        is_main_source = file_spec.source_path.endswith("/main.c") or file_spec.source_path == "main.c"
        if file_spec.source_path and not file_spec.header_path and not is_main_source:
            bundle.diagnostics.append(Diagnostic("error", "missing_header_path", f"Non-main file spec '{trace_id}' is missing HEADER.PATH", str(file_spec.spec_path)))
        for dependency in file_spec.header_dependencies:
            if dependency not in known_headers:
                bundle.diagnostics.append(Diagnostic("error", "unknown_header_dependency", f"HEADER.DEPENDENCY references unknown header '{dependency}'", str(file_spec.spec_path)))
        for dependency in file_spec.source_dependencies:
            if dependency not in known_headers:
                bundle.diagnostics.append(Diagnostic("error", "unknown_source_dependency", f"SOURCE.DEPENDENCY references unknown header '{dependency}'", str(file_spec.spec_path)))
        for data_item in file_spec.header_data:
            type_spec = data_item.get("TYPE_SPEC")
            if isinstance(type_spec, dict):
                for member in _iter_type_members(type_spec):
                    member_type = str(member.get("TYPE", "")).strip()
                    if re.search(r"\[[^\]]+\]", member_type):
                        bundle.diagnostics.append(Diagnostic("error", "unsupported_array_type_spelling", "Array fields must use TYPE plus ARRAY_LEN instead of embedding [] in TYPE", str(file_spec.spec_path)))
                    if member_type in {"struct sockaddr_storage", "socklen_t"} and "sys/socket.h" not in file_spec.header_system_dependencies:
                        bundle.diagnostics.append(Diagnostic("error", "missing_system_header_dependency", f"Public header field type '{member_type}' requires HEADER.SYSTEM_DEPENDENCY to include sys/socket.h", str(file_spec.spec_path)))
            if data_item.get("KIND") != "TYPE" or str(data_item.get("VISIBILITY", "")).upper() != "PUBLIC":
                continue
            if isinstance(data_item.get("TYPE_SPEC"), dict):
                continue
            role = str(data_item.get("ROLE", ""))
            lowered_role = role.lower()
            is_opaque = "opaque" in lowered_role or "不透明" in role or "句柄" in role or "隐藏" in role
            if not is_opaque:
                name = str(data_item.get("NAME", ""))
                bundle.diagnostics.append(Diagnostic("warning", "missing_type_spec", f"Public type '{name}' should define TYPE_SPEC or be explicitly described as opaque", str(file_spec.spec_path)))
        for interface in file_spec.source_interfaces:
            linked = bundle.function_specs_by_trace.get(interface.trace_id)
            if linked is None:
                bundle.diagnostics.append(Diagnostic("warning", "missing_function_spec", f"Source interface '{interface.trace_id}' has no function spec", str(file_spec.spec_path)))
                continue
            if linked.signature.name != interface.name:
                bundle.diagnostics.append(Diagnostic("warning", "name_mismatch", f"Function spec '{linked.trace_id}' name '{linked.signature.name}' differs from source interface '{interface.name}'", str(linked.source_path)))
            if normalize_signature(linked.signature.raw) != normalize_signature(interface.signature):
                bundle.diagnostics.append(Diagnostic("warning", "signature_mismatch", f"Function spec '{linked.trace_id}' signature differs from file SOURCE interface", str(linked.source_path)))
        source_by_name = {item.name: item for item in file_spec.source_interfaces}
        for interface in file_spec.header_interfaces:
            source_interface = source_by_name.get(interface.name) or all_source_by_name.get(interface.name)
            if source_interface is None:
                bundle.diagnostics.append(Diagnostic("warning", "header_without_source", f"Header interface '{interface.name}' has no matching source interface in '{trace_id}'", str(file_spec.spec_path)))
                continue
            linked = bundle.function_specs_by_trace.get(source_interface.trace_id)
            if linked and normalize_signature(linked.signature.raw) != normalize_signature(interface.signature):
                bundle.diagnostics.append(Diagnostic("warning", "header_signature_mismatch", f"Header interface '{interface.name}' differs from function spec signature", str(file_spec.spec_path)))


def _validate_function_specs(bundle: SpecBundle) -> None:
    for trace_id, function_spec in bundle.function_specs_by_trace.items():
        parent = _trace_parent(trace_id)
        if parent not in bundle.file_specs_by_trace:
            bundle.diagnostics.append(Diagnostic("error", "orphan_function_spec", f"Function spec '{trace_id}' has no parent file spec '{parent}'", str(function_spec.source_path)))


def _validate_machine_constraints(bundle: SpecBundle) -> None:
    public_symbols, public_paths = _public_symbols_and_paths(bundle)
    module_raw = read_json(bundle.module_spec_path)
    for item in module_raw.get("FORBIDDEN_SYMBOLS", []):
        name = str(item.get("NAME", item)) if isinstance(item, dict) else str(item)
        if name in public_symbols:
            bundle.diagnostics.append(Diagnostic("error", "forbidden_public_symbol", f"Forbidden symbol '{name}' is also public", str(bundle.module_spec_path)))

    call_signatures: dict[str, str] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        for interface in file_spec.header_interfaces:
            call_signatures[interface.name] = normalize_signature(interface.signature)
        for interface in file_spec.source_interfaces:
            call_signatures[interface.name] = normalize_signature(interface.signature)

    for file_spec in bundle.file_specs_by_trace.values():
        for item in file_spec.raw.get("FORBIDDEN_SYMBOLS", []):
            name = str(item.get("NAME", item)) if isinstance(item, dict) else str(item)
            if name in public_symbols:
                bundle.diagnostics.append(Diagnostic("error", "forbidden_public_symbol", f"Forbidden symbol '{name}' is also public", str(file_spec.spec_path)))
        for contract in file_spec.raw.get("CALL_CONTRACTS", []):
            if not isinstance(contract, dict):
                continue
            name = str(contract.get("NAME", "")).strip()
            signature = str(contract.get("SIGNATURE", "")).strip()
            if name and signature and name in call_signatures and normalize_signature(signature) != call_signatures[name]:
                bundle.diagnostics.append(Diagnostic("error", "call_contract_signature_mismatch", f"CALL_CONTRACTS signature for '{name}' differs from canonical spec", str(file_spec.spec_path)))

    protocol_codec_with_vectors: set[str] = set()
    for function_spec in bundle.function_specs_by_trace.values():
        for mapping in function_spec.raw.get("WIRE_MAPPING", []):
            if not isinstance(mapping, dict):
                continue
            strategy = str(mapping.get("STRATEGY", "")).strip()
            target = str(mapping.get("TARGET", "")).strip()
            if strategy == "store_in_field" and target:
                canonical = _canonical_access_path(target)
                if canonical not in public_paths:
                    bundle.diagnostics.append(Diagnostic("error", "unknown_wire_mapping_target", f"WIRE_MAPPING target '{target}' does not match a public access path", str(function_spec.source_path)))
        if function_spec.raw.get("TEST_VECTORS"):
            protocol_codec_with_vectors.add(_trace_parent(function_spec.trace_id))

    for file_spec in bundle.file_specs_by_trace.values():
        if file_spec.raw.get("TEST_VECTORS"):
            protocol_codec_with_vectors.add(file_spec.trace_id)
        if file_spec.trace_id.startswith("mqtt/protocol/mqtt_decoder") and file_spec.trace_id not in protocol_codec_with_vectors:
            bundle.diagnostics.append(Diagnostic("warning", "missing_test_vectors", f"Protocol codec '{file_spec.trace_id}' should define TEST_VECTORS", str(file_spec.spec_path)))


def _validate_rendered_headers_compile(bundle: SpecBundle) -> None:
    if bundle.has_errors():
        return
    from .generation import render_header

    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        for file_spec in bundle.file_specs_by_trace.values():
            if not file_spec.header_path:
                continue
            target = tmp / file_spec.header_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_header(bundle, file_spec), encoding="utf-8")

        for file_spec in bundle.file_specs_by_trace.values():
            if not file_spec.header_path:
                continue
            check_path = tmp / f"check_{file_spec.header_path.replace('/', '_')}.c"
            check_path.write_text(f'#include "{file_spec.header_path}"\n', encoding="utf-8")
            command = [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-pedantic",
                    "-D_POSIX_C_SOURCE=200809L",
                    "-I.",
                    "-c",
                    str(check_path.relative_to(tmp)),
                    "-o",
                    "/dev/null",
                ]
            result = subprocess.run(
                command,
                cwd=tmp,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                message = detail[0] if detail else "rendered header did not compile"
                bundle.diagnostics.append(
                    Diagnostic(
                        "error",
                        "rendered_header_compile_error",
                        f"{message}; command={' '.join(command)}; include_path={tmp}",
                        file_spec.header_path,
                    )
                )


def _validate_uniqueness(bundle: SpecBundle) -> None:
    seen_paths: dict[str, str] = {}
    seen_headers: dict[str, str] = {}
    seen_sources: dict[str, str] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        for path, seen, code in (
            (file_spec.header_path, seen_headers, "layout_duplicate_header_path"),
            (file_spec.source_path, seen_sources, "layout_duplicate_source_path"),
        ):
            if not path:
                continue
            scoped_owner = seen.get(path)
            if scoped_owner and scoped_owner != file_spec.trace_id:
                bundle.diagnostics.append(Diagnostic("error", code, f"Path '{path}' is claimed by both '{scoped_owner}' and '{file_spec.trace_id}'", str(file_spec.spec_path)))
            else:
                seen[path] = file_spec.trace_id
            owner = seen_paths.get(path)
            if owner and owner != file_spec.trace_id:
                bundle.diagnostics.append(Diagnostic("error", "duplicate_file_path", f"Path '{path}' is claimed by both '{owner}' and '{file_spec.trace_id}'", str(file_spec.spec_path)))
            else:
                seen_paths[path] = file_spec.trace_id


def discover_module_spec(spec_root: str | Path) -> Path:
    root = Path(spec_root)
    candidates: list[Path] = []
    for path in sorted(root.rglob("*_spec.json")):
        raw = read_json(path)
        if raw.get("KIND") == "PROTOCOL_MODULE_SPEC":
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No PROTOCOL_MODULE_SPEC found under spec root '{root}'")
    if len(candidates) > 1:
        joined = ", ".join(str(path) for path in candidates)
        raise ValueError(
            f"Expected exactly one PROTOCOL_MODULE_SPEC under spec root '{root}', "
            f"found {len(candidates)}: {joined}"
        )
    return candidates[0]


def load_spec_bundle_from_root(spec_root: str | Path, *, validate_rendered_headers: bool = True) -> SpecBundle:
    root = Path(spec_root)
    return load_spec_bundle(discover_module_spec(root), root, validate_rendered_headers=validate_rendered_headers)


def load_spec_bundle(module_spec_path: str | Path, spec_root: str | Path, *, validate_rendered_headers: bool = True) -> SpecBundle:
    module_spec = Path(module_spec_path)
    root = Path(spec_root)
    diagnostics: list[Diagnostic] = []

    module_raw = read_json(module_spec)
    protocol, modules, generation_order, consistency_rules = _parse_module_spec(module_spec, module_raw, diagnostics)
    modules_in_order = _modules_in_generation_order(modules, generation_order, module_spec, diagnostics)

    file_specs_by_trace: dict[str, FileSpec] = {}
    file_specs_by_header_path: dict[str, FileSpec] = {}
    file_specs_by_source_path: dict[str, FileSpec] = {}
    function_specs_by_trace: dict[str, FunctionSpec] = {}

    for path in sorted(root.rglob("*_spec.json")):
        if path.resolve() == module_spec.resolve():
            continue
        raw = read_json(path)
        kind = raw.get("KIND")
        if kind == "FILE_SPEC":
            file_spec = _parse_file_spec(path, raw, diagnostics)
            if file_spec.trace_id in file_specs_by_trace:
                diagnostics.append(Diagnostic("error", "duplicate_trace_id", f"Duplicate file spec TRACE_ID '{file_spec.trace_id}'", str(path)))
                continue
            file_specs_by_trace[file_spec.trace_id] = file_spec
            if file_spec.header_path:
                file_specs_by_header_path[file_spec.header_path] = file_spec
            if file_spec.source_path:
                file_specs_by_source_path[file_spec.source_path] = file_spec
        elif kind == "FUNCTION_SPEC":
            function_spec = _parse_function_spec(path, raw, diagnostics)
            if function_spec.trace_id in function_specs_by_trace:
                diagnostics.append(Diagnostic("error", "duplicate_trace_id", f"Duplicate function spec TRACE_ID '{function_spec.trace_id}'", str(path)))
                continue
            function_specs_by_trace[function_spec.trace_id] = function_spec
        else:
            diagnostics.append(Diagnostic("warning", "unknown_kind", f"Skipping unsupported spec kind '{kind}'", str(path)))

    bundle = SpecBundle(
        protocol=protocol,
        module_spec_path=module_spec,
        spec_root=root,
        generation_order=generation_order,
        modules_in_order=modules_in_order,
        file_specs_by_trace=file_specs_by_trace,
        file_specs_by_header_path=file_specs_by_header_path,
        file_specs_by_source_path=file_specs_by_source_path,
        function_specs_by_trace=function_specs_by_trace,
        consistency_rules=consistency_rules,
        diagnostics=diagnostics,
    )
    _validate_uniqueness(bundle)
    _validate_module_order(bundle)
    _validate_module_file_coverage(bundle)
    _validate_file_specs(bundle)
    _validate_function_specs(bundle)
    _validate_machine_constraints(bundle)
    if validate_rendered_headers:
        _validate_rendered_headers_compile(bundle)
    return bundle


def module_files(bundle: SpecBundle, module_name: str) -> list[str]:
    for module in bundle.modules_in_order:
        if module.name == module_name:
            return list(module.files)
    return []


def canonical_signature_for_source(bundle: SpecBundle, source_item: SourceInterface) -> str:
    linked = bundle.function_specs_by_trace.get(source_item.trace_id)
    if linked and linked.signature.raw:
        return linked.signature.raw
    return source_item.signature


def canonical_signature_for_header(bundle: SpecBundle, file_spec: FileSpec, header_item: HeaderInterface) -> str:
    matching_source = next((item for item in file_spec.source_interfaces if item.name == header_item.name), None)
    if matching_source is None:
        return header_item.signature
    linked = bundle.function_specs_by_trace.get(matching_source.trace_id)
    if linked and linked.signature.raw:
        return linked.signature.raw
    return header_item.signature


def function_specs_for_file(bundle: SpecBundle, file_spec: FileSpec) -> list[FunctionSpec]:
    specs: list[FunctionSpec] = []
    for item in file_spec.source_interfaces:
        linked = bundle.function_specs_by_trace.get(item.trace_id)
        if linked is not None:
            specs.append(linked)
    return specs
