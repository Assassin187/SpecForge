from __future__ import annotations

import tempfile
import unittest
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent.common.llm_client import LLMRequest, LLMResponse, LLMUsage
from agent.coder.specs import load_spec_bundle_from_root
from agent.planning.compiler import compile_specs, normalize_plan_for_compiler
from agent.planning.facts import read_json, select_fact_slice, stable_json_hash, write_json
from agent.planning.knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from agent.planning.implementability import analyze_implementability
from agent.planning.metrics import build_run_metrics
from agent.planning.models import Diagnostic
from agent.planning.pipeline import run_planning, validate_existing_run
from agent.planning.planner import (
    LLMStructuredPlanner,
    PLANNING_STAGES,
    RecoverablePlanningError,
    TokenBudgetExceeded,
    _attach_required_callback_bindings,
    _canonicalize_wire_target_copies,
    _assemble_final_plan_candidate,
    _dependency_stage_overlays,
    _empty_recoverable_stage_artifact,
    _function_behavior_partitions,
    _function_call_partitions,
    _merge_function_artifacts,
    _materialize_typed_stage_artifacts,
    _normalize_stage6_callback_data_flow_interfaces,
    _normalize_stage4_callback_roles,
    _normalize_public_inventory_dialects,
    _prune_invalid_stage4_lifecycle_relations,
    _preserved_nonfatal_stage_artifact,
    _validate_completed_stage,
    _validate_partition_artifact,
    build_planning_context,
    planning_resume_points,
    planning_stage_catalog,
)
from agent.planning.prompts import build_local_correction_messages, build_stage_prompt
from agent.planning.registry import CanonicalPlanningRegistry, RegistryInvariantError, build_registry
from agent.planning.validation import (
    anti_hardcoding_scan,
    fact_decision_assumption_separation_check,
    facts_sensitivity_check,
    plan_to_spec_preservation_check,
    reference_isolation_check,
    schema_shape_check,
    structural_consistency_check,
)


ROOT = Path(__file__).resolve().parents[3]
FACTS = ROOT / "agent/facts/gold_facts/mqtt_min/protocol_facts.json"


