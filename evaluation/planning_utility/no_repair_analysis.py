from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .repair_diagnostics import create_diagnostic_snapshot, iter_project_files


_FUNCTION_DEFINITION_RE = re.compile(
    r"(?m)^\s*(?P<static>static\s+)?(?:[A-Za-z_][\w\s\*\(\),]*?\s+)+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
)
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|FIXME|XXX|unimplemented|placeholder|whatever)\b|not\s+implemented", re.IGNORECASE)
_COMMENTED_CASE_RE = re.compile(r"\bcase\s*/\*.*?\*/\s*:", re.IGNORECASE)
_VOID_CAST_RE = re.compile(r"\(\s*void\s*\)\s*[A-Za-z_]\w*\s*;")
_DUMMY_RETURN_RE = re.compile(r"return\s+(?:0|-1|NULL|false|true)\s*;", re.IGNORECASE)


def _source_hashes(project_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(project_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in iter_project_files(project_dir)
    }


def _hash_preservation(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    changed = sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    return {
        "preserved": not changed and not added and not removed,
        "before": before,
        "after": after,
        "changed_paths": changed,
        "added_paths": added,
        "removed_paths": removed,
    }


def _required_functions(specs_root: Path) -> list[dict[str, str]]:
    file_sources: dict[str, str] = {}
    function_specs: list[tuple[str, str]] = []
    for path in sorted(specs_root.rglob("*_spec.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("KIND") == "FILE_SPEC":
            trace_id = str((raw.get("FILE") or {}).get("TRACE_ID", ""))
            source = str((raw.get("SOURCE") or {}).get("PATH", ""))
            if trace_id:
                file_sources[trace_id] = _normalize_project_path(source)
        elif raw.get("KIND") == "FUNCTION_SPEC":
            trace_id = str(raw.get("TRACE_ID", ""))
            signature = raw.get("SIGNATURE") if isinstance(raw.get("SIGNATURE"), dict) else {}
            name = str(signature.get("NAME", ""))
            if not name:
                match = re.search(r"\b([A-Za-z_]\w*)\s*\(", str(signature.get("RAW", "")))
                name = match.group(1) if match else ""
            function_specs.append((trace_id, name))
    functions = [
        {
            "trace_id": trace_id,
            "name": name,
            "required_source": file_sources.get(trace_id.rsplit("/", 1)[0] if "/" in trace_id else "", ""),
        }
        for trace_id, name in function_specs
    ]
    return sorted(functions, key=lambda item: (item["trace_id"], item["name"]))


def _normalize_project_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("../"):
        normalized = normalized[3:]
    return normalized.removeprefix("./")


def _matching_brace(text: str, opening: int) -> int | None:
    depth = 0
    state = "code"
    index = opening
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"string", "char"}:
            if char == "\\":
                index += 1
            elif char == ('"' if state == "string" else "'"):
                state = "code"
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _definition_bodies(project_dir: Path) -> dict[str, list[dict[str, Any]]]:
    definitions: dict[str, list[dict[str, Any]]] = {}
    for path in iter_project_files(project_dir):
        if path.suffix != ".c":
            continue
        relative = path.relative_to(project_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _FUNCTION_DEFINITION_RE.finditer(text):
            end = _matching_brace(text, match.end() - 1)
            if end is None:
                continue
            body = text[match.end() : end]
            reasons = _stub_reasons(body)
            definitions.setdefault(match.group("name"), []).append(
                {
                    "path": relative,
                    "storage": "static" if match.group("static") else "extern",
                    "is_stub": bool(reasons),
                    "stub_reasons": reasons,
                    "line": text.count("\n", 0, match.start()) + 1,
                }
            )
    return definitions


def _stub_reasons(body: str) -> list[str]:
    reasons: list[str] = []
    code = _COMMENT_RE.sub(" ", body).strip()
    if _PLACEHOLDER_RE.search(body):
        reasons.append("placeholder_marker")
    without_void_casts = _VOID_CAST_RE.sub(" ", code).strip()
    if not without_void_casts:
        reasons.append("empty_body")
    elif _DUMMY_RETURN_RE.fullmatch(without_void_casts):
        reasons.append("fixed_dummy_return")
    return reasons


def _definition_coverage(required: list[dict[str, str]], definitions: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    covered = 0
    for item in required:
        matches = definitions.get(item["name"], []) if item["name"] else []
        non_stub = sum(not definition["is_stub"] for definition in matches)
        if not item["name"]:
            status = "invalid_spec"
        elif not matches:
            status = "missing"
        elif len(matches) > 1:
            status = "duplicate"
        elif non_stub != 1:
            status = "stub"
        else:
            status = "covered"
            covered += 1
        details.append(
            {
                **item,
                "definition_count": len(matches),
                "non_stub_definition_count": non_stub,
                "status": status,
                "definitions": [
                    {key: definition[key] for key in ("path", "storage", "is_stub", "stub_reasons")}
                    for definition in matches
                ],
            }
        )
    required_count = len(required)
    return {
        "required_count": required_count,
        "covered_count": covered,
        "coverage_rate": covered / required_count if required_count else None,
        "passed": bool(required_count) and covered == required_count,
        "functions": details,
    }


def _placeholder_report(project_dir: Path, definitions: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for path in iter_project_files(project_dir):
        if path.suffix != ".c":
            continue
        relative = path.relative_to(project_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), 1):
            marker = _PLACEHOLDER_RE.search(line)
            if marker:
                items.append({"path": relative, "line": line_number, "kind": "marker", "evidence": marker.group(0)})
            if _COMMENTED_CASE_RE.search(line):
                items.append({"path": relative, "line": line_number, "kind": "commented_case_label", "evidence": line.strip()})
    for name, entries in definitions.items():
        for entry in entries:
            for reason in entry["stub_reasons"]:
                if reason in {"empty_body", "fixed_dummy_return"}:
                    items.append(
                        {
                            "path": entry["path"],
                            "line": entry["line"],
                            "kind": reason,
                            "evidence": name,
                        }
                    )
    items.sort(key=lambda item: (item["path"], item["line"], item["kind"], item["evidence"]))
    return {"count": len(items), "passed": not items, "items": items}


def _near_empty_sources(coverage: dict[str, Any]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for function in coverage["functions"]:
        by_source.setdefault(function["required_source"], []).append(function)
    sources: list[dict[str, Any]] = []
    for path, functions in sorted(by_source.items()):
        implemented = sorted(
            function["name"]
            for function in functions
            if any(definition["path"] == path and not definition["is_stub"] for definition in function["definitions"])
        )
        if implemented:
            continue
        reasons = ["missing_required_source_mapping"] if not path else ["no_non_stub_specified_definition"]
        sources.append(
            {
                "path": path,
                "specified_functions": [function["name"] for function in functions],
                "non_stub_defined_functions": implemented,
                "reasons": reasons,
            }
        )
    return {"count": len(sources), "passed": not sources, "sources": sources}


def analyze_no_repair(project_dir: str | Path, specs_root: str | Path, binary_name: str) -> dict[str, Any]:
    project = Path(project_dir).resolve()
    specs = Path(specs_root).resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"project directory does not exist: {project}")
    if not specs.is_dir():
        raise FileNotFoundError(f"specs root does not exist: {specs}")
    before = _source_hashes(project)
    required = _required_functions(specs)
    with tempfile.TemporaryDirectory(prefix="specforge_no_repair_") as raw:
        copied_project = Path(raw) / "project"
        shutil.copytree(
            project,
            copied_project,
            ignore=shutil.ignore_patterns(".git", ".repair", "_agent_logs", "__pycache__"),
        )
        snapshot = create_diagnostic_snapshot(copied_project, binary_name)
        definitions = _definition_bodies(copied_project)
        coverage = _definition_coverage(required, definitions)
        placeholders = _placeholder_report(copied_project, definitions)
        near_empty = _near_empty_sources(coverage)
    preservation = _hash_preservation(before, _source_hashes(project))
    return {
        "schema_version": "planning_utility_no_repair_analysis/v1",
        "project_dir": str(project),
        "specs_root": str(specs),
        "binary_name": binary_name,
        "diagnostic_snapshot": snapshot,
        "definition_coverage": coverage,
        "placeholders": placeholders,
        "near_empty_required_sources": near_empty,
        "source_hash_preservation": preservation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze generated C code before any repair without mutating the source project")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--specs-root", required=True)
    parser.add_argument("--binary-name", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(analyze_no_repair(args.project_dir, args.specs_root, args.binary_name), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
