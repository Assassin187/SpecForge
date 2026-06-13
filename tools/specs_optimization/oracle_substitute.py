#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KNOWN_KINDS = {"PROTOCOL_MODULE_SPEC", "FILE_SPEC", "FUNCTION_SPEC"}
STRATEGIES = ("P+GoldDependency", "P+GoldType", "P+GoldSignature")


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


def scan_bundle(root: Path) -> dict[str, Any]:
    if not root.exists():
        raise FailClosed(f"Spec root does not exist: {root}")
    module_path: Path | None = None
    module_raw: dict[str, Any] | None = None
    files: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*_spec.json")):
        raw = read_json(path)
        kind = raw.get("KIND")
        if kind not in KNOWN_KINDS:
            raise FailClosed(f"Unsupported or missing KIND '{kind}' in '{path}'")
        item = {"path": path, "relative_path": str(path.relative_to(root)), "raw": raw}
        if kind == "PROTOCOL_MODULE_SPEC":
            if module_raw is not None:
                raise FailClosed(f"Expected one PROTOCOL_MODULE_SPEC under '{root}', found another at '{path}'")
            module_path = path
            module_raw = raw
        elif kind == "FILE_SPEC":
            files.append(item)
        elif kind == "FUNCTION_SPEC":
            functions.append(item)
    if module_raw is None or module_path is None:
        raise FailClosed(f"No PROTOCOL_MODULE_SPEC found under '{root}'")
    return {"root": root, "module_path": module_path, "module": module_raw, "files": files, "functions": functions}


def module_by_name(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("NAME", "")): item
        for item in bundle["module"].get("MODULES", [])
        if isinstance(item, dict) and item.get("NAME")
    }


