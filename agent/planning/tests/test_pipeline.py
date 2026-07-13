from __future__ import annotations

import tempfile
import unittest
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent.common.llm_client import LLMResponse, LLMUsage
from agent.coder.specs import load_spec_bundle_from_root
from agent.planning.compiler import compile_specs, normalize_plan_for_compiler
from agent.planning.facts import read_json, select_fact_slice, stable_json_hash, write_json
from agent.planning.knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from agent.planning.metrics import build_run_metrics
from agent.planning.models import Diagnostic
from agent.planning.pipeline import run_planning
from agent.planning.planner import (
    LLMStructuredPlanner,
    PLANNING_STAGES,
    RecoverablePlanningError,
    _assemble_final_plan_candidate,
    _dependency_stage_overlays,
    _merge_function_artifacts,
    _materialize_typed_stage_artifacts,
    _validate_completed_stage,
    build_planning_context,
    planning_resume_points,
    planning_stage_catalog,
)
from agent.planning.prompts import build_stage_prompt
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
        "test_vectors": [],
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
                {"type_id": type_id, "definition_overlay": {"type_spec": {"TYPE_KIND": "OPAQUE"}, "role": "fixture"}}
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
        usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}

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
            {"stage_id": "public_artifact_inventory", "artifact": {"forbidden_symbols": []}},
            {"stage_id": "type_and_access_path_design", "artifact": {"types": base["types"]}},
            {"stage_id": "function_interface_design", "artifact": {"function_interfaces": base["functions"]}},
            {"stage_id": "function_behavior_design", "artifact": {}},
            {"stage_id": "function_call_contract_closure", "artifact": {}},
            {"stage_id": "function_test_vector_design", "artifact": {"runtime_test_vectors": []}},
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

        with tempfile.TemporaryDirectory() as raw, patch("agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage):
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

    def test_recoverable_stage_failure_materializes_candidate_without_qualified_specs(self) -> None:
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
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertFalse(result.success)
            self.assertIsNone(result.specs_root)
            self.assertIsNone(manifest["specs_root"])
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
        ), patch("agent.planning.pipeline._coder_validate") as coder_validate:
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
        ), patch("agent.planning.pipeline.validate_planning_run", return_value=[qualification_error]):
            run_dir = Path(raw) / "run"
            result = run_planning(FACTS, run_dir, coder_validate=False)
            manifest = read_json(result.manifest_path)
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertEqual(result.specs_root, Path(manifest["candidate_specs_root"]))
            self.assertEqual(manifest["specs_root"], manifest["candidate_specs_root"])
            self.assertFalse(manifest["qualification_passed"])
            self.assertTrue(manifest["specs_generated"])
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
            bundle = load_spec_bundle_from_root(result.specs_root, validate_rendered_headers=True)
            specs = [read_json(path) for path in result.specs_root.rglob("*_spec.json")]
        self.assertTrue(result.success, result.diagnostics)
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
                payload = {
                    "type_definition_overlays": [
                        {"type_id": type_id, "definition_overlay": {"type_spec": {"TYPE_KIND": "OPAQUE"}}}
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

    def test_unresolved_plan_skips_semantic_closure_and_publishes_candidate_only(self) -> None:
        plan = _minimal_plan()
        plan["unresolved_partitions"] = [
            {
                "stage_id": "function_call_contract_closure",
                "partition_id": "fixture",
                "diagnostic": "artifact_kind_mismatch",
            }
        ]
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner.build_plan", return_value=plan
        ), patch("agent.planning.pipeline._close_implementability") as semantic_closure, patch(
            "agent.planning.pipeline._coder_validate"
        ) as coder_validate:
            result = run_planning(FACTS, Path(raw) / "run")
            report = read_json(result.planning_root / "semantic_closure/implementability_report.json")
            self.assertEqual(result.run_status, "completed_with_candidate_only")
            self.assertTrue(result.specs_root.exists())
            self.assertFalse(report["semantic_patch_attempted"])
            self.assertEqual(report["final_diagnostics"][0]["code"], "unresolved_partition")
            semantic_closure.assert_not_called()
            coder_validate.assert_called_once_with(result.specs_root)

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

    def test_compiler_omits_only_dependencies_incompatible_with_generation_order(self) -> None:
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
        self.assertEqual(dependencies, {"consumer": [], "provider": ["consumer"]})
        self.assertFalse(bundle.has_errors(), bundle.diagnostics)

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

    def test_compiler_omits_unresolved_custom_type_member_without_inventing_type(self) -> None:
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
            compile_specs(plan, specs_root)
            bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
            file_spec = next(iter(bundle.file_specs_by_trace.values())).raw
        fields = file_spec["HEADER"]["DATA"][0]["TYPE_SPEC"]["FIELDS"]
        self.assertEqual([field["NAME"] for field in fields], ["size"])
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

            result = run_planning(FACTS, run_dir, coder_validate=False, resume_from="compile_specs")
            self.assertTrue(result.success, result.diagnostics)
            plan = read_json(result.planning_root / "implementation_plan.json")
            self.assertEqual(plan["structured_planning_usage"]["total_tokens"], 22)

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
