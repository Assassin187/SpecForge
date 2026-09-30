"""Three-layer Spec checks, compiler-checked ABI and bundle publication."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath

import jsonschema

from .documents import ROOT, artifact_errors, digest, hashes, read_json, save_json

FLOW_IDS = ("main_loop", "receive_buffer", "decode_outcomes", "connect", "subscribe",
            "publish", "ping_disconnect_eof", "connection_cleanup", "borrow_copy_ownership", "error_isolation")
TEST_IDS = ("mqtt_cross_client_pubsub", "mqtt_buffer_before_eof", "mqtt_malformed_survival",
            "mqtt_disconnect_handler", "mqtt_smoke_test", "mqtt_mosquitto_interop",
            "mqtt_connect_semantics", "mqtt_suback_contract", "mqtt_exact_binary_payload",
            "mqtt_plus_filter_boundaries", "mqtt_hash_filter_boundaries", "mqtt_fragmented_input",
            "mqtt_coalesced_input", "mqtt_ping_exchange", "mqtt_disconnect_subscription_cleanup",
            "mqtt_invalid_packet_connection_isolation")
SCHEMAS = {"PROTOCOL_MODULE_SPEC": "module_spec_schema.json", "FILE_SPEC": "file_spec_schema.json",
           "FUNCTION_SPEC": "function_spec_schema.json"}
LIBC_CALLS = {"malloc", "calloc", "realloc", "free", "memcpy", "memmove", "memset", "memcmp", "strlen", "strcmp",
              "strncmp", "strdup", "strchr", "strstr", "strtol", "strtoul", "printf", "fprintf", "snprintf", "perror",
              "socket", "bind", "listen", "accept", "recv", "send", "read", "write", "close", "shutdown", "fcntl",
              "setsockopt", "getsockopt", "poll", "signal", "sigaction", "htons", "ntohs", "htonl", "ntohl",
              "epoll_create1", "epoll_ctl", "epoll_wait", "inet_ntop", "exit", "atoi"}


def project_path(path: str, prefix: str) -> bool:
    p = PurePosixPath(path)
    return not p.is_absolute() and ".." not in p.parts and len(p.parts) > 1 and p.parts[0] == prefix and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", path))


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


def compile_probe(text: str, include: Path) -> dict:
    result = subprocess.run(["gcc", "-std=c99", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
                             "-fsyntax-only", "-x", "c", "-", "-I", str(include)],
                            input=text, text=True, capture_output=True, timeout=20)
    return {"passed": result.returncode == 0, "output": result.stdout + result.stderr, "probe": text}


def abi_checks(directory: Path, files: list[tuple[str, dict]], functions: list[tuple[str, dict]]) -> dict:
    include = directory / "abi"
    probes, errors = [], []
    headers = []
    for relative, spec in files:
        header = spec.get("HEADER")
        if not header:
            continue
        if not project_path(header["PATH"], "include"):
            errors.append(f"{relative}: header path must be include/<name>.h")
            continue
        name = str(PurePosixPath(header["PATH"]).relative_to("include"))
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
        probe = compile_probe("\n".join(declarations) + "\n", include)
        probes.append({"header": header["PATH"], **probe})
        if not probe["passed"]:
            errors.append(f"ABI mismatch in {header['PATH']}: {probe['output'][:2000]}"
                          + "\nStructured C declarations from FUNCTION_SPEC SIGNATURE.RETURN/PARAMS (RETURN must be a C type):\n"
                          + "\n".join(structured_declarations)[:1800])
    if headers:
        combined = compile_probe("\n".join(f'#include "{h}"' for h in headers) + "\n", include)
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
    if design or facts is not None:
        planned_count = sum(len(s["SOURCE"]["INTERFACE"]) for _, s in files)
        if len(files) > 4 or len(header_paths) > 3 or planned_count > 24:
            errors.append("Minimum-case design budget: <=4 source files, <=3 public headers, <=24 planned functions")
    for label, values in (("source", source_paths), ("header", header_paths), ("file ID", file_ids), ("function ID", function_ids)):
        errors += [f"Duplicate {label}: {name}" for name, n in Counter(values).items() if n > 1]
    for path in source_paths:
        if not project_path(path, "src") or not path.endswith(".c"):
            errors.append(f"Source must be a project-relative src/*.c path: {path}")
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
        entries = [f for _, f in functions if f["FUNCTION_TYPE"] == "ENTRYPOINT"]
        if len(entries) != 1 or entries[0]["SIGNATURE"]["NAME"] != "main":
            errors.append("Exactly one main ENTRYPOINT Spec is required")
        for trace, (path, function) in by_id.items():
            if trace not in ownership:
                errors.append(f"Function Spec has no source owner: {trace}")
                continue
            owner = ownership[trace][1]
            local_names = {i["NAME"] for i in owner["SOURCE"]["INTERFACE"]}
            for dep in function["RELY"]["FUNC"]:
                if dep["KIND"] == "TYPE_REF" and dep["NAME"] in types:
                    continue
                if dep["NAME"] not in local_names and dep["NAME"] not in publics and dep["NAME"] not in LIBC_CALLS:
                    errors.append(f"Unknown project function dependency in {trace}: {dep['NAME']}")
                if facts is not None and dep["NAME"] in publics and publics[dep["NAME"]][0] != ownership[trace][0]:
                    contract = next((c for c in function.get("CALL_CONTRACTS", []) if c["NAME"] == dep["NAME"]), None)
                    if not contract or any(not contract.get(k) for k in ("RETURN", "OWNERSHIP", "FAILURE")) or "PARAMS" not in contract:
                        errors.append(f"Missing cross-file call contract in {trace}: {dep['NAME']}")
                    elif compact(contract["SIGNATURE"]) != compact(publics[dep["NAME"]][1]["SIGNATURE"]):
                        errors.append(f"Call contract signature mismatch in {trace}: {dep['NAME']}")
            action = (function.get("LOGIC") or function.get("EVENT") or {}).get("ACTION", "")
            if not action.strip():
                errors.append(f"Missing function behavior: {trace}")
        for path, spec in items.items():
            for vector in spec.get("TEST_VECTORS", []):
                for trace in vector.get("TRACE_REFS", []):
                    if trace not in by_id and trace not in file_ids:
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
                    errors.append(f"Traceability must cover every scope requirement exactly once: expected {sorted(expected_reqs)}, got {mapped_reqs}. Acceptance IDs belong in test_ids.")
                tests = set()
                for row in trace["requirements"]:
                    if set(row["fact_ids"]) - fact_ids:
                        errors.append(f"Unknown facts in requirement {row['requirement_id']}")
                    if set(row["test_ids"]) - set(TEST_IDS):
                        errors.append(f"Unknown acceptance IDs in {row['requirement_id']}")
                    tests.update(row["test_ids"])
                if tests != set(TEST_IDS):
                    errors.append("Traceability must cover all 16 acceptance scenarios")
                if {r["id"] for r in trace["processing_chain"]} != set(FLOW_IDS):
                    errors.append("Processing chain must cover all ten flow IDs")
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
        expected_headers = {(s["HEADER"]["PATH"], "abi/" + str(PurePosixPath(s["HEADER"]["PATH"]).relative_to("include")))
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
    headers = [{"path": s["HEADER"]["PATH"], "artifact": "abi/" + str(PurePosixPath(s["HEADER"]["PATH"]).relative_to("include"))}
               for s in items.values() if s["KIND"] == "FILE_SPEC" and "HEADER" in s]
    functions = [{"spec": p, "trace_id": s["TRACE_ID"], "name": s["SIGNATURE"]["NAME"]}
                 for p, s in items.items() if s["KIND"] == "FUNCTION_SPEC"]
    module_path = next(p for p, s in items.items() if s["KIND"] == "PROTOCOL_MODULE_SPEC")
    summary = ["# Spec bundle", "", f"Origin: {origin}; revision: {revision}", "",
               "Runtime: Linux C99 TCP, ./mqtt_broker <port>", "", f"Module Spec: {module_path}", "", "## Files", ""]
    summary += [f"- {f['path']}: {f['spec']} ({f['trace_id']})" for f in files]
    summary += ["", "## Functions", ""] + [f"- {f['trace_id']}: {f['spec']}" for f in functions]
    if (directory / "traceability.json").is_file():
        trace = read_json(directory / "traceability.json")
        summary += ["", "## Processing chain", ""] + [f"- {r['id']}: {r['description']}" for r in trace["processing_chain"]]
    (directory / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    published_paths = set(items) | {h["artifact"] for h in headers} | {"scope.json", "traceability.json", "SUMMARY.md"}
    inventory = {p: digest(directory / p) for p in sorted(published_paths)}
    bundle = {"schema_version": 1, "revision": revision, "origin": origin, "protocol": "MQTT", "version": "3.1.1", "role": "BROKER",
              "runtime_contract": {"binary_name": "mqtt_broker", "argv_contract": "./mqtt_broker <port>"},
              "module": module_path, "files": files, "functions": functions, "headers": headers, "hashes": inventory}
    save_json(directory / "bundle.json", bundle)
    return bundle


def project_readme(bundle: dict) -> str:
    return ("# MQTT 3.1.1 minimum broker\n\n"
            "Linux C99 TCP broker for the selected MQTT 3.1.1 subset.\n\n"
            "## Build and run\n\n```sh\nmake clean\nmake\n./mqtt_broker 1883\n```\n\n"
            "Startup contract: `./mqtt_broker <port>`. Stop with SIGINT or SIGTERM; "
            "normal shutdown releases connections, subscriptions and buffers.\n\n"
            "## Scope\n\nAnonymous CONNECT with Clean Session=1 and no Will; CONNACK; "
            "multi-filter SUBSCRIBE/SUBACK; QoS 0 PUBLISH forwarding; case-sensitive "
            "exact, `+` and `#` matching; binary and empty payloads; PINGREQ/PINGRESP; "
            "DISCONNECT and TCP cleanup. TCP fragments and coalesced packets are "
            "handled, and complete buffered frames are processed before EOF cleanup.\n\n"
            "Excluded: QoS 1/2, retained messages, Will, persistent sessions, "
            "UNSUBSCRIBE, authentication, TLS and keepalive timeout scheduling. "
            "This project does not claim complete MQTT conformance.\n\n"
            "## Self-test\n\n```sh\npython3 tests/mqtt_check.py --binary ./mqtt_broker --out tests/results\n"
            "make sanitize\npython3 tests/mqtt_check.py --binary ./mqtt_broker --out tests/sanitize-results\n"
            "make clean\nmake\n```\n\n"
            "The harness starts its own broker processes and checks 16 MQTT minimum "
            "scenarios. `--legacy` selects only the six historical scenarios for "
            "a Spec-only baseline. Reports record failures and unavailable tools; "
            "a successful build alone does not establish protocol correctness.\n\n"
            "## Planned sources\n\n"
            + "\n".join(f"- `{f['path']}`" for f in bundle["files"]) + "\n\n"
            "Public headers in `include/` and Makefile come from the published Spec "
            "bundle. Private implementation and development tests live in `src/` "
            "and `tests/`. This README is generated from the fixed minimum profile "
            "and bundle inventory, rather than inferred from model claims.\n")


def scaffold(bundle_dir: Path, project: Path) -> None:
    bundle = read_json(bundle_dir / "bundle.json")
    project.mkdir(parents=True, exist_ok=True)
    for directory in ("src", "include", "tests"):
        (project / directory).mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "specforge/mqtt_check.py", project / "tests/mqtt_check.py")
    for header in bundle["headers"]:
        dest = project / header["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle_dir / header["artifact"], dest)
    sources = " ".join(f["path"] for f in bundle["files"])
    makefile = ("CC = gcc\nCPPFLAGS = -D_POSIX_C_SOURCE=200809L -Iinclude\n"
                "CFLAGS = -std=c99 -O2 -g -Wall -Wextra -Wpedantic -Werror\n"
                f"SOURCES = {sources}\nHEADERS = $(shell find include -name '*.h')\n"
                ".PHONY: all clean sanitize\nall: mqtt_broker\n"
                "mqtt_broker: $(SOURCES) $(HEADERS)\n\t$(CC) $(CPPFLAGS) $(CFLAGS) $(SOURCES) $(LDFLAGS) -o $@\n"
                "sanitize: CFLAGS = -std=c99 -O1 -g -Wall -Wextra -Wpedantic -Werror -fno-omit-frame-pointer -fno-pie -fsanitize=address,undefined\n"
                "sanitize: LDFLAGS = -no-pie -fsanitize=address,undefined\n"
                "sanitize: clean mqtt_broker\nclean:\n\trm -f mqtt_broker\n")
    (project / "Makefile").write_text(makefile)
    (project / "README.md").write_text(project_readme(bundle))


def manual_reference() -> dict:
    original = ROOT / "assets/mqtt_reference/original_specs"
    bundle_dir = ROOT / "assets/mqtt_reference/bundle"
    changes = []

    def normalize(node):
        if isinstance(node, list):
            return [normalize(v) for v in node]
        if isinstance(node, dict):
            return {k: normalize(v) for k, v in node.items()}
        if isinstance(node, str) and node.startswith("../") and node.endswith((".h", ".c")):
            return ("include/" if node.endswith(".h") else "src/") + node[3:]
        if node == "mqtt/broker/mqtt_broker_handle_publish":
            return "mqtt/broker/broker/handle_packet"
        return node

    for path in sorted(original.rglob("*.json")):
        raw = read_json(path)
        spec = normalize(raw)
        if spec["KIND"] == "FILE_SPEC":
            for item in spec.get("HEADER", {}).get("DATA", []):
                if item["KIND"] == "TYPE" and "TYPE_SPEC" not in item:
                    hpath = spec["HEADER"]["PATH"].removeprefix("include/")
                    text = (bundle_dir / "abi" / hpath).read_text()
                    if not re.search(r"typedef\s+struct\s+\w+\s+" + re.escape(item["NAME"]) + r"\s*;", text):
                        raise ValueError(f"Cannot verify opaque typedef from header: {item['NAME']}")
                    item["TYPE_SPEC"] = {"TYPE_KIND": "OPAQUE"}
                    changes.append({"original": path.relative_to(original).as_posix(), "change": "Header-confirmed opaque TYPE_SPEC", "symbol": item["NAME"]})
            destination = bundle_dir / "files" / (spec["FILE"]["TRACE_ID"].replace("/", "__") + ".json")
        elif spec["KIND"] == "FUNCTION_SPEC":
            destination = bundle_dir / "functions" / (spec["TRACE_ID"] + ".json")
        else:
            destination = bundle_dir / "module_spec.json"
        save_json(destination, spec)
        if raw != spec:
            changes.append({"original": path.relative_to(original).as_posix(), "normalized": destination.relative_to(bundle_dir).as_posix(),
                            "original_sha256": digest(path), "normalized_sha256": digest(destination)})
    # This is a downstream fixture: no invented document evidence or automatic provenance.
    scope = {"schema_version": 1, "protocol": "MQTT", "version": "3.1.1", "role": "BROKER", "language": "C99", "runtime": "Linux",
             "requirements": [], "required_capabilities": ["manual MQTT subset"], "excluded_features": ["full MQTT compliance"],
             "runtime_contract": {"binary_name": "mqtt_broker", "argv_contract": "./mqtt_broker <port>"}, "engineering_defaults": []}
    save_json(bundle_dir / "scope.json", scope)
    save_json(bundle_dir / "traceability.json", {"schema_version": 1, "origin": "manual_reference", "requirements": [],
                                               "processing_chain": [], "engineering_decisions": [], "limitations": ["Original DOC_REF fields are empty; grounding is not asserted."]})
    report = validate(bundle_dir, published=False)
    if report["passed"]:
        publish(bundle_dir, 1, "manual_reference")
    ledger = read_json(ROOT / "assets/migration.json")
    ledger["changes"] = changes + [{"change": "Normalize ../ paths; replace dangling publish TRACE_REF with existing handle_packet; preserve original data"}]
    save_json(ROOT / "assets/migration.json", ledger)
    save_json(ROOT / "assets/mqtt_reference/validation.json", report)
    return report

