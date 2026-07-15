from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .compiler import compile_specs, normalize_plan_for_compiler
from .facts import collect_top_level_fact_refs, read_json, stable_json_hash, write_json
from .implementability import (
    analyze_implementability,
    apply_semantic_patch,
    build_semantic_patch_slice,
    closure_diagnostics_as_models,
    complete_deterministic_dependencies,
    merge_semantic_patches,
    request_semantic_patch,
    retain_valid_semantic_operations,
    validate_semantic_patch,
)
from .knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from .metrics import build_run_metrics
from .models import Diagnostic, PlanningResult, to_jsonable
from .planner import (
    LLMStructuredPlanner,
    PLANNING_STAGES,
    WHOLE_FRESH_TOKEN_CEILING,
    RecoverablePlanningError,
    build_planning_context,
)
from .registry import CanonicalPlanningRegistry, RegistryInvariantError, register_semantic_patch_additions
from .validation import diagnostics_to_json, validate_planning_run
from .validation_layers import (
    classify_hard_failure,
    semantic_layer_summary,
    validate_deterministic_change_log,
)


QUALIFIED = "completed_with_qualified_specs"
CANDIDATE_ONLY = "completed_with_candidate_only"
FAILED_INTERNAL = "failed_internal"


def _as_diag(level: str, code: str, message: str, path: str | None = None) -> Diagnostic:
    return Diagnostic(
        level,
        code,
        message,
        path,
        "pipeline",
        "pipeline_runtime",
        "inspect_fatal_classification_or_resume_from_last_committed_stage",
    )


def _dedupe_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for diagnostic in diagnostics:
        identity = (diagnostic.level, diagnostic.code, diagnostic.message, diagnostic.path)
        if identity not in seen:
            seen.add(identity)
            out.append(diagnostic)
    return out


