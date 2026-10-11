"""RQ2-only SYSSPEC publication and representation-neutral delivery checks."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import jsonschema

from specforge.documents import digest, hashes, read_json, save_json
from specforge.specs import compile_probe, project_path, validate

SUITE = Path(__file__).resolve().parent
DELIVERY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "files", "tests"],
    "properties": {
        "schema_version": {"const": 1},
        "files": {"type": "array", "minItems": 1, "uniqueItems": True,
                  "items": {"type": "string", "minLength": 1}},
        "tests": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "path", "requirement_ids"],
            "properties": {"id": {"type": "string", "minLength": 1},
                           "path": {"type": "string", "minLength": 1},
                           "requirement_ids": {"type": "array", "uniqueItems": True,
                                               "items": {"type": "string", "minLength": 1}}}}}}}


def sections(text: str) -> dict[str, str]:
    """The four SYSSPEC sections; no filtering of their protocol content."""
    matches = list(re.finditer(r"^\[(PROMPT|RELY|GUARANTEE|SPECIFICATION)\]\s*$", text, re.M))
    if [m[1] for m in matches] != ["PROMPT", "RELY", "GUARANTEE", "SPECIFICATION"]:
        raise ValueError("Expected exactly [PROMPT], [RELY], [GUARANTEE], [SPECIFICATION] in order")
    result = {m[1]: text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
              for i, m in enumerate(matches)}
    if any(not value for value in result.values()):
        raise ValueError("All four sections need content (use 'None' for no dependencies)")
    return result


def compact_signature(text: str) -> str:
    return re.sub(r"\s+", "", text)


def generic_checks(work: Path, scope: dict, *, design: bool = False, review: bool = False) -> dict:
    """Check engineering invariants, not P's protocol JSON schema."""
    errors, probes = [], []
    try:
        plan = read_json(work / "plan.json")
        validator = jsonschema.Draft202012Validator(read_json(SUITE / "schemas/generic_plan.schema.json"))
        errors += [f"plan.json/{'/'.join(map(str, e.absolute_path))}: {e.message}"
                   for e in validator.iter_errors(plan)]
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing or invalid plan.json: {exc}"], "abi": []}
    if errors:
        return {"passed": False, "errors": errors, "abi": []}

    def safe_file(name: str) -> bool:
        path = work / name
        return bool(project_path(name) and path.resolve().is_relative_to(work.resolve())
                    and path.is_file() and not path.is_symlink())

    sources = [f["path"] for f in plan["files"]]
    headers = [h["path"] for h in plan["headers"]]
    function_ids = [f["id"] for f in plan["functions"]]
    for label, names in (("source", sources), ("header", headers), ("function ID", function_ids),
                         ("function", [f["source"] + ":" + f["name"] for f in plan["functions"]])):
        if len(names) != len(set(names)):
            errors.append("Duplicate planned " + label)
    if any(not project_path(p) or not p.endswith(".c") for p in sources):
        errors.append("Planned sources must be concrete project-relative .c paths")
    spec_paths = [plan["project_spec"], *[f["spec"] for f in plan["files"]],
                  *[f["spec"] for f in plan["functions"]]]
    if len(spec_paths) != len(set(spec_paths)):
        errors.append("Each planned project/file/function needs its own .spec document")
    for name in spec_paths:
        if not project_path(name) or not name.endswith(".spec"):
            errors.append("Unsafe or non-SYSSPEC path: " + name)
    parts = {}
    required_specs = spec_paths[:1 + len(sources)] if design else spec_paths
    for name in required_specs:
        if not safe_file(name):
            errors.append("Missing or unsafe specification: " + name)
            continue
        try:
            parts[name] = sections((work / name).read_text())
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
    includes = []
    for header in plan["headers"]:
        if (not project_path(header["path"]) or not header["path"].endswith(".h")
                or header["artifact"] != "abi/" + header["path"] or not safe_file(header["artifact"])):
            errors.append("Missing or unsafe ABI header: " + header["path"])
        else:
            includes.append(header["path"])
    include_root = work / "abi"
    extra_includes = sorted({(include_root / h).parent for h in includes})
    for header in includes:
        probe = compile_probe(f'#include "{header}"\n', include_root, extra_includes)
        probes.append(probe)
        if not probe["passed"]:
            errors.append("Header does not compile independently: " + header + "\n" + probe["output"])
    combined = "".join(f'#include "{h}"\n' for h in includes)
    if includes:
        probe = compile_probe(combined, include_root, extra_includes)
        probes.append(probe)
        if not probe["passed"]:
            errors.append("Combined headers do not compile: " + probe["output"])
    for function in plan["functions"]:
        signature = function["signature"].strip()
        if function["source"] not in sources:
            errors.append("Function source absent from plan: " + function["id"])
        if not signature.endswith(";") or not re.search(r"\b" + re.escape(function["name"]) + r"\s*\(", signature):
            errors.append("Function needs an exact C declaration: " + function["id"])
            continue
        if not design and function["spec"] in parts:
            if compact_signature(signature) not in compact_signature(parts[function["spec"]]["GUARANTEE"]):
                errors.append("GUARANTEE differs from planned signature: " + function["id"])
        if "header" in function:
            if function["header"] not in includes:
                errors.append("Function header absent from ABI: " + function["id"])
                continue
            # Check the symbol is already declared, before redeclaring its type.
            text = combined + f'\nvoid rq2_probe(void) {{ (void)&{function["name"]}; }}\n' + signature + "\n"
            probe = compile_probe(text, include_root, extra_includes)
            probes.append(probe)
            if not probe["passed"]:
                errors.append("ABI declaration differs or is missing: " + function["id"] + "\n" + probe["output"])
    requirement_ids = {r["id"] for r in scope["requirements"]}
    rows = plan["requirements"]
    if len(rows) != len(requirement_ids) or {r["id"] for r in rows} != requirement_ids:
        errors.append("Plan requirement IDs differ from approved scope")
    for row in rows:
        for reference in row["spec_refs"]:
            name = reference.split("#", 1)[0]
            if name not in spec_paths or (not design and not safe_file(name)):
                errors.append("Unknown requirement specification reference: " + reference)
    if not design:
        if not safe_file("scope.json") or read_json(work / "scope.json") != scope:
            errors.append("Scope differs from approved own Facts")
    if review:
        try:
            records = read_json(work / "review.json")["requirements"]
            if (len(records) != len(requirement_ids) or {r["id"] for r in records} != requirement_ids):
                errors.append("Independent review must cover each requirement exactly once")
            for row in records:
                if not row.get("semantic_review", "").strip() or not row.get("spec_refs"):
                    errors.append("Missing independent semantic review: " + row["id"])
                for ref in row.get("spec_refs", []):
                    if ref.split("#", 1)[0] not in spec_paths:
                        errors.append("Unknown review reference: " + ref)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append("Missing or invalid independent review.json: " + str(exc))
    return {"passed": not errors, "errors": errors, "abi": probes}


