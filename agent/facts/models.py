from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FactDiagnostic:
    level: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class DocumentInput:
    path: Path


@dataclass(frozen=True)
class NormalizedDoc:
    path: Path
    title: str
    raw_text: str
    normalized_text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_path: str
    section_hint: str
    section_id: str
    section_order_in_doc: int
    chunk_order_in_section: int
    text: str
    keywords: list[str]


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    doc_path: str
    section_hint: str
    excerpt: str


@dataclass
class ValidationContext:
    protocol_name: str
    docs: list[NormalizedDoc]
    chunks: list[Chunk]
    diagnostics: list[FactDiagnostic] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(diag.level == "error" for diag in self.diagnostics)


@dataclass
class ExtractionResult:
    success: bool
    output_dir: Path
    facts_path: Path
    manifest_path: Path
    diagnostics: list[FactDiagnostic]
    category_outputs: dict[str, dict[str, Any]]


@dataclass
class VerificationResult:
    ok: bool
    diagnostics: list[FactDiagnostic]


@dataclass(frozen=True)
class ChunkScore:
    chunk: Chunk
    base_score: float
    score_breakdown: dict[str, float]


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: str
    section_hint: str
    base_score: float
    score_breakdown: dict[str, float]
    excerpt: str


@dataclass(frozen=True)
class RerankResult:
    category: str
    ranked_chunk_ids: list[str]
    high_priority_chunk_ids: list[str]
    notes: list[str]
    open_questions: list[str]
