"""Three-layer Spec checks, compiler-checked ABI and bundle publication."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath

import jsonschema

from .documents import ROOT, artifact_errors, digest, read_json, save_json

SCHEMAS = {"PROTOCOL_MODULE_SPEC": "module_spec_schema.json", "FILE_SPEC": "file_spec_schema.json",
           "FUNCTION_SPEC": "function_spec_schema.json"}


def project_path(path: str) -> bool:
    p = PurePosixPath(path)
    return not p.is_absolute() and ".." not in p.parts and bool(p.parts) and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", path))


def header_artifact(directory: Path, path: str) -> str:
    manifest = directory / "bundle.json"
    if manifest.is_file():
        for row in read_json(manifest)["headers"]:
            if row["path"] == path:
                artifact = row["artifact"]
                if not project_path(artifact) or not artifact.startswith("abi/"):
                    raise ValueError(f"Invalid header artifact: {artifact}")
                return artifact
    return "abi/" + path


def test_vector_refs(items: dict[str, dict]) -> dict[str, dict]:
    return {f"{path}#/TEST_VECTORS/{i}": vector for path, spec in items.items()
            for i, vector in enumerate(spec.get("TEST_VECTORS", []))}


def load_specs(directory: Path) -> tuple[dict[str, dict], list[str]]:
    items, errors = {}, []
    for path in sorted(directory.rglob("*.json")):
        if path.name not in ("module_spec.json",) and not path.relative_to(directory).parts[0] in ("files", "functions"):
            continue
        relative = path.relative_to(directory).as_posix()
        try:
            value = read_json(path)
            kind = value.get("KIND")
            if kind not in SCHEMAS:
                errors.append(f"Unknown Spec kind in {relative}")
                continue
            validator = jsonschema.Draft202012Validator(read_json(ROOT / "schemas" / SCHEMAS[kind]))
            errors += [f"{relative}:{'/'.join(map(str, e.absolute_path))}: {e.message}"
                       for e in validator.iter_errors(value)]
            items[relative] = value
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
    return items, errors


def pointer(items: dict[str, dict], reference: str):
    path, separator, ptr = reference.partition("#")
    if not separator or path not in items or (ptr and not ptr.startswith("/")):
        raise ValueError(f"Invalid Spec reference: {reference}")
    node = items[path]
    for part in ptr.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def compile_probe(text: str, include: Path, extra_includes: list[Path] = ()) -> dict:
    result = subprocess.run(["gcc", "-std=c99", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
                             "-fsyntax-only", "-x", "c", "-", "-I", str(include),
                             *[arg for p in extra_includes for arg in ("-I", str(p))]],
                            input=text, text=True, capture_output=True, timeout=20)
    return {"passed": result.returncode == 0, "output": result.stdout + result.stderr, "probe": text}


def abi_checks(directory: Path, files: list[tuple[str, dict]], functions: list[tuple[str, dict]]) -> dict:
    include = directory / "abi"
    probes, errors = [], []
    headers = []
    include_dirs = sorted({include / ancestor
                           for _, s in files if "HEADER" in s
                           for ancestor in PurePosixPath(header_artifact(directory, s["HEADER"]["PATH"])).relative_to("abi").parents
                           if ancestor != PurePosixPath(".")})
    for relative, spec in files:
        header = spec.get("HEADER")
        if not header:
            continue
        if not project_path(header["PATH"]) or not header["PATH"].endswith(".h"):
            errors.append(f"{relative}: header must be a project-relative .h path")
            continue
        name = str(PurePosixPath(header_artifact(directory, header["PATH"])).relative_to("abi"))
        headers.append(name)
        if not (include / name).is_file():
            errors.append(f"Missing public header: abi/{name}")
            continue
        declarations = [f'#include "{name}"']
        structured_declarations = []
        for interface in header["INTERFACE"]:
            symbol = interface["NAME"]
            # Check presence before appending the expected declaration.
            declarations += [f"void sf_presence_{symbol}(void) {{ (void)&{symbol}; }}",
                             interface["SIGNATURE"].rstrip(";") + ";"]
            for _, function in functions:
                signature = function["SIGNATURE"]
                if signature["NAME"] == symbol:
                    params = ", ".join(p["TYPE"].replace("(*)", f"(*{p['NAME']})") if "(*)" in p["TYPE"]
                                       else p["TYPE"] + " " + p["NAME"] for p in signature["PARAMS"]) or "void"
                    declaration = f"{signature['RETURN']} {symbol}({params});"
                    declarations.append(declaration)
                    structured_declarations.append(declaration)
        serial = [0]

        def assert_type(expr: str, expected: str):
            serial[0] += 1
            declarations.append(f"typedef char sf_type_{serial[0]}[__builtin_types_compatible_p(__typeof__({expr}), {expected}) ? 1 : -1];")

        def members(expr: str, type_spec: dict):
            for member in type_spec.get("FIELDS", []) + type_spec.get("VARIANTS", []):
                nested_expr = expr + "." + member["NAME"]
                if "TYPE_SPEC" in member:
                    members(nested_expr, member["TYPE_SPEC"])
                else:
                    expected = member["TYPE"] + (f"[{member['ARRAY_LEN']}]" if member.get("ARRAY_LEN") else "")
                    assert_type(nested_expr, expected)

        for data in header["DATA"]:
            if data["KIND"] != "TYPE" or data["VISIBILITY"] != "PUBLIC":
                continue
            name_type, ts = data["NAME"], data.get("TYPE_SPEC")
            if not ts:
                errors.append(f"Missing public TYPE_SPEC: {name_type}")
                continue
            serial[0] += 1
            declarations.append(f"typedef char sf_exists_{serial[0]}[sizeof({name_type}*) > 0 ? 1 : -1];")
            if ts["TYPE_KIND"] in ("STRUCT", "UNION"):
                members(f"(*(({name_type}*)0))", ts)
            if ts["TYPE_KIND"] == "ENUM":
                for enum in ts.get("ENUM_VALUES", []):
                    serial[0] += 1
                    value = enum.get("VALUE")
                    condition = f"{enum['NAME']} == ({value})" if value is not None else f"sizeof({enum['NAME']}) > 0"
                    declarations.append(f"typedef char sf_enum_{serial[0]}[({condition}) ? 1 : -1];")
            if ts["TYPE_KIND"] == "CALLBACK":
                signature = ts["CALLBACK_SIGNATURE"]
                expected_name = "sf_expected_" + name_type
                if name_type not in signature:
                    errors.append(f"CALLBACK_SIGNATURE must name its typedef: {name_type}")
                else:
                    declarations.append("typedef " + signature.replace(name_type, expected_name, 1).rstrip(";") + ";")
                    assert_type(f"(({name_type})0)", expected_name)
            if ts["TYPE_KIND"] == "ALIAS":
                assert_type(f"(*(({name_type}*)0))", ts["ALIAS_OF"])
        probe = compile_probe("\n".join(declarations) + "\n", include, include_dirs)
        probes.append({"header": header["PATH"], **probe})
        if not probe["passed"]:
            errors.append(f"ABI mismatch in {header['PATH']}: {probe['output'][:2000]}"
                          + "\nStructured C declarations from FUNCTION_SPEC SIGNATURE.RETURN/PARAMS (RETURN must be a C type):\n"
                          + "\n".join(structured_declarations)[:1800])
    if headers:
        combined = compile_probe("\n".join(f'#include "{h}"' for h in headers) + "\n", include, include_dirs)
        probes.append({"header": "all", **combined})
        if not combined["passed"]:
            errors.append("Combined headers conflict: " + combined["output"][:2500])
    return {"passed": not errors, "errors": errors, "probes": probes}


def validate(directory: Path, *, design: bool = False, facts: dict | None = None,
             scope: dict | None = None, published: bool = True) -> dict:
    items, errors = load_specs(directory)
    if errors:
        return {"passed": False, "errors": errors}
    modules = [(p, s) for p, s in items.items() if s["KIND"] == "PROTOCOL_MODULE_SPEC"]
    files = [(p, s) for p, s in items.items() if s["KIND"] == "FILE_SPEC"]
    functions = [(p, s) for p, s in items.items() if s["KIND"] == "FUNCTION_SPEC"]
    if len(modules) != 1 or not files:
        return {"passed": False, "errors": ["Exactly one module Spec and at least one file Spec are required"]}
    if modules[0][0] != "module_spec.json":
        errors.append("The module Spec must be module_spec.json")
    module = modules[0][1]
    source_paths = [s["SOURCE"]["PATH"] for _, s in files]
    header_paths = [s["HEADER"]["PATH"] for _, s in files if "HEADER" in s]
    file_ids = [s["FILE"]["TRACE_ID"] for _, s in files]
    function_ids = [s["TRACE_ID"] for _, s in functions]
    for label, values in (("source", source_paths), ("header", header_paths), ("file ID", file_ids), ("function ID", function_ids)):
        errors += [f"Duplicate {label}: {name}" for name, n in Counter(values).items() if n > 1]
    for path in source_paths:
        if not project_path(path) or not path.endswith(".c"):
            errors.append(f"Source must be a project-relative .c path: {path}")
    module_names = [m["NAME"] for m in module["MODULES"]]
    if len(module_names) != len(set(module_names)):
        errors.append("Duplicate module names")
    if set(module["GENERATION_ORDER"]) != set(module_names):
        errors.append("GENERATION_ORDER must name all modules")
    all_paths = set(source_paths + header_paths)
    assigned = [p for m in module["MODULES"] for p in m["FILES"]]
    if set(assigned) != all_paths or len(assigned) != len(set(assigned)):
        errors.append("Module FILES must own each source/header exactly once")
    for m in module["MODULES"]:
        if set(m["DEPENDENCIES"]) - set(module_names):
            errors.append(f"Unknown module dependency: {m['NAME']}")
    publics, types = {}, {}
    for path, file in files:
        for interface in file.get("HEADER", {}).get("INTERFACE", []):
            name = interface["NAME"]
            if name in publics:
                errors.append(f"Multiple public function owners: {name}")
            publics[name] = (path, interface)
        for data in file.get("HEADER", {}).get("DATA", []):
            if data["KIND"] == "TYPE" and data["VISIBILITY"] == "PUBLIC":
                if data["NAME"] in types:
                    errors.append(f"Multiple public type owners: {data['NAME']}")
                types[data["NAME"]] = path
        for section in (file.get("HEADER", {}), file["SOURCE"]):
            for dep in section.get("DEPENDENCY", []):
                if dep not in all_paths:
                    errors.append(f"Unknown file dependency in {path}: {dep}")
    ownership, by_id = {}, {s["TRACE_ID"]: (p, s) for p, s in functions}
    for path, file in files:
        interface_names = [i["NAME"] for i in file["SOURCE"]["INTERFACE"]]
        if len(interface_names) != len(set(interface_names)):
            errors.append(f"Duplicate source function names in {path}")
        interfaces = {i["NAME"]: i for i in file["SOURCE"]["INTERFACE"]}
        for interface in interfaces.values():
            if interface["VISIBILITY"] == "public" and interface["NAME"] != "main" and interface["NAME"] not in publics:
                errors.append(f"Public source function lacks a public header declaration: {interface['NAME']}")
        for name, (owner, declaration) in publics.items():
            if owner == path and (name not in interfaces or compact(declaration["SIGNATURE"]) != compact(interfaces[name]["SIGNATURE"])):
                errors.append(f"Header/source signature mismatch: {name}")
        for interface in interfaces.values():
            trace = interface["TRACE_ID"]
            if trace in ownership:
                errors.append(f"Multiple function owners: {trace}")
            ownership[trace] = (path, file, interface)
            if design:
                continue
            if trace not in by_id:
                errors.append(f"Missing function Spec: {trace}")
                continue
            func = by_id[trace][1]
            if interface["NAME"] != func["SIGNATURE"]["NAME"] or compact(interface["SIGNATURE"]) != compact(func["SIGNATURE"]["RAW"]):
                errors.append(f"Function signature mismatch: {trace}")
    if not design:
        reference_scope = scope
        if reference_scope is None and (directory / "scope.json").is_file():
            reference_scope = read_json(directory / "scope.json")
        requirement_refs = {r["id"] for r in (reference_scope or {}).get("requirements", [])}
        entries = [f for _, f in functions if f["FUNCTION_TYPE"] == "ENTRYPOINT"]
        if len(entries) != 1 or entries[0]["SIGNATURE"]["NAME"] != "main":
            errors.append("Exactly one main ENTRYPOINT Spec is required")
        for trace, (path, function) in by_id.items():
            if trace not in ownership:
                errors.append(f"Function Spec has no source owner: {trace}")
                continue
            owner = ownership[trace][1]
            local_names = {i["NAME"] for i in owner["SOURCE"]["INTERFACE"]}
            external = []
            for dep in function["RELY"]["FUNC"]:
                if dep["KIND"] == "TYPE_REF" and dep["NAME"] in types:
                    continue
                if dep["NAME"] not in local_names and dep["NAME"] not in publics:
                    external.append(dep["NAME"])
                if facts is not None and dep["NAME"] in publics and publics[dep["NAME"]][0] != ownership[trace][0]:
                    contract = next((c for c in function.get("CALL_CONTRACTS", []) if c["NAME"] == dep["NAME"]), None)
                    if not contract or any(not contract.get(k) for k in ("RETURN", "OWNERSHIP", "FAILURE")) or "PARAMS" not in contract:
                        errors.append(f"Missing cross-file call contract in {trace}: {dep['NAME']}")
                    elif compact(contract["SIGNATURE"]) != compact(publics[dep["NAME"]][1]["SIGNATURE"]):
                        errors.append(f"Call contract signature mismatch in {trace}: {dep['NAME']}")
            if external:
                system_headers = sorted(set(owner["SOURCE"].get("SYSTEM_DEPENDENCY", []) + owner.get("HEADER", {}).get("SYSTEM_DEPENDENCY", [])))
                declarations = [f"#include <{h.strip('<>')}>" for h in system_headers]
                declarations += [f"void sf_external_{i}(void) {{ (void)&{name}; }}" for i, name in enumerate(external)]
                probe = compile_probe("\n".join(declarations), directory / "abi")
                if not probe["passed"]:
                    errors.append(f"Unknown project function or undeclared external dependency in {trace}: {external}: {probe['output'][:2000]}")
            action = (function.get("LOGIC") or function.get("EVENT") or {}).get("ACTION", "")
            if not action.strip():
                errors.append(f"Missing function behavior: {trace}")
        for path, spec in items.items():
            for vector in spec.get("TEST_VECTORS", []):
                for trace in vector.get("TRACE_REFS", []):
                    if trace not in by_id and trace not in file_ids and trace not in requirement_refs:
                        errors.append(f"Unknown test-vector reference in {path}: {trace}")
    abi = abi_checks(directory, files, functions)
    errors += abi["errors"]
    if not design and facts is not None and scope is not None:
        if not any(f.get("WIRE_MAPPING") for _, f in functions):
            errors.append("Automatic protocol Specs require explicit WIRE_MAPPING")
        if not any(f.get("TEST_VECTORS") for _, f in functions):
            errors.append("Automatic protocol Specs require TEST_VECTORS")
        try:
            trace = read_json(directory / "traceability.json")
            trace_errors = artifact_errors("traceability", trace)
            errors += trace_errors
            if not trace_errors:
                fact_ids = {f["id"] for f in facts["facts"]}
                expected_reqs = {r["id"] for r in scope["requirements"]}
                mapped_reqs = [r["requirement_id"] for r in trace["requirements"]]
                if set(mapped_reqs) != expected_reqs or len(mapped_reqs) != len(set(mapped_reqs)):
                    errors.append(f"Traceability must cover every scope requirement exactly once: expected {sorted(expected_reqs)}, got {mapped_reqs}.")
                vectors = test_vector_refs(items)
                for row in trace["requirements"]:
                    if set(row["fact_ids"]) - fact_ids:
                        errors.append(f"Unknown facts in requirement {row['requirement_id']}")
                    if set(row["test_ids"]) - set(vectors):
                        errors.append(f"Unknown Spec test-vector references in {row['requirement_id']}")
                flow_ids = [r["id"] for r in trace["processing_chain"]]
                if len(flow_ids) != len(set(flow_ids)):
                    errors.append("Duplicate processing-chain ID")
                refs = [ref for row in trace["requirements"] + trace["processing_chain"] for ref in row["spec_refs"]]
                refs += [r["spec_ref"] for r in trace["engineering_decisions"]]
                for ref in refs:
                    try:
                        if not pointer(items, ref):
                            errors.append(f"Empty referenced Spec content: {ref}")
                    except (ValueError, KeyError, IndexError, TypeError):
                        errors.append(f"Unresolved JSON Pointer: {ref}")
        except (OSError, ValueError) as exc:
            errors.append(f"Traceability missing/invalid: {exc}")
    if published and (directory / "bundle.json").is_file():
        bundle = read_json(directory / "bundle.json")
        bundle_errors = artifact_errors("bundle", bundle)
        errors += bundle_errors
        if bundle_errors:
            return {"passed": False, "errors": errors, "abi": abi}
        expected_files = {(p, s["FILE"]["TRACE_ID"], s["SOURCE"]["PATH"]) for p, s in files}
        expected_functions = {(p, s["TRACE_ID"], s["SIGNATURE"]["NAME"]) for p, s in functions}
        expected_headers = {(s["HEADER"]["PATH"], header_artifact(directory, s["HEADER"]["PATH"]))
                            for _, s in files if "HEADER" in s}
        if {(f["spec"], f["trace_id"], f["path"]) for f in bundle["files"]} != expected_files:
            errors.append("Bundle source index differs from validated Specs")
        if {(f["spec"], f["trace_id"], f["name"]) for f in bundle["functions"]} != expected_functions:
            errors.append("Bundle function index differs from validated Specs")
        if {(h["path"], h["artifact"]) for h in bundle["headers"]} != expected_headers:
            errors.append("Bundle header index differs from validated Specs")
        expected_hashes = set(items) | {h[1] for h in expected_headers} | {"scope.json", "traceability.json", "SUMMARY.md"}
        if expected_hashes != set(bundle["hashes"]):
            errors.append("Bundle hash inventory must contain exactly the published Spec artifacts")
        for relative, expected in bundle["hashes"].items():
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file() or digest(path) != expected:
                errors.append(f"Published bundle hash mismatch: {relative}")
        approved_scope = read_json(directory / "scope.json")
        if any(bundle[k] != approved_scope[k] for k in ("protocol", "version", "role", "runtime_contract")):
            errors.append("Bundle identity or runtime contract differs from scope")
    return {"passed": not errors, "errors": errors, "counts": {"modules": len(modules), "files": len(files), "functions": len(functions)},
            "abi": abi}


def compact(signature: str) -> str:
    return re.sub(r"\s+", "", signature).rstrip(";")


def publish(directory: Path, revision: int, origin: str = "automatic") -> dict:
    items, errors = load_specs(directory)
    if errors:
        raise ValueError("Cannot publish invalid schemas")
    files = [{"spec": p, "trace_id": s["FILE"]["TRACE_ID"], "path": s["SOURCE"]["PATH"]}
             for p, s in items.items() if s["KIND"] == "FILE_SPEC"]
    headers = [{"path": s["HEADER"]["PATH"], "artifact": header_artifact(directory, s["HEADER"]["PATH"])}
               for s in items.values() if s["KIND"] == "FILE_SPEC" and "HEADER" in s]
    functions = [{"spec": p, "trace_id": s["TRACE_ID"], "name": s["SIGNATURE"]["NAME"]}
                 for p, s in items.items() if s["KIND"] == "FUNCTION_SPEC"]
    module_path = next(p for p, s in items.items() if s["KIND"] == "PROTOCOL_MODULE_SPEC")
    scope = read_json(directory / "scope.json")
    summary = ["# Spec bundle", "", f"Origin: {origin}; revision: {revision}", "",
               f"Protocol: {scope['protocol']} {scope['version']}; role: {scope['role']}",
               f"Runtime: {scope['runtime']} {scope['language']}; {scope['runtime_contract']['argv_contract']}",
               "", "## Required capabilities", ""]
    summary += [f"- {v}" for v in scope["required_capabilities"]]
    summary += ["", "## Excluded features", ""] + [f"- {v}" for v in scope["excluded_features"]]
    summary += ["", f"Module Spec: {module_path}", "", "## Files", ""]
    summary += [f"- {f['path']}: {f['spec']} ({f['trace_id']})" for f in files]
    summary += ["", "## Functions", ""] + [f"- {f['trace_id']}: {f['spec']}" for f in functions]
    if (directory / "traceability.json").is_file():
        trace = read_json(directory / "traceability.json")
        summary += ["", "## Processing chain", ""] + [f"- {r['id']}: {r['description']}" for r in trace["processing_chain"]]
    (directory / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    published_paths = set(items) | {h["artifact"] for h in headers} | {"scope.json", "traceability.json", "SUMMARY.md"}
    inventory = {p: digest(directory / p) for p in sorted(published_paths)}
    bundle = {"schema_version": 2, "revision": revision, "origin": origin,
              **{k: scope[k] for k in ("protocol", "version", "role", "runtime_contract")},
              "module": module_path, "files": files, "functions": functions, "headers": headers, "hashes": inventory}
    save_json(directory / "bundle.json", bundle)
    return bundle


def scaffold(bundle_dir: Path, project: Path) -> None:
    """Copy only Planner-authored public interfaces into the Coder workspace."""
    bundle = read_json(bundle_dir / "bundle.json")
    project.mkdir(parents=True, exist_ok=True)
    for header in bundle["headers"]:
        if not project_path(header["path"]):
            raise ValueError("Unsafe public header path")
        dest = project / header["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle_dir / header["artifact"], dest)


def delivery_checks(project: Path, bundle_dir: Path | None) -> dict:
    errors = []
    try:
        delivery = read_json(project / "delivery.json")
        errors += artifact_errors("delivery", delivery)
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing or invalid delivery.json: {exc}"]}
    if errors:
        return {"passed": False, "errors": errors}
    listed = set(delivery["files"])
    for path in listed:
        actual = (project / path).resolve()
        if not project_path(path) or not actual.is_relative_to(project.resolve()) or not actual.is_file():
            errors.append(f"Missing or unsafe delivery file: {path}")
    for name in ("Makefile", "README.md"):
        if name not in listed or not (project / name).is_file() or not (project / name).read_text().strip():
            errors.append(f"Missing nonempty delivery document: {name}")
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (".c", ".h", ".py", ".sh") and path.relative_to(project).as_posix() not in listed:
            errors.append(f"Unlisted source or development test: {path.relative_to(project)}")
    ids = [test["id"] for test in delivery["tests"]]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate development test ID")
    for test in delivery["tests"]:
        if test["path"] not in listed:
            errors.append(f"Test file absent from delivery inventory: {test['path']}")
    if bundle_dir:
        manifest = read_json(bundle_dir / "bundle.json")
        scope = read_json(bundle_dir / "scope.json")
        items, spec_errors = load_specs(bundle_dir)
        errors += spec_errors
        vectors = test_vector_refs(items)
        expected = {r["id"] for r in scope["requirements"]}
        covered = set()
        for test in delivery["tests"]:
            if set(test["requirement_ids"]) - expected:
                errors.append(f"Unknown test requirements: {test['id']}")
            covered.update(test["requirement_ids"])
            unknown = set(test["spec_test_refs"]) - set(vectors)
            if unknown:
                errors.append(f"Unknown Spec test-vector reference in {test['id']}: {sorted(unknown)}; "
                              "use <bundle-relative JSON path>#/TEST_VECTORS/<zero-based index>")
        if covered != expected:
            errors.append(f"Development tests must cover scope requirements: missing {sorted(expected - covered)}")
        for source in manifest["files"]:
            if source["path"] not in listed:
                errors.append(f"Planned source missing from delivery: {source['path']}")
        for header in manifest["headers"]:
            path = project / header["path"]
            if header["path"] not in listed or not path.is_file() or digest(path) != digest(bundle_dir / header["artifact"]):
                errors.append(f"Public header differs from active Spec: {header['path']}")
        if not project_path(manifest["runtime_contract"]["binary_name"]):
            errors.append("Runtime binary must be project-relative")
    return {"passed": not errors, "errors": errors, "test_count": len(ids)}

