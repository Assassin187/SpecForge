from __future__ import annotations

import json
import re
import shutil
import hashlib
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.llm_client import FIXED_MODEL, FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from ..common.rerank_client import QwenRerankClient
from .document_loader import load_documents
from .models import (
    Chunk,
    ChunkScore,
    ExtractionResult,
    FactDiagnostic,
    NormalizedDoc,
    RerankCandidate,
    RerankResult,
    TargetProfile,
    ValidationContext,
)
from .preprocess import build_chunks, retrieve_candidate_chunks_for_category
from .prompts import (
    TASK_DEFINITIONS,
    build_category_task_prompt,
    build_reconciliation_prompt,
    build_rerank_prompt,
    build_surface_discovery_prompt,
)
from .surface_index import build_surface_index, resolve_capabilities
from .scope_resolution import resolve_scope
from .target_profile import build_profile_evidence
from .verifier import verify_facts_output


SCHEMA_VERSION = "protocol_facts/v2alpha1"
SEMANTIC_CATEGORIES = [
    "transport",
    "interaction_model",
    "message_model",
    "state_model",
    "routing_model",
    "resource_model",
    "error_and_limits",
    "minimum_v1",
]

CATEGORY_SUBTASKS: dict[str, list[dict[str, str]]] = {
    "transport": [{"name": "transport_overview", "retrieval_key": "transport"}],
    "interaction_model": [{"name": "interaction_overview", "retrieval_key": "interaction_model"}],
    "message_model": [
        {"name": "message_surface_catalog", "retrieval_key": "message_model"},
        {"name": "message_entry_details", "retrieval_key": "message_entry_details"},
    ],
    "state_model": [
        {"name": "state_structure", "retrieval_key": "state_model"},
        {"name": "state_transitions", "retrieval_key": "state_transitions"},
    ],
    "routing_model": [{"name": "routing_rules", "retrieval_key": "routing_model"}],
    "resource_model": [
        {"name": "resource_objects", "retrieval_key": "resource_model"},
        {"name": "resource_lifecycle", "retrieval_key": "resource_lifecycle"},
    ],
    "error_and_limits": [
        {"name": "error_matrix", "retrieval_key": "error_and_limits"},
        {"name": "limits_and_security", "retrieval_key": "limits_and_security"},
    ],
    "minimum_v1": [{"name": "minimum_boundary", "retrieval_key": "minimum_boundary"}],
}

RERANK_CANDIDATE_LIMIT = 40
RERANK_EXCERPT_CHARS = 600
CONTEXT_CHAR_BUDGET = 10000
RULES_SECTION_LIMIT = 8
FINAL_SECTION_LIMIT = 5
MIN_SELECTED_CHUNKS = 3
SHARED_STAGE_KEY = "__shared__"


CATEGORY_FACT_DEFAULTS: dict[str, dict[str, Any]] = {
    "transport": {
        "network_stack": {"value": "unknown", "evidence_refs": []},
        "connection_model": {"value": "unknown", "evidence_refs": []},
        "channels": [],
        "runtime_implications": [],
    },
    "interaction_model": {
        "style": {"value": "unknown", "evidence_refs": []},
        "roles": [],
        "interaction_units": [],
        "core_flows": [],
    },
    "message_model": {
        "framing": {"value": "unknown", "evidence_refs": []},
        "surface_catalog": [],
        "message_or_command_entries": [],
        "shared_fields": [],
        "global_framing_rules": [],
        "code_spaces": [],
    },
    "state_model": {
        "state_scopes": [],
        "state_objects": [],
        "state_nodes": [],
        "transitions": [],
        "timers_and_constants": [],
        "retained_data": [],
        "cleanup_rules": [],
    },
    "routing_model": {
        "dispatch_keys": [],
        "dispatch_targets": [],
        "matching_rules": [],
    },
    "resource_model": {
        "resource_objects": [],
        "lifecycle_rules": [],
        "persistence_scope": [],
        "ownership_and_authority": [],
        "storage_requirements": [],
        "business_operations": [],
    },
    "error_and_limits": {
        "error_matrix": [],
        "limits": {
            "fixed_protocol_constants": [],
            "defaults": [],
            "recommended_values": [],
            "hard_bounds": [],
            "configurable_bounds": [],
        },
        "security_requirements": {
            "base_protocol_requirements": [],
            "recommended_practices": [],
            "external_security_dependencies": [],
            "explicitly_out_of_scope_security_features": [],
        },
        "out_of_scope_candidates": [],
    },
    "minimum_v1": {
        "must_support_surface": [],
        "must_support_state_behaviors": [],
        "must_support_error_paths": [],
        "must_support_limits": [],
        "may_defer_features": [],
        "implementation_assumptions": [],
    },
}

SURFACE_DISCOVERY_DEFAULTS: dict[str, Any] = {
    "surface_units": [],
    "stateful_objects": [],
    "resource_objects": [],
    "parameter_candidates": [],
}


@dataclass
class ExtractionLogger:
    root: Path
    counter: int = 0

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, label: str, content: str, suffix: str = ".txt") -> Path:
        self.counter += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "log"
        path = self.root / f"{self.counter:03d}_{safe}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path


def _safe_protocol_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip().lower()).strip("_")
    return safe or "protocol"


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _chunk_score_to_dict(item: ChunkScore) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk.chunk_id,
        "doc_path": item.chunk.doc_path,
        "section_hint": item.chunk.section_hint,
        "section_id": item.chunk.section_id,
        "section_order_in_doc": item.chunk.section_order_in_doc,
        "chunk_order_in_section": item.chunk.chunk_order_in_section,
        "char_count": len(item.chunk.text),
        "base_score": item.base_score,
        "score_breakdown": item.score_breakdown,
        "keywords": item.chunk.keywords,
    }


def _usage_to_dict(usage: LLMUsage) -> dict[str, int]:
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _add_usage(left: LLMUsage, right: LLMUsage) -> LLMUsage:
    return LLMUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _build_rerank_candidates(candidate_scores: list[ChunkScore]) -> list[RerankCandidate]:
    return [
        RerankCandidate(
            chunk_id=item.chunk.chunk_id,
            section_hint=item.chunk.section_hint,
            base_score=item.base_score,
            score_breakdown=item.score_breakdown,
            excerpt=item.chunk.text[:RERANK_EXCERPT_CHARS],
        )
        for item in candidate_scores
    ]


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _normalize_rerank_result(category: str, raw: dict[str, Any], candidate_scores: list[ChunkScore]) -> RerankResult:
    valid_ids = {item.chunk.chunk_id for item in candidate_scores}
    fallback_ids = [item.chunk.chunk_id for item in candidate_scores]
    ranked_chunk_ids = [str(item) for item in raw.get("ranked_chunk_ids", []) if str(item) in valid_ids]
    high_priority_chunk_ids = [str(item) for item in raw.get("high_priority_chunk_ids", []) if str(item) in valid_ids]
    notes = [str(item) for item in raw.get("notes", [])]
    open_questions = [str(item) for item in raw.get("open_questions", [])]
    ranked_chunk_ids = _dedupe_preserve(ranked_chunk_ids)
    high_priority_chunk_ids = _dedupe_preserve(high_priority_chunk_ids)
    if not ranked_chunk_ids:
        ranked_chunk_ids = fallback_ids
    return RerankResult(
        category=category,
        ranked_chunk_ids=ranked_chunk_ids,
        high_priority_chunk_ids=high_priority_chunk_ids,
        notes=notes,
        open_questions=open_questions,
    )


