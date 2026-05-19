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
    function_contract_detail_patch_messages,
    function_inventory_candidate_messages,
    module_contracts_candidate_messages,
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
    build_function_detail_context,
    build_function_inventory_context,
    build_module_contract_context,
    build_wire_access_binding_context,
)
from agent.planning.stages.implementation_plan_merger import (
    build_plan_skeleton,
    fallback_calls_allowed,
    fallback_core_design,
    fallback_dependency_repair_patch,
    fallback_file_layout,
    fallback_function_details,
    fallback_function_inventory,
    fallback_module_contracts,
    fallback_wire_access_binding,
    merge_calls_allowed,
    merge_core_design,
    merge_file_layout,
    merge_function_details,
    merge_function_inventory,
    merge_module_contracts,
    merge_wire_access_binding,
)
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.validators.implementation_plan_stages import (
    validate_calls_allowed_candidate,
    validate_core_design_candidate,
    validate_dependency_repair_patch,
    validate_file_layout_candidate,
    validate_function_contract_detail_patch,
    validate_function_inventory_candidate,
    validate_module_contracts_candidate,
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
    for update in candidate["function_contract_updates"]:
        if update["state_access"]:
            update["state_access"][0]["access_kind"] = "bad"
            return
    candidate["function_contract_updates"][0]["state_access"].append({"state_id": "state:missing", "access_kind": "bad", "required": True, "reason": "bad"})


def _break_first_call(candidate: dict) -> None:
    for update in candidate["calls_allowed_updates"]:
        if update["calls_allowed"]:
            update["calls_allowed"][0]["call_kind"] = "bad"
            return
    candidate["calls_allowed_updates"][0]["calls_allowed"].append({"callee_function_id": candidate["calls_allowed_updates"][0]["caller_function_id"], "call_reason": "bad", "required": True, "call_kind": "bad", "trace_ref_keys": [], "status": "assumed"})


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
        modules = fallback_module_contracts(draft, profile, constraints, selected)
        draft = merge_module_contracts(draft, modules)
        inventories = []
        for module in list(draft["module_contracts"]):
            inventory = fallback_function_inventory(draft, module)
            inventories.append(inventory)
            draft = merge_function_inventory(draft, inventory)
        detail_patches = []
        for module in list(draft["module_contracts"]):
            patch = fallback_function_details(draft, str(module["module_id"]))
            detail_patches.append(patch)
            draft = merge_function_details(draft, patch)
        wire = fallback_wire_access_binding(draft, planning_ir)
        draft = merge_wire_access_binding(draft, wire)
        calls = fallback_calls_allowed(draft)
        draft = merge_calls_allowed(draft, calls)
        layout = fallback_file_layout(draft)
        draft = merge_file_layout(draft, layout)
        repair = fallback_dependency_repair_patch(draft, [{"code": "dependency_cycle", "message": "cycle"}])
        plan = build_implementation_plan(planning_ir, profile, constraints, selected)
        return planning_ir, profile, constraints, selected, draft, plan, {
            "core": core,
            "modules": modules,
            "inventory": inventories[0],
            "details": detail_patches[0],
            "wire": wire,
            "calls": calls,
            "layout": layout,
            "repair": repair,
        }

    def test_valid_stage_candidate_fixtures_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            self.assertFalse(validate_core_design_candidate(items["core"], planning_ir, profile, selected, constraints))
            self.assertFalse(validate_module_contracts_candidate(items["modules"], selected, profile, constraints, merge_core_design(build_plan_skeleton(planning_ir, profile, constraints, selected), items["core"])))
            self.assertFalse(validate_function_inventory_candidate(items["inventory"], draft["module_contracts"], draft, profile, planning_ir))
            self.assertFalse(validate_function_contract_detail_patch(items["details"], draft, constraints))
            self.assertFalse(validate_wire_access_binding_patch(items["wire"], draft, planning_ir))
            self.assertFalse(validate_calls_allowed_candidate(items["calls"], draft, selected))
            self.assertFalse(validate_file_layout_candidate(items["layout"], draft))
            self.assertFalse(validate_dependency_repair_patch(items["repair"], draft))

    def test_shape_rejects_extra_missing_and_wrong_enum(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, _, items = self._fixtures(Path(raw_tmp))
            cases = [
                (items["core"], lambda c: validate_core_design_candidate(c, planning_ir, profile, selected, constraints), lambda c: c["canonical_types"][0].__setitem__("kind", "bad")),
                (items["modules"], lambda c: validate_module_contracts_candidate(c, selected, profile, constraints, draft), lambda c: c["module_contracts"][0].__setitem__("status", "bad")),
                (items["inventory"], lambda c: validate_function_inventory_candidate(c, draft["module_contracts"], draft, profile, planning_ir), lambda c: c["functions"][0].__setitem__("function_kind", "bad")),
                (items["details"], lambda c: validate_function_contract_detail_patch(c, draft, constraints), _break_first_state_access),
                (items["wire"], lambda c: validate_wire_access_binding_patch(c, draft, planning_ir), lambda c: c["wire_mapping_entries"][0].__setitem__("direction", "bad")),
                (items["calls"], lambda c: validate_calls_allowed_candidate(c, draft, selected), _break_first_call),
                (items["layout"], lambda c: validate_file_layout_candidate(c, draft), lambda c: c["files"][0].__setitem__("kind", "bad")),
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
            modules["module_contracts"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_module_contracts_candidate(modules, selected, profile, constraints, draft), "unknown_module_contract_module"))
            inventory = copy.deepcopy(items["inventory"])
            inventory["functions"][0]["module_id"] = "missing"
            self.assertTrue(_has(validate_function_inventory_candidate(inventory, draft["module_contracts"], draft, profile, planning_ir), "unknown_function_module"))
            details = copy.deepcopy(items["details"])
            details["function_contract_updates"][0]["function_id"] = "fn:missing"
            self.assertTrue(_has(validate_function_contract_detail_patch(details, draft, constraints), "unknown_function_detail_target"))
            wire = copy.deepcopy(items["wire"])
            wire["wire_mapping_entries"][0]["field_id"] = "field:missing"
            self.assertTrue(_has(validate_wire_access_binding_patch(wire, draft, planning_ir), "unknown_wire_field"))
            calls = copy.deepcopy(items["calls"])
            calls["calls_allowed_updates"][0]["calls_allowed"].append({"callee_function_id": "fn:missing", "call_reason": "bad", "required": True, "call_kind": "utility", "trace_ref_keys": [], "status": "assumed"})
            self.assertTrue(_has(validate_calls_allowed_candidate(calls, draft, selected), "unknown_call_callee"))
            layout = copy.deepcopy(items["layout"])
            layout["function_file_assignments"][0]["function_id"] = "fn:missing"
            self.assertTrue(_has(validate_file_layout_candidate(layout, draft), "layout_assigns_unknown_function"))

    def test_full_plan_and_dependency_graph_are_rejected_as_stage_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            planning_ir, profile, constraints, selected, draft, plan, items = self._fixtures(Path(raw_tmp))
            validators = [
                lambda c: validate_core_design_candidate(c, planning_ir, profile, selected, constraints),
                lambda c: validate_module_contracts_candidate(c, selected, profile, constraints, draft),
                lambda c: validate_function_inventory_candidate(c, draft["module_contracts"], draft, profile, planning_ir),
                lambda c: validate_function_contract_detail_patch(c, draft, constraints),
                lambda c: validate_wire_access_binding_patch(c, draft, planning_ir),
                lambda c: validate_calls_allowed_candidate(c, draft, selected),
                lambda c: validate_file_layout_candidate(c, draft),
                lambda c: validate_dependency_repair_patch(c, draft),
            ]
            for validator in validators:
                self.assertTrue(_has(validator(plan), "invalid_schema_version"))
            for key in ("core", "modules", "inventory", "details", "wire", "calls", "layout", "repair"):
                candidate = copy.deepcopy(items[key])
                candidate["dependency_graph"] = {}
                validator = validators[["core", "modules", "inventory", "details", "wire", "calls", "layout", "repair"].index(key)]
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
            first_module = draft["module_contracts"][0]
            first_module_id = str(first_module["module_id"])
            prompt_payloads = [
                json.loads(core_design_candidate_messages(build_core_design_context(planning_ir, profile, constraints, selected))[1]["content"]),
                json.loads(module_contracts_candidate_messages(build_module_contract_context(draft, profile, constraints, selected))[1]["content"]),
                json.loads(function_inventory_candidate_messages(build_function_inventory_context(draft, first_module))[1]["content"]),
                json.loads(function_contract_detail_patch_messages(build_function_detail_context(draft, first_module_id, constraints))[1]["content"]),
                json.loads(wire_access_binding_patch_messages(build_wire_access_binding_context(draft, planning_ir))[1]["content"]),
                json.loads(calls_allowed_candidate_messages(build_calls_allowed_context(draft, selected))[1]["content"]),
                json.loads(file_layout_candidate_messages(build_file_layout_context(draft, planning_ir, constraints))[1]["content"]),
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
            self.assertIn("contract_kind is a structural category only", joined_rules)
            self.assertIn("status_code, boolean, void, pointer_null, or out_param in error_behavior.return_policy", joined_rules)
            self.assertIn("Parser and serializer functions must not write state", joined_rules)
            self.assertIn("exports_type_ids may contain only canonical type IDs", joined_rules)
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


if __name__ == "__main__":
    unittest.main()
