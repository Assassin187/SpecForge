from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .adapters.facts_input import build_planning_ir
from .adapters.target_profile import load_target_profile
from .artifact_io import ArtifactStore, file_sha256, read_json, run_timestamp, safe_slug
from .config import PlanningConfig
from .diagnostics import PlanningDiagnostic, diagnostics_to_dict, has_errors
from .llm_boundary import request_json_candidate
from .models import PlanningResult, TargetProfile
from .prompts.templates import (
    architecture_candidate_messages,
    architecture_ranking_messages,
    calls_allowed_candidate_messages,
    core_design_candidate_messages,
    dependency_repair_patch_messages,
    file_layout_override_patch_messages,
    function_annotation_candidate_messages,
    function_behavior_contract_patch_messages,
    function_signature_patch_messages,
    module_artifacts_candidate_messages,
    protocol_profile_patch_messages,
    runtime_entrypoint_candidate_messages,
    type_filling_candidate_messages,
    wire_access_binding_patch_messages,
)
from .stages.architecture import build_architecture_context, deterministic_architecture_ranking, select_architecture
from .stages.constraints import activate_constraints
from .stages.dependencies import attach_coder_dependency_sources, build_dependency_validation_report
from .stages.implementation_plan_context import (
    build_calls_allowed_context,
    build_core_design_context,
    build_dependency_repair_context,
    build_file_layout_override_context,
    build_function_behavior_context,
    build_function_inventory_context,
    build_function_signature_context,
    build_module_artifact_context,
    build_runtime_entrypoint_context,
    build_type_inventory_context,
    build_wire_access_binding_context,
    compact_function_annotation_context,
    compact_type_filling_context,
    normalize_function_behavior_contract_patch,
)
from .stages.inventory_planning_space import build_function_planning_space, build_type_planning_space
from .stages.inventory_reconciliation import reconcile_function_annotation_candidate, reconcile_type_filling_candidate
from .stages.layout_runtime_mapping import build_layout_runtime_mapping_report
from .stages.implementation_plan_merger import (
    apply_dependency_repair_patch,
    apply_deterministic_dependency_fallback,
    apply_file_layout_override_patch,
    build_plan_skeleton,
    fallback_calls_allowed,
    fallback_core_design,
    fallback_dependency_repair_patch,
    fallback_file_layout,
    fallback_function_behavior,
    fallback_function_signatures,
    fallback_module_artifacts,
    fallback_runtime_entrypoint,
    fallback_wire_access_binding,
    cleanup_final_unresolved_questions,
    finalize_dependency_graph,
    merge_calls_allowed,
    merge_core_design,
    merge_file_layout,
    merge_function_behavior,
    merge_function_inventory,
    merge_function_signatures,
    merge_module_artifacts,
    merge_runtime_entrypoint,
    merge_type_inventory,
    merge_wire_access_binding,
    normalize_calls_allowed_aggregate,
    normalize_calls_allowed_candidate,
    normalize_file_layout_candidate,
    normalize_function_signature_patch,
    repair_function_inventory_symbols,
    reconcile_type_inventory_function_refs,
)
from .stages.preflight import build_manifest, validate_input_paths
from .stages.protocol_profile import apply_protocol_profile_patch_candidate, build_protocol_profile
from .stages.specs_compiler import compile_spec_bundle
from .token_usage import TokenUsageTracker
from .validators.architecture import validate_architecture_candidates, validate_architecture_ranking, validate_selected_architecture
from .validators.coder_compat import validate_coder_compatibility
from .validators.constraints import validate_constraints
from .validators.dependencies import validate_dependency_graph
from .validators.implementation_plan_stages import (
    stage_passed,
    validate_calls_allowed_candidate,
    validate_core_design_candidate,
    validate_dependency_repair_patch,
    validate_file_layout_override_patch,
    validate_file_layout_candidate,
    validate_full_implementation_plan,
    validate_function_annotation_candidate,
    validate_function_behavior_contract_patch,
    validate_function_inventory_candidate,
    validate_function_signature_patch,
    validate_module_artifacts_candidate,
    validate_plan_skeleton,
    validate_runtime_entrypoint_candidate,
    validate_type_filling_candidate,
    validate_type_inventory_candidate,
    validate_wire_access_binding_patch,
    validation_report,
    function_inventory_decomposition_report,
)
from .validators.llm_outputs import validate_protocol_profile_patch_candidate
from .validators.planning_ir import validate_planning_ir
from .validators.profile import validate_protocol_profile


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "agent" / "planning" / "out"
ARCHITECTURE_DESIGN_STRATEGIES = (
    "capability_clustered",
    "layered_runtime_codec_semantic",
    "minimal_scope",
)


STEP_FILENAMES = {
    "planning_run_manifest": "000_planning_run_manifest.json",
    "planning_ir": "003_planning_ir.json",
    "protocol_profile_patch_candidate": "004_protocol_profile_patch_candidate.json",
    "protocol_profile": "004_protocol_profile.json",
    "engineering_constraints": "005_engineering_constraints.json",
    "architecture_context": "006_architecture_context.json",
    "architecture_candidates": "006_architecture_candidates.json",
    "architecture_ranking": "006_architecture_ranking.json",
    "selected_architecture": "006_selected_architecture.json",
    "implementation_plan_skeleton": "007_5_1_plan_skeleton.json",
    "core_design_candidate": "007_5_2a_core_design_candidate.json",
    "core_design_validation_report": "007_5_2a_core_design_validation_report.json",
    "module_artifacts_candidate": "007_5_2b_module_artifacts_candidate.json",
    "module_artifacts_validation_report": "007_5_2b_module_artifacts_validation_report.json",
    "type_planning_space": "007_5_3_type_data_planning_space.json",
    "type_inventory_candidate": "007_5_3_type_data_inventory_candidate.json",
    "type_inventory_validation_report": "007_5_3_type_data_inventory_validation_report.json",
    "type_reconciliation_report": "007_5_3_type_data_reconciliation_report.json",
    "type_inventory_diagnostics": "007_5_3_type_data_inventory_diagnostics.json",
    "type_obligations": "007_5_3_type_data_obligations.json",
    "public_type_obligation_report": "007_5_3_public_type_obligation_report.json",
    "type_inventory_attempt_summary": "007_5_3_type_data_attempt_summary.json",
    "function_planning_space": "007_5_4a_function_inventory_planning_space.json",
    "function_inventory_candidate": "007_5_4a_function_inventory_candidate.json",
    "function_inventory_validation_report": "007_5_4a_function_inventory_validation_report.json",
    "function_reconciliation_report": "007_5_4a_function_inventory_reconciliation_report.json",
    "function_inventory_diagnostics": "007_5_4a_function_inventory_diagnostics.json",
    "function_inventory_attempt_summary": "007_5_4a_function_inventory_attempt_summary.json",
    "function_signature_patch": "007_5_4b_function_signature_patch.json",
    "function_signature_validation_report": "007_5_4b_function_signature_validation_report.json",
    "function_behavior_patch": "007_5_4c_function_behavior_contract_patch.json",
    "function_behavior_validation_report": "007_5_4c_function_behavior_validation_report.json",
    "wire_access_binding_patch": "007_5_4d_function_wire_access_binding_patch.json",
    "wire_access_binding_validation_report": "007_5_4d_function_wire_access_binding_validation_report.json",
    "calls_allowed_candidate": "007_5_4e_function_call_contracts_candidate.json",
    "calls_allowed_validation_report": "007_5_4e_function_call_contracts_validation_report.json",
    "file_layout_candidate": "007_5_5a_file_layout_candidate.json",
    "file_layout_validation_report": "007_5_5a_file_layout_validation_report.json",
    "runtime_entrypoint_candidate": "007_5_5b_runtime_entrypoint_candidate.json",
    "runtime_entrypoint_validation_report": "007_5_5b_runtime_entrypoint_validation_report.json",
    "dependency_repair_patch": "007_5_6_dependency_repair_patch.json",
    "dependency_repair_validation_report": "007_5_6_dependency_repair_validation_report.json",
    "implementation_plan": "007_implementation_plan.json",
    "dependency_validation_report": "008_dependency_validation_report.json",
    "layout_runtime_mapping_report": "009_layout_runtime_mapping_report.json",
    "planning_repair_statistics": "012_planning_repair_statistics.json",
    "token_usage_summary": "013_token_usage_summary.json",
    "planning_validation_report": "014_planning_validation_report.json",
}

AGENT_LOG_ARTIFACT_KEYS = {
    "protocol_profile_patch_candidate",
    "type_inventory_attempt_summary",
    "function_inventory_attempt_summary",
    "dependency_repair_patch",
}

TOP_LEVEL_RESUME_STAGES = (
    "planning_ir",
    "protocol_profile",
    "engineering_constraints",
    "architecture",
    "implementation_plan",
    "specs_compile",
)
IMPLEMENTATION_PLAN_RESUME_STAGES = (
    "implementation_plan_5_1",
    "implementation_plan_5_2a",
    "implementation_plan_5_2b",
    "implementation_plan_5_3",
    "implementation_plan_5_4a",
    "implementation_plan_5_4b",
    "implementation_plan_5_4c",
    "implementation_plan_5_4d",
    "implementation_plan_5_4e",
    "implementation_plan_5_5a",
    "implementation_plan_5_5b",
    "implementation_plan_5_6",
    "implementation_plan_5_7",
)
RESUME_STAGES = (*TOP_LEVEL_RESUME_STAGES, *IMPLEMENTATION_PLAN_RESUME_STAGES)
RESUME_STAGE_ALIASES = {
    "5.1": "implementation_plan_5_1",
    "5.1_plan_skeleton": "implementation_plan_5_1",
    "implementation_plan_skeleton": "implementation_plan_5_1",
    "5.2a": "implementation_plan_5_2a",
    "5.2a_core_design": "implementation_plan_5_2a",
    "core_design": "implementation_plan_5_2a",
    "5.2b": "implementation_plan_5_2b",
    "5.2b_module_artifacts": "implementation_plan_5_2b",
    "5.3": "implementation_plan_5_3",
    "5.3_type_data": "implementation_plan_5_3",
    "module_artifacts": "implementation_plan_5_2b",
    "type_inventory": "implementation_plan_5_3",
    "5.4a": "implementation_plan_5_4a",
    "5.4a_function_inventory": "implementation_plan_5_4a",
    "5.4b": "implementation_plan_5_4b",
    "5.4b_function_signatures": "implementation_plan_5_4b",
    "function_inventory": "implementation_plan_5_4a",
    "5.4c": "implementation_plan_5_4c",
    "5.4c_behavior_contract": "implementation_plan_5_4c",
    "function_signatures": "implementation_plan_5_4b",
    "5.4d": "implementation_plan_5_4d",
    "5.4d_wire_access_binding": "implementation_plan_5_4d",
    "function_behavior": "implementation_plan_5_4c",
    "5.4e": "implementation_plan_5_4e",
    "5.4e_call_contracts": "implementation_plan_5_4e",
    "wire_access_binding": "implementation_plan_5_4d",
    "calls_allowed": "implementation_plan_5_4e",
    "call_contracts": "implementation_plan_5_4e",
    "5.5a": "implementation_plan_5_5a",
    "5.5a_file_layout": "implementation_plan_5_5a",
    "file_layout": "implementation_plan_5_5a",
    "5.5b": "implementation_plan_5_5b",
    "5.5b_runtime_entrypoint": "implementation_plan_5_5b",
    "runtime_entrypoint": "implementation_plan_5_5b",
    "5.6": "implementation_plan_5_6",
    "5.6_dependency_generation": "implementation_plan_5_6",
    "5.6_dependency_repair": "implementation_plan_5_6",
    "dependency_repair": "implementation_plan_5_6",
    "5.7": "implementation_plan_5_7",
    "5.7_spec_readiness": "implementation_plan_5_7",
    "spec_readiness": "implementation_plan_5_7",
    "6": "specs_compile",
    "6_specs_compile": "specs_compile",
    "coder_specs_compile": "specs_compile",
}
RESUME_STAGE_OPTIONS = tuple(dict.fromkeys((*RESUME_STAGES, *RESUME_STAGE_ALIASES)))
STOP_AFTER_STAGE_OPTIONS = RESUME_STAGE_OPTIONS
_STAGE_ORDER = {stage: index for index, stage in enumerate(TOP_LEVEL_RESUME_STAGES)}
_IMPLEMENTATION_PLAN_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(IMPLEMENTATION_PLAN_RESUME_STAGES)
}
_ARCHITECTURE_ARTIFACT_KEYS = (
    "architecture_context",
    "architecture_candidates",
    "architecture_ranking",
    "selected_architecture",
)
_IMPLEMENTATION_PLAN_PREFIX_KEYS = (
    "planning_ir",
    "protocol_profile",
    "engineering_constraints",
    *_ARCHITECTURE_ARTIFACT_KEYS,
)
_IMPLEMENTATION_PLAN_5_2A_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_PREFIX_KEYS,
    "implementation_plan_skeleton",
)
_IMPLEMENTATION_PLAN_5_2B_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_2A_PREFIX_KEYS,
    "core_design_candidate",
    "core_design_validation_report",
)
_IMPLEMENTATION_PLAN_5_3_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_2B_PREFIX_KEYS,
    "module_artifacts_candidate",
    "module_artifacts_validation_report",
)
_IMPLEMENTATION_PLAN_5_4A_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_3_PREFIX_KEYS,
    "type_inventory_candidate",
    "type_inventory_validation_report",
)
_IMPLEMENTATION_PLAN_5_4B_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_4A_PREFIX_KEYS,
    "function_inventory_candidate",
    "function_inventory_validation_report",
)
_IMPLEMENTATION_PLAN_5_4C_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_4B_PREFIX_KEYS,
    "function_signature_patch",
    "function_signature_validation_report",
)
_IMPLEMENTATION_PLAN_5_4D_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_4C_PREFIX_KEYS,
    "function_behavior_patch",
    "function_behavior_validation_report",
)
_IMPLEMENTATION_PLAN_5_4E_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_4D_PREFIX_KEYS,
    "wire_access_binding_patch",
    "wire_access_binding_validation_report",
)
_IMPLEMENTATION_PLAN_5_5A_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_4E_PREFIX_KEYS,
    "calls_allowed_candidate",
    "calls_allowed_validation_report",
)
_IMPLEMENTATION_PLAN_5_5B_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_5A_PREFIX_KEYS,
    "file_layout_candidate",
    "file_layout_validation_report",
)
_IMPLEMENTATION_PLAN_5_6_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_5B_PREFIX_KEYS,
    "runtime_entrypoint_candidate",
    "runtime_entrypoint_validation_report",
)
_IMPLEMENTATION_PLAN_5_7_PREFIX_KEYS = (
    *_IMPLEMENTATION_PLAN_5_6_PREFIX_KEYS,
    "implementation_plan",
)
_RESUME_REQUIRED_KEYS = {
    "planning_ir": (),
    "protocol_profile": ("planning_ir",),
    "engineering_constraints": ("planning_ir", "protocol_profile"),
    "architecture": ("planning_ir", "protocol_profile", "engineering_constraints"),
    "implementation_plan": _IMPLEMENTATION_PLAN_PREFIX_KEYS,
    "implementation_plan_5_1": _IMPLEMENTATION_PLAN_PREFIX_KEYS,
    "implementation_plan_5_2a": _IMPLEMENTATION_PLAN_5_2A_PREFIX_KEYS,
    "implementation_plan_5_2b": _IMPLEMENTATION_PLAN_5_2B_PREFIX_KEYS,
    "implementation_plan_5_3": _IMPLEMENTATION_PLAN_5_3_PREFIX_KEYS,
    "implementation_plan_5_4a": _IMPLEMENTATION_PLAN_5_4A_PREFIX_KEYS,
    "implementation_plan_5_4b": _IMPLEMENTATION_PLAN_5_4B_PREFIX_KEYS,
    "implementation_plan_5_4c": _IMPLEMENTATION_PLAN_5_4C_PREFIX_KEYS,
    "implementation_plan_5_4d": _IMPLEMENTATION_PLAN_5_4D_PREFIX_KEYS,
    "implementation_plan_5_4e": _IMPLEMENTATION_PLAN_5_4E_PREFIX_KEYS,
    "implementation_plan_5_5a": _IMPLEMENTATION_PLAN_5_5A_PREFIX_KEYS,
    "implementation_plan_5_5b": _IMPLEMENTATION_PLAN_5_5B_PREFIX_KEYS,
    "implementation_plan_5_6": _IMPLEMENTATION_PLAN_5_6_PREFIX_KEYS,
    "implementation_plan_5_7": _IMPLEMENTATION_PLAN_5_7_PREFIX_KEYS,
    "specs_compile": (
        "planning_ir",
        "protocol_profile",
        "engineering_constraints",
        *_ARCHITECTURE_ARTIFACT_KEYS,
        "implementation_plan",
        "dependency_validation_report",
    ),
}


def normalize_resume_stage(stage: str | None) -> str | None:
    if stage is None:
        return None
    normalized = str(stage).strip()
    return RESUME_STAGE_ALIASES.get(normalized, normalized)


def normalize_stop_after_stage(stage: str | None) -> str | None:
    return normalize_resume_stage(stage)


