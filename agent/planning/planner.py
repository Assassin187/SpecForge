from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .facts import write_json
from .models import EngineeringRule, NormalizedCharacteristics, OpenAssumption, to_jsonable


class StructuredPlanner(Protocol):
    def build_plan(self, context: dict[str, Any], *, resume_from: str | None = None) -> dict[str, Any]:
        ...


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
            "forbidden symbols and naming conflicts",
        ],
        required_output={
            "types": "array of planned type symbols with owner file and visibility",
            "constants_or_macros": "array of planned constants/macros with owner file",
            "functions": "array of planned functions with owner file, visibility, and high-level role",
            "public_symbol_table": "symbols intended for FILE_SPEC.PUBLIC_SYMBOLS",
            "forbidden_symbols": "array of names/patterns forbidden because they conflict with the plan",
        },
    ),
    PlanningStage(
        stage_id="type_and_access_path_design",
        title="Type and Access Path Design",
        purpose=(
            "Expand planned types into coder-facing TYPE_SPEC objects and ACCESS_PATHS. "
            "This must include wire-relevant fields, resource handles, ownership fields, and opaque boundaries."
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
            "types": "array of complete implementation-plan type objects",
            "access_paths": "array of {file, path, type, role}",
            "type_dependency_notes": "array of include/order requirements",
            "type_diagnostics": "array of unresolved or unsafe type decisions",
        },
    ),
    PlanningStage(
        stage_id="function_interface_design",
        title="Function Interface Design",
        purpose=(
            "Expand planned function inventory into concrete C interfaces without designing function bodies, call graph, "
            "wire mappings, or test vectors."
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
            "Close function dependencies using existing interfaces and behavior contracts. This stage may only reference "
            "already planned functions and types."
        ),
        engineering_focus=[
            "RELY.STRUCT / RELY.FUNC / RELY.VAR",
            "CALL_CONTRACTS and canonical callee signatures",
            "function call graph",
            "caller/callee visibility constraints",
            "diagnostics for unknown callees or signature drift",
        ],
        required_output={
            "rely_by_function": "mapping function_id -> RELY object",
            "call_contracts": "array of caller -> callee contracts with NAME and SIGNATURE",
            "call_graph": "mapping function_id -> array of called function_ids or names",
            "call_diagnostics": "array of missing callee or visibility findings",
        },
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
            "Close module/file/function dependencies using the already planned calls, types, ownership, and signatures. "
            "This stage checks that no dependency edge invents new protocol behavior."
        ),
        engineering_focus=[
            "module dependency graph",
            "header dependency graph",
            "source dependency graph",
            "function call graph",
            "generation order",
            "cycle and orphan checks",
            "public/private visibility consistency",
        ],
        required_output={
            "modules": "modules with final dependency arrays",
            "files": "files with final header/source dependency arrays",
            "functions": "functions with final interfaces, behaviors, RELY, CALL_CONTRACTS, WIRE_MAPPING, and TEST_VECTORS aligned",
            "generation_order": "array of module names",
            "dependency_diagnostics": "array of cycle/orphan/visibility findings",
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


class LLMStructuredPlanner:
    def __init__(self, api_key_env: str = "ALI_API", stage_log_dir: str | Path | None = None) -> None:
        self.api_key_env = api_key_env
        self.stage_log_dir = Path(stage_log_dir) if stage_log_dir is not None else None
        self.stage_records: list[dict[str, Any]] = []

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
            if resume_from == "compile_specs":
                print("[planning] resume from compile_specs; reusing completed structured planning stages", flush=True)
                return _assemble_plan_from_stage_artifacts(stage_artifacts, self.stage_records)
            _clear_stage_logs_from(self.stage_log_dir, start_index)
            print(f"[planning] resume from stage {start_index + 1}/{len(PLANNING_STAGES)}: {resume_from}", flush=True)

        for stage in PLANNING_STAGES[start_index:]:
            artifact = self._run_stage(stage, context, stage_artifacts)
            stage_artifacts.append(
                {
                    "stage_id": stage.stage_id,
                    "title": stage.title,
                    "artifact": artifact,
                }
            )
            if self.stage_log_dir is not None:
                write_json(self.stage_log_dir / "stage_records.json", self.stage_records)

        return _assemble_plan_from_stage_artifacts(stage_artifacts, self.stage_records)

    def _run_stage(
        self,
        stage: PlanningStage,
        context: dict[str, Any],
        previous_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if stage.partition_strategy == "module_then_file":
            return self._run_partitioned_stage(stage, context, previous_artifacts)
        return self._run_single_stage(stage, context, previous_artifacts)

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
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the SpecForge planning agent. Return only valid JSON. "
                    "Never modify protocol facts, never generate C source code, and never use a fixed protocol template."
                ),
            },
            {
                "role": "user",
                "content": build_stage_prompt(stage, context, previous_artifacts),
            },
        ]
        if stage_dir is not None:
            (stage_dir / "prompt.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        repair_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
            "repaired_json": repaired,
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
        partitions = _function_behavior_partitions(previous_artifacts)
        print(
            f"[planning] stage {stage_index}/{len(PLANNING_STAGES)} start: {stage.stage_id}; partitions={len(partitions)}",
            flush=True,
        )
        started = time.monotonic()
        client = FixedQwenClient(api_key_env=self.api_key_env)
        partition_artifacts: list[dict[str, Any]] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        repair_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are the SpecForge planning agent. Return only valid JSON. "
                            "Never modify protocol facts, never generate C source code, and never use a fixed protocol template."
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_stage_prompt(stage, context, previous_artifacts, partition=partition),
                    },
                ]
                if partition_dir is not None:
                    (partition_dir / "prompt.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
                try:
                    artifact = _parse_json_response(raw_response)
                    repaired = False
                    part_repair_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                except json.JSONDecodeError as parse_exc:
                    artifact, part_repair_usage = self._repair_json_response(
                        client=client,
                        stage=stage,
                        raw_response=raw_response,
                        parse_error=parse_exc,
                        stage_dir=partition_dir,
                    )
                    _add_usage(repair_usage, part_repair_usage)
                    repaired = True
                    repaired_any = True
                artifact = {"partition": partition, "artifact": artifact}
                partition_artifacts.append(artifact)
                if partition_dir is not None:
                    write_json(partition_dir / "artifact.json", artifact)
                    write_json(
                        partition_dir / "partition_manifest.json",
                        {
                            "partition_id": partition["partition_id"],
                            "status": "completed",
                            "usage": part_usage,
                            "repair_usage": part_repair_usage,
                            "repaired_json": repaired,
                        },
                    )
                print(
                    f"[planning]   partition {partition_index}/{len(partitions)} done: {partition['partition_id']}; "
                    f"tokens={part_usage['total_tokens']}; repair_tokens={part_repair_usage['total_tokens']}",
                    flush=True,
                )
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

        artifact = _merge_function_behavior_artifacts(partition_artifacts)
        elapsed = time.monotonic() - started
        record = {
            "stage_id": stage.stage_id,
            "title": stage.title,
            "status": "completed",
            "elapsed_seconds": round(elapsed, 3),
            "usage": usage,
            "repair_usage": repair_usage,
            "repaired_json": repaired_any,
            "partitions_total": len(partitions),
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

        messages = [
            {
                "role": "system",
                "content": (
                    "You repair invalid JSON. Return only valid JSON. Do not add, remove, reinterpret, or improve design content. "
                    "Only fix syntax such as hex literals, quotes, commas, comments, or trailing commas."
                ),
            },
            {
                "role": "user",
                "content": build_json_repair_prompt(stage, raw_response, parse_error),
            },
        ]
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
        artifact = _parse_json_response(response.content)
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        return artifact, usage


def _add_usage(total: dict[str, int], item: dict[str, int]) -> None:
    total["prompt_tokens"] += int(item.get("prompt_tokens", 0))
    total["completion_tokens"] += int(item.get("completion_tokens", 0))
    total["total_tokens"] += int(item.get("total_tokens", 0))


def _stage_artifact(previous_artifacts: list[dict[str, Any]], stage_id: str) -> dict[str, Any]:
    for item in reversed(previous_artifacts):
        if item.get("stage_id") == stage_id and isinstance(item.get("artifact"), dict):
            return item["artifact"]
    return {}


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
        "stages": stage_records,
    }
    return plan


def build_stage_prompt(
    stage: PlanningStage,
    context: dict[str, Any],
    previous_artifacts: list[dict[str, Any]] | None = None,
    partition: dict[str, Any] | None = None,
) -> str:
    prompt = {
        "stage": {
            "id": stage.stage_id,
            "title": stage.title,
            "purpose": stage.purpose,
            "engineering_focus": stage.engineering_focus,
            "required_output": stage.required_output,
        },
        "global_contract": {
            "input_facts_are_read_only": True,
            "do_not_invent_protocol_behavior": True,
            "do_not_emit_c_source_code": True,
            "do_not_use_reference_specs_as_instance_content": True,
            "trace_every_decision_to_fact_rule_or_assumption": True,
            "final_specs_dialect": {
                "module_spec": "PROTOCOL_MODULE_SPEC with PROTOCOL, MODULES, GENERATION_ORDER, CONSISTENCY_RULES",
                "file_spec": "FILE_SPEC with FILE, optional HEADER, SOURCE, PUBLIC_SYMBOLS, ACCESS_PATHS, CALL_CONTRACTS",
                "function_spec": "FUNCTION_SPEC with TRACE_ID, FUNCTION_TYPE, ROLE, SIGNATURE, RELY, LOGIC or EVENT",
            },
        },
        "facts": context["facts"],
        "normalized_characteristics": context["characteristics"],
        "activated_engineering_rules": context["engineering_rules"],
        "open_assumptions": context["open_assumptions"],
        "previous_stage_artifacts": previous_artifacts or [],
    }
    if partition is not None:
        prompt["current_partition"] = partition
        prompt["partition_contract"] = {
            "only_generate_behavior_for_functions_in_current_partition": True,
            "do_not_add_or_rename_functions": True,
            "do_not_generate_test_vectors": True,
            "return_partition_artifact_only": True,
        }
    return json.dumps(prompt, ensure_ascii=False, indent=2)


def build_json_repair_prompt(stage: PlanningStage, raw_response: str, parse_error: json.JSONDecodeError) -> str:
    return json.dumps(
        {
            "stage_id": stage.stage_id,
            "task": "Repair this response into syntactically valid JSON only.",
            "constraints": [
                "Do not add, remove, reinterpret, or improve design content.",
                "Do not invent missing fields.",
                "Convert JSON-invalid numeric forms such as 0x48 into valid decimal integers.",
                "Remove comments and trailing commas if present.",
                "Return only the repaired JSON object.",
            ],
            "parse_error": {
                "message": str(parse_error),
                "line": parse_error.lineno,
                "column": parse_error.colno,
                "position": parse_error.pos,
            },
            "raw_response": raw_response,
        },
        ensure_ascii=False,
        indent=2,
    )


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