def _distinct_unresolved_partitions(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        identity = (
            str(item.get("stage_id", "")),
            str(item.get("partition_id", "")),
            str(item.get("diagnostic", "")),
        )
        if identity not in seen:
            seen.add(identity)
            out.append(item)
    return out


def _distinct_diagnostic_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in values:
        identity = tuple(str(item.get(key, "")) for key in ("level", "code", "message", "path"))
        if identity not in seen:
            seen.add(identity)
            out.append(item)
    return out


def _readiness_manifest_fields(
    plan: dict[str, Any],
    specs_root: Path,
    diagnostics: list[Diagnostic],
    *,
    run_status: str,
    coder_loader_passed: bool | None,
) -> dict[str, Any]:
    spec_objects: list[dict[str, Any]] = []
    if specs_root.exists():
        for path in sorted(specs_root.rglob("*_spec.json")):
            value = read_json(path)
            if isinstance(value, dict):
                spec_objects.append(value)
    function_specs = {
        str(item.get("TRACE_ID")): item
        for item in spec_objects
        if item.get("KIND") == "FUNCTION_SPEC"
    }
    type_names = {
        str(item.get("NAME"))
        for spec in spec_objects
        if spec.get("KIND") == "FILE_SPEC"
        for block in (spec.get("HEADER"), spec.get("SOURCE"))
        if isinstance(block, dict)
        for item in block.get("DATA", [])
        if isinstance(item, dict) and item.get("KIND") == "TYPE"
    }
    required_functions = [item for item in plan.get("functions", []) if isinstance(item, dict)]
    required_types = [item for item in plan.get("types", []) if isinstance(item, dict)]

    wire_required = 0
    wire_materialized = 0
    for function in required_functions:
        targets = function.get("wire_obligation", {}).get("targets", []) if isinstance(function.get("wire_obligation"), dict) else []
        emitted = function_specs.get(str(function.get("trace_id")), {})
        mappings = emitted.get("WIRE_MAPPING", []) if isinstance(emitted, dict) else []
        for target in targets if isinstance(targets, list) else []:
            if not isinstance(target, dict):
                continue
            wire_required += 1
            if any(
                isinstance(mapping, dict)
                and str(mapping.get("PACKET", mapping.get("packet", ""))) == str(target.get("packet", ""))
                and str(mapping.get("WIRE_FIELD", mapping.get("wire_field", ""))) == str(target.get("wire_field", ""))
                and (not target.get("rule") or str(mapping.get("RULE", mapping.get("rule", ""))).strip())
                for mapping in mappings if isinstance(mappings, list)
            ):
                wire_materialized += 1

    tests_required = len(required_functions) + (1 if plan.get("test_obligations") else 0)
    tests_materialized = sum(
        bool(function_specs.get(str(function.get("trace_id")), {}).get("TEST_VECTORS"))
        for function in required_functions
    ) + (1 if plan.get("test_obligations") and any(
        item.get("KIND") == "PROTOCOL_MODULE_SPEC" and item.get("TEST_VECTORS")
        for item in spec_objects
    ) else 0)

    error_diagnostics = [item for item in diagnostics if item.level == "error"]
    unresolved = _distinct_unresolved_partitions(plan.get("unresolved_partitions", []))
    plan_drop_count = sum(item.code.startswith("plan_to_spec_") for item in error_diagnostics)
    runtime_error_count = sum(
        item.code.startswith("runtime_entrypoint")
        or item.code.startswith("runtime_flow")
        or item.code.startswith("runtime_call_chain")
        or item.code.startswith("runtime_failure_cleanup")
        for item in error_diagnostics
    )
    metrics = {
        "required_function_count": len(required_functions),
        "materialized_function_spec_count": sum(
            str(item.get("trace_id")) in function_specs for item in required_functions
        ),
        "required_type_count": len(required_types),
        "materialized_type_count": sum(str(item.get("name")) in type_names for item in required_types),
        "required_wire_target_count": wire_required,
        "materialized_wire_target_count": wire_materialized,
        "required_test_surface_count": tests_required,
        "materialized_test_surface_count": tests_materialized,
        "plan_to_spec_required_field_drop_count": plan_drop_count,
        "runtime_entrypoint_or_flow_error_count": runtime_error_count,
        "runtime_contract_materialized": bool(
            isinstance(plan.get("runtime_entrypoint"), dict)
            and plan.get("runtime_entrypoint")
            and isinstance(plan.get("lifecycle_matrix"), list)
            and plan.get("lifecycle_matrix")
            and isinstance(plan.get("runtime_flow"), dict)
            and plan.get("runtime_flow")
        ),
        "union_error_diagnostic_count": len(error_diagnostics),
        "distinct_unresolved_partition_count": len(unresolved),
        "attempt_unresolved_partition_count": len(plan.get("unresolved_partitions", [])),
    }
    artifact_success = bool(
        specs_root.exists()
        and coder_loader_passed is True
        and not unresolved
    )
    semantic_qualified = bool(
        run_status == QUALIFIED and not error_diagnostics and not unresolved
    )
    implementation_ready = bool(
        artifact_success
        and semantic_qualified
        and plan.get("required_implementation_obligations")
        and metrics["required_function_count"] > 0
        and metrics["required_function_count"] == metrics["materialized_function_spec_count"]
        and metrics["required_type_count"] > 0
        and metrics["required_type_count"] == metrics["materialized_type_count"]
        and metrics["required_wire_target_count"] == metrics["materialized_wire_target_count"]
        and metrics["required_test_surface_count"] == metrics["materialized_test_surface_count"]
        and metrics["plan_to_spec_required_field_drop_count"] == 0
        and metrics["runtime_entrypoint_or_flow_error_count"] == 0
        and metrics["runtime_contract_materialized"]
    )

    def counts(values: list[Diagnostic]) -> dict[str, int]:
        return {
            "error": sum(item.level == "error" for item in values),
            "warning": sum(item.level == "warning" for item in values),
        }

    return {
        "artifact_success": artifact_success,
        "semantic_qualified": semantic_qualified,
        "implementation_ready": implementation_ready,
        "readiness_metrics": metrics,
        "closure_diagnostic_counts": counts(
            [item for item in diagnostics if item.owner_layer == "semantic_closure"]
        ),
        "post_validation_diagnostic_counts": counts(
            [item for item in diagnostics if item.owner_layer == "post_planning_validation"]
        ),
        "union_diagnostic_counts": counts(diagnostics),
    }


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _write_candidate_package(
    planning_root: Path,
    candidate_root: Path,
    *,
    facts_path: Path,
    facts_hash: str,
    run_status: str,
    plan: dict[str, Any] | None,
    unresolved_partitions: list[dict[str, Any]],
    diagnostics: list[Diagnostic],
    candidate_specs_root: Path | None,
) -> Path:
    candidate_root.mkdir(parents=True, exist_ok=True)
    stage_root = planning_root / "stage_logs"
    records_path = stage_root / "stage_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8")) if records_path.exists() else []
    committed: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") != "completed":
            continue
        matches = sorted(stage_root.glob(f"??_{record.get('stage_id', '')}/artifact.json"))
        if matches:
            committed.append({"stage_id": record["stage_id"], "artifact": read_json(matches[-1])})
    typed_path = planning_root / "typed_stage_overlays.json"
    if typed_path.exists():
        typed = read_json(typed_path)
        if isinstance(typed.get("overlays"), list):
            committed = typed["overlays"]

    registry = (plan or {}).get("canonical_registry_snapshot")
    registry_path = planning_root / "canonical_registry_snapshot.json"
    if not isinstance(registry, dict) and registry_path.exists():
        registry = read_json(registry_path)
    if not isinstance(registry, dict):
        registry = {
            "kind": "CANONICAL_PLANNING_SYMBOL_REGISTRY",
            "schema_version": 1,
            "status": "pending_registry_migration",
            "entries": [],
        }
    write_json(candidate_root / "canonical_registry_snapshot.json", registry)
    write_json(candidate_root / "committed_overlays.json", committed)
    write_json(candidate_root / "unresolved_partitions.json", unresolved_partitions)
    write_json(candidate_root / "blocking_diagnostics.json", diagnostics_to_json(diagnostics))
    write_json(
        candidate_root / "provenance.json",
        {"facts_path": str(facts_path), "facts_sha256": facts_hash, "implementation_plan": str(planning_root / "implementation_plan.json") if plan else None},
    )
    manifest = {
        "kind": "CANDIDATE_PLANNING_PACKAGE",
        "schema_version": 1,
        "run_status": run_status,
        "registry_snapshot": str(candidate_root / "canonical_registry_snapshot.json"),
        "committed_overlays": str(candidate_root / "committed_overlays.json"),
        "unresolved_partitions": str(candidate_root / "unresolved_partitions.json"),
        "blocking_diagnostics": str(candidate_root / "blocking_diagnostics.json"),
        "provenance": str(candidate_root / "provenance.json"),
        "metrics": str(planning_root / "run_metrics.json"),
        "typed_stage_overlays": str(typed_path) if typed_path.exists() else None,
        "inventory_amendments": str(planning_root / "inventory_amendments.json")
        if (planning_root / "inventory_amendments.json").exists()
        else None,
        "validation_layers": str(planning_root / "validation_layers.json")
        if (planning_root / "validation_layers.json").exists()
        else None,
        "stage_manifests": [str(path) for path in sorted(stage_root.glob("*/stage_manifest.json"))],
        "partition_manifests": [str(path) for path in sorted(stage_root.glob("*/*/partition_manifest.json"))],
        "candidate_specs_root": str(candidate_specs_root) if candidate_specs_root is not None else None,
    }
    path = candidate_root / "manifest.json"
    write_json(path, manifest)
    return path