def _llm_config_for_stage(config: PlanningConfig, stage: str) -> PlanningConfig:
    return replace(
        config,
        llm_temperature=config.llm_temperature_for(stage),
        llm_top_p=config.llm_top_p_for(stage),
        llm_max_completion_tokens=config.llm_max_completion_tokens_for(stage),
        llm_max_retries=config.llm_max_retries_for(stage),
    )


def _diagnostic_reasons(diagnostics: list[PlanningDiagnostic]) -> list[str]:
    return [f"{diag.level}:{diag.code}: {diag.message}" for diag in diagnostics]


def _file_layout_override_actionable(candidate: dict[str, Any], diagnostics: list[PlanningDiagnostic]) -> bool:
    if has_errors(diagnostics):
        return True
    mechanical = [diag for diag in diagnostics if diag.code == "mechanical_single_file_module_layout"]
    if not mechanical:
        return False
    file_counts: dict[str, int] = {}
    for file_item in candidate.get("files", []):
        if isinstance(file_item, dict):
            module_id = str(file_item.get("module_id", ""))
            file_counts[module_id] = file_counts.get(module_id, 0) + 1
    for diag in mechanical:
        marker = "module '"
        module_id = ""
        if marker in diag.message:
            module_id = diag.message.split(marker, 1)[1].split("'", 1)[0]
        if not module_id or file_counts.get(module_id, 0) > 1:
            return True
    return False


def _retry_messages(base_messages: list[dict[str, str]], previous_reasons: list[str], attempt: int) -> list[dict[str, str]]:
    if not previous_reasons:
        return base_messages
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"Retry attempt {attempt}. The previous LLM output was rejected by deterministic validation. "
                "Return a corrected JSON object only. The first character must be '{' and the last character must be '}'. "
                "Do not include markdown, prose, headings, comments, or analysis. Rejection reasons:\n"
                + "\n".join(f"- {reason}" for reason in previous_reasons)
            ),
        },
    ]


_INVENTORY_JSON_RETRIES = 2


def _json_retry_messages(base_messages: list[dict[str, str]], previous_reasons: list[str], retry_attempt: int) -> list[dict[str, str]]:
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"JSON retry attempt {retry_attempt}. The previous response was not a valid JSON object. "
                "Return strict standard JSON only: the first character must be '{' and the last character must be '}'. "
                "Do not include markdown, prose, headings, comments, trailing commas, or analysis. Previous JSON errors:\n"
                + "\n".join(f"- {reason}" for reason in previous_reasons)
            ),
        },
    ]


def _controlled_inventory_stats(
    *,
    candidate_records: list[dict[str, Any]],
    accepted_by: str | None,
    final_failure_code: str | None,
    reconciliation: dict[str, Any] | None,
) -> dict[str, Any]:
    report = reconciliation.get("reconciliation_report", {}) if isinstance(reconciliation, dict) else {}
    accepted_optional = report.get("accepted_optional_types", report.get("accepted_optional_functions", [])) if isinstance(report, dict) else []
    rejected_optional = report.get("rejected_optional_types", report.get("rejected_optional_functions", [])) if isinstance(report, dict) else []
    return {
        "candidate_attempt_count": 1 if candidate_records else 0,
        "json_retry_count": sum(1 for record in candidate_records if int(record.get("json_attempt", 1) or 1) > 1),
        "full_retry_count": 0,
        "validator_repair_count": 0,
        "accepted_by": accepted_by,
        "final_failure_code": final_failure_code,
        "accepted_optional_count": len(accepted_optional or []),
        "rejected_optional_count": len(rejected_optional or []),
        "repair_failures": [],
    }


def _run_controlled_inventory_candidate(
    *,
    module_index: int,
    module: dict[str, Any],
    stage_name: str,
    stage_key: str,
    prompt_name: str,
    messages: list[dict[str, str]],
    request_config: PlanningConfig,
    enable_thinking: bool,
    empty_llm_candidate: Callable[[], dict[str, Any]],
    validate_llm_candidate: Callable[[dict[str, Any]], list[PlanningDiagnostic]],
    reconcile_candidate: Callable[[dict[str, Any]], dict[str, Any]],
    validate_final_candidate: Callable[[dict[str, Any]], list[PlanningDiagnostic]],
    log_event: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    module_id = str(module.get("module_id", ""))
    stage_label = f"{stage_name}:{module_id}"
    candidate_records: list[dict[str, Any]] = []
    previous_json_reasons: list[str] = []
    raw_candidate: dict[str, Any] | None = None
    llm_candidate_diags: list[PlanningDiagnostic] = []
    request_index = 0
    accepted_by = "llm_semantic_candidate"

    def emit(message: str) -> None:
        if log_event is not None:
            log_event(message)

    for json_attempt in range(1, _INVENTORY_JSON_RETRIES + 2):
        request_index += 1
        attempt_messages = messages if json_attempt == 1 else _json_retry_messages(messages, previous_json_reasons, json_attempt - 1)
        mode = "json_retry" if json_attempt > 1 else "candidate"
        emit(
            f"stage=implementation_plan substage={stage_label} llm_attempt={request_index} "
            f"candidate_attempt=1 json_attempt={json_attempt} mode={mode} prompt={prompt_name} "
            f"event=request_sent temperature={request_config.llm_temperature} "
            f"thinking={str(enable_thinking).lower()}"
        )
        raw_candidate, llm_diags, meta = request_json_candidate(
            prompt_name=prompt_name,
            messages=attempt_messages,
            config=request_config,
            enable_thinking=enable_thinking,
        )
        record: dict[str, Any] = {
            "request_index": request_index,
            "candidate_attempt": 1,
            "json_attempt": json_attempt,
            "meta": meta,
            "accepted": False,
            "validated": False,
            "validator_error": False,
            "rejection_reasons": [],
        }
        if raw_candidate is None:
            previous_json_reasons = _diagnostic_reasons(llm_diags) or ["LLM did not return a JSON object."]
            record["rejection_reasons"] = previous_json_reasons
            candidate_records.append(record)
            emit(
                f"stage=implementation_plan substage={stage_label} llm_attempt={request_index} "
                f"candidate_attempt=1 json_attempt={json_attempt} mode={mode} prompt={prompt_name} "
                f"event=response_received json=invalid {_llm_token_event(meta)} reason={previous_json_reasons[0]}"
            )
            continue
        emit(
            f"stage=implementation_plan substage={stage_label} llm_attempt={request_index} "
            f"candidate_attempt=1 json_attempt={json_attempt} mode={mode} prompt={prompt_name} "
            f"event=response_received json=valid {_llm_token_event(meta)}"
        )
        llm_candidate_diags = validate_llm_candidate(raw_candidate)
        record["validated"] = True
        if has_errors(llm_candidate_diags):
            record["validator_error"] = True
            record["rejection_reasons"] = _diagnostic_reasons(llm_candidate_diags)
            emit(
                f"stage=implementation_plan substage={stage_label} llm_attempt={request_index} "
                f"candidate_attempt=1 json_attempt={json_attempt} mode={mode} prompt={prompt_name} "
                f"event=validator_done validator=llm_candidate status=rejected reason={record['rejection_reasons'][0]}"
            )
            candidate_records.append(record)
            previous_json_reasons = record["rejection_reasons"]
            if json_attempt <= _INVENTORY_JSON_RETRIES:
                continue
            accepted_by = "deterministic_reconciliation_after_invalid_llm_shape"
        else:
            record["accepted"] = True
            record["validator_error"] = False
            accepted_by = "llm_semantic_candidate"
            emit(
                f"stage=implementation_plan substage={stage_label} llm_attempt={request_index} "
                f"candidate_attempt=1 json_attempt={json_attempt} mode={mode} prompt={prompt_name} "
                "event=validator_done validator=llm_candidate status=accepted"
            )
            candidate_records.append(record)
        break

    if raw_candidate is None:
        accepted_by = "deterministic_reconciliation_after_missing_llm_json"
        raw_candidate = empty_llm_candidate()
        llm_candidate_diags = [
            PlanningDiagnostic(
                "warning",
                f"{stage_key}_llm_json_unavailable",
                f"{stage_label} did not return valid JSON; using deterministic empty semantic candidate.",
                stage_label,
            )
        ]

    llm_candidate = raw_candidate if not has_errors(llm_candidate_diags) else empty_llm_candidate()
    reconciliation = reconcile_candidate(llm_candidate)
    accepted = reconciliation.get("candidate", {})
    accepted_diags = validate_final_candidate(accepted)
    fatal_diagnostics: list[PlanningDiagnostic] = []
    final_failure_code = None
    final_reasons = _diagnostic_reasons(accepted_diags)
    if has_errors(accepted_diags):
        final_failure_code = f"{stage_key}_reconciliation_failed"
        fatal_diagnostics = [
            PlanningDiagnostic(
                "error",
                final_failure_code,
                f"{stage_label} deterministic reconciliation produced invalid final inventory.",
                stage_label,
            ),
            *[diag for diag in accepted_diags if diag.level == "error"],
        ]
    emit(
        f"stage=implementation_plan substage={stage_label} "
        f"event=validator_done validator=final_inventory "
        f"status={'rejected' if fatal_diagnostics else 'accepted'}"
        + (f" reason={final_reasons[0]}" if fatal_diagnostics and final_reasons else "")
    )
    return {
        "module_index": module_index,
        "module": module,
        "module_id": module_id,
        "stage_label": stage_label,
        "prompt_name": prompt_name,
        "repair_prompt_name": "",
        "accepted": None if fatal_diagnostics else accepted,
        "accepted_diags": accepted_diags,
        "llm_candidate": raw_candidate,
        "llm_candidate_diags": llm_candidate_diags,
        "candidate_attempts": candidate_records,
        "repair_attempts": [],
        "fatal_diagnostics": fatal_diagnostics,
        "reconciliation": reconciliation,
        "inventory_stats": _controlled_inventory_stats(candidate_records=candidate_records, accepted_by=accepted_by if not fatal_diagnostics else None, final_failure_code=final_failure_code, reconciliation=reconciliation),
    }


def _architecture_json_retry_messages(base_messages: list[dict[str, str]], previous_reasons: list[str], attempt: int) -> list[dict[str, str]]:
    if not previous_reasons:
        return base_messages
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"Architecture JSON retry attempt {attempt}. The previous response was rejected only because it was not valid JSON. "
                "Do not change the architecture task or explain anything. Return the same kind of architecture_candidates/v1 object, "
                "but repair the JSON shape so json.loads(response_text) succeeds. The first character must be '{' and the last "
                "character must be '}'. Use strict standard JSON only: double quotes, true/false/null, no markdown, no prose, "
                "no comments, no trailing comma, and no trailing semicolon. Rejection reasons:\n"
                + "\n".join(f"- {reason}" for reason in previous_reasons)
            ),
        },
    ]


def _is_invalid_llm_json(diagnostics: list[PlanningDiagnostic]) -> bool:
    return any(diag.code == "invalid_llm_json" for diag in diagnostics)


def _usage_from_meta(meta: dict[str, Any]) -> dict[str, int]:
    usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _implementation_stage_key_from_label(stage_label: str) -> str:
    prefix = str(stage_label).split(":", 1)[0]
    mapping = {
        "5.1_plan_skeleton": "implementation_plan_5_1",
        "5.2a_core_design": "implementation_plan_5_2a",
        "5.2b_module_artifacts": "implementation_plan_5_2b",
        "5.3_type_data": "implementation_plan_5_3",
        "5.4a_function_inventory": "implementation_plan_5_4a",
        "5.4b_function_signatures": "implementation_plan_5_4b",
        "5.4c_behavior_contract": "implementation_plan_5_4c",
        "5.4d_wire_access_binding": "implementation_plan_5_4d",
        "5.4e_call_contracts": "implementation_plan_5_4e",
        "5.5a_file_layout": "implementation_plan_5_5a",
        "5.5b_runtime_entrypoint": "implementation_plan_5_5b",
        "5.6_dependency_generation": "implementation_plan_5_6",
        "5.7_spec_readiness_validation": "implementation_plan_5_7",
    }
    return mapping.get(prefix, prefix)


class ValidatorStatsTracker:
    def __init__(self) -> None:
        self._invocations: list[dict[str, Any]] = []

    def record(
        self,
        *,
        stage_key: str,
        substage: str,
        prompt_name: str = "",
        source: str = "generated",
        first_generation_passed: bool | None = None,
        validator_error_triggered_repair: bool = False,
        repair_attempted: bool = False,
        repair_success: bool = False,
        final_validator_passed: bool | None = None,
        fallback_used: bool = False,
        failure_code: str | None = None,
    ) -> None:
        self._invocations.append(
            {
                "stage_key": stage_key,
                "substage": substage,
                "prompt_name": prompt_name,
                "source": source,
                "first_generation_passed": first_generation_passed,
                "validator_error_triggered_repair": validator_error_triggered_repair,
                "repair_attempted": repair_attempted,
                "repair_success": repair_success,
                "final_validator_passed": final_validator_passed,
                "fallback_used": fallback_used,
                "failure_code": failure_code,
            }
        )

    def summary(self) -> dict[str, Any]:
        generated = [item for item in self._invocations if item.get("source") != "inherited"]

        def build_buckets(group_key: str) -> dict[str, dict[str, Any]]:
            buckets: dict[str, dict[str, Any]] = {}
            for item in generated:
                bucket = buckets.setdefault(
                    str(item[group_key]),
                    {
                        "stage_key": item["stage_key"],
                        "invocation_count": 0,
                        "first_generation_count": 0,
                        "first_generation_pass_count": 0,
                        "validator_error_count": 0,
                        "repair_attempt_count": 0,
                        "repair_success_count": 0,
                        "fallback_pass_count": 0,
                        "final_validation_count": 0,
                        "final_validator_pass_count": 0,
                    },
                )
                bucket["invocation_count"] += 1
                if item.get("first_generation_passed") is not None:
                    bucket["first_generation_count"] += 1
                    if item.get("first_generation_passed"):
                        bucket["first_generation_pass_count"] += 1
                if item.get("validator_error_triggered_repair"):
                    bucket["validator_error_count"] += 1
                if item.get("repair_attempted"):
                    bucket["repair_attempt_count"] += 1
                if item.get("repair_success"):
                    bucket["repair_success_count"] += 1
                if item.get("fallback_used") and item.get("final_validator_passed"):
                    bucket["fallback_pass_count"] += 1
                if item.get("final_validator_passed") is not None:
                    bucket["final_validation_count"] += 1
                    if item.get("final_validator_passed"):
                        bucket["final_validator_pass_count"] += 1
            for bucket in buckets.values():
                bucket["first_generation_pass_rate"] = _rate(bucket["first_generation_pass_count"], bucket["first_generation_count"])
                bucket["repair_success_rate"] = _rate(bucket["repair_success_count"], bucket["repair_attempt_count"])
                bucket["final_validator_pass_rate"] = _rate(bucket["final_validator_pass_count"], bucket["final_validation_count"])
            return buckets

        by_stage_key = build_buckets("stage_key")
        by_substage = build_buckets("substage")
        repair_attempt_count = sum(item["repair_attempt_count"] for item in by_stage_key.values())
        repair_success_count = sum(item["repair_success_count"] for item in by_stage_key.values())
        first_generation_count = sum(item["first_generation_count"] for item in by_stage_key.values())
        first_generation_pass_count = sum(item["first_generation_pass_count"] for item in by_stage_key.values())
        final_validation_count = sum(item["final_validation_count"] for item in by_stage_key.values())
        final_validator_pass_count = sum(item["final_validator_pass_count"] for item in by_stage_key.values())
        return {
            "schema_version": "planning_repair_statistics/v1",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "repair_success_summary": {
                "validator_error_count": sum(item["validator_error_count"] for item in by_stage_key.values()),
                "repair_attempt_count": repair_attempt_count,
                "repair_success_count": repair_success_count,
                "repair_success_rate": _rate(repair_success_count, repair_attempt_count),
                "by_stage_key": by_stage_key,
                "by_substage": by_substage,
            },
            "substage_pass_rates": {
                "first_generation_count": first_generation_count,
                "first_generation_pass_count": first_generation_pass_count,
                "first_generation_pass_rate": _rate(first_generation_pass_count, first_generation_count),
                "final_validation_count": final_validation_count,
                "final_validator_pass_count": final_validator_pass_count,
                "final_validator_pass_rate": _rate(final_validator_pass_count, final_validation_count),
                "by_stage_key": by_stage_key,
                "by_substage": by_substage,
            },
            "substage_invocations": list(self._invocations),
        }


def _summarize_llm_meta(meta: dict[str, Any], attempt: int) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "usage": _usage_from_meta(meta),
        "content_length": meta.get("content_length", 0),
        "temperature": meta.get("temperature"),
        "enable_thinking": meta.get("enable_thinking"),
        "failed": bool(meta.get("failed")),
        "hit_completion_limit": bool(meta.get("hit_completion_limit")),
    }


def _merge_architecture_json_retry_meta(metas: list[dict[str, Any]], reasons: list[str]) -> dict[str, Any]:
    if not metas:
        return {"enabled": True, "prompt_name": "architecture_candidate_prompt", "json_retry_attempts": 0}
    merged = dict(metas[-1])
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for meta in metas:
        usage = _usage_from_meta(meta)
        for key in usage_total:
            usage_total[key] += usage[key]
    merged["usage"] = usage_total
    merged["json_retry_attempts"] = len(metas)
    merged["json_retry_failures"] = reasons
    merged["json_retry_meta_summary"] = [_summarize_llm_meta(meta, index + 1) for index, meta in enumerate(metas)]
    return merged


