"""Read-only extraction of obligation-normalized Contract-to-Code Closure metrics for RQ1.

The primary denominators come from an explicit evaluation-owned protocol rubric.
The original code-internal structural metrics remain available as diagnostics.
Method-specific specification coverage may be carried as batch metadata, but
never enters the cross-method denominators produced here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .repair_diagnostics import (
    discover_build_plan,
    iter_project_files,
    run_header_self_checks,
)


SCHEMA_VERSION = "planning_utility_contract_closure/v2"
ANALYSIS_BACKEND = "clang_ast_with_obligation_evidence/v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^\"]+)"', re.MULTILINE)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:TODO|FIXME|XXX|unimplemented|placeholder|whatever)\b|not\s+implemented",
    re.IGNORECASE,
)
_PROJECT_TYPE_RE = re.compile(r"\b(?:struct|union|enum)\s+([A-Za-z_]\w*)\b")
_NON_CODE_RE = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_]\w*\b")
_DEFINE_INT_RE = re.compile(
    r"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)\s+\(?\s*(?P<value>0[xX][0-9A-Fa-f]+|\d+)[uUlL]*\b",
    re.MULTILINE,
)
_ASSIGNED_INT_RE = re.compile(
    r"\b(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*\(?\s*(?P<value>0[xX][0-9A-Fa-f]+|\d+)[uUlL]*\b"
)
_UNDECLARED_CALL_RE = re.compile(
    r"^(?P<path>.+\.c):(?P<line>\d+):\d+: (?:fatal )?error: "
    r"(?:call to undeclared function|implicit declaration of function) [‘'`](?P<symbol>[A-Za-z_]\w*)[’'`]",
    re.MULTILINE,
)
_INCOMPLETE_ACCESS_RE = re.compile(
    r"^(?P<path>.+\.c):(?P<line>\d+):\d+: (?:fatal )?error: "
    r"(?:incomplete definition of type|invalid use of (?:incomplete|undefined) type) [‘'`]"
    r"(?:struct|union) (?P<tag>[A-Za-z_]\w*)[’'`]",
    re.MULTILINE,
)
_KNOWN_EXTERNAL_FUNCTIONS = {
    "accept",
    "accept4",
    "asprintf",
    "bind",
    "calloc",
    "close",
    "connect",
    "free",
    "inet_ntop",
    "listen",
    "malloc",
    "memcmp",
    "memcpy",
    "memmove",
    "memset",
    "perror",
    "printf",
    "read",
    "realloc",
    "recv",
    "select",
    "send",
    "setsockopt",
    "snprintf",
    "socket",
    "strcasecmp",
    "strdup",
    "strlen",
    "strncmp",
    "strncpy",
    "write",
}


def _rate(passed: int, total: int) -> float | None:
    return passed / total if total else None


def _code_identifiers(text: str) -> set[str]:
    code = _NON_CODE_RE.sub(" ", text)
    return {item.lower() for item in _IDENTIFIER_RE.findall(code)}


def _matches_term_groups(identifiers: set[str], groups: list[list[str]]) -> bool:
    return all(
        any(term.lower() in identifier for term in group for identifier in identifiers)
        for group in groups
    )


def _compact_stderr(stderr: str, limit: int = 500) -> str:
    return " ".join(stderr.split())[:limit]


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
        "changed_paths": changed,
        "added_paths": added,
        "removed_paths": removed,
    }


def _relative_project_path(raw: str | None, project_dir: Path, default: str = "") -> str:
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        path = project_dir / path
    try:
        return path.resolve(strict=False).relative_to(project_dir.resolve(strict=False)).as_posix()
    except ValueError:
        return ""


def _node_path(node: dict[str, Any], project_dir: Path, default: str) -> str:
    loc = node.get("loc") or {}
    raw = loc.get("file")
    if not raw:
        begin = (node.get("range") or {}).get("begin") or {}
        raw = begin.get("file")
    if raw:
        return _relative_project_path(str(raw), project_dir)
    if loc.get("includedFrom") or ((node.get("range") or {}).get("begin") or {}).get("includedFrom"):
        return ""
    return default


def _node_line(node: dict[str, Any], source_text: str = "") -> int | None:
    loc = node.get("loc") or {}
    if isinstance(loc.get("line"), int):
        return int(loc["line"])
    begin = (node.get("range") or {}).get("begin") or {}
    if isinstance(begin.get("line"), int):
        return int(begin["line"])
    offset = loc.get("offset", begin.get("offset"))
    if isinstance(offset, int) and source_text:
        return source_text.count("\n", 0, offset) + 1
    return None


def _canonical_type(node: dict[str, Any]) -> str:
    type_info = node.get("type") or {}
    value = type_info.get("desugaredQualType") or type_info.get("qualType") or ""
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return re.sub(r"\bbool\b", "_Bool", normalized)


def _walk(node: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield node
    for child in node.get("inner") or []:
        if isinstance(child, dict):
            yield from _walk(child)


def _descendant(node: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for item in _walk(node):
        if item is not node and item.get("kind") == kind:
            return item
    return None


def _clang_flags(cflags: list[str]) -> list[str]:
    accepted: list[str] = []
    take_next = False
    for flag in cflags:
        if take_next:
            accepted.append(flag)
            take_next = False
            continue
        if flag in {"-I", "-D", "-U", "-include", "-isystem", "-iquote"}:
            accepted.append(flag)
            take_next = True
        elif flag.startswith(("-I", "-D", "-U", "-std=", "-isystem", "-iquote")):
            accepted.append(flag)
        elif flag in {"-pthread", "-fms-extensions"}:
            accepted.append(flag)
    return accepted


def _clang_version(clang: str) -> str:
    try:
        result = subprocess.run([clang, "--version"], text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.splitlines()[0] if result.stdout else "unknown"


def _dump_ast(
    clang: str,
    project_dir: Path,
    path: str,
    flags: list[str],
) -> dict[str, Any]:
    command = [
        clang,
        *flags,
        "-Wno-everything",
        "-Xclang",
        "-ast-dump=json",
        "-fsyntax-only",
        path,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=project_dir,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "path": path,
            "command": command,
            "returncode": 124,
            "ast": None,
            "stderr": str(exc),
            "raw_stderr": str(exc),
        }
    try:
        ast = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        ast = None
    return {
        "path": path,
        "command": command,
        "returncode": result.returncode,
        "ast": ast,
        "stderr": _compact_stderr(result.stderr),
        "raw_stderr": result.stderr,
    }


def _function_is_definition(node: dict[str, Any]) -> bool:
    return any(child.get("kind") == "CompoundStmt" for child in node.get("inner") or [])


def _function_stub_reasons(node: dict[str, Any], source_text: str) -> list[str]:
    body = next((child for child in node.get("inner") or [] if child.get("kind") == "CompoundStmt"), None)
    if not body:
        return []
    statements = body.get("inner") or []
    reasons: list[str] = []
    if not statements:
        reasons.append("empty_body")
    elif len(statements) == 1 and statements[0].get("kind") == "ReturnStmt":
        expression = next(iter(statements[0].get("inner") or []), None)
        while isinstance(expression, dict) and expression.get("kind") in {
            "ImplicitCastExpr",
            "ParenExpr",
            "CStyleCastExpr",
        }:
            expression = next(iter(expression.get("inner") or []), None)
        if isinstance(expression, dict) and expression.get("kind") in {
            "IntegerLiteral",
            "CXXBoolLiteralExpr",
            "GNUNullExpr",
        } and str(expression.get("value", "0")) in {"0", "1"}:
            reasons.append("fixed_dummy_return")
        elif isinstance(expression, dict) and expression.get("kind") == "UnaryOperator" and expression.get("opcode") == "-":
            operand = next(iter(expression.get("inner") or []), None)
            if isinstance(operand, dict) and operand.get("kind") == "IntegerLiteral" and str(operand.get("value")) == "1":
                reasons.append("fixed_dummy_return")
    begin = (body.get("range") or {}).get("begin") or {}
    end = (body.get("range") or {}).get("end") or {}
    if isinstance(begin.get("offset"), int) and isinstance(end.get("offset"), int):
        fragment = source_text[begin["offset"] : end["offset"] + int(end.get("tokLen", 1))]
        if _PLACEHOLDER_RE.search(fragment):
            reasons.append("placeholder_marker")
    return sorted(set(reasons))


def _extract_ast_evidence(
    project_dir: Path,
    ast_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    declarations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    calls: list[dict[str, Any]] = []
    function_refs: list[dict[str, Any]] = []
    function_identifiers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    typedefs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    member_accesses: list[dict[str, Any]] = []
    source_type_uses: list[dict[str, Any]] = []

    for run in ast_runs:
        ast = run.get("ast")
        if not isinstance(ast, dict):
            continue
        tu = str(run["path"])
        source_text = (project_dir / tu).read_text(encoding="utf-8", errors="ignore")

        def visit(node: dict[str, Any], current_function: str = "") -> None:
            kind = str(node.get("kind", ""))
            path = _node_path(node, project_dir, tu)
            next_function = current_function
            if kind == "FunctionDecl" and path:
                name = str(node.get("name", ""))
                signature = _canonical_type(node)
                line = _node_line(node, source_text if path == tu else "")
                is_definition = _function_is_definition(node)
                item = {
                    "name": name,
                    "signature": signature,
                    "path": path,
                    "line": line,
                    "storage": "static" if node.get("storageClass") == "static" else "extern",
                    "inline": bool(node.get("inline")),
                    "is_definition": is_definition,
                }
                if is_definition:
                    item["stub_reasons"] = _function_stub_reasons(node, source_text if path == tu else "")
                    definitions[name].append(item)
                    body = next(
                        (child for child in node.get("inner") or [] if child.get("kind") == "CompoundStmt"),
                        None,
                    )
                    begin = ((body or {}).get("range") or {}).get("begin") or {}
                    end = ((body or {}).get("range") or {}).get("end") or {}
                    fragment = ""
                    if path == tu and isinstance(begin.get("offset"), int) and isinstance(end.get("offset"), int):
                        fragment = source_text[
                            begin["offset"] : end["offset"] + int(end.get("tokLen", 1))
                        ]
                    function_identifiers[name].append(
                        {
                            "path": path,
                            "line": line,
                            "identifiers": sorted({name.lower(), *_code_identifiers(fragment)}),
                        }
                    )
                    next_function = name
                elif path.endswith(".h") and item["storage"] != "static" and not item["inline"]:
                    declarations[name].append(item)
            elif kind == "TypedefDecl" and path and path.endswith(".h"):
                name = str(node.get("name", ""))
                if name and not name.startswith("__"):
                    typedefs[name].append(
                        {
                            "name": name,
                            "underlying": _canonical_type(node),
                            "path": path,
                            "line": _node_line(node),
                        }
                    )
            elif kind in {"RecordDecl", "EnumDecl"} and path:
                name = str(node.get("name", ""))
                if name and not name.startswith("__"):
                    records[name].append(
                        {
                            "name": name,
                            "kind": kind,
                            "path": path,
                            "line": _node_line(node, source_text if path == tu else ""),
                            "complete": bool(node.get("completeDefinition"))
                            or any(
                                child.get("kind") in {"FieldDecl", "EnumConstantDecl"}
                                for child in node.get("inner") or []
                            ),
                        }
                    )
            elif kind == "CallExpr" and path == tu:
                ref = None
                for desc in _walk(node):
                    candidate = desc.get("referencedDecl") or {}
                    if candidate.get("kind") == "FunctionDecl" and candidate.get("name"):
                        ref = candidate
                        break
                if ref:
                    calls.append(
                        {
                            "caller_path": tu,
                            "caller_function": current_function,
                            "line": _node_line(node, source_text),
                            "symbol": str(ref.get("name")),
                            "referenced_signature": _canonical_type(ref),
                        }
                    )
            elif kind == "DeclRefExpr" and path == tu and current_function:
                referenced = node.get("referencedDecl") or {}
                if referenced.get("kind") == "FunctionDecl" and referenced.get("name"):
                    function_refs.append(
                        {
                            "caller_path": tu,
                            "caller_function": current_function,
                            "line": _node_line(node, source_text),
                            "symbol": str(referenced["name"]),
                        }
                    )
            elif kind == "MemberExpr" and path == tu:
                base_type = ""
                for child in node.get("inner") or []:
                    base_type = _canonical_type(child)
                    if base_type:
                        break
                member_accesses.append(
                    {
                        "path": tu,
                        "line": _node_line(node, source_text),
                        "member": str(node.get("name", "")),
                        "base_type": base_type,
                        "is_arrow": bool(node.get("isArrow")),
                    }
                )
            if path == tu and kind in {"VarDecl", "ParmVarDecl", "FieldDecl", "FunctionDecl"}:
                qual_type = _canonical_type(node)
                if qual_type:
                    source_type_uses.append(
                        {
                            "path": tu,
                            "line": _node_line(node, source_text),
                            "qual_type": qual_type,
                            "context": kind,
                        }
                    )
            for child in node.get("inner") or []:
                if isinstance(child, dict):
                    visit(child, next_function)

        visit(ast)
        for match in _UNDECLARED_CALL_RE.finditer(str(run.get("raw_stderr", ""))):
            symbol = match.group("symbol")
            if symbol in _KNOWN_EXTERNAL_FUNCTIONS or "_" not in symbol:
                continue
            calls.append(
                {
                    "caller_path": _relative_project_path(match.group("path"), project_dir, tu) or tu,
                    "caller_function": "",
                    "line": int(match.group("line")),
                    "symbol": symbol,
                    "referenced_signature": "",
                    "unresolved": True,
                }
            )
        for match in _INCOMPLETE_ACCESS_RE.finditer(str(run.get("raw_stderr", ""))):
            member_accesses.append(
                {
                    "path": _relative_project_path(match.group("path"), project_dir, tu) or tu,
                    "line": int(match.group("line")),
                    "member": "<compiler-diagnostic>",
                    "base_type": f"struct {match.group('tag')} *",
                    "is_arrow": True,
                    "diagnostic_evidence": True,
                }
            )

    def dedupe(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in items:
            unique.setdefault(tuple(item.get(key) for key in keys), item)
        return sorted(unique.values(), key=lambda item: tuple(str(item.get(key, "")) for key in keys))

    return {
        "declarations": {
            name: dedupe(items, ("path", "line", "signature")) for name, items in sorted(declarations.items())
        },
        "definitions": {
            name: dedupe(items, ("path", "line", "signature")) for name, items in sorted(definitions.items())
        },
        "calls": dedupe(calls, ("caller_path", "line", "symbol")),
        "function_refs": dedupe(function_refs, ("caller_path", "caller_function", "line", "symbol")),
        "function_identifiers": {
            name: dedupe(items, ("path", "line")) for name, items in sorted(function_identifiers.items())
        },
        "typedefs": {name: dedupe(items, ("path", "line", "underlying")) for name, items in sorted(typedefs.items())},
        "records": {name: dedupe(items, ("path", "line", "complete")) for name, items in sorted(records.items())},
        "member_accesses": dedupe(member_accesses, ("path", "line", "member", "base_type")),
        "source_type_uses": dedupe(source_type_uses, ("path", "line", "qual_type", "context")),
    }


def _header_metric(checks: list[dict[str, Any]]) -> dict[str, Any]:
    items = [
        {
            "path": str(check["path"]),
            "passed": int(check.get("returncode", 1)) == 0,
            "returncode": int(check.get("returncode", 1)),
            "stderr": _compact_stderr(str(check.get("stderr", ""))),
        }
        for check in checks
    ]
    passed = sum(item["passed"] for item in items)
    return {
        "status": "measured",
        "total": len(items),
        "passed": passed,
        "rate": _rate(passed, len(items)),
        "items": items,
    }


def _public_api_metric(evidence: dict[str, Any], parsed_source_count: int, source_count: int) -> dict[str, Any]:
    declarations = evidence["declarations"]
    definitions = evidence["definitions"]
    items: list[dict[str, Any]] = []
    for name, decls in declarations.items():
        if name == "main":
            continue
        external_defs = [item for item in definitions.get(name, []) if item["storage"] == "extern"]
        declared_signatures = sorted({item["signature"] for item in decls})
        definition_signatures = sorted({item["signature"] for item in external_defs})
        reasons: list[str] = []
        if len(declared_signatures) != 1:
            reasons.append("conflicting_public_declarations")
        if not external_defs:
            reasons.append("missing_definition")
        elif len(external_defs) > 1:
            reasons.append("duplicate_external_definitions")
        elif declared_signatures and external_defs[0]["signature"] not in declared_signatures:
            reasons.append("signature_mismatch")
        if len(external_defs) == 1 and external_defs[0].get("stub_reasons"):
            reasons.append("stub_definition")
        items.append(
            {
                "symbol": name,
                "passed": not reasons,
                "reasons": reasons,
                "declaration_signatures": declared_signatures,
                "definition_signatures": definition_signatures,
                "declarations": decls,
                "definitions": external_defs,
            }
        )
    items.sort(key=lambda item: item["symbol"])
    passed = sum(item["passed"] for item in items)
    return {
        "status": "measured" if parsed_source_count == source_count else "partial",
        "total": len(items),
        "passed": passed,
        "rate": _rate(passed, len(items)),
        "status_counts": dict(sorted(Counter(reason for item in items for reason in item["reasons"]).items())),
        "items": items,
    }


def _project_include_graph(project_dir: Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    files = [path for path in iter_project_files(project_dir) if path.suffix in {".c", ".h"}]
    relatives = {path.relative_to(project_dir).as_posix(): path for path in files}
    by_name: dict[str, list[str]] = defaultdict(list)
    for relative in relatives:
        by_name[Path(relative).name].append(relative)
    graph: dict[str, set[str]] = {relative: set() for relative in relatives}
    unresolved: dict[str, str] = {}
    for relative, path in relatives.items():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for include in _INCLUDE_RE.findall(text):
            candidates = [
                (Path(relative).parent / include).as_posix(),
                Path(include).as_posix(),
            ]
            resolved = next((candidate for candidate in candidates if candidate in relatives), "")
            if not resolved and len(by_name.get(Path(include).name, [])) == 1:
                resolved = by_name[Path(include).name][0]
            if resolved:
                graph[relative].add(resolved)
            else:
                unresolved[f"{relative}:{include}"] = include
    return graph, unresolved


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    pending = list(graph.get(start, set()))
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        pending.extend(graph.get(item, set()) - seen)
    return seen


def _dependency_metric(project_dir: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    graph, unresolved_includes = _project_include_graph(project_dir)
    declarations = evidence["declarations"]
    definitions = evidence["definitions"]
    call_items: list[dict[str, Any]] = []
    for call in evidence["calls"]:
        symbol = call["symbol"]
        external_defs = [item for item in definitions.get(symbol, []) if item["storage"] == "extern"]
        unresolved = bool(call.get("unresolved"))
        if not unresolved and (not external_defs or all(item["path"] == call["caller_path"] for item in external_defs)):
            continue
        decls = declarations.get(symbol, [])
        reachable_headers = _reachable(graph, call["caller_path"])
        visible_decls = [item for item in decls if item["path"] in reachable_headers]
        reasons: list[str] = []
        if not external_defs:
            reasons.append("missing_provider")
        elif len(external_defs) != 1:
            reasons.append("provider_not_unique")
        if not visible_decls:
            reasons.append("declaration_not_reachable")
        elif len({item["signature"] for item in visible_decls}) != 1:
            reasons.append("visible_declaration_conflict")
        elif len(external_defs) == 1 and external_defs[0]["signature"] not in {
            item["signature"] for item in visible_decls
        }:
            reasons.append("provider_signature_mismatch")
        call_items.append(
            {
                **call,
                "kind": "function_call",
                "passed": not reasons,
                "reasons": reasons,
                "provider_paths": sorted({item["path"] for item in external_defs}),
                "declaration_paths": sorted({item["path"] for item in decls}),
                "reachable_declaration_paths": sorted({item["path"] for item in visible_decls}),
            }
        )

    type_providers: dict[str, set[str]] = defaultdict(set)
    for name, items in evidence["typedefs"].items():
        type_providers[name].update(item["path"] for item in items)
    for name, items in evidence["records"].items():
        for prefix in (name, f"struct {name}", f"union {name}", f"enum {name}"):
            type_providers[prefix].update(item["path"] for item in items if item["path"].endswith(".h"))

    type_items: list[dict[str, Any]] = []
    seen_type_edges: set[tuple[str, str]] = set()
    names = sorted(type_providers, key=len, reverse=True)
    for use in evidence["source_type_uses"]:
        qual_type = use["qual_type"]
        matched = next((name for name in names if re.search(rf"\b{re.escape(name)}\b", qual_type)), "")
        if not matched:
            continue
        edge_key = (use["path"], matched)
        if edge_key in seen_type_edges:
            continue
        seen_type_edges.add(edge_key)
        providers = sorted(type_providers[matched])
        reachable_headers = _reachable(graph, use["path"])
        visible = sorted(set(providers) & reachable_headers)
        reasons = [] if visible else ["type_declaration_not_reachable"]
        type_items.append(
            {
                "kind": "type_reference",
                "consumer_path": use["path"],
                "line": use["line"],
                "symbol": matched,
                "qual_type": qual_type,
                "passed": not reasons,
                "reasons": reasons,
                "provider_paths": providers,
                "reachable_provider_paths": visible,
            }
        )

    call_items.sort(key=lambda item: (item["caller_path"], item.get("line") or 0, item["symbol"]))
    type_items.sort(key=lambda item: (item["consumer_path"], item["symbol"]))
    call_passed = sum(item["passed"] for item in call_items)
    type_passed = sum(item["passed"] for item in type_items)
    total = len(call_items) + len(type_items)
    passed = call_passed + type_passed
    return {
        "status": "measured",
        "total": total,
        "passed": passed,
        "rate": _rate(passed, total),
        "function_calls": {
            "total": len(call_items),
            "passed": call_passed,
            "rate": _rate(call_passed, len(call_items)),
            "items": call_items,
        },
        "type_references": {
            "total": len(type_items),
            "passed": type_passed,
            "rate": _rate(type_passed, len(type_items)),
            "items": type_items,
        },
        "unresolved_local_includes": unresolved_includes,
    }


def _opaque_name(base_type: str, aliases: dict[str, str], opaque_tags: set[str]) -> str:
    for alias, tag in aliases.items():
        if re.search(rf"\b{re.escape(alias)}\b", base_type) and tag in opaque_tags:
            return tag
    match = _PROJECT_TYPE_RE.search(base_type)
    if match and match.group(1) in opaque_tags:
        return match.group(1)
    return ""


def _ownership_metric(evidence: dict[str, Any]) -> dict[str, Any]:
    declarations = evidence["declarations"]
    definitions = evidence["definitions"]
    symbol_items: list[dict[str, Any]] = []
    for name in sorted(declarations):
        if name == "main":
            continue
        owners = sorted(
            {item["path"] for item in definitions.get(name, []) if item["storage"] == "extern"}
        )
        reasons = [] if len(owners) == 1 else (["missing_external_owner"] if not owners else ["duplicate_external_owners"])
        symbol_items.append(
            {
                "kind": "public_symbol_owner",
                "symbol": name,
                "passed": not reasons,
                "reasons": reasons,
                "owner_paths": owners,
            }
        )

    header_record_decls: dict[str, list[dict[str, Any]]] = {}
    source_complete: dict[str, list[dict[str, Any]]] = {}
    for name, items in evidence["records"].items():
        headers = [item for item in items if item["kind"] == "RecordDecl" and item["path"].endswith(".h")]
        complete_sources = [
            item
            for item in items
            if item["kind"] == "RecordDecl" and item["path"].endswith(".c") and item["complete"]
        ]
        if headers:
            header_record_decls[name] = headers
        if complete_sources:
            source_complete[name] = complete_sources
    opaque_tags = {
        name
        for name, items in header_record_decls.items()
        if not any(item["complete"] for item in items)
    }
    aliases: dict[str, str] = {}
    for alias, items in evidence["typedefs"].items():
        for item in items:
            match = _PROJECT_TYPE_RE.search(item["underlying"])
            if match:
                aliases[alias] = match.group(1)

    violations_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for access in evidence["member_accesses"]:
        tag = _opaque_name(access["base_type"], aliases, opaque_tags)
        if not tag:
            continue
        owners = {item["path"] for item in source_complete.get(tag, [])}
        if access["path"] not in owners:
            violations_by_tag[tag].append(access)

    opaque_items: list[dict[str, Any]] = []
    for tag in sorted(opaque_tags):
        owners = sorted({item["path"] for item in source_complete.get(tag, [])})
        violations = sorted(
            violations_by_tag.get(tag, []),
            key=lambda item: (item["path"], item.get("line") or 0, item["member"]),
        )
        reasons: list[str] = []
        if not owners:
            reasons.append("missing_concrete_owner")
        elif len(owners) > 1:
            reasons.append("multiple_concrete_owners")
        if violations:
            reasons.append("cross_owner_member_access")
        opaque_items.append(
            {
                "kind": "opaque_type_owner",
                "symbol": tag,
                "passed": not reasons,
                "reasons": reasons,
                "declaration_paths": sorted({item["path"] for item in header_record_decls[tag]}),
                "owner_paths": owners,
                "member_access_violations": violations,
            }
        )

    items = symbol_items + opaque_items
    passed = sum(item["passed"] for item in items)
    symbol_passed = sum(item["passed"] for item in symbol_items)
    opaque_passed = sum(item["passed"] for item in opaque_items)
    return {
        "status": "measured",
        "total": len(items),
        "passed": passed,
        "rate": _rate(passed, len(items)),
        "public_symbol_owners": {
            "total": len(symbol_items),
            "passed": symbol_passed,
            "rate": _rate(symbol_passed, len(symbol_items)),
            "items": symbol_items,
        },
        "opaque_type_owners": {
            "total": len(opaque_items),
            "passed": opaque_passed,
            "rate": _rate(opaque_passed, len(opaque_items)),
            "items": opaque_items,
        },
        "scope_note": "Structural provider and opaque-boundary ownership only; heap lifecycle is not inferred.",
    }


_OBLIGATION_METRIC_NAMES = (
    "required_obligation_realization",
    "executable_call_path_closure",
    "semantic_grounding_closure",
)


def _unavailable_obligation_metrics() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "unavailable",
            "total": 0,
            "passed": 0,
            "rate": None,
            "items": [],
        }
        for name in _OBLIGATION_METRIC_NAMES
    }


def _validate_obligation_rubric(rubric: dict[str, Any]) -> None:
    if rubric.get("schema_version") != "planning_utility_obligation_rubric/v1":
        raise ValueError("unsupported obligation rubric schema_version")
    capabilities = rubric.get("capabilities")
    obligations = rubric.get("obligations")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("obligation rubric requires non-empty capabilities")
    if not isinstance(obligations, list) or not obligations:
        raise ValueError("obligation rubric requires non-empty obligations")
    capability_ids = [str(item.get("id", "")) for item in capabilities]
    if any(not item for item in capability_ids) or len(capability_ids) != len(set(capability_ids)):
        raise ValueError("obligation rubric capability ids must be non-empty and unique")
    known = set(capability_ids)
    obligation_ids: set[str] = set()
    for obligation in obligations:
        obligation_id = str(obligation.get("id", ""))
        if not obligation_id or obligation_id in obligation_ids:
            raise ValueError("obligation rubric obligation ids must be non-empty and unique")
        obligation_ids.add(obligation_id)
        required = obligation.get("required_capabilities")
        if not isinstance(required, list) or not required or not set(map(str, required)) <= known:
            raise ValueError(f"obligation {obligation_id} has invalid required_capabilities")
        grounding = obligation.get("grounding") or {}
        if grounding.get("kind") not in {"named_integer_constants", "function_terms"}:
            raise ValueError(f"obligation {obligation_id} has unsupported grounding kind")


def _function_corpora(evidence: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    corpora: dict[str, set[str]] = defaultdict(set)
    paths: dict[str, set[str]] = defaultdict(set)
    for name, definitions in evidence["definitions"].items():
        usable = [item for item in definitions if not item.get("stub_reasons")]
        if not usable:
            continue
        for item in evidence["function_identifiers"].get(name, []):
            if any(
                definition["path"] == item["path"] and definition.get("line") == item.get("line")
                for definition in usable
            ):
                corpora[name].update(item["identifiers"])
                paths[name].add(item["path"])
    return dict(corpora), dict(paths)


def _named_integer_constants(project_dir: Path) -> list[dict[str, Any]]:
    constants: dict[tuple[str, str, int], dict[str, Any]] = {}
    for path in iter_project_files(project_dir):
        if path.suffix not in {".c", ".h"}:
            continue
        relative = path.relative_to(project_dir).as_posix()
        code = _NON_CODE_RE.sub(" ", path.read_text(encoding="utf-8", errors="ignore"))
        for pattern in (_DEFINE_INT_RE, _ASSIGNED_INT_RE):
            for match in pattern.finditer(code):
                value = int(match.group("value"), 0)
                key = (relative, match.group("name"), value)
                constants.setdefault(
                    key,
                    {
                        "path": relative,
                        "line": code.count("\n", 0, match.start()) + 1,
                        "name": match.group("name"),
                        "value": value,
                    },
                )
    return sorted(constants.values(), key=lambda item: (item["path"], item["line"], item["name"]))


def _obligation_metrics(
    project_dir: Path,
    evidence: dict[str, Any],
    dependency: dict[str, Any],
    rubric: dict[str, Any],
    *,
    partial_ast: bool,
) -> dict[str, dict[str, Any]]:
    _validate_obligation_rubric(rubric)
    corpora, function_paths = _function_corpora(evidence)
    capability_items: dict[str, dict[str, Any]] = {}
    for capability in rubric["capabilities"]:
        capability_id = str(capability["id"])
        groups = [[str(term).lower() for term in group] for group in capability["all_term_groups"]]
        candidates = sorted(name for name, identifiers in corpora.items() if _matches_term_groups(identifiers, groups))
        capability_items[capability_id] = {
            "capability_id": capability_id,
            "passed": bool(candidates),
            "matched_functions": candidates,
            "term_groups": groups,
        }

    realization_items: list[dict[str, Any]] = []
    for obligation in rubric["obligations"]:
        required = [str(item) for item in obligation["required_capabilities"]]
        realization_items.append(
            {
                "obligation_id": str(obligation["id"]),
                "source_scenarios": obligation.get("source_scenarios", []),
                "passed": all(capability_items[item]["passed"] for item in required),
                "capabilities": [capability_items[item] for item in required],
            }
        )
    realized_capabilities = sum(item["passed"] for item in capability_items.values())
    realized_obligations = sum(item["passed"] for item in realization_items)
    realization = {
        "status": "partial" if partial_ast else "measured",
        "total": len(realization_items),
        "passed": realized_obligations,
        "rate": _rate(realized_obligations, len(realization_items)),
        "capabilities_total": len(capability_items),
        "capabilities_realized": realized_capabilities,
        "capability_rate": _rate(realized_capabilities, len(capability_items)),
        "items": realization_items,
    }

    call_graph: dict[str, set[str]] = defaultdict(set)
    defined = set(evidence["definitions"])
    for edge in [*evidence["calls"], *evidence["function_refs"]]:
        caller = str(edge.get("caller_function", ""))
        callee = str(edge.get("symbol", ""))
        if caller and callee in defined and caller != callee:
            call_graph[caller].add(callee)
    roots = {"main"} if "main" in corpora else set()
    reachable = set(roots)
    pending = list(roots)
    while pending:
        caller = pending.pop()
        for callee in call_graph.get(caller, set()) - reachable:
            reachable.add(callee)
            pending.append(callee)

    failed_calls = [
        item
        for item in dependency["function_calls"]["items"]
        if not item["passed"]
    ]
    failed_types = [
        item
        for item in dependency["type_references"]["items"]
        if not item["passed"]
    ]
    path_items: list[dict[str, Any]] = []
    for obligation in rubric["obligations"]:
        required = [str(item) for item in obligation["required_capabilities"]]
        matched = {
            capability_id: capability_items[capability_id]["matched_functions"]
            for capability_id in required
        }
        reachable_matches = {
            capability_id: sorted(set(names) & reachable)
            for capability_id, names in matched.items()
        }
        relevant_functions = {name for names in matched.values() for name in names}
        relevant_paths = {path for name in relevant_functions for path in function_paths.get(name, set())}
        blocking_calls = [item for item in failed_calls if item.get("caller_function") in relevant_functions]
        blocking_types = [item for item in failed_types if item.get("consumer_path") in relevant_paths]
        reasons: list[str] = []
        if not roots:
            reasons.append("main_not_available")
        if any(not names for names in matched.values()):
            reasons.append("required_capability_missing")
        if any(not names for names in reachable_matches.values()):
            reasons.append("capability_not_reachable_from_main")
        if blocking_calls:
            reasons.append("blocking_function_dependency")
        if blocking_types:
            reasons.append("blocking_type_dependency")
        path_items.append(
            {
                "obligation_id": str(obligation["id"]),
                "passed": not reasons,
                "reasons": reasons,
                "matched_functions": matched,
                "reachable_functions": reachable_matches,
                "blocking_function_edges": blocking_calls,
                "blocking_type_edges": blocking_types,
            }
        )
    path_passed = sum(item["passed"] for item in path_items)
    path_metric = {
        "status": "partial" if partial_ast else "measured",
        "total": len(path_items),
        "passed": path_passed,
        "rate": _rate(path_passed, len(path_items)),
        "roots": sorted(roots),
        "statically_reachable_functions": sorted(reachable),
        "items": path_items,
    }

    constants = _named_integer_constants(project_dir)
    project_identifiers = set().union(*corpora.values()) if corpora else set()
    grounding_items: list[dict[str, Any]] = []
    for obligation in rubric["obligations"]:
        rule = obligation["grounding"]
        kind = str(rule["kind"])
        evidence_items: list[dict[str, Any]] = []
        if kind == "named_integer_constants":
            for marker in rule["markers"]:
                terms = [str(term).lower() for term in marker["name_terms"]]
                accepted = {int(value) for value in marker["accepted_values"]}
                matches = [
                    item
                    for item in constants
                    if all(term in item["name"].lower() for term in terms)
                    and item["value"] in accepted
                    and item["name"].lower() in project_identifiers
                ]
                evidence_items.append(
                    {
                        "marker_id": str(marker["id"]),
                        "passed": bool(matches),
                        "matches": matches,
                        "accepted_values": sorted(accepted),
                    }
                )
        else:
            groups = [[str(term).lower() for term in group] for group in rule["all_term_groups"]]
            evidence_items.append(
                {
                    "marker_id": "function_terms",
                    "passed": _matches_term_groups(project_identifiers, groups),
                    "term_groups": groups,
                }
            )
        grounding_items.append(
            {
                "obligation_id": str(obligation["id"]),
                "passed": all(item["passed"] for item in evidence_items),
                "evidence": evidence_items,
            }
        )
    grounding_passed = sum(item["passed"] for item in grounding_items)
    grounding = {
        "status": "measured",
        "total": len(grounding_items),
        "passed": grounding_passed,
        "rate": _rate(grounding_passed, len(grounding_items)),
        "items": grounding_items,
    }
    return {
        "required_obligation_realization": realization,
        "executable_call_path_closure": path_metric,
        "semantic_grounding_closure": grounding,
    }


def analyze_contract_closure(
    project_dir: str | Path,
    binary_name: str,
    *,
    method: str = "unknown",
    run_id: str = "",
    sample_status: str = "unknown",
    clang: str = "clang",
    metadata: dict[str, Any] | None = None,
    obligation_rubric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project = Path(project_dir).resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"project directory does not exist: {project}")
    before = _source_hashes(project)
    with tempfile.TemporaryDirectory(prefix="specforge_contract_closure_") as raw:
        copied = Path(raw) / "project"
        shutil.copytree(
            project,
            copied,
            ignore=shutil.ignore_patterns(".git", ".repair", "_agent_logs", "__pycache__", "*.orig", "*.rej"),
        )
        plan = discover_build_plan(copied, binary_name)
        flags = _clang_flags(plan.cflags)
        source_paths = [source for source in plan.sources if (copied / source).is_file()]
        header_paths = [
            path.relative_to(copied).as_posix()
            for path in iter_project_files(copied)
            if path.suffix == ".h"
        ]
        ast_runs = [_dump_ast(clang, copied, path, flags) for path in [*source_paths, *header_paths]]
        evidence = _extract_ast_evidence(copied, ast_runs)
        header_checks = run_header_self_checks(copied, plan)
        source_ast_runs = [run for run in ast_runs if run["path"] in source_paths]
        header_ast_runs = [run for run in ast_runs if run["path"] in header_paths]
        parsed_sources = sum(run["ast"] is not None for run in source_ast_runs)
        parsed_headers = sum(run["ast"] is not None for run in header_ast_runs)
        clean_source_asts = sum(run["ast"] is not None and run["returncode"] == 0 for run in source_ast_runs)
        clean_header_asts = sum(run["ast"] is not None and run["returncode"] == 0 for run in header_ast_runs)
        parse_failures = [
            {
                "path": run["path"],
                "returncode": run["returncode"],
                "ast_available": run["ast"] is not None,
                "stderr": run["stderr"],
            }
            for run in ast_runs
            if run["ast"] is None
        ]
        public_api = _public_api_metric(evidence, parsed_sources, len(source_paths))
        dependency = _dependency_metric(copied, evidence)
        ownership = _ownership_metric(evidence)
        partial_ast = clean_source_asts != len(source_paths) or clean_header_asts != len(header_paths)
        if partial_ast:
            public_api["status"] = "partial"
            dependency["status"] = "partial"
            ownership["status"] = "partial"
        obligation_metrics = (
            _obligation_metrics(
                copied,
                evidence,
                dependency,
                obligation_rubric,
                partial_ast=partial_ast,
            )
            if obligation_rubric
            else _unavailable_obligation_metrics()
        )

    preservation = _hash_preservation(before, _source_hashes(project))
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_backend": ANALYSIS_BACKEND,
        "project_dir": str(project),
        "binary_name": binary_name,
        "run_id": run_id,
        "method": method,
        "sample_status": sample_status,
        "metadata": metadata or {},
        "obligation_rubric": (
            {
                "rubric_id": obligation_rubric.get("rubric_id"),
                "schema_version": obligation_rubric.get("schema_version"),
                "protocol": obligation_rubric.get("protocol"),
                "profile": obligation_rubric.get("profile"),
                "sha256": hashlib.sha256(
                    json.dumps(obligation_rubric, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            if obligation_rubric
            else None
        ),
        "analyzer": {
            "clang": clang,
            "clang_version": _clang_version(clang),
            "cflags": flags,
            "build_sources": source_paths,
        },
        "measurement_coverage": {
            "translation_units_total": len(source_paths),
            "translation_units_with_ast": parsed_sources,
            "translation_units_with_clean_ast": clean_source_asts,
            "headers_total": len(header_paths),
            "headers_with_ast": parsed_headers,
            "headers_with_clean_ast": clean_header_asts,
            "headers_checked": len(header_checks),
        },
        "metrics": {
            **obligation_metrics,
            "header_self_containment": _header_metric(header_checks),
            "public_api_realization": public_api,
            "cross_file_dependency_closure": dependency,
            "ownership_consistency": ownership,
        },
        "diagnostics": {
            "parse_failures": parse_failures,
            "limitations": [
                "Direct calls and named project types are measured; indirect callback calls are excluded.",
                "Ownership covers unique providers and opaque boundaries, not heap lifecycle correctness.",
                "Rates are marked partial when a project translation unit or header yields no Clang AST.",
                "Obligation metrics use a frozen external rubric; lexical/static matches are necessary proxies, not runtime proof.",
            ],
        },
        "source_hash_preservation": preservation,
    }


def _load_batch_manifest(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    runs = payload.get("runs") if isinstance(payload, dict) else payload
    if not isinstance(runs, list):
        raise ValueError("batch manifest must be a JSON list or an object with a 'runs' list")
    rubric = payload.get("obligation_rubric") if isinstance(payload, dict) else None
    return [dict(item) for item in runs], str(rubric) if rubric else None


def _load_obligation_rubric(path: Path) -> dict[str, Any]:
    rubric = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rubric, dict):
        raise ValueError("obligation rubric must be a JSON object")
    _validate_obligation_rubric(rubric)
    return rubric


def analyze_batch(
    manifest_path: str | Path,
    *,
    clang: str = "clang",
    obligation_rubric_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path).resolve()
    runs, manifest_rubric = _load_batch_manifest(manifest)
    rubric_path = Path(obligation_rubric_path).resolve() if obligation_rubric_path else None
    if rubric_path is None and manifest_rubric:
        raw_rubric = Path(manifest_rubric)
        rubric_path = (raw_rubric if raw_rubric.is_absolute() else manifest.parent / raw_rubric).resolve()
    obligation_rubric = _load_obligation_rubric(rubric_path) if rubric_path else None
    results: list[dict[str, Any]] = []
    for item in runs:
        raw_project = Path(str(item["project_dir"]))
        project = raw_project if raw_project.is_absolute() else PROJECT_ROOT / raw_project
        known = {"project_dir", "binary_name", "method", "run_id", "sample_status"}
        metadata = {key: value for key, value in item.items() if key not in known}
        results.append(
            analyze_contract_closure(
                project,
                str(item.get("binary_name", "mqtt_broker")),
                method=str(item.get("method", "unknown")),
                run_id=str(item.get("run_id", "")),
                sample_status=str(item.get("sample_status", "unknown")),
                clang=clang,
                metadata=metadata,
                obligation_rubric=obligation_rubric,
            )
        )
    return {
        "schema_version": f"{SCHEMA_VERSION}/batch",
        "manifest_path": str(manifest),
        "obligation_rubric_path": str(rubric_path) if rubric_path else None,
        "run_count": len(results),
        "runs": results,
    }


def write_flat_csv(path: str | Path, results: list[dict[str, Any]]) -> None:
    metric_names = (
        "required_obligation_realization",
        "executable_call_path_closure",
        "semantic_grounding_closure",
        "header_self_containment",
        "public_api_realization",
        "cross_file_dependency_closure",
        "ownership_consistency",
    )
    metadata_fields = [
        "cohort",
        "formal_rq1_replicate",
        "qualification_passed",
        "freshness",
        "phase",
    ]
    fields = ["run_id", "method", "sample_status", *metadata_fields, "project_dir"]
    for metric in metric_names:
        fields.extend((f"{metric}_status", f"{metric}_passed", f"{metric}_total", f"{metric}_rate"))
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row: dict[str, Any] = {
                "run_id": result["run_id"],
                "method": result["method"],
                "sample_status": result["sample_status"],
                "project_dir": result["project_dir"],
            }
            metadata = result.get("metadata", {})
            row.update({key: metadata.get(key, "") for key in metadata_fields})
            for metric in metric_names:
                data = result["metrics"][metric]
                row[f"{metric}_status"] = data["status"]
                row[f"{metric}_passed"] = data["passed"]
                row[f"{metric}_total"] = data["total"]
                row[f"{metric}_rate"] = data["rate"]
            writer.writerow(row)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract read-only Contract-to-Code Closure metrics from C projects")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--project-dir", type=Path)
    inputs.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--binary-name", default="mqtt_broker")
    parser.add_argument("--method", default="unknown")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--sample-status", default="unknown")
    parser.add_argument("--obligation-rubric", type=Path)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--csv-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.batch_manifest:
        payload = analyze_batch(
            args.batch_manifest,
            clang=args.clang,
            obligation_rubric_path=args.obligation_rubric,
        )
        results = payload["runs"]
    else:
        payload = analyze_contract_closure(
            args.project_dir,
            args.binary_name,
            method=args.method,
            run_id=args.run_id,
            sample_status=args.sample_status,
            clang=args.clang,
            obligation_rubric=(
                _load_obligation_rubric(args.obligation_rubric.resolve())
                if args.obligation_rubric
                else None
            ),
        )
        results = [payload]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if args.csv_out:
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        write_flat_csv(args.csv_out, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
