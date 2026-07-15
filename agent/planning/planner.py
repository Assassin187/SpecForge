from __future__ import annotations

import json
import re
import shutil
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent.common.c_types import c_type_references, is_system_type

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
from .registry import (
    CanonicalPlanningRegistry,
    RegistryBindingError,
    RegistryInvariantError,
    advance_registry,
    build_registry,
)
from .validation_layers import (
    LayeredValidationError,
    ValidationIssue,
    ValidationLedger,
    classify_partition_error,
)


WHOLE_FRESH_TOKEN_CEILING = 783_804
SINGLE_REQUEST_INPUT_TOKEN_CEILING = 64_000
SINGLE_RESPONSE_TOKEN_CEILING = 16_000


class StructuredPlanner(Protocol):
    def build_plan(self, context: dict[str, Any], *, resume_from: str | None = None) -> dict[str, Any]:
        ...


class RecoverablePlanningError(RuntimeError):
    def __init__(self, stage_id: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.stage_id = stage_id
        self.diagnostic = diagnostic


class TokenBudgetExceeded(ValueError):
    def __init__(self, code: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.code = code


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
            "runtime configuration facts and explicit gaps such as listen address or port",
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
            "explicit runtime configuration inputs when facts do not ground a compile-time constant",
            "forbidden symbols and naming conflicts",
            "closed type/function inventory: later stages may not add identities omitted here",
        ],
        required_output={
            "types": "array of {symbol,owner_file,visibility,kind:type|callback,fact_refs,rule_refs,decision_refs}; callback identities must use kind=callback",
            "constants_or_macros": "array of planned constants/macros with owner file",
            "functions": "array of planned functions with owner file, visibility, and high-level role",
            "implementation_coverage_matrix": "array of {obligation_id,artifact_ids,fact_refs,rule_refs,decision_refs}; never an object map",
            "test_obligations": "array of {obligation_id,fact_refs,rule_refs,decision_refs} covering every requires_test obligation; never bare strings",
            "lifecycle_matrix": "array of {resource_id,type_id,create_function,use_functions,destroy_function,fact_refs,rule_refs,decision_refs}",
            "runtime_entrypoint": "{main_function,owner_file,startup_services,run_services,cleanup_services,exit_behavior,fact_refs,rule_refs,decision_refs}",
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
            "type_definition_overlays": "array of {type_id,definition_overlay}; OPAQUE requires ownership_model plus opaque_boundaries:{create,destroy}; ENUM requires grounded role/value per member",
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
            "explicit wire_obligation provenance for Stage 4 decoding/parsing codec artifacts",
            "HEADER.INTERFACE and SOURCE.INTERFACE information",
        ],
        required_output={
            "function_interfaces": "array of {function_id, owner_file, trace_id, function_type, visibility, role, signature, wire_obligation?, trace_refs}",
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
            "wire_mappings": "array of {function_id,wire_mapping:[{packet,wire_field,strategy,target?,source?,rule?}]}; flat items only, strategy is store_in_field|parse_and_skip|reject_if_present",
            "behavior_diagnostics": "array of unresolved behavior findings for the current partition",
        },
        partition_strategy="module_then_file",
    ),
    PlanningStage(
        stage_id="function_call_contract_closure",
        title="Function Call Contract Closure",
        purpose=(
            "Emit typed direct-call intents between existing canonical functions. Callback binding is a separate typed relation; "
            "lifecycle pairing remains in lifecycle_matrix and state prerequisites remain in Stage 7 behavior. Names, signatures, "
            "owners, visibility, RELY.FUNC, dependencies, and coder-facing CALL_CONTRACTS are derived deterministically."
        ),
        engineering_focus=[
            "typed caller/callee function IDs",
            "call purpose, condition, argument semantics, and result usage",
            "caller/callee visibility constraints",
            "every cross-module callee input has an explicit parameter, provider, registry lookup, or access service",
            "callback providers exactly match declared callback signatures and registration callers name every callback dependency",
            "actual create/use/destroy invocations may be direct calls, but lifecycle pairing itself is not a call edge",
            "diagnostics for unknown callees or signature drift",
        ],
        required_output={
            "call_edges": "array of typed direct calls with caller/callee IDs, purpose, reachable condition, argument bindings, result binding, and trace_refs",
            "callback_bindings": "array of typed callback-provider bindings; never encode these as direct call edges",
            "runtime_flow": "main partition only: typed success sequence plus failure cleanup coverage",
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
            "runtime_entrypoint": {}, "implementation_coverage_matrix": [], "test_obligations": [],
            "public_symbol_table": [], "forbidden_symbols": [],
        },
        "function_interface_design": {
            "function_interfaces": [], "header_interfaces": [], "source_interfaces": [],
            "interface_diagnostics": [],
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


def _preserved_nonfatal_stage_artifact(
    stage_id: str, artifact: dict[str, Any], error: ValueError
) -> dict[str, Any] | None:
    code = str(error).split(":", 1)[0]
    if stage_id == "function_interface_design" and code == "stage6_private_type_leak":
        return None
    allowed_prefixes = {
        "public_artifact_inventory": ("stage4_", "fact_gap_wire_constant_"),
        "function_interface_design": ("stage6_", "fact_gap_wire_mapping_", "unknown_artifact_id"),
    }
    return deepcopy(artifact) if code.startswith(allowed_prefixes.get(stage_id, ())) else None


def _normalize_public_inventory_dialects(
    artifact: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Normalize only Stage 4 container dialects with uniquely implied provenance."""
    normalized = deepcopy(artifact)
    obligations = {
        str(item.get("obligation_id")): item
        for item in context.get("required_implementation_obligations", [])
        if isinstance(item, dict) and item.get("obligation_id")
    }

    def grounded(obligation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        obligation = obligations.get(obligation_id, {})
        out = {"obligation_id": obligation_id, **deepcopy(value)}
        for key in ("fact_refs", "rule_refs"):
            if not out.get(key):
                out[key] = deepcopy(obligation.get(key, []))
        out.setdefault("decision_refs", [])
        return out

    matrix = normalized.get("implementation_coverage_matrix")
    if isinstance(matrix, dict):
        normalized["implementation_coverage_matrix"] = [
            grounded(
                str(obligation_id),
                value if isinstance(value, dict) else {"artifact_ids": deepcopy(value)},
            )
            for obligation_id, value in matrix.items()
        ]
    elif isinstance(matrix, list):
        normalized["implementation_coverage_matrix"] = [
            grounded(str(item.get("obligation_id")), item)
            if isinstance(item, dict) and item.get("obligation_id")
            else deepcopy(item)
            for item in matrix
        ]
    merged_coverage: dict[str, dict[str, Any]] = {}
    retained_coverage: list[Any] = []
    for item in normalized.get("implementation_coverage_matrix", []):
        if not isinstance(item, dict) or not item.get("obligation_id"):
            retained_coverage.append(item)
            continue
        obligation_id = str(item["obligation_id"])
        if obligation_id not in merged_coverage:
            merged_coverage[obligation_id] = item
            retained_coverage.append(item)
            continue
        target = merged_coverage[obligation_id]
        for key in ("artifact_ids", "fact_refs", "rule_refs", "decision_refs"):
            target[key] = list(dict.fromkeys([
                *(target.get(key, []) if isinstance(target.get(key), list) else []),
                *(item.get(key, []) if isinstance(item.get(key), list) else []),
            ]))
    if isinstance(normalized.get("implementation_coverage_matrix"), list):
        normalized["implementation_coverage_matrix"] = retained_coverage

    identities: dict[str, dict[str, Any]] = {}
    for collection in ("types", "constants_or_macros", "functions"):
        for item in normalized.get(collection, []):
            if not isinstance(item, dict):
                continue
            owner_file = str(item.get("owner_file", ""))
            visibility = str(item.get("visibility", "")).lower()
            if collection in {"types", "functions"} and visibility not in {"public", "private"}:
                if owner_file.endswith(".h") and visibility in {"", "opaque"}:
                    item["visibility"] = "public"
                elif owner_file.endswith(".c") and visibility in {"", "internal"}:
                    item["visibility"] = "private"
            for key in ("id", "symbol", "name"):
                if item.get(key):
                    identities[str(item[key])] = item

    functions_by_name = {
        str(item.get("symbol") or item.get("name") or item.get("id")): item
        for item in normalized.get("functions", []) if isinstance(item, dict)
    }
    type_names = {
        str(item.get("symbol") or item.get("name") or item.get("id"))
        for item in normalized.get("types", []) if isinstance(item, dict)
    }
    runtime = normalized.get("runtime_entrypoint")
    if isinstance(runtime, dict):
        runtime_coverage = next((
            item for item in normalized.get("implementation_coverage_matrix", [])
            if isinstance(item, dict) and item.get("obligation_id") == "foundation:runtime_services"
        ), None)
        runtime_service_names = {
            str(reference).rsplit("/", 1)[-1]
            for reference in (
                runtime_coverage.get("artifact_ids", [])
                if isinstance(runtime_coverage, dict) else []
            )
            if str(reference).rsplit("/", 1)[-1] in functions_by_name
        }
        if runtime_service_names:
            for field in ("startup_services", "run_services", "cleanup_services"):
                services = runtime.get(field)
                if not isinstance(services, list):
                    continue
                covered_services = [
                    value for value in services
                    if str(value).rsplit("/", 1)[-1] in runtime_service_names
                ]
                if covered_services:
                    runtime[field] = covered_services
        explicit_runtime_cleanup = {
            name for name, item in functions_by_name.items()
            if "runtime" in str(item.get("role", "")).lower()
            and any(
                marker in str(item.get("role", "")).lower()
                for marker in ("cleanup", "shutdown", "destroy", "stop")
            )
        }
        cleanup_services = runtime.get("cleanup_services")
        if explicit_runtime_cleanup and isinstance(cleanup_services, list):
            retained_cleanup = [
                str(value) for value in cleanup_services
                if str(value) in explicit_runtime_cleanup
            ]
            if retained_cleanup:
                runtime["cleanup_services"] = retained_cleanup
    for coverage in normalized.get("implementation_coverage_matrix", []):
        if not isinstance(coverage, dict):
            continue
        for reference in coverage.get("artifact_ids", []):
            type_name = str(reference)
            if type_name in type_names or not type_name.endswith("_t"):
                continue
            stem = type_name[:-2]
            create = functions_by_name.get(f"{stem}_create")
            destroy = functions_by_name.get(f"{stem}_destroy")
            if not create or not destroy or create.get("owner_file") != destroy.get("owner_file"):
                continue
            inferred = {
                "symbol": type_name, "owner_file": create.get("owner_file"),
                "visibility": "public", "kind": "type",
                "fact_refs": deepcopy(coverage.get("fact_refs", [])),
                "rule_refs": deepcopy(coverage.get("rule_refs", [])),
                "decision_refs": deepcopy(coverage.get("decision_refs", [])),
            }
            normalized.setdefault("types", []).append(inferred)
            identities[type_name] = inferred
            type_names.add(type_name)

    session_coverage = next((
        item for item in normalized.get("implementation_coverage_matrix", [])
        if isinstance(item, dict) and item.get("obligation_id") == "foundation:session_access"
    ), None)
    if isinstance(session_coverage, dict):
        covered_types = {
            str(reference) for reference in session_coverage.get("artifact_ids", [])
            if str(reference) in type_names
        }
        for name, create in functions_by_name.items():
            if len(covered_types) >= 2 or not name.endswith("_create"):
                continue
            stem = name.removesuffix("_create")
            destroy = functions_by_name.get(f"{stem}_destroy")
            type_name = f"{stem}_t"
            if (
                not destroy or create.get("owner_file") != destroy.get("owner_file")
                or type_name in type_names
                or not any(marker in stem.lower() for marker in ("manager", "registry", "container"))
            ):
                continue
            inferred = {
                "symbol": type_name, "owner_file": create.get("owner_file"),
                "visibility": "public", "kind": "type",
                "fact_refs": deepcopy(session_coverage.get("fact_refs", [])),
                "rule_refs": deepcopy(session_coverage.get("rule_refs", [])),
                "decision_refs": deepcopy(session_coverage.get("decision_refs", [])),
            }
            normalized.setdefault("types", []).append(inferred)
            identities[type_name] = inferred
            type_names.add(type_name)
            covered_types.add(type_name)
            session_coverage.setdefault("artifact_ids", []).append(type_name)

    lifecycle_coverage = next(
        (
            item for item in normalized.get("implementation_coverage_matrix", [])
            if isinstance(item, dict)
            and item.get("obligation_id") == "foundation:owned_runtime_lifecycle"
        ),
        None,
    )
    lifecycle_obligation = obligations.get("foundation:owned_runtime_lifecycle", {})
    if isinstance(lifecycle_coverage, dict):
        obligation_refs = {
            str(ref)
            for key in ("fact_refs", "rule_refs")
            for ref in lifecycle_obligation.get(key, [])
        }
        implied_ids: list[str] = []
        for lifecycle in normalized.get("lifecycle_matrix", []):
            if not isinstance(lifecycle, dict):
                continue
            lifecycle_refs = {
                str(ref)
                for key in ("fact_refs", "rule_refs")
                for ref in lifecycle.get(key, [])
            }
            if obligation_refs and not obligation_refs.intersection(lifecycle_refs):
                continue
            implied_ids.extend(
                str(reference)
                for reference in (
                    lifecycle.get("type_id"),
                    lifecycle.get("create_function"),
                    *(
                        lifecycle.get("use_functions", [])
                        if isinstance(lifecycle.get("use_functions"), list)
                        else []
                    ),
                    lifecycle.get("destroy_function"),
                )
                if reference and str(reference) in identities
            )
        existing_ids = lifecycle_coverage.get("artifact_ids", [])
        lifecycle_coverage["artifact_ids"] = list(
            dict.fromkeys([*(existing_ids if isinstance(existing_ids, list) else []), *implied_ids])
        )
    for coverage in normalized.get("implementation_coverage_matrix", []):
        if not isinstance(coverage, dict):
            continue
        for reference in coverage.get("artifact_ids", []):
            target = identities.get(str(reference))
            if target is None:
                continue
            for key in ("fact_refs", "rule_refs", "decision_refs"):
                target[key] = list(
                    dict.fromkeys(
                        [
                            *(
                                target.get(key, [])
                                if isinstance(target.get(key), list)
                                else []
                            ),
                            *(
                                coverage.get(key, [])
                                if isinstance(coverage.get(key), list)
                                else []
                            ),
                        ]
                    )
                )

    tests = normalized.get("test_obligations")
    if isinstance(tests, list):
        normalized["test_obligations"] = [
            grounded(str(item), {}) if isinstance(item, str) else (
                grounded(str(item.get("obligation_id")), item)
                if isinstance(item, dict) and item.get("obligation_id")
                else deepcopy(item)
            )
            for item in tests
        ]
        existing_test_ids = {
            str(item.get("obligation_id"))
            for item in normalized["test_obligations"]
            if isinstance(item, dict) and item.get("obligation_id")
        }
        normalized["test_obligations"].extend(
            grounded(obligation_id, {})
            for obligation_id, obligation in obligations.items()
            if obligation.get("requires_test") and obligation_id not in existing_test_ids
        )
    return normalized


def _prune_invalid_stage4_lifecycle_relations(artifact: dict[str, Any]) -> dict[str, Any]:
    """Drop lifecycle relations that cannot denote a direct owned handle pair."""
    normalized = deepcopy(artifact)
    type_ids = {
        str(reference)
        for item in normalized.get("types", [])
        if isinstance(item, dict) and str(item.get("kind", "type")).lower() != "callback"
        for reference in (item.get("id"), item.get("symbol"), item.get("name"))
        if reference
    }
    function_ids = {
        str(reference)
        for item in normalized.get("functions", [])
        if isinstance(item, dict)
        for reference in (item.get("id"), item.get("symbol"), item.get("name"))
        if reference
    }
    mutation = re.compile(
        r"(?:^|_)(?:add|remove|subscribe|unsubscribe|lookup|match|mark|update|set|feed|flush)(?:_|$)"
    )
    valid: list[dict[str, Any]] = []
    for item in normalized.get("lifecycle_matrix", []):
        if not isinstance(item, dict):
            continue
        create = str(item.get("create_function") or "")
        destroy = str(item.get("destroy_function") or "")
        type_tokens = set(re.findall(r"[a-z0-9]+", str(item.get("type_id", "")).lower())) - {"mqtt", "t"}
        create_tokens = set(re.findall(r"[a-z0-9]+", create.lower()))
        destroy_tokens = set(re.findall(r"[a-z0-9]+", destroy.lower()))
        if (
            str(item.get("type_id") or "") not in type_ids
            or create not in function_ids
            or destroy not in function_ids
            or mutation.search(create)
            or mutation.search(destroy)
            or not type_tokens.intersection(create_tokens)
            or not type_tokens.intersection(destroy_tokens)
        ):
            continue
        valid.append(item)
    normalized["lifecycle_matrix"] = valid
    return normalized


class LLMStructuredPlanner:
    def __init__(self, api_key_env: str = "ALI_API", stage_log_dir: str | Path | None = None) -> None:
        self.api_key_env = api_key_env
        self.stage_log_dir = Path(stage_log_dir) if stage_log_dir is not None else None
        self.stage_records: list[dict[str, Any]] = []
        self.registry: CanonicalPlanningRegistry | None = None
        self.unresolved_partitions: list[dict[str, Any]] = []
        self.blocking_diagnostics: list[dict[str, Any]] = []
        self._model_tokens_used = 0
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
            self._model_tokens_used = sum(
                int(record.get(field, {}).get("total_tokens", 0))
                for record in self.stage_records
                for field in ("usage", "repair_usage", "semantic_correction_usage", "inventory_amendment_usage")
            )
            if start_index > 3:
                stage4_artifact = _stage_artifact(stage_artifacts, "public_artifact_inventory")
                stage_artifacts[3]["artifact"] = _prune_invalid_stage4_lifecycle_relations(
                    _normalize_public_inventory_dialects(stage4_artifact, context)
                )
            unresolved_path = self.stage_log_dir.parent / "unresolved_partitions.json"
            blocking_path = self.stage_log_dir.parent / "blocking_diagnostics.json"
            if unresolved_path.exists():
                stored = json.loads(unresolved_path.read_text(encoding="utf-8"))
                completed_ids = {stage.stage_id for stage in PLANNING_STAGES[:start_index]}
                self.unresolved_partitions = [
                    item for item in stored
                    if isinstance(item, dict)
                    and (resume_from == "compile_specs" or item.get("stage_id") in completed_ids)
                ]
            if blocking_path.exists():
                stored = json.loads(blocking_path.read_text(encoding="utf-8"))
                completed_ids = {stage.stage_id for stage in PLANNING_STAGES[:start_index]}
                self.blocking_diagnostics = [
                    item for item in stored
                    if isinstance(item, dict)
                    and (
                        resume_from == "compile_specs"
                        or item.get("stage_id") in completed_ids
                        or str(item.get("path", "")).split(":", 1)[0] in completed_ids
                    )
                ]
            for item in self.blocking_diagnostics:
                path_stage, _, path_partition = str(item.get("path", "")).partition(":")
                item.setdefault("stage_id", path_stage)
                item.setdefault("partition_id", path_partition)
            blocked = {
                (
                    str(item.get("stage_id") or str(item.get("path", "")).split(":", 1)[0]),
                    str(item.get("partition_id") or str(item.get("path", "")).partition(":")[2]),
                )
                for item in self.blocking_diagnostics
            }
            for item in self.unresolved_partitions:
                key = (str(item.get("stage_id", "")), str(item.get("partition_id", "")))
                if key in blocked:
                    continue
                self.blocking_diagnostics.append(
                    {
                        "level": "error", "code": "unresolved_partition",
                        "message": str(item.get("diagnostic", "partition did not commit")),
                        "path": f"{key[0]}:{key[1]}", "stage_id": key[0],
                        "partition_id": key[1],
                    }
                )
                blocked.add(key)
            write_json(unresolved_path, self.unresolved_partitions)
            write_json(blocking_path, self.blocking_diagnostics)
            self.registry = build_registry(stage_artifacts, protocol_slug=_context_protocol_slug(context))
            if start_index > 3 and self.registry is not None:
                _normalize_stage4_callback_roles(stage_artifacts[3]["artifact"], self.registry)
                try:
                    _validate_completed_stage(
                        "public_artifact_inventory", stage_artifacts[:4], context,
                        registry=self.registry,
                    )
                except ValueError:
                    pass
                else:
                    self.unresolved_partitions = [
                        item for item in self.unresolved_partitions
                        if item.get("stage_id") != "public_artifact_inventory"
                    ]
                    self.blocking_diagnostics = [
                        item for item in self.blocking_diagnostics
                        if str(
                            item.get("stage_id")
                            or str(item.get("path", "")).split(":", 1)[0]
                        ) != "public_artifact_inventory"
                    ]
                    write_json(unresolved_path, self.unresolved_partitions)
                    write_json(blocking_path, self.blocking_diagnostics)
                write_json(
                    _stage_log_path(self.stage_log_dir, 3, PLANNING_STAGES[3]) / "artifact.json",
                    stage_artifacts[3]["artifact"],
                )
            if start_index > 5 and self.registry is not None:
                _normalize_stage6_callback_data_flow_interfaces(
                    stage_artifacts[5]["artifact"], stage_artifacts[:5], self.registry
                )
                write_json(
                    _stage_log_path(self.stage_log_dir, 5, PLANNING_STAGES[5]) / "artifact.json",
                    stage_artifacts[5]["artifact"],
                )
            self.amendments.set_registry(self.registry)
            if resume_from == "compile_specs":
                print("[planning] resume from compile_specs; reusing completed structured planning stages", flush=True)
                reconciled = _assemble_final_plan_candidate(
                    context, stage_artifacts[:-1], registry=self.registry,
                    allow_incomplete=bool(self.unresolved_partitions),
                )
                if self.unresolved_partitions:
                    reconciled["unresolved_partitions"] = deepcopy(self.unresolved_partitions)
                    reconciled["blocking_diagnostics"] = deepcopy(self.blocking_diagnostics)
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
                if stage.partition_strategy is not None:
                    self._record_unresolved_partition(
                        stage_id=stage.stage_id,
                        partition_id="whole_stage",
                        diagnostic=str(exc),
                        correction_attempted=stage.partition_strategy is not None,
                        issue=issue,
                    )
                    candidate_registry = self.registry
                elif self.unresolved_partitions and stage.stage_id != "function_interface_design":
                    fallback = _preserved_nonfatal_stage_artifact(
                        stage.stage_id, completed["artifact"], exc
                    ) or _empty_recoverable_stage_artifact(stage.stage_id)
                    if fallback is None:
                        self._record_stage_validation_failure(stage, exc)
                        raise RecoverablePlanningError(stage.stage_id, str(exc)) from exc
                    completed["artifact"] = fallback
                    self._record_unresolved_partition(
                        stage_id=stage.stage_id,
                        partition_id="whole_stage",
                        diagnostic=str(exc),
                        correction_attempted=False,
                        issue=issue,
                    )
                    candidate_registry = self._validate_stage_commit(
                        stage, stage_artifacts, completed, context
                    )
                    if self.stage_records and self.stage_records[-1].get("stage_id") == stage.stage_id:
                        self.stage_records[-1]["nonfatal_fallback"] = True
                        self.stage_records[-1]["artifact_keys"] = sorted(fallback)
                    stage_dir = self._stage_dir(stage)
                    if stage_dir is not None:
                        write_json(stage_dir / "artifact.json", fallback)
                        write_json(stage_dir / "stage_manifest.json", self.stage_records[-1])
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
                    uncorrected_artifact = deepcopy(completed["artifact"])
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
                        fallback = _preserved_nonfatal_stage_artifact(
                            stage.stage_id, uncorrected_artifact, exc
                        ) or _empty_recoverable_stage_artifact(stage.stage_id)
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
        if stage.stage_id == "function_interface_design":
            if self.registry is not None:
                normalized_interfaces: list[dict[str, Any]] = []
                for interface in completed["artifact"].get("function_interfaces", []):
                    if not isinstance(interface, dict):
                        continue
                    reference = interface.get("function_id") or interface.get("id") or interface.get("name")
                    if str(reference).startswith("fn:"):
                        try:
                            interface["function_id"] = self.registry.resolve(
                                str(reference)[3:], expected_kinds={"function"}
                            )["artifact_id"]
                        except RegistryBindingError:
                            completed["artifact"].setdefault("interface_diagnostics", []).append({
                                "code": "stage6_unregistered_fn_dialect_pruned",
                                "function_id": str(reference),
                            })
                            continue
                    normalized_interfaces.append(interface)
                completed["artifact"]["function_interfaces"] = normalized_interfaces
                _normalize_stage6_callback_data_flow_interfaces(
                    completed["artifact"], stage_artifacts, self.registry
                )
            _canonicalize_wire_target_copies(completed["artifact"], context)
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
            preserve_nonfatal=bool(self.unresolved_partitions),
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
            first_record = "semantic_correction_usage" not in record
            record["semantic_correction_usage"] = usage
            if first_record:
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
            {
                "level": "error", "code": "unresolved_partition", "message": diagnostic,
                "path": f"{stage_id}:{partition_id}", "stage_id": stage_id,
                "partition_id": partition_id,
            }
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
        interface_artifact = _stage_artifact(previous_artifacts, "function_interface_design")
        if (
            stage.stage_id in {"function_test_vector_design", "dependency_closure"}
            and "function_interfaces" in interface_artifact
            and not interface_artifact.get("function_interfaces")
        ):
            fallback = _empty_recoverable_stage_artifact(stage.stage_id)
            assert fallback is not None
            return self._run_zero_token_stage(
                stage, fallback, mode="deterministic_no_committed_interfaces"
            )
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

    def _generate_with_budget(self, client: Any, request: Any) -> Any:
        from agent.common.llm_client import estimate_message_input_tokens

        input_tokens = estimate_message_input_tokens(request.messages)
        if input_tokens > SINGLE_REQUEST_INPUT_TOKEN_CEILING:
            raise TokenBudgetExceeded(
                "single_request_input_token_ceiling",
                "single_request_input_token_ceiling: "
                f"projected_input_tokens={input_tokens}, ceiling={SINGLE_REQUEST_INPUT_TOKEN_CEILING}"
            )
        response_reserve = min(
            int(request.max_completion_tokens or SINGLE_RESPONSE_TOKEN_CEILING),
            SINGLE_RESPONSE_TOKEN_CEILING,
        )
        projected_total = self._model_tokens_used + input_tokens + response_reserve
        if projected_total > WHOLE_FRESH_TOKEN_CEILING:
            raise TokenBudgetExceeded(
                "whole_fresh_token_ceiling",
                "whole_fresh_token_ceiling: "
                f"used_tokens={self._model_tokens_used}, projected_input_tokens={input_tokens}, "
                f"response_reserve={response_reserve}, projected_total={projected_total}, "
                f"ceiling={WHOLE_FRESH_TOKEN_CEILING}"
            )
        response = client.generate_with_usage(request)
        self._model_tokens_used += int(response.usage.total_tokens)
        return response

    @property
    def model_tokens_used(self) -> int:
        return self._model_tokens_used

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
        primary_request_count = 0
        try:
            response = self._generate_with_budget(
                client,
                LLMRequest(
                    messages=messages,
                    top_p=0.2,
                    temperature=0.1,
                    is_stream=True,
                    enable_thinking=False,
                    max_completion_tokens=16000,
                )
            )
            primary_request_count = 1
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
            if stage.stage_id == "public_artifact_inventory":
                artifact = _normalize_public_inventory_dialects(artifact, context)
                artifact = _prune_invalid_stage4_lifecycle_relations(artifact)
            self._process_artifact_requests(stage, artifact)
        except BaseException as exc:
            if stage_dir is not None and not repair_usage["request_count"]:
                repair_usage_path = stage_dir / "repair_usage.json"
                if repair_usage_path.exists():
                    repair_usage = json.loads(repair_usage_path.read_text(encoding="utf-8"))
            elapsed = time.monotonic() - started
            fallback = _empty_recoverable_stage_artifact(stage.stage_id)
            has_inventory = self.registry is not None and all(
                self.registry.typed_view({kind}) for kind in ("module", "file", "function")
            )
            if isinstance(exc, (json.JSONDecodeError, TokenBudgetExceeded)) and fallback is not None and has_inventory:
                budget_exhausted = isinstance(exc, TokenBudgetExceeded)
                issue = ValidationIssue(
                    "structural",
                    exc.code if budget_exhausted else "syntax_repair_failed",
                    str(exc) if budget_exhausted else f"syntax_repair_failed: {type(exc).__name__}: {exc}",
                    "materialize_candidate_without_additional_model_calls" if budget_exhausted else "partition_regeneration",
                )
                self._record_unresolved_partition(
                    stage_id=stage.stage_id,
                    partition_id="whole_stage",
                    diagnostic=issue.message,
                    correction_attempted=False,
                    issue=issue,
                )
                record = {
                    "stage_id": stage.stage_id,
                    "title": stage.title,
                    "status": "completed",
                    "mode": "nonfatal_json_fallback",
                    "elapsed_seconds": round(elapsed, 3),
                    "usage": usage,
                    "repair_usage": repair_usage,
                    "request_count": primary_request_count + int(repair_usage.get("request_count", 0)),
                    "prompt_characters": primary_request_count * len(prompt_content) + int(repair_usage.get("prompt_characters", 0)),
                    "repaired_json": bool(repair_usage.get("request_count", 0)),
                    "nonfatal_fallback": True,
                    "unresolved_partitions": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                    "artifact_keys": sorted(fallback),
                }
                self.stage_records.append(record)
                if stage_dir is not None:
                    write_json(stage_dir / "artifact.json", fallback)
                    write_json(stage_dir / "stage_manifest.json", record)
                    write_json(self.stage_log_dir / "stage_records.json", self.stage_records)
                print(
                    f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} completed with non-fatal JSON fallback: "
                    f"{stage.stage_id}; {record['error']}",
                    flush=True,
                )
                return fallback
            status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            record = {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": status,
                "elapsed_seconds": round(elapsed, 3),
                "usage": usage,
                "repair_usage": repair_usage,
                "request_count": primary_request_count + int(repair_usage.get("request_count", 0)),
                "prompt_characters": primary_request_count * len(prompt_content) + int(repair_usage.get("prompt_characters", 0)),
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
            interface_artifact = _stage_artifact(previous_artifacts, "function_interface_design")
            if "function_interfaces" in interface_artifact:
                interface_ids = {
                    self.registry.resolve(
                        _stable_artifact_key(item, "function"), expected_kinds={"function"}
                    )["artifact_id"]
                    for item in interface_artifact.get("function_interfaces", [])
                    if isinstance(item, dict)
                }
                missing_interfaces = sorted(
                    {item["artifact_id"] for item in self.registry.typed_view({"function"})} - interface_ids
                )
                if missing_interfaces:
                    issue = ValidationIssue(
                        "binding", "stage8_interface_missing",
                        f"stage8_interface_missing: {missing_interfaces}",
                        "resume_or_regenerate_failed_partition",
                    )
                    self._record_unresolved_partition(
                        stage_id=stage.stage_id, partition_id="missing_stage6_interfaces",
                        diagnostic=issue.message, correction_attempted=False, issue=issue,
                    )
                partitions = _function_call_partitions(self.registry, interface_ids)
                if not partitions:
                    return self._run_zero_token_stage(
                        stage,
                        {
                            "call_edges": [], "callback_bindings": [], "runtime_flow": {},
                            "artifact_requests": [], "call_diagnostics": [],
                        },
                        mode="deterministic_no_committed_interfaces",
                    )
            else:
                partitions = _function_call_partitions(self.registry)
        elif stage.partition_strategy == "registry_types":
            if self.registry is None:
                raise RegistryInvariantError("registry_snapshot_missing: Stage 5 requires type registry")
            partitions = _type_definition_partitions(self.registry)
        else:
            interface_artifact = _stage_artifact(previous_artifacts, "function_interface_design")
            if (
                "function_interfaces" in interface_artifact
                and not interface_artifact.get("function_interfaces")
            ):
                return self._run_zero_token_stage(
                    stage,
                    {
                        "function_behaviors_by_group": [], "function_behaviors": [],
                        "wire_mappings": [], "behavior_diagnostics": [],
                    },
                    mode="deterministic_no_committed_interfaces",
                )
            partitions = _function_behavior_partitions(previous_artifacts, registry=self.registry)
        if stage.partition_strategy == "function_callers" and self.registry is not None:
            _attach_required_callback_bindings(
                partitions, previous_artifacts, self.registry
            )
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
                try:
                    response = self._generate_with_budget(
                        client,
                        LLMRequest(
                            messages=messages,
                            top_p=0.2,
                            temperature=0.1,
                            is_stream=True,
                            enable_thinking=False,
                            max_completion_tokens=16000,
                        )
                    )
                except TokenBudgetExceeded as budget_exc:
                    for deferred in partitions[partition_index - 1 :]:
                        issue = ValidationIssue(
                            "structural",
                            budget_exc.code,
                            str(budget_exc),
                            "materialize_candidate_without_additional_model_calls",
                        )
                        self._record_unresolved_partition(
                            stage_id=stage.stage_id,
                            partition_id=str(deferred["partition_id"]),
                            diagnostic=str(budget_exc),
                            correction_attempted=False,
                            issue=issue,
                        )
                    break
                request_count += 1
                prompt_characters += len(prompt_content)
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
                        repair_request_count += int(part_repair_usage.get("request_count", 0))
                        _add_usage(repair_usage, part_repair_usage)
                        budget_exhausted = isinstance(repair_exc, TokenBudgetExceeded)
                        diagnostic = (
                            str(repair_exc)
                            if budget_exhausted
                            else f"syntax_repair_failed: {type(repair_exc).__name__}: {repair_exc}"
                        )
                        issue = ValidationIssue(
                            "structural",
                            repair_exc.code if budget_exhausted else "syntax_repair_failed",
                            diagnostic,
                            "materialize_candidate_without_additional_model_calls" if budget_exhausted else "partition_regeneration",
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
                    repair_request_count += int(part_repair_usage.get("request_count", 0))
                    _add_usage(repair_usage, part_repair_usage)
                    repaired = True
                    repaired_any = True
                correction_attempted = False
                correction_error: str | None = None
                validation_issue: ValidationIssue | None = None
                part_correction_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                try:
                    assert self.registry is not None
                    _validate_partition_artifact(
                        stage, artifact, partition, self.registry, context, previous_artifacts
                    )
                except LayeredValidationError as exc:
                    validation_issue = exc.issue
                    correction_usage_added = False
                    correction_attempted = True
                    local_corrections += 1
                    self.validation_ledger.record(
                        exc.issue,
                        stage_id=stage.stage_id,
                        partition_id=str(partition["partition_id"]),
                        outcome="correction_requested",
                    )
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
                        _validate_partition_artifact(
                            stage, artifact, partition, self.registry, context, previous_artifacts
                        )
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
                        if isinstance(correction_exc, TokenBudgetExceeded):
                            correction_attempted = False
                            local_corrections -= 1
                            validation_issue = ValidationIssue(
                                "structural",
                                correction_exc.code,
                                str(correction_exc),
                                "materialize_candidate_without_additional_model_calls",
                            )
                            correction_error = str(correction_exc)
                            self.validation_ledger.record(
                                validation_issue,
                                stage_id=stage.stage_id,
                                partition_id=str(partition["partition_id"]),
                                outcome="deferred_by_token_budget",
                            )
                        elif isinstance(correction_exc, LayeredValidationError):
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
        response = self._generate_with_budget(
            client,
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
        response = self._generate_with_budget(
            client,
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
        if stage.stage_id == "public_artifact_inventory":
            corrected = _normalize_public_inventory_dialects(corrected, context)
            corrected = _prune_invalid_stage4_lifecycle_relations(corrected)
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
            response = self._generate_with_budget(
                client,
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


def _normalize_grounded_runtime_literals(
    artifact: dict[str, Any], context: dict[str, Any]
) -> None:
    default_port = _find_named_integer(context.get("facts", {}), "default_port")
    if default_port is None:
        return
    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict):
            continue
        for binding in edge.get("argument_semantics", []):
            if (
                isinstance(binding, dict)
                and binding.get("source_kind") == "literal"
                and str(binding.get("parameter", "")).lower().endswith("port")
            ):
                binding["source_ref"] = str(default_port)


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
    previous_artifacts: list[dict[str, Any]], registry: CanonicalPlanningRegistry | None = None,
    *, allow_incomplete: bool = False,
) -> list[dict[str, Any]]:
    interfaces = _stage_artifact(previous_artifacts, "function_interface_design")
    behaviors = _stage_artifact(previous_artifacts, "function_behavior_design")
    calls = _stage_artifact(previous_artifacts, "function_call_contract_closure")
    tests = _stage_artifact(previous_artifacts, "function_test_vector_design")
    dependencies = _stage_artifact(previous_artifacts, "dependency_closure")
    by_id, by_name = _canonical_function_indexes(interfaces, registry)

    def target(reference: Any, stage_id: str) -> dict[str, Any] | None:
        try:
            return _resolve_function(reference, by_id, by_name, stage_id=stage_id, registry=registry)
        except ValueError:
            if allow_incomplete and registry is not None:
                entry = registry.resolve(reference, expected_kinds={"function"})
                if entry["artifact_id"] not in by_id:
                    return None
            raise

    for item in behaviors.get("function_behaviors", []):
        if not isinstance(item, dict):
            continue
        current = target(item.get("function_id"), "function_behavior_design")
        if current is None:
            continue
        forbidden = {"owner_file", "file", "name", "signature", "visibility"}.intersection(item)
        if forbidden:
            raise ValueError(f"overlay_modifies_canonical_identity: function_behavior_design changes {sorted(forbidden)}")
        for key in ("LOGIC", "EVENT", "wire_mapping", "WIRE_MAPPING"):
            if key in item:
                current[key] = deepcopy(item[key])
        current["trace_refs"] = list(dict.fromkeys([*current.get("trace_refs", []), *item.get("trace_refs", [])]))
    for item in behaviors.get("wire_mappings", []):
        if isinstance(item, dict):
            current = target(item.get("function_id"), "function_behavior_design")
            if current is not None:
                current["wire_mapping"] = deepcopy(item.get("wire_mapping", item.get("WIRE_MAPPING")))

    call_edges, rely_by_function = _typed_call_stage_artifact(calls, registry)
    callback_bindings = {
        str(item.get("binding_id")): item
        for item in calls.get("callback_bindings", [])
        if isinstance(item, dict) and item.get("binding_id")
    }
    if isinstance(rely_by_function, dict):
        for function_id, rely in rely_by_function.items():
            current = target(function_id, "function_call_contract_closure")
            if current is not None:
                current["RELY"] = deepcopy(rely)
    contracts_by_caller: dict[str, list[dict[str, Any]]] = {}
    for edge in call_edges:
        caller = target(edge.get("caller_function_id"), "function_call_contract_closure")
        callee = target(edge.get("callee_function_id"), "function_call_contract_closure")
        if caller is None or callee is None:
            continue
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
        for argument in contract.get("argument_semantics", []):
            if not isinstance(argument, dict) or argument.get("source_kind") != "callback_binding":
                continue
            binding = callback_bindings.get(str(argument.get("source_ref", "")))
            if binding is None:
                continue
            provider = registry.resolve(
                binding.get("provider_function_id"), expected_kinds={"function"}
            ) if registry is not None else {}
            argument["callback_provider"] = {
                "function_id": str(binding.get("provider_function_id", "")),
                "name": str(provider.get("canonical_name", "")),
                "callback_type_id": str(binding.get("callback_type_id", "")),
                "user_data_source": str(binding.get("user_data_source", "")),
            }
        contracts_by_caller.setdefault(caller_id, []).append(contract)
    for caller_id, contracts in contracts_by_caller.items():
        by_id[caller_id]["CALL_CONTRACTS"] = _canonicalize_contracts(
            contracts, by_id, by_name, stage_id="function_call_contract_closure", registry=registry
        )

    function_vectors = tests.get("function_test_vectors", {})
    if isinstance(function_vectors, dict):
        for function_id, vectors in function_vectors.items():
            current = target(function_id, "function_test_vector_design")
            if current is not None:
                current["TEST_VECTORS"] = deepcopy(vectors)

    for item in dependencies.get("functions", []):
        if not isinstance(item, dict):
            continue
        current = target(item.get("function_id") or item.get("id"), "dependency_closure")
        if current is None:
            continue
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

    allowed_top = {
        "call_edges", "call_edges_by_group", "callback_bindings", "runtime_flow",
        "artifact_requests", "call_diagnostics",
    }
    extra_top = set(artifact) - allowed_top
    if extra_top:
        raise ValueError(f"typed_delta_forbidden_field: function_call_contract_closure {sorted(extra_top)}")
    legacy_required = {
        "caller_function_id",
        "callee_function_id",
        "call_purpose",
        "condition",
        "argument_semantics",
        "result_usage",
    }
    typed_required = {*legacy_required, "trace_refs"}
    edges = []
    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict) or frozenset(edge) not in {frozenset(legacy_required), frozenset(typed_required)}:
            raise ValueError(
                f"typed_delta_invalid_shape: Stage 8 call edge requires only {sorted(typed_required)}"
            )
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


def _validate_typed_call_relations(
    artifact: dict[str, Any],
    edges: list[dict[str, Any]],
    partition: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
) -> None:
    from .compiler import _STANDARD_TYPES, _lower_type_spec, _parse_param, _signature, _split_params

    interfaces, _ = _canonical_function_indexes(
        _stage_artifact(previous_artifacts, "function_interface_design"), registry
    )

    def c_type(value: Any) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\s*\*\s*", "*", str(value).strip()))

    def provider_type_matches(actual: str, expected: str) -> bool:
        if actual == expected:
            return True
        if expected.startswith("const ") and expected.endswith("*") and actual == expected.removeprefix("const "):
            return True
        if expected in {"void*", "const void*"} and actual.endswith("*"):
            return True
        if actual == "void*" and expected.endswith("*"):
            return True
        return actual == "const void*" and expected.startswith("const ") and expected.endswith("*")

    signatures = {
        function_id: _signature(item.get("signature"), function_id)
        for function_id, item in interfaces.items()
    }
    edge_interface_ids = {
        str(edge.get(key, ""))
        for edge in edges
        for key in ("caller_function_id", "callee_function_id")
    }
    missing_interfaces = sorted(edge_interface_ids - set(interfaces))
    if missing_interfaces:
        raise ValueError(f"stage8_interface_missing: {missing_interfaces}")
    allowed_callers = set(map(str, partition.get("caller_function_ids", [])))
    access_refs: set[str] = set()
    type_designs = _canonicalize_registry_types(
        _stage_artifact(previous_artifacts, "type_and_access_path_design"), registry,
        allow_missing=True,
    )
    field_types_by_type: dict[str, dict[str, str]] = {}
    for design in type_designs:
        spec = _lower_type_spec(design)
        if spec.get("TYPE_KIND") == "STRUCT":
            field_types_by_type[design["name"]] = {
                str(field.get("NAME")): c_type(field.get("TYPE"))
                for field in spec.get("FIELDS", [])
                if isinstance(field, dict) and field.get("NAME") and field.get("TYPE")
            }
        for item in [*design.get("access_paths", []), *design.get("ownership_fields", [])]:
            if isinstance(item, dict):
                reference = item.get("path") or item.get("PATH") or item.get("name") or item.get("NAME")
            else:
                reference = item
            if str(reference or "").strip():
                access_refs.add(str(reference))
    behavior_artifact = _stage_artifact(previous_artifacts, "function_behavior_design")
    for behavior in [
        *behavior_artifact.get("function_behaviors", []),
        *behavior_artifact.get("wire_mappings", []),
    ]:
        if not isinstance(behavior, dict):
            continue
        mappings = behavior.get("wire_mapping", behavior.get("WIRE_MAPPING", []))
        for mapping in mappings if isinstance(mappings, list) else []:
            if isinstance(mapping, dict) and str(mapping.get("target", mapping.get("TARGET", ""))).strip():
                access_refs.add(str(mapping.get("target", mapping.get("TARGET"))))
    for item in behavior_artifact.get("wire_mappings", []):
        if not isinstance(item, dict):
            continue
        mappings = item.get("wire_mapping", item.get("WIRE_MAPPING", []))
        for mapping in mappings if isinstance(mappings, list) else []:
            if isinstance(mapping, dict) and str(mapping.get("target", mapping.get("TARGET", ""))).strip():
                access_refs.add(str(mapping.get("target", mapping.get("TARGET"))))

    callback_specs: dict[str, tuple[str, list[str]]] = {}
    for design in type_designs:
        spec = _lower_type_spec(design)
        if spec.get("TYPE_KIND") != "CALLBACK":
            continue
        match = re.fullmatch(
            r"(?P<return>.+?)\(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*\((?P<params>.*)\)",
            str(spec.get("CALLBACK_SIGNATURE", "")),
        )
        if match is not None:
            callback_specs[design["id"]] = (
                c_type(match.group("return")),
                [c_type(_parse_param(value).get("TYPE")) for value in _split_params(match.group("params"))],
            )
    callback_type_names = {
        registry.resolve(callback_id, expected_kinds={"callback"})["canonical_name"]
        for callback_id in callback_specs
    }

    callback_bindings: dict[str, dict[str, Any]] = {}
    required_binding_fields = {
        "binding_id", "owner_function_id", "consumer_function_id", "consumer_parameter",
        "callback_type_id", "provider_function_id", "user_data_source", "trace_refs",
    }
    for binding in artifact.get("callback_bindings", []):
        if not isinstance(binding, dict) or set(binding) != required_binding_fields:
            raise ValueError(
                f"stage8_callback_binding_shape: requires only {sorted(required_binding_fields)}"
            )
        binding_id = str(binding.get("binding_id", ""))
        if not binding_id or binding_id in callback_bindings or not _support_refs(binding):
            raise ValueError(f"stage8_callback_binding_identity_or_grounding: {binding_id}")
        owner_id = registry.resolve(
            binding["owner_function_id"], expected_kinds={"function"}
        )["artifact_id"]
        consumer_id = registry.resolve(
            binding["consumer_function_id"], expected_kinds={"function"}
        )["artifact_id"]
        provider_id = registry.resolve(
            binding["provider_function_id"], expected_kinds={"function"}
        )["artifact_id"]
        callback_id = registry.resolve(
            binding["callback_type_id"], expected_kinds={"callback"}
        )["artifact_id"]
        missing = sorted({owner_id, consumer_id, provider_id} - set(interfaces))
        if missing:
            raise ValueError(f"stage8_interface_missing: {missing}")
        if owner_id not in allowed_callers or not str(binding.get("user_data_source", "")).strip():
            raise ValueError(f"stage8_callback_binding_owner_or_user_data: {binding_id}")
        consumer_params = {
            str(param.get("NAME")): c_type(param.get("TYPE"))
            for param in signatures[consumer_id].get("PARAMS", [])
            if isinstance(param, dict)
        }
        callback_name = registry.resolve(callback_id)["canonical_name"]
        if consumer_params.get(str(binding["consumer_parameter"])) != callback_name:
            raise ValueError(f"stage8_callback_consumer_abi_mismatch: {binding_id}")
        expected_return, expected_params = callback_specs.get(callback_id, ("", []))
        provider_signature = signatures[provider_id]
        actual_params = [
            c_type(param.get("TYPE")) for param in provider_signature.get("PARAMS", [])
            if isinstance(param, dict)
        ]
        if c_type(provider_signature.get("RETURN")) != expected_return or actual_params != expected_params:
            raise ValueError(f"stage8_callback_provider_abi_mismatch: {binding_id}")
        callback_bindings[binding_id] = {
            **deepcopy(binding),
            "owner_function_id": owner_id,
            "consumer_function_id": consumer_id,
            "provider_function_id": provider_id,
            "callback_type_id": callback_id,
        }

    used_binding_ids = {
        str(binding.get("source_ref", ""))
        for edge in edges
        for binding in edge.get("argument_semantics", [])
        if isinstance(binding, dict) and binding.get("source_kind") == "callback_binding"
    }
    missing_required_bindings: list[str] = []
    for required in partition.get("required_callback_bindings", []):
        if not isinstance(required, dict):
            continue
        matches = [
            binding for binding in callback_bindings.values()
            if binding["callback_type_id"] == required.get("callback_type_id")
            and binding["consumer_function_id"] == required.get("consumer_function_id")
            and binding["provider_function_id"] == required.get("provider_function_id")
        ]
        if not matches or not any(
            str(binding.get("binding_id", "")) in used_binding_ids for binding in matches
        ):
            missing_required_bindings.append(
                f"{required.get('callback_type_id')}:{required.get('consumer_function_id')}"
                f"->{required.get('provider_function_id')}"
            )
    if missing_required_bindings:
        raise ValueError(
            f"stage8_required_callback_binding_missing: {sorted(missing_required_bindings)}"
        )

    prior_results: dict[str, set[str]] = {}
    for edge in edges:
        caller_id = str(edge["caller_function_id"])
        callee_id = str(edge["callee_function_id"])
        purpose = str(edge.get("call_purpose", "")).strip()
        if (
            caller_id not in allowed_callers
            or not purpose
            or purpose.lower() in {"unknown", "todo", "none", "n/a"}
            or not _support_refs(edge)
        ):
            raise ValueError(f"stage8_direct_call_identity_or_grounding: {caller_id}->{callee_id}")
        condition = edge.get("condition")
        if (
            not isinstance(condition, dict)
            or set(condition) != {"expression", "reachable"}
            or not str(condition.get("expression", "")).strip()
            or str(condition.get("expression", "")).strip().lower() in {"never", "none", "unknown", "todo"}
            or condition.get("reachable") is not True
        ):
            raise ValueError(f"stage8_direct_call_condition_invalid: {caller_id}->{callee_id}")
        caller = interfaces[caller_id]
        callee = interfaces[callee_id]
        if caller["file"] != callee["file"] and callee["visibility"] != "public":
            raise ValueError(f"stage8_cross_file_private_callee: {caller_id}->{callee_id}")

        callee_params = [
            param for param in signatures[callee_id].get("PARAMS", []) if isinstance(param, dict)
        ]
        bindings = edge.get("argument_semantics")
        if not isinstance(bindings, list) or len(bindings) != len(callee_params):
            raise ValueError(f"stage8_argument_binding_coverage: {caller_id}->{callee_id}")
        by_parameter = {
            str(binding.get("parameter")): binding
            for binding in bindings if isinstance(binding, dict)
        }
        if len(by_parameter) != len(bindings) or set(by_parameter) != {
            str(param.get("NAME")) for param in callee_params
        }:
            raise ValueError(f"stage8_argument_binding_coverage: {caller_id}->{callee_id}")
        caller_params = {
            str(param.get("NAME")): c_type(param.get("TYPE"))
            for param in signatures[caller_id].get("PARAMS", []) if isinstance(param, dict)
        }
        caller_param_roles = {
            str(param.get("NAME")): str(param.get("ROLE", "")).lower()
            for param in signatures[caller_id].get("PARAMS", []) if isinstance(param, dict)
        }
        caller_fields: dict[str, str] = {}
        for caller_parameter, caller_type in caller_params.items():
            base_type = re.sub(r"\bconst\b|\*", "", caller_type).strip()
            for field_name, field_type in field_types_by_type.get(base_type, {}).items():
                caller_fields[f"{caller_parameter}->{field_name}"] = field_type
                caller_fields[f"{caller_parameter}.{field_name}"] = field_type
        semantic_context = " ".join((
            str(edge.get("call_purpose", "")),
            str(edge.get("condition", {}).get("expression", "")),
        )).lower()
        void_parameters = [
            name for name, value in caller_params.items() if c_type(value) in {"void*", "const void*"}
        ]
        payload_parameters = [
            name for name in void_parameters
            if name.lower() in {"packet", "packet_payload", "payload", "message", "data"}
            or name.lower().endswith(("_packet", "_payload", "_message"))
            or (name.lower() != "user_data" and name.lower().endswith("_data"))
            or any(
                marker in caller_param_roles.get(name, "")
                for marker in ("decoded_payload", "payload", "message_data")
            )
        ]
        selected_payload = payload_parameters[0] if len(payload_parameters) == 1 else (
            void_parameters[0] if len(void_parameters) == 1 else ""
        )
        if selected_payload:
            for type_name, fields in field_types_by_type.items():
                discriminants = set(type_name.lower().split("_")) - {"mqtt", "packet", "type", "t"}
                if not any(len(value) > 2 and value in semantic_context for value in discriminants):
                    continue
                for field_name, field_type in fields.items():
                    caller_fields[
                        f"(({type_name}*){selected_payload})->{field_name}"
                    ] = field_type
        for param in callee_params:
            parameter = str(param.get("NAME"))
            binding = by_parameter[parameter]
            if set(binding) != {"parameter", "source_kind", "source_ref", "source_type"}:
                raise ValueError(f"stage8_argument_binding_shape: {caller_id}->{callee_id}:{parameter}")
            source_kind = str(binding.get("source_kind", ""))
            source_ref = str(binding.get("source_ref", ""))
            expected_type = c_type(param.get("TYPE"))
            if not provider_type_matches(c_type(binding.get("source_type")), expected_type) or not source_ref:
                raise ValueError(f"stage8_argument_binding_type: {caller_id}->{callee_id}:{parameter}")
            if expected_type in callback_type_names and source_kind != "callback_binding":
                raise ValueError(f"stage8_callback_binding_missing: {caller_id}->{callee_id}:{parameter}")
            if source_kind == "caller_param" and not provider_type_matches(
                caller_params.get(source_ref, ""), expected_type
            ):
                raise ValueError(f"stage8_argument_provider_missing: {caller_id}->{callee_id}:{parameter}")
            if source_kind == "caller_field" and not provider_type_matches(
                caller_fields.get(source_ref, ""), expected_type
            ):
                raise ValueError(f"stage8_argument_provider_missing: {caller_id}->{callee_id}:{parameter}")
            if source_kind == "local_value" and not re.fullmatch(
                r"&?[A-Za-z_][A-Za-z0-9_]*(?:\[(?:\d+|[A-Za-z_][A-Za-z0-9_]*)\])?", source_ref
            ):
                raise ValueError(f"stage8_argument_provider_missing: {caller_id}->{callee_id}:{parameter}")
            if source_kind == "prior_result":
                source_id = registry.resolve(source_ref, expected_kinds={"function"})["artifact_id"]
                if source_id not in signatures:
                    raise ValueError(f"stage8_interface_missing: {[source_id]}")
                if source_id not in prior_results.get(caller_id, set()) or not provider_type_matches(
                    c_type(signatures[source_id].get("RETURN")), expected_type
                ):
                    raise ValueError(f"stage8_argument_provider_missing: {caller_id}->{callee_id}:{parameter}")
            elif source_kind == "constant":
                registry.resolve(source_ref, expected_kinds={"constant"})
            elif source_kind == "literal":
                custom_types = {
                    type_name
                    for type_name in c_type_references(expected_type)
                    if type_name not in _STANDARD_TYPES and not is_system_type(type_name)
                }
                if custom_types:
                    raise ValueError(f"stage8_custom_type_literal_forbidden: {caller_id}->{callee_id}:{parameter}")
            elif source_kind in {"access_path", "owned_state"} and source_ref not in access_refs:
                raise ValueError(f"stage8_argument_provider_missing: {caller_id}->{callee_id}:{parameter}")
            elif source_kind == "callback_binding":
                callback = callback_bindings.get(source_ref)
                if (
                    callback is None
                    or callback["owner_function_id"] != caller_id
                    or callback["consumer_function_id"] != callee_id
                    or callback["consumer_parameter"] != parameter
                ):
                    raise ValueError(f"stage8_callback_binding_missing: {caller_id}->{callee_id}:{parameter}")
            elif source_kind not in {
                "caller_param", "caller_field", "local_value", "prior_result", "constant", "literal", "access_path",
                "owned_state", "callback_binding",
            }:
                raise ValueError(f"stage8_argument_source_kind_invalid: {source_kind}")

        result = edge.get("result_usage")
        if not isinstance(result, dict) or set(result) != {"usage", "target"}:
            raise ValueError(f"stage8_result_binding_shape: {caller_id}->{callee_id}")
        usage = str(result.get("usage", ""))
        return_type = c_type(signatures[callee_id].get("RETURN"))
        if usage not in {"ignored", "checked", "stored", "returned", "passed"}:
            raise ValueError(f"stage8_result_usage_invalid: {caller_id}->{callee_id}")
        if (return_type == "void") != (usage == "ignored") or (return_type != "void" and not str(result.get("target", "")).strip()):
            raise ValueError(f"stage8_result_usage_mismatch: {caller_id}->{callee_id}")
        prior_results.setdefault(caller_id, set()).add(callee_id)

def _validate_runtime_flow_artifact(
    artifact: dict[str, Any],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry,
) -> None:
    expected_main = str(partition.get("main_function_id", ""))
    flow = artifact.get("runtime_flow")
    if not expected_main:
        if flow not in (None, {}):
            raise ValueError("stage8_runtime_flow_partition_escape: only the main partition may emit runtime_flow")
        return
    if not isinstance(flow, dict):
        raise ValueError("stage8_runtime_flow_missing: main partition requires runtime_flow")
    required = {"main_function_id", "success_sequence", "failure_cleanup", "trace_refs"}
    if set(flow) != required or not _support_refs(flow):
        raise ValueError(f"stage8_runtime_flow_shape: requires only {sorted(required)}")
    main_id = registry.resolve(flow.get("main_function_id"), expected_kinds={"function"})["artifact_id"]
    if main_id != expected_main:
        raise ValueError("stage8_runtime_flow_main_mismatch: runtime_flow must own the canonical main")
    sequence = flow.get("success_sequence")
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("stage8_runtime_success_sequence_empty: runtime services must be ordered")
    resolved_sequence = [
        registry.resolve(value, expected_kinds={"function"})["artifact_id"] for value in sequence
    ]
    if len(resolved_sequence) != len(set(resolved_sequence)) or main_id in resolved_sequence:
        raise ValueError("stage8_runtime_success_sequence_invalid: service IDs must be unique and exclude main")
    cleanup = flow.get("failure_cleanup")
    if not isinstance(cleanup, list) or not cleanup:
        raise ValueError("stage8_runtime_failure_cleanup_empty: failure cleanup coverage is required")
    normalized_cleanup: list[dict[str, Any]] = []
    for item in cleanup:
        if not isinstance(item, dict) or set(item) != {"after_function_id", "cleanup_function_ids"}:
            raise ValueError("stage8_runtime_failure_cleanup_shape: each entry needs after_function_id and cleanup_function_ids")
        cleanup_ids = item.get("cleanup_function_ids")
        if not isinstance(cleanup_ids, list) or not cleanup_ids:
            raise ValueError("stage8_runtime_failure_cleanup_empty: every fallible step needs cleanup")
        normalized_cleanup.append(
            {
                "after_function_id": registry.resolve(
                    item.get("after_function_id"), expected_kinds={"function"}
                )["artifact_id"],
                "cleanup_function_ids": [
                    registry.resolve(value, expected_kinds={"function"})["artifact_id"]
                    for value in cleanup_ids
                ],
            }
        )
    flow["main_function_id"] = main_id
    flow["success_sequence"] = resolved_sequence
    flow["failure_cleanup"] = normalized_cleanup
    direct_services: set[str] = set()
    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict):
            continue
        try:
            caller_id = registry.resolve(
                edge.get("caller_function_id"), expected_kinds={"function"}
            )["artifact_id"]
            callee_id = registry.resolve(
                edge.get("callee_function_id"), expected_kinds={"function"}
            )["artifact_id"]
        except RegistryBindingError:
            continue
        if caller_id == main_id:
            direct_services.add(callee_id)
    missing_direct_services = sorted(set(resolved_sequence) - direct_services)
    if missing_direct_services:
        raise ValueError(
            f"stage8_runtime_direct_edge_missing: {missing_direct_services}"
        )


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


def _support_refs(value: dict[str, Any]) -> list[str]:
    refs = [
        str(item)
        for key in ("fact_refs", "trace_refs", "rule_refs", "decision_refs")
        for item in (value.get(key, []) if isinstance(value.get(key), list) else [])
    ]
    return [item for item in refs if item]


def _normalize_stage4_callback_roles(
    inventory: dict[str, Any], registry: CanonicalPlanningRegistry
) -> None:
    coverage = next((
        item for item in inventory.get("implementation_coverage_matrix", [])
        if isinstance(item, dict) and item.get("obligation_id") == "foundation:callback_provider"
    ), None)
    if not isinstance(coverage, dict):
        return
    entries = [registry.resolve(reference) for reference in coverage.get("artifact_ids", [])]
    callbacks = [entry for entry in entries if entry["artifact_kind"] == "callback"]
    functions = [entry for entry in entries if entry["artifact_kind"] == "function"]
    if not callbacks:
        return
    functions_by_id = {
        registry.resolve(
            item.get("symbol") or item.get("name") or item.get("function_id"),
            expected_kinds={"function"},
        )["artifact_id"]: item
        for item in inventory.get("functions", []) if isinstance(item, dict)
    }
    covered_ids = {entry["artifact_id"] for entry in entries}
    all_functions = registry.typed_view({"function"})
    callbacks_by_owner: dict[str, list[dict[str, Any]]] = {}
    for callback in registry.typed_view({"callback"}):
        callbacks_by_owner.setdefault(str(callback.get("owner_file_id", "")), []).append(callback)
    for owner_file_id, owner_callbacks in callbacks_by_owner.items():
        callback_names = [entry["canonical_name"] for entry in owner_callbacks]
        callback_role_tokens = {
            name: {
                token for token in re.findall(r"[a-z]+", name.lower())
                if token not in {"callback", "cb", "fn", "handler", "mqtt", "tcp", "t"}
            }
            for name in callback_names
        }
        provider_by_callback: dict[str, dict[str, Any]] = {}
        for name in callback_names:
            tokens = callback_role_tokens[name]
            candidates = [
                entry for entry in all_functions
                if entry.get("owner_file_id") != owner_file_id
                and tokens.intersection(re.findall(
                    r"[a-z]+",
                    " ".join((
                        entry["canonical_name"].lower(),
                        str(functions_by_id.get(entry["artifact_id"], {}).get("role", "")).lower(),
                    )),
                ))
                and (
                    any(marker in entry["canonical_name"].lower() for marker in ("_on_", "_handle_"))
                    or any(
                        marker in str(functions_by_id.get(entry["artifact_id"], {}).get("role", "")).lower()
                        for marker in ("provide", "implement")
                    )
                )
            ]
            if len(candidates) == 1:
                provider_by_callback[name] = candidates[0]
        if len(provider_by_callback) != len(callback_names):
            continue
        providers = list({entry["artifact_id"]: entry for entry in provider_by_callback.values()}.values())
        covered_owner_consumers = [
            entry for entry in functions
            if entry.get("owner_file_id") == owner_file_id and entry not in providers
        ]
        consumers = covered_owner_consumers if len(covered_owner_consumers) == 1 else [
            entry for entry in all_functions
            if entry.get("owner_file_id") == owner_file_id
            and any(
                marker in str(functions_by_id.get(entry["artifact_id"], {}).get("role", "")).lower()
                for marker in ("consume", "register", "accept", "startup", "start", "create", "decoder", "feed")
            )
        ]
        if len(consumers) != 1:
            continue
        consumer = functions_by_id[consumers[0]["artifact_id"]]
        role = str(consumer.get("role", "")).strip()
        if "callback" not in role.lower() or not any(
            marker in role.lower() for marker in ("consume", "register", "accept", "store")
        ):
            consumer["role"] = f"{role}; accept and consume {', '.join(callback_names)} callback".lstrip("; ")
        for name, entry in provider_by_callback.items():
            provider = functions_by_id[entry["artifact_id"]]
            role = str(provider.get("role", "")).strip()
            if not ("callback" in role.lower() and any(
                marker in role.lower() for marker in ("provide", "implement")
            )):
                provider["role"] = f"{role}; implement and provide {name} callback".lstrip("; ")
        for entry in [*owner_callbacks, consumers[0], *providers]:
            if entry["artifact_id"] not in covered_ids:
                coverage.setdefault("artifact_ids", []).append(entry["artifact_id"])
                covered_ids.add(entry["artifact_id"])

    entries = [registry.resolve(reference) for reference in coverage.get("artifact_ids", [])]
    callbacks = [entry for entry in entries if entry["artifact_kind"] == "callback"]
    functions = [entry for entry in entries if entry["artifact_kind"] == "function"]
    def has_role(entry: dict[str, Any], markers: tuple[str, ...]) -> bool:
        item = functions_by_id.get(entry["artifact_id"], {})
        role = str(item.get("role", "")).lower()
        return "callback" in role and any(marker in role for marker in markers)

    consumers = [entry for entry in functions if has_role(entry, ("consume", "register", "accept", "store"))]
    providers = [entry for entry in functions if has_role(entry, ("provide", "implement"))]
    if not consumers and len(callbacks) == 1:
        consumers = [
            entry for entry in functions if entry not in providers
            and entry.get("owner_file_id") == callbacks[0].get("owner_file_id")
        ]
    if not consumers:
        callback_owner_ids = {entry.get("owner_file_id") for entry in callbacks}
        consumers = [
            entry for entry in registry.typed_view({"function"}) if entry not in providers
            and entry.get("owner_file_id") in callback_owner_ids
            and any(
                marker in str(functions_by_id.get(entry["artifact_id"], {}).get("role", "")).lower()
                for marker in ("startup", "start", "decoder", "feed", "register")
            )
        ]
    if not providers and len(callbacks) == 1:
        providers = [entry for entry in functions if entry not in consumers]
    if len(consumers) != 1 or not providers:
        return
    callback_name = ", ".join(entry["canonical_name"] for entry in callbacks)
    consumer = functions_by_id.get(consumers[0]["artifact_id"])
    if consumer is not None:
        role = str(consumer.get("role", "")).strip()
        if not ("callback" in role.lower() and any(
            marker in role.lower() for marker in ("consume", "register", "accept", "store")
        )):
            consumer["role"] = f"{role}; accept and consume {callback_name} callback".lstrip("; ")
        if consumers[0]["artifact_id"] not in {entry["artifact_id"] for entry in entries}:
            coverage["artifact_ids"].append(consumers[0]["artifact_id"])
    provider = functions_by_id.get(providers[0]["artifact_id"]) if len(providers) == 1 else None
    if provider is not None:
        role = str(provider.get("role", "")).strip()
        if not ("callback" in role.lower() and any(
            marker in role.lower() for marker in ("provide", "implement")
        )):
            provider["role"] = f"{role}; implement and provide {callback_name} callback".lstrip("; ")


def _validate_public_inventory_obligations(
    inventory: dict[str, Any],
    context: dict[str, Any],
    registry: CanonicalPlanningRegistry,
) -> None:
    obligations = {
        str(item.get("obligation_id")): item
        for item in context.get("required_implementation_obligations", [])
        if isinstance(item, dict) and item.get("obligation_id")
    }
    matrix = inventory.get("implementation_coverage_matrix")
    if not isinstance(matrix, list):
        raise ValueError("stage4_coverage_matrix_missing: implementation_coverage_matrix must be an array")
    coverage = {
        str(item.get("obligation_id")): item
        for item in matrix
        if isinstance(item, dict) and item.get("obligation_id")
    }
    if len(matrix) != len(coverage) or set(coverage) != set(obligations):
        raise ValueError(
            "stage4_obligation_coverage_mismatch: "
            f"missing={sorted(set(obligations) - set(coverage))}, extra={sorted(set(coverage) - set(obligations))}"
        )
    kind_deficits: list[str] = []
    for obligation_id, obligation in obligations.items():
        item = coverage[obligation_id]
        if not _support_refs(item):
            raise ValueError(f"stage4_coverage_grounding_missing: {obligation_id}")
        if not isinstance(item.get("artifact_ids"), list):
            raise ValueError(f"stage4_coverage_artifacts_invalid: {obligation_id}")
        counts: dict[str, int] = {}
        resolved_ids: set[str] = set()
        for reference in item.get("artifact_ids", []):
            entry = registry.resolve(reference)
            if not entry.get("provenance", {}).get("refs"):
                raise ValueError(
                    f"stage4_artifact_grounding_missing: {obligation_id} references {entry['artifact_id']}"
                )
            if entry["artifact_id"] in resolved_ids:
                continue
            resolved_ids.add(entry["artifact_id"])
            counts[entry["artifact_kind"]] = counts.get(entry["artifact_kind"], 0) + 1
        for kind, minimum in obligation.get("required_kind_counts", {}).items():
            if counts.get(kind, 0) < int(minimum):
                kind_deficits.append(
                    f"{obligation_id} requires {minimum} {kind}, found {counts.get(kind, 0)}"
                )

    functions_by_id = {
        registry.resolve(
            item.get("symbol") or item.get("name") or item.get("function_id"),
            expected_kinds={"function"},
        )["artifact_id"]: item
        for item in inventory.get("functions", []) if isinstance(item, dict)
    }

    def covered_function_roles(obligation_id: str) -> list[str]:
        return [
            str(functions_by_id[entry["artifact_id"]].get("role", "")).lower()
            for reference in coverage.get(obligation_id, {}).get("artifact_ids", [])
            for entry in [registry.resolve(reference)]
            if entry["artifact_kind"] == "function" and entry["artifact_id"] in functions_by_id
        ]

    def covered_function_texts(obligation_id: str) -> list[str]:
        return [
            f"{role} {registry.resolve(reference)['canonical_name'].lower()}"
            for reference, role in zip(
                [
                    reference
                    for reference in coverage.get(obligation_id, {}).get("artifact_ids", [])
                    if registry.resolve(reference)["artifact_kind"] == "function"
                ],
                covered_function_roles(obligation_id),
            )
        ]

    def has_role(roles: list[str], markers: tuple[str, ...]) -> bool:
        return any(
            marker in set(re.findall(r"[a-z]+", role))
            for role in roles for marker in markers
        )

    role_deficits: list[str] = []
    runtime_roles = covered_function_texts("foundation:runtime_services")
    for required, markers in (
        ("startup", ("startup", "start", "init", "create")),
        ("run", ("run", "serve", "listen", "accept", "poll", "loop")),
        ("cleanup", ("cleanup", "destroy", "shutdown", "stop")),
    ):
        if not has_role(runtime_roles, markers):
            role_deficits.append(f"foundation:runtime_services missing {required} role")
    session_roles = covered_function_texts("foundation:session_access")
    for required, markers in (
        ("create", ("create",)),
        ("destroy", ("destroy", "cleanup", "remove")),
        ("lookup", ("lookup", "find", "get", "retrieve")),
        ("state_query", ("check", "query", "is")),
        ("mutation_or_access", ("add", "remove", "mark", "update", "set", "access", "check")),
    ):
        if not has_role(session_roles, markers):
            role_deficits.append(f"foundation:session_access missing {required} role")
    routing_roles = covered_function_texts("foundation:routing")
    for required, markers in (
        ("create", ("create",)),
        ("destroy", ("destroy", "cleanup")),
        ("match_or_registry", ("match", "route", "subscribe", "registry")),
    ):
        if routing_roles and not has_role(routing_roles, markers):
            role_deficits.append(f"foundation:routing missing {required} role")
    if "foundation:transport_accept_callback" in obligations:
        accept_entries = [
            registry.resolve(reference)
            for reference in coverage["foundation:transport_accept_callback"].get("artifact_ids", [])
        ]
        accept_roles = covered_function_roles("foundation:transport_accept_callback")
        if not any(entry["artifact_kind"] == "callback" for entry in accept_entries):
            role_deficits.append("foundation:transport_accept_callback missing callback type")
        if not any(
            "callback" in role and "accept" in role
            and any(marker in role for marker in ("consume", "register", "store"))
            for role in accept_roles
        ):
            role_deficits.append("foundation:transport_accept_callback missing consumer role")
        if not any(
            "callback" in role and "accept" in role
            and any(marker in role for marker in ("implement", "provide"))
            for role in accept_roles
        ):
            role_deficits.append("foundation:transport_accept_callback missing provider role")
    if "foundation:transport_output" in obligations:
        output_roles = covered_function_roles("foundation:transport_output")
        output_names = [
            registry.resolve(reference)["canonical_name"].lower()
            for reference in coverage["foundation:transport_output"].get("artifact_ids", [])
            if registry.resolve(reference)["artifact_kind"] == "function"
        ]
        if not any(
            any(
                marker in set(re.findall(r"[a-z]+", text))
                for marker in ("send", "queue", "write", "flush")
            )
            for text in [*output_roles, *output_names]
        ):
            role_deficits.append("foundation:transport_output missing send_or_queue role")
    if "foundation:transport_input_buffer" in obligations:
        input_roles = covered_function_texts("foundation:transport_input_buffer")
        for required, markers in (
            ("buffer_access", ("buffer", "read", "access")),
            ("buffer_consume", ("consume", "advance", "discard")),
            ("connection_identity", ("fd", "descriptor", "identity")),
        ):
            if not has_role(input_roles, markers):
                role_deficits.append(
                    f"foundation:transport_input_buffer missing {required} role"
                )

    runtime_entrypoint = inventory.get("runtime_entrypoint", {})
    if isinstance(runtime_entrypoint, dict):
        for field, required, markers in (
            ("startup_services", "startup", ("startup", "start", "init", "create")),
            ("run_services", "run", ("run", "serve", "listen", "accept", "poll", "loop")),
            ("cleanup_services", "cleanup", ("cleanup", "destroy", "shutdown", "stop")),
        ):
            roles = [
                f"{str(functions_by_id[registry.resolve(value, expected_kinds={'function'})['artifact_id']].get('role', '')).lower()} "
                f"{registry.resolve(value, expected_kinds={'function'})['canonical_name'].lower()}"
                for value in runtime_entrypoint.get(field, [])
                if registry.resolve(value, expected_kinds={"function"})["artifact_id"] in functions_by_id
            ]
            if not roles or not has_role(roles, markers):
                role_deficits.append(f"runtime_entrypoint.{field} missing {required} role")

    registered_type_stems = {
        re.sub(r"^mqtt_|_t$", "", entry["canonical_name"].lower())
        for entry in registry.typed_view({"type"})
    }
    for function_id, item in functions_by_id.items():
        name = registry.resolve(function_id)["canonical_name"].lower()
        role = str(item.get("role", "")).lower()
        if "_get_" not in name or "accessor" not in role or "handle" not in role:
            continue
        stem = name.rsplit("_get_", 1)[1]
        if not any(candidate == stem or candidate.endswith(f"_{stem}") for candidate in registered_type_stems):
            role_deficits.append(f"{name} accessor missing registered {stem} type")

    outstanding_suffix = "".join((
        f"; outstanding_kind_deficits={kind_deficits}" if kind_deficits else "",
        f"; outstanding_role_deficits={role_deficits}" if role_deficits else "",
    ))
    callback_coverage = coverage.get("foundation:callback_provider")
    if isinstance(callback_coverage, dict):
        callback_entries = [registry.resolve(value) for value in callback_coverage.get("artifact_ids", [])]
        callback_types = [entry for entry in callback_entries if entry["artifact_kind"] == "callback"]
        covered_callback_ids = {entry["artifact_id"] for entry in callback_types}
        uncovered_callbacks = sorted(
            entry["canonical_name"] for entry in registry.typed_view({"callback"})
            if entry["artifact_id"] not in covered_callback_ids
        )
        covered_functions = [
            functions_by_id[entry["artifact_id"]]
            for entry in callback_entries
            if entry["artifact_kind"] == "function" and entry["artifact_id"] in functions_by_id
        ]
        consumers = [
            item for item in covered_functions
            if "callback" in str(item.get("role", "")).lower()
            and any(marker in str(item.get("role", "")).lower() for marker in ("consume", "register", "accept", "store"))
        ]
        providers = [
            item for item in covered_functions
            if "callback" in str(item.get("role", "")).lower()
            and any(marker in str(item.get("role", "")).lower() for marker in ("provide", "implement"))
        ]
        if uncovered_callbacks or not callback_types or not consumers or not providers:
            raise ValueError(
                "stage4_callback_role_coverage_missing: callback, consumer, and provider roles are required"
                + (f"; uncovered_callbacks={uncovered_callbacks}" if uncovered_callbacks else "")
                + outstanding_suffix
            )
        consumer_owner_ids = {
            registry.resolve(
                item.get("symbol") or item.get("name") or item.get("function_id"),
                expected_kinds={"function"},
            )["owner_file_id"]
            for item in consumers
        }
        if any(callback["owner_file_id"] not in consumer_owner_ids for callback in callback_types):
            raise ValueError(
                "stage4_callback_owner_consumer_mismatch: callback must be owned by its consumer file"
                + outstanding_suffix
            )

    if kind_deficits:
        kind_deficit_message = f"stage4_obligation_kind_missing: {'; '.join(kind_deficits)}"
    else:
        kind_deficit_message = ""

    if role_deficits:
        suffix = f"; outstanding_kind_deficits={kind_deficits}" if kind_deficits else ""
        raise ValueError(
            f"stage4_role_coverage_missing: {'; '.join(role_deficits)}{suffix}"
        )
    if kind_deficit_message:
        raise ValueError(kind_deficit_message)

    test_expected = {
        obligation_id for obligation_id, item in obligations.items() if item.get("requires_test")
    }
    tests = inventory.get("test_obligations")
    if not isinstance(tests, list):
        raise ValueError("stage4_test_obligations_missing: test_obligations must be an array")
    test_actual = {
        str(item.get("obligation_id"))
        for item in tests
        if isinstance(item, dict) and item.get("obligation_id") and _support_refs(item)
    }
    if len(tests) != len(test_actual) or test_actual != test_expected:
        raise ValueError(
            f"stage4_test_obligation_mismatch: missing={sorted(test_expected - test_actual)}, extra={sorted(test_actual - test_expected)}"
        )

    runtime = inventory.get("runtime_entrypoint")
    if not isinstance(runtime, dict) or not _support_refs(runtime):
        raise ValueError("stage4_runtime_entrypoint_invalid: grounded runtime_entrypoint is required")
    main = registry.resolve(runtime.get("main_function"), expected_kinds={"function"})
    if main["canonical_name"] != "main":
        raise ValueError("stage4_runtime_main_identity_invalid: runtime_entrypoint must reference main")
    owner = registry.resolve(runtime.get("owner_file"), expected_kinds={"file"})
    if owner["artifact_id"] != main["owner_file_id"]:
        raise ValueError("stage4_runtime_owner_drift: main owner differs from runtime_entrypoint owner_file")
    raw_main_count = sum(
        str(item.get("symbol") or item.get("name") or item.get("function_id")) == "main"
        for item in inventory.get("functions", [])
        if isinstance(item, dict)
    )
    if raw_main_count != 1 or sum(item["canonical_name"] == "main" for item in registry.typed_view({"function"})) != 1:
        raise ValueError("stage4_runtime_main_count_invalid: exactly one main identity is required")
    for field in ("startup_services", "run_services", "cleanup_services"):
        services = runtime.get(field)
        if not isinstance(services, list) or not services:
            raise ValueError(f"stage4_runtime_service_missing: {field}")
        for service in services:
            registry.resolve(service, expected_kinds={"function"})
    cleanup_ids = {
        registry.resolve(service, expected_kinds={"function"})["artifact_id"]
        for service in runtime["cleanup_services"]
    }

    lifecycle = inventory.get("lifecycle_matrix")
    if not isinstance(lifecycle, list) or not lifecycle:
        raise ValueError("stage4_lifecycle_matrix_empty: at least one owned resource lifecycle is required")
    lifecycle_destroy_ids: set[str] = set()
    for index, item in enumerate(lifecycle):
        if not isinstance(item, dict) or not _support_refs(item):
            raise ValueError(f"stage4_lifecycle_grounding_missing: lifecycle_matrix[{index}]")
        try:
            registry.resolve(item.get("type_id"), expected_kinds={"type"})
            create = registry.resolve(item.get("create_function"), expected_kinds={"function"})
            destroy = registry.resolve(item.get("destroy_function"), expected_kinds={"function"})
        except (RegistryInvariantError, ValueError) as exc:
            raise ValueError(
                f"stage4_lifecycle_identity_invalid: lifecycle_matrix[{index}] must use exact type/create/destroy identities"
            ) from exc
        mutation = re.compile(
            r"(?:^|_)(?:add|remove|subscribe|unsubscribe|lookup|match|mark|update|set|feed|flush)(?:_|$)"
        )
        if mutation.search(create["canonical_name"]) or mutation.search(destroy["canonical_name"]):
            raise ValueError(
                f"stage4_lifecycle_mutator_misclassified: lifecycle_matrix[{index}] uses mutation instead of direct create/destroy"
            )
        destroy_id = destroy["artifact_id"]
        lifecycle_destroy_ids.add(destroy_id)
        uses = item.get("use_functions")
        if not isinstance(uses, list) or not uses:
            raise ValueError(f"stage4_lifecycle_use_missing: lifecycle_matrix[{index}]")
        for function in uses:
            registry.resolve(function, expected_kinds={"function"})
    if not cleanup_ids.intersection(lifecycle_destroy_ids):
        raise ValueError(
            "stage4_lifecycle_destroy_not_in_cleanup: runtime cleanup must include at least one top-level lifecycle destroy function"
        )

    inventory_constants = {
        str(item.get("symbol") or item.get("name"))
        for item in inventory.get("constants_or_macros", [])
        if isinstance(item, dict)
    }
    registry_constants = {item["canonical_name"] for item in registry.typed_view({"constant"})}
    if not inventory_constants or inventory_constants != registry_constants:
        raise ValueError("stage4_constant_registry_drift: required constants must match canonical registry")
    for item in inventory.get("constants_or_macros", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or item.get("name") or "")
        value_present = "value" in item or "VALUE" in item
        value = item.get("value", item.get("VALUE"))
        if not value_present or value is None:
            raise ValueError(f"fact_gap_wire_constant_value_missing: {symbol}")
        fact_refs = [
            str(ref)
            for key in ("fact_refs", "trace_refs")
            for ref in (item.get(key, []) if isinstance(item.get(key), list) else [])
            if str(ref).strip()
        ]
        if not fact_refs:
            raise ValueError(f"fact_gap_wire_constant_grounding_missing: {symbol}")


def _validate_type_definition_semantics(
    designs: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
    *,
    require_grounding: bool = True,
) -> None:
    from .compiler import _STANDARD_TYPES, _lower_type_spec

    lowered: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for design in designs:
        entry = registry.resolve(design.get("id"), expected_kinds={"type", "callback"})
        spec = _lower_type_spec({**design, "name": entry["canonical_name"]})
        kind = str(spec.get("TYPE_KIND", "")).upper()
        if entry["artifact_kind"] == "callback" and kind != "CALLBACK":
            raise ValueError(f"stage5_callback_kind_invalid: {entry['artifact_id']}")
        if kind == "ENUM":
            values = spec.get("ENUM_VALUES")
            source_spec = design.get("type_spec") if isinstance(design.get("type_spec"), dict) else {}
            source_values = design.get("values", design.get("enum_values", source_spec.get("ENUM_VALUES", [])))
            if not isinstance(values, list) or not values:
                raise ValueError(f"stage5_enum_values_empty: {entry['artifact_id']}")
            names = [str(item.get("NAME", "")) for item in values if isinstance(item, dict)]
            raw_values = [repr(item.get("VALUE")) for item in values if isinstance(item, dict)]
            if len(names) != len(values) or any(not name for name in names) or len(names) != len(set(names)):
                raise ValueError(f"stage5_enum_names_invalid: {entry['artifact_id']}")
            if any(item.get("VALUE") is None for item in values) or len(raw_values) != len(set(raw_values)):
                raise ValueError(f"stage5_enum_values_invalid: {entry['artifact_id']}")
            if any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item.get("NAME", "")))
                or isinstance(item.get("VALUE"), bool)
                or not isinstance(item.get("VALUE"), (str, int))
                or not str(item.get("ROLE", "")).strip()
                for item in values
            ):
                raise ValueError(f"stage5_enum_schema_invalid: {entry['artifact_id']}")
            if require_grounding:
                enum_value_refs = design.get("enum_value_refs", {})
                for index, value in enumerate(source_values if isinstance(source_values, list) else []):
                    name = str(value.get("NAME") or value.get("name") or "") if isinstance(value, dict) else ""
                    mapped_refs = enum_value_refs.get(name, []) if isinstance(enum_value_refs, dict) else []
                    if (
                        not isinstance(value, dict)
                        or not (_support_refs(value) or (isinstance(mapped_refs, list) and any(str(ref).strip() for ref in mapped_refs)))
                    ):
                        raise ValueError(f"stage5_enum_grounding_missing: {entry['artifact_id']}[{index}]")
        if kind in {"STRUCT", "UNION"}:
            members = spec.get("FIELDS" if kind == "STRUCT" else "VARIANTS", [])
            member_names = [str(item.get("NAME", "")) for item in members if isinstance(item, dict)]
            if len(member_names) != len(members) or any(not name for name in member_names) or len(member_names) != len(set(member_names)):
                raise ValueError(f"stage5_member_names_invalid: {entry['artifact_id']}")
            for member in members:
                for type_name in c_type_references(str(member.get("TYPE", ""))):
                    if type_name not in _STANDARD_TYPES and not is_system_type(type_name):
                        referenced = registry.resolve(type_name, expected_kinds={"type", "callback"})
                        if entry["visibility"] == "public" and referenced["visibility"] != "public":
                            raise ValueError(
                                f"stage5_private_type_leak: {entry['artifact_id']} uses {referenced['artifact_id']}"
                            )
        if kind == "CALLBACK":
            signature = str(spec.get("CALLBACK_SIGNATURE", ""))
            callback_match = re.fullmatch(
                r"(?P<return>.+)\(\s*\*\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\((?P<params>.*)\)",
                signature,
            )
            if (
                callback_match is None
                or callback_match.group("name") != entry["canonical_name"]
                or not callback_match.group("params").strip()
            ):
                raise ValueError(f"stage5_callback_signature_invalid: {entry['artifact_id']}")
            for type_name in c_type_references(signature):
                if type_name not in _STANDARD_TYPES and not is_system_type(type_name) and type_name != entry["canonical_name"]:
                    referenced = registry.resolve(type_name, expected_kinds={"type", "callback"})
                    if entry["visibility"] == "public" and referenced["visibility"] != "public":
                        raise ValueError(
                            f"stage5_private_type_leak: {entry['artifact_id']} uses {referenced['artifact_id']}"
                        )
        if kind == "OPAQUE":
            boundaries = design.get("opaque_boundaries")
            if (
                not str(design.get("ownership_model", "")).strip()
                or not isinstance(boundaries, dict)
                or any(not str(boundaries.get(key, "")).strip() for key in ("create", "destroy"))
            ):
                raise ValueError(f"stage5_opaque_boundary_missing: {entry['artifact_id']}")
        lowered.append((design, entry, spec))

    opaque_names = {
        entry["canonical_name"] for _, entry, spec in lowered if spec.get("TYPE_KIND") == "OPAQUE"
    }
    for _, entry, spec in lowered:
        if spec.get("TYPE_KIND") in {"STRUCT", "UNION"}:
            members = spec.get("FIELDS" if spec["TYPE_KIND"] == "STRUCT" else "VARIANTS", [])
            for member in members:
                c_type = str(member.get("TYPE", "")) if isinstance(member, dict) else ""
                for opaque in opaque_names:
                    if re.search(rf"\b{re.escape(opaque)}\b", c_type) and "*" not in c_type:
                        raise ValueError(
                            f"stage5_opaque_by_value_member: {entry['artifact_id']} uses {opaque}"
                        )
        if spec.get("TYPE_KIND") != "CALLBACK":
            continue
        signature = str(spec.get("CALLBACK_SIGNATURE", ""))
        match = re.fullmatch(r"(?P<return>.+?)\(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*\((?P<params>.*)\)", signature)
        if match is None:
            continue
        slots = [match.group("return"), *[item.strip() for item in match.group("params").split(",")]]
        for opaque in opaque_names:
            if any(re.search(rf"\b{re.escape(opaque)}\b", slot) and "*" not in slot for slot in slots):
                raise ValueError(f"stage5_opaque_by_value_callback: {entry['artifact_id']} uses {opaque}")


def _validate_function_interface_abi(
    artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
    context: dict[str, Any],
) -> None:
    from .compiler import _STANDARD_TYPES, _lower_type_spec, _parse_param, _signature, _split_params

    type_designs = _canonicalize_registry_types(
        _stage_artifact(artifacts, "type_and_access_path_design"), registry
    )
    type_specs = {
        item["name"]: _lower_type_spec(item) for item in type_designs
    }
    type_designs_by_name = {item["name"]: item for item in type_designs}
    opaque_names = {name for name, spec in type_specs.items() if spec.get("TYPE_KIND") == "OPAQUE"}
    canonical, _ = _canonical_function_indexes(
        _stage_artifact(artifacts, "function_interface_design"), registry
    )
    def normalized_type(value: Any) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\s*\*\s*", "*", str(value).strip()))

    for function_id, item in canonical.items():
        raw_signature = item.get("signature")
        signature = _signature(raw_signature, function_id)
        if isinstance(raw_signature, dict):
            parsed = _signature(str(raw_signature.get("RAW", "")), function_id)

            structured_params = [
                (normalized_type(param.get("TYPE")), str(param.get("NAME", "")))
                for param in signature.get("PARAMS", [])
                if isinstance(param, dict)
            ]
            parsed_params = [
                (normalized_type(param.get("TYPE")), str(param.get("NAME", "")))
                for param in parsed.get("PARAMS", [])
                if isinstance(param, dict)
            ]
            if normalized_type(signature.get("RETURN")) != normalized_type(parsed.get("RETURN")) or structured_params != parsed_params:
                raise ValueError(f"stage6_signature_structure_drift: {function_id}")
        raw_params = signature.get("PARAMS")
        if signature.get("PARAMS") and (
            not isinstance(raw_params, list)
            or any(
                not isinstance(param, dict)
                or "NULLABLE" not in param
                or "OWNERSHIP" not in param
                or not str(param.get("OWNERSHIP", "")).strip()
                for param in raw_params
            )
        ):
            raise ValueError(f"stage6_parameter_contract_missing: {function_id}")
        slots = [
            str(signature.get("RETURN", "")),
            *[str(param.get("TYPE", "")) for param in signature.get("PARAMS", []) if isinstance(param, dict)],
        ]
        for c_type in slots:
            for type_name in c_type_references(c_type):
                if type_name in _STANDARD_TYPES or is_system_type(type_name):
                    continue
                type_entry = registry.resolve(type_name, expected_kinds={"type", "callback"})
                if type_name in opaque_names and "*" not in c_type:
                    raise ValueError(f"stage6_opaque_by_value: {function_id} uses {type_name}")
                if item.get("visibility") == "public" and type_entry["owner_file_id"] != item.get("file") and type_entry["visibility"] != "public":
                    raise ValueError(f"stage6_private_foreign_type: {function_id} uses {type_name}")

    inventory = _stage_artifact(artifacts, "public_artifact_inventory")
    coverage_wire_function_ids = _wire_obligation_function_ids(inventory, registry)
    explicit_wire_function_ids = {
        function_id
        for function_id, item in canonical.items()
        if isinstance(item.get("wire_obligation"), dict)
        and item["wire_obligation"].get("required") is True
    }
    wire_function_ids = coverage_wire_function_ids | explicit_wire_function_ids
    expected_targets = {
        str(item.get("requirement_id")): item
        for item in context.get("required_wire_mapping_targets", [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    if wire_function_ids and not expected_targets:
        raise ValueError("fact_gap_wire_mapping_targets_missing: message facts do not ground codec responsibilities")
    covered_targets: set[str] = set()
    for function_id in wire_function_ids:
        obligation = canonical[function_id].get("wire_obligation")
        targets = obligation.get("targets") if isinstance(obligation, dict) else None
        if (
            not isinstance(obligation, dict)
            or obligation.get("required") is not True
            or not (_support_refs(obligation) or any(_support_refs(target) for target in targets or [] if isinstance(target, dict)))
        ):
            raise ValueError(f"stage6_wire_obligation_missing: {function_id}")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"stage6_wire_targets_missing: {function_id}")
        local_ids: set[str] = set()
        output_packet = _encoder_wire_packet_name(canonical[function_id])
        for target in targets:
            requirement_id = str(target.get("requirement_id", "")) if isinstance(target, dict) else ""
            expected = expected_targets.get(requirement_id)
            if expected is None:
                raise ValueError(f"stage6_wire_target_unknown: {function_id}:{requirement_id}")
            if requirement_id in local_ids:
                raise ValueError(f"stage6_wire_target_duplicate: {function_id}:{requirement_id}")
            packet_matches = str(target.get("packet", "")) == str(expected.get("packet", "")) or (
                bool(output_packet)
                and str(expected.get("packet", "")).lower() != "fixed_header"
                and str(target.get("packet", "")) == output_packet
            )
            if (
                not packet_matches
                or any(str(target.get(key, "")) != str(expected.get(key, "")) for key in ("wire_field", "rule"))
                or set(map(str, target.get("fact_refs", []))) != set(map(str, expected.get("fact_refs", [])))
            ):
                raise ValueError(f"stage6_wire_target_drift: {function_id}:{requirement_id}")
            local_ids.add(requirement_id)
        covered_targets.update(local_ids)
    if covered_targets != set(expected_targets):
        raise ValueError(
            f"stage6_wire_target_coverage: missing={sorted(set(expected_targets) - covered_targets)}, "
            f"extra={sorted(covered_targets - set(expected_targets))}"
        )

    invalid_encoder_targets: list[str] = []
    for function_id in wire_function_ids:
        item = canonical[function_id]
        signature = _signature(item.get("signature"), function_id)
        semantic_role = f"{item.get('role', '')} {signature.get('NAME', '')}".lower()
        if not any(marker in semantic_role for marker in ("encode", "encoder", "serialize", "serializer")):
            continue
        source_fields: set[str] = set()
        for param in signature.get("PARAMS", []):
            if not isinstance(param, dict):
                continue
            if not str(param.get("ROLE", "")).lower().startswith("output"):
                source_fields.add(str(param.get("NAME", "")))
            for type_name in c_type_references(str(param.get("TYPE", ""))):
                spec = type_specs.get(type_name, {})
                if spec.get("TYPE_KIND") == "STRUCT":
                    source_fields.update(
                        str(field.get("NAME"))
                        for field in spec.get("FIELDS", []) if isinstance(field, dict)
                    )
        obligation = item.get("wire_obligation", {})
        for target in obligation.get("targets", []) if isinstance(obligation, dict) else []:
            if not isinstance(target, dict) or str(target.get("packet", "")).lower() == "fixed_header":
                continue
            wire_field = str(target.get("wire_field", ""))
            if wire_field not in source_fields:
                invalid_encoder_targets.append(
                    f"{function_id}:{target.get('requirement_id', '')}:{wire_field}"
                )
    if invalid_encoder_targets:
        raise ValueError(
            f"stage6_encoder_wire_source_missing: {sorted(invalid_encoder_targets)}"
        )

    callback_coverage = next(
        (
            item for item in inventory.get("implementation_coverage_matrix", [])
            if isinstance(item, dict) and item.get("obligation_id") == "foundation:callback_provider"
        ),
        {},
    )
    callback_entries = [
        registry.resolve(reference)
        for reference in callback_coverage.get("artifact_ids", [])
    ]
    callback_names = {
        entry["canonical_name"] for entry in callback_entries if entry["artifact_kind"] == "callback"
    }
    coverage_callback_function_ids = {
        entry["artifact_id"] for entry in callback_entries if entry["artifact_kind"] == "function"
    }
    canonical_signatures = {
        function_id: _signature(item.get("signature"), function_id)
        for function_id, item in canonical.items()
    }
    closed_callbacks: set[str] = set()
    for callback_name in callback_names:
        callback_spec = type_specs.get(callback_name, {})
        match = re.fullmatch(
            r"(?P<return>.+?)\(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*\((?P<params>.*)\)",
            str(callback_spec.get("CALLBACK_SIGNATURE", "")),
        )
        if match is None:
            continue
        expected_return = normalized_type(match.group("return"))
        expected_params = [
            normalized_type(_parse_param(value).get("TYPE"))
            for value in _split_params(match.group("params"))
        ]
        consumer_ids = {
            function_id
            for function_id, signature in canonical_signatures.items()
            if callback_name in {
                normalized_type(param.get("TYPE"))
                for param in signature.get("PARAMS", []) if isinstance(param, dict)
            }
        }
        has_consumer = bool(consumer_ids)
        for consumer_id in consumer_ids:
            consumer_param_types = {
                normalized_type(param.get("TYPE"))
                for param in canonical_signatures[consumer_id].get("PARAMS", [])
                if isinstance(param, dict)
            }
            consumer_owner = registry.resolve(
                consumer_id, expected_kinds={"function"}
            )["owner_file_id"]
            forwarded_context_types: set[str] = {
                "void*" for value in expected_params if value in {"void*", "const void*"}
            }
            for value in expected_params:
                custom_names = {
                    type_name
                    for type_name in c_type_references(value)
                    if type_name not in _STANDARD_TYPES and not is_system_type(type_name)
                }
                for custom_name in custom_names:
                    type_owner = registry.resolve(
                        custom_name, expected_kinds={"type", "callback"}
                    )["owner_file_id"]
                    if type_owner != consumer_owner and value.endswith("*"):
                        forwarded_context_types.add(value)
            missing_context = sorted(forwarded_context_types - consumer_param_types)
            if missing_context:
                raise ValueError(
                    f"stage6_callback_consumer_context_missing: {consumer_id}:{missing_context}"
                )
        callback_owner = registry.resolve(callback_name, expected_kinds={"callback"})["owner_file_id"]
        if has_consumer and not any(
            registry.resolve(function_id, expected_kinds={"function"})["owner_file_id"] == callback_owner
            for function_id in consumer_ids
        ):
            raise ValueError(f"stage6_callback_owner_consumer_mismatch: {callback_name}")
        hinted_provider_ids: set[str] = set()
        for reference in type_designs_by_name.get(callback_name, {}).get("access_paths", []):
            try:
                hinted_provider_ids.add(
                    registry.resolve(reference, expected_kinds={"function"})["artifact_id"]
                )
            except ValueError:
                continue
        provider_ids = hinted_provider_ids or coverage_callback_function_ids
        has_provider = any(
            function_id in provider_ids
            and normalized_type(signature.get("RETURN")) == expected_return
            and [
                normalized_type(param.get("TYPE"))
                for param in signature.get("PARAMS", []) if isinstance(param, dict)
            ] == expected_params
            for function_id, signature in canonical_signatures.items()
        )
        if has_consumer and has_provider:
            closed_callbacks.add(callback_name)
    if callback_names != closed_callbacks:
        raise ValueError(
            f"stage6_callback_provider_abi_mismatch: missing={sorted(callback_names - closed_callbacks)}"
        )

    for lifecycle in inventory.get("lifecycle_matrix", []):
        if not isinstance(lifecycle, dict):
            continue
        type_name = registry.resolve(lifecycle.get("type_id"), expected_kinds={"type"})["canonical_name"]
        create_id = registry.resolve(lifecycle.get("create_function"), expected_kinds={"function"})["artifact_id"]
        destroy_id = registry.resolve(lifecycle.get("destroy_function"), expected_kinds={"function"})["artifact_id"]
        create_signature = _signature(canonical[create_id].get("signature"), create_id)
        destroy_signature = _signature(canonical[destroy_id].get("signature"), destroy_id)
        if not re.search(rf"\b{re.escape(type_name)}\b", str(create_signature.get("RETURN", ""))) or "*" not in str(create_signature.get("RETURN", "")):
            raise ValueError(f"stage6_constructor_handle_mismatch: {create_id}")
        destroy_types = [str(item.get("TYPE", "")) for item in destroy_signature.get("PARAMS", []) if isinstance(item, dict)]
        if not any(re.search(rf"\b{re.escape(type_name)}\b", value) and "*" in value for value in destroy_types):
            raise ValueError(f"stage6_destructor_handle_mismatch: {destroy_id}")


def _validate_function_interface_loader_safety(
    artifacts: list[dict[str, Any]], registry: CanonicalPlanningRegistry
) -> None:
    from .compiler import _STANDARD_TYPES, _signature

    canonical, _ = _canonical_function_indexes(
        _stage_artifact(artifacts, "function_interface_design"), registry
    )
    private_tags = {
        entry["canonical_name"].removesuffix("_t"): entry
        for entry in registry.typed_view({"type", "callback"})
        if entry["visibility"] != "public"
    }
    for function_id, item in canonical.items():
        signature = _signature(item.get("signature"), function_id)
        slots = [
            str(signature.get("RETURN", "")),
            *[
                str(param.get("TYPE", ""))
                for param in signature.get("PARAMS", [])
                if isinstance(param, dict)
            ],
        ]
        for type_name in {
            value
            for slot in slots
            for value in c_type_references(slot)
            if not value.startswith(("struct ", "union ", "enum "))
        }:
            if type_name not in _STANDARD_TYPES and not is_system_type(type_name):
                type_entry = registry.resolve(type_name, expected_kinds={"type", "callback"})
                if item.get("visibility") == "public" and type_entry["visibility"] != "public":
                    raise ValueError(
                        f"stage6_private_type_leak: {function_id} uses {type_entry['artifact_id']}"
                    )
        if item.get("visibility") == "public":
            for slot in slots:
                for tag in re.findall(r"\b(?:struct|union)\s+([A-Za-z_][A-Za-z0-9_]*)\b", slot):
                    if tag in private_tags:
                        raise ValueError(
                            f"stage6_private_type_leak: {function_id} uses {private_tags[tag]['artifact_id']}"
                        )


def _encoder_wire_packet_name(interface: dict[str, Any]) -> str:
    signature = interface.get("signature")
    name = str(signature.get("NAME", "")) if isinstance(signature, dict) else ""
    if not name:
        name = str(interface.get("function_id") or interface.get("id") or "").rsplit("/", 1)[-1]
    match = re.search(r"(?:^|_)encode_([a-z0-9_]+)$", name.lower())
    if match is None:
        return ""
    return re.sub(r"_qos\d+$", "", match.group(1)).upper()


def _canonicalize_encoder_wire_packets(artifact: dict[str, Any]) -> None:
    for interface in artifact.get("function_interfaces", []):
        if not isinstance(interface, dict):
            continue
        obligation = interface.get("wire_obligation")
        if not isinstance(obligation, dict) or not isinstance(obligation.get("targets"), list):
            continue
        output_packet = _encoder_wire_packet_name(interface)
        if output_packet:
            for target in obligation["targets"]:
                if isinstance(target, dict) and str(target.get("packet", "")).lower() != "fixed_header":
                    target["packet"] = output_packet


def _canonicalize_wire_target_copies(
    artifact: dict[str, Any], context: dict[str, Any]
) -> None:
    expected = {
        str(item.get("requirement_id")): item
        for item in context.get("required_wire_mapping_targets", [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    obligations: list[dict[str, Any]] = []
    for interface in artifact.get("function_interfaces", []):
        if not isinstance(interface, dict):
            continue
        obligation = interface.get("wire_obligation")
        if not isinstance(obligation, dict) or not isinstance(obligation.get("targets"), list):
            continue
        obligation["targets"] = [
            deepcopy(expected.get(str(target.get("requirement_id")), target))
            if isinstance(target, dict) else target
            for target in obligation["targets"]
        ]
        obligations.append(obligation)
    _canonicalize_encoder_wire_packets(artifact)
    assigned_ids = {
        str(target.get("requirement_id"))
        for obligation in obligations
        for target in obligation["targets"]
        if isinstance(target, dict)
    }
    owners_by_wire_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for obligation in obligations:
        for target in obligation["targets"]:
            if isinstance(target, dict):
                owners_by_wire_key.setdefault(
                    (str(target.get("packet", "")), str(target.get("wire_field", ""))), []
                ).append(obligation)
    for requirement_id, target in expected.items():
        if requirement_id in assigned_ids:
            continue
        owners = owners_by_wire_key.get(
            (str(target.get("packet", "")), str(target.get("wire_field", ""))), []
        )
        if owners:
            owners[0]["targets"].append(deepcopy(target))
            assigned_ids.add(requirement_id)
    missing_ids = [requirement_id for requirement_id in expected if requirement_id not in assigned_ids]
    if missing_ids and obligations:
        largest = max(len(obligation["targets"]) for obligation in obligations)
        leaders = [obligation for obligation in obligations if len(obligation["targets"]) == largest]
        if len(leaders) == 1 and largest >= max(2, len(expected) // 2):
            leaders[0]["targets"].extend(deepcopy(expected[requirement_id]) for requirement_id in missing_ids)


def _wire_obligation_function_ids(
    inventory: dict[str, Any], registry: CanonicalPlanningRegistry
) -> set[str]:
    function_ids: set[str] = set()
    for coverage in inventory.get("implementation_coverage_matrix", []):
        if not isinstance(coverage, dict) or coverage.get("obligation_id") != "foundation:codec":
            continue
        for reference in coverage.get("artifact_ids", []):
            entry = registry.resolve(reference)
            if entry["artifact_kind"] == "function":
                function_ids.add(entry["artifact_id"])
    return function_ids


def _validate_completed_stage(
    stage_id: str,
    artifacts: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    registry: CanonicalPlanningRegistry | None = None,
    allow_incomplete: bool = False,
    preserve_nonfatal: bool = False,
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
        if context.get("required_implementation_obligations") and not allow_incomplete:
            if registry is None:
                raise ValueError("stage4_registry_missing: public artifact inventory requires canonical registry")
            _normalize_stage4_callback_roles(inventory, registry)
            _validate_public_inventory_obligations(inventory, context, registry)
    elif stage_id == "type_and_access_path_design":
        if registry is not None:
            _validate_type_definition_semantics(
                _type_definition_designs(_stage_artifact(artifacts, stage_id), registry), registry
            )
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
                actual_values = [
                    registry.resolve(_stable_artifact_key(item, "function"), expected_kinds={"function"})["artifact_id"]
                    for item in _stage_artifact(artifacts, stage_id).get("function_interfaces", [])
                    if isinstance(item, dict)
                ]
                actual = set(actual_values)
            else:
                inventory = _stage_artifact(artifacts, "public_artifact_inventory")
                expected = {
                    str(item.get("function_id") or item.get("id") or item.get("symbol") or item.get("name"))
                    for item in inventory.get("functions", [])
                    if isinstance(item, dict)
                }
                actual_values = [
                    _stable_artifact_key(item, "function")
                    for item in _stage_artifact(artifacts, stage_id).get("function_interfaces", [])
                    if isinstance(item, dict)
                ]
                actual = set(actual_values)
            if (expected != actual or len(actual_values) != len(actual)) and not allow_incomplete:
                raise ValueError(
                    f"canonical_function_inventory_mismatch: missing={sorted(expected - actual)}, "
                    f"unknown={sorted(actual - expected)}, duplicate_count={len(actual_values) - len(actual)}"
                )
            if (
                registry is not None
                and context.get("required_implementation_obligations")
                and not preserve_nonfatal
            ):
                _validate_function_interface_loader_safety(artifacts, registry)
                _validate_function_interface_abi(artifacts, registry, context)
        if (
            stage_id == "function_test_vector_design"
            and registry is not None
            and context.get("required_implementation_obligations")
            and not allow_incomplete
        ):
            test_stage = _stage_artifact(artifacts, stage_id)
            vectors = test_stage.get("function_test_vectors")
            expected_functions = {
                item["artifact_id"] for item in registry.typed_view({"function"})
            }
            if not isinstance(vectors, dict):
                raise ValueError("stage9_function_test_vectors_invalid: mapping is required")
            resolved_vectors = {
                registry.resolve(key, expected_kinds={"function"})["artifact_id"]: value
                for key, value in vectors.items()
            }
            missing = expected_functions - set(resolved_vectors)
            empty = {
                function_id
                for function_id, value in resolved_vectors.items()
                if not isinstance(value, list) or not value
            }
            if missing or empty:
                raise ValueError(
                    f"stage9_function_test_coverage: missing={sorted(missing)}, empty={sorted(empty)}"
                )
            if not isinstance(test_stage.get("runtime_test_vectors"), list) or not test_stage["runtime_test_vectors"]:
                raise ValueError("stage9_runtime_test_vectors_empty: runtime coverage is required")
            if test_stage.get("test_vector_diagnostics"):
                raise ValueError("stage9_test_vector_diagnostics_unresolved: required tests cannot be deferred")
        _merge_function_artifacts(artifacts, registry, allow_incomplete=allow_incomplete)
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
    call_relations = _stage_artifact(previous_artifacts, "function_call_contract_closure")
    _normalize_grounded_runtime_literals(call_relations, context)
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
        if ("input" in vector or "INPUT" in vector) and ("expect" in vector or "EXPECT" in vector):
            runtime_vectors.append(deepcopy(vector))
        else:
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
    file_vectors = tests.get("file_test_vectors", {})
    if isinstance(file_vectors, dict):
        for file_item in files:
            keys = [
                file_item.get("id"), file_item.get("trace_id"),
                file_item.get("header_path"), file_item.get("source_path"),
            ]
            vectors = next((file_vectors[key] for key in keys if key in file_vectors), [])
            if isinstance(vectors, list):
                file_item["test_vectors"] = [*file_item.get("test_vectors", []), *deepcopy(vectors)]
    functions = _merge_function_artifacts(
        previous_artifacts, registry, allow_incomplete=allow_incomplete
    )
    constants_or_macros = [
        deepcopy(item) for item in public_inventory.get("constants_or_macros", []) if isinstance(item, dict)
    ]
    mapping = [
        *[
            {"plan_id": item["id"], "spec_kind": "PROTOCOL_MODULE_SPEC.MODULES", "spec_key": item["name"], "lowering_rule": "deterministic stage reconciliation"}
            for item in modules
        ],
        *[
            {"plan_id": str(item.get("id", "")), "spec_kind": "FILE_SPEC", "spec_key": str(item.get("trace_id", item.get("id", ""))), "lowering_rule": "deterministic stage reconciliation"}
            for item in files
        ],
        *[
            {
                "plan_id": str(item.get("id") or item.get("symbol") or item.get("name") or ""),
                "spec_kind": "FILE_SPEC.DATA",
                "spec_key": str(item.get("symbol") or item.get("name") or ""),
                "lowering_rule": "deterministic Stage 4 artifact lowering",
            }
            for item in constants_or_macros
        ],
    ]
    return {
        "schema_version": "specforge_planning_ir_v1",
        "protocol": protocol,
        "modules": modules,
        "files": files,
        "types": types,
        "constants_or_macros": constants_or_macros,
        "functions": functions,
        "required_implementation_obligations": deepcopy(context.get("required_implementation_obligations", [])),
        "implementation_coverage_matrix": deepcopy(public_inventory.get("implementation_coverage_matrix", [])),
        "test_obligations": deepcopy(public_inventory.get("test_obligations", [])),
        "lifecycle_matrix": deepcopy(public_inventory.get("lifecycle_matrix", [])),
        "runtime_entrypoint": deepcopy(public_inventory.get("runtime_entrypoint", {})),
        "callback_bindings": deepcopy(call_relations.get("callback_bindings", [])),
        "runtime_flow": deepcopy(call_relations.get("runtime_flow", {})),
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


def _function_behavior_partitions(
    previous_artifacts: list[dict[str, Any]],
    max_functions: int = 10,
    registry: CanonicalPlanningRegistry | None = None,
) -> list[dict[str, Any]]:
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

    wire_function_ids = (
        _wire_obligation_function_ids(public_inventory, registry) if registry is not None else set()
    )
    wire_targets_by_function: dict[str, list[dict[str, Any]]] = {}
    if registry is not None:
        interfaces = _stage_artifact(previous_artifacts, "function_interface_design")
        for interface in interfaces.get("function_interfaces", []):
            if not isinstance(interface, dict):
                continue
            function_id = registry.resolve(
                _stable_artifact_key(interface, "function"), expected_kinds={"function"}
            )["artifact_id"]
            obligation = interface.get("wire_obligation")
            if isinstance(obligation, dict) and isinstance(obligation.get("targets"), list):
                wire_targets_by_function[function_id] = deepcopy(obligation["targets"])
                if obligation.get("required") is True and obligation["targets"]:
                    wire_function_ids.add(function_id)
    partitions: list[dict[str, Any]] = []
    by_module: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}
    for (module, owner), items in grouped.items():
        by_module.setdefault(module, []).append((owner, items))

    def append_partition(partition_id: str, module: str, owner: str, items: list[dict[str, Any]]) -> None:
        partition_wire_ids = sorted(
            wire_function_ids.intersection(_registry_function_ids(items, registry))
        )
        partitions.append(
            {
                "partition_id": _safe_partition_id(partition_id),
                "module": module,
                "owner_file": owner,
                "functions": items,
                "wire_function_ids": partition_wire_ids,
                "wire_targets_by_function": {
                    function_id: deepcopy(wire_targets_by_function.get(function_id, []))
                    for function_id in partition_wire_ids
                },
            }
        )

    for module, owner_groups in sorted(by_module.items()):
        module_functions = [function for _, items in owner_groups for function in items]
        if len(module_functions) <= max_functions:
            append_partition(module, module, "", module_functions)
            continue
        for owner, items in sorted(owner_groups):
            append_partition(f"{module}_{owner or 'file'}", module, owner, items)
    return partitions or [
        {
            "partition_id": "all_functions",
            "module": "",
            "owner_file": "",
            "functions": functions,
            "wire_function_ids": sorted(wire_function_ids),
            "wire_targets_by_function": deepcopy(wire_targets_by_function),
        }
    ]


def _registry_function_ids(
    functions: list[dict[str, Any]], registry: CanonicalPlanningRegistry | None
) -> set[str]:
    ids: set[str] = set()
    for function in functions:
        reference = str(
            function.get("function_id")
            or function.get("id")
            or function.get("symbol")
            or function.get("name")
            or ""
        )
        if registry is None:
            ids.add(reference)
        else:
            ids.add(registry.resolve(reference, expected_kinds={"function"})["artifact_id"])
    return {value for value in ids if value}


def _complete_wire_mapping(value: Any) -> bool:
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    required_upper = {"PACKET", "WIRE_FIELD", "STRATEGY"}
    required_lower = {"packet", "wire_field", "strategy"}
    return bool(items) and all(
        isinstance(item, dict)
        and (required_upper <= set(item) or required_lower <= set(item))
        and str(item.get("STRATEGY", item.get("strategy", "")))
        in {"store_in_field", "parse_and_skip", "reject_if_present"}
        and all(str(field).strip() for field in item.values())
        for item in items
    )


def _wire_mapping_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value if isinstance(value, list) else [value]) if isinstance(item, dict)]


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
    registry: CanonicalPlanningRegistry,
    allowed_function_ids: set[str] | None = None,
    max_callers: int = 8,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for entry in registry.typed_view({"function"}):
        if allowed_function_ids is not None and entry["artifact_id"] not in allowed_function_ids:
            continue
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
                    "main_function_id": next(
                        (
                            function_id
                            for function_id in chunk
                            if registry.resolve(function_id, expected_kinds={"function"})["canonical_name"] == "main"
                        ),
                        "",
                    ),
                }
            )
    if allowed_function_ids is not None:
        return partitions
    return partitions or [{"partition_id": "all_callers", "owner_module_id": "", "owner_file_id": "", "caller_function_ids": []}]


def _normalize_stage6_callback_data_flow_interfaces(
    artifact: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
) -> None:
    interfaces: dict[str, dict[str, Any]] = {}
    for interface in artifact.get("function_interfaces", []):
        if not isinstance(interface, dict):
            continue
        function_id = registry.resolve(
            interface.get("function_id") or interface.get("id"),
            expected_kinds={"function"},
        )["artifact_id"]
        interfaces[function_id] = interface
    inventory_functions: dict[str, dict[str, Any]] = {}
    for item in _stage_artifact(previous_artifacts, "public_artifact_inventory").get("functions", []):
        if not isinstance(item, dict):
            continue
        function_id = registry.resolve(
            item.get("symbol") or item.get("name") or item.get("function_id"),
            expected_kinds={"function"},
        )["artifact_id"]
        inventory_functions[function_id] = item
    for provider_id, provider_item in inventory_functions.items():
        provider_role = str(provider_item.get("role", "")).lower()
        if (
            provider_id not in interfaces
            or "data" not in provider_role
            or not any(marker in provider_role for marker in ("implement", "provide"))
        ):
            continue
        provider_signature = interfaces[provider_id].get("signature", {})
        if not isinstance(provider_signature, dict):
            continue
        propagated = [
            deepcopy(parameter)
            for parameter in provider_signature.get("PARAMS", [])
            if isinstance(parameter, dict)
            and str(parameter.get("ROLE", "")).lower() in {"input_buffer", "input_length"}
        ]
        if not propagated:
            continue
        provider_file = registry.resolve(provider_id)["owner_file_id"]
        targets = [
            function_id for function_id, item in inventory_functions.items()
            if registry.resolve(function_id)["owner_file_id"] == provider_file
            and "feed" in str(item.get("role", "")).lower()
            and any(marker in str(item.get("role", "")).lower() for marker in ("decode", "parse"))
            and function_id in interfaces
        ]
        if len(targets) != 1:
            continue
        target_signature = interfaces[targets[0]].get("signature", {})
        if not isinstance(target_signature, dict):
            continue
        parameters = target_signature.get("PARAMS")
        if not isinstance(parameters, list):
            continue
        existing_names = {
            str(parameter.get("NAME", "")) for parameter in parameters if isinstance(parameter, dict)
        }
        parameters.extend(
            parameter for parameter in propagated
            if str(parameter.get("NAME", "")) not in existing_names
        )
        parameter_text = ", ".join(
            f"{parameter.get('TYPE')} {parameter.get('NAME')}"
            for parameter in parameters if isinstance(parameter, dict)
        ) or "void"
        target_signature["RAW"] = (
            f"{target_signature.get('RETURN', 'void')} {target_signature.get('NAME', registry.resolve(targets[0])['canonical_name'])}"
            f"({parameter_text})"
        )


def _attach_required_callback_bindings(
    partitions: list[dict[str, Any]],
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
) -> None:
    inventory = _stage_artifact(previous_artifacts, "public_artifact_inventory")
    callbacks = [
        item for item in inventory.get("types", [])
        if isinstance(item, dict) and str(item.get("kind", "")).lower() == "callback"
    ]
    functions = [item for item in inventory.get("functions", []) if isinstance(item, dict)]
    obligations: list[dict[str, str]] = []
    for callback in callbacks:
        callback_id = registry.resolve(
            callback.get("symbol") or callback.get("name") or callback.get("id"),
            expected_kinds={"callback"},
        )["artifact_id"]
        callback_name = registry.resolve(callback_id)["canonical_name"]
        consumers = [
            item for item in functions
            if callback_name in str(item.get("role", ""))
            and any(marker in str(item.get("role", "")).lower() for marker in ("accept", "consume", "register"))
        ]
        providers = [
            item for item in functions
            if callback_name in str(item.get("role", ""))
            and any(marker in str(item.get("role", "")).lower() for marker in ("implement", "provide"))
        ]
        if len(consumers) != 1:
            continue
        consumer_id = registry.resolve(
            consumers[0].get("symbol") or consumers[0].get("name"),
            expected_kinds={"function"},
        )["artifact_id"]
        for provider in providers:
            provider_id = registry.resolve(
                provider.get("symbol") or provider.get("name"),
                expected_kinds={"function"},
            )["artifact_id"]
            obligations.append({
                "callback_type_id": callback_id,
                "consumer_function_id": consumer_id,
                "provider_function_id": provider_id,
            })
    for partition in partitions:
        partition["required_callback_bindings"] = []
    run_service_ids = {
        registry.resolve(value, expected_kinds={"function"})["artifact_id"]
        for value in inventory.get("runtime_entrypoint", {}).get("run_services", [])
    }
    by_consumer: dict[str, list[dict[str, str]]] = {}
    for obligation in obligations:
        by_consumer.setdefault(obligation["consumer_function_id"], []).append(obligation)
    for group in by_consumer.values():
        provider_ids = {item["provider_function_id"] for item in group}
        candidates = [
            partition for partition in partitions
            if provider_ids.intersection(map(str, partition.get("caller_function_ids", [])))
        ]
        if not candidates:
            continue
        owner = max(
            candidates,
            key=lambda partition: (
                len(provider_ids.intersection(map(str, partition.get("caller_function_ids", [])))),
                bool(run_service_ids.intersection(map(str, partition.get("caller_function_ids", [])))),
            ),
        )
        owner["required_callback_bindings"].extend(deepcopy(group))


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
        "callback_bindings": [],
        "runtime_flow": {},
        "artifact_requests": [],
        "call_diagnostics": [],
    }
    for item in partition_artifacts:
        artifact = item.get("artifact", {})
        if not isinstance(artifact, dict):
            continue
        for key in ("call_edges", "callback_bindings", "artifact_requests", "call_diagnostics"):
            value = artifact.get(key, [])
            if isinstance(value, list):
                merged[key].extend(value)
        flow = artifact.get("runtime_flow")
        if isinstance(flow, dict) and flow:
            if merged["runtime_flow"]:
                raise ValueError("stage8_runtime_flow_ambiguous: more than one partition emitted runtime_flow")
            merged["runtime_flow"] = deepcopy(flow)
    return merged


def _validate_partition_artifact(
    stage: PlanningStage,
    artifact: dict[str, Any],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry,
    context: dict[str, Any] | None = None,
    previous_artifacts: list[dict[str, Any]] | None = None,
) -> None:
    try:
        _validate_partition_artifact_unlayered(
            stage, artifact, partition, registry, context or {}, previous_artifacts or []
        )
    except LayeredValidationError:
        raise
    except ValueError as exc:
        raise LayeredValidationError(classify_partition_error(exc)) from exc


def _validate_partition_artifact_unlayered(
    stage: PlanningStage,
    artifact: dict[str, Any],
    partition: dict[str, Any],
    registry: CanonicalPlanningRegistry,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
) -> None:
    if stage.stage_id == "type_and_access_path_design":
        designs = _type_definition_designs(artifact, registry)
        actual = {registry.resolve(item["id"], expected_kinds={"type", "callback"})["artifact_id"] for item in designs}
        expected = set(partition.get("type_ids", []))
        if actual != expected:
            raise ValueError(
                f"typed_delta_partition_coverage: Stage 5 missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        _validate_type_definition_semantics(designs, registry)
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
        mappings_by_id: dict[str, list[dict[str, Any]]] = {}
        for behavior in artifact.get("function_behaviors", []):
            if not isinstance(behavior, dict):
                raise ValueError("typed_delta_invalid_shape: Stage 7 function behavior must be an object")
            forbidden = {"owner_file", "file", "name", "signature", "visibility", "artifact_kind"}.intersection(behavior)
            if forbidden:
                raise ValueError(f"typed_delta_modifies_canonical_identity: Stage 7 changes {sorted(forbidden)}")
            function_id = registry.resolve(
                behavior.get("function_id"), expected_kinds={"function"}
            )["artifact_id"]
            if function_id in actual:
                raise ValueError(f"typed_delta_duplicate_id: {function_id}")
            actual.add(function_id)
            if "wire_mapping" in behavior:
                if not _complete_wire_mapping(behavior["wire_mapping"]):
                    raise ValueError(f"stage7_wire_mapping_invalid: {function_id}")
                mappings_by_id.setdefault(function_id, []).extend(
                    _wire_mapping_items(behavior["wire_mapping"])
                )
        for mapping in artifact.get("wire_mappings", []):
            if not isinstance(mapping, dict):
                raise ValueError("typed_delta_invalid_shape: Stage 7 wire mapping must be an object")
            function_id = registry.resolve(
                mapping.get("function_id"), expected_kinds={"function"}
            )["artifact_id"]
            if function_id not in allowed_ids:
                raise ValueError(f"typed_delta_partition_escape: wire mapping {function_id}")
            value = mapping.get("wire_mapping", mapping.get("WIRE_MAPPING"))
            if not _complete_wire_mapping(value):
                raise ValueError(f"stage7_wire_mapping_invalid: {function_id}")
            mappings_by_id.setdefault(function_id, []).extend(_wire_mapping_items(value))
        if actual != allowed_ids:
            raise ValueError(
                f"typed_delta_partition_coverage: Stage 7 missing={sorted(allowed_ids - actual)}, extra={sorted(actual - allowed_ids)}"
            )
        required_wire_ids = set(map(str, partition.get("wire_function_ids", [])))
        if not required_wire_ids <= set(mappings_by_id):
            raise ValueError(
                f"stage7_wire_obligation_unfulfilled: missing={sorted(required_wire_ids - set(mappings_by_id))}"
            )
        from .compiler import _lower_type_spec, _signature

        targets_by_function = partition.get("wire_targets_by_function", {})
        typed_wire_targets: set[str] = set()
        for design in _canonicalize_registry_types(
            _stage_artifact(previous_artifacts, "type_and_access_path_design"), registry,
            allow_missing=True,
        ):
            spec = _lower_type_spec(design)
            if spec.get("TYPE_KIND") == "STRUCT":
                typed_wire_targets.update(
                    f"{design['name']}.{field['NAME']}"
                    for field in spec.get("FIELDS", [])
                    if isinstance(field, dict) and field.get("NAME")
                )
        typed_output_targets: dict[str, set[str]] = {}
        for interface in _stage_artifact(
            previous_artifacts, "function_interface_design"
        ).get("function_interfaces", []):
            if not isinstance(interface, dict):
                continue
            function_id = registry.resolve(
                interface.get("function_id"), expected_kinds={"function"}
            )["artifact_id"]
            signature = _signature(interface.get("signature"), function_id)
            typed_output_targets[function_id] = {
                str(param.get("NAME"))
                for param in signature.get("PARAMS", [])
                if isinstance(param, dict)
                and "*" in str(param.get("TYPE", ""))
                and str(param.get("ROLE", "")).lower().startswith("output")
                and param.get("NAME")
            }
        for mappings in mappings_by_id.values():
            for mapping in mappings:
                if str(mapping.get("STRATEGY", mapping.get("strategy", ""))) != "store_in_field":
                    continue
                target_key = "TARGET" if "TARGET" in mapping else "target"
                target = str(mapping.get(target_key, "")).strip()
                if target in typed_wire_targets:
                    continue
                cast_path = re.fullmatch(
                    r"\(\(\s*(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s*\*\s*\)\s*"
                    r"[A-Za-z_][A-Za-z0-9_]*\s*\)->(?P<field>[A-Za-z_][A-Za-z0-9_]*)",
                    target,
                )
                if cast_path is not None:
                    canonical = f"{cast_path.group('type')}.{cast_path.group('field')}"
                    if canonical in typed_wire_targets:
                        mapping[target_key] = canonical
                        continue
                match = re.fullmatch(
                    r"(?P<base>[A-Za-z_][A-Za-z0-9_]*)(?P<access>->|\.)(?P<field>[A-Za-z_][A-Za-z0-9_]*)",
                    target,
                )
                bare_field = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target)
                field_name = match.group("field") if match is not None else (
                    bare_field.group(0) if bare_field is not None else ""
                )
                packet = _safe_partition_id(
                    str(mapping.get("PACKET", mapping.get("packet", "")))
                ).lower()
                candidates = [
                    value for value in typed_wire_targets
                    if field_name
                    and not (
                        match is not None
                        and match.group("access") == "."
                        and match.group("base").endswith("_t")
                    )
                    and value.endswith(f".{field_name}")
                    and packet in value.rsplit(".", 1)[0].lower()
                ]
                if len(candidates) == 1:
                    mapping[target_key] = candidates[0]
                elif (
                    bare_field is not None
                    and packet == "fixed_header"
                    and target == str(mapping.get("WIRE_FIELD", mapping.get("wire_field", "")))
                ):
                    strategy_key = "STRATEGY" if "STRATEGY" in mapping else "strategy"
                    mapping[strategy_key] = "parse_and_skip"
                    mapping.pop(target_key, None)
        invalid_wire_paths: list[str] = []
        for function_id in required_wire_ids:
            mappings = mappings_by_id.get(function_id, [])
            targets = targets_by_function.get(function_id, []) if isinstance(targets_by_function, dict) else []
            for target in targets:
                if not isinstance(target, dict):
                    continue
                matches = [
                    mapping for mapping in mappings
                    if str(mapping.get("PACKET", mapping.get("packet", ""))) == str(target.get("packet", ""))
                    and str(mapping.get("WIRE_FIELD", mapping.get("wire_field", ""))) == str(target.get("wire_field", ""))
                ]
                if not matches or (target.get("rule") and not any(str(item.get("RULE", item.get("rule", ""))).strip() for item in matches)):
                    raise ValueError(
                        f"stage7_wire_target_unfulfilled: {function_id}:{target.get('requirement_id', '')}"
                    )
            for mapping in mappings:
                if str(mapping.get("STRATEGY", mapping.get("strategy", ""))) != "store_in_field":
                    continue
                target = str(mapping.get("TARGET", mapping.get("target", ""))).strip()
                source = str(mapping.get("SOURCE", mapping.get("source", ""))).strip()
                valid_targets = typed_wire_targets | typed_output_targets.get(function_id, set())
                if (target and target not in valid_targets) or (not target and not source):
                    invalid_wire_paths.append(f"{function_id}:{target or '<missing>'}")
        if invalid_wire_paths:
            raise ValueError(
                f"stage7_wire_target_access_path_missing: {sorted(invalid_wire_paths)}"
            )
        return
    if stage.stage_id == "function_call_contract_closure":
        if not partition.get("main_function_id") and artifact.get("runtime_flow") == []:
            artifact.pop("runtime_flow")
        for edge in artifact.get("call_edges", []):
            if not isinstance(edge, dict):
                continue
            result = edge.get("result_usage")
            if isinstance(result, dict) and result.get("usage") == "ignored" and "target" not in result:
                result["target"] = ""
        _normalize_stage8_caller_field_dialects(
            artifact, partition, previous_artifacts, registry, context
        )
        edges, _ = _typed_call_stage_artifact(
            {**artifact, "call_edges_by_group": [{"partition": partition, "artifact": artifact}]}, registry
        )
        if context.get("required_implementation_obligations"):
            _validate_runtime_flow_artifact(artifact, partition, registry)
            _validate_typed_call_relations(
                artifact, edges, partition, previous_artifacts, registry
            )
        required_edge_artifact_id = partition.get("required_edge_artifact_id")
        if required_edge_artifact_id and not any(
            required_edge_artifact_id in {edge["caller_function_id"], edge["callee_function_id"]}
            for edge in edges
        ):
            raise ValueError(
                f"typed_delta_amendment_edge_missing: {required_edge_artifact_id} is not referenced"
            )


def _normalize_stage8_caller_field_dialects(
    artifact: dict[str, Any],
    partition: dict[str, Any],
    previous_artifacts: list[dict[str, Any]],
    registry: CanonicalPlanningRegistry,
    context: dict[str, Any],
) -> None:
    from .compiler import _lower_type_spec, _signature

    interfaces, _ = _canonical_function_indexes(
        _stage_artifact(previous_artifacts, "function_interface_design"), registry
    )
    allowed_callers = set(map(str, partition.get("caller_function_ids", [])))

    def caller_belongs_to_partition(edge: dict[str, Any]) -> bool:
        try:
            caller_id = registry.resolve(
                edge.get("caller_function_id"), expected_kinds={"function"}
            )["artifact_id"]
        except RegistryBindingError:
            return True
        return not allowed_callers or caller_id in allowed_callers

    artifact["call_edges"] = [
        edge for edge in artifact.get("call_edges", [])
        if not isinstance(edge, dict) or caller_belongs_to_partition(edge)
    ]
    inventory = _stage_artifact(previous_artifacts, "public_artifact_inventory")
    default_port = _find_named_integer(context.get("facts", {}), "default_port")
    callback_provider_ids = {
        registry.resolve(
            item.get("symbol") or item.get("name") or item.get("function_id"),
            expected_kinds={"function"},
        )["artifact_id"]
        for item in inventory.get("functions", []) if isinstance(item, dict)
        and "callback" in str(item.get("role", "")).lower()
        and any(marker in str(item.get("role", "")).lower() for marker in ("provide", "implement"))
    }
    def calls_callback_provider(edge: dict[str, Any]) -> bool:
        try:
            callee_id = registry.resolve(
                edge.get("callee_function_id"), expected_kinds={"function"}
            )["artifact_id"]
        except RegistryBindingError:
            return False
        return callee_id in callback_provider_ids

    artifact["call_edges"] = [
        edge for edge in artifact.get("call_edges", [])
        if not isinstance(edge, dict) or not calls_callback_provider(edge)
    ]
    flow = artifact.get("runtime_flow")
    runtime = inventory.get("runtime_entrypoint", {})
    if isinstance(flow, dict) and isinstance(runtime, dict):
        def resolve_function_id(value: Any) -> str:
            try:
                return str(registry.resolve(value, expected_kinds={"function"})["artifact_id"])
            except RegistryBindingError:
                return str(value)

        startup = list(map(str, runtime.get("startup_services", [])))
        run = list(map(str, runtime.get("run_services", [])))
        cleanup = list(map(str, runtime.get("cleanup_services", [])))
        flow["success_sequence"] = list(dict.fromkeys([*startup, *run, *cleanup]))
        main_id = resolve_function_id(runtime.get("main_function", ""))
        run_ids = {resolve_function_id(value) for value in run}
        for edge in artifact.get("call_edges", []):
            if not isinstance(edge, dict):
                continue
            callee_id = resolve_function_id(edge.get("callee_function_id", ""))
            result = edge.get("result_usage")
            if (
                resolve_function_id(edge.get("caller_function_id", "")) == main_id
                and callee_id in run_ids
                and isinstance(result, dict)
                and result.get("usage") == "returned"
            ):
                result["usage"] = "stored"
                callee_name = str(
                    _signature(interfaces.get(callee_id, {}).get("signature"), callee_id).get(
                        "NAME", "run"
                    )
                )
                result["target"] = f"{callee_name}_result"
        if cleanup:
            existing_cleanup = flow.get("failure_cleanup")
            if not isinstance(existing_cleanup, list):
                existing_cleanup = []
                flow["failure_cleanup"] = existing_cleanup
            covered_services = {
                resolve_function_id(item.get("after_function_id", ""))
                for item in existing_cleanup if isinstance(item, dict)
            }
            existing_cleanup.extend(
                {"after_function_id": service_id, "cleanup_function_ids": cleanup}
                for service_id in [*startup, *run]
                if resolve_function_id(service_id) not in covered_services
            )
    def keep_callee(reference: Any) -> bool:
        try:
            registry.resolve(reference, expected_kinds={"function"})
        except RegistryBindingError:
            return not str(reference).startswith("function:")
        return True

    artifact["call_edges"] = [
        edge for edge in artifact.get("call_edges", [])
        if not isinstance(edge, dict) or keep_callee(edge.get("callee_function_id"))
    ]
    artifact["artifact_requests"] = [
        request for request in artifact.get("artifact_requests", [])
        if not isinstance(request, dict)
        or not {
            "invalid", "placeholder",
        } <= set(re.findall(r"[a-z]+", str(request.get("semantic_role", "")).lower()))
    ]
    def type_key(value: Any) -> str:
        return re.sub(r"\s*\*\s*", "*", re.sub(r"\s+", " ", str(value))).strip()

    accessors_by_contract: dict[tuple[str, str, str], list[str]] = {}
    for function_id, interface in interfaces.items():
        signature = _signature(interface.get("signature"), function_id)
        name = str(signature.get("NAME", ""))
        if "_get_" not in name or len(signature.get("PARAMS", [])) != 1:
            continue
        field_name = name.rsplit("_get_", 1)[1]
        parameter = signature["PARAMS"][0]
        accessors_by_contract.setdefault(
            (field_name, type_key(signature.get("RETURN")), type_key(parameter.get("TYPE"))), []
        ).append(function_id)
    fields_by_type: dict[str, set[str]] = {}
    field_types_by_type: dict[str, dict[str, str]] = {}
    access_refs: set[str] = set()
    for design in _canonicalize_registry_types(
        _stage_artifact(previous_artifacts, "type_and_access_path_design"), registry,
        allow_missing=True,
    ):
        spec = _lower_type_spec(design)
        if spec.get("TYPE_KIND") == "STRUCT":
            fields_by_type[design["name"]] = {
                str(field.get("NAME"))
                for field in spec.get("FIELDS", [])
                if isinstance(field, dict) and field.get("NAME")
            }
            field_types_by_type[design["name"]] = {
                str(field.get("NAME")): str(field.get("TYPE", ""))
                for field in spec.get("FIELDS", [])
                if isinstance(field, dict) and field.get("NAME")
            }
        for item in [*design.get("access_paths", []), *design.get("ownership_fields", [])]:
            reference = (
                item.get("path") or item.get("PATH") or item.get("name") or item.get("NAME")
                if isinstance(item, dict) else item
            )
            if str(reference or "").strip():
                access_refs.add(str(reference))
    behavior_artifact = _stage_artifact(previous_artifacts, "function_behavior_design")
    for behavior in behavior_artifact.get("function_behaviors", []):
        if not isinstance(behavior, dict):
            continue
        for mapping in behavior.get("wire_mapping", behavior.get("WIRE_MAPPING", [])):
            if isinstance(mapping, dict):
                target = mapping.get("target", mapping.get("TARGET"))
                if str(target or "").strip():
                    access_refs.add(str(target))
    binding_usages: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict):
            continue
        caller = str(edge.get("caller_function_id", ""))
        for argument in edge.get("argument_semantics", []):
            if isinstance(argument, dict) and argument.get("source_kind") == "callback_binding":
                binding_usages.setdefault(str(argument.get("source_ref", "")), []).append((caller, edge))
    required_binding_keys = {
        (
            str(item.get("callback_type_id", "")),
            str(item.get("consumer_function_id", "")),
            str(item.get("provider_function_id", "")),
        )
        for item in partition.get("required_callback_bindings", []) if isinstance(item, dict)
    }
    runtime_run_ids = []
    for reference in inventory.get("runtime_entrypoint", {}).get("run_services", []):
        try:
            runtime_run_ids.append(
                registry.resolve(reference, expected_kinds={"function"})["artifact_id"]
            )
        except RegistryBindingError:
            continue

    def callback_owner(binding: dict[str, Any]) -> str:
        provider_id = registry.resolve(
            binding.get("provider_function_id"), expected_kinds={"function"}
        )["artifact_id"]
        consumer_name = registry.resolve(
            binding.get("consumer_function_id"), expected_kinds={"function"}
        )["canonical_name"].lower()
        candidates = [
            function_id for function_id in allowed_callers
            if function_id in interfaces and function_id != provider_id
        ]
        runtime_candidates = [item for item in runtime_run_ids if item in candidates]
        if runtime_candidates and any(
            marker in consumer_name for marker in ("server", "start", "listen", "event_loop")
        ):
            return runtime_candidates[0]
        consumer_tokens = [
            token for token in consumer_name.split("_")
            if token not in {"mqtt", "protocol", "function", "t"}
        ]
        def score(function_id: str) -> int:
            name = registry.resolve(function_id)["canonical_name"].lower()
            tokens = name.split("_")
            return sum(
                any(left.startswith(right[:5]) or right.startswith(left[:5]) for right in tokens)
                for left in consumer_tokens if len(left) >= 5
            )
        return max(candidates, key=score, default="")

    for binding in artifact.get("callback_bindings", []):
        if not isinstance(binding, dict):
            continue
        usages = binding_usages.get(str(binding.get("binding_id", "")), [])
        if len(usages) == 1:
            binding["owner_function_id"] = usages[0][0]
            user_data = next((
                argument for argument in usages[0][1].get("argument_semantics", [])
                if isinstance(argument, dict) and argument.get("parameter") == "user_data"
            ), None)
            if user_data is not None and str(user_data.get("source_ref", "")).strip():
                binding["user_data_source"] = str(user_data["source_ref"])
            continue
        try:
            key = (
                registry.resolve(binding.get("callback_type_id"), expected_kinds={"callback"})["artifact_id"],
                registry.resolve(binding.get("consumer_function_id"), expected_kinds={"function"})["artifact_id"],
                registry.resolve(binding.get("provider_function_id"), expected_kinds={"function"})["artifact_id"],
            )
        except RegistryBindingError:
            continue
        if key not in required_binding_keys:
            continue
        owner_id = callback_owner(binding)
        if not owner_id:
            continue
        binding["owner_function_id"] = owner_id
        owner_signature = _signature(interfaces[owner_id].get("signature"), owner_id)
        user_data_parameter = next((
            str(parameter.get("NAME", ""))
            for parameter in owner_signature.get("PARAMS", []) if isinstance(parameter, dict)
            and str(parameter.get("NAME", "")).lower() in {"context", "user_data"}
        ), "")
        if user_data_parameter:
            binding["user_data_source"] = user_data_parameter
    callback_pairs = {
        (
            str(binding.get("consumer_function_id", "")),
            str(binding.get("provider_function_id", "")),
        )
        for binding in artifact.get("callback_bindings", [])
        if isinstance(binding, dict)
    }
    artifact["call_edges"] = [
        edge for edge in artifact.get("call_edges", [])
        if not isinstance(edge, dict)
        or (
            str(edge.get("caller_function_id", "")),
            str(edge.get("callee_function_id", "")),
        ) not in callback_pairs
    ]
    used_binding_ids = {
        str(binding.get("source_ref", ""))
        for edge in artifact.get("call_edges", []) if isinstance(edge, dict)
        for binding in edge.get("argument_semantics", []) if isinstance(binding, dict)
        if binding.get("source_kind") == "callback_binding"
    }
    def is_required_binding(binding: dict[str, Any]) -> bool:
        try:
            key = (
                registry.resolve(binding.get("callback_type_id"), expected_kinds={"callback"})["artifact_id"],
                registry.resolve(binding.get("consumer_function_id"), expected_kinds={"function"})["artifact_id"],
                registry.resolve(binding.get("provider_function_id"), expected_kinds={"function"})["artifact_id"],
            )
        except RegistryBindingError:
            return False
        return key in required_binding_keys
    artifact["callback_bindings"] = [
        binding for binding in artifact.get("callback_bindings", [])
        if not isinstance(binding, dict)
        or str(binding.get("binding_id", "")) in used_binding_ids
        or is_required_binding(binding)
    ]
    required_accessor_edges: list[tuple[dict[str, Any], str, str, str, str]] = []
    invalid_caller_field_edges: set[int] = set()
    result_providers: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for candidate in artifact.get("call_edges", []):
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_caller = registry.resolve(
                candidate.get("caller_function_id"), expected_kinds={"function"}
            )["artifact_id"]
            candidate_callee = registry.resolve(
                candidate.get("callee_function_id"), expected_kinds={"function"}
            )["artifact_id"]
        except RegistryBindingError:
            continue
        if candidate_callee not in interfaces:
            continue
        candidate_signature = _signature(
            interfaces[candidate_callee].get("signature"), candidate_callee
        )
        return_type = type_key(candidate_signature.get("RETURN", ""))
        result = candidate.get("result_usage", {})
        if return_type != "void" and isinstance(result, dict):
            result_providers.setdefault((candidate_caller, return_type), []).append(
                (candidate_callee, str(result.get("target", "")))
            )

    def provider_type_matches(actual: str, expected: str) -> bool:
        actual = type_key(actual)
        expected = type_key(expected)
        return (
            actual == expected
            or (expected.startswith("const ") and expected.endswith("*") and actual == expected.removeprefix("const "))
            or (expected in {"void*", "const void*"} and actual.endswith("*"))
            or (actual == "void*" and expected.endswith("*"))
            or (actual == "const void*" and expected.startswith("const ") and expected.endswith("*"))
        )

    for edge in artifact.get("call_edges", []):
        if not isinstance(edge, dict):
            continue
        caller_id = registry.resolve(
            edge.get("caller_function_id"), expected_kinds={"function"}
        )["artifact_id"]
        callee_id = registry.resolve(
            edge.get("callee_function_id"), expected_kinds={"function"}
        )["artifact_id"]
        if callee_id not in interfaces:
            continue
        result = edge.get("result_usage")
        callee_signature = _signature(interfaces[callee_id].get("signature"), callee_id)
        if (
            isinstance(result, dict)
            and result.get("usage") == "ignored"
            and str(callee_signature.get("RETURN", "")).strip() != "void"
        ):
            result["usage"] = "checked"
            result["target"] = f"{callee_signature['NAME']}_result"
        if caller_id not in interfaces:
            continue
        signature = _signature(interfaces[caller_id].get("signature"), caller_id)
        expected_parameter_types = {
            str(parameter.get("NAME", "")): str(parameter.get("TYPE", ""))
            for parameter in callee_signature.get("PARAMS", []) if isinstance(parameter, dict)
        }
        parameters_by_type: dict[str, list[str]] = {}
        parameter_types: dict[str, str] = {}
        parameter_c_types: dict[str, str] = {}
        parameter_roles: dict[str, str] = {}
        for parameter in signature.get("PARAMS", []):
            if not isinstance(parameter, dict):
                continue
            base_type = re.sub(r"\bconst\b|\*", "", str(parameter.get("TYPE", ""))).strip()
            parameter_name = str(parameter.get("NAME", ""))
            parameters_by_type.setdefault(base_type, []).append(parameter_name)
            parameter_types[parameter_name] = base_type
            parameter_c_types[parameter_name] = str(parameter.get("TYPE", ""))
            parameter_roles[parameter_name] = str(parameter.get("ROLE", "")).lower()
        for binding in edge.get("argument_semantics", []):
            if not isinstance(binding, dict):
                continue
            source_ref = str(binding.get("source_ref", ""))
            expected_type = expected_parameter_types.get(str(binding.get("parameter", "")), "")
            if (
                binding.get("source_kind") == "constant"
                and source_ref.lower() in {"true", "false"}
            ):
                binding["source_kind"] = "literal"
            elif binding.get("source_kind") == "constant" and re.fullmatch(
                r"[-+]?(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*", source_ref
            ):
                binding["source_kind"] = "literal"
            if (
                binding.get("source_kind") == "caller_param"
                and str(binding.get("parameter", "")).lower().endswith("port")
                and source_ref.lstrip("&").split("[", 1)[0] == "argv"
            ):
                binding["source_kind"] = "local_value"
                binding["source_ref"] = str(binding.get("parameter", "port"))
                binding["source_type"] = expected_type
            if (
                binding.get("source_kind") == "literal"
                and str(binding.get("parameter", "")).lower().endswith("port")
                and default_port is not None
            ):
                binding["source_ref"] = str(default_port)
            elif (
                binding.get("source_kind") == "constant"
                and source_ref.upper().endswith("DEFAULT_PORT")
                and {"argc", "argv"} <= set(parameter_c_types)
            ):
                binding["source_kind"] = "local_value"
                binding["source_ref"] = str(binding.get("parameter", "port"))
            elif (
                binding.get("source_kind") == "constant"
                and source_ref.upper().endswith("DEFAULT_PORT")
                and default_port is not None
            ):
                binding["source_kind"] = "literal"
                binding["source_ref"] = str(default_port)
            elif binding.get("source_kind") == "local_value" and type_key(expected_type).endswith("*"):
                providers = result_providers.get((caller_id, type_key(expected_type)), [])
                if source_ref.lstrip("&") and len(providers) == 1 and re.search(
                    rf"\b{re.escape(source_ref.lstrip('&'))}\b", providers[0][1]
                ):
                    binding["source_kind"] = "prior_result"
                    binding["source_ref"] = providers[0][0]
            elif binding.get("source_kind") == "constant" and str(
                binding.get("parameter", "")
            ).endswith("_size"):
                buffer_parameter = str(binding["parameter"]).removesuffix("_size")
                buffer_binding = next((
                    candidate for candidate in edge.get("argument_semantics", [])
                    if isinstance(candidate, dict)
                    and candidate.get("parameter") == buffer_parameter
                    and candidate.get("source_kind") == "local_value"
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(candidate.get("source_ref", "")))
                ), None)
                if buffer_binding is not None:
                    try:
                        registry.resolve(source_ref, expected_kinds={"constant"})
                    except RegistryBindingError:
                        binding["source_kind"] = "local_value"
                        binding["source_ref"] = f"{buffer_binding['source_ref']}_size"
            indexed_field = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:->|\.)([A-Za-z_][A-Za-z0-9_]*)\[(?:i|index)\]",
                source_ref,
            )
            if binding.get("source_kind") == "caller_field" and indexed_field is not None:
                base_type = parameter_types.get(indexed_field.group(1), "")
                field_type = field_types_by_type.get(base_type, {}).get(indexed_field.group(2), "")
                element_type = type_key(re.sub(r"\*\s*$", "", field_type))
                supplied_type = type_key(re.sub(r"\bconst\b", "", str(binding.get("source_type", ""))))
                if element_type and type_key(re.sub(r"\bconst\b", "", element_type)) == supplied_type:
                    binding["source_kind"] = "local_value"
                    binding["source_ref"] = str(binding.get("parameter", ""))
                continue
            caller_field = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:->|\.)([A-Za-z_][A-Za-z0-9_]*)",
                source_ref,
            )
            if binding.get("source_kind") == "caller_field" and caller_field is not None:
                parameter_name, field_name = caller_field.groups()
                base_type = parameter_types.get(parameter_name, "")
                if field_name not in fields_by_type.get(base_type, set()):
                    candidates = accessors_by_contract.get(
                        (
                            field_name,
                            type_key(binding.get("source_type")),
                            type_key(parameter_c_types.get(parameter_name, "")),
                        ),
                        [],
                    )
                    if len(candidates) == 1:
                        binding["source_kind"] = "prior_result"
                        binding["source_ref"] = candidates[0]
                        required_accessor_edges.append(
                            (edge, candidates[0], parameter_name, parameter_c_types[parameter_name], field_name)
                        )
                    else:
                        invalid_caller_field_edges.add(id(edge))
                continue
            if binding.get("source_kind") == "caller_param" and caller_field is not None:
                base_type = parameter_types.get(caller_field.group(1), "")
                if caller_field.group(2) in fields_by_type.get(base_type, set()):
                    binding["source_kind"] = "caller_field"
                continue
            if binding.get("source_kind") == "caller_param":
                actual_type = parameter_c_types.get(source_ref, "")
                if not actual_type or not provider_type_matches(actual_type, expected_type):
                    invalid_caller_field_edges.add(id(edge))
                else:
                    binding["source_type"] = actual_type
                continue
            if binding.get("source_kind") != "access_path":
                continue
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)",
                source_ref,
            )
            if match is None or match.group(2) not in fields_by_type.get(match.group(1), set()):
                if source_ref not in access_refs:
                    invalid_caller_field_edges.add(id(edge))
                continue
            parameters = parameters_by_type.get(match.group(1), [])
            if len(parameters) == 1:
                binding["source_kind"] = "caller_field"
                binding["source_ref"] = f"{parameters[0]}->{match.group(2)}"
                continue
            semantic_context = " ".join((
                str(edge.get("call_purpose", "")),
                str(edge.get("condition", {}).get("expression", "")),
            )).lower()
            discriminants = set(match.group(1).lower().split("_")) - {"mqtt", "packet", "type", "t"}
            void_parameters = [
                name for name, value in parameter_c_types.items()
                if type_key(value) in {"void*", "const void*"}
            ]
            payload_parameters = [
                name for name in void_parameters
                if name.lower() in {"packet", "packet_payload", "payload", "message", "data"}
                or name.lower().endswith(("_packet", "_payload", "_message"))
                or (name.lower() != "user_data" and name.lower().endswith("_data"))
                or any(
                    marker in parameter_roles.get(name, "")
                    for marker in ("decoded_payload", "payload", "message_data")
                )
            ]
            selected_payload = payload_parameters[0] if len(payload_parameters) == 1 else (
                void_parameters[0] if len(void_parameters) == 1 else ""
            )
            if (
                selected_payload
                and any(len(value) > 2 and value in semantic_context for value in discriminants)
            ):
                binding["source_kind"] = "caller_field"
                binding["source_ref"] = (
                    f"(({match.group(1)}*){selected_payload})->{match.group(2)}"
                )

    artifact["call_edges"] = [
        edge for edge in artifact.get("call_edges", [])
        if not isinstance(edge, dict) or id(edge) not in invalid_caller_field_edges
    ]

    edges = artifact.get("call_edges", [])
    for consumer_edge, accessor_id, parameter_name, parameter_type, field_name in required_accessor_edges:
        caller_id = str(consumer_edge.get("caller_function_id", ""))
        if not any(
            isinstance(edge, dict)
            and edge.get("caller_function_id") == caller_id
            and edge.get("callee_function_id") == accessor_id
            for edge in edges
        ):
            edges.append({
                "caller_function_id": caller_id,
                "callee_function_id": accessor_id,
                "call_purpose": f"Access typed {field_name} from opaque caller context.",
                "condition": {"expression": "typed child handle is required", "reachable": True},
                "argument_semantics": [{
                    "parameter": _signature(interfaces[accessor_id].get("signature"), accessor_id)["PARAMS"][0]["NAME"],
                    "source_kind": "caller_param",
                    "source_ref": parameter_name,
                    "source_type": parameter_type,
                }],
                "result_usage": {"usage": "stored", "target": field_name},
                "trace_refs": deepcopy(consumer_edge.get("trace_refs", [])),
            })
    for _ in range(len(edges)):
        moved = False
        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            caller_id = str(edge.get("caller_function_id", ""))
            prior_ids = {
                str(binding.get("source_ref", ""))
                for binding in edge.get("argument_semantics", [])
                if isinstance(binding, dict) and binding.get("source_kind") == "prior_result"
            }
            provider_index = next((
                candidate for candidate in range(index + 1, len(edges))
                if isinstance(edges[candidate], dict)
                and edges[candidate].get("caller_function_id") == caller_id
                and str(edges[candidate].get("callee_function_id", "")) in prior_ids
            ), None)
            if provider_index is not None:
                edges.insert(index, edges.pop(provider_index))
                moved = True
                break
        if not moved:
            break
    available_results: dict[str, set[str]] = {}
    retained_edges: list[Any] = []
    for edge in edges:
        if not isinstance(edge, dict):
            retained_edges.append(edge)
            continue
        caller_id = str(edge.get("caller_function_id", ""))
        required_results = {
            str(binding.get("source_ref", ""))
            for binding in edge.get("argument_semantics", [])
            if isinstance(binding, dict) and binding.get("source_kind") == "prior_result"
        }
        if not required_results <= available_results.get(caller_id, set()):
            continue
        retained_edges.append(edge)
        available_results.setdefault(caller_id, set()).add(
            str(edge.get("callee_function_id", ""))
        )
    artifact["call_edges"] = retained_edges
    flow = artifact.get("runtime_flow")
    if isinstance(flow, dict) and str(partition.get("main_function_id", "")):
        try:
            main_id = registry.resolve(
                partition["main_function_id"], expected_kinds={"function"}
            )["artifact_id"]
            sequence = [
                registry.resolve(value, expected_kinds={"function"})["artifact_id"]
                for value in flow.get("success_sequence", [])
            ]
        except RegistryBindingError:
            return
        sequence_set = set(sequence)
        direct_order: list[str] = []
        for edge in retained_edges:
            if not isinstance(edge, dict):
                continue
            try:
                caller_id = registry.resolve(
                    edge.get("caller_function_id"), expected_kinds={"function"}
                )["artifact_id"]
                callee_id = registry.resolve(
                    edge.get("callee_function_id"), expected_kinds={"function"}
                )["artifact_id"]
            except RegistryBindingError:
                continue
            if caller_id == main_id and callee_id in sequence_set and callee_id not in direct_order:
                direct_order.append(callee_id)
        flow["success_sequence"] = [*direct_order, *[item for item in sequence if item not in direct_order]]


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
        artifact = _load_json_object(path)
        if stage.stage_id == "function_interface_design":
            _canonicalize_encoder_wire_packets(artifact)
        artifacts.append({"stage_id": stage.stage_id, "title": stage.title, "artifact": artifact})
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


def _derive_implementation_obligations(
    facts: dict[str, Any],
    characteristics: NormalizedCharacteristics,
    rules: list[EngineeringRule],
) -> list[dict[str, Any]]:
    rule_ids = [rule.rule_id for rule in rules]
    base_refs = ["fact:minimum_v1", *characteristics.fact_refs]
    obligations: list[dict[str, Any]] = []

    def add(
        obligation_id: str,
        role: str,
        required_kind_counts: dict[str, int],
        *,
        fact_refs: list[str] | None = None,
        requires_test: bool = False,
    ) -> None:
        obligations.append(
            {
                "obligation_id": obligation_id,
                "role": role,
                "required_kind_counts": required_kind_counts,
                "fact_refs": list(dict.fromkeys(fact_refs or base_refs)),
                "rule_refs": rule_ids,
                "requires_test": requires_test,
            }
        )

    add("foundation:message_constants", "Represent minimum-scope packet/message identities as constants or macros.", {"constant": 1})
    add("foundation:message_representation", "Represent decoded and encoded protocol units with canonical types.", {"type": 1})
    add("foundation:runtime_entrypoint", "Provide exactly one executable main identity.", {"function": 1}, requires_test=True)
    add("foundation:runtime_services", "Provide explicit startup, run, and cleanup services.", {"function": 3}, requires_test=True)
    add(
        "foundation:owned_runtime_lifecycle",
        "Provide a direct create/use/destroy lifecycle for process-owned runtime state; when callbacks need registry/router handles, use one explicit orchestrator context instead of hidden globals.",
        {"type": 1, "function": 3},
        requires_test=True,
    )
    add(
        "foundation:callback_provider",
        "Provide a typed callback identity, a consumer/registration function that accepts it, and a provider function whose ABI implements it.",
        {"callback": 1, "function": 2},
    )
    add("foundation:codec", "Provide parser/decoder and encoder/serializer services.", {"function": 2}, requires_test=True)
    add("foundation:dispatcher", "Dispatch decoded protocol units to minimum-scope handlers.", {"function": 1}, requires_test=True)
    if characteristics.transport_shape != "unknown":
        add("foundation:transport", "Provide transport startup, IO, and shutdown behavior.", {"function": 1}, fact_refs=["fact:transport"], requires_test=True)
        transport_text = json.dumps(
            {
                "transport": facts.get("transport", {}),
                "resource_model": facts.get("resource_model", {}),
            },
            ensure_ascii=False,
        ).lower()
        if "accept" in transport_text:
            add(
                "foundation:transport_accept_callback",
                "Provide a typed connection-accept callback, its transport consumer, and an upper-layer provider so accepted connections can create protocol session state.",
                {"callback": 1, "function": 2},
                fact_refs=["fact:transport.runtime_implications", "fact:resource_model.lifecycle_rules"],
                requires_test=True,
            )
        if any(marker in transport_text for marker in ("output queue", "outbound", "socket writes", "send")):
            add(
                "foundation:transport_output",
                "Provide a typed connection send/queue service for protocol responses and routed outbound messages.",
                {"function": 1},
                fact_refs=["fact:transport.runtime_implications"],
                requires_test=True,
            )
        if "buffer" in transport_text and any(
            marker in transport_text for marker in ("consume", "unread", "incomplete", "partial")
        ):
            add(
                "foundation:transport_input_buffer",
                "Provide typed input-buffer access, consumed-byte advancement, and connection identity access.",
                {"function": 3},
                fact_refs=["fact:transport.runtime_implications", "fact:connection_buffering"],
                requires_test=True,
            )
    if characteristics.statefulness == "stateful":
        add(
            "foundation:session_access",
            "Provide owned session state plus create, destroy/remove, lookup, state-query, and mutation services.",
            {"type": 1, "function": 5},
            fact_refs=["fact:state_model", "fact:resource_model"],
            requires_test=True,
        )
    if characteristics.routing_required:
        add(
            "foundation:routing",
            "Provide a routing container handle plus create, destroy, and matching or registry services required by facts.",
            {"type": 1, "function": 3},
            fact_refs=["fact:routing_model"],
            requires_test=True,
        )

    minimum = facts.get("minimum_v1", {}) if isinstance(facts.get("minimum_v1"), dict) else {}
    for key in ("must_support_surface", "must_support_state_behaviors", "must_support_error_paths", "must_support_limits"):
        values = minimum.get(key, [])
        for index, item in enumerate(values if isinstance(values, list) else []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("name") or item.get("condition") or item.get("summary") or index)
            evidence = [f"fact:{value}" for value in item.get("evidence_refs", []) if str(value)]
            add(
                f"minimum:{key}:{index}:{_safe_partition_id(label).lower()}",
                str(item.get("summary") or item.get("reason") or item.get("required_action") or label),
                {"function": 1},
                fact_refs=[f"fact:minimum_v1.{key}.{index}", *evidence],
                requires_test=True,
            )
    return obligations


def _derive_wire_mapping_targets(facts: dict[str, Any]) -> list[dict[str, Any]]:
    message_model = facts.get("message_model", {})
    if not isinstance(message_model, dict):
        return []
    entries = message_model.get("message_or_command_entries", [])
    targets: list[dict[str, Any]] = []
    fields_by_packet: dict[str, list[str]] = {}

    for entry_index, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        packet = str(entry.get("name") or entry.get("surface_unit") or f"packet_{entry_index}")
        fields_by_packet[packet] = []
        for field_index, field in enumerate(entry.get("fields", []) if isinstance(entry.get("fields"), list) else []):
            if not isinstance(field, dict) or not str(field.get("name", "")).strip():
                continue
            wire_field = str(field["name"])
            fields_by_packet[packet].append(wire_field)
            evidence = [str(value) for value in field.get("evidence_refs", []) if str(value)]
            targets.append(
                {
                    "requirement_id": f"wire:field:{entry_index}:{field_index}:{_safe_partition_id(packet).lower()}:{_safe_partition_id(wire_field).lower()}",
                    "packet": packet,
                    "wire_field": wire_field,
                    "fact_refs": [
                        f"fact:message_model.message_or_command_entries.{entry_index}.fields.{field_index}",
                        *[f"fact:{value}" for value in evidence],
                    ],
                }
            )

    for constraint_index, constraint in enumerate(
        message_model.get("field_constraints", []) if isinstance(message_model.get("field_constraints"), list) else []
    ):
        if not isinstance(constraint, dict):
            continue
        condition = str(constraint.get("condition", ""))
        condition_lower = condition.lower()
        packets = [packet for packet in fields_by_packet if packet.lower() in condition_lower]
        packet = packets[0] if packets else "protocol"
        matched_fields = [
            field for candidate in (packets or list(fields_by_packet))
            for field in fields_by_packet.get(candidate, [])
            if field.lower() in condition_lower
        ]
        if not matched_fields:
            matched_fields = ["fixed_header_flags" if "flag" in condition_lower else "constraint"]
        evidence = [str(value) for value in constraint.get("evidence_refs", []) if str(value)]
        for field_index, wire_field in enumerate(dict.fromkeys(matched_fields)):
            targets.append(
                {
                    "requirement_id": f"wire:constraint:{constraint_index}:{field_index}:{_safe_partition_id(packet).lower()}:{_safe_partition_id(wire_field).lower()}",
                    "packet": packet,
                    "wire_field": wire_field,
                    "rule": f"{condition} -> {constraint.get('required_action', '')}",
                    "fact_refs": [
                        f"fact:message_model.field_constraints.{constraint_index}",
                        *[f"fact:{value}" for value in evidence],
                    ],
                }
            )
    return targets


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
        "required_implementation_obligations": _derive_implementation_obligations(facts, characteristics, rules),
        "required_wire_mapping_targets": _derive_wire_mapping_targets(facts),
        "target_profile_visible_to_planner": False,
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