def request_architecture_json_candidate(
    *,
    messages: list[dict[str, str]],
    config: PlanningConfig,
    temperature: float,
    enable_thinking: bool,
) -> tuple[dict[str, Any] | None, list[PlanningDiagnostic], dict[str, Any]]:
    previous_reasons: list[str] = []
    metas: list[dict[str, Any]] = []
    diagnostics: list[PlanningDiagnostic] = []
    for attempt in range(1, config.llm_max_retries + 1):
        candidate, diagnostics, meta = request_json_candidate(
            prompt_name="architecture_candidate_prompt",
            messages=_architecture_json_retry_messages(messages, previous_reasons, attempt),
            config=config,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
        metas.append(meta)
        if candidate is not None or not _is_invalid_llm_json(diagnostics):
            return candidate, diagnostics, _merge_architecture_json_retry_meta(metas, previous_reasons)
        previous_reasons = _diagnostic_reasons(diagnostics) or ["LLM did not return a JSON object."]
    return None, diagnostics, _merge_architecture_json_retry_meta(metas, previous_reasons)


def _llm_failure_diagnostic(stage: str, reasons: list[str]) -> PlanningDiagnostic:
    detail = "; ".join(reasons[-8:]) if reasons else "No valid LLM output was produced."
    return PlanningDiagnostic(
        "error",
        f"{stage}_mandatory_llm_failed",
        f"Mandatory LLM stage failed after retries: {detail}",
    )


def _llm_token_event(meta: dict[str, Any]) -> str:
    usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
    return (
        f"tokens prompt={int(usage.get('prompt_tokens', 0) or 0)} "
        f"completion={int(usage.get('completion_tokens', 0) or 0)} "
        f"total={int(usage.get('total_tokens', 0) or 0)} "
        f"temperature={meta.get('temperature', 'unknown') if isinstance(meta, dict) else 'unknown'} "
        f"thinking={str(bool(meta.get('enable_thinking')) if isinstance(meta, dict) else False).lower()} "
        f"failed={bool(meta.get('failed')) if isinstance(meta, dict) else False} "
        f"hit_completion_limit={bool(meta.get('hit_completion_limit')) if isinstance(meta, dict) else False}"
    )


def _suffixed_step_filename(filename: str, suffix: str | None) -> str:
    if not suffix:
        return filename
    path = Path(filename)
    return f"{path.stem}__{safe_slug(suffix)}{path.suffix}"


def _diagnostics_as_dependency_errors(diagnostics: list[PlanningDiagnostic]) -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "path": item.path,
            "message": item.message,
            "severity": item.level,
            "repairable": True,
        }
        for item in diagnostics
    ]


def _architecture_generation_id(round_name: str, strategy: str) -> str:
    return f"{round_name}:{strategy}"


def _tag_architecture_candidates(candidate_set: dict[str, Any], *, strategy: str, request_id: str) -> dict[str, Any]:
    tagged = dict(candidate_set)
    items = []
    for idx, candidate in enumerate(candidate_set.get("candidates", [])):
        if not isinstance(candidate, dict):
            items.append(candidate)
            continue
        item = dict(candidate)
        base_id = str(item.get("candidate_id", "")).strip() or f"candidate_{idx + 1}"
        item["candidate_id"] = base_id if base_id.startswith(f"{strategy}_") else f"{strategy}_{base_id}"
        item["generation_strategy"] = strategy
        item["generation_request_id"] = request_id
        item.setdefault("generation_mode", "llm_candidate")
        item["module_graph_hints"] = []
        modules = []
        for module in item.get("modules", []):
            if not isinstance(module, dict):
                modules.append(module)
                continue
            normalized_module = dict(module)
            normalized_module["dependency_hints"] = [
                str(dep).strip()
                for dep in normalized_module.get("dependency_hints", [])
                if str(dep).strip()
            ]
            modules.append(normalized_module)
        item["modules"] = modules
        items.append(item)
    tagged["candidates"] = items
    return tagged


def _protocol_name_from_facts(facts_path: Path) -> str:
    if not facts_path.exists():
        return "protocol"
    try:
        raw = read_json(facts_path)
    except Exception:
        return "protocol"
    return str(raw.get("protocol_meta", {}).get("protocol_name", "protocol"))


def default_output_dir(facts_path: str | Path, target_profile: TargetProfile | None = None) -> Path:
    facts = Path(facts_path)
    protocol_slug = safe_slug(_protocol_name_from_facts(facts))
    target_slug = target_profile.slug if target_profile is not None else "target"
    return DEFAULT_OUTPUT_ROOT / protocol_slug / target_slug / run_timestamp()


def _top_level_resume_stage(resume_from_stage: str | None) -> str | None:
    if resume_from_stage in IMPLEMENTATION_PLAN_RESUME_STAGES:
        return "implementation_plan"
    return resume_from_stage


def _stage_should_run(stage: str, resume_from_stage: str | None) -> bool:
    top_stage = _top_level_resume_stage(resume_from_stage)
    return top_stage is None or _STAGE_ORDER[stage] >= _STAGE_ORDER[top_stage]


def _implementation_plan_substage_should_run(stage: str, resume_from_stage: str | None) -> bool:
    if resume_from_stage not in IMPLEMENTATION_PLAN_RESUME_STAGES:
        return True
    return _IMPLEMENTATION_PLAN_STAGE_ORDER[stage] >= _IMPLEMENTATION_PLAN_STAGE_ORDER[resume_from_stage]


def _should_stop_after(stage: str, stop_after_stage: str | None) -> bool:
    return stop_after_stage == stage


def _artifact_path(root: Path, key: str) -> Path:
    filename = STEP_FILENAMES[key]
    if key in AGENT_LOG_ARTIFACT_KEYS:
        return root / "_agent_logs" / filename
    if "validation_report" in filename:
        return root / "_validation_reports" / filename
    return root / "_step_logs" / filename


def _write_artifact_json(store: ArtifactStore, key: str, filename: str, data: Any) -> Path:
    if key in AGENT_LOG_ARTIFACT_KEYS:
        return store.write_agent_json(filename, data)
    return store.write_step_json(filename, data)


def find_latest_resume_source(
    facts_path: str | Path,
    target_profile_path: str | Path,
    exclude_output_dir: str | Path,
) -> Path | None:
    facts = Path(facts_path).expanduser()
    target_profile, target_diags = load_target_profile(target_profile_path)
    if target_profile is None or has_errors(target_diags):
        return None
    base = DEFAULT_OUTPUT_ROOT / safe_slug(_protocol_name_from_facts(facts)) / target_profile.slug
    if not base.exists():
        return None
    exclude = Path(exclude_output_dir).expanduser().resolve()
    candidates = []
    for path in base.iterdir():
        if not path.is_dir() or not (path / "_step_logs").exists():
            continue
        if path.resolve() == exclude:
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda item: item.name, reverse=True)[0] if candidates else None


def validate_resume_source_dir(source_dir: str | Path) -> tuple[Path | None, list[PlanningDiagnostic]]:
    root = Path(source_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return None, [PlanningDiagnostic("error", "invalid_resume_source_dir", "Resume source directory does not exist", str(root))]
    if not (root / "_step_logs").is_dir():
        return None, [PlanningDiagnostic("error", "missing_resume_step_logs", "Resume source directory must contain _step_logs", str(root / "_step_logs"))]
    return root, []


def load_resume_artifacts(source_dir: str | Path, required_keys: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Path], list[PlanningDiagnostic]]:
    root = Path(source_dir).expanduser()
    artifacts: dict[str, Any] = {}
    artifact_paths: dict[str, Path] = {}
    diagnostics: list[PlanningDiagnostic] = []
    for key in required_keys:
        path = _artifact_path(root, key)
        if not path.exists():
            diagnostics.append(PlanningDiagnostic("error", "missing_resume_artifact", f"Missing resume artifact '{key}'", str(path)))
            continue
        try:
            artifacts[key] = read_json(path)
            artifact_paths[key] = path
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(PlanningDiagnostic("error", "invalid_resume_artifact_json", f"Could not read resume artifact '{key}': {exc}", str(path)))
    return artifacts, artifact_paths, diagnostics


def validate_resume_prefix(
    source_dir: str | Path,
    from_stage: str,
    facts_path: str | Path,
    target_profile_path: str | Path,
    config: PlanningConfig,
) -> tuple[dict[str, Any], dict[str, Path], list[PlanningDiagnostic]]:
    root = Path(source_dir).expanduser()
    required_keys = _RESUME_REQUIRED_KEYS[from_stage]
    artifacts, artifact_paths, diagnostics = load_resume_artifacts(root, required_keys)
    manifest_path = _artifact_path(root, "planning_run_manifest")
    if not manifest_path.exists():
        diagnostics.append(PlanningDiagnostic("error", "missing_resume_manifest", "Missing resume source manifest", str(manifest_path)))
    else:
        try:
            manifest = read_json(manifest_path)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(PlanningDiagnostic("error", "invalid_resume_manifest_json", f"Could not read resume source manifest: {exc}", str(manifest_path)))
        else:
            facts_sha = file_sha256(Path(facts_path).expanduser())
            target_sha = file_sha256(Path(target_profile_path).expanduser())
            manifest_facts_sha = manifest.get("inputs", {}).get("facts", {}).get("sha256")
            manifest_target_sha = manifest.get("inputs", {}).get("target_profile", {}).get("sha256")
            if manifest_facts_sha != facts_sha:
                diagnostics.append(PlanningDiagnostic("error", "resume_facts_mismatch", "Resume source facts hash does not match current facts input", str(manifest_path)))
            if manifest_target_sha != target_sha:
                diagnostics.append(PlanningDiagnostic("error", "resume_target_profile_mismatch", "Resume source target profile hash does not match current target profile input", str(manifest_path)))
            expected_compat = {
                "facts_input_format_version": config.facts_input_format_version,
                "target_profile_format_version": config.target_profile_format_version,
                "coder_output_format_version": config.coder_output_format_version,
            }
            if manifest.get("compatibility", {}) != expected_compat:
                diagnostics.append(PlanningDiagnostic("error", "resume_compatibility_mismatch", "Resume source compatibility versions do not match current planning config", str(manifest_path)))
    if has_errors(diagnostics):
        return artifacts, artifact_paths, diagnostics

    planning_ir = artifacts.get("planning_ir")
    profile = artifacts.get("protocol_profile")
    constraints = artifacts.get("engineering_constraints")
    if isinstance(planning_ir, dict):
        diagnostics.extend(validate_planning_ir(planning_ir, path=str(artifact_paths["planning_ir"])))
    if isinstance(profile, dict):
        diagnostics.extend(validate_protocol_profile(profile, path=str(artifact_paths["protocol_profile"])))
    if isinstance(constraints, dict):
        diagnostics.extend(validate_constraints(constraints, path=str(artifact_paths["engineering_constraints"])))
    if all(isinstance(item, dict) for item in (profile, constraints, artifacts.get("architecture_candidates"))):
        architecture_candidates = artifacts["architecture_candidates"]
        diagnostics.extend(validate_architecture_candidates(architecture_candidates, profile, constraints, path=str(artifact_paths["architecture_candidates"])))
        if isinstance(artifacts.get("architecture_ranking"), dict):
            diagnostics.extend(validate_architecture_ranking(artifacts["architecture_ranking"], architecture_candidates, profile, constraints, path=str(artifact_paths["architecture_ranking"])))
        if isinstance(artifacts.get("selected_architecture"), dict):
            diagnostics.extend(validate_selected_architecture(artifacts["selected_architecture"], profile, constraints, path=str(artifact_paths["selected_architecture"])))
    if isinstance(artifacts.get("implementation_plan"), dict):
        implementation_plan = artifacts["implementation_plan"]
        if isinstance(profile, dict) and isinstance(planning_ir, dict):
            diagnostics.extend(validate_full_implementation_plan(implementation_plan, profile=profile, planning_ir=planning_ir, path=str(artifact_paths["implementation_plan"])))
        diagnostics.extend(validate_dependency_graph(implementation_plan, path=str(artifact_paths["implementation_plan"])))
        dependency_report = artifacts.get("dependency_validation_report")
        if isinstance(dependency_report, dict) and dependency_report.get("status") == "failed":
            diagnostics.append(PlanningDiagnostic("error", "resume_dependency_validation_failed", "Resume source dependency validation report is failed", str(artifact_paths["dependency_validation_report"])))
    return artifacts, artifact_paths, diagnostics


def _write_manifest(
    *,
    store: ArtifactStore,
    facts_path: Path,
    target_profile_path: Path,
    config: PlanningConfig,
    status: str,
    diagnostics: list[PlanningDiagnostic],
    artifact_paths: dict[str, Path],
    failure: dict[str, Any] | None = None,
) -> Path:
    manifest = build_manifest(
        facts_path=facts_path,
        target_profile_path=target_profile_path,
        store=store,
        config=config,
        status=status,
        diagnostics=diagnostics,
        artifact_paths=artifact_paths,
        failure=failure,
        resume=store.resume_metadata,
        stop=store.stop_metadata,
    )
    return store.write_step_json(STEP_FILENAMES["planning_run_manifest"], manifest)


def _write_token_usage_summary(
    *,
    store: ArtifactStore,
    tracker: TokenUsageTracker,
    artifact_paths: dict[str, Path],
) -> Path:
    path = store.write_step_json(STEP_FILENAMES["token_usage_summary"], tracker.summary())
    artifact_paths["token_usage_summary"] = path
    return path


def _write_repair_statistics(
    *,
    store: ArtifactStore,
    tracker: ValidatorStatsTracker,
    artifact_paths: dict[str, Path],
) -> Path:
    path = store.write_step_json(STEP_FILENAMES["planning_repair_statistics"], tracker.summary())
    artifact_paths["planning_repair_statistics"] = path
    return path


def _validation_report(
    *,
    status: str,
    diagnostics: list[PlanningDiagnostic],
    artifact_paths: dict[str, Path],
    coder_compatibility_status: str = "not_run",
    coder_schema_status: str = "not_run",
    coder_loader_status: str = "not_run",
) -> dict[str, Any]:
    return {
        "schema_version": "planning_validation_report/v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "facts_compatibility_status": "failed" if any(item.code.startswith("missing_") or item.code.startswith("invalid_facts") for item in diagnostics if item.level == "error") else "passed",
        "coder_compatibility_status": coder_compatibility_status,
        "coder_schema_status": coder_schema_status,
        "coder_loader_status": coder_loader_status,
        "artifact_status": {key: str(value) for key, value in artifact_paths.items()},
        "diagnostics": diagnostics_to_dict(diagnostics),
        "summary": {
            "error_count": sum(1 for item in diagnostics if item.level == "error"),
            "warning_count": sum(1 for item in diagnostics if item.level == "warning"),
        },
    }


