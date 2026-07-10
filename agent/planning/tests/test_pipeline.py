from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from agent.common.llm_client import LLMResponse, LLMUsage
from agent.coder.specs import load_spec_bundle_from_root
from agent.planning.compiler import compile_specs
from agent.planning.facts import read_json, stable_json_hash, write_json
from agent.planning.knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from agent.planning.pipeline import run_planning
from agent.planning.planner import (
    LLMStructuredPlanner,
    PLANNING_STAGES,
    build_planning_context,
    build_stage_prompt,
    planning_resume_points,
    planning_stage_catalog,
)
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
    return {
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
                "functions": ["function:mqtt/core/mqtt_core_init"],
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
            }
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
        ],
    }


class PlanningPipelineTests(unittest.TestCase):
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
        self.assertIn("PROTOCOL_MODULE_SPEC", prompt)
        self.assertIn("FILE_SPEC", prompt)
        self.assertIn("FUNCTION_SPEC", prompt)
        self.assertIn("scope_fact_inventory", prompt)

    def test_pipeline_uses_staged_llm_output_without_local_planner(self) -> None:
        def fake_stage(self, stage, context, previous_artifacts):
            if stage.stage_id == "final_plan_assembly":
                return _minimal_plan()
            return {"stage_id": stage.stage_id, "status": "ok"}

        with tempfile.TemporaryDirectory() as raw, patch("agent.planning.planner.LLMStructuredPlanner._run_stage", fake_stage):
            result = run_planning(FACTS, Path(raw) / "run")
            self.assertTrue(result.success, result.diagnostics)
            self.assertTrue((result.planning_root / "structured_planning_stages.json").exists())
            bundle = load_spec_bundle_from_root(result.specs_root, validate_rendered_headers=True)
            self.assertFalse(bundle.has_errors(), bundle.diagnostics)

            facts = read_json(FACTS)
            plan = read_json(result.planning_root / "implementation_plan.json")
            self.assertEqual(schema_shape_check(result.specs_root), [])
            self.assertEqual(structural_consistency_check(plan, result.specs_root), [])
            self.assertEqual(plan_to_spec_preservation_check(plan, result.specs_root), [])
            self.assertEqual(fact_decision_assumption_separation_check(stable_json_hash(facts), facts, plan), [])

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
                                "function_id": partition["functions"][0]["symbol"],
                                "LOGIC": {"INPUT": "", "ACTION": "partition behavior", "OUTPUT": "", "INVARIANTS_USED": []},
                            }
                        ],
                        "wire_mappings": [],
                        "behavior_diagnostics": [],
                    }
                )
                return LLMResponse(content=content, usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3))

        functions = [{"symbol": f"fn_{index}", "owner_file": "a.h" if index < 11 else "b.h"} for index in range(12)]
        previous = [
            {"stage_id": "module_file_plan", "title": "", "artifact": {"files": [{"id": "a.h", "module": "core"}, {"id": "b.h", "module": "core"}]}},
            {"stage_id": "public_artifact_inventory", "title": "", "artifact": {"functions": functions}},
        ]
        stage = next(item for item in PLANNING_STAGES if item.stage_id == "function_behavior_design")
        context = {"facts": {}, "characteristics": {}, "engineering_rules": [], "open_assumptions": []}
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            planner = LLMStructuredPlanner(stage_log_dir=Path(raw))
            artifact = planner._run_stage(stage, context, previous)
        self.assertEqual(len(artifact["function_behaviors_by_group"]), 2)
        self.assertEqual(len(artifact["function_behaviors"]), 2)
        self.assertEqual(planner.stage_records[-1]["partitions_total"], 2)

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

    def test_resume_from_compile_specs_reuses_stage_logs_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.planner.LLMStructuredPlanner._run_stage",
            side_effect=AssertionError("resume_from=compile_specs must not call the model"),
        ):
            run_dir = Path(raw) / "run"
            stage_root = run_dir / "_planning" / "stage_logs"
            stage_root.mkdir(parents=True)
            records = []
            for index, stage in enumerate(PLANNING_STAGES, 1):
                stage_dir = stage_root / f"{index:02d}_{stage.stage_id}"
                stage_dir.mkdir()
                write_json(stage_dir / "artifact.json", _minimal_plan() if stage.stage_id == "final_plan_assembly" else {"stage_id": stage.stage_id})
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
                    return _minimal_plan() if stage.stage_id == "final_plan_assembly" else {"completed": stage.stage_id}

                with patch.object(LLMStructuredPlanner, "_run_stage", fake_stage):
                    plan = LLMStructuredPlanner(stage_log_dir=stage_root).build_plan(context, resume_from=resume_stage.stage_id)
                self.assertEqual(called, [stage.stage_id for stage in PLANNING_STAGES[start_index:]])
                self.assertEqual(plan["protocol"]["slug"], "mqtt")


if __name__ == "__main__":
    unittest.main()
