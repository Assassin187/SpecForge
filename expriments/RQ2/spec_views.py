"""Deterministic, published-artifact-only views of a frozen SpecForge bundle."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from specforge.documents import digest, hashes, read_json, save_json

CONDITIONS = ("direct", "generic", "full", "full_no_vectors", "full_no_contracts")
GENERIC_REMOVED = {"TEST_VECTORS", "WIRE_MAPPING", "CALL_CONTRACTS",
                   "TRACE_ID", "TRACE_REFS", "DOC_REF"}


def _part(key) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def remove_fields(value, names: set[str], path: str, changes: list, pointer: str = ""):
    """Remove named fields, recording their source pointers, sizes and hashes."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            ref = pointer + "/" + _part(key)
            if key in names:
                encoded = json.dumps(child, ensure_ascii=False, sort_keys=True).encode()
                changes.append({"source": path + "#" + ref, "operation": "remove",
                                "item_count": len(child) if isinstance(child, (list, dict)) else 1,
                                "sha256": hashlib.sha256(encoded).hexdigest()})
            else:
                result[key] = remove_fields(child, names, path, changes, ref)
        return result
    if isinstance(value, list):
        return [remove_fields(v, names, path, changes, pointer + "/" + str(i))
                for i, v in enumerate(value)]
    return value


def _text(value, level=0) -> str:
    """Render data literally; do not summarize or infer missing contracts."""
    if isinstance(value, dict):
        lines = []
        for key, child in value.items():
            lines.append("  " * level + key + ":")
            lines.append(_text(child, level + 1))
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join("  " * level + "- " + _text(v, level + 1).lstrip() for v in value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def scope_text(scope: dict) -> str:
    parts = ["# Project scope", "", f"{scope['protocol']} {scope['version']}; {scope['role']}",
             f"{scope['language']}; {scope['runtime']}", "",
             _text(scope["runtime_contract"]), "", "## Requirements"]
    parts.extend(f"- {r['id']}: {r['description']}" for r in scope["requirements"])
    for label, key in (("Capabilities", "required_capabilities"), ("Exclusions", "excluded_features")):
        parts += ["", "## " + label] + ["- " + v for v in scope[key]]
    parts += ["", "## Engineering decisions"]
    parts += ["- " + r["decision"] for r in scope.get("engineering_defaults", [])]
    return "\n".join(parts) + "\n"


def _copy_published(source: Path, target: Path, manifest: dict) -> None:
    target.mkdir(parents=True)
    for relative in ["bundle.json", *manifest["hashes"]]:
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, output)