class PlanningAgent:
    def __init__(
        self,
        facts_path: str | Path,
        target_profile_path: str | Path,
        *,
        output_dir: str | Path | None = None,
        config: PlanningConfig | None = None,
    ) -> None:
        self.facts_path = Path(facts_path).expanduser()
        self.target_profile_path = Path(target_profile_path).expanduser()
        self.config = config or PlanningConfig()
        target_profile, _ = load_target_profile(self.target_profile_path) if self.target_profile_path.exists() else (None, [])
        self.output_dir = Path(output_dir).expanduser() if output_dir else default_output_dir(self.facts_path, target_profile)

    def validate_inputs(self) -> list[PlanningDiagnostic]:
        diagnostics = validate_input_paths(self.facts_path, self.target_profile_path)
        if has_errors(diagnostics):
            return diagnostics
        target_profile, target_diags = load_target_profile(self.target_profile_path)
        diagnostics.extend(target_diags)
        if target_profile and target_profile.language.strip().lower() != "c":
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "unsupported_target_language",
                    "Planning coder-compatible compiler currently supports only language=C",
                    str(self.target_profile_path),
                )
            )
        return diagnostics

    def validate(self) -> PlanningResult:
        store = ArtifactStore(self.output_dir)
        artifact_paths: dict[str, Path] = {}
        diagnostics = self.validate_inputs()
        manifest_path = _write_manifest(
            store=store,
            facts_path=self.facts_path,
            target_profile_path=self.target_profile_path,
            config=self.config,
            status="failed" if has_errors(diagnostics) else "validated",
            diagnostics=diagnostics,
            artifact_paths=artifact_paths,
            failure={"stage": "preflight", "code": "validation_errors"} if has_errors(diagnostics) else None,
        )
        artifact_paths["planning_run_manifest"] = manifest_path
        return PlanningResult(not has_errors(diagnostics), self.output_dir, diagnostics, artifact_paths)

    def plan(
        self,
        *,
        resume_from_stage: str | None = None,
        resume_source_dir: str | Path | None = None,
        stop_after_stage: str | None = None,
    ) -> PlanningResult:
        resume_from_stage = normalize_resume_stage(resume_from_stage)
        stop_after_stage = normalize_stop_after_stage(stop_after_stage)
        store = ArtifactStore(self.output_dir)
        token_tracker = TokenUsageTracker()
        validator_stats = ValidatorStatsTracker()
        store.log_event("stage=preflight start")
        diagnostics = self.validate_inputs()
        artifact_paths: dict[str, Path] = {}
        inherited_artifacts: dict[str, Any] = {}

        def finish_run(
            stage: str,
            *,
            status: str,
            failure: dict[str, Any] | None = None,
            coder_compatibility_status: str = "not_run",
            coder_schema_status: str = "not_run",
            coder_loader_status: str = "not_run",
        ) -> PlanningResult:
            _write_repair_statistics(store=store, tracker=validator_stats, artifact_paths=artifact_paths)
            _write_token_usage_summary(store=store, tracker=token_tracker, artifact_paths=artifact_paths)
            report = _validation_report(
                status=status,
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                coder_compatibility_status=coder_compatibility_status,
                coder_schema_status=coder_schema_status,
                coder_loader_status=coder_loader_status,
            )
            report_path = store.write_step_json(STEP_FILENAMES["planning_validation_report"], report)
            artifact_paths["planning_validation_report"] = report_path
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status=status,
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure=failure,
            )
            store.log_event(f"planning finalize stage={stage} status={status}")
            return PlanningResult(status != "failed" and not has_errors(diagnostics), self.output_dir, diagnostics, artifact_paths)
        if resume_from_stage is not None:
            if resume_from_stage not in RESUME_STAGES:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "invalid_resume_stage",
                        f"Unsupported resume stage '{resume_from_stage}'. Expected one of: {', '.join(RESUME_STAGE_OPTIONS)}",
                    )
                )
            store.resume_metadata = {
                "enabled": True,
                "from_stage": resume_from_stage,
                "source_output_dir": None,
                "inherited_artifacts": {},
            }
        elif resume_source_dir is not None:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "resume_source_without_stage",
                    "--resume-source-dir requires --resume-from-stage",
                    str(Path(resume_source_dir).expanduser()),
                )
            )
        if stop_after_stage is not None:
            if stop_after_stage not in RESUME_STAGES:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "invalid_stop_after_stage",
                        f"Unsupported stop-after stage '{stop_after_stage}'. Expected one of: {', '.join(STOP_AFTER_STAGE_OPTIONS)}",
                    )
                )
            store.stop_metadata = {
                "enabled": True,
                "after_stage": stop_after_stage,
            }
        manifest_path = _write_manifest(
            store=store,
            facts_path=self.facts_path,
            target_profile_path=self.target_profile_path,
            config=self.config,
            status="running",
            diagnostics=diagnostics,
            artifact_paths=artifact_paths,
            failure=None,
        )
        artifact_paths["planning_run_manifest"] = manifest_path
        if has_errors(diagnostics):
            return finish_run("preflight", status="failed", failure={"stage": "preflight", "code": "validation_errors"})
        store.log_event("stage=preflight done")

        store.log_event("stage=target_profile load start")
        target_profile, target_diags = load_target_profile(self.target_profile_path)
        diagnostics.extend(target_diags)
        if target_profile is None or has_errors(target_diags):
            return finish_run("target_profile", status="failed", failure={"stage": "target_profile", "code": "target_profile_load_failed"})
        store.log_event("stage=target_profile load done")

        if resume_from_stage is not None and not has_errors(diagnostics):
            required_keys = _RESUME_REQUIRED_KEYS[resume_from_stage]
            if required_keys:
                if resume_source_dir is None:
                    source_dir = find_latest_resume_source(self.facts_path, self.target_profile_path, self.output_dir)
                    source_diags: list[PlanningDiagnostic] = []
                else:
                    source_dir, source_diags = validate_resume_source_dir(resume_source_dir)
                    diagnostics.extend(source_diags)
                if source_dir is None and not source_diags:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "resume_source_not_found",
                            "Could not find a previous Planning Agent run for the current facts and target profile",
                            str(DEFAULT_OUTPUT_ROOT),
                        )
                    )
                else:
                    store.resume_metadata["source_output_dir"] = str(source_dir)
                    store.log_event(f"resume source={source_dir} from_stage={resume_from_stage}")
                    inherited_artifacts, source_artifact_paths, resume_diags = validate_resume_prefix(
                        source_dir,
                        resume_from_stage,
                        self.facts_path,
                        self.target_profile_path,
                        self.config,
                    )
                    diagnostics.extend(resume_diags)
                    if not has_errors(resume_diags):
                        for key in required_keys:
                            inherited_path = _write_artifact_json(store, key, STEP_FILENAMES[key], inherited_artifacts[key])
                            artifact_paths[key] = inherited_path
                        store.resume_metadata["inherited_artifacts"] = {
                            key: str(artifact_paths[key])
                            for key in required_keys
                            if key in artifact_paths
                        }
                        store.log_event(f"resume inherited_artifacts={len(store.resume_metadata['inherited_artifacts'])}")
                    else:
                        store.resume_metadata["inherited_artifacts"] = {
                            key: str(path)
                            for key, path in source_artifact_paths.items()
                        }
            else:
                store.log_event(f"resume from_stage={resume_from_stage} has no prior persisted artifacts")
        if has_errors(diagnostics):
            return finish_run("resume", status="failed", failure={"stage": "resume", "code": "resume_validation_failed"})

        def finish_early(stage: str) -> PlanningResult:
            status = "failed" if has_errors(diagnostics) else "stopped"
            store.log_event(f"planning stopped after stage={stage} status={status}")
            return finish_run(stage, status=status, failure={"stage": stage, "code": "validation_errors"} if status == "failed" else None)

        if _stage_should_run("planning_ir", resume_from_stage):
            store.log_event("stage=planning_ir build start")
            planning_ir, ir_diags = build_planning_ir(self.facts_path, target_profile)
            diagnostics.extend(ir_diags)
            if planning_ir is None:
                return finish_run("planning_ir", status="failed", failure={"stage": "planning_ir", "code": "planning_ir_build_failed"})
            planning_ir_path = store.write_step_json(STEP_FILENAMES["planning_ir"], planning_ir)
            artifact_paths["planning_ir"] = planning_ir_path
            diagnostics.extend(validate_planning_ir(planning_ir, path=str(planning_ir_path)))
            if has_errors(diagnostics):
                return finish_run("planning_ir", status="failed", failure={"stage": "planning_ir", "code": "planning_ir_validation_failed"})
            store.log_event("stage=planning_ir build done")
        else:
            planning_ir = inherited_artifacts["planning_ir"]
            store.log_event("stage=planning_ir resume inherited")
        if _should_stop_after("planning_ir", stop_after_stage):
            return finish_early("planning_ir")

        if _stage_should_run("protocol_profile", resume_from_stage):
            store.log_event("stage=protocol_profile build start")
            profile = build_protocol_profile(planning_ir)
            accepted_profile = None
            previous_reasons: list[str] = []
            base_messages = protocol_profile_patch_messages(planning_ir, profile)
            protocol_profile_config = _llm_config_for_stage(self.config, "protocol_profile")
            protocol_profile_thinking = self.config.llm_enable_thinking_for("protocol_profile")
            for attempt in range(1, self.config.llm_max_retries_for("protocol_profile") + 1):
                store.log_event(
                    f"stage=protocol_profile llm_attempt={attempt} prompt=protocol_profile_patch_prompt thinking={str(protocol_profile_thinking).lower()} start"
                )
                candidate, candidate_diags, meta = request_json_candidate(
                    prompt_name="protocol_profile_patch_prompt",
                    messages=_retry_messages(base_messages, previous_reasons, attempt),
                    config=protocol_profile_config,
                    enable_thinking=protocol_profile_thinking,
                )
                store.write_agent_log(f"004_protocol_profile_patch_llm_attempt_{attempt}_meta", str(meta))
                token_tracker.add_attempt(stage="protocol_profile", prompt_name="protocol_profile_patch_prompt", attempt=attempt, meta=meta)
                store.log_event(f"stage=protocol_profile llm_attempt={attempt} prompt=protocol_profile_patch_prompt {_llm_token_event(meta)}")
                if candidate is None:
                    previous_reasons = _diagnostic_reasons(candidate_diags) or ["LLM did not return a JSON object."]
                    store.write_agent_log(f"004_protocol_profile_patch_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                    store.log_event(f"stage=protocol_profile llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
                    continue
                candidate_path = _write_artifact_json(store, "protocol_profile_patch_candidate", STEP_FILENAMES["protocol_profile_patch_candidate"], candidate)
                artifact_paths["protocol_profile_patch_candidate"] = candidate_path
                patch_diags = validate_protocol_profile_patch_candidate(candidate, profile, planning_ir=planning_ir, path=str(candidate_path))
                if not has_errors(patch_diags):
                    patched_profile = apply_protocol_profile_patch_candidate(profile, candidate)
                    patched_diags = validate_protocol_profile(patched_profile)
                    if not has_errors(patched_diags):
                        diagnostics.extend([diag for diag in patch_diags if diag.level != "error"])
                        accepted_profile = patched_profile
                        token_tracker.mark_attempt_accepted(stage="protocol_profile", prompt_name="protocol_profile_patch_prompt", attempt=attempt)
                        store.log_event(f"stage=protocol_profile llm_attempt={attempt} accepted")
                        break
                    patch_diags.extend(patched_diags)
                previous_reasons = _diagnostic_reasons(patch_diags)
                store.write_agent_log(f"004_protocol_profile_patch_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                store.log_event(f"stage=protocol_profile llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
            if accepted_profile is None:
                diagnostics.append(_llm_failure_diagnostic("protocol_profile", previous_reasons))
                return finish_run("protocol_profile", status="failed", failure={"stage": "protocol_profile", "code": "mandatory_llm_failed"})
            profile = accepted_profile
            profile_path = store.write_step_json(STEP_FILENAMES["protocol_profile"], profile)
            artifact_paths["protocol_profile"] = profile_path
            diagnostics.extend(validate_protocol_profile(profile, path=str(profile_path)))
            if has_errors(diagnostics):
                return finish_run("protocol_profile", status="failed", failure={"stage": "protocol_profile", "code": "protocol_profile_validation_failed"})
            store.log_event("stage=protocol_profile build done")
        else:
            profile = inherited_artifacts["protocol_profile"]
            store.log_event("stage=protocol_profile resume inherited")
        if _should_stop_after("protocol_profile", stop_after_stage):
            return finish_early("protocol_profile")

        if _stage_should_run("engineering_constraints", resume_from_stage):
            store.log_event("stage=engineering_constraints activate start")
            constraints = activate_constraints(profile)
            constraints_path = store.write_step_json(STEP_FILENAMES["engineering_constraints"], constraints)
            artifact_paths["engineering_constraints"] = constraints_path
            diagnostics.extend(validate_constraints(constraints, path=str(constraints_path)))
            if has_errors(diagnostics):
                store.log_event("stage=engineering_constraints activate done status=failed")
                return finish_run("engineering_constraints", status="failed", failure={"stage": "engineering_constraints", "code": "validation_errors"})
            store.log_event("stage=engineering_constraints activate done")
        else:
            constraints = inherited_artifacts["engineering_constraints"]
            store.log_event("stage=engineering_constraints resume inherited")
        if _should_stop_after("engineering_constraints", stop_after_stage):
            return finish_early("engineering_constraints")

        if _stage_should_run("architecture", resume_from_stage):
            store.log_event("stage=architecture build start")
            architecture_context = build_architecture_context(planning_ir, profile, constraints)
            architecture_context_path = store.write_step_json(STEP_FILENAMES["architecture_context"], architecture_context)
            artifact_paths["architecture_context"] = architecture_context_path
            accepted_candidates: list[dict[str, Any]] = []
            architecture_warnings: list[str] = []
            request_counter = 0
            rejection_reasons: list[str] = []
            generation_rounds = (
                ("high_variance", "architecture_candidate_high_variance"),
                ("low_variance_retry", "architecture_candidate_low_variance"),
            )
            for round_name, stage_config_key in generation_rounds:
                if accepted_candidates:
                    break
                round_config = _llm_config_for_stage(self.config, stage_config_key)
                temperature = self.config.llm_temperature_for(stage_config_key)
                enable_thinking = self.config.llm_enable_thinking_for(stage_config_key)
                round_requests: list[dict[str, Any]] = []
                for strategy in ARCHITECTURE_DESIGN_STRATEGIES:
                    request_counter += 1
                    request_id = _architecture_generation_id(round_name, strategy)
                    messages = architecture_candidate_messages(architecture_context, strategy)
                    store.log_event(
                        f"stage=architecture generation_request={request_counter} strategy={strategy} temperature={temperature} thinking={str(enable_thinking).lower()} start"
                    )
                    round_requests.append(
                        {
                            "request_counter": request_counter,
                            "request_id": request_id,
                            "strategy": strategy,
                            "messages": messages,
                        }
                    )
                round_results: list[tuple[dict[str, Any], dict[str, Any] | None, list[PlanningDiagnostic], dict[str, Any]]] = []
                with ThreadPoolExecutor(max_workers=len(round_requests)) as executor:
                    future_map = {
                        executor.submit(
                            request_architecture_json_candidate,
                            messages=request["messages"],
                            config=round_config,
                            temperature=temperature,
                            enable_thinking=enable_thinking,
                        ): request
                        for request in round_requests
                    }
                    for future in as_completed(future_map):
                        request = future_map[future]
                        try:
                            llm_candidates, llm_diags, meta = future.result()
                        except Exception as exc:  # noqa: BLE001
                            llm_candidates = None
                            llm_diags = [
                                PlanningDiagnostic(
                                    "warning",
                                    "llm_request_failed",
                                    f"architecture_candidate_prompt failed: {exc}",
                                )
                            ]
                            meta = {
                                "enabled": True,
                                "prompt_name": "architecture_candidate_prompt",
                                "temperature": temperature,
                                "enable_thinking": enable_thinking,
                                "failed": True,
                            }
                        store.log_event(
                            f"stage=architecture generation_request={request['request_counter']} "
                            f"prompt=architecture_candidate_prompt event=response_received "
                            f"json={'valid' if llm_candidates is not None else 'invalid'} {_llm_token_event(meta)}"
                        )
                        round_results.append((request, llm_candidates, llm_diags, meta))
                for request, llm_candidates, llm_diags, meta in sorted(round_results, key=lambda item: item[0]["request_counter"]):
                    request_id = request["request_id"]
                    request_counter_for_log = request["request_counter"]
                    store.write_agent_log(f"006_architecture_candidate_{request_id}_meta", str(meta))
                    token_tracker.add_attempt(stage="architecture", prompt_name="architecture_candidate_prompt", attempt=request_counter_for_log, meta=meta)
                    store.log_event(
                        f"stage=architecture generation_request={request_counter_for_log} prompt=architecture_candidate_prompt {_llm_token_event(meta)}"
                    )
                    if llm_candidates is None:
                        reasons = _diagnostic_reasons(llm_diags) or ["LLM did not return a JSON object."]
                        rejection_reasons.extend(reasons)
                        architecture_warnings.append(f"{request_id} rejected: {reasons[0]}")
                        store.write_agent_log(f"006_architecture_candidate_{request_id}_rejection", "\n".join(reasons))
                        store.log_event(f"stage=architecture generation_request={request_counter_for_log} rejected reason={reasons[0]}")
                        continue
                    tagged_candidates = _tag_architecture_candidates(llm_candidates, strategy=request["strategy"], request_id=request_id)
                    store.write_agent_log(f"006_architecture_candidate_{request_id}_raw", str(tagged_candidates))
                    architecture_diags = validate_architecture_candidates(tagged_candidates, profile, constraints)
                    if has_errors(architecture_diags):
                        reasons = _diagnostic_reasons(architecture_diags)
                        rejection_reasons.extend(reasons)
                        architecture_warnings.append(f"{request_id} rejected: {reasons[0] if reasons else 'validation failed'}")
                        store.write_agent_log(f"006_architecture_candidate_{request_id}_rejection", "\n".join(reasons))
                        store.log_event(
                            f"stage=architecture generation_request={request_counter_for_log} rejected reason={reasons[0] if reasons else 'validation failed'}"
                        )
                        continue
                    token_tracker.mark_attempt_accepted(stage="architecture", prompt_name="architecture_candidate_prompt", attempt=request_counter_for_log)
                    accepted_candidates.extend([item for item in tagged_candidates.get("candidates", []) if isinstance(item, dict)])
                    architecture_warnings.extend(str(item) for item in tagged_candidates.get("generation_warnings", []) if str(item).strip())
                    store.log_event(f"stage=architecture generation_request={request_counter_for_log} accepted")
            if not accepted_candidates:
                diagnostics.append(_llm_failure_diagnostic("architecture", rejection_reasons))
                return finish_run("architecture", status="failed", failure={"stage": "architecture", "code": "mandatory_llm_failed"})
            architecture_candidates = {
                "schema_version": "architecture_candidates/v1",
                "candidates": accepted_candidates,
                "generation_warnings": architecture_warnings,
            }
            candidates_path = store.write_step_json(STEP_FILENAMES["architecture_candidates"], architecture_candidates)
            artifact_paths["architecture_candidates"] = candidates_path
            diagnostics.extend(validate_architecture_candidates(architecture_candidates, profile, constraints, path=str(candidates_path)))
            ranking_messages = architecture_ranking_messages(architecture_context, architecture_candidates)
            ranking_config = _llm_config_for_stage(self.config, "architecture_ranking")
            ranking_thinking = self.config.llm_enable_thinking_for("architecture_ranking")
            store.log_event(
                f"stage=architecture llm_attempt=ranking prompt=architecture_ranking_prompt thinking={str(ranking_thinking).lower()} start"
            )
            ranking_candidate, ranking_llm_diags, ranking_meta = request_json_candidate(
                prompt_name="architecture_ranking_prompt",
                messages=ranking_messages,
                config=ranking_config,
                enable_thinking=ranking_thinking,
            )
            token_tracker.add_attempt(stage="architecture", prompt_name="architecture_ranking_prompt", attempt=1, meta=ranking_meta)
            store.write_agent_log("006_architecture_ranking_llm_attempt_1_meta", str(ranking_meta))
            store.log_event(f"stage=architecture llm_attempt=ranking prompt=architecture_ranking_prompt {_llm_token_event(ranking_meta)}")
            if ranking_candidate is not None:
                ranking_diags = validate_architecture_ranking(ranking_candidate, architecture_candidates, profile, constraints)
            else:
                ranking_diags = ranking_llm_diags
            if ranking_candidate is None or has_errors(ranking_diags):
                reasons = _diagnostic_reasons(ranking_diags) or ["Architecture ranking LLM did not return a valid ranking."]
                store.write_agent_log("006_architecture_ranking_llm_attempt_1_rejection", "\n".join(reasons))
                ranking = deterministic_architecture_ranking(architecture_candidates, profile, warning="LLM architecture ranking invalid; deterministic ranking fallback used.")
            else:
                ranking = ranking_candidate
                token_tracker.mark_attempt_accepted(stage="architecture", prompt_name="architecture_ranking_prompt", attempt=1)
            ranking_path = store.write_step_json(STEP_FILENAMES["architecture_ranking"], ranking)
            artifact_paths["architecture_ranking"] = ranking_path
            diagnostics.extend(validate_architecture_ranking(ranking, architecture_candidates, profile, constraints, path=str(ranking_path)))
            selected_architecture = select_architecture(architecture_candidates, profile, ranking)
            selected_path = store.write_step_json(STEP_FILENAMES["selected_architecture"], selected_architecture)
            artifact_paths["selected_architecture"] = selected_path
            diagnostics.extend(validate_selected_architecture(selected_architecture, profile, constraints, path=str(selected_path)))
            if has_errors(diagnostics):
                store.log_event("stage=architecture build done status=failed")
                return finish_run("architecture", status="failed", failure={"stage": "architecture", "code": "validation_errors"})
            store.log_event("stage=architecture build done")

        else:
            architecture_context = inherited_artifacts["architecture_context"]
            architecture_candidates = inherited_artifacts["architecture_candidates"]
            ranking = inherited_artifacts["architecture_ranking"]
            selected_architecture = inherited_artifacts["selected_architecture"]
            store.log_event("stage=architecture resume inherited")
        if _should_stop_after("architecture", stop_after_stage):
            return finish_early("architecture")

        if _stage_should_run("implementation_plan", resume_from_stage):
            store.log_event("stage=implementation_plan build start")
            if _implementation_plan_substage_should_run("implementation_plan_5_1", resume_from_stage):
                draft = build_plan_skeleton(planning_ir, profile, constraints, selected_architecture)
                skeleton_path = store.write_step_json(STEP_FILENAMES["implementation_plan_skeleton"], draft)
                artifact_paths["implementation_plan_skeleton"] = skeleton_path
                store.log_event("stage=implementation_plan substage=5.1_plan_skeleton build done")
            else:
                draft = inherited_artifacts["implementation_plan_skeleton"]
                skeleton_path = artifact_paths["implementation_plan_skeleton"]
                store.log_event("stage=implementation_plan substage=5.1_plan_skeleton resume inherited")
            skeleton_diags = validate_plan_skeleton(draft, selected_architecture, profile, constraints, path=str(skeleton_path))
            skeleton_errors = [diag for diag in skeleton_diags if diag.level == "error"]
            validator_stats.record(
                stage_key="implementation_plan_5_1",
                substage="5.1_plan_skeleton",
                source="generated" if _implementation_plan_substage_should_run("implementation_plan_5_1", resume_from_stage) else "inherited",
                first_generation_passed=stage_passed(skeleton_diags),
                final_validator_passed=stage_passed(skeleton_diags),
                failure_code=skeleton_errors[0].code if skeleton_errors else None,
            )
            if has_errors(skeleton_diags):
                diagnostics.extend(skeleton_diags)
                return finish_run("implementation_plan", status="failed", failure={"stage": "implementation_plan", "code": "invalid_plan_skeleton"})
            if _should_stop_after("implementation_plan_5_1", stop_after_stage):
                return finish_early("implementation_plan_5_1")

            def stage_candidate(
                *,
                stage_label: str,
                thinking_stage: str,
                prompt_name: str,
                messages: list[dict[str, str]],
                candidate_key: str,
                report_key: str,
                fallback: dict[str, Any],
                validator,
                normalizer: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, int]]] | None = None,
                max_attempts: int | None = None,
                step_log_suffix: str | None = None,
                record_diagnostics: bool = True,
            ) -> dict[str, Any]:
                previous_reasons: list[str] = []
                accepted: dict[str, Any] | None = None
                accepted_diags: list[PlanningDiagnostic] = []
                attempts = max_attempts if max_attempts is not None else self.config.llm_max_retries_for(thinking_stage)
                artifact_suffix = safe_slug(step_log_suffix) if step_log_suffix else ""
                enable_thinking = self.config.llm_enable_thinking_for(thinking_stage)
                request_config = _llm_config_for_stage(self.config, thinking_stage)
                first_generation_passed: bool | None = None
                validator_error_seen = False
                repair_attempted = False
                repair_success = False
                fallback_used = False
                for attempt in range(1, attempts + 1):
                    repair_attempt = validator_error_seen
                    if repair_attempt:
                        repair_attempted = True
                    store.log_event(
                        f"stage=implementation_plan substage={stage_label} llm_attempt={attempt} prompt={prompt_name} thinking={str(enable_thinking).lower()} start"
                    )
                    candidate, llm_diags, meta = request_json_candidate(
                        prompt_name=prompt_name,
                        messages=_retry_messages(messages, previous_reasons, attempt),
                        config=request_config,
                        enable_thinking=enable_thinking,
                    )
                    log_key = f"{candidate_key}_{artifact_suffix}" if artifact_suffix else candidate_key
                    store.write_agent_log(f"{log_key}_llm_attempt_{attempt}_meta", str(meta))
                    token_tracker.add_attempt(stage="implementation_plan", prompt_name=prompt_name, attempt=attempt, meta=meta)
                    store.log_event(f"stage=implementation_plan substage={stage_label} llm_attempt={attempt} prompt={prompt_name} {_llm_token_event(meta)}")
                    if candidate is None:
                        previous_reasons = _diagnostic_reasons(llm_diags) or ["LLM did not return a JSON object."]
                        store.write_agent_log(f"{log_key}_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                        continue
                    if normalizer is not None:
                        candidate, normalization_stats = normalizer(candidate)
                        nonzero_stats = {key: value for key, value in normalization_stats.items() if value}
                        if nonzero_stats:
                            stats_text = " ".join(f"{key}={value}" for key, value in sorted(nonzero_stats.items()))
                            store.log_event(f"stage=implementation_plan substage={stage_label} llm_attempt={attempt} normalized {stats_text}")
                    candidate_diags = validator(candidate)
                    if stage_passed(candidate_diags):
                        if first_generation_passed is None:
                            first_generation_passed = True
                        if repair_attempt:
                            repair_success = True
                        accepted = candidate
                        accepted_diags = candidate_diags
                        token_tracker.mark_attempt_accepted(stage="implementation_plan", prompt_name=prompt_name, attempt=attempt)
                        store.log_event(f"stage=implementation_plan substage={stage_label} llm_attempt={attempt} accepted")
                        break
                    if first_generation_passed is None:
                        first_generation_passed = False
                    validator_error_seen = True
                    previous_reasons = _diagnostic_reasons(candidate_diags)
                    store.write_agent_log(f"{log_key}_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                    store.log_event(f"stage=implementation_plan substage={stage_label} llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
                if accepted is None:
                    accepted = fallback
                    accepted_diags = validator(accepted)
                    fallback_used = True
                    store.log_event(f"stage=implementation_plan substage={stage_label} fallback=deterministic")
                final_passed = stage_passed(accepted_diags)
                accepted_errors = [diag for diag in accepted_diags if diag.level == "error"]
                validator_stats.record(
                    stage_key=thinking_stage,
                    substage=stage_label,
                    prompt_name=prompt_name,
                    first_generation_passed=first_generation_passed,
                    validator_error_triggered_repair=validator_error_seen,
                    repair_attempted=repair_attempted,
                    repair_success=repair_success,
                    final_validator_passed=final_passed,
                    fallback_used=fallback_used,
                    failure_code=accepted_errors[0].code if accepted_errors else None,
                )
                candidate_filename = _suffixed_step_filename(STEP_FILENAMES[candidate_key], step_log_suffix)
                candidate_path = store.write_agent_json(candidate_filename, accepted) if step_log_suffix else _write_artifact_json(store, candidate_key, candidate_filename, accepted)
                report_path = store.write_step_json(_suffixed_step_filename(STEP_FILENAMES[report_key], step_log_suffix), validation_report(stage_label, accepted_diags))
                artifact_key = f"{candidate_key}_{artifact_suffix}" if artifact_suffix else candidate_key
                artifact_report_key = f"{report_key}_{artifact_suffix}" if artifact_suffix else report_key
                artifact_paths[artifact_key] = candidate_path
                artifact_paths[artifact_report_key] = report_path
                if record_diagnostics and has_errors(accepted_diags):
                    diagnostics.extend(accepted_diags)
                return accepted

            def inherited_stage_candidate(*, stage_label: str, candidate_key: str, validator) -> dict[str, Any]:
                candidate = inherited_artifacts[candidate_key]
                candidate_diags = validator(candidate)
                passed = stage_passed(candidate_diags)
                candidate_errors = [diag for diag in candidate_diags if diag.level == "error"]
                validator_stats.record(
                    stage_key=_implementation_stage_key_from_label(stage_label),
                    substage=stage_label,
                    source="inherited",
                    first_generation_passed=passed,
                    final_validator_passed=passed,
                    failure_code=candidate_errors[0].code if candidate_errors else None,
                )
                if has_errors(candidate_diags):
                    store.log_event(f"stage=implementation_plan substage={stage_label} resume inherited has_errors={len([diag for diag in candidate_diags if diag.level == 'error'])}")
                else:
                    store.log_event(f"stage=implementation_plan substage={stage_label} resume inherited")
                return candidate

            if _implementation_plan_substage_should_run("implementation_plan_5_2a", resume_from_stage):
                core_context = build_core_design_context(planning_ir, profile, constraints, selected_architecture)
                core_candidate = stage_candidate(
                    stage_label="5.2a_core_design",
                    thinking_stage="implementation_plan_5_2a",
                    prompt_name="core_design_candidate_prompt",
                    messages=core_design_candidate_messages(core_context),
                    candidate_key="core_design_candidate",
                    report_key="core_design_validation_report",
                    fallback=fallback_core_design(draft, planning_ir, constraints, selected_architecture, profile),
                    validator=lambda candidate: validate_core_design_candidate(candidate, planning_ir, profile, selected_architecture, constraints),
                )
            else:
                core_candidate = inherited_stage_candidate(
                    stage_label="5.2a_core_design",
                    candidate_key="core_design_candidate",
                    validator=lambda candidate: validate_core_design_candidate(candidate, planning_ir, profile, selected_architecture, constraints),
                )
            draft = merge_core_design(draft, core_candidate)
            if _should_stop_after("implementation_plan_5_2a", stop_after_stage):
                return finish_early("implementation_plan_5_2a")

            if _implementation_plan_substage_should_run("implementation_plan_5_2b", resume_from_stage):
                module_context = build_module_artifact_context(draft, profile, constraints, selected_architecture)
                module_candidate = stage_candidate(
                    stage_label="5.2b_module_artifacts",
                    thinking_stage="implementation_plan_5_2b",
                    prompt_name="module_artifacts_candidate_prompt",
                    messages=module_artifacts_candidate_messages(module_context),
                    candidate_key="module_artifacts_candidate",
                    report_key="module_artifacts_validation_report",
                    fallback=fallback_module_artifacts(draft, profile, constraints, selected_architecture),
                    validator=lambda candidate: validate_module_artifacts_candidate(candidate, selected_architecture, profile, constraints, draft),
                )
            else:
                module_candidate = inherited_stage_candidate(
                    stage_label="5.2b_module_artifacts",
                    candidate_key="module_artifacts_candidate",
                    validator=lambda candidate: validate_module_artifacts_candidate(candidate, selected_architecture, profile, constraints, draft),
                )
            draft = merge_module_artifacts(draft, module_candidate)
            if _should_stop_after("implementation_plan_5_2b", stop_after_stage):
                return finish_early("implementation_plan_5_2b")

            def log_controlled_inventory_attempts(
                result: dict[str, Any],
                *,
                candidate_log_prefix: str,
            ) -> None:
                module_id = str(result["module_id"])
                prompt_name = str(result["prompt_name"])
                log_key = f"{candidate_log_prefix}_{safe_slug(module_id)}"

                def log_candidate_record(record: dict[str, Any]) -> None:
                    attempt = int(record["request_index"])
                    store.write_agent_log(f"{log_key}_llm_attempt_{attempt}_meta", str(record["meta"]))
                    token_tracker.add_attempt(
                        stage="implementation_plan",
                        prompt_name=prompt_name,
                        attempt=attempt,
                        meta=record["meta"],
                        accepted=bool(record["accepted"]),
                    )
                    reasons = [str(reason) for reason in record.get("rejection_reasons", []) if str(reason)]
                    if reasons:
                        store.write_agent_log(f"{log_key}_llm_attempt_{attempt}_rejection", "\n".join(reasons))

                candidate_attempt_numbers = sorted(
                    {
                        int(record["candidate_attempt"])
                        for record in result["candidate_attempts"]
                    }
                )
                for candidate_attempt in candidate_attempt_numbers:
                    for record in result["candidate_attempts"]:
                        if int(record["candidate_attempt"]) == candidate_attempt:
                            log_candidate_record(record)

                stats = result.get("inventory_stats", {})
                store.write_agent_log(f"{log_key}_attempt_summary", json.dumps(stats, ensure_ascii=False, sort_keys=True))

            def inventory_attempt_summary(stage_name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
                return {
                    "schema_version": "controlled_inventory_attempt_summary/v1",
                    "stage": stage_name,
                    "max_full_retries": 0,
                    "max_validator_repairs": 0,
                    "max_json_retries": _INVENTORY_JSON_RETRIES,
                    "modules": [
                        {
                            "module_id": str(result.get("module_id", "")),
                            **dict(result.get("inventory_stats", {})),
                        }
                        for result in sorted(results, key=lambda item: item["module_index"])
                    ],
                }

            def record_controlled_inventory_stats(result: dict[str, Any]) -> None:
                records = [record for record in result.get("candidate_attempts", []) if isinstance(record, dict)]
                validated_records = [record for record in records if record.get("validated")]
                first_generation_passed = None
                if validated_records:
                    first_generation_passed = bool(validated_records[0].get("accepted")) and not result.get("fatal_diagnostics")
                first_error_index = next(
                    (
                        index
                        for index, record in enumerate(records)
                        if record.get("validator_error")
                    ),
                    None,
                )
                repair_attempted = first_error_index is not None and any(record.get("validated") for record in records[first_error_index + 1 :])
                repair_success = False
                if first_error_index is not None:
                    repair_success = any(record.get("validated") and record.get("accepted") for record in records[first_error_index + 1 :]) and not result.get("fatal_diagnostics")
                accepted_diags = result.get("accepted_diags", [])
                final_passed = not result.get("fatal_diagnostics") and not has_errors(accepted_diags)
                failure_diags = list(result.get("fatal_diagnostics", [])) or [diag for diag in accepted_diags if diag.level == "error"]
                stats = result.get("inventory_stats", {}) if isinstance(result.get("inventory_stats"), dict) else {}
                accepted_by = str(stats.get("accepted_by", "") or "")
                validator_stats.record(
                    stage_key=_implementation_stage_key_from_label(str(result.get("stage_label", ""))),
                    substage=str(result.get("stage_label", "")),
                    prompt_name=str(result.get("prompt_name", "")),
                    first_generation_passed=first_generation_passed,
                    validator_error_triggered_repair=first_error_index is not None,
                    repair_attempted=repair_attempted,
                    repair_success=repair_success,
                    final_validator_passed=final_passed,
                    fallback_used=accepted_by.startswith("deterministic_"),
                    failure_code=failure_diags[0].code if failure_diags else None,
                )

            if _implementation_plan_substage_should_run("implementation_plan_5_3", resume_from_stage):
                type_aggregate = {
                    "schema_version": "type_inventory_candidate/v1",
                    "candidate_id": "candidate:type_inventory:all_modules",
                    "producer": {
                        "stage": "5.3_type_data",
                        "prompt_name": "type_filling_candidate_prompt",
                        "prompt_version": "aggregate",
                    },
                    "module_id": "all_modules",
                    "types": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                }
                type_modules = [module for module in draft.get("module_artifacts", []) if isinstance(module, dict)]
                type_base_draft = draft

                def request_type_candidate(module_index: int, module: dict[str, Any]) -> dict[str, Any]:
                    module_id = str(module.get("module_id", ""))
                    context = build_type_inventory_context(type_base_draft, module, planning_ir)
                    planning_space = context["type_planning_space"]

                    def empty_type_filling() -> dict[str, Any]:
                        return {
                            "schema_version": "type_filling_candidate/v1",
                            "candidate_id": f"candidate:type_filling:{module_id}",
                            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "empty"},
                            "module_id": module_id,
                            "slot_fillings": [],
                            "optional_type_proposals": [],
                            "assumptions": [],
                            "unresolved_questions": [],
                            "expansion_notes": [],
                        }

                    result = _run_controlled_inventory_candidate(
                        module_index=module_index,
                        module=module,
                        stage_name="5.3_type_data",
                        stage_key="implementation_plan_5_3",
                        prompt_name="type_filling_candidate_prompt",
                        messages=type_filling_candidate_messages(compact_type_filling_context(context)),
                        request_config=_llm_config_for_stage(self.config, "implementation_plan_5_3"),
                        enable_thinking=self.config.llm_enable_thinking_for("implementation_plan_5_3"),
                        empty_llm_candidate=empty_type_filling,
                        validate_llm_candidate=lambda candidate: validate_type_filling_candidate(
                            candidate,
                            type_base_draft.get("module_artifacts", []),
                        ),
                        reconcile_candidate=lambda candidate: reconcile_type_filling_candidate(planning_space, candidate),
                        validate_final_candidate=lambda candidate: validate_type_inventory_candidate(
                            candidate,
                            type_base_draft.get("module_artifacts", []),
                            type_base_draft,
                            profile,
                            planning_ir,
                        ),
                        log_event=store.log_event,
                    )
                    result["planning_space"] = planning_space
                    return result

                type_results: list[dict[str, Any]] = []
                with ThreadPoolExecutor(max_workers=max(1, len(type_modules))) as executor:
                    future_map = {
                        executor.submit(request_type_candidate, module_index, module): module_index
                        for module_index, module in enumerate(type_modules)
                    }
                    for future in as_completed(future_map):
                        type_results.append(future.result())

                type_stage_failed = False
                for result in sorted(type_results, key=lambda item: item["module_index"]):
                    module_id = result["module_id"]
                    stage_label = result["stage_label"]
                    log_controlled_inventory_attempts(
                        result,
                        candidate_log_prefix="type_inventory_candidate",
                    )
                    record_controlled_inventory_stats(result)
                    planning_space_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["type_planning_space"], module_id),
                        result.get("planning_space", {}),
                    )
                    reconciliation = result.get("reconciliation", {})
                    type_candidate = result.get("accepted") or reconciliation.get("candidate", {})
                    type_candidate_diags = result["accepted_diags"]
                    reconciliation_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["type_reconciliation_report"], module_id),
                        reconciliation.get("reconciliation_report", {}),
                    )
                    diagnostics_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["type_inventory_diagnostics"], module_id),
                        {"schema_version": "inventory_diagnostics/v1", "stage": stage_label, "diagnostics": reconciliation.get("diagnostics", [])},
                    )
                    obligations_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["type_obligations"], module_id),
                        reconciliation.get("type_obligations", {}),
                    )
                    public_obligations_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["public_type_obligation_report"], module_id),
                        reconciliation.get("public_type_obligation_report", {}),
                    )
                    candidate_path = store.write_agent_json(_suffixed_step_filename(STEP_FILENAMES["type_inventory_candidate"], module_id), type_candidate)
                    report_path = store.write_step_json(
                        _suffixed_step_filename(STEP_FILENAMES["type_inventory_validation_report"], module_id),
                        validation_report(
                            f"5.3_type_data:{module_id}",
                            type_candidate_diags,
                            quality_diagnostics=reconciliation.get("diagnostics", []),
                            richness_summary=reconciliation.get("reconciliation_report", {}),
                        ),
                    )
                    artifact_suffix = safe_slug(module_id)
                    artifact_paths[f"type_planning_space_{artifact_suffix}"] = planning_space_path
                    artifact_paths[f"type_reconciliation_report_{artifact_suffix}"] = reconciliation_path
                    artifact_paths[f"type_inventory_diagnostics_{artifact_suffix}"] = diagnostics_path
                    artifact_paths[f"type_obligations_{artifact_suffix}"] = obligations_path
                    artifact_paths[f"public_type_obligation_report_{artifact_suffix}"] = public_obligations_path
                    artifact_paths[f"type_inventory_candidate_{artifact_suffix}"] = candidate_path
                    artifact_paths[f"type_inventory_validation_report_{artifact_suffix}"] = report_path
                    fatal_diags = result.get("fatal_diagnostics", [])
                    if fatal_diags:
                        diagnostics.extend(fatal_diags)
                        type_stage_failed = True
                        for diag in fatal_diags:
                            store.log_event(f"stage=implementation_plan substage={stage_label} fatal code={diag.code}")
                        continue
                    if has_errors(type_candidate_diags):
                        diagnostics.extend(type_candidate_diags)
                    type_aggregate["types"].extend(type_candidate.get("types", []))
                    type_aggregate["assumptions"].extend(type_candidate.get("assumptions", []))
                    type_aggregate["unresolved_questions"].extend(type_candidate.get("unresolved_questions", []))
                    draft = merge_type_inventory(draft, type_candidate)
                type_attempt_summary_path = _write_artifact_json(
                    store,
                    "type_inventory_attempt_summary",
                    STEP_FILENAMES["type_inventory_attempt_summary"],
                    inventory_attempt_summary("5.3_type_data", type_results),
                )
                artifact_paths["type_inventory_attempt_summary"] = type_attempt_summary_path
                if type_stage_failed:
                    return finish_early("implementation_plan_5_3")
                type_diags = validate_type_inventory_candidate(type_aggregate, draft.get("module_artifacts", []), draft, profile, planning_ir)
                type_path = store.write_step_json(STEP_FILENAMES["type_inventory_candidate"], type_aggregate)
                artifact_paths["type_inventory_candidate"] = type_path
                type_report_path = store.write_step_json(STEP_FILENAMES["type_inventory_validation_report"], validation_report("5.3_type_data:all_modules", type_diags))
                artifact_paths["type_inventory_validation_report"] = type_report_path
                if has_errors(type_diags):
                    diagnostics.extend(type_diags)
            else:
                type_aggregate = inherited_stage_candidate(
                    stage_label="5.3_type_data:all_modules",
                    candidate_key="type_inventory_candidate",
                    validator=lambda candidate: validate_type_inventory_candidate(candidate, draft.get("module_artifacts", []), draft, profile, planning_ir),
                )
                draft = merge_type_inventory(draft, type_aggregate)
            if _should_stop_after("implementation_plan_5_3", stop_after_stage):
                return finish_early("implementation_plan_5_3")

            if _implementation_plan_substage_should_run("implementation_plan_5_4a", resume_from_stage):
                inventory_aggregate = {
                    "schema_version": "function_inventory_candidate/v2",
                    "candidate_id": "candidate:function_inventory:all_modules",
                    "producer": {
                        "stage": "5.4a_function_inventory",
                        "prompt_name": "function_annotation_candidate_prompt",
                        "prompt_version": "aggregate",
                    },
                    "module_id": "all_modules",
                    "functions": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                }
                inventory_modules = [module for module in draft.get("module_artifacts", []) if isinstance(module, dict)]
                inventory_draft = draft

                def request_inventory_candidate(module_index: int, module: dict[str, Any]) -> dict[str, Any]:
                    module_id = str(module.get("module_id", ""))
                    enable_thinking = self.config.llm_enable_thinking_for("implementation_plan_5_4a")
                    request_config = _llm_config_for_stage(self.config, "implementation_plan_5_4a")
                    planning_space = build_function_planning_space(inventory_draft, module, planning_ir, profile, constraints)
                    context = build_function_inventory_context(inventory_draft, module, planning_ir, profile, constraints)
                    context["function_planning_space"] = planning_space

                    def empty_function_annotation() -> dict[str, Any]:
                        return {
                            "schema_version": "function_annotation_candidate/v1",
                            "candidate_id": f"candidate:function_annotation:{module_id}",
                            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "empty"},
                            "module_id": module_id,
                            "seed_annotations": [],
                            "optional_function_proposals": [],
                            "assumptions": [],
                            "unresolved_questions": [],
                            "decomposition_notes": [],
                        }

                    result = _run_controlled_inventory_candidate(
                        module_index=module_index,
                        module=module,
                        stage_name="5.4a_function_inventory",
                        stage_key="implementation_plan_5_4a",
                        prompt_name="function_annotation_candidate_prompt",
                        messages=function_annotation_candidate_messages(compact_function_annotation_context(context)),
                        request_config=request_config,
                        enable_thinking=enable_thinking,
                        empty_llm_candidate=empty_function_annotation,
                        validate_llm_candidate=lambda candidate: validate_function_annotation_candidate(
                            candidate,
                            inventory_draft.get("module_artifacts", []),
                        ),
                        reconcile_candidate=lambda candidate: reconcile_function_annotation_candidate(planning_space, candidate),
                        validate_final_candidate=lambda candidate: validate_function_inventory_candidate(
                            candidate,
                            inventory_draft.get("module_artifacts", []),
                            inventory_draft,
                            profile,
                            planning_ir,
                        ),
                        log_event=store.log_event,
                    )
                    result["planning_space"] = planning_space
                    if result.get("fatal_diagnostics"):
                        result["coverage_report"] = {}
                        return result
                    result["coverage_report"] = function_inventory_decomposition_report(result["accepted"], inventory_draft.get("module_artifacts", []), inventory_draft)
                    result["function_family_obligation_report"] = result.get("reconciliation", {}).get("function_family_obligation_report", {})
                    return result

                inventory_results: list[dict[str, Any]] = []
                with ThreadPoolExecutor(max_workers=max(1, len(inventory_modules))) as executor:
                    future_map = {
                        executor.submit(request_inventory_candidate, module_index, module): module_index
                        for module_index, module in enumerate(inventory_modules)
                    }
                    for future in as_completed(future_map):
                        inventory_results.append(future.result())

                inventory_stage_failed = False
                for result in sorted(inventory_results, key=lambda item: item["module_index"]):
                    module_id = result["module_id"]
                    stage_label = result["stage_label"]
                    log_controlled_inventory_attempts(
                        result,
                        candidate_log_prefix="function_inventory_candidate",
                    )
                    record_controlled_inventory_stats(result)
                    fatal_diags = result.get("fatal_diagnostics", [])
                    reconciliation = result.get("reconciliation", {})
                    inventory_candidate = result.get("accepted") or reconciliation.get("candidate")
                    if inventory_candidate is None:
                        inventory_stage_failed = True
                        diagnostics.append(
                            PlanningDiagnostic(
                                "error",
                                "implementation_plan_5_4a_missing_candidate",
                                f"{stage_label} did not produce an accepted candidate.",
                                stage_label,
                            )
                        )
                        continue
                    log_key = f"function_inventory_candidate_{safe_slug(module_id)}"
                    store.write_agent_log(f"{log_key}_decomposition_coverage_report", str(result.get("coverage_report", {})))
                    store.write_agent_log(f"{log_key}_function_family_obligation_report", str(result.get("function_family_obligation_report", {})))
                    planning_space_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["function_planning_space"], module_id),
                        result.get("planning_space", {}),
                    )
                    reconciliation_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["function_reconciliation_report"], module_id),
                        reconciliation.get("reconciliation_report", {}),
                    )
                    diagnostics_path = store.write_agent_json(
                        _suffixed_step_filename(STEP_FILENAMES["function_inventory_diagnostics"], module_id),
                        {
                            "schema_version": "inventory_diagnostics/v1",
                            "stage": stage_label,
                            "diagnostics": reconciliation.get("diagnostics", []),
                            "decomposition_coverage": result.get("coverage_report", {}),
                            "function_family_obligations": result.get("function_family_obligation_report", {}),
                        },
                    )
                    candidate_path = store.write_agent_json(_suffixed_step_filename(STEP_FILENAMES["function_inventory_candidate"], module_id), inventory_candidate)
                    report_path = store.write_step_json(
                        _suffixed_step_filename(STEP_FILENAMES["function_inventory_validation_report"], module_id),
                        validation_report(
                            stage_label,
                            result["accepted_diags"],
                            quality_diagnostics=[
                                *reconciliation.get("diagnostics", []),
                                result.get("coverage_report", {}),
                                result.get("function_family_obligation_report", {}),
                            ],
                            richness_summary=reconciliation.get("reconciliation_report", {}),
                        ),
                    )
                    artifact_suffix = safe_slug(module_id)
                    artifact_paths[f"function_planning_space_{artifact_suffix}"] = planning_space_path
                    artifact_paths[f"function_reconciliation_report_{artifact_suffix}"] = reconciliation_path
                    artifact_paths[f"function_inventory_diagnostics_{artifact_suffix}"] = diagnostics_path
                    artifact_paths[f"function_inventory_candidate_{artifact_suffix}"] = candidate_path
                    artifact_paths[f"function_inventory_validation_report_{artifact_suffix}"] = report_path
                    if fatal_diags:
                        diagnostics.extend(fatal_diags)
                        inventory_stage_failed = True
                        for diag in fatal_diags:
                            store.log_event(f"stage=implementation_plan substage={stage_label} fatal code={diag.code}")
                        continue
                    inventory_aggregate["functions"].extend(inventory_candidate.get("functions", []))
                    inventory_aggregate["assumptions"].extend(inventory_candidate.get("assumptions", []))
                    inventory_aggregate["unresolved_questions"].extend(inventory_candidate.get("unresolved_questions", []))
                    draft = merge_function_inventory(draft, inventory_candidate)
                inventory_attempt_summary_path = _write_artifact_json(
                    store,
                    "function_inventory_attempt_summary",
                    STEP_FILENAMES["function_inventory_attempt_summary"],
                    inventory_attempt_summary("5.4a_function_inventory", inventory_results),
                )
                artifact_paths["function_inventory_attempt_summary"] = inventory_attempt_summary_path
                if inventory_stage_failed:
                    return finish_early("implementation_plan_5_4a")
                draft = reconcile_type_inventory_function_refs(draft)
                draft, inventory_aggregate, symbol_repair_report = repair_function_inventory_symbols(draft, inventory_aggregate)
                symbol_repair_report_path = store.write_agent_json("007_5_4a_function_symbol_repair_report.json", symbol_repair_report)
                artifact_paths["function_symbol_repair_report"] = symbol_repair_report_path
                draft = reconcile_type_inventory_function_refs(draft)
                inventory_diags = validate_function_inventory_candidate(inventory_aggregate, draft.get("module_artifacts", []), draft, profile, planning_ir)
                inventory_path = store.write_step_json(STEP_FILENAMES["function_inventory_candidate"], inventory_aggregate)
                artifact_paths["function_inventory_candidate"] = inventory_path
                inventory_report_path = store.write_step_json(STEP_FILENAMES["function_inventory_validation_report"], validation_report("5.4a_function_inventory:all_modules", inventory_diags))
                artifact_paths["function_inventory_validation_report"] = inventory_report_path
                if has_errors(inventory_diags):
                    diagnostics.extend(inventory_diags)
                    return finish_early("implementation_plan_5_4a")
            else:
                inventory_aggregate = inherited_stage_candidate(
                    stage_label="5.4a_function_inventory:all_modules",
                    candidate_key="function_inventory_candidate",
                    validator=lambda candidate: validate_function_inventory_candidate(candidate, draft.get("module_artifacts", []), draft, profile, planning_ir),
                )
                draft = merge_function_inventory(draft, inventory_aggregate)
                draft = reconcile_type_inventory_function_refs(draft)
                inventory_diags = validate_function_inventory_candidate(inventory_aggregate, draft.get("module_artifacts", []), draft, profile, planning_ir)
                inventory_report_path = store.write_step_json(STEP_FILENAMES["function_inventory_validation_report"], validation_report("5.4a_function_inventory:all_modules", inventory_diags))
                artifact_paths["function_inventory_validation_report"] = inventory_report_path
                if has_errors(inventory_diags):
                    diagnostics.extend(inventory_diags)
                    return finish_early("implementation_plan_5_4a")
            if _should_stop_after("implementation_plan_5_4a", stop_after_stage):
                return finish_early("implementation_plan_5_4a")

            if _implementation_plan_substage_should_run("implementation_plan_5_4b", resume_from_stage):
                signature_aggregate = {
                    "schema_version": "function_signature_patch/v1",
                    "patch_id": "patch:function_signatures:all_modules",
                    "producer": {
                        "stage": "5.4b_function_signatures",
                        "prompt_name": "function_signature_patch_prompt",
                        "prompt_version": "aggregate",
                    },
                    "module_id": "all_modules",
                    "batch": {"index": 0, "size": 0},
                    "function_signature_updates": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                }
                for module in list(draft.get("module_artifacts", [])):
                    module_id = str(module.get("module_id", ""))
                    module_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict) and str(item.get("module_id")) == module_id]
                    signature_batch_size = self.config.module_scoped_batch_size_for("implementation_plan_5_4b")
                    batches = [module_functions[index:index + signature_batch_size] for index in range(0, len(module_functions), signature_batch_size)] or [[]]
                    for batch_index, batch in enumerate(batches):
                        expected_ids = {str(item.get("function_id", "")) for item in batch if isinstance(item, dict)}
                        signature_context = build_function_signature_context(draft, module_id, batch, batch_index=batch_index, batch_size=signature_batch_size)
                        signature_patch = stage_candidate(
                            stage_label=f"5.4b_function_signatures:{module_id}:{batch_index}",
                            thinking_stage="implementation_plan_5_4b",
                            prompt_name="function_signature_patch_prompt",
                            messages=function_signature_patch_messages(signature_context),
                            candidate_key="function_signature_patch",
                            report_key="function_signature_validation_report",
                            fallback=fallback_function_signatures(draft, module_id, batch, batch_index=batch_index, batch_size=signature_batch_size),
                            validator=lambda candidate, ids=expected_ids: validate_function_signature_patch(candidate, draft, ids),
                            normalizer=lambda candidate, module_id=module_id, batch=batch, batch_index=batch_index, batch_size=signature_batch_size: normalize_function_signature_patch(
                                candidate,
                                draft,
                                module_id,
                                batch,
                                batch_index=batch_index,
                                batch_size=batch_size,
                            ),
                            step_log_suffix=f"{module_id}__batch_{batch_index}",
                        )
                        signature_aggregate["function_signature_updates"].extend(signature_patch.get("function_signature_updates", []))
                        signature_aggregate["assumptions"].extend(signature_patch.get("assumptions", []))
                        signature_aggregate["unresolved_questions"].extend(signature_patch.get("unresolved_questions", []))
                        draft = merge_function_signatures(draft, signature_patch)
                signature_aggregate["batch"]["size"] = len(signature_aggregate["function_signature_updates"])
                signature_diags = validate_function_signature_patch(signature_aggregate, draft, {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)})
                signature_path = _write_artifact_json(store, "function_signature_patch", STEP_FILENAMES["function_signature_patch"], signature_aggregate)
                artifact_paths["function_signature_patch"] = signature_path
                signature_report_path = store.write_step_json(STEP_FILENAMES["function_signature_validation_report"], validation_report("5.4b_function_signatures:all_modules", signature_diags))
                artifact_paths["function_signature_validation_report"] = signature_report_path
                if has_errors(signature_diags):
                    diagnostics.extend(signature_diags)
            else:
                signature_aggregate = inherited_stage_candidate(
                    stage_label="5.4b_function_signatures:all_modules",
                    candidate_key="function_signature_patch",
                    validator=lambda candidate: validate_function_signature_patch(candidate, draft, {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)}),
                )
                draft = merge_function_signatures(draft, signature_aggregate)
            if _should_stop_after("implementation_plan_5_4b", stop_after_stage):
                return finish_early("implementation_plan_5_4b")

            if _implementation_plan_substage_should_run("implementation_plan_5_4c", resume_from_stage):
                behavior_aggregate = {
                    "schema_version": "function_behavior_contract_patch/v1",
                    "patch_id": "patch:function_behavior:all_modules",
                    "producer": {
                        "stage": "5.4c_behavior_contract",
                        "prompt_name": "function_behavior_contract_patch_prompt",
                        "prompt_version": "aggregate",
                    },
                    "module_id": "all_modules",
                    "batch": {"index": 0, "size": 0},
                    "function_behavior_updates": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                }
                behavior_batch_size = self.config.module_scoped_batch_size_for("implementation_plan_5_4c")
                for module in list(draft.get("module_artifacts", [])):
                    module_id = str(module.get("module_id", ""))
                    module_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict) and str(item.get("module_id")) == module_id]
                    batches = [module_functions[index:index + behavior_batch_size] for index in range(0, len(module_functions), behavior_batch_size)] or [[]]
                    for batch_index, batch in enumerate(batches):
                        expected_ids = {str(item.get("function_id", "")) for item in batch if isinstance(item, dict)}
                        behavior_context = build_function_behavior_context(draft, module_id, batch, constraints, batch_index=batch_index, batch_size=behavior_batch_size)
                        behavior_patch = stage_candidate(
                            stage_label=f"5.4c_behavior_contract:{module_id}:{batch_index}",
                            thinking_stage="implementation_plan_5_4c",
                            prompt_name="function_behavior_contract_patch_prompt",
                            messages=function_behavior_contract_patch_messages(behavior_context),
                            candidate_key="function_behavior_patch",
                            report_key="function_behavior_validation_report",
                            fallback=fallback_function_behavior(draft, module_id, batch, batch_index=batch_index, batch_size=behavior_batch_size),
                            validator=lambda candidate, ids=expected_ids: validate_function_behavior_contract_patch(candidate, draft, constraints, ids),
                            normalizer=lambda candidate: normalize_function_behavior_contract_patch(candidate, draft, constraints),
                            step_log_suffix=f"{module_id}__batch_{batch_index}",
                        )
                        behavior_aggregate["function_behavior_updates"].extend(behavior_patch.get("function_behavior_updates", []))
                        behavior_aggregate["assumptions"].extend(behavior_patch.get("assumptions", []))
                        behavior_aggregate["unresolved_questions"].extend(behavior_patch.get("unresolved_questions", []))
                        draft = merge_function_behavior(draft, behavior_patch)
                behavior_aggregate["batch"]["size"] = len(behavior_aggregate["function_behavior_updates"])
                behavior_diags = validate_function_behavior_contract_patch(behavior_aggregate, draft, constraints, {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)})
                behavior_path = _write_artifact_json(store, "function_behavior_patch", STEP_FILENAMES["function_behavior_patch"], behavior_aggregate)
                artifact_paths["function_behavior_patch"] = behavior_path
                behavior_report_path = store.write_step_json(STEP_FILENAMES["function_behavior_validation_report"], validation_report("5.4c_behavior_contract:all_modules", behavior_diags))
                artifact_paths["function_behavior_validation_report"] = behavior_report_path
                if has_errors(behavior_diags):
                    diagnostics.extend(behavior_diags)
            else:
                behavior_aggregate = inherited_stage_candidate(
                    stage_label="5.4c_behavior_contract:all_modules",
                    candidate_key="function_behavior_patch",
                    validator=lambda candidate: validate_function_behavior_contract_patch(candidate, draft, constraints, {str(item.get("function_id", "")) for item in draft.get("function_contracts", []) if isinstance(item, dict)}),
                )
                draft = merge_function_behavior(draft, behavior_aggregate)
            if _should_stop_after("implementation_plan_5_4c", stop_after_stage):
                return finish_early("implementation_plan_5_4c")

            if _implementation_plan_substage_should_run("implementation_plan_5_4d", resume_from_stage):
                wire_context = build_wire_access_binding_context(draft, planning_ir)
                wire_patch = stage_candidate(
                    stage_label="5.4d_wire_access_binding",
                    thinking_stage="implementation_plan_5_4d",
                    prompt_name="wire_access_binding_patch_prompt",
                    messages=wire_access_binding_patch_messages(wire_context),
                    candidate_key="wire_access_binding_patch",
                    report_key="wire_access_binding_validation_report",
                    fallback=fallback_wire_access_binding(draft, planning_ir),
                    validator=lambda candidate: validate_wire_access_binding_patch(candidate, draft, planning_ir),
                )
            else:
                wire_patch = inherited_stage_candidate(
                    stage_label="5.4d_wire_access_binding",
                    candidate_key="wire_access_binding_patch",
                    validator=lambda candidate: validate_wire_access_binding_patch(candidate, draft, planning_ir),
                )
            draft = merge_wire_access_binding(draft, wire_patch)
            if _should_stop_after("implementation_plan_5_4d", stop_after_stage):
                return finish_early("implementation_plan_5_4d")

            if _implementation_plan_substage_should_run("implementation_plan_5_4e", resume_from_stage):
                calls_aggregate = {
                    "schema_version": "calls_allowed_candidate/v2",
                    "candidate_id": "candidate:calls_allowed:all_modules",
                    "producer": {
                        "stage": "5.4e_call_contracts",
                        "prompt_name": "calls_allowed_candidate_prompt",
                        "prompt_version": "aggregate",
                    },
                    "call_updates": [],
                    "unresolved_service_requirements": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                }
                calls_batch_size = self.config.module_scoped_batch_size_for("implementation_plan_5_4e")
                for module in list(draft.get("module_artifacts", [])):
                    module_id = str(module.get("module_id", ""))
                    module_functions = [item for item in draft.get("function_contracts", []) if isinstance(item, dict) and str(item.get("module_id")) == module_id]
                    batches = [module_functions[index:index + calls_batch_size] for index in range(0, len(module_functions), calls_batch_size)] or [[]]
                    for batch_index, batch in enumerate(batches):
                        expected_ids = {str(item.get("function_id", "")) for item in batch if isinstance(item, dict)}
                        expected_service_ids = {
                            str(requirement.get("service_requirement_id", ""))
                            for function in batch
                            for requirement in function.get("service_requirements", [])
                            if isinstance(requirement, dict)
                            and str(requirement.get("requirement_kind", "cross_module_service")) == "cross_module_service"
                            and str(requirement.get("service_requirement_id", ""))
                        }
                        calls_fallback = fallback_calls_allowed(draft, batch, batch_index=batch_index, batch_size=calls_batch_size)
                        stage_label = f"5.4e_call_contracts:{module_id}:{batch_index}"
                        if expected_service_ids:
                            calls_context = build_calls_allowed_context(draft, selected_architecture, module_id, batch, batch_index=batch_index, batch_size=calls_batch_size)
                            callable_ids = {str(item.get("function_id", "")) for item in calls_context.get("callable_functions", []) if isinstance(item, dict)}
                            callable_ids.update(
                                str(function_id)
                                for item in calls_context.get("candidate_provider_functions", [])
                                if isinstance(item, dict)
                                for function_id in item.get("provider_function_ids", [])
                                if str(function_id).strip()
                            )
                            calls_candidate = stage_candidate(
                                stage_label=stage_label,
                                thinking_stage="implementation_plan_5_4e",
                                prompt_name="calls_allowed_candidate_prompt",
                                messages=calls_allowed_candidate_messages(calls_context),
                                candidate_key="calls_allowed_candidate",
                                report_key="calls_allowed_validation_report",
                                fallback=calls_fallback,
                                validator=lambda candidate, ids=expected_ids, service_ids=expected_service_ids, call_ids=callable_ids: validate_calls_allowed_candidate(
                                    candidate,
                                    draft,
                                    selected_architecture,
                                    expected_caller_ids=ids,
                                    expected_service_requirement_ids=service_ids,
                                    callable_function_ids=call_ids,
                                ),
                                normalizer=lambda candidate, ids=expected_ids, service_ids=expected_service_ids, call_ids=callable_ids, fallback=calls_fallback: normalize_calls_allowed_candidate(
                                    candidate,
                                    draft,
                                    ids,
                                    service_ids,
                                    call_ids,
                                    fallback,
                                ),
                                step_log_suffix=f"{module_id}__batch_{batch_index}",
                            )
                        else:
                            calls_candidate = calls_fallback
                            batch_diags = validate_calls_allowed_candidate(
                                calls_candidate,
                                draft,
                                selected_architecture,
                                expected_caller_ids=expected_ids,
                                expected_service_requirement_ids=expected_service_ids,
                                callable_function_ids=set(),
                            )
                            artifact_suffix = safe_slug(f"{module_id}__batch_{batch_index}")
                            candidate_filename = _suffixed_step_filename(STEP_FILENAMES["calls_allowed_candidate"], f"{module_id}__batch_{batch_index}")
                            report_filename = _suffixed_step_filename(STEP_FILENAMES["calls_allowed_validation_report"], f"{module_id}__batch_{batch_index}")
                            artifact_paths[f"calls_allowed_candidate_{artifact_suffix}"] = store.write_agent_json(candidate_filename, calls_candidate)
                            artifact_paths[f"calls_allowed_validation_report_{artifact_suffix}"] = store.write_step_json(report_filename, validation_report(stage_label, batch_diags))
                            validator_stats.record(
                                stage_key="implementation_plan_5_4e",
                                substage=stage_label,
                                prompt_name="calls_allowed_candidate_prompt",
                                first_generation_passed=True,
                                final_validator_passed=stage_passed(batch_diags),
                                fallback_used=True,
                            )
                            store.log_event(f"stage=implementation_plan substage={stage_label} llm=skipped reason=no_cross_module_service")
                        calls_aggregate["call_updates"].extend(calls_candidate.get("call_updates", []))
                        calls_aggregate["unresolved_service_requirements"].extend(calls_candidate.get("unresolved_service_requirements", []))
                        calls_aggregate["assumptions"].extend(calls_candidate.get("assumptions", []))
                        calls_aggregate["unresolved_questions"].extend(calls_candidate.get("unresolved_questions", []))
                calls_aggregate["unresolved_service_requirements"] = sorted({str(item) for item in calls_aggregate["unresolved_service_requirements"] if str(item)})
                calls_aggregate, aggregate_call_stats = normalize_calls_allowed_aggregate(calls_aggregate, draft)
                if any(aggregate_call_stats.values()):
                    store.log_event(
                        "stage=implementation_plan substage=5.4e_call_contracts:all_modules normalized "
                        + " ".join(f"{key}={value}" for key, value in sorted(aggregate_call_stats.items()) if value)
                    )
                calls_diags = validate_calls_allowed_candidate(calls_aggregate, draft, selected_architecture)
                calls_path = store.write_step_json(STEP_FILENAMES["calls_allowed_candidate"], calls_aggregate)
                artifact_paths["calls_allowed_candidate"] = calls_path
                calls_report_path = store.write_step_json(STEP_FILENAMES["calls_allowed_validation_report"], validation_report("5.4e_call_contracts:all_modules", calls_diags))
                artifact_paths["calls_allowed_validation_report"] = calls_report_path
                if has_errors(calls_diags):
                    diagnostics.extend(calls_diags)
            else:
                calls_aggregate = inherited_stage_candidate(
                    stage_label="5.4e_call_contracts:all_modules",
                    candidate_key="calls_allowed_candidate",
                    validator=lambda candidate: validate_calls_allowed_candidate(candidate, draft, selected_architecture),
                )
            draft = merge_calls_allowed(draft, calls_aggregate)
            if _should_stop_after("implementation_plan_5_4e", stop_after_stage):
                return finish_early("implementation_plan_5_4e")

            if _implementation_plan_substage_should_run("implementation_plan_5_5a", resume_from_stage):
                file_candidate, file_norm_stats = normalize_file_layout_candidate(fallback_file_layout(draft), draft)
                file_diags = validate_file_layout_candidate(file_candidate, draft)
                mechanical_warning = any(diag.code == "mechanical_single_file_module_layout" for diag in file_diags)
                needs_override = _file_layout_override_actionable(file_candidate, file_diags)
                first_generation_passed = stage_passed(file_diags)
                repair_attempted = False
                repair_success = False
                if needs_override:
                    repair_attempted = True
                    file_context = build_file_layout_override_context(draft, file_candidate, file_diags)
                    file_config = _llm_config_for_stage(self.config, "implementation_plan_5_5a")
                    file_thinking = self.config.llm_enable_thinking_for("implementation_plan_5_5a")
                    store.log_event(
                        f"stage=implementation_plan substage=5.5a_file_layout llm_attempt=1 prompt=file_layout_override_patch_prompt "
                        f"thinking={str(file_thinking).lower()} start"
                    )
                    override_patch, override_llm_diags, override_meta = request_json_candidate(
                        prompt_name="file_layout_override_patch_prompt",
                        messages=file_layout_override_patch_messages(file_context),
                        config=file_config,
                        enable_thinking=file_thinking,
                    )
                    token_tracker.add_attempt(stage="implementation_plan", prompt_name="file_layout_override_patch_prompt", attempt=1, meta=override_meta)
                    store.write_agent_log("007_5_5a_file_layout_override_patch_llm_attempt_1_meta", str(override_meta))
                    store.log_event(f"stage=implementation_plan substage=5.5a_file_layout llm_attempt=1 prompt=file_layout_override_patch_prompt {_llm_token_event(override_meta)}")
                    if override_patch is None:
                        reasons = _diagnostic_reasons(override_llm_diags) or ["LLM did not return a file layout override patch."]
                        store.write_agent_log("007_5_5a_file_layout_override_patch_llm_attempt_1_rejection", "\n".join(reasons))
                    else:
                        override_diags = validate_file_layout_override_patch(override_patch, file_candidate, draft)
                        if has_errors(override_diags):
                            reasons = _diagnostic_reasons(override_diags)
                            store.write_agent_log("007_5_5a_file_layout_override_patch_llm_attempt_1_rejection", "\n".join(reasons))
                        else:
                            store.write_agent_json("007_5_5a_file_layout_override_patch.json", override_patch)
                            override_candidate, file_norm_stats = normalize_file_layout_candidate(apply_file_layout_override_patch(file_candidate, override_patch, draft), draft)
                            override_candidate_diags = validate_file_layout_candidate(override_candidate, draft)
                            if not has_errors(override_candidate_diags):
                                file_candidate = override_candidate
                                file_diags = override_candidate_diags
                                repair_success = True
                                token_tracker.mark_attempt_accepted(stage="implementation_plan", prompt_name="file_layout_override_patch_prompt", attempt=1)
                                store.log_event("stage=implementation_plan substage=5.5a_file_layout override=accepted")
                            else:
                                reasons = _diagnostic_reasons(override_candidate_diags)
                                store.write_agent_log("007_5_5a_file_layout_override_patch_candidate_rejection", "\n".join(reasons))
                else:
                    reason = "override_cannot_add_file_units" if mechanical_warning else "deterministic_baseline_valid"
                    store.log_event(f"stage=implementation_plan substage=5.5a_file_layout llm=skipped reason={reason}")
                if any(file_norm_stats.values()):
                    store.log_event(
                        "stage=implementation_plan substage=5.5a_file_layout normalized "
                        + " ".join(f"{key}={value}" for key, value in sorted(file_norm_stats.items()) if value)
                    )
                file_path = _write_artifact_json(store, "file_layout_candidate", STEP_FILENAMES["file_layout_candidate"], file_candidate)
                artifact_paths["file_layout_candidate"] = file_path
                file_report_path = store.write_step_json(STEP_FILENAMES["file_layout_validation_report"], validation_report("5.5a_file_layout", file_diags))
                artifact_paths["file_layout_validation_report"] = file_report_path
                validator_stats.record(
                    stage_key="implementation_plan_5_5a",
                    substage="5.5a_file_layout",
                    prompt_name="file_layout_override_patch_prompt" if needs_override else "",
                    first_generation_passed=first_generation_passed,
                    validator_error_triggered_repair=needs_override,
                    repair_attempted=repair_attempted,
                    repair_success=repair_success,
                    final_validator_passed=stage_passed(file_diags),
                    fallback_used=not repair_success,
                    failure_code=next((diag.code for diag in file_diags if diag.level == "error"), None),
                )
                if has_errors(file_diags):
                    diagnostics.extend(file_diags)
            else:
                file_candidate = inherited_stage_candidate(
                    stage_label="5.5a_file_layout",
                    candidate_key="file_layout_candidate",
                    validator=lambda candidate: validate_file_layout_candidate(candidate, draft),
                )
            draft = merge_file_layout(draft, file_candidate)
            if _should_stop_after("implementation_plan_5_5a", stop_after_stage):
                return finish_early("implementation_plan_5_5a")

            if _implementation_plan_substage_should_run("implementation_plan_5_5b", resume_from_stage):
                runtime_context = build_runtime_entrypoint_context(draft, planning_ir, selected_architecture)
                runtime_candidate = stage_candidate(
                    stage_label="5.5b_runtime_entrypoint",
                    thinking_stage="implementation_plan_5_5b",
                    prompt_name="runtime_entrypoint_candidate_prompt",
                    messages=runtime_entrypoint_candidate_messages(runtime_context),
                    candidate_key="runtime_entrypoint_candidate",
                    report_key="runtime_entrypoint_validation_report",
                    fallback=fallback_runtime_entrypoint(draft),
                    validator=lambda candidate: validate_runtime_entrypoint_candidate(candidate, draft),
                )
            else:
                runtime_candidate = inherited_stage_candidate(
                    stage_label="5.5b_runtime_entrypoint",
                    candidate_key="runtime_entrypoint_candidate",
                    validator=lambda candidate: validate_runtime_entrypoint_candidate(candidate, draft),
                )
            draft = merge_runtime_entrypoint(draft, runtime_candidate)
            if _should_stop_after("implementation_plan_5_5b", stop_after_stage):
                return finish_early("implementation_plan_5_5b")

            implementation_plan = cleanup_final_unresolved_questions(finalize_dependency_graph(draft))
            dependency_diags = validate_dependency_graph(implementation_plan)
            if _implementation_plan_substage_should_run("implementation_plan_5_6", resume_from_stage) and has_errors(dependency_diags):
                repair_context = build_dependency_repair_context(draft, _diagnostics_as_dependency_errors(dependency_diags))
                repair_patch = stage_candidate(
                    stage_label="5.6_dependency_generation",
                    thinking_stage="implementation_plan_5_6",
                    prompt_name="dependency_repair_patch_prompt",
                    messages=dependency_repair_patch_messages(repair_context),
                    candidate_key="dependency_repair_patch",
                    report_key="dependency_repair_validation_report",
                    fallback=fallback_dependency_repair_patch(draft, _diagnostics_as_dependency_errors(dependency_diags)),
                    validator=lambda candidate: validate_dependency_repair_patch(candidate, draft),
                )
                draft = apply_dependency_repair_patch(draft, repair_patch)
                implementation_plan = cleanup_final_unresolved_questions(finalize_dependency_graph(draft))
                dependency_diags = validate_dependency_graph(implementation_plan)
                if has_errors(dependency_diags):
                    draft = apply_deterministic_dependency_fallback(draft, _diagnostics_as_dependency_errors(dependency_diags))
                    implementation_plan = cleanup_final_unresolved_questions(finalize_dependency_graph(draft))

            implementation_plan_path = store.write_step_json(STEP_FILENAMES["implementation_plan"], implementation_plan)
            artifact_paths["implementation_plan"] = implementation_plan_path
            if _should_stop_after("implementation_plan_5_6", stop_after_stage):
                return finish_early("implementation_plan_5_6")

            plan_diags = validate_full_implementation_plan(implementation_plan, profile=profile, planning_ir=planning_ir, path=str(implementation_plan_path))
            dependency_diags = validate_dependency_graph(implementation_plan, path=str(implementation_plan_path))
            dependency_report = build_dependency_validation_report(implementation_plan, dependency_diags)
            dependency_report_path = store.write_step_json(STEP_FILENAMES["dependency_validation_report"], dependency_report)
            artifact_paths["dependency_validation_report"] = dependency_report_path
            layout_runtime_report, layout_runtime_diags = build_layout_runtime_mapping_report(implementation_plan)
            layout_runtime_report_path = store.write_step_json(STEP_FILENAMES["layout_runtime_mapping_report"], layout_runtime_report)
            artifact_paths["layout_runtime_mapping_report"] = layout_runtime_report_path
            diagnostics.extend(plan_diags)
            diagnostics.extend(dependency_diags)
            diagnostics.extend(layout_runtime_diags)
            final_readiness_passed = not has_errors([*plan_diags, *dependency_diags, *layout_runtime_diags])
            readiness_errors = [diag for diag in [*plan_diags, *dependency_diags, *layout_runtime_diags] if diag.level == "error"]
            validator_stats.record(
                stage_key="implementation_plan_5_7",
                substage="5.7_spec_readiness_validation",
                source="generated",
                first_generation_passed=final_readiness_passed,
                final_validator_passed=final_readiness_passed,
                failure_code=readiness_errors[0].code if readiness_errors else None,
            )
            store.log_event("stage=implementation_plan substage=5.7_spec_readiness_validation build done")
            if has_errors(diagnostics):
                store.log_event("stage=implementation_plan build done status=failed")
                return finish_run("implementation_plan", status="failed", failure={"stage": "implementation_plan", "code": "validation_errors"})
            store.log_event("stage=implementation_plan build done")
            if _should_stop_after("implementation_plan_5_7", stop_after_stage):
                return finish_early("implementation_plan_5_7")

        else:
            implementation_plan = inherited_artifacts["implementation_plan"]
            store.log_event("stage=implementation_plan resume inherited")
        if _should_stop_after("implementation_plan", stop_after_stage) or _should_stop_after("implementation_plan_5_7", stop_after_stage):
            return finish_early(stop_after_stage or "implementation_plan")

        if _stage_should_run("specs_compile", resume_from_stage):
            store.log_event("stage=specs_compile start")
            if "layout_runtime_mapping_report" not in artifact_paths:
                layout_runtime_report, layout_runtime_diags = build_layout_runtime_mapping_report(implementation_plan)
                artifact_paths["layout_runtime_mapping_report"] = store.write_step_json(STEP_FILENAMES["layout_runtime_mapping_report"], layout_runtime_report)
                diagnostics.extend(layout_runtime_diags)
                if has_errors(layout_runtime_diags):
                    store.log_event("stage=specs_compile blocked by layout_runtime_mapping_report")
                    return finish_run("specs_compile", status="failed", failure={"stage": "specs_compile", "code": "layout_runtime_mapping_errors"})
            coder_manifest, coder_manifest_path = compile_spec_bundle(implementation_plan, self.output_dir)
            artifact_paths["coder_manifest"] = coder_manifest_path
            spec_root = Path(coder_manifest["spec_root"])
            artifact_paths["spec_bundle"] = spec_root
            if "dependency_validation_report" in artifact_paths:
                dependency_report = read_json(artifact_paths["dependency_validation_report"])
                if not all(key in dependency_report for key in ("dependency_sources", "call_edge_sources", "errors", "warnings")):
                    dependency_report = build_dependency_validation_report(implementation_plan, validate_dependency_graph(implementation_plan))
                dependency_report = attach_coder_dependency_sources(dependency_report, spec_root)
                artifact_paths["dependency_validation_report"] = store.write_step_json(STEP_FILENAMES["dependency_validation_report"], dependency_report)
            coder_diags = validate_coder_compatibility(spec_root)
            diagnostics.extend(coder_diags)
            coder_status = "failed" if has_errors(coder_diags) else "passed"
            coder_schema_status = "failed" if any(item.level == "error" and item.code.startswith("coder_schema_") for item in coder_diags) else "passed"
            coder_loader_status = "failed" if any(item.level == "error" and not item.code.startswith("coder_schema_") for item in coder_diags) else "passed"
            status = "failed" if has_errors(diagnostics) else "stopped" if _should_stop_after("specs_compile", stop_after_stage) else "success"
            store.log_event(f"stage=specs_compile done coder_status={coder_status}")

            return finish_run(
                "specs_compile",
                status=status,
                coder_compatibility_status=coder_status,
                coder_schema_status=coder_schema_status,
                coder_loader_status=coder_loader_status,
                failure={"stage": "specs_compile", "code": "validation_errors"} if status == "failed" else None,
            )
        return finish_run("planning", status="success")