def publish_generic(work: Path, scope: dict, revision: int) -> dict:
    result = generic_checks(work, scope, review=True)
    if not result["passed"]:
        raise ValueError("Cannot publish invalid SYSSPEC: " + "; ".join(result["errors"]))
    plan = read_json(work / "plan.json")
    summary = ["# Independently generated SYSSPEC project", "", f"Revision: {revision}",
               "", "Read scope.json and project.spec, then relevant file/function contracts.",
               "Runtime: " + scope["runtime_contract"]["argv_contract"], "", "## Sources", ""]
    summary += [f'- {f["path"]}: {f["spec"]}' for f in plan["files"]]
    summary += ["", "## Functions", ""] + [f'- {f["id"]}: {f["spec"]}' for f in plan["functions"]]
    summary += ["", "## Public headers", ""] + [f'- {h["path"]}: {h["artifact"]}' for h in plan["headers"]]
    (work / "SUMMARY.md").write_text("\n".join(summary) + "\n")
    paths = {"plan.json", "scope.json", "review.json", "SUMMARY.md", plan["project_spec"],
             *[f["spec"] for f in plan["files"]], *[f["spec"] for f in plan["functions"]],
             *[h["artifact"] for h in plan["headers"]]}
    manifest = {"schema_version": 1, "representation": "sysspec", "revision": revision,
                "runtime_contract": scope["runtime_contract"], "files": plan["files"],
                "headers": plan["headers"], "functions": plan["functions"],
                "hashes": {p: digest(work / p) for p in sorted(paths)}}
    save_json(work / "bundle.json", manifest)
    return manifest


