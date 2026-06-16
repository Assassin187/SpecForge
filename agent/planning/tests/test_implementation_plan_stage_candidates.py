from __future__ import annotations

import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.config import PlanningConfig
from agent.planning.orchestrator import PlanningAgent, _file_layout_override_actionable
from agent.planning.prompts.templates import (
    architecture_ranking_messages,
    calls_allowed_candidate_messages,
    core_design_candidate_messages,
    dependency_repair_patch_messages,
    file_layout_candidate_messages,
    file_layout_override_patch_messages,
    function_annotation_candidate_messages,
    function_behavior_contract_patch_messages,
    function_signature_patch_messages,
    module_artifacts_candidate_messages,
    runtime_entrypoint_candidate_messages,
    type_filling_candidate_messages,
    wire_access_binding_patch_messages,
)
from agent.planning.stages.architecture import deterministic_architecture_ranking, select_architecture
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.implementation_plan_context import (
    build_calls_allowed_context,
    build_core_design_context,
    build_dependency_repair_context,
    build_file_layout_context,
    build_file_layout_override_context,
    build_function_behavior_context,
    build_function_inventory_context,
    build_function_signature_context,
    build_module_artifact_context,
    build_runtime_entrypoint_context,
    build_type_inventory_context,
    build_wire_access_binding_context,
    derive_type_generation_targets,
    derive_type_obligations,
    normalize_function_behavior_contract_patch,
    normalize_type_inventory_candidate,
)
from agent.planning.stages.inventory_planning_space import build_function_planning_space, build_type_planning_space
from agent.planning.stages.inventory_reconciliation import reconcile_function_annotation_candidate, reconcile_type_filling_candidate
from agent.planning.stages.function_inventory_decomposition import DECOMPOSITION_RULES, select_top_decomposition_hints
from agent.planning.stages.dependencies import derive_dependency_graph
from agent.planning.stages.implementation_plan_merger import (
    apply_deterministic_dependency_fallback,
    build_plan_skeleton,
    fallback_calls_allowed,
    fallback_core_design,
    fallback_dependency_repair_patch,
    fallback_file_layout,
    fallback_function_behavior,
    fallback_function_signatures,
    fallback_module_artifacts,
    fallback_runtime_entrypoint,
    fallback_wire_access_binding,
    apply_file_layout_override_patch,
    cleanup_final_unresolved_questions,
    finalize_dependency_graph,
    normalize_calls_allowed_aggregate,
    normalize_calls_allowed_candidate,
    normalize_file_layout_candidate,
    normalize_function_signature_patch,
    repair_function_inventory_symbols,
    merge_calls_allowed,
    merge_core_design,
    merge_file_layout,
    merge_function_behavior,
    merge_function_inventory,
    merge_function_signatures,
    merge_module_artifacts,
    merge_runtime_entrypoint,
    merge_type_inventory,
    merge_wire_access_binding,
    reconcile_type_inventory_function_refs,
)
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.tests.current_flow_fixtures import (
    current_architecture_candidates,
    current_function_inventory_candidate,
    current_type_inventory_candidate,
)
from agent.planning.validators.implementation_plan_stages import (
    validate_calls_allowed_candidate,
    validate_core_design_candidate,
    validate_dependency_repair_patch,
    validate_file_layout_candidate,
    validate_file_layout_override_patch,
    validate_function_behavior_contract_patch,
    validate_function_inventory_candidate,
    validate_function_signature_patch,
    validate_full_implementation_plan,
    validate_module_artifacts_candidate,
    validate_runtime_entrypoint_candidate,
    validate_type_inventory_candidate,
    validate_wire_access_binding_patch,
    validation_report,
    function_inventory_decomposition_report,
)
from agent.planning.validators.dependencies import validate_dependency_graph


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


def _has(diags, code: str) -> bool:
    return any(diag.code == code for diag in diags)


def _has_error(diags) -> bool:
    return any(diag.level == "error" for diag in diags)


def _break_first_state_access(candidate: dict) -> None:
    for update in candidate["function_behavior_updates"]:
        if update["state_access"]:
            update["state_access"][0]["access_kind"] = "bad"
            return
    candidate["function_behavior_updates"][0]["state_access"].append({"state_id": "state:missing", "access_kind": "bad", "required": True, "reason": "bad"})