def verify_output_dir(output_dir: str | Path) -> PlanningResult:
    root = Path(output_dir).expanduser()
    validation_reports = root / "_validation_reports"
    diagnostics: list[PlanningDiagnostic] = []
    artifact_paths: dict[str, Path] = {}
    required = {
        "planning_run_manifest": root / "_step_logs" / STEP_FILENAMES["planning_run_manifest"],
        "planning_ir": root / "_step_logs" / STEP_FILENAMES["planning_ir"],
        "protocol_profile": root / "_step_logs" / STEP_FILENAMES["protocol_profile"],
        "engineering_constraints": root / "_step_logs" / STEP_FILENAMES["engineering_constraints"],
        "architecture_context": root / "_step_logs" / STEP_FILENAMES["architecture_context"],
        "architecture_candidates": root / "_step_logs" / STEP_FILENAMES["architecture_candidates"],
        "architecture_ranking": root / "_step_logs" / STEP_FILENAMES["architecture_ranking"],
        "selected_architecture": root / "_step_logs" / STEP_FILENAMES["selected_architecture"],
        "implementation_plan": root / "_step_logs" / STEP_FILENAMES["implementation_plan"],
        "dependency_validation_report": validation_reports / STEP_FILENAMES["dependency_validation_report"],
        "planning_repair_statistics": root / "_step_logs" / STEP_FILENAMES["planning_repair_statistics"],
        "token_usage_summary": root / "_step_logs" / STEP_FILENAMES["token_usage_summary"],
        "coder_manifest": root / "coder_manifest.json",
        "spec_bundle": root / "spec_bundle",
        "planning_validation_report": validation_reports / STEP_FILENAMES["planning_validation_report"],
    }
    for key, path in required.items():
        if path.exists():
            artifact_paths[key] = path
        else:
            diagnostics.append(PlanningDiagnostic("error", "missing_artifact", f"Missing artifact '{key}'", str(path)))
    if "planning_ir" in artifact_paths:
        diagnostics.extend(validate_planning_ir(read_json(artifact_paths["planning_ir"]), path=str(artifact_paths["planning_ir"])))
    if "protocol_profile" in artifact_paths:
        diagnostics.extend(validate_protocol_profile(read_json(artifact_paths["protocol_profile"]), path=str(artifact_paths["protocol_profile"])))
    if "engineering_constraints" in artifact_paths:
        diagnostics.extend(validate_constraints(read_json(artifact_paths["engineering_constraints"]), path=str(artifact_paths["engineering_constraints"])))
    if "architecture_candidates" in artifact_paths and "protocol_profile" in artifact_paths and "engineering_constraints" in artifact_paths:
        profile = read_json(artifact_paths["protocol_profile"])
        constraints = read_json(artifact_paths["engineering_constraints"])
        architecture_candidates = read_json(artifact_paths["architecture_candidates"])
        diagnostics.extend(validate_architecture_candidates(architecture_candidates, profile, constraints, path=str(artifact_paths["architecture_candidates"])))
        if "architecture_ranking" in artifact_paths:
            diagnostics.extend(
                validate_architecture_ranking(
                    read_json(artifact_paths["architecture_ranking"]),
                    architecture_candidates,
                    profile,
                    constraints,
                    path=str(artifact_paths["architecture_ranking"]),
                )
            )
        if "selected_architecture" in artifact_paths:
            diagnostics.extend(validate_selected_architecture(read_json(artifact_paths["selected_architecture"]), profile, constraints, path=str(artifact_paths["selected_architecture"])))
    if "implementation_plan" in artifact_paths:
        implementation_plan = read_json(artifact_paths["implementation_plan"])
        profile = read_json(artifact_paths["protocol_profile"]) if "protocol_profile" in artifact_paths else None
        planning_ir = read_json(artifact_paths["planning_ir"]) if "planning_ir" in artifact_paths else None
        if profile is not None and planning_ir is not None:
            diagnostics.extend(validate_full_implementation_plan(implementation_plan, profile=profile, planning_ir=planning_ir, path=str(artifact_paths["implementation_plan"])))
        diagnostics.extend(validate_dependency_graph(implementation_plan, path=str(artifact_paths["implementation_plan"])))
    if "spec_bundle" in artifact_paths:
        diagnostics.extend(validate_coder_compatibility(artifact_paths["spec_bundle"]))
    return PlanningResult(not has_errors(diagnostics), root, diagnostics, artifact_paths)