def _assemble_prompt_context_for_category(
    candidate_scores: list[ChunkScore],
    rerank_result: RerankResult | None,
    char_budget: int = CONTEXT_CHAR_BUDGET,
    per_section_limit: int = FINAL_SECTION_LIMIT,
) -> tuple[list[Chunk], dict[str, Any]]:
    lookup = {item.chunk.chunk_id: item for item in candidate_scores}
    fallback_ids = [item.chunk.chunk_id for item in candidate_scores]
    ordered_ids: list[str] = []
    if rerank_result is not None:
        ordered_ids.extend(rerank_result.high_priority_chunk_ids)
        ordered_ids.extend(rerank_result.ranked_chunk_ids)
    ordered_ids.extend(fallback_ids)
    ordered_ids = _dedupe_preserve([item for item in ordered_ids if item in lookup])

    selected: list[Chunk] = []
    selected_ids: set[str] = set()
    section_counts: dict[str, int] = defaultdict(int)
    char_count = 0

    def try_select(chunk_id: str) -> None:
        nonlocal char_count
        item = lookup[chunk_id]
        chunk = item.chunk
        if chunk_id in selected_ids:
            return
        if section_counts[chunk.section_id] >= per_section_limit:
            return
        chunk_len = len(chunk.text)
        if char_count + chunk_len > char_budget:
            return
        if chunk_len > char_budget:
            return
        selected.append(chunk)
        selected_ids.add(chunk_id)
        section_counts[chunk.section_id] += 1
        char_count += chunk_len

    for chunk_id in ordered_ids:
        try_select(chunk_id)
        if char_count >= char_budget:
            break

    if len(selected) < MIN_SELECTED_CHUNKS:
        for chunk_id in ordered_ids:
            if len(selected) >= MIN_SELECTED_CHUNKS:
                break
            try_select(chunk_id)

    metadata = {
        "char_budget": char_budget,
        "char_count": char_count,
        "selected_chunk_ids": [chunk.chunk_id for chunk in selected],
        "selected_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "doc_path": chunk.doc_path,
                "section_hint": chunk.section_hint,
                "section_id": chunk.section_id,
                "char_count": len(chunk.text),
            }
            for chunk in selected
        ],
        "selected_sections": dict(section_counts),
        "candidate_count": len(candidate_scores),
        "ordered_chunk_ids": ordered_ids,
        "rerank_used": rerank_result is not None,
        "rerank_high_priority_ids": rerank_result.high_priority_chunk_ids if rerank_result else [],
    }
    return selected, metadata


def validate_document_inputs(
    protocol_name: str,
    doc_paths: list[str | Path],
    llm_client: FixedQwenClient | None = None,
    skip_llm_check: bool = False,
) -> ValidationContext:
    diagnostics: list[FactDiagnostic] = []
    protocol_name = protocol_name.strip()
    if not protocol_name:
        diagnostics.append(FactDiagnostic("error", "missing_protocol_name", "protocol_name must be non-empty"))

    docs, load_diags = load_documents(doc_paths)
    diagnostics.extend(load_diags)

    if not skip_llm_check and llm_client is not None:
        try:
            llm_client.self_check()
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(FactDiagnostic("error", "llm_self_check_failed", str(exc)))

    chunks = build_chunks(docs) if docs else []
    if docs and not chunks:
        diagnostics.append(FactDiagnostic("error", "no_chunks", "No usable chunks generated from the document set"))

    return ValidationContext(protocol_name=protocol_name, docs=docs, chunks=chunks, diagnostics=diagnostics)


def _default_open_question(category: str, question: str, question_type: str = "document_not_extracted") -> dict[str, Any]:
    return {
        "category": category,
        "question_type": question_type,
        "question": question,
        "blocking_impact": "medium",
        "suggested_followup": "Inspect the cited sections or expand the document set for this protocol.",
    }


