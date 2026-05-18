from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters.facts_input import build_planning_ir
from .adapters.target_profile import load_target_profile
from .artifact_io import ArtifactStore, read_json, run_timestamp, safe_slug, write_json
from .config import PlanningConfig
from .diagnostics import PlanningDiagnostic, diagnostics_to_dict, has_errors
from .llm_boundary import request_json_candidate
from .models import PlanningResult, TargetProfile
from .prompts.templates import architecture_candidate_messages, implementation_plan_candidate_messages, protocol_profile_patch_messages
from .stages.architecture import build_architecture_candidates, select_architecture
from .stages.blueprint import build_spec_blueprint
from .stages.constraints import activate_constraints
from .stages.dependencies import build_dependency_validation_report, derive_dependency_graph
from .stages.implementation_plan import build_implementation_plan
from .stages.preflight import build_manifest, validate_input_paths
from .stages.protocol_profile import apply_protocol_profile_patch_candidate, build_protocol_profile
from .stages.specs_compiler import compile_spec_bundle
from .token_usage import TokenUsageTracker
from .validators.architecture import validate_architecture_candidates, validate_selected_architecture
from .validators.blueprint import validate_spec_blueprint
from .validators.coder_compat import validate_coder_compatibility
from .validators.constraints import validate_constraints
from .validators.dependencies import validate_dependency_graph
from .validators.implementation_plan import validate_implementation_plan
from .validators.llm_outputs import validate_protocol_profile_patch_candidate
from .validators.planning_ir import validate_planning_ir
from .validators.profile import validate_protocol_profile


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "agent" / "planning" / "out"


STEP_FILENAMES = {
    "planning_run_manifest": "000_planning_run_manifest.json",
    "planning_ir": "003_planning_ir.json",
    "protocol_profile_patch_candidate": "004_protocol_profile_patch_candidate.json",
    "protocol_profile": "004_protocol_profile.json",
    "engineering_constraints": "005_engineering_constraints.json",
    "llm_architecture_candidates": "006_llm_architecture_candidates.json",
    "architecture_candidates": "006_architecture_candidates.json",
    "selected_architecture": "006_selected_architecture.json",
    "implementation_plan_candidate": "007_implementation_plan_candidate.json",
    "implementation_plan": "007_implementation_plan.json",
    "dependency_validation_report": "008_dependency_validation_report.json",
    "spec_blueprint": "010_spec_blueprint.json",
    "token_usage_summary": "013_token_usage_summary.json",
    "planning_validation_report": "014_planning_validation_report.json",
}


def _diagnostic_reasons(diagnostics: list[PlanningDiagnostic]) -> list[str]:
    return [f"{diag.level}:{diag.code}: {diag.message}" for diag in diagnostics]


def _retry_messages(base_messages: list[dict[str, str]], previous_reasons: list[str], attempt: int) -> list[dict[str, str]]:
    if not previous_reasons:
        return base_messages
    return [
        *base_messages,
        {
            "role": "user",
            "content": (
                f"Retry attempt {attempt}. The previous LLM output was rejected by deterministic validation. "
                "Return a corrected JSON object only. Rejection reasons:\n" + "\n".join(f"- {reason}" for reason in previous_reasons)
            ),
        },
    ]


def _llm_failure_diagnostic(stage: str, reasons: list[str]) -> PlanningDiagnostic:
    detail = "; ".join(reasons[-8:]) if reasons else "No valid LLM output was produced."
    return PlanningDiagnostic(
        "error",
        f"{stage}_mandatory_llm_failed",
        f"Mandatory LLM stage failed after retries: {detail}",
    )


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