def _break_first_call(candidate: dict) -> None:
    for update in candidate["call_updates"]:
        if update["calls_allowed"]:
            update["calls_allowed"][0]["call_kind"] = "bad"
            return
    candidate["call_updates"][0]["calls_allowed"].append({"callee_function_id": candidate["call_updates"][0]["caller_function_id"], "call_kind": "bad", "required": True, "service_requirement_ids": [], "call_reason": "bad", "param_bindings": [], "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""}, "failure_behavior": "ignore", "trace_ref_keys": [], "status": "assumed"})


def _call_edge(callee: str, service_ids: list[str]) -> dict:
    return {
        "callee_function_id": callee,
        "call_kind": "service_requirement",
        "required": True,
        "service_requirement_ids": service_ids,
        "call_reason": "test edge",
        "param_bindings": [],
        "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
        "failure_behavior": "return_error",
        "trace_ref_keys": [],
        "status": "inferred",
    }


def _inventory_function(name: str, module_id: str, *, function_id: str | None = None, kind: str = "public_api", public: bool = True, purpose: str = "test function") -> dict:
    return {
        "function_id": function_id or f"fn:{module_id}:{name}",
        "name": name,
        "module_id": module_id,
        "function_kind": kind,
        "coder_function_type": "ALGORITHM",
        "visibility": "public" if public else "internal",
        "api_surface": "public" if public else "module_internal",
        "exported": public,
        "export_reason": "test public boundary" if public else "",
        "public_api_role": "module_boundary_operation" if public else "",
        "grouping_hint": module_id,
        "purpose": purpose,
        "capability_ids": [],
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "trace_ref_keys": [],
        "status": "inferred",
    }


class ImplementationPlanStageCandidateTests(unittest.TestCase):
    def test_type_and_function_inventory_stages_use_controlled_reconciliation(self) -> None:
        plan_source = inspect.getsource(PlanningAgent.plan)
        self.assertGreaterEqual(plan_source.count("_run_controlled_inventory_candidate("), 2)
        self.assertIn("type_filling_candidate_prompt", plan_source)
        self.assertIn("function_annotation_candidate_prompt", plan_source)
        self.assertLess(plan_source.index("repair_function_inventory_symbols"), plan_source.index("function_signature_patch_prompt"))

    def _fixtures(self, tmp: Path):
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        target, target_diags = load_target_profile(_target_profile(tmp))
        self.assertIsNotNone(target, [diag.__dict__ for diag in target_diags])
        planning_ir, ir_diags = build_planning_ir(facts, target)
        self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
        profile = build_protocol_profile(planning_ir)
        constraints = activate_constraints(profile)
        candidates = current_architecture_candidates(planning_ir, profile, constraints)
        selected = select_architecture(candidates, profile)
        draft = build_plan_skeleton(planning_ir, profile, constraints, selected)

        core = fallback_core_design(draft, planning_ir, constraints, selected, profile)
        draft = merge_core_design(draft, core)
        modules = fallback_module_artifacts(draft, profile, constraints, selected)
        draft = merge_module_artifacts(draft, modules)
        type_inventories = []
        for module in list(draft["module_artifacts"]):
            type_inventory = current_type_inventory_candidate(draft, module, planning_ir, profile, constraints)
            type_inventories.append(type_inventory)
            draft = merge_type_inventory(draft, type_inventory)
        inventories = []
        for module in list(draft["module_artifacts"]):
            inventory = current_function_inventory_candidate(draft, module, planning_ir, profile, constraints)
            inventories.append(inventory)
            draft = merge_function_inventory(draft, inventory)
        signature_patches = []
        for module in list(draft["module_artifacts"]):
            patch = fallback_function_signatures(draft, str(module["module_id"]))
            signature_patches.append(patch)
            draft = merge_function_signatures(draft, patch)
        behavior_patches = []
        for module in list(draft["module_artifacts"]):
            patch = fallback_function_behavior(draft, str(module["module_id"]))
            behavior_patches.append(patch)
            draft = merge_function_behavior(draft, patch)
        wire = fallback_wire_access_binding(draft, planning_ir)
        draft = merge_wire_access_binding(draft, wire)
        calls = fallback_calls_allowed(draft)
        draft = merge_calls_allowed(draft, calls)
        layout = fallback_file_layout(draft)
        draft = merge_file_layout(draft, layout)
        runtime = fallback_runtime_entrypoint(draft)
        final_draft = merge_runtime_entrypoint(draft, runtime)
        repair = fallback_dependency_repair_patch(draft, [{"code": "dependency_cycle", "message": "cycle"}])
        plan = finalize_dependency_graph(final_draft)
        return planning_ir, profile, constraints, selected, draft, plan, {
            "core": core,
            "modules": modules,
            "type_inventory": type_inventories[0],
            "type_inventories": type_inventories,
            "inventory": inventories[0],
            "inventories": inventories,
            "signature": signature_patches[0],
            "behavior": behavior_patches[0],
            "wire": wire,
            "calls": calls,
            "layout": layout,
            "runtime": runtime,
            "final_draft": final_draft,
            "repair": repair,
        }

    def test_architecture_ranking_fallback_prefers_engineering_boundaries(self) -> None:
        profile = {
            "required_capabilities": [
                {"capability_id": "transport_io"},
                {"capability_id": "message_decode"},
                {"capability_id": "message_encode"},
                {"capability_id": "session_state"},
                {"capability_id": "routing_index"},
                {"capability_id": "semantic_dispatch"},
                {"capability_id": "role_composition"},
            ]
        }
        minimal = {
            "candidate_id": "minimal_scope",
            "modules": [
                {
                    "module_id": "runtime_core",
                    "name": "runtime_core",
                    "responsibilities": ["transport, codec, session, and routing catch-all"],
                    "owned_capabilities": ["transport_io", "message_decode", "message_encode", "session_state", "routing_index"],
                    "state_owned": ["connection_state", "session_state"],
                    "dependency_hints": [],
                },
                {
                    "module_id": "broker",
                    "name": "broker",
                    "responsibilities": ["broker dispatch and role composition"],
                    "owned_capabilities": ["semantic_dispatch", "role_composition"],
                    "state_owned": [],
                    "dependency_hints": ["runtime_core"],
                },
            ],
        }
        layered = {
            "candidate_id": "capability_clustered",
            "modules": [
                {"module_id": "network", "name": "network", "responsibilities": ["TCP epoll connection runtime"], "owned_capabilities": ["transport_io"], "state_owned": ["connection_state"], "dependency_hints": []},
                {"module_id": "mqtt_codec", "name": "mqtt_codec", "responsibilities": ["packet data model, decoder, encoder"], "owned_capabilities": ["message_decode", "message_encode"], "state_owned": [], "dependency_hints": []},
                {"module_id": "session", "name": "session", "responsibilities": ["session state manager"], "owned_capabilities": ["session_state"], "state_owned": ["session_state"], "dependency_hints": []},
                {"module_id": "topic_router", "name": "topic_router", "responsibilities": ["topic tree and routing dispatch"], "owned_capabilities": ["routing_index"], "state_owned": ["subscription_index"], "dependency_hints": ["session"]},
                {"module_id": "broker_app", "name": "broker_app", "responsibilities": ["broker lifecycle and packet dispatch"], "owned_capabilities": ["semantic_dispatch", "role_composition"], "state_owned": [], "dependency_hints": ["network", "mqtt_codec", "session", "topic_router"]},
            ],
        }
        ranking = deterministic_architecture_ranking({"schema_version": "architecture_candidates/v1", "candidates": [minimal, layered]}, profile)
        self.assertEqual(ranking["selected_candidate_id"], "capability_clustered")
        self.assertGreater(
            ranking["scores"][0]["dimension_scores"]["cohesion"],
            next(item for item in ranking["scores"] if item["candidate_id"] == "minimal_scope")["dimension_scores"]["cohesion"],
        )

        prompt_payload = json.loads(architecture_ranking_messages({}, {"candidates": [minimal, layered]})[1]["content"])
        rules_text = "\n".join(prompt_payload["application_protocol_engineering_experience"])
        self.assertIn("source/header boundaries", rules_text)
        self.assertIn("minimal_scope", rules_text)

    def test_full_readiness_requires_metadata_and_valid_call_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _constraints, _selected, _draft, plan, _items = self._fixtures(Path(raw_tmp))
            diagnostics = validate_full_implementation_plan(plan, profile=profile, planning_ir=planning_ir)
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            self.assertEqual(plan["protocol_metadata"]["protocol_version"], "3.1.1")
            self.assertEqual(plan["protocol_metadata"]["roles"], ["BROKER"])

            missing_metadata = copy.deepcopy(plan)
            missing_metadata["protocol_metadata"]["protocol_version"] = ""
            diagnostics = validate_full_implementation_plan(missing_metadata, profile=profile, planning_ir=planning_ir)
            self.assertTrue(_has(diagnostics, "readiness_missing_protocol_version"))

            bad_call = copy.deepcopy(plan)
            caller = next(function for function in bad_call["function_contracts"] if function.get("call_contracts"))
            caller["call_contracts"][0]["param_bindings"] = [{"param_name": "x", "value_ref": "missing_local"}]
            diagnostics = validate_full_implementation_plan(bad_call, profile=profile, planning_ir=planning_ir)
            self.assertTrue(
                _has(diagnostics, "readiness_call_contract_param_count_mismatch")
                or _has(diagnostics, "readiness_call_contract_param_name_mismatch")
            )

    def test_full_readiness_allows_c_expressions_and_local_value_refs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _constraints, _selected, _draft, plan, _items = self._fixtures(Path(raw_tmp))
            caller = next(function for function in plan["function_contracts"] if function.get("signature", {}).get("params"))
            callee = copy.deepcopy(caller)
            callee["function_id"] = "fn:test:callee"
            callee["name"] = "test_callee"
            callee["module_id"] = caller.get("module_id", "")
            callee["signature"] = {
                "return_type": "int",
                "name": "test_callee",
                "params": [{"name": "packet", "type": "mqtt_packet_t *"}],
                "raw": "int test_callee(mqtt_packet_t *packet)",
            }
            caller["call_contracts"] = [
                {
                    "callee_function_id": callee["function_id"],
                    "call_kind": "utility",
                    "required": True,
                    "service_requirement_ids": [],
                    "call_reason": "test local expression binding",
                    "param_bindings": [{"param_name": "packet", "value_ref": "payload->topics[i].topic_filter", "ownership": "borrowed", "nullability": "non_null"}],
                    "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
                    "failure_behavior": "return_error",
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ]
            plan["function_contracts"].append(callee)
            for value_ref in (
                "payload->topics[i].topic_filter",
                "conn->input_buffer.data + conn->input_buffer.len",
                "(mqtt_codec_decode_result_t*)packet",
                "header->packet_type << 4 | encode_remaining_length_flags",
                "strlen(client_id)",
                "local_packet",
                '"$SYS/broker/init"',
                '(uint8_t*)"init"',
                '(const uint8_t *)"init"',
                "'x'",
            ):
                caller["call_contracts"][0]["param_bindings"][0]["value_ref"] = value_ref
                diagnostics = validate_full_implementation_plan(plan, profile=profile, planning_ir=planning_ir)
                self.assertFalse(_has(diagnostics, "readiness_call_contract_unknown_param_binding"), value_ref)
            for value_ref in ('"unterminated', '(evil_t*)"init"'):
                caller["call_contracts"][0]["param_bindings"][0]["value_ref"] = value_ref
                diagnostics = validate_full_implementation_plan(plan, profile=profile, planning_ir=planning_ir)
                self.assertTrue(_has(diagnostics, "readiness_call_contract_unknown_param_binding"), value_ref)

    def test_full_readiness_allows_known_function_value_ref_and_rejects_unknown_function_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _constraints, _selected, _draft, plan, _items = self._fixtures(Path(raw_tmp))
            caller = next(function for function in plan["function_contracts"] if function.get("signature", {}).get("params"))
            callback = copy.deepcopy(caller)
            callback["function_id"] = "fn:test:on_data_cb"
            callback["name"] = "test_on_data_cb"
            callback["module_id"] = caller.get("module_id", "")
            callback["visibility"] = "internal"
            callback["api_surface"] = "module_internal"
            callback["exported"] = False
            callee = copy.deepcopy(caller)
            callee["function_id"] = "fn:test:register"
            callee["name"] = "test_register"
            callee["module_id"] = caller.get("module_id", "")
            callee["signature"] = {
                "return_type": "int",
                "name": "test_register",
                "params": [{"name": "callback", "type": "test_on_data_fn"}],
                "raw": "int test_register(test_on_data_fn callback)",
            }
            caller["call_contracts"] = [
                {
                    "callee_function_id": callee["function_id"],
                    "call_kind": "utility",
                    "required": True,
                    "service_requirement_ids": [],
                    "call_reason": "register callback",
                    "param_bindings": [{"param_name": "callback", "value_ref": callback["function_id"], "ownership": "borrowed", "nullability": "non_null"}],
                    "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""},
                    "failure_behavior": "return_error",
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ]
            plan["function_contracts"].extend([callback, callee])

            diagnostics = validate_full_implementation_plan(plan, profile=profile, planning_ir=planning_ir)
            self.assertFalse(_has(diagnostics, "readiness_call_contract_unknown_param_binding"), [diag.__dict__ for diag in diagnostics])

            caller["call_contracts"][0]["param_bindings"][0]["value_ref"] = "fn:test:missing_cb"
            diagnostics = validate_full_implementation_plan(plan, profile=profile, planning_ir=planning_ir)
            self.assertTrue(_has(diagnostics, "readiness_call_contract_unknown_param_binding"))

    def test_function_symbol_repair_renames_internal_duplicate_and_updates_type_refs(self) -> None:
        public_codec = _inventory_function("mqtt_encoder_encode", "codec", function_id="fn:codec:mqtt_encoder_encode", kind="serializer", public=True)
        internal_network = _inventory_function("mqtt_encoder_encode", "network", function_id="fn:network:mqtt_encoder_encode", kind="resource_lifecycle", public=False)
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:function_inventory:all_modules",
            "producer": {"stage": "test", "prompt_name": "test", "prompt_version": "test"},
            "module_id": "all_modules",
            "functions": [copy.deepcopy(public_codec), copy.deepcopy(internal_network)],
            "assumptions": [],
            "unresolved_questions": [],
        }
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [{"module_id": "codec"}, {"module_id": "network"}],
            "function_contracts": [copy.deepcopy(public_codec), copy.deepcopy(internal_network)],
            "type_inventory": [
                {
                    "type_id": "type:network:mqtt_network_encode_buffer_t",
                    "name": "mqtt_network_encode_buffer_t",
                    "module_id": "network",
                    "visibility": "module_internal",
                    "defined_in": "internal_header",
                    "lifecycle": {"created_by": ["mqtt_encoder_encode"], "initialized_by": ["mqtt_encoder_encode"], "destroyed_by": [], "freed_by": ["mqtt_network_encode_buffer_free"]},
                    "related_functions": ["mqtt_encoder_encode"],
                }
            ],
        }
        self.assertTrue(_has(validate_function_inventory_candidate(candidate, draft["module_artifacts"], draft, {}, {}), "duplicate_function_name"))
        repaired_draft, repaired_candidate, report = repair_function_inventory_symbols(draft, candidate)
        self.assertFalse(_has(validate_function_inventory_candidate(repaired_candidate, draft["module_artifacts"], repaired_draft, {}, {}), "duplicate_function_name"))
        repaired_network = next(item for item in repaired_candidate["functions"] if item["function_id"] == "fn:network:mqtt_encoder_encode")
        self.assertEqual(repaired_network["name"], "mqtt_network_encode_buffer_create")
        lifecycle = repaired_draft["type_inventory"][0]["lifecycle"]
        self.assertEqual(lifecycle["created_by"], ["mqtt_network_encode_buffer_create"])
        self.assertEqual(lifecycle["initialized_by"], ["mqtt_network_encode_buffer_create"])
        self.assertEqual(report["renamed_functions"][0]["function_id"], "fn:network:mqtt_encoder_encode")

    def test_signature_normalizer_canonicalizes_mechanical_fields(self) -> None:
        function = _inventory_function("mqtt_codec_helper", "codec", function_id="fn:codec:mqtt_codec_helper", kind="resource_lifecycle", public=False)
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [{"module_id": "codec"}],
            "function_contracts": [copy.deepcopy(function)],
            "canonical_types": [],
            "type_inventory": [
                {
                    "type_id": "type:codec:mqtt_codec_private_t",
                    "name": "mqtt_codec_private_t",
                    "module_id": "codec",
                    "visibility": "module_internal",
                    "defined_in": "internal_header",
                }
            ],
        }
        candidate = {
            "schema_version": "function_signature_patch/v1",
            "patch_id": "patch:test",
            "producer": {"stage": "test", "prompt_name": "function_signature_patch_prompt", "prompt_version": "test"},
            "module_id": "codec",
            "batch": {"index": 0, "size": 1},
            "function_signature_updates": [
                {
                    "function_id": "fn:codec:mqtt_codec_helper",
                    "signature": {
                        "raw": "int mqtt_codec_helper(mqtt_codec_private_t value, size_t len)",
                        "name": "mqtt_codec_helper",
                        "storage_class": "none",
                        "return_type": "int",
                        "params": [
                            {"name": "value", "type": "mqtt_codec_private_t", "type_ref": "type:codec:mqtt_codec_private_t", "direction": "in", "nullable": False, "ownership": "BY_VALUE", "passing_mode": "by_value"},
                            {"name": "len", "type": "size_t", "type_ref": "size_t", "direction": "in", "nullable": False, "ownership": "BORROWED", "passing_mode": "by_value"},
                        ],
                    },
                    "signature_dependencies": [
                        {"symbol_name": "mqtt_codec_private_t", "symbol_kind": "type", "type_ref": "type:codec:mqtt_codec_private_t", "owner_module_id": "codec", "dependency_scope": "internal_header", "reason": "private helper input"},
                        {"symbol_name": "size_t", "symbol_kind": "system_type", "type_ref": "size_t", "owner_module_id": "system", "dependency_scope": "header", "reason": "length"},
                    ],
                    "interface_type_declarations": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        normalized, stats = normalize_function_signature_patch(candidate, draft, "codec", [function], batch_index=0, batch_size=1)
        update = normalized["function_signature_updates"][0]
        self.assertEqual(update["signature"]["storage_class"], "static")
        self.assertTrue(update["signature"]["raw"].startswith("static "))
        self.assertEqual(update["signature"]["params"][0]["ownership"], "UNKNOWN")
        self.assertEqual(update["signature"]["params"][0]["passing_mode"], "by_value")
        self.assertEqual(update["signature_dependencies"][0]["dependency_scope"], "source")
        self.assertEqual(update["signature_dependencies"][1]["owner_module_id"], "")
        self.assertGreater(stats["signature_param_ownership_normalized"], 0)
        self.assertFalse(validate_function_signature_patch(normalized, draft, {"fn:codec:mqtt_codec_helper"}))

    def test_calls_normalizer_fills_batch_and_closes_service_requirements(self) -> None:
        caller = _inventory_function("mqtt_network_cleanup_session", "network", function_id="fn:network:mqtt_network_cleanup_session", public=False)
        caller["service_requirements"] = [{"service_requirement_id": "svc:network:session_cleanup", "requirement_kind": "cross_module_service"}]
        missing_caller = _inventory_function("mqtt_network_bind_connection", "network", function_id="fn:network:mqtt_network_bind_connection", public=False)
        missing_caller["service_requirements"] = [{"service_requirement_id": "svc:network:session_bind", "requirement_kind": "cross_module_service"}]
        session_api = _inventory_function("mqtt_session_cleanup", "session", function_id="fn:session:mqtt_session_cleanup", public=True)
        private_router = _inventory_function("mqtt_router_cleanup", "router", function_id="fn:router:mqtt_router_cleanup", public=False)
        draft = {
            "module_artifacts": [{"module_id": "network"}, {"module_id": "session"}, {"module_id": "router"}],
            "function_contracts": [caller, missing_caller, session_api, private_router],
        }
        fallback = fallback_calls_allowed(draft, [caller, missing_caller], batch_index=0, batch_size=2)
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test",
            "producer": {"stage": "test", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [
                {
                    "caller_function_id": "fn:network:mqtt_network_cleanup_session",
                    "calls_allowed": [
                        {"callee_function_id": "fn:network:mqtt_network_cleanup_session", "call_kind": "cleanup", "required": True, "service_requirement_ids": [], "call_reason": "self should drop", "param_bindings": [], "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": "none"}, "failure_behavior": "ignore", "trace_ref_keys": [], "status": "inferred"},
                        {"callee_function_id": "fn:router:mqtt_router_cleanup", "call_kind": "cleanup_and_return", "required": True, "service_requirement_ids": [], "call_reason": "private cross module should drop", "param_bindings": [], "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": "none"}, "failure_behavior": "ignore", "trace_ref_keys": [], "status": "inferred"},
                        {"callee_function_id": "fn:session:mqtt_session_cleanup", "call_kind": "cleanup", "required": True, "service_requirement_ids": ["svc:network:session_cleanup", "svc:wrong"], "call_reason": "delegate cleanup", "param_bindings": [], "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": "none"}, "failure_behavior": "ignore", "trace_ref_keys": [], "status": "inferred"},
                    ],
                },
                {"caller_function_id": "fn:extra:outside", "calls_allowed": []},
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [
                {
                    "question_id": "q:test",
                    "target_kind": "function",
                    "target_id": "fn:network:mqtt_network_cleanup_session",
                    "question": "still useful context",
                    "unresolved_reason": "test uncertainty",
                    "blocking": True,
                    "trace_ref_keys": [],
                }
            ],
        }
        normalized, stats = normalize_calls_allowed_candidate(
            candidate,
            draft,
            {"fn:network:mqtt_network_cleanup_session", "fn:network:mqtt_network_bind_connection"},
            {"svc:network:session_cleanup", "svc:network:session_bind"},
            {"fn:session:mqtt_session_cleanup"},
            fallback,
        )
        self.assertEqual({item["caller_function_id"] for item in normalized["call_updates"]}, {"fn:network:mqtt_network_cleanup_session", "fn:network:mqtt_network_bind_connection"})
        cleanup_update = next(item for item in normalized["call_updates"] if item["caller_function_id"] == "fn:network:mqtt_network_cleanup_session")
        self.assertEqual(len(cleanup_update["calls_allowed"]), 1)
        edge = cleanup_update["calls_allowed"][0]
        self.assertEqual(edge["call_kind"], "error_handling")
        self.assertEqual(edge["return_binding"]["cleanup_function_id"], "")
        self.assertEqual(edge["service_requirement_ids"], ["svc:network:session_cleanup"])
        self.assertEqual(normalized["unresolved_service_requirements"], ["svc:network:session_bind"])
        merged = merge_calls_allowed(draft, normalized)
        self.assertEqual(merged["unresolved_questions"][0]["blocking"], False)
        self.assertGreater(stats["out_of_batch_call_updates_dropped"], 0)
        self.assertFalse(
            validate_calls_allowed_candidate(
                normalized,
                draft,
                {"architecture": {"modules": draft["module_artifacts"]}},
                expected_caller_ids={"fn:network:mqtt_network_cleanup_session", "fn:network:mqtt_network_bind_connection"},
                expected_service_requirement_ids={"svc:network:session_cleanup", "svc:network:session_bind"},
                callable_function_ids={"fn:session:mqtt_session_cleanup"},
            )
        )

    def _provider_seed_draft(self) -> tuple[dict, dict, dict, dict]:
        network = {
            "module_id": "network",
            "name": "network",
            "role": "TCP network epoll server",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_server_t", "kind": "TYPE", "role": "Public server boundary handle"},
                {"name": "mqtt_connection_t", "kind": "TYPE", "role": "Per-connection state with socket fd and recv buffer"},
                {"name": "mqtt_network_run", "kind": "FUNC", "role": "Run epoll server"},
            ],
            "state_owned": ["listening socket"],
            "owned_capabilities": ["transport_io"],
            "files": [],
            "doc_ref": [],
        }
        codec = {
            "module_id": "codec",
            "name": "codec",
            "role": "MQTT codec parser and serializer",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_parser_t", "kind": "TYPE", "role": "Public parser boundary"},
                {"name": "mqtt_packet_t", "kind": "TYPE", "role": "Decoded packet container"},
                {"name": "mqtt_decode", "kind": "FUNC", "role": "Decode packet bytes"},
                {"name": "mqtt_encode", "kind": "FUNC", "role": "Encode packet bytes"},
            ],
            "state_owned": [],
            "owned_capabilities": ["message_decode", "message_encode"],
            "files": [],
            "doc_ref": [],
        }
        session = {
            "module_id": "session",
            "name": "session",
            "role": "MQTT session state machine",
            "dependencies": ["codec", "network"],
            "artifacts": [{"name": "mqtt_session_t", "kind": "TYPE", "role": "Public session handle"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        return {"protocol_name": "mqtt", "module_artifacts": [network, codec, session], "canonical_types": [], "type_inventory": []}, network, codec, session

    def _minimal_mqtt_message_ir(self) -> dict:
        return {
            "protocol_facts": {
                "message_model": {
                    "message_or_command_entries": [
                        {
                            "name": "CONNECT",
                            "fields": [
                                {"name": "client_id", "fact_id": "fact:connect:client_id", "type": "utf8_string"},
                                {"name": "keep_alive", "fact_id": "fact:connect:keep_alive", "type": "uint16"},
                            ],
                        },
                        {
                            "name": "PUBLISH",
                            "fields": [
                                {"name": "topic_name", "fact_id": "fact:publish:topic_name", "type": "utf8_string"},
                                {"name": "payload", "fact_id": "fact:publish:payload", "type": "bytes"},
                            ],
                        },
                    ]
                }
            },
            "normalization_index": {
                "field_id_by_message_and_name": {
                    "CONNECT": {"client_id": "fact:connect:client_id", "keep_alive": "fact:connect:keep_alive"},
                    "PUBLISH": {"topic_name": "fact:publish:topic_name", "payload": "fact:publish:payload"},
                }
            },
        }

    def test_valid_stage_candidate_fixtures_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            self.assertFalse(validate_core_design_candidate(items["core"], planning_ir, profile, selected, constraints))
            self.assertFalse(validate_module_artifacts_candidate(items["modules"], selected, profile, constraints, merge_core_design(build_plan_skeleton(planning_ir, profile, constraints, selected), items["core"])))
            self.assertFalse(_has_error(validate_type_inventory_candidate(items["type_inventory"], draft["module_artifacts"], draft, profile, planning_ir)))
            self.assertFalse(_has_error(validate_function_inventory_candidate(items["inventory"], draft["module_artifacts"], draft, profile, planning_ir)))
            self.assertFalse(validate_function_signature_patch(items["signature"], draft))
            self.assertFalse(validate_function_behavior_contract_patch(items["behavior"], draft, constraints))
            self.assertFalse(validate_wire_access_binding_patch(items["wire"], draft, planning_ir))
            self.assertFalse(validate_calls_allowed_candidate(items["calls"], draft, selected))
            self.assertFalse(_has_error(validate_file_layout_candidate(items["layout"], draft)))
            self.assertFalse(validate_runtime_entrypoint_candidate(items["runtime"], draft))
            self.assertFalse(validate_dependency_repair_patch(items["repair"], draft))

    def test_module_artifact_role_classification_ignores_cross_module_responsibility_text(self) -> None:
        selected = {
            "architecture": {
                "modules": [
                    {"module_id": "broker_app", "name": "broker_app", "owned_capabilities": ["role_composition"], "responsibilities": ["Dispatch decoded messages to session handlers", "Initiate publish routing via topic_router"]},
                    {"module_id": "session", "name": "session", "owned_capabilities": ["session_state_ownership"], "responsibilities": ["Own parser state for incremental framing progress"]},
                ]
            }
        }
        candidate = {
            "schema_version": "module_artifacts_candidate/v1",
            "candidate_id": "candidate:test:module_artifact_roles",
            "producer": {"stage": "5.2b_module_artifacts", "prompt_name": "module_artifacts_candidate_prompt", "prompt_version": "test"},
            "modules": [
                {
                    "module_id": "session", "name": "session", "role": "session state lifecycle", "dependencies": [],
                    "artifacts": [{"name": "mqtt_session_t", "kind": "TYPE", "role": "session context"}, {"name": "mqtt_session_process", "kind": "FUNC", "role": "state transition handler"}],
                    "files": [], "doc_ref": [],
                },
                {
                    "module_id": "broker_app", "name": "broker_app", "role": "application composition and dispatch", "dependencies": ["session"],
                    "artifacts": [{"name": "mqtt_broker_run", "kind": "FUNC", "role": "main event loop"}], "files": [], "doc_ref": [],
                },
            ],
            "generation_order": ["session", "broker_app"],
            "consistency_rules": [],
            "forbidden_symbols": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_module_artifacts_candidate(candidate, selected, {}, {}, {})
        self.assertFalse(_has(diags, "codec_module_missing_decoder_encoder_artifacts"))
        self.assertFalse(_has(diags, "session_module_missing_type_artifact"))
        self.assertFalse(_has(diags, "router_module_missing_type_artifact"))
        self.assertFalse(_has(diags, "topic_module_missing_type_artifact"))

    def test_shape_rejects_extra_missing_and_wrong_enum(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            cases = [
                (items["core"], lambda c: validate_core_design_candidate(c, planning_ir, profile, selected, constraints), lambda c: c["canonical_types"][0].__setitem__("kind", "bad")),
                (items["modules"], lambda c: validate_module_artifacts_candidate(c, selected, profile, constraints, draft), lambda c: c["modules"][0]["artifacts"][0].__setitem__("kind", "bad")),
                (items["type_inventory"], lambda c: validate_type_inventory_candidate(c, draft["module_artifacts"], draft, profile, planning_ir), lambda c: c["types"][0].__setitem__("kind", "bad")),
                (items["inventory"], lambda c: validate_function_inventory_candidate(c, draft["module_artifacts"], draft, profile, planning_ir), lambda c: c["functions"][0].__setitem__("function_kind", "bad")),
                (items["signature"], lambda c: validate_function_signature_patch(c, draft), lambda c: c["function_signature_updates"][0]["signature"].__setitem__("storage_class", "bad")),
                (items["behavior"], lambda c: validate_function_behavior_contract_patch(c, draft, constraints), _break_first_state_access),
                (items["wire"], lambda c: validate_wire_access_binding_patch(c, draft, planning_ir), lambda c: c["wire_mapping_entries"][0].__setitem__("direction", "bad")),
                (items["calls"], lambda c: validate_calls_allowed_candidate(c, draft, selected), _break_first_call),
                (items["layout"], lambda c: validate_file_layout_candidate(c, draft), lambda c: c["files"][0].__setitem__("kind", "bad")),
                (items["runtime"], lambda c: validate_runtime_entrypoint_candidate(c, draft), lambda c: c["entrypoint_signature"].__setitem__("storage_class", "bad")),
                (items["repair"], lambda c: validate_dependency_repair_patch(c, draft), lambda c: c["repair_actions"][0].__setitem__("action_kind", "bad")),
            ]
            for valid, validator, break_enum in cases:
                extra = copy.deepcopy(valid)
                extra["unexpected"] = True
                self.assertTrue(_has(validator(extra), "forbidden_extra_field"))
                missing = copy.deepcopy(valid)
                missing.pop("producer")
                self.assertTrue(_has(validator(missing), "missing_required_field"))
                wrong_enum = copy.deepcopy(valid)
                break_enum(wrong_enum)
                self.assertTrue(_has(validator(wrong_enum), "invalid_enum_value"))

    def test_semantic_unknown_refs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            core = copy.deepcopy(items["core"])
            core["state_design"][0]["owner_module_id"] = "missing"
            self.assertTrue(_has(validate_core_design_candidate(core, planning_ir, profile, selected, constraints), "unknown_state_owner"))
            core = copy.deepcopy(items["core"])
            core["canonical_types"][0]["name"] = "connect"
            self.assertTrue(_has(validate_core_design_candidate(core, planning_ir, profile, selected, constraints), "forbidden_bare_canonical_type_name"))
            modules = copy.deepcopy(items["modules"])
            modules["modules"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_module_artifacts_candidate(modules, selected, profile, constraints, draft), "unknown_module_artifacts_module"))
            type_inventory = copy.deepcopy(items["type_inventory"])
            type_inventory["types"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_type_inventory_candidate(type_inventory, draft["module_artifacts"], draft, profile, planning_ir), "unknown_type_inventory_owner"))
            type_inventory = copy.deepcopy(items["type_inventory"])
            public_type = next(item for item in type_inventory["types"] if item.get("visibility") == "public" and item.get("defined_in") == "public_header")
            public_type["name"] = "publish"
            self.assertTrue(_has(validate_type_inventory_candidate(type_inventory, draft["module_artifacts"], draft, profile, planning_ir), "forbidden_bare_public_type_name"))
            inventory = copy.deepcopy(items["inventory"])
            inventory["functions"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir), "unknown_function_module"))
            signature = copy.deepcopy(items["signature"])
            signature["function_signature_updates"][0]["function_id"] = "fn:missing"
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "unknown_function_signature_target"))
            behavior = copy.deepcopy(items["behavior"])
            behavior["function_behavior_updates"][0]["function_id"] = "fn:missing"
            self.assertTrue(_has(validate_function_behavior_contract_patch(behavior, draft, constraints), "unknown_function_behavior_target"))
            wire = copy.deepcopy(items["wire"])
            wire["wire_mapping_entries"][0]["field_id"] = "field:missing"
            self.assertTrue(_has(validate_wire_access_binding_patch(wire, draft, planning_ir), "unknown_wire_field"))
            calls = copy.deepcopy(items["calls"])
            calls["call_updates"][0]["calls_allowed"].append({"callee_function_id": "fn:missing", "call_kind": "utility", "required": True, "service_requirement_ids": [], "call_reason": "bad", "param_bindings": [], "return_binding": {"policy": "ignore", "target_ref": "", "cleanup_function_id": ""}, "failure_behavior": "ignore", "trace_ref_keys": [], "status": "assumed"})
            self.assertTrue(_has(validate_calls_allowed_candidate(calls, draft, selected), "unknown_call_callee"))
            layout = copy.deepcopy(items["layout"])
            layout["function_file_assignments"][0]["function_id"] = "fn:missing"
            self.assertTrue(_has(validate_file_layout_candidate(layout, draft), "layout_assigns_unknown_function"))
            runtime = copy.deepcopy(items["runtime"])
            runtime["key_flow_module_id"] = "missing"
            self.assertTrue(_has(validate_runtime_entrypoint_candidate(runtime, draft), "runtime_entrypoint_unknown_key_module"))

    def test_runtime_entrypoint_rejects_handler_proxy_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            runtime = copy.deepcopy(items["runtime"])
            key_module = runtime["key_flow_module_id"]
            draft["function_contracts"].append(
                {
                    "function_id": f"fn:{key_module}:handle_proxy",
                    "name": "handle_connect_message",
                    "module_id": key_module,
                    "function_kind": "handler",
                    "visibility": "public",
                    "api_surface": "public",
                    "exported": True,
                    "public_api_role": "runtime_create",
                }
            )
            runtime["lifecycle_function_ids"]["create"] = f"fn:{key_module}:handle_proxy"
            self.assertTrue(_has(validate_runtime_entrypoint_candidate(runtime, draft), "runtime_entrypoint_lifecycle_not_api"))

    def test_runtime_entrypoint_fallback_ignores_non_runtime_lifecycle_names(self) -> None:
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [{"module_id": "broker_app", "owned_capabilities": ["role_composition"]}],
            "function_contracts": [
                {
                    "function_id": "fn:broker_app:mqtt_broker_create",
                    "name": "mqtt_broker_create",
                    "module_id": "broker_app",
                    "function_kind": "resource_lifecycle",
                    "visibility": "public",
                    "api_surface": "public",
                    "exported": True,
                    "public_api_role": "module_boundary_operation",
                }
            ],
        }
        runtime = fallback_runtime_entrypoint(draft)
        self.assertEqual(runtime["key_flow_module_id"], "broker_app")
        self.assertEqual(runtime["lifecycle_function_ids"]["create"], "fn:broker_app:create")
        self.assertFalse(validate_runtime_entrypoint_candidate(runtime, draft))

    def test_module_artifacts_candidate_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, _, _, items = self._fixtures(Path(raw_tmp))
            core_draft = merge_core_design(build_plan_skeleton(planning_ir, profile, constraints, selected), items["core"])
            artifacts = fallback_module_artifacts(core_draft, profile, constraints, selected)
            self.assertEqual(artifacts["schema_version"], "module_artifacts_candidate/v1")
            self.assertFalse(validate_module_artifacts_candidate(artifacts, selected, profile, constraints, core_draft))

            missing = copy.deepcopy(artifacts)
            missing["modules"] = missing["modules"][1:]
            self.assertTrue(_has(validate_module_artifacts_candidate(missing, selected, profile, constraints, core_draft), "missing_architecture_module_artifacts"))

            duplicate = copy.deepcopy(artifacts)
            duplicate["modules"][0]["artifacts"].append(copy.deepcopy(duplicate["modules"][0]["artifacts"][0]))
            self.assertTrue(_has(validate_module_artifacts_candidate(duplicate, selected, profile, constraints, core_draft), "duplicate_module_artifact_name"))

            bad_dep = copy.deepcopy(artifacts)
            bad_dep["modules"][0]["dependencies"] = ["missing"]
            self.assertTrue(_has(validate_module_artifacts_candidate(bad_dep, selected, profile, constraints, core_draft), "unknown_module_artifact_dependency"))

            bad_name = copy.deepcopy(artifacts)
            bad_name["modules"][0]["artifacts"][0]["name"] = "read"
            self.assertTrue(_has(validate_module_artifacts_candidate(bad_name, selected, profile, constraints, core_draft), "forbidden_bare_module_artifact_name"))

    def test_function_inventory_is_seeded_by_module_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, _, _, items = self._fixtures(Path(raw_tmp))
            core_draft = merge_core_design(build_plan_skeleton(planning_ir, profile, constraints, selected), items["core"])
            artifact_candidate = fallback_module_artifacts(core_draft, profile, constraints, selected)
            draft = merge_module_artifacts(core_draft, artifact_candidate)
            module = next(item for item in draft["module_artifacts"] if item["artifacts"])
            inventory = current_function_inventory_candidate(draft, module)
            func_artifacts = {item["name"] for item in module["artifacts"] if item["kind"] == "FUNC"}
            inventory_names = {item["name"] for item in inventory["functions"]}
            self.assertTrue(func_artifacts.issubset(inventory_names))

            missing = copy.deepcopy(inventory)
            missing["functions"] = [item for item in missing["functions"] if item["name"] != next(iter(func_artifacts))]
            self.assertTrue(_has(validate_function_inventory_candidate(missing, draft["module_artifacts"], draft, profile, planning_ir), "function_inventory_missing_artifact_function"))

    def test_function_inventory_accepts_surface_catalog_messages_but_rejects_non_wire_field_ids(self) -> None:
        module = {
            "module_id": "broker_app",
            "name": "broker app",
            "role": "broker orchestration",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_handle_connack", "kind": "FUNC", "role": "Handle CONNACK"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        planning_ir = {
            "protocol_facts": {
                "message_model": {
                    "surface_catalog": [{"name": "connack"}, {"name": "suback"}, {"name": "pingresp"}],
                    "message_or_command_entries": [{"name": "connect", "fields": [{"name": "client_id", "fact_id": "field:wire:client_id"}]}],
                }
            }
        }
        function = _inventory_function("mqtt_handle_connack", "broker_app")
        function["covers_message_ids"] = ["message:connack", "message:suback", "message:pingresp"]
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:broker_app:surface_messages",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": "broker_app",
            "functions": [function],
            "assumptions": [],
            "unresolved_questions": [],
        }
        draft = {"module_artifacts": [module], "type_inventory": [], "function_contracts": []}
        self.assertFalse(_has_error(validate_function_inventory_candidate(candidate, [module], draft, {}, planning_ir)))

        bad = copy.deepcopy(candidate)
        bad["functions"][0]["covers_field_ids"] = ["field:mqtt_connection_t:fd"]
        self.assertTrue(_has(validate_function_inventory_candidate(bad, [module], draft, {}, planning_ir), "unknown_function_field_ref"))

    def test_type_inventory_is_seeded_by_module_artifacts(self) -> None:
        module = {
            "module_id": "mqtt_codec",
            "name": "mqtt_codec",
            "role": "MQTT codec and packet framing",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_packet", "kind": "TYPE", "role": "Decoded packet container"},
                {"name": "mqtt_packet_free", "kind": "FUNC", "role": "Free decoded packet"},
            ],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "canonical_types": [], "resource_lifecycle": []}
        candidate = current_type_inventory_candidate(draft, module)
        type_names = {item["name"] for item in candidate["types"]}
        self.assertIn("mqtt_packet", type_names)
        self.assertFalse(_has_error(validate_type_inventory_candidate(candidate, [module], draft)))

        missing = copy.deepcopy(candidate)
        missing["types"] = [item for item in missing["types"] if item["name"] != "mqtt_packet"]
        self.assertTrue(_has(validate_type_inventory_candidate(missing, [module], draft), "type_inventory_missing_artifact_type"))

    def test_type_inventory_context_exposes_protocol_type_generation_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            context = build_type_inventory_context(draft, codec, planning_ir)
            targets = context["type_generation_targets"]
            kinds = {target["target_kind"] for target in targets}
            self.assertIn("packet_enum", kinds)
            self.assertIn("payload_struct", kinds)
            self.assertIn("packet_container_struct", kinds)
            self.assertIn("global_module_artifacts_reference", context)
            self.assertTrue(any(target["required_fields"] for target in targets if target["target_kind"] == "payload_struct"))
            enum_target = next(target for target in targets if target["target_kind"] == "packet_enum")
            enum_names = {field["field_name"] for field in enum_target["required_fields"]}
            self.assertTrue({"RESERVED", "CONNECT", "CONNACK", "PUBLISH", "SUBSCRIBE", "SUBACK", "PINGREQ", "PINGRESP", "DISCONNECT"}.issubset(enum_names))
            connect_target = next(target for target in targets if target["suggested_name"] == "mqtt_connect_payload_t")
            connect_fields = {field["field_name"]: field["field_type"] for field in connect_target["required_fields"]}
            self.assertEqual(connect_fields["client_id"], "char*")
            self.assertEqual(connect_fields["keep_alive"], "uint16_t")
            self.assertEqual(connect_fields["clean_session"], "bool")
            publish_target = next(target for target in targets if target["suggested_name"] == "mqtt_publish_payload_t")
            publish_fields = {field["field_name"]: field["field_type"] for field in publish_target["required_fields"]}
            self.assertEqual(publish_fields["payload"], "uint8_t*")
            self.assertEqual(publish_fields["payload_len"], "size_t")
            packet_target = next(target for target in targets if target["target_kind"] == "packet_container_struct")
            self.assertTrue(any(field.get("field_type") == "union" and field.get("variants") for field in packet_target["required_fields"]))
            fixed_target = next(target for target in targets if target["suggested_name"] == "mqtt_fixed_header_payload_t")
            fixed_fields = {field["field_name"]: field["field_type"] for field in fixed_target["required_fields"]}
            self.assertEqual(fixed_fields["packet_type"], "mqtt_packet_type_t")
            self.assertEqual(fixed_fields["remaining_length"], "uint32_t")

    def test_codec_payload_and_packet_type_duplicates_keep_derived_kind(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, _, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            codec = copy.deepcopy(codec)
            codec["artifacts"].extend(
                [
                    {"name": "mqtt_packet_type_t", "kind": "TYPE", "role": "Decoded packet discriminator"},
                    {"name": "mqtt_connect_payload_t", "kind": "TYPE", "role": "CONNECT payload"},
                    {"name": "mqtt_publish_payload_t", "kind": "TYPE", "role": "PUBLISH payload"},
                    {"name": "mqtt_subscribe_payload_t", "kind": "TYPE", "role": "SUBSCRIBE payload"},
                ]
            )
            candidate = current_type_inventory_candidate(draft, codec, planning_ir, profile, constraints)
            by_name = {item["name"]: item for item in candidate["types"]}
            self.assertEqual(by_name["mqtt_packet_type_t"]["kind"], "enum")
            self.assertEqual(by_name["mqtt_connect_payload_t"]["kind"], "struct")
            self.assertEqual(by_name["mqtt_publish_payload_t"]["kind"], "struct")
            self.assertEqual(by_name["mqtt_subscribe_payload_t"]["kind"], "struct")
            diags = validate_type_inventory_candidate(candidate, draft["module_artifacts"], draft, profile, planning_ir)
            self.assertFalse(_has(diags, "missing_packet_enum_type"))
            self.assertFalse(_has(diags, "missing_payload_struct_type"))
            self.assertFalse(_has(diags, "buffer_type_missing_size_fields"))

    def test_type_generation_targets_do_not_create_private_state_for_stateless_codec_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, _, _, _ = self._fixtures(Path(raw_tmp))
            draft = {"protocol_name": "mqtt", "module_artifacts": [], "canonical_types": []}
            codec = {
                "module_id": "codec",
                "name": "codec",
                "role": "Stateless incremental decode/encode of protocol units",
                "dependencies": [],
                "artifacts": [],
                "state_owned": [],
                "owned_capabilities": ["message_decode", "message_encode", "incremental_message_framing", "canonical_type_ownership"],
                "files": [],
                "doc_ref": [],
            }
            target_kinds = {target["target_kind"] for target in derive_type_generation_targets(draft, codec, planning_ir)}
            self.assertNotIn("internal_state", target_kinds)
            self.assertIn("packet_enum", target_kinds)
            self.assertIn("payload_struct", target_kinds)

            network = {
                **codec,
                "module_id": "network",
                "name": "network",
                "role": "TCP transport runtime connection lifecycle",
                "state_owned": ["socket descriptors", "per-connection I/O buffers"],
                "owned_capabilities": ["transport_io", "connection_lifecycle"],
            }
            self.assertIn("internal_state", {target["target_kind"] for target in derive_type_generation_targets(draft, network, planning_ir)})

    def test_type_inventory_context_exposes_provider_public_seed_types_only_for_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, _, _, _ = self._fixtures(Path(raw_tmp))
            draft, network, _, session = self._provider_seed_draft()
            context = build_type_inventory_context(draft, session, planning_ir)
            provider_types = {group["module_id"]: {item["name"] for item in group["types"]} for group in context["provider_public_types"]}
            self.assertIn("mqtt_packet_t", provider_types["codec"])
            self.assertIn("mqtt_connection_t", provider_types["network"])
            self.assertIn("type:codec:mqtt_packet_t", context["legal_id_universe"]["type_ids"])
            self.assertIn("type:mqtt_packet_t", context["legal_id_universe"]["type_ids"])
            self.assertIn("type:network:mqtt_connection_t", context["legal_id_universe"]["type_ids"])

            network_context = build_type_inventory_context(draft, network, planning_ir)
            network_provider_types = {item["name"] for group in network_context["provider_public_types"] for item in group["types"]}
            self.assertNotIn("mqtt_parser_t", network_provider_types)

    def test_type_inventory_validator_accepts_provider_seed_alias_but_rejects_internal_provider_type(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, _, _, _ = self._fixtures(Path(raw_tmp))
            draft, _, _, session = self._provider_seed_draft()
            candidate = {
                "schema_version": "type_inventory_candidate/v1",
                "candidate_id": "candidate:session:provider_seed",
                "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
                "module_id": "session",
                "types": [
                    {
                        "type_id": "type:session:mqtt_session_t",
                        "name": "mqtt_session_t",
                        "module_id": "session",
                        "kind": "opaque_handle",
                        "visibility": "public",
                        "defined_in": "public_header",
                        "purpose": "session handle",
                        "fields": [],
                        "enum_values": [],
                        "callback_signature": None,
                        "ownership_lifetime": "",
                        "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                        "related_functions": [],
                        "dependencies": ["type:mqtt_packet_t"],
                        "trace_ref_keys": [],
                        "status": "inferred",
                    }
                ],
                "assumptions": [],
                "unresolved_questions": [],
            }
            self.assertFalse(_has_error(validate_type_inventory_candidate(normalize_type_inventory_candidate(candidate), draft["module_artifacts"], draft, planning_ir=planning_ir)))

            bad = copy.deepcopy(candidate)
            bad["types"][0]["dependencies"] = ["type:network:mqtt_socket_state_t"]
            self.assertTrue(_has(validate_type_inventory_candidate(normalize_type_inventory_candidate(bad), draft["module_artifacts"], draft, planning_ir=planning_ir), "unknown_type_ref"))

    def test_type_inventory_validator_resolves_provider_canonical_public_type(self) -> None:
        codec = {
            "module_id": "codec",
            "name": "codec",
            "role": "MQTT codec parser and serializer",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_fixed_header_t", "kind": "TYPE", "role": "Public MQTT fixed header"}],
            "state_owned": [],
            "owned_capabilities": ["message_decode"],
            "files": [],
            "doc_ref": [],
        }
        broker = {
            "module_id": "broker_app",
            "name": "broker_app",
            "role": "MQTT broker application dispatch",
            "dependencies": ["codec"],
            "artifacts": [{"name": "mqtt_broker_app_dispatch_context_t", "kind": "TYPE", "role": "Internal dispatch context"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        fixed_header = {
            "type_id": "mqtt_fixed_header_t",
            "name": "mqtt_fixed_header_t",
            "kind": "struct",
            "owner_module_id": "codec",
        }
        candidate = {
            "schema_version": "type_inventory_candidate/v1",
            "candidate_id": "candidate:broker_app:type_inventory",
            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
            "module_id": "broker_app",
            "types": [
                {
                    "type_id": "type:broker_app:mqtt_broker_app_dispatch_context_t",
                    "name": "mqtt_broker_app_dispatch_context_t",
                    "module_id": "broker_app",
                    "kind": "view_struct",
                    "visibility": "module_internal",
                    "defined_in": "internal_header",
                    "purpose": "broker dispatch context",
                    "fields": [
                        {
                            "field_name": "fixed_header",
                            "field_type": "mqtt_fixed_header_t",
                            "type_ref": "mqtt_fixed_header_t",
                            "required": True,
                            "ownership": "BORROWED",
                            "lifetime": "dispatch",
                            "length_field": "",
                            "capacity_field": "",
                            "validation_notes": "",
                        }
                    ],
                    "enum_values": [],
                    "callback_signature": None,
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": ["mqtt_fixed_header_t"],
                    "trace_ref_keys": ["fact:mqtt:fixed_header"],
                    "status": "inferred",
                }
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        draft = {"module_artifacts": [codec, broker], "canonical_types": [fixed_header], "type_inventory": []}
        normalized = normalize_type_inventory_candidate(candidate)
        diagnostics = validate_type_inventory_candidate(normalized, draft["module_artifacts"], draft)
        self.assertFalse(_has_error(diagnostics), [diag.__dict__ for diag in diagnostics])

        blocked_broker = copy.deepcopy(broker)
        blocked_broker["dependencies"] = []
        blocked_draft = {"module_artifacts": [codec, blocked_broker], "canonical_types": [fixed_header], "type_inventory": []}
        self.assertTrue(_has(validate_type_inventory_candidate(normalized, blocked_draft["module_artifacts"], blocked_draft), "unknown_type_ref"))

    def test_type_inventory_validator_keeps_public_private_boundary_and_normalizes_union_ref(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "topic router",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_router_t", "kind": "TYPE", "role": "Public router handle"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        candidate = {
            "schema_version": "type_inventory_candidate/v1",
            "candidate_id": "candidate:router:union",
            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
            "module_id": "router",
            "types": [
                {
                    "type_id": "type:router:mqtt_router_t",
                    "name": "mqtt_router_t",
                    "module_id": "router",
                    "kind": "struct",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "purpose": "router handle",
                    "fields": [{"field_name": "v", "field_type": "union", "type_ref": "union", "required": True, "ownership": "BORROWED", "lifetime": "valid while router is valid", "length_field": "", "capacity_field": "", "validation_notes": ""}],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        normalized = normalize_type_inventory_candidate(candidate)
        self.assertEqual(normalized["types"][0]["fields"][0]["type_ref"], "")
        self.assertFalse(_has_error(validate_type_inventory_candidate(normalized, [module], {"module_artifacts": [module]})))

        leaked = copy.deepcopy(candidate)
        leaked["types"].append(
            {
                **copy.deepcopy(candidate["types"][0]),
                "type_id": "type:router:mqtt_router_private_t",
                "name": "mqtt_router_private_t",
                "kind": "internal_state",
                "visibility": "private",
                "defined_in": "source_file",
                "fields": [],
            }
        )
        leaked["types"][0]["fields"][0].update({"field_type": "mqtt_router_private_t*", "type_ref": "type:router:mqtt_router_private_t"})
        self.assertTrue(_has(validate_type_inventory_candidate(normalize_type_inventory_candidate(leaked), [module], {"module_artifacts": [module]}), "public_type_field_uses_private_type"))

    def test_type_generation_targets_do_not_create_callback_boundary_from_connection_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, _, _, _ = self._fixtures(Path(raw_tmp))
            draft, network, codec, session = self._provider_seed_draft()
            codec["role"] = "MQTT codec for bytes read from a connection"
            session["role"] = "MQTT session connection state"
            self.assertFalse(any(target["target_kind"] == "callback_or_event_boundary" for target in derive_type_generation_targets(draft, codec, planning_ir)))
            self.assertFalse(any(target["target_kind"] == "callback_or_event_boundary" for target in derive_type_generation_targets(draft, session, planning_ir)))
            self.assertTrue(any(target["target_kind"] == "callback_or_event_boundary" for target in derive_type_generation_targets(draft, network, planning_ir)))

    def test_type_inventory_normalizes_callback_collection_and_private_callback_param_refs(self) -> None:
        module = {
            "module_id": "network",
            "name": "network",
            "role": "TCP server accept data close timer epoll runtime",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_connection_t", "kind": "TYPE", "role": "per-connection context with socket fd and buffers"}, {"name": "mqtt_server_t", "kind": "TYPE", "role": "server context"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        connection = next(item for item in candidate["types"] if item["name"] == "mqtt_connection_t")
        connection.update({"kind": "internal_state", "visibility": "module_internal", "defined_in": "source_file", "fields": []})
        callbacks = next(item for item in candidate["types"] if item["name"] == "mqtt_network_callbacks_t")
        callbacks.update(
            {
                "kind": "callback_type",
                "fields": [],
                "callback_signature": {
                    "return_type": "void",
                    "params": [
                        {"name": "on_accept", "type": "mqtt_network_on_accept_fn", "type_ref": "type:network:mqtt_network_on_accept_fn", "ownership": "BORROWED"},
                        {"name": "on_data", "type": "mqtt_network_on_data_fn", "type_ref": "type:network:mqtt_network_on_data_fn", "ownership": "BORROWED"},
                        {"name": "on_close", "type": "mqtt_network_on_close_fn", "type_ref": "type:network:mqtt_network_on_close_fn", "ownership": "BORROWED"},
                        {"name": "on_timer", "type": "mqtt_network_on_timer_fn", "type_ref": "type:network:mqtt_network_on_timer_fn", "ownership": "BORROWED"},
                    ],
                },
            }
        )
        for callback_type in [item for item in candidate["types"] if str(item["name"]).endswith(("_on_accept_fn", "_on_data_fn", "_on_close_fn"))]:
            callback_type["dependencies"] = ["type:network:mqtt_connection_t"]
            callback_type["callback_signature"]["params"] = [{"name": "conn", "type": "mqtt_connection_t*", "type_ref": "type:network:mqtt_connection_t", "ownership": "BORROWED"}]
        normalized = normalize_type_inventory_candidate(candidate)
        normalized_callbacks = next(item for item in normalized["types"] if item["name"] == "mqtt_network_callbacks_t")
        self.assertEqual(normalized_callbacks["kind"], "event_struct")
        self.assertTrue({"on_accept", "on_data", "on_close", "on_timer"}.issubset({field["field_name"] for field in normalized_callbacks["fields"]}))
        normalized_accept = next(item for item in normalized["types"] if item["name"] == "mqtt_network_on_accept_fn")
        self.assertEqual(normalized_accept["callback_signature"]["params"][0]["type"], "void*")
        self.assertEqual(normalized_accept["callback_signature"]["params"][0]["type_ref"], "void")
        self.assertFalse(_has_error(validate_type_inventory_candidate(normalized, [module], {"protocol_name": "mqtt", "module_artifacts": [module]})))

    def test_type_inventory_normalizes_private_impl_detail_cross_module_refs(self) -> None:
        network = {
            "module_id": "network",
            "name": "network",
            "role": "TCP network runtime",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_connection_t", "kind": "TYPE", "role": "per-connection context with socket fd and buffers"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        network_candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [network]}, network)
        connection = next(item for item in network_candidate["types"] if item["name"] == "mqtt_connection_t")
        connection.update(
            {
                "kind": "internal_state",
                "visibility": "module_internal",
                "defined_in": "source_file",
                "dependencies": ["type:mqtt_parser_t"],
                "fields": [{"field_name": "parser", "field_type": "mqtt_parser_t", "type_ref": "type:mqtt_parser_t", "required": True, "ownership": "OWNED", "lifetime": "connection duration", "length_field": "", "capacity_field": "", "validation_notes": ""}],
            }
        )
        normalized_network = normalize_type_inventory_candidate(network_candidate)
        normalized_connection = next(item for item in normalized_network["types"] if item["name"] == "mqtt_connection_t")
        self.assertEqual(normalized_connection["dependencies"], [])
        self.assertEqual(normalized_connection["fields"][0]["field_type"], "void*")
        self.assertFalse(_has_error(validate_type_inventory_candidate(normalized_network, [network], {"module_artifacts": [network]})))

        session = {
            "module_id": "session",
            "name": "session",
            "role": "session state machine",
            "dependencies": ["network"],
            "artifacts": [{"name": "mqtt_session_t", "kind": "TYPE", "role": "session state"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        session_candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [network, session]}, session)
        session_type = next(item for item in session_candidate["types"] if item["name"] == "mqtt_session_t")
        session_type.update(
            {
                "kind": "internal_state",
                "visibility": "module_internal",
                "defined_in": "source_file",
                "dependencies": ["type:network:mqtt_connection_t"],
                "fields": [{"field_name": "connection", "field_type": "mqtt_connection_t*", "type_ref": "type:network:mqtt_connection_t", "required": True, "ownership": "BORROWED", "lifetime": "session duration", "length_field": "", "capacity_field": "", "validation_notes": ""}],
            }
        )
        normalized_session = normalize_type_inventory_candidate(session_candidate)
        normalized_session_type = next(item for item in normalized_session["types"] if item["name"] == "mqtt_session_t")
        self.assertEqual(normalized_session_type["dependencies"], [])
        self.assertEqual(normalized_session_type["fields"][0]["type_ref"], "void")
        self.assertFalse(_has_error(validate_type_inventory_candidate(normalized_session, [network, session], {"module_artifacts": [network, session]})))

        router = {
            "module_id": "router",
            "name": "router",
            "role": "topic routing and subscription registry",
            "dependencies": ["session"],
            "artifacts": [{"name": "mqtt_router_t", "kind": "TYPE", "role": "router handle"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        router_candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [session, router]}, router)
        router_state = next(item for item in router_candidate["types"] if item["name"] == "mqtt_router_t")
        router_state.update(
            {
                "kind": "internal_state",
                "visibility": "private",
                "defined_in": "source_file",
                "dependencies": ["type:session:mqtt_session_t"],
                "fields": [],
            }
        )
        normalized_router = normalize_type_inventory_candidate(router_candidate)
        normalized_router_state = next(item for item in normalized_router["types"] if item["name"] == "mqtt_router_t")
        self.assertEqual(normalized_router_state["dependencies"], [])

    def test_type_inventory_normalizes_public_packet_payload_visibility_without_hiding_other_private_leaks(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "MQTT codec",
            "dependencies": [],
            "artifacts": [],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        base = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        base["types"] = [
            {
                **copy.deepcopy(base["types"][0]),
                "type_id": "type:codec:mqtt_packet_t",
                "name": "mqtt_packet_t",
                "kind": "struct",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "public packet container with variant payload",
                "dependencies": ["type:codec:mqtt_connect_payload_t"],
                "fields": [{"field_name": "v", "field_type": "union", "type_ref": "union", "required": True, "ownership": "BORROWED", "lifetime": "valid", "length_field": "", "capacity_field": "", "validation_notes": ""}],
            },
            {
                **copy.deepcopy(base["types"][0]),
                "type_id": "type:codec:mqtt_connect_payload_t",
                "name": "mqtt_connect_payload_t",
                "kind": "struct",
                "visibility": "module_internal",
                "defined_in": "source_file",
                "purpose": "CONNECT payload",
                "dependencies": [],
                "fields": [],
            },
        ]
        normalized = normalize_type_inventory_candidate(base)
        payload = next(item for item in normalized["types"] if item["name"] == "mqtt_connect_payload_t")
        self.assertEqual(payload["visibility"], "public")
        self.assertFalse(_has_error(validate_type_inventory_candidate(normalized, [module], {"module_artifacts": [module]})))

        leaked = copy.deepcopy(base)
        leaked["types"][1].update({"name": "mqtt_codec_private_state_t", "type_id": "type:codec:mqtt_codec_private_state_t", "purpose": "private helper state"})
        leaked["types"][0]["dependencies"] = ["type:codec:mqtt_codec_private_state_t"]
        self.assertTrue(_has(validate_type_inventory_candidate(normalize_type_inventory_candidate(leaked), [module], {"module_artifacts": [module]}), "public_type_field_uses_private_type"))

    def test_fallback_keeps_connection_and_server_handles_public(self) -> None:
        module = {
            "module_id": "io",
            "name": "io",
            "role": "TCP network runtime",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_connection_t", "kind": "TYPE", "role": "per-connection context with socket fd and buffers"},
                {"name": "mqtt_server_t", "kind": "TYPE", "role": "server context with event loop state"},
            ],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        connection = next(item for item in candidate["types"] if item["name"] == "mqtt_connection_t")
        server = next(item for item in candidate["types"] if item["name"] == "mqtt_server_t")
        self.assertEqual((connection["kind"], connection["visibility"], connection["defined_in"]), ("opaque_handle", "public", "public_header"))
        self.assertEqual((server["visibility"], server["defined_in"]), ("public", "public_header"))

    def test_type_reconciler_sanitizes_callback_param_and_dependency_refs(self) -> None:
        module = {
            "module_id": "unit",
            "name": "unit",
            "role": "unit test module",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_connection_t", "kind": "TYPE", "role": "private connection implementation"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        space = build_type_planning_space({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        private_slot = next(slot for slot in space["mandatory_type_slots"] if slot["name"] == "mqtt_connection_t")
        private_slot.update({"kind": "internal_state", "visibility": "module_internal", "defined_in": "source_file"})
        callback_slot = {
            "slot_id": "slot:type:unit:derived:mqtt_unit_on_accept_fn",
            "slot_class": "derived",
            "source_kind": "test",
            "source_id": "test",
            "source_refs": [],
            "type_id": "type:unit:mqtt_unit_on_accept_fn",
            "name": "mqtt_unit_on_accept_fn",
            "module_id": "unit",
            "kind": "callback_type",
            "visibility": "public",
            "defined_in": "public_header",
            "source_reason": "public callback",
            "required_fields": [],
            "trace_ref_keys": [],
            "default_include": True,
        }
        space["derived_type_slots"] = [callback_slot]
        space["allowed_type_refs"]["local_slots"] = [private_slot, callback_slot]
        result = reconcile_type_filling_candidate(
            space,
            {
                "schema_version": "type_filling_candidate/v1",
                "module_id": "unit",
                "slot_fillings": [
                    {
                        "slot_id": callback_slot["slot_id"],
                        "callback_signature": {"return_type": "void", "params": [{"name": "conn", "type": "mqtt_connection_t*", "type_ref": "", "ownership": "BORROWED"}]},
                        "dependencies": ["type:unit:mqtt_connection_t"],
                    }
                ],
                "optional_type_proposals": [],
            },
        )
        callback = next(item for item in result["candidate"]["types"] if item["name"] == "mqtt_unit_on_accept_fn")
        self.assertEqual(callback["callback_signature"]["params"][0]["type_ref"], "void")
        self.assertEqual(callback["dependencies"], [])
        self.assertFalse(_has_error(validate_type_inventory_candidate(result["candidate"], [module], {"module_artifacts": [module]})))

    def test_type_inventory_accepts_system_namespace_and_opaque_backing_pair(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "topic routing",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_router_t", "kind": "TYPE", "role": "router handle"}],
            "state_owned": ["topic tree"],
            "owned_capabilities": ["resource_ownership"],
            "files": [],
            "doc_ref": [],
        }
        candidate = {
            "schema_version": "type_inventory_candidate/v1",
            "candidate_id": "candidate:router:type_inventory",
            "producer": {"stage": "5.3_type_data", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "test"},
            "module_id": "router",
            "types": [
                {
                    "type_id": "type:router:mqtt_router_t",
                    "name": "mqtt_router_t",
                    "module_id": "router",
                    "kind": "opaque_handle",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "purpose": "public router handle",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                },
                {
                    "type_id": "type:router:struct_mqtt_router",
                    "name": "struct mqtt_router",
                    "module_id": "router",
                    "kind": "internal_state",
                    "visibility": "private",
                    "defined_in": "source_file",
                    "purpose": "owned private router state",
                    "fields": [
                        {
                            "field_name": "topic_tree",
                            "field_type": "void*",
                            "type_ref": "system:void",
                            "required": True,
                            "ownership": "OWNED",
                            "lifetime": "until router destroy",
                            "length_field": "",
                            "capacity_field": "",
                            "validation_notes": "",
                        }
                    ],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "owned private state",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": ["mqtt_router_destroy"], "freed_by": []},
                    "related_functions": ["mqtt_router_destroy"],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                },
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertFalse(_has_error(validate_type_inventory_candidate(candidate, [module], {"module_artifacts": [module]})))
        normalized = normalize_type_inventory_candidate(candidate)
        self.assertEqual(normalized["types"][1]["fields"][0]["type_ref"], "void")
        merged = merge_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, candidate)
        merged_state = next(item for item in merged["type_inventory"] if item["name"] == "struct mqtt_router")
        self.assertEqual(merged_state["fields"][0]["type_ref"], "void")

    def test_type_inventory_rejects_non_opaque_duplicate_names(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "topic routing",
            "dependencies": [],
            "artifacts": [],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        base = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        first = base["types"][0]
        first.update({"name": "struct mqtt_router", "kind": "struct", "visibility": "private", "defined_in": "source_file"})
        second = copy.deepcopy(first)
        second["type_id"] = "type:router:duplicate_router"
        second["name"] = "mqtt_router_t"
        second["kind"] = "struct"
        base["types"] = [first, second]
        self.assertTrue(_has(validate_type_inventory_candidate(base, [module], {"module_artifacts": [module]}), "duplicate_type_inventory_name"))

    def test_merge_type_inventory_skips_existing_normalized_type_name(self) -> None:
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [{"module_id": "session", "artifacts": []}],
            "type_inventory": [
                {
                    "type_id": "type:session:mqtt_session_t",
                    "name": "mqtt_session_t",
                    "module_id": "session",
                    "kind": "opaque_handle",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "canonical_types": [],
        }
        candidate = {
            "schema_version": "type_inventory_candidate/v1",
            "candidate_id": "candidate:session",
            "producer": {"stage": "planning", "prompt_name": "type_filling_candidate_prompt", "prompt_version": "planning/prompts/v1"},
            "module_id": "session",
            "types": [
                {
                    "type_id": "type:session:mqtt_session",
                    "name": "mqtt_session",
                    "module_id": "session",
                    "kind": "struct",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "purpose": "Duplicate alias for mqtt_session_t.",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        merged = merge_type_inventory(draft, candidate)
        self.assertEqual([item["type_id"] for item in merged["type_inventory"]], ["type:session:mqtt_session_t"])

    def test_merge_function_signatures_normalizes_type_aliases_and_struct_declarations(self) -> None:
        function = _inventory_function("mqtt_broker_app_handle_packet", "broker_app", function_id="fn:broker_app:mqtt_broker_app_handle_packet", public=True)
        draft = {
            "module_artifacts": [{"module_id": "codec"}, {"module_id": "broker_app"}],
            "type_inventory": [
                {
                    "type_id": "type:codec:mqtt_packet_t",
                    "name": "mqtt_packet_t",
                    "module_id": "codec",
                    "kind": "struct",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "canonical_types": [],
            "function_contracts": [function],
        }
        patch = {
            "function_signature_updates": [
                {
                    "function_id": "fn:broker_app:mqtt_broker_app_handle_packet",
                    "signature": {
                        "raw": "int mqtt_broker_app_handle_packet(const struct mqtt_packet* packet)",
                        "name": "mqtt_broker_app_handle_packet",
                        "storage_class": "none",
                        "return_type": "int",
                        "params": [{"name": "packet", "type": "const struct mqtt_packet*", "type_ref": "", "direction": "in", "nullable": False, "ownership": "BORROWED", "passing_mode": "by_pointer"}],
                    },
                    "signature_dependencies": [{"symbol_name": "struct mqtt_packet", "symbol_kind": "type", "type_ref": "", "owner_module_id": "codec", "dependency_scope": "header", "reason": "decoded packet"}],
                    "interface_type_declarations": [{"name": "struct mqtt_broker_config", "kind": "type", "owner_module_id": "broker_app", "visibility": "public", "reason": "config"}],
                }
            ]
        }
        merged = merge_function_signatures(draft, patch)
        merged_function = merged["function_contracts"][0]
        self.assertEqual(merged_function["signature"]["params"][0]["type_ref"], "type:codec:mqtt_packet_t")
        self.assertEqual(merged_function["signature_dependencies"][0]["type_ref"], "type:codec:mqtt_packet_t")
        self.assertEqual(merged_function["interface_type_declarations"][0]["name"], "mqtt_broker_config")

    def test_codec_type_inventory_requires_protocol_packet_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            candidate = current_type_inventory_candidate(draft, codec, planning_ir)
            self.assertFalse(_has_error(validate_type_inventory_candidate(candidate, draft["module_artifacts"], draft, profile, planning_ir)))

            opaque_only = copy.deepcopy(candidate)
            opaque_only["types"] = [
                type_item
                for type_item in opaque_only["types"]
                if type_item["name"] in {artifact["name"] for artifact in codec["artifacts"] if artifact["kind"] == "TYPE"}
            ]
            for type_item in opaque_only["types"]:
                type_item["kind"] = "opaque_handle"
                type_item["fields"] = []
                type_item["enum_values"] = []
            diags = validate_type_inventory_candidate(opaque_only, draft["module_artifacts"], draft, profile, planning_ir)
            self.assertTrue(_has(diags, "missing_packet_enum_type"))
            self.assertTrue(_has(diags, "missing_payload_struct_type"))
            self.assertTrue(_has(diags, "missing_packet_container_type"))

    def test_type_inventory_packet_container_accepts_optional_variants(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            candidate = current_type_inventory_candidate(draft, codec, planning_ir)
            packet = next(item for item in candidate["types"] if item["name"] == "mqtt_packet_t")
            variant_field = next(field for field in packet["fields"] if field["field_name"] == "v")
            variant_field["field_type"] = "mqtt_packet_payload_u"
            variant_field["type_ref"] = ""
            variant_field["validation_notes"] = ""
            variant_field["variants"] = [
                {"field_name": "connect", "field_type": "mqtt_connect_payload_t", "source_field_id": ""},
                {"field_name": "publish", "field_type": "mqtt_publish_payload_t", "source_field_id": ""},
            ]

            self.assertFalse(_has_error(validate_type_inventory_candidate(candidate, draft["module_artifacts"], draft, profile, planning_ir)))

            without_variants = copy.deepcopy(candidate)
            packet_without_variants = next(item for item in without_variants["types"] if item["name"] == "mqtt_packet_t")
            packet_without_variants["fields"] = [field for field in packet_without_variants["fields"] if field["field_name"] == "type"]
            diags = validate_type_inventory_candidate(without_variants, draft["module_artifacts"], draft, profile, planning_ir)
            self.assertTrue(_has(diags, "missing_packet_container_type"))

    def test_type_reconciler_preserves_packet_container_variants_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            space = build_type_planning_space(draft, codec, planning_ir, profile, constraints)
            packet_slot = next(slot for slot in space["derived_type_slots"] if slot["type_id"] == "type:protocol_codec:mqtt_packet_t")

            result = reconcile_type_filling_candidate(
                space,
                {
                    "schema_version": "type_filling_candidate/v1",
                    "candidate_id": "candidate:type_filling:protocol_codec",
                    "module_id": "protocol_codec",
                    "slot_fillings": [
                        {
                            "slot_id": packet_slot["slot_id"],
                            "semantic_purpose": "Unified packet container for decoder and dispatch",
                            "fields": [
                                {
                                    "field_name": "type",
                                    "field_type": "mqtt_packet_type_t",
                                    "type_ref": "type:protocol_codec:mqtt_packet_type_t",
                                    "required": True,
                                    "ownership": "OWNED",
                                    "lifetime": "packet_scope",
                                    "length_field": "",
                                    "capacity_field": "",
                                    "validation_notes": "Discriminant for union",
                                },
                                {
                                    "field_name": "v",
                                    "field_type": "union",
                                    "type_ref": "union",
                                    "required": True,
                                    "ownership": "OWNED",
                                    "lifetime": "packet_scope",
                                    "length_field": "",
                                    "capacity_field": "",
                                    "validation_notes": "Union of packet payloads",
                                },
                            ],
                            "lifecycle": {"created_by": ["mqtt_decode"], "initialized_by": ["mqtt_decode"], "destroyed_by": ["mqtt_network_close"], "freed_by": ["mqtt_network_close"]},
                            "dependencies": ["type:protocol_codec:mqtt_packet_type_t"],
                            "trace_ref_keys": packet_slot["trace_ref_keys"],
                            "status": "supported",
                        }
                    ],
                    "optional_type_proposals": [],
                    "assumptions": [],
                    "unresolved_questions": [],
                },
            )
            packet = next(item for item in result["candidate"]["types"] if item["type_id"] == "type:protocol_codec:mqtt_packet_t")
            variant_field = next(field for field in packet["fields"] if field["field_name"] == "v")
            self.assertTrue(variant_field.get("variants"))
            self.assertEqual(packet["lifecycle"]["destroyed_by"], [])
            self.assertEqual(packet["lifecycle"]["freed_by"], ["mqtt_packet_free"])
            self.assertTrue(any(item["type_id"] == "type:protocol_codec:mqtt_packet_t" and item["action"] == "release_owned_data" for item in result["type_obligations"]["obligations"]))
            self.assertFalse(_has_error(validate_type_inventory_candidate(result["candidate"], draft["module_artifacts"], draft, profile, planning_ir)))

    def test_type_inventory_caller_owned_packet_fields_do_not_require_release_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            candidate = current_type_inventory_candidate(draft, codec, planning_ir)
            caller_owned_packet = {
                "type_id": "type:protocol_codec:mqtt_connect_packet_t",
                "name": "mqtt_connect_packet_t",
                "module_id": "protocol_codec",
                "kind": "struct",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "MQTT CONNECT packet fields; string storage remains caller-owned.",
                "fields": [
                    {
                        "field_name": "client_id",
                        "field_type": "char *",
                        "type_ref": "",
                        "required": True,
                        "ownership": "OWNED_BY_CALLER",
                        "lifetime": "caller keeps storage valid for the call",
                        "length_field": "",
                        "capacity_field": "",
                        "validation_notes": "",
                    }
                ],
                "enum_values": [],
                "callback_signature": {"return_type": "", "params": []},
                "ownership_lifetime": "caller_owned_strings",
                "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                "related_functions": [],
                "dependencies": [],
                "trace_ref_keys": [],
                "status": "inferred",
            }
            candidate["types"].insert(0, caller_owned_packet)

            diags = validate_type_inventory_candidate(candidate, draft["module_artifacts"], draft, profile, planning_ir)
            self.assertFalse(_has(diags, "owned_type_missing_release_path"))
            self.assertFalse(_has(diags, "packet_container_missing_release_path"))

            scalar_owned = copy.deepcopy(caller_owned_packet)
            scalar_owned.update(
                {
                    "type_id": "type:protocol_codec:mqtt_scalar_packet_t",
                    "name": "mqtt_scalar_packet_t",
                    "ownership_lifetime": "owned scalars",
                    "fields": [
                        {
                            "field_name": "qos",
                            "field_type": "uint8_t",
                            "type_ref": "uint8_t",
                            "required": True,
                            "ownership": "OWNED",
                            "lifetime": "packet-scoped",
                            "length_field": "",
                            "capacity_field": "",
                            "validation_notes": "",
                        }
                    ],
                }
            )
            scalar_candidate = copy.deepcopy(candidate)
            scalar_candidate["types"].insert(0, scalar_owned)
            self.assertFalse(_has(validate_type_inventory_candidate(scalar_candidate, draft["module_artifacts"], draft, profile, planning_ir), "owned_type_missing_release_path"))

            aliased_draft = copy.deepcopy(draft)
            aliased_draft["protocol_name"] = "protocol"
            alias_diags = validate_type_inventory_candidate(candidate, aliased_draft["module_artifacts"], aliased_draft, profile, planning_ir)
            self.assertFalse(_has(alias_diags, "payload_struct_field_mismatch"))

            owned_by_codec = copy.deepcopy(candidate)
            owned_by_codec["types"][0]["fields"][0]["ownership"] = "OWNED"
            owned_by_codec["types"][0]["ownership_lifetime"] = "owned strings are released by the packet cleanup path"
            owned_by_codec["types"][0]["purpose"] = "MQTT CONNECT packet owns dynamic string storage."
            self.assertTrue(_has(validate_type_inventory_candidate(owned_by_codec, draft["module_artifacts"], draft, profile, planning_ir), "owned_type_missing_release_path"))

    def test_public_callback_can_reference_same_module_public_opaque_handle(self) -> None:
        module = {
            "module_id": "broker_app",
            "name": "broker_app",
            "role": "broker application callbacks",
            "dependencies": [],
            "artifacts": [],
        }

        def type_item(type_id: str, name: str, kind: str, visibility: str, defined_in: str, **overrides):
            item = {
                "type_id": type_id,
                "name": name,
                "module_id": "broker_app",
                "kind": kind,
                "visibility": visibility,
                "defined_in": defined_in,
                "purpose": name,
                "fields": [],
                "enum_values": [],
                "callback_signature": {"return_type": "", "params": []},
                "ownership_lifetime": "",
                "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                "related_functions": [],
                "dependencies": [],
                "trace_ref_keys": [],
                "status": "supported",
            }
            item.update(overrides)
            return item

        candidate = {
            "schema_version": "type_inventory_candidate/v1",
            "candidate_id": "candidate:type_inventory:broker_app",
            "producer": {"stage": "test", "prompt_name": "test", "prompt_version": "test"},
            "module_id": "broker_app",
            "types": [
                type_item(
                    "type:broker_app:mqtt_broker_app_on_accept_fn",
                    "mqtt_broker_app_on_accept_fn",
                    "callback_type",
                    "public",
                    "public_header",
                    callback_signature={
                        "return_type": "void",
                        "params": [
                            {
                                "name": "broker",
                                "type": "mqtt_broker_app_t*",
                                "type_ref": "type:broker_app:mqtt_broker_app_t",
                                "ownership": "BORROWED",
                            }
                        ],
                    },
                    ownership_lifetime="borrows broker",
                    dependencies=["type:broker_app:mqtt_broker_app_t"],
                ),
                type_item("type:broker_app:mqtt_broker_app", "struct mqtt_broker_app", "internal_state", "private", "source_file", status="inferred"),
                type_item("type:broker_app:mqtt_broker_app_t", "mqtt_broker_app_t", "opaque_handle", "public", "public_header"),
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_type_inventory_candidate(candidate, [module], {"module_artifacts": [module]})
        self.assertFalse(_has(diags, "public_type_field_uses_private_type"))
        self.assertFalse(_has(diags, "public_callback_param_uses_private_type"))

    def test_network_type_inventory_generates_callback_collection(self) -> None:
        module = {
            "module_id": "network",
            "name": "network",
            "role": "TCP server connection accept read close timer epoll runtime",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_network_run", "kind": "FUNC", "role": "run epoll event loop"},
                {"name": "mqtt_timer_register", "kind": "FUNC", "role": "schedule timer callback"},
            ],
            "state_owned": ["server"],
            "owned_capabilities": ["transport_runtime"],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module]}
        candidate = current_type_inventory_candidate(draft, module)
        callback_collection = next(item for item in candidate["types"] if item["name"] == "mqtt_network_callbacks_t")
        field_names = {field["field_name"] for field in callback_collection["fields"]}
        self.assertTrue({"on_accept", "on_data", "on_close", "on_timer"}.issubset(field_names))
        self.assertFalse(_has_error(validate_type_inventory_candidate(candidate, [module], draft)))

    def test_type_inventory_validator_missing_private_leak_and_ownership(self) -> None:
        module = {
            "module_id": "mqtt_codec",
            "name": "mqtt_codec",
            "role": "MQTT codec with buffer ownership",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_packet", "kind": "TYPE", "role": "Decoded packet"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        candidate = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        public_packet = next(item for item in candidate["types"] if item["name"] == "mqtt_packet")
        private_state = copy.deepcopy(public_packet)
        private_state.update(
            {
                "type_id": "type:mqtt_codec:mqtt_codec_state",
                "name": "struct mqtt_codec_state",
                "kind": "internal_state",
                "visibility": "private",
                "defined_in": "source_file",
            }
        )
        public_packet["fields"].append(
            {
                "field_name": "state",
                "field_type": "struct mqtt_codec_state *",
                "type_ref": private_state["type_id"],
                "required": True,
                "ownership": "BORROWED",
                "lifetime": "valid during call",
                "length_field": "",
                "capacity_field": "",
                "validation_notes": "",
            }
        )
        public_packet["fields"].append(
            {
                "field_name": "payload",
                "field_type": "uint8_t*",
                "type_ref": "uint8_t",
                "required": False,
                "ownership": "UNKNOWN",
                "lifetime": "",
                "length_field": "",
                "capacity_field": "",
                "validation_notes": "",
            }
        )
        owned_buffer = copy.deepcopy(public_packet)
        owned_buffer.update(
            {
                "type_id": "type:mqtt_codec:mqtt_payload_buffer",
                "name": "mqtt_payload_buffer",
                "kind": "owned_buffer",
                "visibility": "private",
                "defined_in": "source_file",
                "fields": [],
            }
        )
        candidate["types"].extend([private_state, owned_buffer])
        diags = validate_type_inventory_candidate(candidate, [module], {"module_artifacts": [module]})
        self.assertTrue(_has(diags, "public_type_field_uses_private_type"))
        self.assertTrue(_has(diags, "pointer_field_missing_ownership"))
        self.assertTrue(_has(diags, "buffer_type_missing_size_fields"))

    def test_function_inventory_context_exposes_type_obligations(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "codec with callbacks and owned packet buffers",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_decode", "kind": "FUNC", "role": "Decode"}],
            "files": [],
            "doc_ref": [],
        }
        packet = {
            "type_id": "type:codec:mqtt_packet_t",
            "name": "mqtt_packet_t",
            "module_id": "codec",
            "kind": "struct",
            "visibility": "public",
            "defined_in": "public_header",
            "purpose": "packet with owned payload",
            "fields": [
                {
                    "field_name": "payload",
                    "field_type": "uint8_t*",
                    "type_ref": "uint8_t",
                    "required": False,
                    "ownership": "OWNED",
                    "lifetime": "owned until packet cleanup",
                    "length_field": "payload_len",
                    "capacity_field": "",
                    "validation_notes": "",
                }
            ],
            "enum_values": [],
            "callback_signature": {"return_type": "", "params": []},
            "ownership_lifetime": "owns dynamic payload",
            "lifecycle": {"created_by": ["mqtt_packet_create"], "initialized_by": [], "destroyed_by": ["mqtt_packet_destroy"], "freed_by": []},
            "related_functions": ["mqtt_packet_destroy"],
            "dependencies": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        callback = copy.deepcopy(packet)
        callback.update(
            {
                "type_id": "type:codec:mqtt_on_packet_fn",
                "name": "mqtt_on_packet_fn",
                "kind": "callback_type",
                "fields": [],
                "callback_signature": {"return_type": "void", "params": [{"name": "user", "type": "void*", "type_ref": "void", "ownership": "BORROWED"}]},
                "lifecycle": {"created_by": ["mqtt_callback_create"], "initialized_by": [], "destroyed_by": ["mqtt_transport_close"], "freed_by": ["mqtt_transport_close"]},
                "related_functions": ["mqtt_decoder_register_callback"],
            }
        )
        normalized_callback = normalize_type_inventory_candidate({"types": [copy.deepcopy(callback)]})["types"][0]
        self.assertEqual(normalized_callback["lifecycle"], {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []})
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "type_inventory": [packet, callback]}
        obligations = derive_type_obligations(draft, module)
        actions = {item["action"] for item in obligations}
        self.assertIn("create", actions)
        self.assertIn("destroy", actions)
        self.assertIn("release_owned_data", actions)
        self.assertIn("register_callback", actions)
        callback_obligations = [item for item in obligations if item["type_id"] == "type:codec:mqtt_on_packet_fn"]
        self.assertEqual([item["action"] for item in callback_obligations], ["register_callback"])
        context = build_function_inventory_context(draft, module)
        self.assertEqual(context["type_obligations"], obligations)

    def test_type_obligations_ignore_broker_lifecycle_names_outside_broker_app(self) -> None:
        module = {
            "module_id": "network",
            "name": "network",
            "role": "network runtime",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_network_run", "kind": "FUNC", "role": "run network loop"}],
            "files": [],
            "doc_ref": [],
        }
        server = {
            "type_id": "type:network:mqtt_server_t",
            "name": "mqtt_server_t",
            "module_id": "network",
            "kind": "opaque_handle",
            "visibility": "public",
            "defined_in": "public_header",
            "purpose": "network server context",
            "fields": [],
            "enum_values": [],
            "callback_signature": {"return_type": "", "params": []},
            "ownership_lifetime": "owned by broker but implemented by network",
            "lifecycle": {"created_by": ["mqtt_broker_init"], "initialized_by": ["mqtt_broker_init"], "destroyed_by": ["mqtt_broker_run"], "freed_by": ["mqtt_broker_run"]},
            "related_functions": [],
            "dependencies": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        obligations = derive_type_obligations({"type_inventory": [server]}, module)
        by_action = {item["action"]: item["required_function_names"] for item in obligations}
        self.assertIn("mqtt_server_create", by_action["create"])
        self.assertIn("mqtt_server_destroy", by_action["destroy"])
        self.assertNotIn("mqtt_broker_init", by_action["create"])
        self.assertNotIn("mqtt_broker_run", by_action["destroy"])

    def test_function_inventory_must_cover_type_obligations_or_block(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "codec",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_decode", "kind": "FUNC", "role": "Decode"}],
            "files": [],
            "doc_ref": [],
        }
        packet = {
            "type_id": "type:codec:mqtt_packet_t",
            "name": "mqtt_packet_t",
            "module_id": "codec",
            "kind": "struct",
            "visibility": "public",
            "defined_in": "public_header",
            "purpose": "packet with owned payload",
            "fields": [
                {
                    "field_name": "payload",
                    "field_type": "uint8_t*",
                    "type_ref": "uint8_t",
                    "required": False,
                    "ownership": "OWNED",
                    "lifetime": "owned until packet cleanup",
                    "length_field": "payload_len",
                    "capacity_field": "",
                    "validation_notes": "",
                }
            ],
            "enum_values": [],
            "callback_signature": {"return_type": "", "params": []},
            "ownership_lifetime": "owns dynamic payload",
            "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": ["mqtt_packet_free"]},
            "related_functions": ["mqtt_packet_free"],
            "dependencies": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "type_inventory": [packet]}
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:type_obligation",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": "codec",
            "functions": [_inventory_function("mqtt_decode", "codec", kind="parser")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], draft, {}, {})
        self.assertTrue(_has(diags, "type_obligation_uncovered"))

        blocked = copy.deepcopy(candidate)
        blocked["unresolved_questions"] = [
            {
                "question_id": "q:type_obligation",
                "target_kind": "type_obligation",
                "target_id": "obligation:type:codec:mqtt_packet_t:release_owned_data",
                "question": "Need a packet cleanup boundary.",
                "unresolved_reason": "No evidence for public or internal ownership boundary.",
                "blocking": True,
                "trace_ref_keys": [],
            }
        ]
        self.assertFalse(_has(validate_function_inventory_candidate(blocked, [module], draft, {}, {}), "type_obligation_uncovered"))

        covered = copy.deepcopy(candidate)
        covered["functions"].append(_inventory_function("mqtt_packet_free", "codec", kind="resource_lifecycle"))
        self.assertFalse(_has_error(validate_function_inventory_candidate(covered, [module], draft, {}, {})))

    def test_function_inventory_rejects_type_function_reference_drift(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "codec",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_decode", "kind": "FUNC", "role": "Decode"}],
            "files": [],
            "doc_ref": [],
        }
        type_item = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["related_functions"] = ["mqtt_missing_cleanup"]
        type_item["lifecycle"]["freed_by"] = ["mqtt_missing_cleanup"]
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "type_inventory": [type_item], "function_contracts": []}
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:type_ref_drift",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": "all_modules",
            "functions": [_inventory_function("mqtt_decode", "codec", kind="parser")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], draft, {}, {})
        self.assertTrue(_has(diags, "type_function_reference_unresolved"))

    def test_function_inventory_warns_on_unknown_assumption_function_ref(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "routing registry",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_router_match", "kind": "FUNC", "role": "Match routes"}],
            "files": [],
            "doc_ref": [],
        }
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:assumption_ref",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "functions": [_inventory_function("mqtt_router_match", module["module_id"], kind="handler")],
            "assumptions": [
                {
                    "assumption_id": "assume:router:missing_helper",
                    "target_kind": "function",
                    "target_id": "fn:router:mqtt_router_missing_helper",
                    "statement": "Missing helper exists.",
                    "rationale": "Test unresolved function reference diagnostics.",
                    "confidence": "medium",
                    "trace_ref_keys": [],
                }
            ],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {"module_artifacts": [module]}, {}, {})
        self.assertFalse(_has_error(diags))
        self.assertTrue(_has(diags, "assumption_function_reference_unresolved"))

    def test_type_function_reference_reconciliation_uses_generated_lifecycle(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "codec",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_decode", "kind": "FUNC", "role": "Decode"}],
            "files": [],
            "doc_ref": [],
        }
        type_item = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["lifecycle"]["freed_by"] = ["old_packet_cleanup"]
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [module],
            "type_inventory": [type_item],
            "function_contracts": [_inventory_function("mqtt_packet_free", "codec", kind="resource_lifecycle")],
        }
        reconciled = reconcile_type_inventory_function_refs(draft)
        self.assertEqual(reconciled["type_inventory"][0]["lifecycle"]["freed_by"], ["mqtt_packet_free"])

    def test_type_function_reference_reconciliation_marks_unmatched_refs_nonblocking(self) -> None:
        module = {
            "module_id": "timer",
            "name": "timer",
            "role": "timer",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_timer_create", "kind": "FUNC", "role": "Create"}],
            "files": [],
            "doc_ref": [],
        }
        type_item = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["lifecycle"]["destroyed_by"] = ["timer_expiry"]
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [module],
            "type_inventory": [type_item],
            "function_contracts": [_inventory_function("mqtt_timer_create", "timer", kind="resource_lifecycle")],
        }
        reconciled = reconcile_type_inventory_function_refs(draft)
        unresolved = [item for item in reconciled["unresolved_questions"] if item.get("target_id") == "timer_expiry"]
        self.assertTrue(unresolved)
        self.assertFalse(unresolved[0].get("blocking"))

    def test_type_function_reference_reconciliation_is_idempotent_after_repair(self) -> None:
        module = {
            "module_id": "timer",
            "name": "timer",
            "role": "timer",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_timer_cancel", "kind": "FUNC", "role": "Cancel"}],
            "files": [],
            "doc_ref": [],
        }
        type_item = current_type_inventory_candidate({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["name"] = "mqtt_timer_t"
        type_item["lifecycle"]["destroyed_by"] = ["timer_expiry"]
        type_item["lifecycle"]["freed_by"] = ["timer_expiry"]
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [module],
            "type_inventory": [type_item],
            "function_contracts": [_inventory_function("mqtt_timer_cancel", "timer", kind="resource_lifecycle")],
            "unresolved_questions": [{"question_id": "q:type_function_ref:timer:old:destroyed_by:timer_expiry", "target_id": "timer_expiry"}],
        }
        reconciled = reconcile_type_inventory_function_refs(reconcile_type_inventory_function_refs(draft))
        lifecycle = reconciled["type_inventory"][0]["lifecycle"]
        self.assertEqual(lifecycle["destroyed_by"], ["mqtt_timer_cancel"])
        self.assertEqual(lifecycle["freed_by"], ["mqtt_timer_cancel"])
        self.assertFalse([item for item in reconciled.get("unresolved_questions", []) if str(item.get("question_id", "")).startswith("q:type_function_ref:")])

    def test_key_flow_module_requires_lifecycle_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            modules = copy.deepcopy(items["modules"])
            key_module = next(module for module in modules["modules"] if "broker" in module["module_id"] or "app" in module["module_id"])
            key_module["artifacts"] = [artifact for artifact in key_module["artifacts"] if artifact["kind"] != "FUNC"]
            self.assertTrue(_has(validate_module_artifacts_candidate(modules, selected, profile, constraints, draft), "broker_role_module_missing_lifecycle_artifact"))

    def test_function_inventory_public_api_visibility_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            inventory = copy.deepcopy(items["inventory"])
            public_function = next(function for function in inventory["functions"] if function["exported"])
            public_function["visibility"] = "internal"
            self.assertTrue(_has(validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir), "exported_function_not_public"))

            inventory = copy.deepcopy(items["inventory"])
            public_function = next(function for function in inventory["functions"] if function["exported"])
            public_function["visibility"] = "static"
            self.assertTrue(_has(validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir), "static_function_exported"))

    def test_function_inventory_decomposition_warnings_are_nonblocking(self) -> None:
        module = {
            "module_id": "generic_framing_unit",
            "name": "generic_framing_unit",
            "role": "incremental byte stream framing, command decoding, and response serialization",
            "dependencies": [],
            "artifacts": [{"name": "proto_decode", "kind": "FUNC", "role": "Decode command stream"}],
            "files": [],
            "doc_ref": ["decoder_commands"],
        }
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:inventory",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "functions": [_inventory_function("proto_decode", module["module_id"], kind="parser", purpose="Parse, validate, dispatch, update state, encode response, send reply, and cleanup resources.")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {}, {}, {})
        self.assertFalse(_has_error(diags))
        self.assertTrue(_has(diags, "under_decomposed_inventory"))
        self.assertTrue(_has(diags, "coarse_function_should_split"))
        self.assertTrue(_has(diags, "missing_parser_or_serializer_helpers"))
        report = validation_report("5.4a_function_inventory:test", diags)
        self.assertTrue(report["passed"])
        self.assertTrue(any("mirrors mandatory FUNC artifacts" in hint for hint in report["repair_hints"]))

    def test_function_inventory_requires_handlers_only_for_handler_owner_module(self) -> None:
        session = {
            "module_id": "session",
            "name": "session",
            "role": "session state machine",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_session_create", "kind": "FUNC", "role": "create session"},
                {"name": "mqtt_session_process", "kind": "FUNC", "role": "process packet through state machine"},
                {"name": "mqtt_session_destroy", "kind": "FUNC", "role": "destroy session"},
            ],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        broker = {
            "module_id": "broker_app",
            "name": "broker app",
            "role": "broker dispatch",
            "dependencies": ["session"],
            "artifacts": [{"name": "mqtt_broker_dispatch", "kind": "FUNC", "role": "dispatch packet"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        core_design = {
            "module_artifacts": [session, broker],
            "handler_matrix": [{"handler_id": "handle_connect", "owner_module_id": "broker_app", "handler_kind": "message"}],
            "type_inventory": [],
            "function_contracts": [],
        }
        profile = {"required_capabilities": [{"capability_id": "state_machine"}, {"capability_id": "semantic_dispatch"}]}
        session_candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:session:no_handler_required",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": "session",
            "functions": [
                _inventory_function("mqtt_session_create", "session", kind="resource_lifecycle", purpose="create session"),
                _inventory_function("mqtt_session_process", "session", kind="public_api", purpose="run state machine"),
                _inventory_function("mqtt_session_destroy", "session", kind="resource_lifecycle", purpose="destroy session"),
            ],
            "assumptions": [],
            "unresolved_questions": [],
        }
        session_candidate["functions"][1]["capability_ids"] = ["state_machine"]
        self.assertFalse(_has(validate_function_inventory_candidate(session_candidate, [session, broker], core_design, profile, {}), "missing_handler_function"))

        broker_candidate = {
            **copy.deepcopy(session_candidate),
            "candidate_id": "candidate:broker_app:missing_handler",
            "module_id": "broker_app",
            "functions": [_inventory_function("mqtt_broker_dispatch", "broker_app", kind="public_api", purpose="dispatch packet")],
        }
        broker_candidate["functions"][0]["capability_ids"] = ["semantic_dispatch"]
        self.assertTrue(_has(validate_function_inventory_candidate(broker_candidate, [session, broker], core_design, profile, {}), "missing_handler_function"))

        all_modules = copy.deepcopy(session_candidate)
        all_modules["module_id"] = "all_modules"
        all_modules["functions"].append(_inventory_function("mqtt_broker_dispatch", "broker_app", kind="handler", purpose="dispatch packet"))
        all_modules["functions"][-1]["covers_handler_ids"] = ["handle_connect"]
        all_modules["functions"][-1]["capability_ids"] = ["semantic_dispatch"]
        self.assertFalse(_has(validate_function_inventory_candidate(all_modules, [session, broker], core_design, profile, {}), "missing_handler_function"))

    def test_function_planning_space_keeps_same_trigger_handlers_distinct(self) -> None:
        broker = {
            "module_id": "broker_app",
            "name": "broker_app",
            "role": "semantic dispatch coordination",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_broker_run", "kind": "FUNC", "role": "run dispatch loop"}],
            "files": [],
            "doc_ref": [],
        }
        draft = {
            "module_artifacts": [broker],
            "handler_matrix": [
                {"handler_id": "handler:publish_in", "owner_module_id": "broker_app", "trigger": "PUBLISH", "trace_ref_keys": [], "message_ids": ["message:publish"]},
                {"handler_id": "handler:publish_out", "owner_module_id": "broker_app", "trigger": "PUBLISH", "trace_ref_keys": [], "message_ids": ["message:publish"]},
            ],
            "traceability": {"required_capabilities": []},
            "type_inventory": [],
            "canonical_types": [],
        }
        planning_ir = {"message_model": {"message_or_command_entries": [{"message_id": "message:publish", "fields": []}]}}
        space = build_function_planning_space(draft, broker, planning_ir)
        handler_seeds = space["handler_function_seeds"]
        self.assertEqual({"handler:publish_in", "handler:publish_out"}, {seed["covers_handler_ids"][0] for seed in handler_seeds})
        self.assertEqual(len(handler_seeds), len({seed["name"] for seed in handler_seeds}))

        result = reconcile_function_annotation_candidate(
            space,
            {
                "schema_version": "function_annotation_candidate/v1",
                "module_id": "broker_app",
                "seed_annotations": [],
                "optional_function_proposals": [],
                "assumptions": [],
                "unresolved_questions": [],
                "decomposition_notes": [],
            },
        )
        self.assertFalse(_has(validate_function_inventory_candidate(result["candidate"], [broker], draft, {}, planning_ir), "missing_handler_function"))

    def test_function_reconciler_drops_optional_provider_capability_refs(self) -> None:
        broker = {"module_id": "broker_app", "name": "broker_app", "role": "dispatch", "dependencies": ["protocol_codec"], "artifacts": [], "files": [], "doc_ref": []}
        draft = {"module_artifacts": [broker], "handler_matrix": [], "traceability": {"required_capabilities": ["message_encode", "transport_io"]}, "type_inventory": [], "canonical_types": []}
        space = build_function_planning_space(draft, broker, {})
        result = reconcile_function_annotation_candidate(
            space,
            {
                "schema_version": "function_annotation_candidate/v1",
                "module_id": "broker_app",
                "seed_annotations": [],
                "optional_function_proposals": [
                    {
                        "proposal_key": "opt:broker_app:send_response",
                        "name_hint": "mqtt_broker_app_send_response_packet",
                        "function_kind": "internal_helper",
                        "coder_function_type": "ALGORITHM",
                        "family": "protocol_event_helpers",
                        "expansion_reason": "broker dispatch calls provider encoder and transport send",
                        "source_refs": ["encoder_responses"],
                        "capability_ids": ["message_encode", "transport_io"],
                    }
                ],
                "assumptions": [],
                "unresolved_questions": [],
                "decomposition_notes": [],
            },
        )
        helper = next(function for function in result["candidate"]["functions"] if function["name"] == "mqtt_broker_app_send_response_packet")
        self.assertEqual(helper["capability_ids"], [])
        self.assertIn("message_encode", helper["trace_ref_keys"])
        self.assertFalse(_has(validate_function_inventory_candidate(result["candidate"], [broker], draft, {}, {}), "missing_serializer_function"))

    def test_derived_public_api_requires_justification_warning(self) -> None:
        module = {
            "module_id": "generic_runtime_boundary",
            "name": "generic_runtime_boundary",
            "role": "application boundary lifecycle and callback registration",
            "dependencies": [],
            "artifacts": [{"name": "proto_run", "kind": "FUNC", "role": "Run application boundary"}],
            "files": [],
            "doc_ref": [],
        }
        derived = _inventory_function("proto_register_callback", module["module_id"], public=False, purpose="Register a callback boundary for integration.")
        derived["visibility"] = "public"
        derived["api_surface"] = "public"
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:derived_public",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "functions": [_inventory_function("proto_run", module["module_id"], public=True), derived],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {}, {}, {})
        self.assertFalse(_has_error(diags))
        self.assertTrue(_has(diags, "derived_public_api_without_justification"))

    def test_function_family_warnings_cover_resource_dispatch_and_codec_helpers(self) -> None:
        module = {
            "module_id": "generic_command_state_transfer",
            "name": "generic_command_state_transfer",
            "role": "line-oriented command dispatch, login state, and data transfer orchestration",
            "dependencies": [],
            "artifacts": [{"name": "proto_handle", "kind": "FUNC", "role": "Handle command state transfer"}],
            "files": [],
            "doc_ref": ["login_state", "data_transfer"],
        }
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:families",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "functions": [_inventory_function("proto_handle", module["module_id"], kind="public_api", purpose="Handle command state transfer.")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {}, {}, {})
        self.assertFalse(_has_error(diags))
        self.assertTrue(_has(diags, "missing_dispatch_boundary"))
        self.assertTrue(_has(diags, "missing_cleanup_for_resource_owner"))

    def test_key_flow_function_inventory_requires_lifecycle_functions(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            key_module = next(module for module in draft["module_artifacts"] if "role_composition" in module["owned_capabilities"])
            inventory = next(item for item in items["inventories"] if item["module_id"] == key_module["module_id"])
            self.assertFalse(_has_error(validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir)))

            missing_run = copy.deepcopy(inventory)
            missing_run["functions"] = [function for function in missing_run["functions"] if function.get("public_api_role") != "runtime_run"]
            self.assertTrue(_has(validate_function_inventory_candidate(missing_run, draft["module_artifacts"], draft, profile, planning_ir), "function_inventory_missing_artifact_function"))

            handler_lifecycle = copy.deepcopy(inventory)
            lifecycle = next(function for function in handler_lifecycle["functions"] if function.get("public_api_role") == "runtime_create")
            lifecycle["function_kind"] = "handler"
            lifecycle["name"] = "handle_connect_message"
            self.assertTrue(_has(validate_function_inventory_candidate(handler_lifecycle, draft["module_artifacts"], draft, profile, planning_ir), "lifecycle_role_uses_handler"))

            internal_lifecycle = copy.deepcopy(inventory)
            lifecycle = next(function for function in internal_lifecycle["functions"] if function.get("public_api_role") == "runtime_run")
            lifecycle["exported"] = False
            lifecycle["visibility"] = "internal"
            lifecycle["api_surface"] = "module_internal"
            self.assertTrue(_has(validate_function_inventory_candidate(internal_lifecycle, draft["module_artifacts"], draft, profile, planning_ir), "lifecycle_function_not_public"))

    def test_public_function_signature_and_contract_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, constraints, _, draft, _, items = self._fixtures(Path(raw_tmp))
            public_id = next(function["function_id"] for function in draft["function_contracts"] if function.get("exported"))
            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["function_id"] == public_id)
            update["signature"]["raw"] = ""
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_function_incomplete_signature"))

            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["function_id"] == public_id)
            update["interface_type_declarations"].append({"name": "zap_private_t", "kind": "type", "owner_module_id": draft["module_artifacts"][0]["module_id"], "visibility": "internal", "reason": "bad"})
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_private_interface_type"))

            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["function_id"] == public_id)
            update["signature"]["raw"] = f"{update['signature']['raw']};"
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "signature_raw_trailing_semicolon"))

            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["function_id"] == public_id)
            update["signature"]["name"] = update["signature"]["name"]
            duplicate = copy.deepcopy(update)
            duplicate["function_id"] = next(function["function_id"] for function in draft["function_contracts"] if function.get("exported") and function["function_id"] != public_id)
            duplicate["signature"]["name"] = update["signature"]["name"]
            signature["function_signature_updates"].append(duplicate)
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "duplicate_public_signature_name"))

            behavior = copy.deepcopy(items["behavior"])
            update = next(item for item in behavior["function_behavior_updates"] if item["function_id"] == public_id)
            update["contract"]["input"] = ""
            self.assertTrue(_has(validate_function_behavior_contract_patch(behavior, draft, constraints), "public_function_incomplete_contract"))

    def test_file_layout_declares_public_only_in_headers(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            public_id = next(function["function_id"] for function in draft["function_contracts"] if function.get("exported"))
            layout = copy.deepcopy(items["layout"])
            for file_item in layout["files"]:
                file_item["exports_function_ids"] = [function_id for function_id in file_item["exports_function_ids"] if function_id != public_id]
            for assignment in layout["function_file_assignments"]:
                if assignment["function_id"] == public_id:
                    assignment["declaration_file_id"] = ""
            diags = validate_file_layout_candidate(layout, draft)
            self.assertTrue(_has(diags, "public_function_header_export_count_mismatch"))
            self.assertTrue(_has(diags, "public_function_not_declared"))

            private_id = public_id
            for function in draft["function_contracts"]:
                if function["function_id"] == private_id:
                    function["exported"] = False
                    function["visibility"] = "internal"
                    function["api_surface"] = "module_internal"
                    function["public_api_role"] = ""
                    function["function_kind"] = "internal_helper"
            layout = copy.deepcopy(items["layout"])
            for assignment in layout["function_file_assignments"]:
                if assignment["function_id"] == private_id:
                    assignment["visibility"] = "internal"
                    assignment["declaration_file_id"] = ""
            layout["files"][0]["exports_function_ids"].append(private_id)
            self.assertTrue(_has(validate_file_layout_candidate(layout, draft), "private_function_exported_in_header"))

            layout = copy.deepcopy(items["layout"])
            layout["files"][0]["implements_function_ids"].append(layout["files"][0]["implements_function_ids"][0])
            self.assertTrue(_has(validate_file_layout_candidate(layout, draft), "function_definition_count_mismatch"))

    def test_file_layout_normalizes_public_type_exports_to_canonical_public_types(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _constraints, _selected, draft, plan, items = self._fixtures(Path(raw_tmp))
            module_id = draft["module_artifacts"][0]["module_id"]
            canonical_type = {
                "type_id": f"type:{module_id}:public_frame",
                "name": "public_frame_t",
                "kind": "alias",
                "owner_module_id": module_id,
                "alias_of": "uint8_t",
            }
            public_inventory = {
                "type_id": f"type:{module_id}:public_frame_inventory",
                "name": "public_frame_t",
                "module_id": module_id,
                "kind": "alias",
                "visibility": "public",
                "defined_in": "public_header",
            }
            internal_inventory = {
                "type_id": f"type:{module_id}:decode_result_t",
                "name": "decode_result_t",
                "module_id": module_id,
                "kind": "result_struct",
                "visibility": "module_internal",
                "defined_in": "internal_header",
            }
            private_inventory = {
                "type_id": f"type:{module_id}:state",
                "name": "struct state",
                "module_id": module_id,
                "kind": "internal_state",
                "visibility": "private",
                "defined_in": "source_file",
            }
            for target in (draft, plan):
                target.setdefault("canonical_types", []).append(copy.deepcopy(canonical_type))
                target.setdefault("type_inventory", []).extend([copy.deepcopy(public_inventory), copy.deepcopy(internal_inventory), copy.deepcopy(private_inventory)])

            layout = copy.deepcopy(items["layout"])
            layout["files"][0]["exports_type_ids"] = [
                canonical_type["type_id"],
                public_inventory["type_id"],
                internal_inventory["type_id"],
                private_inventory["type_id"],
                f"type:{module_id}:missing",
            ]
            diags = validate_file_layout_candidate(layout, draft)
            self.assertTrue(_has(diags, "layout_exports_noncanonical_type"))
            self.assertTrue(_has(diags, "layout_exports_nonpublic_type"))
            self.assertTrue(_has(diags, "layout_exports_unknown_type"))

            normalized, stats = normalize_file_layout_candidate(layout, draft)
            self.assertEqual(normalized["files"][0]["exports_type_ids"], [canonical_type["type_id"]])
            self.assertGreater(stats["layout_export_type_normalized"], 0)
            self.assertGreaterEqual(stats["layout_internal_export_dropped"], 2)
            self.assertGreater(stats["layout_unknown_export_dropped"], 0)
            self.assertFalse(_has_error(validate_file_layout_candidate(normalized, draft)))

            bad_plan = copy.deepcopy(plan)
            bad_plan["file_layout"]["files"][0]["exports_type_ids"] = [internal_inventory["type_id"]]
            self.assertTrue(_has(validate_full_implementation_plan(bad_plan, profile=profile, planning_ir=planning_ir), "layout_exports_nonpublic_type"))

            forbidden_canonical = copy.deepcopy(canonical_type)
            forbidden_canonical["type_id"] = f"type:{module_id}:connect"
            forbidden_canonical["name"] = "connect"
            forbidden_layout = copy.deepcopy(items["layout"])
            forbidden_layout["files"][0]["exports_type_ids"] = [forbidden_canonical["type_id"]]
            forbidden_draft = copy.deepcopy(draft)
            forbidden_draft.setdefault("canonical_types", []).append(forbidden_canonical)
            forbidden_draft["forbidden_symbols"] = [{"name": "connect", "kind": "FUNC", "reason": "public ABI must avoid POSIX names"}]
            self.assertTrue(_has(validate_file_layout_candidate(forbidden_layout, forbidden_draft), "layout_exports_forbidden_public_type_name"))

            forbidden_plan = copy.deepcopy(plan)
            forbidden_plan.setdefault("canonical_types", []).append(forbidden_canonical)
            forbidden_plan["forbidden_symbols"] = [{"name": "connect", "kind": "FUNC", "reason": "public ABI must avoid POSIX names"}]
            forbidden_plan["file_layout"]["files"][0]["exports_type_ids"] = [forbidden_canonical["type_id"]]
            self.assertTrue(_has(validate_full_implementation_plan(forbidden_plan, profile=profile, planning_ir=planning_ir), "layout_exports_forbidden_public_type_name"))

            forbidden_artifact_plan = copy.deepcopy(plan)
            forbidden_artifact_plan["forbidden_symbols"] = [{"name": "connect", "kind": "FUNC", "reason": "public ABI must avoid POSIX names"}]
            forbidden_artifact_plan["module_artifacts"][0]["artifacts"][0]["name"] = "connect"
            self.assertTrue(_has(validate_full_implementation_plan(forbidden_artifact_plan, profile=profile, planning_ir=planning_ir), "readiness_forbidden_public_symbol"))

    def test_full_plan_and_dependency_graph_are_rejected_as_stage_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, plan, items = self._fixtures(Path(raw_tmp))
            validators = [
                lambda c: validate_core_design_candidate(c, planning_ir, profile, selected, constraints),
                lambda c: validate_module_artifacts_candidate(c, selected, profile, constraints, draft),
                lambda c: validate_type_inventory_candidate(c, draft["module_artifacts"], draft, profile, planning_ir),
                lambda c: validate_function_inventory_candidate(c, draft["module_artifacts"], draft, profile, planning_ir),
                lambda c: validate_function_signature_patch(c, draft),
                lambda c: validate_function_behavior_contract_patch(c, draft, constraints),
                lambda c: validate_wire_access_binding_patch(c, draft, planning_ir),
                lambda c: validate_calls_allowed_candidate(c, draft, selected),
                lambda c: validate_file_layout_candidate(c, draft),
                lambda c: validate_runtime_entrypoint_candidate(c, draft),
                lambda c: validate_dependency_repair_patch(c, draft),
            ]
            for validator in validators:
                self.assertTrue(_has(validator(plan), "invalid_schema_version"))
            for key in ("core", "modules", "type_inventory", "inventory", "signature", "behavior", "wire", "calls", "layout", "runtime", "repair"):
                candidate = copy.deepcopy(items[key])
                candidate["dependency_graph"] = {}
                validator = validators[["core", "modules", "type_inventory", "inventory", "signature", "behavior", "wire", "calls", "layout", "runtime", "repair"].index(key)]
                self.assertTrue(_has(validator(candidate), "forbidden_extra_field"))

    def test_prompt_uses_output_shape_not_allowed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, _, _, _ = self._fixtures(Path(raw_tmp))
            payload = json.loads(core_design_candidate_messages(build_core_design_context(planning_ir, profile, constraints, selected))[1]["content"])
            self.assertEqual(payload["output_schema"], "core_design_candidate/v1")
            self.assertIn("output_shape", payload)
            self.assertNotIn("allowed_fields", payload)
            self.assertTrue(any("output_shape" in rule for rule in payload["hard_validation_rules"]))
            self.assertIn("semantic_validation_rules", payload)
            self.assertIn("id_reference_rules", payload)
            self.assertIn("enum_usage_rules", payload)

    def test_function_inventory_classifier_uses_semantic_top_three_hints(self) -> None:
        self.assertTrue(all(rule.expected_function_families for rule in DECOMPOSITION_RULES))
        self.assertTrue(all(rule.concrete_function_slots for rule in DECOMPOSITION_RULES))
        cases = [
            (
                "incremental byte stream framing, command decoding, and response serialization",
                {"framing_and_parsing"},
                {"encoding_and_response", "dispatch_and_handlers"},
            ),
            (
                "UDP endpoint runtime, request transaction state, retransmission timer management",
                {"transport_runtime_io", "session_transaction_state"},
                set(),
            ),
            (
                "line-oriented command dispatch, login state, and data transfer orchestration",
                {"dispatch_and_handlers"},
                {"session_transaction_state", "payload_data_transfer"},
            ),
            (
                "resource registry, path matching, and payload delivery",
                {"registry_routing_namespace", "payload_data_transfer"},
                set(),
            ),
        ]
        for role, required, alternatives in cases:
            module = {
                "module_id": "generic_unit",
                "name": "generic_unit",
                "role": role,
                "dependencies": [],
                "artifacts": [{"name": "proto_seed", "kind": "FUNC", "role": role}],
                "files": [],
                "doc_ref": [],
            }
            selected = select_top_decomposition_hints(module, {"core_design_summary": {}}, max_hints=3)
            ids = selected["detected_rule_ids"]
            self.assertEqual(ids, selected["selected_rule_ids"])
            self.assertEqual(set(ids), set(selected["expected_function_families_by_rule"]))
            self.assertTrue(required.issubset(set(ids)))
            if alternatives:
                self.assertTrue(set(ids) & alternatives)
            renamed = copy.deepcopy(module)
            renamed["module_id"] = "mqtt_codec_network_router_session_store_broker_app"
            self.assertEqual(ids, select_top_decomposition_hints(renamed, {"core_design_summary": {}}, max_hints=3)["detected_rule_ids"])

    def test_function_inventory_classifier_filters_weak_generic_matches(self) -> None:
        timer = {
            "module_id": "timer",
            "name": "timer",
            "role": "timer callback scheduling",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_timer_cancel", "kind": "FUNC", "role": "Cancel timer"}],
            "files": [],
            "doc_ref": [],
        }
        codec = {
            "module_id": "codec",
            "name": "codec",
            "role": "packet data model decoder encoder",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_decode", "kind": "FUNC", "role": "Decode packet"}],
            "files": [],
            "doc_ref": [],
        }
        self.assertNotIn("transport_runtime_io", select_top_decomposition_hints(timer, {"core_design_summary": {}}, max_hints=3)["detected_rule_ids"])
        self.assertNotIn("session_transaction_state", select_top_decomposition_hints(codec, {"core_design_summary": {}}, max_hints=3)["detected_rule_ids"])

    def test_function_planning_space_uses_concrete_recommended_helpers(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "resource registry, path matching, subscription routing, and session cleanup",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_router_match", "kind": "FUNC", "role": "Match published topic against subscriptions"},
            ],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "handler_matrix": []}
        space = build_function_planning_space(draft, module)
        recommended = space["recommended_function_families"]
        abstract_families = {
            family
            for rule in DECOMPOSITION_RULES
            for family in rule.expected_function_families
        }
        self.assertGreaterEqual(len(recommended), 5)
        for seed in recommended:
            name = str(seed["name"])
            self.assertEqual(seed["module_id"], module["module_id"])
            self.assertEqual(seed["visibility"], "internal")
            self.assertEqual(seed["api_surface"], "module_internal")
            self.assertFalse(seed["exported"])
            self.assertFalse(any(name.endswith(f"_{family}") for family in abstract_families), name)
        candidate = reconcile_function_annotation_candidate(space, None)["candidate"]
        diags = validate_function_inventory_candidate(candidate, [module], draft, {}, {})
        self.assertFalse(_has_error(diags))
        self.assertFalse(_has(diags, "abstract_function_family_name"))
        report = function_inventory_decomposition_report(candidate, [module], draft)
        router_report = next(item for item in report["modules"] if item["module_id"] == "router")
        self.assertGreaterEqual(router_report["coverage_score"], 0.65)

    def test_function_reconciler_rejects_abstract_optional_helper_names(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "resource registry, path matching, subscription routing, and session cleanup",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_router_match", "kind": "FUNC", "role": "Match routes"}],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "handler_matrix": []}
        space = build_function_planning_space(draft, module)
        result = reconcile_function_annotation_candidate(
            space,
            {
                "schema_version": "function_annotation_candidate/v1",
                "candidate_id": "candidate:function_annotation:router",
                "module_id": "router",
                "seed_annotations": [],
                "optional_function_proposals": [
                    {
                        "proposal_key": "abstract_lookup",
                        "name_hint": "mqtt_router_lookup_or_match",
                        "family": "lookup_or_match",
                        "function_kind": "internal_helper",
                        "coder_function_type": "ALGORITHM",
                        "purpose": "abstract helper",
                        "expansion_reason": "test abstract rejection",
                        "source_refs": ["decision:test:abstract"],
                    }
                ],
                "assumptions": [],
                "unresolved_questions": [],
            },
        )
        self.assertFalse(any(item["name"] == "mqtt_router_lookup_or_match" for item in result["candidate"]["functions"]))
        self.assertEqual(result["reconciliation_report"]["rejected_optional_functions"][0]["proposal_key"], "abstract_lookup")

    def test_type_reconciler_filters_actor_lifecycle_names(self) -> None:
        module = {
            "module_id": "protocol_codec",
            "name": "protocol_codec",
            "role": "codec owned buffer helpers",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_bytes_t", "kind": "TYPE", "role": "Owned byte buffer result"}],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module]}
        space = build_type_planning_space(draft, module)
        slot = next(item for item in space["mandatory_type_slots"] if item["name"] == "mqtt_bytes_t")
        result = reconcile_type_filling_candidate(
            space,
            {
                "schema_version": "type_filling_candidate/v1",
                "candidate_id": "candidate:type_filling:protocol_codec",
                "module_id": "protocol_codec",
                "slot_fillings": [
                    {
                        "slot_id": slot["slot_id"],
                        "semantic_purpose": "Owned encoded byte buffer.",
                        "fields": [],
                        "enum_values": [],
                        "callback_signature": {"return_type": "", "params": []},
                        "ownership_lifetime": "caller receives an owned buffer and must release it",
                        "lifecycle": {"created_by": ["mqtt_encode_*"], "initialized_by": [], "destroyed_by": [], "freed_by": ["caller"]},
                        "dependencies": [],
                        "trace_ref_keys": ["decision:test:owned_buffer"],
                        "status": "inferred",
                    }
                ],
                "optional_type_proposals": [],
                "assumptions": [],
                "unresolved_questions": [],
                "expansion_notes": [],
            },
        )
        bytes_type = next(item for item in result["candidate"]["types"] if item["name"] == "mqtt_bytes_t")
        self.assertEqual(bytes_type["lifecycle"]["created_by"], [])
        self.assertEqual(bytes_type["lifecycle"]["freed_by"], ["mqtt_bytes_free"])
        obligation_names = [name for item in result["type_obligations"]["obligations"] for name in item["required_function_names"]]
        self.assertNotIn("mqtt_encode_*", obligation_names)
        self.assertNotIn("caller", obligation_names)
        self.assertIn("mqtt_bytes_free", obligation_names)
        self.assertFalse(_has_error(validate_type_inventory_candidate(result["candidate"], [module], draft)))
        context_obligations = derive_type_obligations({"type_inventory": [bytes_type], "module_artifacts": [module]}, module)
        self.assertNotIn("create", {item["action"] for item in context_obligations})
        release = next(item for item in context_obligations if item["action"] == "release_owned_data")
        self.assertIn("mqtt_bytes_free", release["required_function_names"])
        self.assertNotIn("mqtt_free", release["required_function_names"])

        inherited_bytes_type = copy.deepcopy(bytes_type)
        inherited_bytes_type["lifecycle"]["created_by"] = ["mqtt_encode_*"]
        inherited_obligations = derive_type_obligations({"type_inventory": [inherited_bytes_type], "module_artifacts": [module]}, module)
        inherited_names = [name for item in inherited_obligations for name in item["required_function_names"]]
        self.assertNotIn("create", {item["action"] for item in inherited_obligations})
        self.assertNotIn("mqtt_encode_*", inherited_names)

    def test_type_function_reference_reconciliation_removes_dangling_encode_refs(self) -> None:
        type_item = {
            "type_id": "type:protocol_codec:mqtt_protocol_codec",
            "name": "mqtt_protocol_codec",
            "module_id": "protocol_codec",
            "kind": "struct",
            "visibility": "module_internal",
            "defined_in": "internal_header",
            "purpose": "codec state",
            "fields": [],
            "enum_values": [],
            "callback_signature": {"return_type": "", "params": []},
            "ownership_lifetime": "",
            "lifecycle": {"created_by": [], "initialized_by": ["mqtt_encode_init"], "destroyed_by": ["mqtt_encode_cleanup"], "freed_by": []},
            "related_functions": ["mqtt_encode_cleanup"],
            "dependencies": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        draft = {
            "type_inventory": [type_item],
            "function_contracts": [
                _inventory_function("mqtt_protocol_codec_init", "protocol_codec", kind="resource_lifecycle"),
                _inventory_function("mqtt_protocol_codec_cleanup", "protocol_codec", kind="resource_lifecycle"),
            ],
        }
        reconciled = reconcile_type_inventory_function_refs(draft)
        lifecycle = reconciled["type_inventory"][0]["lifecycle"]
        self.assertEqual(lifecycle["initialized_by"], ["mqtt_protocol_codec_init"])
        self.assertEqual(lifecycle["destroyed_by"], ["mqtt_protocol_codec_cleanup"])
        self.assertEqual(reconciled["type_inventory"][0]["related_functions"], ["mqtt_protocol_codec_cleanup"])

    def test_function_planning_space_keeps_transport_boundary_byte_oriented(self) -> None:
        planning_ir = self._minimal_mqtt_message_ir()
        network = {
            "module_id": "network",
            "name": "network",
            "role": "TCP network epoll server reads bytes and hands buffers to protocol_codec decoder",
            "dependencies": ["protocol_codec"],
            "artifacts": [
                {"name": "mqtt_network_run", "kind": "FUNC", "role": "Run epoll event loop"},
                {"name": "mqtt_network_read", "kind": "FUNC", "role": "Read transport bytes"},
                {"name": "mqtt_network_send", "kind": "FUNC", "role": "Send transport bytes"},
            ],
            "state_owned": ["socket set"],
            "owned_capabilities": ["transport_io"],
            "files": [],
            "doc_ref": [],
        }
        codec = {
            "module_id": "protocol_codec",
            "name": "protocol_codec",
            "role": "MQTT protocol codec parser serializer",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_decode", "kind": "FUNC", "role": "Decode MQTT packets"},
                {"name": "mqtt_encode", "kind": "FUNC", "role": "Encode MQTT packets"},
            ],
            "state_owned": [],
            "owned_capabilities": ["message_decode", "message_encode"],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [network, codec], "handler_matrix": [], "type_inventory": [], "canonical_types": []}
        space = build_function_planning_space(draft, network, planning_ir)
        names = {seed["name"] for group in ("parser_serializer_function_seeds", "recommended_function_families") for seed in space[group]}
        self.assertFalse(space["parser_serializer_function_seeds"])
        self.assertFalse(any("_decode_message" in name or "_encode_message" in name for name in names))
        self.assertFalse(any("_read_wire_field" in name or "_write_wire_field" in name for name in names))
        self.assertTrue(any("_read_into_buffer" in name or "_flush_output" in name for name in names))

    def test_codec_function_planning_space_adds_field_covered_helpers(self) -> None:
        planning_ir = self._minimal_mqtt_message_ir()
        codec = {
            "module_id": "protocol_codec",
            "name": "protocol_codec",
            "role": "MQTT protocol codec parser serializer",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_decode", "kind": "FUNC", "role": "Decode MQTT packet bytes"},
                {"name": "mqtt_encode", "kind": "FUNC", "role": "Encode MQTT packet bytes"},
            ],
            "state_owned": [],
            "owned_capabilities": ["message_decode", "message_encode"],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [codec], "handler_matrix": [], "traceability": {"required_capabilities": ["message_decode", "message_encode"]}, "type_inventory": [], "canonical_types": []}
        profile = {"required_capabilities": [{"capability_id": "message_decode"}, {"capability_id": "message_encode"}]}
        space = build_function_planning_space(draft, codec, planning_ir)
        codec_seeds = space["parser_serializer_function_seeds"]
        self.assertTrue(any(seed["family"] == "message_or_command_specific_parser" and seed["covers_field_ids"] for seed in codec_seeds))
        self.assertTrue(any(seed["family"] == "primitive_reader_or_tokenizer" and seed["covers_field_ids"] for seed in codec_seeds))
        self.assertTrue(any(seed["family"] == "message_or_response_specific_encoder" and seed["covers_field_ids"] for seed in codec_seeds))
        candidate = reconcile_function_annotation_candidate(space, None)["candidate"]
        self.assertFalse(_has_error(validate_function_inventory_candidate(candidate, [codec], draft, profile, planning_ir)))

    def test_mqtt_codec_planning_space_uses_concrete_helpers_without_double_prefix(self) -> None:
        planning_ir = self._minimal_mqtt_message_ir()
        codec = {
            "module_id": "mqtt_codec",
            "name": "mqtt_codec",
            "role": "MQTT packet model decoder encoder",
            "dependencies": [],
            "artifacts": [
                {"name": "mqtt_decode", "kind": "FUNC", "role": "Decode MQTT packet bytes"},
                {"name": "mqtt_encode", "kind": "FUNC", "role": "Encode MQTT packet bytes"},
            ],
            "state_owned": [],
            "owned_capabilities": ["message_decode", "message_encode"],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [codec], "handler_matrix": [], "traceability": {"required_capabilities": ["message_decode", "message_encode"]}, "type_inventory": [], "canonical_types": []}
        space = build_function_planning_space(draft, codec, planning_ir)
        names = {seed["name"] for group in ("parser_serializer_function_seeds", "recommended_function_families") for seed in space[group]}
        self.assertFalse(any(name.startswith("mqtt_mqtt_codec_") for name in names))
        self.assertTrue(any("read_u16" in name for name in names))
        self.assertTrue(any("read_string" in name for name in names))
        self.assertTrue(any("remaining_length" in name for name in names))

    def test_function_reconciler_accepts_selected_concrete_optional_families(self) -> None:
        module = {
            "module_id": "router",
            "name": "router",
            "role": "resource registry, topic filter validation, lookup and match routing",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_router_match", "kind": "FUNC", "role": "Match topic filter"}],
            "files": [],
            "doc_ref": [],
        }
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "handler_matrix": [], "type_inventory": [], "canonical_types": []}
        space = build_function_planning_space(draft, module)
        allowed = set(space["optional_expansion_policy"]["allowed_families"])
        self.assertIn("lookup_or_match", allowed)
        self.assertIn("key_filter_path_validation", allowed)
        result = reconcile_function_annotation_candidate(
            space,
            {
                "schema_version": "function_annotation_candidate/v1",
                "candidate_id": "candidate:function_annotation:router",
                "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
                "module_id": "router",
                "seed_annotations": [],
                "optional_function_proposals": [
                    {
                        "proposal_key": "opt:router:find_match",
                        "name_hint": "mqtt_router_find_matching_subscription",
                        "function_kind": "internal_helper",
                        "coder_function_type": "ALGORITHM",
                        "visibility": "internal",
                        "api_surface": "module_internal",
                        "exported": False,
                        "export_reason": "",
                        "public_api_role": "",
                        "grouping_hint": "lookup_or_match",
                        "purpose": "Find subscriptions whose topic filters match the publish topic.",
                        "capability_ids": [],
                        "covers_handler_ids": [],
                        "covers_message_ids": [],
                        "covers_field_ids": [],
                        "family": "lookup_or_match",
                        "expansion_reason": "routing registry needs a concrete lookup helper beyond the public match facade",
                        "source_refs": ["decision:test:router:lookup"],
                        "trace_ref_keys": [],
                        "status": "inferred",
                    }
                ],
                "assumptions": [],
                "unresolved_questions": [],
                "decomposition_notes": [],
            },
        )
        self.assertFalse(result["reconciliation_report"]["rejected_optional_functions"])
        self.assertTrue(any(function["name"] == "mqtt_router_find_matching_subscription" for function in result["candidate"]["functions"]))
        self.assertFalse(_has_error(validate_function_inventory_candidate(result["candidate"], [module], draft, {}, {})))

    def test_function_inventory_quality_diagnostics_are_warnings_only(self) -> None:
        module = {
            "module_id": "codec",
            "name": "codec",
            "role": "codec helper boundary",
            "dependencies": [],
            "artifacts": [{"name": "caller", "kind": "FUNC", "role": "Compatibility placeholder"}],
            "files": [],
            "doc_ref": [],
        }
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:quality_warnings",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
            "module_id": "codec",
            "functions": [_inventory_function("caller", "codec")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {"module_artifacts": [module]}, {}, {})
        self.assertTrue(_has(diags, "non_function_lifecycle_name_in_inventory"))
        self.assertTrue(_has(diags, "function_inventory_missing_trace_refs"))
        self.assertFalse(_has_error(diags))

    def test_function_inventory_prompt_documents_seed_semantics_and_selected_hints_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            module = copy.deepcopy(draft["module_artifacts"][0])
            module["role"] = "incremental byte stream framing, command decoding, and response serialization"
            annotation_context = {
                "schema_version": "function_annotation_context/v1",
                "function_planning_space": build_function_planning_space(draft, module, planning_ir, profile, constraints),
            }
            payload = json.loads(function_annotation_candidate_messages(annotation_context)[1]["content"])
            rules_text = "\n".join(payload["semantic_validation_rules"])
            self.assertIn("function_planning_space seeds", rules_text)
            self.assertIn("do not generate a complete function inventory", rules_text)
            self.assertIn("Preserve every required seed identity", rules_text)
            decomposition = payload["function_annotation_context"]["function_planning_space"]["source_context"]["decomposition_context"]
            self.assertGreaterEqual(len(decomposition["selected_decomposition_hints"]), 1)
            self.assertLessEqual(len(decomposition["selected_decomposition_hints"]), 3)
            prompt_text = json.dumps(payload, ensure_ascii=False)
            selected_hints = set(decomposition["selected_decomposition_hints"])
            self.assertEqual(set(decomposition["selected_rule_ids"]), set(decomposition["expected_function_families_by_rule"]))
            self.assertEqual(set(decomposition["selected_rule_ids"]), set(decomposition["recommended_concrete_slots_by_rule"]))
            for rule in DECOMPOSITION_RULES:
                count = prompt_text.count(rule.hint)
                self.assertEqual(count, 1 if rule.hint in selected_hints else 0)
            annotation_payload = json.loads(
                function_annotation_candidate_messages(
                    {
                        "schema_version": "function_annotation_context/v1",
                        "function_planning_space": {"source_context": {"decomposition_context": decomposition}},
                    }
                )[1]["content"]
            )
            annotation_rules = "\n".join(annotation_payload["semantic_validation_rules"])
            self.assertIn("recommended_concrete_slots_by_rule", annotation_rules)
            self.assertIn("concrete implementation action", annotation_rules)
            self.assertIn("function_budget", annotation_rules)

    def test_function_budget_is_calibrated_from_specs_example_density(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            spaces = {
                str(module["module_id"]): build_function_planning_space(draft, module, planning_ir, profile, constraints)
                for module in draft["module_artifacts"]
            }
            self.assertEqual(spaces["transport_runtime"]["function_budget"]["example_baseline"]["baseline"], 28)
            self.assertEqual(spaces["protocol_codec"]["function_budget"]["example_baseline"]["baseline"], 16)
            self.assertEqual(spaces["mqtt_broker_app"]["function_budget"]["example_baseline"]["baseline"], 12)
            for space in spaces.values():
                budget = space["function_budget"]
                required = sum(
                    len(space[group])
                    for group in (
                        "mandatory_function_seeds",
                        "obligation_function_seeds",
                        "handler_function_seeds",
                        "parser_serializer_function_seeds",
                    )
                )
                self.assertEqual(budget["basis"], "specs-example/mqtt_specs")
                self.assertGreaterEqual(budget["module_soft_cap"], required)
                self.assertLessEqual(space["optional_expansion_policy"]["max_optional_functions"], 1)

    def test_function_annotation_reconciliation_rejects_optional_helpers_over_budget(self) -> None:
        module = {"module_id": "router", "name": "router", "role": "routing index", "dependencies": [], "artifacts": [], "files": [], "doc_ref": []}
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "handler_matrix": [], "traceability": {"required_capabilities": []}, "type_inventory": [], "canonical_types": []}
        space = build_function_planning_space(draft, module, {})
        space["optional_expansion_policy"]["max_optional_functions"] = 1
        proposal = {
            "function_kind": "internal_helper",
            "coder_function_type": "ALGORITHM",
            "visibility": "internal",
            "api_surface": "module_internal",
            "exported": False,
            "export_reason": "",
            "public_api_role": "",
            "grouping_hint": "lookup_or_match",
            "purpose": "Find matching entries.",
            "capability_ids": [],
            "covers_handler_ids": [],
            "covers_message_ids": [],
            "covers_field_ids": [],
            "family": "lookup_or_match",
            "expansion_reason": "routing lookup helper",
            "source_refs": ["decision:test:router"],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        result = reconcile_function_annotation_candidate(
            space,
            {
                "schema_version": "function_annotation_candidate/v1",
                "module_id": "router",
                "seed_annotations": [],
                "optional_function_proposals": [
                    {"proposal_key": "opt:router:first", "name_hint": "mqtt_router_first_match", **proposal},
                    {"proposal_key": "opt:router:second", "name_hint": "mqtt_router_second_match", **proposal},
                ],
                "assumptions": [],
                "unresolved_questions": [],
                "decomposition_notes": [],
            },
        )
        accepted = result["reconciliation_report"]["accepted_optional_functions"]
        rejected = result["reconciliation_report"]["rejected_optional_functions"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected[0]["reason"], "function_budget_exceeded")

    def test_signature_and_behavior_contexts_are_scoped_to_current_batch(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            first_module_id = str(draft["module_artifacts"][0]["module_id"])
            batch = [item for item in draft["function_contracts"] if item.get("module_id") == first_module_id][:4]
            signature_context = build_function_signature_context(draft, first_module_id, batch, batch_index=0, batch_size=12)
            behavior_context = build_function_behavior_context(draft, first_module_id, batch, constraints, batch_index=0, batch_size=8)

            self.assertNotIn("core_design_summary", signature_context)
            self.assertNotIn("core_design_summary", behavior_context)
            self.assertEqual(signature_context["batch"]["size"], 12)
            self.assertEqual(behavior_context["batch"]["size"], 8)
            self.assertEqual(set(signature_context["legal_id_universe"]["function_ids"]), {item["function_id"] for item in batch})
            self.assertIn("signature_style_guide", signature_context)
            self.assertIn("signature_type_table", signature_context)
            self.assertIn("global_public_symbol_names", signature_context)
            self.assertNotIn("current_module_type_inventory", signature_context)
            self.assertIn("module_state_access_policy", behavior_context)
            self.assertIn("allowed_required_capability_ids", behavior_context["service_requirement_policy"])

            signature_payload = json.loads(function_signature_patch_messages(signature_context)[1]["content"])
            behavior_payload = json.loads(function_behavior_contract_patch_messages(behavior_context)[1]["content"])
            self.assertIn("scoped signature context", signature_payload["task"])
            self.assertIn("scoped behavior context", behavior_payload["task"])
            self.assertIn("do not resolve service requirements into concrete call edges", behavior_payload["task"])
            self.assertTrue(any("memory_allocation" in item for item in behavior_payload["semantic_validation_rules"]))
            self.assertNotIn("function_contracts", json.dumps(signature_payload["function_signature_context"], ensure_ascii=False))
            self.assertNotIn("function_contracts", json.dumps(behavior_payload["function_behavior_context"], ensure_ascii=False))

    def test_behavior_context_keeps_signatures_and_trims_repeated_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, constraints, _, draft, _, _ = self._fixtures(Path(raw_tmp))
            module = draft["module_artifacts"][0]
            provider_module = draft["module_artifacts"][1]
            module["dependencies"] = [provider_module["module_id"]]
            first_module_id = str(module["module_id"])
            batch = [item for item in draft["function_contracts"] if item.get("module_id") == first_module_id][:2]
            batch[0]["capability_ids"] = ["transport_io"]
            constraints = copy.deepcopy(constraints)
            constraints["constraints"].extend(
                [
                    {"constraint_id": "constraint:global", "affected_capabilities": [], "obligation": "global", "severity": "must", "validation_rule": "global"},
                    {"constraint_id": "constraint:relevant", "affected_capabilities": ["transport_io"], "obligation": "relevant", "severity": "must", "validation_rule": "relevant"},
                    {"constraint_id": "constraint:irrelevant", "affected_capabilities": ["unrelated_capability"], "obligation": "irrelevant", "severity": "must", "validation_rule": "irrelevant"},
                ]
            )
            draft["type_inventory"].extend(
                [
                    {
                        "type_id": f"type:{first_module_id}:internal_kept",
                        "name": "internal_kept_t",
                        "module_id": first_module_id,
                        "kind": "internal_state",
                        "visibility": "internal",
                        "defined_in": "source_file",
                        "fields": [],
                        "enum_values": [],
                    },
                    {
                        "type_id": f"type:{first_module_id}:private_trimmed",
                        "name": "private_trimmed_t",
                        "module_id": first_module_id,
                        "kind": "internal_state",
                        "visibility": "private",
                        "defined_in": "source_file",
                        "fields": [],
                        "enum_values": [],
                    },
                ]
            )
            signature_ref = f"type:{first_module_id}:private_trimmed"
            batch[0]["signature"]["params"][0]["type_ref"] = signature_ref

            behavior_context = build_function_behavior_context(draft, first_module_id, batch, constraints, batch_index=0, batch_size=8)

            self.assertIn("signature", behavior_context["functions"][0])
            self.assertIn("params", behavior_context["functions"][0]["signature"])
            self.assertIn("return_type", behavior_context["functions"][0]["signature"])
            self.assertEqual(set(behavior_context["required_update_skeleton"][0]), {"function_id", "trace_ref_keys", "status"})
            self.assertTrue(behavior_context["provider_public_api_summary"])
            self.assertNotIn("signature", behavior_context["provider_public_api_summary"][0])
            constraint_ids = {item["constraint_id"] for item in behavior_context["engineering_constraints"]}
            self.assertIn("constraint:global", constraint_ids)
            self.assertIn("constraint:relevant", constraint_ids)
            self.assertNotIn("constraint:irrelevant", constraint_ids)
            type_ids = set(behavior_context["legal_id_universe"]["type_ids"])
            self.assertIn(signature_ref, type_ids)
            self.assertIn(f"type:{first_module_id}:internal_kept", type_ids)

            draft["type_inventory"][-1]["type_id"] = f"type:{first_module_id}:private_unreferenced"
            behavior_context = build_function_behavior_context(draft, first_module_id, batch, constraints, batch_index=0, batch_size=8)
            self.assertNotIn(f"type:{first_module_id}:private_unreferenced", set(behavior_context["legal_id_universe"]["type_ids"]))

    def test_function_behavior_normalizer_repairs_enum_and_external_state_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, constraints, _, draft, _, items = self._fixtures(Path(raw_tmp))
            local_update = copy.deepcopy(items["behavior"]["function_behavior_updates"][0])
            local_function = next(item for item in draft["function_contracts"] if item["function_id"] == local_update["function_id"])
            local_state = {"state_id": "state:test:own", "owner_module_id": local_function["module_id"]}
            external_state = {"state_id": "state:test:external", "owner_module_id": "external_owner"}
            draft["state_design"].extend([local_state, external_state])
            local_update["error_behavior"]["recovery"] = "log_only"
            local_update["service_requirements"] = [
                {
                    "service_requirement_id": "svc:req:test:log_only",
                    "requirement_kind": "external_runtime_service",
                    "operation": "log diagnostic",
                    "required_capability_ids": [],
                    "expected_inputs": [],
                    "expected_output": "none",
                    "failure_policy": "log_only",
                }
            ]
            local_update["state_access"] = [
                {"state_id": local_state["state_id"], "access_kind": "read_write", "required": True, "reason": "own state"},
                {"state_id": external_state["state_id"], "access_kind": "write", "required": True, "reason": "external write"},
                {"state_id": external_state["state_id"], "access_kind": "read_write", "required": True, "reason": "external read/write"},
            ]
            patch_candidate = {
                "schema_version": "function_behavior_contract_patch/v1",
                "patch_id": "patch:test:behavior",
                "producer": {"stage": "5.4c_behavior_contract", "prompt_name": "function_behavior_contract_patch_prompt", "prompt_version": "test"},
                "module_id": local_function["module_id"],
                "batch": {"index": 0, "size": 1},
                "function_behavior_updates": [local_update],
                "assumptions": [],
                "unresolved_questions": [],
            }

            normalized, stats = normalize_function_behavior_contract_patch(patch_candidate, draft, constraints)
            update = normalized["function_behavior_updates"][0]

            self.assertEqual(stats, {"enum_fixes": 2, "state_owner_fixes": 2})
            self.assertEqual(update["error_behavior"]["recovery"], "none")
            self.assertEqual(update["service_requirements"][0]["failure_policy"], "ignore")
            self.assertEqual({item["function_id"] for item in normalized["function_behavior_updates"]}, {local_update["function_id"]})
            self.assertTrue(any(item["state_id"] == local_state["state_id"] and item["access_kind"] == "read_write" for item in update["state_access"]))
            self.assertFalse(any(item["state_id"] == external_state["state_id"] and item["access_kind"] == "write" for item in update["state_access"]))
            self.assertTrue(any(item["state_id"] == external_state["state_id"] and item["access_kind"] == "read" for item in update["state_access"]))
            generated_requirements = [item for item in update["service_requirements"] if item["requirement_kind"] == "cross_module_service"]
            self.assertEqual(len(generated_requirements), 1)
            self.assertEqual(generated_requirements[0]["failure_policy"], "return_error")
            self.assertFalse(validate_function_behavior_contract_patch(normalized, draft, constraints, {local_update["function_id"]}))

    def test_5_4c_stage_candidate_uses_behavior_normalizer(self) -> None:
        plan_source = inspect.getsource(PlanningAgent.plan)
        self.assertIn("normalizer: Callable", plan_source)
        self.assertIn("normalize_function_behavior_contract_patch(candidate, draft, constraints)", plan_source)

    def test_stage_prompts_distinguish_local_ids_from_reference_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            module = draft["module_artifacts"][0]
            type_payload = json.loads(type_filling_candidate_messages(build_type_inventory_context(draft, module, planning_ir))[1]["content"])
            function_payload = json.loads(function_annotation_candidate_messages(build_function_inventory_context(draft, module))[1]["content"])
            combined = json.dumps([type_payload, function_payload], ensure_ascii=False)

            self.assertNotIn("Use only IDs present in the context legal ID universe.", combined)
            self.assertTrue(any("candidate:type_filling:{module_id}" in rule for rule in type_payload["local_id_rules"]))
            self.assertTrue(any("slot_fillings[].slot_id" in rule for rule in type_payload["local_id_rules"]))
            self.assertTrue(any("candidate:function_annotation:{module_id}" in rule for rule in function_payload["local_id_rules"]))
            self.assertTrue(any("seed_annotations[].seed_id" in rule for rule in function_payload["local_id_rules"]))
            self.assertTrue(any("Reference IDs must come from" in rule for rule in type_payload["hard_validation_rules"]))
            type_rules = "\n".join(type_payload["semantic_validation_rules"])
            self.assertIn("deterministic type_planning_space", type_rules)
            self.assertIn("Preserve every mandatory, derived, and recommended slot identity", type_rules)
            self.assertIn("optional_type_proposals", type_rules)

    def test_function_inventory_coverage_scoring_thresholds(self) -> None:
        module = {
            "module_id": "generic_unit",
            "name": "generic_unit",
            "role": "incremental byte stream framing, command decoding, and response serialization",
            "dependencies": [],
            "artifacts": [{"name": "proto_decode", "kind": "FUNC", "role": "Decode command stream"}],
            "files": [],
            "doc_ref": [],
        }
        selected = select_top_decomposition_hints(module, {"core_design_summary": {}}, max_hints=3)
        families = [
            family
            for rule_id in selected["selected_rule_ids"]
            for family in selected["expected_function_families_by_rule"][rule_id]
        ]

        def candidate_with(count: int) -> dict:
            functions = [_inventory_function("proto_decode", module["module_id"], kind="parser")]
            functions.extend(
                _inventory_function(
                    f"proto_{family}",
                    module["module_id"],
                    function_id=f"fn:{module['module_id']}:{family}",
                    kind="internal_helper",
                    public=False,
                    purpose=f"Cover {family} responsibility.",
                )
                for family in families[:count]
            )
            return {
                "schema_version": "function_inventory_candidate/v2",
                "candidate_id": "candidate:test:coverage",
                "producer": {"stage": "5.4a_function_inventory", "prompt_name": "function_annotation_candidate_prompt", "prompt_version": "test"},
                "module_id": module["module_id"],
                "functions": functions,
                "assumptions": [],
                "unresolved_questions": [],
            }

        high = function_inventory_decomposition_report(candidate_with(len(families)), [module], {})
        self.assertGreaterEqual(high["coverage_score"], 0.65)
        self.assertFalse(high["repair_required"])

        low = function_inventory_decomposition_report(candidate_with(0), [module], {})
        self.assertLess(low["coverage_score"], 0.45)
        self.assertTrue(low["repair_required"])

        middle = next(
            report
            for count in range(1, len(families))
            for report in [function_inventory_decomposition_report(candidate_with(count), [module], {})]
            if 0.45 <= report["coverage_score"] < 0.65
        )
        self.assertGreaterEqual(middle["coverage_score"], 0.45)
        self.assertLess(middle["coverage_score"], 0.65)
        self.assertFalse(middle["repair_required"])
        self.assertTrue(middle["warning"])

    def test_stage_prompts_expose_semantic_validator_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            first_module = draft["module_artifacts"][0]
            first_module_id = str(first_module["module_id"])
            first_functions = [item for item in draft["function_contracts"] if item.get("module_id") == first_module_id][:4]
            annotation_context = {
                "schema_version": "function_annotation_context/v1",
                "function_planning_space": build_function_planning_space(draft, first_module, planning_ir, profile, constraints),
            }
            file_layout_baseline = fallback_file_layout(draft)
            prompt_payloads = [
                json.loads(core_design_candidate_messages(build_core_design_context(planning_ir, profile, constraints, selected))[1]["content"]),
                json.loads(module_artifacts_candidate_messages(build_module_artifact_context(draft, profile, constraints, selected))[1]["content"]),
                json.loads(type_filling_candidate_messages(build_type_inventory_context(draft, first_module))[1]["content"]),
                json.loads(function_annotation_candidate_messages(build_function_inventory_context(draft, first_module))[1]["content"]),
                json.loads(function_annotation_candidate_messages(annotation_context)[1]["content"]),
                json.loads(function_signature_patch_messages(build_function_signature_context(draft, first_module_id, first_functions, batch_index=0, batch_size=8))[1]["content"]),
                json.loads(function_behavior_contract_patch_messages(build_function_behavior_context(draft, first_module_id, first_functions, constraints, batch_index=0, batch_size=4))[1]["content"]),
                json.loads(wire_access_binding_patch_messages(build_wire_access_binding_context(draft, planning_ir))[1]["content"]),
                json.loads(calls_allowed_candidate_messages(build_calls_allowed_context(draft, selected))[1]["content"]),
                json.loads(file_layout_candidate_messages(build_file_layout_context(draft, planning_ir, constraints))[1]["content"]),
                json.loads(file_layout_override_patch_messages(build_file_layout_override_context(draft, file_layout_baseline, []))[1]["content"]),
                json.loads(runtime_entrypoint_candidate_messages(build_runtime_entrypoint_context(draft, planning_ir, selected))[1]["content"]),
                json.loads(dependency_repair_patch_messages(build_dependency_repair_context(draft, [{"code": "dependency_cycle", "message": "cycle"}]))[1]["content"]),
            ]
            for payload in prompt_payloads:
                self.assertIn("semantic_validation_rules", payload)
                self.assertIn("id_reference_rules", payload)
                self.assertIn("enum_usage_rules", payload)
                self.assertTrue(payload["semantic_validation_rules"])
            payload_by_schema = {payload["output_schema"]: payload for payload in prompt_payloads}
            joined_rules = "\n".join(
                rule
                for payload in prompt_payloads
                for rule in payload["semantic_validation_rules"] + payload["enum_usage_rules"] + payload["id_reference_rules"]
            )
            self.assertIn("handler_matrix[].trigger must exactly equal that surface", joined_rules)
            self.assertIn("Preserve every required seed identity", joined_rules)
            self.assertIn("signature.name must match the existing function name", joined_rules)
            self.assertIn("named public callback_type", joined_rules)
            self.assertIn("callback/event/visitor outputs as ABI type boundaries", joined_rules)
            self.assertIn("service_requirements may describe needed operations", joined_rules)
            self.assertIn("concrete implementation boundaries", joined_rules)
            self.assertIn("invariants_used should name concrete invariants", joined_rules)
            self.assertIn("role-aware units", joined_rules)
            self.assertIn("Parser and serializer functions must not write state", joined_rules)
            self.assertIn("callee_function_id must come from candidate_provider_functions.provider_function_ids", joined_rules)
            self.assertIn("exports_type_ids may contain only IDs copied from file_layout_context.public_exportable_type_ids", joined_rules)
            self.assertIn("This patch may only adjust an existing deterministic file layout baseline", joined_rules)
            self.assertIn("entrypoint_signature should normally be int main", joined_rules)
            self.assertIn("must be copied exactly from legal_id_universe.file_ids or files[].file_id", joined_rules)
            self.assertIn("behavior_quality_policy", payload_by_schema["function_behavior_contract_patch/v1"]["function_behavior_context"])
            self.assertIn("candidate_provider_functions", payload_by_schema["calls_allowed_candidate/v2"]["calls_allowed_context"])
            self.assertIn("module_file_layout_summaries", payload_by_schema["file_layout_candidate/v2"]["file_layout_context"])
            self.assertIn("public_exportable_type_ids", payload_by_schema["file_layout_candidate/v2"]["file_layout_context"])
            self.assertIn("public_exportable_types_by_module", payload_by_schema["file_layout_candidate/v2"]["file_layout_context"])
            self.assertTrue(payload_by_schema["file_layout_candidate/v2"]["file_layout_context"]["file_split_policy"]["split_by_role_when_non_trivial"])
            self.assertIn("baseline_files", payload_by_schema["file_layout_override_patch/v1"]["file_layout_override_context"])
            self.assertIn("allowed_file_ids_by_module", payload_by_schema["file_layout_override_patch/v1"]["file_layout_override_context"]["override_policy"])

    def test_valid_candidate_merges_and_invalid_candidate_is_not_merged(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, _, _, items = self._fixtures(Path(raw_tmp))
            draft = build_plan_skeleton(planning_ir, profile, constraints, selected)
            self.assertFalse(validate_core_design_candidate(items["core"], planning_ir, profile, selected, constraints))
            merged = merge_core_design(draft, items["core"])
            self.assertTrue(merged["state_design"])
            invalid = copy.deepcopy(items["core"])
            invalid["dependency_graph"] = {}
            self.assertTrue(validate_core_design_candidate(invalid, planning_ir, profile, selected, constraints))
            self.assertEqual(draft["state_design"], [])

    def test_file_layout_uses_source_header_pair_file_units(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            layout = items["layout"]
            self.assertEqual(layout["schema_version"], "file_layout_candidate/v2")
            for file_item in layout["files"]:
                self.assertEqual(file_item["kind"], "source_header_pair")
                self.assertEqual(file_item["file_id"], f"file:{file_item['source_path'].removesuffix('.c')}")
                self.assertFalse(file_item["file_id"].startswith("header:"))
                for imported in file_item["imports_allowed"]:
                    self.assertTrue(imported.startswith("file:"))
                    self.assertFalse(imported.startswith("header:"))
                    self.assertFalse(imported.endswith((".h", ".c")))
            final_file_ids = {item["file_id"] for item in draft["file_layout"]["files"]}
            self.assertTrue(final_file_ids)
            for file_item in draft["file_layout"]["files"]:
                self.assertFalse(file_item["file_id"].startswith("header:"))
                self.assertTrue(set(file_item["imports_allowed"]).issubset(final_file_ids))

    def test_file_layout_fallback_generates_role_aware_src_file_ids(self) -> None:
        functions = [
            _inventory_function("mqtt_tcp_server_listen", "network_io", function_id="fn:network_io:listen", kind="public_api", purpose="Listen and accept TCP server sockets"),
            _inventory_function("mqtt_connection_read", "network_io", function_id="fn:network_io:read", kind="internal_helper", public=False, purpose="Read bytes from a client connection"),
            _inventory_function("mqtt_packet_free", "mqtt_codec", function_id="fn:mqtt_codec:packet_free", kind="resource_lifecycle", purpose="Free packet model storage"),
            _inventory_function("mqtt_decode_packet", "mqtt_codec", function_id="fn:mqtt_codec:decode", kind="parser", purpose="Decode MQTT packet bytes"),
            _inventory_function("mqtt_codec_read_u16", "mqtt_codec", function_id="fn:mqtt_codec:read_u16", kind="internal_helper", public=False, purpose="Read and decode a uint16 field"),
            _inventory_function("mqtt_encode_packet", "mqtt_codec", function_id="fn:mqtt_codec:encode", kind="serializer", purpose="Encode MQTT packet bytes"),
            _inventory_function("mqtt_codec_write_u16", "mqtt_codec", function_id="fn:mqtt_codec:write_u16", kind="internal_helper", public=False, purpose="Write and encode a uint16 field"),
        ]
        draft = {
            "module_artifacts": [
                {"module_id": "network_io", "role": "TCP server and connection IO", "owned_capabilities": [], "dependencies": []},
                {"module_id": "mqtt_codec", "role": "MQTT packet model decoder encoder", "owned_capabilities": [], "dependencies": []},
            ],
            "function_contracts": functions,
            "canonical_types": [],
            "type_inventory": [],
        }
        layout = fallback_file_layout(draft)
        file_ids = {item["file_id"] for item in layout["files"]}
        self.assertIn("file:src/network_io/tcp_server", file_ids)
        self.assertIn("file:src/network_io/connection", file_ids)
        self.assertIn("file:src/mqtt_codec/packet", file_ids)
        self.assertIn("file:src/mqtt_codec/decoder", file_ids)
        self.assertIn("file:src/mqtt_codec/encoder", file_ids)
        for file_item in layout["files"]:
            self.assertEqual(file_item["file_id"], f"file:{file_item['source_path'].removesuffix('.c')}")
            self.assertTrue(file_item["source_path"].startswith("src/"))
            self.assertTrue(file_item["header_path"].startswith("src/"))

        exported_by_function = {
            function_id: file_item["file_id"]
            for file_item in layout["files"]
            for function_id in file_item["exports_function_ids"]
        }
        self.assertIn("fn:mqtt_codec:decode", exported_by_function)
        self.assertNotIn("fn:mqtt_codec:read_u16", exported_by_function)
        self.assertFalse(_has_error(validate_file_layout_candidate(layout, draft)))

    def test_file_layout_override_patch_is_bounded_to_existing_same_module_units(self) -> None:
        functions = [
            _inventory_function("mqtt_connection_read", "network_io", function_id="fn:network_io:read", kind="internal_helper", public=False, purpose="Read client connection bytes"),
            _inventory_function("mqtt_packet_free", "mqtt_codec", function_id="fn:mqtt_codec:packet_free", kind="resource_lifecycle", purpose="Free packet model storage"),
            _inventory_function("mqtt_decode_packet", "mqtt_codec", function_id="fn:mqtt_codec:decode", kind="parser", purpose="Decode packet bytes"),
            _inventory_function("mqtt_codec_read_u16", "mqtt_codec", function_id="fn:mqtt_codec:read_u16", kind="internal_helper", public=False, purpose="Read and decode uint16"),
        ]
        draft = {
            "module_artifacts": [
                {"module_id": "network_io", "role": "connection IO", "owned_capabilities": [], "dependencies": []},
                {"module_id": "mqtt_codec", "role": "packet decoder", "owned_capabilities": [], "dependencies": []},
            ],
            "function_contracts": functions,
            "canonical_types": [],
            "type_inventory": [],
        }
        baseline = fallback_file_layout(draft)
        decoder_file_id = "file:src/mqtt_codec/decoder"
        packet_file_id = "file:src/mqtt_codec/packet"
        valid_patch = {
            "schema_version": "file_layout_override_patch/v1",
            "patch_id": "patch:test:layout_override",
            "producer": {"stage": "5.5a_file_layout", "prompt_name": "file_layout_override_patch_prompt", "prompt_version": "test"},
            "keep_baseline": False,
            "force_single_unit_module_ids": [],
            "file_responsibility_overrides": [{"file_id": decoder_file_id, "responsibility": "Decode packets and nearby read helpers.", "trace_ref_keys": ["trace:test"]}],
            "function_reassignments": [{"function_id": "fn:mqtt_codec:packet_free", "target_file_id": decoder_file_id, "reason": "keep packet cleanup close to decoder ownership"}],
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertFalse(validate_file_layout_override_patch(valid_patch, baseline, draft))
        applied = apply_file_layout_override_patch(baseline, valid_patch, draft)
        assignments = {item["function_id"]: item["implementation_file_id"] for item in applied["function_file_assignments"]}
        self.assertEqual(assignments["fn:mqtt_codec:packet_free"], decoder_file_id)
        self.assertFalse(_has_error(validate_file_layout_candidate(applied, draft)))

        invalid_patch = copy.deepcopy(valid_patch)
        invalid_patch["file_responsibility_overrides"] = [{"file_id": "file:src/mqtt_codec/missing", "responsibility": "bad", "trace_ref_keys": []}]
        invalid_patch["function_reassignments"] = [
            {"function_id": "fn:mqtt_codec:missing", "target_file_id": decoder_file_id, "reason": "bad"},
            {"function_id": "fn:network_io:read", "target_file_id": packet_file_id, "reason": "cross module move"},
        ]
        diags = validate_file_layout_override_patch(invalid_patch, baseline, draft)
        self.assertTrue(_has(diags, "override_unknown_file"))
        self.assertTrue(_has(diags, "override_unknown_function"))
        self.assertTrue(_has(diags, "override_cross_module_reassignment"))

    def test_file_layout_imports_same_module_public_type_owner_file(self) -> None:
        packet_type = {
            "type_id": "type:mqtt_codec:packet",
            "name": "mqtt_packet_t",
            "kind": "struct",
            "owner_module_id": "mqtt_codec",
            "source_message_ids": [],
            "source_field_ids": [],
            "fields": [{"field_name": "packet_type", "field_type": "uint8_t", "required": True, "source_field_id": "", "validation_notes": ""}],
            "enum_values": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        decode = _inventory_function("mqtt_decode_packet", "mqtt_codec", function_id="fn:mqtt_codec:decode", kind="parser", purpose="Decode packet bytes")
        encode = _inventory_function("mqtt_encode_packet", "mqtt_codec", function_id="fn:mqtt_codec:encode", kind="serializer", purpose="Encode packet bytes")
        encode["signature_dependencies"] = [
            {
                "symbol_name": "mqtt_packet_t",
                "symbol_kind": "type",
                "type_ref": packet_type["type_id"],
                "owner_module_id": "mqtt_codec",
                "dependency_scope": "header",
                "reason": "public packet parameter",
            }
        ]
        draft = {
            "module_artifacts": [{"module_id": "mqtt_codec", "role": "MQTT packet decoder encoder", "owned_capabilities": [], "dependencies": []}],
            "function_contracts": [decode, encode],
            "canonical_types": [packet_type],
            "type_inventory": [],
        }
        layout = fallback_file_layout(draft)
        files = {item["file_id"]: item for item in layout["files"]}
        self.assertIn("type:mqtt_codec:packet", files["file:src/mqtt_codec/decoder"]["exports_type_ids"])
        self.assertIn("file:src/mqtt_codec/decoder", files["file:src/mqtt_codec/encoder"]["imports_allowed"])
        self.assertFalse(_has_error(validate_file_layout_candidate(layout, draft)))

    def test_file_layout_prefers_owner_core_file_for_module_handle_type(self) -> None:
        session_type = {
            "type_id": "type:session:mqtt_session_t",
            "name": "mqtt_session_t",
            "kind": "opaque",
            "owner_module_id": "session",
            "source_message_ids": [],
            "source_field_ids": [],
            "fields": [],
            "enum_values": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        router = _inventory_function("mqtt_session_dispatch", "session", function_id="fn:session:dispatch", kind="handler", purpose="Route session messages")
        create = _inventory_function("mqtt_session_create", "session", function_id="fn:session:create", kind="resource_lifecycle", purpose="Create session")
        draft = {
            "module_artifacts": [{"module_id": "session", "role": "session router and session state", "owned_capabilities": [], "dependencies": []}],
            "function_contracts": [router, create],
            "canonical_types": [session_type],
            "type_inventory": [],
        }

        layout = fallback_file_layout(draft)
        files = {item["file_id"]: item for item in layout["files"]}

        self.assertIn("type:session:mqtt_session_t", files["file:src/session/session"]["exports_type_ids"])
        self.assertNotIn("type:session:mqtt_session_t", files["file:src/session/router"]["exports_type_ids"])

    def test_file_layout_override_is_not_actionable_for_single_file_warning_only(self) -> None:
        functions = [
            _inventory_function("proto_public", "custom", function_id="fn:custom:public", kind="public_api"),
            _inventory_function("proto_handle", "custom", function_id="fn:custom:handle", kind="handler", public=False),
            _inventory_function("proto_helper", "custom", function_id="fn:custom:helper", kind="internal_helper", public=False),
            _inventory_function("proto_validate", "custom", function_id="fn:custom:validate", kind="validator", public=False),
            _inventory_function("proto_cleanup", "custom", function_id="fn:custom:cleanup", kind="resource_lifecycle", public=False),
            _inventory_function("proto_state", "custom", function_id="fn:custom:state", kind="state_machine", public=False),
        ]
        draft = {
            "module_artifacts": [{"module_id": "custom", "role": "custom module", "owned_capabilities": [], "dependencies": []}],
            "function_contracts": functions,
            "canonical_types": [],
            "type_inventory": [],
        }
        layout = fallback_file_layout(draft)
        diags = validate_file_layout_candidate(layout, draft)
        self.assertTrue(_has(diags, "mechanical_single_file_module_layout"))
        self.assertFalse(_file_layout_override_actionable(layout, diags))

    def test_dependency_graph_does_not_treat_state_access_as_compile_edge(self) -> None:
        plan = {
            "state_design": [
                {"state_id": "state:a", "owner_module_id": "a"},
                {"state_id": "state:b", "owner_module_id": "b"},
            ],
            "file_layout": {
                "files": [
                    {"file_id": "file:a/a", "module_id": "a", "imports_allowed": [], "header_path": "a/a.h"},
                    {"file_id": "file:b/b", "module_id": "b", "imports_allowed": [], "header_path": "b/b.h"},
                ]
            },
            "function_contracts": [
                {"function_id": "fn:a:read_b", "module_id": "a", "file_id": "file:a/a", "state_access": [{"state_id": "state:b", "access_kind": "read"}], "calls_allowed": [], "signature_dependencies": []},
                {"function_id": "fn:b:read_a", "module_id": "b", "file_id": "file:b/b", "state_access": [{"state_id": "state:a", "access_kind": "read"}], "calls_allowed": [], "signature_dependencies": []},
            ],
        }
        graph = derive_dependency_graph(plan)
        self.assertFalse([edge for edge in graph["module_edges"] if edge["kind"] == "state_access"])
        self.assertNotIn("state_access", graph["derived_from"])

    def test_dependency_validation_rejects_call_contracts_without_calls_allowed(self) -> None:
        caller = _inventory_function("parse", "codec", function_id="fn:codec:parse", kind="parser")
        callee = _inventory_function("read_u8", "codec", function_id="fn:codec:read_u8", kind="internal_helper", public=False)
        caller["file_id"] = "file:codec/parser"
        callee["file_id"] = "file:codec/parser"
        caller["calls_allowed"] = []
        caller["call_contracts"] = [_call_edge(callee["function_id"], [])]
        callee["calls_allowed"] = []
        callee["call_contracts"] = []
        plan = {
            "module_artifacts": [{"module_id": "codec"}],
            "file_layout": {
                "files": [
                    {"file_id": "file:codec/parser", "module_id": "codec", "imports_allowed": [], "header_path": "codec/parser.h"},
                ]
            },
            "function_contracts": [caller, callee],
        }
        plan["dependency_graph"] = derive_dependency_graph(plan)

        diagnostics = validate_dependency_graph(plan)
        self.assertTrue(_has(diagnostics, "dependency_call_contracts_without_calls_allowed"))
        self.assertTrue(_has(diagnostics, "dependency_call_contract_not_allowed"))
        self.assertTrue(_has(diagnostics, "dependency_graph_missing_function_edges"))

    def test_dependency_validation_rejects_signature_dependencies_without_imports(self) -> None:
        function = _inventory_function("encode", "codec", function_id="fn:codec:encode", kind="serializer")
        function["file_id"] = "file:codec/encoder"
        function["calls_allowed"] = []
        function["call_contracts"] = []
        function["signature_dependencies"] = [
            {
                "symbol_name": "shared_packet_t",
                "symbol_kind": "type",
                "type_ref": "type:shared:packet",
                "owner_module_id": "shared",
                "dependency_scope": "header",
                "reason": "public encode signature uses shared packet type",
            }
        ]
        plan = {
            "module_artifacts": [{"module_id": "codec"}, {"module_id": "shared"}],
            "file_layout": {
                "files": [
                    {"file_id": "file:codec/encoder", "module_id": "codec", "imports_allowed": [], "header_path": "codec/encoder.h"},
                    {"file_id": "file:shared/types", "module_id": "shared", "imports_allowed": [], "header_path": "shared/types.h", "exports_type_ids": ["type:shared:packet"]},
                ]
            },
            "function_contracts": [function],
        }
        plan["dependency_graph"] = derive_dependency_graph(plan)

        diagnostics = validate_dependency_graph(plan)
        self.assertTrue(_has(diagnostics, "dependency_signature_missing_import"))

    def test_dependency_validation_accepts_consistent_call_and_signature_graph(self) -> None:
        caller = _inventory_function("encode", "codec", function_id="fn:codec:encode", kind="serializer")
        callee = _inventory_function("shared_validate", "shared", function_id="fn:shared:validate", kind="validator")
        caller["file_id"] = "file:codec/encoder"
        callee["file_id"] = "file:shared/types"
        caller["calls_allowed"] = [callee["function_id"]]
        caller["call_contracts"] = [_call_edge(callee["function_id"], [])]
        caller["signature_dependencies"] = [
            {
                "symbol_name": "shared_packet_t",
                "symbol_kind": "type",
                "type_ref": "type:shared:packet",
                "owner_module_id": "shared",
                "dependency_scope": "header",
                "reason": "public encode signature uses shared packet type",
            }
        ]
        callee["calls_allowed"] = []
        callee["call_contracts"] = []
        callee["signature_dependencies"] = []
        plan = {
            "module_artifacts": [{"module_id": "codec"}, {"module_id": "shared"}],
            "file_layout": {
                "files": [
                    {"file_id": "file:codec/encoder", "module_id": "codec", "imports_allowed": ["file:shared/types"], "header_path": "codec/encoder.h"},
                    {"file_id": "file:shared/types", "module_id": "shared", "imports_allowed": [], "header_path": "shared/types.h", "exports_type_ids": ["type:shared:packet"]},
                ]
            },
            "function_contracts": [caller, callee],
        }
        plan["dependency_graph"] = derive_dependency_graph(plan)

        diagnostics = validate_dependency_graph(plan)
        self.assertFalse(_has_error(diagnostics), [diag.__dict__ for diag in diagnostics])

    def test_dependency_validation_allows_same_module_unexported_signature_dependency(self) -> None:
        function = _inventory_function("parse", "codec", function_id="fn:codec:parse", kind="parser")
        function["file_id"] = "file:codec/parser"
        function["calls_allowed"] = []
        function["call_contracts"] = []
        function["signature_dependencies"] = [
            {
                "symbol_name": "codec_cursor_t",
                "symbol_kind": "type",
                "type_ref": "type:codec:cursor",
                "owner_module_id": "codec",
                "dependency_scope": "source",
                "reason": "source-local parser cursor",
            }
        ]
        plan = {
            "module_artifacts": [{"module_id": "codec"}],
            "file_layout": {
                "files": [
                    {"file_id": "file:codec/common", "module_id": "codec", "imports_allowed": [], "header_path": "codec/common.h", "exports_type_ids": []},
                    {"file_id": "file:codec/parser", "module_id": "codec", "imports_allowed": [], "header_path": "codec/parser.h", "exports_type_ids": []},
                ]
            },
            "function_contracts": [function],
        }
        plan["dependency_graph"] = derive_dependency_graph(plan)

        diagnostics = validate_dependency_graph(plan)
        self.assertFalse(_has(diagnostics, "dependency_signature_missing_import"), [diag.__dict__ for diag in diagnostics])
        self.assertFalse(_has(diagnostics, "dependency_signature_unresolved_provider_file"), [diag.__dict__ for diag in diagnostics])

    def test_dependency_cycle_check_ignores_source_call_cycles_but_rejects_signature_cycles(self) -> None:
        plan = {
            "module_artifacts": [{"module_id": "a"}, {"module_id": "b"}],
            "file_layout": {
                "files": [
                    {"file_id": "file:a/a", "module_id": "a", "imports_allowed": [], "header_path": "a/a.h"},
                    {"file_id": "file:b/b", "module_id": "b", "imports_allowed": [], "header_path": "b/b.h"},
                ]
            },
            "function_contracts": [],
            "dependency_graph": {
                "schema_version": "dependency_graph/v1",
                "module_edges": [
                    {"from": "a", "to": "b", "kind": "function_call"},
                    {"from": "b", "to": "a", "kind": "function_call"},
                ],
                "file_edges": [
                    {"from": "file:a/a", "to": "file:b/b", "kind": "function_call"},
                    {"from": "file:b/b", "to": "file:a/a", "kind": "function_call"},
                ],
                "function_edges": [],
            },
        }
        diagnostics = validate_dependency_graph(plan)
        self.assertFalse(_has(diagnostics, "dependency_cycle"), [diag.__dict__ for diag in diagnostics])

        plan["dependency_graph"]["file_edges"][0]["kind"] = "signature_dependency"
        plan["dependency_graph"]["file_edges"][1]["kind"] = "signature_dependency"
        diagnostics = validate_dependency_graph(plan)
        self.assertTrue(_has(diagnostics, "dependency_cycle"))

    def test_dependency_fallback_preserves_inputs_and_marks_blocking(self) -> None:
        draft = {
            "function_contracts": [
                {"function_id": "fn:codec:parse", "calls_allowed": ["fn:codec:read_u8"]},
                {"function_id": "fn:codec:read_u8", "calls_allowed": []},
            ],
            "file_layout": {
                "files": [
                    {"file_id": "file:codec/parser", "imports_allowed": ["file:codec/primitives"]},
                    {"file_id": "file:codec/primitives", "imports_allowed": []},
                ]
            },
            "unresolved_questions": [],
        }
        result = apply_deterministic_dependency_fallback(
            draft,
            [{"code": "dependency_cycle", "message": "dependency cycle", "path": "plan.json"}],
        )
        self.assertEqual(result["function_contracts"][0]["calls_allowed"], ["fn:codec:read_u8"])
        self.assertEqual(result["file_layout"]["files"][0]["imports_allowed"], ["file:codec/primitives"])
        self.assertTrue(any(item.get("blocking") for item in result["unresolved_questions"] if isinstance(item, dict)))
        self.assertEqual(result["unresolved_questions"][-1]["dependency_error_code"], "dependency_cycle")

    def test_file_layout_allows_multiple_source_header_pairs_per_module_and_warns_on_mechanical_layout(self) -> None:
        functions = [
            _inventory_function("mqtt_packet_free", "codec", function_id="fn:codec:packet_free", kind="resource_lifecycle", public=True),
            _inventory_function("mqtt_decode", "codec", function_id="fn:codec:decode", kind="parser", public=True),
            _inventory_function("mqtt_codec_read_u16", "codec", function_id="fn:codec:read_u16", kind="parser", public=False),
            _inventory_function("mqtt_codec_read_string", "codec", function_id="fn:codec:read_string", kind="parser", public=False),
            _inventory_function("mqtt_encode", "codec", function_id="fn:codec:encode", kind="serializer", public=True),
            _inventory_function("mqtt_codec_put_u16", "codec", function_id="fn:codec:put_u16", kind="serializer", public=False),
        ]
        draft = {
            "module_artifacts": [{"module_id": "codec", "role": "packet model decoder encoder", "owned_capabilities": ["message_decode", "message_encode"], "dependencies": []}],
            "function_contracts": functions,
            "canonical_types": [],
            "type_inventory": [],
        }

        def file_item(source: str, exports: list[str], implements: list[str], imports: list[str] | None = None) -> dict:
            return {
                "file_id": f"file:{source.removesuffix('.c')}",
                "source_path": source,
                "header_path": source.removesuffix(".c") + ".h",
                "module_id": "codec",
                "kind": "source_header_pair",
                "responsibility": source,
                "exports_function_ids": exports,
                "implements_function_ids": implements,
                "exports_type_ids": [],
                "imports_allowed": imports or [],
                "trace_ref_keys": [],
                "status": "inferred",
            }

        files = [
            file_item("codec/packet.c", ["fn:codec:packet_free"], ["fn:codec:packet_free"]),
            file_item("codec/decoder.c", ["fn:codec:decode"], ["fn:codec:decode", "fn:codec:read_u16", "fn:codec:read_string"], ["file:codec/packet"]),
            file_item("codec/encoder.c", ["fn:codec:encode"], ["fn:codec:encode", "fn:codec:put_u16"], ["file:codec/packet"]),
        ]
        assignments = []
        for file in files:
            for function_id in file["implements_function_ids"]:
                function = next(item for item in functions if item["function_id"] == function_id)
                public = function.get("exported")
                assignments.append(
                    {
                        "function_id": function_id,
                        "implementation_file_id": file["file_id"],
                        "declaration_file_id": file["file_id"] if public else "",
                        "visibility": "public" if public else "internal",
                        "reason": "clustered by codec role",
                        "status": "inferred",
                    }
                )
        layout = {
            "schema_version": "file_layout_candidate/v2",
            "candidate_id": "candidate:test:layout",
            "producer": {"stage": "5.5a_file_layout", "prompt_name": "file_layout_candidate_prompt", "prompt_version": "test"},
            "files": files,
            "function_file_assignments": assignments,
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertFalse(validate_file_layout_candidate(layout, draft))

        single_file = copy.deepcopy(layout)
        single_file["files"] = [file_item("codec/codec.c", ["fn:codec:packet_free", "fn:codec:decode", "fn:codec:encode"], [function["function_id"] for function in functions])]
        for assignment in single_file["function_file_assignments"]:
            assignment["implementation_file_id"] = "file:codec/codec"
            if assignment["declaration_file_id"]:
                assignment["declaration_file_id"] = "file:codec/codec"
        self.assertTrue(_has(validate_file_layout_candidate(single_file, draft), "mechanical_single_file_module_layout"))

        context = build_file_layout_context(draft, {}, {})
        self.assertIn("module_file_layout_summaries", context)
        self.assertIn("decoder", context["module_file_layout_summaries"][0]["recommended_file_units"])

    def test_signature_validator_allows_system_types_and_rejects_wrong_namespace_refs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["signature"]["params"])
            update["signature"]["params"][0]["type_ref"] = "size_t"
            self.assertFalse(validate_function_signature_patch(signature, draft))
            update["signature"]["params"][0]["type_ref"] = "state:session_state"
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "invalid_signature_param_type_ref_namespace"))
            update["signature"]["params"][0]["type_ref"] = "made_up_type"
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "unknown_signature_param_type_ref"))

    def test_signature_validator_uses_type_inventory_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            signature = copy.deepcopy(items["signature"])
            update = next(item for item in signature["function_signature_updates"] if item["signature"]["params"])
            module_id = str(signature["module_id"])
            public_handle = next(item for item in draft["type_inventory"] if item["module_id"] == module_id and item["kind"] == "opaque_handle")
            private_state = copy.deepcopy(public_handle)
            private_state.update(
                {
                    "type_id": f"type:{module_id}:private_state_for_signature",
                    "name": "struct private_state_for_signature",
                    "kind": "internal_state",
                    "visibility": "private",
                    "defined_in": "source_file",
                }
            )
            draft["type_inventory"].append(private_state)
            param = update["signature"]["params"][0]
            param["type"] = "struct private_state_for_signature *"
            param["type_ref"] = private_state["type_id"]
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_private_type"))
            param["type"] = f"{public_handle['name']} *"
            param["type_ref"] = public_handle["type_id"]
            self.assertFalse(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_private_type"))
            param["type"] = public_handle["name"]
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_opaque_by_value"))
            param["type"] = f"{public_handle['name']} *"
            update["signature"]["return_type"] = public_handle["name"]
            update["signature"]["raw"] = f"{public_handle['name']} {update['signature']['name']}({public_handle['name']} *{param['name']})"
            self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_signature_returns_opaque_by_value"))

            plan = copy.deepcopy(draft)
            plan["function_contracts"] = copy.deepcopy(draft["function_contracts"])
            target_function = next(function for function in plan["function_contracts"] if function["function_id"] == update["function_id"])
            target_function["signature"] = copy.deepcopy(update["signature"])
            target_function["signature"]["params"] = [{"name": "handle", "type": public_handle["name"], "type_ref": public_handle["type_id"], "nullable": False, "ownership": "BORROWED"}]
            target_function["signature"]["return_type"] = "void"
            target_function["signature"]["raw"] = f"void {target_function['name']}({public_handle['name']} handle)"
            self.assertTrue(_has(validate_full_implementation_plan(plan, profile={}, planning_ir={}), "readiness_public_signature_opaque_by_value"))

    def test_signature_validator_rejects_public_anonymous_callback_pointer(self) -> None:
        public_function = _inventory_function("mqtt_router_match", "router", public=True)
        draft = {"protocol_name": "mqtt", "module_artifacts": [{"module_id": "router"}], "function_contracts": [public_function], "canonical_types": [], "type_inventory": []}
        signature = fallback_function_signatures(draft, "router", [public_function])
        param = signature["function_signature_updates"][0]["signature"]["params"][0]
        param.update({"name": "callback", "type": "void (*)(uint32_t, void*)", "type_ref": "", "passing_mode": "by_pointer"})
        self.assertTrue(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_anonymous_callback_pointer"))

        private_function = _inventory_function("mqtt_router_match_impl", "router", public=False)
        draft["function_contracts"] = [private_function]
        signature = fallback_function_signatures(draft, "router", [private_function])
        param = signature["function_signature_updates"][0]["signature"]["params"][0]
        param.update({"name": "callback", "type": "void (*)(uint32_t, void*)", "type_ref": "", "passing_mode": "by_pointer"})
        self.assertFalse(_has(validate_function_signature_patch(signature, draft), "public_signature_uses_anonymous_callback_pointer"))

    def test_behavior_service_requirements_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, constraints, _, draft, _, items = self._fixtures(Path(raw_tmp))
            draft.setdefault("traceability", {})["required_capabilities"] = ["transport_io"]
            behavior = copy.deepcopy(items["behavior"])
            behavior["function_behavior_updates"][0]["service_requirements"] = [
                {
                    "service_requirement_id": "srv:test_runtime",
                    "requirement_kind": "external_runtime_service",
                    "operation": "read socket",
                    "required_capability_ids": [],
                    "expected_inputs": [],
                    "expected_output": "bytes",
                    "failure_policy": "return_error",
                },
                {
                    "service_requirement_id": "srv:test_cross",
                    "requirement_kind": "cross_module_service",
                    "operation": "ask another module",
                    "required_capability_ids": [],
                    "expected_inputs": [],
                    "expected_output": "status",
                    "failure_policy": "return_error",
                },
            ]
            self.assertFalse(validate_function_behavior_contract_patch(behavior, draft, constraints))
            behavior["function_behavior_updates"][0]["service_requirements"][0]["required_capability_ids"] = ["memory_allocation"]
            self.assertTrue(_has(validate_function_behavior_contract_patch(behavior, draft, constraints), "unknown_service_requirement_capability"))
            behavior["function_behavior_updates"][0]["service_requirements"][0]["required_capability_ids"] = ["transport_io"]
            self.assertFalse(validate_function_behavior_contract_patch(behavior, draft, constraints))
            behavior["function_behavior_updates"][0]["service_requirements"][0].pop("requirement_kind")
            self.assertTrue(_has(validate_function_behavior_contract_patch(behavior, draft, constraints), "missing_required_field"))

    def test_event_behavior_contract_preserves_coder_event_type_and_warns_on_missing_invariants(self) -> None:
        event_function = _inventory_function("mqtt_network_on_data_cb", "network", function_id="fn:network:on_data_cb", kind="handler", public=False, purpose="Handle readable connection callback")
        draft = {
            "module_artifacts": [{"module_id": "network"}],
            "function_contracts": [event_function],
            "state_design": [],
            "error_strategy": [],
            "traceability": {"required_capabilities": []},
        }
        update = {
            "function_id": event_function["function_id"],
            "contract": {
                "input": "Connection pointer and readable event payload.",
                "action": "Read bytes from the connection and dispatch complete frames to protocol handling.",
                "output": "Returns callback status through the event response path.",
                "preconditions": ["connection fd is readable"],
                "postconditions": ["input buffer position advances"],
                "invariants_used": ["connection fd ownership remains with network module"],
                "idempotent": False,
                "thread_safety": "single_thread_only",
            },
            "event_contract": {
                "trigger": "epoll readable event",
                "precondition": "connection is open",
                "input": "fd readable notification",
                "action": "read and dispatch received bytes",
                "state_change": "receive buffer may grow or compact",
                "response": "continue, close, or return error",
                "event_type": "ON_DATA",
            },
            "error_behavior": {"error_ids": [], "propagation": "return_code", "recovery": "close_connection", "return_policy": "status_code"},
            "state_access": [],
            "resource_access": [],
            "internal_type_refs": [],
            "service_requirements": [],
            "logic_kind": "EVENT",
            "forbidden_symbols": [],
            "trace_ref_keys": [],
            "status": "inferred",
        }
        patch_candidate = {
            "schema_version": "function_behavior_contract_patch/v1",
            "patch_id": "patch:test:event",
            "producer": {"stage": "5.4c_behavior_contract", "prompt_name": "function_behavior_contract_patch_prompt", "prompt_version": "test"},
            "module_id": "network",
            "batch": {"index": 0, "size": 1},
            "function_behavior_updates": [copy.deepcopy(update)],
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertFalse(validate_function_behavior_contract_patch(patch_candidate, draft, {}))
        merged = merge_function_behavior(draft, patch_candidate)
        merged_function = merged["function_contracts"][0]
        self.assertEqual(merged_function["logic_kind"], "EVENT")
        self.assertEqual(merged_function["coder_function_type"], "EVENT")

        patch_candidate["function_behavior_updates"][0]["contract"]["invariants_used"] = []
        self.assertTrue(_has(validate_function_behavior_contract_patch(patch_candidate, draft, {}), "behavior_missing_invariants"))

    def test_calls_allowed_context_is_cross_module_scoped(self) -> None:
        caller = _inventory_function(
            "mqtt_session_handle_publish",
            "session",
            function_id="fn:session:handle_publish",
            kind="handler",
            purpose="Process inbound PUBLISH and route it to subscribers",
        )
        caller["service_requirements"] = [
            {
                "service_requirement_id": "srv:session:route_publish",
                "requirement_kind": "cross_module_service",
                "operation": "route_publish",
                "expected_inputs": ["publish packet"],
                "expected_output": "route status",
                "failure_policy": "return_error",
            }
        ]
        local_serializer = _inventory_function("mqtt_session_serialize_ack", "session", function_id="fn:session:serialize_ack", kind="serializer", public=False, purpose="Serialize local ACK response")
        local_cleanup = _inventory_function("mqtt_session_cleanup", "session", function_id="fn:session:cleanup", kind="resource_lifecycle", public=False, purpose="Cleanup session resources")
        provider = _inventory_function("mqtt_topic_router_route_publish", "topic_router", function_id="fn:topic_router:route_publish", kind="handler", purpose="Route publish packets to topic subscribers")
        unrelated_provider = _inventory_function("mqtt_timer_arm", "timer_service", function_id="fn:timer_service:arm", kind="public_api", purpose="Arm timer events")
        draft = {
            "module_artifacts": [{"module_id": "session"}, {"module_id": "topic_router"}, {"module_id": "timer_service"}],
            "function_contracts": [caller, local_serializer, local_cleanup, provider, unrelated_provider],
        }
        selected = {"architecture": {"modules": draft["module_artifacts"]}}
        context = build_calls_allowed_context(draft, selected, "session", [caller, local_serializer, local_cleanup], batch_index=0, batch_size=3)
        self.assertEqual([item["function_id"] for item in context["callers"]], [caller["function_id"]])
        self.assertEqual([item["function_id"] for item in context["service_requirements"]], [caller["function_id"]])
        self.assertEqual([item["function_id"] for item in context["callable_functions"]], [provider["function_id"]])
        providers = context["candidate_provider_functions"][0]["provider_function_ids"]
        self.assertEqual(providers, [provider["function_id"]])
        self.assertLessEqual(len(providers), 4)
        self.assertNotIn(local_serializer["function_id"], {item["function_id"] for item in context["callable_functions"]})

    def test_calls_allowed_normalizer_merges_baseline_and_cross_module_edges(self) -> None:
        caller = _inventory_function(
            "mqtt_session_handle_publish",
            "session",
            function_id="fn:session:handle_publish",
            kind="handler",
            purpose="Process inbound PUBLISH and route it to subscribers",
        )
        caller["service_requirements"] = [
            {
                "service_requirement_id": "srv:session:route_publish",
                "requirement_kind": "cross_module_service",
                "operation": "route_publish",
                "expected_inputs": ["publish packet"],
                "expected_output": "route status",
                "failure_policy": "return_error",
            }
        ]
        serializer = _inventory_function("mqtt_session_serialize_ack", "session", function_id="fn:session:serialize_ack", kind="serializer", public=False, purpose="Serialize response after handling publish")
        provider = _inventory_function("mqtt_topic_router_route_publish", "topic_router", function_id="fn:topic_router:route_publish", kind="handler", purpose="Route publish packets to subscribers")
        draft = {
            "module_artifacts": [{"module_id": "session"}, {"module_id": "topic_router"}],
            "function_contracts": [caller, serializer, provider],
        }
        selected = {"architecture": {"modules": draft["module_artifacts"]}}
        fallback = fallback_calls_allowed(draft, [caller, serializer], batch_index=0, batch_size=2)
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:cross",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [{"caller_function_id": caller["function_id"], "calls_allowed": [_call_edge(provider["function_id"], ["srv:session:route_publish"])]}],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        normalized, stats = normalize_calls_allowed_candidate(
            candidate,
            draft,
            {caller["function_id"], serializer["function_id"]},
            {"srv:session:route_publish"},
            {provider["function_id"]},
            fallback,
        )
        updates = {item["caller_function_id"]: item for item in normalized["call_updates"]}
        caller_callees = {edge["callee_function_id"] for edge in updates[caller["function_id"]]["calls_allowed"]}
        self.assertIn(serializer["function_id"], caller_callees)
        self.assertIn(provider["function_id"], caller_callees)
        self.assertEqual(normalized["unresolved_service_requirements"], [])
        self.assertGreater(stats["missing_call_updates_filled"], 0)
        self.assertFalse(
            validate_calls_allowed_candidate(
                normalized,
                draft,
                selected,
                expected_caller_ids={caller["function_id"], serializer["function_id"]},
                expected_service_requirement_ids={"srv:session:route_publish"},
                callable_function_ids={provider["function_id"]},
            )
        )

    def test_calls_allowed_normalizer_keeps_cross_module_service_unresolved_for_same_module_edge(self) -> None:
        caller = _inventory_function("mqtt_session_handle_publish", "session", function_id="fn:session:handle_publish", kind="handler")
        caller["service_requirements"] = [
            {
                "service_requirement_id": "srv:session:route_publish",
                "requirement_kind": "cross_module_service",
                "operation": "route_publish",
                "expected_inputs": [],
                "expected_output": "status",
                "failure_policy": "return_error",
            }
        ]
        same_module = _inventory_function("mqtt_session_serialize_ack", "session", function_id="fn:session:serialize_ack", kind="serializer", public=False)
        draft = {"module_artifacts": [{"module_id": "session"}], "function_contracts": [caller, same_module]}
        fallback = fallback_calls_allowed(draft, [caller], batch_index=0, batch_size=1)
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:same_module_service",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [{"caller_function_id": caller["function_id"], "calls_allowed": [_call_edge(same_module["function_id"], ["srv:session:route_publish"])]}],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        normalized, stats = normalize_calls_allowed_candidate(
            candidate,
            draft,
            {caller["function_id"]},
            {"srv:session:route_publish"},
            set(),
            fallback,
        )
        self.assertGreater(stats["invalid_call_edges_dropped"], 0)
        self.assertEqual(normalized["unresolved_service_requirements"], ["srv:session:route_publish"])
        self.assertFalse(
            validate_calls_allowed_candidate(
                normalized,
                draft,
                {"architecture": {"modules": draft["module_artifacts"]}},
                expected_caller_ids={caller["function_id"]},
                expected_service_requirement_ids={"srv:session:route_publish"},
            )
        )

    def test_calls_allowed_scoped_validation_and_callable_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, _, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            functions = draft["function_contracts"]
            caller = functions[0]
            same_module_callee = next(item for item in functions if item["module_id"] == caller["module_id"] and item["function_id"] != caller["function_id"])
            other_module_callee = next(item for item in functions if item["module_id"] != caller["module_id"])
            caller["service_requirements"] = [
                {
                    "service_requirement_id": "srv:test_cross",
                    "requirement_kind": "cross_module_service",
                    "operation": "test cross-module need",
                    "required_capability_ids": [],
                    "expected_inputs": [],
                    "expected_output": "status",
                    "failure_policy": "return_error",
                }
            ]
            candidate = {
                "schema_version": "calls_allowed_candidate/v2",
                "candidate_id": "candidate:test:scoped",
                "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
                "call_updates": [{"caller_function_id": caller["function_id"], "calls_allowed": [_call_edge(same_module_callee["function_id"], ["srv:test_cross"])]}],
                "unresolved_service_requirements": [],
                "assumptions": [],
                "unresolved_questions": [],
            }
            self.assertTrue(
                _has(
                    validate_calls_allowed_candidate(
                        candidate,
                        draft,
                        selected,
                        expected_caller_ids={caller["function_id"]},
                        expected_service_requirement_ids={"srv:test_cross"},
                        callable_function_ids=set(),
                    ),
                    "cross_module_service_bound_to_same_module_call",
                )
            )
            candidate["call_updates"].append({"caller_function_id": same_module_callee["function_id"], "calls_allowed": []})
            self.assertTrue(
                _has(
                    validate_calls_allowed_candidate(candidate, draft, selected, expected_caller_ids={caller["function_id"]}, expected_service_requirement_ids={"srv:test_cross"}),
                    "calls_allowed_batch_coverage_mismatch",
                )
            )
            candidate["call_updates"] = [{"caller_function_id": caller["function_id"], "calls_allowed": [_call_edge(other_module_callee["function_id"], ["srv:test_cross"])]}]
            self.assertTrue(
                _has(
                    validate_calls_allowed_candidate(
                        candidate,
                        draft,
                        selected,
                        expected_caller_ids={caller["function_id"]},
                        expected_service_requirement_ids={"srv:test_cross"},
                        callable_function_ids=set(),
                    ),
                    "call_not_in_callable_universe",
                )
            )
            same_module_callee["visibility"] = "static"
            same_module_callee["api_surface"] = "static_helper"
            helper_edge = _call_edge(same_module_callee["function_id"], [])
            helper_edge["call_kind"] = "utility"
            candidate["call_updates"] = [{"caller_function_id": caller["function_id"], "calls_allowed": [helper_edge]}]
            candidate["unresolved_service_requirements"] = ["srv:test_cross"]
            self.assertFalse(
                validate_calls_allowed_candidate(
                    candidate,
                    draft,
                    selected,
                    expected_caller_ids={caller["function_id"]},
                    expected_service_requirement_ids={"srv:test_cross"},
                )
            )

    def test_calls_allowed_context_and_aggregate_normalizer_break_reciprocal_lifecycle_cycle(self) -> None:
        public_close = _inventory_function(
            "mqtt_connection_close",
            "network",
            function_id="fn:network:mqtt_connection_close",
            kind="public_api",
            public=True,
            purpose="Terminate client connection",
        )
        internal_close = _inventory_function(
            "mqtt_network_close_connection",
            "network",
            function_id="fn:network:mqtt_network_close_connection",
            kind="resource_lifecycle",
            public=False,
            purpose="Close and detach a connection or endpoint record",
        )
        draft = {
            "module_artifacts": [{"module_id": "network"}],
            "function_contracts": [public_close, internal_close],
        }
        selected = {"architecture": {"modules": [{"module_id": "network"}]}}
        context = build_calls_allowed_context(draft, selected, "network", [public_close, internal_close], batch_index=0, batch_size=16)
        payload = json.loads(calls_allowed_candidate_messages(context)[1]["content"])
        self.assertTrue(context["call_graph_direction_hints"])
        self.assertIn("only cross-module service call contracts", payload["task"])
        self.assertTrue(any("deterministic normalization will merge your edges with the baseline" in item for item in payload["semantic_validation_rules"]))

        def lifecycle_edge(callee: str) -> dict:
            edge = _call_edge(callee, [])
            edge["call_kind"] = "lifecycle"
            edge["failure_behavior"] = "ignore"
            return edge

        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:cycle",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [
                {"caller_function_id": public_close["function_id"], "calls_allowed": [lifecycle_edge(internal_close["function_id"])]},
                {"caller_function_id": internal_close["function_id"], "calls_allowed": [lifecycle_edge(public_close["function_id"])]},
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertTrue(_has(validate_calls_allowed_candidate(candidate, draft, selected), "calls_allowed_cycle"))
        normalized, stats = normalize_calls_allowed_aggregate(candidate, draft)
        self.assertEqual(stats["aggregate_cycle_edges_removed"], 1)
        self.assertFalse(validate_calls_allowed_candidate(normalized, draft, selected))
        updates = {item["caller_function_id"]: item for item in normalized["call_updates"]}
        self.assertEqual([edge["callee_function_id"] for edge in updates[public_close["function_id"]]["calls_allowed"]], [internal_close["function_id"]])
        self.assertEqual(updates[internal_close["function_id"]]["calls_allowed"], [])

    def test_calls_allowed_validator_rejects_param_binding_count_mismatch(self) -> None:
        caller = _inventory_function("caller", "codec", function_id="fn:codec:caller", kind="handler")
        callee = _inventory_function("callee", "codec", function_id="fn:codec:callee", kind="parser")
        callee["signature"] = {
            "return_type": "int",
            "name": "callee",
            "params": [{"name": "packet", "type": "mqtt_packet_t *"}, {"name": "len", "type": "size_t"}],
            "raw": "int callee(mqtt_packet_t *packet, size_t len)",
        }
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:param_mismatch",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [
                {
                    "caller_function_id": caller["function_id"],
                    "calls_allowed": [
                        {
                            **_call_edge(callee["function_id"], []),
                            "param_bindings": [{"param_name": "packet", "value_ref": "packet", "ownership": "borrowed", "nullability": "nullable"}],
                        }
                    ],
                }
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        draft = {"module_artifacts": [{"module_id": "codec"}], "function_contracts": [caller, callee]}
        self.assertTrue(_has(validate_calls_allowed_candidate(candidate, draft, {"architecture": {"modules": [{"module_id": "codec"}]}}), "call_contract_param_count_mismatch"))

    def test_calls_allowed_aggregate_prefers_route_over_delivery_back_edge(self) -> None:
        session_process = _inventory_function(
            "mqtt_session_process_publish",
            "session",
            function_id="fn:session:mqtt_session_process_publish",
            kind="handler",
            purpose="Process inbound PUBLISH packet for a client session",
        )
        session_process["service_requirements"] = [
            {
                "service_requirement_id": "sr:session:route_publish",
                "requirement_kind": "cross_module_service",
                "operation": "route_message",
                "expected_inputs": ["session", "publish packet"],
                "expected_output": "Delivery status",
                "failure_policy": "return_error",
            }
        ]
        topic_dispatch = _inventory_function(
            "mqtt_topic_router_dispatch",
            "topic_router",
            function_id="fn:topic_router:mqtt_topic_router_dispatch",
            kind="handler",
            purpose="Route PUBLISH to subscribers",
        )
        topic_dispatch["service_requirements"] = [
            {
                "service_requirement_id": "req:topic_router:dispatch:session_delivery",
                "requirement_kind": "cross_module_service",
                "operation": "deliver_publish_to_session",
                "expected_inputs": ["session pointer", "PUBLISH packet"],
                "expected_output": "Delivery status",
                "failure_policy": "return_error",
            }
        ]
        draft = {
            "module_artifacts": [{"module_id": "session"}, {"module_id": "topic_router"}],
            "function_contracts": [session_process, topic_dispatch],
        }
        selected = {"architecture": {"modules": draft["module_artifacts"]}}
        context = build_calls_allowed_context(draft, selected, "session", [session_process], batch_index=0, batch_size=16)
        payload = json.loads(calls_allowed_candidate_messages(context)[1]["content"])
        self.assertTrue(context["service_cycle_risk_hints"])
        self.assertTrue(any("outbound delivery requirement" in item for item in payload["semantic_validation_rules"]))

        route_edge = _call_edge(topic_dispatch["function_id"], ["sr:session:route_publish"])
        route_edge["call_reason"] = "Routes PUBLISH packet to matching subscribers"
        route_edge["status"] = "supported"
        delivery_edge = _call_edge(session_process["function_id"], ["req:topic_router:dispatch:session_delivery"])
        delivery_edge["call_reason"] = "Deliver matched PUBLISH to each subscriber session"
        delivery_edge["status"] = "supported"
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:publish_cycle",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [
                {"caller_function_id": session_process["function_id"], "calls_allowed": [route_edge]},
                {"caller_function_id": topic_dispatch["function_id"], "calls_allowed": [delivery_edge]},
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        self.assertTrue(_has(validate_calls_allowed_candidate(candidate, draft, selected), "calls_allowed_cycle"))
        normalized, stats = normalize_calls_allowed_aggregate(candidate, draft)
        self.assertEqual(stats["aggregate_cycle_edges_removed"], 1)
        self.assertEqual(stats["aggregate_removed_service_requirements_closed"], 1)
        self.assertFalse(validate_calls_allowed_candidate(normalized, draft, selected))
        updates = {item["caller_function_id"]: item for item in normalized["call_updates"]}
        self.assertEqual([edge["callee_function_id"] for edge in updates[session_process["function_id"]]["calls_allowed"]], [topic_dispatch["function_id"]])
        self.assertEqual(updates[topic_dispatch["function_id"]]["calls_allowed"], [])
        self.assertEqual(normalized["unresolved_service_requirements"], ["req:topic_router:dispatch:session_delivery"])

    def test_calls_allowed_aggregate_closes_service_ids_removed_with_cycle_edge(self) -> None:
        alpha = _inventory_function("alpha_route", "alpha", function_id="fn:alpha:alpha_route", kind="handler", purpose="Route request to beta")
        beta = _inventory_function("beta_route", "beta", function_id="fn:beta:beta_route", kind="handler", purpose="Route request to alpha")
        alpha["service_requirements"] = [{"service_requirement_id": "srv:alpha:beta", "requirement_kind": "cross_module_service", "operation": "route_beta"}]
        beta["service_requirements"] = [{"service_requirement_id": "srv:beta:alpha", "requirement_kind": "cross_module_service", "operation": "route_alpha"}]
        draft = {
            "module_artifacts": [{"module_id": "alpha"}, {"module_id": "beta"}],
            "function_contracts": [alpha, beta],
        }
        selected = {"architecture": {"modules": draft["module_artifacts"]}}
        candidate = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:test:generic_cycle",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
            "call_updates": [
                {"caller_function_id": alpha["function_id"], "calls_allowed": [_call_edge(beta["function_id"], ["srv:alpha:beta"])]},
                {"caller_function_id": beta["function_id"], "calls_allowed": [_call_edge(alpha["function_id"], ["srv:beta:alpha"])]},
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        normalized, stats = normalize_calls_allowed_aggregate(candidate, draft)
        self.assertEqual(stats["aggregate_cycle_edges_removed"], 1)
        self.assertEqual(stats["aggregate_removed_service_requirements_closed"], 1)
        self.assertFalse(validate_calls_allowed_candidate(normalized, draft, selected))
        self.assertEqual(len(normalized["unresolved_service_requirements"]), 1)
        self.assertIn(normalized["unresolved_service_requirements"][0], {"srv:alpha:beta", "srv:beta:alpha"})

    def test_calls_allowed_fallback_generates_internal_contracts(self) -> None:
        draft = {
            "module_artifacts": [{"module_id": "codec"}],
            "function_contracts": [
                _inventory_function("codec_public", "codec", function_id="fn:codec:public", kind="public_api"),
                _inventory_function("parse_frame", "codec", function_id="fn:codec:parse", kind="parser", public=False),
                _inventory_function("serialize_frame", "codec", function_id="fn:codec:serialize", kind="serializer", public=False),
                _inventory_function("handle_frame", "codec", function_id="fn:codec:handle", kind="handler", public=False),
                _inventory_function("read_u8", "codec", function_id="fn:codec:read_u8", kind="internal_helper", public=False),
                _inventory_function("write_u8", "codec", function_id="fn:codec:write_u8", kind="internal_helper", public=False),
                _inventory_function("cleanup_frame", "codec", function_id="fn:codec:cleanup", kind="resource_lifecycle", public=False),
                _inventory_function("destroy_codec", "codec", function_id="fn:codec:destroy", kind="resource_lifecycle"),
                _inventory_function("free_codec_items", "codec", function_id="fn:codec:free_items", kind="internal_helper", public=False),
            ],
        }
        candidate = fallback_calls_allowed(draft)
        self.assertFalse(validate_calls_allowed_candidate(candidate, draft, {"architecture": {"modules": [{"module_id": "codec"}]}}))
        updates = {item["caller_function_id"]: item for item in candidate["call_updates"]}
        self.assertTrue(any(edge["callee_function_id"] == "fn:codec:read_u8" for edge in updates["fn:codec:parse"]["calls_allowed"]))
        self.assertTrue(any(edge["callee_function_id"] == "fn:codec:write_u8" for edge in updates["fn:codec:serialize"]["calls_allowed"]))
        self.assertTrue(any(edge["callee_function_id"] == "fn:codec:serialize" for edge in updates["fn:codec:handle"]["calls_allowed"]))
        self.assertTrue(any(edge["callee_function_id"] == "fn:codec:free_items" for edge in updates["fn:codec:destroy"]["calls_allowed"]))
        self.assertTrue(all(edge["service_requirement_ids"] == [] for update in updates.values() for edge in update["calls_allowed"]))

        merged = merge_calls_allowed(draft, candidate)
        caller = next(item for item in merged["function_contracts"] if item["function_id"] == "fn:codec:handle")
        self.assertIn("fn:codec:serialize", caller["calls_allowed"])

    def test_wire_access_fallback_keeps_field_specific_targets_and_types(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            wire_context = build_wire_access_binding_context(draft, planning_ir)
            wire_payload = json.loads(wire_access_binding_patch_messages(wire_context)[1]["content"])
            self.assertEqual(wire_context["wire_binding_policy"]["parser_serializer_access_kind"], "read")
            self.assertTrue(wire_context["wire_binding_policy"]["required_field_ids"])
            self.assertTrue(any("access_kind" in item and "read" in item for item in wire_payload["semantic_validation_rules"]))
            wire = items["wire"]
            self.assertFalse(any(entry.get("c_type") == "unknown" for entry in wire["access_path_entries"]))
            connect_entries = [
                entry for entry in wire["wire_mapping_entries"]
                if "connect" in str(entry.get("packet_name", "")).lower()
            ]
            for entry in connect_entries:
                self.assertNotEqual(entry.get("target_path"), "fixed_header.packet_type")
                self.assertIn(str(entry.get("wire_field", "")).lower().replace(" ", "_"), str(entry.get("target_path", "")).lower())

            merged = merge_wire_access_binding(draft, wire)
            access_by_id = {entry["access_path_id"]: entry for entry in merged["access_path_table"]}
            wire_by_id = {entry["wire_mapping_id"]: entry for entry in wire["wire_mapping_entries"]}
            table_by_field = {entry["field_id"]: entry for entry in merged["wire_mapping_table"]}
            for function in merged["function_contracts"]:
                mappings = function.get("wire_mapping", [])
                if len(mappings) < 2:
                    continue
                access_ids = [mapping["access_path_id"] for mapping in mappings]
                self.assertGreater(len(set(access_ids)), 1)
                for mapping in mappings:
                    access = access_by_id[mapping["access_path_id"]]
                    source = wire_by_id[mapping["mapping_id"]]
                    self.assertEqual(access["field_id"], mapping["field_id"])
                    self.assertEqual(mapping["strategy"], source["strategy"])
                    self.assertEqual(mapping["target_path"], access["path"])
                    self.assertEqual(mapping["rule"], source["rule"])
                    self.assertEqual(table_by_field[mapping["field_id"]]["rule"], source["rule"])
                break
            else:
                self.fail("expected at least one codec function with multiple wire mappings")

            forbidden = copy.deepcopy(wire)
            target_function_id = merged["function_contracts"][0]["function_id"]
            forbidden["forbidden_symbols"] = [f"{target_function_id}|FIELD|pkt->data|wrong union name", "not parseable"]
            merged = merge_wire_access_binding(draft, forbidden)
            target_function = next(item for item in merged["function_contracts"] if item["function_id"] == target_function_id)
            self.assertIn({"NAME": "pkt->data", "KIND": "FIELD", "REASON": "wrong union name"}, target_function["forbidden_symbols"])
            self.assertTrue(any("Unable to attach forbidden symbol" in item.get("question", "") for item in merged["unresolved_questions"]))

            broken = copy.deepcopy(wire)
            broken["access_path_entries"][0]["c_type"] = "unknown"
            self.assertTrue(_has(validate_wire_access_binding_patch(broken, draft, planning_ir), "unknown_coder_access_path_type"))

            broken = copy.deepcopy(wire)
            broken["access_path_entries"][0]["access_kind"] = "write"
            self.assertTrue(_has(validate_wire_access_binding_patch(broken, draft, planning_ir), "wire_access_kind_conflict"))

            broken = copy.deepcopy(wire)
            broken["access_path_entries"][0]["access_kind"] = "read_write"
            self.assertTrue(_has(validate_wire_access_binding_patch(broken, draft, planning_ir), "wire_access_kind_conflict"))

    def test_5_4d_rejects_blocking_unresolved_as_required_wire_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            full = items["wire"]
            required_fields = {entry["field_id"] for entry in full["wire_mapping_entries"]}
            kept_fields = set(sorted(required_fields)[:2])
            broken = copy.deepcopy(full)
            broken["wire_mapping_entries"] = [entry for entry in broken["wire_mapping_entries"] if entry["field_id"] in kept_fields]
            kept_wire_ids = {entry["wire_mapping_id"] for entry in broken["wire_mapping_entries"]}
            broken["access_path_entries"] = [entry for entry in broken["access_path_entries"] if entry["field_id"] in kept_fields]
            kept_access_ids = {entry["access_path_id"] for entry in broken["access_path_entries"]}
            broken["function_binding_updates"] = [
                {
                    **update,
                    "wire_mapping_ids": [wire_id for wire_id in update["wire_mapping_ids"] if wire_id in kept_wire_ids],
                    "access_path_ids": [access_id for access_id in update["access_path_ids"] if access_id in kept_access_ids],
                }
                for update in broken["function_binding_updates"]
                if any(wire_id in kept_wire_ids for wire_id in update["wire_mapping_ids"])
            ]
            broken["unresolved_questions"] = [
                {
                    "question_id": f"q:wire:{field_id}",
                    "target_kind": "field",
                    "target_id": field_id,
                    "question": "How is this required wire field mapped?",
                    "unresolved_reason": "LLM did not provide a mapping.",
                    "blocking": True,
                    "trace_ref_keys": [],
                }
                for field_id in sorted(required_fields - kept_fields)
            ]

            self.assertEqual(len(required_fields), 14)
            self.assertEqual(len({entry["field_id"] for entry in broken["wire_mapping_entries"]}), 2)
            self.assertEqual(len(broken["unresolved_questions"]), 12)
            diags = validate_wire_access_binding_patch(broken, draft, planning_ir)
            self.assertEqual(len([diag for diag in diags if diag.code == "uncovered_wire_field"]), 12)

    def test_5_4d_accepts_full_real_wire_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            wire = items["wire"]
            self.assertEqual(len({entry["field_id"] for entry in wire["wire_mapping_entries"]}), 14)
            self.assertFalse(wire["unresolved_questions"])
            self.assertFalse(_has_error(validate_wire_access_binding_patch(wire, draft, planning_ir)))

    def test_5_4d_does_not_reject_valid_recent_success_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            recent_root = ROOT / "agent" / "planning" / "out" / "mqtt" / "broker__c__linux_epoll__minimum_v1" / "20260609_214554_036173_t" / "_step_logs"
            if recent_root.exists():
                recent = json.loads((recent_root / "007_5_4d_function_wire_access_binding_patch.json").read_text(encoding="utf-8"))
                recent_draft = json.loads((recent_root / "007_implementation_plan.json").read_text(encoding="utf-8"))
                recent_ir = json.loads((recent_root / "003_planning_ir.json").read_text(encoding="utf-8"))
            else:
                recent, recent_draft, recent_ir = items["wire"], draft, planning_ir

            self.assertEqual(len({entry["field_id"] for entry in recent["wire_mapping_entries"]}), 14)
            self.assertFalse(_has_error(validate_wire_access_binding_patch(recent, recent_draft, recent_ir)))

    def test_final_and_stage_wire_coverage_policy_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _, _, draft, plan, items = self._fixtures(Path(raw_tmp))
            missing_field = items["wire"]["wire_mapping_entries"][0]["field_id"]
            broken_patch = copy.deepcopy(items["wire"])
            broken_patch["wire_mapping_entries"] = [entry for entry in broken_patch["wire_mapping_entries"] if entry["field_id"] != missing_field]
            broken_patch["access_path_entries"] = [entry for entry in broken_patch["access_path_entries"] if entry["field_id"] != missing_field]
            broken_patch["unresolved_questions"] = [
                {
                    "question_id": "q:wire:missing",
                    "target_kind": "field",
                    "target_id": missing_field,
                    "question": "How is this required wire field mapped?",
                    "unresolved_reason": "LLM did not provide a mapping.",
                    "blocking": True,
                    "trace_ref_keys": [],
                }
            ]
            broken_plan = copy.deepcopy(plan)
            broken_plan["wire_mapping_table"] = [entry for entry in broken_plan["wire_mapping_table"] if entry["field_id"] != missing_field]
            broken_plan["unresolved_questions"].extend(copy.deepcopy(broken_patch["unresolved_questions"]))
            for function in broken_plan["function_contracts"]:
                function["wire_mapping"] = [entry for entry in function.get("wire_mapping", []) if entry.get("field_id") != missing_field]

            stage_diags = validate_wire_access_binding_patch(broken_patch, draft, planning_ir)
            final_diags = validate_full_implementation_plan(broken_plan, profile=profile, planning_ir=planning_ir)
            self.assertTrue(_has(stage_diags, "uncovered_wire_field"))
            self.assertTrue(_has(final_diags, "uncovered_wire_field"))
            self.assertTrue(_has(final_diags, "blocking_unresolved_questions"))

    def test_wire_access_merge_canonicalizes_helper_local_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            wire = copy.deepcopy(items["wire"])
            entry = next(
                item
                for item in wire["wire_mapping_entries"]
                if item.get("direction") == "parse"
                and any(access.get("function_id") == item.get("function_id") and access.get("field_id") == item.get("field_id") for access in wire["access_path_entries"])
            )
            access = next(item for item in wire["access_path_entries"] if item["function_id"] == entry["function_id"] and item["field_id"] == entry["field_id"])
            entry["target_path"] = "helper_local"
            self.assertTrue(_has(validate_wire_access_binding_patch(wire, draft, planning_ir), "wire_mapping_target_path_not_canonical"))

            merged = merge_wire_access_binding(draft, wire)
            table_entry = next(item for item in merged["wire_mapping_table"] if item["field_id"] == entry["field_id"])
            self.assertEqual(table_entry["target_path"], access["path"])
            function = next(item for item in merged["function_contracts"] if item["function_id"] == entry["function_id"])
            function_entry = next(item for item in function["wire_mapping"] if item["mapping_id"] == entry["wire_mapping_id"])
            self.assertEqual(function_entry["target_path"], access["path"])

    def test_final_cleanup_removes_stale_private_state_blocker_and_resolved_free_question(self) -> None:
        plan = {
            "type_inventory": [
                {
                    "type_id": "type:codec:mqtt_codec",
                    "name": "struct mqtt_codec",
                    "module_id": "codec",
                    "kind": "internal_state",
                    "visibility": "private",
                    "defined_in": "source_file",
                    "purpose": "Resource-owning modules need private implementation state.",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": ["decision:type_slot:codec:internal_state:target_codec_private_state"],
                    "status": "unresolved",
                },
                {
                    "type_id": "type:codec:mqtt_bytes_t",
                    "name": "mqtt_bytes_t",
                    "module_id": "codec",
                    "kind": "owned_buffer",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "purpose": "Encoding result buffer",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                },
            ],
            "canonical_types": [],
            "function_contracts": [_inventory_function("mqtt_bytes_free", "codec", function_id="fn:codec:mqtt_bytes_free", kind="resource_lifecycle")],
            "file_layout": {"files": []},
            "wire_mapping_table": [],
            "access_path_table": [],
            "dependency_graph": {},
            "unresolved_questions": [
                {
                    "question_id": "question:codec:mqtt_codec:lifecycle",
                    "target_kind": "type",
                    "target_id": "slot:type:codec:derived:mqtt_codec",
                    "question": "What functions create, initialize, destroy, and free the private codec state?",
                    "unresolved_reason": "No function artifacts specify lifecycle management for internal state",
                    "blocking": True,
                    "trace_ref_keys": ["decision:type_slot:codec:internal_state:target_codec_private_state"],
                },
                {
                    "question_id": "question:codec:mqtt_bytes_t:free_function_existence",
                    "target_kind": "type",
                    "target_id": "type:codec:mqtt_bytes_t",
                    "question": "Does mqtt_bytes_t have a free function?",
                    "unresolved_reason": "Asked before function inventory was generated",
                    "blocking": False,
                    "trace_ref_keys": [],
                },
            ],
        }
        cleaned = cleanup_final_unresolved_questions(plan)
        self.assertFalse(any(item.get("blocking") for item in cleaned["unresolved_questions"] if isinstance(item, dict)))
        self.assertFalse(any(item.get("type_id") == "type:codec:mqtt_codec" for item in cleaned["type_inventory"]))
        self.assertEqual(cleaned["unresolved_questions"], [])

    def test_cleanup_final_unresolved_questions_removes_stale_response_packet_question(self) -> None:
        question = {
            "question_id": "missing_response_packet_types",
            "target_kind": "module",
            "target_id": "codec",
            "question": "How should codec represent and encode mandatory broker response packets (CONNACK, SUBACK, PINGRESP) absent from canonical_types?",
            "unresolved_reason": "Core design summary canonical_types only includes client-originating packets; protocol requires broker responses",
            "blocking": True,
            "trace_ref_keys": ["encoder_responses"],
        }
        plan = {
            "canonical_types": [],
            "type_inventory": [
                {
                    "type_id": "type:codec:mqtt_packet_type_t",
                    "name": "mqtt_packet_type_t",
                    "module_id": "codec",
                    "kind": "enum",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "enum_values": [{"name": name, "value": str(index)} for index, name in enumerate(["CONNECT", "CONNACK", "SUBACK", "PINGRESP"])],
                }
            ],
            "function_contracts": [
                _inventory_function("mqtt_codec_encode_message", "codec", function_id="fn:codec:mqtt_codec_encode_message", kind="serializer"),
            ],
            "file_layout": {"files": []},
            "wire_mapping_table": [],
            "access_path_table": [],
            "dependency_graph": {},
            "unresolved_questions": [question],
        }
        cleaned = cleanup_final_unresolved_questions(plan)
        self.assertEqual(cleaned["unresolved_questions"], [])

        missing_encoder = copy.deepcopy(plan)
        missing_encoder["function_contracts"] = [_inventory_function("mqtt_encode_publish", "codec", function_id="fn:codec:mqtt_encode_publish", kind="serializer")]
        blocked = cleanup_final_unresolved_questions(missing_encoder)
        self.assertTrue(any(item.get("blocking") for item in blocked["unresolved_questions"] if isinstance(item, dict)))

    def test_cleanup_final_unresolved_questions_removes_resolved_type_visibility_question(self) -> None:
        plan = {
            "canonical_types": [],
            "type_inventory": [
                {
                    "type_id": "type:session:mqtt_session_t",
                    "name": "mqtt_session_t",
                    "module_id": "session",
                    "kind": "opaque_handle",
                    "visibility": "public",
                    "defined_in": "public_header",
                    "fields": [],
                    "enum_values": [],
                    "callback_signature": {"return_type": "", "params": []},
                    "ownership_lifetime": "",
                    "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                    "related_functions": [],
                    "dependencies": [],
                    "trace_ref_keys": [],
                    "status": "inferred",
                }
            ],
            "function_contracts": [],
            "file_layout": {"files": []},
            "wire_mapping_table": [],
            "access_path_table": [],
            "dependency_graph": {},
            "unresolved_questions": [
                {
                    "question_id": "question:router:dispatch_context_session_type_ref",
                    "target_kind": "field",
                    "target_id": "field:mqtt_router_dispatch_context_t:session",
                    "question": "Is mqtt_session_t a public or internal type? If internal, should this field use an opaque handle instead?",
                    "unresolved_reason": "mqtt_session_t appears in session module artifacts but its visibility is not specified in allowed_type_refs.",
                    "blocking": True,
                    "trace_ref_keys": [],
                }
            ],
        }
        cleaned = cleanup_final_unresolved_questions(plan)
        self.assertEqual(cleaned["unresolved_questions"], [])

    def test_merge_file_layout_updates_module_files_from_actual_layout(self) -> None:
        draft = {
            "module_artifacts": [{"module_id": "codec", "files": ["seed/codec.h", "seed/codec.c"]}],
            "function_contracts": [_inventory_function("codec_public", "codec", function_id="fn:codec:public")],
        }
        candidate = {
            "files": [
                {
                    "file_id": "file:actual/codec/frame_codec",
                    "module_id": "codec",
                    "source_path": "actual/codec/frame_codec.c",
                    "header_path": "actual/codec/frame_codec.h",
                    "responsibility": "Actual codec layout.",
                    "exports_function_ids": ["fn:codec:public"],
                    "exports_type_ids": [],
                    "implements_function_ids": ["fn:codec:public"],
                    "imports_allowed": [],
                }
            ],
            "function_file_assignments": [
                {
                    "function_id": "fn:codec:public",
                    "implementation_file_id": "file:actual/codec/frame_codec",
                    "declaration_file_id": "file:actual/codec/frame_codec",
                    "visibility": "public",
                }
            ],
            "unresolved_questions": [],
        }
        merged = merge_file_layout(draft, candidate)
        self.assertEqual(merged["module_artifacts"][0]["files"], ["actual/codec/frame_codec.h", "actual/codec/frame_codec.c"])


if __name__ == "__main__":
    unittest.main()
