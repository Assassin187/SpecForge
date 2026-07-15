from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .models import Chunk, TargetProfile


SURFACE_HEADING_RE = re.compile(
    r"^(?P<section>\d+(?:\.\d+)+)\s+(?P<name>[A-Z][A-Z0-9_-]{2,})(?:\s+[-–—:]\s*|\s+)(?P<summary>.+)$"
)
TOKEN_RE = re.compile(r"[a-z0-9]+")
IGNORED_QUERY_TOKENS = {
    "a", "an", "and", "at", "client", "for", "from", "handle", "minimum", "of", "or",
    "protocol", "server", "support", "the", "to", "with",
}


def build_surface_index(chunks: list[Chunk]) -> list[dict[str, Any]]:
    by_heading: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        by_heading[chunk.section_hint].append(chunk)

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for heading, section_chunks in by_heading.items():
        match = SURFACE_HEADING_RE.match(heading)
        if not match:
            continue
        section = match.group("section")
        name = match.group("name")
        key = (section, name)
        if key in seen:
            continue
        seen.add(key)
        descendant_chunks = [
            chunk
            for chunk in chunks
            if chunk.section_hint == heading
            or chunk.section_hint.startswith(f"{section}.")
        ]
        entries.append(
            {
                "name": name,
                "kind": "surface_unit",
                "section_anchor": section,
                "section_hint": heading,
                "summary": match.group("summary").strip(),
                "chunk_ids": [chunk.chunk_id for chunk in descendant_chunks],
                "doc_path": section_chunks[0].doc_path,
            }
        )
    return sorted(entries, key=lambda item: [int(part) for part in item["section_anchor"].split(".")])


def _tokens(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.casefold()) if token not in IGNORED_QUERY_TOKENS and len(token) > 2}


def resolve_capabilities(
    profile: TargetProfile,
    surface_index: list[dict[str, Any]],
    chunks: list[Chunk],
) -> list[dict[str, Any]]:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    resolutions: list[dict[str, Any]] = []
    for capability in profile.data["required_capabilities"]:
        query_tokens = _tokens(f"{capability['capability_id']} {capability['summary']}")
        ranked: list[tuple[float, dict[str, Any], list[str]]] = []
        for entry in surface_index:
            heading_tokens = _tokens(f"{entry['name']} {entry['summary']}")
            body = " ".join(chunks_by_id[item].text for item in entry["chunk_ids"] if item in chunks_by_id)
            body_tokens = _tokens(body)
            matched = sorted(query_tokens & (heading_tokens | body_tokens))
            heading_matches = len(query_tokens & heading_tokens)
            score = heading_matches * 3.0 + len(matched) + (2.0 if entry["name"].casefold() in capability["capability_id"].casefold() else 0.0)
            if score > 0:
                ranked.append((score, entry, matched))
        ranked.sort(key=lambda item: (-item[0], item[1]["section_anchor"]))
        if ranked:
            best_score = ranked[0][0]
            candidates = [
                {
                    "surface": entry["name"],
                    "section_anchor": entry["section_anchor"],
                    "chunk_ids": entry["chunk_ids"],
                    "score": score,
                    "matched_terms": matched,
                }
                for score, entry, matched in ranked
                if score >= max(2.0, best_score * 0.6)
            ][:5]
        else:
            candidates = []
        direct = [
            candidate
            for candidate in candidates
            if any(
                len(token) >= 5 and (token.startswith(candidate["surface"].casefold()) or candidate["surface"].casefold().startswith(token))
                for token in query_tokens
            )
        ]
        if direct:
            selected_seeds = [candidate["surface"] for candidate in direct]
        else:
            request_candidates = [
                candidate
                for candidate in candidates
                if "request" in next(
                    (entry["summary"].casefold() for entry in surface_index if entry["name"] == candidate["surface"]),
                    "",
                )
            ]
            selected_seeds = [request_candidates[0]["surface"]] if request_candidates else ([candidates[0]["surface"]] if candidates else [])
        resolutions.append(
            {
                "capability_id": capability["capability_id"],
                "candidates": candidates,
                "selected_seeds": selected_seeds,
                "status": "candidate" if candidates else "unresolved",
                "unresolved_question": None if candidates else "No document-backed surface candidate matched this capability.",
            }
        )
    return resolutions
