"""Deterministic input freezing, PDF extraction and evidence checks."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): digest(p)
            for p in sorted(directory.rglob("*")) if p.is_file()}


def artifact_errors(kind: str, value) -> list[str]:
    schema = read_json(ROOT / "schemas/artifacts.schema.json")
    validator = jsonschema.Draft202012Validator({"$ref": f"#/$defs/{kind}", "$defs": schema["$defs"]})
    return [f"{kind}:{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in sorted(validator.iter_errors(value), key=lambda e: str(e.absolute_path))]


def prepare(run: Path, task: Path, requirements: Path, protocol: Path) -> dict:
    if protocol.suffix.lower() not in (".pdf", ".txt"):
        raise ValueError("Protocol input must be a PDF or UTF-8 .txt document")
    pdf = protocol.suffix.lower() == ".pdf"
    for binary in (("pdftotext",) if pdf else ()) + ("gcc", "make", "bwrap"):
        if not shutil.which(binary):
            raise RuntimeError(f"Required tool unavailable: {binary}")
    inputs, documents = run / "inputs", run / "documents"
    inputs.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    protocol_name = "protocol.pdf" if pdf else "protocol.txt"
    for source, name in ((task, "TASK.md"), (requirements, "REQUIREMENTS.md"), (protocol, protocol_name)):
        shutil.copyfile(source, inputs / name)
    if pdf:
        result = subprocess.run(["pdftotext", "-layout", str(inputs / protocol_name), "-"],
                                capture_output=True, text=True, timeout=60, check=True)
        pages = result.stdout.split("\f")
        if not pages[-1].strip():
            pages.pop()
    else:
        lines = (inputs / protocol_name).read_text(encoding="utf-8").splitlines()
        pages = ["\n".join(lines[i:i + 200]) for i in range(0, len(lines), 200)]
    index = {"schema_version": 2, "extractor": "pdftotext -layout" if pdf else "utf8-lines",
             "cleaning": "None; preserve original content and chunk-local text lines.", "chunks": []}
    for number, page in enumerate(pages, 1):
        name = f"page_{number:03d}.txt" if pdf else f"chunk_{number:03d}.txt"
        (documents / name).write_text(page, encoding="utf-8")
        index["chunks"].append({"chunk_id": f"p{number:03d}", **({"page": number} if pdf else {"source_line_start": (number - 1) * 200 + 1}),
                                "path": name, "line_count": len(page.splitlines()),
                                "sha256": digest(documents / name)})
    if not any((documents / c["path"]).read_text().strip() for c in index["chunks"]):
        raise ValueError("Protocol contains no readable text; OCR is outside this implementation")
    save_json(documents / "index.json", index)
    navigation = ["# Protocol document index", "", "Tool line numbers start at 1 within each chunk, independently of printed counters.", "", "## Extracted section headings", ""]
    for c in index["chunks"]:
        for line_number, line in enumerate((documents / c["path"]).read_text().splitlines(), 1):
            match = re.match(r"^\s*(?:\d+\s+)?(\d+(?:\.\d+)*\.?)\s+([A-Z].{2,100})$", line)
            if match:
                navigation.append(f"- {c['chunk_id']}:{line_number}: {match[1]} {match[2].strip()}")
    navigation += ["", "## Chunks", "", "Full chunk IDs, file paths and line counts are in index.json."]
    navigation += [f"- {c['chunk_id']}: {c['path']} ({c['line_count']} lines)" for c in index["chunks"]]
    (documents / "SUMMARY.md").write_text("\n".join(navigation) + "\n")
    return {"inputs": hashes(inputs), "documents": hashes(documents), "pages": len(pages)}


def check_facts(facts_dir: Path, inputs: Path, documents: Path) -> dict:
    errors = []
    try:
        scope, facts = read_json(facts_dir / "scope.json"), read_json(facts_dir / "facts.json")
        errors += artifact_errors("scope", scope) + artifact_errors("facts", facts)
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [str(exc)]}
    if errors:
        return {"passed": False, "errors": errors}
    chunks = {c["chunk_id"]: c for c in read_json(documents / "index.json")["chunks"]}
    ids = set()
    for fact in facts["facts"]:
        if fact["id"] in ids:
            errors.append(f"Duplicate fact ID: {fact['id']}")
        ids.add(fact["id"])
        for evidence in fact["evidence"]:
            chunk = chunks.get(evidence["chunk_id"])
            if not chunk or not 1 <= evidence["line_start"] <= evidence["line_end"] <= chunk["line_count"]:
                errors.append(f"Invalid evidence location in {fact['id']}: {evidence}")
            elif not "\n".join((documents / chunk["path"]).read_text().splitlines()[
                    evidence["line_start"] - 1:evidence["line_end"]]).strip():
                errors.append(f"Empty evidence in {fact['id']}")
    if facts["open_questions"]:
        errors.append("Unresolved in-scope protocol questions")
    requirement_lines = (inputs / "REQUIREMENTS.md").read_text().splitlines()
    expected = set(re.findall(r"\b(R\d+)\s*:", "\n".join(requirement_lines)))
    actual = [r["id"] for r in scope["requirements"]]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        errors.append(f"Requirement IDs must equal input IDs: {sorted(expected)}")
    for req in scope["requirements"]:
        src = req["source"]
        if src["file"] != "REQUIREMENTS.md" or not 1 <= src["line_start"] <= src["line_end"] <= len(requirement_lines):
            errors.append(f"Invalid requirement source for {req['id']}")
        elif req["id"] not in "\n".join(requirement_lines[src["line_start"] - 1:src["line_end"]]):
            errors.append(f"Requirement ID absent at source: {req['id']}")
    return {"passed": not errors, "errors": errors, "fact_count": len(ids), "requirement_count": len(actual)}