def module_for_paths(bundle: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for module in bundle["module"].get("MODULES", []):
        if not isinstance(module, dict):
            continue
        name = str(module.get("NAME", ""))
        for path in module.get("FILES", []):
            mapping[normalize_repo_path(str(path))] = name
    return mapping


def file_records(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    module_paths = module_for_paths(bundle)
    records: list[dict[str, Any]] = []
    for item in bundle["files"]:
        raw = item["raw"]
        header = raw.get("HEADER", {})
        source = raw.get("SOURCE", {})
        header_path = normalize_repo_path(str(header.get("PATH", ""))) if isinstance(header, dict) else ""
        source_path = normalize_repo_path(str(source.get("PATH", ""))) if isinstance(source, dict) else ""
        module = module_paths.get(source_path) or module_paths.get(header_path) or ""
        records.append(
            {
                "path": item["path"],
                "relative_path": item["relative_path"],
                "raw": raw,
                "header_path": header_path,
                "source_path": source_path,
                "header_base": Path(header_path).name if header_path else "",
                "source_base": Path(source_path).name if source_path else "",
                "module": module,
            }
        )
    return records


def unique_map(pairs: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, record in pairs:
        if key:
            grouped[key].append(record)
    unique = {key: records[0] for key, records in grouped.items() if len(records) == 1}
    ambiguous = {key for key, records in grouped.items() if len(records) > 1}
    return unique, ambiguous


def gold_file_indexes(gold: dict[str, Any]) -> dict[str, Any]:
    records = file_records(gold)
    exact_pairs: list[tuple[str, dict[str, Any]]] = []
    base_pairs: list[tuple[str, dict[str, Any]]] = []
    for record in records:
        exact_pairs.extend([(record["header_path"], record), (record["source_path"], record)])
        base_pairs.extend([(record["header_base"], record), (record["source_base"], record)])
    exact, ambiguous_exact = unique_map(exact_pairs)
    base, ambiguous_base = unique_map(base_pairs)
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["module"]:
            by_module[record["module"]].append(record)
    return {
        "records": records,
        "exact": exact,
        "ambiguous_exact": ambiguous_exact,
        "base": base,
        "ambiguous_base": ambiguous_base,
        "by_module": by_module,
    }


def match_gold_file(planning_record: dict[str, Any], indexes: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    for key in (planning_record["source_path"], planning_record["header_path"]):
        if key in indexes["ambiguous_exact"]:
            return None, f"ambiguous exact path match for {key}"
        if key in indexes["exact"]:
            return indexes["exact"][key], "exact_path"
    for key in (planning_record["source_base"], planning_record["header_base"]):
        if key in indexes["ambiguous_base"]:
            return None, f"ambiguous file basename match for {key}"
        if key in indexes["base"]:
            return indexes["base"][key], "file_basename"
    module = planning_record.get("module", "")
    candidates = indexes["by_module"].get(module, [])
    if module and len(candidates) == 1:
        return candidates[0], "module_name_single_file"
    if module and len(candidates) > 1:
        return None, f"module '{module}' has {len(candidates)} gold files; refusing to guess"
    return None, "no compatible gold file match"


def summarize(value: Any) -> Any:
    if isinstance(value, list):
        return {"count": len(value), "values": value}
    if isinstance(value, dict):
        return {"keys": sorted(value.keys())}
    return value


def note_replacement(manifest: dict[str, Any], path: str, field: str, before: Any, after: Any, match_reason: str) -> None:
    manifest["replacements"].append(
        {
            "path": path,
            "field": field,
            "operation": "replace_with_gold_oracle",
            "source_kind": "gold_spec_code_derived_oracle",
            "match_reason": match_reason,
            "before": summarize(before),
            "after": summarize(after),
        }
    )


def replace_field(
    container: dict[str, Any],
    path: str,
    field: str,
    value: Any,
    manifest: dict[str, Any],
    match_reason: str,
    display_field: str | None = None,
) -> None:
    before = container.get(field)
    container[field] = list(value) if isinstance(value, list) else value
    note_replacement(manifest, path, display_field or field, before, container[field], match_reason)


def is_public_type(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("KIND") == "TYPE"
        and str(item.get("VISIBILITY", "")).upper() == "PUBLIC"
        and bool(item.get("NAME"))
    )


def is_type_artifact(item: Any) -> bool:
    return isinstance(item, dict) and item.get("KIND") == "TYPE" and bool(item.get("NAME"))


def replace_list_partition(
    container: dict[str, Any],
    path: str,
    field: str,
    predicate,
    replacement_items: list[dict[str, Any]],
    manifest: dict[str, Any],
    match_reason: str,
    display_field: str,
) -> None:
    before = container.get(field, [])
    if not isinstance(before, list):
        manifest["unmatched"].append({"path": path, "field": display_field, "reason": f"{field} is not a list"})
        return
    kept = [copy.deepcopy(item) for item in before if not predicate(item)]
    container[field] = kept + [copy.deepcopy(item) for item in replacement_items]
    note_replacement(manifest, path, display_field, before, container[field], match_reason)


def apply_gold_dependency(output_bundle: dict[str, Any], gold_bundle: dict[str, Any], manifest: dict[str, Any]) -> None:
    gold_modules = module_by_name(gold_bundle)
    output_module_path = str(output_bundle["module_path"].relative_to(output_bundle["root"]))
    for index, module in enumerate(output_bundle["module"].get("MODULES", [])):
        if not isinstance(module, dict):
            continue
        name = str(module.get("NAME", ""))
        gold_module = gold_modules.get(name)
        if not gold_module:
            manifest["unmatched"].append({"path": output_module_path, "field": f"MODULES[{index}].DEPENDENCIES", "reason": f"no gold module named '{name}'"})
            continue
        replace_field(module, output_module_path, "DEPENDENCIES", gold_module.get("DEPENDENCIES", []), manifest, "module_name_exact", f"MODULES[{index}].DEPENDENCIES")

    indexes = gold_file_indexes(gold_bundle)
    for record in file_records(output_bundle):
        gold_record, reason = match_gold_file(record, indexes)
        if gold_record is None:
            manifest["unmatched"].append({"path": record["relative_path"], "field": "HEADER/SOURCE dependency", "reason": reason})
            continue
        raw = record["raw"]
        gold_raw = gold_record["raw"]
        header = raw.get("HEADER")
        gold_header = gold_raw.get("HEADER")
        if isinstance(header, dict) and isinstance(gold_header, dict):
            replace_field(header, record["relative_path"], "DEPENDENCY", gold_header.get("DEPENDENCY", []), manifest, reason, "HEADER.DEPENDENCY")
            if "SYSTEM_DEPENDENCY" in gold_header or "SYSTEM_DEPENDENCY" in header:
                replace_field(header, record["relative_path"], "SYSTEM_DEPENDENCY", gold_header.get("SYSTEM_DEPENDENCY", []), manifest, reason, "HEADER.SYSTEM_DEPENDENCY")
        elif isinstance(header, dict):
            manifest["unmatched"].append({"path": record["relative_path"], "field": "HEADER.DEPENDENCY", "reason": "matched gold file has no HEADER block"})
        source = raw.get("SOURCE")
        gold_source = gold_raw.get("SOURCE")
        if isinstance(source, dict) and isinstance(gold_source, dict):
            replace_field(source, record["relative_path"], "DEPENDENCY", gold_source.get("DEPENDENCY", []), manifest, reason, "SOURCE.DEPENDENCY")
        elif isinstance(source, dict):
            manifest["unmatched"].append({"path": record["relative_path"], "field": "SOURCE.DEPENDENCY", "reason": "matched gold file has no SOURCE block"})


def apply_gold_type(output_bundle: dict[str, Any], gold_bundle: dict[str, Any], manifest: dict[str, Any]) -> None:
    gold_modules = module_by_name(gold_bundle)
    output_module_path = str(output_bundle["module_path"].relative_to(output_bundle["root"]))
    for index, module in enumerate(output_bundle["module"].get("MODULES", [])):
        if not isinstance(module, dict):
            continue
        name = str(module.get("NAME", ""))
        gold_module = gold_modules.get(name)
        if not gold_module:
            manifest["unmatched"].append({"path": output_module_path, "field": f"MODULES[{index}].ARTIFACTS TYPE", "reason": f"no gold module named '{name}'"})
            continue
        gold_type_artifacts = [item for item in gold_module.get("ARTIFACTS", []) if is_type_artifact(item)]
        replace_list_partition(
            module,
            output_module_path,
            "ARTIFACTS",
            is_type_artifact,
            gold_type_artifacts,
            manifest,
            "module_name_exact",
            f"MODULES[{index}].ARTIFACTS TYPE",
        )

    indexes = gold_file_indexes(gold_bundle)
    for record in file_records(output_bundle):
        gold_record, reason = match_gold_file(record, indexes)
        if gold_record is None:
            manifest["unmatched"].append({"path": record["relative_path"], "field": "HEADER.DATA public TYPE", "reason": reason})
            continue
        header = record["raw"].get("HEADER")
        gold_header = gold_record["raw"].get("HEADER")
        if isinstance(header, dict) and isinstance(gold_header, dict):
            gold_public_types = [item for item in gold_header.get("DATA", []) if is_public_type(item)]
            replace_list_partition(
                header,
                record["relative_path"],
                "DATA",
                is_public_type,
                gold_public_types,
                manifest,
                reason,
                "HEADER.DATA public TYPE",
            )
        elif isinstance(header, dict):
            manifest["unmatched"].append({"path": record["relative_path"], "field": "HEADER.DATA public TYPE", "reason": "matched gold file has no HEADER block"})


def unique_functions_by_name(bundle: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in bundle["functions"]:
        signature = item["raw"].get("SIGNATURE", {})
        if isinstance(signature, dict) and signature.get("NAME"):
            grouped[str(signature["NAME"])].append(item)
    unique = {name: items[0] for name, items in grouped.items() if len(items) == 1}
    ambiguous = {name for name, items in grouped.items() if len(items) > 1}
    return unique, ambiguous


def gold_signature_by_name(bundle: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    functions, ambiguous = unique_functions_by_name(bundle)
    signatures: dict[str, dict[str, Any]] = {}
    for name, item in functions.items():
        signature = item["raw"].get("SIGNATURE")
        if isinstance(signature, dict):
            signatures[name] = signature
    return signatures, ambiguous


def apply_gold_signature(output_bundle: dict[str, Any], gold_bundle: dict[str, Any], manifest: dict[str, Any]) -> None:
    gold_signatures, ambiguous_gold = gold_signature_by_name(gold_bundle)
    output_functions, ambiguous_output = unique_functions_by_name(output_bundle)
    for name in sorted(ambiguous_gold):
        manifest["unmatched"].append({"path": str(gold_bundle["root"]), "field": f"FUNCTION_SPEC.SIGNATURE[{name}]", "reason": "ambiguous gold function name"})
    for name in sorted(ambiguous_output):
        manifest["unmatched"].append({"path": str(output_bundle["root"]), "field": f"FUNCTION_SPEC.SIGNATURE[{name}]", "reason": "ambiguous planning function name"})

    for name, item in output_functions.items():
        gold_signature = gold_signatures.get(name)
        if gold_signature is None:
            manifest["unmatched"].append({"path": item["relative_path"], "field": "FUNCTION_SPEC.SIGNATURE", "reason": f"no unique gold function named '{name}'"})
            continue
        replace_field(item["raw"], item["relative_path"], "SIGNATURE", copy.deepcopy(gold_signature), manifest, "function_name_exact", "FUNCTION_SPEC.SIGNATURE")

    for record in file_records(output_bundle):
        raw = record["raw"]
        for block_name in ("HEADER", "SOURCE"):
            block = raw.get(block_name)
            if not isinstance(block, dict):
                continue
            interfaces = block.get("INTERFACE", [])
            if not isinstance(interfaces, list):
                manifest["unmatched"].append({"path": record["relative_path"], "field": f"{block_name}.INTERFACE", "reason": "INTERFACE is not a list"})
                continue
            for index, interface in enumerate(interfaces):
                if not isinstance(interface, dict):
                    continue
                name = str(interface.get("NAME", ""))
                if name in ambiguous_output:
                    manifest["unmatched"].append({"path": record["relative_path"], "field": f"{block_name}.INTERFACE[{index}].SIGNATURE", "reason": f"ambiguous planning function name '{name}'"})
                    continue
                gold_signature = gold_signatures.get(name)
                if gold_signature is None:
                    manifest["unmatched"].append({"path": record["relative_path"], "field": f"{block_name}.INTERFACE[{index}].SIGNATURE", "reason": f"no unique gold function named '{name}'"})
                    continue
                raw_signature = gold_signature.get("RAW")
                if not isinstance(raw_signature, str) or not raw_signature:
                    manifest["unmatched"].append({"path": record["relative_path"], "field": f"{block_name}.INTERFACE[{index}].SIGNATURE", "reason": f"gold function '{name}' has no RAW signature"})
                    continue
                replace_field(interface, record["relative_path"], "SIGNATURE", raw_signature, manifest, "function_name_exact", f"{block_name}.INTERFACE[{index}].SIGNATURE")


def validate_output(spec_root: Path, schema_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": [], "loader": [], "has_errors": False}
    try:
        from agent.coder.specs import load_spec_bundle_from_root
        from agent.planning.validators.coder_schema import validate_coder_spec_bundle_against_schema
    except Exception as exc:  # noqa: BLE001
        result["has_errors"] = True
        result["schema"].append({"level": "error", "code": "import_failed", "message": str(exc), "path": str(REPO_ROOT)})
        return result
    schema_diags = validate_coder_spec_bundle_against_schema(spec_root, schema_root)
    result["schema"] = [diag.__dict__ for diag in schema_diags]
    if any(diag.level == "error" for diag in schema_diags):
        result["has_errors"] = True
        return result
    try:
        bundle = load_spec_bundle_from_root(spec_root, validate_rendered_headers=True)
    except Exception as exc:  # noqa: BLE001
        result["has_errors"] = True
        result["loader"].append({"level": "error", "code": "loader_failed", "message": str(exc), "path": str(spec_root)})
        return result
    result["loader"] = [diag.__dict__ for diag in bundle.diagnostics]
    result["has_errors"] = any(diag.level == "error" for diag in bundle.diagnostics)
    return result


def write_summary(strategy_dir: Path, manifest: dict[str, Any]) -> None:
    lines = [
        f"# Oracle Substitution: {manifest['strategy']}",
        "",
        f"- Status: `{manifest['status']}`",
        f"- Source kind: `{manifest['source_kind']}`",
        f"- Planning input: `{manifest['planning_root']}`",
        f"- Gold input: `{manifest['gold_root']}`",
        f"- Output specs: `{manifest['output_specs_root']}`",
        f"- Replacements: {len(manifest['replacements'])}",
        f"- Unmatched/skipped: {len(manifest['unmatched']) + len(manifest['skipped'])}",
    ]
    if manifest["skipped"]:
        lines.extend(["", "## Skipped Strategy Work"])
        for item in manifest["skipped"]:
            lines.append(f"- `{item['field']}`: {item['reason']}")
    if manifest["unmatched"]:
        lines.extend(["", "## Unmatched"])
        for item in manifest["unmatched"][:60]:
            lines.append(f"- `{item['path']}` `{item['field']}`: {item['reason']}")
    validation = manifest.get("validation")
    if validation:
        lines.extend(["", "## Validation", f"- Has errors: `{validation['has_errors']}`"])
    (strategy_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_strategy(args: argparse.Namespace) -> dict[str, Any]:
    planning_root = args.planning.resolve()
    gold_root = args.gold.resolve()
    output_root = args.output.resolve()
    schema_root = args.schema_root.resolve()
    strategy_dir = output_root / args.strategy
    specs_dir = strategy_dir / "specs"
    if strategy_dir.exists():
        if not args.overwrite:
            raise FailClosed(f"Output strategy directory already exists: {strategy_dir}")
        shutil.rmtree(strategy_dir)
    shutil.copytree(planning_root, specs_dir)

    gold_bundle = scan_bundle(gold_root)
    output_bundle = scan_bundle(specs_dir)
    manifest: dict[str, Any] = {
        "schema_version": "specforge_oracle_substitution_manifest/v1",
        "tool": "oracle_substitute.py",
        "generated_at": now_utc(),
        "strategy": args.strategy,
        "source_kind": "gold_spec_code_derived_oracle",
        "not_protocol_fact": True,
        "planning_root": str(planning_root),
        "gold_root": str(gold_root),
        "output_specs_root": str(specs_dir),
        "status": "ok",
        "replacements": [],
        "unmatched": [],
        "skipped": [],
    }

    if args.strategy == "P+GoldDependency":
        apply_gold_dependency(output_bundle, gold_bundle, manifest)
        for item in output_bundle["files"]:
            write_json(item["path"], item["raw"])
        write_json(output_bundle["module_path"], output_bundle["module"])
    elif args.strategy == "P+GoldType":
        apply_gold_type(output_bundle, gold_bundle, manifest)
        for item in output_bundle["files"]:
            write_json(item["path"], item["raw"])
        write_json(output_bundle["module_path"], output_bundle["module"])
    elif args.strategy == "P+GoldSignature":
        apply_gold_signature(output_bundle, gold_bundle, manifest)
        for item in output_bundle["files"]:
            write_json(item["path"], item["raw"])
        for item in output_bundle["functions"]:
            write_json(item["path"], item["raw"])
    else:
        raise FailClosed(f"Unsupported strategy: {args.strategy}")

    if args.validate:
        manifest["validation"] = validate_output(specs_dir, schema_root)
        if manifest["validation"]["has_errors"]:
            manifest["status"] = "validation_failed"

    write_json(strategy_dir / "substitution_manifest.json", manifest)
    write_summary(strategy_dir, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create minimal oracle substitution spec bundles.")
    parser.add_argument("--planning", required=True, type=Path, help="Planning spec_bundle root")
    parser.add_argument("--gold", required=True, type=Path, help="Gold/example specs root")
    parser.add_argument("--output", required=True, type=Path, help="Output root for strategy directories")
    parser.add_argument("--strategy", choices=STRATEGIES, required=True)
    parser.add_argument("--schema-root", type=Path, default=REPO_ROOT / "specs-example" / "specs_schema")
    parser.add_argument("--validate", action="store_true", help="Run schema and coder loader/header validation on output specs")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing strategy directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = run_strategy(args)
    except FailClosed as exc:
        print(f"ERROR fail_closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"strategy": manifest["strategy"], "status": manifest["status"], "output": manifest["output_specs_root"]}, ensure_ascii=False, indent=2))
    return 1 if manifest["status"] == "validation_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