def compare_output_to_reference(output_dir: str | Path, reference_spec_root: str | Path) -> PlanningResult:
    root = Path(output_dir).expanduser()
    reference = Path(reference_spec_root).expanduser()
    diagnostics: list[PlanningDiagnostic] = []
    artifact_paths: dict[str, Path] = {
        "spec_bundle": root / "spec_bundle",
        "reference_spec_bundle": reference,
    }
    try:
        from agent.coder.specs import load_spec_bundle_from_root

        planning_bundle = load_spec_bundle_from_root(root / "spec_bundle")
        reference_bundle = load_spec_bundle_from_root(reference)
    except Exception as exc:  # noqa: BLE001
        diagnostics.append(PlanningDiagnostic("error", "regression_loader_failed", f"Could not load regression bundles: {exc}"))
        return PlanningResult(False, root, diagnostics, artifact_paths)

    for item in planning_bundle.diagnostics:
        if item.level == "error":
            diagnostics.append(PlanningDiagnostic("error", f"planning_{item.code}", item.message, item.path))
    for item in reference_bundle.diagnostics:
        if item.level == "error":
            diagnostics.append(PlanningDiagnostic("error", f"reference_{item.code}", item.message, item.path))
    if planning_bundle.protocol.name != reference_bundle.protocol.name:
        diagnostics.append(
            PlanningDiagnostic(
                "warning",
                "regression_protocol_name_differs",
                f"planning protocol '{planning_bundle.protocol.name}' differs from reference '{reference_bundle.protocol.name}'",
            )
        )
    planning_modules = {module.name for module in planning_bundle.modules_in_order}
    reference_modules = {module.name for module in reference_bundle.modules_in_order}
    if not planning_modules:
        diagnostics.append(PlanningDiagnostic("error", "regression_planning_no_modules", "planning spec bundle contains no modules"))
    if not reference_modules:
        diagnostics.append(PlanningDiagnostic("error", "regression_reference_no_modules", "reference spec bundle contains no modules"))
    missing_reference_modules = sorted(reference_modules - planning_modules)
    extra_planning_modules = sorted(planning_modules - reference_modules)
    if missing_reference_modules:
        diagnostics.append(
            PlanningDiagnostic(
                "warning",
                "regression_reference_modules_not_matched",
                f"Reference modules not matched by baseline: {', '.join(missing_reference_modules)}",
            )
        )
    if extra_planning_modules:
        diagnostics.append(
            PlanningDiagnostic(
                "warning",
                "regression_planning_extra_modules",
                f"planning modules not present in reference: {', '.join(extra_planning_modules)}",
            )
        )
    return PlanningResult(not has_errors(diagnostics), root, diagnostics, artifact_paths)