def _prune_refs(value, removed: set[str], path: str, changes: list, pointer=""):
    def dropped(child, ref):
        if not isinstance(child, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+\.json#(/.*)?", child):
            return False
        if any(child == old or child.startswith(old + "/") for old in removed):
            changes.append({"source": path + "#" + ref, "operation": "remove_dangling_reference",
                            "reference": child})
            return True
        return False
    if isinstance(value, dict):
        return {k: _prune_refs(v, removed, path, changes, pointer + "/" + _part(k))
                for k, v in value.items() if not dropped(v, pointer + "/" + _part(k))}
    if isinstance(value, list):
        return [_prune_refs(v, removed, path, changes, pointer + "/" + str(i))
                for i, v in enumerate(value) if not dropped(v, pointer + "/" + str(i))]
    return value


def _generic(source: Path, target: Path, manifest: dict) -> dict:
    target.mkdir(parents=True)
    changes, mapping = [], []
    scope = read_json(source / "scope.json")
    (target / "scope.md").write_text(scope_text(scope), encoding="utf-8")
    mapping.append({"source": "scope.json", "target": "scope.md",
                    "operation": "render_scope_without_source_locations"})
    for header in manifest["headers"]:
        output = target / header["artifact"]
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / header["artifact"], output)
        mapping.append({"source": header["artifact"], "target": header["artifact"], "operation": "copy_abi"})
    module = remove_fields(read_json(source / manifest["module"]), GENERIC_REMOVED,
                           manifest["module"], changes)
    module.pop("KIND", None)
    (target / "project.spec").write_text(
        "[PROMPT]\nProject design.\n\n[RELY]\n" + _text(module.get("MODULES", [])) +
        "\n\n[GUARANTEE]\n" + _text(module.get("PROTOCOL", {})) +
        "\n\n[SPECIFICATION]\n" + _text({k: v for k, v in module.items()
                                             if k not in ("MODULES", "PROTOCOL")}) + "\n", encoding="utf-8")
    mapping.append({"source": manifest["module"], "target": "project.spec", "operation": "render_design"})
    navigation = ["# Generic engineering specifications", "", "Read scope.md and project.spec first.",
                  "Function specifications use [RELY], [GUARANTEE] and [SPECIFICATION].",
                  "", "## Sources"]
    for row in manifest["files"]:
        relative = str(Path(row["spec"]).with_suffix(".spec"))
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        value = remove_fields(read_json(source / row["spec"]), GENERIC_REMOVED, row["spec"], changes)
        (output).write_text(
            "[PROMPT]\n" + _text(value["FILE"]) +
            "\n\n[RELY]\n" + _text({k: v for k, v in value["SOURCE"].items()
                                       if k in ("DEPENDENCY", "SYSTEM_DEPENDENCY")}) +
            "\n\n[GUARANTEE]\n" + _text(value.get("HEADER", {})) +
            "\n\n[SPECIFICATION]\n" + _text({k: v for k, v in value.items()
                                                if k not in ("KIND", "FILE", "HEADER")}) + "\n", encoding="utf-8")
        mapping.append({"source": row["spec"], "target": relative, "operation": "render_file"})
        navigation.append(f"- {row['path']}: {relative}")
    navigation += ["", "## Functions"]
    for row in manifest["functions"]:
        value = remove_fields(read_json(source / row["spec"]), GENERIC_REMOVED, row["spec"], changes)
        relative = str(Path(row["spec"]).with_suffix(".spec"))
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        behavior = {k: v for k, v in value.items()
                    if k not in ("KIND", "ROLE", "SIGNATURE", "RELY")}
        output.write_text(
            "[PROMPT]\n" + value["ROLE"] + "\n\n[RELY]\n" + _text(value["RELY"]) +
            "\n\n[GUARANTEE]\n" + _text(value["SIGNATURE"]) +
            "\n\n[SPECIFICATION]\n" + _text(behavior) + "\n", encoding="utf-8")
        for key, section in (("RELY", "RELY"), ("SIGNATURE", "GUARANTEE"),
                             ("LOGIC", "SPECIFICATION"), ("EVENT", "SPECIFICATION")):
            if key in value:
                mapping.append({"source": row["spec"] + "#/" + key,
                                "target": relative + "#[" + section + "]", "operation": "render_literal"})
        navigation.append(f"- {row['name']}: {relative}")
    (target / "SUMMARY.md").write_text("\n".join(navigation) + "\n", encoding="utf-8")
    changes.append({"source": "traceability.json", "operation": "remove_artifact"})
    return {"mapping": mapping, "changes": changes}


def make_view(source: Path, target: Path, condition: str) -> dict:
    """Never copy unlisted history, reviewer scripts, checkpoints or code."""
    manifest = read_json(source / "bundle.json")
    if condition == "generic":
        result = _generic(source, target, manifest)
    elif condition in ("full", "full_no_vectors", "full_no_contracts"):
        _copy_published(source, target, manifest)
        changes = []
        names = ({"TEST_VECTORS"} if condition == "full_no_vectors" else
                 {"WIRE_MAPPING", "CALL_CONTRACTS"} if condition == "full_no_contracts" else set())
        if names:
            for relative in manifest["hashes"]:
                if relative.endswith(".json"):
                    path = target / relative
                    save_json(path, remove_fields(read_json(path), names, relative, changes))
            removed = {c["source"] for c in changes}
            for relative in manifest["hashes"]:
                if relative.endswith(".json"):
                    path = target / relative
                    save_json(path, _prune_refs(read_json(path), removed, relative, changes))
            updated = {**manifest, "origin": "rq2_" + condition,
                       "hashes": {p: digest(target / p) for p in manifest["hashes"]}}
            save_json(target / "bundle.json", updated)
        result = {"mapping": [{"source": p, "target": p, "operation": "copy_published"}
                              for p in ["bundle.json", *manifest["hashes"]]], "changes": changes}
    else:
        raise ValueError(f"Unsupported Spec condition: {condition}")
    return {**result, "condition": condition, "hashes": hashes(target),
            "planned_sources": [r["path"] for r in manifest["files"]],
            "headers": manifest["headers"],
            "bytes": sum(p.stat().st_size for p in target.rglob("*") if p.is_file())}