def _relocate_compile_manifest(manifest: dict[str, Any], source: Path, destination: Path) -> dict[str, Any]:
    source_text = str(source)
    destination_text = str(destination)

    def relocate(value: Any) -> Any:
        if isinstance(value, str) and (value == source_text or value.startswith(source_text + "/")):
            return destination_text + value[len(source_text) :]
        if isinstance(value, list):
            return [relocate(item) for item in value]
        return value

    return {key: relocate(value) for key, value in manifest.items()}


def _write_failed_manifest(
    planning_root: Path,
    *,
    facts_path: Path,
    facts_hash: str,
    planner_mode: str,
    error: BaseException,
) -> Path:
    planning_root.mkdir(parents=True, exist_ok=True)
    hard_failure_code = classify_hard_failure(error, facts_read=facts_hash == "")
    diagnostic = _as_diag("error", hard_failure_code, f"{type(error).__name__}: {error}")
    write_json(planning_root / "diagnostics.json", diagnostics_to_json([diagnostic]))
    path = planning_root / "run_manifest.json"
    write_json(
        path,
        {
            "kind": "PLANNING_RUN_MANIFEST",
            "facts_path": str(facts_path),
            "facts_sha256": facts_hash,
            "planner_mode": planner_mode,
            "run_status": FAILED_INTERNAL,
            "hard_failure_code": hard_failure_code,
            "fatal": True,
            "fatal_reason_code": hard_failure_code,
            "nonfatal_no_specs_count": 0,
            "candidate_root": None,
            "candidate_specs_root": None,
            "specs_root": None,
            "module_spec": None,
            "summary": None,
            "written_files": [],
            "specs_generated": False,
            "artifact_success": False,
            "semantic_qualified": False,
            "implementation_ready": False,
            "planning_validation_passed": False,
            "coder_loader_passed": None,
            "qualification_passed": False,
            "semantic_diagnostic_counts": {"error": 0, "warning": 0},
            "closure_diagnostic_counts": {"error": 0, "warning": 0},
            "post_validation_diagnostic_counts": {"error": 0, "warning": 0},
            "union_diagnostic_counts": {"error": 1, "warning": 0},
            "unresolved_stage_partition_count": 0,
            "unresolved_stage_partition_attempt_count": 0,
            "diagnostic_counts": {"error": 1, "warning": 0},
        },
    )
    return path


def _record_internal_failure(
    planning_root: Path,
    *,
    facts_path: Path,
    facts_hash: str,
    planner_mode: str,
    stage_records: list[dict[str, Any]],
    error: BaseException,
) -> None:
    hard_failure_code = classify_hard_failure(error, facts_read=facts_hash == "")
    _write_failed_manifest(
        planning_root,
        facts_path=facts_path,
        facts_hash=facts_hash,
        planner_mode=planner_mode,
        error=error,
    )
    write_json(
        planning_root / "run_metrics.json",
        build_run_metrics(
            stage_records,
            expected_stages=len(PLANNING_STAGES),
            run_status=FAILED_INTERNAL,
            diagnostics=[{"code": hard_failure_code, "message": str(error)}],
        ),
    )