def _minimal_plan() -> dict:
    plan = {
        "schema_version": "specforge_planning_ir_v1",
        "protocol": {
            "name": "MQTT",
            "slug": "mqtt",
            "spec_version": "3.1.1",
            "roles": ["BROKER"],
            "default_port": 1884,
            "scope": "unit-test staged LLM plan fixture",
            "trace_refs": ["fact:protocol_meta"],
        },
        "activated_rule_refs": ["RULE_MINIMUM_SCOPE"],
        "modules": [
            {
                "id": "module:core_runtime",
                "name": "core_runtime",
                "role": "Minimal test module emitted by mocked LLM stage output.",
                "dependencies": [],
                "files": ["core.h", "core.c"],
                "artifacts": [
                    {"NAME": "mqtt_core_t", "KIND": "TYPE", "ROLE": "Opaque core handle."},
                    {"NAME": "mqtt_core_init", "KIND": "FUNC", "ROLE": "Initialize core handle."},
                    {"NAME": "main", "KIND": "FUNC", "ROLE": "Executable entrypoint."},
                ],
                "trace_refs": ["fact:minimum_v1"],
                "rule_refs": ["RULE_MINIMUM_SCOPE"],
                "decision_refs": ["DEC001"],
            }
        ],
        "files": [
            {
                "id": "file:mqtt/core",
                "module": "core_runtime",
                "trace_id": "mqtt/core",
                "role": "Minimal file plan for staged planner tests.",
                "language": "C",
                "header_path": "core.h",
                "source_path": "core.c",
                "header_dependencies": [],
                "source_dependencies": ["core.h"],
                "types": ["type:mqtt_core_t"],
                "functions": ["function:mqtt/core/mqtt_core_init", "function:mqtt/core/main"],
                "trace_refs": ["fact:minimum_v1"],
                "decision_refs": ["DEC001"],
                "rule_refs": ["RULE_MINIMUM_SCOPE"],
                "forbidden_symbols": [],
                "test_vectors": [],
            }
        ],
        "types": [
            {
                "id": "type:mqtt_core_t",
                "file": "file:mqtt/core",
                "name": "mqtt_core_t",
                "kind": "TYPE",
                "visibility": "PUBLIC",
                "role": "Opaque core runtime handle.",
                "type_spec": {"TYPE_KIND": "OPAQUE"},
                "trace_refs": ["fact:minimum_v1"],
                "decision_refs": ["DEC001"],
                "rule_refs": ["RULE_MINIMUM_SCOPE"],
            }
        ],
        "functions": [
            {
                "id": "function:mqtt/core/mqtt_core_init",
                "file": "file:mqtt/core",
                "trace_id": "mqtt/core/mqtt_core_init",
                "name": "mqtt_core_init",
                "function_type": "ALGORITHM",
                "visibility": "public",
                "role": "Initialize a caller-provided core handle.",
                "signature": {
                    "RAW": "bool mqtt_core_init(mqtt_core_t* core)",
                    "NAME": "mqtt_core_init",
                    "RETURN": "bool",
                    "PARAMS": [{"TYPE": "mqtt_core_t*", "NAME": "core", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
                },
                "rely": {"STRUCT": [{"NAME": "mqtt_core_t", "ROLE": "Initialized handle."}], "FUNC": [], "VAR": []},
                "logic": {
                    "INPUT": "Borrowed core handle.",
                    "ACTION": "Validate the handle and initialize implementation-defined fields.",
                    "OUTPUT": "Return true on success.",
                    "INVARIANTS_USED": ["Protocol facts remain read-only."],
                },
                "trace_refs": ["fact:minimum_v1"],
                "decision_refs": ["DEC001"],
                "rule_refs": ["RULE_MINIMUM_SCOPE"],
                "test_vectors": [],
            },
            {
                "id": "function:mqtt/core/main",
                "file": "file:mqtt/core",
                "trace_id": "mqtt/core/main",
                "name": "main",
                "function_type": "ENTRYPOINT",
                "visibility": "private",
                "role": "Create the process runtime, run it, clean it up, and return a process exit code.",
                "signature": {
                    "RAW": "int main(int argc, char** argv)",
                    "NAME": "main",
                    "RETURN": "int",
                    "PARAMS": [
                        {"TYPE": "int", "NAME": "argc", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                        {"TYPE": "char**", "NAME": "argv", "NULLABLE": True, "OWNERSHIP": "BORROWED"},
                    ],
                },
                "rely": {
                    "STRUCT": [{"NAME": "mqtt_core_t", "ROLE": "Process-local core handle."}],
                    "FUNC": [{"NAME": "mqtt_core_init", "KIND": "CALL", "ROLE": "Initialize core."}],
                    "VAR": [],
                },
                "logic": {
                    "INPUT": "Process arguments.",
                    "ACTION": "Initialize the core and return its status.",
                    "OUTPUT": "Return zero on success and non-zero on failure.",
                    "INVARIANTS_USED": ["Protocol facts remain read-only."],
                },
                "trace_refs": ["fact:minimum_v1"],
                "decision_refs": ["DEC001"],
                "rule_refs": ["RULE_MINIMUM_SCOPE"],
                "test_vectors": [],
            },
        ],
        "engineering_decisions": [
            {
                "decision_id": "DEC001",
                "content": "Use one mocked core module for pipeline tests.",
                "supporting_fact_refs": ["fact:minimum_v1"],
                "activated_rule_refs": ["RULE_MINIMUM_SCOPE"],
                "rationale": "Test fixture only; production planning must come from LLM stages.",
                "affected_artifacts": ["module:core_runtime"],
            }
        ],
        "open_assumptions": [],
        "architecture": {"stage": "test_fixture"},
        "consistency_rules": [
            {"ID": "PC1", "RULE": "Facts are read-only.", "DOC_REF": ["fact:protocol_meta"]},
        ],
        "forbidden_symbols": [],
        "test_vectors": [
            {"NAME": "runtime_fixture", "INPUT": {}, "EXPECT": {"exit": 0}, "LEVEL": "RUNTIME", "TRACE_REFS": ["fact:minimum_v1"]}
        ],
        "plan_to_spec_mapping": [
            {"plan_id": "module:core_runtime", "spec_kind": "PROTOCOL_MODULE_SPEC.MODULES", "spec_key": "core_runtime", "lowering_rule": "copy from plan"},
            {"plan_id": "file:mqtt/core", "spec_kind": "FILE_SPEC", "spec_key": "mqtt/core", "lowering_rule": "copy from plan"},
            {"plan_id": "function:mqtt/core/mqtt_core_init", "spec_kind": "FUNCTION_SPEC", "spec_key": "mqtt/core/mqtt_core_init", "lowering_rule": "copy from plan"},
            {"plan_id": "function:mqtt/core/main", "spec_kind": "FUNCTION_SPEC", "spec_key": "mqtt/core/main", "lowering_rule": "copy from plan"},
        ],
    }
    registry_artifacts = [
        {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
        {
            "stage_id": "public_artifact_inventory",
            "artifact": {"types": plan["types"], "functions": plan["functions"], "constants_or_macros": []},
        },
        {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
        {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
    ]
    plan["canonical_registry_snapshot"] = build_registry(registry_artifacts, protocol_slug="mqtt").snapshot()
    return plan


def _implementation_ready_plan() -> dict:
    plan = _minimal_plan()
    file_item = plan["files"][0]
    module = plan["modules"][0]

    def service(name: str, return_type: str) -> dict:
        return {
            "id": f"function:mqtt/core/{name}", "file": file_item["id"],
            "trace_id": f"mqtt/core/{name}", "name": name,
            "function_type": "ALGORITHM", "visibility": "public",
            "role": f"{name} runtime service.",
            "signature": {
                "RAW": f"{return_type} {name}(void)", "NAME": name,
                "RETURN": return_type, "PARAMS": [],
            },
            "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
            "logic": {
                "INPUT": "Runtime state.", "ACTION": f"Execute {name}.",
                "OUTPUT": "Return service status." if return_type != "void" else "No return value.",
                "INVARIANTS_USED": ["Runtime lifecycle order is preserved."],
            },
            "trace_refs": ["fact:minimum_v1"], "decision_refs": ["DEC001"],
            "rule_refs": ["RULE_MINIMUM_SCOPE"],
            "test_vectors": [{
                "NAME": f"{name}_smoke", "INPUT": {}, "EXPECT": {"ok": True},
                "LEVEL": "FUNCTION", "TRACE_REFS": ["fact:minimum_v1"],
            }],
        }

    services = [
        service("runtime_start", "bool"), service("runtime_create", "bool"),
        service("runtime_run", "bool"), service("runtime_destroy", "void"),
    ]
    plan["functions"].extend(services)
    file_item["functions"].extend(item["id"] for item in services)
    module["artifacts"].extend(
        {"NAME": item["name"], "KIND": "FUNC", "ROLE": item["role"]} for item in services
    )
    for function in plan["functions"]:
        function["test_vectors"] = function.get("test_vectors") or [{
            "NAME": f"{function['name']}_smoke", "INPUT": {}, "EXPECT": {"ok": True},
            "LEVEL": "FUNCTION", "TRACE_REFS": ["fact:minimum_v1"],
        }]
    main = next(item for item in plan["functions"] if item["name"] == "main")
    main["rely"]["FUNC"] = [
        {"NAME": item["name"], "KIND": "CALL", "ROLE": item["role"]} for item in services
    ]
    main["call_contracts"] = [
        {
            "caller_function_id": main["id"], "callee_function_id": item["id"],
            "call_purpose": f"Invoke {item['name']}.",
            "condition": {"expression": "runtime path", "reachable": True},
            "argument_semantics": [],
            "result_usage": {
                "usage": "ignored" if item["signature"]["RETURN"] == "void" else "checked",
                "target": "" if item["signature"]["RETURN"] == "void" else f"{item['name']}_ok",
            },
            "trace_refs": ["fact:minimum_v1"],
            "NAME": item["name"], "SIGNATURE": item["signature"]["RAW"],
        }
        for item in services
    ]
    plan["required_implementation_obligations"] = [{"obligation_id": "fixture"}]
    plan["test_obligations"] = [{"obligation_id": "fixture"}]
    plan["runtime_entrypoint"] = {
        "main_function": main["id"], "owner_file": file_item["id"],
        "startup_services": [services[0]["id"]], "run_services": [services[2]["id"]],
        "cleanup_services": [services[3]["id"]], "exit_behavior": "zero on success",
        "fact_refs": ["fact:minimum_v1"],
    }
    plan["lifecycle_matrix"] = [{
        "resource_id": "runtime", "type_id": plan["types"][0]["id"],
        "create_function": services[1]["id"], "use_functions": [services[2]["id"]],
        "destroy_function": services[3]["id"], "fact_refs": ["fact:minimum_v1"],
    }]
    plan["runtime_flow"] = {
        "main_function_id": main["id"], "success_sequence": [item["id"] for item in services],
        "failure_cleanup": [
            {"after_function_id": item["id"], "cleanup_function_ids": [services[3]["id"]]}
            for item in services[:3]
        ],
        "trace_refs": ["fact:minimum_v1"],
    }
    registry_artifacts = [
        {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
        {"stage_id": "public_artifact_inventory", "artifact": {"types": plan["types"], "functions": plan["functions"], "constants_or_macros": []}},
        {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
        {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
    ]
    plan["canonical_registry_snapshot"] = build_registry(registry_artifacts, protocol_slug="mqtt").snapshot()
    return plan


def _empty_stage_artifact(stage_id: str) -> dict:
    if stage_id == "type_and_access_path_design":
        return {"type_definition_overlays": [], "artifact_requests": [], "type_dependency_notes": [], "type_diagnostics": []}
    if stage_id == "function_call_contract_closure":
        return {"call_edges": [], "artifact_requests": [], "call_diagnostics": []}
    if stage_id == "dependency_closure":
        return {"ordering_choices": [], "architecture_choices": [], "artifact_requests": [], "dependency_diagnostics": []}
    return {"stage_id": stage_id, "status": "ok"}


class PlanningPipelineTests(unittest.TestCase):
    def test_stability_metrics_cover_survival_binding_recovery_and_tokens(self) -> None:
        records = [
            {
                "status": "completed",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "repair_usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                "partitions_total": 2,
                "local_corrections": 1,
                "local_correction_successes": 1,
                "inventory_amendments": 1,
                "request_count": 2,
                "prompt_characters": 100,
            },
            {
                "status": "failed",
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                "stage_validation_error": "overlay_unknown_stable_id: fixture",
                "unresolved_partitions": 1,
                "request_count": 1,
                "prompt_characters": 50,
            },
        ]
        metrics = build_run_metrics(
            records,
            expected_stages=11,
            run_status="completed_with_candidate_only",
            candidate_produced=True,
            diagnostics=[{"code": "artifact_kind_mismatch"}],
            semantic_patch_usage={
                "prompt_tokens": 7,
                "completion_tokens": 1,
                "total_tokens": 8,
                "request_count": 1,
                "prompt_characters": 20,
            },
        )
        self.assertEqual(metrics["stage_survival"]["completed"], 1)
        self.assertEqual(metrics["partition_survival"], {"completed": 2, "total": 2, "unresolved": 1, "rate": 1.0})
        self.assertEqual(metrics["binding"], {"unknown_id_count": 1, "artifact_kind_mismatch_count": 1})
        self.assertEqual(metrics["recovery"]["local_correction_rate"], 1.0)
        self.assertEqual(metrics["token_accounting"]["observable_lower_bound"], 30)
        self.assertFalse(metrics["token_accounting"]["complete"])
        self.assertEqual(metrics["request_accounting"], {
            "structured_requests": 3,
            "semantic_requests": 1,
            "total_requests": 4,
            "prompt_characters": 170,
        })
        self.assertEqual(metrics["production"], {"candidate": True, "qualified": False})
        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.stage_records = records
            planner._write_run_metrics("completed_with_candidate_only")
            self.assertEqual(read_json(Path(raw) / "run_metrics.json")["token_accounting"]["total_tokens"], 22)

    def test_token_accounting_does_not_require_call_for_deterministic_amendment(self) -> None:
        metrics = build_run_metrics(
            [{
                "status": "completed",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                "request_count": 1,
                "inventory_amendments": 1,
                "inventory_amendment_usage": {},
            }],
            expected_stages=1,
            run_status="completed_with_candidate_only",
        )

        self.assertTrue(metrics["token_accounting"]["complete"])

    def test_fresh_failure_regression_stage5_unknown_type_identity(self) -> None:
        artifacts = [
            {"stage_id": "public_artifact_inventory", "artifact": {"types": [{"symbol": "registered_callback_t"}]}},
            {
                "stage_id": "type_and_access_path_design",
                "artifact": {"types": [{"type_name": "unregistered_callback_t"}]},
            },
        ]
        with self.assertRaisesRegex(ValueError, "overlay_unknown_stable_id"):
            _validate_completed_stage("type_and_access_path_design", artifacts, {})

    def test_stage5_typed_delta_accepts_only_registry_id_and_definition_overlay(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        type_id = registry.typed_view({"type"})[0]["artifact_id"]
        artifact = {
            "type_definition_overlays": [
                {
                    "type_id": type_id,
                    "definition_overlay": {
                        "type_spec": {"TYPE_KIND": "OPAQUE"},
                        "role": "fixture",
                        "ownership_model": "owner file controls the handle",
                        "opaque_boundaries": {"create": "constructor", "destroy": "destructor"},
                    },
                }
            ],
            "artifact_requests": [],
            "type_dependency_notes": [],
            "type_diagnostics": [],
        }
        _validate_completed_stage(
            "type_and_access_path_design",
            [{"stage_id": "type_and_access_path_design", "artifact": artifact}],
            {},
            registry=registry,
        )
        artifact["type_definition_overlays"][0]["definition_overlay"]["name"] = "drifted_t"
        with self.assertRaisesRegex(ValueError, "typed_delta_modifies_canonical_identity"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact}],
                {},
                registry=registry,
            )

    def test_stage4_rejects_representation_value_in_visibility(self) -> None:
        artifact = {"types": [{"symbol": "fixture_t", "kind": "type", "visibility": "opaque"}], "functions": []}
        with self.assertRaisesRegex(ValueError, "canonical_visibility_invalid"):
            _validate_completed_stage(
                "public_artifact_inventory",
                [{"stage_id": "public_artifact_inventory", "artifact": artifact}],
                {},
            )

    def test_stage4_closes_generic_obligations_runtime_lifecycle_and_registry(self) -> None:
        facts = read_json(FACTS)
        characteristics = normalize_characteristics(facts)
        rules = activate_engineering_rules(characteristics)
        context = build_planning_context(facts, characteristics, rules, extract_open_assumptions(facts, characteristics))
        obligations_by_id = {
            item["obligation_id"]: item for item in context["required_implementation_obligations"]
        }
        self.assertEqual(
            obligations_by_id["foundation:session_access"]["required_kind_counts"],
            {"type": 1, "function": 5},
        )
        self.assertEqual(
            obligations_by_id["foundation:routing"]["required_kind_counts"],
            {"type": 1, "function": 3},
        )
        first_obligation = context["required_implementation_obligations"][0]
        normalized_dialect = _normalize_public_inventory_dialects(
            {
                "functions": [{"symbol": "main"}],
                "implementation_coverage_matrix": {
                    first_obligation["obligation_id"]: ["main"]
                },
                "test_obligations": [first_obligation["obligation_id"]],
            },
            context,
        )
        self.assertIsInstance(normalized_dialect["implementation_coverage_matrix"], list)
        self.assertEqual(
            normalized_dialect["implementation_coverage_matrix"][0]["artifact_ids"], ["main"]
        )
        self.assertEqual(
            normalized_dialect["test_obligations"][0]["fact_refs"],
            first_obligation["fact_refs"],
        )
        self.assertEqual(
            normalized_dialect["functions"][0]["fact_refs"], first_obligation["fact_refs"]
        )
        merged_dialect = _normalize_public_inventory_dialects(
            {
                "implementation_coverage_matrix": [
                    {"obligation_id": first_obligation["obligation_id"], "artifact_ids": ["a"]},
                    {"obligation_id": first_obligation["obligation_id"], "artifact_ids": ["b"]},
                ],
            },
            context,
        )
        self.assertEqual(len(merged_dialect["implementation_coverage_matrix"]), 1)
        self.assertEqual(
            merged_dialect["implementation_coverage_matrix"][0]["artifact_ids"], ["a", "b"]
        )
        normalized_runtime_cleanup = _normalize_public_inventory_dialects(
            {
                "functions": [
                    {"symbol": "runtime_cleanup", "role": "runtime cleanup service"},
                    {"symbol": "connection_close", "role": "transport shutdown"},
                ],
                "runtime_entrypoint": {
                    "cleanup_services": ["runtime_cleanup", "connection_close"],
                },
            },
            {},
        )
        self.assertEqual(
            normalized_runtime_cleanup["runtime_entrypoint"]["cleanup_services"],
            ["runtime_cleanup"],
        )
        normalized_visibility = _normalize_public_inventory_dialects(
            {
                "types": [{"symbol": "public_t", "owner_file": "core.h", "visibility": "opaque"}],
                "functions": [{"symbol": "private_helper", "owner_file": "core.c", "visibility": "internal"}],
            },
            context,
        )
        self.assertEqual(normalized_visibility["types"][0]["visibility"], "public")
        self.assertEqual(normalized_visibility["functions"][0]["visibility"], "private")
        owned = obligations_by_id["foundation:owned_runtime_lifecycle"]
        normalized_lifecycle = _normalize_public_inventory_dialects(
            {
                "types": [{"symbol": "runtime_t"}],
                "functions": [
                    {"symbol": "runtime_create"}, {"symbol": "runtime_use"},
                    {"symbol": "runtime_destroy"},
                ],
                "implementation_coverage_matrix": [{
                    "obligation_id": owned["obligation_id"],
                    "artifact_ids": ["runtime_t"],
                }],
                "lifecycle_matrix": [{
                    "type_id": "runtime_t", "create_function": "runtime_create",
                    "use_functions": ["runtime_use"], "destroy_function": "runtime_destroy",
                    "fact_refs": owned["fact_refs"], "rule_refs": owned["rule_refs"],
                }],
            },
            context,
        )
        self.assertEqual(
            normalized_lifecycle["implementation_coverage_matrix"][0]["artifact_ids"],
            ["runtime_t", "runtime_create", "runtime_use", "runtime_destroy"],
        )
        inferred_manager = _normalize_public_inventory_dialects(
            {
                "types": [],
                "functions": [
                    {"symbol": "session_manager_create", "owner_file": "session.h"},
                    {"symbol": "session_manager_destroy", "owner_file": "session.h"},
                ],
                "implementation_coverage_matrix": [{
                    "obligation_id": "foundation:session_access",
                    "artifact_ids": [
                        "session_manager_t", "session_manager_create", "session_manager_destroy",
                    ],
                }],
            },
            {"required_implementation_obligations": [{
                "obligation_id": "foundation:session_access",
                "fact_refs": ["fact:state_model"], "rule_refs": ["RULE_STATEFUL_SESSION"],
            }]},
        )
        self.assertEqual(inferred_manager["types"], [{
            "symbol": "session_manager_t", "owner_file": "session.h",
            "visibility": "public", "kind": "type",
            "fact_refs": ["fact:state_model"], "rule_refs": ["RULE_STATEFUL_SESSION"],
            "decision_refs": [],
        }])
        layout = {
            "modules": [{"name": "core"}],
            "files": [{"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"}],
        }
        function_names = [
            "main", "runtime_start", "runtime_run", "runtime_cleanup", "runtime_create",
            "decode_unit", "encode_unit", "dispatch_unit", "transport_poll", "state_lookup",
            "state_update", "callback_provider", "callback_consumer",
            "transport_accept_callback_provider", "transport_accept_callback_consumer",
            "transport_queue_output", "connection_buffer_access", "connection_consume_input",
            "connection_get_fd", "state_is_connected", "routing_create", "routing_destroy",
            "routing_match",
        ]
        inventory = {
            "types": [
                {"symbol": "runtime_t", "owner_file": "core.c", "visibility": "public", "kind": "type", "trace_refs": ["fact:resource_model"]},
                {"symbol": "state_registry_t", "owner_file": "core.c", "visibility": "public", "kind": "type", "trace_refs": ["fact:state_model"]},
                {"symbol": "routing_container_t", "owner_file": "core.c", "visibility": "public", "kind": "type", "trace_refs": ["fact:routing_model"]},
                {"symbol": "event_callback_t", "owner_file": "core.c", "visibility": "public", "kind": "callback", "trace_refs": ["fact:interaction_model"]},
                {"symbol": "transport_accept_callback_t", "owner_file": "core.c", "visibility": "public", "kind": "callback", "trace_refs": ["fact:transport"]},
            ],
            "constants_or_macros": [
                {"symbol": "PACKET_KIND", "value": 1, "owner_file": "core.c", "visibility": "public", "trace_refs": ["fact:minimum_v1"]}
            ],
            "functions": [
                {
                    "symbol": name, "owner_file": "core.c",
                    "visibility": "private" if name == "main" else "public",
                    "role": (
                        "consume and register event_callback_t callback"
                        if name == "callback_consumer"
                        else "implement and provide event_callback_t callback"
                        if name == "callback_provider"
                        else "consume and register transport accept callback"
                        if name == "transport_accept_callback_consumer"
                        else "implement and provide transport accept callback"
                        if name == "transport_accept_callback_provider"
                        else "transport output"
                        if name == "transport_queue_output"
                        else "access transport input buffer"
                        if name == "connection_buffer_access"
                        else "consume decoded bytes from transport input buffer"
                        if name == "connection_consume_input"
                        else "access connection fd identity"
                        if name == "connection_get_fd"
                        else "query whether session is connected"
                        if name == "state_is_connected"
                        else f"{name} service"
                    ),
                    "trace_refs": ["fact:minimum_v1"],
                }
                for name in function_names
            ],
            "runtime_entrypoint": {
                "main_function": "main", "owner_file": "core.c",
                "startup_services": ["runtime_start"], "run_services": ["runtime_run"],
                "cleanup_services": ["runtime_cleanup"], "exit_behavior": "zero on success",
                "fact_refs": ["fact:minimum_v1"], "rule_refs": ["RULE_MINIMUM_SCOPE"], "decision_refs": [],
            },
            "lifecycle_matrix": [
                {
                    "resource_id": "runtime", "type_id": "runtime_t", "create_function": "runtime_create",
                    "use_functions": ["runtime_run"], "destroy_function": "runtime_cleanup",
                    "fact_refs": ["fact:resource_model"], "rule_refs": ["RULE_STATEFUL_SESSION"], "decision_refs": [],
                }
            ],
            "public_symbol_table": [],
            "forbidden_symbols": [],
        }
        kind_refs = {
            "constant": ["PACKET_KIND"],
            "type": ["runtime_t", "state_registry_t", "routing_container_t"],
            "callback": ["event_callback_t"],
            "function": function_names,
        }
        inventory["implementation_coverage_matrix"] = [
            {
                "obligation_id": obligation["obligation_id"],
                "artifact_ids": [
                    reference
                    for kind, minimum in obligation["required_kind_counts"].items()
                    for reference in kind_refs[kind][:minimum]
                ],
                "fact_refs": obligation["fact_refs"],
                "rule_refs": obligation["rule_refs"],
                "decision_refs": [],
            }
            for obligation in context["required_implementation_obligations"]
        ]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:callback_provider"
        )["artifact_ids"] = [
            "event_callback_t", "callback_consumer", "callback_provider",
            "transport_accept_callback_t", "transport_accept_callback_consumer",
            "transport_accept_callback_provider",
        ]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:runtime_services"
        )["artifact_ids"] = ["runtime_start", "runtime_run", "runtime_cleanup"]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:transport_accept_callback"
        )["artifact_ids"] = [
            "transport_accept_callback_t", "transport_accept_callback_consumer",
            "transport_accept_callback_provider",
        ]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:transport_output"
        )["artifact_ids"] = ["transport_queue_output"]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:transport_input_buffer"
        )["artifact_ids"] = [
            "connection_buffer_access", "connection_consume_input", "connection_get_fd",
        ]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:session_access"
        )["artifact_ids"] = [
            "runtime_t", "state_registry_t", "runtime_create", "runtime_cleanup",
            "state_lookup", "state_update", "state_is_connected",
        ]
        next(
            item for item in inventory["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:routing"
        )["artifact_ids"] = [
            "routing_container_t", "routing_create", "routing_destroy", "routing_match",
        ]
        inventory["test_obligations"] = [
            {"obligation_id": item["obligation_id"], "fact_refs": item["fact_refs"], "rule_refs": item["rule_refs"]}
            for item in context["required_implementation_obligations"]
            if item["requires_test"]
        ]
        artifacts = [
            {"stage_id": "module_file_plan", "artifact": layout},
            {"stage_id": "public_artifact_inventory", "artifact": inventory},
        ]
        registry = build_registry(artifacts, protocol_slug="fixture")
        _validate_completed_stage("public_artifact_inventory", artifacts, context, registry=registry)
        self.assertFalse(context["target_profile_visible_to_planner"])
        self.assertTrue(context["required_implementation_obligations"])
        target_pairs = {
            (item["packet"], item["wire_field"])
            for item in context["required_wire_mapping_targets"]
        }
        self.assertIn(("fixed_header", "packet_type"), target_pairs)
        self.assertIn(("fixed_header", "remaining_length"), target_pairs)
        self.assertTrue(any(item.get("rule") for item in context["required_wire_mapping_targets"]))
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "public_artifact_inventory")
        prompt = json.loads(build_stage_prompt(stage, context, [artifacts[0]]))
        self.assertEqual(prompt["required_implementation_obligations"], context["required_implementation_obligations"])
        self.assertEqual(prompt["input_policy"], {"target_profile_visible_to_planner": False, "target_directives": []})
        self.assertIn("runtime context", prompt["stage"]["artifact_boundary"]["runtime_context_rule"])
        self.assertIn("never invent a default constant", prompt["stage"]["artifact_boundary"]["runtime_configuration_rule"])

        generic_callback_roles = deepcopy(inventory)
        generic_callback_roles["types"] = [
            item for item in generic_callback_roles["types"]
            if item["symbol"] != "transport_accept_callback_t"
        ]
        generic_callback_roles["functions"] = [
            item for item in generic_callback_roles["functions"]
            if not item["symbol"].startswith("transport_accept_callback_")
        ]
        generic_callback_roles["implementation_coverage_matrix"] = [
            item for item in generic_callback_roles["implementation_coverage_matrix"]
            if item["obligation_id"] in {
                "foundation:callback_provider", "foundation:runtime_services",
                "foundation:session_access",
            }
        ]
        next(
            item for item in generic_callback_roles["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:callback_provider"
        )["artifact_ids"] = [
            "event_callback_t", "callback_consumer", "callback_provider",
        ]
        generic_callback_roles["test_obligations"] = [
            {"obligation_id": obligation_id,
             "fact_refs": obligations_by_id[obligation_id]["fact_refs"],
             "rule_refs": obligations_by_id[obligation_id]["rule_refs"]}
            for obligation_id in ("foundation:runtime_services", "foundation:session_access")
            if obligations_by_id[obligation_id]["requires_test"]
        ]
        for item in generic_callback_roles["functions"]:
            if item["symbol"] == "callback_consumer":
                item["role"] = "input service"
        generic_context = {
            **context,
            "required_implementation_obligations": [
                obligations_by_id[obligation_id]
                for obligation_id in (
                    "foundation:callback_provider", "foundation:runtime_services",
                    "foundation:session_access",
                )
            ],
        }
        generic_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": generic_callback_roles},
        ]
        _validate_completed_stage(
            "public_artifact_inventory", generic_artifacts, generic_context,
            registry=build_registry(generic_artifacts, protocol_slug="fixture"),
        )
        callback_roles = {
            item["symbol"]: item["role"] for item in generic_callback_roles["functions"]
            if item["symbol"] in {"callback_consumer", "callback_provider"}
        }
        self.assertIn("accept and consume event_callback_t callback", callback_roles["callback_consumer"])
        self.assertIn("implement and provide event_callback_t callback", callback_roles["callback_provider"])

        multi_callback = deepcopy(generic_callback_roles)
        multi_callback["types"].append({
            "symbol": "event_close_callback_t", "owner_file": "core.c",
            "visibility": "public", "kind": "callback", "trace_refs": ["fact:interaction_model"],
        })
        next(
            item for item in multi_callback["functions"] if item["symbol"] == "callback_consumer"
        )["role"] = "generic input service"
        callback_coverage = next(
            item for item in multi_callback["implementation_coverage_matrix"]
            if item["obligation_id"] == "foundation:callback_provider"
        )
        callback_coverage["artifact_ids"] = [
            "event_callback_t", "event_close_callback_t", "callback_provider",
        ]
        multi_artifacts = [
            artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": multi_callback},
        ]
        _validate_completed_stage(
            "public_artifact_inventory", multi_artifacts, generic_context,
            registry=build_registry(multi_artifacts, protocol_slug="fixture"),
        )
        self.assertIn("runtime_start", callback_coverage["artifact_ids"][-1])
        self.assertIn(
            "event_callback_t, event_close_callback_t callback",
            next(item["role"] for item in multi_callback["functions"] if item["symbol"] == "runtime_start"),
        )

        transport_callbacks = {
            "types": [
                {"symbol": f"tcp_{event}_callback_t", "owner_file": "transport.c",
                 "visibility": "public", "kind": "callback", "trace_refs": ["fact:transport"]}
                for event in ("accept", "data", "close")
            ],
            "functions": [
                {"symbol": "connection_create", "owner_file": "transport.c", "visibility": "public",
                 "role": "create connection", "trace_refs": ["fact:transport"]},
                *[
                    {"symbol": f"broker_on_{event}", "owner_file": "broker.c", "visibility": "public",
                     "role": f"handle {event} event", "trace_refs": ["fact:transport"]}
                    for event in ("accept", "data", "close")
                ],
            ],
            "implementation_coverage_matrix": [{
                "obligation_id": "foundation:callback_provider",
                "artifact_ids": [
                    "tcp_accept_callback_t", "connection_create", "broker_on_accept",
                ],
                "fact_refs": ["fact:transport"],
            }],
        }
        transport_layout = {
            "modules": [{"name": "core"}],
            "files": [
                {"id": "transport.c", "module": "core", "header_path": "transport.h", "source_path": "transport.c"},
                {"id": "broker.c", "module": "core", "header_path": "broker.h", "source_path": "broker.c"},
            ],
        }
        transport_registry = build_registry([
            {"stage_id": "module_file_plan", "artifact": transport_layout},
            {"stage_id": "public_artifact_inventory", "artifact": transport_callbacks},
        ], protocol_slug="fixture")
        _normalize_stage4_callback_roles(transport_callbacks, transport_registry)
        callback_ids = transport_callbacks["implementation_coverage_matrix"][0]["artifact_ids"]
        self.assertEqual(len(callback_ids), 7)
        for item in transport_callbacks["functions"]:
            if item["symbol"].startswith("broker_on_"):
                self.assertIn("implement and provide tcp_", item["role"])

        orphan_callback = deepcopy(inventory)
        orphan_callback["types"].append({
            "symbol": "orphan_callback_t", "owner_file": "core.c",
            "visibility": "public", "kind": "callback", "trace_refs": ["fact:interaction_model"],
        })
        orphan_artifacts = [
            artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": orphan_callback},
        ]
        with self.assertRaisesRegex(ValueError, "uncovered_callbacks=.*orphan_callback_t"):
            _validate_completed_stage(
                "public_artifact_inventory", orphan_artifacts, context,
                registry=build_registry(orphan_artifacts, protocol_slug="fixture"),
            )

        missing_accessor_type = deepcopy(inventory)
        missing_accessor_type["functions"].append({
            "symbol": "runtime_get_missing_manager", "owner_file": "core.c",
            "visibility": "public", "role": "typed accessor to missing_manager handle",
            "trace_refs": ["fact:minimum_v1"],
        })
        missing_accessor_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": missing_accessor_type},
        ]
        with self.assertRaisesRegex(ValueError, "accessor missing registered missing_manager type"):
            _validate_completed_stage(
                "public_artifact_inventory", missing_accessor_artifacts, context,
                registry=build_registry(missing_accessor_artifacts, protocol_slug="fixture"),
            )

        primitive_accessor = deepcopy(inventory)
        primitive_accessor["functions"].append({
            "symbol": "runtime_get_fd", "owner_file": "core.c", "visibility": "public",
            "role": "accessor for the integer file descriptor", "trace_refs": ["fact:minimum_v1"],
        })
        primitive_artifacts = [
            artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": primitive_accessor},
        ]
        _validate_completed_stage(
            "public_artifact_inventory", primitive_artifacts, context,
            registry=build_registry(primitive_artifacts, protocol_slug="fixture"),
        )

        combined_deficits = deepcopy(missing_accessor_type)
        for item in combined_deficits["functions"]:
            if "callback" in item["symbol"]:
                item["role"] = "generic packet service"
        combined_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": combined_deficits},
        ]
        with self.assertRaises(ValueError) as combined_error:
            _validate_completed_stage(
                "public_artifact_inventory", combined_artifacts, context,
                registry=build_registry(combined_artifacts, protocol_slug="fixture"),
            )
        self.assertIn("stage4_callback_role_coverage_missing", str(combined_error.exception))
        self.assertIn("outstanding_role_deficits", str(combined_error.exception))
        self.assertIn("accessor missing registered missing_manager type", str(combined_error.exception))

        broken = deepcopy(inventory)
        broken["implementation_coverage_matrix"] = broken["implementation_coverage_matrix"][:-1]
        broken_artifacts = [artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": broken}]
        with self.assertRaisesRegex(ValueError, "stage4_obligation_coverage_mismatch"):
            _validate_completed_stage(
                "public_artifact_inventory", broken_artifacts, context,
                registry=build_registry(broken_artifacts, protocol_slug="fixture"),
            )

        duplicate_main = deepcopy(inventory)
        duplicate_main["functions"].append(deepcopy(duplicate_main["functions"][0]))
        duplicate_artifacts = [artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": duplicate_main}]
        with self.assertRaisesRegex(ValueError, "stage4_runtime_main_count_invalid"):
            _validate_completed_stage(
                "public_artifact_inventory", duplicate_artifacts, context,
                registry=build_registry(duplicate_artifacts, protocol_slug="fixture"),
            )

        lifecycle_drift = deepcopy(inventory)
        lifecycle_drift["lifecycle_matrix"][0]["destroy_function"] = "missing_destroy"
        drift_artifacts = [artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": lifecycle_drift}]
        with self.assertRaisesRegex(ValueError, "stage4_lifecycle_identity_invalid"):
            _validate_completed_stage(
                "public_artifact_inventory", drift_artifacts, context,
                registry=build_registry(drift_artifacts, protocol_slug="fixture"),
            )

        lifecycle_mutator = deepcopy(inventory)
        lifecycle_mutator["lifecycle_matrix"][0]["create_function"] = "state_lookup"
        mutator_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": lifecycle_mutator},
        ]
        with self.assertRaisesRegex(ValueError, "stage4_lifecycle_mutator_misclassified"):
            _validate_completed_stage(
                "public_artifact_inventory", mutator_artifacts, context,
                registry=build_registry(mutator_artifacts, protocol_slug="fixture"),
            )

        cleanup_drift = deepcopy(inventory)
        cleanup_drift["runtime_entrypoint"]["cleanup_services"] = ["runtime_start"]
        cleanup_artifacts = [artifacts[0], {"stage_id": "public_artifact_inventory", "artifact": cleanup_drift}]
        with self.assertRaisesRegex(ValueError, "stage4_role_coverage_missing"):
            _validate_completed_stage(
                "public_artifact_inventory",
                cleanup_artifacts,
                context,
                registry=build_registry(cleanup_artifacts, protocol_slug="fixture"),
            )

        ungrounded_constant = deepcopy(inventory)
        ungrounded_constant["constants_or_macros"][0].pop("trace_refs")
        ungrounded_constant["constants_or_macros"][0]["decision_refs"] = ["DEC_FIXTURE"]
        constant_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": ungrounded_constant},
        ]
        with self.assertRaisesRegex(ValueError, "stage4_artifact_grounding_missing"):
            _validate_completed_stage(
                "public_artifact_inventory",
                constant_artifacts,
                context,
                registry=build_registry(constant_artifacts, protocol_slug="fixture"),
            )
        missing_value = deepcopy(inventory)
        missing_value["constants_or_macros"][0].pop("value")
        missing_value_artifacts = [
            artifacts[0],
            {"stage_id": "public_artifact_inventory", "artifact": missing_value},
        ]
        with self.assertRaisesRegex(ValueError, "fact_gap_wire_constant_value_missing"):
            _validate_completed_stage(
                "public_artifact_inventory",
                missing_value_artifacts,
                context,
                registry=build_registry(missing_value_artifacts, protocol_slug="fixture"),
            )

    def test_whole_stage_binding_failure_gets_one_local_correction(self) -> None:
        context = {
            "facts": {},
            "characteristics": {"protocol_slug": "mqtt"},
            "engineering_rules": [],
            "open_assumptions": [],
        }
        layout = {
            "modules": [{"name": "codec"}],
            "files": [{"id": "codec.h", "module": "codec", "header_path": "include/codec.h"}],
        }
        invalid = {
            "types": [{"symbol": "codec_t", "owner_file": "codec.h", "visibility": "opaque"}],
            "functions": [],
            "constants_or_macros": [],
        }
        corrected = deepcopy(invalid)
        corrected["types"][0]["visibility"] = "public"
        usage = {
            "prompt_tokens": 3,
            "completion_tokens": 1,
            "total_tokens": 4,
            "prompt_characters": 7,
            "request_count": 1,
        }

        with tempfile.TemporaryDirectory() as raw:
            stage_root = Path(raw) / "stage_logs"
            prior_artifacts = ({}, {}, layout)
            records = []
            for index, stage in enumerate(PLANNING_STAGES[:3]):
                stage_dir = stage_root / f"{index + 1:02d}_{stage.stage_id}"
                stage_dir.mkdir(parents=True)
                write_json(stage_dir / "artifact.json", prior_artifacts[index])
                records.append(
                    {
                        "stage_id": stage.stage_id,
                        "status": "completed",
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
                )
            write_json(stage_root / "stage_records.json", records)
            planner = LLMStructuredPlanner(stage_log_dir=stage_root)

            def fake_stage(self, stage, _context, _previous):
                if stage.stage_id != "public_artifact_inventory":
                    raise RuntimeError("fixture stop after corrected Stage 4 commit")
                self.stage_records.append(
                    {
                        "stage_id": stage.stage_id,
                        "status": "completed",
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                        "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "request_count": 1,
                        "prompt_characters": 10,
                    }
                )
                return invalid

            with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage), patch.object(
                planner, "_request_local_semantic_correction", return_value=(corrected, usage)
            ) as correction:
                with self.assertRaisesRegex(RuntimeError, "fixture stop"):
                    planner.build_plan(context, resume_from="public_artifact_inventory")
            correction.assert_called_once()
            record = planner.stage_records[3]
            self.assertEqual(record["local_corrections"], 1)
            self.assertEqual(record["local_correction_successes"], 1)
            self.assertEqual(record["semantic_correction_usage"], usage)
            self.assertEqual(record["request_count"], 2)
            self.assertEqual(record["prompt_characters"], 17)
            self.assertEqual(
                planner.registry.resolve("codec_t", expected_kinds={"type"})["visibility"], "public"
            )
            ledger = read_json(Path(raw) / "validation_layers.json")
            self.assertEqual(ledger["events"][0]["outcomes"], ["correction_requested", "corrected"])

    def test_whole_stage_correction_failure_uses_empty_delta_and_continues(self) -> None:
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        called: list[str] = []

        def fake_stage(planner, stage, _context, _previous):
            called.append(stage.stage_id)
            planner.stage_records.append(
                {
                    "stage_id": stage.stage_id,
                    "status": "completed",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )
            if stage.stage_id == "dependency_closure":
                return {"invalid_inventory": []}
            return _minimal_plan() if stage.stage_id == "final_plan_assembly" else {}

        def validate_commit(planner, stage, _previous, completed, _context):
            if stage.stage_id == "dependency_closure" and "invalid_inventory" in completed["artifact"]:
                raise ValueError("typed_delta_forbidden_field: fixture invalid dependency delta")
            return planner.registry

        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage), patch.object(
                LLMStructuredPlanner, "_validate_stage_commit", validate_commit
            ), patch.object(
                planner,
                "_request_local_semantic_correction",
                return_value=({"invalid_inventory": []}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
            ):
                plan = planner.build_plan(context)
        self.assertEqual(called, [stage.stage_id for stage in PLANNING_STAGES])
        self.assertEqual(len(planner.unresolved_partitions), 1)
        dependency = next(
            item["artifact"] for item in plan["structured_planning_stages"] if item["stage_id"] == "dependency_closure"
        )
        self.assertEqual(dependency["ordering_choices"], [])
        self.assertEqual(len([record for record in planner.stage_records if record["status"] == "completed"]), 11)

    def test_stage4_failed_correction_preserves_original_candidate_not_degraded_reply(self) -> None:
        original = {
            "types": [{"symbol": "runtime_t"}],
            "functions": [{"symbol": "main"}],
            "implementation_coverage_matrix": {"fixture": {"artifact_ids": ["main"]}},
        }
        degraded = {"implementation_coverage_matrix": []}
        attempts = 0

        def fake_stage(planner, stage, _context, _previous):
            if stage.stage_id == "type_and_access_path_design":
                raise RuntimeError("fixture stop after Stage 4")
            planner.stage_records.append(
                {
                    "stage_id": stage.stage_id, "status": "completed",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )
            return deepcopy(original) if stage.stage_id == "public_artifact_inventory" else {}

        def validate_commit(planner, stage, _previous, completed, _context):
            nonlocal attempts
            if stage.stage_id != "public_artifact_inventory":
                return planner.registry
            attempts += 1
            if attempts == 1:
                raise ValueError("stage4_coverage_matrix_missing: fixture")
            if attempts == 2:
                raise ValueError("stage4_obligation_coverage_mismatch: fixture")
            self.assertEqual(completed["artifact"], original)
            return planner.registry

        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage), patch.object(
                LLMStructuredPlanner, "_validate_stage_commit", validate_commit
            ), patch.object(
                planner,
                "_request_local_semantic_correction",
                return_value=(degraded, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
            ):
                with self.assertRaisesRegex(RuntimeError, "fixture stop"):
                    planner.build_plan({"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []})
            committed = read_json(Path(raw) / "04_public_artifact_inventory" / "artifact.json")

        self.assertEqual(attempts, 3)
        self.assertEqual(committed, original)
        self.assertEqual(planner.unresolved_partitions[0]["stage_id"], "public_artifact_inventory")

    def test_stage6_prior_unresolved_correction_fallback_is_committed_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stage_root = Path(raw) / "stage_logs"
            for index, stage in enumerate(PLANNING_STAGES[:5]):
                stage_dir = stage_root / f"{index + 1:02d}_{stage.stage_id}"
                stage_dir.mkdir(parents=True)
                write_json(stage_dir / "artifact.json", {})
            planner = LLMStructuredPlanner(stage_log_dir=stage_root)
            planner.unresolved_partitions = [{
                "stage_id": "type_and_access_path_design", "partition_id": "fixture",
                "diagnostic": "fixture upstream unresolved",
            }]

            def fake_stage(self, stage, _context, _previous):
                if stage.stage_id != "function_interface_design":
                    raise RuntimeError("fixture stop after Stage 6")
                self.stage_records.append({
                    "stage_id": stage.stage_id, "status": "completed",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
                (stage_root / "06_function_interface_design").mkdir(exist_ok=True)
                write_json(stage_root / "06_function_interface_design" / "artifact.json", {"invalid": []})
                return {"invalid": []}

            def validate_commit(self, stage, _previous, completed, _context):
                if stage.stage_id == "function_interface_design" and "invalid" in completed["artifact"]:
                    raise ValueError("stage6_private_type_leak: fixture")
                return self.registry

            with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage), patch.object(
                LLMStructuredPlanner, "_validate_stage_commit", validate_commit
            ), patch.object(
                planner, "_request_local_semantic_correction",
                return_value=({"invalid": []}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
            ):
                with self.assertRaisesRegex(RuntimeError, "fixture stop"):
                    planner.build_plan(
                        {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []},
                        resume_from="function_interface_design",
                    )

            committed = read_json(stage_root / "06_function_interface_design" / "artifact.json")
            self.assertEqual(committed, {
                "function_interfaces": [], "header_interfaces": [], "source_interfaces": [],
                "interface_diagnostics": [],
            })

    def test_stage4_correction_can_complete_grounded_inventory(self) -> None:
        obligation = {
            "obligation_id": "foundation:callback_provider",
            "required_kind_counts": {"callback": 1, "function": 2},
            "fact_refs": ["fact:interaction_model"],
            "rule_refs": ["RULE_CALLBACK"],
            "requires_test": False,
        }
        messages = build_local_correction_messages(
            PLANNING_STAGES[3],
            {
                "facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
                "required_implementation_obligations": [obligation],
            },
            [],
            {"partition_id": "whole_stage"},
            None,
            {"types": [], "functions": []},
            "stage4_obligation_kind_missing: foundation:callback_provider requires 1 callback, found 0",
            validation_layer="semantic",
            diagnostic_code="stage4_obligation_kind_missing",
            required_recovery="local_semantic_correction_or_artifact_request",
        )
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(
            payload["original_partition_contract"]["required_implementation_obligations"],
            [obligation],
        )
        self.assertTrue(payload["binding_recovery_invariants"]["stage4_may_add_grounded_inventory_identities"])
        self.assertEqual(payload["stage4_inventory_recovery_target"]["obligation"], obligation)
        self.assertEqual(payload["stage4_inventory_recovery_targets"], [obligation])
        self.assertIn("types[].kind=callback", payload["stage4_inventory_recovery_target"]["required_action"])
        self.assertIn("do not delete or reclassify", payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("audit foundation:callback_provider", payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("callback owner_file", payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("return the complete corrected Stage 4 artifact", messages[0]["content"])
        self.assertIn("remove void*", messages[0]["content"])
        self.assertIn("Do not use ArtifactRequest", messages[0]["content"])

        callback_messages = build_local_correction_messages(
            PLANNING_STAGES[3],
            {
                "facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
                "required_implementation_obligations": [obligation],
            },
            [], {"partition_id": "whole_stage"}, None,
            {"types": [], "functions": []},
            "stage4_callback_role_coverage_missing: fixture; outstanding_kind_deficits=['foundation:callback_provider']; "
            "outstanding_role_deficits=['runtime_get_missing_manager accessor missing registered missing_manager type']",
            validation_layer="semantic", diagnostic_code="stage4_callback_role_coverage_missing",
            required_recovery="local_semantic_correction_or_artifact_request",
        )
        callback_payload = json.loads(callback_messages[-1]["content"])
        self.assertIn("Do not relabel an unrelated protocol handler", callback_payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("outstanding_kind_deficits", callback_payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("outstanding_role_deficits", callback_payload["diagnostic_specific_recovery_guidance"])
        self.assertIn("typed _get_<handle> accessor", callback_payload["diagnostic_specific_recovery_guidance"])

        pruned = _prune_invalid_stage4_lifecycle_relations({
            "types": [
                {"symbol": "runtime_t", "kind": "type"},
                {"symbol": "mqtt_connection_t", "kind": "type"},
            ],
            "functions": [
                {"symbol": "runtime_create"}, {"symbol": "runtime_destroy"},
                {"symbol": "runtime_add"}, {"symbol": "runtime_remove"},
                {"symbol": "tcp_server_start"}, {"symbol": "tcp_server_stop"},
            ],
            "lifecycle_matrix": [
                {"type_id": "void", "create_function": "runtime_create", "destroy_function": "runtime_destroy"},
                {"type_id": "runtime_t", "create_function": "runtime_add", "destroy_function": "runtime_remove"},
                {"type_id": "mqtt_connection_t", "create_function": "tcp_server_start", "destroy_function": "tcp_server_stop"},
                {"type_id": "runtime_t", "create_function": "runtime_create", "destroy_function": "runtime_destroy"},
            ],
        })
        self.assertEqual(len(pruned["lifecycle_matrix"]), 1)
        self.assertEqual(pruned["lifecycle_matrix"][0]["create_function"], "runtime_create")

        unknown_messages = build_local_correction_messages(
            PLANNING_STAGES[3],
            {
                "facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
                "required_implementation_obligations": [obligation],
            },
            [], {"partition_id": "whole_stage"}, None,
            {"types": [], "functions": []}, "unknown_artifact_id: cannot bind 'runtime_registry_t'",
            validation_layer="binding", diagnostic_code="unknown_artifact_id",
            required_recovery="local_semantic_correction_or_artifact_request",
        )
        unknown_payload = json.loads(unknown_messages[-1]["content"])
        self.assertIn("add that missing type identity", unknown_payload["diagnostic_specific_recovery_guidance"])

        stage6_messages = build_local_correction_messages(
            PLANNING_STAGES[5],
            {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []},
            [], {"partition_id": "whole_stage"}, None,
            {"function_interfaces": []}, "stage6_private_type_leak: fixture",
            validation_layer="binding", diagnostic_code="stage6_private_type_leak",
            required_recovery="local_semantic_correction_or_artifact_request",
        )
        stage6_payload = json.loads(stage6_messages[-1]["content"])
        self.assertTrue(stage6_payload["binding_recovery_invariants"]["stage6_must_return_every_registered_function_interface"])
        self.assertIn("complete corrected Stage 6 artifact", stage6_messages[0]["content"])

    def test_stage5_typed_delta_rejects_non_type_registry_id(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        function_id = registry.typed_view({"function"})[0]["artifact_id"]
        artifact = {
            "type_definition_overlays": [{"type_id": function_id, "definition_overlay": {"type_spec": {"TYPE_KIND": "OPAQUE"}}}],
            "artifact_requests": [],
            "type_dependency_notes": [],
            "type_diagnostics": [],
        }
        with self.assertRaisesRegex(ValueError, "artifact_kind_mismatch"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact}],
                {},
                registry=registry,
            )

    def test_stage5_commit_rejects_empty_enum_unresolved_member_and_opaque_boundary(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        type_id = registry.typed_view({"type"})[0]["artifact_id"]

        def artifact(overlay):
            return {
                "type_definition_overlays": [{"type_id": type_id, "definition_overlay": overlay}],
                "artifact_requests": [], "type_dependency_notes": [], "type_diagnostics": [],
            }

        for overlay, code in (
            ({"type_spec": {"TYPE_KIND": "ENUM", "ENUM_VALUES": []}}, "stage5_enum_values_empty"),
            (
                {
                    "type_spec": {
                        "TYPE_KIND": "ENUM",
                        "ENUM_VALUES": [
                            {"NAME": "FRAME_ONE", "VALUE": 1},
                            {"NAME": "FRAME_ONE", "VALUE": 2},
                        ],
                    },
                    "trace_refs": ["fact:minimum_v1"],
                },
                "stage5_enum_names_invalid",
            ),
            (
                {
                    "type_spec": {
                        "TYPE_KIND": "ENUM",
                        "ENUM_VALUES": [
                            {"NAME": "FRAME_ONE", "VALUE": 1},
                            {"NAME": "FRAME_TWO", "VALUE": 1},
                        ],
                    },
                    "trace_refs": ["fact:minimum_v1"],
                },
                "stage5_enum_values_invalid",
            ),
            (
                {
                    "type_spec": {
                        "TYPE_KIND": "ENUM",
                        "ENUM_VALUES": [{"NAME": "FRAME-ONE", "VALUE": True, "ROLE": ""}],
                    },
                    "trace_refs": ["fact:minimum_v1"],
                },
                "stage5_enum_schema_invalid",
            ),
            (
                {
                    "type_spec": {
                        "TYPE_KIND": "ENUM",
                        "ENUM_VALUES": [{"NAME": "FRAME_ONE", "VALUE": 1, "ROLE": "fixture"}],
                    },
                    "trace_refs": ["fact:minimum_v1"],
                },
                "stage5_enum_grounding_missing",
            ),
            (
                {"type_spec": {"TYPE_KIND": "STRUCT", "FIELDS": [{"NAME": "missing", "TYPE": "missing_t"}]}},
                "unknown_artifact_id",
            ),
            ({"type_spec": {"TYPE_KIND": "OPAQUE"}}, "stage5_opaque_boundary_missing"),
        ):
            with self.assertRaisesRegex(ValueError, code):
                _validate_completed_stage(
                    "type_and_access_path_design",
                    [{"stage_id": "type_and_access_path_design", "artifact": artifact(overlay)}],
                    {},
                    registry=registry,
                )

        valid = artifact(
            {
                "type_spec": {
                    "TYPE_KIND": "ENUM",
                    "ENUM_VALUES": [{"NAME": "FRAME_ONE", "VALUE": 1, "ROLE": "fixture"}],
                },
                "trace_refs": ["fact:minimum_v1"],
                "enum_value_refs": {"FRAME_ONE": ["packet_types"]},
            }
        )
        _validate_completed_stage(
            "type_and_access_path_design",
            [{"stage_id": "type_and_access_path_design", "artifact": valid}],
            {},
            registry=registry,
        )

    def test_stage5_callback_uses_complete_pointer_safe_abi(self) -> None:
        layout = {
            "modules": [{"name": "core"}],
            "files": [{"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"}],
        }
        inventory = {
            "types": [
                {"symbol": "runtime_t", "owner_file": "core.c", "visibility": "public", "kind": "type"},
                {"symbol": "event_callback_t", "owner_file": "core.c", "visibility": "public", "kind": "callback"},
                {"symbol": "holder_t", "owner_file": "core.c", "visibility": "public", "kind": "type"},
            ],
            "functions": [],
        }
        registry = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": layout},
                {"stage_id": "public_artifact_inventory", "artifact": inventory},
            ],
            protocol_slug="fixture",
        )
        self.assertEqual(_function_call_partitions(registry, set()), [])
        self.assertTrue(_function_call_partitions(registry))
        runtime_id = registry.resolve("runtime_t", expected_kinds={"type"})["artifact_id"]
        callback_id = registry.resolve("event_callback_t", expected_kinds={"callback"})["artifact_id"]
        holder_id = registry.resolve("holder_t", expected_kinds={"type"})["artifact_id"]

        def artifact(signature: str, holder_type: str = "runtime_t*") -> dict:
            return {
                "type_definition_overlays": [
                    {
                        "type_id": runtime_id,
                        "definition_overlay": {
                            "type_spec": {"TYPE_KIND": "OPAQUE"},
                            "ownership_model": "caller owns handle",
                            "opaque_boundaries": {"create": "runtime_create", "destroy": "runtime_destroy"},
                        },
                    },
                    {
                        "type_id": callback_id,
                        "definition_overlay": {
                            "type_spec": {"TYPE_KIND": "CALLBACK", "CALLBACK_SIGNATURE": signature}
                        },
                    },
                    {
                        "type_id": holder_id,
                        "definition_overlay": {
                            "type_spec": {
                                "TYPE_KIND": "STRUCT",
                                "FIELDS": [{"NAME": "runtime", "TYPE": holder_type}],
                            }
                        },
                    },
                ],
                "artifact_requests": [],
                "type_dependency_notes": [],
                "type_diagnostics": [],
            }

        valid = artifact("void (*event_callback_t)(runtime_t* runtime)")
        _validate_completed_stage(
            "type_and_access_path_design",
            [{"stage_id": "type_and_access_path_design", "artifact": valid}],
            {},
            registry=registry,
        )
        with self.assertRaisesRegex(ValueError, "stage5_opaque_by_value_callback"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact("void (*event_callback_t)(runtime_t runtime)")}],
                {},
                registry=registry,
            )
        with self.assertRaisesRegex(ValueError, "stage5_callback_signature_invalid"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact("void event_callback_t(runtime_t* runtime)")}],
                {},
                registry=registry,
            )
        with self.assertRaisesRegex(ValueError, "stage5_callback_signature_invalid"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact("void (*wrong_callback_t)(runtime_t* runtime)")}],
                {},
                registry=registry,
            )
        with self.assertRaisesRegex(ValueError, "stage5_opaque_by_value_member"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": artifact("void (*event_callback_t)(runtime_t* runtime)", "runtime_t")}],
                {},
                registry=registry,
            )

    def test_stage6_closes_opaque_lifecycle_parameter_and_wire_abi(self) -> None:
        layout = {
            "modules": [{"name": "core"}],
            "files": [
                {"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"},
                {"id": "other.c", "module": "core", "header_path": "other.h", "source_path": "other.c"},
            ],
        }
        function_names = ["runtime_create", "runtime_destroy", "decode_unit", "dispatch_unit", "callback_provider", "event_handler"]
        inventory = {
            "types": [
                {"symbol": "runtime_t", "owner_file": "other.c", "visibility": "public", "kind": "type"},
                {"symbol": "event_callback_t", "owner_file": "core.c", "visibility": "public", "kind": "callback"},
                {"symbol": "internal_t", "owner_file": "other.c", "visibility": "private", "kind": "type"},
            ],
            "functions": [
                {"symbol": name, "owner_file": "core.c", "visibility": "public"}
                for name in function_names
            ],
            "implementation_coverage_matrix": [
                {"obligation_id": "foundation:codec", "artifact_ids": ["decode_unit"]},
                {"obligation_id": "foundation:dispatcher", "artifact_ids": ["dispatch_unit"]},
                {"obligation_id": "foundation:callback_provider", "artifact_ids": ["event_callback_t", "callback_provider", "event_handler"]},
            ],
            "lifecycle_matrix": [
                {
                    "type_id": "runtime_t",
                    "create_function": "runtime_create",
                    "use_functions": ["dispatch_unit"],
                    "destroy_function": "runtime_destroy",
                }
            ],
        }
        registry = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": layout},
                {"stage_id": "public_artifact_inventory", "artifact": inventory},
            ],
            protocol_slug="fixture",
        )
        runtime_id = registry.resolve("runtime_t", expected_kinds={"type"})["artifact_id"]
        callback_id = registry.resolve("event_callback_t", expected_kinds={"callback"})["artifact_id"]
        internal_id = registry.resolve("internal_t", expected_kinds={"type"})["artifact_id"]
        type_stage = {
            "type_definition_overlays": [
                {
                    "type_id": runtime_id,
                    "definition_overlay": {
                        "type_spec": {"TYPE_KIND": "OPAQUE"},
                        "ownership_model": "caller owns handle",
                        "opaque_boundaries": {"create": "runtime_create", "destroy": "runtime_destroy"},
                    },
                },
                {
                    "type_id": callback_id,
                    "definition_overlay": {
                        "type_spec": {
                            "TYPE_KIND": "CALLBACK",
                            "CALLBACK_SIGNATURE": "void (*event_callback_t)(runtime_t* runtime)",
                        },
                        "access_paths": ["event_handler"],
                    },
                },
                {
                    "type_id": internal_id,
                    "definition_overlay": {
                        "type_spec": {"TYPE_KIND": "STRUCT", "FIELDS": []}
                    },
                },
            ],
            "artifact_requests": [], "type_dependency_notes": [], "type_diagnostics": [],
        }
        private_type_leak = deepcopy(type_stage)
        private_type_leak["type_definition_overlays"][1]["definition_overlay"]["type_spec"][
            "CALLBACK_SIGNATURE"
        ] = "void (*event_callback_t)(internal_t* internal)"
        with self.assertRaisesRegex(ValueError, "stage5_private_type_leak"):
            _validate_completed_stage(
                "type_and_access_path_design",
                [{"stage_id": "type_and_access_path_design", "artifact": private_type_leak}],
                {}, registry=registry,
            )

        def signature(name: str, result: str, params: list[dict]) -> dict:
            raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}" for item in params) or "void"
            return {"RAW": f"{result} {name}({raw_params})", "NAME": name, "RETURN": result, "PARAMS": params}

        interfaces = [
            {"function_id": "runtime_create", "owner_file": "core.c", "trace_id": "core/runtime_create", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("runtime_create", "runtime_t*", [])},
            {"function_id": "runtime_destroy", "owner_file": "core.c", "trace_id": "core/runtime_destroy", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("runtime_destroy", "void", [{"TYPE": "runtime_t*", "NAME": "runtime", "NULLABLE": False, "OWNERSHIP": "OWNED"}])},
            {"function_id": "decode_unit", "owner_file": "core.c", "trace_id": "core/decode_unit", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("decode_unit", "bool", [{"TYPE": "const uint8_t*", "NAME": "bytes", "NULLABLE": False, "OWNERSHIP": "BORROWED"}]), "wire_obligation": {"required": True, "trace_refs": ["fact:minimum_v1"], "targets": [{"requirement_id": "wire:field:kind", "packet": "unit", "wire_field": "kind"}]}},
            {"function_id": "dispatch_unit", "owner_file": "core.c", "trace_id": "core/dispatch_unit", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("dispatch_unit", "bool", [{"TYPE": "runtime_t*", "NAME": "runtime", "NULLABLE": False, "OWNERSHIP": "BORROWED"}]), "wire_obligation": {"required": True, "rule_refs": ["RULE_MINIMUM_SCOPE"], "targets": [{"requirement_id": "wire:field:dispatch", "packet": "unit", "wire_field": "dispatch"}]}},
            {"function_id": "callback_provider", "owner_file": "core.c", "trace_id": "core/callback_provider", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("callback_provider", "void", [{"TYPE": "event_callback_t", "NAME": "callback", "NULLABLE": False, "OWNERSHIP": "BORROWED"}, {"TYPE": "runtime_t*", "NAME": "runtime", "NULLABLE": False, "OWNERSHIP": "BORROWED"}])},
            {"function_id": "event_handler", "owner_file": "core.c", "trace_id": "core/event_handler", "function_type": "ALGORITHM", "visibility": "public", "signature": signature("event_handler", "void", [{"TYPE": "runtime_t*", "NAME": "runtime", "NULLABLE": False, "OWNERSHIP": "BORROWED"}])},
        ]
        artifacts = [
            {"stage_id": "public_artifact_inventory", "artifact": inventory},
            {"stage_id": "type_and_access_path_design", "artifact": type_stage},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": interfaces}},
        ]
        context = {
            "required_implementation_obligations": [{"obligation_id": "fixture"}],
            "required_wire_mapping_targets": [
                {"requirement_id": "wire:field:kind", "packet": "unit", "wire_field": "kind"},
                {"requirement_id": "wire:field:dispatch", "packet": "unit", "wire_field": "dispatch"},
            ],
        }
        _validate_completed_stage("function_interface_design", artifacts, context, registry=registry)

        missing_callback_context = deepcopy(artifacts)
        callback_consumer = next(
            item for item in missing_callback_context[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "callback_provider"
        )
        callback_consumer["signature"]["PARAMS"] = callback_consumer["signature"]["PARAMS"][:1]
        callback_consumer["signature"]["RAW"] = "void callback_provider(event_callback_t callback)"
        with self.assertRaisesRegex(ValueError, "stage6_callback_consumer_context_missing"):
            _validate_completed_stage(
                "function_interface_design", missing_callback_context, context, registry=registry
            )

        missing_encoder_source = deepcopy(artifacts)
        next(
            item for item in missing_encoder_source[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "dispatch_unit"
        )["role"] = "encode one unit"
        with self.assertRaisesRegex(ValueError, "stage6_encoder_wire_source_missing"):
            _validate_completed_stage(
                "function_interface_design", missing_encoder_source, context, registry=registry
            )

        callback_owner_drift = deepcopy(inventory)
        next(
            item for item in callback_owner_drift["types"]
            if item["symbol"] == "event_callback_t"
        )["owner_file"] = "other.c"
        drift_registry = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": layout},
                {"stage_id": "public_artifact_inventory", "artifact": callback_owner_drift},
            ],
            protocol_slug="fixture",
        )
        owner_drift_artifacts = [
            {"stage_id": "public_artifact_inventory", "artifact": callback_owner_drift},
            *artifacts[1:],
        ]
        with self.assertRaisesRegex(ValueError, "stage6_callback_owner_consumer_mismatch"):
            _validate_completed_stage(
                "function_interface_design", owner_drift_artifacts, context,
                registry=drift_registry,
            )

        paired = {
            "function_interfaces": [{
                "function_id": "decode_unit",
                "wire_obligation": {
                    "required": True,
                    "targets": [{
                        "requirement_id": "wire:field:qos", "packet": "PUBLISH",
                        "wire_field": "qos", "fact_refs": ["fact:qos"],
                    }],
                },
            }],
        }
        paired_context = {"required_wire_mapping_targets": [
            {"requirement_id": "wire:field:qos", "packet": "PUBLISH", "wire_field": "qos", "fact_refs": ["fact:qos"]},
            {"requirement_id": "wire:constraint:qos", "packet": "PUBLISH", "wire_field": "qos", "rule": "qos must be zero", "fact_refs": ["fact:qos"]},
            {"requirement_id": "wire:constraint:flags", "packet": "SUBSCRIBE", "wire_field": "fixed_header_flags", "rule": "flags are fixed", "fact_refs": ["fact:flags"]},
        ]}
        _canonicalize_wire_target_copies(paired, paired_context)
        self.assertEqual(
            [item["requirement_id"] for item in paired["function_interfaces"][0]["wire_obligation"]["targets"]],
            ["wire:field:qos", "wire:constraint:qos", "wire:constraint:flags"],
        )

        response = {
            "function_interfaces": [{
                "function_id": "mqtt_encode_suback",
                "signature": {"NAME": "mqtt_encode_suback"},
                "wire_obligation": {"required": True, "targets": [{
                    "requirement_id": "wire:field:packet_id", "packet": "SUBSCRIBE",
                    "wire_field": "packet_id", "fact_refs": ["fact:encoder_responses"],
                }]},
            }],
        }
        _canonicalize_wire_target_copies(response, {"required_wire_mapping_targets": [{
            "requirement_id": "wire:field:packet_id", "packet": "SUBSCRIBE",
            "wire_field": "packet_id", "fact_refs": ["fact:encoder_responses"],
        }]})
        self.assertEqual(
            response["function_interfaces"][0]["wire_obligation"]["targets"][0]["packet"],
            "SUBACK",
        )

        target_grounded = deepcopy(artifacts)
        decode_obligation = next(
            item for item in target_grounded[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "decode_unit"
        )["wire_obligation"]
        decode_obligation.pop("trace_refs")
        decode_obligation["targets"][0]["fact_refs"] = ["fact:minimum_v1"]
        target_grounded_context = deepcopy(context)
        target_grounded_context["required_wire_mapping_targets"][0]["fact_refs"] = [
            "fact:minimum_v1"
        ]
        _validate_completed_stage(
            "function_interface_design", target_grounded, target_grounded_context,
            registry=registry,
        )

        duplicate_interface = deepcopy(artifacts)
        duplicate_interface[-1]["artifact"]["function_interfaces"].append(
            deepcopy(duplicate_interface[-1]["artifact"]["function_interfaces"][0])
        )
        with self.assertRaisesRegex(ValueError, "duplicate_count=1"):
            _validate_completed_stage(
                "function_interface_design", duplicate_interface, context, registry=registry
            )

        incomplete_tests = [
            *artifacts,
            {
                "stage_id": "function_test_vector_design",
                "artifact": {
                    "function_test_vectors": {interfaces[0]["function_id"]: [{"NAME": "create"}]},
                    "file_test_vectors": {},
                    "runtime_test_vectors": [{"NAME": "runtime"}],
                    "test_vector_diagnostics": [],
                },
            },
        ]
        with self.assertRaisesRegex(ValueError, "stage9_function_test_coverage"):
            _validate_completed_stage(
                "function_test_vector_design", incomplete_tests, context, registry=registry
            )

        for function_name, field, value, code in (
            ("runtime_create", "RETURN", "runtime_t", "stage6_opaque_by_value"),
            ("runtime_destroy", "OWNERSHIP", "", "stage6_parameter_contract_missing"),
            ("decode_unit", "wire_obligation", None, "stage6_wire_obligation_missing"),
        ):
            broken = deepcopy(artifacts)
            target = next(
                item for item in broken[-1]["artifact"]["function_interfaces"]
                if item["function_id"] == function_name
            )
            if field == "RETURN":
                target["signature"]["RETURN"] = value
                target["signature"]["RAW"] = "runtime_t runtime_create(void)"
            elif field == "OWNERSHIP":
                target["signature"]["PARAMS"][0][field] = value
            else:
                target.pop(field)
            with self.assertRaisesRegex(ValueError, code):
                _validate_completed_stage("function_interface_design", broken, context, registry=registry)

        signature_drift = deepcopy(artifacts)
        next(
            item for item in signature_drift[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "dispatch_unit"
        )["signature"]["PARAMS"][0]["NAME"] = "different_runtime"
        with self.assertRaisesRegex(ValueError, "stage6_signature_structure_drift"):
            _validate_completed_stage("function_interface_design", signature_drift, context, registry=registry)

        callback_drift = deepcopy(artifacts)
        provider = next(
            item for item in callback_drift[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "event_handler"
        )
        provider["signature"] = signature(
            "event_handler", "void",
            [{"TYPE": "void*", "NAME": "runtime", "NULLABLE": True, "OWNERSHIP": "BORROWED"}],
        )
        with self.assertRaisesRegex(ValueError, "stage6_callback_provider_abi_mismatch"):
            _validate_completed_stage("function_interface_design", callback_drift, context, registry=registry)

        visibility_drift = deepcopy(artifacts)
        dispatch = next(
            item for item in visibility_drift[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "dispatch_unit"
        )
        dispatch["signature"] = signature(
            "dispatch_unit", "bool",
            [{"TYPE": "internal_t*", "NAME": "internal", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        )
        with self.assertRaisesRegex(ValueError, "stage6_private_type_leak"):
            _validate_completed_stage("function_interface_design", visibility_drift, context, registry=registry)

        private_tag_leak = deepcopy(artifacts)
        dispatch = next(
            item for item in private_tag_leak[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "dispatch_unit"
        )
        dispatch["signature"] = signature(
            "dispatch_unit", "bool",
            [{"TYPE": "struct internal*", "NAME": "internal", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        )
        with self.assertRaisesRegex(ValueError, "stage6_private_type_leak"):
            _validate_completed_stage(
                "function_interface_design", private_tag_leak, context,
                registry=registry, allow_incomplete=True,
            )

        unknown_type = deepcopy(artifacts)
        dispatch = next(
            item for item in unknown_type[-1]["artifact"]["function_interfaces"]
            if item["function_id"] == "dispatch_unit"
        )
        dispatch["signature"] = signature(
            "dispatch_unit", "bool",
            [{"TYPE": "missing_runtime_t*", "NAME": "runtime", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        )
        with self.assertRaisesRegex(ValueError, "unknown_artifact_id"):
            _validate_completed_stage(
                "function_interface_design", unknown_type, context,
                registry=registry, allow_incomplete=True,
            )

        missing_interface = deepcopy(artifacts)
        missing_interface[-1]["artifact"]["function_interfaces"] = [
            item for item in missing_interface[-1]["artifact"]["function_interfaces"]
            if item["function_id"] != "decode_unit"
        ]
        decode_id = registry.resolve("decode_unit", expected_kinds={"function"})["artifact_id"]
        dispatch_id = registry.resolve("dispatch_unit", expected_kinds={"function"})["artifact_id"]
        with self.assertRaisesRegex(ValueError, "stage8_interface_missing"):
            _validate_partition_artifact(
                PLANNING_STAGES[7],
                {
                    "call_edges": [{
                        "caller_function_id": decode_id, "callee_function_id": dispatch_id,
                        "call_purpose": "fixture", "condition": {"expression": "decoded", "reachable": True},
                        "argument_semantics": [], "result_usage": {"usage": "checked", "target": "result"},
                        "trace_refs": ["fact:minimum_v1"],
                    }],
                    "callback_bindings": [], "artifact_requests": [], "call_diagnostics": [],
                },
                {"partition_id": "fixture", "caller_function_ids": [decode_id], "main_function_id": ""},
                registry, context, missing_interface,
            )

    def test_runtime_flow_closes_main_lifecycle_and_failure_cleanup(self) -> None:
        ids = {name: f"function:fixture/{name}" for name in ("main", "start", "create", "run", "destroy")}

        def function(name: str, return_type: str = "bool") -> dict:
            return {
                "id": ids[name], "file": "file:fixture/runtime", "name": name,
                "signature": {
                    "RAW": f"{return_type} {name}(void)", "NAME": name,
                    "RETURN": return_type, "PARAMS": [],
                },
                "call_contracts": [],
            }

        functions = [
            function("main", "int"), function("start"), function("create"),
            function("run"), function("destroy", "void"),
        ]
        functions[0]["signature"] = {
            "RAW": "int main(void)", "NAME": "main", "RETURN": "int", "PARAMS": []
        }
        functions[0]["call_contracts"] = [
            {
                "NAME": name,
                "callee_function_id": ids[name],
                "result_usage": {
                    "usage": "ignored" if name == "destroy" else "checked",
                    "target": "" if name == "destroy" else f"{name}_ok",
                },
            }
            for name in ("start", "create", "run", "destroy")
        ]
        plan = {
            "modules": [], "files": [], "types": [], "functions": functions,
            "test_vectors": [{"NAME": "runtime"}],
            "runtime_entrypoint": {
                "main_function": ids["main"], "startup_services": [ids["start"]],
                "run_services": [ids["run"]], "cleanup_services": [ids["destroy"]],
            },
            "lifecycle_matrix": [{
                "create_function": ids["create"], "use_functions": [ids["run"]],
                "destroy_function": ids["destroy"],
            }],
            "runtime_flow": {
                "main_function_id": ids["main"],
                "success_sequence": [ids["start"], ids["create"], ids["run"], ids["destroy"]],
                "failure_cleanup": [
                    {"after_function_id": ids[name], "cleanup_function_ids": [ids["destroy"]]}
                    for name in ("start", "create", "run")
                ],
                "trace_refs": ["fact:runtime"],
            },
        }
        self.assertFalse(
            {item["code"] for item in analyze_implementability(plan) if item["code"].startswith("runtime_")}
        )

        stored_result = deepcopy(plan)
        stored_result["functions"][0]["call_contracts"][2]["result_usage"] = {
            "usage": "stored", "target": "run_result",
        }
        self.assertFalse(
            {item["code"] for item in analyze_implementability(stored_result) if item["code"].startswith("runtime_")}
        )

        nested_create = deepcopy(plan)
        nested_create["functions"][0]["call_contracts"] = [
            item for item in nested_create["functions"][0]["call_contracts"]
            if item["NAME"] != "create"
        ]
        nested_create["functions"][1]["call_contracts"] = [{
            "NAME": "create", "callee_function_id": ids["create"],
            "result_usage": {"usage": "checked", "target": "created"},
        }]
        nested_create["runtime_flow"]["success_sequence"].remove(ids["create"])
        nested_create["runtime_flow"]["failure_cleanup"] = [
            item for item in nested_create["runtime_flow"]["failure_cleanup"]
            if item["after_function_id"] != ids["create"]
        ]
        consumer_id = "function:fixture/register_callback"
        provider_id = "function:fixture/on_event"
        for artifact_id, name in ((consumer_id, "register_callback"), (provider_id, "on_event")):
            nested_create["functions"].append({
                "id": artifact_id, "file": "file:fixture/runtime", "name": name,
                "signature": {
                    "RAW": f"void {name}(void)", "NAME": name,
                    "RETURN": "void", "PARAMS": [],
                },
                "call_contracts": [],
            })
        nested_create["functions"][3]["call_contracts"] = [{
            "NAME": "register_callback", "callee_function_id": consumer_id,
            "result_usage": {"usage": "ignored", "target": ""},
        }]
        nested_create["lifecycle_matrix"][0]["use_functions"].append(provider_id)
        nested_create["callback_bindings"] = [{
            "owner_function_id": ids["run"],
            "consumer_function_id": consumer_id,
            "provider_function_id": provider_id,
        }]
        self.assertFalse(
            {item["code"] for item in analyze_implementability(nested_create) if item["code"].startswith("runtime_")}
        )

        broken = deepcopy(plan)
        broken["runtime_flow"]["failure_cleanup"] = broken["runtime_flow"]["failure_cleanup"][:-1]
        self.assertIn("runtime_failure_cleanup_incomplete", {item["code"] for item in analyze_implementability(broken)})

        bad_main = deepcopy(plan)
        bad_main["functions"][0]["signature"] = {
            "RAW": "void main(void)", "NAME": "main", "RETURN": "void", "PARAMS": []
        }
        self.assertIn("runtime_entrypoint_signature_invalid", {item["code"] for item in analyze_implementability(bad_main)})

    def test_nonfatal_stage6_abi_failure_preserves_candidate_specs(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        interfaces = deepcopy(plan["functions"])
        interfaces[0]["signature"]["RAW"] = "bool mqtt_core_init(mqtt_core_t core)"
        interfaces[0]["signature"]["PARAMS"][0]["TYPE"] = "mqtt_core_t"
        interface_artifact = {"function_interfaces": interfaces}
        artifacts = [
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {"types": plan["types"], "functions": plan["functions"]},
            },
            {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
            {"stage_id": "function_interface_design", "artifact": interface_artifact},
        ]
        context = {"required_implementation_obligations": [{"obligation_id": "fixture"}]}
        with self.assertRaisesRegex(ValueError, "stage6_opaque_by_value") as raised:
            _validate_completed_stage("function_interface_design", artifacts, context, registry=registry)
        preserved = _preserved_nonfatal_stage_artifact(
            "function_interface_design", interface_artifact, raised.exception
        )
        self.assertEqual(preserved, interface_artifact)
        _validate_completed_stage(
            "function_interface_design", artifacts, context, registry=registry,
            allow_incomplete=True, preserve_nonfatal=True,
        )

        plan["functions"] = interfaces
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "mqtt_specs"
            compile_specs(normalize_plan_for_compiler(plan), specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_stage6_nonfatal_unknown_type_preserves_complete_interface_inventory(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        interfaces = deepcopy(plan["functions"])
        interfaces[0]["signature"]["RAW"] = "missing_manager_t* mqtt_core_init(mqtt_core_t* core)"
        interfaces[0]["signature"]["RETURN"] = "missing_manager_t*"
        artifacts = [{
            "stage_id": "function_interface_design",
            "artifact": {"function_interfaces": interfaces},
        }]
        context = {"required_implementation_obligations": [{"obligation_id": "fixture"}]}
        with self.assertRaisesRegex(ValueError, "unknown_artifact_id"):
            _validate_completed_stage(
                "function_interface_design", artifacts, context, registry=registry,
            )
        _validate_completed_stage(
            "function_interface_design", artifacts, context, registry=registry,
            allow_incomplete=True, preserve_nonfatal=True,
        )
        self.assertEqual(len(artifacts[0]["artifact"]["function_interfaces"]), len(plan["functions"]))

        compiler_plan = deepcopy(plan)
        compiler_plan.pop("canonical_registry_snapshot", None)
        file_id = compiler_plan["files"][0]["id"]
        compiler_plan["functions"].extend([
            {
                "id": "function:mqtt/core/missing_manager_create", "file": file_id,
                "name": "missing_manager_create", "visibility": "public",
                "signature": "missing_manager_t* missing_manager_create(void)",
            },
            {
                "id": "function:mqtt/core/missing_manager_destroy", "file": file_id,
                "name": "missing_manager_destroy", "visibility": "public",
                "signature": "void missing_manager_destroy(missing_manager_t* manager)",
            },
        ])
        normalized = normalize_plan_for_compiler(compiler_plan)
        self.assertIn("missing_manager_t", {item["name"] for item in normalized["types"]})
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "mqtt_specs"
            compile_specs(normalized, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_stage6_normalizes_fn_prefix_before_registry_advancement(self) -> None:
        plan = _minimal_plan()
        planner = LLMStructuredPlanner()
        planner.registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        interfaces = deepcopy(plan["functions"])
        interfaces[0]["function_id"] = "fn:mqtt_core_init"
        interfaces.append({
            "function_id": "fn:invented_helper", "signature": "void invented_helper(void)",
        })
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_interface_design")
        completed = {
            "stage_id": stage.stage_id,
            "artifact": {"function_interfaces": interfaces},
        }
        planner._validate_stage_commit(stage, [], completed, {})
        self.assertEqual(
            interfaces[0]["function_id"],
            planner.registry.resolve("mqtt_core_init", expected_kinds={"function"})["artifact_id"],
        )
        self.assertEqual(len(completed["artifact"]["function_interfaces"]), len(plan["functions"]))
        self.assertEqual(
            completed["artifact"]["interface_diagnostics"][0]["code"],
            "stage6_unregistered_fn_dialect_pruned",
        )

    def test_stage7_fulfills_explicit_wire_obligation_without_name_guessing(self) -> None:
        previous = [
            {
                "stage_id": "module_file_plan",
                "artifact": {
                    "modules": [{"name": "core"}],
                    "files": [{"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"}],
                },
            },
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {
                    "types": [
                        {"symbol": "unit_packet_t", "owner_file": "core.c", "visibility": "public"}
                    ],
                    "functions": [
                        {
                            "symbol": "consume_unit", "owner_file": "core.c", "visibility": "public",
                            "signature": {
                                "RAW": "bool consume_unit(uint8_t* out_kind)", "NAME": "consume_unit", "RETURN": "bool",
                                "PARAMS": [{"NAME": "out_kind", "TYPE": "uint8_t*", "ROLE": "output_discriminant"}],
                            },
                        },
                        {"symbol": "decode_named_but_not_wire", "owner_file": "core.c", "visibility": "public"},
                    ],
                    "implementation_coverage_matrix": [
                        {"obligation_id": "foundation:codec", "artifact_ids": ["consume_unit"]}
                    ],
                },
            },
            {
                "stage_id": "type_and_access_path_design",
                "artifact": {
                    "type_definition_overlays": [{
                        "type_id": "unit_packet_t",
                        "definition_overlay": {
                            "type_kind": "STRUCT",
                            "fields": [{"name": "kind", "c_type": "uint8_t"}],
                        },
                    }]
                },
            },
            {
                "stage_id": "function_interface_design",
                "artifact": {
                    "function_interfaces": [
                        {
                            "function_id": "consume_unit",
                            "signature": {
                                "RAW": "bool consume_unit(uint8_t* out_kind)", "NAME": "consume_unit", "RETURN": "bool",
                                "PARAMS": [{"NAME": "out_kind", "TYPE": "uint8_t*", "ROLE": "output_discriminant"}],
                            },
                            "wire_obligation": {
                                "required": True,
                                "targets": [
                                    {
                                        "requirement_id": "wire:field:kind",
                                        "packet": "unit",
                                        "wire_field": "kind",
                                        "fact_refs": ["EV1"],
                                    }
                                ],
                            },
                        }
                    ]
                },
            },
        ]
        registry = build_registry(previous, protocol_slug="fixture")
        partition = _function_behavior_partitions(previous, registry=registry)[0]
        required_id = registry.resolve("consume_unit", expected_kinds={"function"})["artifact_id"]
        other_id = registry.resolve("decode_named_but_not_wire", expected_kinds={"function"})["artifact_id"]
        self.assertEqual(partition["wire_function_ids"], [required_id])
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_behavior_design")
        prompt = json.loads(
            build_stage_prompt(
                stage,
                {
                    "facts": {
                        "protocol_meta": {"name": "Fixture"}, "minimum_v1": {},
                        "evidence_index": [{"evidence_id": "EV1", "text": "exact wire evidence"}],
                    },
                    "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
                },
                previous,
                partition=partition,
                registry=registry,
            )
        )
        self.assertEqual(prompt["facts"]["evidence_index"][0]["evidence_id"], "EV1")
        artifact = {
            "function_behaviors": [
                {"function_id": required_id, "LOGIC": {"ACTION": "consume one unit"}},
                {"function_id": other_id, "LOGIC": {"ACTION": "helper"}},
            ],
            "wire_mappings": [],
            "behavior_diagnostics": [],
        }
        with self.assertRaisesRegex(ValueError, "stage7_wire_obligation_unfulfilled"):
            _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        artifact["function_behaviors"][0]["wire_mapping"] = [
            {"PACKET": "other", "WIRE_FIELD": "kind", "STRATEGY": "parse_and_skip"}
        ]
        with self.assertRaisesRegex(ValueError, "stage7_wire_target_unfulfilled"):
            _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        artifact["function_behaviors"][0]["wire_mapping"] = [
            {
                "PACKET": "unit", "WIRE_FIELD": "kind",
                "STRATEGY": "store_in_field", "TARGET": "missing_t.kind",
            },
            {
                "PACKET": "unit", "WIRE_FIELD": "kind",
                "STRATEGY": "store_in_field",
            },
        ]
        with self.assertRaisesRegex(
            ValueError, r"stage7_wire_target_access_path_missing: .*<missing>.*missing_t.kind"
        ):
            _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        artifact["function_behaviors"][0]["wire_mapping"] = [
            {"PACKET": "unit", "WIRE_FIELD": "kind", "STRATEGY": "parse_and_skip"}
        ]
        _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        artifact["function_behaviors"][0]["wire_mapping"] = [{
            "PACKET": "unit", "WIRE_FIELD": "kind", "STRATEGY": "store_in_field",
            "TARGET": "out_kind",
        }]
        _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        artifact["function_behaviors"][0]["wire_mapping"] = [{
            "PACKET": "unit", "WIRE_FIELD": "kind", "STRATEGY": "store_in_field",
            "TARGET": "((unit_packet_t*)out_payload)->kind",
        }]
        _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        self.assertEqual(
            artifact["function_behaviors"][0]["wire_mapping"][0]["TARGET"],
            "unit_packet_t.kind",
        )
        artifact["function_behaviors"][0]["wire_mapping"] = [
            {
                "PACKET": "unit", "WIRE_FIELD": "kind", "STRATEGY": "store_in_field",
                "TARGET": "kind",
            },
            {
                "PACKET": "fixed_header", "WIRE_FIELD": "remaining_length",
                "STRATEGY": "store_in_field", "TARGET": "remaining_length",
            },
        ]
        _validate_partition_artifact(stage, artifact, partition, registry, {}, previous)
        self.assertEqual(
            artifact["function_behaviors"][0]["wire_mapping"][0]["TARGET"],
            "unit_packet_t.kind",
        )
        self.assertEqual(
            artifact["function_behaviors"][0]["wire_mapping"][1],
            {"PACKET": "fixed_header", "WIRE_FIELD": "remaining_length", "STRATEGY": "parse_and_skip"},
        )

    def test_stage6_accepts_and_normalizes_c_declaration_semicolons(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        interfaces = deepcopy(plan["functions"])
        for interface in interfaces:
            interface["signature"] = interface["signature"]["RAW"] + ";"
        _validate_completed_stage(
            "function_interface_design",
            [{"stage_id": "function_interface_design", "artifact": {"function_interfaces": interfaces}}],
            {},
            registry=registry,
        )

    def test_fresh_failure_regression_stage8_type_cannot_be_callee(self) -> None:
        base = _minimal_plan()
        caller = base["functions"][0]
        artifacts = [
            {"stage_id": "public_artifact_inventory", "artifact": {"types": base["types"], "functions": base["functions"]}},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": base["functions"]}},
            {
                "stage_id": "function_call_contract_closure",
                "artifact": {"call_contracts": [{"caller": caller["id"], "callee": base["types"][0]["id"]}]},
            },
        ]
        with self.assertRaisesRegex(ValueError, "overlay_unknown_stable_id"):
            _validate_completed_stage("function_call_contract_closure", artifacts, {})

    def test_stage8_typed_edge_derives_canonical_rely_contract_and_signature(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        caller, callee = registry.typed_view({"function"})
        artifacts = [
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
            {
                "stage_id": "function_call_contract_closure",
                "artifact": {
                    "call_edges": [
                        {
                            "caller_function_id": caller["artifact_id"],
                            "callee_function_id": callee["artifact_id"],
                            "call_purpose": "fixture call",
                            "condition": "always",
                            "argument_semantics": "borrow arguments",
                            "result_usage": "check result",
                        }
                    ],
                    "artifact_requests": [],
                    "call_diagnostics": [],
                },
            },
        ]
        functions = _merge_function_artifacts(artifacts, registry)
        merged_caller = next(item for item in functions if item["id"] == caller["artifact_id"])
        merged_callee = next(item for item in functions if item["id"] == callee["artifact_id"])
        self.assertEqual(merged_caller["CALL_CONTRACTS"][0]["NAME"], merged_callee["name"])
        self.assertEqual(merged_caller["CALL_CONTRACTS"][0]["SIGNATURE"], merged_callee["signature"]["RAW"])
        self.assertIn(merged_callee["name"], {item["NAME"] for item in merged_caller["rely"]["FUNC"]})

    def test_stage8_strict_direct_call_requires_typed_reachable_providers_and_result(self) -> None:
        layout = {
            "modules": [{"name": "core"}],
            "files": [{"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"}],
        }
        inventory = {
            "types": [{"symbol": "payload_t", "owner_file": "core.c", "visibility": "public"}],
            "functions": [
                {"symbol": "caller", "owner_file": "core.c", "visibility": "public"},
                {"symbol": "callee", "owner_file": "core.c", "visibility": "public"},
                {"symbol": "sink", "owner_file": "core.c", "visibility": "public"},
                {"symbol": "payload_get_value", "owner_file": "core.c", "visibility": "public"},
            ],
        }
        registry = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": layout},
                {"stage_id": "public_artifact_inventory", "artifact": inventory},
            ],
            protocol_slug="fixture",
        )

        def signature(name: str, result: str) -> dict:
            return {
                "RAW": f"{result} {name}(int value)", "NAME": name, "RETURN": result,
                "PARAMS": [{"TYPE": "int", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
            }

        previous = [
            {"stage_id": "module_file_plan", "artifact": layout},
            {"stage_id": "public_artifact_inventory", "artifact": inventory},
            {"stage_id": "type_and_access_path_design", "artifact": {"type_definition_overlays": [{
                "type_id": "payload_t",
                "definition_overlay": {
                    "type_kind": "STRUCT",
                    "fields": [{"name": "value", "c_type": "int"}],
                },
            }]}},
            {
                "stage_id": "function_interface_design",
                "artifact": {
                    "function_interfaces": [
                        {"function_id": "caller", "owner_file": "core.c", "visibility": "public", "signature": signature("caller", "void")},
                        {"function_id": "callee", "owner_file": "core.c", "visibility": "public", "signature": signature("callee", "bool")},
                        {"function_id": "sink", "owner_file": "core.c", "visibility": "public", "signature": signature("sink", "bool")},
                        {
                            "function_id": "payload_get_value", "owner_file": "core.c", "visibility": "public",
                            "signature": {
                                "RAW": "int payload_get_value(payload_t* context)", "NAME": "payload_get_value", "RETURN": "int",
                                "PARAMS": [{"TYPE": "payload_t*", "NAME": "context", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
                            },
                        },
                    ]
                },
            },
        ]
        caller_id = registry.resolve("caller", expected_kinds={"function"})["artifact_id"]
        callee_id = registry.resolve("callee", expected_kinds={"function"})["artifact_id"]
        sink_id = registry.resolve("sink", expected_kinds={"function"})["artifact_id"]
        accessor_id = registry.resolve("payload_get_value", expected_kinds={"function"})["artifact_id"]
        edge = {
            "caller_function_id": caller_id,
            "callee_function_id": callee_id,
            "call_purpose": "validate one value",
            "condition": {"expression": "value is available", "reachable": True},
            "argument_semantics": [
                {"parameter": "value", "source_kind": "caller_param", "source_ref": "value", "source_type": "int"}
            ],
            "result_usage": {"usage": "checked", "target": "status"},
            "trace_refs": ["fact:minimum_v1"],
        }
        artifact = {"call_edges": [edge], "callback_bindings": [], "artifact_requests": [], "call_diagnostics": []}
        partition = {"partition_id": "core", "caller_function_ids": [caller_id]}
        context = {"required_implementation_obligations": [{"obligation_id": "fixture"}]}
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        _validate_partition_artifact(stage, artifact, partition, registry, context, previous)

        unknown_callee = deepcopy(artifact)
        unknown_callee["call_edges"][0]["callee_function_id"] = "function:fixture/core/missing"
        _validate_partition_artifact(stage, unknown_callee, partition, registry, context, previous)
        self.assertEqual(unknown_callee["call_edges"], [])

        escaped_caller = deepcopy(artifact)
        escaped_caller["call_edges"][0]["caller_function_id"] = sink_id
        _validate_partition_artifact(stage, escaped_caller, partition, registry, context, previous)
        self.assertEqual(escaped_caller["call_edges"], [])

        ignored_nonvoid = deepcopy(artifact)
        ignored_nonvoid["call_edges"][0]["result_usage"] = {"usage": "ignored", "target": ""}
        _validate_partition_artifact(stage, ignored_nonvoid, partition, registry, context, previous)
        self.assertEqual(
            ignored_nonvoid["call_edges"][0]["result_usage"],
            {"usage": "checked", "target": "callee_result"},
        )

        ignored_result_dialect = deepcopy(artifact)
        ignored_result_dialect["call_edges"][0]["result_usage"] = {"usage": "ignored"}
        ignored_result_dialect["runtime_flow"] = []
        ignored_result_dialect["call_edges"][0]["callee_function_id"] = caller_id
        ignored_result_dialect["call_edges"][0]["argument_semantics"] = [{
            "parameter": "value", "source_kind": "caller_param", "source_ref": "value", "source_type": "int",
        }]
        _validate_partition_artifact(
            stage, ignored_result_dialect, partition, registry, context, previous
        )
        self.assertNotIn("runtime_flow", ignored_result_dialect)
        self.assertEqual(ignored_result_dialect["call_edges"][0]["result_usage"]["target"], "")

        unreachable = deepcopy(artifact)
        unreachable["call_edges"][0]["condition"]["reachable"] = False
        with self.assertRaisesRegex(ValueError, "stage8_direct_call_condition_invalid"):
            _validate_partition_artifact(stage, unreachable, partition, registry, context, previous)
        missing_provider = deepcopy(artifact)
        missing_provider["call_edges"][0]["argument_semantics"][0]["source_ref"] = "missing"
        _validate_partition_artifact(stage, missing_provider, partition, registry, context, previous)
        self.assertEqual(missing_provider["call_edges"], [])

        missing_access_path = deepcopy(artifact)
        missing_access_path["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "access_path", "source_ref": "payload_t.missing",
        })
        _validate_partition_artifact(
            stage, missing_access_path, partition, registry, context, previous
        )
        self.assertEqual(missing_access_path["call_edges"], [])

        pointer_previous = deepcopy(previous)
        pointer_interfaces = pointer_previous[-1]["artifact"]["function_interfaces"]
        pointer_interfaces[0]["signature"] = {
            "RAW": "void caller(void* value)", "NAME": "caller", "RETURN": "void",
            "PARAMS": [{"TYPE": "void*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        pointer_interfaces[1]["signature"] = {
            "RAW": "bool callee(int* value)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [{"TYPE": "int*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        pointer_artifact = deepcopy(artifact)
        pointer_artifact["call_edges"][0]["argument_semantics"][0]["source_type"] = "int*"
        _validate_partition_artifact(
            stage, pointer_artifact, partition, registry, context, pointer_previous
        )
        local_artifact = deepcopy(pointer_artifact)
        local_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "local_value", "source_ref": "&local_value",
        })
        _validate_partition_artifact(
            stage, local_artifact, partition, registry, context, pointer_previous
        )
        local_artifact["call_edges"][0]["argument_semantics"][0]["source_ref"] = "&local_value[1]"
        _validate_partition_artifact(
            stage, local_artifact, partition, registry, context, pointer_previous
        )
        local_artifact["call_edges"][0]["argument_semantics"][0]["source_ref"] = "local value"
        with self.assertRaisesRegex(ValueError, "stage8_argument_provider_missing"):
            _validate_partition_artifact(
                stage, local_artifact, partition, registry, context, pointer_previous
            )
        standard_literal_previous = deepcopy(previous)
        standard_literal_interfaces = standard_literal_previous[-1]["artifact"]["function_interfaces"]
        standard_literal_interfaces[1]["signature"] = {
            "RAW": "bool callee(uint16_t value)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [{"TYPE": "uint16_t", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        standard_literal_artifact = deepcopy(artifact)
        standard_literal_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "literal", "source_ref": "1883", "source_type": "uint16_t",
        })
        _validate_partition_artifact(
            stage, standard_literal_artifact, partition, registry, context,
            standard_literal_previous,
        )
        numeric_constant_dialect = deepcopy(standard_literal_artifact)
        numeric_constant_dialect["call_edges"][0]["argument_semantics"][0]["source_kind"] = "constant"
        _validate_partition_artifact(
            stage, numeric_constant_dialect, partition, registry, context,
            standard_literal_previous,
        )
        self.assertEqual(
            numeric_constant_dialect["call_edges"][0]["argument_semantics"][0]["source_kind"],
            "literal",
        )
        default_port_dialect = deepcopy(standard_literal_artifact)
        default_port_dialect["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "constant", "source_ref": "constant:FIXTURE_DEFAULT_PORT",
        })
        _validate_partition_artifact(
            stage, default_port_dialect, partition, registry,
            {
                **context,
                "facts": {"constants": [{"name": "default_port", "value_or_rule": "1884"}]},
            },
            standard_literal_previous,
        )
        self.assertEqual(
            default_port_dialect["call_edges"][0]["argument_semantics"][0],
            {"parameter": "value", "source_kind": "literal", "source_ref": "1884", "source_type": "uint16_t"},
        )
        external_config_previous = deepcopy(standard_literal_previous)
        external_config_previous[-1]["artifact"]["function_interfaces"][0]["signature"] = {
            "RAW": "void caller(int argc, char* argv)", "NAME": "caller", "RETURN": "void",
            "PARAMS": [
                {"TYPE": "int", "NAME": "argc", "NULLABLE": False, "OWNERSHIP": "VALUE"},
                {"TYPE": "char*", "NAME": "argv", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            ],
        }
        external_config_artifact = deepcopy(standard_literal_artifact)
        external_config_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "constant", "source_ref": "constant:FIXTURE_DEFAULT_PORT",
        })
        _validate_partition_artifact(
            stage, external_config_artifact, partition, registry, context,
            external_config_previous,
        )
        self.assertEqual(
            external_config_artifact["call_edges"][0]["argument_semantics"][0],
            {"parameter": "value", "source_kind": "local_value", "source_ref": "value", "source_type": "uint16_t"},
        )
        buffer_size_previous = deepcopy(previous)
        buffer_size_previous[-1]["artifact"]["function_interfaces"][1]["signature"] = {
            "RAW": "bool callee(uint8_t* buffer, size_t buffer_size)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [
                {"TYPE": "uint8_t*", "NAME": "buffer", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                {"TYPE": "size_t", "NAME": "buffer_size", "NULLABLE": False, "OWNERSHIP": "VALUE"},
            ],
        }
        buffer_size_artifact = deepcopy(artifact)
        buffer_size_artifact["call_edges"][0]["argument_semantics"] = [
            {"parameter": "buffer", "source_kind": "local_value", "source_ref": "response_buffer", "source_type": "uint8_t*"},
            {"parameter": "buffer_size", "source_kind": "constant", "source_ref": "MISSING_MAX_SIZE", "source_type": "size_t"},
        ]
        _validate_partition_artifact(
            stage, buffer_size_artifact, partition, registry, context, buffer_size_previous,
        )
        self.assertEqual(
            buffer_size_artifact["call_edges"][0]["argument_semantics"][1]["source_ref"],
            "response_buffer_size",
        )
        field_previous = deepcopy(previous)
        field_interfaces = field_previous[-1]["artifact"]["function_interfaces"]
        field_interfaces[0]["signature"] = {
            "RAW": "void caller(payload_t* payload)", "NAME": "caller", "RETURN": "void",
            "PARAMS": [{"TYPE": "payload_t*", "NAME": "payload", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        field_artifact = deepcopy(artifact)
        field_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_field", "source_ref": "payload->value",
        })
        _validate_partition_artifact(
            stage, field_artifact, partition, registry, context, field_previous
        )

        type_field_dialect = deepcopy(field_artifact)
        type_field_dialect["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "access_path", "source_ref": "payload_t.value",
        })
        _validate_partition_artifact(
            stage, type_field_dialect, partition, registry, context, field_previous
        )
        self.assertEqual(
            type_field_dialect["call_edges"][0]["argument_semantics"][0],
            {
                "parameter": "value", "source_kind": "caller_field",
                "source_ref": "payload->value", "source_type": "int",
            },
        )

        discriminated_payload_previous = deepcopy(field_previous)
        discriminated_payload_previous[-1]["artifact"]["function_interfaces"][0]["signature"] = {
            "RAW": "void caller(void* user_data, void* packet)", "NAME": "caller", "RETURN": "void",
            "PARAMS": [
                {"TYPE": "void*", "NAME": "user_data", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                {"TYPE": "void*", "NAME": "packet", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            ],
        }
        discriminated_payload_artifact = deepcopy(artifact)
        discriminated_payload_artifact["call_edges"][0]["condition"]["expression"] = "payload packet is selected"
        discriminated_payload_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "access_path", "source_ref": "payload_t.value", "source_type": "int",
        })
        _validate_partition_artifact(
            stage, discriminated_payload_artifact, partition, registry, context,
            discriminated_payload_previous,
        )
        self.assertEqual(
            discriminated_payload_artifact["call_edges"][0]["argument_semantics"][0]["source_ref"],
            "((payload_t*)packet)->value",
        )
        packet_data_previous = deepcopy(discriminated_payload_previous)
        packet_data_previous[-1]["artifact"]["function_interfaces"][0]["signature"]["PARAMS"][1].update({
            "NAME": "packet_data", "ROLE": "decoded_payload",
        })
        packet_data_artifact = deepcopy(discriminated_payload_artifact)
        packet_data_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "access_path", "source_ref": "payload_t.value",
        })
        _validate_partition_artifact(
            stage, packet_data_artifact, partition, registry, context, packet_data_previous,
        )
        self.assertEqual(
            packet_data_artifact["call_edges"][0]["argument_semantics"][0]["source_ref"],
            "((payload_t*)packet_data)->value",
        )

        indexed_field_previous = deepcopy(field_previous)
        indexed_field_previous[2]["artifact"]["type_definition_overlays"][0]["definition_overlay"]["fields"].append(
            {"name": "values", "c_type": "char**"}
        )
        indexed_field_previous[-1]["artifact"]["function_interfaces"][1]["signature"] = {
            "RAW": "bool callee(const char* value)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [{"TYPE": "const char*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        indexed_field_artifact = deepcopy(artifact)
        indexed_field_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_field", "source_ref": "payload->values[i]", "source_type": "const char*",
        })
        _validate_partition_artifact(
            stage, indexed_field_artifact, partition, registry, context, indexed_field_previous
        )
        self.assertEqual(
            indexed_field_artifact["call_edges"][0]["argument_semantics"][0]["source_kind"],
            "local_value",
        )

        opaque_field_previous = deepcopy(previous)
        opaque_field_previous[2]["artifact"]["type_definition_overlays"][0]["definition_overlay"] = {
            "type_kind": "OPAQUE", "c_type": "payload_t",
        }
        opaque_interfaces = opaque_field_previous[-1]["artifact"]["function_interfaces"]
        opaque_interfaces[0]["signature"] = {
            "RAW": "void caller(payload_t* context)", "NAME": "caller", "RETURN": "void",
            "PARAMS": [{"TYPE": "payload_t*", "NAME": "context", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        opaque_field_artifact = deepcopy(artifact)
        opaque_field_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_field", "source_ref": "context->value",
        })
        _validate_partition_artifact(
            stage, opaque_field_artifact, partition, registry, context, opaque_field_previous
        )
        self.assertEqual(
            [item["callee_function_id"] for item in opaque_field_artifact["call_edges"][:2]],
            [accessor_id, callee_id],
        )
        self.assertEqual(
            opaque_field_artifact["call_edges"][1]["argument_semantics"][0]["source_kind"],
            "prior_result",
        )

        missing_accessor_previous = deepcopy(opaque_field_previous)
        missing_accessor_previous[-1]["artifact"]["function_interfaces"] = [
            item for item in missing_accessor_previous[-1]["artifact"]["function_interfaces"]
            if item["function_id"] != "payload_get_value"
        ]
        missing_accessor_artifact = deepcopy(opaque_field_artifact)
        missing_accessor_artifact["call_edges"] = [deepcopy(edge)]
        missing_accessor_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_field", "source_ref": "context->value",
        })
        missing_accessor_artifact["artifact_requests"] = [{
            "requested_kind": "constant",
            "semantic_role": "invalid placeholder for an impossible call",
        }]
        _validate_partition_artifact(
            stage, missing_accessor_artifact, partition, registry, context,
            missing_accessor_previous,
        )
        self.assertEqual(missing_accessor_artifact["call_edges"], [])
        self.assertEqual(missing_accessor_artifact["artifact_requests"], [])

        const_field_previous = deepcopy(field_previous)
        const_field_previous[2]["artifact"]["type_definition_overlays"][0]["definition_overlay"]["fields"] = [
            {"name": "text", "c_type": "char*"}
        ]
        const_field_previous[-1]["artifact"]["function_interfaces"][1]["signature"] = {
            "RAW": "bool callee(const char* value)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [{"TYPE": "const char*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        const_field_artifact = deepcopy(artifact)
        const_field_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_field", "source_ref": "payload->text", "source_type": "char*",
        })
        _validate_partition_artifact(
            stage, const_field_artifact, partition, registry, context, const_field_previous
        )

        prior_result_previous = deepcopy(previous)
        prior_interfaces = prior_result_previous[-1]["artifact"]["function_interfaces"]
        prior_interfaces[1]["signature"]["RAW"] = "char* callee(int value)"
        prior_interfaces[1]["signature"]["RETURN"] = "char*"
        prior_interfaces[2]["signature"] = {
            "RAW": "bool sink(const char* value)", "NAME": "sink", "RETURN": "bool",
            "PARAMS": [{"TYPE": "const char*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        prior_result_artifact = deepcopy(artifact)
        prior_result_artifact["call_edges"].append({
            **deepcopy(edge),
            "callee_function_id": sink_id,
            "argument_semantics": [{
                "parameter": "value", "source_kind": "prior_result",
                "source_ref": callee_id, "source_type": "char*",
            }],
        })
        _validate_partition_artifact(
            stage, prior_result_artifact, partition, registry, context, prior_result_previous
        )

        void_context_previous = deepcopy(field_previous)
        void_context_previous[-1]["artifact"]["function_interfaces"][1]["signature"] = {
            "RAW": "bool callee(void* value)", "NAME": "callee", "RETURN": "bool",
            "PARAMS": [{"TYPE": "void*", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        }
        void_context_artifact = deepcopy(artifact)
        void_context_artifact["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "caller_param", "source_ref": "payload", "source_type": "void*",
        })
        _validate_partition_artifact(
            stage, void_context_artifact, partition, registry, context, void_context_previous
        )

        wire_access = deepcopy(artifact)
        wire_access["call_edges"][0]["argument_semantics"][0].update({
            "source_kind": "access_path", "source_ref": "payload->value",
        })
        previous_with_behavior = [
            *previous,
            {
                "stage_id": "function_behavior_design",
                "artifact": {
                    "function_behaviors": [{
                        "function_id": caller_id,
                        "wire_mapping": [{
                            "packet": "FIXTURE", "wire_field": "value",
                            "strategy": "store_in_field", "target": "payload->value",
                        }],
                    }],
                },
            },
        ]
        _validate_partition_artifact(
            stage, wire_access, partition, registry, context, previous_with_behavior
        )

    def test_stage8_callback_binding_is_typed_and_not_a_direct_provider_call(self) -> None:
        layout = {
            "modules": [{"name": "core"}],
            "files": [{"id": "core.c", "module": "core", "header_path": "core.h", "source_path": "core.c"}],
        }
        inventory = {
            "types": [{"symbol": "event_callback_t", "owner_file": "core.c", "visibility": "public", "kind": "callback"}],
            "functions": [
                {
                    "symbol": name, "owner_file": "core.c", "visibility": "public",
                    "role": (
                        "implement and provide data event_callback_t callback" if name == "event_provider"
                        else "accept and consume event_callback_t callback" if name == "register_callback"
                        else "feed data to decoder" if name == "feed_decoder"
                        else "service"
                    ),
                }
                for name in ("caller", "register_callback", "event_provider", "feed_decoder")
            ],
        }
        registry = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": layout},
                {"stage_id": "public_artifact_inventory", "artifact": inventory},
            ],
            protocol_slug="fixture",
        )
        callback_id = registry.resolve("event_callback_t", expected_kinds={"callback"})["artifact_id"]

        def signature(name: str, parameter_type: str, parameter_name: str = "value") -> dict:
            return {
                "RAW": f"void {name}({parameter_type} {parameter_name})",
                "NAME": name,
                "RETURN": "void",
                "PARAMS": [{"TYPE": parameter_type, "NAME": parameter_name, "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
            }

        previous = [
            {"stage_id": "module_file_plan", "artifact": layout},
            {"stage_id": "public_artifact_inventory", "artifact": inventory},
            {
                "stage_id": "type_and_access_path_design",
                "artifact": {
                    "type_definition_overlays": [
                        {
                            "type_id": callback_id,
                            "definition_overlay": {
                                "type_spec": {
                                    "TYPE_KIND": "CALLBACK",
                                    "CALLBACK_SIGNATURE": "void (*event_callback_t)(int value)",
                                }
                            },
                        }
                    ]
                },
            },
            {
                "stage_id": "function_interface_design",
                "artifact": {
                    "function_interfaces": [
                        {"function_id": "caller", "owner_file": "core.c", "visibility": "public", "signature": signature("caller", "int")},
                        {"function_id": "register_callback", "owner_file": "core.c", "visibility": "public", "signature": signature("register_callback", "event_callback_t", "callback")},
                        {"function_id": "event_provider", "owner_file": "core.c", "visibility": "public", "signature": signature("event_provider", "int")},
                        {
                            "function_id": "feed_decoder", "owner_file": "core.c", "visibility": "public",
                            "signature": {
                                "RAW": "void feed_decoder(void)", "NAME": "feed_decoder", "RETURN": "void", "PARAMS": [],
                            },
                        },
                    ]
                },
            },
        ]
        previous[-1]["artifact"]["function_interfaces"][2]["signature"]["PARAMS"][0]["ROLE"] = "input_buffer"
        _normalize_stage6_callback_data_flow_interfaces(
            previous[-1]["artifact"], previous[:-1], registry
        )
        self.assertEqual(
            previous[-1]["artifact"]["function_interfaces"][3]["signature"]["RAW"],
            "void feed_decoder(int value)",
        )
        caller_id = registry.resolve("caller", expected_kinds={"function"})["artifact_id"]
        consumer_id = registry.resolve("register_callback", expected_kinds={"function"})["artifact_id"]
        provider_id = registry.resolve("event_provider", expected_kinds={"function"})["artifact_id"]
        binding = {
            "binding_id": "binding:event",
            "owner_function_id": caller_id,
            "consumer_function_id": consumer_id,
            "consumer_parameter": "callback",
            "callback_type_id": callback_id,
            "provider_function_id": provider_id,
            "user_data_source": "none",
            "trace_refs": ["fact:minimum_v1"],
        }
        edge = {
            "caller_function_id": caller_id,
            "callee_function_id": consumer_id,
            "call_purpose": "install event provider",
            "condition": {"expression": "runtime initialization", "reachable": True},
            "argument_semantics": [
                {"parameter": "callback", "source_kind": "callback_binding", "source_ref": "binding:event", "source_type": "event_callback_t"}
            ],
            "result_usage": {"usage": "ignored", "target": ""},
            "trace_refs": ["fact:minimum_v1"],
        }
        artifact = {"call_edges": [edge], "callback_bindings": [binding], "artifact_requests": [], "call_diagnostics": []}
        partition = {"partition_id": "core", "caller_function_ids": [caller_id]}
        callback_partition = {
            "partition_id": "core_callbacks",
            "caller_function_ids": [caller_id, provider_id],
        }
        _attach_required_callback_bindings(
            [callback_partition], previous, registry
        )
        self.assertEqual(callback_partition["required_callback_bindings"], [{
            "callback_type_id": callback_id,
            "consumer_function_id": consumer_id,
            "provider_function_id": provider_id,
        }])
        context = {"required_implementation_obligations": [{"obligation_id": "fixture"}]}
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        _validate_partition_artifact(stage, artifact, partition, registry, context, previous)
        merged = _merge_function_artifacts(
            [*previous, {"stage_id": "function_call_contract_closure", "artifact": artifact}], registry
        )
        caller = next(item for item in merged if item["id"] == caller_id)
        provider_projection = caller["CALL_CONTRACTS"][0]["argument_semantics"][0]["callback_provider"]
        self.assertEqual(provider_projection["name"], "event_provider")
        self.assertEqual(provider_projection["callback_type_id"], callback_id)

        wrong_owner = deepcopy(artifact)
        wrong_owner["callback_bindings"][0]["owner_function_id"] = provider_id
        _validate_partition_artifact(stage, wrong_owner, partition, registry, context, previous)
        self.assertEqual(wrong_owner["callback_bindings"][0]["owner_function_id"], caller_id)

        missing_binding = deepcopy(artifact)
        missing_binding["callback_bindings"] = []
        with self.assertRaisesRegex(ValueError, "stage8_callback_binding_missing"):
            _validate_partition_artifact(stage, missing_binding, partition, registry, context, previous)

        missing_required_binding = {
            "call_edges": [], "callback_bindings": [],
            "artifact_requests": [], "call_diagnostics": [],
        }
        with self.assertRaisesRegex(ValueError, "stage8_required_callback_binding_missing"):
            _validate_partition_artifact(
                stage, missing_required_binding, callback_partition, registry, context, previous
            )

        unconsumed_required_binding = {
            "call_edges": [],
            "callback_bindings": [{**deepcopy(binding), "owner_function_id": provider_id}],
            "artifact_requests": [], "call_diagnostics": [],
        }
        with self.assertRaisesRegex(ValueError, "stage8_required_callback_binding_missing"):
            _validate_partition_artifact(
                stage, unconsumed_required_binding, callback_partition, registry, context, previous
            )
        self.assertEqual(
            unconsumed_required_binding["callback_bindings"][0]["owner_function_id"], caller_id
        )

        self_binding = {**deepcopy(binding), "owner_function_id": consumer_id, "consumer_function_id": consumer_id}
        provider_edge = {
            **deepcopy(edge),
            "caller_function_id": consumer_id,
            "callee_function_id": provider_id,
            "argument_semantics": [{
                "parameter": "value", "source_kind": "local_value",
                "source_ref": "value", "source_type": "int",
            }],
        }
        false_registration = {
            "call_edges": [provider_edge], "callback_bindings": [self_binding],
            "artifact_requests": [], "call_diagnostics": [],
        }
        _validate_partition_artifact(
            stage, false_registration,
            {"partition_id": "consumer", "caller_function_ids": [consumer_id]},
            registry, context, previous,
        )
        self.assertEqual(false_registration["call_edges"], [])
        self.assertEqual(false_registration["callback_bindings"], [])

    def test_stage8_typed_edge_rejects_type_id_and_canonical_fields(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        caller = registry.typed_view({"function"})[0]["artifact_id"]
        type_id = registry.typed_view({"type"})[0]["artifact_id"]
        edge = {
            "caller_function_id": caller,
            "callee_function_id": type_id,
            "call_purpose": "invalid",
            "condition": "always",
            "argument_semantics": "",
            "result_usage": "",
        }
        artifacts = [
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
            {"stage_id": "function_call_contract_closure", "artifact": {"call_edges": [edge]}},
        ]
        with self.assertRaisesRegex(ValueError, "artifact_kind_mismatch"):
            _merge_function_artifacts(artifacts, registry)
        edge["callee_function_id"] = caller
        edge["SIGNATURE"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "typed_delta_invalid_shape"):
            _merge_function_artifacts(artifacts, registry)

    def test_structured_planning_stage_catalog_is_explicit(self) -> None:
        stage_ids = [stage["stage_id"] for stage in planning_stage_catalog()]
        self.assertEqual(stage_ids[0], "scope_fact_inventory")
        self.assertNotIn("function_contract_design", stage_ids)
        self.assertIn("function_interface_design", stage_ids)
        self.assertIn("function_behavior_design", stage_ids)
        self.assertIn("function_call_contract_closure", stage_ids)
        self.assertIn("function_test_vector_design", stage_ids)
        self.assertEqual(stage_ids[-1], "final_plan_assembly")
        self.assertEqual(len(stage_ids), 11)
        self.assertEqual(planning_resume_points(), [*stage_ids, "compile_specs"])

    def test_stage_prompt_carries_specs_contract_and_previous_artifacts(self) -> None:
        facts = read_json(FACTS)
        characteristics = normalize_characteristics(facts)
        rules = activate_engineering_rules(characteristics)
        assumptions = extract_open_assumptions(facts, characteristics)
        context = build_planning_context(facts, characteristics, rules, assumptions)
        prompt = build_stage_prompt(PLANNING_STAGES[1], context, [{"stage_id": "scope_fact_inventory", "artifact": {"ok": True}}])
        self.assertEqual(prompt, json.dumps(json.loads(prompt), ensure_ascii=False, separators=(",", ":")))
        self.assertIn("PROTOCOL_MODULE_SPEC", prompt)
        self.assertIn("FILE_SPEC", prompt)
        self.assertIn("FUNCTION_SPEC", prompt)
        self.assertIn("scope_fact_inventory", prompt)

        inventory_stage = next(
            item for item in PLANNING_STAGES if item.stage_id == "public_artifact_inventory"
        )
        inventory_prompt = json.loads(build_stage_prompt(inventory_stage, context, []))
        opaque_rule = inventory_prompt["stage"]["artifact_boundary"]["runtime_context_rule"]
        self.assertIn("every state/resource handle used outside its owner", opaque_rule)
        self.assertIn("cannot expose fields", opaque_rule)

        type_stage = next(item for item in PLANNING_STAGES if item.stage_id == "type_and_access_path_design")
        type_prompt = json.loads(
            build_stage_prompt(
                type_stage,
                context,
                [{"stage_id": "public_artifact_inventory", "artifact": {"types": [{"symbol": "fixture_t"}]}}],
            )
        )
        self.assertEqual(type_prompt["stage"]["artifact_boundary"]["allowed_stable_ids"], ["fixture_t"])
        self.assertEqual(type_prompt["stage"]["artifact_boundary"]["mode"], "typed_delta")
        request_contract = type_prompt["global_contract"]["controlled_inventory_amendment"]
        self.assertIn("requested_kind", request_contract["artifact_request_required_fields"])
        self.assertIn("provenance", request_contract["artifact_request_required_fields"])
        self.assertIn("proposed_name", request_contract["artifact_request_required_fields"])
        self.assertNotIn("optional_fields", request_contract)

        call_stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        call_prompt = json.loads(
            build_stage_prompt(
                call_stage,
                context,
                [],
                partition={"partition_id": "fixture", "caller_function_ids": []},
                registry=CanonicalPlanningRegistry(),
            )
        )
        self.assertTrue(call_prompt["partition_contract"]["type_payload_field_and_parameter_access_are_not_call_edges"])
        self.assertTrue(call_prompt["partition_contract"]["remove_invalid_edge_before_artifact_request"])

        dependency_stage = next(item for item in PLANNING_STAGES if item.stage_id == "dependency_closure")
        dependency_prompt = json.loads(build_stage_prompt(dependency_stage, context, []))
        self.assertEqual(
            dependency_prompt["stage"]["artifact_boundary"]["choice_item_schema"]["required_fields"],
            ["reason", "provenance", "affected_artifact_ids"],
        )

    def test_fact_slice_preserves_exact_values_and_evidence(self) -> None:
        facts = {
            "protocol_meta": {"name": "Fixture"},
            "messages": [{"name": "PING", "evidence_refs": ["EV1"]}],
            "evidence_index": [
                {"evidence_id": "EV1", "text": "exact evidence"},
                {"evidence_id": "EV2", "text": "unrelated"},
            ],
        }
        sliced = select_fact_slice(facts, ["fact:messages.0.name", "fact:EV1"])
        self.assertEqual(sliced["fact_slices"][0]["value"], "PING")
        self.assertEqual([item["evidence_id"] for item in sliced["evidence_index"]], ["EV1"])

    def test_fact_slice_groups_equivalent_resolved_subtrees_without_losing_refs(self) -> None:
        facts = {
            "messages": [
                {"name": "CONNECT", "evidence_refs": ["EV1"]},
                {"name": "PUBLISH", "evidence_refs": ["EV2"]},
            ],
            "same_values": {"left": 1, "right": 1},
            "evidence_index": [
                {"evidence_id": "EV1", "text": "connect"},
                {"evidence_id": "EV2", "text": "publish"},
            ],
        }
        sliced = select_fact_slice(
            facts,
            [
                "fact:messages.CONNECT",
                "fact:messages.PUBLISH",
                "fact:same_values.left",
                "fact:same_values.right",
            ],
        )
        message_slice = sliced["fact_slices"][0]
        self.assertEqual(message_slice["fact_ref"], "fact:messages.CONNECT")
        self.assertEqual(message_slice["equivalent_fact_refs"], ["fact:messages.PUBLISH"])
        self.assertEqual(message_slice["resolved_path"], "messages")
        self.assertEqual(message_slice["value"], facts["messages"])
        self.assertEqual(
            [(item["resolved_path"], item["value"]) for item in sliced["fact_slices"][1:]],
            [("same_values.left", 1), ("same_values.right", 1)],
        )
        self.assertEqual(
            [item["evidence_id"] for item in sliced["evidence_index"]], ["EV1", "EV2"]
        )

    def test_stage5_prompt_preserves_unprefixed_evidence_ids(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        type_id = registry.typed_view({"type"})[0]["artifact_id"]
        previous = [
            {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {"types": [{"id": type_id, "trace_refs": ["EV1"]}], "functions": []},
            },
        ]
        context = {
            "facts": {
                "protocol_meta": {"name": "Fixture"}, "minimum_v1": {},
                "evidence_index": [{"evidence_id": "EV1", "text": "exact enum value evidence"}],
            },
            "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
        }
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "type_and_access_path_design")
        prompt = json.loads(build_stage_prompt(
            stage, context, previous,
            partition={"partition_id": "core", "owner_file_id": plan["files"][0]["id"], "type_ids": [type_id]},
            registry=registry,
        ))
        self.assertEqual(prompt["facts"]["evidence_index"][0]["evidence_id"], "EV1")

    def test_stage6_prompt_exposes_exact_registered_abi_vocabulary(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        previous = [
            {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
            {"stage_id": "public_artifact_inventory", "artifact": {"types": plan["types"], "functions": plan["functions"]}},
            {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
        ]
        context = {
            "facts": {"protocol_meta": {"name": "Fixture"}, "minimum_v1": {}},
            "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
            "required_wire_mapping_targets": [],
        }
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_interface_design")
        prompt = json.loads(build_stage_prompt(stage, context, previous, registry=registry))
        catalog = prompt["registry_catalog"]
        self.assertTrue(catalog)
        self.assertTrue(all(item.get("canonical_name") for item in catalog))
        self.assertEqual(
            prompt["stage"]["artifact_boundary"]["signature_schema"]["NAME"],
            "exact canonical function name",
        )
        self.assertIn(
            "uppercase NULLABLE",
            prompt["stage"]["artifact_boundary"]["signature_schema"]["PARAMS"],
        )
        self.assertEqual(
            prompt["stage"]["artifact_boundary"]["required_wire_function_ids"],
            [],
        )
        self.assertIn("Never invent", prompt["stage"]["artifact_boundary"]["public_abi_rule"])

    def test_stage9_prompt_requires_exact_function_and_file_ids(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        previous = [
            {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
            {"stage_id": "public_artifact_inventory", "artifact": {"types": plan["types"], "functions": plan["functions"]}},
            {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
            {"stage_id": "function_behavior_design", "artifact": {"function_behaviors": [], "wire_mappings": []}},
            {"stage_id": "function_call_contract_closure", "artifact": {"call_edges": []}},
        ]
        context = {
            "facts": {"protocol_meta": {"name": "Fixture"}, "minimum_v1": {}},
            "characteristics": {}, "engineering_rules": [], "open_assumptions": [],
        }
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_test_vector_design")
        prompt = json.loads(build_stage_prompt(stage, context, previous, registry=registry))
        boundary = prompt["stage"]["artifact_boundary"]
        self.assertEqual(
            set(boundary["file_id_schema"]["enum"]),
            {item["artifact_id"] for item in registry.typed_view({"file"})},
        )
        self.assertEqual(
            set(boundary["function_id_schema"]["enum"]),
            {item["artifact_id"] for item in registry.typed_view({"function"})},
        )
        self.assertTrue(prompt["registry_catalog"])

    def test_stage8_prompt_projects_to_current_callers_and_compact_catalog(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        caller_ids = [item["artifact_id"] for item in registry.typed_view({"function"})]
        previous = [
            {"stage_id": "scope_fact_inventory", "artifact": {"fact_inventory": [{"large": "unused"}]}},
            {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {
                    "functions": [
                        {"symbol": item["name"], "owner_file": item["file"], "role": item["role"]}
                        for item in plan["functions"]
                    ],
                    "lifecycle_matrix": {},
                    "runtime_entrypoint": {},
                },
            },
            {
                "stage_id": "type_and_access_path_design",
                "artifact": {
                    "type_definition_overlays": [{
                        "type_id": plan["types"][0]["id"],
                        "definition_overlay": {
                            "fields": [{"name": "value", "c_type": "int"}],
                            "access_paths": ["core->value"],
                        },
                    }],
                },
            },
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
            {
                "stage_id": "function_behavior_design",
                "artifact": {
                    "function_behaviors": [
                        {"function_id": item["id"], "LOGIC": {"ACTION": item["name"]}}
                        for item in plan["functions"]
                    ]
                },
            },
        ]
        context = {
            "facts": {"protocol_meta": {"protocol_name": "MQTT"}, "minimum_v1": {"scope": "fixture"}},
            "characteristics": {},
            "engineering_rules": [],
            "open_assumptions": [],
        }
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        prompt = json.loads(
            build_stage_prompt(
                stage,
                context,
                previous,
                partition={"partition_id": "one", "caller_function_ids": [caller_ids[0]]},
                registry=registry,
            )
        )
        self.assertNotIn("scope_fact_inventory", {item["stage_id"] for item in prompt["previous_stage_artifacts"]})
        behaviors = next(
            item["artifact"]["function_behaviors"]
            for item in prompt["previous_stage_artifacts"]
            if item["stage_id"] == "function_behavior_design"
        )
        self.assertEqual([item["function_id"] for item in behaviors], [caller_ids[0]])
        catalog_ids = {item["artifact_id"] for item in prompt["registry_catalog"]}
        self.assertTrue(set(caller_ids).issubset(catalog_ids))
        boundary = prompt["stage"]["artifact_boundary"]
        self.assertEqual(
            {item["function_id"] for item in boundary["function_signature_catalog"]},
            {item["id"] for item in plan["functions"]},
        )
        projected_interfaces = next(
            item["artifact"]["function_interfaces"]
            for item in prompt["previous_stage_artifacts"]
            if item["stage_id"] == "function_interface_design"
        )
        self.assertTrue(all("signature" not in item for item in projected_interfaces))
        self.assertTrue(all("visibility" not in item for item in projected_interfaces))
        self.assertEqual(
            {
                item["function_id"]: (item["signature"], item["visibility"])
                for item in boundary["function_signature_catalog"]
            },
            {
                item["id"]: (item["signature"], item["visibility"])
                for item in plan["functions"]
            },
        )
        self.assertIn(
            "exact canonical function ID",
            boundary["argument_binding_schema"]["source_rules"]["prior_result"],
        )
        self.assertIn("empty call_edges", boundary["edge_minimization_rule"])
        self.assertEqual(boundary["caller_field_catalog"][0]["fields"], [{"name": "value", "type": "int"}])
        self.assertEqual(boundary["allowed_access_provider_refs"], ["core->value"])

    def test_dependency_stage_skips_llm_without_ambiguity(self) -> None:
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "dependency_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            artifact = planner._run_stage(stage, context, [])
        self.assertEqual(artifact["dependency_diagnostics"], [])
        self.assertEqual(planner.stage_records[-1]["mode"], "deterministic_no_ambiguity")
        self.assertEqual(planner.stage_records[-1]["request_count"], 0)

    def test_dependency_stage_calls_llm_once_with_ambiguity(self) -> None:
        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                prompt = json.loads(request.messages[-1]["content"])
                self.assert_prompt(prompt)
                content = json.dumps(
                    {
                        "ordering_choices": [],
                        "architecture_choices": [],
                        "artifact_requests": [],
                        "dependency_diagnostics": [],
                    }
                )
                return LLMResponse(content=content, usage=LLMUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3))

            @staticmethod
            def assert_prompt(prompt):
                assert prompt["open_assumptions"][0]["assumption_id"] == "ASM001"

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "dependency_closure")
        context = {
            "facts": {},
            "characteristics": {},
            "engineering_rules": [],
            "open_assumptions": [{"assumption_id": "ASM001", "fact_refs": ["fact:open_questions"]}],
        }
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner._run_stage(stage, context, [])
        self.assertEqual(FakeClient.calls, 1)
        self.assertEqual(planner.stage_records[-1]["request_count"], 1)

    def test_final_plan_assembly_is_deterministic_reconciliation(self) -> None:
        base = _minimal_plan()
        context = {
            "facts": {"settings": [{"name": "default_port", "value_or_rule": "1884 for fixture"}]},
            "characteristics": {"protocol_name": "MQTT", "protocol_slug": "mqtt", "spec_version": "3.1.1", "target_roles": ["BROKER"]},
            "engineering_rules": [],
            "open_assumptions": [],
        }
        artifacts = [
            {"stage_id": "scope_fact_inventory", "artifact": {"confirmed_scope": ["fixture scope"]}},
            {"stage_id": "architecture_boundaries", "artifact": {"module_candidates": [], "ownership_decisions": []}},
            {"stage_id": "module_file_plan", "artifact": {"modules": base["modules"], "files": base["files"]}},
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {
                    "forbidden_symbols": [],
                    "constants_or_macros": [
                        {"id": "constant:fixture", "symbol": "FIXTURE_LEVEL", "owner_file": base["files"][0]["id"], "value": 4}
                    ],
                },
            },
            {"stage_id": "type_and_access_path_design", "artifact": {"types": base["types"]}},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": base["functions"]}},
            {"stage_id": "function_behavior_design", "artifact": {}},
            {"stage_id": "function_call_contract_closure", "artifact": {}},
            {
                "stage_id": "function_test_vector_design",
                "artifact": {
                    "file_test_vectors": {
                        base["files"][0]["id"]: [
                            {"name": "file_fixture", "input": {"value": 1}, "expect": {"value": 1}, "trace_refs": ["fact:fixture"]}
                        ]
                    },
                    "runtime_test_vectors": [
                        {"name": "runtime_fixture", "input": {"argv": []}, "expect": {"exit": 0}, "trace_refs": ["fact:fixture"]}
                    ],
                },
            },
            {
                "stage_id": "dependency_closure",
                "artifact": {
                    "modules": [{"name": base["modules"][0]["name"], "dependencies": []}],
                    "files": [{"id": base["files"][0]["id"], "header_dependencies": [], "source_dependencies": ["core.h"]}],
                    "functions": [],
                },
            },
        ]
        assembled = _assemble_final_plan_candidate(context, artifacts)
        self.assertEqual(assembled["protocol"]["default_port"], 1884)
        self.assertEqual(len(assembled["functions"]), 2)
        self.assertEqual(assembled["constants_or_macros"][0]["symbol"], "FIXTURE_LEVEL")
        self.assertEqual(assembled["files"][0]["test_vectors"][0]["name"], "file_fixture")
        self.assertEqual(assembled["test_vectors"][0]["input"], {"argv": []})
        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            artifact = planner._run_stage(PLANNING_STAGES[-1], context, artifacts)
            self.assertEqual(artifact, assembled)
            self.assertEqual(planner.stage_records[0]["mode"], "deterministic_reconciliation")
            self.assertTrue((Path(raw) / "01_final_plan_assembly" / "response.raw.txt").exists())

    def test_run3_regression_joins_incomplete_function_overlay_by_stable_id(self) -> None:
        base = _minimal_plan()
        caller, callee = base["functions"]
        caller["rely"] = {"STRUCT": [], "FUNC": [{"NAME": callee["name"], "KIND": "CALL", "ROLE": "fixture"}], "VAR": []}
        artifacts = [
            {"stage_id": "scope_fact_inventory", "artifact": {}},
            {"stage_id": "architecture_boundaries", "artifact": {}},
            {"stage_id": "module_file_plan", "artifact": {"modules": base["modules"], "files": base["files"]}},
            {"stage_id": "public_artifact_inventory", "artifact": {"types": [], "forbidden_symbols": []}},
            {"stage_id": "type_and_access_path_design", "artifact": {"types": base["types"]}},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": [caller, callee]}},
            {"stage_id": "function_behavior_design", "artifact": {"function_behaviors": []}},
            {
                "stage_id": "function_call_contract_closure",
                "artifact": {
                    "rely_by_function": {caller["id"]: caller["rely"]},
                    "call_contracts": [{"caller": caller["id"], "callee": callee["id"], "NAME": callee["name"], "SIGNATURE": callee["name"]}],
                },
            },
            {"stage_id": "function_test_vector_design", "artifact": {"function_test_vectors": {}}},
            {
                "stage_id": "dependency_closure",
                "artifact": {
                    "modules": [{"name": base["modules"][0]["name"], "dependencies": []}],
                    "files": [{"id": base["files"][0]["id"], "header_dependencies": [], "source_dependencies": ["core.h"]}],
                    "functions": [{"function_id": caller["id"], "interfaces": {"CALL_CONTRACTS": [{"callee": callee["id"], "NAME": callee["name"], "SIGNATURE": callee["name"]}]}}],
                },
            },
        ]
        context = {"facts": {}, "characteristics": {"protocol_name": "Fixture", "protocol_slug": "fixture"}, "engineering_rules": [], "open_assumptions": []}
        assembled = _assemble_final_plan_candidate(context, artifacts)
        assembled_callee = next(item for item in assembled["functions"] if item["id"] == callee["id"])
        assembled_caller = next(item for item in assembled["functions"] if item["id"] == caller["id"])
        self.assertEqual(assembled_callee["signature"], callee["signature"])
        self.assertEqual(assembled_callee["file"], callee["file"])
        self.assertEqual(assembled_caller["CALL_CONTRACTS"][0]["SIGNATURE"], callee["signature"]["RAW"])

        artifacts[-1]["artifact"]["functions"][0]["function_id"] = "function:missing"
        with self.assertRaisesRegex(ValueError, "overlay_unknown_stable_id"):
            _assemble_final_plan_candidate(context, artifacts)
        with tempfile.TemporaryDirectory() as raw:
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            with self.assertRaisesRegex(ValueError, "overlay_unknown_stable_id"):
                planner._run_stage(PLANNING_STAGES[-1], context, artifacts)
            self.assertTrue((Path(raw) / "01_final_plan_assembly" / "stage_validation_errors.json").exists())

    def test_pipeline_uses_staged_llm_output_without_local_planner(self) -> None:
        def fake_stage(self, stage, context, previous_artifacts):
            if stage.stage_id == "final_plan_assembly":
                return _minimal_plan()
            return _empty_stage_artifact(stage.stage_id)

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage
        ), patch("agent.planning.planner._derive_implementation_obligations", return_value=[]):
            result = run_planning(FACTS, Path(raw) / "run")
            self.assertTrue(result.success, result.diagnostics)
            self.assertEqual(result.run_status, "completed_with_qualified_specs")
            self.assertIsNotNone(result.specs_root)
            manifest = read_json(result.manifest_path)
            self.assertTrue(Path(manifest["candidate_specs_root"]).exists())
            self.assertTrue(manifest["qualification_passed"])
            self.assertTrue(manifest["specs_generated"])
            self.assertTrue(manifest["planning_validation_passed"])
            self.assertTrue(manifest["coder_loader_passed"])
            self.assertTrue(manifest["artifact_success"])
            self.assertTrue(manifest["semantic_qualified"])
            self.assertFalse(manifest["implementation_ready"])
            self.assertEqual(manifest["union_diagnostic_counts"]["error"], 0)
            self.assertTrue(manifest["fresh"])
            self.assertFalse(manifest["resume"])
            self.assertEqual(manifest["stage_survival"]["expected"], 11)
            self.assertTrue((result.candidate_root / "manifest.json").exists())
            self.assertTrue((result.planning_root / "structured_planning_stages.json").exists())
            bundle = load_spec_bundle_from_root(result.specs_root, validate_rendered_headers=True)
            self.assertFalse(bundle.has_errors(), bundle.diagnostics)

            facts = read_json(FACTS)
            plan = read_json(result.planning_root / "implementation_plan.json")
            self.assertEqual(schema_shape_check(result.specs_root), [])
            self.assertEqual(structural_consistency_check(plan, result.specs_root), [])
            self.assertEqual(plan_to_spec_preservation_check(plan, result.specs_root), [])
            self.assertEqual(fact_decision_assumption_separation_check(stable_json_hash(facts), facts, plan), [])

    def test_closed_no_repair_fixture_is_implementation_ready(self) -> None:
        ready_plan = _implementation_ready_plan()

        def fake_stage(self, stage, context, previous_artifacts):
            del self, context, previous_artifacts
            return deepcopy(ready_plan) if stage.stage_id == "final_plan_assembly" else _empty_stage_artifact(stage.stage_id)

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage
        ), patch("agent.planning.planner._derive_implementation_obligations", return_value=[]):
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)

        self.assertEqual(result.run_status, "completed_with_qualified_specs", result.diagnostics)
        self.assertTrue(manifest["artifact_success"])
        self.assertTrue(manifest["semantic_qualified"])
        self.assertTrue(manifest["implementation_ready"])
        self.assertEqual(
            manifest["readiness_metrics"]["required_function_count"],
            manifest["readiness_metrics"]["materialized_function_spec_count"],
        )
        self.assertTrue(manifest["readiness_metrics"]["runtime_contract_materialized"])

    def test_failure_before_serializable_inventory_is_fatal_and_materialized(self) -> None:
        def fail_with_committed_stage(planner, context, *, resume_from=None):
            del context, resume_from
            planner.stage_records = [
                {
                    "stage_id": "scope_fact_inventory",
                    "status": "completed",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
                {
                    "stage_id": "type_and_access_path_design",
                    "status": "failed",
                    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "stage_validation_error": "overlay_unknown_stable_id: unregistered type",
                },
            ]
            stage_dir = planner.stage_log_dir / "01_scope_fact_inventory"
            write_json(stage_dir / "artifact.json", {"confirmed_scope": ["fixture"]})
            write_json(stage_dir / "stage_manifest.json", planner.stage_records[0])
            write_json(planner.stage_log_dir / "stage_records.json", planner.stage_records)
            raise RecoverablePlanningError("type_and_access_path_design", "overlay_unknown_stable_id: unregistered type")

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", fail_with_committed_stage
        ), patch("agent.planning.pipeline._coder_validate") as coder_validate:
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)
            package = read_json(result.candidate_root / "manifest.json")
            committed = json.loads((result.candidate_root / "committed_overlays.json").read_text(encoding="utf-8"))
            self.assertEqual(result.run_status, "failed_internal")
            self.assertFalse(result.success)
            self.assertIsNone(result.specs_root)
            self.assertIsNone(manifest["specs_root"])
            self.assertTrue(manifest["fatal"])
            self.assertEqual(manifest["fatal_reason_code"], "candidate_serialization_impossible")
            self.assertEqual(manifest["hard_failure_code"], "candidate_serialization_impossible")
            self.assertEqual(manifest["nonfatal_no_specs_count"], 0)
            self.assertEqual([item["stage_id"] for item in committed], ["scope_fact_inventory"])
            self.assertTrue(package["unresolved_partitions"].endswith("unresolved_partitions.json"))
            self.assertEqual(read_json(result.planning_root / "run_metrics.json")["token_accounting"]["total_tokens"], 14)
            coder_validate.assert_not_called()

    def test_artifact_request_produces_candidate_and_amendment_audit(self) -> None:
        base = _minimal_plan()

        def fake_stage(planner, stage, context, previous_artifacts):
            if stage.stage_id == "final_plan_assembly":
                artifact = _assemble_final_plan_candidate(
                    context, previous_artifacts, registry=planner.registry, allow_incomplete=True
                )
                artifact["unresolved_partitions"] = deepcopy(planner.unresolved_partitions)
                return artifact
            if stage.stage_id == "module_file_plan":
                return {"modules": base["modules"], "files": base["files"]}
            if stage.stage_id == "public_artifact_inventory":
                return {
                    "types": base["types"],
                    "functions": base["functions"],
                    "constants_or_macros": [],
                    "forbidden_symbols": [],
                }
            if stage.stage_id == "type_and_access_path_design":
                artifact = {
                    "type_definition_overlays": [],
                    "artifact_requests": [
                        {
                            "requested_kind": "callback",
                            "proposed_name": "fixture_callback_t",
                            "semantic_role": "Adapt a typed event callback.",
                            "requested_owner": "core.h",
                            "required_by": base["functions"][0]["id"],
                            "reason": "The planned interface requires a callback identity.",
                            "provenance": {"kind": "inferred_engineering_decision", "refs": ["RULE_FIXTURE"]},
                            "preferred_visibility": "public",
                        }
                    ],
                    "type_dependency_notes": [],
                    "type_diagnostics": [],
                }
                planner._process_artifact_requests(stage, artifact)
                return artifact
            return _empty_stage_artifact(stage.stage_id)

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage
        ), patch("agent.planning.planner._derive_implementation_obligations", return_value=[]), patch(
            "agent.planning.pipeline._coder_validate"
        ) as coder_validate:
            result = run_planning(FACTS, Path(raw) / "run")
            amendment_log = read_json(result.planning_root / "inventory_amendments.json")
            snapshot = read_json(result.candidate_root / "canonical_registry_snapshot.json")
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertEqual(amendment_log["records"][0]["status"], "accepted_pending_rerun")
            self.assertIn("callback:fixture_callback_t", {item["artifact_id"] for item in snapshot["entries"]})
            self.assertIsNotNone(read_json(result.candidate_root / "manifest.json")["inventory_amendments"])
            coder_validate.assert_called_once_with(result.specs_root)

    def test_qualification_error_keeps_specs_inside_candidate_package(self) -> None:
        def fake_stage(self, stage, context, previous_artifacts):
            del self, context, previous_artifacts
            return _minimal_plan() if stage.stage_id == "final_plan_assembly" else _empty_stage_artifact(stage.stage_id)

        qualification_error = Diagnostic("error", "fixture_qualification_error", "fixture gate remains strict")
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage
        ), patch("agent.planning.planner._derive_implementation_obligations", return_value=[]), patch(
            "agent.planning.pipeline.validate_planning_run", return_value=[qualification_error]
        ):
            run_dir = Path(raw) / "run"
            result = run_planning(FACTS, run_dir, coder_validate=False)
            manifest = read_json(result.manifest_path)
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertEqual(result.specs_root, Path(manifest["candidate_specs_root"]))
            self.assertEqual(manifest["specs_root"], manifest["candidate_specs_root"])
            self.assertFalse(manifest["qualification_passed"])
            self.assertTrue(manifest["specs_generated"])
            self.assertFalse(manifest["fatal"])
            self.assertIsNone(manifest["fatal_reason_code"])
            self.assertEqual(manifest["nonfatal_no_specs_count"], 0)
            self.assertEqual(manifest["semantic_diagnostic_counts"]["error"], 0)
            self.assertTrue(Path(manifest["candidate_specs_root"]).exists())
            self.assertFalse((run_dir / "mqtt_specs").exists())

    def test_semantic_patch_invalid_still_materializes_specs_and_preserves_diagnostic(self) -> None:
        plan = _minimal_plan()
        report = {
            "final_diagnostics": [
                {
                    "level": "error",
                    "code": "semantic_patch_invalid",
                    "message": "fixture patch remains invalid",
                    "artifact_ids": [],
                    "details": {},
                }
            ]
        }
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch(
            "agent.planning.pipeline._close_implementability", return_value=(plan, report, usage)
        ), patch("agent.planning.pipeline._coder_validate", return_value=[]) as coder_validate:
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertTrue(result.success)
            self.assertTrue(result.specs_root.exists())
            self.assertFalse(manifest["qualification_passed"])
            self.assertEqual(manifest["semantic_diagnostic_counts"]["error"], 1)
            diagnostic = next(item for item in result.diagnostics if item.code == "semantic_patch_invalid")
            self.assertEqual(diagnostic.level, "error")
            coder_validate.assert_called_once_with(result.specs_root)

    def test_semantic_patch_registry_invariant_becomes_diagnostic_and_specs(self) -> None:
        plan = _minimal_plan()
        closure_error = {
            "level": "error", "code": "fixture_closure", "message": "fixture requires patch",
            "artifact_ids": [], "details": {},
        }
        patch_value = {"patch_id": "fixture", "operations": []}
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch(
            "agent.planning.pipeline.analyze_implementability", return_value=[closure_error]
        ), patch(
            "agent.planning.pipeline.request_semantic_patch", return_value=(patch_value, usage)
        ), patch(
            "agent.planning.pipeline._apply_registry_additions",
            side_effect=RegistryInvariantError("semantic_patch_noncanonical_id: fixture"),
        ) as registry_additions:
            result = run_planning(FACTS, Path(raw) / "run")
        self.assertTrue(result.success, result.diagnostics)
        self.assertIn("semantic_patch_invalid", {item.code for item in result.diagnostics})
        registry_additions.assert_called()

    def test_semantic_failed_specs_with_missing_optional_overlays_load_in_real_coder(self) -> None:
        plan = _minimal_plan()
        for function in plan["functions"]:
            for field in ("logic", "event", "LOGIC", "EVENT", "call_contracts", "CALL_CONTRACTS", "test_vectors", "TEST_VECTORS"):
                function.pop(field, None)
        report = {
            "final_diagnostics": [
                {
                    "level": "error",
                    "code": "fixture_semantic_gap",
                    "message": "optional semantic overlays remain unresolved",
                    "artifact_ids": [],
                    "details": {},
                }
            ]
        }
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch(
            "agent.planning.pipeline._close_implementability", return_value=(plan, report, usage)
        ):
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)
            bundle = load_spec_bundle_from_root(result.specs_root, validate_rendered_headers=True)
            specs = [read_json(path) for path in result.specs_root.rglob("*_spec.json")]
        self.assertTrue(result.success, result.diagnostics)
        self.assertEqual(result.run_status, "completed_with_candidate_only")
        self.assertFalse(manifest["qualification_passed"])
        self.assertTrue(manifest["specs_generated"])
        self.assertEqual(manifest["specs_root"], manifest["candidate_specs_root"])
        self.assertTrue(manifest["coder_loader_passed"])
        self.assertFalse(manifest["fatal"])
        self.assertEqual(manifest["nonfatal_no_specs_count"], 0)
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)
        self.assertEqual(sum(item.get("KIND") == "PROTOCOL_MODULE_SPEC" for item in specs), 1)
        self.assertEqual(sum(item.get("KIND") == "FILE_SPEC" for item in specs), len(plan["files"]))
        self.assertEqual(sum(item.get("KIND") == "FUNCTION_SPEC" for item in specs), len(plan["functions"]))
        self.assertEqual(set(bundle.function_specs_by_trace), {item["trace_id"] for item in plan["functions"]})

    def test_coder_loader_failure_keeps_specs_and_is_recorded(self) -> None:
        plan = _minimal_plan()
        coder_error = Diagnostic("error", "coder_validation_exception", "fixture loader failure")
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch("agent.planning.pipeline._coder_validate", return_value=[coder_error]):
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)
            stored = json.loads((result.planning_root / "diagnostics.json").read_text(encoding="utf-8"))
            specs_exist = result.specs_root.exists()
        self.assertFalse(result.success)
        self.assertTrue(specs_exist)
        self.assertEqual(manifest["specs_root"], str(result.specs_root))
        self.assertIn("coder_validation_exception", {item["code"] for item in stored})

    def test_internal_failure_records_failed_internal_before_raising(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", side_effect=RuntimeError("fixture invariant")
        ):
            run_dir = Path(raw) / "run"
            with self.assertRaisesRegex(RuntimeError, "fixture invariant"):
                run_planning(FACTS, run_dir)
            manifest = read_json(run_dir / "_planning/run_manifest.json")
            self.assertEqual(manifest["run_status"], "failed_internal")
            self.assertEqual(manifest["hard_failure_code"], "deterministic_internal_invariant")
            self.assertFalse(manifest["specs_generated"])
            self.assertFalse(manifest["qualification_passed"])

    def test_facts_read_failure_is_materialized_from_hard_failure_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            facts = root / "malformed.json"
            facts.write_text("{not-json", encoding="utf-8")
            run_dir = root / "run"
            with self.assertRaises(json.JSONDecodeError):
                run_planning(facts, run_dir)
            manifest = read_json(run_dir / "_planning/run_manifest.json")
            diagnostics = json.loads((run_dir / "_planning/diagnostics.json").read_text(encoding="utf-8"))
            metrics = read_json(run_dir / "_planning/run_metrics.json")
            self.assertEqual(manifest["run_status"], "failed_internal")
            self.assertEqual(manifest["hard_failure_code"], "facts_read_failure")
            self.assertEqual(diagnostics[0]["code"], "facts_read_failure")
            self.assertEqual(metrics["diagnostic_codes"], ["facts_read_failure"])

    def test_reference_and_hardcoding_scans_are_clean(self) -> None:
        self.assertEqual(reference_isolation_check(), [])
        self.assertEqual(anti_hardcoding_scan(), [])

    def test_facts_sensitivity_changes_rules_and_context(self) -> None:
        self.assertEqual(facts_sensitivity_check(read_json(FACTS)), [])

    def test_partitioned_function_behavior_design_merges_by_file(self) -> None:
        class FakeClient:
            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                prompt = json.loads(request.messages[-1]["content"])
                partition = prompt["current_partition"]
                content = json.dumps(
                    {
                        "function_behaviors": [
                            {
                                "function_id": function["symbol"],
                                "LOGIC": {"INPUT": "", "ACTION": "partition behavior", "OUTPUT": "", "INVARIANTS_USED": []},
                            }
                            for function in partition["functions"]
                        ],
                        "wire_mappings": [],
                        "behavior_diagnostics": [],
                    }
                )
                return LLMResponse(content=content, usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3))

        functions = [
            {"symbol": f"fn_{index}", "owner_file": "a.h" if index < 11 else "b.h", "visibility": "public"}
            for index in range(12)
        ]
        previous = [
            {
                "stage_id": "module_file_plan",
                "title": "",
                "artifact": {
                    "modules": [{"name": "core"}],
                    "files": [
                        {"id": "a.h", "module": "core", "header_path": "a.h", "source_path": "a.c"},
                        {"id": "b.h", "module": "core", "header_path": "b.h", "source_path": "b.c"},
                    ],
                },
            },
            {"stage_id": "public_artifact_inventory", "title": "", "artifact": {"functions": functions}},
        ]
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_behavior_design")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = build_registry(previous, protocol_slug="fixture")
            planner.amendments.set_registry(planner.registry)
            artifact = planner._run_stage(stage, context, previous)
        self.assertEqual(len(artifact["function_behaviors_by_group"]), 2)
        self.assertEqual(len(artifact["function_behaviors"]), 12)
        self.assertEqual(planner.stage_records[-1]["partitions_total"], 2)

    def test_partitioned_stage8_emits_typed_edges_without_canonical_fields(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])

        class FakeClient:
            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                prompt = json.loads(request.messages[-1]["content"])
                caller = prompt["current_partition"]["caller_function_ids"][0]
                callees = prompt["stage"]["artifact_boundary"]["function_id_schema"]["enum"]
                callee = next(item for item in callees if item != caller)
                content = json.dumps(
                    {
                        "call_edges": [
                            {
                                "caller_function_id": caller,
                                "callee_function_id": callee,
                                "call_purpose": "fixture",
                                "condition": "always",
                                "argument_semantics": "borrow",
                                "result_usage": "check",
                            }
                        ],
                        "artifact_requests": [],
                        "call_diagnostics": [],
                    }
                )
                return LLMResponse(content=content, usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3))

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            artifact = planner._run_stage(stage, context, [])
        self.assertEqual(len(artifact["call_edges"]), 1)
        self.assertNotIn("NAME", artifact["call_edges"][0])
        self.assertNotIn("SIGNATURE", artifact["call_edges"][0])
        self.assertEqual(planner.stage_records[-1]["partitions_total"], 1)

    def test_stage8_partition_uses_one_semantic_correction_then_commits(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        functions = registry.typed_view({"function"})
        type_id = registry.typed_view({"type"})[0]["artifact_id"]

        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                callee = type_id if FakeClient.calls == 1 else functions[1]["artifact_id"]
                content = json.dumps(
                    {
                        "call_edges": [
                            {
                                "caller_function_id": functions[0]["artifact_id"],
                                "callee_function_id": callee,
                                "call_purpose": "fixture",
                                "condition": "always",
                                "argument_semantics": "borrow",
                                "result_usage": "check",
                            }
                        ],
                        "artifact_requests": [],
                        "call_diagnostics": [],
                    }
                )
                return LLMResponse(content=content, usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            artifact = planner._run_stage(stage, context, [])
            partition_dir = next(path for path in Path(raw).glob("01_function_call_contract_closure/*") if path.is_dir())
            self.assertTrue((partition_dir / "semantic_correction_usage.json").exists())
            correction_prompt = json.loads(
                (partition_dir / "semantic_correction_prompt.json").read_text(encoding="utf-8")
            )
            correction_payload = json.loads(correction_prompt[-1]["content"])
            recovery = correction_payload["binding_recovery_invariants"]
            self.assertTrue(recovery["type_payload_field_and_parameter_access_are_not_call_edges"])
            self.assertNotIn("global_contract", correction_payload["original_partition_contract"])
        self.assertEqual(artifact["call_edges"][0]["callee_function_id"], functions[1]["artifact_id"])
        self.assertEqual(planner.stage_records[-1]["local_corrections"], 1)
        self.assertEqual(planner.stage_records[-1]["local_correction_successes"], 1)
        self.assertEqual(planner.stage_records[-1]["semantic_correction_usage"]["total_tokens"], 2)
        self.assertEqual(planner.unresolved_partitions, [])

    def test_partition_correction_is_not_blocked_by_retired_stage_ceiling(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        functions = registry.typed_view({"function"})
        invalid_callee = registry.typed_view({"type"})[0]["artifact_id"]

        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                pass

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                content = json.dumps(
                    {
                        "call_edges": [{
                            "caller_function_id": functions[0]["artifact_id"],
                            "callee_function_id": invalid_callee,
                            "call_purpose": "fixture",
                            "condition": "always",
                            "argument_semantics": "borrow",
                            "result_usage": "check",
                        }],
                        "artifact_requests": [],
                        "call_diagnostics": [],
                    }
                )
                return LLMResponse(
                    content=content,
                    usage=LLMUsage(prompt_tokens=180000, completion_tokens=1, total_tokens=180001),
                )

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {
            "facts": {},
            "characteristics": {},
            "engineering_rules": [],
            "open_assumptions": [],
        }
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.common.llm_client.FixedQwenClient", FakeClient
        ):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            artifact = planner._run_stage(stage, context, [])

        self.assertEqual(FakeClient.calls, 2)
        self.assertEqual(artifact["call_edges"], [])
        self.assertEqual(planner.stage_records[-1]["local_corrections"], 1)
        self.assertNotIn("stage_token_budget_reserved", planner.unresolved_partitions[0]["diagnostic"])

    def test_whole_fresh_budget_blocks_request_before_client_call(self) -> None:
        class FakeClient:
            calls = 0

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                return LLMResponse("{}", LLMUsage(1, 1, 2))

        planner = LLMStructuredPlanner()
        planner._model_tokens_used = 783000
        request = LLMRequest(
            messages=[{"role": "user", "content": "fixture"}],
            top_p=0.1,
            temperature=0.0,
            max_completion_tokens=16000,
        )

        with self.assertRaisesRegex(TokenBudgetExceeded, "whole_fresh_token_ceiling"):
            planner._generate_with_budget(FakeClient(), request)

        self.assertEqual(FakeClient.calls, 0)

    def test_partition_budget_exhaustion_materializes_nonfatal_stage_artifact(self) -> None:
        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                pass

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                return LLMResponse("{}", LLMUsage(1, 1, 2))

        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.common.llm_client.FixedQwenClient", FakeClient
        ):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            planner._model_tokens_used = 783000
            artifact = planner._run_stage(stage, context, [])

        self.assertEqual(FakeClient.calls, 0)
        self.assertEqual(artifact["call_edges"], [])
        self.assertEqual(planner.stage_records[-1]["status"], "completed")
        self.assertEqual(planner.stage_records[-1]["request_count"], 0)
        self.assertEqual(
            planner.unresolved_partitions[0]["diagnostic_code"],
            "whole_fresh_token_ceiling",
        )

    def test_failed_partition_correction_rolls_back_and_next_partition_commits(self) -> None:
        stage_artifacts = [
            {
                "stage_id": "module_file_plan",
                "artifact": {
                    "modules": [{"name": "core"}],
                    "files": [
                        {"id": "a.c", "module": "core", "source_path": "a.c", "header_path": "a.h"},
                        {"id": "b.c", "module": "core", "source_path": "b.c", "header_path": "b.h"},
                    ],
                },
            },
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {
                    "types": [{"symbol": "fixture_t", "owner_file": "a.h", "visibility": "public", "kind": "type"}],
                    "functions": [
                        {"symbol": "a_run", "owner_file": "a.c", "visibility": "public"},
                        {"symbol": "b_run", "owner_file": "b.c", "visibility": "public"},
                    ],
                    "constants_or_macros": [],
                },
            },
            {
                "stage_id": "function_interface_design",
                "artifact": {
                    "function_interfaces": [
                        {"function_id": "a_run", "owner_file": "a.c", "visibility": "public", "signature": "void a_run(void)"},
                        {"function_id": "b_run", "owner_file": "b.c", "visibility": "public", "signature": "void b_run(void)"},
                    ]
                },
            },
        ]
        registry = build_registry(stage_artifacts, protocol_slug="fixture")
        type_id = registry.typed_view({"type"})[0]["artifact_id"]

        class FakeClient:
            generation_calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                is_correction = "only correction attempt" in request.messages[0]["content"]
                if not is_correction:
                    FakeClient.generation_calls += 1
                prompt = json.loads(request.messages[-1]["content"])
                if is_correction:
                    original = prompt["original_partition_contract"]
                    caller = original["current_partition"]["caller_function_ids"][0]
                    payload = {
                        "call_edges": [
                            {
                                "caller_function_id": caller,
                                "callee_function_id": type_id,
                                "call_purpose": "still invalid",
                                "condition": "always",
                                "argument_semantics": "",
                                "result_usage": "",
                            }
                        ]
                    }
                else:
                    caller = prompt["current_partition"]["caller_function_ids"][0]
                    payload = (
                        {
                            "call_edges": [
                                {
                                    "caller_function_id": caller,
                                    "callee_function_id": type_id,
                                    "call_purpose": "invalid",
                                    "condition": "always",
                                    "argument_semantics": "",
                                    "result_usage": "",
                                }
                            ]
                        }
                        if FakeClient.generation_calls == 1
                        else {"call_edges": []}
                    )
                payload.update({"artifact_requests": [], "call_diagnostics": []})
                return LLMResponse(content=json.dumps(payload), usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        before = registry.snapshot()
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            artifact = planner._run_stage(stage, context, stage_artifacts)
            manifests = [read_json(path) for path in Path(raw).glob("01_function_call_contract_closure/*/partition_manifest.json")]
        self.assertEqual(len(planner.unresolved_partitions), 1)
        self.assertEqual({item["status"] for item in manifests}, {"completed", "unresolved"})
        self.assertEqual(artifact["call_edges"], [])
        self.assertEqual(registry.snapshot(), before)
        self.assertEqual(planner.stage_records[-1]["local_corrections"], 1)
        self.assertEqual(planner.stage_records[-1]["local_correction_successes"], 0)

    def test_stage5_amendment_runs_only_new_type_partition_and_resolves(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        existing_type = registry.typed_view({"type"})[0]["artifact_id"]
        registry.apply_overlay(existing_type, {"status": "declared"})

        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                prompt = json.loads(request.messages[-1]["content"])
                is_correction = "only correction attempt" in request.messages[0]["content"]
                contract = prompt["original_partition_contract"] if is_correction else prompt
                type_id = contract["current_partition"]["type_ids"][0]
                definition = (
                    {"type_spec": {"TYPE_KIND": "CALLBACK"}, "signature": "void (*fixture_callback_t)(void*)"}
                    if type_id == "callback:fixture_callback_t"
                    else {
                        "type_spec": {"TYPE_KIND": "OPAQUE"},
                        "ownership_model": "owner file creates and destroys the handle",
                        "opaque_boundaries": {"create": "planned constructor", "destroy": "planned destructor"},
                    }
                )
                payload = {
                    "type_definition_overlays": [
                        {
                            "type_id": type_id,
                            "definition_overlay": definition,
                        }
                    ],
                    "artifact_requests": [],
                    "type_dependency_notes": [],
                    "type_diagnostics": [],
                }
                if FakeClient.calls == 1:
                    payload["type_definition_overlays"].append(
                        {
                            "type_id": "callback:fixture_callback_t",
                            "definition_overlay": {"signature": "void (*fixture_callback_t)(void*)"},
                        }
                    )
                elif is_correction:
                    payload["artifact_requests"] = [
                        {
                            "requested_kind": "callback",
                            "proposed_name": "fixture_callback_t",
                            "semantic_role": "Typed callback adapter.",
                            "requested_owner": "core.h",
                            "required_by": plan["functions"][0]["id"],
                            "reason": "A planned interface needs an explicit callback type.",
                            "provenance": {"kind": "inferred_engineering_decision", "refs": ["RULE_FIXTURE"]},
                            "preferred_visibility": "public",
                        }
                    ]
                return LLMResponse(content=json.dumps(payload), usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "type_and_access_path_design")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            artifact = planner._run_stage(stage, context, [])
        callback = registry.resolve("fixture_callback_t", expected_kinds={"callback"})
        self.assertEqual(callback["status"], "defined")
        self.assertEqual(len(artifact["type_definition_overlays"]), 2)
        self.assertEqual(planner.stage_records[-1]["partitions_total"], 2)
        self.assertEqual(planner.stage_records[-1]["local_corrections"], 1)
        self.assertEqual(planner.stage_records[-1]["local_correction_successes"], 1)
        self.assertEqual(planner.amendments.records[0]["status"], "resolved_after_local_rerun")
        self.assertEqual(planner.unresolved_partitions, [])

    def test_stage8_function_amendment_reruns_only_interface_behavior_and_calls(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        required_by = plan["functions"][0]["id"]
        helper_id = "function:mqtt/core/fixture_helper"
        previous = [
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": deepcopy(plan["functions"])}},
            {
                "stage_id": "function_behavior_design",
                "artifact": {"function_behaviors": [], "wire_mappings": [], "behavior_diagnostics": []},
            },
        ]

        class FakeClient:
            stage8_calls = 0

            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.api_key_env = api_key_env

            def generate_with_usage(self, request):
                system = request.messages[0]["content"]
                prompt = json.loads(request.messages[-1]["content"])
                if "bounded inventory-amendment" in system:
                    stage_id = prompt["stage"]["id"]
                    if stage_id == "function_interface_design":
                        payload = {
                            "function_interfaces": [
                                {
                                    "function_id": helper_id,
                                    "owner_file": "file:mqtt/core",
                                    "visibility": "private",
                                    "function_type": "ALGORITHM",
                                    "role": "Fixture helper requested by an existing function.",
                                    "signature": "void fixture_helper(void)",
                                    "trace_refs": ["RULE_FIXTURE"],
                                }
                            ]
                        }
                    else:
                        payload = {
                            "function_behaviors": [
                                {
                                    "function_id": helper_id,
                                    "LOGIC": {
                                        "INPUT": "No input.",
                                        "ACTION": "Perform the requested helper role.",
                                        "OUTPUT": "No return value.",
                                        "INVARIANTS_USED": [],
                                    },
                                }
                            ],
                            "wire_mappings": [],
                            "behavior_diagnostics": [],
                        }
                else:
                    FakeClient.stage8_calls += 1
                    if FakeClient.stage8_calls == 1:
                        payload = {
                            "call_edges": [],
                            "artifact_requests": [
                                {
                                    "requested_kind": "function",
                                    "proposed_name": "fixture_helper",
                                    "semantic_role": "Provide a bounded helper for an existing caller.",
                                    "requested_owner": "core.c",
                                    "required_by": required_by,
                                    "reason": "The caller requires an explicit service rather than an invented inline call.",
                                    "provenance": {"kind": "inferred_engineering_decision", "refs": ["RULE_FIXTURE"]},
                                    "preferred_visibility": "private",
                                }
                            ],
                            "call_diagnostics": [],
                        }
                    else:
                        payload = {
                            "call_edges": [
                                {
                                    "caller_function_id": required_by,
                                    "callee_function_id": helper_id,
                                    "call_purpose": "Use the requested helper.",
                                    "condition": "always",
                                    "argument_semantics": "none",
                                    "result_usage": "none",
                                }
                            ],
                            "artifact_requests": [],
                            "call_diagnostics": [],
                        }
                return LLMResponse(content=json.dumps(payload), usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_call_contract_closure")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = registry
            planner.amendments.set_registry(registry)
            artifact = planner._run_stage(stage, context, previous)
        self.assertEqual(registry.resolve(helper_id, expected_kinds={"function"})["status"], "defined")
        self.assertEqual(len(previous[0]["artifact"]["function_interfaces"]), 3)
        self.assertEqual(len(previous[1]["artifact"]["function_behaviors"]), 1)
        self.assertEqual(artifact["call_edges"][0]["callee_function_id"], helper_id)
        self.assertEqual(planner.stage_records[-1]["partitions_total"], 2)
        self.assertEqual(planner.stage_records[-1]["inventory_amendment_usage"]["total_tokens"], 4)
        self.assertEqual(planner.amendments.records[0]["status"], "resolved_after_local_rerun")
        self.assertEqual(planner.unresolved_partitions, [])

    def test_unresolved_plan_runs_deterministic_completion_and_publishes_candidate_only(self) -> None:
        plan = _minimal_plan()
        plan["unresolved_partitions"] = [
            {
                "stage_id": "function_call_contract_closure",
                "partition_id": "fixture",
                "diagnostic": "artifact_kind_mismatch",
            },
            {
                "stage_id": "function_call_contract_closure",
                "partition_id": "fixture",
                "diagnostic": "artifact_kind_mismatch",
            },
        ]
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch("agent.planning.pipeline._close_implementability") as semantic_closure, patch(
            "agent.planning.pipeline._coder_validate"
        ) as coder_validate:
            result = run_planning(FACTS, Path(raw) / "run")
            report = read_json(result.planning_root / "semantic_closure/implementability_report.json")
            manifest = read_json(result.manifest_path)
            diagnostics = json.loads((result.planning_root / "diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertTrue(result.specs_root.exists())
            self.assertFalse(report["semantic_patch_attempted"])
            self.assertEqual(report["final_diagnostics"][0]["code"], "unresolved_partition")
            self.assertIn(
                "deterministic_call_contract_completion",
                {item["code"] for item in report["deterministic_completion"]},
            )
            completed_plan = read_json(result.planning_root / "implementation_plan.json")
            main = next(item for item in completed_plan["functions"] if item["name"] == "main")
            self.assertEqual(main["call_contracts"][0]["NAME"], "mqtt_core_init")
            self.assertEqual(manifest["unresolved_stage_partition_count"], 1)
            self.assertEqual(manifest["unresolved_stage_partition_attempt_count"], 2)
            self.assertEqual(manifest["diagnostic_counts"]["error"], sum(item["level"] == "error" for item in diagnostics))
            self.assertEqual(diagnostics[0]["authoritative_stage"], "function_call_contract_closure")
            semantic_closure.assert_not_called()
            coder_validate.assert_called_once_with(result.specs_root)

    def test_single_stage_json_repair_failure_after_inventory_is_nonfatal(self) -> None:
        class FakeClient:
            def __init__(self, api_key_env: str = "ALI_API") -> None:
                pass

            def generate_with_usage(self, request):
                return LLMResponse(
                    content='{"invalid": [0x48]}',
                    usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
                )

        plan = _minimal_plan()
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_test_vector_design")
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.common.llm_client.FixedQwenClient", FakeClient
        ), patch(
            "agent.planning.planner.build_stage_messages",
            return_value=[{"role": "user", "content": "fixture"}],
        ):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            planner.registry = CanonicalPlanningRegistry.from_snapshot(
                plan["canonical_registry_snapshot"]
            )
            artifact = planner._run_stage(stage, {}, [])

        self.assertEqual(artifact, _empty_recoverable_stage_artifact(stage.stage_id))
        self.assertEqual(planner.stage_records[-1]["status"], "completed")
        self.assertTrue(planner.stage_records[-1]["nonfatal_fallback"])
        self.assertEqual(planner.stage_records[-1]["request_count"], 2)
        self.assertEqual(planner.stage_records[-1]["repair_usage"]["total_tokens"], 12)
        self.assertEqual(planner.unresolved_partitions[0]["stage_id"], stage.stage_id)

    def test_stage10_accepts_only_non_derivable_choices(self) -> None:
        typed = {
            "ordering_choices": [
                {
                    "reason": "fixture ordering cannot be uniquely derived",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                    "affected_artifact_ids": ["module:fixture"],
                }
            ],
            "architecture_choices": [],
            "artifact_requests": [],
            "dependency_diagnostics": [],
        }
        self.assertEqual(_dependency_stage_overlays(typed, allow_legacy=False), {"modules": [], "files": [], "functions": []})
        typed["functions"] = []
        with self.assertRaisesRegex(ValueError, "typed_delta_forbidden_inventory"):
            _dependency_stage_overlays(typed, allow_legacy=False)

    def test_missing_stage6_inventory_short_circuits_downstream_function_stages(self) -> None:
        previous = [{
            "stage_id": "function_interface_design",
            "artifact": {"function_interfaces": []},
        }]
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        stage_ids = {
            "function_behavior_design",
            "function_test_vector_design",
            "dependency_closure",
        }
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.common.llm_client.FixedQwenClient",
            side_effect=AssertionError("empty committed interface inventory must not call the model"),
        ):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            for stage in (item for item in PLANNING_STAGES if item.stage_id in stage_ids):
                artifact = planner._run_stage(stage, context, previous)
                previous.append({"stage_id": stage.stage_id, "artifact": artifact})
                self.assertEqual(planner.stage_records[-1]["request_count"], 0)
                self.assertEqual(
                    planner.stage_records[-1]["mode"], "deterministic_no_committed_interfaces"
                )

    def test_incomplete_merge_drops_only_overlays_without_committed_interfaces(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        function_id = registry.typed_view({"function"})[0]["artifact_id"]
        artifacts = [
            {
                "stage_id": "function_interface_design",
                "artifact": {"function_interfaces": []},
            },
            {
                "stage_id": "function_behavior_design",
                "artifact": {
                    "function_behaviors": [{"function_id": function_id, "LOGIC": "stale overlay"}],
                    "wire_mappings": [],
                },
            },
        ]
        with self.assertRaisesRegex(ValueError, "overlay_unknown_stable_id"):
            _merge_function_artifacts(artifacts, registry)
        self.assertEqual(
            _merge_function_artifacts(artifacts, registry, allow_incomplete=True), []
        )

    def test_legacy_high_risk_stages_materialize_as_typed_overlays(self) -> None:
        plan = _minimal_plan()
        registry = CanonicalPlanningRegistry.from_snapshot(plan["canonical_registry_snapshot"])
        caller, callee = registry.typed_view({"function"})
        artifacts = [
            {
                "stage_id": "type_and_access_path_design",
                "artifact": {"types": [{"type_name": "mqtt_core_t", "owner_file": "core.h", "fields": []}]},
            },
            {
                "stage_id": "function_call_contract_closure",
                "artifact": {"call_contracts": [{"caller": caller["canonical_name"], "callee": callee["canonical_name"], "NAME": callee["canonical_name"], "SIGNATURE": "legacy"}]},
            },
            {
                "stage_id": "dependency_closure",
                "artifact": {"modules": [], "files": [], "functions": [], "generation_order": []},
            },
        ]
        typed = _materialize_typed_stage_artifacts(artifacts, registry)
        self.assertEqual(set(typed[0]["artifact"]["type_definition_overlays"][0]), {"type_id", "definition_overlay"})
        self.assertNotIn("type_name", typed[0]["artifact"]["type_definition_overlays"][0]["definition_overlay"])
        self.assertEqual(
            set(typed[1]["artifact"]["call_edges"][0]),
            {"caller_function_id", "callee_function_id", "call_purpose", "condition", "argument_semantics", "result_usage"},
        )
        self.assertEqual(typed[2]["artifact"]["ordering_choices"], [])
        self.assertNotIn("modules", typed[2]["artifact"])

    def test_json_repair_path_fixes_hex_literal_without_real_model(self) -> None:
        class FakeClient:
            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.calls = 0

            def generate_with_usage(self, request):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content='{"payload": [0x48]}', usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12))
                return LLMResponse(content='{"payload": [72]}', usage=LLMUsage(prompt_tokens=11, completion_tokens=13, total_tokens=24))

        stage = PLANNING_STAGES[0]
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            artifact = planner._run_stage(stage, {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}, [])
        self.assertEqual(artifact, {"payload": [72]})
        self.assertTrue(planner.stage_records[-1]["repaired_json"])
        self.assertEqual(planner.stage_records[-1]["usage"]["total_tokens"], 12)
        self.assertEqual(planner.stage_records[-1]["repair_usage"]["total_tokens"], 24)

    def test_failed_json_repair_usage_is_still_accounted(self) -> None:
        class FakeClient:
            def __init__(self, api_key_env: str = "ALI_API") -> None:
                self.calls = 0

            def generate_with_usage(self, request):
                self.calls += 1
                usage = LLMUsage(prompt_tokens=5 * self.calls, completion_tokens=7, total_tokens=5 * self.calls + 7)
                return LLMResponse(content='{"payload": [0x48]}', usage=usage)

        stage = PLANNING_STAGES[0]
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            with self.assertRaises(json.JSONDecodeError):
                planner._run_stage(
                    stage,
                    {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []},
                    [],
                )
        record = planner.stage_records[-1]
        self.assertEqual(record["request_count"], 2)
        self.assertEqual(record["repair_usage"]["total_tokens"], 17)
        self.assertGreater(record["prompt_characters"], 0)

    def test_compile_specs_normalizes_coder_facing_plan(self) -> None:
        plan = {
            "schema_version": "specforge_planning_ir_v1",
            "protocol": {"name": "MQTT", "slug": "mqtt", "spec_version": "3.1.1", "roles": ["BROKER"], "default_port": 1884},
            "modules": [{"id": "core", "name": "core", "role": "Core runtime.", "dependencies": []}],
            "files": [
                {"id": "core.h", "module": "core", "role": "public_header", "header_path": "core.h", "source_path": None, "dependency_intent": {}, "trace_refs": []},
                {"id": "core.c", "module": "core", "role": "private_source", "header_path": None, "source_path": "core.c", "dependency_intent": {}, "trace_refs": []},
                {"id": "private_detail.h", "module": "core", "role": "private_header", "header_path": "private_detail.h", "source_path": None, "dependency_intent": {}, "trace_refs": []},
                {"id": "ops.h", "module": "core", "role": "public_header", "header_path": "ops.h", "source_path": None, "dependency_intent": {}, "trace_refs": []},
                {
                    "id": "ops.c",
                    "module": "core",
                    "role": "private_source",
                    "header_path": None,
                    "source_path": "ops.c",
                    "dependency_intent": {"public_includes": ["ops.h"], "private_includes": ["private_detail.h"]},
                    "trace_refs": [],
                },
            ],
            "types": [{"type_name": "mqtt_core_t", "type_kind": "OPAQUE", "owner_file": "core.h", "public_visibility": True, "ownership_semantics": "opaque runtime handle"}],
            "functions": [
                {
                    "function_id": "mqtt_core_init",
                    "owner_file": "ops.c",
                    "function_type": "INITIALIZER",
                    "visibility": "public",
                    "role": "Initialize core.",
                    "signature": "bool mqtt_core_init(mqtt_core_t *core)",
                    "RELY": {"STRUCT": ["mqtt_core_t"]},
                    "CALL_CONTRACTS": [],
                    "WIRE_MAPPING": None,
                    "TEST_VECTORS": [{"input": {"core": "valid"}, "expect": {"return": True}}],
                    "trace_refs": ["fact:minimum_v1"],
                }
            ],
            "structured_planning_stages": [
                {
                    "stage_id": "function_behavior_design",
                    "artifact": {
                        "function_behaviors": [
                            {
                                "function_id": "mqtt_core_init",
                                "LOGIC": {
                                    "preconditions": ["core != NULL"],
                                    "postconditions": ["core is initialized"],
                                    "state_changes": ["core fields are initialized"],
                                    "response_behavior": "returns true on success",
                                },
                            }
                        ]
                    },
                }
            ],
            "consistency_rules": ["facts are read-only"],
            "forbidden_symbols": ["hardcoded_reference"],
            "test_vectors": [{"name": "runtime_smoke", "input": {}, "expect": {"ok": True}}],
        }
        with tempfile.TemporaryDirectory() as raw:
            compile_specs(plan, Path(raw) / "specs")
            self.assertIn("id", plan["types"][0])
            self.assertIn("logic", plan["functions"][0])
            ops_file = next(item for item in plan["files"] if item["header_path"] == "ops.h")
            self.assertEqual(ops_file["header_dependencies"], ["core.h"])
            self.assertEqual(ops_file["source_dependencies"], ["ops.h", "private_detail.h"])
            bundle = load_spec_bundle_from_root(Path(raw) / "specs", validate_rendered_headers=True)
            self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_lowers_one_dimensional_array_member_for_coder_loader(self) -> None:
        plan = _minimal_plan()
        plan["types"][0]["type_spec"] = {
            "TYPE_KIND": "STRUCT",
            "FIELDS": [{"NAME": "peer_addr", "TYPE": "char[64]", "ROLE": "address text"}],
        }
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            file_spec = next(iter(bundle.file_specs_by_trace.values())).raw
        member = file_spec["HEADER"]["DATA"][0]["TYPE_SPEC"]["FIELDS"][0]
        self.assertEqual(member["TYPE"], "char")
        self.assertEqual(member["ARRAY_LEN"], "64")
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_preserves_cyclic_dependencies_for_semantic_gate(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        consumer = plan["modules"][0]
        consumer.update({"id": "module:consumer", "name": "consumer", "dependencies": ["provider"]})
        provider = {
            "id": "module:provider", "name": "provider", "role": "fixture provider",
            "dependencies": ["consumer"], "files": [], "trace_refs": [],
        }
        plan["modules"].append(provider)
        plan["files"][0]["module"] = "consumer"
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            module_spec = read_json(Path(manifest["module_spec"]))
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        dependencies = {item["NAME"]: item["DEPENDENCIES"] for item in module_spec["MODULES"]}
        self.assertEqual(dependencies, {"consumer": ["provider"], "provider": ["consumer"]})
        self.assertIn("generation_order_violation", {item.code for item in bundle.diagnostics})

    def test_compiler_normalizes_source_only_entrypoint_path_for_coder_loader(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        main = next(function for function in plan["functions"] if function["name"] == "main")
        main_file_id = "file:mqtt/mqtt_main"
        main["file"] = main_file_id
        plan["files"].append(
            {
                "id": main_file_id, "module": "core_runtime", "trace_id": "mqtt/mqtt_main",
                "role": "runtime entrypoint", "header_path": None, "source_path": "mqtt_main.c",
                "header_dependencies": [], "source_dependencies": [], "types": [], "functions": [main["id"]],
                "trace_refs": [], "forbidden_symbols": [], "test_vectors": [],
            }
        )
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        entrypoint_file = next(item for item in bundle.file_specs_by_trace.values() if item.trace_id == "mqtt/mqtt_main")
        self.assertEqual(entrypoint_file.source_path, "main.c")
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_assigns_unique_companion_headers_for_shared_header_layout(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["files"].extend(
            [
                {
                    "id": f"file:mqtt/{name}", "module": "core_runtime", "trace_id": f"mqtt/{name}",
                    "role": name, "header_path": "shared.h", "source_path": f"{name}.c",
                    "header_dependencies": [], "source_dependencies": [], "types": [], "functions": [],
                    "trace_refs": [], "forbidden_symbols": [], "test_vectors": [],
                }
                for name in ("decoder", "encoder")
            ]
        )
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        headers = {
            item.trace_id: item.header_path
            for item in bundle.file_specs_by_trace.values()
            if item.trace_id in {"mqtt/decoder", "mqtt/encoder"}
        }
        self.assertEqual(headers, {"mqtt/decoder": "shared.h", "mqtt/encoder": "encoder.h"})
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_rejects_function_name_as_signature_fallback(self) -> None:
        plan = _minimal_plan()
        plan["functions"][0]["signature"] = plan["functions"][0]["name"]
        with self.assertRaisesRegex(ValueError, "missing a complete C signature"):
            normalize_plan_for_compiler(plan)

    def test_structured_callback_signature_lowers_idempotently(self) -> None:
        plan = _minimal_plan()
        plan["types"].append(
            {
                "symbol": "mqtt_event_callback_t",
                "kind": "CALLBACK",
                "type_spec": {"TYPE_KIND": "CALLBACK"},
                "owner_file": "file:mqtt/core",
                "visibility": "public",
                "c_type": "typedef void (*mqtt_event_callback_t)(void* user_data, size_t len);",
                "signature": {
                    "return_type": "void",
                    "parameters": [
                        {"name": "user_data", "type": "void*"},
                        {"name": "len", "type": "size_t"},
                    ],
                },
            }
        )
        plan["canonical_registry_snapshot"] = build_registry(
            [
                {"stage_id": "module_file_plan", "artifact": {"modules": plan["modules"], "files": plan["files"]}},
                {
                    "stage_id": "public_artifact_inventory",
                    "artifact": {"types": plan["types"], "functions": plan["functions"], "constants_or_macros": []},
                },
                {"stage_id": "type_and_access_path_design", "artifact": {"types": plan["types"]}},
                {"stage_id": "function_interface_design", "artifact": {"function_interfaces": plan["functions"]}},
            ],
            protocol_slug="mqtt",
        ).snapshot()
        normalized = normalize_plan_for_compiler(plan)
        normalized = normalize_plan_for_compiler(normalized)
        callback = next(item for item in normalized["types"] if item["name"] == "mqtt_event_callback_t")
        self.assertEqual(callback["type_spec"]["CALLBACK_SIGNATURE"], "void (*mqtt_event_callback_t)(void* user_data, size_t len)")
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "mqtt_specs"
            compile_specs(normalized, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_preserves_stage5_enum_and_callback_dialects(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["types"] = [
            {
                "id": "type:packet_kind_t", "file": "file:mqtt/core", "name": "packet_kind_t",
                "kind": "TYPE", "visibility": "PUBLIC", "role": "wire packet kind",
                "type_spec": "packet_kind_t", "type_kind": "ENUM",
                "values": [
                    {"name": "PACKET_CONNECT", "value": 1, "role": "CONNECT packet", "fact_refs": ["fact:connect"]},
                    {"name": "PACKET_PUBLISH", "value": 3, "role": "PUBLISH packet", "fact_refs": ["fact:publish"]},
                ],
                "trace_refs": ["fact:packet_types"], "decision_refs": [], "rule_refs": [],
            },
            {
                "id": "type:on_packet_fn", "file": "file:mqtt/core", "name": "on_packet_fn",
                "kind": "TYPE", "visibility": "PUBLIC", "role": "decode callback",
                "type_spec": "CALLBACK", "type_kind": "CALLBACK",
                "signature": {
                    "return_type": "bool",
                    "parameters": [
                        {"name": "kind", "c_type": "packet_kind_t"},
                        {"name": "user_data", "c_type": "void*"},
                    ],
                },
                "trace_refs": ["fact:decoder"], "decision_refs": [], "rule_refs": [],
            },
        ]
        plan["files"][0]["types"] = [item["id"] for item in plan["types"]]

        normalized = normalize_plan_for_compiler(plan)

        enum_spec = normalized["types"][0]["type_spec"]
        callback_spec = normalized["types"][1]["type_spec"]
        self.assertEqual(enum_spec["TYPE_KIND"], "ENUM")
        self.assertEqual(
            [(item["NAME"], item["VALUE"]) for item in enum_spec["ENUM_VALUES"]],
            [("PACKET_CONNECT", 1), ("PACKET_PUBLISH", 3)],
        )
        self.assertEqual(enum_spec["ENUM_VALUES"][0]["TRACE_REFS"], ["fact:connect"])
        self.assertEqual(callback_spec["CALLBACK_SIGNATURE"], "bool (*on_packet_fn)(packet_kind_t kind, void* user_data)")

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            file_spec = read_json(specs_root / "core" / "core_spec.json")
            sidecar = read_json(Path(manifest["semantic_mapping"]))
        emitted = next(item for item in file_spec["HEADER"]["DATA"] if item["NAME"] == "packet_kind_t")
        self.assertNotIn("TRACE_REFS", emitted["TYPE_SPEC"]["ENUM_VALUES"][0])
        mapped = next(item for item in sidecar["artifacts"] if item["plan_id"] == "type:packet_kind_t")
        self.assertEqual(mapped["planning_type_spec"]["ENUM_VALUES"][0]["TRACE_REFS"], ["fact:connect"])

    def test_compiler_does_not_rewrite_opaque_by_value_into_loadable_kind(self) -> None:
        plan = _minimal_plan()
        function = plan["functions"][0]
        function["signature"]["RAW"] = "bool mqtt_core_init(mqtt_core_t core)"
        function["signature"]["PARAMS"][0]["TYPE"] = "mqtt_core_t"
        normalized = normalize_plan_for_compiler(plan)
        self.assertEqual(normalized["types"][0]["type_spec"]["TYPE_KIND"], "OPAQUE")
        self.assertEqual(normalized["functions"][0]["signature"]["PARAMS"][0]["TYPE"], "mqtt_core_t")

    def test_compiler_preserves_explicit_wire_obligation_for_semantic_gate(self) -> None:
        plan = _minimal_plan()
        plan["functions"][0]["wire_obligation"] = {
            "required": True,
            "targets": [{"requirement_id": "wire:field:kind", "packet": "unit", "wire_field": "kind"}],
            "trace_refs": ["fact:minimum_v1"],
        }
        normalized = normalize_plan_for_compiler(plan)
        self.assertEqual(
            normalized["functions"][0]["wire_obligation"],
            plan["functions"][0]["wire_obligation"],
        )

    def test_compiler_and_coder_accept_typed_output_parameter_wire_target(self) -> None:
        plan = _minimal_plan()
        function = plan["functions"][0]
        function["signature"] = {
            "RAW": "bool mqtt_core_init(uint8_t* out_kind)", "NAME": "mqtt_core_init", "RETURN": "bool",
            "PARAMS": [{"NAME": "out_kind", "TYPE": "uint8_t*", "ROLE": "output_discriminant"}],
        }
        function["wire_mapping"] = [{
            "PACKET": "fixed_header", "WIRE_FIELD": "packet_type",
            "STRATEGY": "store_in_field", "TARGET": "out_kind",
        }]
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        self.assertFalse(manifest["diagnostics"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_preserves_behavior_vectors_and_function_constraints(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        function = plan["functions"][0]
        function["logic"] = "Parse exactly one complete frame and retain incomplete bytes."
        function["wire_mapping"] = json.dumps(
            [{"PACKET": "CONNECT", "WIRE_FIELD": "protocol_level", "STRATEGY": "store_in_field", "TARGET": "packet.protocol_level"}]
        )
        function["access_paths"] = [{"path": "packet.protocol_level", "type": "uint8_t", "role": "decoded protocol level"}]
        function["forbidden_symbols"] = [{"name": "unsafe_parse", "kind": "FUNC", "reason": "bypasses bounds checks"}]
        function["test_vectors"] = [
            {
                "name": "complete_connect",
                "input_bytes": [16, 0],
                "expected_output": {"return": True},
                "expected_state": {"buffered": 0},
                "trace_refs": ["fact:connect"],
            }
        ]
        plan["files"][0]["test_vectors"] = [
            {"name": "file_smoke", "input": {"bytes": [16, 0]}, "expected_output": {"accepted": True}, "trace_refs": ["fact:connect"]}
        ]

        with tempfile.TemporaryDirectory() as raw:
            manifest = compile_specs(plan, Path(raw) / "specs")
            function_spec = read_json(Path(raw) / "specs" / "core" / "mqtt_core_init_spec.json")
            file_spec = read_json(Path(raw) / "specs" / "core" / "core_spec.json")
            bundle = load_spec_bundle_from_root(Path(raw) / "specs", validate_rendered_headers=True)

        self.assertEqual(function_spec["LOGIC"]["ACTION"], function["logic"])
        self.assertEqual(function_spec["WIRE_MAPPING"][0]["TARGET"], "packet.protocol_level")
        self.assertEqual(function_spec["ACCESS_PATHS"], [{"PATH": "packet.protocol_level", "TYPE": "uint8_t", "ROLE": "decoded protocol level"}])
        self.assertEqual(function_spec["FORBIDDEN_SYMBOLS"][0]["NAME"], "unsafe_parse")
        self.assertEqual(function_spec["TEST_VECTORS"][0]["INPUT"], {"input_bytes": [16, 0]})
        self.assertEqual(
            function_spec["TEST_VECTORS"][0]["EXPECT"],
            {"expected_output": {"return": True}, "expected_state": {"buffered": 0}},
        )
        self.assertEqual(function_spec["TEST_VECTORS"][0]["TRACE_REFS"], ["fact:connect"])
        self.assertEqual(file_spec["TEST_VECTORS"][0]["NAME"], "file_smoke")
        self.assertFalse(manifest["diagnostics"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_preserves_string_event_and_reports_unstructured_wire_mapping(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        function = plan["functions"][0]
        function["function_type"] = "EVENT"
        function["event"] = "On readable input, consume one frame and update connection state."
        function["wire_mapping"] = "fixed header high nibble selects packet type"
        function["access_paths"] = [{"path": "session.state"}]

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            function_spec = read_json(specs_root / "core" / "mqtt_core_init_spec.json")
            sidecar = read_json(Path(manifest["semantic_mapping"]))
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)

        self.assertEqual(function_spec["EVENT"]["ACTION"], function["event"])
        self.assertNotIn("WIRE_MAPPING", function_spec)
        self.assertEqual(
            [item["code"] for item in manifest["diagnostics"]],
            ["semantic_lowering_unstructured_wire_mapping", "semantic_lowering_incomplete_access_path"],
        )
        self.assertEqual(
            sidecar["lowering_diagnostics"][0]["omitted_value"],
            "fixed header high nibble selects packet type",
        )
        function_mapping = next(item for item in sidecar["artifacts"] if item["plan_id"] == function["id"])
        self.assertEqual(function_mapping["planning_access_paths"], [{"path": "session.state"}])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_compiler_materializes_constants_and_traceability_sidecar(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["constants_or_macros"] = [
            {
                "id": "constant:protocol_level", "symbol": "PROTOCOL_LEVEL", "owner_file": "file:mqtt/core",
                "kind": "CONST", "visibility": "public", "role": "wire protocol level", "value": 4,
                "trace_refs": ["fact:protocol_level"], "decision_refs": ["DEC001"], "rule_refs": ["RULE_MINIMUM_SCOPE"],
            }
        ]

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            file_spec = read_json(specs_root / "core" / "core_spec.json")
            module_spec = read_json(specs_root / "mqtt_module_spec.json")
            sidecar = read_json(Path(manifest["semantic_mapping"]))
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)

        constant = next(item for item in file_spec["HEADER"]["DATA"] if item["NAME"] == "PROTOCOL_LEVEL")
        self.assertEqual((constant["NAME"], constant["KIND"], constant["VALUE"]), ("PROTOCOL_LEVEL", "CONST", 4))
        self.assertIn({"NAME": "PROTOCOL_LEVEL", "KIND": "CONST", "ROLE": "wire protocol level"}, module_spec["MODULES"][0]["ARTIFACTS"])
        mapping = next(item for item in sidecar["artifacts"] if item["plan_id"] == "constant:protocol_level")
        self.assertEqual(len(sidecar["field_preservation_matrix"]), 11)
        self.assertEqual(mapping["trace_refs"], ["fact:protocol_level"])
        self.assertEqual(mapping["decision_refs"], ["DEC001"])
        self.assertEqual(mapping["rule_refs"], ["RULE_MINIMUM_SCOPE"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_unresolved_member_keeps_identity_with_blocking_sentinel_lowering(self) -> None:
        plan = _minimal_plan()
        plan["types"][0]["type_spec"] = {
            "TYPE_KIND": "STRUCT",
            "FIELDS": [
                {"NAME": "size", "TYPE": "size_t", "ROLE": "known field"},
                {"NAME": "missing", "TYPE": "missing_planned_t *", "ROLE": "unresolved field"},
            ],
        }
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            sidecar = read_json(Path(manifest["semantic_mapping"]))
            file_spec = next(
                read_json(path)
                for path in specs_root.rglob("*_spec.json")
                if read_json(path).get("KIND") == "FILE_SPEC"
            )
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)

        self.assertEqual([item["code"] for item in manifest["diagnostics"]], ["semantic_lowering_unresolved_type_member"])
        self.assertEqual(sidecar["lowering_diagnostics"], manifest["diagnostics"])
        self.assertEqual(manifest["diagnostics"][0]["owner_layer"], "compiler")
        self.assertEqual(manifest["diagnostics"][0]["authoritative_stage"], "type_and_access_path_design")
        members = file_spec["HEADER"]["DATA"][0]["TYPE_SPEC"]["FIELDS"]
        self.assertEqual([item["NAME"] for item in members], ["size", "missing"])
        self.assertEqual(members[1]["TYPE"], "void *")
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_private_type_leak_is_lowered_to_coder_loadable_candidate(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        public_type = plan["types"][0]
        public_type["type_spec"] = {
            "TYPE_KIND": "STRUCT",
            "FIELDS": [{"NAME": "storage", "TYPE": "private_storage_t", "ROLE": "private state"}],
        }
        plan["types"].insert(
            0,
            {
                "id": "type:private_storage_t", "file": public_type["file"],
                "name": "private_storage_t", "kind": "TYPE", "visibility": "PRIVATE",
                "role": "private storage", "type_spec": {
                    "TYPE_KIND": "STRUCT",
                    "FIELDS": [{"NAME": "value", "TYPE": "int", "ROLE": "fixture"}],
                },
            },
        )

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            file_spec = next(
                read_json(path)
                for path in specs_root.rglob("*_spec.json")
                if read_json(path).get("KIND") == "FILE_SPEC"
            )
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)

        emitted = next(item for item in file_spec["HEADER"]["DATA"] if item["NAME"] == public_type["name"])
        self.assertEqual(emitted["TYPE_SPEC"]["FIELDS"][0]["TYPE"], "void *")
        self.assertIn("semantic_lowering_unresolved_type_member", [item["code"] for item in manifest["diagnostics"]])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_unmaterialized_callback_abi_is_lowered_to_coder_loadable_candidate(self) -> None:
        plan = _minimal_plan()
        callback_name = "core_event_callback_fn"
        callback_id = f"callback:{callback_name}"
        plan["canonical_registry_snapshot"]["entries"].append({
            "artifact_id": callback_id,
            "artifact_kind": "callback",
            "canonical_name": callback_name,
            "owner_module_id": "module:core_runtime",
            "owner_file_id": "file:mqtt/core",
            "visibility": "public",
            "definition_stage": "public_artifact_inventory",
            "status": "declared",
            "provenance": {"kind": "inferred_engineering_decision", "refs": ["fact:minimum_v1"]},
            "declaration_kind": "callback",
            "aliases": [callback_id, callback_name],
        })
        function = plan["functions"][0]
        function["signature"] = {
            "RAW": f"bool mqtt_core_init({callback_name} callback)",
            "NAME": "mqtt_core_init",
            "RETURN": "bool",
            "PARAMS": [{"TYPE": callback_name, "NAME": "callback"}],
        }

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            emitted = read_json(specs_root / "core" / "mqtt_core_init_spec.json")

        diagnostic = next(
            item for item in manifest["diagnostics"]
            if item["code"] == "semantic_lowering_missing_public_abi_type"
        )
        self.assertEqual(diagnostic["authoritative_stage"], "type_and_access_path_design")
        self.assertEqual(emitted["SIGNATURE"]["RAW"], "bool mqtt_core_init(void *callback)")
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_structured_callback_signature_takes_precedence_over_type_identity(self) -> None:
        plan = _minimal_plan()
        callback_name = "core_event_callback_fn"
        callback_id = f"callback:{callback_name}"
        callback = {
            "artifact_id": callback_id,
            "artifact_kind": "callback",
            "canonical_name": callback_name,
            "owner_module_id": "module:core_runtime",
            "owner_file_id": "file:mqtt/core",
            "visibility": "public",
            "definition_stage": "public_artifact_inventory",
            "status": "defined",
            "provenance": {"kind": "inferred_engineering_decision", "refs": ["fact:minimum_v1"]},
            "declaration_kind": "callback",
            "aliases": [callback_id, callback_name],
        }
        plan["canonical_registry_snapshot"]["entries"].append(callback)
        plan["types"].append({
            "id": callback_id,
            "file": "file:mqtt/core",
            "name": callback_name,
            "kind": "CALLBACK",
            "visibility": "PUBLIC",
            "c_type": callback_name,
            "signature": {
                "return_type": "void",
                "parameters": [{"name": "core", "type": "mqtt_core_t *"}],
            },
            "trace_refs": ["fact:minimum_v1"],
        })

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            file_spec = read_json(specs_root / "core" / "core_spec.json")

        emitted = next(item for item in file_spec["HEADER"]["DATA"] if item["NAME"] == callback_name)
        self.assertEqual(
            emitted["TYPE_SPEC"]["CALLBACK_SIGNATURE"],
            "void (*core_event_callback_fn)(mqtt_core_t * core)",
        )
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_cyclic_public_abi_is_lowered_without_losing_candidate_specs(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["files"][0]["header_dependencies"] = ["peer.h"]
        plan["types"].append({
            "id": "type:peer_t", "file": "file:mqtt/peer", "name": "peer_t",
            "kind": "TYPE", "visibility": "PUBLIC", "role": "Peer handle.",
            "type_spec": {"TYPE_KIND": "OPAQUE"}, "trace_refs": ["fact:minimum_v1"],
        })
        plan["types"].append({
            "id": "type:peer_callback_t", "file": "file:mqtt/core", "name": "peer_callback_t",
            "kind": "TYPE", "visibility": "PUBLIC", "role": "Peer callback.",
            "type_spec": {
                "TYPE_KIND": "CALLBACK",
                "CALLBACK_SIGNATURE": "void (*peer_callback_t)(peer_t *peer)",
            },
            "trace_refs": ["fact:minimum_v1"],
        })
        plan["files"].append({
            "id": "file:mqtt/peer", "module": "peer_runtime", "trace_id": "mqtt/peer",
            "role": "Peer fixture.", "language": "C", "header_path": "peer.h",
            "source_path": "peer.c", "header_dependencies": ["core.h"],
            "source_dependencies": ["peer.h"], "types": ["type:peer_t"],
            "functions": ["function:mqtt/peer/peer_use"], "trace_refs": ["fact:minimum_v1"],
        })
        plan["modules"].append({
            "id": "module:peer_runtime", "name": "peer_runtime", "role": "Peer fixture.",
            "dependencies": [], "files": ["peer.h", "peer.c"], "artifacts": [],
            "trace_refs": ["fact:minimum_v1"],
        })
        plan["functions"][0]["signature"] = {
            "RAW": "bool mqtt_core_init(peer_t* peer)", "NAME": "mqtt_core_init",
            "RETURN": "bool", "PARAMS": [{"TYPE": "peer_t*", "NAME": "peer"}],
        }
        plan["functions"].append({
            "id": "function:mqtt/peer/peer_use", "file": "file:mqtt/peer",
            "name": "peer_use", "function_type": "ALGORITHM", "visibility": "public",
            "role": "Use core from peer.",
            "signature": {
                "RAW": "void peer_use(mqtt_core_t* core)", "NAME": "peer_use",
                "RETURN": "void", "PARAMS": [{"TYPE": "mqtt_core_t*", "NAME": "core"}],
            },
            "rely": {
                "STRUCT": [],
                "FUNC": [{"NAME": "mqtt_core_init", "KIND": "FUNC", "ROLE": "initialize core"}],
                "VAR": [],
            },
            "call_contracts": [{
                "NAME": "mqtt_core_init", "SIGNATURE": "bool mqtt_core_init(peer_t* peer)",
            }],
            "logic": {"INPUT": "core", "ACTION": "use", "OUTPUT": "none", "INVARIANTS_USED": []},
            "trace_refs": ["fact:minimum_v1"],
        })

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            emitted = read_json(specs_root / "core" / "mqtt_core_init_spec.json")
            core_file = read_json(specs_root / "core" / "core_spec.json")
            peer_use = read_json(specs_root / "peer" / "peer_use_spec.json")

        self.assertIn(
            "semantic_lowering_cyclic_public_abi_type",
            [item["code"] for item in manifest["diagnostics"]],
        )
        self.assertIn("void *", emitted["SIGNATURE"]["RAW"])
        self.assertIn("void *", peer_use["CALL_CONTRACTS"][0]["SIGNATURE"])
        callback = next(item for item in core_file["HEADER"]["DATA"] if item["NAME"] == "peer_callback_t")
        self.assertIn("void *", callback["TYPE_SPEC"]["CALLBACK_SIGNATURE"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_wire_target_and_parameter_array_dialects_remain_coder_loadable(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["functions"][0]["signature"]["RAW"] = (
            "bool mqtt_core_init(mqtt_core_t* core, "
            "void (*on_event)(void* user_data, mqtt_core_t* item))"
        )
        plan["functions"][0]["signature"]["PARAMS"].append({
            "TYPE": "void (*)(void*, mqtt_core_t*)", "NAME": "on_event",
            "NULLABLE": False, "OWNERSHIP": "BORROWED",
        })
        plan["functions"][0]["wire_mapping"] = [{
            "packet": "FIXTURE", "wire_field": "value", "strategy": "store_in_field",
            "target": "core->value",
        }]
        plan["functions"][1]["signature"]["RAW"] = "int main(int argc, char* argv[])"
        plan["functions"][1]["signature"]["PARAMS"][1]["TYPE"] = "char*"

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            manifest = compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            function_spec = read_json(specs_root / "core" / "mqtt_core_init_spec.json")
            main_spec = read_json(specs_root / "core" / "main_spec.json")

        self.assertIn(
            "semantic_lowering_wire_target_access_path_missing",
            [item["code"] for item in manifest["diagnostics"]],
        )
        self.assertIn("core->value", [item["PATH"] for item in function_spec["ACCESS_PATHS"]])
        self.assertNotIn("[]", main_spec["SIGNATURE"]["RAW"])
        self.assertNotIn("[]", main_spec["SIGNATURE"]["PARAMS"][1]["TYPE"])
        self.assertIn("void (*on_event)(void*, mqtt_core_t*)", function_spec["SIGNATURE"]["RAW"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_same_header_types_are_emitted_in_reference_order(self) -> None:
        plan = _minimal_plan()
        plan.pop("canonical_registry_snapshot")
        plan["types"] = [
            {
                "id": "type:event_callback_t", "file": "file:mqtt/core",
                "name": "event_callback_t", "kind": "TYPE", "visibility": "PUBLIC",
                "role": "Callback using packet enum.",
                "type_spec": {
                    "TYPE_KIND": "CALLBACK",
                    "CALLBACK_SIGNATURE": "void (*event_callback_t)(packet_kind_t packet)",
                },
            },
            {
                "id": "type:packet_kind_t", "file": "file:mqtt/core",
                "name": "packet_kind_t", "kind": "TYPE", "visibility": "PUBLIC",
                "role": "Packet enum.",
                "type_spec": {
                    "TYPE_KIND": "ENUM",
                    "ENUM_VALUES": [{"NAME": "PACKET_ONE", "VALUE": 1, "ROLE": "fixture"}],
                },
            },
            *plan["types"],
        ]

        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            file_spec = read_json(specs_root / "core" / "core_spec.json")
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)

        names = [item["NAME"] for item in file_spec["HEADER"]["DATA"] if item.get("KIND") == "TYPE"]
        self.assertLess(names.index("packet_kind_t"), names.index("event_callback_t"))
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_lowering_diagnostic_blocks_qualification_without_blocking_specs(self) -> None:
        plan = _minimal_plan()
        plan["types"][0]["type_spec"] = {
            "TYPE_KIND": "STRUCT",
            "FIELDS": [{"NAME": "missing", "TYPE": "missing_planned_t*", "ROLE": "unresolved field"}],
        }

        def fake_stage(self, stage, context, previous_artifacts):
            del self, context, previous_artifacts
            return deepcopy(plan) if stage.stage_id == "final_plan_assembly" else _empty_stage_artifact(stage.stage_id)

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage
        ), patch("agent.planning.planner._derive_implementation_obligations", return_value=[]):
            result = run_planning(FACTS, Path(raw) / "run")
            manifest = read_json(result.manifest_path)
            diagnostics = json.loads((result.planning_root / "diagnostics.json").read_text(encoding="utf-8"))
            bundle = load_spec_bundle_from_root(result.specs_root, validate_rendered_headers=True)
            revalidated = validate_existing_run(result.output_root)
            revalidated_manifest = read_json(revalidated.manifest_path)

        lowering = next(item for item in diagnostics if item["code"] == "semantic_lowering_unresolved_type_member")
        self.assertEqual(result.run_status, "completed_with_candidate_only")
        self.assertTrue(manifest["specs_generated"])
        self.assertTrue(manifest["coder_loader_passed"])
        self.assertFalse(manifest["qualification_passed"])
        self.assertTrue(manifest["artifact_success"])
        self.assertFalse(manifest["semantic_qualified"])
        self.assertFalse(manifest["implementation_ready"])
        self.assertFalse(manifest["fatal"])
        self.assertEqual(manifest["nonfatal_no_specs_count"], 0)
        self.assertEqual(lowering["owner_layer"], "compiler")
        self.assertEqual(lowering["authoritative_stage"], "type_and_access_path_design")
        self.assertEqual(revalidated.run_status, "completed_with_candidate_only")
        self.assertFalse(revalidated_manifest["qualification_passed"])
        self.assertTrue(revalidated_manifest["coder_loader_passed"])
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

    def test_missing_required_function_spec_is_detected_as_task_evaporation(self) -> None:
        plan = _minimal_plan()
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            (specs_root / "core" / "mqtt_core_init_spec.json").unlink()
            diagnostics = plan_to_spec_preservation_check(plan, specs_root)

        codes = {item.code for item in diagnostics}
        self.assertIn("plan_to_spec_function_drift", codes)
        self.assertIn("required_function_spec_not_materialized", codes)

    def test_plan_to_spec_preservation_detects_interface_and_dependency_drops(self) -> None:
        plan = _minimal_plan()
        with tempfile.TemporaryDirectory() as raw:
            specs_root = Path(raw) / "specs"
            compile_specs(plan, specs_root)
            for path in specs_root.rglob("*_spec.json"):
                spec = read_json(path)
                if spec.get("KIND") == "FUNCTION_SPEC" and spec.get("TRACE_ID") == "mqtt/core/main":
                    spec["CALL_CONTRACTS"] = []
                    spec["SIGNATURE"]["RAW"] = "int main(void)"
                    write_json(path, spec)
                elif spec.get("KIND") == "FILE_SPEC":
                    spec["SOURCE"]["DEPENDENCY"] = []
                    write_json(path, spec)
            diagnostics = plan_to_spec_preservation_check(plan, specs_root)

        codes = {item.code for item in diagnostics}
        self.assertIn("plan_to_spec_function_semantic_drift", codes)
        self.assertIn("plan_to_spec_file_dependency_drift", codes)

    def test_resume_from_compile_specs_reuses_stage_logs_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage",
            side_effect=AssertionError("resume_from=compile_specs must not call the model"),
        ):
            run_dir = Path(raw) / "run"
            stage_root = run_dir / "_planning" / "stage_logs"
            stage_root.mkdir(parents=True)
            base = _minimal_plan()
            fixture_artifacts = {
                "scope_fact_inventory": {"confirmed_scope": ["fixture"]},
                "architecture_boundaries": {"module_candidates": [], "ownership_decisions": []},
                "module_file_plan": {"modules": base["modules"], "files": base["files"]},
                "public_artifact_inventory": {
                    "types": base["types"],
                    "functions": base["functions"],
                    "constants_or_macros": [],
                    "forbidden_symbols": [],
                },
                "type_and_access_path_design": {"types": base["types"]},
                "function_interface_design": {"function_interfaces": base["functions"]},
                "function_behavior_design": {"function_behaviors": [], "wire_mappings": []},
                "function_call_contract_closure": {"call_contracts": [], "rely_by_function": {}},
                "function_test_vector_design": {"function_test_vectors": {}, "runtime_test_vectors": []},
                "dependency_closure": {
                    "modules": [{"name": base["modules"][0]["name"], "dependencies": []}],
                    "files": [{"id": base["files"][0]["id"], "header_dependencies": [], "source_dependencies": ["core.h"]}],
                    "functions": [],
                },
                "final_plan_assembly": base,
            }
            records = []
            for index, stage in enumerate(PLANNING_STAGES, 1):
                stage_dir = stage_root / f"{index:02d}_{stage.stage_id}"
                stage_dir.mkdir()
                write_json(stage_dir / "artifact.json", fixture_artifacts[stage.stage_id])
                records.append(
                    {
                        "stage_id": stage.stage_id,
                        "title": stage.title,
                        "status": "completed",
                        "elapsed_seconds": 0,
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                        "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "repaired_json": False,
                    }
                )
            write_json(stage_root / "stage_records.json", records)
            unresolved = [{
                "stage_id": "function_behavior_design", "partition_id": "fixture",
                "diagnostic": "stage7_wire_mapping_invalid: fixture",
            }]
            write_json(run_dir / "_planning" / "unresolved_partitions.json", unresolved)
            write_json(run_dir / "_planning" / "blocking_diagnostics.json", unresolved)

            result = run_planning(FACTS, run_dir, coder_validate=False, resume_from="compile_specs")
            self.assertTrue(result.success, result.diagnostics)
            plan = read_json(result.planning_root / "implementation_plan.json")
            self.assertEqual(plan["structured_planning_usage"]["total_tokens"], 22)
            self.assertEqual(plan["unresolved_partitions"], unresolved)
            manifest = read_json(result.manifest_path)
            self.assertEqual(manifest["unresolved_stage_partition_count"], 1)
            self.assertFalse(manifest["artifact_success"])

    def test_stage_resume_reconciles_unresolved_and_blocking_ledgers(self) -> None:
        resume_stage = next(
            item for item in PLANNING_STAGES if item.stage_id == "function_test_vector_design"
        )
        start_index = PLANNING_STAGES.index(resume_stage)
        with tempfile.TemporaryDirectory() as raw:
            stage_root = Path(raw) / "_planning" / "stage_logs"
            records = []
            for index, stage in enumerate(PLANNING_STAGES[:start_index]):
                stage_dir = stage_root / f"{index + 1:02d}_{stage.stage_id}"
                stage_dir.mkdir(parents=True)
                write_json(stage_dir / "artifact.json", _empty_stage_artifact(stage.stage_id))
                records.append({
                    "stage_id": stage.stage_id,
                    "status": "completed",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
            write_json(stage_root / "stage_records.json", records)
            write_json(stage_root.parent / "unresolved_partitions.json", [
                {"stage_id": "function_interface_design", "partition_id": "kept", "diagnostic": "stage6 fixture"},
                {"stage_id": "function_test_vector_design", "partition_id": "stale", "diagnostic": "stage9 stale"},
            ])
            write_json(stage_root.parent / "blocking_diagnostics.json", [])

            def fake_stage(planner, stage, _context, _previous):
                planner.stage_records.append({
                    "stage_id": stage.stage_id,
                    "status": "completed",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
                return _minimal_plan() if stage.stage_id == "final_plan_assembly" else _empty_stage_artifact(stage.stage_id)

            with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage), patch.object(
                LLMStructuredPlanner, "_validate_stage_commit", return_value=None
            ):
                LLMStructuredPlanner(stage_log_dir=stage_root).build_plan(
                    {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []},
                    resume_from=resume_stage.stage_id,
                )

            unresolved = json.loads(
                (stage_root.parent / "unresolved_partitions.json").read_text(encoding="utf-8")
            )
            blocking = json.loads(
                (stage_root.parent / "blocking_diagnostics.json").read_text(encoding="utf-8")
            )
            self.assertEqual([(item["stage_id"], item["partition_id"]) for item in unresolved], [
                ("function_interface_design", "kept")
            ])
            self.assertEqual(blocking[0]["path"], "function_interface_design:kept")
            self.assertEqual(blocking[0]["stage_id"], "function_interface_design")

    def test_every_structured_stage_can_be_a_resume_point(self) -> None:
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        for start_index, resume_stage in enumerate(PLANNING_STAGES):
            with self.subTest(resume_from=resume_stage.stage_id), tempfile.TemporaryDirectory() as raw:
                stage_root = Path(raw) / "stage_logs"
                for index, completed_stage in enumerate(PLANNING_STAGES[:start_index]):
                    stage_dir = stage_root / f"{index + 1:02d}_{completed_stage.stage_id}"
                    stage_dir.mkdir(parents=True)
                    write_json(stage_dir / "artifact.json", {"completed": completed_stage.stage_id})
                stale_dir = stage_root / f"{start_index + 1:02d}_{resume_stage.stage_id}"
                stale_dir.mkdir(parents=True)
                (stale_dir / "stale.txt").write_text("stale", encoding="utf-8")
                called: list[str] = []

                def fake_stage(planner, stage, _context, previous_artifacts):
                    if not called:
                        self.assertEqual(len(previous_artifacts), start_index)
                        self.assertFalse((stale_dir / "stale.txt").exists())
                    called.append(stage.stage_id)
                    planner.stage_records.append(
                        {
                            "stage_id": stage.stage_id,
                            "status": "completed",
                            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                            "repair_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        }
                    )
                    return _minimal_plan() if stage.stage_id == "final_plan_assembly" else _empty_stage_artifact(stage.stage_id)

                with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage):
                    plan = LLMStructuredPlanner(stage_log_dir=stage_root).build_plan(context, resume_from=resume_stage.stage_id)
                self.assertEqual(called, [stage.stage_id for stage in PLANNING_STAGES[start_index:]])
                self.assertEqual(plan["protocol"]["slug"], "mqtt")


if __name__ == "__main__":
    unittest.main()
