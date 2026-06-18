from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.stages.implementation_plan_merger import (
    merge_core_design,
    merge_module_artifacts,
    merge_type_inventory,
    repair_function_inventory_symbols,
)
from agent.planning.stages.inventory_planning_space import build_type_planning_space
from agent.planning.stages.inventory_reconciliation import reconcile_type_filling_candidate
from agent.planning.stages.specs_compiler import compile_spec_bundle
from agent.planning.tests.current_flow_fixtures import current_function_inventory_candidate
from agent.planning.validators.coder_compat import validate_coder_compatibility
from agent.planning.validators.implementation_plan_stages import (
    validate_calls_allowed_candidate,
    validate_function_inventory_candidate,
    validate_type_inventory_candidate,
    validate_wire_access_binding_patch,
)


ROOT = Path(__file__).resolve().parents[3]
HISTORY_ROOT = ROOT / "agent" / "planning" / "out" / "mqtt" / "broker__c__linux_epoll__minimum_v1"

FAILURE_CLASSIFICATIONS = {
    "20260617_172331_047723": "deterministic repair",
    "20260617_205742_685499": "deterministic repair",
    "20260617_103648_329047": "validator rule",
    "20260610_093743_377786": "validator rule",
    "20260616_194800_796436": "deterministic repair",
    "20260612_092317_251284": "deterministic repair",
    "20260616_231307_313900": "validator rule",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _has(diags, code: str) -> bool:
    return any(diag.code == code for diag in diags)


def _error_codes(diags) -> set[str]:
    return {diag.code for diag in diags if diag.level == "error"}


def _replay(run_id: str) -> Path:
    root = HISTORY_ROOT / run_id
    if not root.exists():
        raise AssertionError(f"missing historical replay directory: {root}")
    return root


def _load_stage_draft(run_id: str) -> tuple[dict, dict, dict, dict]:
    logs = _replay(run_id) / "_step_logs"
    planning_ir = _read_json(logs / "003_planning_ir.json")
    profile = _read_json(logs / "004_protocol_profile.json")
    constraints = _read_json(logs / "005_engineering_constraints.json")
    draft = _read_json(logs / "007_5_1_plan_skeleton.json")
    draft = merge_core_design(draft, _read_json(logs / "007_5_2a_core_design_candidate.json"))
    draft = merge_module_artifacts(draft, _read_json(logs / "007_5_2b_module_artifacts_candidate.json"))
    return planning_ir, profile, constraints, draft


def _candidate_as_fillings(candidate: dict, space: dict) -> dict:
    slots = {
        str(slot.get("type_id") or slot.get("name")): slot
        for slot in [*space.get("mandatory_type_slots", []), *space.get("derived_type_slots", []), *space.get("recommended_type_slots", [])]
        if isinstance(slot, dict)
    }
    slots.update(
        {
            str(slot.get("name")): slot
            for slot in [*space.get("mandatory_type_slots", []), *space.get("derived_type_slots", []), *space.get("recommended_type_slots", [])]
            if isinstance(slot, dict)
        }
    )
    fillings = []
    for type_item in candidate.get("types", []):
        if not isinstance(type_item, dict):
            continue
        slot = slots.get(str(type_item.get("type_id", ""))) or slots.get(str(type_item.get("name", "")))
        if slot is None:
            continue
        fillings.append(
            {
                "slot_id": slot["slot_id"],
                "semantic_purpose": type_item.get("purpose", ""),
                "fields": copy.deepcopy(type_item.get("fields", [])),
                "enum_values": copy.deepcopy(type_item.get("enum_values", [])),
                "dependencies": copy.deepcopy(type_item.get("dependencies", [])),
                "callback_signature": copy.deepcopy(type_item.get("callback_signature", {})),
                "ownership_lifetime": type_item.get("ownership_lifetime", ""),
                "lifecycle": copy.deepcopy(type_item.get("lifecycle", {})),
                "trace_ref_keys": copy.deepcopy(type_item.get("trace_ref_keys", [])),
                "status": type_item.get("status", "inferred"),
            }
        )
    return {
        "schema_version": "type_filling_candidate/v1",
        "candidate_id": f"replay:{candidate.get('candidate_id', 'type_alias')}",
        "module_id": space.get("module_id", ""),
        "slot_fillings": fillings,
        "optional_type_proposals": [],
        "assumptions": [],
        "unresolved_questions": [],
    }


def _field_surface_values(candidate: dict) -> set[str]:
    return {
        str(field.get(key, ""))
        for type_item in candidate.get("types", [])
        if isinstance(type_item, dict)
        for field in type_item.get("fields", [])
        if isinstance(field, dict)
        for key in ("field_type", "type_ref")
    }


def _reconciled_type_candidate(run_id: str, module_id: str, candidate_file: str) -> tuple[dict, dict, dict]:
    planning_ir, profile, constraints, draft = _load_stage_draft(run_id)
    module = next(item for item in draft["module_artifacts"] if item["module_id"] == module_id)
    legacy_candidate = _read_json(_replay(run_id) / "_agent_logs" / candidate_file)
    space = build_type_planning_space(draft, module, planning_ir, profile, constraints)
    filling = _candidate_as_fillings(legacy_candidate, space)
    reconciled = reconcile_type_filling_candidate(space, filling)["candidate"]
    return planning_ir, draft, reconciled


def _inventory_function(name: str, module_id: str, *, function_id: str | None = None, public: bool = True) -> dict:
    return {
        "function_id": function_id or f"fn:{module_id}:{name}",
        "name": name,
        "module_id": module_id,
        "function_kind": "public_api",
        "coder_function_type": "ALGORITHM",
        "visibility": "public" if public else "internal",
        "api_surface": "public" if public else "module_internal",
        "exported": public,
        "export_reason": "test public boundary" if public else "",
        "public_api_role": "module_boundary_operation" if public else "",
        "grouping_hint": module_id,
        "purpose": "test function",
        "capability_ids": [],
        "covers_handler_ids": [],
        "covers_message_ids": [],
        "covers_field_ids": [],
        "trace_ref_keys": [],
        "status": "inferred",
    }


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


class PipelineInvariantRegressionGoldenTests(unittest.TestCase):
    def test_failure_classification_table_covers_all_round5_replays(self) -> None:
        allowed = {
            "prompt/LLM output",
            "validator rule",
            "deterministic repair",
            "missing fact/open assumption",
            "coder generation gap",
        }
        self.assertEqual(
            set(FAILURE_CLASSIFICATIONS),
            {
                "20260617_172331_047723",
                "20260617_205742_685499",
                "20260617_103648_329047",
                "20260610_093743_377786",
                "20260616_194800_796436",
                "20260612_092317_251284",
                "20260616_231307_313900",
            },
        )
        self.assertTrue(set(FAILURE_CLASSIFICATIONS.values()).issubset(allowed))
        for run_id in FAILURE_CLASSIFICATIONS:
            self.assertTrue(_replay(run_id).exists())

    def test_20260617_string_and_buffer_alias_replay_reconciles_to_declared_view_types(self) -> None:
        run_id = "20260617_172331_047723"
        legacy = _read_json(_replay(run_id) / "_agent_logs" / "007_5_3_type_data_inventory_candidate__codec.json")
        self.assertTrue({"string_view", "buffer_view"} & _field_surface_values(legacy))

        planning_ir, draft, reconciled = _reconciled_type_candidate(
            run_id,
            "codec",
            "007_5_3_type_data_inventory_candidate__codec.json",
        )
        values = _field_surface_values(reconciled)
        self.assertFalse({"string_view", "buffer_view", "byte_buffer"} & values)
        self.assertIn("mqtt_string_view_t", values)
        self.assertIn("mqtt_buffer_view_t", values)
        self.assertIn("type:codec:mqtt_string_view_t", values)
        self.assertIn("type:codec:mqtt_buffer_view_t", values)
        diagnostics = validate_type_inventory_candidate(reconciled, draft["module_artifacts"], draft, planning_ir=planning_ir)
        self.assertNotIn("unknown_type_ref", _error_codes(diagnostics))
        self.assertNotIn("undeclared_view_type_alias", _error_codes(diagnostics))
        self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

    def test_20260617_byte_buffer_replay_reconciles_field_type_and_type_ref_together(self) -> None:
        run_id = "20260617_205742_685499"
        legacy = _read_json(_replay(run_id) / "_agent_logs" / "007_5_3_type_data_inventory_candidate__mqtt_codec.json")
        self.assertIn("byte_buffer", _field_surface_values(legacy))

        planning_ir, draft, reconciled = _reconciled_type_candidate(
            run_id,
            "mqtt_codec",
            "007_5_3_type_data_inventory_candidate__mqtt_codec.json",
        )
        by_name = {item["name"]: item for item in reconciled["types"]}
        publish = by_name["mqtt_publish_t"]
        payload = next(field for field in publish["fields"] if field["field_name"] == "payload")
        self.assertEqual(payload["field_type"], "mqtt_buffer_view_t")
        self.assertEqual(payload["type_ref"], "type:mqtt_codec:mqtt_buffer_view_t")
        self.assertNotIn("byte_buffer", _field_surface_values(reconciled))
        diagnostics = validate_type_inventory_candidate(reconciled, draft["module_artifacts"], draft, planning_ir=planning_ir)
        self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

    def test_20260617_prose_value_ref_replay_blocks_without_dropping_valid_bindings(self) -> None:
        run_id = "20260617_103648_329047"
        logs = _replay(run_id) / "_step_logs"
        draft = _read_json(logs / "007_implementation_plan.json")
        candidate = _read_json(logs / "007_5_4e_function_call_contracts_candidate.json")
        selected = {"architecture": {"modules": draft["module_artifacts"]}}
        diagnostics = validate_calls_allowed_candidate(candidate, draft, selected)
        messages = "\n".join(diag.message for diag in diagnostics if diag.code == "call_contract_unknown_value_ref")
        self.assertIn("client ID", messages)
        self.assertIn("decoded PUBLISH packet", messages)
        self.assertIn("connection handle", messages)

        caller = _inventory_function("handle_publish", "broker", function_id="fn:broker:handle_publish")
        caller["signature"] = {
            "return_type": "int",
            "name": "handle_publish",
            "params": [{"name": "message", "type": "const void*"}],
            "raw": "int handle_publish(const void* message)",
        }
        caller["service_requirements"] = [{"service_requirement_id": "srv:broker:route_publish", "requirement_kind": "cross_module_service"}]
        callee = _inventory_function("route_publish", "topic", function_id="fn:topic:route_publish")
        callee["signature"] = {
            "return_type": "int",
            "name": "route_publish",
            "params": [{"name": "message", "type": "const void*"}],
            "raw": "int route_publish(const void* message)",
        }
        valid = {
            "schema_version": "calls_allowed_candidate/v2",
            "candidate_id": "candidate:round5:valid_value_ref",
            "producer": {"stage": "5.4e_call_contracts", "prompt_name": "test", "prompt_version": "test"},
            "call_updates": [
                {
                    "caller_function_id": caller["function_id"],
                    "calls_allowed": [
                        {
                            **_call_edge(callee["function_id"], ["srv:broker:route_publish"]),
                            "param_bindings": [{"param_name": "message", "value_ref": "message", "ownership": "borrowed", "nullability": "non_null"}],
                        }
                    ],
                }
            ],
            "unresolved_service_requirements": [],
            "assumptions": [],
            "unresolved_questions": [],
        }
        valid_diags = validate_calls_allowed_candidate(
            valid,
            {"module_artifacts": [{"module_id": "broker"}, {"module_id": "topic"}], "function_contracts": [caller, callee]},
            {"architecture": {"modules": [{"module_id": "broker"}, {"module_id": "topic"}]}},
        )
        self.assertNotIn("call_contract_unknown_value_ref", _error_codes(valid_diags))

    def test_20260610_fixed_header_only_wire_replay_blocks_each_uncovered_payload_field(self) -> None:
        run_id = "20260610_093743_377786"
        logs = _replay(run_id) / "_step_logs"
        planning_ir = _read_json(logs / "003_planning_ir.json")
        draft = _read_json(logs / "007_implementation_plan.json")
        broken = _read_json(logs / "007_5_4d_function_wire_access_binding_patch.json")
        mapped_fields = {entry["field_id"] for entry in broken["wire_mapping_entries"] if entry.get("direction") in {"parse", "serialize"}}
        self.assertEqual(len(mapped_fields), 2)

        diagnostics = validate_wire_access_binding_patch(broken, draft, planning_ir)
        uncovered = [diag for diag in diagnostics if diag.code == "uncovered_wire_field"]
        self.assertEqual(len(uncovered), 12)
        uncovered_text = "\n".join(diag.message for diag in uncovered)
        self.assertIn("fields_4", uncovered_text)
        self.assertIn("fields_3", uncovered_text)
        self.assertTrue(any("publish" in diag.message.lower() or "fields_3" in diag.message for diag in uncovered))

    def test_20260616_lifecycle_replay_generates_public_runtime_lifecycle_apis(self) -> None:
        run_id = "20260616_194800_796436"
        planning_ir, profile, constraints, draft = _load_stage_draft(run_id)
        logs = _replay(run_id) / "_step_logs"
        modules = _read_json(logs / "007_5_2b_module_artifacts_candidate.json")
        raw_broker = next(module for module in modules["modules"] if module["module_id"] == "broker_app")
        raw_funcs = {(artifact["name"], artifact["kind"]) for artifact in raw_broker["artifacts"]}
        self.assertNotIn(("mqtt_broker_create", "FUNC"), raw_funcs)
        self.assertNotIn(("mqtt_broker_start", "FUNC"), raw_funcs)
        self.assertNotIn(("mqtt_broker_destroy", "FUNC"), raw_funcs)

        draft = merge_type_inventory(draft, _read_json(logs / "007_5_3_type_data_inventory_candidate.json"))
        broker = next(module for module in draft["module_artifacts"] if module["module_id"] == "broker_app")
        inventory = current_function_inventory_candidate(draft, broker, planning_ir, profile, constraints)
        roles = {function.get("public_api_role"): function for function in inventory["functions"] if str(function.get("public_api_role", "")).startswith("runtime_")}
        self.assertEqual(set(roles), {"runtime_create", "runtime_start", "runtime_run", "runtime_destroy"})
        self.assertTrue(all(function["exported"] and function["visibility"] == "public" and function["api_surface"] == "public" for function in roles.values()))
        diagnostics = validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir)
        self.assertNotIn("runtime_lifecycle_api_missing", _error_codes(diagnostics))

    def test_20260612_duplicate_function_replay_repairs_names_and_refs(self) -> None:
        run_id = "20260612_092317_251284"
        logs = _replay(run_id) / "_step_logs"
        profile = _read_json(logs / "004_protocol_profile.json")
        planning_ir = _read_json(logs / "003_planning_ir.json")
        modules = _read_json(logs / "007_5_2b_module_artifacts_candidate.json")
        type_inventory = _read_json(logs / "007_5_3_type_data_inventory_candidate.json")
        candidate = _read_json(logs / "007_5_4a_function_inventory_candidate.json")
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [
                {
                    "module_id": module["module_id"],
                    "name": module.get("name", module["module_id"]),
                    "purpose": module.get("role", ""),
                    "role": module.get("role", ""),
                    "owned_capabilities": [],
                    "support_module": False,
                    "dependencies": module.get("dependencies", []),
                    "artifacts": module.get("artifacts", []),
                }
                for module in modules["modules"]
            ],
            "type_inventory": type_inventory["types"],
            "function_contracts": copy.deepcopy(candidate["functions"]),
            "file_layout": {"files": []},
        }
        self.assertTrue(_has(validate_function_inventory_candidate(candidate, draft["module_artifacts"], draft, profile, planning_ir), "duplicate_function_name"))

        repaired_draft, repaired_candidate, report = repair_function_inventory_symbols(draft, candidate)
        self.assertFalse(report["unrepaired_duplicates"])
        self.assertNotIn("duplicate_function_name", _error_codes(validate_function_inventory_candidate(repaired_candidate, repaired_draft["module_artifacts"], repaired_draft, profile, planning_ir)))
        names = [function["name"] for function in repaired_candidate["functions"]]
        self.assertEqual(len(names), len(set(names)))
        by_id = {function["function_id"]: function for function in repaired_candidate["functions"]}
        self.assertEqual(by_id["fn:transport:mqtt_transport_close"]["name"], "mqtt_transport_close")
        self.assertEqual(by_id["fn:broker:mqtt_transport_close"]["name"], "mqtt_broker_transport_close")

    def test_function_symbol_repair_updates_name_valued_refs_in_dependent_artifacts(self) -> None:
        public_codec = _inventory_function("mqtt_encoder_encode", "codec", function_id="fn:codec:mqtt_encoder_encode")
        internal_network = _inventory_function("mqtt_encoder_encode", "network", function_id="fn:network:mqtt_encoder_encode", public=False)
        draft_public = copy.deepcopy(public_codec)
        draft_network = copy.deepcopy(internal_network)
        draft_network["signature"] = {
            "name": "mqtt_encoder_encode",
            "raw": "int mqtt_encoder_encode(void *self)",
            "return_type": "int",
            "params": [{"name": "self", "type": "void *"}],
        }
        draft_public["call_contracts"] = [
            {
                **_call_edge(internal_network["function_id"], []),
                "param_bindings": [{"param_name": "callback", "value_ref": "mqtt_encoder_encode", "ownership": "borrowed", "nullability": "non_null"}],
                "return_binding": {"policy": "store", "target_ref": "mqtt_encoder_encode", "cleanup_function_id": "mqtt_encoder_encode"},
            }
        ]
        candidate = {
            "schema_version": "function_inventory_candidate/v2",
            "candidate_id": "candidate:round5:duplicate_ref_sync",
            "producer": {"stage": "5.4a_function_inventory", "prompt_name": "test", "prompt_version": "test"},
            "module_id": "all_modules",
            "functions": [copy.deepcopy(public_codec), copy.deepcopy(internal_network)],
            "assumptions": [],
            "unresolved_questions": [],
        }
        draft = {
            "protocol_name": "mqtt",
            "module_artifacts": [{"module_id": "codec"}, {"module_id": "network"}],
            "function_contracts": [draft_public, draft_network],
            "type_inventory": [
                {
                    "type_id": "type:network:mqtt_network_encode_buffer_t",
                    "name": "mqtt_network_encode_buffer_t",
                    "module_id": "network",
                    "visibility": "module_internal",
                    "defined_in": "internal_header",
                    "lifecycle": {"created_by": ["mqtt_encoder_encode"], "initialized_by": ["mqtt_encoder_encode"], "destroyed_by": [], "freed_by": []},
                    "related_functions": ["mqtt_encoder_encode"],
                }
            ],
            "file_layout": {"files": [{"file_id": "file:network", "exports": ["mqtt_encoder_encode"], "implements": ["mqtt_encoder_encode"]}]},
        }
        repaired_draft, repaired_candidate, _ = repair_function_inventory_symbols(draft, candidate)
        repaired_network = next(item for item in repaired_candidate["functions"] if item["function_id"] == "fn:network:mqtt_encoder_encode")
        self.assertEqual(repaired_network["name"], "mqtt_network_encoder_encode")
        lifecycle = repaired_draft["type_inventory"][0]["lifecycle"]
        self.assertEqual(lifecycle["created_by"], ["mqtt_network_encoder_encode"])
        self.assertEqual(lifecycle["initialized_by"], ["mqtt_network_encoder_encode"])
        self.assertEqual(repaired_draft["file_layout"]["files"][0]["exports"], ["mqtt_network_encoder_encode"])
        self.assertEqual(repaired_draft["file_layout"]["files"][0]["implements"], ["mqtt_network_encoder_encode"])
        repaired_contract = repaired_draft["function_contracts"][0]["call_contracts"][0]
        self.assertEqual(repaired_contract["param_bindings"][0]["value_ref"], "mqtt_network_encoder_encode")
        self.assertEqual(repaired_contract["return_binding"]["target_ref"], "mqtt_network_encoder_encode")
        self.assertEqual(repaired_contract["return_binding"]["cleanup_function_id"], "mqtt_network_encoder_encode")

    def test_20260616_header_surface_replay_compiles_to_single_owner_and_reports_rendered_header_diagnostics(self) -> None:
        run_id = "20260616_231307_313900"
        plan = _read_json(_replay(run_id) / "_step_logs" / "007_implementation_plan.json")
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            spec_root = Path(manifest["spec_root"])
            diagnostics = validate_coder_compatibility(spec_root)
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

            codec_path = next(path for path in spec_root.rglob("*_spec.json") if path.name == "codec_spec.json")
            owner_path = next(path for path in spec_root.rglob("*_spec.json") if path.name == "protocol_codec_spec.json")
            codec_spec = _read_json(codec_path)
            owner_spec = _read_json(owner_path)
            self.assertEqual(codec_spec["HEADER"]["DEPENDENCY"], ["src/protocol_codec/protocol_codec.h"])
            self.assertNotIn("src/protocol_codec/codec.h", owner_spec["HEADER"]["DEPENDENCY"])
            self.assertFalse({item["NAME"] for item in codec_spec["HEADER"]["DATA"] if item.get("KIND") == "TYPE"})
            owner_types = {item["NAME"] for item in owner_spec["HEADER"]["DATA"] if item.get("KIND") == "TYPE"}
            self.assertIn("mqtt_packet_t", owner_types)
            self.assertIn("mqtt_bytes_t", owner_types)

            broken_codec = copy.deepcopy(codec_spec)
            broken_owner = copy.deepcopy(owner_spec)
            broken_codec["HEADER"]["DEPENDENCY"] = ["src/protocol_codec/protocol_codec.h"]
            broken_owner["HEADER"]["DEPENDENCY"] = ["src/protocol_codec/codec.h"]
            codec_path.write_text(json.dumps(broken_codec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            owner_path.write_text(json.dumps(broken_owner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            cycle_diags = validate_coder_compatibility(spec_root)
            self.assertIn("coder_public_header_type_cycle", _error_codes(cycle_diags))

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            spec_root = Path(manifest["spec_root"])
            owner_path = next(path for path in spec_root.rglob("*_spec.json") if path.name == "protocol_codec_spec.json")
            owner_spec = _read_json(owner_path)
            public_type = next(item for item in owner_spec["HEADER"]["DATA"] if item.get("KIND") == "TYPE" and item.get("TYPE_SPEC", {}).get("TYPE_KIND") == "STRUCT")
            public_type["TYPE_SPEC"].setdefault("FIELDS", []).append(
                {"NAME": "round5_ssize", "TYPE": "ssize_t", "ROLE": "round5 rendered header diagnostic fixture"}
            )
            owner_path.write_text(json.dumps(owner_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            diagnostics = validate_coder_compatibility(spec_root)
            rendered = next((diag for diag in diagnostics if diag.code == "coder_rendered_header_compile_error"), None)
            self.assertIsNotNone(rendered, [diag.__dict__ for diag in diagnostics])
            self.assertIn("stderr_path=", rendered.message)
            self.assertIn("rendered_header=", rendered.message)


if __name__ == "__main__":
    unittest.main()
