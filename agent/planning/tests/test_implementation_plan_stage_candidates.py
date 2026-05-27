from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.prompts.templates import (
    calls_allowed_candidate_messages,
    core_design_candidate_messages,
    dependency_repair_patch_messages,
    file_layout_candidate_messages,
    function_behavior_contract_patch_messages,
    function_inventory_candidate_messages,
    function_inventory_repair_patch_messages,
    function_signature_patch_messages,
    module_artifacts_candidate_messages,
    runtime_entrypoint_candidate_messages,
    type_inventory_candidate_messages,
    type_inventory_repair_patch_messages,
    wire_access_binding_patch_messages,
)
from agent.planning.stages.architecture import build_architecture_candidates, select_architecture
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.implementation_plan import build_implementation_plan
from agent.planning.stages.implementation_plan_context import (
    build_calls_allowed_context,
    build_core_design_context,
    build_dependency_repair_context,
    build_file_layout_context,
    build_function_behavior_context,
    build_function_inventory_context,
    build_function_inventory_repair_context,
    build_function_signature_context,
    build_module_artifact_context,
    build_runtime_entrypoint_context,
    build_type_inventory_context,
    build_type_inventory_repair_context,
    build_wire_access_binding_context,
    derive_type_generation_targets,
    derive_type_obligations,
    normalize_type_inventory_candidate,
)
from agent.planning.stages.function_inventory_decomposition import DECOMPOSITION_RULES, select_top_decomposition_hints
from agent.planning.stages.implementation_plan_merger import (
    build_plan_skeleton,
    fallback_calls_allowed,
    fallback_core_design,
    fallback_dependency_repair_patch,
    fallback_file_layout,
    fallback_function_behavior,
    fallback_function_inventory,
    fallback_function_signatures,
    fallback_module_artifacts,
    fallback_runtime_entrypoint,
    fallback_type_inventory,
    fallback_wire_access_binding,
    apply_function_inventory_repair_patch,
    apply_type_inventory_repair_patch,
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
from agent.planning.validators.implementation_plan_stages import (
    validate_calls_allowed_candidate,
    validate_core_design_candidate,
    validate_dependency_repair_patch,
    validate_file_layout_candidate,
    validate_function_behavior_contract_patch,
    validate_function_inventory_candidate,
    validate_function_inventory_repair_patch,
    validate_function_signature_patch,
    validate_module_artifacts_candidate,
    validate_runtime_entrypoint_candidate,
    validate_type_inventory_candidate,
    validate_type_inventory_repair_patch,
    validate_wire_access_binding_patch,
    validation_report,
    function_inventory_decomposition_report,
)


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
    def _fixtures(self, tmp: Path):
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        target, target_diags = load_target_profile(_target_profile(tmp))
        self.assertIsNotNone(target, [diag.__dict__ for diag in target_diags])
        planning_ir, ir_diags = build_planning_ir(facts, target)
        self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
        profile = build_protocol_profile(planning_ir)
        constraints = activate_constraints(profile)
        candidates = build_architecture_candidates(planning_ir, profile, constraints)
        selected = select_architecture(candidates, profile)
        draft = build_plan_skeleton(planning_ir, profile, constraints, selected)

        core = fallback_core_design(draft, planning_ir, constraints, selected, profile)
        draft = merge_core_design(draft, core)
        modules = fallback_module_artifacts(draft, profile, constraints, selected)
        draft = merge_module_artifacts(draft, modules)
        type_inventories = []
        for module in list(draft["module_artifacts"]):
            type_inventory = fallback_type_inventory(draft, module)
            type_inventories.append(type_inventory)
            draft = merge_type_inventory(draft, type_inventory)
        inventories = []
        for module in list(draft["module_artifacts"]):
            inventory = fallback_function_inventory(draft, module)
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
        plan = build_implementation_plan(planning_ir, profile, constraints, selected)
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
            self.assertFalse(validate_file_layout_candidate(items["layout"], draft))
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
            "producer": {"stage": "5.3_module_artifacts", "prompt_name": "module_artifacts_candidate_prompt", "prompt_version": "test"},
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
            modules = copy.deepcopy(items["modules"])
            modules["modules"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_module_artifacts_candidate(modules, selected, profile, constraints, draft), "unknown_module_artifacts_module"))
            type_inventory = copy.deepcopy(items["type_inventory"])
            type_inventory["types"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_type_inventory_candidate(type_inventory, draft["module_artifacts"], draft, profile, planning_ir), "unknown_type_inventory_owner"))
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
            inventory = fallback_function_inventory(draft, module)
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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
        candidate = fallback_type_inventory(draft, module)
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

    def test_type_inventory_context_exposes_provider_public_seed_types_only_for_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, _, _, _ = self._fixtures(Path(raw_tmp))
            draft, network, _, session = self._provider_seed_draft()
            context = build_type_inventory_context(draft, session, planning_ir)
            provider_types = {group["module_id"]: {item["name"] for item in group["types"]} for group in context["provider_public_types"]}
            self.assertIn("mqtt_packet_t", provider_types["codec"])
            self.assertNotIn("mqtt_connection_t", provider_types["network"])
            self.assertIn("type:codec:mqtt_packet_t", context["legal_id_universe"]["type_ids"])
            self.assertIn("type:mqtt_packet_t", context["legal_id_universe"]["type_ids"])

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
                "producer": {"stage": "5.4a_type_inventory", "prompt_name": "type_inventory_candidate_prompt", "prompt_version": "test"},
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
            bad["types"][0]["dependencies"] = ["type:mqtt_connection_t"]
            self.assertTrue(_has(validate_type_inventory_candidate(normalize_type_inventory_candidate(bad), draft["module_artifacts"], draft, planning_ir=planning_ir), "unknown_type_ref"))

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
            "producer": {"stage": "5.4a_type_inventory", "prompt_name": "type_inventory_candidate_prompt", "prompt_version": "test"},
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
        candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
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
        network_candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [network]}, network)
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
        session_candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [network, session]}, session)
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
        router_candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [session, router]}, router)
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
        base = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
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

    def test_fallback_keeps_connection_state_private_but_server_public(self) -> None:
        module = {
            "module_id": "network",
            "name": "network",
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
        candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        connection = next(item for item in candidate["types"] if item["name"] == "mqtt_connection_t")
        server = next(item for item in candidate["types"] if item["name"] == "mqtt_server_t")
        self.assertEqual((connection["visibility"], connection["defined_in"]), ("module_internal", "source_file"))
        self.assertEqual((server["visibility"], server["defined_in"]), ("public", "public_header"))

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
            "producer": {"stage": "5.4a_type_inventory", "prompt_name": "type_inventory_candidate_prompt", "prompt_version": "test"},
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
        base = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        first = base["types"][0]
        first.update({"name": "struct mqtt_router", "kind": "struct", "visibility": "private", "defined_in": "source_file"})
        second = copy.deepcopy(first)
        second["type_id"] = "type:router:duplicate_router"
        second["name"] = "mqtt_router_t"
        second["kind"] = "struct"
        base["types"] = [first, second]
        self.assertTrue(_has(validate_type_inventory_candidate(base, [module], {"module_artifacts": [module]}), "duplicate_type_inventory_name"))

    def test_codec_type_inventory_requires_protocol_packet_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            codec = next(module for module in draft["module_artifacts"] if any("decode" in str(artifact.get("role", "")).lower() for artifact in module["artifacts"]))
            candidate = fallback_type_inventory(draft, codec, planning_ir)
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
        candidate = fallback_type_inventory(draft, module)
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
        candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
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
                "lifecycle": {"created_by": [], "initialized_by": [], "destroyed_by": [], "freed_by": []},
                "related_functions": ["mqtt_decoder_register_callback"],
            }
        )
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "type_inventory": [packet, callback]}
        obligations = derive_type_obligations(draft, module)
        actions = {item["action"] for item in obligations}
        self.assertIn("create", actions)
        self.assertIn("destroy", actions)
        self.assertIn("release_owned_data", actions)
        self.assertIn("register_callback", actions)
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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
        type_item = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["related_functions"] = ["mqtt_missing_cleanup"]
        type_item["lifecycle"]["freed_by"] = ["mqtt_missing_cleanup"]
        draft = {"protocol_name": "mqtt", "module_artifacts": [module], "type_inventory": [type_item], "function_contracts": []}
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:type_ref_drift",
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
            "module_id": "all_modules",
            "functions": [_inventory_function("mqtt_decode", "codec", kind="parser")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], draft, {}, {})
        self.assertTrue(_has(diags, "type_function_reference_unresolved"))

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
        type_item = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)["types"][0]
        type_item["lifecycle"]["freed_by"] = ["old_packet_cleanup"]
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [module],
            "type_inventory": [type_item],
            "function_contracts": [_inventory_function("mqtt_packet_free", "codec", kind="resource_lifecycle")],
        }
        reconciled = reconcile_type_inventory_function_refs(draft)
        self.assertEqual(reconciled["type_inventory"][0]["lifecycle"]["freed_by"], ["mqtt_packet_free"])

    def test_type_inventory_repair_patch_and_merge(self) -> None:
        module = {
            "module_id": "mqtt_codec",
            "name": "mqtt_codec",
            "role": "MQTT codec",
            "dependencies": [],
            "artifacts": [{"name": "mqtt_packet", "kind": "TYPE", "role": "Decoded packet"}],
            "state_owned": [],
            "owned_capabilities": [],
            "files": [],
            "doc_ref": [],
        }
        candidate = fallback_type_inventory({"protocol_name": "mqtt", "module_artifacts": [module]}, module)
        base_type = candidate["types"][0]
        added = copy.deepcopy(base_type)
        added.update({"type_id": "type:mqtt_codec:mqtt_reason_code", "name": "mqtt_reason_code", "kind": "enum"})
        patch = {
            "schema_version": "type_inventory_repair_patch/v1",
            "patch_id": "patch:test:type_inventory",
            "producer": {"stage": "5.4a_type_inventory", "prompt_name": "type_inventory_repair_patch_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "added_types": [added],
            "updated_types": [{"type_id": base_type["type_id"], "ownership_lifetime": "Caller owns until mqtt_packet_free.", "related_functions": ["mqtt_packet_free"]}],
            "added_assumptions": [],
            "added_unresolved_questions": [],
        }
        context = build_type_inventory_repair_context({"module_artifacts": [module]}, module, candidate, [{"code": "type_inventory_missing_artifact_type", "message": "missing"}])
        payload = json.loads(type_inventory_repair_patch_messages(context)[1]["content"])
        self.assertEqual(payload["output_schema"], "type_inventory_repair_patch/v1")
        self.assertIn("added_types", payload["output_shape"]["properties"])
        self.assertFalse(validate_type_inventory_repair_patch(patch, candidate, [module]))
        merged = apply_type_inventory_repair_patch(candidate, patch)
        self.assertEqual(len(merged["types"]), len(candidate["types"]) + 1)
        self.assertEqual(merged["types"][0]["ownership_lifetime"], "Caller owns until mqtt_packet_free.")

        duplicate = copy.deepcopy(patch)
        duplicate["added_types"][0]["type_id"] = base_type["type_id"]
        self.assertTrue(_has(validate_type_inventory_repair_patch(duplicate, candidate, [module]), "repair_duplicate_added_type_id"))

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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
        report = validation_report("5.4b_function_inventory:test", diags)
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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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
            self.assertEqual(len(ids), 3)
            self.assertEqual(ids, selected["selected_rule_ids"])
            self.assertEqual(set(ids), set(selected["expected_function_families_by_rule"]))
            self.assertTrue(required.issubset(set(ids)))
            if alternatives:
                self.assertTrue(set(ids) & alternatives)
            renamed = copy.deepcopy(module)
            renamed["module_id"] = "mqtt_codec_network_router_session_store_broker_app"
            self.assertEqual(ids, select_top_decomposition_hints(renamed, {"core_design_summary": {}}, max_hints=3)["detected_rule_ids"])

    def test_function_inventory_prompt_documents_seed_semantics_and_selected_hints_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            module = copy.deepcopy(draft["module_artifacts"][0])
            module["role"] = "incremental byte stream framing, command decoding, and response serialization"
            payload = json.loads(function_inventory_candidate_messages(build_function_inventory_context(draft, module))[1]["content"])
            rules_text = "\n".join(payload["semantic_validation_rules"])
            self.assertIn("mandatory public/API seeds", rules_text)
            self.assertIn("not the complete function list", rules_text)
            self.assertIn("one-to-one artifact mirroring", rules_text)
            decomposition = payload["function_inventory_context"]["decomposition_context"]
            self.assertEqual(len(decomposition["selected_decomposition_hints"]), 3)
            prompt_text = json.dumps(payload, ensure_ascii=False)
            selected_hints = set(decomposition["selected_decomposition_hints"])
            self.assertEqual(len(decomposition["selected_rule_ids"]), 3)
            self.assertEqual(set(decomposition["selected_rule_ids"]), set(decomposition["expected_function_families_by_rule"]))
            for rule in DECOMPOSITION_RULES:
                count = prompt_text.count(rule.hint)
                self.assertEqual(count, 1 if rule.hint in selected_hints else 0)

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
                "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
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

    def test_function_inventory_repair_prompt_and_patch_merge(self) -> None:
        module = {
            "module_id": "generic_framing_unit",
            "name": "generic_framing_unit",
            "role": "incremental byte stream framing and command decoding",
            "dependencies": [],
            "artifacts": [{"name": "proto_decode", "kind": "FUNC", "role": "Decode command stream"}],
            "files": [],
            "doc_ref": [],
        }
        draft = {"module_artifacts": [module], "handler_matrix": [], "required_capabilities": []}
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:test:repair",
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_candidate_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "functions": [_inventory_function("proto_decode", module["module_id"], kind="parser", purpose="Parse, validate, dispatch, update state, encode response, send reply, and cleanup resources.")],
            "assumptions": [],
            "unresolved_questions": [],
        }
        diags = validate_function_inventory_candidate(candidate, [module], {}, {}, {})
        coverage = function_inventory_decomposition_report(candidate, [module], {})
        context = build_function_inventory_repair_context(draft, module, candidate, coverage, [{"code": diag.code, "message": diag.message} for diag in diags], repair_mode="coarse_function_split")
        payload = json.loads(function_inventory_repair_patch_messages(context)[1]["content"])
        self.assertEqual(payload["output_schema"], "function_inventory_repair_patch/v1")
        self.assertIn("added_functions", payload["output_shape"]["properties"])
        self.assertNotIn("functions", payload["output_shape"]["properties"])
        rules_text = "\n".join(payload["semantic_validation_rules"])
        self.assertIn("minimal patch", rules_text)
        self.assertIn("facade", rules_text)

        patch = {
            "schema_version": "function_inventory_repair_patch/v1",
            "patch_id": "patch:test:repair",
            "producer": {"stage": "5.4b_function_inventory", "prompt_name": "function_inventory_repair_patch_prompt", "prompt_version": "test"},
            "module_id": module["module_id"],
            "added_functions": [
                _inventory_function("proto_read_primitive_field", module["module_id"], kind="internal_helper", public=False, purpose="Read primitive fields for parser helper coverage."),
                _inventory_function("proto_validate_malformed_input", module["module_id"], kind="validator", public=False, purpose="Validate malformed command input before dispatch."),
            ],
            "updated_functions": [{"function_id": f"fn:{module['module_id']}:proto_decode", "purpose": "Facade parser entry that delegates detailed parsing and validation helpers.", "grouping_hint": "parser_facade", "status": "inferred"}],
            "added_assumptions": [],
            "added_unresolved_questions": [],
        }
        self.assertFalse(validate_function_inventory_repair_patch(patch, candidate, [module]))
        merged = apply_function_inventory_repair_patch(candidate, patch)
        self.assertEqual(len(merged["functions"]), 3)
        self.assertEqual(merged["functions"][0]["name"], "proto_decode")
        self.assertEqual(merged["functions"][0]["function_id"], f"fn:{module['module_id']}:proto_decode")
        self.assertFalse(_has_error(validate_function_inventory_candidate(merged, [module], {}, {}, {})))

        duplicate = copy.deepcopy(patch)
        duplicate["added_functions"][0]["name"] = "proto_decode"
        self.assertTrue(_has(validate_function_inventory_repair_patch(duplicate, candidate, [module]), "repair_duplicate_added_function_name"))
        identity_update = copy.deepcopy(patch)
        identity_update["updated_functions"][0]["name"] = "proto_decode_renamed"
        self.assertTrue(_has(validate_function_inventory_repair_patch(identity_update, candidate, [module]), "forbidden_extra_field"))

    def test_stage_prompts_expose_semantic_validator_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            first_module = draft["module_artifacts"][0]
            first_module_id = str(first_module["module_id"])
            first_functions = [item for item in draft["function_contracts"] if item.get("module_id") == first_module_id][:4]
            prompt_payloads = [
                json.loads(core_design_candidate_messages(build_core_design_context(planning_ir, profile, constraints, selected))[1]["content"]),
                json.loads(module_artifacts_candidate_messages(build_module_artifact_context(draft, profile, constraints, selected))[1]["content"]),
                json.loads(type_inventory_candidate_messages(build_type_inventory_context(draft, first_module))[1]["content"]),
                json.loads(function_inventory_candidate_messages(build_function_inventory_context(draft, first_module))[1]["content"]),
                json.loads(function_signature_patch_messages(build_function_signature_context(draft, first_module_id, first_functions, batch_index=0, batch_size=8))[1]["content"]),
                json.loads(function_behavior_contract_patch_messages(build_function_behavior_context(draft, first_module_id, first_functions, constraints, batch_index=0, batch_size=4))[1]["content"]),
                json.loads(wire_access_binding_patch_messages(build_wire_access_binding_context(draft, planning_ir))[1]["content"]),
                json.loads(calls_allowed_candidate_messages(build_calls_allowed_context(draft, selected))[1]["content"]),
                json.loads(file_layout_candidate_messages(build_file_layout_context(draft, planning_ir, constraints))[1]["content"]),
                json.loads(runtime_entrypoint_candidate_messages(build_runtime_entrypoint_context(draft, planning_ir, selected))[1]["content"]),
                json.loads(dependency_repair_patch_messages(build_dependency_repair_context(draft, [{"code": "dependency_cycle", "message": "cycle"}]))[1]["content"]),
            ]
            for payload in prompt_payloads:
                self.assertIn("semantic_validation_rules", payload)
                self.assertIn("id_reference_rules", payload)
                self.assertIn("enum_usage_rules", payload)
                self.assertTrue(payload["semantic_validation_rules"])
            joined_rules = "\n".join(
                rule
                for payload in prompt_payloads
                for rule in payload["semantic_validation_rules"] + payload["enum_usage_rules"] + payload["id_reference_rules"]
            )
            self.assertIn("handler_matrix[].trigger must exactly equal that surface", joined_rules)
            self.assertIn("mandatory seeds", joined_rules)
            self.assertIn("signature.name must match the existing function name", joined_rules)
            self.assertIn("service_requirements may describe needed operations", joined_rules)
            self.assertIn("Parser and serializer functions must not write state", joined_rules)
            self.assertIn("exports_type_ids may contain only canonical type IDs", joined_rules)
            self.assertIn("entrypoint_signature should normally be int main", joined_rules)
            self.assertIn("must be copied exactly from legal_id_universe.file_ids or files[].file_id", joined_rules)

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

    def test_behavior_service_requirements_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            _, _, constraints, _, draft, _, items = self._fixtures(Path(raw_tmp))
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
            behavior["function_behavior_updates"][0]["service_requirements"][0].pop("requirement_kind")
            self.assertTrue(_has(validate_function_behavior_contract_patch(behavior, draft, constraints), "missing_required_field"))

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
                "producer": {"stage": "5.4f_call_planning", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
                "call_updates": [{"caller_function_id": caller["function_id"], "calls_allowed": [_call_edge(same_module_callee["function_id"], ["srv:test_cross"])]}],
                "unresolved_service_requirements": [],
                "assumptions": [],
                "unresolved_questions": [],
            }
            self.assertFalse(
                validate_calls_allowed_candidate(
                    candidate,
                    draft,
                    selected,
                    expected_caller_ids={caller["function_id"]},
                    expected_service_requirement_ids={"srv:test_cross"},
                    callable_function_ids=set(),
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

    def test_wire_access_fallback_keeps_field_specific_targets_and_types(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, _, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
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
            for function in merged["function_contracts"]:
                mappings = function.get("wire_mapping", [])
                if len(mappings) < 2:
                    continue
                access_ids = [mapping["access_path_id"] for mapping in mappings]
                self.assertGreater(len(set(access_ids)), 1)
                for mapping in mappings:
                    access = access_by_id[mapping["access_path_id"]]
                    self.assertEqual(access["field_id"], mapping["field_id"])
                break
            else:
                self.fail("expected at least one codec function with multiple wire mappings")

            broken = copy.deepcopy(wire)
            broken["access_path_entries"][0]["c_type"] = "unknown"
            self.assertTrue(_has(validate_wire_access_binding_patch(broken, draft, planning_ir), "unknown_coder_access_path_type"))


if __name__ == "__main__":
    unittest.main()
