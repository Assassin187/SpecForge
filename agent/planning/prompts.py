from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You are an evidence-grounded protocol implementation planner.

Rules:
- Output JSON only, with no markdown fences.
- Respect the provided rule activations and protocol facts.
- Do not invent facts that are not supported by evidence or upstream deterministic stages.
- Prefer implementation decisions that are explicit, testable, and coder-compatible.
"""


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_architecture_prompt(protocol_profile: dict[str, Any], target_profile: dict[str, Any], activations: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Produce JSON with key 'candidates'. Return up to 3 candidate architectures. "
                "Each candidate must include candidate_id, title, summary, modules, thread_model, "
                "component_relationships, strengths, and risks.\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"target_profile:\n{_json(target_profile)}\n\n"
                f"expert_activations:\n{_json(activations)}"
            ),
        },
    ]


def build_decision_enrichment_prompt(
    base_decisions: list[dict[str, Any]],
    protocol_profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    activations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "You are enriching an existing decision graph. "
                "Do not change selected_option unless the provided data is inconsistent. "
                "Return JSON with key 'decisions'. Each output item must include decision_id, rationale, "
                "downstream_spec_impact, and optional source_steps.\n\n"
                f"base_decisions:\n{_json(base_decisions)}\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"chosen_architecture:\n{_json(chosen_architecture)}\n\n"
                f"expert_activations:\n{_json(activations)}"
            ),
        },
    ]


def build_implementation_plan_prompt(
    planning_ir: dict[str, Any],
    protocol_profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Refine the provided implementation plan scaffold without changing its top-level shape. "
                "Return JSON object only.\n\n"
                f"planning_ir_summary:\n{_json(planning_ir)}\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"chosen_architecture:\n{_json(chosen_architecture)}\n\n"
                f"design_decisions:\n{_json(decisions)}"
            ),
        },
    ]
