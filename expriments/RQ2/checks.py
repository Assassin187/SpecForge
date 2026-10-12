"""RQ2 gates. V0/V1 never obtain native cross-view diagnostics."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from specforge import specs as native
from specforge.documents import artifact_errors, digest, read_json


def type_description_errors(description: dict, name: str) -> list[str]:
    required = {"STRUCT": "FIELDS", "UNION": "VARIANTS", "ENUM": "ENUM_VALUES",
                "CALLBACK": "CALLBACK_SIGNATURE", "ALIAS": "ALIAS_OF"}.get(description["TYPE_KIND"])
    errors = []
    if required and required not in description:
        errors.append(f"Incomplete public TYPE_SPEC: {name}: missing {required}")
    elif required in ("CALLBACK_SIGNATURE", "ALIAS_OF") and not description[required].strip():
        errors.append(f"Incomplete public TYPE_SPEC: {name}: empty {required}")
    for member in description.get("FIELDS", []) + description.get("VARIANTS", []):
        if "TYPE_SPEC" in member:
            errors += type_description_errors(member["TYPE_SPEC"], name + "." + member["NAME"])
        elif not member.get("TYPE", "").strip():
            errors.append(f"Missing public member type: {name}.{member['NAME']}")
    return errors


def resolved_reference(items: dict, reference: str):
    # Preserve the native resolver, with RFC 6901 array-index and escape syntax constraints.
    path, separator, pointer = reference.partition("#")
    if not separator or path not in items or (pointer and not pointer.startswith("/")) or re.search(r"~(?![01])", pointer):
        raise ValueError(reference)
    node = items[path]
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list) and not re.fullmatch(r"0|[1-9][0-9]*", key):
            raise ValueError(reference)
        node = node[int(key)] if isinstance(node, list) else node[key]
    return native.pointer(items, reference)


def artifact_inventory(directory: Path) -> dict[str, str]:
    items, errors = native.load_specs(directory)
    if errors:
        raise ValueError(errors)
    paths = set(items) | {"scope.json", "traceability.json"}
    paths |= {p.relative_to(directory).as_posix() for p in (directory / "abi").rglob("*") if p.is_file()}
    return {p: digest(directory / p) for p in sorted(paths)}


def basic(directory: Path, *, facts: dict, scope: dict, design: bool = False, review: bool = False) -> dict:
    items, errors = native.load_specs(directory)
    if errors:
        return {"passed": False, "errors": errors}
    modules = [(p, s) for p, s in items.items() if s["KIND"] == "PROTOCOL_MODULE_SPEC"]
    files = [(p, s) for p, s in items.items() if s["KIND"] == "FILE_SPEC"]
    functions = [(p, s) for p, s in items.items() if s["KIND"] == "FUNCTION_SPEC"]
    if len(modules) != 1 or modules[0][0] != "module_spec.json" or not files:
        errors.append("Exactly one module_spec.json and at least one FileSpec are required")
    declared_paths = set()
    for path, file in files:
        source = file["SOURCE"]["PATH"]
        declared_paths.add(source)
        if not native.project_path(source) or not source.endswith(".c"):
            errors.append(f"Unsafe source path: {source}")
        for interface in file["SOURCE"]["INTERFACE"]:
            if "FUNCTION_TYPE" not in interface:
                errors.append(f"{path}: SOURCE.INTERFACE {interface['TRACE_ID']} needs FUNCTION_TYPE for native behavior-job navigation")
        header = file.get("HEADER")
        if header:
            name = header["PATH"]
            declared_paths.add(name)
            if not native.project_path(name) or not name.endswith(".h"):
                errors.append(f"Unsafe public header path: {name}")
            else:
                artifact = directory / native.header_artifact(directory, name)
                if not artifact.is_file() or not artifact.read_text().strip():
                    errors.append(f"Missing nonempty public header: {name}")
            for data in header["DATA"]:
                if data["KIND"] == "TYPE" and data["VISIBILITY"] == "PUBLIC":
                    if not data.get("TYPE_SPEC"):
                        errors.append(f"Missing public TYPE_SPEC: {data['NAME']}")
                    else:
                        errors += type_description_errors(data["TYPE_SPEC"], data["NAME"])
    # Presence only: assignment multiplicity, unassigned files and symbol owners belong to V2.
    for _, module in modules:
        for entry in module["MODULES"]:
            for path in entry["FILES"]:
                if not native.project_path(path) or path not in declared_paths:
                    errors.append(f"Missing FileSpec for declared path: {path}")
    if design:
        return {"passed": not errors, "errors": errors}
    try:
        saved_scope = read_json(directory / "scope.json")
        errors += artifact_errors("scope", saved_scope)
        if saved_scope != scope:
            errors.append("Scope differs from frozen facts-stage scope")
        if not native.project_path(saved_scope["runtime_contract"]["binary_name"]):
            errors.append("Runtime binary must be project-relative")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"Missing/invalid scope: {exc}")
    by_id = {s["TRACE_ID"]: s for _, s in functions}
    for _, file in files:
        for interface in file["SOURCE"]["INTERFACE"]:
            if interface["TRACE_ID"] not in by_id:
                errors.append(f"Missing function Spec: {interface['TRACE_ID']}")
    entries = [s for _, s in functions if s["FUNCTION_TYPE"] == "ENTRYPOINT"]
    if len(entries) != 1 or entries[0]["SIGNATURE"]["NAME"] != "main":
        errors.append("Exactly one main ENTRYPOINT Spec is required")
    for path, function in functions:
        action = (function.get("LOGIC") or function.get("EVENT") or {}).get("ACTION", "")
        if not action.strip():
            errors.append(f"Missing function behavior: {function['TRACE_ID']}")
    for path, spec in items.items():
        for contract in spec.get("CALL_CONTRACTS", []):
            if "PARAMS" not in contract or any(not contract.get(k) for k in ("RETURN", "OWNERSHIP", "FAILURE")):
                errors.append(f"Incomplete existing call contract in {path}: {contract['NAME']}")
    if not any(s.get("WIRE_MAPPING") for _, s in functions):
        errors.append("Protocol Specs require explicit WIRE_MAPPING")
    if not any(s.get("TEST_VECTORS") for _, s in functions):
        errors.append("Protocol Specs require TEST_VECTORS")
    requirement_ids = {r["id"] for r in scope["requirements"]}
    fact_ids = {f["id"] for f in facts["facts"]}
    known_traces = set(by_id) | {s["FILE"]["TRACE_ID"] for _, s in files} | requirement_ids
    vectors = native.test_vector_refs(items)
    for ref, vector in vectors.items():
        for trace in vector.get("TRACE_REFS", []):
            if trace not in known_traces:
                errors.append(f"Unknown test-vector TRACE_REFS: {ref}: {trace}")
    try:
        trace = read_json(directory / "traceability.json")
        syntax = artifact_errors("traceability", trace)
        errors += syntax
        if not syntax:
            mapped = [r["requirement_id"] for r in trace["requirements"]]
            if set(mapped) != requirement_ids or len(mapped) != len(set(mapped)):
                errors.append("Traceability must cover every scope requirement exactly once")
            for row in trace["requirements"]:
                if set(row["fact_ids"]) - fact_ids:
                    errors.append(f"Unknown facts: {row['requirement_id']}")
                if set(row["test_ids"]) - set(vectors):
                    errors.append(f"Unknown canonical test-vector references: {row['requirement_id']}")
                if review and not row.get("semantic_review", "").strip():
                    errors.append(f"Missing independent semantic review: {row['requirement_id']}")
            flow_ids = [r["id"] for r in trace["processing_chain"]]
            if len(flow_ids) != len(set(flow_ids)):
                errors.append("Duplicate processing-chain ID")
            refs = [ref for row in trace["requirements"] + trace["processing_chain"] for ref in row["spec_refs"]]
            refs += [r["spec_ref"] for r in trace["engineering_decisions"]]
            for ref in refs:
                try:
                    if not resolved_reference(items, ref):
                        errors.append(f"Empty referenced Spec content: {ref}")
                except (ValueError, KeyError, IndexError, TypeError):
                    errors.append(f"Unresolved JSON Pointer: {ref}")
    except (OSError, ValueError) as exc:
        errors.append(f"Missing/invalid traceability: {exc}")
    return {"passed": not errors, "errors": errors,
            "counts": {"modules": len(modules), "files": len(files), "functions": len(functions)}}


def headers(directory: Path) -> dict:
    items, errors = native.load_specs(directory)
    if errors:
        return {"passed": False, "errors": errors, "probes": []}
    paths = [s["HEADER"]["PATH"] for s in items.values() if s["KIND"] == "FILE_SPEC" and "HEADER" in s]
    names = [str(PurePosixPath(native.header_artifact(directory, p)).relative_to("abi")) for p in paths]
    include = directory / "abi"
    roots = sorted({include / ancestor for name in names for ancestor in PurePosixPath(name).parents
                    if ancestor != PurePosixPath(".")})
    probes = []
    for label, included in [*zip(paths, ([n] for n in names)), ("all", names)]:
        if not included:
            continue
        source = "\n".join(f'#include "{n}"' for n in included) + "\n"
        probe = native.compile_probe(source, include, roots)
        probes.append({"header": label, **probe})
        if not probe["passed"]:
            errors.append(f"Header compilation failed ({label}): {probe['output']}")
    return {"passed": not errors, "errors": errors, "probes": probes}


def check(directory: Path, policy: str, **kwargs) -> dict:
    if policy not in ("basic", "V0", "V1", "V2", "full"):
        raise ValueError(f"Unknown check policy: {policy}")
    result = basic(directory, **kwargs)
    if not result["passed"]:
        return result
    if policy in ("V1", "V2", "full"):
        result["headers"] = headers(directory)
        result["errors"] += result["headers"]["errors"]
    if policy in ("V2", "full"):
        design = kwargs.get("design", False)
        result["native"] = native.validate(directory, design=design, published=False,
                                            facts=None if design else kwargs["facts"],
                                            scope=None if design else kwargs["scope"])
        result["errors"] += result["native"]["errors"]
    result["passed"] = not result["errors"]
    return result


def package_integrity(directory: Path) -> dict:
    """Publication/load integrity only; deliberately no engineering relation checks."""
    bundle = read_json(directory / "bundle.json")
    errors = artifact_errors("bundle", bundle)
    items, syntax = native.load_specs(directory)
    errors += syntax
    if errors:
        return {"passed": False, "errors": errors}
    expected_files = {(p, s["FILE"]["TRACE_ID"], s["SOURCE"]["PATH"]) for p, s in items.items() if s["KIND"] == "FILE_SPEC"}
    expected_functions = {(p, s["TRACE_ID"], s["SIGNATURE"]["NAME"]) for p, s in items.items() if s["KIND"] == "FUNCTION_SPEC"}
    expected_headers = {(s["HEADER"]["PATH"], native.header_artifact(directory, s["HEADER"]["PATH"]))
                        for s in items.values() if s["KIND"] == "FILE_SPEC" and "HEADER" in s}
    if {(r["spec"], r["trace_id"], r["path"]) for r in bundle["files"]} != expected_files:
        errors.append("Bundle source index differs from Specs")
    if {(r["spec"], r["trace_id"], r["name"]) for r in bundle["functions"]} != expected_functions:
        errors.append("Bundle function index differs from Specs")
    if {(r["path"], r["artifact"]) for r in bundle["headers"]} != expected_headers:
        errors.append("Bundle header index differs from Specs")
    for row in bundle["files"]:
        if not native.project_path(row["path"]) or not row["path"].endswith(".c"):
            errors.append(f"Unsafe published source path: {row['path']}")
    for row in bundle["headers"]:
        if not native.project_path(row["path"]) or not row["path"].endswith(".h"):
            errors.append(f"Unsafe published header path: {row['path']}")
    expected = set(items) | {a for _, a in expected_headers} | {"scope.json", "traceability.json", "SUMMARY.md"}
    if set(bundle["hashes"]) != expected:
        errors.append("Bundle hash inventory differs from published artifacts")
    for path, sha in bundle["hashes"].items():
        actual = (directory / path).resolve()
        if not actual.is_relative_to(directory.resolve()) or not actual.is_file() or digest(actual) != sha:
            errors.append(f"Bundle hash/path mismatch: {path}")
    scope = read_json(directory / "scope.json")
    if any(bundle[k] != scope[k] for k in ("protocol", "version", "role", "runtime_contract")):
        errors.append("Bundle identity differs from scope")
    return {"passed": not errors, "errors": errors}


def code_complete(project: Path, bundle: Path | None) -> dict:
    """Initial Code metric measures an attempted delivery, not development gate success."""
    if bundle is None:
        return {"passed": False, "errors": ["No actually published Specs"]}
    manifest = read_json(bundle / "bundle.json")
    try:
        delivery = read_json(project / "delivery.json")
        errors = artifact_errors("delivery", delivery)
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing/invalid delivery.json: {exc}"]}
    if errors:
        return {"passed": False, "errors": errors}
    listed = set(delivery["files"])
    required = {r["path"] for r in manifest["files"] + manifest["headers"]} | {"Makefile", "README.md"}
    required |= {r["path"] for r in delivery["tests"]}
    for path in required | listed:
        actual = (project / path).resolve()
        if not native.project_path(path) or not actual.is_relative_to(project.resolve()) or not actual.is_file() or not actual.stat().st_size:
            errors.append(f"Missing nonempty delivery file: {path}")
        elif path in required and path not in listed:
            errors.append(f"Required file absent from delivery inventory: {path}")
    return {"passed": not errors, "errors": errors}
