from __future__ import annotations

import json
from typing import Any

from .constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN, MODULE_BUDGET_TARGET, MODULE_PREFERRED_MAX, MODULE_PREFERRED_MIN


SYSTEM_PROMPT = """You are an evidence-grounded protocol implementation planner.

Rules:
- Output JSON only, with no markdown fences.
- Respect the provided rule activations and protocol facts.
- Do not invent facts that are not supported by evidence or upstream deterministic stages.
- Prefer implementation decisions that are explicit, testable, and coder-compatible.
- Keep the planner protocol-agnostic: derive all protocol-specific names and behavior only from the provided inputs.
"""


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def build_architecture_prompt(
    protocol_profile: dict[str, Any],
    target_profile: dict[str, Any],
    activations: list[dict[str, Any]],
    generation_strategy: str,
    generation_round: int,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Produce JSON with key 'candidate'. Return exactly one candidate architecture. "
                "The candidate must include candidate_id, title, summary, modules, thread_model, "
                "component_relationships, strengths, and risks. Each module must include name, role, "
                "owned_capabilities, and evidence_refs.\n\n"
                f"Generation round: {generation_round}. Generation strategy: {generation_strategy}.\n"
                f"Hard module-count safety range: minimum {MODULE_BUDGET_MIN}, maximum {MODULE_BUDGET_MAX}. "
                f"Preferred typical range: {MODULE_PREFERRED_MIN}-{MODULE_PREFERRED_MAX}, target {MODULE_BUDGET_TARGET}. "
                "The preferred range is guidance, not a proof of quality; justify any smaller or larger architecture "
                "through clearer ownership or better minimum-scope fit. "
                "Prefer broad implementation modules over micro-modules. Treat rule activations as required "
                "capabilities to assign, not as module names to copy. A capability can be owned by a larger "
                "runtime, codec, semantic-handler, state/resource, routing/dispatch, or role-composition module. "
                "Every capability listed in protocol_profile.required_capabilities, including role_composition, "
                "must appear as an exact string in at least one module's owned_capabilities; the role field alone "
                "does not satisfy capability coverage. "
                "Use module names built from protocol/profile/capability terms or generic architecture terms, "
                "so later verification can trace each boundary to the current facts and target profile. "
                "Do not create a separate module for every helper, buffer, timer, error policy, or type registry "
                "unless the input facts require an independently owned lifecycle.\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"target_profile:\n{_json(target_profile)}\n\n"
                f"expert_activations:\n{_json(activations)}"
            ),
        },
    ]


def build_architecture_judge_prompt(
    planning_ir_summary: dict[str, Any],
    protocol_profile: dict[str, Any],
    target_profile: dict[str, Any],
    activations: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    lint_results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "You are the architecture judge for the planning agent. Evaluate all candidate architectures "
                "for engineering quality and select at most one. Return JSON only with key 'architecture_review'. "
                "The review must include selected_candidate_id, rejection_reason, and scores. Each score item must "
                "include candidate_id, verdict ('accept', 'reject', or 'needs_revision'), total_score from 0-100, "
                "scores, major_issues, rationale, and recommended_selection.\n\n"
                "Rubric scores must include protocol_fit, role_composition_fit, capability_ownership_semantics, "
                "module_cohesion, separation_of_concerns, coder_usability, traceability, and "
                "module_count_reasonableness. Do not select a candidate only because capability tokens are covered. "
                "Judge whether each capability is owned by the right kind of module. In particular, role_composition "
                "belongs on a target-role orchestration boundary such as an app/core/session/semantic/handler module; "
                "it is usually misplaced on a local codec, type registry, or protocol-error-only module. "
                f"The hard module-count safety range is {MODULE_BUDGET_MIN}-{MODULE_BUDGET_MAX}; "
                f"the preferred typical range is {MODULE_PREFERRED_MIN}-{MODULE_PREFERRED_MAX}. "
                "A count outside the preferred range can be accepted if the architecture is better justified. "
                "Treat deterministic lint as evidence: hard lint failures are serious, but your job is to assess "
                "engineering semantics, cohesion, and downstream coder usability.\n\n"
                f"planning_ir_summary:\n{_json(planning_ir_summary)}\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"target_profile:\n{_json(target_profile)}\n\n"
                f"expert_activations:\n{_json(activations)}\n\n"
                f"candidates:\n{_json(candidates)}\n\n"
                f"deterministic_lint_results:\n{_json(lint_results)}"
            ),
        },
    ]


