from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DECLARATION_KINDS = {"EnumDecl", "FunctionDecl", "RecordDecl", "TypedefDecl", "VarDecl"}
_INCLUDE_RE = re.compile(r"^\s*#\s*include\b")
_DEFINE_RE = re.compile(r"^\s*#\s*define\b")
_GUARD_RE = re.compile(r"^\s*#\s*ifndef\s+([A-Za-z_]\w*)\s*$")


@dataclass(frozen=True)
class HeaderExtraction:
    blocks: tuple[str, ...]
    status: str
    reason: str = ""


def _strip_c_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    state = "code"
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                output.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                output.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            output.append(char)
            if char == '"':
                state = "string"
            elif char == "'":
                state = "char"
        elif state == "line_comment":
            if char == "\n":
                output.append(char)
                state = "code"
            else:
                output.append(" ")
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                output.extend((" ", " "))
                index += 2
                state = "code"
                continue
            output.append("\n" if char == "\n" else " ")
        else:
            output.append(char)
            if char == "\\" and next_char:
                output.append(next_char)
                index += 2
                continue
            if (state == "string" and char == '"') or (state == "char" and char == "'"):
                state = "code"
        index += 1
    return "".join(output)


def _guard_macro(text: str) -> str:
    for line in _strip_c_comments(text).splitlines():
        if not line.strip():
            continue
        match = _GUARD_RE.match(line)
        return match.group(1) if match else ""
    return ""


def _preprocessor_blocks(text: str) -> list[str]:
    guard = _guard_macro(text)
    lines = _strip_c_comments(text).splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not (_INCLUDE_RE.match(line) or _DEFINE_RE.match(line)):
            index += 1
            continue
        block = [line.rstrip()]
        while block[-1].rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            block.append(lines[index].rstrip())
        rendered = "\n".join(block).strip()
        if guard and re.match(rf"^\s*#\s*define\s+{re.escape(guard)}(?:\s|$)", rendered):
            index += 1
            continue
        if rendered:
            blocks.append(rendered)
        index += 1
    return blocks


def _source_point(point: Any) -> dict[str, Any]:
    if not isinstance(point, dict):
        return {}
    if isinstance(point.get("expansionLoc"), dict):
        return point["expansionLoc"]
    if isinstance(point.get("spellingLoc"), dict):
        return point["spellingLoc"]
    return point


def _has_compound_statement(node: dict[str, Any]) -> bool:
    return any(isinstance(item, dict) and item.get("kind") == "CompoundStmt" for item in node.get("inner", []))


def _node_span(node: dict[str, Any], content: str, header: Path) -> tuple[int, int] | None:
    source_range = node.get("range")
    if not isinstance(source_range, dict):
        return None
    begin = _source_point(source_range.get("begin"))
    end = _source_point(source_range.get("end"))
    if not begin or not end or "offset" not in begin or "offset" not in end:
        return None
    for point in (begin, end):
        raw_file = point.get("file")
        if raw_file and Path(str(raw_file)).resolve() != header:
            return None
        if point.get("includedFrom"):
            return None
    start = int(begin["offset"])
    stop = int(end["offset"]) + int(end.get("tokLen", 1) or 1)
    if start < 0 or stop <= start or stop > len(content):
        return None
    cursor = stop
    while cursor < len(content) and content[cursor].isspace():
        cursor += 1
    if cursor < len(content) and content[cursor] == ";":
        stop = cursor + 1
    return start, stop


def _declaration_blocks(ast: dict[str, Any], content: str, header: Path) -> list[str]:
    spans: list[tuple[int, int]] = []
    for node in ast.get("inner", []):
        if not isinstance(node, dict) or node.get("kind") not in _DECLARATION_KINDS:
            continue
        if node.get("kind") == "FunctionDecl" and _has_compound_statement(node):
            continue
        span = _node_span(node, content, header)
        if span is not None:
            spans.append(span)
    selected: list[tuple[int, int]] = []
    for start, stop in sorted(set(spans), key=lambda item: (item[0], -item[1])):
        if any(existing_start <= start and stop <= existing_stop for existing_start, existing_stop in selected):
            continue
        selected.append((start, stop))
    blocks: list[str] = []
    for start, stop in sorted(selected):
        block = _strip_c_comments(content[start:stop]).strip()
        if block and block not in blocks:
            blocks.append(block)
    return blocks


def _clean_header_fallback(content: str) -> str:
    cleaned = _strip_c_comments(content)
    guard = _guard_macro(cleaned)
    lines = cleaned.splitlines()
    if guard:
        lines = [
            line
            for line in lines
            if not re.match(rf"^\s*#\s*(?:ifndef|define)\s+{re.escape(guard)}(?:\s|$)", line)
        ]
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].strip():
                if re.match(r"^\s*#\s*endif\b", lines[index]):
                    del lines[index]
                break
    return "\n".join(lines).strip()


def _brief_reason(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def extract_header_declarations(
    header_path: Path,
    project_dir: Path,
    *,
    clang_path: str | None = None,
) -> HeaderExtraction:
    header = header_path.resolve()
    content = header.read_text(encoding="utf-8", errors="ignore")
    executable = clang_path if clang_path is not None else shutil.which("clang")
    fallback_reason = ""
    if executable:
        try:
            result = subprocess.run(
                [
                    executable,
                    "-Xclang",
                    "-ast-dump=json",
                    "-fsyntax-only",
                    "-ferror-limit=0",
                    "-I",
                    str(project_dir.resolve()),
                    "-x",
                    "c",
                    str(header),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            ast = json.loads(result.stdout)
            declarations = _declaration_blocks(ast, content, header)
            if result.returncode == 0 and declarations:
                return HeaderExtraction(tuple([*_preprocessor_blocks(content), *declarations]), "parsed")
            if result.returncode != 0:
                fallback_reason = "clang rejected header: " + (_brief_reason(result.stderr) or f"exit {result.returncode}")
            else:
                fallback_reason = "clang AST contained no declarations from the target header"
        except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
            fallback_reason = f"clang AST extraction failed: {type(exc).__name__}: {exc}"
    else:
        fallback_reason = "clang executable not found"
    fallback = _clean_header_fallback(content)
    blocks = (fallback,) if fallback else ()
    return HeaderExtraction(blocks, "fallback", _brief_reason(fallback_reason))


def fit_header_context(
    extractions: list[tuple[str, HeaderExtraction]],
    max_bytes: int,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    context: dict[str, str] = {}
    diagnostics: list[dict[str, str]] = []
    used = 0
    exhausted = False
    for relative, extraction in extractions:
        accepted: list[str] = []
        omitted = exhausted
        for block in extraction.blocks:
            size = len(block.encode("utf-8")) + (2 if accepted else 0)
            if exhausted or used + size > max_bytes:
                omitted = True
                exhausted = True
                break
            accepted.append(block)
            used += size
        if accepted:
            context[relative] = "\n\n".join(accepted)
        status = "omitted_due_budget" if omitted else extraction.status
        reason = extraction.reason
        if omitted:
            reason = f"header context budget exhausted after {used} bytes" + (f"; {reason}" if reason else "")
        diagnostics.append({"path": relative, "status": status, "reason": reason})
    return context, diagnostics
