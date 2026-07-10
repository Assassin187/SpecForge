from __future__ import annotations

import hashlib
import json
import re
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
