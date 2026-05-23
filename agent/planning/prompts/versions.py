from __future__ import annotations


PROMPT_VERSION = "planning/prompts/v1"


PROMPT_REGISTRY = {
    "protocol_profile_patch_prompt": PROMPT_VERSION,
    "architecture_candidate_prompt": PROMPT_VERSION,
    "architecture_ranking_prompt": PROMPT_VERSION,
    "core_design_candidate_prompt": PROMPT_VERSION,
    "module_contracts_candidate_prompt": PROMPT_VERSION,
    "module_artifacts_candidate_prompt": PROMPT_VERSION,
    "function_inventory_candidate_prompt": PROMPT_VERSION,
    "function_signature_patch_prompt": PROMPT_VERSION,
    "function_behavior_contract_patch_prompt": PROMPT_VERSION,
    "wire_access_binding_patch_prompt": PROMPT_VERSION,
    "calls_allowed_candidate_prompt": PROMPT_VERSION,
    "runtime_entrypoint_candidate_prompt": PROMPT_VERSION,
    "file_layout_candidate_prompt": PROMPT_VERSION,
    "dependency_repair_patch_prompt": PROMPT_VERSION,
    "validation_explanation_prompt": PROMPT_VERSION,
}
