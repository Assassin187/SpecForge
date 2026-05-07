from __future__ import annotations

from .architecture import normalize_candidate_modules
from .models import ArchitectureScore, CandidateArchitecture, ExpertActivation, PlanningIR, ProtocolProfile


def score_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    candidates: list[CandidateArchitecture],
) -> list[ArchitectureScore]:
    required_components = {component for act in activations for component in act.required_components}
    minimum_surfaces = {
        str(item.get("name"))
        for item in planning_ir.minimum_v1.get("must_support_surface", [])
        if isinstance(item, dict) and item.get("name")
    }
    scores: list[ArchitectureScore] = []
    for candidate in candidates:
        normalized_modules = normalize_candidate_modules(candidate.modules, [])
        module_text = " ".join(f"{module.get('name', '')} {module.get('role', '')}" for module in normalized_modules).lower()
        hard = 0.0
        reasons = []
        coverage = 1.0 if normalized_modules else 0.0
        hard += coverage * 25
        required_hit_count = sum(1 for component in required_components if component.replace("_", " ") in module_text or component in module_text)
        hard += min(25.0, required_hit_count * 6.0)
        if str(profile.data.get("transport_shape")) == "stream" and "codec" in module_text:
            hard += 10
            reasons.append("Includes codec separation for stream parsing")
        if minimum_surfaces:
            hard += min(20.0, len(minimum_surfaces) / max(1, len(minimum_surfaces)) * 20.0)
            reasons.append("Assumes full minimum_v1 surface coverage planning")
        if "app" in module_text or "broker" in module_text or "client" in module_text:
            hard += 10
            reasons.append("Contains composition/root module")
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
                breakdown={"coverage": coverage * 25, "required_components": min(25.0, required_hit_count * 6.0), "coder_fit": soft},
                reasons=reasons,
                selected=False,
            )
        )
    if scores:
        best_id = max(scores, key=lambda item: item.total_score).candidate_id
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
