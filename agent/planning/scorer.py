from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .architecture import normalize_candidate_modules
from .constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN, MODULE_BUDGET_TARGET
from .models import (
    ArchitectureJudgeScore,
    ArchitectureLintResult,
    ArchitectureReview,
    ArchitectureScore,
    CandidateArchitecture,
    ExpertActivation,
    PlanningIR,
    ProtocolProfile,
)
from .prompts import build_architecture_judge_prompt


GENERIC_MODULE_NAME_TOKENS = {
    "adapter",
    "and",
    "app",
    "application",
    "codec",
    "composition",
    "connection",
    "core",
    "datagram",
    "dispatch",
    "endpoint",
    "framing",
    "handler",
    "handlers",
    "io",
    "hub",
    "logic",
    "message",
    "module",
    "network",
    "protocol",
    "registry",
    "resource",
    "role",
    "router",
    "routing",
    "runtime",
    "semantic",
    "server",
    "service",
    "session",
    "state",
    "store",
    "transport",
    "types",
}


def _owned_capabilities(modules: list[dict[str, object]]) -> set[str]:
    owned: set[str] = set()
    for module in modules:
        raw = module.get("owned_capabilities", [])
        if not isinstance(raw, list):
            continue
        owned.update(str(item) for item in raw if str(item).strip())
    return owned


def _required_capabilities(profile: ProtocolProfile, activations: list[ExpertActivation]) -> set[str]:
    required_capabilities = {capability for act in activations for capability in act.required_capabilities}
    required_capabilities.update(str(item) for item in profile.data.get("required_capabilities", []) if str(item).strip())
    return required_capabilities


def _known_capabilities(profile: ProtocolProfile, activations: list[ExpertActivation]) -> set[str]:
    known = _required_capabilities(profile, activations)
    for key, value in profile.data.items():
        if key.endswith("_capabilities") and isinstance(value, list):
            known.update(str(item) for item in value if str(item).strip())
    return known


def _name_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}


def _allowed_module_name_tokens(planning_ir: PlanningIR, profile: ProtocolProfile) -> set[str]:
    allowed = set(GENERIC_MODULE_NAME_TOKENS)
    allowed.update(_name_tokens(planning_ir.protocol_name))
    allowed.update(_name_tokens(planning_ir.target_profile.target_role))
    for role in planning_ir.normalized_roles:
        allowed.update(_name_tokens(role))
    for capability in profile.data.get("required_capabilities", []):
        allowed.update(_name_tokens(str(capability)))
    for collection in (planning_ir.surface_units, planning_ir.message_entries, planning_ir.state_nodes, planning_ir.resource_objects):
        for item in collection:
            if isinstance(item, dict):
                allowed.update(_name_tokens(str(item.get("name", ""))))
    for item in planning_ir.facts.get("routing_model", {}).get("dispatch_keys", []):
        if isinstance(item, dict):
            allowed.update(_name_tokens(str(item.get("name", ""))))
    return allowed


def _ungrounded_module_name_tokens(planning_ir: PlanningIR, profile: ProtocolProfile, modules: list[dict[str, object]]) -> dict[str, list[str]]:
    allowed = _allowed_module_name_tokens(planning_ir, profile)
    gaps: dict[str, list[str]] = {}
    for module in modules:
        name = str(module.get("name", ""))
        unknown = sorted(token for token in _name_tokens(name) if token not in allowed)
        if unknown:
            gaps[name] = unknown
    return gaps


def lint_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    candidates: list[CandidateArchitecture],
) -> list[ArchitectureLintResult]:
    required_capabilities = _required_capabilities(profile, activations)
    known_capabilities = _known_capabilities(profile, activations)
    results: list[ArchitectureLintResult] = []
    for candidate in candidates:
        schema_errors: list[str] = []
        warnings: list[str] = []
        if not candidate.candidate_id.strip():
            schema_errors.append("candidate_id is required")
        if not candidate.title.strip():
            schema_errors.append("title is required")
        if not candidate.summary.strip():
            schema_errors.append("summary is required")
        modules = normalize_candidate_modules(candidate.modules, [])
        if not modules:
            schema_errors.append("modules must contain at least one module")
        for idx, module in enumerate(modules):
            if not str(module.get("name", "")).strip():
                schema_errors.append(f"modules[{idx}].name is required")
            if not str(module.get("role", "")).strip():
                schema_errors.append(f"modules[{idx}].role is required")
            if not isinstance(module.get("owned_capabilities", []), list):
                schema_errors.append(f"modules[{idx}].owned_capabilities must be a list")
            if not isinstance(module.get("evidence_refs", []), list):
                schema_errors.append(f"modules[{idx}].evidence_refs must be a list")
        module_count = len(modules)
        module_count_ok = MODULE_BUDGET_MIN <= module_count <= MODULE_BUDGET_MAX
        owned_capabilities = _owned_capabilities(modules)
        missing_capabilities = sorted(required_capabilities - owned_capabilities)
        unknown_capabilities = sorted(owned_capabilities - known_capabilities)
        ungrounded_tokens = _ungrounded_module_name_tokens(planning_ir, profile, modules)
        if missing_capabilities:
            warnings.append(f"Missing required capabilities: {', '.join(missing_capabilities)}")
        if unknown_capabilities:
            warnings.append(f"Unknown capability tokens: {', '.join(unknown_capabilities)}")
        if ungrounded_tokens:
            warnings.append(f"Ungrounded module name tokens: {ungrounded_tokens}")
        if not module_count_ok:
            schema_errors.append(f"module count {module_count} outside hard safety range {MODULE_BUDGET_MIN}-{MODULE_BUDGET_MAX}")
        results.append(
            ArchitectureLintResult(
                candidate_id=candidate.candidate_id,
                ok=not schema_errors,
                module_count=module_count,
                module_count_ok=module_count_ok,
                missing_capabilities=missing_capabilities,
                unknown_capabilities=unknown_capabilities,
                ungrounded_module_name_tokens=ungrounded_tokens,
                schema_errors=schema_errors,
                warnings=warnings,
            )
        )
    return results


