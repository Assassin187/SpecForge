from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.coder.specs import load_spec_bundle_from_root
from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.orchestrator import PlanningAgent, compare_output_to_reference
from agent.planning.stages.architecture import build_architecture_candidates, select_architecture
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.implementation_plan import build_implementation_plan
from agent.planning.stages.protocol_profile import build_protocol_profile
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
            architecture_candidates = build_architecture_candidates(planning_ir, profile, constraints)
            selected_architecture = select_architecture(architecture_candidates, profile)
            implementation_plan = build_implementation_plan(planning_ir, profile, constraints, selected_architecture)

            def fake_request(*, prompt_name, messages, config):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "function_contract_prompt":
                    return implementation_plan, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run")
                result = agent.plan()
            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            for filename in (
                "003_planning_ir.json",
                "004_protocol_profile.json",
                "005_engineering_constraints.json",
                "006_architecture_candidates.json",
                "006_selected_architecture.json",
                "007_implementation_plan.json",
                "008_dependency_validation_report.json",
                "010_spec_blueprint.json",
                "013_token_usage_summary.json",
                "014_planning_validation_report.json",
            ):
                self.assertTrue((result.output_dir / "_step_logs" / filename).exists(), filename)
            self.assertTrue((result.output_dir / "coder_manifest.json").exists())
            self.assertTrue((result.output_dir / "spec_bundle").exists())
            profile = json.loads((result.output_dir / "_step_logs" / "004_protocol_profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["schema_version"], "protocol_profile/v1")
            self.assertTrue(profile["required_capabilities"])
            plan = json.loads((result.output_dir / "_step_logs" / "007_implementation_plan.json").read_text(encoding="utf-8"))
            self.assertTrue(plan["wire_mapping_table"])
            self.assertTrue(plan["access_path_table"])
            dependency_report = json.loads((result.output_dir / "_step_logs" / "008_dependency_validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(dependency_report["status"], "passed")
            token_usage = json.loads((result.output_dir / "_step_logs" / "013_token_usage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(token_usage["schema_version"], "planning_token_usage_summary/v1")
            self.assertEqual(token_usage["total"]["total_tokens"], 0)
            bundle = load_spec_bundle_from_root(result.output_dir / "spec_bundle")
            self.assertFalse([diag.__dict__ for diag in bundle.diagnostics if diag.level == "error"])
            report = json.loads((result.output_dir / "_step_logs" / "014_planning_validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["coder_compatibility_status"], "passed")
            comparison = compare_output_to_reference(result.output_dir, ROOT / "specs-example" / "mqtt_specs")
            self.assertTrue(comparison.success, [diag.__dict__ for diag in comparison.diagnostics])


if __name__ == "__main__":
    unittest.main()
