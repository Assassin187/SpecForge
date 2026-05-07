from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .models import ArchitectureScore, CandidateArchitecture, DesignDecision, ExpertActivation, PlanningIR, ProtocolProfile
from .prompts import build_decision_enrichment_prompt


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


def _generate_with_usage(llm_client: FixedQwenClient, messages: list[dict[str, str]]) -> LLMResponse:
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    request = LLMRequest(
        messages=messages,
        top_p=0.4,
        temperature=0.2,
        is_stream=True,
    )
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def _base_decisions(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    chosen_architecture: CandidateArchitecture,
    activations: list[ExpertActivation],
    scores: list[ArchitectureScore],
) -> list[DesignDecision]:
    score_map = {item.candidate_id: item for item in scores}
    selected_score = score_map.get(chosen_architecture.candidate_id)
    active_rule_ids = [item.rule_id for item in activations]
    facts = planning_ir.facts
    decisions = [
        DesignDecision(
            decision_id="dec_target_scope",
            decision_type="target_scope",
            selected_option=str(planning_ir.target_profile.scope),
            rationale=f"Target profile requires role={planning_ir.target_profile.target_role}, language={planning_ir.target_profile.language}, runtime={planning_ir.target_profile.runtime}.",
            evidence_refs=planning_ir.traceability_index.get("minimum_v1.must_support_surface[0]", []),
            expert_rule_ids=[],
            downstream_spec_impact=["Filter implementation plan to minimum_v1-first delivery"],
            origin="deterministic",
            source_steps=["target_profile", "stage1_ir"],
        ),
        DesignDecision(
            decision_id="dec_runtime_shape",
            decision_type="runtime_model",
            selected_option=str(profile.data.get("transport_shape")),
            rationale=f"Transport profile indicates {profile.data.get('transport_shape')} runtime with {planning_ir.target_profile.runtime}.",
            evidence_refs=planning_ir.traceability_index.get("transport.connection_model", []),
            expert_rule_ids=[rule_id for rule_id in active_rule_ids if "stream" in rule_id],
            downstream_spec_impact=["Create transport/runtime module", "Bind codec ingress to transport events"],
            origin="rule",
            source_steps=["stage2_profile", "stage3_rules"],
        ),
        DesignDecision(
            decision_id="dec_architecture_choice",
            decision_type="architecture_selection",
            selected_option=chosen_architecture.candidate_id,
            rationale=f"Selected highest-scoring candidate '{chosen_architecture.title}' with score {selected_score.total_score if selected_score else 0:.1f}.",
            evidence_refs=[],
            expert_rule_ids=active_rule_ids,
            downstream_spec_impact=["Use selected module graph as implementation backbone"],
            origin="rule",
            source_steps=["stage4_candidate_architectures", "stage4_scoring"],
        ),
        DesignDecision(
            decision_id="dec_state_ownership",
            decision_type="state_ownership",
            selected_option=str(profile.data.get("statefulness")),
            rationale=f"Statefulness classified as {profile.data.get('statefulness')}, driving ownership and cleanup boundaries.",
            evidence_refs=planning_ir.traceability_index.get("state_model.state_nodes[0]", []),
            expert_rule_ids=[rule_id for rule_id in active_rule_ids if "session" in rule_id or "ownership" in rule_id],
            downstream_spec_impact=["Assign canonical owners for stateful public types", "Generate lifecycle-oriented functions"],
            origin="rule",
            source_steps=["stage1_ir", "stage2_profile", "stage3_rules"],
        ),
        DesignDecision(
            decision_id="dec_error_policy",
            decision_type="error_policy",
            selected_option=str(profile.data.get("failure_semantics")),
            rationale=f"Error matrix implies policy '{profile.data.get('failure_semantics')}'.",
            evidence_refs=planning_ir.traceability_index.get("error_and_limits.error_matrix[0]", []),
            expert_rule_ids=[rule_id for rule_id in active_rule_ids if "error_policy" in rule_id or "close" in rule_id],
            downstream_spec_impact=["Generate shared malformed-input handling path", "Add error-path tests to implementation plan"],
            origin="rule",
            source_steps=["stage1_ir", "stage2_profile", "stage3_rules"],
        ),
        DesignDecision(
            decision_id="dec_handler_coverage",
            decision_type="handler_matrix",
            selected_option="minimum_v1_complete_mapping",
            rationale="Every minimum_v1 surface must map to a concrete handler path in the implementation plan.",
            evidence_refs=[],
            expert_rule_ids=["canonical_ownership_required"],
            downstream_spec_impact=["Generate handler matrix rows for all minimum_v1 surfaces"],
            origin="deterministic",
            source_steps=["stage1_ir", "stage6_impl_plan"],
        ),
    ]
    if facts.get("routing_model", {}).get("dispatch_keys"):
        decisions.append(
            DesignDecision(
                decision_id="dec_routing_split",
                decision_type="routing_strategy",
                selected_option="dedicated_router_module",
                rationale="Dispatch keys and routing rules justify dedicated routing ownership instead of embedding dispatch in transport or codec.",
                evidence_refs=planning_ir.traceability_index.get("routing_model.dispatch_keys[0]", []),
                expert_rule_ids=[rule_id for rule_id in active_rule_ids if "routing" in rule_id],
                downstream_spec_impact=["Create routing-facing file and function specs"],
                origin="rule",
                source_steps=["stage1_ir", "stage3_rules", "stage4_candidate_architectures"],
            )
        )
    return decisions