def build_implementation_plan_prompt(
    planning_ir: dict[str, Any],
    protocol_profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Refine the provided implementation plan scaffold without changing its top-level shape. "
                "Return JSON object only. Preserve protocol agnosticism: derive behavior from facts/profile, "
                "target profile, selected architecture, and rule-grounded capabilities. If you modify module_graph, keep "
                f"the module count between {MODULE_BUDGET_MIN} and {MODULE_BUDGET_MAX} and include "
                "owned_capabilities/evidence_refs on each module.\n\n"
                f"planning_ir_summary:\n{_json(planning_ir)}\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"chosen_architecture:\n{_json(chosen_architecture)}"
            ),
        },
    ]


def build_dependency_graph_prompt(
    planning_ir: dict[str, Any],
    protocol_profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    implementation_plan: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Produce JSON with key 'dependency_graph'. Do not change module boundaries, handler names, "
                "or canonical type ownership. Describe implementation dependencies at three levels: "
                "module_edges, interface_contracts, function_edges, and data_edges. Every module edge must include "
                "consumer_module, provider_module, dependency_kind, required_capabilities, reason, evidence_refs, "
                "and decision_refs from implementation_plan.traceability.decision_ids when applicable. Distinguish compile-time include dependencies from runtime orchestration "
                "relationships in dependency_kind/reason. Use only modules, capabilities, handlers, and types that "
                "appear in the provided implementation plan. If a dependency is uncertain, omit it rather than "
                "inventing an unsupported edge.\n\n"
                f"planning_ir_summary:\n{_json(planning_ir)}\n\n"
                f"protocol_profile:\n{_json(protocol_profile)}\n\n"
                f"chosen_architecture:\n{_json(chosen_architecture)}\n\n"
                f"implementation_plan:\n{_json(implementation_plan)}"
            ),
        },
    ]


def build_file_layout_prompt(
    layout_context: dict[str, Any],
    validation_errors: list[str] | None = None,
) -> list[dict[str, str]]:
    retry_text = ""
    if validation_errors:
        retry_text = (
            "\n\nPrevious file_layout proposal was rejected by deterministic validation. "
            "Regenerate the full file_layout JSON, fixing these errors only:\n"
            f"{_compact_json(validation_errors)}"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Produce JSON with key 'file_layout'. Split each existing module into realistic C files. "
                "Do not change module boundaries, capability ownership, handler names, canonical type ownership, "
                "or dependency graph semantics. A simple module may remain a single source/header pair, but modules "
                "with multiple responsibilities, many handlers, or clear decode/encode/state/store/router/runtime "
                "sub-responsibilities should be split into multiple source units. "
                "Keep the layout coder-compatible: file_layout.files must contain source .c files only. "
                "Never create a header-only file entry, never put a .h path in file.path/source_path, and never use "
                "a header file_id as owns_header. A source file may own one actual .h path via owns_header/header_path, "
                "and the same header path must not be owned by multiple source file entries. "
                "If a module needs a public header, attach it directly to that module's public API source file. "
                "Return file_layout.files, file_layout.function_placement, and file_layout.unresolved_layout_questions. "
                "file_layout.file_edges will be derived deterministically from the accepted files, function placements, "
                "and layout_context.dependency_graph, so omit it or return an empty array. Each file item must include file_id, module, path, "
                "file_kind, visibility, role, owns_header, defines_functions, declares_symbols, uses_types, "
                "evidence_refs, and decision_refs. "
                "If functions_by_module contains an entrypoint named main or unit=main, place it in a source-only main.c file. "
                "Every generated function listed in layout_context.functions_by_module must appear exactly once in "
                "function_placement. "
                "Use the compact context only; do not invent modules, files, functions, public types, or graph edge ids. "
                "If uncertain, prefer a conservative single-file layout for that module rather than inventing unsupported source units."
                f"{retry_text}\n\n"
                f"layout_context:\n{_compact_json(layout_context)}"
            ),
        },
    ]