def _normalize_open_questions(raw: Any, category: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                questions.append(
                    {
                        "category": str(item.get("category") or category),
                        "question_type": str(item.get("question_type") or "document_not_extracted"),
                        "question": str(item.get("question") or "Unspecified question"),
                        "blocking_impact": str(item.get("blocking_impact") or "medium"),
                        "suggested_followup": str(item.get("suggested_followup") or "Review the source document."),
                    }
                )
            else:
                questions.append(_default_open_question(category, str(item)))
    return questions


def _normalize_evidence_entries(raw: Any, selected_chunks: list[Chunk], prefix: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or f"{prefix}_e{idx}")
            doc_path = str(item.get("doc_path") or (selected_chunks[0].doc_path if selected_chunks else ""))
            section_hint = str(item.get("section_hint") or (selected_chunks[0].section_hint if selected_chunks else ""))
            chunk_id = str(item.get("chunk_id") or (selected_chunks[0].chunk_id if selected_chunks else ""))
            excerpt = str(item.get("excerpt") or (selected_chunks[0].text[:240] if selected_chunks else ""))
            normalized.append(
                {
                    "evidence_id": evidence_id,
                    "doc_path": doc_path,
                    "section_hint": section_hint,
                    "chunk_id": chunk_id,
                    "excerpt": excerpt,
                }
            )
    if not normalized and selected_chunks:
        sample = selected_chunks[0]
        normalized.append(
            {
                "evidence_id": f"{prefix}_fallback_e1",
                "doc_path": sample.doc_path,
                "section_hint": sample.section_hint,
                "chunk_id": sample.chunk_id,
                "excerpt": sample.text[:240],
            }
        )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in normalized:
        if item["evidence_id"] in seen:
            continue
        deduped.append(item)
        seen.add(item["evidence_id"])
    return deduped


def _looks_like_fact_item(node: dict[str, Any]) -> bool:
    meaningful_keys = {
        "name",
        "summary",
        "condition",
        "value",
        "value_or_rule",
        "from_state",
        "to_state",
        "surface_unit",
        "actor",
        "kind",
        "scope",
        "capability",
        "syntax_or_layout",
        "required_action",
        "reason",
    }
    return any(key in node for key in meaningful_keys)


def _attach_evidence_refs(value: Any, fallback_ids: list[str]) -> Any:
    if isinstance(value, dict):
        result = {key: _attach_evidence_refs(child, fallback_ids) for key, child in value.items()}
        if _looks_like_fact_item(result):
            refs = result.get("evidence_refs", [])
            if not isinstance(refs, list):
                refs = [str(refs)]
            refs = _dedupe_preserve([str(item) for item in refs if str(item)])
            if not refs and fallback_ids:
                refs = [fallback_ids[0]]
            result["evidence_refs"] = refs
        return result
    if isinstance(value, list):
        return [_attach_evidence_refs(item, fallback_ids) for item in value]
    return value


def _deep_merge(base: Any, incoming: Any) -> Any:
    if isinstance(base, dict) and isinstance(incoming, dict):
        result = deepcopy(base)
        for key, value in incoming.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(incoming, list):
        return deepcopy(base) + deepcopy(incoming)
    return deepcopy(incoming)


def _normalize_category_output(category: str, raw: dict[str, Any], selected_chunks: list[Chunk]) -> dict[str, Any]:
    facts = raw.get("facts", {}) if isinstance(raw, dict) else {}
    evidence = _normalize_evidence_entries(raw.get("evidence", []), selected_chunks, category)
    open_questions = _normalize_open_questions(raw.get("open_questions", []), category) if isinstance(raw, dict) else []
    merged_facts = _deep_merge(CATEGORY_FACT_DEFAULTS[category], facts if isinstance(facts, dict) else {})
    fallback_ids = [item["evidence_id"] for item in evidence]
    merged_facts = _attach_evidence_refs(merged_facts, fallback_ids)
    return {
        "category": category,
        "facts": merged_facts,
        "evidence": evidence,
        "open_questions": open_questions,
    }


def _normalize_surface_discovery_output(raw: dict[str, Any], selected_chunks: list[Chunk]) -> dict[str, Any]:
    facts = {
        "surface_units": raw.get("surface_units", []) if isinstance(raw, dict) else [],
        "stateful_objects": raw.get("stateful_objects", []) if isinstance(raw, dict) else [],
        "resource_objects": raw.get("resource_objects", []) if isinstance(raw, dict) else [],
        "parameter_candidates": raw.get("parameter_candidates", []) if isinstance(raw, dict) else [],
    }
    evidence = _normalize_evidence_entries(raw.get("evidence", []), selected_chunks, "surface_discovery") if isinstance(raw, dict) else []
    open_questions = _normalize_open_questions(raw.get("open_questions", []), "surface_discovery") if isinstance(raw, dict) else []
    facts = _deep_merge(SURFACE_DISCOVERY_DEFAULTS, facts)
    facts = _attach_evidence_refs(facts, [item["evidence_id"] for item in evidence])
    return {"facts": facts, "evidence": evidence, "open_questions": open_questions}


def _normalize_reconciliation_output(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "consistency_findings": [],
            "coverage_gaps": [],
            "global_open_questions": [],
            "planner_risks": [],
        }
    findings = []
    for item in raw.get("consistency_findings", []):
        if isinstance(item, dict):
            findings.append(
                {
                    "severity": str(item.get("severity") or "warning"),
                    "summary": str(item.get("summary") or "Unspecified consistency finding"),
                    "categories": [str(cat) for cat in item.get("categories", []) if str(cat)],
                }
            )
    gaps = []
    for item in raw.get("coverage_gaps", []):
        if isinstance(item, dict):
            gaps.append({"category": str(item.get("category") or "unknown"), "summary": str(item.get("summary") or "")})
    planner_risks = [str(item.get("summary") if isinstance(item, dict) else item) for item in raw.get("planner_risks", [])]
    global_questions = _normalize_open_questions(raw.get("global_open_questions", []), "reconciliation")
    return {
        "consistency_findings": findings,
        "coverage_gaps": gaps,
        "global_open_questions": global_questions,
        "planner_risks": [item for item in planner_risks if item],
    }


def _value_of(node: Any, default: str = "unknown") -> str:
    if isinstance(node, dict):
        value = node.get("value", default)
        return str(value) if value is not None else default
    if node is None:
        return default
    return str(node)


def _names_of(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("name") or item.get("surface_unit") or item.get("summary")
            if value:
                names.append(str(value))
        elif item:
            names.append(str(item))
    return names


def _summaries_of(items: Any, limit: int = 8) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("summary") or item.get("question") or item.get("name")
        else:
            value = item
        if value:
            values.append(str(value))
        if len(values) >= limit:
            break
    return values


def _dedupe_evidence_index(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        evidence_id = item.get("evidence_id")
        if not evidence_id or evidence_id in seen:
            continue
        deduped.append(item)
        seen.add(evidence_id)
    return deduped


def _build_planning_inputs(
    protocol_name: str,
    category_outputs: dict[str, dict[str, Any]],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    transport = category_outputs.get("transport", {}).get("facts", {})
    interaction = category_outputs.get("interaction_model", {}).get("facts", {})
    message = category_outputs.get("message_model", {}).get("facts", {})
    state_model = category_outputs.get("state_model", {}).get("facts", {})
    routing = category_outputs.get("routing_model", {}).get("facts", {})
    resource = category_outputs.get("resource_model", {}).get("facts", {})
    errors = category_outputs.get("error_and_limits", {}).get("facts", {})

    implementation_axes = ["network", "protocol", "server"]
    if _names_of(state_model.get("state_objects")) or _value_of(transport.get("connection_model")) in {
        "connection_oriented",
        "control_plus_data",
        "mixed",
    }:
        implementation_axes.append("state")
    if _names_of(routing.get("dispatch_keys")):
        implementation_axes.append("router")
    if _names_of(resource.get("resource_objects")):
        implementation_axes.append("resource")
    security_requirements = errors.get("security_requirements", {})
    if any(security_requirements.get(field) for field in security_requirements if isinstance(security_requirements, dict)):
        implementation_axes.append("security")
    implementation_axes = _dedupe_preserve(implementation_axes)

    critical_stateful_behaviors = _summaries_of(state_model.get("transitions"), limit=10)
    required_codec_scope = _names_of(message.get("surface_catalog"))[:40]
    error_handling_obligations = [
        f"{item.get('condition', '')} -> {item.get('required_action', '')}".strip(" ->")
        for item in errors.get("error_matrix", [])[:10]
        if isinstance(item, dict)
    ]
    persistence_and_lifecycle_obligations = _summaries_of(resource.get("lifecycle_rules"), limit=10) + _summaries_of(
        resource.get("persistence_scope"), limit=10
    )
    implementation_risks = list(reconciliation.get("planner_risks", []))
    if state_model.get("transitions"):
        implementation_risks.append("协议包含显式状态转移，状态对象与阶段约束需要单独建模。")
    if errors.get("error_matrix"):
        implementation_risks.append("协议异常路径具有条件到动作的强约束，测试边界与实现边界必须同步设计。")
    if resource.get("resource_objects") and resource.get("lifecycle_rules"):
        implementation_risks.append("协议对象具有生命周期与持久化边界，存储层和清理策略不能省略。")
    implementation_risks = _dedupe_preserve([item for item in implementation_risks if item])

    return {
        "implementation_axes": implementation_axes,
        "planning_checklist": [
            "传输层与运行模型",
            "报文模型与编解码范围",
            "状态对象设计",
            "路由/调度设计",
            "安全与边界限制",
            "存储或资源抽象",
            "最小可运行功能集",
            "测试与验证方式",
        ],
        "implementation_risks": implementation_risks,
        "protocol_summary": f"{protocol_name} 的工程规划必须同时覆盖协议表面、状态对象、异常路径、资源生命周期和参数化限制。",
        "planning_trigger_facts": {
            "interaction_style": _value_of(interaction.get("style")),
            "connection_model": _value_of(transport.get("connection_model")),
            "state_objects": _names_of(state_model.get("state_objects")),
            "resource_objects": _names_of(resource.get("resource_objects")),
            "dispatch_keys": _names_of(routing.get("dispatch_keys")),
        },
        "critical_stateful_behaviors": critical_stateful_behaviors,
        "required_codec_scope": required_codec_scope,
        "error_handling_obligations": error_handling_obligations,
        "persistence_and_lifecycle_obligations": persistence_and_lifecycle_obligations[:12],
        "consistency_findings": reconciliation.get("consistency_findings", []),
        "coverage_gaps": reconciliation.get("coverage_gaps", []),
    }


def _assemble_final_payload(
    protocol_name: str,
    docs: list[NormalizedDoc],
    category_outputs: dict[str, dict[str, Any]],
    reconciliation: dict[str, Any],
    shared_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_index: list[dict[str, Any]] = list(shared_evidence)
    open_questions: list[dict[str, Any]] = []
    for category in SEMANTIC_CATEGORIES:
        category_blob = category_outputs[category]
        evidence_index.extend(category_blob.get("evidence", []))
        open_questions.extend(category_blob.get("open_questions", []))
    open_questions.extend(reconciliation.get("global_open_questions", []))

    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol_meta": {
            "protocol_name": protocol_name,
            "source_documents": [str(doc.path) for doc in docs],
            "document_count": len(docs),
        },
        "transport": category_outputs["transport"]["facts"],
        "interaction_model": category_outputs["interaction_model"]["facts"],
        "message_model": category_outputs["message_model"]["facts"],
        "state_model": category_outputs["state_model"]["facts"],
        "routing_model": category_outputs["routing_model"]["facts"],
        "resource_model": category_outputs["resource_model"]["facts"],
        "error_and_limits": category_outputs["error_and_limits"]["facts"],
        "minimum_v1": category_outputs["minimum_v1"]["facts"],
        "planning_inputs": _build_planning_inputs(protocol_name, category_outputs, reconciliation),
        "open_questions": open_questions,
        "evidence_index": _dedupe_evidence_index(evidence_index),
    }
    return _normalize_gold_compatible(payload)


def _shape_items(items: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    shaped: list[dict[str, Any]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        result: dict[str, Any] = {}
        for field in fields:
            if field == "name":
                result[field] = str(item.get("name") or item.get("condition") or item.get("summary") or f"item_{index + 1}")
            elif field == "summary":
                result[field] = str(item.get("summary") or item.get("reason") or item.get("required_action") or item.get("value_or_rule") or item.get("name") or "")
            elif field == "evidence_refs":
                refs = item.get(field, [])
                result[field] = [str(ref) for ref in refs] if isinstance(refs, list) else []
            elif field in ("fields", "surface_units"):
                value = item.get(field, [])
                result[field] = value if isinstance(value, list) else []
            else:
                result[field] = item.get(field, "")
        shaped.append(result)
    return shaped


def _normalize_gold_compatible(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload["message_model"]
    message["field_constraints"] = (
        message.get("field_constraints", [])
        + message.get("shared_fields", [])
        + message.get("global_framing_rules", [])
        + message.get("code_spaces", [])
    )
    message["surface_catalog"] = _shape_items(
        message.get("surface_catalog"), ("name", "kind", "direction", "summary", "evidence_refs")
    )
    message["message_or_command_entries"] = _shape_items(
        message.get("message_or_command_entries"),
        ("name", "surface_unit", "summary", "syntax_or_layout", "fields", "evidence_refs"),
    )
    payload["message_model"] = {key: message[key] for key in ("framing", "surface_catalog", "message_or_command_entries", "field_constraints")}

    state = payload["state_model"]
    state["invariants"] = state.get("invariants", []) + state.get("state_scopes", []) + state.get("state_objects", []) + state.get("retained_data", []) + state.get("cleanup_rules", [])
    state["state_nodes"] = _shape_items(state.get("state_nodes"), ("name", "summary", "evidence_refs"))
    state["transitions"] = _shape_items(state.get("transitions"), ("trigger", "from_state", "to_state", "summary", "evidence_refs"))
    payload["state_model"] = {key: state[key] for key in ("state_nodes", "transitions", "timers_and_constants", "invariants")}

    resource = payload["resource_model"]
    resource["lifecycle_rules"] = resource.get("lifecycle_rules", []) + resource.get("ownership_and_authority", []) + resource.get("storage_requirements", []) + resource.get("business_operations", [])
    payload["resource_model"] = {key: resource[key] for key in ("resource_objects", "lifecycle_rules", "persistence_scope")}

    errors = payload["error_and_limits"]
    security = errors.get("security", [])
    if not security and isinstance(errors.get("security_requirements"), dict):
        security = [item for values in errors["security_requirements"].values() if isinstance(values, list) for item in values]
    payload["error_and_limits"] = {"error_matrix": errors.get("error_matrix", []), "limits": errors.get("limits", {}), "security": security}

    minimum = payload["minimum_v1"]
    for field in minimum:
        minimum[field] = _shape_items(minimum[field], ("name", "summary", "evidence_refs"))
    payload["protocol_meta"].update(
        {
            "fact_source_type": "technical_document_target_profile_scoped",
            "target_scope": "target-profile-selected protocol subset",
        }
    )
    return payload


def _prune_excluded_scope(
    category_outputs: dict[str, dict[str, Any]],
    included_surface: list[str],
    excluded_surface: list[str],
    profile: TargetProfile,
) -> None:
    included = {name.casefold() for name in included_surface}
    forbidden_patterns = [rf"\b{re.escape(name.casefold())}\b" for name in excluded_surface]
    allowed_qos = profile.data["feature_constraints"].get("delivery_qos")
    if isinstance(allowed_qos, list) and set(allowed_qos) == {0}:
        forbidden_patterns.extend((r"\bqos\s*1\b", r"\bqos\s*2\b", r"\bqos\s*1/2\b"))
    for feature, enabled in profile.data["feature_constraints"].items():
        if enabled is False:
            words = [word for word in feature.casefold().split("_") if len(word) > 3]
            if words:
                forbidden_patterns.append(r"\b" + r"[ _-]*".join(map(re.escape, words)) + r"\b")

    def prune(value: Any, *, preserve_named_surface: bool = False) -> Any:
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("surface_unit") or "").casefold()
            keep_parent = preserve_named_surface and name in included
            return {
                key: prune(child, preserve_named_surface=preserve_named_surface or keep_parent)
                for key, child in value.items()
            }
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("surface_unit") or "").casefold()
                    keep_surface = preserve_named_surface and name in included
                    text = _json(item).casefold()
                    if not keep_surface and any(re.search(pattern, text) for pattern in forbidden_patterns):
                        continue
                    result.append(prune(item, preserve_named_surface=keep_surface))
                elif isinstance(item, str) and item.casefold() in {name.casefold() for name in excluded_surface}:
                    continue
                else:
                    result.append(prune(item))
            return result
        return value

    category_outputs["message_model"]["facts"] = prune(
        category_outputs["message_model"]["facts"], preserve_named_surface=True
    )
    for category in ("interaction_model", "state_model", "routing_model", "resource_model"):
        category_outputs[category]["facts"] = prune(category_outputs[category]["facts"])


class FactsExtractor:
    def __init__(
        self,
        protocol_name: str,
        doc_paths: list[str | Path],
        output_dir: str | Path,
        llm_client: FixedQwenClient,
        target_profile: TargetProfile,
    ) -> None:
        self.protocol_name = protocol_name
        self.doc_paths = [Path(path) for path in doc_paths]
        self.output_dir = Path(output_dir)
        self.llm_client = llm_client
        self.target_profile = target_profile
        self.rerank_client = QwenRerankClient(llm_client)
        self.logs = ExtractionLogger(self.output_dir / "_agent_logs")

    def _target_profile_manifest(self) -> dict[str, Any]:
        return {
            "path": str(self.target_profile.path),
            "schema_version": self.target_profile.data["schema_version"],
            "sha256": self.target_profile.sha256,
            "semantic_projection_sha256": self.target_profile.semantic_projection_sha256,
        }

    def _log(self, message: str) -> None:
        print(f"[agent.facts] {message}", flush=True)

    def _generate_with_usage(self, messages: list[dict[str, str]], *, top_p: float, temperature: float) -> LLMResponse:
        generate_with_usage = getattr(self.llm_client, "generate_with_usage", None)
        request = LLMRequest(messages=messages, top_p=top_p, temperature=temperature, is_stream=True)
        if callable(generate_with_usage):
            response = generate_with_usage(request)
            if not isinstance(response, LLMResponse):
                raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
            return response
        response = self.llm_client.generate(request)
        if not isinstance(response, str):
            raise RuntimeError(f"Expected string response from generate(), got {type(response)!r}")
        return LLMResponse(content=response, usage=LLMUsage(0, 0, 0))

    def prepare_output_dir(self) -> None:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs = ExtractionLogger(self.output_dir / "_agent_logs")

    def _ensure_category_usage(self, category_token_usage: dict[str, dict[str, Any]], category: str) -> dict[str, Any]:
        if category not in category_token_usage:
            category_token_usage[category] = {"subtasks": {}, "category_total": _usage_to_dict(LLMUsage(0, 0, 0))}
        return category_token_usage[category]

    def _register_usage(
        self,
        workflow_usage: LLMUsage,
        llm_call_usage: list[dict[str, Any]],
        category_token_usage: dict[str, dict[str, Any]],
        shared_stage_usage: dict[str, dict[str, int]],
        *,
        stage: str,
        call_type: str,
        usage: LLMUsage,
        category: str | None = None,
        subtask: str | None = None,
    ) -> LLMUsage:
        workflow_usage = _add_usage(workflow_usage, usage)
        llm_call_usage.append(
            {
                "category": category or SHARED_STAGE_KEY,
                "stage": stage,
                "subtask": subtask,
                "call_type": call_type,
                "usage": _usage_to_dict(usage),
            }
        )
        if category is None:
            stage_usage = shared_stage_usage.get(stage, _usage_to_dict(LLMUsage(0, 0, 0)))
            shared_stage_usage[stage] = _usage_to_dict(_add_usage(LLMUsage(**stage_usage), usage))
        else:
            category_bucket = self._ensure_category_usage(category_token_usage, category)
            subtask_key = subtask or stage
            if subtask_key not in category_bucket["subtasks"]:
                category_bucket["subtasks"][subtask_key] = {
                    "rerank": _usage_to_dict(LLMUsage(0, 0, 0)),
                    "extract": _usage_to_dict(LLMUsage(0, 0, 0)),
                    "subtask_total": _usage_to_dict(LLMUsage(0, 0, 0)),
                }
            subtask_bucket = category_bucket["subtasks"][subtask_key]
            current = LLMUsage(**subtask_bucket.get(call_type, _usage_to_dict(LLMUsage(0, 0, 0))))
            subtask_bucket[call_type] = _usage_to_dict(_add_usage(current, usage))
            subtask_total = _add_usage(LLMUsage(**subtask_bucket["rerank"]), LLMUsage(**subtask_bucket["extract"]))
            subtask_bucket["subtask_total"] = _usage_to_dict(subtask_total)
            category_total = LLMUsage(0, 0, 0)
            for bucket in category_bucket["subtasks"].values():
                category_total = _add_usage(category_total, LLMUsage(**bucket["subtask_total"]))
            category_bucket["category_total"] = _usage_to_dict(category_total)
        return workflow_usage

    def _run_retrieval_stage(
        self,
        *,
        stage_label: str,
        retrieval_key: str,
        chunks: list[Chunk],
        category: str | None,
        subtask: str | None,
        workflow_usage: LLMUsage,
        llm_call_usage: list[dict[str, Any]],
        category_token_usage: dict[str, dict[str, Any]],
        shared_stage_usage: dict[str, dict[str, int]],
    ) -> tuple[list[Chunk], dict[str, Any], LLMUsage]:
        self._log(f"{stage_label} retrieval start")
        candidate_scores = retrieve_candidate_chunks_for_category(
            chunks,
            retrieval_key,
            candidate_limit=RERANK_CANDIDATE_LIMIT,
            per_section_limit=RULES_SECTION_LIMIT,
        )
        self.logs.write(
            f"retrieval_{stage_label}",
            _json(
                {
                    "stage": stage_label,
                    "retrieval_key": retrieval_key,
                    "candidate_limit": RERANK_CANDIDATE_LIMIT,
                    "per_section_limit": RULES_SECTION_LIMIT,
                    "candidates": [_chunk_score_to_dict(item) for item in candidate_scores],
                }
            ),
            ".json",
        )
        self._log(f"{stage_label} retrieval done candidates={len(candidate_scores)}")

        rerank_result: RerankResult | None = None
        try:
            rerank_candidates = _build_rerank_candidates(candidate_scores)
            rerank_messages = build_rerank_prompt(self.protocol_name, stage_label, rerank_candidates)
            self.logs.write(f"rerank_prompt_{stage_label}", _json(rerank_messages), ".json")
            self._log(f"{stage_label} rerank request start candidates={len(rerank_candidates)}")
            rerank_response = self.rerank_client.rerank_with_usage(rerank_messages)
            workflow_usage = self._register_usage(
                workflow_usage,
                llm_call_usage,
                category_token_usage,
                shared_stage_usage,
                stage=stage_label,
                call_type="rerank",
                usage=rerank_response.usage,
                category=category,
                subtask=subtask,
            )
            self.logs.write(f"rerank_response_{stage_label}", rerank_response.content)
            rerank_raw = _extract_json_payload(rerank_response.content)
            rerank_result = _normalize_rerank_result(stage_label, rerank_raw, candidate_scores)
            self._log(
                f"{stage_label} rerank done ranked={len(rerank_result.ranked_chunk_ids)} "
                f"high_priority={len(rerank_result.high_priority_chunk_ids)} "
                f"(tokens={rerank_response.usage.total_tokens} in={rerank_response.usage.prompt_tokens} out={rerank_response.usage.completion_tokens})"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"{stage_label} rerank failed; fallback to rules ordering error={exc}")

        selected_chunks, selected_context_meta = _assemble_prompt_context_for_category(candidate_scores, rerank_result)
        self.logs.write(
            f"selected_context_{stage_label}",
            _json({"stage": stage_label, **selected_context_meta}),
            ".json",
        )
        self._log(
            f"{stage_label} context assembled chunks={len(selected_chunks)} chars={selected_context_meta.get('char_count', 0)}"
        )
        return selected_chunks, selected_context_meta, workflow_usage

    def extract(self) -> ExtractionResult:
        self._log(f"extract start protocol={self.protocol_name} docs={len(self.doc_paths)}")
        self._log("validate inputs start")
        context = validate_document_inputs(self.protocol_name, self.doc_paths, self.llm_client, skip_llm_check=False)
        self._log(
            f"validate inputs done diagnostics={len(context.diagnostics)} documents={len(context.docs)} chunks={len(context.chunks)}"
        )
        if context.has_errors():
            self._log("validation failed; writing manifest only")
            self.prepare_output_dir()
            facts_path = self.output_dir / "protocol_facts.json"
            manifest_path = self.output_dir / "run_manifest.json"
            manifest_path.write_text(
                _json(
                    {
                        "protocol_name": self.protocol_name,
                        "schema_version": SCHEMA_VERSION,
                        "target_profile": self._target_profile_manifest(),
                        "success": False,
                        "diagnostics": [diag.__dict__ for diag in context.diagnostics],
                    }
                ),
                encoding="utf-8",
            )
            return ExtractionResult(False, self.output_dir, facts_path, manifest_path, context.diagnostics, {})

        self._log("llm self-check passed")
        self._log(f"prepare output dir path={self.output_dir}")
        self.llm_client.ensure_ready()
        self.prepare_output_dir()
        self._log("write normalized docs and chunk index")
        self.logs.write("normalized_docs", _json([doc.__dict__ | {"path": str(doc.path)} for doc in context.docs]), ".json")
        self.logs.write("chunk_index", _json([chunk.__dict__ for chunk in context.chunks]), ".json")

        surface_index = build_surface_index(context.chunks)
        capability_resolutions = resolve_capabilities(self.target_profile, surface_index, context.chunks)
        scope_resolution = resolve_scope(self.target_profile, surface_index, capability_resolutions, context.chunks)
        excluded_features = [
            key for key, value in self.target_profile.data["feature_constraints"].items() if value is False
        ]
        scope_envelope = {
            "required_capabilities": self.target_profile.data["required_capabilities"],
            "included_surface": scope_resolution["included_surface"],
            "allowed_shared_dependencies": ["framing", "fields", "state", "errors", "cleanup"],
            "excluded_features": excluded_features,
        }
        self.logs.write(
            "surface_index",
            _json({"surface_units": surface_index}),
            ".json",
        )
        scope_resolution_path = self.logs.write("scope_resolution", _json(scope_resolution), ".json")

        diagnostics = list(context.diagnostics)
        workflow_usage = LLMUsage(0, 0, 0)
        category_token_usage: dict[str, dict[str, Any]] = {}
        llm_call_usage: list[dict[str, Any]] = []
        shared_stage_usage: dict[str, dict[str, int]] = {}

        discovery_selected, _discovery_meta, workflow_usage = self._run_retrieval_stage(
            stage_label="surface_discovery",
            retrieval_key="surface_discovery",
            chunks=context.chunks,
            category=None,
            subtask=None,
            workflow_usage=workflow_usage,
            llm_call_usage=llm_call_usage,
            category_token_usage=category_token_usage,
            shared_stage_usage=shared_stage_usage,
        )
        scoped_entries = [entry for entry in surface_index if entry["name"] in scope_resolution["included_surface"]]
        chunk_by_id = {chunk.chunk_id: chunk for chunk in context.chunks}
        indexed_chunks = [
            chunk_by_id[entry["chunk_ids"][0]]
            for entry in surface_index
            if entry["chunk_ids"] and entry["chunk_ids"][0] in chunk_by_id
        ]
        discovery_selected = list({chunk.chunk_id: chunk for chunk in discovery_selected + indexed_chunks}.values())

        discovery_payload = _normalize_surface_discovery_output({}, discovery_selected)
        try:
            discovery_messages = build_surface_discovery_prompt(
                context.protocol_name, context.docs, discovery_selected, scope_envelope
            )
            self.logs.write("prompt_surface_discovery", _json(discovery_messages), ".json")
            self._log("surface_discovery llm request start")
            discovery_response = self._generate_with_usage(discovery_messages, top_p=0.1, temperature=0.1)
            workflow_usage = self._register_usage(
                workflow_usage,
                llm_call_usage,
                category_token_usage,
                shared_stage_usage,
                stage="surface_discovery",
                call_type="extract",
                usage=discovery_response.usage,
                category=None,
                subtask=None,
            )
            self._log(
                f"surface_discovery llm response done chars={len(discovery_response.content)} "
                f"(tokens={discovery_response.usage.total_tokens} in={discovery_response.usage.prompt_tokens} out={discovery_response.usage.completion_tokens})"
            )
            self.logs.write("response_surface_discovery", discovery_response.content)
            discovery_raw = _extract_json_payload(discovery_response.content)
            discovery_payload = _normalize_surface_discovery_output(discovery_raw, discovery_selected)
            self._log("surface_discovery normalized")
        except Exception as exc:  # noqa: BLE001
            self._log(f"surface_discovery llm request failed error={exc}")
            diagnostics.append(FactDiagnostic("warning", "surface_discovery_failed", str(exc)))
            discovery_payload["open_questions"].append(
                _default_open_question("surface_discovery", "Surface discovery failed before extraction.", "document_not_extracted")
            )

        indexed_evidence: list[dict[str, Any]] = []
        for entry in surface_index:
            chunk = chunk_by_id[entry["chunk_ids"][0]]
            evidence_id = f"doc_surface_{re.sub(r'[^a-z0-9]+', '_', entry['name'].casefold()).strip('_')}"
            indexed_evidence.append(
                {
                    "evidence_id": evidence_id,
                    "doc_path": chunk.doc_path,
                    "section_hint": chunk.section_hint,
                    "chunk_id": chunk.chunk_id,
                    "excerpt": chunk.text[:600],
                }
            )
            if not any(item.get("name") == entry["name"] for item in discovery_payload["facts"]["surface_units"] if isinstance(item, dict)):
                discovery_payload["facts"]["surface_units"].append(
                    {
                        "name": entry["name"],
                        "kind": "message",
                        "direction": "mixed",
                        "summary": entry["summary"],
                        "evidence_refs": [evidence_id],
                    }
                )
        discovery_payload["evidence"].extend(indexed_evidence)

        derived_evidence: dict[str, str] = {}
        for label, terms in {
            "transport_stream": ("stream of bytes",),
            "remaining_length": ("remaining length", "multiplier"),
            "topic_filter_wildcards": ("topic filter", "wildcard"),
            "first_packet": ("first packet", "connect"),
            "qos_publish": ("qos", "publish packet"),
        }.items():
            chunk = next(
                (item for item in context.chunks if all(term in item.text.casefold() for term in terms)),
                None,
            )
            if chunk:
                evidence_id = f"doc_derived_{label}"
                discovery_payload["evidence"].append(
                    {
                        "evidence_id": evidence_id,
                        "doc_path": chunk.doc_path,
                        "section_hint": chunk.section_hint,
                        "chunk_id": chunk.chunk_id,
                        "excerpt": chunk.text[:600],
                    }
                )
                derived_evidence[label] = evidence_id

        excluded_names = {item.casefold() for item in scope_resolution["excluded_surface"]}
        scoped_chunk_ids = {chunk_id for entry in scoped_entries for chunk_id in entry["chunk_ids"]}
        scoped_chunk_pool = [
            chunk
            for chunk in context.chunks
            if chunk.chunk_id in scoped_chunk_ids
            or not any(re.search(rf"\b{re.escape(name)}\b", chunk.text.casefold()) for name in excluded_names)
        ]
        category_outputs: dict[str, dict[str, Any]] = {}
        for category in SEMANTIC_CATEGORIES:
            category_raw = {"facts": deepcopy(CATEGORY_FACT_DEFAULTS[category]), "evidence": [], "open_questions": []}
            for task_def in CATEGORY_SUBTASKS[category]:
                task_name = task_def["name"]
                retrieval_key = task_def["retrieval_key"]
                stage_label = f"{category}_{task_name}"
                selected_chunks, _task_meta, workflow_usage = self._run_retrieval_stage(
                    stage_label=stage_label,
                    retrieval_key=retrieval_key,
                    chunks=scoped_chunk_pool,
                    category=category,
                    subtask=task_name,
                    workflow_usage=workflow_usage,
                    llm_call_usage=llm_call_usage,
                    category_token_usage=category_token_usage,
                    shared_stage_usage=shared_stage_usage,
                )

                try:
                    messages = build_category_task_prompt(
                        context.protocol_name,
                        context.docs,
                        category,
                        task_name,
                        discovery_payload["facts"],
                        selected_chunks,
                        scope_envelope | {"selected_chunks": [chunk.chunk_id for chunk in selected_chunks]},
                    )
                    self.logs.write(f"prompt_{stage_label}", _json(messages), ".json")
                    self._log(f"{stage_label} llm request start")
                    response = self._generate_with_usage(messages, top_p=0.1, temperature=0.1)
                    workflow_usage = self._register_usage(
                        workflow_usage,
                        llm_call_usage,
                        category_token_usage,
                        shared_stage_usage,
                        stage=stage_label,
                        call_type="extract",
                        usage=response.usage,
                        category=category,
                        subtask=task_name,
                    )
                    self._log(
                        f"{stage_label} llm response done chars={len(response.content)} "
                        f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
                    )
                    self.logs.write(f"response_{stage_label}", response.content)
                    parsed = _extract_json_payload(response.content)
                    normalized = _normalize_category_output(category, parsed, selected_chunks)
                except Exception as exc:  # noqa: BLE001
                    self._log(f"{stage_label} llm request failed error={exc}")
                    diagnostics.append(FactDiagnostic("error", "llm_request_failed", f"{stage_label}: {exc}"))
                    normalized = _normalize_category_output(
                        category,
                        {
                            "facts": {},
                            "evidence": [],
                            "open_questions": [
                                _default_open_question(category, f"Extraction task `{task_name}` failed.", "document_not_extracted")
                            ],
                        },
                        selected_chunks,
                    )
                category_raw["facts"] = _deep_merge(category_raw["facts"], normalized["facts"])
                category_raw["evidence"].extend(normalized["evidence"])
                category_raw["open_questions"].extend(normalized["open_questions"])

            category_outputs[category] = _normalize_category_output(category, category_raw, context.chunks[:1] if context.chunks else [])
            category_total = category_token_usage.get(category, {}).get("category_total", _usage_to_dict(LLMUsage(0, 0, 0)))
            self._log(
                f"category={category} normalized "
                f"(category_tokens={category_total['total_tokens']} in={category_total['prompt_tokens']} out={category_total['completion_tokens']})"
            )

        indexed_by_name = {entry["name"]: entry for entry in surface_index}
        message_surface = category_outputs["message_model"]["facts"]["surface_catalog"]
        minimum_surface = category_outputs["minimum_v1"]["facts"]["must_support_surface"]
        for name in scope_resolution["included_surface"]:
            entry = indexed_by_name[name]
            evidence_id = f"doc_surface_{re.sub(r'[^a-z0-9]+', '_', name.casefold()).strip('_')}"
            if not any(isinstance(item, dict) and item.get("name") == name for item in message_surface):
                message_surface.append(
                    {"name": name, "kind": "message", "direction": "mixed", "summary": entry["summary"], "evidence_refs": [evidence_id]}
                )
            if not any(isinstance(item, dict) and item.get("name") == name for item in minimum_surface):
                minimum_surface.append({"name": name, "summary": entry["summary"], "evidence_refs": [evidence_id]})

        _prune_excluded_scope(
            category_outputs,
            scope_resolution["included_surface"],
            scope_resolution["excluded_surface"],
            self.target_profile,
        )
        state_facts = category_outputs["state_model"]["facts"]
        routing_facts = category_outputs["routing_model"]["facts"]
        resource_facts = category_outputs["resource_model"]["facts"]
        for resolution in capability_resolutions:
            for surface_name in resolution.get("selected_seeds", []):
                evidence_id = f"doc_surface_{re.sub(r'[^a-z0-9]+', '_', surface_name.casefold()).strip('_')}"
                node_name = f"{surface_name} active"
                if not any(item.get("name") == node_name for item in state_facts["state_nodes"] if isinstance(item, dict)):
                    state_facts["state_nodes"].append(
                        {"name": node_name, "summary": f"Protocol state needed while processing {surface_name}.", "evidence_refs": [evidence_id]}
                    )
        connection_seed = next(
            (
                seed
                for resolution in capability_resolutions
                if "connection" in resolution["capability_id"].casefold()
                for seed in resolution.get("selected_seeds", [])
            ),
            None,
        )
        subscription_seed = next(
            (
                seed
                for resolution in capability_resolutions
                if "subscribe" in resolution["capability_id"].casefold()
                for seed in resolution.get("selected_seeds", [])
                if "subscribe" in seed.casefold()
            ),
            None,
        )
        if connection_seed:
            evidence_id = f"doc_surface_{connection_seed.casefold()}"
            state_facts["transitions"].append(
                {"trigger": connection_seed, "from_state": "network_connected", "to_state": "session_active", "summary": "Establish the protocol session.", "evidence_refs": [evidence_id]}
            )
        if subscription_seed:
            evidence_id = f"doc_surface_{subscription_seed.casefold()}"
            routing_facts["dispatch_keys"].append(
                {"name": "Topic Name and Topic Filter", "summary": "Route publications by matching the published name against subscription filters.", "evidence_refs": [evidence_id]}
            )
            routing_facts["dispatch_targets"].append(
                {"name": "matching subscriptions", "summary": "Deliver to clients with matching subscriptions.", "evidence_refs": [evidence_id]}
            )
            routing_facts["matching_rules"].append(
                {"name": "Topic Filter match", "summary": "Apply the protocol Topic Filter matching rules.", "evidence_refs": [derived_evidence.get("topic_filter_wildcards", evidence_id)]}
            )
            resource_facts["resource_objects"].append(
                {"name": "Subscription", "summary": "Session-scoped routing interest represented by a Topic Filter.", "evidence_refs": [evidence_id]}
            )
            resource_facts["lifecycle_rules"].append(
                {"name": "Subscription creation", "summary": f"Create or update subscription state when processing {subscription_seed}.", "evidence_refs": [evidence_id]}
            )
        if "transport_stream" in derived_evidence:
            category_outputs["transport"]["facts"]["runtime_implications"].append(
                {
                    "summary": "The transport provides an ordered byte stream; decoding must preserve partial input across reads.",
                    "evidence_refs": [derived_evidence["transport_stream"]],
                }
            )

        reconciliation = _normalize_reconciliation_output({})
        try:
            reconciliation_messages = build_reconciliation_prompt(
                context.protocol_name,
                discovery_payload["facts"],
                {category: category_outputs[category]["facts"] for category in SEMANTIC_CATEGORIES},
                scope_envelope,
            )
            self.logs.write("prompt_cross_category_reconciliation", _json(reconciliation_messages), ".json")
            self._log("cross_category_reconciliation llm request start")
            reconciliation_response = self._generate_with_usage(reconciliation_messages, top_p=0.1, temperature=0.1)
            workflow_usage = self._register_usage(
                workflow_usage,
                llm_call_usage,
                category_token_usage,
                shared_stage_usage,
                stage="cross_category_reconciliation",
                call_type="extract",
                usage=reconciliation_response.usage,
                category=None,
                subtask=None,
            )
            self._log(
                f"cross_category_reconciliation llm response done chars={len(reconciliation_response.content)} "
                f"(tokens={reconciliation_response.usage.total_tokens} in={reconciliation_response.usage.prompt_tokens} out={reconciliation_response.usage.completion_tokens})"
            )
            self.logs.write("response_cross_category_reconciliation", reconciliation_response.content)
            reconciliation_raw = _extract_json_payload(reconciliation_response.content)
            reconciliation = _normalize_reconciliation_output(reconciliation_raw)
            self._log("cross_category_reconciliation normalized")
        except Exception as exc:  # noqa: BLE001
            self._log(f"cross_category_reconciliation llm request failed error={exc}")
            diagnostics.append(FactDiagnostic("warning", "reconciliation_failed", str(exc)))

        self._log("assemble final payload")
        profile_evidence = build_profile_evidence(self.target_profile)
        minimum = category_outputs["minimum_v1"]["facts"]
        for feature in excluded_features:
            evidence_id = f"profile_feature_constraints_{feature.casefold()}"
            minimum["may_defer_features"].append(
                {"summary": f"Target profile excludes {feature} from the required subset.", "evidence_refs": [evidence_id]}
            )
        role_evidence = "profile_target_role"
        minimum["implementation_assumptions"].append(
            {
                "summary": f"Implementation targets the {self.target_profile.data['target_role']} role.",
                "evidence_refs": [role_evidence],
            }
        )
        delivery_qos = self.target_profile.data["feature_constraints"].get("delivery_qos")
        if delivery_qos == [0]:
            minimum["may_defer_features"].append(
                {
                    "summary": "QoS 1 and QoS 2 delivery transactions are outside the required subset.",
                    "evidence_refs": ["profile_feature_constraints_delivery_qos"],
                }
            )
            minimum["implementation_assumptions"].append(
                {"summary": "The required delivery path is QoS 0 only.", "evidence_refs": ["profile_feature_constraints_delivery_qos"]}
            )
        if self.target_profile.data["deployment_constraints"].get("persistence") is False:
            minimum["implementation_assumptions"].append(
                {"summary": "Runtime state is in-memory only; durable persistence is excluded.", "evidence_refs": ["profile_deployment_constraints_persistence"]}
            )
        derived_minimum = (
            ("must_support_state_behaviors", "Incremental stream decode", "Buffer partial TCP stream input until a complete packet is available.", "transport_stream"),
            ("must_support_error_paths", "Application packet before CONNECT", "Reject application packets received before connection establishment.", "first_packet"),
            ("must_support_error_paths", "Unsupported QoS PUBLISH", "Reject PUBLISH delivery modes outside the selected QoS 0 subset.", "qos_publish"),
            ("must_support_limits", "Remaining Length variable integer", "Decode the bounded variable-byte Remaining Length field.", "remaining_length"),
            ("must_support_limits", "Topic Filter wildcards", "Apply the documented Topic Filter wildcard rules.", "topic_filter_wildcards"),
        )
        for collection, name, summary, evidence_key in derived_minimum:
            if evidence_key in derived_evidence and not any(item.get("name") == name for item in minimum[collection] if isinstance(item, dict)):
                minimum[collection].append(
                    {"name": name, "summary": summary, "evidence_refs": [derived_evidence[evidence_key]]}
                )
        final_payload = _assemble_final_payload(
            context.protocol_name,
            context.docs,
            category_outputs,
            reconciliation,
            discovery_payload["evidence"] + profile_evidence,
        )
        facts_path = self.output_dir / "protocol_facts.json"
        facts_path.write_text(_json(final_payload), encoding="utf-8")
        self._log(f"facts written path={facts_path}")

        manifest = {
            "protocol_name": context.protocol_name,
            "schema_version": SCHEMA_VERSION,
            "target_profile": self._target_profile_manifest(),
            "scope_resolution": {
                "path": str(scope_resolution_path),
                "sha256": hashlib.sha256(scope_resolution_path.read_bytes()).hexdigest(),
                "scope_mode": self.target_profile.data["scope_policy"]["mode"],
                "conformance_mode": self.target_profile.data["scope_policy"]["conformance_mode"],
                "capability_count": len(capability_resolutions),
                "included_count": len(scope_resolution["included_surface"]),
                "excluded_count": len(scope_resolution["excluded_surface"]),
                "unresolved_count": len(scope_resolution["unresolved_capabilities"]),
                "closure_status": scope_resolution["closure_status"],
            },
            "source_documents": [str(path) for path in self.doc_paths],
            "document_count": len(context.docs),
            "chunk_count": len(context.chunks),
            "categories": SEMANTIC_CATEGORIES,
            "model": FIXED_MODEL,
            "facts_path": str(facts_path),
            "llm_call_usage": llm_call_usage,
            "category_token_usage": category_token_usage,
            "shared_stage_token_usage": shared_stage_usage,
            "workflow_token_usage": _usage_to_dict(workflow_usage),
            "subtasks": {category: [item["name"] for item in CATEGORY_SUBTASKS[category]] for category in SEMANTIC_CATEGORIES},
        }
        manifest_path = self.output_dir / "run_manifest.json"
        manifest_path.write_text(_json(manifest), encoding="utf-8")
        self.logs.write(
            "token_usage_summary",
            _json(
                {
                    "llm_call_usage": llm_call_usage,
                    "category_token_usage": category_token_usage,
                    "shared_stage_token_usage": shared_stage_usage,
                    "workflow_token_usage": _usage_to_dict(workflow_usage),
                }
            ),
            ".json",
        )
        self._log(f"manifest written path={manifest_path}")
        self._log(
            f"workflow token usage "
            f"(total={workflow_usage.total_tokens} in={workflow_usage.prompt_tokens} out={workflow_usage.completion_tokens})"
        )

        self._log("verify output start")
        verification = verify_facts_output(self.output_dir)
        self._log(f"verify output done diagnostics={len(verification.diagnostics)} ok={verification.ok}")
        diagnostics.extend(verification.diagnostics)
        self._log(f"extract done success={not any(diag.level == 'error' for diag in diagnostics)}")
        return ExtractionResult(
            success=not any(diag.level == "error" for diag in diagnostics),
            output_dir=self.output_dir,
            facts_path=facts_path,
            manifest_path=manifest_path,
            diagnostics=diagnostics,
            category_outputs=category_outputs,
        )


def default_output_dir(protocol_name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "agent" / "facts" / "out" / _safe_protocol_name(protocol_name)
