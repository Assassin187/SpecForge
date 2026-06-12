#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KNOWN_KINDS = {"PROTOCOL_MODULE_SPEC", "FILE_SPEC", "FUNCTION_SPEC"}


class FailClosed(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosed(f"Could not read JSON '{path}': {exc}") from exc
    if not isinstance(raw, dict):
        raise FailClosed(f"Spec file '{path}' must contain a JSON object")
    return raw


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_repo_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("../"):
        normalized = normalized[3:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_signature(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def function_family(name: str) -> str:
    parts = [part for part in name.split("_") if part]
    if len(parts) >= 3 and parts[0] in {"mqtt", "coap"}:
        return f"{parts[0]}_{parts[1]}_*"
    if len(parts) >= 2:
        return f"{parts[0]}_*"
    return name or "<missing>"


def pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 4)


def sorted_list(values: set[str]) -> list[str]:
    return sorted(item for item in values if item)


def scan_bundle(root: Path) -> dict[str, Any]:
    if not root.exists():
        raise FailClosed(f"Spec root does not exist: {root}")
    module_raw: dict[str, Any] | None = None
    files: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    spec_count = 0
    for path in sorted(root.rglob("*_spec.json")):
        raw = read_json(path)
        kind = raw.get("KIND")
        if kind not in KNOWN_KINDS:
            raise FailClosed(f"Unsupported or missing KIND '{kind}' in '{path}'")
        spec_count += 1
        item = {"path": path, "relative_path": str(path.relative_to(root)), "raw": raw}
        if kind == "PROTOCOL_MODULE_SPEC":
            if module_raw is not None:
                raise FailClosed(f"Expected one PROTOCOL_MODULE_SPEC under '{root}', found another at '{path}'")
            module_raw = raw
        elif kind == "FILE_SPEC":
            files.append(item)
        elif kind == "FUNCTION_SPEC":
            functions.append(item)
    if spec_count == 0:
        raise FailClosed(f"No *_spec.json files found under '{root}'")
    if module_raw is None:
        raise FailClosed(f"No PROTOCOL_MODULE_SPEC found under '{root}'")
    return {"root": root, "module": module_raw, "files": files, "functions": functions}


def module_names(bundle: dict[str, Any]) -> set[str]:
    return {str(item.get("NAME", "")) for item in bundle["module"].get("MODULES", []) if isinstance(item, dict)}


def module_artifact_types(bundle: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for module in bundle["module"].get("MODULES", []):
        if not isinstance(module, dict):
            continue
        for artifact in module.get("ARTIFACTS", []):
            if isinstance(artifact, dict) and artifact.get("KIND") == "TYPE":
                types.add(str(artifact.get("NAME", "")))
    return types


def file_trace_ids(bundle: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for file_item in bundle["files"]:
        meta = file_item["raw"].get("FILE", {})
        if isinstance(meta, dict):
            values.add(str(meta.get("TRACE_ID", "")))
    return values


def file_paths(bundle: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for file_item in bundle["files"]:
        raw = file_item["raw"]
        header = raw.get("HEADER", {})
        source = raw.get("SOURCE", {})
        if isinstance(header, dict) and header.get("PATH"):
            values.add(normalize_repo_path(str(header["PATH"])))
        if isinstance(source, dict) and source.get("PATH"):
            values.add(normalize_repo_path(str(source["PATH"])))
    return values


def source_visibility_by_trace(bundle: dict[str, Any]) -> dict[str, str]:
    visibility: dict[str, str] = {}
    for file_item in bundle["files"]:
        source = file_item["raw"].get("SOURCE", {})
        if not isinstance(source, dict):
            continue
        for interface in source.get("INTERFACE", []):
            if isinstance(interface, dict):
                visibility[str(interface.get("TRACE_ID", ""))] = str(interface.get("VISIBILITY", ""))
    return visibility


def function_info(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    visibility = source_visibility_by_trace(bundle)
    info: dict[str, dict[str, Any]] = {}
    for item in bundle["functions"]:
        raw = item["raw"]
        signature = raw.get("SIGNATURE", {})
        name = str(signature.get("NAME", "")) if isinstance(signature, dict) else ""
        raw_sig = str(signature.get("RAW", "")) if isinstance(signature, dict) else ""
        trace_id = str(raw.get("TRACE_ID", ""))
        inferred_visibility = visibility.get(trace_id)
        if not inferred_visibility:
            inferred_visibility = "private" if raw_sig.strip().startswith("static ") else "public"
        info[name] = {
            "trace_id": trace_id,
            "signature": raw_sig,
            "normalized_signature": normalize_signature(raw_sig),
            "visibility": inferred_visibility,
            "family": function_family(name),
            "path": item["relative_path"],
            "raw": raw,
        }
    return info


def public_types(bundle: dict[str, Any]) -> set[str]:
    types = module_artifact_types(bundle)
    for file_item in bundle["files"]:
        header = file_item["raw"].get("HEADER", {})
        if not isinstance(header, dict):
            continue
        for data in header.get("DATA", []):
            if not isinstance(data, dict):
                continue
            if data.get("KIND") == "TYPE" and str(data.get("VISIBILITY", "")).upper() == "PUBLIC":
                types.add(str(data.get("NAME", "")))
    return types


def count_nonempty_function_behavior(functions: list[dict[str, Any]]) -> dict[str, int]:
    logic_count = event_count = nonempty = 0
    for item in functions:
        raw = item["raw"]
        if isinstance(raw.get("LOGIC"), dict):
            logic_count += 1
            if any(raw["LOGIC"].get(key) for key in ("INPUT", "ACTION", "OUTPUT", "INVARIANTS_USED")):
                nonempty += 1
        if isinstance(raw.get("EVENT"), dict):
            event_count += 1
            if any(raw["EVENT"].get(key) for key in ("TRIGGER", "PRECONDITION", "INPUT", "ACTION", "STATE_CHANGE", "RESPONSE", "EVENT_TYPE")):
                nonempty += 1
    return {"logic_count": logic_count, "event_count": event_count, "nonempty_count": nonempty}


def count_source_contracts(files: list[dict[str, Any]]) -> dict[str, int]:
    total = nonempty = 0
    for item in files:
        source = item["raw"].get("SOURCE", {})
        if not isinstance(source, dict):
            continue
        for interface in source.get("INTERFACE", []):
            if not isinstance(interface, dict):
                continue
            contract = interface.get("CONTRACT")
            if not isinstance(contract, dict):
                continue
            total += 1
            if contract.get("PRECONDITION") or contract.get("POSTCONDITION") or contract.get("THREAD_SAFETY"):
                nonempty += 1
    return {"contract_count": total, "nonempty_contract_count": nonempty}


def field_presence(bundle: dict[str, Any]) -> dict[str, Any]:
    functions = bundle["functions"]
    files = bundle["files"]
    behavior = count_nonempty_function_behavior(functions)
    contracts = count_source_contracts(files)
    wire_count = sum(len(item["raw"].get("WIRE_MAPPING", []) or []) for item in functions)
    function_access_count = sum(len(item["raw"].get("ACCESS_PATHS", []) or []) for item in functions)
    file_access_count = sum(len(item["raw"].get("ACCESS_PATHS", []) or []) for item in files)
    rely_func_count = 0
    function_call_contracts = 0
    for item in functions:
        rely = item["raw"].get("RELY", {})
        if isinstance(rely, dict):
            rely_func_count += len(rely.get("FUNC", []) or [])
        function_call_contracts += len(item["raw"].get("CALL_CONTRACTS", []) or [])
    file_call_contracts = sum(len(item["raw"].get("CALL_CONTRACTS", []) or []) for item in files)
    module_deps = sum(len(item.get("DEPENDENCIES", []) or []) for item in bundle["module"].get("MODULES", []) if isinstance(item, dict))
    header_deps = 0
    header_system_deps = 0
    source_deps = 0
    for item in files:
        header = item["raw"].get("HEADER", {})
        source = item["raw"].get("SOURCE", {})
        if isinstance(header, dict):
            header_deps += len(header.get("DEPENDENCY", []) or [])
            header_system_deps += len(header.get("SYSTEM_DEPENDENCY", []) or [])
        if isinstance(source, dict):
            source_deps += len(source.get("DEPENDENCY", []) or [])
    module_vectors = len(bundle["module"].get("TEST_VECTORS", []) or [])
    file_vectors = sum(len(item["raw"].get("TEST_VECTORS", []) or []) for item in files)
    function_vectors = sum(len(item["raw"].get("TEST_VECTORS", []) or []) for item in functions)
    return {
        "behavior": behavior | contracts,
        "wire_access": {
            "wire_mapping_count": wire_count,
            "function_access_path_count": function_access_count,
            "file_access_path_count": file_access_count,
        },
        "calls_dependency": {
            "rely_func_count": rely_func_count,
            "function_call_contract_count": function_call_contracts,
            "file_call_contract_count": file_call_contracts,
            "module_dependency_count": module_deps,
            "header_dependency_count": header_deps,
            "header_system_dependency_count": header_system_deps,
            "source_dependency_count": source_deps,
        },
        "test_vectors": {
            "module_test_vector_count": module_vectors,
            "file_test_vector_count": file_vectors,
            "function_test_vector_count": function_vectors,
            "total_test_vector_count": module_vectors + file_vectors + function_vectors,
        },
    }


def coverage(gold_values: set[str], planning_values: set[str]) -> dict[str, Any]:
    matched = gold_values & planning_values
    return {
        "gold_count": len(gold_values),
        "planning_count": len(planning_values),
        "matched_count": len(matched),
        "coverage": pct(len(matched), len(gold_values)),
        "gold": sorted_list(gold_values),
        "planning": sorted_list(planning_values),
        "matched": sorted_list(matched),
        "missing_from_planning": sorted_list(gold_values - planning_values),
        "extra_in_planning": sorted_list(planning_values - gold_values),
    }


def signature_coverage(gold: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    gold_functions = function_info(gold)
    planning_functions = function_info(planning)
    exact: list[str] = []
    normalized: list[str] = []
    mismatches: list[dict[str, str]] = []
    for name, gold_item in gold_functions.items():
        planning_item = planning_functions.get(name)
        if not planning_item:
            continue
        if gold_item["signature"] == planning_item["signature"]:
            exact.append(name)
        if gold_item["normalized_signature"] == planning_item["normalized_signature"]:
            normalized.append(name)
        else:
            mismatches.append({"name": name, "gold": gold_item["signature"], "planning": planning_item["signature"]})
    missing = set(gold_functions) - set(planning_functions)
    extra = set(planning_functions) - set(gold_functions)
    return {
        "gold_function_count": len(gold_functions),
        "planning_function_count": len(planning_functions),
        "name_overlap_count": len(set(gold_functions) & set(planning_functions)),
        "exact_signature_match_count": len(exact),
        "normalized_signature_match_count": len(normalized),
        "coverage": pct(len(normalized), len(gold_functions)),
        "exact_matches": sorted(exact),
        "normalized_matches": sorted(normalized),
        "mismatches": sorted(mismatches, key=lambda item: item["name"]),
        "missing_from_planning": sorted(missing),
        "extra_in_planning": sorted(extra),
    }


def function_count(bundle: dict[str, Any]) -> dict[str, int]:
    info = function_info(bundle)
    public_count = sum(1 for item in info.values() if str(item["visibility"]).lower() == "public")
    return {
        "total": len(info),
        "public": public_count,
        "private": len(info) - public_count,
    }


def family_coverage(gold: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    gold_counter = Counter(item["family"] for item in function_info(gold).values())
    planning_counter = Counter(item["family"] for item in function_info(planning).values())
    gold_families = set(gold_counter)
    planning_families = set(planning_counter)
    return {
        "coverage": coverage(gold_families, planning_families),
        "gold_counts": dict(sorted(gold_counter.items())),
        "planning_counts": dict(sorted(planning_counter.items())),
    }


def validate_input(root: Path, schema_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": [], "loader": [], "has_errors": False}
    try:
        from agent.coder.specs import load_spec_bundle_from_root
        from agent.planning.validators.coder_schema import validate_coder_spec_bundle_against_schema
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": [{"level": "error", "code": "import_failed", "message": str(exc), "path": str(REPO_ROOT)}],
            "loader": [],
            "has_errors": True,
        }
    schema_diags = validate_coder_spec_bundle_against_schema(root, schema_root)
    result["schema"] = [diag.__dict__ for diag in schema_diags]
    if any(diag.level == "error" for diag in schema_diags):
        result["has_errors"] = True
        return result
    try:
        bundle = load_spec_bundle_from_root(root, validate_rendered_headers=True)
    except Exception as exc:  # noqa: BLE001
        result["loader"].append({"level": "error", "code": "loader_failed", "message": str(exc), "path": str(root)})
        result["has_errors"] = True
        return result
    result["loader"] = [diag.__dict__ for diag in bundle.diagnostics]
    result["has_errors"] = any(diag.level == "error" for diag in bundle.diagnostics)
    return result


def build_comparison(gold_root: Path, planning_root: Path, schema_root: Path, match_mode: str) -> dict[str, Any]:
    gold = scan_bundle(gold_root)
    planning = scan_bundle(planning_root)
    diagnostics = {
        "gold": validate_input(gold_root, schema_root),
        "planning": validate_input(planning_root, schema_root),
    }
    status = "degraded_input" if diagnostics["gold"]["has_errors"] or diagnostics["planning"]["has_errors"] else "ok"
    return {
        "schema_version": "specforge_specs_comparison/v1",
        "tool": "compare_specs.py",
        "generated_at": now_utc(),
        "status": status,
        "match_mode": match_mode,
        "gold_root": str(gold_root),
        "planning_root": str(planning_root),
        "module_coverage": coverage(module_names(gold), module_names(planning)),
        "file_coverage": {
            "trace_id": coverage(file_trace_ids(gold), file_trace_ids(planning)),
            "path": coverage(file_paths(gold), file_paths(planning)),
        },
        "function_count": {
            "gold": function_count(gold),
            "planning": function_count(planning),
        },
        "function_family_coverage": family_coverage(gold, planning),
        "public_type_coverage": coverage(public_types(gold), public_types(planning)),
        "signature_coverage": signature_coverage(gold, planning),
        "behavior_field_presence": {
            "gold": field_presence(gold)["behavior"],
            "planning": field_presence(planning)["behavior"],
        },
        "wire_access_field_presence": {
            "gold": field_presence(gold)["wire_access"],
            "planning": field_presence(planning)["wire_access"],
        },
        "calls_dependency_presence": {
            "gold": field_presence(gold)["calls_dependency"],
            "planning": field_presence(planning)["calls_dependency"],
        },
        "test_vector_presence": {
            "gold": field_presence(gold)["test_vectors"],
            "planning": field_presence(planning)["test_vectors"],
        },
        "diagnostics": diagnostics,
    }


def write_summary(output_dir: Path, comparison: dict[str, Any]) -> None:
    lines = [
        "# Planning-vs-Gold Specs Comparison",
        "",
        f"- Status: `{comparison['status']}`",
        f"- Gold: `{comparison['gold_root']}`",
        f"- Planning: `{comparison['planning_root']}`",
        f"- Module coverage: `{comparison['module_coverage']['matched_count']}/{comparison['module_coverage']['gold_count']}`",
        f"- File path coverage: `{comparison['file_coverage']['path']['matched_count']}/{comparison['file_coverage']['path']['gold_count']}`",
        f"- Function signature coverage: `{comparison['signature_coverage']['normalized_signature_match_count']}/{comparison['signature_coverage']['gold_function_count']}`",
        f"- Public type coverage: `{comparison['public_type_coverage']['matched_count']}/{comparison['public_type_coverage']['gold_count']}`",
        "",
        "## Missing Function Families",
    ]
    missing_families = comparison["function_family_coverage"]["coverage"]["missing_from_planning"]
    if missing_families:
        lines.extend(f"- `{item}`" for item in missing_families)
    else:
        lines.append("- None")
    lines.extend(["", "## Diagnostics"])
    for side in ("gold", "planning"):
        diag = comparison["diagnostics"][side]
        lines.append(f"- {side}: errors=`{diag['has_errors']}` schema={len(diag['schema'])} loader={len(diag['loader'])}")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare gold/example specs against planning specs.")
    parser.add_argument("--gold", required=True, type=Path, help="Gold/example specs root")
    parser.add_argument("--planning", required=True, type=Path, help="Planning spec_bundle root")
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    parser.add_argument("--schema-root", type=Path, default=REPO_ROOT / "specs-example" / "specs_schema")
    parser.add_argument("--match-mode", choices=["name", "trace_id"], default="name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output.resolve()
    try:
        comparison = build_comparison(args.gold.resolve(), args.planning.resolve(), args.schema_root.resolve(), args.match_mode)
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(output_dir / "comparison.json", comparison)
        write_summary(output_dir, comparison)
    except FailClosed as exc:
        print(f"ERROR fail_closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": comparison["status"], "output": str(output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
