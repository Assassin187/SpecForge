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
from .planner import LLMStructuredPlanner, PLANNING_STAGES, RecoverablePlanningError, build_planning_context
from .registry import CanonicalPlanningRegistry, register_semantic_patch_additions
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
    return Diagnostic(level, code, message, path)


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
            "candidate_root": None,
            "candidate_specs_root": None,
            "specs_root": None,
            "module_spec": None,
            "summary": None,
            "written_files": [],
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
            diagnostics.append(Diagnostic(diag.level, f"coder_{diag.code}", diag.message, diag.path))
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
    candidate_specs_root: Path | None,
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
    manifest = {
        "kind": "PLANNING_RUN_MANIFEST",
        "facts_path": str(facts_path),
        "facts_sha256": facts_hash,
        "planner_mode": planner_mode,
        "run_status": run_status,
        "candidate_root": str(candidate_root),
        "candidate_specs_root": str(candidate_specs_root) if candidate_specs_root is not None else None,
        "specs_root": compile_manifest["specs_root"] if run_status == QUALIFIED else None,
        "module_spec": compile_manifest["module_spec"] if run_status == QUALIFIED else None,
        "summary": compile_manifest["summary"] if run_status == QUALIFIED else None,
        "written_files": compile_manifest["written_files"] if run_status == QUALIFIED else [],
        "implementability_report": str(implementability_report),
        "semantic_patch_usage": semantic_patch_usage,
        "diagnostic_counts": {
            "error": sum(1 for diag in diagnostics if diag.level == "error"),
            "warning": sum(1 for diag in diagnostics if diag.level == "warning"),
        },
    }
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
    write_json(semantic_root / "initial_diagnostics.json", initial_diagnostics)
    write_json(semantic_root / "deterministic_completion.json", deterministic_changes)

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

    def validate_candidate(candidate: dict[str, Any]) -> list[str]:
        errors = validate_semantic_patch(normalized, candidate)
        try:
            trial = apply_semantic_patch(normalized, candidate)
            _apply_registry_additions(trial, candidate)
            trial = normalize_plan_for_compiler(trial)
            complete_deterministic_dependencies(trial)
            residual = analyze_implementability(trial)
        except (KeyError, TypeError, ValueError) as exc:
            return [*errors, f"patch application failed: {type(exc).__name__}: {exc}"]
        return [*errors, *[f"residual closure {item['code']}: {item['message']}" for item in residual]]

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
        payload = build_semantic_patch_slice(normalized, context, initial_diagnostics)
        write_json(semantic_root / "input_slice.json", payload)
        try:
            patch, call_usage = request_semantic_patch(payload, api_key_env=api_key_env, log_dir=semantic_root)
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
            for index, (group, group_errors) in enumerate(correction_groups.items(), 1):
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
        diagnostics = [_as_diag("error", "stage_binding_failure", exc.diagnostic, exc.stage_id)]
        unresolved = [{"stage_id": exc.stage_id, "partition_id": "whole_stage", "diagnostic": exc.diagnostic}]
        _write_candidate_package(
            planning_root,
            candidate_root,
            facts_path=facts_path,
            facts_hash=facts_hash,
            run_status=CANDIDATE_ONLY,
            plan=None,
            unresolved_partitions=unresolved,
            diagnostics=diagnostics,
            candidate_specs_root=None,
        )
        metrics = build_run_metrics(
            provider.stage_records,
            expected_stages=len(PLANNING_STAGES),
            run_status=CANDIDATE_ONLY,
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
                "run_status": CANDIDATE_ONLY,
                "candidate_root": str(candidate_root),
                "candidate_specs_root": None,
                "specs_root": None,
                "module_spec": None,
                "summary": None,
                "written_files": [],
                "diagnostic_counts": {"error": 1, "warning": 0},
            },
        )
        return PlanningResult(output_root, None, planning_root, candidate_root, manifest_path, diagnostics, CANDIDATE_ONLY)
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
        if plan.get("unresolved_partitions"):
            semantic_patch_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            unresolved_diagnostics = [
                {
                    "level": "error",
                    "code": "unresolved_partition",
                    "message": str(item.get("diagnostic", "partition did not commit")),
                    "artifact_ids": [],
                    "details": item,
                }
                for item in plan["unresolved_partitions"]
            ]
            implementability_report = {
                "kind": "IMPLEMENTABILITY_REPORT",
                "initial_diagnostics": [],
                "deterministic_completion": [],
                "semantic_patch_attempted": False,
                "semantic_patch_reused": False,
                "semantic_patch_validation_errors": [],
                "semantic_patch_usage": semantic_patch_usage,
                "final_diagnostics": unresolved_diagnostics,
                "validation_layers": semantic_layer_summary(
                    unresolved_diagnostics, plan["unresolved_partitions"]
                ),
                "success": False,
            }
            semantic_root = planning_root / "semantic_closure"
            write_json(semantic_root / "implementability_report.json", implementability_report)
            write_json(semantic_root / "semantic_patch_usage.json", semantic_patch_usage)
        else:
            plan, implementability_report, semantic_patch_usage = _close_implementability(
                plan, context, planning_root, api_key_env=api_key_env, resume_from=resume_from
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
        candidate_specs_root: Path | None = None
        if closure_diagnostics:
            compile_manifest = {
                "specs_root": None,
                "module_spec": None,
                "summary": None,
                "written_files": [],
            }
            diagnostics = closure_diagnostics_as_models(closure_diagnostics)
        else:
            candidate_specs_root = candidate_root / "specs"
            compile_manifest = compile_specs(plan, candidate_specs_root, clean=True)
            diagnostics = validate_planning_run(facts, facts_hash, plan, candidate_specs_root)
            if coder_validate:
                diagnostics.extend(_coder_validate(candidate_specs_root))
        run_status = CANDIDATE_ONLY if any(diag.level == "error" for diag in diagnostics) else QUALIFIED
        if run_status == QUALIFIED and candidate_specs_root is not None:
            shutil.move(str(candidate_specs_root), str(qualified_specs_root))
            compile_manifest = _relocate_compile_manifest(compile_manifest, candidate_specs_root, qualified_specs_root)
            candidate_specs_root = None
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
    )
    return PlanningResult(
        output_root=output_root,
        specs_root=qualified_specs_root if run_status == QUALIFIED else None,
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
    if run_status != QUALIFIED or not manifest.get("specs_root"):
        stored = read_json(planning_root / "diagnostics.json") if (planning_root / "diagnostics.json").exists() else []
        diagnostics = [Diagnostic(item["level"], item["code"], item["message"], item.get("path")) for item in stored]
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
    if any(diag.level == "error" for diag in diagnostics):
        candidate_root = candidate_root or planning_root / "candidate_planning_package"
        candidate_root.mkdir(parents=True, exist_ok=True)
        candidate_specs_root = candidate_root / "specs"
        if candidate_specs_root.exists():
            shutil.rmtree(candidate_specs_root)
        if specs_root.exists():
            shutil.move(str(specs_root), str(candidate_specs_root))
        run_status = CANDIDATE_ONLY
        manifest.update(
            {
                "run_status": run_status,
                "candidate_root": str(candidate_root),
                "candidate_specs_root": str(candidate_specs_root),
                "specs_root": None,
                "module_spec": None,
                "summary": None,
                "written_files": [],
            }
        )
    write_json(planning_root / "run_manifest.json", manifest)
    return PlanningResult(
        root,
        specs_root if run_status == QUALIFIED else None,
        planning_root,
        candidate_root,
        planning_root / "run_manifest.json",
        diagnostics,
        run_status,
    )