def build_design_decisions(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    chosen_architecture: CandidateArchitecture,
    activations: list[ExpertActivation],
    scores: list[ArchitectureScore],
    llm_client: FixedQwenClient | None,
    log: Callable[[str], None] | None = None,
    log_artifact: Callable[[str, str, str], None] | None = None,
    register_usage: Callable[[str, str, LLMUsage], None] | None = None,
) -> list[DesignDecision]:
    base = _base_decisions(planning_ir, profile, chosen_architecture, activations, scores)
    if llm_client is None:
        if log is not None:
            log(f"decisions llm skipped fallback=base decisions={len(base)}")
        return base
    try:
        messages = build_decision_enrichment_prompt(
            [item.__dict__ for item in base],
            profile.data,
            chosen_architecture.__dict__,
            [item.__dict__ for item in activations],
        )
        if log_artifact is not None:
            log_artifact("decision_enrichment_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"decisions llm_request_start messages={len(messages)}")
        response = _generate_with_usage(llm_client, messages)
        if register_usage is not None:
            register_usage("design_decisions", "generate", response.usage)
        if log is not None:
            log(
                f"decisions llm_response_done chars={len(response.content)} "
                f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
            )
        if log_artifact is not None:
            log_artifact("decision_enrichment_response", response.content, ".txt")
        payload = _load_json_payload(response.content)
        enrichments = {str(item.get("decision_id")): item for item in payload.get("decisions", []) if isinstance(item, dict)}
        merged: list[DesignDecision] = []
        for item in base:
            patch = enrichments.get(item.decision_id, {})
            merged.append(
                DesignDecision(
                    decision_id=item.decision_id,
                    decision_type=item.decision_type,
                    selected_option=str(patch.get("selected_option", item.selected_option)),
                    rationale=str(patch.get("rationale", item.rationale)),
                    evidence_refs=list(item.evidence_refs),
                    expert_rule_ids=list(item.expert_rule_ids),
                    downstream_spec_impact=[str(v) for v in patch.get("downstream_spec_impact", item.downstream_spec_impact)],
                    origin="rule_plus_llm",
                    source_steps=[str(v) for v in patch.get("source_steps", item.source_steps)],
                )
            )
        if log is not None:
            log(f"decisions llm_merge_done decisions={len(merged)}")
        return merged
    except Exception:
        if log is not None:
            log("decisions llm_failed fallback=base")
        return base
