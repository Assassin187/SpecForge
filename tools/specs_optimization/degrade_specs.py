#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KNOWN_KINDS = {"PROTOCOL_MODULE_SPEC", "FILE_SPEC", "FUNCTION_SPEC"}
DEFAULT_PROFILES = [
    "full",
    "no_behavior_detail",
    "no_wire_binding",
    "no_calls",
    "min_interface",
    "no_test_vectors",
    "sidecar_only_traceability",
]
MINIMAL_TEXT = "detail removed by diagnostic degradation profile"


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


def scan_specs(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not root.exists():
        raise FailClosed(f"Spec root does not exist: {root}")
    specs: list[tuple[Path, dict[str, Any]]] = []
    module_specs = 0
    for path in sorted(root.rglob("*_spec.json")):
        raw = read_json(path)
        kind = raw.get("KIND")
        if kind not in KNOWN_KINDS:
            raise FailClosed(f"Unsupported or missing KIND '{kind}' in '{path}'")
        if kind == "PROTOCOL_MODULE_SPEC":
            module_specs += 1
        specs.append((path, raw))
    if not specs:
        raise FailClosed(f"No *_spec.json files found under '{root}'")
    if module_specs != 1:
        raise FailClosed(f"Expected exactly one PROTOCOL_MODULE_SPEC under '{root}', found {module_specs}")
    return specs


def note_action(
    manifest: dict[str, Any],
    path: Path,
    field: str,
    operation: str,
    before: Any,
    after: Any,
) -> None:
    manifest["mutations"].append(
        {
            "path": str(path),
            "field": field,
            "operation": operation,
            "before": before,
            "after": after,
        }
    )


def note_skipped(manifest: dict[str, Any], path: str, field: str, reason: str) -> None:
    manifest["skipped"].append({"path": path, "field": field, "reason": reason})


def remove_optional(raw: dict[str, Any], path: Path, field: str, manifest: dict[str, Any]) -> bool:
    if field not in raw:
        return False
    before = raw.pop(field)
    note_action(manifest, path, field, "delete_optional_field", summarize(before), None)
    return True


def set_list(container: dict[str, Any], path: Path, field: str, manifest: dict[str, Any], display_field: str | None = None) -> bool:
    if field not in container:
        return False
    before = container.get(field)
    container[field] = []
    note_action(manifest, path, display_field or field, "clear_array", summarize(before), [])
    return True


def summarize(value: Any) -> Any:
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(value.keys())}
    return value


def weaken_contracts(raw: dict[str, Any], path: Path, manifest: dict[str, Any]) -> int:
    changed = 0
    source = raw.get("SOURCE")
    if not isinstance(source, dict):
        return changed
    interfaces = source.get("INTERFACE")
    if not isinstance(interfaces, list):
        return changed
    for index, item in enumerate(interfaces):
        if not isinstance(item, dict):
            continue
        contract = item.get("CONTRACT")
        if not isinstance(contract, dict):
            continue
        before = copy.deepcopy(contract)
        contract["PRECONDITION"] = [{"TEXT": MINIMAL_TEXT}]
        contract["POSTCONDITION"] = [{"TEXT": MINIMAL_TEXT}]
        note_action(
            manifest,
            path,
            f"SOURCE.INTERFACE[{index}].CONTRACT",
            "weaken_contract_text",
            summarize(before),
            summarize(contract),
        )
        changed += 1
    return changed


def weaken_behavior(raw: dict[str, Any], path: Path, manifest: dict[str, Any]) -> int:
    changed = 0
    logic = raw.get("LOGIC")
    if isinstance(logic, dict):
        before = copy.deepcopy(logic)
        logic["INPUT"] = ""
        logic["ACTION"] = MINIMAL_TEXT
        logic["OUTPUT"] = ""
        logic["INVARIANTS_USED"] = []
        note_action(manifest, path, "LOGIC", "weaken_behavior_text", summarize(before), summarize(logic))
        changed += 1
    event = raw.get("EVENT")
    if isinstance(event, dict):
        before = copy.deepcopy(event)
        event["TRIGGER"] = ""
        event["PRECONDITION"] = ""
        event["INPUT"] = ""
        event["ACTION"] = MINIMAL_TEXT
        event["STATE_CHANGE"] = MINIMAL_TEXT
        event["RESPONSE"] = ""
        if not str(event.get("EVENT_TYPE", "")).strip():
            event["EVENT_TYPE"] = "degraded_event"
        note_action(manifest, path, "EVENT", "weaken_behavior_text", summarize(before), summarize(event))
        changed += 1
    return changed


