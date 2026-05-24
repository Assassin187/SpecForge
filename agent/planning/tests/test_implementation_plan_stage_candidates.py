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
    function_signature_patch_messages,
    module_artifacts_candidate_messages,
    runtime_entrypoint_candidate_messages,
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
    build_function_signature_context,
    build_module_artifact_context,
    build_runtime_entrypoint_context,
    build_wire_access_binding_context,
)
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
    fallback_wire_access_binding,
    merge_calls_allowed,
    merge_core_design,
    merge_file_layout,
    merge_function_behavior,
    merge_function_inventory,
    merge_function_signatures,
    merge_module_artifacts,
    merge_runtime_entrypoint,
    merge_wire_access_binding,
)
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.validators.implementation_plan_stages import (
    validate_calls_allowed_candidate,
    validate_core_design_candidate,
    validate_dependency_repair_patch,
    validate_file_layout_candidate,
    validate_function_behavior_contract_patch,
    validate_function_inventory_candidate,
    validate_function_signature_patch,
    validate_module_artifacts_candidate,
    validate_runtime_entrypoint_candidate,
    validate_wire_access_binding_patch,
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

    def test_valid_stage_candidate_fixtures_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            self.assertFalse(validate_core_design_candidate(items["core"], planning_ir, profile, selected, constraints))
            self.assertFalse(validate_module_artifacts_candidate(items["modules"], selected, profile, constraints, merge_core_design(build_plan_skeleton(planning_ir, profile, constraints, selected), items["core"])))
            self.assertFalse(validate_function_inventory_candidate(items["inventory"], draft["module_artifacts"], draft, profile, planning_ir))
            self.assertFalse(validate_function_signature_patch(items["signature"], draft))
            self.assertFalse(validate_function_behavior_contract_patch(items["behavior"], draft, constraints))
            self.assertFalse(validate_wire_access_binding_patch(items["wire"], draft, planning_ir))
            self.assertFalse(validate_calls_allowed_candidate(items["calls"], draft, selected))
            self.assertFalse(validate_file_layout_candidate(items["layout"], draft))
            self.assertFalse(validate_runtime_entrypoint_candidate(items["runtime"], draft))
            self.assertFalse(validate_dependency_repair_patch(items["repair"], draft))

    def test_shape_rejects_extra_missing_and_wrong_enum(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            cases = [
                (items["core"], lambda c: validate_core_design_candidate(c, planning_ir, profile, selected, constraints), lambda c: c["canonical_types"][0].__setitem__("kind", "bad")),
                (items["modules"], lambda c: validate_module_artifacts_candidate(c, selected, profile, constraints, draft), lambda c: c["modules"][0]["artifacts"][0].__setitem__("kind", "bad")),
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

    def test_key_flow_function_inventory_requires_lifecycle_functions(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, _, _, draft, _, items = self._fixtures(Path(raw_tmp))
            key_module = next(module for module in draft["module_artifacts"] if "role_composition" in module["owned_capabilities"])
            inventory = next(item for item in items["inventories"] if item["module_id"] == key_module["module_id"])
            self.assertFalse(validate_function_inventory_candidate(inventory, draft["module_artifacts"], draft, profile, planning_ir))

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
            for key in ("core", "modules", "inventory", "signature", "behavior", "wire", "calls", "layout", "runtime", "repair"):
                candidate = copy.deepcopy(items[key])
                candidate["dependency_graph"] = {}
                validator = validators[["core", "modules", "inventory", "signature", "behavior", "wire", "calls", "layout", "runtime", "repair"].index(key)]
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

    def test_stage_prompts_expose_semantic_validator_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, _ = self._fixtures(Path(raw_tmp))
            first_module = draft["module_artifacts"][0]
            first_module_id = str(first_module["module_id"])
            first_functions = [item for item in draft["function_contracts"] if item.get("module_id") == first_module_id][:4]
            prompt_payloads = [
                json.loads(core_design_candidate_messages(build_core_design_context(planning_ir, profile, constraints, selected))[1]["content"]),
                json.loads(module_artifacts_candidate_messages(build_module_artifact_context(draft, profile, constraints, selected))[1]["content"]),
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
                "producer": {"stage": "5.4e_call_planning", "prompt_name": "calls_allowed_candidate_prompt", "prompt_version": "test"},
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
