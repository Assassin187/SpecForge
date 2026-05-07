from __future__ import annotations

import re
from collections import defaultdict

from .models import Chunk, ChunkScore, NormalizedDoc


MAX_CHARS_PER_CHUNK = 1800
NUMBERED_HEADING_WITH_DOT_RE = re.compile(r"^(?:\d+(?:\.\d+)*)\.\s+.+$")
APPENDIX_HEADING_RE = re.compile(r"^Appendix\s+[A-Z]\.?\s+.+$", re.IGNORECASE)
TOC_ENTRY_SUFFIX_RE = re.compile(r"\s(?:\.\s*){2,}\d+\s*$")
TOC_MARKERS = {"table of contents", "contents"}
DEFINITION_CUES = (
    " is defined as ",
    " are defined as ",
    " refers to ",
    " consists of ",
    " contains ",
    " is used to ",
    " are used to ",
    " format ",
    " message type ",
    " response code ",
    " request method ",
)
NOISE_SECTION_MARKERS = (
    "references",
    "acknowledgements",
    "acknowledgments",
    "iana considerations",
    "contributors",
    "author's address",
    "authors' addresses",
    "example",
    "examples",
)

CATEGORY_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "surface_discovery": {
        "query_terms": (
            "packet",
            "message",
            "command",
            "reply",
            "response",
            "method",
            "option",
            "field",
            "header",
            "payload",
            "session",
            "state",
            "resource",
            "topic",
            "file",
            "mail",
            "timeout",
            "length",
            "limit",
        ),
        "title_terms": ("format", "message", "packet", "command", "state", "resource", "error", "operation"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "transport": {
        "query_terms": ("tcp", "udp", "socket", "connection", "port", "transport", "listen", "datagram", "channel"),
        "title_terms": ("transport", "connection", "communications model", "binding", "endpoint", "message layer"),
        "negative_title_terms": ("reference", "acknowledg", "example"),
    },
    "interaction_model": {
        "query_terms": ("request", "response", "command", "publish", "subscribe", "client", "server", "broker", "reply"),
        "title_terms": ("operations", "method", "request", "response", "client", "server", "command"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "message_model": {
        "query_terms": ("header", "packet", "message", "frame", "option", "field", "payload", "format", "encoding"),
        "title_terms": ("message format", "packet format", "format", "option", "header", "payload", "encoding"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "state_model": {
        "query_terms": ("session", "state", "transaction", "authenticated", "login", "keep alive", "lifetime", "mode"),
        "title_terms": ("state", "session", "transaction", "authentication", "lifetime"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "routing_model": {
        "query_terms": ("uri", "path", "topic", "filter", "command", "method", "dispatch", "route", "resource"),
        "title_terms": ("uri", "path", "topic", "method", "resource", "request", "command"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "resource_model": {
        "query_terms": ("resource", "file", "directory", "mail", "message store", "topic", "subscription", "object"),
        "title_terms": ("resource", "file", "directory", "mail", "topic", "store"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "error_and_limits": {
        "query_terms": ("error", "invalid", "forbidden", "denied", "limit", "maximum", "length", "unsupported", "malformed"),
        "title_terms": ("error", "response code", "status", "security", "considerations", "limits"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "minimum_v1": {
        "query_terms": ("must", "required", "support", "implementation", "minimum", "subset", "optional", "mandatory"),
        "title_terms": ("requirements", "implementation", "conformance", "summary"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "message_entry_details": {
        "query_terms": ("message", "command", "method", "response", "field", "payload", "header", "reply", "syntax"),
        "title_terms": ("message", "command", "method", "response", "payload", "header", "format"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "state_transitions": {
        "query_terms": ("state", "transition", "timer", "timeout", "session", "transaction", "retry", "reconnect", "cleanup"),
        "title_terms": ("state", "session", "transaction", "retry", "timeout", "lifecycle"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "resource_lifecycle": {
        "query_terms": ("resource", "store", "retained", "persist", "delete", "remove", "create", "subscription", "authority"),
        "title_terms": ("resource", "storage", "lifecycle", "state", "operations"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "limits_and_security": {
        "query_terms": ("must", "length", "maximum", "minimum", "default", "recommended", "security", "tls", "dtls", "timeout"),
        "title_terms": ("security", "considerations", "limits", "encoding", "timeout"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
    "minimum_boundary": {
        "query_terms": ("must", "minimum", "required", "support", "implementation", "conformance", "obligation"),
        "title_terms": ("requirements", "conformance", "minimum", "implementation"),
        "negative_title_terms": ("reference", "acknowledg"),
    },
}


def _canonical_heading_line(line: str) -> str | None:
    raw = line.strip()
    if not raw:
        return None

    cleaned = TOC_ENTRY_SUFFIX_RE.sub("", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if NUMBERED_HEADING_WITH_DOT_RE.match(cleaned):
        return cleaned
    if APPENDIX_HEADING_RE.match(cleaned):
        return cleaned
    return None


def _is_body_heading_line(line: str) -> bool:
    return _canonical_heading_line(line) is not None and TOC_ENTRY_SUFFIX_RE.search(line.strip()) is None


def _extract_toc_headings(lines: list[str]) -> tuple[int | None, list[str]]:
    toc_start: int | None = None
    for idx, raw_line in enumerate(lines):
        if raw_line.strip().lower() in TOC_MARKERS:
            toc_start = idx
            break
    if toc_start is None:
        return None, []

    headings: list[str] = []
    seen: set[str] = set()
    for raw_line in lines[toc_start + 1 :]:
        stripped = raw_line.strip()
        if not stripped:
            continue

        heading = _canonical_heading_line(stripped)
        if heading and TOC_ENTRY_SUFFIX_RE.search(stripped):
            if heading not in seen:
                headings.append(heading)
                seen.add(heading)
            continue

        if headings and _is_body_heading_line(stripped):
            break

    return toc_start, headings


def _split_sections_by_headings(lines: list[str], heading_points: list[tuple[int, str]]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if not heading_points:
        return sections

    for idx, (start_line, title) in enumerate(heading_points):
        end_line = heading_points[idx + 1][0] if idx + 1 < len(heading_points) else len(lines)
        body_lines = lines[start_line + 1 : end_line]
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((title, body))
    return sections


def _split_sections_by_toc(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    toc_start, toc_headings = _extract_toc_headings(lines)
    if toc_start is None or not toc_headings:
        return []

    heading_points: list[tuple[int, str]] = []
    expected_idx = 0
    for line_idx in range(toc_start + 1, len(lines)):
        raw_line = lines[line_idx]
        stripped = raw_line.strip()
        if not stripped or raw_line != raw_line.lstrip() or not _is_body_heading_line(stripped):
            continue
        heading = _canonical_heading_line(stripped)
        if heading == toc_headings[expected_idx]:
            heading_points.append((line_idx, heading))
            expected_idx += 1
            if expected_idx >= len(toc_headings):
                break

    return _split_sections_by_headings(lines, heading_points)


def _split_sections_by_numbered_headings(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "intro"
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_title, body))

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            current_lines.append("")
            continue
        is_heading = raw_line == raw_line.lstrip() and (
            bool(NUMBERED_HEADING_WITH_DOT_RE.match(line)) or bool(APPENDIX_HEADING_RE.match(line))
        )
        if is_heading and current_lines:
            flush()
            current_lines = []
            current_title = line
            continue
        current_lines.append(line)

    flush()
    return sections or [("intro", text)]


def _split_sections(text: str) -> list[tuple[str, str]]:
    toc_sections = _split_sections_by_toc(text)
    if toc_sections:
        return toc_sections
    return _split_sections_by_numbered_headings(text)


def _keywords_for_text(text: str) -> list[str]:
    lower = text.lower()
    found = set()
    for profile in CATEGORY_PROFILES.values():
        for word in profile["query_terms"]:
            if word in lower:
                found.add(word)
    return sorted(found)


def build_chunks(docs: list[NormalizedDoc]) -> list[Chunk]:
    chunks: list[Chunk] = []

    for doc in docs:
        sections = _split_sections(doc.normalized_text)
        for section_idx, (section_title, section_body) in enumerate(sections, start=1):
            paragraphs = [p.strip() for p in section_body.split("\n\n") if p.strip()]
            buf: list[str] = []
            buf_len = 0
            part_idx = 1
            section_id = f"{doc.path.stem}-s{section_idx}"
            for paragraph in paragraphs:
                addition = len(paragraph) + (2 if buf else 0)
                if buf and buf_len + addition > MAX_CHARS_PER_CHUNK:
                    text = "\n\n".join(buf)
                    chunk_id = f"{section_id}-p{part_idx}"
                    chunks.append(
                        Chunk(
                            chunk_id=chunk_id,
                            doc_path=str(doc.path),
                            section_hint=section_title,
                            section_id=section_id,
                            section_order_in_doc=section_idx,
                            chunk_order_in_section=part_idx,
                            text=text,
                            keywords=_keywords_for_text(text),
                        )
                    )
                    part_idx += 1
                    buf = []
                    buf_len = 0
                buf.append(paragraph)
                buf_len += addition
            if buf:
                text = "\n\n".join(buf)
                chunk_id = f"{section_id}-p{part_idx}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        doc_path=str(doc.path),
                        section_hint=section_title,
                        section_id=section_id,
                        section_order_in_doc=section_idx,
                        chunk_order_in_section=part_idx,
                        text=text,
                        keywords=_keywords_for_text(text),
                    )
                )
    return chunks


def _count_occurrences(text: str, term: str) -> int:
    if " " in term:
        return text.count(term)
    return len(re.findall(rf"\b{re.escape(term)}\b", text))


def _score_title(title: str, title_terms: tuple[str, ...], negative_title_terms: tuple[str, ...]) -> tuple[float, float, float]:
    positive_hits = sum(1 for term in title_terms if term in title)
    negative_hits = sum(1 for term in negative_title_terms if term in title)
    return min(float(positive_hits) * 3.0, 9.0), min(float(negative_hits) * 2.0, 6.0), 0.0


def _definition_cue_score(text: str) -> float:
    hits = sum(1 for cue in DEFINITION_CUES if cue in text)
    return min(float(hits), 4.0)


def _noise_penalty(title: str) -> float:
    lowered = title.lower()
    if any(marker in lowered for marker in NOISE_SECTION_MARKERS):
        return 5.0
    return 0.0


def _appendix_penalty(title: str) -> float:
    return 1.5 if title.lower().startswith("appendix ") else 0.0


def _score_chunk(chunk: Chunk, category: str) -> ChunkScore:
    profile = CATEGORY_PROFILES.get(category, {})
    query_terms = profile.get("query_terms", ())
    title_terms = profile.get("title_terms", ())
    negative_title_terms = profile.get("negative_title_terms", ())

    lower_text = f" {chunk.text.lower()} "
    lower_title = chunk.section_hint.lower()

    title_score, negative_title_penalty, _unused = _score_title(lower_title, title_terms, negative_title_terms)

    matched_terms = [term for term in query_terms if term in lower_text]
    term_coverage_score = min(float(len(set(matched_terms))) * 1.5, 12.0)

    raw_frequency = sum(min(_count_occurrences(lower_text, term), 3) for term in query_terms)
    term_frequency_score = min(float(raw_frequency) * 0.5, 8.0)

    density_base = raw_frequency / max(len(chunk.text) / 250.0, 1.0)
    density_score = min(density_base, 4.0)

    definition_score = _definition_cue_score(lower_text)
    noise_penalty = _noise_penalty(lower_title)
    appendix_penalty = _appendix_penalty(lower_title)

    breakdown = {
        "section_title_score": round(title_score, 3),
        "term_coverage_score": round(term_coverage_score, 3),
        "term_frequency_score": round(term_frequency_score, 3),
        "density_score": round(density_score, 3),
        "definition_cue_score": round(definition_score, 3),
        "noise_penalty": round(-noise_penalty, 3),
        "negative_title_penalty": round(-negative_title_penalty, 3),
        "appendix_penalty": round(-appendix_penalty, 3),
    }
    base_score = round(sum(breakdown.values()), 3)
    return ChunkScore(chunk=chunk, base_score=base_score, score_breakdown=breakdown)


def retrieve_candidate_chunks_for_category(
    chunks: list[Chunk],
    category: str,
    candidate_limit: int = 40,
    per_section_limit: int = 8,
) -> list[ChunkScore]:
    scored = [_score_chunk(chunk, category) for chunk in chunks]
    positive_scored = [item for item in scored if item.base_score > 0]
    ranked = sorted(
        positive_scored,
        key=lambda item: (
            item.base_score,
            item.score_breakdown.get("section_title_score", 0.0),
            item.score_breakdown.get("term_coverage_score", 0.0),
            -item.chunk.section_order_in_doc,
            -item.chunk.chunk_order_in_section,
        ),
        reverse=True,
    )

    selected: list[ChunkScore] = []
    per_section_counts: dict[str, int] = defaultdict(int)
    source = ranked if ranked else sorted(
        scored,
        key=lambda item: (
            item.base_score,
            item.score_breakdown.get("section_title_score", 0.0),
            -item.chunk.section_order_in_doc,
            -item.chunk.chunk_order_in_section,
        ),
        reverse=True,
    )
    for item in source:
        if per_section_counts[item.chunk.section_id] >= per_section_limit:
            continue
        selected.append(item)
        per_section_counts[item.chunk.section_id] += 1
        if len(selected) >= candidate_limit:
            break

    return selected
