from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent.coder.generation import render_header
from agent.coder.header_recipes import render_public_declarations
from agent.coder.models import FileSpec, FunctionSpec, SpecBundle
from agent.coder.specs import (
    canonical_signature_for_header,
    canonical_signature_for_source,
    function_specs_for_file,
    load_spec_bundle_from_root,
)

from .configs import DEFAULT_OUTPUT_ROOT, PROTOCOL_CONFIGS, ProtocolConfig, protocol_keys, rel_to_repo


TRANSFORMER_VERSION = "spec_ablation_s1_transformer/v1"
MANIFEST_SCHEMA = "spec_ablation_s1_transformation_manifest/v1"
EXECUTION_MANIFEST_SCHEMA = "spec_ablation_s1_execution_manifest/v1"

FORBIDDEN_VISIBLE_TERMS: tuple[str, ...] = (
    "PROTOCOL_MODULE_SPEC",
    "FILE_SPEC",
    "FUNCTION_SPEC",
    "ACCESS_PATHS",
    "WIRE_MAPPING",
    "CALL_CONTRACTS",
    "FORBIDDEN_SYMBOLS",
    "TEST_VECTORS",
    "CONSISTENCY_RULES",
    "DOC_REF",
    "TRACE_REFS",
)

_LOCAL_DEPENDENCY_CATEGORIES = ("STRUCT", "FUNC", "VAR")


class TransformationError(RuntimeError):
    """Raised when the deterministic S1 projection cannot be produced safely."""