def _load_json_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


def _generate_with_usage(
    llm_client: FixedQwenClient,
    messages: list[dict[str, str]],
    *,
    top_p: float,
    temperature: float,
) -> LLMResponse:
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    request = LLMRequest(
        messages=messages,
        top_p=top_p,
        temperature=temperature,
        is_stream=True,
    )
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def _coerce_score(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_major_issues(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            issues.append({str(key): str(val) for key, val in item.items()})
        elif str(item).strip():
            issues.append({"severity": "major", "issue": str(item)})
    return issues


def _parse_judge_score(item: dict[str, Any]) -> ArchitectureJudgeScore:
    raw_scores = item.get("scores", {})
    scores = {
        str(key): _coerce_score(value)
        for key, value in raw_scores.items()
    } if isinstance(raw_scores, dict) else {}
    return ArchitectureJudgeScore(
        candidate_id=str(item.get("candidate_id") or ""),
        verdict=str(item.get("verdict") or "reject"),
        total_score=_coerce_score(item.get("total_score")),
        scores=scores,
        major_issues=_parse_major_issues(item.get("major_issues", [])),
        rationale=str(item.get("rationale") or ""),
        recommended_selection=bool(item.get("recommended_selection", False)),
    )


def judge_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    candidates: list[CandidateArchitecture],
    lint_results: list[ArchitectureLintResult],
    llm_client: FixedQwenClient | None,
    log: Callable[[str], None] | None = None,
    log_artifact: Callable[[str, str, str], None] | None = None,
    register_usage: Callable[[str, str, LLMUsage], None] | None = None,
) -> ArchitectureReview | None:
    if llm_client is None:
        if log is not None:
            log("architecture_judge llm_required_missing fallback=disabled")
        return None
    planning_ir_summary = {
        "protocol_name": planning_ir.protocol_name,
        "normalized_roles": planning_ir.normalized_roles,
        "minimum_v1_surface": [
            item.get("name")
            for item in planning_ir.minimum_v1.get("must_support_surface", [])
            if isinstance(item, dict)
        ],
        "resource_count": len(planning_ir.resource_objects),
        "state_count": len(planning_ir.state_nodes),
        "blocking_questions": planning_ir.open_questions.get("blocking", []),
    }
    messages = build_architecture_judge_prompt(
        planning_ir_summary,
        profile.data,
        planning_ir.target_profile.raw,
        [item.__dict__ for item in activations],
        [candidate.__dict__ for candidate in candidates],
        [item.__dict__ for item in lint_results],
    )
    try:
        if log_artifact is not None:
            log_artifact("architecture_judge_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"architecture_judge request_start messages={len(messages)}")
        response = _generate_with_usage(llm_client, messages, top_p=0.2, temperature=0.1)
        if register_usage is not None:
            register_usage("architecture_judge", "judge", response.usage)
        if log is not None:
            log(
                f"architecture_judge response_done chars={len(response.content)} "
                f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
            )
        if log_artifact is not None:
            log_artifact("architecture_judge_response", response.content, ".txt")
        payload = _load_json_payload(response.content)
        review_payload = payload.get("architecture_review", payload)
        if not isinstance(review_payload, dict):
            return None
        score_items = review_payload.get("scores", [])
        if not isinstance(score_items, list):
            return None
        scores = [
            _parse_judge_score(item)
            for item in score_items
            if isinstance(item, dict)
        ]
        review = ArchitectureReview(
            selected_candidate_id=str(review_payload.get("selected_candidate_id") or ""),
            rejection_reason=str(review_payload.get("rejection_reason") or ""),
            scores=scores,
        )
        if log_artifact is not None:
            log_artifact("architecture_review", json.dumps(review.__dict__, ensure_ascii=False, indent=2, default=lambda obj: obj.__dict__), ".json")
        return review
    except Exception as exc:
        if log is not None:
            log(f"architecture_judge failed error={type(exc).__name__}")
        return None


def architecture_scores_from_review(review: ArchitectureReview, candidates: list[CandidateArchitecture]) -> list[ArchitectureScore]:
    candidate_ids = {candidate.candidate_id for candidate in candidates}
    scores: list[ArchitectureScore] = []
    for item in review.scores:
        if item.candidate_id not in candidate_ids:
            continue
        reasons = [f"Judge verdict: {item.verdict}", item.rationale]
        reasons.extend(str(issue.get("issue") or issue) for issue in item.major_issues)
        scores.append(
            ArchitectureScore(
                candidate_id=item.candidate_id,
                hard_score=0.0,
                llm_score=item.total_score,
                total_score=item.total_score,
                breakdown=item.scores,
                reasons=[reason for reason in reasons if reason],
                selected=item.candidate_id == review.selected_candidate_id,
            )
        )
    return scores


def score_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    candidates: list[CandidateArchitecture],
) -> list[ArchitectureScore]:
    required_capabilities = {capability for act in activations for capability in act.required_capabilities}
    required_capabilities.update(str(item) for item in profile.data.get("required_capabilities", []) if str(item).strip())
    minimum_surfaces = {
        str(item.get("name"))
        for item in planning_ir.minimum_v1.get("must_support_surface", [])
        if isinstance(item, dict) and item.get("name")
    }
    scores: list[ArchitectureScore] = []
    for candidate in candidates:
        normalized_modules = normalize_candidate_modules(candidate.modules, [])
        owned_capabilities = _owned_capabilities(normalized_modules)
        missing_capabilities = sorted(required_capabilities - owned_capabilities)
        ungrounded_tokens = _ungrounded_module_name_tokens(planning_ir, profile, normalized_modules)
        module_text = " ".join(f"{module.get('name', '')} {module.get('role', '')}" for module in normalized_modules).lower()
        hard = 0.0
        reasons = []
        coverage = 1.0 if normalized_modules else 0.0
        hard += coverage * 25
        if not (MODULE_BUDGET_MIN <= len(normalized_modules) <= MODULE_BUDGET_MAX):
            reasons.append(f"Module budget violation: {len(normalized_modules)} modules")
            hard -= 1000.0
        else:
            compactness_penalty = abs(len(normalized_modules) - MODULE_BUDGET_TARGET) * 2.0
            hard += max(0.0, 15.0 - compactness_penalty)
            reasons.append(f"Module budget satisfied: {len(normalized_modules)} modules")
        if required_capabilities:
            covered = len(required_capabilities) - len(missing_capabilities)
            hard += 35.0 * (covered / len(required_capabilities))
        if missing_capabilities:
            hard -= min(200.0, len(missing_capabilities) * 12.0)
            reasons.append(f"Capability coverage gap: {', '.join(missing_capabilities[:8])}")
        if ungrounded_tokens:
            reasons.append(f"Ungrounded module name tokens: {ungrounded_tokens}")
        if str(profile.data.get("transport_shape")) == "stream" and "codec" in module_text:
            hard += 10
            reasons.append("Includes codec separation for stream parsing")
        if minimum_surfaces:
            hard += min(20.0, len(minimum_surfaces) / max(1, len(minimum_surfaces)) * 20.0)
            reasons.append("Assumes full minimum scope surface coverage planning")
        if str(planning_ir.target_profile.target_role).lower() in module_text:
            hard += 10
            reasons.append("Contains target-role composition boundary")
        soft = 12.0
        if candidate.candidate_id == "candidate_layered":
            soft += 8.0
            reasons.append("Layered layout is most compatible with current coder")
        if "risk" in " ".join(candidate.risks).lower():
            soft -= 1.0
        total = hard + soft
        scores.append(
            ArchitectureScore(
                candidate_id=candidate.candidate_id,
                hard_score=hard,
                llm_score=soft,
                total_score=total,
                breakdown={
                    "coverage": coverage * 25,
                    "capability_coverage": 0.0 if required_capabilities and missing_capabilities else 35.0,
                    "module_budget": 1.0 if MODULE_BUDGET_MIN <= len(normalized_modules) <= MODULE_BUDGET_MAX else 0.0,
                    "coder_fit": soft,
                },
                reasons=reasons,
                selected=False,
            )
        )
    if scores:
        valid_scores = [
            item for item in scores
            if item.breakdown.get("module_budget", 0.0) == 1.0
            and not any(reason.startswith("Capability coverage gap:") for reason in item.reasons)
        ]
        best_id = max(valid_scores, key=lambda item: item.total_score).candidate_id if valid_scores else None
        scores = [
            ArchitectureScore(
                candidate_id=item.candidate_id,
                hard_score=item.hard_score,
                llm_score=item.llm_score,
                total_score=item.total_score,
                breakdown=item.breakdown,
                reasons=item.reasons,
                selected=item.candidate_id == best_id,
            )
            for item in scores
        ]
    return scores
