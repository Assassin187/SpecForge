from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CATEGORIES = ("C1", "C2", "C3", "C4", "C5")
PROJECT_SUFFIXES = {".c", ".h"}
IGNORED_DIRS = {
    ".git",
    ".repair",
    "__pycache__",
    "spec_bundle",
    "gold_specs",
    "specs-example",
    "protocol-example",
}
CONTROL_WORDS = {"if", "for", "while", "switch", "return", "sizeof"}

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
_FUNC_DEF_RE = re.compile(
    r"(?m)^\s*(static\s+)?(?:[A-Za-z_][\w\s\*\(\),]*?\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
)
_FUNC_DECL_RE = re.compile(
    r"(?m)^\s*(?:extern\s+)?(?:[A-Za-z_][\w\s\*\(\),]*?\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*;"
)
_DIAG_START_RE = re.compile(r"^(.+\.(?:c|h)):\d+:\d+:\s*((?:fatal\s+)?error|warning|note):\s*(.*)")
_LINK_UNDEF_RE = re.compile(r"undefined reference to [`'‘]([^`'’]+)[`'’]")
_LINK_MULTI_RE = re.compile(r"multiple definition of [`'‘]([^`'’]+)[`'’]")


@dataclass
class BuildPlan:
    cc: str
    cflags: list[str]
    ldflags: list[str]
    sources: list[str]
    include_paths: list[str]
    target: str


def relpath(path: Path, root: Path) -> str:
    return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()


def iter_project_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(project_dir.rglob("*")):
        if any(part in IGNORED_DIRS for part in path.relative_to(project_dir).parts):
            continue
        if path.is_file() and (path.name == "Makefile" or path.suffix in PROJECT_SUFFIXES):
            files.append(path)
    return files


def validate_source_tree(project_dir: Path, binary_name: str) -> list[str]:
    reasons: list[str] = []
    if not project_dir.is_dir():
        return ["project_dir_missing"]
    if not (project_dir / "Makefile").is_file():
        reasons.append("missing_makefile")
    sources = [path for path in iter_project_files(project_dir) if path.suffix == ".c"]
    if not sources:
        reasons.append("missing_c_sources")
    if not any(path.name == "main.c" for path in sources):
        reasons.append("missing_main_c")
    if binary_name and (project_dir / "Makefile").is_file():
        text = (project_dir / "Makefile").read_text(encoding="utf-8", errors="ignore")
        if binary_name not in text and "TARGET" not in text:
            reasons.append("makefile_missing_binary_target")
    return reasons


def _logical_make_lines(text: str) -> list[str]:
    logical: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not current:
            current = line
        else:
            current += " " + line.lstrip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        logical.append(current)
        current = ""
    if current:
        logical.append(current)
    return logical


def _make_vars(text: str) -> dict[str, str]:
    vars_: dict[str, str] = {}
    for line in _logical_make_lines(text):
        if not line or line.lstrip().startswith("#") or ":" in line.split("=", 1)[0]:
            continue
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?=|:=|=)\s*(.*)$", line)
        if match:
            vars_[match.group(1)] = match.group(2).strip()
    return vars_