@dataclass(frozen=True)
class Declaration:
    name: str
    category: str
    declaration: str
    file_trace_id: str
    header_path: str
    source_path: str


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_semicolon(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped.endswith(";") else f"{stripped};"


def _sanitize_text(value: str) -> str:
    return (
        value.replace("\x00", r"\0")
        .replace("\x01", r"\x01")
        .replace("\x02", r"\x02")
        .replace("\x03", r"\x03")
        .replace("\x04", r"\x04")
        .replace("\x05", r"\x05")
        .replace("\x06", r"\x06")
        .replace("\x07", r"\a")
        .replace("\x08", r"\b")
        .replace("\x0b", r"\v")
        .replace("\x0c", r"\f")
    )


def _trace_parent(trace_id: str) -> str:
    return trace_id.rsplit("/", 1)[0] if "/" in trace_id else ""


def _tree_hash(root: Path, *, pattern: str = "*", exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob(pattern) if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _ordered_files(bundle: SpecBundle) -> list[FileSpec]:
    by_path: dict[str, FileSpec] = {}
    for file_spec in bundle.file_specs_by_trace.values():
        if file_spec.header_path:
            by_path[file_spec.header_path] = file_spec
        if file_spec.source_path:
            by_path[file_spec.source_path] = file_spec

    ordered: list[FileSpec] = []
    seen: set[str] = set()
    for module in bundle.modules_in_order:
        for file_path in module.files:
            file_spec = by_path.get(file_path)
            if file_spec is None or file_spec.trace_id in seen:
                continue
            ordered.append(file_spec)
            seen.add(file_spec.trace_id)

    missing = [item for item in bundle.file_specs_by_trace.values() if item.trace_id not in seen]
    return [*ordered, *sorted(missing, key=lambda item: item.trace_id)]


def _render_single_data_item(file_spec: FileSpec, item: dict[str, Any]) -> str:
    name = str(item.get("NAME", "")).strip()
    if re.match(r"^(?:struct|union|enum)\s+[A-Za-z_][A-Za-z0-9_]*$", name):
        return f"{name};"
    public_item = dict(item)
    public_item["VISIBILITY"] = "PUBLIC"
    single = FileSpec(
        trace_id=file_spec.trace_id,
        role=file_spec.role,
        lang=file_spec.lang,
        header_path=file_spec.header_path,
        source_path=file_spec.source_path,
        header_dependencies=file_spec.header_dependencies,
        header_system_dependencies=file_spec.header_system_dependencies,
        source_dependencies=file_spec.source_dependencies,
        header_data=[public_item],
        source_data=[],
        header_interfaces=[],
        source_interfaces=[],
        spec_path=file_spec.spec_path,
        raw=file_spec.raw,
    )
    rendered = render_public_declarations(single).strip()
    if rendered:
        return rendered
    if str(item.get("KIND", "")).upper() == "TYPE" and name.endswith("_t"):
        return f"typedef struct {name[:-2]} {name};"
    return ""


def _declaration_index(bundle: SpecBundle) -> dict[tuple[str, str], list[Declaration]]:
    index: dict[tuple[str, str], list[Declaration]] = {}

    def add(name: str, category: str, declaration: str, file_spec: FileSpec) -> None:
        name = name.strip()
        declaration = declaration.strip()
        if not name or not declaration:
            return
        key = (category, name)
        item = Declaration(
            name=name,
            category=category,
            declaration=declaration,
            file_trace_id=file_spec.trace_id,
            header_path=file_spec.header_path,
            source_path=file_spec.source_path,
        )
        entries = index.setdefault(key, [])
        if item not in entries:
            entries.append(item)

    for file_spec in bundle.file_specs_by_trace.values():
        for data_item in [*file_spec.header_data, *file_spec.source_data]:
            name = str(data_item.get("NAME", "")).strip()
            declaration = _render_single_data_item(file_spec, data_item)
            kind = str(data_item.get("KIND", "")).upper()
            if kind == "TYPE":
                add(name, "STRUCT", declaration, file_spec)
                add(name, "TYPE", declaration, file_spec)
            elif kind in {"VAR", "CONST", "MACRO"}:
                add(name, "VAR", declaration, file_spec)
                add(name, kind, declaration, file_spec)

        for source_item in file_spec.source_interfaces:
            add(
                source_item.name,
                "FUNC",
                _ensure_semicolon(canonical_signature_for_source(bundle, source_item)),
                file_spec,
            )
        for header_item in file_spec.header_interfaces:
            add(
                header_item.name,
                "FUNC",
                _ensure_semicolon(canonical_signature_for_header(bundle, file_spec, header_item)),
                file_spec,
            )

    for entries in index.values():
        entries.sort(key=lambda item: (item.file_trace_id, item.name, item.declaration))
    return index


def _choose_declaration(
    category: str,
    name: str,
    current_file: FileSpec,
    index: dict[tuple[str, str], list[Declaration]],
) -> tuple[Declaration | None, str]:
    candidates = index.get((category, name), [])
    if not candidates:
        return None, "external_name_only"

    same_parent = [item for item in candidates if item.file_trace_id == current_file.trace_id]
    if len(same_parent) == 1:
        return same_parent[0], "same_parent"
    if len(same_parent) > 1:
        raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: same parent")

    dependency_paths = set(current_file.header_dependencies) | set(current_file.source_dependencies)
    dependency_candidates = [item for item in candidates if item.header_path in dependency_paths]
    if len(dependency_candidates) == 1:
        return dependency_candidates[0], "dependency_provider"
    if len(dependency_candidates) > 1:
        providers = ", ".join(sorted(item.file_trace_id for item in dependency_candidates))
        raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: dependency providers {providers}")

    if len(candidates) == 1:
        return candidates[0], "global_unique"

    providers = ", ".join(sorted(item.file_trace_id for item in candidates))
    raise TransformationError(f"Ambiguous RELY.{category} '{name}' in {current_file.trace_id}: {providers}")


def _format_rely(entries: list[dict[str, str]]) -> str:
    if not entries:
        return "None."
    blocks: list[str] = []
    for entry in entries:
        role = entry.get("role", "")
        declaration = entry.get("declaration", "")
        if declaration:
            blocks.append(f"- {entry['category']} `{entry['name']}`\n  role: {role}\n```c\n{declaration}\n```")
        else:
            blocks.append(f"- {entry['category']} `{entry['name']}`\n  role: {role}\n  declaration: external dependency; canonical declaration unavailable.")
    return "\n\n".join(blocks)


def _format_specification(function_spec: FunctionSpec) -> str:
    body = function_spec.body if isinstance(function_spec.body, dict) else {}
    if function_spec.function_type.upper() == "EVENT":
        precondition = "\n".join(
            f"- {label}: {body.get(key, '')}".strip()
            for label, key in (("Trigger", "TRIGGER"), ("Precondition", "PRECONDITION"), ("Input", "INPUT"))
            if str(body.get(key, "")).strip()
        )
        postcondition = "\n".join(
            f"- {label}: {body.get(key, '')}".strip()
            for label, key in (("State Change", "STATE_CHANGE"), ("Response", "RESPONSE"))
            if str(body.get(key, "")).strip()
        )
        invariants = "- None specified."
        action = str(body.get("ACTION", "")).strip()
    else:
        precondition = str(body.get("INPUT", "")).strip()
        postcondition = str(body.get("OUTPUT", "")).strip()
        raw_invariants = body.get("INVARIANTS_USED", [])
        invariants = "\n".join(f"- {item}" for item in raw_invariants if str(item).strip()) if isinstance(raw_invariants, list) else str(raw_invariants).strip()
        action = str(body.get("ACTION", "")).strip()

    def bullet(value: str) -> str:
        value = value.strip()
        if not value:
            return "- None specified."
        return value if value.startswith("- ") else f"- {value}"

    return "\n".join(
        [
            "**Pre-Condition**:",
            bullet(precondition),
            "",
            "**Post-Condition**:",
            bullet(postcondition),
            "",
            "**Invariant**:",
            invariants.strip() or "- None specified.",
            "",
            "**System Algorithm**:",
            bullet(action),
        ]
    )


def _function_projection(
    function_spec: FunctionSpec,
    file_spec: FileSpec,
    index: dict[tuple[str, str], list[Declaration]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    rely_entries: list[dict[str, str]] = []
    resolutions: list[dict[str, str]] = []
    for category in _LOCAL_DEPENDENCY_CATEGORIES:
        for raw_entry in function_spec.rely.get(category, []):
            if not isinstance(raw_entry, dict):
                continue
            name = str(raw_entry.get("NAME", "")).strip()
            if not name:
                continue
            declaration, strategy = _choose_declaration(category, name, file_spec, index)
            rely_entries.append(
                {
                    "category": category,
                    "name": name,
                    "role": str(raw_entry.get("ROLE", "")).strip(),
                    "declaration": declaration.declaration if declaration is not None else "",
                }
            )
            resolutions.append(
                {
                    "category": category,
                    "name": name,
                    "strategy": strategy,
                    "provider_trace_id": declaration.file_trace_id if declaration is not None else "",
                    "provider_header_path": declaration.header_path if declaration is not None else "",
                }
            )

    prompt = f"Implement function `{function_spec.signature.name}`. Responsibility: {function_spec.role}"
    blocks = {
        "PROMPT": prompt,
        "RELY": _format_rely(rely_entries),
        "GUARANTEE": f"```c\n{_ensure_semicolon(function_spec.signature.raw)}\n```",
        "SPECIFICATION": _format_specification(function_spec),
    }
    return blocks, resolutions


def _render_function_spec(blocks: dict[str, str]) -> str:
    ordered = ("PROMPT", "RELY", "GUARANTEE", "SPECIFICATION")
    return "\n\n".join(f"[{name}]\n{_sanitize_text(blocks[name].strip())}" for name in ordered) + "\n"


def _specfs_header(rendered_header: str) -> str:
    lines: list[str] = []
    for line in rendered_header.splitlines():
        stripped = line.strip()
        if stripped in {"#pragma once", "#ifdef __cplusplus", 'extern "C" {', "#endif", "}"}:
            continue
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(line.rstrip())
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _visible_leakage_scan(root: Path) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for path in sorted((root / "specfs_projection").rglob("*")):
        if not path.is_file() or path.suffix not in {".spec", ".header"}:
            continue
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN_VISIBLE_TERMS:
            if term in text:
                violations.append({"path": path.relative_to(root).as_posix(), "term": term})
    return {
        "status": "passed" if not violations else "failed",
        "forbidden_terms": list(FORBIDDEN_VISIBLE_TERMS),
        "violations": violations,
    }


def _execution_manifest(
    config: ProtocolConfig,
    bundle: SpecBundle,
    ordered_files: list[FileSpec],
    function_artifacts: dict[str, str],
    header_artifacts: dict[str, str],
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for order, file_spec in enumerate(ordered_files):
        functions = [
            {
                "trace_id": spec.trace_id,
                "name": spec.signature.name,
                "artifact_path": function_artifacts[spec.trace_id],
            }
            for spec in function_specs_for_file(bundle, file_spec)
        ]
        sources.append(
            {
                "order": order,
                "file_trace_id": file_spec.trace_id,
                "source_path": file_spec.source_path,
                "header_path": file_spec.header_path,
                "header_artifact": header_artifacts.get(file_spec.header_path, ""),
                "functions": functions,
            }
        )
    return {
        "schema_version": EXECUTION_MANIFEST_SCHEMA,
        "protocol": config.protocol,
        "visibility": "hidden_evaluator_only",
        "note": "This manifest is evaluator control data and must not be inserted into S1 generation or repair prompts.",
        "sources": sources,
    }


def _loader_validation(bundle: SpecBundle) -> dict[str, Any]:
    diagnostics = [
        {
            "level": item.level,
            "code": item.code,
            "message": item.message,
            "path": item.path or "",
        }
        for item in bundle.diagnostics
    ]
    return {
        "status": "failed" if any(item["level"] == "error" for item in diagnostics) else "passed",
        "diagnostics": diagnostics,
    }


def _build_transformation(config: ProtocolConfig, root: Path) -> dict[str, Any]:
    bundle = load_spec_bundle_from_root(config.specs_root, validate_rendered_headers=False)
    loader_validation = _loader_validation(bundle)
    if loader_validation["status"] != "passed":
        codes = ", ".join(item["code"] for item in loader_validation["diagnostics"] if item["level"] == "error")
        raise TransformationError(f"Source oracle failed loader validation: {codes}")

    specfs_root = root / "specfs_projection"
    functions_root = specfs_root / "functions"
    headers_root = specfs_root / "headers"
    ordered_files = _ordered_files(bundle)
    declaration_index = _declaration_index(bundle)

    header_artifacts: dict[str, str] = {}
    header_records: list[dict[str, str]] = []
    for file_spec in ordered_files:
        if not file_spec.header_path:
            continue
        rendered = _specfs_header(render_header(bundle, file_spec))
        artifact = headers_root / f"{file_spec.header_path}.header"
        _write_text(artifact, rendered)
        relative = artifact.relative_to(root).as_posix()
        header_artifacts[file_spec.header_path] = relative
        header_records.append(
            {
                "header_path": file_spec.header_path,
                "artifact_path": relative,
                "sha256": _sha256_text(rendered),
            }
        )

    function_artifacts: dict[str, str] = {}
    function_records: list[dict[str, Any]] = []
    rely_resolutions: list[dict[str, str]] = []
    for file_spec in ordered_files:
        for function_spec in function_specs_for_file(bundle, file_spec):
            blocks, resolutions = _function_projection(function_spec, file_spec, declaration_index)
            content = _render_function_spec(blocks)
            artifact = functions_root / f"{function_spec.trace_id}.spec"
            _write_text(artifact, content)
            relative = artifact.relative_to(root).as_posix()
            function_artifacts[function_spec.trace_id] = relative
            function_records.append(
                {
                    "trace_id": function_spec.trace_id,
                    "name": function_spec.signature.name,
                    "source_path": file_spec.source_path,
                    "artifact_path": relative,
                    "sha256": _sha256_text(content),
                }
            )
            for resolution in resolutions:
                rely_resolutions.append({"trace_id": function_spec.trace_id, **resolution})

    execution_manifest = _execution_manifest(config, bundle, ordered_files, function_artifacts, header_artifacts)
    _write_json(root / "execution_manifest.json", execution_manifest)

    leakage = _visible_leakage_scan(root)
    if leakage["status"] != "passed":
        raise TransformationError(f"S1 visible artifact leakage detected: {leakage['violations']}")

    output_payload_hash = _tree_hash(root, exclude={"transformation_manifest.json"})
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "transformer_version": TRANSFORMER_VERSION,
        "protocol": config.protocol,
        "source_root": rel_to_repo(config.specs_root),
        "output_root": ".",
        "counts": {
            "files": len(bundle.file_specs_by_trace),
            "functions": len(function_records),
            "headers": len(header_records),
        },
        "hashes": {
            "source_specs_tree_sha256": _tree_hash(config.specs_root, pattern="*_spec.json"),
            "output_payload_sha256": output_payload_hash,
        },
        "artifacts": {
            "functions": function_records,
            "headers": header_records,
            "execution_manifest": "execution_manifest.json",
        },
        "validation": {
            "source_loader": loader_validation,
            "leakage_scan": leakage,
        },
        "rely_resolution": rely_resolutions,
    }
    manifest["transformation_hash"] = _sha256_text(_canonical_json(manifest))
    _write_json(root / "transformation_manifest.json", manifest)
    return manifest


def transform_protocol(protocol: str, output_dir: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    if protocol not in PROTOCOL_CONFIGS:
        raise TransformationError(f"Unsupported protocol '{protocol}'")
    target = Path(output_dir)
    if target.exists():
        if not overwrite and any(target.iterdir()):
            raise TransformationError(f"Output directory already exists: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return _build_transformation(PROTOCOL_CONFIGS[protocol], target)


def transform_many(protocols: Iterable[str], output_root: str | Path, *, overwrite: bool = False) -> list[dict[str, Any]]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    selected: list[str] = []
    for value in protocols:
        selected.extend(protocol_keys(value))
    if not selected:
        selected = protocol_keys("all")
    selected = list(dict.fromkeys(selected))
    return [transform_protocol(protocol, root / protocol, overwrite=overwrite) for protocol in selected]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministically project Full-SpecForge specs to S1 SpecFS-Flat artifacts.")
    parser.add_argument("--protocol", action="append", default=[], help="Protocol(s) to transform: mqtt/http/coap/smtp/all")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_ROOT), help="Output root; protocol subdirectories are created under it")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing protocol output directories")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifests = transform_many(args.protocol or ["all"], args.output, overwrite=args.overwrite)
    summaries = [
        {
            "protocol": manifest["protocol"],
            "transformation_hash": manifest["transformation_hash"],
            "counts": manifest["counts"],
        }
        for manifest in manifests
    ]
    print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
