from __future__ import annotations

from typing import Any

from .models import ArchitectureScore, CandidateArchitecture, DesignDecision, ExpertActivation, PlanningIR, ProtocolProfile


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
            rationale=f"Selected judge-accepted candidate '{chosen_architecture.title}' with score {selected_score.total_score if selected_score else 0:.1f}.",
            evidence_refs=[],
            expert_rule_ids=active_rule_ids,
            downstream_spec_impact=["Use selected module graph as implementation backbone"],
            origin="llm_judge",
            source_steps=["stage4_candidate_architectures", "stage4_architecture_judge"],
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

def derive_design_decisions(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    chosen_architecture: CandidateArchitecture,
    activations: list[ExpertActivation],
    scores: list[ArchitectureScore],
    implementation_plan: dict[str, Any],
) -> list[DesignDecision]:
    decisions = _base_decisions(planning_ir, profile, chosen_architecture, activations, scores)
    error_policy = str(implementation_plan.get("error_strategy", {}).get("policy", "")).strip()
    handler_count = len([item for item in implementation_plan.get("handler_matrix", []) if isinstance(item, dict)])
    surface_count = len([item for item in implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", []) if isinstance(item, dict)])
    derived: list[DesignDecision] = []
    for item in decisions:
        selected_option = item.selected_option
        rationale = item.rationale
        origin = "derived_from_implementation_plan"
        source_steps = list(dict.fromkeys([*item.source_steps, "stage7_implementation_plan"]))
        if item.decision_type == "error_policy" and error_policy:
            selected_option = error_policy
            rationale = f"Implementation plan fixed error_strategy.policy='{error_policy}' from protocol profile and facts."
        elif item.decision_type == "handler_matrix":
            selected_option = f"{handler_count}_handlers_for_{surface_count}_minimum_surfaces"
            rationale = "Implementation plan fixed the minimum_v1 surface to handler_matrix mapping."
        derived.append(
            DesignDecision(
                decision_id=item.decision_id,
                decision_type=item.decision_type,
                selected_option=selected_option,
                rationale=rationale,
                evidence_refs=list(item.evidence_refs),
                expert_rule_ids=list(item.expert_rule_ids),
                downstream_spec_impact=list(item.downstream_spec_impact),
                origin=origin,
                source_steps=source_steps,
            )
        )

    dependency_graph = implementation_plan.get("dependency_graph", {})
    if isinstance(dependency_graph, dict):
        validation = dependency_graph.get("validation", {})
        edge_count = len([item for item in dependency_graph.get("module_edges", []) if isinstance(item, dict)])
        downstream_impact = [
            "Drive module_graph dependencies",
            "Drive blueprint source dependencies, RELY context, and call contracts",
        ]
        if isinstance(validation, dict) and validation.get("dropped_cycle_or_duplicate_count"):
            downstream_impact.append("Dropped cycle-prone or duplicate dependency candidates")
        derived.append(
            DesignDecision(
                decision_id="dec_dependency_graph",
                decision_type="dependency_graph",
                selected_option=f"{edge_count}_module_edges",
                rationale="Implementation plan fixed module/interface/function/data dependencies in dependency_graph.",
                evidence_refs=[],
                expert_rule_ids=[],
                downstream_spec_impact=downstream_impact,
                origin="derived_from_implementation_plan",
                source_steps=["stage7_implementation_plan", "stage8_dependency_graph"],
            )
        )
    file_layout = implementation_plan.get("file_layout", {})
    if isinstance(file_layout, dict):
        validation = file_layout.get("validation", {})
        file_count = len([item for item in file_layout.get("files", []) if isinstance(item, dict)])
        edge_count = len([item for item in file_layout.get("file_edges", []) if isinstance(item, dict)])
        downstream_impact = [
            "Drive module_graph files and artifacts",
            "Drive blueprint file specs, header/source dependencies, and dependency projections",
        ]
        if isinstance(validation, dict) and validation.get("fallback_used"):
            downstream_impact.append("Used deterministic fallback or repair for invalid LLM file layout")
        derived.append(
            DesignDecision(
                decision_id="dec_file_layout",
                decision_type="file_layout",
                selected_option=f"{file_count}_files_{edge_count}_file_edges",
                rationale="Implementation plan fixed module-internal source/header layout in file_layout.",
                evidence_refs=[],
                expert_rule_ids=[],
                downstream_spec_impact=downstream_impact,
                origin="derived_from_implementation_plan",
                source_steps=["stage7_implementation_plan", "stage9_file_layout"],
            )
        )
    return derived