def clear_rely_func(raw: dict[str, Any], path: Path, manifest: dict[str, Any]) -> bool:
    rely = raw.get("RELY")
    if not isinstance(rely, dict):
        return False
    if "FUNC" not in rely:
        return False
    before = rely.get("FUNC")
    rely["FUNC"] = []
    note_action(manifest, path, "RELY.FUNC", "clear_array", summarize(before), [])
    return True


def minimize_rely(raw: dict[str, Any], path: Path, manifest: dict[str, Any]) -> bool:
    rely = raw.get("RELY")
    if not isinstance(rely, dict):
        return False
    before = copy.deepcopy(rely)
    rely["STRUCT"] = []
    rely["FUNC"] = []
    rely["VAR"] = []
    note_action(manifest, path, "RELY", "minimize_required_rely", summarize(before), summarize(rely))
    return True


def find_traceability_keys(value: Any, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            next_prefix = f"{prefix}.{key}"
            upper = str(key).upper()
            if upper == "TRACEABILITY" or upper.startswith("TRACEABILITY_"):
                hits.append(next_prefix)
            hits.extend(find_traceability_keys(child, next_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(find_traceability_keys(child, f"{prefix}[{index}]"))
    return hits


def apply_profile(raw: dict[str, Any], rel_path: Path, profile: str, manifest: dict[str, Any], sidecar: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    kind = raw.get("KIND")

    def inc(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    if profile == "full":
        return counts

    if profile == "no_behavior_detail":
        if kind == "FUNCTION_SPEC":
            inc("behavior", weaken_behavior(raw, rel_path, manifest))
        if kind == "FILE_SPEC":
            inc("contracts", weaken_contracts(raw, rel_path, manifest))
        return counts

    if profile == "no_wire_binding":
        if kind == "FUNCTION_SPEC":
            if remove_optional(raw, rel_path, "WIRE_MAPPING", manifest):
                inc("wire_mapping")
            if raw.get("ACCESS_PATHS"):
                note_skipped(
                    manifest,
                    str(rel_path),
                    "ACCESS_PATHS",
                    "kept because this profile cannot safely distinguish wire-only paths from general state access paths",
                )
        elif kind == "FILE_SPEC" and raw.get("ACCESS_PATHS"):
            note_skipped(
                manifest,
                str(rel_path),
                "ACCESS_PATHS",
                "kept because file-level access paths may be public API validation data",
            )
        return counts

    if profile == "no_calls":
        if kind == "FUNCTION_SPEC":
            if clear_rely_func(raw, rel_path, manifest):
                inc("rely_func")
            if set_list(raw, rel_path, "CALL_CONTRACTS", manifest):
                inc("call_contracts")
        elif kind == "FILE_SPEC":
            if set_list(raw, rel_path, "CALL_CONTRACTS", manifest):
                inc("call_contracts")
        return counts

    if profile == "min_interface":
        if kind == "PROTOCOL_MODULE_SPEC":
            remove_optional(raw, rel_path, "PUBLIC_SYMBOLS", manifest)
            remove_optional(raw, rel_path, "FORBIDDEN_SYMBOLS", manifest)
            remove_optional(raw, rel_path, "TEST_VECTORS", manifest)
            protocol = raw.get("PROTOCOL")
            if isinstance(protocol, dict) and "SCOPE" in protocol:
                before = protocol.pop("SCOPE")
                note_action(manifest, rel_path, "PROTOCOL.SCOPE", "delete_optional_field", summarize(before), None)
            for index, module in enumerate(raw.get("MODULES", [])):
                if isinstance(module, dict) and set_list(module, rel_path, "DOC_REF", manifest, f"MODULES[{index}].DOC_REF"):
                    inc("doc_ref")
        elif kind == "FILE_SPEC":
            file_meta = raw.get("FILE")
            if isinstance(file_meta, dict) and set_list(file_meta, rel_path, "DOC_REF", manifest, "FILE.DOC_REF"):
                inc("doc_ref")
            for field in ("PUBLIC_SYMBOLS", "ACCESS_PATHS", "CALL_CONTRACTS", "FORBIDDEN_SYMBOLS", "TEST_VECTORS"):
                remove_optional(raw, rel_path, field, manifest)
            inc("contracts", weaken_contracts(raw, rel_path, manifest))
        elif kind == "FUNCTION_SPEC":
            for field in ("PUBLIC_SYMBOLS", "ACCESS_PATHS", "WIRE_MAPPING", "CALL_CONTRACTS", "FORBIDDEN_SYMBOLS", "TEST_VECTORS"):
                remove_optional(raw, rel_path, field, manifest)
            if minimize_rely(raw, rel_path, manifest):
                inc("rely")
            inc("behavior", weaken_behavior(raw, rel_path, manifest))
        return counts

    if profile == "no_test_vectors":
        if remove_optional(raw, rel_path, "TEST_VECTORS", manifest):
            inc("test_vectors")
        return counts

    if profile == "sidecar_only_traceability":
        traceability_hits = find_traceability_keys(raw)
        if traceability_hits:
            joined = ", ".join(traceability_hits)
            raise FailClosed(f"Refusing to silently remove non-schema traceability fields in '{rel_path}': {joined}")
        if kind == "PROTOCOL_MODULE_SPEC":
            for index, module in enumerate(raw.get("MODULES", [])):
                if not isinstance(module, dict):
                    continue
                doc_ref = module.get("DOC_REF")
                if doc_ref:
                    sidecar["module_doc_refs"].append({"path": str(rel_path), "module": module.get("NAME", ""), "DOC_REF": doc_ref})
                if set_list(module, rel_path, "DOC_REF", manifest, f"MODULES[{index}].DOC_REF"):
                    inc("doc_ref")
        elif kind == "FILE_SPEC":
            file_meta = raw.get("FILE")
            if isinstance(file_meta, dict):
                doc_ref = file_meta.get("DOC_REF")
                if doc_ref:
                    sidecar["file_doc_refs"].append({"path": str(rel_path), "TRACE_ID": file_meta.get("TRACE_ID", ""), "DOC_REF": doc_ref})
                if set_list(file_meta, rel_path, "DOC_REF", manifest, "FILE.DOC_REF"):
                    inc("doc_ref")
        return counts

    raise FailClosed(f"Unsupported profile: {profile}")


def expected_fields(profile: str) -> list[str]:
    return {
        "full": [],
        "no_behavior_detail": ["FUNCTION_SPEC.LOGIC/EVENT", "FILE_SPEC.SOURCE.INTERFACE.CONTRACT"],
        "no_wire_binding": ["FUNCTION_SPEC.WIRE_MAPPING"],
        "no_calls": ["FUNCTION_SPEC.RELY.FUNC", "CALL_CONTRACTS"],
        "min_interface": ["DOC_REF", "PUBLIC_SYMBOLS", "ACCESS_PATHS", "CALL_CONTRACTS", "FORBIDDEN_SYMBOLS", "TEST_VECTORS", "RELY", "LOGIC/EVENT"],
        "no_test_vectors": ["TEST_VECTORS"],
        "sidecar_only_traceability": ["MODULES[].DOC_REF", "FILE.DOC_REF"],
    }[profile]


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


def write_summary(profile_dir: Path, manifest: dict[str, Any]) -> None:
    lines = [
        f"# Degradation Profile: {manifest['profile']}",
        "",
        f"- Status: `{manifest['status']}`",
        f"- Input: `{manifest['input_root']}`",
        f"- Output specs: `{manifest['output_specs_root']}`",
        f"- Mutations: {len(manifest['mutations'])}",
        f"- Skipped: {len(manifest['skipped'])}",
    ]
    validation = manifest.get("validation")
    if validation:
        lines.append(f"- Validation errors: `{validation.get('has_errors')}`")
    lines.extend(["", "## Skipped"])
    if manifest["skipped"]:
        for item in manifest["skipped"][:50]:
            lines.append(f"- `{item['path']}` `{item['field']}`: {item['reason']}")
    else:
        lines.append("- None")
    (profile_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_profiles(value: str) -> list[str]:
    profiles = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in profiles if item not in DEFAULT_PROFILES]
    if unknown:
        raise argparse.ArgumentTypeError(f"Unknown profiles: {', '.join(unknown)}")
    return profiles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate degraded copies of coder-facing specs.")
    parser.add_argument("--input", required=True, type=Path, help="Input specs root, for example specs-example/mqtt_specs")
    parser.add_argument("--output", required=True, type=Path, help="Output root for generated profile directories")
    parser.add_argument("--profiles", type=parse_profiles, default=DEFAULT_PROFILES, help="Comma-separated profiles; default is all profiles")
    parser.add_argument("--schema-root", type=Path, default=REPO_ROOT / "specs-example" / "specs_schema")
    parser.add_argument("--validate", action="store_true", help="Run schema and coder loader/header validation on generated specs")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing profile directories")
    return parser


def run_profile(input_root: Path, output_root: Path, schema_root: Path, profile: str, validate: bool, overwrite: bool) -> dict[str, Any]:
    profile_dir = output_root / profile
    specs_dir = profile_dir / "specs"
    if profile_dir.exists():
        if not overwrite:
            raise FailClosed(f"Output profile directory already exists: {profile_dir}")
        shutil.rmtree(profile_dir)
    shutil.copytree(input_root, specs_dir)
    scan_specs(specs_dir)

    manifest: dict[str, Any] = {
        "schema_version": "specforge_specs_degradation_manifest/v1",
        "tool": "degrade_specs.py",
        "generated_at": now_utc(),
        "profile": profile,
        "input_root": str(input_root),
        "output_specs_root": str(specs_dir),
        "source_kind": "gold_spec_code_derived",
        "status": "ok",
        "mutations": [],
        "skipped": [],
    }
    sidecar: dict[str, Any] = {
        "schema_version": "specforge_traceability_sidecar/v1",
        "source_kind": "gold_spec_code_derived",
        "module_doc_refs": [],
        "file_doc_refs": [],
    }

    profile_counts: dict[str, int] = {}
    for path, raw in scan_specs(specs_dir):
        rel_path = path.relative_to(specs_dir)
        counts = apply_profile(raw, rel_path, profile, manifest, sidecar)
        for key, value in counts.items():
            profile_counts[key] = profile_counts.get(key, 0) + value
        write_json(path, raw)

    if profile == "sidecar_only_traceability":
        sidecar_path = profile_dir / "traceability_sidecar.json"
        write_json(sidecar_path, sidecar)
        manifest["traceability_sidecar_path"] = str(sidecar_path)

    for field in expected_fields(profile):
        if not manifest["mutations"] and profile != "full":
            note_skipped(manifest, ".", field, "field did not appear in this specs bundle or profile had no safe mutation")
            continue
        if profile_counts and not any(field_part in str(item.get("field", "")) for item in manifest["mutations"] for field_part in field.split("/")):
            note_skipped(manifest, ".", field, "field did not appear in this specs bundle or was not safely applicable")

    if validate:
        manifest["validation"] = validate_output(specs_dir, schema_root)
        if manifest["validation"]["has_errors"]:
            manifest["status"] = "validation_failed"

    write_json(profile_dir / "manifest.json", manifest)
    write_summary(profile_dir, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_root = args.input.resolve()
    output_root = args.output.resolve()
    schema_root = args.schema_root.resolve()
    try:
        scan_specs(input_root)
        output_root.mkdir(parents=True, exist_ok=True)
        manifests = [
            run_profile(input_root, output_root, schema_root, profile, args.validate, args.overwrite)
            for profile in args.profiles
        ]
    except FailClosed as exc:
        print(f"ERROR fail_closed: {exc}", file=sys.stderr)
        return 2
    failed = [item for item in manifests if item["status"] != "ok"]
    print(json.dumps({"profiles": [item["profile"] for item in manifests], "failed": [item["profile"] for item in failed]}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
