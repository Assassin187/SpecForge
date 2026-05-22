from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.config import PlanningConfig
from agent.planning.diagnostics import PlanningDiagnostic
from agent.planning.orchestrator import PlanningAgent, STEP_FILENAMES, request_architecture_json_candidate
from agent.planning.prompts.templates import architecture_candidate_messages, core_design_candidate_messages
from agent.planning.stages.architecture import build_architecture_candidates, build_architecture_context, select_architecture
from agent.planning.stages.blueprint import build_spec_blueprint
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.implementation_plan import build_implementation_plan
from agent.planning.stages.implementation_plan_context import build_core_design_context
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.validators.architecture import validate_architecture_candidates
from agent.planning.validators.blueprint import validate_spec_blueprint
from agent.planning.validators.implementation_plan import validate_implementation_plan


ROOT = Path(__file__).resolve().parents[3]


def _target_profile(root: Path) -> Path:
    path = root / "target_profile.json"
    path.write_text(
        json.dumps(
            {
                "target_role": "broker",
                "language": "C",
                "runtime": "Linux epoll",
                "scope": "minimum_v1",
                "deployment_constraints": {"memory_limit": "low", "persistence": False, "tls_mode": "terminated_upstream"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _build_artifacts(tmp: Path):
    facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
    target, target_diags = load_target_profile(_target_profile(tmp))
    assert target is not None, target_diags
    planning_ir, ir_diags = build_planning_ir(facts, target)
    assert planning_ir is not None, ir_diags
    profile = build_protocol_profile(planning_ir)
    constraints = activate_constraints(profile)
    architecture_candidates = build_architecture_candidates(planning_ir, profile, constraints)
    selected_architecture = select_architecture(architecture_candidates, profile)
    implementation_plan = build_implementation_plan(planning_ir, profile, constraints, selected_architecture)
    return planning_ir, profile, constraints, architecture_candidates, selected_architecture, implementation_plan


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


class PlanningValidatorTests(unittest.TestCase):
    def test_architecture_validator_rejects_uncovered_capability(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, profile, constraints, candidates, _, _ = _build_artifacts(Path(raw_tmp))
            broken = copy.deepcopy(candidates)
            broken["candidates"][0]["modules"][0]["owned_capabilities"] = []
            diags = validate_architecture_candidates(broken, profile, constraints)
            self.assertTrue(any(diag.code == "architecture_module_without_capability" for diag in diags), [diag.__dict__ for diag in diags])
            self.assertTrue(any(diag.code == "architecture_uncovered_capability" for diag in diags), [diag.__dict__ for diag in diags])

    def test_architecture_dependency_hints_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, profile, constraints, candidates, _, _ = _build_artifacts(Path(raw_tmp))
            base = {"schema_version": "architecture_candidates/v1", "candidates": [copy.deepcopy(candidates["candidates"][0])], "generation_warnings": []}
            modules = base["candidates"][0]["modules"]
            provider = modules[1]["module_id"]
            consumer = modules[0]["module_id"]
            modules[0]["dependency_hints"] = [provider]
            self.assertFalse(validate_architecture_candidates(base, profile, constraints))

            unknown = copy.deepcopy(base)
            unknown["candidates"][0]["modules"][0]["dependency_hints"] = ["missing_module"]
            self.assertTrue(any(diag.code == "architecture_unknown_dependency_hint" for diag in validate_architecture_candidates(unknown, profile, constraints)))

            self_dep = copy.deepcopy(base)
            self_dep["candidates"][0]["modules"][0]["dependency_hints"] = [consumer]
            self.assertTrue(any(diag.code == "architecture_self_dependency_hint" for diag in validate_architecture_candidates(self_dep, profile, constraints)))

            cycle = copy.deepcopy(base)
            cycle["candidates"][0]["modules"][1]["dependency_hints"] = [consumer]
            self.assertTrue(any(diag.code == "architecture_dependency_cycle" for diag in validate_architecture_candidates(cycle, profile, constraints)))

    def test_implementation_validator_rejects_missing_wire_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _, _, _, implementation_plan = _build_artifacts(Path(raw_tmp))
            broken = copy.deepcopy(implementation_plan)
            removed = broken["wire_mapping_table"].pop(0)
            for function in broken["function_contracts"]:
                if function.get("function_kind") in {"parser", "serializer"}:
                    function["wire_mapping"] = [item for item in function.get("wire_mapping", []) if item.get("field_id") != removed["field_id"]]
            diags = validate_implementation_plan(broken, profile=profile, planning_ir=planning_ir)
            self.assertTrue(any(diag.code == "uncovered_wire_field" for diag in diags), [diag.__dict__ for diag in diags])

    def test_blueprint_validator_rejects_added_function(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, _, implementation_plan = _build_artifacts(Path(raw_tmp))
            blueprint = build_spec_blueprint(implementation_plan)
            added = copy.deepcopy(blueprint["functions"][0])
            added["function_id"] = "fn:file:rogue"
            blueprint["functions"].append(added)
            diags = validate_spec_blueprint(blueprint, implementation_plan)
            self.assertTrue(any(diag.code == "blueprint_added_function" for diag in diags), [diag.__dict__ for diag in diags])

    def test_architecture_context_is_compact_and_prompt_marks_hints_non_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, _, _, _ = _build_artifacts(Path(raw_tmp))
            context = build_architecture_context(planning_ir, profile, constraints)
            self.assertNotIn("protocol_facts", context)
            self.assertNotIn("required_capabilities", context)
            self.assertEqual(
                sorted(context["required_capability_ids"]),
                sorted(item["capability_id"] for item in profile["required_capabilities"]),
            )
            messages = architecture_candidate_messages(context, "minimal_scope")
            prompt_payload = json.loads(messages[1]["content"])
            self.assertIn("non-binding engineering priors", messages[0]["content"])
            self.assertIn("architecture_context", prompt_payload)
            self.assertNotIn("protocol_profile", prompt_payload)
            self.assertNotIn("forbidden_top_level_keys", prompt_payload)
            self.assertNotIn("known_rejection_patterns_to_avoid", prompt_payload)
            module_shape = prompt_payload["expected_response"]["candidates"][0]["modules"][0]
            self.assertEqual(module_shape["dependency_hints"], ["provider module_id"])
            self.assertIn("json.loads", messages[0]["content"])
            self.assertFalse(any("dependency_hints must be []" in rule for rule in prompt_payload["hard_validation_rules"]))
            self.assertTrue(any("consumer module -> provider module" in rule for rule in prompt_payload["hard_validation_rules"]))
            self.assertTrue(any("trailing semicolon" in rule for rule in prompt_payload["hard_validation_rules"]))
            self.assertTrue(any("DAG" in rule for rule in prompt_payload["hard_validation_rules"]))
            self.assertTrue(any("High-level role modules" in rule for rule in prompt_payload["hard_validation_rules"]))
            self.assertTrue(any("fallback names only" in rule for rule in prompt_payload["domain_derivation_instructions"]))
            self.assertTrue(any("broker_app" in rule and "topic" in rule for rule in prompt_payload["domain_derivation_instructions"]))

    def test_architecture_json_retry_accepts_third_valid_response(self) -> None:
        messages = [{"role": "user", "content": "{}"}]
        valid = {"schema_version": "architecture_candidates/v1", "candidates": [], "generation_warnings": []}
        calls: list[list[dict[str, str]]] = []

        def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
            calls.append(messages)
            meta = {
                "enabled": True,
                "prompt_name": prompt_name,
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                "content_length": 5,
                "temperature": temperature,
                "enable_thinking": enable_thinking,
            }
            if len(calls) < 3:
                return None, [PlanningDiagnostic("warning", "invalid_llm_json", "bad json")], meta
            return valid, [], meta

        with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
            candidate, diags, meta = request_architecture_json_candidate(
                messages=messages,
                config=PlanningConfig(),
                temperature=0.7,
                enable_thinking=True,
            )

        self.assertEqual(candidate, valid)
        self.assertFalse(diags)
        self.assertEqual(len(calls), 3)
        self.assertEqual(meta["json_retry_attempts"], 3)
        self.assertEqual(meta["usage"], {"prompt_tokens": 3, "completion_tokens": 6, "total_tokens": 9})
        self.assertIn("json.loads", calls[1][-1]["content"])
        self.assertIn("Do not change the architecture task", calls[1][-1]["content"])

    def test_architecture_json_retry_stops_after_three_invalid_json_responses(self) -> None:
        calls = 0

        def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
            nonlocal calls
            calls += 1
            return (
                None,
                [PlanningDiagnostic("warning", "invalid_llm_json", f"bad json {calls}")],
                {
                    "enabled": True,
                    "prompt_name": prompt_name,
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "content_length": 0,
                },
            )

        with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
            candidate, diags, meta = request_architecture_json_candidate(
                messages=[{"role": "user", "content": "{}"}],
                config=PlanningConfig(),
                temperature=0.7,
                enable_thinking=True,
            )

        self.assertIsNone(candidate)
        self.assertEqual(calls, 3)
        self.assertTrue(any(diag.code == "invalid_llm_json" for diag in diags))
        self.assertEqual(meta["json_retry_attempts"], 3)
        self.assertEqual(meta["usage"], {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6})

    def test_core_design_prompt_uses_compact_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, _, selected_architecture, implementation_plan = _build_artifacts(Path(raw_tmp))
            context = build_core_design_context(planning_ir, profile, constraints, selected_architecture)
            messages = core_design_candidate_messages(context)
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["prompt_name"], "core_design_candidate_prompt")
            self.assertNotIn("planning_ir", payload)
            self.assertNotIn("protocol_profile", payload)
            self.assertNotIn("deterministic_baseline_plan", payload)
            self.assertIn("core_design_context", payload)
            context = payload["core_design_context"]
            self.assertEqual(context["schema_version"], "core_design_context/v1")
            selected_module_ids = {
                str(module["module_id"])
                for module in selected_architecture["architecture"]["modules"]
                if isinstance(module, dict)
            }
            context_module_ids = {str(module["module_id"]) for module in context["selected_modules"]}
            self.assertTrue(selected_module_ids <= context_module_ids)
            required_capability_ids = {str(item["capability_id"]) for item in profile["required_capabilities"]}
            context_capability_ids = {str(item["capability_id"]) for item in context["required_capabilities"]}
            self.assertEqual(required_capability_ids, context_capability_ids)
            self.assertIn("dependency_graph", payload["forbidden_fields"])

    def test_invalid_llm_candidate_retries_then_fails_without_fallback(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = _target_profile(Path(raw_tmp))
            calls: list[tuple[str, list[dict[str, str]]]] = []
            invalid_architecture = {
                "schema_version": "architecture_candidates/v1",
                "candidates": [
                    {
                        "candidate_id": "bad",
                        "modules": [{"module_id": "empty", "name": "empty", "owned_capabilities": [], "responsibilities": []}],
                    }
                ],
            }

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                calls.append((prompt_name, messages))
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return invalid_architecture, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run", config=PlanningConfig())
                result = agent.plan()

            self.assertFalse(result.success)
            self.assertTrue(any(diag.code == "architecture_mandatory_llm_failed" for diag in result.diagnostics), [diag.__dict__ for diag in result.diagnostics])
            self.assertFalse((result.output_dir / "_step_logs" / "006_architecture_candidates.json").exists())
            token_usage = json.loads((result.output_dir / "_step_logs" / "013_token_usage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(token_usage["by_stage"]["architecture"]["attempt_count"], 6)
            architecture_calls = [messages for prompt_name, messages in calls if prompt_name == "architecture_candidate_prompt"]
            self.assertEqual(len(architecture_calls), 6)
            architecture_payloads = [json.loads(messages[-1]["content"]) for messages in architecture_calls]
            self.assertEqual(
                {"capability_clustered", "layered_runtime_codec_semantic", "minimal_scope"},
                {payload["design_strategy"] for payload in architecture_payloads[:3]},
            )
            self.assertEqual(
                {"capability_clustered", "layered_runtime_codec_semantic", "minimal_scope"},
                {payload["design_strategy"] for payload in architecture_payloads[3:]},
            )

    def test_invalid_architecture_ranking_uses_deterministic_ranking_fallback(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = _target_profile(Path(raw_tmp))
            _, _, _, architecture_candidates, _, implementation_plan = _build_artifacts(Path(raw_tmp))

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return {"schema_version": "architecture_ranking/v1", "scores": [], "selected_candidate_id": "missing"}, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run", config=PlanningConfig())
                result = agent.plan()

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            ranking = json.loads((result.output_dir / "_step_logs" / "006_architecture_ranking.json").read_text(encoding="utf-8"))
            self.assertTrue(ranking["fallback_used"])
            self.assertTrue(ranking["selected_candidate_id"])

    def test_per_module_function_stage_outputs_are_preserved(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = _target_profile(Path(raw_tmp))
            _, _, _, architecture_candidates, _, _ = _build_artifacts(Path(raw_tmp))

            def fake_request(*, prompt_name, messages, config, temperature=None, enable_thinking=False):
                if prompt_name == "protocol_profile_patch_prompt":
                    return _noop_profile_patch_candidate(), [], {"mocked": True}
                if prompt_name == "architecture_candidate_prompt":
                    return architecture_candidates, [], {"mocked": True}
                if prompt_name == "architecture_ranking_prompt":
                    return {"schema_version": "architecture_ranking/v1", "scores": [], "selected_candidate_id": "missing"}, [], {"mocked": True}
                return None, [], {"mocked": True}

            with patch("agent.planning.orchestrator.request_json_candidate", side_effect=fake_request):
                agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run", config=PlanningConfig(llm_max_retries=1))
                result = agent.plan()

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            step_logs = result.output_dir / "_step_logs"
            plan = json.loads((step_logs / STEP_FILENAMES["implementation_plan"]).read_text(encoding="utf-8"))
            module_count = len(plan["module_contracts"])
            inventory_files = sorted(step_logs.glob("007_5_4a_function_inventory_candidate__*.json"))
            signature_files = sorted(step_logs.glob("007_5_4b_function_signature_patch__*.json"))
            behavior_files = sorted(step_logs.glob("007_5_4c_function_behavior_contract_patch__*.json"))
            self.assertEqual(module_count, len(inventory_files))
            self.assertGreaterEqual(len(signature_files), module_count)
            self.assertGreaterEqual(len(behavior_files), module_count)
            inventory = json.loads((step_logs / STEP_FILENAMES["function_inventory_candidate"]).read_text(encoding="utf-8"))
            signature = json.loads((step_logs / STEP_FILENAMES["function_signature_patch"]).read_text(encoding="utf-8"))
            behavior = json.loads((step_logs / STEP_FILENAMES["function_behavior_patch"]).read_text(encoding="utf-8"))
            inventory_ids = {item["function_id"] for item in inventory["functions"]}
            runtime_functions = [
                item for item in plan["function_contracts"]
                if item.get("function_id") not in inventory_ids
            ]
            self.assertEqual(len(plan["function_contracts"]), len(inventory["functions"]) + len(runtime_functions))
            self.assertEqual(len(inventory["functions"]), len(signature["function_signature_updates"]))
            self.assertEqual(len(inventory["functions"]), len(behavior["function_behavior_updates"]))
            self.assertTrue((step_logs / STEP_FILENAMES["runtime_entrypoint_candidate"]).exists())


if __name__ == "__main__":
    unittest.main()