def _split_flags(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def discover_build_plan(project_dir: Path, binary_name: str) -> BuildPlan:
    makefile = project_dir / "Makefile"
    text = makefile.read_text(encoding="utf-8", errors="ignore") if makefile.is_file() else ""
    vars_ = _make_vars(text)
    cflags = _split_flags(vars_.get("CFLAGS", "-std=c11 -Wall -Wextra -Werror=implicit-function-declaration -I."))
    ldflags = _split_flags(vars_.get("LDFLAGS", ""))
    cc = vars_.get("CC", "gcc").split()[0] or "gcc"
    target = vars_.get("TARGET", binary_name) or binary_name
    source_value = " ".join(value for key, value in vars_.items() if key.endswith("SRCS") or key == "SRCS")
    sources = [item for item in _split_flags(source_value) if item.endswith(".c")]
    if not sources:
        sources = [relpath(path, project_dir) for path in iter_project_files(project_dir) if path.suffix == ".c"]
    sources = [source for source in sources if (project_dir / source).is_file()]
    include_paths = [flag[2:] for flag in cflags if flag.startswith("-I") and len(flag) > 2]
    if "." not in include_paths:
        include_paths.insert(0, ".")
    return BuildPlan(cc=cc, cflags=cflags, ldflags=ldflags, sources=sorted(dict.fromkeys(sources)), include_paths=include_paths, target=target)


def _run(cmd: list[str], cwd: Path, *, input_text: str | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return {
            "command": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + "\n[timeout]",
        }


def run_clean_build(project_dir: Path, binary_name: str) -> dict[str, Any]:
    clean = _run(["make", "clean"], project_dir, timeout=20) if (project_dir / "Makefile").is_file() else {
        "command": ["make", "clean"],
        "returncode": 2,
        "stdout": "",
        "stderr": "missing Makefile",
    }
    build = _run(["make", binary_name], project_dir, timeout=60) if (project_dir / "Makefile").is_file() else {
        "command": ["make", binary_name],
        "returncode": 2,
        "stdout": "",
        "stderr": "missing Makefile",
    }
    return {"clean": clean, **build}


def run_header_self_checks(project_dir: Path, plan: BuildPlan) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    flags = [flag for flag in plan.cflags if not flag.startswith("-o")]
    for header in sorted(project_dir.rglob("*.h")):
        if any(part in IGNORED_DIRS for part in header.relative_to(project_dir).parts):
            continue
        relative = relpath(header, project_dir)
        source = f'#include "{relative}"\n#include "{relative}"\nint main(void) {{ return 0; }}\n'
        cmd = [plan.cc, *flags, "-xc", "-fsyntax-only", "-"]
        result = _run(cmd, project_dir, input_text=source, timeout=20)
        checks.append({"path": relative, **result})
    return checks


def run_source_checks(project_dir: Path, plan: BuildPlan) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    flags = [flag for flag in plan.cflags if not flag.startswith("-o")]
    for source in plan.sources:
        cmd = [plan.cc, *flags, "-fsyntax-only", source]
        checks.append({"path": source, **_run(cmd, project_dir, timeout=30)})
    return checks


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def build_repair_index(project_dir: Path, plan: BuildPlan, build_result: dict[str, Any]) -> dict[str, Any]:
    declarations: dict[str, list[dict[str, str]]] = {}
    definitions: dict[str, list[dict[str, str]]] = {}
    files: list[dict[str, Any]] = []
    headers_by_name: dict[str, list[str]] = {}
    for path in iter_project_files(project_dir):
        relative = relpath(path, project_dir)
        text = _read(path)
        includes = _INCLUDE_RE.findall(text)
        files.append(
            {
                "path": relative,
                "kind": "makefile" if path.name == "Makefile" else path.suffix[1:],
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "line_count": len(text.splitlines()),
                "includes": includes,
            }
        )
        if path.suffix == ".h":
            headers_by_name.setdefault(path.name, []).append(relative)
        for static_kw, name in _FUNC_DEF_RE.findall(text):
            if name in CONTROL_WORDS:
                continue
            definitions.setdefault(name, []).append({"path": relative, "storage": "static" if static_kw else "extern"})
        for name in _FUNC_DECL_RE.findall(text):
            if name in CONTROL_WORDS:
                continue
            declarations.setdefault(name, []).append({"path": relative})
    stderr = str(build_result.get("stderr", ""))
    undefined = sorted(set(_LINK_UNDEF_RE.findall(stderr)))
    duplicate = sorted(set(_LINK_MULTI_RE.findall(stderr)))
    missing_headers = sorted(set(re.findall(r"fatal error: ([^:]+): No such file or directory", stderr)))
    return {
        "build_plan": {
            "cc": plan.cc,
            "cflags": plan.cflags,
            "ldflags": plan.ldflags,
            "sources": plan.sources,
            "include_paths": plan.include_paths,
            "target": plan.target,
        },
        "files": files,
        "headers_by_name": headers_by_name,
        "function_declarations": declarations,
        "function_definitions": definitions,
        "duplicate_definition": {name: defs for name, defs in definitions.items() if len([d for d in defs if d["storage"] == "extern"]) > 1},
        "undefined_reference": undefined,
        "missing_header": missing_headers,
        "link_duplicate_symbol": duplicate,
    }


def diagnostic_blocks(stdout: str, stderr: str, project_dir: Path) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current:
            current["text"] = "\n".join(current.pop("lines"))
            blocks.append(current)
        current = None

    for line in f"{stdout}\n{stderr}".splitlines():
        match = _DIAG_START_RE.match(line)
        if match:
            flush()
            raw_path = match.group(1)
            try:
                path = Path(raw_path)
                absolute = path if path.is_absolute() else project_dir / path
                relative = absolute.resolve(strict=False).relative_to(project_dir.resolve(strict=False)).as_posix()
            except Exception:  # noqa: BLE001
                relative = raw_path
            current = {
                "path": relative,
                "level": match.group(2),
                "message": match.group(3),
                "lines": [line],
            }
        elif current:
            current["lines"].append(line)
    flush()
    return blocks


def _symbol_from_text(text: str) -> str:
    for pattern in (
        r"function [`'‘]([^`'’]+)[`'’]",
        r"for [`'‘]([^`'’]+)[`'’]",
        r"reference to [`'‘]([^`'’]+)[`'’]",
        r"[`'‘]([A-Za-z_]\w*)[`'’] undeclared",
        r"unknown type name [`'‘]([^`'’]+)[`'’]",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _is_protocol_semantic(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "packet",
            "message",
            "parse",
            "parser",
            "decode",
            "encode",
            "serializer",
            "handler",
            "mqtt_",
            "coap_",
            "smtp_",
            "http_",
            "connack",
            "suback",
            "publish",
            "subscribe",
        )
    )


def classify_text(text: str, path: str = "", phase: str = "compile", index: dict[str, Any] | None = None) -> tuple[str, bool, str]:
    lowered = text.lower()
    symbol = _symbol_from_text(text)
    if "missing makefile" in lowered or "no rule to make target" in lowered:
        return "C1", False, symbol
    if any(token in lowered for token in ("expected", "stray", "unterminated", "syntax error")):
        return "C1", False, symbol
    if "unknown type name" in lowered and any(name in lowered for name in ("size_t", "ssize_t", "uint", "bool", "fd_set", "socklen_t")):
        return ("C2" if path.endswith(".h") else "C1"), False, symbol
    if "implicit declaration of function" in lowered:
        if any(name in lowered for name in ("malloc", "free", "memcpy", "strlen", "read", "write", "socket", "bind", "listen", "accept", "select", "asprintf")):
            return "C1", False, symbol
        defs = (index or {}).get("function_definitions", {}).get(symbol, []) if symbol else []
        return ("C2" if defs else "C3"), False, symbol
    if "fatal error" in lowered and "no such file" in lowered:
        return "C2", False, symbol
    if any(token in lowered for token in ("static declaration", "previous declaration", "conflicting types")):
        return "C3", False, symbol
    if any(token in lowered for token in ("too few arguments", "too many arguments", "incompatible pointer type", "void value not ignored")):
        return "C3", False, symbol
    if any(token in lowered for token in ("redefinition of ‘struct", "redefinition of 'struct", "invalid use of undefined type", "storage size of")):
        return "C4", True, symbol
    if "multiple definition" in lowered:
        return "C4", True, symbol
    if "undefined reference" in lowered:
        return ("C5" if _is_protocol_semantic(text) else "C3"), _is_protocol_semantic(text), symbol
    if ("undeclared" in lowered or "has no member" in lowered) and _is_protocol_semantic(text):
        return "C5", True, symbol
    if _is_protocol_semantic(text) and any(token in lowered for token in ("undeclared", "no member", "incomplete type")):
        return "C5", True, symbol
    return ("C3" if phase == "link" else "C2"), False, symbol


def _cause_id(category: str, phase: str, path: str, symbol: str, text: str) -> str:
    payload = "|".join((category, phase, path, symbol, " ".join(text.split())[:300]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def classify_diagnostics(
    project_dir: Path,
    build_result: dict[str, Any],
    header_checks: list[dict[str, Any]],
    source_checks: list[dict[str, Any]],
    file_index: dict[str, Any],
) -> list[dict[str, Any]]:
    causes: list[dict[str, Any]] = []

    def add(category: str, phase: str, path: str, text: str, planning_dependent: bool, symbol: str = "") -> None:
        cause = {
            "id": _cause_id(category, phase, path, symbol, text),
            "category": category,
            "phase": phase,
            "path": path,
            "symbol": symbol,
            "message": " ".join(text.split())[:500],
            "planning_dependent": planning_dependent,
        }
        if cause["id"] not in {item["id"] for item in causes}:
            causes.append(cause)

    for block in diagnostic_blocks(str(build_result.get("stdout", "")), str(build_result.get("stderr", "")), project_dir):
        if "error" not in block["level"]:
            continue
        category, dep, symbol = classify_text(block["text"], block["path"], "compile", file_index)
        add(category, "compile", block["path"], block["text"], dep, symbol)

    for check in header_checks:
        if int(check.get("returncode", 0)) == 0:
            continue
        for block in diagnostic_blocks(str(check.get("stdout", "")), str(check.get("stderr", "")), project_dir):
            if "error" in block["level"]:
                category, dep, symbol = classify_text(block["text"], str(check.get("path", "")), "header", file_index)
                add(category, "header", str(check.get("path", "")), block["text"], dep, symbol)

    for check in source_checks:
        if int(check.get("returncode", 0)) == 0:
            continue
        for block in diagnostic_blocks(str(check.get("stdout", "")), str(check.get("stderr", "")), project_dir):
            if "error" in block["level"]:
                category, dep, symbol = classify_text(block["text"], str(check.get("path", "")), "source", file_index)
                add(category, "source", str(check.get("path", "")), block["text"], dep, symbol)

    for symbol in file_index.get("undefined_reference", []):
        text = f"undefined reference to `{symbol}`"
        category, dep, _ = classify_text(text, "", "link", file_index)
        add(category, "link", "", text, dep, symbol)
    for symbol in file_index.get("link_duplicate_symbol", []):
        text = f"multiple definition of `{symbol}`"
        category, dep, _ = classify_text(text, "", "link", file_index)
        add(category, "link", "", text, dep, symbol)
    return sorted(causes, key=lambda item: (item["category"], item["phase"], item["path"], item["symbol"], item["id"]))


def category_counts(causes: list[dict[str, Any]]) -> dict[str, int]:
    return {category: sum(1 for item in causes if item.get("category") == category) for category in CATEGORIES}


def error_counts(header_checks: list[dict[str, Any]], source_checks: list[dict[str, Any]], build_result: dict[str, Any]) -> dict[str, int]:
    return {
        "header_compile_errors": sum(1 for item in header_checks if int(item.get("returncode", 0)) != 0),
        "source_errors": sum(1 for item in source_checks if int(item.get("returncode", 0)) != 0),
        "link_errors": len(_LINK_UNDEF_RE.findall(str(build_result.get("stderr", "")))) + len(_LINK_MULTI_RE.findall(str(build_result.get("stderr", "")))),
    }


def create_diagnostic_snapshot(project_dir: Path, binary_name: str) -> dict[str, Any]:
    plan = discover_build_plan(project_dir, binary_name)
    build = run_clean_build(project_dir, binary_name)
    headers = run_header_self_checks(project_dir, plan)
    sources = run_source_checks(project_dir, plan)
    index = build_repair_index(project_dir, plan, build)
    causes = classify_diagnostics(project_dir, build, headers, sources, index)
    return {
        "build": build,
        "header_checks": headers,
        "source_checks": sources,
        "file_index": index,
        "root_causes": causes,
        "category_counts": category_counts(causes),
        "error_counts": error_counts(headers, sources, build),
    }
