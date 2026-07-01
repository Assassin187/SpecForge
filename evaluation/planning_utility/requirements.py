from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MINIMUM_KEYS = (
    "must_support_surface",
    "must_support_state_behaviors",
    "must_support_error_paths",
    "must_support_limits",
    "may_defer_features",
    "implementation_assumptions",
)

LOCAL_PATH_MARKERS = (
    "/home/",
    "SpecForge/specs-example",
    "SpecForge/protocol-example",
    "specs-example/",
    "protocol-example/",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _looks_like_local_path(value: str) -> bool:
    return any(marker in value for marker in LOCAL_PATH_MARKERS)


def sanitize_facts_for_baseline(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"source_documents", "doc_path", "document_path", "file_path", "source_path"}:
                continue
            if isinstance(item, str) and _looks_like_local_path(item):
                continue
            sanitized[str(key)] = sanitize_facts_for_baseline(item)
        return sanitized
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and _looks_like_local_path(item):
                continue
            out.append(sanitize_facts_for_baseline(item))
        return out
    return value


def extract_minimum_requirements(facts: dict[str, Any]) -> dict[str, Any]:
    minimum = facts.get("minimum_v1", {})
    if not isinstance(minimum, dict):
        minimum = {}
    requirements = {key: minimum.get(key, []) for key in MINIMUM_KEYS}
    requirements["source_section"] = "minimum_v1"
    requirements["surface_count"] = len(requirements["must_support_surface"]) if isinstance(requirements["must_support_surface"], list) else 0
    return sanitize_facts_for_baseline(requirements)


def build_allowed_inputs(
    facts_path: Path,
    target_profile_path: Path,
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    facts = read_json(facts_path)
    target_profile = read_json(target_profile_path)
    return {
        "input_hashes": {
            "protocol_facts_sha256": file_sha256(facts_path),
            "target_profile_sha256": file_sha256(target_profile_path),
        },
        "facts_view": sanitize_facts_for_baseline(facts),
        "target_profile": target_profile,
        "minimum_requirements": extract_minimum_requirements(facts),
        "runtime_contract": runtime_contract,
    }