def describe_bundle(bundle: Path, condition: str, requirements: list[str], contract: dict,
                    revision: int) -> dict:
    if condition == "G":
        result = generic_checks(bundle, read_json(bundle / "scope.json"), review=True)
    elif condition == "P":
        result = validate(bundle)
    else:
        raise ValueError("Only G/P have a published specification")
    if not result["passed"]:
        raise ValueError("Invalid publication: " + "; ".join(result["errors"]))
    manifest = read_json(bundle / "bundle.json")
    scope = read_json(bundle / "scope.json")
    if manifest["revision"] != revision:
        raise ValueError("Stale specification revision")
    if manifest["runtime_contract"] != contract or scope["runtime_contract"] != contract:
        raise ValueError("Published runtime contract differs from raw task")
    if sorted(r["id"] for r in scope["requirements"]) != sorted(requirements):
        raise ValueError("Published requirement IDs differ from raw task")
    for name, expected in manifest["hashes"].items():
        path = bundle / name
        if (not project_path(name) or not path.resolve().is_relative_to(bundle.resolve())
                or not path.is_file() or digest(path) != expected):
            raise ValueError("Published artifact changed or is unsafe: " + name)
    return {"representation": "sysspec" if condition == "G" else "protocol-json",
            "revision": revision, "requirement_ids": requirements, "runtime_contract": contract,
            "published_directory": str(bundle.resolve()),
            "planned_sources": [f["path"] for f in manifest["files"]], "headers": manifest["headers"],
            "hashes": {"bundle.json": digest(bundle / "bundle.json"), **manifest["hashes"]}}


def copy_publication(bundle: Path, descriptor: dict, destination: Path) -> None:
    destination.mkdir(parents=True)
    for name, expected in descriptor["hashes"].items():
        if digest(bundle / name) != expected:
            raise ValueError("Published artifact changed before handoff: " + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / name, target)
    if hashes(destination) != descriptor["hashes"]:
        raise ValueError("Publication handoff changed bytes")


def check_delivery(project: Path, requirement_ids: list[str], descriptor: dict | None,
                   view_root: Path | None) -> dict:
    errors = []
    try:
        delivery = read_json(project / "delivery.json")
        errors += [f"delivery.json/{'/'.join(map(str, e.absolute_path))}: {e.message}"
                   for e in jsonschema.Draft202012Validator(DELIVERY_SCHEMA).iter_errors(delivery)]
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing or invalid delivery.json: {exc}"], "test_count": 0}
    if errors:
        return {"passed": False, "errors": errors, "test_count": 0}
    listed = set(delivery["files"])
    for name in listed:
        path = project / name
        if not project_path(name) or not path.resolve().is_relative_to(project.resolve()) or not path.is_file():
            errors.append("Missing or unsafe delivery file: " + name)
    for name in ("Makefile", "README.md"):
        path = project / name
        if name not in listed or not path.is_file() or not path.read_text().strip():
            errors.append("Missing nonempty delivery document: " + name)
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (".c", ".h", ".py", ".sh") and path.relative_to(project).as_posix() not in listed:
            errors.append("Unlisted source or development test: " + path.relative_to(project).as_posix())
    if len([p for p in listed if p.endswith(".c") and (project / p).is_file()]) < 2:
        errors.append("The task requires multiple C source files")
    ids = [t["id"] for t in delivery["tests"]]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate development test ID")
    covered = set()
    for test in delivery["tests"]:
        if test["path"] not in listed:
            errors.append("Test file absent from inventory: " + test["path"])
        covered.update(test["requirement_ids"])
    if covered != set(requirement_ids):
        errors.append(f"Requirement coverage differs: missing {sorted(set(requirement_ids) - covered)}, "
                      f"unknown {sorted(covered - set(requirement_ids))}")
    if descriptor:
        if hashes(view_root) != descriptor["hashes"]:
            errors.append("Read-only published view changed after handoff")
        for name in descriptor["planned_sources"]:
            if name not in listed:
                errors.append("Planned source missing: " + name)
        for header in descriptor["headers"]:
            path = project / header["path"]
            if (header["path"] not in listed or not path.is_file()
                    or digest(path) != descriptor["hashes"][header["artifact"]]):
                errors.append("Public header differs from published ABI: " + header["path"])
    return {"passed": not errors, "errors": errors, "test_count": len(ids)}