def _coder_validate(specs_root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    try:
        from agent.coder.specs import load_spec_bundle_from_root

        bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        for diag in bundle.diagnostics:
            diagnostics.append(
                Diagnostic(
                    diag.level,
                    f"coder_{diag.code}",
                    diag.message,
                    diag.path,
                    "coder_loader",
                    "compile_specs",
                    "regenerate_authoritative_planning_partition",
                )
            )
    except Exception as exc:  # pragma: no cover - defensive path, tested by end-to-end command.
        diagnostics.append(_as_diag("error", "coder_validation_exception", f"{type(exc).__name__}: {exc}", str(specs_root)))
    return diagnostics


def _write_planning_artifacts(
    planning_root: Path,
    facts_path: Path,
    facts_hash: str,
    fact_refs: list[dict[str, Any]],
    characteristics: Any,
    rules: Any,
    assumptions: Any,
    context: dict[str, Any],
    plan: dict[str, Any],
    compile_manifest: dict[str, Any],
    diagnostics: list[Diagnostic],
    planner_mode: str,
    implementability_report: Path,
    semantic_patch_usage: dict[str, int],
    run_status: str,
    candidate_root: Path,
    candidate_specs_root: Path,
    planning_validation_passed: bool,
    coder_loader_passed: bool | None,
    semantic_diagnostics: list[dict[str, Any]],
    resume_from: str | None,
) -> Path:
    write_json(planning_root / "fact_refs.json", fact_refs)
    write_json(planning_root / "normalized_characteristics.json", to_jsonable(characteristics))
    write_json(planning_root / "activated_engineering_rules.json", [to_jsonable(rule) for rule in rules])
    write_json(planning_root / "planning_context.json", context)
    write_json(planning_root / "selected_architecture.json", plan.get("architecture", {}))
    write_json(planning_root / "structured_planning_stages.json", plan.get("structured_planning_stages", []))
    write_json(planning_root / "structured_planning_usage.json", plan.get("structured_planning_usage", {}))
    write_json(planning_root / "implementation_plan.json", plan)
    write_json(planning_root / "engineering_decisions.json", plan.get("engineering_decisions", []))
    write_json(planning_root / "open_assumptions.json", [to_jsonable(item) for item in assumptions])
    write_json(planning_root / "diagnostics.json", diagnostics_to_json(diagnostics))
    metrics = read_json(planning_root / "run_metrics.json")
    manifest = {
        "kind": "PLANNING_RUN_MANIFEST",
        "facts_path": str(facts_path),
        "facts_sha256": facts_hash,
        "planner_mode": planner_mode,
        "run_status": run_status,
        "fatal": False,
        "fatal_reason_code": None,
        "nonfatal_no_specs_count": 0,
        "candidate_root": str(candidate_root),
        "candidate_specs_root": str(candidate_specs_root),
        "specs_root": compile_manifest["specs_root"],
        "module_spec": compile_manifest["module_spec"],
        "summary": compile_manifest["summary"],
        "semantic_mapping": compile_manifest.get("semantic_mapping"),
        "written_files": compile_manifest["written_files"],
        "specs_generated": True,
        "planning_validation_passed": planning_validation_passed,
        "coder_loader_passed": coder_loader_passed,
        "qualification_passed": run_status == QUALIFIED,
        "semantic_diagnostic_counts": {
            "error": sum(item.get("level") == "error" for item in semantic_diagnostics),
            "warning": sum(item.get("level") == "warning" for item in semantic_diagnostics),
        },
        "unresolved_stage_partition_count": len(
            _distinct_unresolved_partitions(plan.get("unresolved_partitions", []))
        ),
        "unresolved_stage_partition_attempt_count": len(plan.get("unresolved_partitions", [])),
        "stage_survival": metrics.get("stage_survival", {}),
        "partition_survival": metrics.get("partition_survival", {}),
        "token_accounting": metrics.get("token_accounting", {}),
        "token_accounting_complete": bool(metrics.get("token_accounting", {}).get("complete")),
        "fresh": resume_from is None,
        "resume": resume_from is not None,
        "resume_from": resume_from,
        "replay": False,
        "implementability_report": str(implementability_report),
        "semantic_patch_usage": semantic_patch_usage,
        "diagnostic_counts": {
            "error": sum(1 for diag in diagnostics if diag.level == "error"),
            "warning": sum(1 for diag in diagnostics if diag.level == "warning"),
        },
    }
    manifest.update(
        _readiness_manifest_fields(
            plan,
            Path(compile_manifest["specs_root"]),
            diagnostics,
            run_status=run_status,
            coder_loader_passed=coder_loader_passed,
        )
    )
    manifest_path = planning_root / "run_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def _add_usage(total: dict[str, int], item: dict[str, int]) -> None:
    for key in total:
        total[key] += int(item.get(key, 0))


def _apply_registry_additions(plan: dict[str, Any], patch: dict[str, Any]) -> None:
    snapshot = plan.get("canonical_registry_snapshot")
    if not isinstance(snapshot, dict):
        return
    registry = CanonicalPlanningRegistry.from_snapshot(snapshot)
    register_semantic_patch_additions(registry, plan, patch)
    plan["canonical_registry_snapshot"] = registry.snapshot()


def _close_implementability(
    plan: dict[str, Any],
    context: dict[str, Any],
    planning_root: Path,
    *,
    api_key_env: str,
    resume_from: str | None,
    planning_tokens_used: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    semantic_root = planning_root / "semantic_closure"
    semantic_root.mkdir(parents=True, exist_ok=True)
    if resume_from != "compile_specs":
        for pattern in (
            "01_semantic_patch_*",
            "02_patch_correction_*",
            "applied_patch.json",
            "primary_patch_*",
            "correction_patch_*",
        ):
            for path in semantic_root.glob(pattern):
                path.unlink()
    normalized = normalize_plan_for_compiler(plan)
    deterministic_changes = complete_deterministic_dependencies(normalized)
    validate_deterministic_change_log(deterministic_changes)
    initial_diagnostics = analyze_implementability(normalized)
    stage_positions = {
        stage_id: index
        for index, stage_id in enumerate(
            (
                "scope_fact_inventory",
                "architecture_boundaries",
                "module_file_plan",
                "public_artifact_inventory",
                "type_and_access_path_design",
                "function_interface_design",
                "function_behavior_design",
                "function_call_contract_closure",
                "function_test_vector_design",
                "dependency_closure",
                "final_plan_assembly",
            )
        )
    }
    compile_critical = [item for item in initial_diagnostics if item.get("compile_critical") is True]
    if compile_critical:
        earliest_position = min(
            stage_positions.get(str(item.get("authoritative_stage", "")), len(stage_positions))
            for item in compile_critical
        )
        targeted_diagnostics = [
            item
            for item in compile_critical
            if stage_positions.get(str(item.get("authoritative_stage", "")), len(stage_positions)) == earliest_position
        ]
    else:
        targeted_diagnostics = initial_diagnostics
    write_json(semantic_root / "initial_diagnostics.json", initial_diagnostics)
    write_json(semantic_root / "targeted_diagnostics.json", targeted_diagnostics)
    write_json(semantic_root / "deterministic_completion.json", deterministic_changes)
    payload = build_semantic_patch_slice(normalized, context, targeted_diagnostics)
    write_json(semantic_root / "input_slice.json", payload)
    related_artifact_ids = {
        str(item.get("id", ""))
        for values in payload.get("related_artifacts", {}).values()
        for item in values
        if isinstance(item, dict) and item.get("id")
    }
    targeted_artifact_ids = {
        str(value)
        for item in targeted_diagnostics
        for value in item.get("artifact_ids", [])
        if value
    }

    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_characters": 0,
        "request_count": 0,
    }
    patch: dict[str, Any] | None = None
    patch_errors: list[str] = []
    reused_patch = False
    applied_patch_path = semantic_root / "applied_patch.json"

    def diagnostic_identity(item: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        return str(item.get("code", "")), tuple(sorted(str(value) for value in item.get("artifact_ids", [])))

    initial_identities = {diagnostic_identity(item) for item in initial_diagnostics}
    targeted_identities = {diagnostic_identity(item) for item in targeted_diagnostics}

    def validate_candidate(candidate: dict[str, Any]) -> list[str]:
        errors = validate_semantic_patch(normalized, candidate)
        operations = candidate.get("operations", [])
        for index, operation in enumerate(operations if isinstance(operations, list) else []):
            if not isinstance(operation, dict):
                continue
            artifact_id = str(operation.get("artifact_id", ""))
            if operation.get("op") == "update" and artifact_id not in related_artifact_ids:
                errors.append(f"operations[{index}] updates artifact outside targeted diagnostic slice: {artifact_id}")
            if operation.get("op") == "add" and targeted_artifact_ids.isdisjoint(
                str(value) for value in operation.get("affected_artifact_ids", [])
            ):
                errors.append(f"operations[{index}] adds artifact without a targeted affected artifact")
        try:
            trial = apply_semantic_patch(normalized, candidate)
            _apply_registry_additions(trial, candidate)
            trial = normalize_plan_for_compiler(trial)
            complete_deterministic_dependencies(trial)
            residual = analyze_implementability(trial)
        except (KeyError, TypeError, ValueError, RegistryInvariantError) as exc:
            return [*errors, f"patch application failed: {type(exc).__name__}: {exc}"]
        blocking_residual = [
            item
            for item in residual
            if diagnostic_identity(item) in targeted_identities
            or (item.get("compile_critical") is True and diagnostic_identity(item) not in initial_identities)
        ]
        return [
            *errors,
            *[f"residual closure {item['code']}: {item['message']}" for item in blocking_residual],
        ]

    def add_logged_usage(prefix: str) -> None:
        path = semantic_root / f"{prefix}_usage.json"
        if path.exists():
            _add_usage(usage, read_json(path))

    if initial_diagnostics and resume_from == "compile_specs":
        candidate_paths = [
            applied_patch_path,
            semantic_root / "01_semantic_patch_response.raw.txt",
            semantic_root / "02_patch_correction_response.raw.txt",
        ]
        for candidate_path in candidate_paths:
            if not candidate_path.exists():
                continue
            reused_patch = True
            try:
                raw_candidate = candidate_path.read_text(encoding="utf-8").strip()
                if raw_candidate.startswith("```"):
                    lines = raw_candidate.splitlines()[1:]
                    if lines and lines[-1].startswith("```"):
                        lines.pop()
                    raw_candidate = "\n".join(lines).strip()
                candidate = json.loads(raw_candidate)
                candidate_errors = validate_candidate(candidate)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                candidate_errors = [f"stored patch candidate invalid: {type(exc).__name__}: {exc}"]
                candidate = {}
            patch = candidate
            patch_errors = candidate_errors
            if not patch_errors:
                break
        if patch is None:
            patch = {}
            patch_errors = ["no stored semantic patch candidate is available for compile_specs resume"]
    elif initial_diagnostics and resume_from != "compile_specs":
        try:
            patch, call_usage = request_semantic_patch(
                payload,
                api_key_env=api_key_env,
                log_dir=semantic_root,
                tokens_used=planning_tokens_used,
                token_ceiling=WHOLE_FRESH_TOKEN_CEILING,
            )
            _add_usage(usage, call_usage)
            patch_errors = validate_candidate(patch)
        except (ValueError, TypeError) as exc:
            add_logged_usage("01_semantic_patch")
            patch = {}
            patch_errors = [f"{type(exc).__name__}: {exc}"]
        if patch_errors:
            write_json(semantic_root / "primary_patch_validation_errors.json", patch_errors)
            retained_patch, rejected_operations = retain_valid_semantic_operations(normalized, patch)
            write_json(semantic_root / "primary_patch_retained.json", retained_patch)
            write_json(semantic_root / "primary_patch_rejected_operations.json", rejected_operations)
            correction_groups: dict[str, list[str]] = {}
            structural_feedback: list[str] = []
            for error in patch_errors:
                match = re.search(r"residual closure ([a-z][a-z0-9_]+):", error)
                if match:
                    correction_groups.setdefault(match.group(1), []).append(error)
                else:
                    structural_feedback.append(error)
            if correction_groups and structural_feedback:
                next(iter(correction_groups.values()))[:0] = structural_feedback
            elif structural_feedback:
                correction_groups["patch_structure"] = structural_feedback
            patch = retained_patch
            correction_records: list[dict[str, Any]] = []
            for index, (group, group_errors) in enumerate(list(correction_groups.items())[:1], 1):
                partition = f"{index:02d}_{group}" if len(correction_groups) > 1 else None
                prefix = f"02_patch_correction_{partition}" if partition else "02_patch_correction"
                try:
                    correction, correction_usage = request_semantic_patch(
                        payload,
                        api_key_env=api_key_env,
                        log_dir=semantic_root,
                        previous_patch=patch,
                        validation_errors=group_errors,
                        correction_partition=partition,
                        tokens_used=planning_tokens_used + usage["total_tokens"],
                        token_ceiling=WHOLE_FRESH_TOKEN_CEILING,
                    )
                    _add_usage(usage, correction_usage)
                    patch = merge_semantic_patches(patch, correction)
                    structural_errors = validate_semantic_patch(normalized, patch)
                    correction_records.append(
                        {"partition": partition or "all", "diagnostic_group": group, "errors": structural_errors}
                    )
                    if structural_errors:
                        patch_errors = structural_errors
                        break
                except (ValueError, TypeError) as exc:
                    add_logged_usage(prefix)
                    patch_errors = [f"{type(exc).__name__}: {exc}"]
                    correction_records.append(
                        {"partition": partition or "all", "diagnostic_group": group, "errors": patch_errors}
                    )
                    break
            else:
                patch_errors = validate_candidate(patch)
            write_json(semantic_root / "correction_partitions.json", correction_records)
            if patch_errors:
                write_json(semantic_root / "correction_patch_validation_errors.json", patch_errors)
                write_json(
                    semantic_root / "correction_patch_failure.json",
                    {"kind": "validation_failure", "errors": patch_errors},
                )

    if patch is not None and not patch_errors:
        normalized = apply_semantic_patch(normalized, patch)
        _apply_registry_additions(normalized, patch)
        normalized = normalize_plan_for_compiler(normalized)
        deterministic_changes.extend(complete_deterministic_dependencies(normalized))
        validate_deterministic_change_log(deterministic_changes)
        write_json(applied_patch_path, patch)
    final_diagnostics = analyze_implementability(normalized)
    if patch_errors:
        final_diagnostics.append(
            {
                "level": "error",
                "code": "semantic_patch_invalid",
                "message": "; ".join(patch_errors),
                "artifact_ids": [],
                "details": {"validation_errors": patch_errors},
            }
        )
    report = {
        "kind": "IMPLEMENTABILITY_REPORT",
        "initial_diagnostics": initial_diagnostics,
        "targeted_diagnostics": targeted_diagnostics,
        "deterministic_completion": deterministic_changes,
        "semantic_patch_attempted": patch is not None and not reused_patch,
        "semantic_patch_reused": reused_patch,
        "semantic_patch_validation_errors": patch_errors,
        "semantic_patch_usage": usage,
        "final_diagnostics": final_diagnostics,
        "validation_layers": semantic_layer_summary(final_diagnostics),
        "success": not final_diagnostics,
    }
    report_path = semantic_root / "implementability_report.json"
    write_json(report_path, report)
    write_json(semantic_root / "semantic_patch_usage.json", usage)
    return normalized, report, usage


def run_planning(
    facts_path: str | Path,
    out: str | Path,
    *,
    planner_mode: str = "llm",
    api_key_env: str = "ALI_API",
    coder_validate: bool = True,
    resume_from: str | None = None,
) -> PlanningResult:
    facts_path = Path(facts_path)
    output_root = Path(out)
    planning_root = output_root / "_planning"
    output_root.mkdir(parents=True, exist_ok=True)
    if resume_from is None:
        _clean_dir(planning_root)
    else:
        planning_root.mkdir(parents=True, exist_ok=True)
    try:
        facts = read_json(facts_path)
    except BaseException as exc:
        _record_internal_failure(
            planning_root,
            facts_path=facts_path,
            facts_hash="",
            planner_mode=planner_mode,
            stage_records=[],
            error=exc,
        )
        raise
    facts_hash = stable_json_hash(facts)
    characteristics = normalize_characteristics(facts)
    rules = activate_engineering_rules(characteristics)
    assumptions = extract_open_assumptions(facts, characteristics)
    context = build_planning_context(facts, characteristics, rules, assumptions)
    if planner_mode != "llm":
        raise ValueError("Only planner_mode='llm' is supported; deterministic local plan generation is intentionally disabled.")
    candidate_root = planning_root / "candidate_planning_package"
    _clean_dir(candidate_root)
    provider = LLMStructuredPlanner(api_key_env=api_key_env, stage_log_dir=planning_root / "stage_logs")
    try:
        plan = provider.build_plan(context, resume_from=resume_from)
    except RecoverablePlanningError as exc:
        fatal_reason_code = "candidate_serialization_impossible"
        diagnostics = [
            _as_diag(
                "error",
                fatal_reason_code,
                f"No fact-grounded module/file/function interface inventory can be serialized after {exc.stage_id}: {exc.diagnostic}",
                exc.stage_id,
            )
        ]
        unresolved = [{"stage_id": exc.stage_id, "partition_id": "whole_stage", "diagnostic": exc.diagnostic}]
        _write_candidate_package(
            planning_root,
            candidate_root,
            facts_path=facts_path,
            facts_hash=facts_hash,
            run_status=FAILED_INTERNAL,
            plan=None,
            unresolved_partitions=unresolved,
            diagnostics=diagnostics,
            candidate_specs_root=None,
        )
        metrics = build_run_metrics(
            provider.stage_records,
            expected_stages=len(PLANNING_STAGES),
            run_status=FAILED_INTERNAL,
            candidate_produced=True,
            diagnostics=diagnostics_to_json(diagnostics),
        )
        write_json(planning_root / "run_metrics.json", metrics)
        write_json(planning_root / "diagnostics.json", diagnostics_to_json(diagnostics))
        manifest_path = planning_root / "run_manifest.json"
        write_json(
            manifest_path,
            {
                "kind": "PLANNING_RUN_MANIFEST",
                "facts_path": str(facts_path),
                "facts_sha256": facts_hash,
                "planner_mode": planner_mode,
                "run_status": FAILED_INTERNAL,
                "hard_failure_code": fatal_reason_code,
                "fatal": True,
                "fatal_reason_code": fatal_reason_code,
                "nonfatal_no_specs_count": 0,
                "candidate_root": str(candidate_root),
                "candidate_specs_root": None,
                "specs_root": None,
                "module_spec": None,
                "summary": None,
                "written_files": [],
                "specs_generated": False,
                "artifact_success": False,
                "semantic_qualified": False,
                "implementation_ready": False,
                "planning_validation_passed": False,
                "coder_loader_passed": None,
                "qualification_passed": False,
                "semantic_diagnostic_counts": {"error": 0, "warning": 0},
                "closure_diagnostic_counts": {"error": 0, "warning": 0},
                "post_validation_diagnostic_counts": {"error": 0, "warning": 0},
                "union_diagnostic_counts": {"error": 1, "warning": 0},
                "unresolved_stage_partition_count": 1,
                "unresolved_stage_partition_attempt_count": len(unresolved),
                "stage_survival": metrics.get("stage_survival", {}),
                "partition_survival": metrics.get("partition_survival", {}),
                "token_accounting": metrics.get("token_accounting", {}),
                "token_accounting_complete": bool(metrics.get("token_accounting", {}).get("complete")),
                "fresh": resume_from is None,
                "resume": resume_from is not None,
                "resume_from": resume_from,
                "replay": False,
                "diagnostic_counts": {"error": 1, "warning": 0},
            },
        )
        return PlanningResult(output_root, None, planning_root, candidate_root, manifest_path, diagnostics, FAILED_INTERNAL)
    except BaseException as exc:
        _record_internal_failure(
            planning_root,
            facts_path=facts_path,
            facts_hash=facts_hash,
            planner_mode=planner_mode,
            stage_records=provider.stage_records,
            error=exc,
        )
        raise

    try:
        if not isinstance(plan.get("canonical_registry_snapshot"), dict):
            raise RuntimeError("registry_snapshot_missing: structured planning completed without canonical registry")
        unresolved_partitions = _distinct_unresolved_partitions(plan.get("unresolved_partitions", []))
        plan, implementability_report, semantic_patch_usage = _close_implementability(
            plan,
            context,
            planning_root,
            api_key_env=api_key_env,
            resume_from=resume_from,
            planning_tokens_used=provider.model_tokens_used,
        )
        if unresolved_partitions:
            unresolved_diagnostics = [
                {
                    "level": "error",
                    "code": "unresolved_partition",
                    "message": str(item.get("diagnostic", "partition did not commit")),
                    "artifact_ids": [],
                    "owner_layer": str(item.get("validation_layer", "partition_validation")),
                    "authoritative_stage": str(item.get("stage_id", "unknown_stage")),
                    "recovery_action": "resume_or_regenerate_failed_partition",
                    "details": item,
                }
                for item in unresolved_partitions
            ]
            final_diagnostics = _distinct_diagnostic_dicts(
                [*unresolved_diagnostics, *implementability_report.get("final_diagnostics", [])]
            )
            implementability_report["final_diagnostics"] = final_diagnostics
            implementability_report["validation_layers"] = semantic_layer_summary(
                final_diagnostics,
                unresolved_partitions,
            )
            implementability_report["success"] = False
            write_json(
                planning_root / "semantic_closure" / "implementability_report.json",
                implementability_report,
            )
        snapshot = plan.get("canonical_registry_snapshot")
        if isinstance(snapshot, dict):
            registry = CanonicalPlanningRegistry.from_snapshot(snapshot)
            plan["canonical_registry_snapshot"] = registry.snapshot()
            write_json(planning_root / "canonical_registry_snapshot.json", registry.snapshot())
    except BaseException as exc:
        _record_internal_failure(
            planning_root,
            facts_path=facts_path,
            facts_hash=facts_hash,
            planner_mode=planner_mode,
            stage_records=provider.stage_records,
            error=exc,
        )
        raise
    try:
        qualified_specs_root = output_root / f"{plan['protocol']['slug']}_specs"
        if qualified_specs_root.exists():
            shutil.rmtree(qualified_specs_root)
        closure_diagnostics = implementability_report["final_diagnostics"]
        candidate_specs_root = candidate_root / "specs"
        compile_manifest = compile_specs(plan, candidate_specs_root, clean=True)
        diagnostics = closure_diagnostics_as_models(closure_diagnostics)
        diagnostics.extend(
            Diagnostic(
                level=str(item.get("level", "error")),
                code=str(item.get("code", "semantic_lowering_error")),
                message=str(item.get("message", "Compiler could not losslessly lower planning semantics")),
                path=str(item.get("path")) if item.get("path") else None,
                owner_layer=str(item.get("owner_layer", "compiler")),
                authoritative_stage=str(item.get("authoritative_stage", "final_plan_assembly")),
                recovery_action=str(item.get("recovery_action", "regenerate_authoritative_partition")),
            )
            for item in compile_manifest.get("diagnostics", [])
            if isinstance(item, dict)
        )
        planning_diagnostics = validate_planning_run(facts, facts_hash, plan, candidate_specs_root)
        for diagnostic in planning_diagnostics:
            identity = (diagnostic.level, diagnostic.code, diagnostic.message, diagnostic.path)
            if identity not in {(item.level, item.code, item.message, item.path) for item in diagnostics}:
                diagnostics.append(diagnostic)
        coder_diagnostics: list[Diagnostic] = []
        if coder_validate:
            coder_diagnostics = _coder_validate(candidate_specs_root)
            diagnostics.extend(coder_diagnostics)
        diagnostics = _dedupe_diagnostics(diagnostics)
        run_status = CANDIDATE_ONLY if any(diag.level == "error" for diag in diagnostics) else QUALIFIED
        if run_status == QUALIFIED:
            shutil.copytree(candidate_specs_root, qualified_specs_root)
            compile_manifest = _relocate_compile_manifest(compile_manifest, candidate_specs_root, qualified_specs_root)
    except BaseException as exc:
        _record_internal_failure(
            planning_root,
            facts_path=facts_path,
            facts_hash=facts_hash,
            planner_mode=planner_mode,
            stage_records=provider.stage_records,
            error=exc,
        )
        raise

    write_json(
        planning_root / "run_metrics.json",
        build_run_metrics(
            provider.stage_records,
            expected_stages=len(PLANNING_STAGES),
            run_status=run_status,
            candidate_produced=True,
            qualified_produced=run_status == QUALIFIED,
            diagnostics=diagnostics_to_json(diagnostics),
            semantic_patch_usage=semantic_patch_usage,
        ),
    )
    _write_candidate_package(
        planning_root,
        candidate_root,
        facts_path=facts_path,
        facts_hash=facts_hash,
        run_status=run_status,
        plan=plan,
        unresolved_partitions=deepcopy(plan.get("unresolved_partitions", [])),
        diagnostics=diagnostics,
        candidate_specs_root=candidate_specs_root,
    )
    implementability_report_path = planning_root / "semantic_closure" / "implementability_report.json"
    manifest_path = _write_planning_artifacts(
        planning_root,
        facts_path,
        facts_hash,
        collect_top_level_fact_refs(facts),
        characteristics,
        rules,
        assumptions,
        context,
        plan,
        compile_manifest,
        diagnostics,
        planner_mode,
        implementability_report_path,
        semantic_patch_usage,
        run_status,
        candidate_root,
        candidate_specs_root,
        not any(diag.level == "error" for diag in planning_diagnostics),
        None if not coder_validate else not any(diag.level == "error" for diag in coder_diagnostics),
        closure_diagnostics,
        resume_from,
    )
    return PlanningResult(
        output_root=output_root,
        specs_root=Path(compile_manifest["specs_root"]),
        planning_root=planning_root,
        candidate_root=candidate_root,
        manifest_path=manifest_path,
        diagnostics=diagnostics,
        run_status=run_status,
    )


def validate_existing_run(run_dir: str | Path, *, coder_validate: bool = True) -> PlanningResult:
    root = Path(run_dir)
    planning_root = root / "_planning"
    manifest = read_json(planning_root / "run_manifest.json")
    run_status = str(manifest.get("run_status", QUALIFIED))
    candidate_value = manifest.get("candidate_root")
    candidate_root = Path(candidate_value) if candidate_value else None
    if not manifest.get("specs_root"):
        stored = json.loads((planning_root / "diagnostics.json").read_text(encoding="utf-8")) if (planning_root / "diagnostics.json").exists() else []
        diagnostics = [
            Diagnostic(
                item["level"],
                item["code"],
                item["message"],
                item.get("path"),
                item.get("owner_layer"),
                item.get("authoritative_stage"),
                item.get("recovery_action"),
            )
            for item in stored
        ]
        return PlanningResult(root, None, planning_root, candidate_root, planning_root / "run_manifest.json", diagnostics, run_status)
    facts = read_json(manifest["facts_path"])
    plan = read_json(planning_root / "implementation_plan.json")
    specs_root = Path(manifest["specs_root"])
    diagnostics = validate_planning_run(facts, stable_json_hash(facts), plan, specs_root)
    if coder_validate:
        diagnostics.extend(_coder_validate(specs_root))
    write_json(planning_root / "diagnostics.json", diagnostics_to_json(diagnostics))
    manifest["diagnostic_counts"] = {
        "error": sum(1 for diag in diagnostics if diag.level == "error"),
        "warning": sum(1 for diag in diagnostics if diag.level == "warning"),
    }
    coder_diagnostics = [diag for diag in diagnostics if diag.code.startswith("coder_")]
    manifest.update(
        {
            "specs_generated": specs_root.exists(),
            "planning_validation_passed": not any(
                diag.level == "error" and not diag.code.startswith("coder_") for diag in diagnostics
            ),
            "coder_loader_passed": None if not coder_validate else not any(
                diag.level == "error" for diag in coder_diagnostics
            ),
        }
    )
    run_status = CANDIDATE_ONLY if any(diag.level == "error" for diag in diagnostics) else QUALIFIED
    manifest.update({"run_status": run_status, "qualification_passed": run_status == QUALIFIED})
    manifest.update(
        _readiness_manifest_fields(
            plan,
            specs_root,
            diagnostics,
            run_status=run_status,
            coder_loader_passed=manifest["coder_loader_passed"],
        )
    )
    write_json(planning_root / "run_manifest.json", manifest)
    return PlanningResult(
        root,
        specs_root,
        planning_root,
        candidate_root,
        planning_root / "run_manifest.json",
        diagnostics,
        run_status,
    )