def _validation_report(
    *,
    status: str,
    diagnostics: list[PlanningDiagnostic],
    artifact_paths: dict[str, Path],
    coder_compatibility_status: str = "not_run",
) -> dict[str, Any]:
    return {
        "schema_version": "planning_validation_report/v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "facts_compatibility_status": "failed" if any(item.code.startswith("missing_") or item.code.startswith("invalid_facts") for item in diagnostics if item.level == "error") else "passed",
        "coder_compatibility_status": coder_compatibility_status,
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

    def plan(self) -> PlanningResult:
        store = ArtifactStore(self.output_dir)
        token_tracker = TokenUsageTracker()
        store.log_event("stage=preflight start")
        diagnostics = self.validate_inputs()
        artifact_paths: dict[str, Path] = {}
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
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "preflight", "code": "validation_errors"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=preflight done")

        store.log_event("stage=target_profile load start")
        target_profile, target_diags = load_target_profile(self.target_profile_path)
        diagnostics.extend(target_diags)
        if target_profile is None or has_errors(target_diags):
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "target_profile", "code": "target_profile_load_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=target_profile load done")

        store.log_event("stage=planning_ir build start")
        planning_ir, ir_diags = build_planning_ir(self.facts_path, target_profile)
        diagnostics.extend(ir_diags)
        if planning_ir is None:
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "planning_ir", "code": "planning_ir_build_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        planning_ir_path = store.write_step_json(STEP_FILENAMES["planning_ir"], planning_ir)
        artifact_paths["planning_ir"] = planning_ir_path
        diagnostics.extend(validate_planning_ir(planning_ir, path=str(planning_ir_path)))
        if has_errors(diagnostics):
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "planning_ir", "code": "planning_ir_validation_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=planning_ir build done")

        store.log_event("stage=protocol_profile build start")
        profile = build_protocol_profile(planning_ir)
        accepted_profile = None
        previous_reasons: list[str] = []
        base_messages = protocol_profile_patch_messages(planning_ir, profile)
        for attempt in range(1, self.config.llm_max_retries + 1):
            store.log_event(f"stage=protocol_profile llm_attempt={attempt} prompt=protocol_profile_patch_prompt start")
            candidate, candidate_diags, meta = request_json_candidate(
                prompt_name="protocol_profile_patch_prompt",
                messages=_retry_messages(base_messages, previous_reasons, attempt),
                config=self.config,
            )
            store.write_agent_log(f"004_protocol_profile_patch_llm_attempt_{attempt}_meta", str(meta))
            token_tracker.add_attempt(stage="protocol_profile", prompt_name="protocol_profile_patch_prompt", attempt=attempt, meta=meta)
            if candidate is None:
                previous_reasons = _diagnostic_reasons(candidate_diags) or ["LLM did not return a JSON object."]
                store.write_agent_log(f"004_protocol_profile_patch_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                store.log_event(f"stage=protocol_profile llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
                continue
            candidate_path = store.write_step_json(STEP_FILENAMES["protocol_profile_patch_candidate"], candidate)
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
            _write_token_usage_summary(store=store, tracker=token_tracker, artifact_paths=artifact_paths)
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "protocol_profile", "code": "mandatory_llm_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        profile = accepted_profile
        profile_path = store.write_step_json(STEP_FILENAMES["protocol_profile"], profile)
        artifact_paths["protocol_profile"] = profile_path
        diagnostics.extend(validate_protocol_profile(profile, path=str(profile_path)))
        if has_errors(diagnostics):
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "protocol_profile", "code": "protocol_profile_validation_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=protocol_profile build done")

        store.log_event("stage=engineering_constraints activate start")
        constraints = activate_constraints(profile)
        constraints_path = store.write_step_json(STEP_FILENAMES["engineering_constraints"], constraints)
        artifact_paths["engineering_constraints"] = constraints_path
        diagnostics.extend(validate_constraints(constraints, path=str(constraints_path)))
        if has_errors(diagnostics):
            status = "failed"
            report = _validation_report(status=status, diagnostics=diagnostics, artifact_paths=artifact_paths)
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
                failure={"stage": "engineering_constraints", "code": "validation_errors"},
            )
            store.log_event(f"stage=engineering_constraints activate done status={status}")
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=engineering_constraints activate done")

        store.log_event("stage=architecture build start")
        accepted_architecture = None
        previous_reasons = []
        base_messages = architecture_candidate_messages(planning_ir, profile, constraints)
        for attempt in range(1, self.config.llm_max_retries + 1):
            store.log_event(f"stage=architecture llm_attempt={attempt} prompt=architecture_candidate_prompt start")
            llm_candidates, llm_diags, meta = request_json_candidate(
                prompt_name="architecture_candidate_prompt",
                messages=_retry_messages(base_messages, previous_reasons, attempt),
                config=self.config,
            )
            store.write_agent_log(f"006_architecture_candidate_llm_attempt_{attempt}_meta", str(meta))
            token_tracker.add_attempt(stage="architecture", prompt_name="architecture_candidate_prompt", attempt=attempt, meta=meta)
            if llm_candidates is None:
                previous_reasons = _diagnostic_reasons(llm_diags) or ["LLM did not return a JSON object."]
                store.write_agent_log(f"006_architecture_candidate_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                store.log_event(f"stage=architecture llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
                continue
            llm_candidates_path = store.write_step_json(STEP_FILENAMES["llm_architecture_candidates"], llm_candidates)
            artifact_paths["llm_architecture_candidates"] = llm_candidates_path
            architecture_diags = validate_architecture_candidates(llm_candidates, profile, constraints, path=str(llm_candidates_path))
            if not has_errors(architecture_diags):
                accepted_architecture = llm_candidates
                token_tracker.mark_attempt_accepted(stage="architecture", prompt_name="architecture_candidate_prompt", attempt=attempt)
                store.log_event(f"stage=architecture llm_attempt={attempt} accepted")
                break
            previous_reasons = _diagnostic_reasons(architecture_diags)
            store.write_agent_log(f"006_architecture_candidate_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
            store.log_event(f"stage=architecture llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
        if accepted_architecture is None:
            diagnostics.append(_llm_failure_diagnostic("architecture", previous_reasons))
            _write_token_usage_summary(store=store, tracker=token_tracker, artifact_paths=artifact_paths)
            report = _validation_report(status="failed", diagnostics=diagnostics, artifact_paths=artifact_paths)
            report_path = store.write_step_json(STEP_FILENAMES["planning_validation_report"], report)
            artifact_paths["planning_validation_report"] = report_path
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "architecture", "code": "mandatory_llm_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        architecture_candidates = accepted_architecture
        candidates_path = store.write_step_json(STEP_FILENAMES["architecture_candidates"], architecture_candidates)
        artifact_paths["architecture_candidates"] = candidates_path
        diagnostics.extend(validate_architecture_candidates(architecture_candidates, profile, constraints, path=str(candidates_path)))
        selected_architecture = select_architecture(architecture_candidates, profile)
        selected_path = store.write_step_json(STEP_FILENAMES["selected_architecture"], selected_architecture)
        artifact_paths["selected_architecture"] = selected_path
        diagnostics.extend(validate_selected_architecture(selected_architecture, profile, constraints, path=str(selected_path)))
        if has_errors(diagnostics):
            status = "failed"
            report = _validation_report(status=status, diagnostics=diagnostics, artifact_paths=artifact_paths)
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
                failure={"stage": "architecture", "code": "validation_errors"},
            )
            store.log_event(f"stage=architecture build done status={status}")
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=architecture build done")

        store.log_event("stage=implementation_plan build start")
        implementation_plan = build_implementation_plan(planning_ir, profile, constraints, selected_architecture)
        accepted_plan = None
        previous_reasons = []
        base_messages = implementation_plan_candidate_messages(planning_ir, profile, constraints, selected_architecture, implementation_plan)
        for attempt in range(1, self.config.llm_max_retries + 1):
            store.log_event(f"stage=implementation_plan llm_attempt={attempt} prompt=function_contract_prompt start")
            plan_candidate, plan_llm_diags, meta = request_json_candidate(
                prompt_name="function_contract_prompt",
                messages=_retry_messages(base_messages, previous_reasons, attempt),
                config=self.config,
            )
            store.write_agent_log(f"007_implementation_plan_candidate_llm_attempt_{attempt}_meta", str(meta))
            token_tracker.add_attempt(stage="implementation_plan", prompt_name="function_contract_prompt", attempt=attempt, meta=meta)
            if plan_candidate is None:
                previous_reasons = _diagnostic_reasons(plan_llm_diags) or ["LLM did not return a JSON object."]
                store.write_agent_log(f"007_implementation_plan_candidate_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
                store.log_event(f"stage=implementation_plan llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
                continue
            candidate_path = store.write_step_json(STEP_FILENAMES["implementation_plan_candidate"], plan_candidate)
            artifact_paths["implementation_plan_candidate"] = candidate_path
            if isinstance(plan_candidate, dict):
                plan_candidate["dependency_graph"] = derive_dependency_graph(plan_candidate)
            plan_candidate_diags = validate_implementation_plan(plan_candidate, profile=profile, planning_ir=planning_ir, path=str(candidate_path))
            plan_candidate_diags.extend(validate_dependency_graph(plan_candidate, path=str(candidate_path)))
            if not has_errors(plan_candidate_diags):
                accepted_plan = plan_candidate
                token_tracker.mark_attempt_accepted(stage="implementation_plan", prompt_name="function_contract_prompt", attempt=attempt)
                store.log_event(f"stage=implementation_plan llm_attempt={attempt} accepted")
                break
            previous_reasons = _diagnostic_reasons(plan_candidate_diags)
            store.write_agent_log(f"007_implementation_plan_candidate_llm_attempt_{attempt}_rejection", "\n".join(previous_reasons))
            store.log_event(f"stage=implementation_plan llm_attempt={attempt} rejected reason={previous_reasons[0] if previous_reasons else 'unknown'}")
        if accepted_plan is None:
            diagnostics.append(_llm_failure_diagnostic("implementation_plan", previous_reasons))
            _write_token_usage_summary(store=store, tracker=token_tracker, artifact_paths=artifact_paths)
            report = _validation_report(status="failed", diagnostics=diagnostics, artifact_paths=artifact_paths)
            report_path = store.write_step_json(STEP_FILENAMES["planning_validation_report"], report)
            artifact_paths["planning_validation_report"] = report_path
            _write_manifest(
                store=store,
                facts_path=self.facts_path,
                target_profile_path=self.target_profile_path,
                config=self.config,
                status="failed",
                diagnostics=diagnostics,
                artifact_paths=artifact_paths,
                failure={"stage": "implementation_plan", "code": "mandatory_llm_failed"},
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        implementation_plan = accepted_plan
        implementation_plan_path = store.write_step_json(STEP_FILENAMES["implementation_plan"], implementation_plan)
        artifact_paths["implementation_plan"] = implementation_plan_path
        plan_diags = validate_implementation_plan(implementation_plan, profile=profile, planning_ir=planning_ir, path=str(implementation_plan_path))
        dependency_diags = validate_dependency_graph(implementation_plan, path=str(implementation_plan_path))
        dependency_report = build_dependency_validation_report(implementation_plan, dependency_diags)
        dependency_report_path = store.write_step_json(STEP_FILENAMES["dependency_validation_report"], dependency_report)
        artifact_paths["dependency_validation_report"] = dependency_report_path
        diagnostics.extend(plan_diags)
        diagnostics.extend(dependency_diags)
        if has_errors(diagnostics):
            status = "failed"
            report = _validation_report(status=status, diagnostics=diagnostics, artifact_paths=artifact_paths)
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
                failure={"stage": "implementation_plan", "code": "validation_errors"},
            )
            store.log_event(f"stage=implementation_plan build done status={status}")
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=implementation_plan build done")

        store.log_event("stage=spec_blueprint lower start")
        spec_blueprint = build_spec_blueprint(implementation_plan)
        spec_blueprint_path = store.write_step_json(STEP_FILENAMES["spec_blueprint"], spec_blueprint)
        artifact_paths["spec_blueprint"] = spec_blueprint_path
        diagnostics.extend(validate_spec_blueprint(spec_blueprint, implementation_plan, path=str(spec_blueprint_path)))
        if has_errors(diagnostics):
            status = "failed"
            report = _validation_report(status=status, diagnostics=diagnostics, artifact_paths=artifact_paths)
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
                failure={"stage": "spec_blueprint", "code": "validation_errors"},
            )
            store.log_event(f"stage=spec_blueprint lower done status={status}")
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        store.log_event("stage=spec_blueprint lower done")

        store.log_event("stage=specs_compile start")
        coder_manifest, coder_manifest_path = compile_spec_bundle(spec_blueprint, self.output_dir)
        artifact_paths["coder_manifest"] = coder_manifest_path
        spec_root = Path(coder_manifest["spec_root"])
        artifact_paths["spec_bundle"] = spec_root
        coder_diags = validate_coder_compatibility(spec_root)
        diagnostics.extend(coder_diags)
        coder_status = "failed" if has_errors(coder_diags) else "passed"
        status = "failed" if has_errors(diagnostics) else "success"
        store.log_event(f"stage=specs_compile done coder_status={coder_status}")

        _write_token_usage_summary(store=store, tracker=token_tracker, artifact_paths=artifact_paths)
        report = _validation_report(
            status=status,
            diagnostics=diagnostics,
            artifact_paths=artifact_paths,
            coder_compatibility_status=coder_status,
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
            failure={"stage": "specs_compile", "code": "validation_errors"} if status == "failed" else None,
        )
        store.log_event(f"planning done status={status}")
        return PlanningResult(status == "success", self.output_dir, diagnostics, artifact_paths)


def verify_output_dir(output_dir: str | Path) -> PlanningResult:
    root = Path(output_dir).expanduser()
    diagnostics: list[PlanningDiagnostic] = []
    artifact_paths: dict[str, Path] = {}
    required = {
        "planning_run_manifest": root / "_step_logs" / STEP_FILENAMES["planning_run_manifest"],
        "planning_ir": root / "_step_logs" / STEP_FILENAMES["planning_ir"],
        "protocol_profile": root / "_step_logs" / STEP_FILENAMES["protocol_profile"],
        "engineering_constraints": root / "_step_logs" / STEP_FILENAMES["engineering_constraints"],
        "architecture_candidates": root / "_step_logs" / STEP_FILENAMES["architecture_candidates"],
        "selected_architecture": root / "_step_logs" / STEP_FILENAMES["selected_architecture"],
        "implementation_plan": root / "_step_logs" / STEP_FILENAMES["implementation_plan"],
        "dependency_validation_report": root / "_step_logs" / STEP_FILENAMES["dependency_validation_report"],
        "spec_blueprint": root / "_step_logs" / STEP_FILENAMES["spec_blueprint"],
        "token_usage_summary": root / "_step_logs" / STEP_FILENAMES["token_usage_summary"],
        "coder_manifest": root / "coder_manifest.json",
        "spec_bundle": root / "spec_bundle",
        "planning_validation_report": root / "_step_logs" / STEP_FILENAMES["planning_validation_report"],
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
        diagnostics.extend(validate_architecture_candidates(read_json(artifact_paths["architecture_candidates"]), profile, constraints, path=str(artifact_paths["architecture_candidates"])))
        if "selected_architecture" in artifact_paths:
            diagnostics.extend(validate_selected_architecture(read_json(artifact_paths["selected_architecture"]), profile, constraints, path=str(artifact_paths["selected_architecture"])))
    if "implementation_plan" in artifact_paths:
        implementation_plan = read_json(artifact_paths["implementation_plan"])
        profile = read_json(artifact_paths["protocol_profile"]) if "protocol_profile" in artifact_paths else None
        planning_ir = read_json(artifact_paths["planning_ir"]) if "planning_ir" in artifact_paths else None
        diagnostics.extend(validate_implementation_plan(implementation_plan, profile=profile, planning_ir=planning_ir, path=str(artifact_paths["implementation_plan"])))
        diagnostics.extend(validate_dependency_graph(implementation_plan, path=str(artifact_paths["implementation_plan"])))
        if "spec_blueprint" in artifact_paths:
            diagnostics.extend(
                validate_spec_blueprint(
                    read_json(artifact_paths["spec_blueprint"]),
                    implementation_plan,
                    path=str(artifact_paths["spec_blueprint"]),
                )
            )
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
