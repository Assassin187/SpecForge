from __future__ import annotations

import json
import re
import shutil
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .facts import write_json
from .amendment import InventoryAmendmentProcessor
from .metrics import build_run_metrics
from .models import EngineeringRule, NormalizedCharacteristics, OpenAssumption, to_jsonable
from .prompts import (
    build_amendment_messages,
    build_json_repair_messages,
    build_local_correction_messages,
    build_stage_messages,
    dependency_ambiguities,
)
from .registry import CanonicalPlanningRegistry, RegistryInvariantError, advance_registry, build_registry
from .validation_layers import (
    LayeredValidationError,
    ValidationIssue,
    ValidationLedger,
    classify_partition_error,
)


class StructuredPlanner(Protocol):
    def build_plan(self, context: dict[str, Any], *, resume_from: str | None = None) -> dict[str, Any]:
        ...


class RecoverablePlanningError(RuntimeError):
    def __init__(self, stage_id: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.stage_id = stage_id
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class PlanningStage:
    stage_id: str
    title: str
    purpose: str
    required_output: dict[str, Any]
    engineering_focus: list[str]
    consumes_previous: bool = True
    partition_strategy: str | None = None


PLANNING_STAGES: tuple[PlanningStage, ...] = (
    PlanningStage(
        stage_id="scope_fact_inventory",
        title="Scope and Fact Inventory",
        purpose=(
            "Extract the implementation-relevant inventory from protocol facts without making architecture decisions. "
            "The output separates confirmed protocol facts, minimum scope, deferred features, open questions, and evidence refs."
        ),
        engineering_focus=[
            "target role and minimum implementation scope",
            "message/surface catalog and directions",
            "state transitions and invalid-state behavior",
            "transport/framing facts",
            "routing/resource/error facts",
            "facts that are insufficient for implementation decisions",
        ],
        required_output={
            "confirmed_scope": "array of fact-backed implementation surfaces and behaviors",
            "deferred_scope": "array of explicitly out-of-scope features",
            "fact_inventory": "array of {fact_ref, content, evidence_refs, used_by_later_stage}",
            "open_assumption_candidates": "array of unresolved implementation policy items",
            "no_invention_checks": "array of checks later stages must obey",
        },
        consumes_previous=False,
    ),
    PlanningStage(
        stage_id="architecture_boundaries",
        title="Architecture Boundaries",
        purpose=(
            "Design protocol-specific module candidates from the fact inventory and activated generic engineering rules. "
            "This stage may propose module names, but every module must be justified by facts/rules rather than a template."
        ),
        engineering_focus=[
            "module decomposition",
            "module responsibilities and non-responsibilities",
            "ownership and lifecycle boundaries",
            "cross-module services",
            "protocol behavior that each module covers",
            "architecture risks and rejected alternatives",
        ],
        required_output={
            "module_candidates": "array of {name, role, boundaries, fact_refs, rule_refs, decision_refs}",
            "ownership_decisions": "array of fact/rule-backed decisions",
            "cross_module_services": "array of service contracts without concrete C signatures yet",
            "coverage_matrix": "minimum-scope item -> owning module candidate",
            "architecture_diagnostics": "array of missing/overlapping responsibility findings",
        },
    ),
    PlanningStage(
        stage_id="module_file_plan",
        title="Module and File Plan",
        purpose=(
            "Lower selected architecture into a source/header file layout matching the coder-facing FILE_SPEC dialect. "
            "This stage chooses files and file roles, but does not yet finalize detailed type fields or function bodies."
        ),
        engineering_focus=[
            "one or more FILE_SPEC candidates per module",
            "header/source ownership",
            "public vs private file responsibilities",
            "header dependency intent",
            "source dependency intent",
            "module generation order constraints",
            "every non-main source has one dedicated header owner; header paths are not shared across FILE_SPEC candidates",
        ],
        required_output={
            "modules": "array of planned modules with dependencies and planned files",
            "files": "array of {id, module, trace_id, role, header_path, source_path, dependency_intent, trace_refs}",
            "generation_order_rationale": "why dependencies can be generated in this order",
            "file_coverage_matrix": "minimum-scope item -> file owner",
            "layout_diagnostics": "array of missing owner or illegal dependency findings",
        },
    ),
    PlanningStage(
        stage_id="public_artifact_inventory",
        title="Public Artifact Inventory",
        purpose=(
            "Plan the public and private symbols needed by each file before writing detailed specs. "
            "The output is an inventory of types, constants, callbacks, functions, and visibility."
        ),
        engineering_focus=[
            "public C type names",
            "opaque vs structural type decisions",
            "callback types",
            "public API functions",
            "private helper functions",
            "complete create/use/destroy lifecycle for every owned resource",
            "explicit state registry, lookup, or access services required by cross-module data flow",
            "typed runtime context and access helpers that bridge callback user_data to owned session/routing/transport state",
            "a unique main function and its startup/run/cleanup call chain for executable targets",
            "forbidden symbols and naming conflicts",
            "closed type/function inventory: later stages may not add identities omitted here",
        ],
        required_output={
            "types": "array of planned type symbols with owner file and visibility",
            "constants_or_macros": "array of planned constants/macros with owner file",
            "functions": "array of planned functions with owner file, visibility, and high-level role",
            "lifecycle_matrix": "owned type/resource -> create/use/destroy function identities",
            "runtime_entrypoint": "unique main function identity, owner file, startup/run/cleanup services, and exit behavior",
            "public_symbol_table": "symbols intended for FILE_SPEC.PUBLIC_SYMBOLS",
            "forbidden_symbols": "array of names/patterns forbidden because they conflict with the plan",
        },
    ),
    PlanningStage(
        stage_id="type_and_access_path_design",
        title="Type and Access Path Design",
        purpose=(
            "Expand planned types into coder-facing TYPE_SPEC objects and ACCESS_PATHS. "
            "This must include wire-relevant fields, resource handles, ownership fields, and opaque boundaries. "
            "It may only define type identities already present in public_artifact_inventory."
        ),
        engineering_focus=[
            "TYPE_SPEC for OPAQUE/STRUCT/ENUM/UNION/CALLBACK/ALIAS",
            "field names and C types",
            "wire mapping targets",
            "public access paths",
            "memory ownership encoded in fields",
            "schema-valid header data",
        ],
        required_output={
            "type_definition_overlays": "array of {type_id, definition_overlay}; type_id must be selected from the registry enum",
            "artifact_requests": "array of controlled requests for a genuinely missing artifact; never inline a new identity",
            "type_dependency_notes": "array of include/order requirements",
            "type_diagnostics": "array of unresolved or unsafe type decisions",
        },
        partition_strategy="registry_types",
    ),
    PlanningStage(
        stage_id="function_interface_design",
        title="Function Interface Design",
        purpose=(
            "Expand planned function inventory into concrete C interfaces without designing function bodies, call graph, "
            "wire mappings, or test vectors. Every identity must already exist in public_artifact_inventory."
        ),
        engineering_focus=[
            "owner file for every function",
            "TRACE_ID and function identity",
            "FUNCTION_TYPE and visibility",
            "C function names and signatures",
            "parameter nullability and ownership",
            "HEADER.INTERFACE and SOURCE.INTERFACE information",
        ],
        required_output={
            "function_interfaces": "array of {function_id, owner_file, trace_id, function_type, visibility, role, signature, trace_refs}",
            "header_interfaces": "array of coder-facing HEADER.INTERFACE items grouped by file",
            "source_interfaces": "array of coder-facing SOURCE.INTERFACE items grouped by file",
            "interface_diagnostics": "array of missing owner/signature/visibility findings",
        },
    ),
    PlanningStage(
        stage_id="function_behavior_design",
        title="Function Behavior Design",
        purpose=(
            "Design LOGIC or EVENT behavior contracts for a bounded group of functions. This stage is partitioned by module "
            "and by owner file when needed; it must not generate test vectors or add new functions."
        ),
        engineering_focus=[
            "LOGIC or EVENT body for each function in the current partition",
            "preconditions, postconditions, state changes, and response behavior",
            "wire behavior summaries for parser/serializer functions",
            "WIRE_MAPPING when facts and access paths support it",
            "behavior diagnostics for unresolved assumptions",
        ],
        required_output={
            "function_behaviors": "array of {function_id, trace_id, LOGIC or EVENT, wire_mapping?, trace_refs}",
            "wire_mappings": "array of function-scoped wire mappings; use JSON strings/numbers only",
            "behavior_diagnostics": "array of unresolved behavior findings for the current partition",
        },
        partition_strategy="module_then_file",
    ),
    PlanningStage(
        stage_id="function_call_contract_closure",
        title="Function Call Contract Closure",
        purpose=(
            "Emit typed call intents between existing canonical functions. Names, signatures, owners, visibility, RELY.FUNC, "
            "header dependencies, and coder-facing CALL_CONTRACTS are derived deterministically."
        ),
        engineering_focus=[
            "typed caller/callee function IDs",
            "call purpose, condition, argument semantics, and result usage",
            "caller/callee visibility constraints",
            "every cross-module callee input has an explicit parameter, provider, registry lookup, or access service",
            "callback providers exactly match declared callback signatures and registration callers name every callback dependency",
            "owned resources have complete create/use/destroy call paths",
            "diagnostics for unknown callees or signature drift",
        ],
        required_output={
            "call_edges": "array of {caller_function_id, callee_function_id, call_purpose, condition, argument_semantics, result_usage}",
            "artifact_requests": "array of controlled requests for missing callable artifacts",
            "call_diagnostics": "array of unresolved or ambiguous call intents",
        },
        partition_strategy="function_callers",
    ),
    PlanningStage(
        stage_id="function_test_vector_design",
        title="Function Test Vector Design",
        purpose=(
            "Generate JSON-safe function/file/runtime test vectors only. Do not change signatures, behavior contracts, "
            "call contracts, or wire mappings."
        ),
        engineering_focus=[
            "function-level TEST_VECTORS",
            "file-level TEST_VECTORS",
            "runtime TEST_VECTORS",
            "JSON-safe byte data: decimal integers or strings only",
            "traceability from tests to facts/functions",
        ],
        required_output={
            "function_test_vectors": "mapping function_id -> array of test vectors",
            "file_test_vectors": "mapping file_id -> array of test vectors",
            "runtime_test_vectors": "array of module/protocol-level test vectors",
            "test_vector_diagnostics": "array of missing or deferred test-vector findings",
        },
    ),
    PlanningStage(
        stage_id="dependency_closure",
        title="Dependency Closure",
        purpose=(
            "Choose only dependency ordering or architecture relations that cannot be derived uniquely from registry ownership, "
            "foreign type references, callbacks, and typed call edges. Do not repeat artifact inventories."
        ),
        engineering_focus=[
            "module dependency graph",
            "header dependency graph",
            "source dependency graph",
            "function call graph",
            "generation order",
            "cycle and orphan checks",
            "public/private visibility consistency",
            "opaque type constructability and cleanup",
            "runtime main entrypoint and startup/cleanup closure",
            "architecture ownership and interface dependency consistency",
        ],
        required_output={
            "ordering_choices": "array of non-derivable ordering choices with reason, provenance, and affected artifact IDs",
            "architecture_choices": "array of non-derivable dependency choices with reason and provenance",
            "artifact_requests": "array of controlled requests; never repeat or replace inventory",
            "dependency_diagnostics": "array of unresolved ordering or architecture findings",
        },
    ),
    PlanningStage(
        stage_id="final_plan_assembly",
        title="Final Implementation Plan Assembly",
        purpose=(
            "Assemble the final implementation_plan JSON consumed by the deterministic specs compiler. "
            "No new module/file/type/function may be introduced here; this stage only reconciles prior stage artifacts."
        ),
        engineering_focus=[
            "complete modules/files/types/functions arrays",
            "engineering_decisions",
            "open_assumptions",
            "architecture object",
            "consistency rules",
            "forbidden symbols",
            "test vectors",
            "plan-to-spec mapping",
        ],
        required_output={
            "schema_version": "specforge_planning_ir_v1",
            "protocol": "object with name, slug, spec_version, roles, default_port, scope, trace_refs",
            "modules": "array; compiler will lower to PROTOCOL_MODULE_SPEC.MODULES",
            "files": "array; compiler will lower to FILE_SPEC documents",
            "types": "array; compiler will lower to HEADER.DATA/PUBLIC_SYMBOLS/ACCESS_PATHS",
            "functions": "array; assemble from function_interface_design, function_behavior_design, function_call_contract_closure, and function_test_vector_design",
            "engineering_decisions": "array with supporting_fact_refs and activated_rule_refs",
            "open_assumptions": "array",
            "architecture": "object",
            "consistency_rules": "array",
            "forbidden_symbols": "array",
            "test_vectors": "array",
            "plan_to_spec_mapping": "array explaining lowering provenance",
        },
    ),
)


def _empty_recoverable_stage_artifact(stage_id: str) -> dict[str, Any] | None:
    return {
        "scope_fact_inventory": {
            "confirmed_scope": [], "deferred_scope": [], "fact_inventory": [],
            "open_assumption_candidates": [], "no_invention_checks": [],
        },
        "architecture_boundaries": {
            "module_candidates": [], "ownership_decisions": [], "cross_module_services": [],
            "coverage_matrix": [], "architecture_diagnostics": [],
        },
        "public_artifact_inventory": {
            "types": [], "constants_or_macros": [], "functions": [], "lifecycle_matrix": [],
            "runtime_entrypoint": {}, "public_symbol_table": [], "forbidden_symbols": [],
        },
        "function_test_vector_design": {
            "function_test_vectors": {}, "file_test_vectors": {}, "runtime_test_vectors": [],
            "test_vector_diagnostics": [],
        },
        "dependency_closure": {
            "ordering_choices": [], "architecture_choices": [], "artifact_requests": [],
            "dependency_diagnostics": [],
        },
    }.get(stage_id)


class LLMStructuredPlanner:
    def __init__(self, api_key_env: str = "ALI_API", stage_log_dir: str | Path | None = None) -> None:
        self.api_key_env = api_key_env
        self.stage_log_dir = Path(stage_log_dir) if stage_log_dir is not None else None
        self.stage_records: list[dict[str, Any]] = []
        self.registry: CanonicalPlanningRegistry | None = None
        self.unresolved_partitions: list[dict[str, Any]] = []
        self.blocking_diagnostics: list[dict[str, Any]] = []
        amendment_log = self.stage_log_dir.parent / "inventory_amendments.json" if self.stage_log_dir is not None else None
        self.amendments = InventoryAmendmentProcessor(log_path=amendment_log)
        validation_log = self.stage_log_dir.parent / "validation_layers.json" if self.stage_log_dir is not None else None
        self.validation_ledger = ValidationLedger(validation_log)

    def build_plan(self, context: dict[str, Any], *, resume_from: str | None = None) -> dict[str, Any]:
        if self.stage_log_dir is not None:
            self.stage_log_dir.mkdir(parents=True, exist_ok=True)
        start_index = 0
        stage_artifacts: list[dict[str, Any]] = []
        if resume_from is not None:
            if self.stage_log_dir is None:
                raise ValueError("resume_from requires stage_log_dir")
            start_index = _resume_start_index(resume_from)
            stage_artifacts = _load_logged_stage_artifacts(self.stage_log_dir, start_index)
            self.stage_records = _load_logged_stage_records(self.stage_log_dir, start_index)
            self.registry = build_registry(stage_artifacts, protocol_slug=_context_protocol_slug(context))
            self.amendments.set_registry(self.registry)
            if resume_from == "compile_specs":
                print("[planning] resume from compile_specs; reusing completed structured planning stages", flush=True)
                reconciled = _assemble_final_plan_candidate(context, stage_artifacts[:-1], registry=self.registry)
                stage_artifacts[-1] = {
                    "stage_id": PLANNING_STAGES[-1].stage_id,
                    "title": PLANNING_STAGES[-1].title,
                    "artifact": reconciled,
                }
                self._write_typed_stage_overlays(stage_artifacts)
                return _assemble_plan_from_stage_artifacts(stage_artifacts, self.stage_records)
            _clear_stage_logs_from(self.stage_log_dir, start_index)
            print(f"[planning] resume from stage {start_index + 1}/{len(PLANNING_STAGES)}: {resume_from}", flush=True)

        for stage in PLANNING_STAGES[start_index:]:
            try:
                artifact = self._run_stage(stage, context, stage_artifacts)
            except BaseException:
                self._write_run_metrics("structured_planning_failed")
                raise
            completed = {"stage_id": stage.stage_id, "title": stage.title, "artifact": artifact}
            try:
                candidate_registry = self._validate_stage_commit(
                    stage, stage_artifacts, completed, context
                )
            except ValueError as exc:
                issue = classify_partition_error(exc)
                if stage.stage_id == "final_plan_assembly":
                    self._record_stage_validation_failure(stage, exc)
                    raise RecoverablePlanningError(stage.stage_id, str(exc)) from exc
                if stage.partition_strategy is not None or self.unresolved_partitions:
                    self._record_unresolved_partition(
                        stage_id=stage.stage_id,
                        partition_id="whole_stage",
                        diagnostic=str(exc),
                        correction_attempted=stage.partition_strategy is not None,
                        issue=issue,
                    )
                    candidate_registry = self.registry
                else:
                    self.validation_ledger.record(
                        issue, stage_id=stage.stage_id, partition_id="whole_stage", outcome="correction_requested"
                    )
                    from agent.common.llm_client import FixedQwenClient

                    stage_dir = self._stage_dir(stage)
                    if stage_dir is not None:
                        stage_dir.mkdir(parents=True, exist_ok=True)
                    correction_usage: dict[str, int] = {}
                    correction_error: ValueError | None = None
                    try:
                        corrected, correction_usage = self._request_local_semantic_correction(
                            client=FixedQwenClient(api_key_env=self.api_key_env),
                            stage=stage,
                            partition={"partition_id": "whole_stage"},
                            artifact=artifact,
                            validation_error=str(exc),
                            partition_dir=stage_dir,
                            context=context,
                            previous_artifacts=stage_artifacts,
                            validation_issue=issue,
                        )
                        completed["artifact"] = corrected
                        self._record_whole_stage_correction(stage, corrected, correction_usage, success=False)
                        candidate_registry = self._validate_stage_commit(
                            stage, stage_artifacts, completed, context
                        )
                    except ValueError as failed_correction:
                        correction_error = failed_correction
                        fallback = _empty_recoverable_stage_artifact(stage.stage_id)
                        if fallback is None:
                            self._record_stage_validation_failure(stage, failed_correction)
                            raise RecoverablePlanningError(stage.stage_id, str(failed_correction)) from failed_correction
                        completed["artifact"] = fallback
                        self._record_unresolved_partition(
                            stage_id=stage.stage_id,
                            partition_id="whole_stage",
                            diagnostic=str(failed_correction),
                            correction_attempted=True,
                            issue=classify_partition_error(failed_correction),
                        )
                        candidate_registry = self._validate_stage_commit(
                            stage, stage_artifacts, completed, context
                        )
                        self._record_whole_stage_correction(stage, fallback, correction_usage, success=False)
                    if correction_error is None:
                        self.validation_ledger.record(
                            issue, stage_id=stage.stage_id, partition_id="whole_stage", outcome="corrected"
                        )
                        self._record_whole_stage_correction(stage, completed["artifact"], correction_usage, success=True)
            stage_artifacts.append(completed)
            self.registry = candidate_registry
            self.amendments.set_registry(self.registry)
            self._write_registry_snapshot()
            if self.stage_log_dir is not None:
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
            self._write_run_metrics("structured_planning_in_progress")

        self._write_run_metrics("structured_planning_completed")
        self._write_typed_stage_overlays(stage_artifacts)
        return _assemble_plan_from_stage_artifacts(stage_artifacts, self.stage_records)

    def _validate_stage_commit(
        self,
        stage: PlanningStage,
        stage_artifacts: list[dict[str, Any]],
        completed: dict[str, Any],
        context: dict[str, Any],
    ) -> CanonicalPlanningRegistry | None:
        candidate_registry = self.registry
        if stage.stage_id in {"module_file_plan", "public_artifact_inventory"}:
            candidate_registry = build_registry(
                [*stage_artifacts, completed], protocol_slug=_context_protocol_slug(context)
            )
        elif stage.stage_id in {
            "type_and_access_path_design",
            "function_interface_design",
            "function_test_vector_design",
        } and self.registry is not None:
            candidate_registry = CanonicalPlanningRegistry.from_snapshot(self.registry.snapshot())
            advance_registry(candidate_registry, [*stage_artifacts, completed], stage_id=stage.stage_id)
        for pending in self.amendments.pending_for_stage(stage.stage_id):
            partition_id = str(pending.get("partition_id") or "whole_stage")
            if not any(
                item["stage_id"] == stage.stage_id and item["partition_id"] == partition_id
                for item in self.unresolved_partitions
            ):
                self._record_unresolved_partition(
                    stage_id=stage.stage_id,
                    partition_id=partition_id,
                    diagnostic=str(pending["diagnostic"]),
                    correction_attempted=False,
                )
        _validate_completed_stage(
            stage.stage_id,
            [*stage_artifacts, completed],
            context,
            registry=candidate_registry,
            allow_incomplete=bool(self.unresolved_partitions),
        )
        return candidate_registry

    def _stage_dir(self, stage: PlanningStage) -> Path | None:
        if self.stage_log_dir is None:
            return None
        stage_index = next(index for index, item in enumerate(PLANNING_STAGES) if item.stage_id == stage.stage_id)
        return _stage_log_path(self.stage_log_dir, stage_index, stage)

    def _record_whole_stage_correction(
        self,
        stage: PlanningStage,
        artifact: dict[str, Any],
        usage: dict[str, int],
        *,
        success: bool,
    ) -> None:
        if self.stage_records and self.stage_records[-1].get("stage_id") == stage.stage_id:
            record = self.stage_records[-1]
            record["semantic_correction_usage"] = usage
            record["request_count"] = int(record.get("request_count", 0)) + 1
            record["prompt_characters"] = int(record.get("prompt_characters", 0)) + int(
                usage.get("prompt_characters", 0)
            )
            record["local_corrections"] = 1
            record["local_correction_successes"] = int(success)
        stage_dir = self._stage_dir(stage)
        if stage_dir is not None:
            write_json(stage_dir / "artifact.json", artifact)
            if self.stage_records:
                write_json(stage_dir / "stage_manifest.json", self.stage_records[-1])
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)

    def _record_stage_validation_failure(self, stage: PlanningStage, error: ValueError) -> None:
        if self.stage_records and self.stage_records[-1].get("stage_id") == stage.stage_id:
            self.stage_records[-1]["status"] = "failed"
            self.stage_records[-1]["stage_validation_error"] = str(error)
        stage_dir = self._stage_dir(stage)
        if stage_dir is not None:
            write_json(stage_dir / "stage_validation_errors.json", {"stage_id": stage.stage_id, "error": str(error)})
            if self.stage_records:
                write_json(stage_dir / "stage_manifest.json", self.stage_records[-1])
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
        self._write_run_metrics("structured_planning_failed")

    def _write_run_metrics(self, run_status: str) -> None:
        if self.stage_log_dir is None:
            return
        write_json(
            self.stage_log_dir / "run_metrics.json",
            build_run_metrics(self.stage_records, expected_stages=len(PLANNING_STAGES), run_status=run_status),
        )

    def _write_registry_snapshot(self) -> None:
        if self.stage_log_dir is None or self.registry is None:
            return
        write_json(self.stage_log_dir.parent / "canonical_registry_snapshot.json", self.registry.snapshot())

    def _write_typed_stage_overlays(self, artifacts: list[dict[str, Any]]) -> None:
        if self.stage_log_dir is None or self.registry is None:
            return
        write_json(
            self.stage_log_dir.parent / "typed_stage_overlays.json",
            {"kind": "TYPED_STAGE_OVERLAYS", "schema_version": 1, "overlays": _materialize_typed_stage_artifacts(artifacts, self.registry)},
        )

    def _process_artifact_requests(
        self,
        stage: PlanningStage,
        artifact: dict[str, Any],
        *,
        partition_id: str | None = None,
    ) -> list[dict[str, Any]]:
        outcomes = self.amendments.process(
            artifact.get("artifact_requests", []), stage_id=stage.stage_id, partition_id=partition_id
        )
        if outcomes:
            self._write_registry_snapshot()
        return outcomes

    def _record_unresolved_partition(
        self,
        *,
        stage_id: str,
        partition_id: str,
        diagnostic: str,
        correction_attempted: bool,
        issue: ValidationIssue | None = None,
    ) -> None:
        issue = issue or ValidationIssue(
            "binding",
            diagnostic.split(":", 1)[0],
            diagnostic,
            "local_semantic_correction_or_artifact_request",
        )
        diagnostic_id = self.validation_ledger.record(
            issue,
            stage_id=stage_id,
            partition_id=partition_id,
            outcome="unresolved",
        )
        record = {
            "stage_id": stage_id,
            "partition_id": partition_id,
            "status": "unresolved",
            "diagnostic": diagnostic,
            "correction_attempted": correction_attempted,
            "diagnostic_id": diagnostic_id,
            "validation_layer": issue.layer,
            "diagnostic_code": issue.code,
            "recovery": issue.recovery,
        }
        self.unresolved_partitions.append(record)
        self.blocking_diagnostics.append(
            {"level": "error", "code": "unresolved_partition", "message": diagnostic, "path": f"{stage_id}:{partition_id}"}
        )
        if self.stage_log_dir is not None:
            write_json(self.stage_log_dir.parent / "unresolved_partitions.json", self.unresolved_partitions)
            write_json(self.stage_log_dir.parent / "blocking_diagnostics.json", self.blocking_diagnostics)

    def _run_stage(
        self,
        stage: PlanningStage,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if stage.stage_id == "final_plan_assembly":
            return self._run_deterministic_final_stage(stage, context, previous_artifacts)
        if stage.stage_id == "dependency_closure":
            ambiguities = dependency_ambiguities(previous_artifacts, context)
            if not ambiguities["open_assumptions"] and not ambiguities["upstream_diagnostics"]:
                return self._run_zero_token_stage(
                    stage,
                    {
                        "ordering_choices": [],
                        "architecture_choices": [],
                        "artifact_requests": [],
                        "dependency_diagnostics": [],
                    },
                    mode="deterministic_no_ambiguity",
                )
        if stage.partition_strategy is not None:
            return self._run_partitioned_stage(stage, context, previous_artifacts)
        return self._run_single_stage(stage, context, previous_artifacts)

    def _run_zero_token_stage(
        self, stage: PlanningStage, artifact: dict[str, Any], *, mode: str
    ) -> dict[str, Any]:
        stage_index = len(self.stage_records) + 1
        stage_dir = self.stage_log_dir / f"{stage_index:02d}_{stage.stage_id}" if self.stage_log_dir is not None else None
        record = {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "status": "completed",
            "mode": mode,
            "elapsed_seconds": 0.0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "request_count": 0,
            "prompt_characters": 0,
            "repaired_json": False,
            "inventory_amendments": 0,
            "artifact_keys": sorted(artifact),
        }
        self.stage_records.append(record)
        if stage_dir is not None:
            stage_dir.mkdir(parents=True, exist_ok=True)
            write_json(stage_dir / "prompt.json", {"mode": mode, "stage_id": stage.stage_id})
            write_json(stage_dir / "artifact.json", artifact)
            write_json(stage_dir / "stage_manifest.json", record)
        print(f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} done: {stage.stage_id}; mode={mode}; tokens=0", flush=True)
        return artifact

    def _run_deterministic_final_stage(
        self,
        stage: PlanningStage,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stage_index = len(self.stage_records) + 1
        stage_dir = self.stage_log_dir / f"{stage_index:02d}_{stage.stage_id}" if self.stage_log_dir is not None else None
        print(f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} start: {stage.stage_id}; mode=deterministic", flush=True)
        started = time.monotonic()
        try:
            artifact = _assemble_final_plan_candidate(
                context,
                previous_artifacts,
                registry=self.registry,
                allow_incomplete=bool(self.unresolved_partitions),
            )
            if self.unresolved_partitions:
                artifact["unresolved_partitions"] = deepcopy(self.unresolved_partitions)
                artifact["blocking_diagnostics"] = deepcopy(self.blocking_diagnostics)
        except ValueError as exc:
            record = {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": "failed",
                "mode": "deterministic_reconciliation",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "stage_validation_error": str(exc),
            }
            self.stage_records.append(record)
            if stage_dir is not None:
                stage_dir.mkdir(parents=True, exist_ok=True)
                write_json(stage_dir / "stage_validation_errors.json", {"stage_id": stage.stage_id, "error": str(exc)})
                write_json(stage_dir / "stage_manifest.json", record)
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
            raise
        elapsed = time.monotonic() - started
        record = {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "status": "completed",
            "mode": "deterministic_reconciliation",
            "elapsed_seconds": round(elapsed, 3),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "repaired_json": False,
            "artifact_keys": sorted(artifact.keys()),
        }
        self.stage_records.append(record)
        if stage_dir is not None:
            stage_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                stage_dir / "prompt.json",
                {
                    "mode": "deterministic_reconciliation",
                    "stage_id": stage.stage_id,
                    "source_stage_ids": [item.get("stage_id") for item in previous_artifacts],
                    "contract": "Reconcile existing stable artifacts only; do not add module/file/type/function identities.",
                },
            )
            (stage_dir / "response.raw.txt").write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_json(stage_dir / "artifact.json", artifact)
            write_json(stage_dir / "stage_manifest.json", record)
        print(
            f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} done: {stage.stage_id}; "
            f"mode=deterministic; tokens=0; elapsed={elapsed:.1f}s",
            flush=True,
        )
        return artifact

    def _run_single_stage(
        self,
        stage: PlanningStage,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from agent.common.llm_client import FixedQwenClient, LLMRequest

        stage_index = len(self.stage_records) + 1
        stage_prefix = f"{stage_index:02d}_{stage.stage_id}"
        stage_dir = self.stage_log_dir / stage_prefix if self.stage_log_dir is not None else None
        if stage_dir is not None:
            stage_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                stage_dir / "stage_manifest.json",
                {
                    "stage_id": stage.stage_id,
                    "title": stage.title,
                    "status": "started",
                    "previous_stage_count": len(previous_artifacts),
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
        print(f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} start: {stage.stage_id}", flush=True)
        started = time.monotonic()
        client = FixedQwenClient(api_key_env=self.api_key_env)
        messages = build_stage_messages(stage, context, previous_artifacts, registry=self.registry)
        prompt_content = messages[-1]["content"]
        if stage_dir is not None:
            (stage_dir / "prompt.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        repair_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_characters": 0,
            "request_count": 0,
        }
        try:
            response = client.generate_with_usage(
                LLMRequest(
                    messages=messages,
                    top_p=0.2,
                    temperature=0.1,
                    is_stream=True,
                    enable_thinking=False,
                    max_completion_tokens=16000,
                )
            )
            raw_response = response.content
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if stage_dir is not None:
                (stage_dir / "response.raw.txt").write_text(raw_response, encoding="utf-8")
            try:
                artifact = _parse_json_response(raw_response)
                repaired = False
            except json.JSONDecodeError as parse_exc:
                artifact, repair_usage = self._repair_json_response(
                    client=client,
                    stage=stage,
                    raw_response=raw_response,
                    parse_error=parse_exc,
                    stage_dir=stage_dir,
                )
                repaired = True
            self._process_artifact_requests(stage, artifact)
        except BaseException as exc:
            elapsed = time.monotonic() - started
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            record = {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": status,
                "elapsed_seconds": round(elapsed, 3),
                "usage": usage,
                "repair_usage": repair_usage,
                "request_count": 1,
                "prompt_characters": len(prompt_content) + int(repair_usage.get("prompt_characters", 0)),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.stage_records.append(record)
            if stage_dir is not None:
                write_json(stage_dir / "stage_manifest.json", record)
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
            print(f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} {status}: {stage.stage_id}; {record['error']}", flush=True)
            raise
        elapsed = time.monotonic() - started
        record = {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "status": "completed",
            "elapsed_seconds": round(elapsed, 3),
            "usage": usage,
            "repair_usage": repair_usage,
            "request_count": 1 + int(repaired),
            "prompt_characters": len(prompt_content) + int(repair_usage.get("prompt_characters", 0)),
            "repaired_json": repaired,
            "inventory_amendments": self.amendments.count_for_stage(stage.stage_id),
            "artifact_keys": sorted(artifact.keys()),
        }
        self.stage_records.append(record)
        if stage_dir is not None:
            write_json(stage_dir / "artifact.json", artifact)
            write_json(stage_dir / "stage_manifest.json", record)
        print(
            "[planning] stage "
            f"{stage_index}/{len(PLANNING_STAGES)} done: {stage.stage_id}; "
            f"tokens={usage['total_tokens']} "
            f"(prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}); "
            f"repair_tokens={repair_usage['total_tokens']}; "
            f"elapsed={elapsed:.1f}s",
            flush=True,
        )
        return artifact

    def _run_partitioned_stage(
        self,
        stage: PlanningStage,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from agent.common.llm_client import FixedQwenClient, LLMRequest

        stage_index = len(self.stage_records) + 1
        stage_prefix = f"{stage_index:02d}_{stage.stage_id}"
        stage_dir = self.stage_log_dir / stage_prefix if self.stage_log_dir is not None else None
        if stage_dir is not None:
            stage_dir.mkdir(parents=True, exist_ok=True)
        if stage.partition_strategy == "function_callers":
            if self.registry is None:
                raise RegistryInvariantError("registry_snapshot_missing: Stage 8 requires function registry")
            partitions = _function_call_partitions(self.registry)
        elif stage.partition_strategy == "registry_types":
            if self.registry is None:
                raise RegistryInvariantError("registry_snapshot_missing: Stage 5 requires type registry")
            partitions = _type_definition_partitions(self.registry)
        else:
            partitions = _function_behavior_partitions(previous_artifacts)
        print(
            f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} start: {stage.stage_id}; partitions={len(partitions)}",
            flush=True,
        )
        started = time.monotonic()
        client = FixedQwenClient(api_key_env=self.api_key_env)
        partition_artifacts: list[dict[str, Any]] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        repair_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_characters": 0,
            "request_count": 0,
        }
        semantic_correction_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_characters": 0,
            "request_count": 0,
        }
        request_count = 0
        prompt_characters = 0
        repair_request_count = 0
        local_corrections = 0
        local_correction_successes = 0
        repaired_any = False
        try:
            for partition_index, partition in enumerate(partitions, 1):
                partition_prefix = f"{partition_index:02d}_{partition['partition_id']}"
                partition_dir = stage_dir / partition_prefix if stage_dir is not None else None
                if partition_dir is not None:
                    partition_dir.mkdir(parents=True, exist_ok=True)
                print(
                    f"[planning]   partition {partition_index}/{len(partitions)} start: {partition['partition_id']}",
                    flush=True,
                )
                messages = build_stage_messages(
                    stage,
                    context,
                    previous_artifacts,
                    partition=partition,
                    registry=self.registry,
                )
                prompt_content = messages[-1]["content"]
                if partition_dir is not None:
                    (partition_dir / "prompt.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                request_count += 1
                prompt_characters += len(prompt_content)
                response = client.generate_with_usage(
                    LLMRequest(
                        messages=messages,
                        top_p=0.2,
                        temperature=0.1,
                        is_stream=True,
                        enable_thinking=False,
                        max_completion_tokens=16000,
                    )
                )
                raw_response = response.content
                part_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                _add_usage(usage, part_usage)
                if partition_dir is not None:
                    (partition_dir / "response.raw.txt").write_text(raw_response, encoding="utf-8")
                part_repair_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                try:
                    artifact = _parse_json_response(raw_response)
                    repaired = False
                except json.JSONDecodeError as parse_exc:
                    repair_request_count += 1
                    try:
                        artifact, part_repair_usage = self._repair_json_response(
                            client=client,
                            stage=stage,
                            raw_response=raw_response,
                            parse_error=parse_exc,
                            stage_dir=partition_dir,
                        )
                    except Exception as repair_exc:
                        usage_path = partition_dir / "repair_usage.json" if partition_dir is not None else None
                        if usage_path is not None and usage_path.exists():
                            part_repair_usage = json.loads(usage_path.read_text(encoding="utf-8"))
                        _add_usage(repair_usage, part_repair_usage)
                        diagnostic = f"syntax_repair_failed: {type(repair_exc).__name__}: {repair_exc}"
                        issue = ValidationIssue(
                            "structural", "syntax_repair_failed", diagnostic, "partition_regeneration"
                        )
                        self._record_unresolved_partition(
                            stage_id=stage.stage_id,
                            partition_id=str(partition["partition_id"]),
                            diagnostic=diagnostic,
                            correction_attempted=False,
                            issue=issue,
                        )
                        if partition_dir is not None:
                            write_json(
                                partition_dir / "partition_manifest.json",
                                {
                                    "partition_id": partition["partition_id"],
                                    "status": "unresolved",
                                    "usage": part_usage,
                                    "repair_usage": part_repair_usage,
                                    "diagnostic": diagnostic,
                                    "validation_layer": issue.layer,
                                    "diagnostic_code": issue.code,
                                    "recovery": issue.recovery,
                                    "phases": {"generate": "completed", "parse": "failed", "bind": "not_run", "validate": "not_run", "commit": "rolled_back"},
                                },
                            )
                        continue
                    _add_usage(repair_usage, part_repair_usage)
                    repaired = True
                    repaired_any = True
                correction_attempted = False
                correction_error: str | None = None
                validation_issue: ValidationIssue | None = None
                part_correction_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                try:
                    assert self.registry is not None
                    _validate_partition_artifact(stage, artifact, partition, self.registry)
                except LayeredValidationError as exc:
                    validation_issue = exc.issue
                    correction_attempted = True
                    local_corrections += 1
                    self.validation_ledger.record(
                        exc.issue,
                        stage_id=stage.stage_id,
                        partition_id=str(partition["partition_id"]),
                        outcome="correction_requested",
                    )
                    correction_usage_added = False
                    try:
                        artifact, part_correction_usage = self._request_local_semantic_correction(
                            client=client,
                            stage=stage,
                            partition=partition,
                            artifact=artifact,
                            validation_error=str(exc),
                            partition_dir=partition_dir,
                            context=context,
                            previous_artifacts=previous_artifacts,
                            validation_issue=exc.issue,
                        )
                        _add_usage(semantic_correction_usage, part_correction_usage)
                        correction_usage_added = True
                        _validate_partition_artifact(stage, artifact, partition, self.registry)
                        local_correction_successes += 1
                        self.validation_ledger.record(
                            exc.issue,
                            stage_id=stage.stage_id,
                            partition_id=str(partition["partition_id"]),
                            outcome="corrected",
                        )
                    except Exception as correction_exc:
                        usage_path = partition_dir / "semantic_correction_usage.json" if partition_dir is not None else None
                        if not correction_usage_added and usage_path is not None and usage_path.exists():
                            part_correction_usage = json.loads(usage_path.read_text(encoding="utf-8"))
                            _add_usage(semantic_correction_usage, part_correction_usage)
                        correction_error = f"{type(correction_exc).__name__}: {correction_exc}"
                        if isinstance(correction_exc, LayeredValidationError):
                            validation_issue = correction_exc.issue
                            self.validation_ledger.record(
                                correction_exc.issue,
                                stage_id=stage.stage_id,
                                partition_id=str(partition["partition_id"]),
                                outcome="unresolved",
                            )
                if correction_error is not None:
                    self._record_unresolved_partition(
                        stage_id=stage.stage_id,
                        partition_id=str(partition["partition_id"]),
                        diagnostic=correction_error,
                        correction_attempted=correction_attempted,
                        issue=validation_issue,
                    )
                    if partition_dir is not None:
                        write_json(partition_dir / "uncommitted_artifact.json", artifact)
                        write_json(
                            partition_dir / "partition_manifest.json",
                            {
                                "partition_id": partition["partition_id"],
                                "status": "unresolved",
                                "usage": part_usage,
                                "repair_usage": part_repair_usage,
                                "semantic_correction_usage": part_correction_usage,
                                "diagnostic": correction_error,
                                "validation_layer": validation_issue.layer if validation_issue else "binding",
                                "diagnostic_code": validation_issue.code if validation_issue else "partition_validation_failed",
                                "recovery": validation_issue.recovery if validation_issue else "local_semantic_correction_or_artifact_request",
                                "phases": {"generate": "completed", "parse": "completed", "bind": "failed", "validate": "failed", "commit": "rolled_back"},
                            },
                        )
                    continue

                outcomes = self._process_artifact_requests(
                    stage, artifact, partition_id=str(partition["partition_id"])
                )
                for outcome in outcomes:
                    entry_kind = (
                        self.registry.resolve(outcome["artifact_id"])["artifact_kind"]
                        if outcome.get("artifact_id")
                        else None
                    )
                    if (
                        outcome["status"] == "accepted_pending_rerun"
                        and stage.stage_id == "type_and_access_path_design"
                        and entry_kind in {"type", "callback"}
                    ):
                        partitions.append(
                            {
                                "partition_id": _safe_partition_id(f"amendment_{outcome['artifact_id']}"),
                                "owner_file_id": self.registry.resolve(outcome["artifact_id"])["owner_file_id"],
                                "type_ids": [outcome["artifact_id"]],
                                "amendment_artifact_id": outcome["artifact_id"],
                            }
                        )
                    elif (
                        outcome["status"] == "accepted_pending_rerun"
                        and stage.stage_id == "function_call_contract_closure"
                        and entry_kind == "function"
                    ):
                        try:
                            rerun_partition, _ = self._run_function_amendment_reruns(
                                client=client,
                                artifact_id=str(outcome["artifact_id"]),
                                required_by=str(outcome["request"]["required_by"]),
                                context=context,
                                previous_artifacts=previous_artifacts,
                                partition_dir=partition_dir,
                            )
                            partitions.append(rerun_partition)
                        except Exception as amendment_exc:
                            self._record_unresolved_partition(
                                stage_id=stage.stage_id,
                                partition_id=str(partition["partition_id"]),
                                diagnostic=f"inventory_amendment_rerun_failed: {type(amendment_exc).__name__}: {amendment_exc}",
                                correction_attempted=correction_attempted,
                            )
                    elif outcome["status"] in {"accepted_pending_rerun", "unresolved"}:
                        self._record_unresolved_partition(
                            stage_id=stage.stage_id,
                            partition_id=str(partition["partition_id"]),
                            diagnostic=outcome["diagnostic"],
                            correction_attempted=correction_attempted,
                        )
                committed = {"partition": partition, "artifact": artifact}
                partition_artifacts.append(committed)
                amendment_artifact_id = partition.get("amendment_artifact_id")
                if amendment_artifact_id:
                    amendment_kind = self.registry.resolve(amendment_artifact_id)["artifact_kind"]
                    self.registry.mark_defined(
                        amendment_artifact_id,
                        stage_id=stage.stage_id,
                        expected_kinds={amendment_kind},
                    )
                    self.amendments.resolve_after_local_rerun(
                        amendment_artifact_id, stage_id=stage.stage_id
                    )
                    self._write_registry_snapshot()
                if partition_dir is not None:
                    write_json(partition_dir / "artifact.json", committed)
                    write_json(
                        partition_dir / "partition_manifest.json",
                        {
                            "partition_id": partition["partition_id"],
                            "status": "completed",
                            "usage": part_usage,
                            "repair_usage": part_repair_usage,
                            "semantic_correction_usage": part_correction_usage,
                            "repaired_json": repaired,
                            "inventory_amendments": self.amendments.count_for_stage(stage.stage_id),
                            "phases": {"generate": "completed", "parse": "completed", "bind": "completed", "validate": "completed", "commit": "completed"},
                        },
                    )
                print(
                    f"[planning]   partition {partition_index}/{len(partitions)} done: {partition['partition_id']}; "
                    f"tokens={part_usage['total_tokens']}; repair_tokens={part_repair_usage['total_tokens']}; "
                    f"correction={correction_attempted}",
                    flush=True,
                )
        except BaseException as exc:
            elapsed = time.monotonic() - started
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            inventory_amendment_usage = _sum_logged_usage(stage_dir, "*/amendment_*/usage.json")
            record = {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": status,
                "elapsed_seconds": round(elapsed, 3),
                "usage": usage,
                "repair_usage": repair_usage,
                "semantic_correction_usage": semantic_correction_usage,
                "request_count": request_count
                + repair_request_count
                + local_corrections
                + int(inventory_amendment_usage.get("request_count", 0)),
                "prompt_characters": prompt_characters
                + int(repair_usage.get("prompt_characters", 0))
                + int(semantic_correction_usage.get("prompt_characters", 0))
                + int(inventory_amendment_usage.get("prompt_characters", 0)),
                "inventory_amendment_usage": inventory_amendment_usage,
                "local_corrections": local_corrections,
                "local_correction_successes": local_correction_successes,
                "partitions_completed": len(partition_artifacts),
                "partitions_total": len(partitions),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.stage_records.append(record)
            if stage_dir is not None:
                write_json(stage_dir / "stage_manifest.json", record)
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
            print(f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} {status}: {stage.stage_id}; {record['error']}", flush=True)
            raise

        if stage.partition_strategy == "function_callers":
            artifact = _merge_call_edge_artifacts(partition_artifacts)
        elif stage.partition_strategy == "registry_types":
            artifact = _merge_type_definition_artifacts(partition_artifacts)
        else:
            artifact = _merge_function_behavior_artifacts(partition_artifacts)
        elapsed = time.monotonic() - started
        inventory_amendment_usage = _sum_logged_usage(stage_dir, "*/amendment_*/usage.json")
        record = {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "status": "completed",
            "elapsed_seconds": round(elapsed, 3),
            "usage": usage,
            "repair_usage": repair_usage,
            "semantic_correction_usage": semantic_correction_usage,
            "request_count": request_count
            + repair_request_count
            + local_corrections
            + int(inventory_amendment_usage.get("request_count", 0)),
            "prompt_characters": prompt_characters
            + int(repair_usage.get("prompt_characters", 0))
            + int(semantic_correction_usage.get("prompt_characters", 0))
            + int(inventory_amendment_usage.get("prompt_characters", 0)),
            "inventory_amendment_usage": inventory_amendment_usage,
            "repaired_json": repaired_any,
            "partitions_completed": len(partition_artifacts),
            "partitions_total": len(partitions),
            "inventory_amendments": self.amendments.count_for_stage(stage.stage_id),
            "local_corrections": local_corrections,
            "local_correction_successes": local_correction_successes,
            "unresolved_partitions": sum(
                item["stage_id"] == stage.stage_id for item in self.unresolved_partitions
            ),
            "artifact_keys": sorted(artifact.keys()),
        }
        self.stage_records.append(record)
        if stage_dir is not None:
            write_json(stage_dir / "artifact.json", artifact)
            write_json(stage_dir / "stage_manifest.json", record)
        print(
            "[planning] stage "
            f"{stage_index}/{len(PLANNING_STAGES)} done: {stage.stage_id}; "
            f"tokens={usage['total_tokens']}; repair_tokens={repair_usage['total_tokens']}; "
            f"partitions={len(partitions)}; elapsed={elapsed:.1f}s",
            flush=True,
        )
        return artifact

    def _repair_json_response(
        self,
        *,
        client: Any,
        stage: PlanningStage,
        raw_response: str,
        parse_error: json.JSONDecodeError,
        stage_dir: Path | None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        from agent.common.llm_client import LLMRequest

        messages = build_json_repair_messages(stage, raw_response, parse_error)
        prompt_content = messages[-1]["content"]
        if stage_dir is not None:
            (stage_dir / "repair_prompt.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        response = client.generate_with_usage(
            LLMRequest(
                messages=messages,
                top_p=0.1,
                temperature=0.0,
                is_stream=True,
                enable_thinking=False,
                max_completion_tokens=16000,
            )
        )
        if stage_dir is not None:
            (stage_dir / "repair_response.raw.txt").write_text(response.content, encoding="utf-8")
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "prompt_characters": len(prompt_content),
            "request_count": 1,
        }
        if stage_dir is not None:
            write_json(stage_dir / "repair_usage.json", usage)
        artifact = _parse_json_response(response.content)
        return artifact, usage

    def _request_local_semantic_correction(
        self,
        *,
        client: Any,
        stage: PlanningStage,
        partition: dict[str, Any],
        artifact: dict[str, Any],
        validation_error: str,
        partition_dir: Path | None,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
        validation_issue: ValidationIssue,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        from agent.common.llm_client import LLMRequest

        messages = build_local_correction_messages(
            stage,
            context,
            previous_artifacts,
            partition,
            self.registry,
            artifact,
            validation_error,
            validation_layer=validation_issue.layer,
            diagnostic_code=validation_issue.code,
            required_recovery=validation_issue.recovery,
        )
        prompt_content = messages[-1]["content"]
        if partition_dir is not None:
            (partition_dir / "semantic_correction_prompt.json").write_text(
                json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        response = client.generate_with_usage(
            LLMRequest(
                messages=messages,
                top_p=0.1,
                temperature=0.0,
                is_stream=True,
                enable_thinking=False,
                max_completion_tokens=16000,
            )
        )
        if partition_dir is not None:
            (partition_dir / "semantic_correction_response.raw.txt").write_text(response.content, encoding="utf-8")
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "prompt_characters": len(prompt_content),
            "request_count": 1,
        }
        if partition_dir is not None:
            write_json(partition_dir / "semantic_correction_usage.json", usage)
        corrected = _parse_json_response(response.content)
        return corrected, usage

    def _run_function_amendment_reruns(
        self,
        *,
        client: Any,
        artifact_id: str,
        required_by: str,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
        partition_dir: Path | None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        from agent.common.llm_client import LLMRequest

        assert self.registry is not None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        def generate(stage_id: str, contract: dict[str, Any]) -> dict[str, Any]:
            stage = next(item for item in PLANNING_STAGES if item.stage_id == stage_id)
            messages = build_amendment_messages(
                stage,
                context,
                previous_artifacts,
                self.registry,
                artifact_id=artifact_id,
                required_by=required_by,
                semantic_role=str(self.registry.resolve(artifact_id).get("semantic_role", "")),
                contract=contract,
            )
            log_dir = partition_dir / f"amendment_{stage_id}" if partition_dir is not None else None
            if log_dir is not None:
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "prompt.json").write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            response = client.generate_with_usage(
                LLMRequest(
                    messages=messages,
                    top_p=0.1,
                    temperature=0.0,
                    is_stream=True,
                    enable_thinking=False,
                    max_completion_tokens=16000,
                )
            )
            call_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "prompt_characters": len(messages[-1]["content"]),
                "request_count": 1,
            }
            _add_usage(usage, call_usage)
            if log_dir is not None:
                (log_dir / "response.raw.txt").write_text(response.content, encoding="utf-8")
                write_json(log_dir / "usage.json", call_usage)
            artifact = _parse_json_response(response.content)
            if log_dir is not None:
                write_json(log_dir / "artifact.json", artifact)
            return artifact

        interface_artifact = generate(
            "function_interface_design",
            {
                "required_output": "exactly one function_interfaces entry for artifact_id",
                "canonical_fields_from_registry": ["function_id", "owner_file", "visibility", "name"],
                "must_supply": ["complete signature", "function_type", "role", "trace_refs"],
            },
        )
        interfaces = interface_artifact.get("function_interfaces", [])
        by_id, _ = _canonical_function_indexes({"function_interfaces": interfaces}, self.registry)
        if set(by_id) != {artifact_id}:
            raise ValueError(f"amendment_interface_partition_coverage: expected only {artifact_id}")
        interface_stage = _stage_artifact(previous_artifacts, "function_interface_design")
        interface_stage.setdefault("function_interfaces", []).extend(deepcopy(interfaces))
        self.registry.mark_defined(
            artifact_id, stage_id="function_interface_design", expected_kinds={"function"}
        )

        behavior_artifact = generate(
            "function_behavior_design",
            {
                "required_output": "exactly one function_behaviors entry for artifact_id",
                "allowed_fields": ["function_id", "trace_id", "LOGIC", "EVENT", "wire_mapping", "trace_refs"],
                "do_not_generate_calls_or_tests": True,
            },
        )
        behavior_partition = {
            "partition_id": _safe_partition_id(f"amendment_behavior_{artifact_id}"),
            "functions": [{"function_id": artifact_id}],
        }
        behavior_stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_behavior_design")
        _validate_partition_artifact(
            behavior_stage, behavior_artifact, behavior_partition, self.registry
        )
        committed_behavior = _stage_artifact(previous_artifacts, "function_behavior_design")
        for key in ("function_behaviors", "wire_mappings", "behavior_diagnostics"):
            committed_behavior.setdefault(key, []).extend(deepcopy(behavior_artifact.get(key, [])))

        caller = self.registry.resolve(required_by, expected_kinds={"function"})["artifact_id"]
        return (
            {
                "partition_id": _safe_partition_id(f"amendment_calls_{artifact_id}"),
                "owner_module_id": self.registry.resolve(artifact_id)["owner_module_id"],
                "owner_file_id": self.registry.resolve(artifact_id)["owner_file_id"],
                "caller_function_ids": list(dict.fromkeys([caller, artifact_id])),
                "amendment_artifact_id": artifact_id,
                "required_edge_artifact_id": artifact_id,
            },
            usage,
        )


def _add_usage(total: dict[str, int], item: dict[str, int]) -> None:
    for key in total:
        total[key] += int(item.get(key, 0))


def _sum_logged_usage(root: Path | None, pattern: str) -> dict[str, int]:
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_characters": 0,
        "request_count": 0,
    }
    if root is None:
        return total
    for path in root.glob(pattern):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            _add_usage(total, item)
    return total


def _stage_artifact(previous_artifacts: list[dict[str, Any]], stage_id: str) -> dict[str, Any]:
    for item in reversed(previous_artifacts):
        if item.get("stage_id") == stage_id and isinstance(item.get("artifact"), dict):
            return item["artifact"]
    return {}


def _context_protocol_slug(context: dict[str, Any]) -> str:
    characteristics = context.get("characteristics", {})
    return str(characteristics.get("protocol_slug") or characteristics.get("protocol_name") or "protocol").lower()


def _find_named_integer(value: Any, target_name: str) -> int | None:
    if isinstance(value, dict):
        if str(value.get("name", "")) == target_name:
            match = re.search(r"\b([1-9][0-9]{0,4})\b", str(value.get("value_or_rule", value.get("value", ""))))
            if match and int(match.group(1)) <= 65535:
                return int(match.group(1))
        for item in value.values():
            found = _find_named_integer(item, target_name)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_named_integer(item, target_name)
            if found is not None:
                return found
    return None


def _stable_artifact_key(item: dict[str, Any], kind: str) -> str:
    keys = {
        "module": ("id", "name"),
        "file": ("id",),
        "type": ("id", "type_name", "symbol", "name"),
        "function": ("function_id", "id"),
    }
    return next((str(item.get(key)) for key in keys[kind] if item.get(key)), "")


def _canonical_function_indexes(
    interfaces: dict[str, Any], registry: CanonicalPlanningRegistry | None = None
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for item in interfaces.get("function_interfaces", []):
        if not isinstance(item, dict):
            continue
        function_id = _stable_artifact_key(item, "function")
        raw_signature = item.get("signature", {})
        signature = str(raw_signature.get("RAW", "")) if isinstance(raw_signature, dict) else str(raw_signature or "")
        signature = signature.strip().removesuffix(";").strip()
        match = re.match(r".+?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(.*\)\s*$", signature)
        name = str(item.get("name") or (raw_signature.get("NAME") if isinstance(raw_signature, dict) else "") or (match.group(1) if match else ""))
        owner = str(item.get("owner_file") or item.get("file") or "")
        visibility = str(item.get("visibility") or "")
        aliases = {function_id, name}
        if registry is not None:
            entry = registry.resolve(function_id or name, expected_kinds={"function"})
            owner_entry = registry.resolve(owner, expected_kinds={"file"})
            if owner_entry["artifact_id"] != entry["owner_file_id"]:
                errors.append(f"canonical_function_owner_conflict: {function_id} owner {owner!r} differs from registry")
                continue
            if visibility.lower() != str(entry["visibility"]).lower():
                errors.append(f"canonical_function_visibility_conflict: {function_id} differs from registry")
                continue
            function_id = entry["artifact_id"]
            name = entry["canonical_name"]
            owner = entry["owner_file_id"]
            aliases.update(entry.get("aliases", []))
        if not function_id:
            errors.append("canonical_function_id_missing: Stage 6 function has no stable function_id")
            continue
        if not owner or not visibility or not match:
            errors.append(
                f"canonical_function_incomplete: {function_id} requires owner, visibility, and a complete C signature"
            )
            continue
        if match.group(1) != name:
            errors.append(f"canonical_function_signature_identity_conflict: {function_id} names {match.group(1)!r}")
            continue
        previous = by_id.get(function_id)
        if previous is not None:
            previous_signature = previous.get("signature", {})
            previous_raw = str(previous_signature.get("RAW", "")) if isinstance(previous_signature, dict) else str(previous_signature)
            previous_owner = str(previous.get("owner_file") or previous.get("file") or "")
            if previous_owner != owner or previous_raw != signature:
                errors.append(
                    f"canonical_function_conflict: {function_id} has multiple owners or signatures"
                )
            else:
                errors.append(f"canonical_function_duplicate: {function_id} occurs more than once")
            continue
        if name in by_name:
            errors.append(f"canonical_function_name_ambiguous: {name} resolves to multiple stable IDs")
            continue
        canonical = deepcopy(item)
        canonical.update(
            {
                "id": function_id,
                "function_id": function_id,
                "name": name,
                "owner_file": owner,
                "file": owner,
                "visibility": visibility.lower(),
                "signature": (
                    {**deepcopy(raw_signature), "RAW": signature}
                    if isinstance(raw_signature, dict)
                    else signature
                ),
            }
        )
        by_id[function_id] = canonical
        for alias in aliases:
            if alias in by_name and by_name[alias] is not canonical:
                errors.append(f"canonical_function_name_ambiguous: {alias} resolves to multiple stable IDs")
            by_name[alias] = canonical
    if errors:
        raise ValueError("Stage artifact validation failed: " + "; ".join(errors))
    return by_id, by_name


def _resolve_function(
    reference: Any,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    *,
    stage_id: str,
    registry: CanonicalPlanningRegistry | None = None,
) -> dict[str, Any]:
    key = str(reference or "")
    target = by_id.get(key) or by_name.get(key)
    if target is None:
        if registry is not None:
            registry.resolve(key, expected_kinds={"function"})
        raise ValueError(f"overlay_unknown_stable_id: {stage_id} references unknown function {key!r}")
    return target


def _canonicalize_contracts(
    contracts: Any,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    *,
    stage_id: str,
    registry: CanonicalPlanningRegistry | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for contract in contracts if isinstance(contracts, list) else []:
        if not isinstance(contract, dict):
            raise ValueError(f"overlay_invalid_call_contract: {stage_id} call contract must be an object")
        callee = _resolve_function(
            contract.get("callee_function_id")
            or contract.get("callee")
            or contract.get("function_id")
            or contract.get("NAME"),
            by_id,
            by_name,
            stage_id=stage_id,
            registry=registry,
        )
        raw_signature = callee.get("signature", {})
        signature = str(raw_signature.get("RAW", "")) if isinstance(raw_signature, dict) else str(raw_signature or "")
        if not re.match(r".+?\s*[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", signature):
            raise ValueError(
                f"canonical_signature_missing: {stage_id} cannot resolve a complete signature for {_stable_artifact_key(callee, 'function')}"
            )
        item = deepcopy(contract)
        item["NAME"] = str(raw_signature.get("NAME", "")) if isinstance(raw_signature, dict) else ""
        if not item["NAME"]:
            match = re.match(r".+?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", signature)
            item["NAME"] = match.group(1) if match else ""
        item["SIGNATURE"] = signature
        out.append(item)
    return out


def _merge_function_artifacts(
    previous_artifacts: list[dict[str, Any]], registry: CanonicalPlanningRegistry | None = None
) -> list[dict[str, Any]]:
    interfaces = _stage_artifact(previous_artifacts, "function_interface_design")
    behaviors = _stage_artifact(previous_artifacts, "function_behavior_design")
    calls = _stage_artifact(previous_artifacts, "function_call_contract_closure")
    tests = _stage_artifact(previous_artifacts, "function_test_vector_design")
    dependencies = _stage_artifact(previous_artifacts, "dependency_closure")
    by_id, by_name = _canonical_function_indexes(interfaces, registry)

    def target(reference: Any, stage_id: str) -> dict[str, Any]:
        return _resolve_function(reference, by_id, by_name, stage_id=stage_id, registry=registry)

    for item in behaviors.get("function_behaviors", []):
        if not isinstance(item, dict):
            continue
        current = target(item.get("function_id"), "function_behavior_design")
        forbidden = {"owner_file", "file", "name", "signature", "visibility"}.intersection(item)
        if forbidden:
            raise ValueError(f"overlay_modifies_canonical_identity: function_behavior_design changes {sorted(forbidden)}")
        for key in ("LOGIC", "EVENT", "wire_mapping", "WIRE_MAPPING"):
            if key in item:
                current[key] = deepcopy(item[key])
        current["trace_refs"] = list(dict.fromkeys([*current.get("trace_refs", []), *item.get("trace_refs", [])]))
    for item in behaviors.get("wire_mappings", []):
        if isinstance(item, dict):
            target(item.get("function_id"), "function_behavior_design")["WIRE_MAPPING"] = deepcopy(
                item.get("wire_mapping", item.get("WIRE_MAPPING"))
            )

    call_edges, rely_by_function = _typed_call_stage_artifact(calls, registry)
    if isinstance(rely_by_function, dict):
        for function_id, rely in rely_by_function.items():
            target(function_id, "function_call_contract_closure")["RELY"] = deepcopy(rely)
    contracts_by_caller: dict[str, list[dict[str, Any]]] = {}
    for edge in call_edges:
        caller = target(edge.get("caller_function_id"), "function_call_contract_closure")
        callee = target(edge.get("callee_function_id"), "function_call_contract_closure")
        caller_id = _stable_artifact_key(caller, "function")
        if isinstance(caller.get("rely"), dict):
            rely = caller["rely"]
        else:
            rely = caller.setdefault("RELY", {"STRUCT": [], "FUNC": [], "VAR": []})
        func_key = "FUNC" if "FUNC" in rely or "RELY.FUNC" not in rely else "RELY.FUNC"
        rely.setdefault(func_key, [])
        existing_names = {
            str(item.get("NAME")) if isinstance(item, dict) else str(item)
            for item in rely[func_key]
        }
        if callee["name"] not in existing_names:
            rely[func_key].append({"NAME": callee["name"], "KIND": "CALL", "ROLE": callee.get("role", "")})
        contract = edge.get("_legacy_contract")
        if not isinstance(contract, dict):
            contract = {key: deepcopy(value) for key, value in edge.items() if not key.startswith("_")}
        contracts_by_caller.setdefault(caller_id, []).append(contract)
    for caller_id, contracts in contracts_by_caller.items():
        by_id[caller_id]["CALL_CONTRACTS"] = _canonicalize_contracts(
            contracts, by_id, by_name, stage_id="function_call_contract_closure", registry=registry
        )

    function_vectors = tests.get("function_test_vectors", {})
    if isinstance(function_vectors, dict):
        for function_id, vectors in function_vectors.items():
            target(function_id, "function_test_vector_design")["TEST_VECTORS"] = deepcopy(vectors)

    for item in dependencies.get("functions", []):
        if not isinstance(item, dict):
            continue
        current = target(item.get("function_id") or item.get("id"), "dependency_closure")
        forbidden = {"owner_file", "file", "name", "signature", "visibility"}.intersection(item)
        if forbidden:
            raise ValueError(f"overlay_modifies_canonical_identity: dependency_closure changes {sorted(forbidden)}")
        extra = set(item) - {"function_id", "id", "interfaces"}
        if extra:
            raise ValueError(f"incomplete_overlay_as_inventory: dependency_closure function has fields {sorted(extra)}")
        overlay = item.get("interfaces", {})
        if not isinstance(overlay, dict):
            raise ValueError("overlay_invalid_shape: dependency_closure interfaces must be an object")
        for key, value in overlay.items():
            if key not in {"RELY", "CALL_CONTRACTS", "WIRE_MAPPING", "TEST_VECTORS", "LOGIC", "EVENT"}:
                raise ValueError(f"overlay_forbidden_field: dependency_closure function interface {key}")
            current[key] = (
                _canonicalize_contracts(
                    value, by_id, by_name, stage_id="dependency_closure", registry=registry
                )
                if key == "CALL_CONTRACTS"
                else deepcopy(value)
            )
    return list(by_id.values())


def _typed_call_stage_artifact(
    artifact: dict[str, Any], registry: CanonicalPlanningRegistry | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "call_edges" not in artifact:
        edges: list[dict[str, Any]] = []
        for contract in artifact.get("call_contracts", []):
            if not isinstance(contract, dict):
                continue
            caller_reference = contract.get("caller")
            callee_reference = contract.get("callee") or contract.get("function_id") or contract.get("NAME")
            if registry is not None:
                caller_reference = registry.resolve(caller_reference, expected_kinds={"function"})["artifact_id"]
                callee_reference = registry.resolve(callee_reference, expected_kinds={"function"})["artifact_id"]
            edges.append(
                {
                    "caller_function_id": caller_reference,
                    "callee_function_id": callee_reference,
                    "call_purpose": "legacy Stage 8 migration",
                    "condition": "",
                    "argument_semantics": "",
                    "result_usage": "",
                    "_legacy_contract": deepcopy(contract),
                }
            )
        return edges, artifact.get("rely_by_function", {})

    allowed_top = {"call_edges", "call_edges_by_group", "artifact_requests", "call_diagnostics"}
    extra_top = set(artifact) - allowed_top
    if extra_top:
        raise ValueError(f"typed_delta_forbidden_field: function_call_contract_closure {sorted(extra_top)}")
    required = {
        "caller_function_id",
        "callee_function_id",
        "call_purpose",
        "condition",
        "argument_semantics",
        "result_usage",
    }
    edges = []
    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict) or set(edge) != required:
            raise ValueError(f"typed_delta_invalid_shape: Stage 8 call edge requires only {sorted(required)}")
        value = deepcopy(edge)
        if registry is not None:
            value["caller_function_id"] = registry.resolve(
                value["caller_function_id"], expected_kinds={"function"}
            )["artifact_id"]
            value["callee_function_id"] = registry.resolve(
                value["callee_function_id"], expected_kinds={"function"}
            )["artifact_id"]
        edges.append(value)
    for group in artifact.get("call_edges_by_group", []):
        if not isinstance(group, dict):
            continue
        callers = set(group.get("partition", {}).get("caller_function_ids", []))
        for edge in group.get("artifact", {}).get("call_edges", []):
            if isinstance(edge, dict) and edge.get("caller_function_id") not in callers:
                raise ValueError(
                    f"typed_delta_partition_escape: caller {edge.get('caller_function_id')!r} is outside partition"
                )
    return edges, {}


def _merge_canonical_collection(canonical: Any, overlays: Any, kind: str, allowed: set[str]) -> list[dict[str, Any]]:
    items = [deepcopy(item) for item in canonical if isinstance(item, dict)] if isinstance(canonical, list) else []
    alias_fields = {"module": ("id", "name"), "file": ("id",), "type": ("id", "type_name", "symbol", "name")}[kind]
    by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        aliases = [str(item.get(field)) for field in alias_fields if item.get(field)]
        if not aliases:
            raise ValueError(f"canonical_{kind}_identity_invalid: missing stable ID")
        for alias in aliases:
            if alias in by_key and by_key[alias] is not item:
                raise ValueError(f"canonical_{kind}_identity_invalid: duplicate stable ID {alias}")
            by_key[alias] = item
    for overlay in overlays if isinstance(overlays, list) else []:
        if not isinstance(overlay, dict):
            continue
        keys = [str(overlay.get(field)) for field in alias_fields if overlay.get(field)]
        key = keys[0] if keys else ""
        if not keys or any(value not in by_key for value in keys):
            raise ValueError(f"overlay_unknown_stable_id: dependency_closure references unknown {kind} {key!r}")
        targets = {id(by_key[value]): by_key[value] for value in keys if value in by_key}
        target = next(iter(targets.values())) if len(targets) == 1 else None
        if target is None:
            raise ValueError(f"overlay_unknown_stable_id: dependency_closure references unknown {kind} {key!r}")
        extra = set(overlay) - allowed
        if extra:
            raise ValueError(f"overlay_modifies_canonical_identity: dependency_closure {kind} changes {sorted(extra)}")
        for field in allowed - {"id", "name"}:
            if field in overlay:
                target[field] = deepcopy(overlay[field])
    return items


def _canonicalize_registry_collection(
    items: list[dict[str, Any]], kind: str, registry: CanonicalPlanningRegistry
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        references = {
            "module": [item.get("id"), item.get("name")],
            "file": [item.get("id"), item.get("source_path"), item.get("header_path")],
        }[kind]
        entry = next(
            (
                registry.resolve(reference, expected_kinds={kind})
                for reference in references
                if reference
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"unknown_artifact_id: cannot bind canonical {kind}")
        value = deepcopy(item)
        value["id"] = entry["artifact_id"]
        if kind == "module":
            value["name"] = entry["canonical_name"]
        else:
            owner = registry.resolve(entry["owner_module_id"], expected_kinds={"module"})
            value["module"] = owner["canonical_name"]
        out.append(value)
    return out


def _canonicalize_registry_types(
    designs: Any, registry: CanonicalPlanningRegistry, *, allow_missing: bool = False
) -> list[dict[str, Any]]:
    designs = _type_definition_designs(designs, registry)
    overlays: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for item in designs if isinstance(designs, list) else []:
        if not isinstance(item, dict):
            continue
        reference = _stable_artifact_key(item, "type")
        entry = registry.resolve(reference, expected_kinds={"type", "callback"})
        if entry["artifact_id"] in overlays:
            raise ValueError(f"canonical_type_duplicate: {entry['artifact_id']}")
        if item.get("owner_file") or item.get("file"):
            owner = registry.resolve(item.get("owner_file") or item.get("file"), expected_kinds={"file"})
            if owner["artifact_id"] != entry["owner_file_id"]:
                raise ValueError(f"canonical_type_owner_conflict: {entry['artifact_id']}")
        if isinstance(item.get("public_visibility"), bool):
            overlay_visibility = "public" if item["public_visibility"] else "private"
            if overlay_visibility != entry["visibility"]:
                raise ValueError(f"canonical_type_visibility_conflict: {entry['artifact_id']}")
        overlays[entry["artifact_id"]] = item
        ordered_ids.append(entry["artifact_id"])

    out: list[dict[str, Any]] = []
    entries_by_id = {entry["artifact_id"]: entry for entry in registry.typed_view({"type", "callback"})}
    for entry_id in [*ordered_ids, *[value for value in entries_by_id if value not in overlays]]:
        entry = entries_by_id[entry_id]
        item = overlays.get(entry["artifact_id"])
        if item is None:
            if entry["artifact_kind"] != "type" or entry.get("declaration_kind") not in {"type", "opaque"}:
                if allow_missing:
                    continue
                raise ValueError(f"canonical_type_definition_missing: {entry['artifact_id']}")
            item = {
                "kind": "OPAQUE",
                "fields": [],
                "trace_refs": entry.get("provenance", {}).get("refs", []),
                "migration_reason": "Stage 4 explicitly declared an opaque type; no fields are required.",
            }
        value = deepcopy(item)
        value.update(
            {
                "id": entry["artifact_id"],
                "name": entry["canonical_name"],
                "type_name": entry["canonical_name"],
                "file": entry["owner_file_id"],
                "owner_file": entry["owner_file_id"],
                "visibility": entry["visibility"],
            }
        )
        out.append(value)
    return out


def _type_definition_designs(value: Any, registry: CanonicalPlanningRegistry) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "type_definition_overlays" in value:
        allowed_top = {"type_definition_overlays", "artifact_requests", "type_dependency_notes", "type_diagnostics"}
        extra_top = set(value) - allowed_top
        if extra_top:
            raise ValueError(f"typed_delta_forbidden_field: type_and_access_path_design {sorted(extra_top)}")
        designs: list[dict[str, Any]] = []
        seen: set[str] = set()
        forbidden = {"id", "type_id", "type_name", "symbol", "name", "owner_file", "file", "visibility", "artifact_kind", "kind"}
        for item in value.get("type_definition_overlays", []):
            if not isinstance(item, dict) or set(item) != {"type_id", "definition_overlay"}:
                raise ValueError("typed_delta_invalid_shape: Stage 5 overlay requires only type_id and definition_overlay")
            entry = registry.resolve(item["type_id"], expected_kinds={"type", "callback"})
            overlay = item["definition_overlay"]
            if not isinstance(overlay, dict):
                raise ValueError("typed_delta_invalid_shape: Stage 5 definition_overlay must be an object")
            changed = forbidden.intersection(overlay)
            if changed:
                raise ValueError(f"typed_delta_modifies_canonical_identity: Stage 5 changes {sorted(changed)}")
            if entry["artifact_id"] in seen:
                raise ValueError(f"typed_delta_duplicate_id: {entry['artifact_id']}")
            seen.add(entry["artifact_id"])
            designs.append({"id": entry["artifact_id"], **deepcopy(overlay)})
        return designs
    legacy = value.get("types", []) if isinstance(value, dict) else value
    return [item for item in legacy if isinstance(item, dict)] if isinstance(legacy, list) else []


def _validate_completed_stage(
    stage_id: str,
    artifacts: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    registry: CanonicalPlanningRegistry | None = None,
    allow_incomplete: bool = False,
) -> None:
    if stage_id == "module_file_plan":
        layout = _stage_artifact(artifacts, stage_id)
        _merge_canonical_collection(layout.get("modules", []), [], "module", {"id", "name"})
        _merge_canonical_collection(layout.get("files", []), [], "file", {"id"})
    elif stage_id == "public_artifact_inventory":
        inventory = _stage_artifact(artifacts, stage_id)
        for collection in ("types", "functions"):
            for item in inventory.get(collection, []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("visibility", "")).lower() not in {"public", "private"}:
                    name = str(item.get("symbol") or item.get("name") or item.get("id") or "")
                    raise ValueError(
                        f"canonical_visibility_invalid: {collection} entry {name!r} requires public or private"
                    )
    elif stage_id == "type_and_access_path_design":
        if registry is not None:
            _canonicalize_registry_types(
                _stage_artifact(artifacts, stage_id), registry, allow_missing=allow_incomplete
            )
            return
        inventory = _stage_artifact(artifacts, "public_artifact_inventory")
        known = {
            _stable_artifact_key(item, "type")
            for item in inventory.get("types", [])
            if isinstance(item, dict)
        }
        for item in _stage_artifact(artifacts, stage_id).get("types", []):
            if isinstance(item, dict) and known and _stable_artifact_key(item, "type") not in known:
                raise ValueError(
                    f"overlay_unknown_stable_id: type_and_access_path_design references unknown type {_stable_artifact_key(item, 'type')!r}"
                )
    elif stage_id in {
        "function_interface_design",
        "function_behavior_design",
        "function_call_contract_closure",
        "function_test_vector_design",
    }:
        if stage_id == "function_interface_design":
            if registry is not None:
                expected = {item["artifact_id"] for item in registry.typed_view({"function"})}
                actual = {
                    registry.resolve(_stable_artifact_key(item, "function"), expected_kinds={"function"})["artifact_id"]
                    for item in _stage_artifact(artifacts, stage_id).get("function_interfaces", [])
                    if isinstance(item, dict)
                }
            else:
                inventory = _stage_artifact(artifacts, "public_artifact_inventory")
                expected = {
                    str(item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name"))
                    for item in inventory.get("functions", [])
                    if isinstance(item, dict)
                }
                actual = {
                    _stable_artifact_key(item, "function")
                    for item in _stage_artifact(artifacts, stage_id).get("function_interfaces", [])
                    if isinstance(item, dict)
                }
            if expected != actual:
                raise ValueError(
                    f"canonical_function_inventory_mismatch: missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
                )
        _merge_function_artifacts(artifacts, registry)
    elif stage_id == "dependency_closure":
        _dependency_stage_overlays(_stage_artifact(artifacts, stage_id), allow_legacy=False)
        _assemble_final_plan_candidate(
            context, artifacts, registry=registry, allow_incomplete=allow_incomplete
        )


def _assemble_final_plan_candidate(
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    *,
    registry: CanonicalPlanningRegistry | None = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    scope = _stage_artifact(previous_artifacts, "scope_fact_inventory")
    architecture = _stage_artifact(previous_artifacts, "architecture_boundaries")
    layout = _stage_artifact(previous_artifacts, "module_file_plan")
    public_inventory = _stage_artifact(previous_artifacts, "public_artifact_inventory")
    type_design = _stage_artifact(previous_artifacts, "type_and_access_path_design")
    interfaces = _stage_artifact(previous_artifacts, "function_interface_design")
    dependencies = _stage_artifact(previous_artifacts, "dependency_closure")
    dependency_overlays = _dependency_stage_overlays(dependencies, allow_legacy=True)
    tests = _stage_artifact(previous_artifacts, "function_test_vector_design")
    characteristics = context.get("characteristics", {})

    modules = _merge_canonical_collection(
        layout.get("modules", []), dependency_overlays.get("modules", []), "module", {"id", "name", "dependencies"}
    )
    if registry is not None:
        modules = _canonicalize_registry_collection(modules, "module", registry)
    for item in modules:
        name = str(item.get("name", ""))
        item["id"] = str(item.get("id") or f"module:{name}")
        item["role"] = str(item.get("role") or "; ".join(str(value) for value in item.get("responsibilities", [])) or f"{name} implementation module.")
        item["dependencies"] = [str(value) for value in item.get("dependencies", [])]
        item["trace_refs"] = [str(value) for value in item.get("trace_refs", [])]

    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(architecture.get("ownership_decisions", []), 1):
        if not isinstance(item, dict):
            continue
        decisions.append(
            {
                "decision_id": f"DEC_CLOSURE_{index:03d}",
                "content": str(item.get("decision", "")),
                "supporting_fact_refs": [str(value) for value in item.get("fact_refs", [])],
                "activated_rule_refs": [str(value) for value in item.get("rule_refs", [])],
                "rationale": str(item.get("justification", "")),
                "affected_artifacts": [],
            }
        )

    consistency_rules: list[dict[str, Any]] = []
    for rule in context.get("engineering_rules", []):
        if not isinstance(rule, dict):
            continue
        for constraint in rule.get("constraints", []):
            consistency_rules.append(
                {
                    "ID": f"PC{len(consistency_rules) + 1}",
                    "RULE": str(constraint),
                    "DOC_REF": [str(value) for value in rule.get("fact_refs", [])],
                }
            )

    runtime_vectors: list[dict[str, Any]] = []
    for index, vector in enumerate(tests.get("runtime_test_vectors", []), 1):
        if not isinstance(vector, dict):
            continue
        runtime_vectors.append(
            {
                "name": str(vector.get("scenario") or vector.get("name") or f"runtime_{index}"),
                "input": {"steps": vector.get("steps", []), "components": vector.get("components", [])},
                "expect": {"outcomes": vector.get("expected_outcomes", [])},
                "trace_refs": [str(value) for value in vector.get("trace_refs", [])],
            }
        )

    protocol = {
        "name": str(characteristics.get("protocol_name", "Protocol")),
        "slug": str(characteristics.get("protocol_slug", "protocol")),
        "spec_version": str(characteristics.get("spec_version", "unspecified")),
        "roles": [str(value) for value in characteristics.get("target_roles", [])],
        "scope": "; ".join(str(value) for value in scope.get("confirmed_scope", [])),
        "trace_refs": ["fact:protocol_meta", "fact:minimum_v1"],
    }
    default_port = _find_named_integer(context.get("facts", {}), "default_port")
    if default_port is not None:
        protocol["default_port"] = default_port

    files = _merge_canonical_collection(
        layout.get("files", []), dependency_overlays.get("files", []), "file", {"id", "header_dependencies", "source_dependencies"}
    )
    if registry is not None:
        files = _canonicalize_registry_collection(files, "file", registry)
        types = _canonicalize_registry_types(type_design, registry, allow_missing=allow_incomplete)
    else:
        types = [dict(item) for item in type_design.get("types", []) if isinstance(item, dict)]
    functions = _merge_function_artifacts(previous_artifacts, registry)
    mapping = [
        *[
            {"plan_id": item["id"], "spec_kind": "PROTOCOL_MODULE_SPEC.MODULES", "spec_key": item["name"], "lowering_rule": "deterministic stage reconciliation"}
            for item in modules
        ],
        *[
            {"plan_id": str(item.get("id", "")), "spec_kind": "FILE_SPEC", "spec_key": str(item.get("trace_id", item.get("id", ""))), "lowering_rule": "deterministic stage reconciliation"}
            for item in files
        ],
    ]
    return {
        "schema_version": "specforge_planning_ir_v1",
        "protocol": protocol,
        "modules": modules,
        "files": files,
        "types": types,
        "functions": functions,
        "engineering_decisions": decisions,
        "open_assumptions": list(context.get("open_assumptions", [])),
        "architecture": {
            "module_candidates": architecture.get("module_candidates", []),
            "ownership_decisions": architecture.get("ownership_decisions", []),
            "cross_module_services": architecture.get("cross_module_services", []),
            "coverage_matrix": architecture.get("coverage_matrix", []),
            "ordering_choices": dependencies.get("ordering_choices", []),
            "dependency_architecture_choices": dependencies.get("architecture_choices", []),
        },
        "consistency_rules": consistency_rules,
        "forbidden_symbols": list(public_inventory.get("forbidden_symbols", [])),
        "test_vectors": runtime_vectors,
        "plan_to_spec_mapping": mapping,
        "canonical_registry_snapshot": registry.snapshot() if registry is not None else None,
        "dependency_derivation": {
            "mode": "deterministic_from_registry_relations",
            "source_relations": ["ownership", "foreign_type_references", "typed_call_edges", "callback_references", "public_interface_exposure"],
        },
    }


def _dependency_stage_overlays(artifact: dict[str, Any], *, allow_legacy: bool) -> dict[str, Any]:
    inventory_fields = {"modules", "files", "functions", "types", "call_graph", "generation_order"}.intersection(artifact)
    if inventory_fields:
        if not allow_legacy:
            raise ValueError(f"typed_delta_forbidden_inventory: Stage 10 repeats {sorted(inventory_fields)}")
        return artifact
    allowed = {"ordering_choices", "architecture_choices", "artifact_requests", "dependency_diagnostics"}
    extra = set(artifact) - allowed
    if extra:
        raise ValueError(f"typed_delta_forbidden_field: dependency_closure {sorted(extra)}")
    for collection in ("ordering_choices", "architecture_choices"):
        for choice in artifact.get(collection, []):
            if not isinstance(choice, dict) or not {"reason", "provenance", "affected_artifact_ids"} <= set(choice):
                raise ValueError(
                    f"typed_delta_invalid_shape: {collection} requires reason, provenance, and affected_artifact_ids"
                )
    return {"modules": [], "files": [], "functions": []}


def _materialize_typed_stage_artifacts(
    artifacts: list[dict[str, Any]], registry: CanonicalPlanningRegistry
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    identity_fields = {"id", "type_id", "type_name", "symbol", "name", "owner_file", "file", "visibility", "public_visibility", "artifact_kind", "kind"}
    for item in artifacts:
        stage_id = str(item.get("stage_id", ""))
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        if stage_id == "type_and_access_path_design" and "type_definition_overlays" not in artifact:
            overlays = []
            for design in artifact.get("types", []):
                if not isinstance(design, dict):
                    continue
                entry = registry.resolve(_stable_artifact_key(design, "type"), expected_kinds={"type", "callback"})
                overlays.append(
                    {
                        "type_id": entry["artifact_id"],
                        "definition_overlay": {key: deepcopy(value) for key, value in design.items() if key not in identity_fields},
                    }
                )
            artifact = {
                "type_definition_overlays": overlays,
                "artifact_requests": [],
                "type_dependency_notes": deepcopy(artifact.get("type_dependency_notes", [])),
                "type_diagnostics": deepcopy(artifact.get("type_diagnostics", [])),
            }
        elif stage_id == "function_call_contract_closure" and "call_edges" not in artifact:
            edges, _ = _typed_call_stage_artifact(artifact, registry)
            artifact = {
                "call_edges": [{key: deepcopy(value) for key, value in edge.items() if not key.startswith("_")} for edge in edges],
                "artifact_requests": [],
                "call_diagnostics": deepcopy(artifact.get("call_diagnostics", [])),
            }
        elif stage_id == "dependency_closure" and {"modules", "files", "functions"}.intersection(artifact):
            artifact = {
                "ordering_choices": [],
                "architecture_choices": [],
                "artifact_requests": [],
                "dependency_diagnostics": deepcopy(artifact.get("dependency_diagnostics", [])),
            }
        out.append({**{key: deepcopy(value) for key, value in item.items() if key != "artifact"}, "artifact": deepcopy(artifact)})
    return out


def _safe_partition_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("_") or "partition"


def _function_behavior_partitions(previous_artifacts: list[dict[str, Any]], max_functions: int = 10) -> list[dict[str, Any]]:
    public_inventory = _stage_artifact(previous_artifacts, "public_artifact_inventory")
    file_plan = _stage_artifact(previous_artifacts, "module_file_plan")
    functions = public_inventory.get("functions", [])
    files = file_plan.get("files", [])
    if not isinstance(functions, list) or not functions:
        return [{"partition_id": "all_functions", "module": "", "owner_file": "", "functions": []}]

    file_to_module: dict[str, str] = {}
    for item in files if isinstance(files, list) else []:
        if not isinstance(item, dict):
            continue
        module = str(item.get("module", ""))
        for key in ("id", "header_path", "source_path"):
            value = item.get(key)
            if isinstance(value, str) and value:
                file_to_module[value] = module
                file_to_module[value.rsplit("/", 1)[-1]] = module

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for function in functions:
        if not isinstance(function, dict):
            continue
        owner = str(function.get("owner_file") or function.get("file") or function.get("owner") or "")
        module = str(function.get("module") or file_to_module.get(owner) or file_to_module.get(owner.rsplit("/", 1)[-1], "unknown_module"))
        grouped.setdefault((module, owner), []).append(function)

    partitions: list[dict[str, Any]] = []
    by_module: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}
    for (module, owner), items in grouped.items():
        by_module.setdefault(module, []).append((owner, items))

    for module, owner_groups in sorted(by_module.items()):
        module_functions = [function for _, items in owner_groups for function in items]
        if len(module_functions) <= max_functions:
            partitions.append(
                {
                    "partition_id": _safe_partition_id(module),
                    "module": module,
                    "owner_file": "",
                    "functions": module_functions,
                }
            )
            continue
        for owner, items in sorted(owner_groups):
            partitions.append(
                {
                    "partition_id": _safe_partition_id(f"{module}_{owner or 'file'}"),
                    "module": module,
                    "owner_file": owner,
                    "functions": items,
                }
            )
    return partitions or [{"partition_id": "all_functions", "module": "", "owner_file": "", "functions": functions}]


def _merge_function_behavior_artifacts(partition_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "function_behaviors_by_group": partition_artifacts,
        "function_behaviors": [],
        "wire_mappings": [],
        "behavior_diagnostics": [],
    }
    for item in partition_artifacts:
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        for key in ("function_behaviors", "wire_mappings", "behavior_diagnostics"):
            value = artifact.get(key, [])
            if isinstance(value, list):
                merged[key].extend(value)
    return merged


def _function_call_partitions(
    registry: CanonicalPlanningRegistry, max_callers: int = 8
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for entry in registry.typed_view({"function"}):
        grouped.setdefault((str(entry["owner_module_id"]), str(entry["owner_file_id"])), []).append(
            entry["artifact_id"]
        )
    partitions: list[dict[str, Any]] = []
    for (module_id, file_id), function_ids in sorted(grouped.items()):
        for index in range(0, len(function_ids), max_callers):
            chunk = function_ids[index : index + max_callers]
            suffix = f"_{index // max_callers + 1}" if len(function_ids) > max_callers else ""
            partitions.append(
                {
                    "partition_id": _safe_partition_id(f"{file_id}{suffix}"),
                    "owner_module_id": module_id,
                    "owner_file_id": file_id,
                    "caller_function_ids": chunk,
                }
            )
    return partitions or [{"partition_id": "all_callers", "owner_module_id": "", "owner_file_id": "", "caller_function_ids": []}]


def _type_definition_partitions(registry: CanonicalPlanningRegistry) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for entry in registry.typed_view({"type", "callback"}):
        if entry["status"] in {"declared", "requested"}:
            grouped.setdefault(str(entry["owner_file_id"]), []).append(entry["artifact_id"])
    return [
        {
            "partition_id": _safe_partition_id(owner_file_id),
            "owner_file_id": owner_file_id,
            "type_ids": type_ids,
        }
        for owner_file_id, type_ids in sorted(grouped.items())
    ]


def _merge_type_definition_artifacts(partition_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "type_definition_overlays": [],
        "artifact_requests": [],
        "type_dependency_notes": [],
        "type_diagnostics": [],
    }
    for item in partition_artifacts:
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        for key in ("type_definition_overlays", "artifact_requests", "type_dependency_notes", "type_diagnostics"):
            value = artifact.get(key, [])
            if isinstance(value, list):
                merged[key].extend(value)
    return merged


def _merge_call_edge_artifacts(partition_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "call_edges": [],
        "artifact_requests": [],
        "call_diagnostics": [],
    }
    for item in partition_artifacts:
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        for key in ("call_edges", "artifact_requests", "call_diagnostics"):
            value = artifact.get(key, [])
            if isinstance(value, list):
                merged[key].extend(value)
    return merged


def _validate_partition_artifact(
    stage: PlanningStage,
    artifact: dict[str, Any],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry,
) -> None:
    try:
        _validate_partition_artifact_unlayered(stage, artifact, partition, registry)
    except LayeredValidationError:
        raise
    except ValueError as exc:
        raise LayeredValidationError(classify_partition_error(exc)) from exc


def _validate_partition_artifact_unlayered(
    stage: PlanningStage,
    artifact: dict[str, Any],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry,
) -> None:
    if stage.stage_id == "type_and_access_path_design":
        designs = _type_definition_designs(artifact, registry)
        actual = {registry.resolve(item["id"], expected_kinds={"type", "callback"})["artifact_id"] for item in designs}
        expected = set(partition.get("type_ids", []))
        if actual != expected:
            raise ValueError(
                f"typed_delta_partition_coverage: Stage 5 missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        return
    if stage.stage_id == "function_behavior_design":
        allowed_top = {"function_behaviors", "wire_mappings", "behavior_diagnostics", "artifact_requests"}
        extra = set(artifact) - allowed_top
        if extra:
            raise ValueError(f"typed_delta_forbidden_field: function_behavior_design {sorted(extra)}")
        allowed_ids = {
            registry.resolve(
                item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name"),
                expected_kinds={"function"},
            )["artifact_id"]
            for item in partition.get("functions", [])
            if isinstance(item, dict)
        }
        actual: set[str] = set()
        for behavior in artifact.get("function_behaviors", []):
            if not isinstance(behavior, dict):
                raise ValueError("typed_delta_invalid_shape: Stage 7 function behavior must be an object")
            forbidden = {"owner_file", "file", "name", "signature", "visibility", "artifact_kind"}.intersection(behavior)
            if forbidden:
                raise ValueError(f"typed_delta_modifies_canonical_identity: Stage 7 changes {sorted(forbidden)}")
            actual.add(
                registry.resolve(behavior.get("function_id"), expected_kinds={"function"})["artifact_id"]
            )
        if actual != allowed_ids:
            raise ValueError(
                f"typed_delta_partition_coverage: Stage 7 missing={sorted(allowed_ids - actual)}, extra={sorted(actual - allowed_ids)}"
            )
        return
    if stage.stage_id == "function_call_contract_closure":
        edges, _ = _typed_call_stage_artifact(
            {**artifact, "call_edges_by_group": [{"partition": partition, "artifact": artifact}]}, registry
        )
        required_edge_artifact_id = partition.get("required_edge_artifact_id")
        if required_edge_artifact_id and not any(
            required_edge_artifact_id in {edge["caller_function_id"], edge["callee_function_id"]}
            for edge in edges
        ):
            raise ValueError(
                f"typed_delta_amendment_edge_missing: {required_edge_artifact_id} is not referenced"
            )


def _resume_start_index(resume_from: str) -> int:
    if resume_from == "compile_specs":
        return len(PLANNING_STAGES)
    for index, stage in enumerate(PLANNING_STAGES):
        if stage.stage_id == resume_from:
            return index
    allowed = ", ".join([stage.stage_id for stage in PLANNING_STAGES] + ["compile_specs"])
    raise ValueError(f"Unknown resume point {resume_from!r}; expected one of: {allowed}")


def _stage_log_path(stage_log_dir: Path, index: int, stage: PlanningStage) -> Path:
    return stage_log_dir / f"{index + 1:02d}_{stage.stage_id}"


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _load_logged_stage_artifacts(stage_log_dir: Path, stop_index: int) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for index, stage in enumerate(PLANNING_STAGES[:stop_index]):
        path = _stage_log_path(stage_log_dir, index, stage) / "artifact.json"
        if not path.exists():
            raise FileNotFoundError(f"Cannot resume: missing completed stage artifact {path}")
        artifacts.append({"stage_id": stage.stage_id, "title": stage.title, "artifact": _load_json_object(path)})
    return artifacts


def _load_logged_stage_records(stage_log_dir: Path, stop_index: int) -> list[dict[str, Any]]:
    path = stage_log_dir / "stage_records.json"
    raw_records: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            raw_records = [item for item in data if isinstance(item, dict)]
    records: list[dict[str, Any]] = []
    for stage in PLANNING_STAGES[:stop_index]:
        record = next((item for item in raw_records if item.get("stage_id") == stage.stage_id and item.get("status") == "completed"), None)
        records.append(
            record
            or {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": "completed",
                "elapsed_seconds": 0,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "repaired_json": False,
            }
        )
    return records


def _clear_stage_logs_from(stage_log_dir: Path, start_index: int) -> None:
    for index, stage in enumerate(PLANNING_STAGES[start_index:], start_index):
        path = _stage_log_path(stage_log_dir, index, stage)
        if path.exists():
            shutil.rmtree(path)


def _assemble_plan_from_stage_artifacts(stage_artifacts: list[dict[str, Any]], stage_records: list[dict[str, Any]]) -> dict[str, Any]:
    if len(stage_artifacts) != len(PLANNING_STAGES):
        raise ValueError("Cannot assemble implementation plan until all structured planning stages are complete")
    final_artifact = stage_artifacts[-1]["artifact"]
    plan = final_artifact.get("implementation_plan", final_artifact)
    if not isinstance(plan, dict):
        raise ValueError("final_plan_assembly must return an implementation plan object")
    plan["structured_planning_stages"] = [
        *stage_artifacts[:-1],
        {
            "stage_id": PLANNING_STAGES[-1].stage_id,
            "title": PLANNING_STAGES[-1].title,
            "artifact": {"assembled_plan_keys": sorted(plan.keys())},
        },
    ]
    plan["structured_planning_usage"] = {
        "prompt_tokens": sum(record.get("usage", {}).get("prompt_tokens", 0) for record in stage_records),
        "completion_tokens": sum(record.get("usage", {}).get("completion_tokens", 0) for record in stage_records),
        "total_tokens": sum(record.get("usage", {}).get("total_tokens", 0) for record in stage_records),
        "repair_prompt_tokens": sum(record.get("repair_usage", {}).get("prompt_tokens", 0) for record in stage_records),
        "repair_completion_tokens": sum(record.get("repair_usage", {}).get("completion_tokens", 0) for record in stage_records),
        "repair_total_tokens": sum(record.get("repair_usage", {}).get("total_tokens", 0) for record in stage_records),
        "semantic_correction_prompt_tokens": sum(
            record.get("semantic_correction_usage", {}).get("prompt_tokens", 0) for record in stage_records
        ),
        "semantic_correction_completion_tokens": sum(
            record.get("semantic_correction_usage", {}).get("completion_tokens", 0) for record in stage_records
        ),
        "semantic_correction_total_tokens": sum(
            record.get("semantic_correction_usage", {}).get("total_tokens", 0) for record in stage_records
        ),
        "inventory_amendment_prompt_tokens": sum(
            record.get("inventory_amendment_usage", {}).get("prompt_tokens", 0) for record in stage_records
        ),
        "inventory_amendment_completion_tokens": sum(
            record.get("inventory_amendment_usage", {}).get("completion_tokens", 0) for record in stage_records
        ),
        "inventory_amendment_total_tokens": sum(
            record.get("inventory_amendment_usage", {}).get("total_tokens", 0) for record in stage_records
        ),
        "request_count": sum(int(record.get("request_count", 0)) for record in stage_records),
        "prompt_characters": sum(int(record.get("prompt_characters", 0)) for record in stage_records),
        "stages": stage_records,
    }
    return plan




def _parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("LLM planning response must be a JSON object")
    return data


def build_planning_context(
    facts: dict[str, Any],
    characteristics: NormalizedCharacteristics,
    rules: list[EngineeringRule],
    assumptions: list[OpenAssumption],
) -> dict[str, Any]:
    return {
        "facts": facts,
        "characteristics": to_jsonable(characteristics),
        "engineering_rules": [to_jsonable(rule) for rule in rules],
        "open_assumptions": [to_jsonable(assumption) for assumption in assumptions],
    }


def planning_stage_catalog() -> list[dict[str, Any]]:
    return [
        {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "purpose": stage.purpose,
            "engineering_focus": stage.engineering_focus,
            "required_output": stage.required_output,
        }
        for stage in PLANNING_STAGES
    ]


def planning_resume_points() -> list[str]:
    return [stage.stage_id for stage in PLANNING_STAGES] + ["compile_specs"]
