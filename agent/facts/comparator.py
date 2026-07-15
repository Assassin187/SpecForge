from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    candidate = path / "protocol_facts.json" if path.is_dir() else path
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {candidate}")
    return value


def _at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _normalized_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text)


def _concept_match(concept: str, text: str) -> tuple[bool, float, list[str]]:
    phrase = re.sub(r"[^a-z0-9]+", " ", concept.casefold()).strip()
    alternatives = {phrase}
    alternatives.add(re.sub(r"\bvarint\b", "variable integer", phrase))
    alternatives.add(re.sub(r"\bqos([012])\b", r"qos \1", phrase))
    for alternative in alternatives:
        if alternative and alternative in text:
            return True, 1.0, [alternative]
    best_confidence = 0.0
    best_matched: list[str] = []
    for alternative in alternatives:
        tokens = [token for token in alternative.split() if len(token) > 2 or token.isdigit()]
        matched = [token for token in tokens if re.search(rf"\b{re.escape(token)}\b", text)]
        confidence = len(matched) / len(tokens) if tokens else 0.0
        if confidence > best_confidence:
            best_confidence = confidence
            best_matched = matched
    return best_confidence >= 0.75, best_confidence, best_matched


def _forbidden_match(concept: str, text: str) -> tuple[bool, float, list[str]]:
    phrase = re.sub(r"[^a-z0-9]+", " ", concept.casefold()).strip()
    present = bool(phrase and re.search(rf"\b{re.escape(phrase)}\b", text))
    return present, 1.0 if present else 0.0, [phrase] if present else []


def _structural_compare(candidate: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    expected_top = set(contract["top_level_keys"])
    actual_top = set(candidate)
    for key in sorted(expected_top - actual_top):
        mismatches.append({"classification": "structural_mismatch", "path": f"$.{key}", "issue": "missing_key"})
    for key in sorted(actual_top - expected_top):
        mismatches.append({"classification": "structural_mismatch", "path": f"$.{key}", "issue": "extra_key"})
    for path, expected_keys in contract["object_keys"].items():
        value = _at_path(candidate, path)
        if not isinstance(value, dict):
            mismatches.append({"classification": "structural_mismatch", "path": f"$.{path}", "issue": "expected_object"})
        elif set(value) != set(expected_keys):
            mismatches.append(
                {
                    "classification": "structural_mismatch",
                    "path": f"$.{path}",
                    "issue": "object_keys",
                    "missing": sorted(set(expected_keys) - set(value)),
                    "extra": sorted(set(value) - set(expected_keys)),
                }
            )
    for path, expected_fields in contract["critical_item_shapes"].items():
        value = _at_path(candidate, path)
        if not isinstance(value, list):
            mismatches.append({"classification": "structural_mismatch", "path": f"$.{path}", "issue": "expected_list"})
            continue
        for index, item in enumerate(value):
            if not isinstance(item, dict) or set(item) != set(expected_fields):
                mismatches.append(
                    {
                        "classification": "structural_mismatch",
                        "path": f"$.{path}[{index}]",
                        "issue": "item_shape",
                        "expected": sorted(expected_fields),
                        "actual": sorted(item) if isinstance(item, dict) else type(item).__name__,
                    }
                )
    return {"ok": not mismatches, "mismatches": mismatches}


def _semantic_compare(candidate: dict[str, Any], gold: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    candidate_text = _normalized_text(candidate)
    gold_text = _normalized_text(gold)
    minimum_names = {
        str(item.get("name", "")).casefold()
        for item in candidate.get("minimum_v1", {}).get("must_support_surface", [])
        if isinstance(item, dict)
    }
    checks: list[dict[str, Any]] = []
    for dimension, concepts in rubric["critical"].items():
        for concept in concepts:
            if dimension == "minimum_surface":
                passed = concept.casefold() in minimum_names
                confidence = 1.0 if passed else 0.0
                matched: list[str] = [concept] if passed else []
            else:
                passed, confidence, matched = _concept_match(concept, candidate_text)
            checks.append(
                {
                    "classification": "acceptable_variation" if passed else "semantic_gap",
                    "dimension": dimension,
                    "concept": concept,
                    "passed": passed,
                    "confidence": round(confidence, 3),
                    "matched_terms": matched,
                    "gold_contains_concept": _concept_match(concept, gold_text)[0],
                }
            )
    codec_checks = []
    for concept in rubric.get("required_codec_concepts", []):
        passed, confidence, matched = _concept_match(concept, candidate_text)
        codec_checks.append({"concept": concept, "passed": passed, "confidence": round(confidence, 3), "matched_terms": matched})
    minimum = candidate.get("minimum_v1", {})
    required_scope_text = _normalized_text(
        {
            "must_support_surface": minimum.get("must_support_surface"),
            "must_support_state_behaviors": minimum.get("must_support_state_behaviors"),
            "must_support_error_paths": minimum.get("must_support_error_paths"),
            "must_support_limits": minimum.get("must_support_limits"),
            "required_codec_scope": candidate.get("planning_inputs", {}).get("required_codec_scope"),
        }
    )
    forbidden = []
    for concept in rubric.get("forbidden_required_scope", []):
        present, confidence, matched = _forbidden_match(concept, required_scope_text)
        forbidden.append({"concept": concept, "present": present, "confidence": round(confidence, 3), "matched_terms": matched})
    critical_ok = all(item["passed"] for item in checks if item["dimension"] in ("target_role", "transport", "minimum_surface")) and not any(item["present"] for item in forbidden)
    return {"critical_ok": critical_ok, "checks": checks, "codec_checks": codec_checks, "forbidden_scope_checks": forbidden}


def compare_facts(candidate_path: Path, gold_path: Path, contract_path: Path, rubric_path: Path) -> dict[str, Any]:
    candidate = _load(candidate_path)
    gold = _load(gold_path)
    contract = _load(contract_path)
    rubric = _load(rubric_path)
    return {
        "schema_version": "facts_comparison/v1",
        "candidate": str(candidate_path),
        "gold": str(gold_path),
        "structural": _structural_compare(candidate, contract),
        "semantic": _semantic_compare(candidate, gold, rubric),
        "comparison_notes": [
            {"classification": "gold_only_implementation_detail", "items": rubric.get("noncritical_gold_only_details", [])},
            {"classification": "candidate_only_supported_fact", "items": []},
        ],
    }


def write_comparison_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "comparison.json"
    markdown_path = output_dir / "comparison.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    gaps = [item for item in report["semantic"]["checks"] if not item["passed"]]
    lines = [
        "# Facts Comparison Report",
        "",
        f"- Structural: {'PASS' if report['structural']['ok'] else 'FAIL'}",
        f"- Critical semantics: {'PASS' if report['semantic']['critical_ok'] else 'FAIL'}",
        f"- Structural mismatches: {len(report['structural']['mismatches'])}",
        f"- Semantic gaps: {len(gaps)}",
        "",
        "## Semantic gaps",
        "",
    ]
    lines.extend(f"- `{item['dimension']}` / `{item['concept']}` (confidence={item['confidence']})" for item in gaps)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
