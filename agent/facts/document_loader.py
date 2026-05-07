from __future__ import annotations

import re
from pathlib import Path

from .models import FactDiagnostic, NormalizedDoc


SUPPORTED_DOC_EXTENSIONS = {".txt"}


def normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_documents(paths: list[str | Path]) -> tuple[list[NormalizedDoc], list[FactDiagnostic]]:
    docs: list[NormalizedDoc] = []
    diagnostics: list[FactDiagnostic] = []

    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            diagnostics.append(FactDiagnostic("error", "missing_doc", f"Document not found: {path}", str(path)))
            continue
        if path.suffix.lower() not in SUPPORTED_DOC_EXTENSIONS:
            diagnostics.append(
                FactDiagnostic(
                    "error",
                    "unsupported_doc_type",
                    f"Unsupported document type: {path.suffix}",
                    str(path),
                )
            )
            continue

        try:
            raw_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            diagnostics.append(FactDiagnostic("error", "decode_failed", "Expected UTF-8 document", str(path)))
            continue

        docs.append(
            NormalizedDoc(
                path=path,
                title=path.name,
                raw_text=raw_text,
                normalized_text=normalize_text(raw_text),
            )
        )

    return docs, diagnostics
