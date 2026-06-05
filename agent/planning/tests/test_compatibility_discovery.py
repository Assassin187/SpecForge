from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.coder.specs import load_spec_bundle_from_root
from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.config import LLMStageConfig, PlanningConfig
from agent.planning.orchestrator import PlanningAgent, STEP_FILENAMES, compare_output_to_reference, find_latest_resume_source, validate_resume_prefix, validate_resume_source_dir
from agent.planning.stages.architecture import select_architecture
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.tests.current_flow_fixtures import (
    current_architecture_candidates,
    current_implementation_plan,
    current_inventory_prompt_candidate,
)
from agent.planning.validators.planning_ir import validate_planning_ir


ROOT = Path(__file__).resolve().parents[3]


def _write_target_profile(root: Path, *, role: str = "broker") -> Path:
    path = root / f"{role}_target_profile.json"
    path.write_text(
        json.dumps(
            {
                "target_role": role,
                "language": "C",
                "runtime": "Linux epoll",
                "scope": "minimum_v1",
                "deployment_constraints": {
                    "memory_limit": "low",
                    "persistence": False,
                    "tls_mode": "terminated_upstream",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _noop_profile_patch_candidate() -> dict:
    reviews = [
        ("interaction_model", "mixed", "readme_supported", "The interaction facts include multiple message styles."),
        ("statefulness", "persistent_state", "session_state", "The resource facts include persistent session state."),
        ("routing_intensity", "high", "broker_handlers", "Routing uses multiple dispatch keys."),
        ("resource_intensity", "high", "connection_buffering", "The protocol facts include multiple owned resources."),
        ("failure_semantics", "close_connection_on_protocol_error", "decoder_connect", "Malformed protocol input closes the connection."),
    ]
    return {
        "schema_version": "protocol_profile_patch_candidate/v1",
        "field_reviews": {
            field: {"deterministic_value": value, "llm_value": value, "decision": "keep", "support_ids": [support_id], "reason": reason}
            for field, value, support_id, reason in reviews
        },
        "capability_gap_review": {
            "missing_capabilities": [],
            "unsupported_existing_capabilities": [],
            "reason": "No capability gaps found.",
        },
        "patch": {},
        "uncertainties": [],
        "rationale": "No profile patch needed.",
    }


def _fallback_inventory_candidate(prompt_name: str, messages: list[dict[str, str]]) -> dict | None:
    return current_inventory_prompt_candidate(prompt_name, messages)


def _ranking_candidate(messages: list[dict[str, str]]) -> dict:
    payload = json.loads(messages[-1]["content"])
    candidate_ids = [item["candidate_id"] for item in payload["architecture_candidates"]["candidates"]]
    return {
        "schema_version": "architecture_ranking/v1",
        "scores": [
            {
                "candidate_id": candidate_id,
                "total_score": 80 - idx,
                "dimension_scores": {
                    "capability_coverage": 10,
                    "constraint_satisfaction": 8,
                    "cohesion": 8,
                    "coupling": 7,
                    "acyclicity": 8,
                    "state_ownership_clarity": 8,
                    "testability": 7,
                    "implementation_simplicity": 7,
                    "target_scope_fit": 9,
                },
                "strengths": [],
                "weaknesses": [],
                "risks": [],
            }
            for idx, candidate_id in enumerate(candidate_ids)
        ],
        "selected_candidate_id": candidate_ids[0],
        "selection_rationale": "Mock ranking selects the first valid candidate.",
        "ranking_warnings": [],
    }


class PlanningCompatibilityDiscoveryTests(unittest.TestCase):
    def test_current_facts_fixtures_build_planning_ir(self) -> None:
        fixtures = [
            ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
            ROOT / "agent" / "facts" / "gold_facts" / "mqtt" / "protocol_facts.json",
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            target_path = _write_target_profile(Path(raw_tmp), role="broker")
            for facts_path in fixtures:
                with self.subTest(facts=facts_path):
                    target, target_diags = load_target_profile(target_path)
                    self.assertIsNotNone(target, [diag.__dict__ for diag in target_diags])
                    artifact, diagnostics = build_planning_ir(facts_path, target)
                    self.assertIsNotNone(artifact, [diag.__dict__ for diag in diagnostics])
                    self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
                    self.assertEqual(artifact["schema_version"], "planning_ir/v1")
                    self.assertIn("protocol_facts", artifact)
                    self.assertIn("target_directives", artifact)
                    self.assertNotIn("target_role", artifact["protocol_facts"])
                    self.assertTrue(artifact["normalization_index"]["fact_id_by_path"])
                    self.assertFalse([diag.__dict__ for diag in validate_planning_ir(artifact) if diag.level == "error"])

    def test_existing_coder_example_bundle_loads(self) -> None:
        bundle = load_spec_bundle_from_root(ROOT / "specs-example" / "mqtt_specs")
        self.assertFalse([diag.__dict__ for diag in bundle.diagnostics if diag.level == "error"])

    def test_plan_writes_coder_compatible_spec_bundle(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = _write_target_profile(Path(raw_tmp), role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)
            selected_architecture = select_architecture(architecture_candidates, profile)
            implementation_plan = current_implementation_plan(planning_ir, profile, constraints, selected_architecture)

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run")
                result = agent.plan()
            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            for filename in (
                "003_planning_ir.json",
                "004_protocol_profile.json",
                "005_engineering_constraints.json",
                "006_architecture_context.json",
                "006_architecture_candidates.json",
                "006_architecture_ranking.json",
                "006_selected_architecture.json",
                "007_implementation_plan.json",
                "012_planning_repair_statistics.json",
                "013_token_usage_summary.json",
            ):
                self.assertTrue((result.output_dir / "_step_logs" / filename).exists(), filename)
            for filename in (
                "008_dependency_validation_report.json",
                "014_planning_validation_report.json",
            ):
                self.assertTrue((result.output_dir / "_validation_reports" / filename).exists(), filename)
                self.assertFalse((result.output_dir / "_step_logs" / filename).exists(), filename)
            self.assertTrue((result.output_dir / "coder_manifest.json").exists())
            self.assertTrue((result.output_dir / "spec_bundle").exists())
            self.assertFalse(list((result.output_dir / "spec_bundle").glob("planning_*.json")))
            self.assertTrue((result.output_dir / "planning_traceability.json").exists())
            self.assertTrue((result.output_dir / "planning_decisions.json").exists())
            self.assertTrue((result.output_dir / "planning_ir_refs.json").exists())
            self.assertFalse(list((result.output_dir / "spec_bundle").rglob("functions")))
            self.assertTrue([path for path in (result.output_dir / "spec_bundle").rglob("*_spec.json") if path.name != "mqtt_module_spec.json"])
            profile = json.loads((result.output_dir / "_step_logs" / "004_protocol_profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["schema_version"], "protocol_profile/v1")
            self.assertTrue(profile["required_capabilities"])
            plan = json.loads((result.output_dir / "_step_logs" / "007_implementation_plan.json").read_text(encoding="utf-8"))
            self.assertTrue(plan["wire_mapping_table"])
            self.assertTrue(plan["access_path_table"])
            dependency_report = json.loads((result.output_dir / "_validation_reports" / "008_dependency_validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(dependency_report["status"], "passed")
            token_usage = json.loads((result.output_dir / "_step_logs" / "013_token_usage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(token_usage["schema_version"], "planning_token_usage_summary/v1")
            self.assertEqual(token_usage["total"]["total_tokens"], 0)
            repair_stats = json.loads((result.output_dir / "_step_logs" / "012_planning_repair_statistics.json").read_text(encoding="utf-8"))
            self.assertEqual(repair_stats["schema_version"], "planning_repair_statistics/v1")
            self.assertIn("implementation_plan_5_7", repair_stats["substage_pass_rates"]["by_stage_key"])
            bundle = load_spec_bundle_from_root(result.output_dir / "spec_bundle")
            self.assertFalse([diag.__dict__ for diag in bundle.diagnostics if diag.level == "error"])
            report = json.loads((result.output_dir / "_validation_reports" / "014_planning_validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["coder_compatibility_status"], "passed")
            comparison = compare_output_to_reference(result.output_dir, ROOT / "specs-example" / "mqtt_specs")
            self.assertTrue(comparison.success, [diag.__dict__ for diag in comparison.diagnostics])

    def test_resume_from_specs_compile_inherits_prefix_and_rebuilds_bundle(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            output_root = tmp / "planning_out"
            source_dir = output_root / "mqtt" / target_profile.slug / "20260101_000000_000000"
            resumed_dir = output_root / "mqtt" / target_profile.slug / "20260102_000000_000000"
            with patch("agent.planning.orchestrator.DEFAULT_OUTPUT_ROOT", output_root):
                with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                    source = PlanningAgent(facts, target, output_dir=source_dir).plan()
                self.assertTrue(source.success, [diag.__dict__ for diag in source.diagnostics])
                latest = find_latest_resume_source(facts, target, resumed_dir)
                self.assertEqual(source_dir, latest)
                with patch("agent.planning.orchestrator.request_json_candidate", side_effect=AssertionError("resume should not call LLM")):
                    resumed = PlanningAgent(facts, target, output_dir=resumed_dir).plan(resume_from_stage="6")

            self.assertTrue(resumed.success, [diag.__dict__ for diag in resumed.diagnostics])
            self.assertFalse((resumed.output_dir / "_step_logs" / "010_spec_blueprint.json").exists())
            self.assertTrue((resumed.output_dir / "spec_bundle").exists())
            manifest = json.loads((resumed.output_dir / "_step_logs" / "000_planning_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["resume"]["from_stage"], "specs_compile")
            self.assertEqual(Path(manifest["resume"]["source_output_dir"]), source_dir)
            self.assertIn("implementation_plan", manifest["resume"]["inherited_artifacts"])
            self.assertNotIn("spec_blueprint", manifest["resume"]["inherited_artifacts"])

    def test_resume_from_explicit_source_dir(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            source_dir = tmp / "manual_source"
            resumed_dir = tmp / "manual_resumed"
            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                source = PlanningAgent(facts, target, output_dir=source_dir).plan()
            self.assertTrue(source.success, [diag.__dict__ for diag in source.diagnostics])
            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=AssertionError("resume should use explicit artifacts")):
                resumed = PlanningAgent(facts, target, output_dir=resumed_dir).plan(
                    resume_from_stage="specs_compile",
                    resume_source_dir=source_dir,
                )

            self.assertTrue(resumed.success, [diag.__dict__ for diag in resumed.diagnostics])
            manifest = json.loads((resumed.output_dir / "_step_logs" / "000_planning_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(manifest["resume"]["source_output_dir"]), source_dir)
            self.assertIn("implementation_plan", manifest["resume"]["inherited_artifacts"])
            self.assertNotIn("spec_blueprint", manifest["resume"]["inherited_artifacts"])

    def test_resume_source_dir_requires_step_logs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            source_dir, diagnostics = validate_resume_source_dir(Path(raw_tmp))
        self.assertIsNone(source_dir)
        self.assertEqual([diag.code for diag in diagnostics], ["missing_resume_step_logs"])

    def test_stop_after_architecture_writes_prefix_only(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)
            thinking_by_prompt: dict[str, list[bool]] = {}

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                thinking_by_prompt.setdefault(prompt_name, []).append(enable_thinking)
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                raise AssertionError(f"{prompt_name} should not run after architecture")

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                result = PlanningAgent(facts, target, output_dir=tmp / "run").plan(stop_after_stage="architecture")

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertTrue((result.output_dir / "_step_logs" / "006_selected_architecture.json").exists())
            self.assertFalse((result.output_dir / "_step_logs" / "007_implementation_plan.json").exists())
            manifest = json.loads((result.output_dir / "_step_logs" / "000_planning_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "stopped")
            self.assertEqual(manifest["stop"]["after_stage"], "architecture")
            self.assertEqual(thinking_by_prompt["protocol_profile_patch_prompt"], [False])
            self.assertTrue(all(thinking_by_prompt["architecture_candidate_prompt"]))
            self.assertEqual(thinking_by_prompt["architecture_ranking_prompt"], [True])

    def test_implementation_plan_thinking_can_be_set_per_substage(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)
            thinking_by_prompt: dict[str, list[bool]] = {}

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                thinking_by_prompt.setdefault(prompt_name, []).append(enable_thinking)
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True, "enable_thinking": enable_thinking}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True, "enable_thinking": enable_thinking}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True, "enable_thinking": enable_thinking}
                return None, [], {"mocked": True, "enable_thinking": enable_thinking}

            config = PlanningConfig(
                llm_stage_configs={
                    "implementation_plan_5_2a": LLMStageConfig(enable_thinking=True),
                    "implementation_plan_5_2b": LLMStageConfig(enable_thinking=False),
                }
            )
            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                result = PlanningAgent(facts, target, output_dir=tmp / "run", config=config).plan(stop_after_stage="5.3")

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertTrue(all(thinking_by_prompt["core_design_candidate_prompt"]))
            self.assertFalse(any(thinking_by_prompt["module_artifacts_candidate_prompt"]))

    def test_implementation_plan_temperature_can_be_set_per_substage(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)
            temperature_by_prompt: dict[str, list[float]] = {}

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                temperature_by_prompt.setdefault(prompt_name, []).append(config.llm_temperature if temperature is None else temperature)
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            config = PlanningConfig(
                llm_stage_configs={
                    "implementation_plan_5_2a": LLMStageConfig(temperature=0.61),
                    "implementation_plan_5_2b": LLMStageConfig(temperature=0.19),
                }
            )
            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                result = PlanningAgent(facts, target, output_dir=tmp / "run", config=config).plan(stop_after_stage="5.3")

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertEqual(set(temperature_by_prompt["core_design_candidate_prompt"]), {0.61})
            self.assertEqual(set(temperature_by_prompt["module_artifacts_candidate_prompt"]), {0.19})

    def test_validator_repair_statistics_track_inventory_retry(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)
            sent_invalid_type_candidate = False

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                nonlocal sent_invalid_type_candidate
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                if prompt_name == "type_filling_candidate_prompt":
                    payload = json.loads(messages[1]["content"])
                    context = payload["type_filling_context"]
                    module_id = str(context.get("module_id") or context.get("module_artifact", {}).get("module_id", ""))
                    if module_id == "transport_runtime" and not sent_invalid_type_candidate:
                        sent_invalid_type_candidate = True
                        return {
                            "schema_version": "type_filling_candidate/v1",
                            "candidate_id": "candidate:type_filling:bad_module",
                            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
                            "module_id": "missing_module",
                            "slot_fillings": [],
                            "optional_type_proposals": [],
                            "assumptions": [],
                            "unresolved_questions": [],
                            "expansion_notes": [],
                        }, [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                result = PlanningAgent(facts, target, output_dir=tmp / "run", config=PlanningConfig(llm_max_retries=1)).plan(stop_after_stage="5.3")

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            stats = json.loads((result.output_dir / "_step_logs" / STEP_FILENAMES["planning_repair_statistics"]).read_text(encoding="utf-8"))
            stage_stats = stats["repair_success_summary"]["by_stage_key"]["implementation_plan_5_3"]
            self.assertGreaterEqual(stage_stats["validator_error_count"], 1)
            self.assertGreaterEqual(stage_stats["repair_attempt_count"], 1)
            self.assertGreaterEqual(stage_stats["repair_success_count"], 1)
            self.assertEqual(stage_stats["repair_success_rate"], 1.0)

    def test_resume_from_implementation_plan_substage_inherits_prior_substage_artifacts(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            architecture_candidates = current_architecture_candidates(planning_ir, profile, constraints)

            def source_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return _ranking_candidate(messages), [], {"mocked": True}
                inventory_candidate = _fallback_inventory_candidate(prompt_name, messages)
                if inventory_candidate is not None:
                    return inventory_candidate, [], {"mocked": True}
                return None, [], {"mocked": True}

            resumed_prompts: list[str] = []
            forbidden = {
                "protocol_profile_patch_prompt",
                "architecture_candidate_prompt",
                "architecture_ranking_prompt",
                "core_design_candidate_prompt",
                "module_artifacts_candidate_prompt",
                "type_filling_candidate_prompt",
                "function_annotation_candidate_prompt",
                "function_signature_patch_prompt",
                "function_behavior_contract_patch_prompt",
            }

            def resumed_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                resumed_prompts.append(prompt_name)
                if prompt_name in forbidden:
                    raise AssertionError(f"{prompt_name} should have been inherited before 5.4d_wire_access_binding")
                return None, [], {"mocked": True}

            output_root = tmp / "planning_out"
            source_dir = output_root / "mqtt" / target_profile.slug / "20260101_000000_000000"
            resumed_dir = output_root / "mqtt" / target_profile.slug / "20260102_000000_000000"
            with patch("agent.planning.orchestrator.DEFAULT_OUTPUT_ROOT", output_root):
                with patch("agent.planning.orchestrator.request_json_candidate", side_effect=source_request):
                    source = PlanningAgent(facts, target, output_dir=source_dir).plan()
                self.assertTrue(source.success, [diag.__dict__ for diag in source.diagnostics])
                type_inventory_path = source_dir / "_step_logs" / "007_5_3_type_data_inventory_candidate.json"
                type_inventory = json.loads(type_inventory_path.read_text(encoding="utf-8"))
                function_inventory = json.loads((source_dir / "_step_logs" / "007_5_4a_function_inventory_candidate.json").read_text(encoding="utf-8"))
                release_modules = {
                    str(function.get("module_id", ""))
                    for function in function_inventory["functions"]
                    if str(function.get("name", "")).endswith(("_cancel", "_destroy", "_free", "_cleanup", "_close"))
                }
                drift_type = next(
                    item
                    for item in type_inventory["types"]
                    if str(item.get("module_id", "")) in release_modules and isinstance(item.get("lifecycle"), dict)
                )
                drift_type["lifecycle"]["destroyed_by"] = ["timer_expiry"]
                drift_type["lifecycle"]["freed_by"] = ["timer_expiry"]
                type_inventory_path.write_text(json.dumps(type_inventory, indent=2), encoding="utf-8")
                with patch("agent.planning.orchestrator.request_json_candidate", side_effect=resumed_request):
                    resumed = PlanningAgent(facts, target, output_dir=resumed_dir).plan(resume_from_stage="5.4d_wire_access_binding")

            self.assertTrue(resumed.success, [diag.__dict__ for diag in resumed.diagnostics])
            self.assertFalse([diag for diag in resumed.diagnostics if diag.code == "type_function_reference_unresolved"])
            self.assertIn("wire_access_binding_patch_prompt", resumed_prompts)
            self.assertIn("function_behavior_patch", resumed.artifact_paths)
            manifest = json.loads((resumed.output_dir / "_step_logs" / "000_planning_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["resume"]["from_stage"], "implementation_plan_5_4d")
            self.assertIn("function_behavior_patch", manifest["resume"]["inherited_artifacts"])
            self.assertNotIn("wire_access_binding_patch", manifest["resume"]["inherited_artifacts"])

    def test_resume_prefix_rejects_input_hash_mismatch(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            target_profile, target_diags = load_target_profile(target)
            self.assertIsNotNone(target_profile, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target_profile)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            source_dir = tmp / "run"
            step_logs = source_dir / "_step_logs"
            step_logs.mkdir(parents=True)
            (step_logs / "000_planning_run_manifest.json").write_text(
                json.dumps(
                    {
                        "inputs": {"facts": {"sha256": "bad"}, "target_profile": {"sha256": "bad"}},
                        "compatibility": {
                            "facts_input_format_version": "protocol_facts/v2alpha1",
                            "target_profile_format_version": "target_profile/v1",
                            "coder_output_format_version": "spec_bundle/current",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (step_logs / "003_planning_ir.json").write_text(json.dumps(planning_ir), encoding="utf-8")
            _, _, diagnostics = validate_resume_prefix(source_dir, "protocol_profile", facts, target, PlanningConfig())
            codes = {diag.code for diag in diagnostics}
            self.assertIn("resume_facts_mismatch", codes)
            self.assertIn("resume_target_profile_mismatch", codes)

    def test_resume_prefix_reports_missing_required_artifact(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target = _write_target_profile(tmp, role="broker")
            source_dir = tmp / "run"
            step_logs = source_dir / "_step_logs"
            step_logs.mkdir(parents=True)
            (step_logs / "000_planning_run_manifest.json").write_text(
                json.dumps(
                    {
                        "inputs": {
                            "facts": {"sha256": hashlib.sha256(facts.read_bytes()).hexdigest()},
                            "target_profile": {"sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
                        },
                        "compatibility": {
                            "facts_input_format_version": "protocol_facts/v2alpha1",
                            "target_profile_format_version": "target_profile/v1",
                            "coder_output_format_version": "spec_bundle/current",
                        },
                    }
                ),
                encoding="utf-8",
            )
            _, _, diagnostics = validate_resume_prefix(source_dir, "architecture", facts, target, PlanningConfig())
            self.assertTrue(any(diag.code == "missing_resume_artifact" for diag in diagnostics), [diag.__dict__ for diag in diagnostics])


if __name__ == "__main__":
    unittest.main()
