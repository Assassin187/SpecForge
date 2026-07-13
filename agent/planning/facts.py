from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import JsonObject


def read_json(path: str | Path) -> JsonObject:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def stable_json_hash(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "protocol"


def protocol_name(facts: JsonObject) -> str:
    meta = facts.get("protocol_meta", {})
    if isinstance(meta, dict):
        raw = str(meta.get("protocol_name", "")).strip()
        if raw:
            return raw
    summary = facts.get("planning_inputs", {})
    if isinstance(summary, dict):
        text = str(summary.get("protocol_summary", "")).strip()
        if text:
            return text.split()[0]
    return "protocol"


def infer_spec_version(facts: JsonObject) -> str:
    search_values: list[str] = []
    meta = facts.get("protocol_meta", {})
    if isinstance(meta, dict):
        search_values.append(str(meta.get("target_scope", "")))
    planning_inputs = facts.get("planning_inputs", {})
    if isinstance(planning_inputs, dict):
        search_values.append(str(planning_inputs.get("protocol_summary", "")))
    text = " ".join(search_values)
    match = re.search(r"\b\d+(?:\.\d+){1,3}\b", text)
    if match:
        return match.group(0)
    schema_version = str(facts.get("schema_version", "")).strip()
    return schema_version or "unknown"


def evidence_index(facts: JsonObject) -> dict[str, JsonObject]:
    index: dict[str, JsonObject] = {}
    raw = facts.get("evidence_index", [])
    if not isinstance(raw, list):
        return index
    for item in raw:
        if isinstance(item, dict) and item.get("evidence_id"):
            index[str(item["evidence_id"])] = item
    return index


def fact_id(path: str) -> str:
    return "fact:" + path.replace("[", ".").replace("]", "").replace("/", ".").strip(".")


def collect_evidence_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        raw = value.get("evidence_refs")
        if isinstance(raw, list):
            refs.extend(str(item) for item in raw if str(item).strip())
        for child in value.values():
            for ref in collect_evidence_refs(child):
                if ref not in refs:
                    refs.append(ref)
    elif isinstance(value, list):
        for child in value:
            for ref in collect_evidence_refs(child):
                if ref not in refs:
                    refs.append(ref)
    return refs


def summary_text(value: Any, limit: int = 180) -> str:
    if isinstance(value, dict):
        for key in ("summary", "value", "name", "condition", "required_action", "question"):
            raw = value.get(key)
            if isinstance(raw, (str, int, float, bool)):
                text = str(raw)
                return text[:limit]
    if isinstance(value, list):
        return f"{len(value)} items"
    text = str(value)
    return text[:limit]


def make_fact_ref(path: str, value: Any) -> JsonObject:
    return {
        "fact_id": fact_id(path),
        "path": path,
        "summary": summary_text(value),
        "evidence_refs": collect_evidence_refs(value),
    }


def collect_top_level_fact_refs(facts: JsonObject) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for key, value in facts.items():
        if key == "evidence_index":
            continue
        refs.append(make_fact_ref(key, value))
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                refs.append(make_fact_ref(f"{key}.{child_key}", child_value))
        elif isinstance(value, list):
            for idx, child_value in enumerate(value):
                refs.append(make_fact_ref(f"{key}[{idx}]", child_value))
    return refs


def select_fact_slice(facts: JsonObject, refs: list[str]) -> JsonObject:
    """Return exact fact subtrees and evidence for a bounded planning prompt."""
    slices: list[JsonObject] = []
    evidence_ids: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for raw_ref in refs:
        fact_ref = str(raw_ref).strip()
        if not fact_ref.startswith("fact:"):
            continue
        requested_path = fact_ref.removeprefix("fact:").strip(".")
        resolved_path, value = _resolve_longest_fact_path(facts, requested_path)
        if resolved_path:
            key = (fact_ref, resolved_path)
            if key not in seen:
                slices.append(
                    {
                        "fact_ref": fact_ref,
                        "resolved_path": resolved_path,
                        "value": deepcopy(value),
                    }
                )
                seen.add(key)
            evidence_ids.update(collect_evidence_refs(value))
        else:
            evidence_ids.add(requested_path)

    selected_evidence = [
        deepcopy(item)
        for item in facts.get("evidence_index", [])
        if isinstance(item, dict) and str(item.get("evidence_id", "")) in evidence_ids
    ]
    return {"fact_slices": slices, "evidence_index": selected_evidence}


def _resolve_longest_fact_path(facts: JsonObject, path: str) -> tuple[str, Any]:
    parts = [part for part in re.split(r"\.|\[|\]", path) if part]
    current: Any = facts
    resolved: list[str] = []
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            break
        resolved.append(part)
    return (".".join(resolved), current) if resolved else ("", None)


def fact_ref_ids_for_paths(paths: list[str]) -> list[str]:
    return [fact_id(path) for path in paths]


def evidence_doc_refs(facts: JsonObject, refs: list[str]) -> list[str]:
    index = evidence_index(facts)
    out: list[str] = []
    for ref in refs:
        item = index.get(ref)
        if not item:
            continue
        doc_path = str(item.get("doc_path", "")).strip()
        chunk = str(item.get("chunk_id", "")).strip()
        text = f"{ref}:{chunk}" if chunk else ref
        if doc_path:
            text = f"{text}@{doc_path}"
        if text not in out:
            out.append(text)
    return out
