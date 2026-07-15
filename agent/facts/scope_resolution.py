from __future__ import annotations

import re
from typing import Any

from .models import Chunk, TargetProfile


RESPONSE_CUES = ("acknowledg", "response", "reply", "return the result")
MANDATORY_CUES_RE = re.compile(r"\b(must|shall|required|respond|sends?|acknowledg)\b", re.IGNORECASE)
QOS_RE = re.compile(r"\bqos\s*([012])\b", re.IGNORECASE)


def _entry_text(entry: dict[str, Any], chunks_by_id: dict[str, Chunk]) -> str:
    return "\n".join(chunks_by_id[chunk_id].text for chunk_id in entry["chunk_ids"] if chunk_id in chunks_by_id)


def _relation_windows(source_text: str, target_name: str) -> list[str]:
    windows: list[str] = []
    for match in re.finditer(rf"\b{re.escape(target_name)}\b", source_text, re.IGNORECASE):
        windows.append(source_text[max(0, match.start() - 180) : match.end() + 180])
    return windows


def _is_mandatory_relation(window: str, target_name: str) -> bool:
    match = re.search(rf"\b{re.escape(target_name)}\b", window, re.IGNORECASE)
    if not match:
        return False
    local = window[max(0, match.start() - 100) : match.end() + 100]
    return bool(MANDATORY_CUES_RE.search(local))


def _allowed_by_features(windows: list[str], target_name: str, profile: TargetProfile) -> bool:
    allowed_qos = profile.data["feature_constraints"].get("delivery_qos")
    if not isinstance(allowed_qos, list):
        return True
    specific_modes: set[int] = set()
    mentioned: set[int] = set()
    for window in windows:
        target = re.search(rf"\b{re.escape(target_name)}\b", window, re.IGNORECASE)
        mentioned.update(int(value) for value in QOS_RE.findall(window))
        if target:
            preceding = window[max(0, target.start() - 48) : target.start()]
            matches = QOS_RE.findall(preceding)
            if matches:
                specific_modes.add(int(matches[-1]))
    if specific_modes:
        return bool(specific_modes & set(allowed_qos))
    return not mentioned or bool(mentioned & set(allowed_qos))


def resolve_scope(
    profile: TargetProfile,
    surface_index: list[dict[str, Any]],
    capability_resolutions: list[dict[str, Any]],
    chunks: list[Chunk],
) -> dict[str, Any]:
    entries = {entry["name"]: entry for entry in surface_index}
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    included = {
        seed
        for resolution in capability_resolutions
        for seed in resolution.get("selected_seeds", [])
        if seed in entries
    }
    edges: list[dict[str, Any]] = []
    excluded_edges: list[dict[str, Any]] = []

    changed = True
    while changed:
        changed = False
        for source_name in sorted(included):
            source = entries[source_name]
            source_text = _entry_text(source, chunks_by_id)
            for target_name, target in entries.items():
                if target_name == source_name:
                    continue
                target_summary = target["summary"].casefold()
                if not any(cue in target_summary for cue in RESPONSE_CUES):
                    continue
                windows = _relation_windows(source_text, target_name)
                mandatory_windows = [window for window in windows if _is_mandatory_relation(window, target_name)]
                if not mandatory_windows:
                    continue
                edge = {
                    "source": source_name,
                    "target": target_name,
                    "type": "mandatory_response",
                    "supporting_chunk_ids": source["chunk_ids"],
                }
                if not _allowed_by_features(mandatory_windows, target_name, profile):
                    if edge not in excluded_edges:
                        excluded_edges.append(edge | {"reason": "feature_constraint"})
                    continue
                if edge not in edges:
                    edges.append(edge)
                if target_name not in included:
                    included.add(target_name)
                    changed = True

    all_names = set(entries)
    unresolved = [
        resolution["capability_id"]
        for resolution in capability_resolutions
        if not resolution.get("selected_seeds")
    ]
    return {
        "profile_sha256": profile.sha256,
        "semantic_projection_sha256": profile.semantic_projection_sha256,
        "capability_resolutions": capability_resolutions,
        "included_surface": sorted(included),
        "excluded_surface": sorted(all_names - included),
        "dependency_edges": edges,
        "excluded_dependency_edges": excluded_edges,
        "unresolved_capabilities": unresolved,
        "closure_status": "complete" if not unresolved else "incomplete",
    }
