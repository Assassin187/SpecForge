from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.planning.implementability import (
    analyze_implementability,
    apply_semantic_patch,
    complete_deterministic_dependencies,
    merge_semantic_patches,
    request_semantic_patch,
    validate_semantic_patch,
)
from agent.planning.pipeline import _close_implementability


def _function(
    artifact_id: str,
    file_id: str,
    name: str,
    signature: str,
    return_type: str,
    params: list[dict],
    *,
    calls: list[str] | None = None,
    structs: list[str] | None = None,
    visibility: str = "public",
    function_type: str = "ALGORITHM",
) -> dict:
    return {
        "id": artifact_id,
        "file": file_id,
        "trace_id": artifact_id.removeprefix("function:"),
        "name": name,
        "function_type": function_type,
        "visibility": visibility,
        "role": f"Fixture function {name}.",
        "signature": {"RAW": signature, "NAME": name, "RETURN": return_type, "PARAMS": params},
        "rely": {
            "STRUCT": [{"NAME": item, "ROLE": "Fixture type."} for item in structs or []],
            "FUNC": [{"NAME": item, "KIND": "CALL", "ROLE": "Fixture call."} for item in calls or []],
            "VAR": [],
        },
        "call_contracts": [],
        "logic": {"INPUT": "", "ACTION": name, "OUTPUT": "", "INVARIANTS_USED": []},
        "trace_refs": ["fact:fixture"],
        "test_vectors": [],
    }


def _closed_plan() -> dict:
    handle_param = {"TYPE": "fixture_handle_t*", "NAME": "handle", "NULLABLE": False, "OWNERSHIP": "BORROWED"}
    create = _function(
        "function:fixture/provider/fixture_handle_create",
        "file:fixture/provider",
        "fixture_handle_create",
        "fixture_handle_t* fixture_handle_create(void)",
        "fixture_handle_t*",
        [],
    )
    destroy = _function(
        "function:fixture/provider/fixture_handle_destroy",
        "file:fixture/provider",
        "fixture_handle_destroy",
        "void fixture_handle_destroy(fixture_handle_t* handle)",
        "void",
        [handle_param],
        structs=["fixture_handle_t"],
    )
    main = _function(
        "function:fixture/app/main",
        "file:fixture/app",
        "main",
        "int main(int argc, char** argv)",
        "int",
        [
            {"TYPE": "int", "NAME": "argc", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            {"TYPE": "char**", "NAME": "argv", "NULLABLE": True, "OWNERSHIP": "BORROWED"},
        ],
        calls=["fixture_handle_create", "fixture_handle_destroy"],
        structs=["fixture_handle_t"],
        visibility="private",
        function_type="ENTRYPOINT",
    )
    main["call_contracts"] = [
        {"NAME": "fixture_handle_create", "SIGNATURE": create["signature"]["RAW"]},
        {"NAME": "fixture_handle_destroy", "SIGNATURE": destroy["signature"]["RAW"]},
    ]
    return {
        "protocol": {"name": "Fixture", "slug": "fixture", "spec_version": "1", "roles": ["SERVER"]},
        "modules": [
            {"id": "module:provider", "name": "provider", "role": "Own resource.", "dependencies": []},
            {"id": "module:app", "name": "app", "role": "Run process.", "dependencies": ["provider"]},
        ],
        "files": [
            {
                "id": "file:fixture/provider",
                "module": "provider",
                "trace_id": "fixture/provider",
                "role": "Provider.",
                "header_path": "provider.h",
                "source_path": "provider.c",
                "header_dependencies": [],
                "source_dependencies": ["provider.h"],
                "types": ["type:fixture_handle_t"],
                "functions": [create["id"], destroy["id"]],
            },
            {
                "id": "file:fixture/app",
                "module": "app",
                "trace_id": "fixture/app",
                "role": "Application.",
                "header_path": "app.h",
                "source_path": "app.c",
                "header_dependencies": [],
                "source_dependencies": ["app.h", "provider.h"],
                "types": [],
                "functions": [main["id"]],
            },
        ],
        "types": [
            {
                "id": "type:fixture_handle_t",
                "file": "file:fixture/provider",
                "name": "fixture_handle_t",
                "visibility": "PUBLIC",
                "role": "Owns a fixture resource.",
                "type_spec": {"TYPE_KIND": "OPAQUE"},
            }
        ],
        "functions": [create, destroy, main],
        "engineering_decisions": [],
        "open_assumptions": [],
        "architecture": {},
        "structured_planning_stages": [],
    }


def _codes(plan: dict) -> set[str]:
    return {item["code"] for item in analyze_implementability(plan)}


class ImplementabilityTests(unittest.TestCase):
    def test_closed_fixture_is_implementable(self) -> None:
        self.assertEqual(analyze_implementability(_closed_plan()), [])

    def test_private_symbol_cannot_cross_files(self) -> None:
        plan = _closed_plan()
        plan["functions"][1]["visibility"] = "private"
        plan["types"][0]["visibility"] = "PRIVATE"
        codes = _codes(plan)
        self.assertIn("cross_file_private_function", codes)
        self.assertIn("cross_file_private_type", codes)

    def test_foreign_type_requires_owner_header(self) -> None:
        plan = _closed_plan()
        plan["files"][1]["source_dependencies"] = ["app.h"]
        self.assertIn("foreign_type_dependency_missing", _codes(plan))

    def test_non_main_source_requires_planned_header(self) -> None:
        plan = _closed_plan()
        plan["files"][1]["header_path"] = None
        self.assertIn("file_header_missing", _codes(plan))

    def test_unresolved_callee_is_blocking(self) -> None:
        plan = _closed_plan()
        plan["functions"][2]["rely"]["FUNC"].append({"NAME": "missing_service", "KIND": "CALL", "ROLE": "Missing."})
        self.assertIn("unresolved_callee", _codes(plan))

    def test_opaque_type_requires_constructor(self) -> None:
        plan = _closed_plan()
        plan["functions"][0]["signature"]["RETURN"] = "void"
        plan["functions"][0]["signature"]["RAW"] = "void fixture_handle_create(void)"
        self.assertIn("opaque_constructor_missing", _codes(plan))

    def test_owned_opaque_type_requires_cleanup(self) -> None:
        plan = _closed_plan()
        plan["functions"][1]["name"] = "fixture_handle_touch"
        self.assertIn("opaque_destructor_missing", _codes(plan))

    def test_cross_module_call_requires_access_service(self) -> None:
        plan = _closed_plan()
        handle_param = {"TYPE": "fixture_handle_t*", "NAME": "handle", "NULLABLE": False, "OWNERSHIP": "BORROWED"}
        use = _function(
            "function:fixture/provider/fixture_handle_use",
            "file:fixture/provider",
            "fixture_handle_use",
            "void fixture_handle_use(fixture_handle_t* handle)",
            "void",
            [handle_param],
            structs=["fixture_handle_t"],
        )
        route = _function(
            "function:fixture/app/route",
            "file:fixture/app",
            "route",
            "void route(void)",
            "void",
            [],
            calls=["fixture_handle_use"],
        )
        plan["functions"].extend([use, route])
        plan["files"][0]["functions"].append(use["id"])
        plan["files"][1]["functions"].append(route["id"])
        self.assertIn("access_service_missing", _codes(plan))

    def test_module_dependency_must_match_foreign_reference(self) -> None:
        plan = _closed_plan()
        plan["modules"][1]["dependencies"] = []
        self.assertIn("module_dependency_conflict", _codes(plan))

    def test_callback_provider_and_signature_must_close(self) -> None:
        plan = _closed_plan()
        callback_param = {
            "TYPE": "void (*)(fixture_handle_t*, void*)",
            "NAME": "on_event",
            "NULLABLE": False,
            "OWNERSHIP": "BORROWED",
        }
        register = _function(
            "function:fixture/provider/register_callback",
            "file:fixture/provider",
            "register_callback",
            "void register_callback(void (*on_event)(fixture_handle_t*, void*))",
            "void",
            [callback_param],
            structs=["fixture_handle_t"],
        )
        plan["functions"].append(register)
        plan["files"][0]["functions"].append(register["id"])
        plan["functions"][2]["rely"]["FUNC"].append({"NAME": "register_callback", "KIND": "CALL", "ROLE": "Register callback."})
        self.assertIn("callback_provider_missing", _codes(plan))

        bad = _function(
            "function:fixture/app/bad_callback",
            "file:fixture/app",
            "bad_callback",
            "void bad_callback(int value, void* user_data)",
            "void",
            [
                {"TYPE": "int", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                {"TYPE": "void*", "NAME": "user_data", "NULLABLE": True, "OWNERSHIP": "BORROWED"},
            ],
            visibility="private",
        )
        plan["functions"].append(bad)
        plan["files"][1]["functions"].append(bad["id"])
        plan["functions"][2]["rely"]["FUNC"].append({"NAME": "bad_callback", "KIND": "CALL", "ROLE": "Callback candidate."})
        plan["functions"][2]["logic"]["ACTION"] = "Register bad_callback."
        self.assertIn("callback_signature_mismatch", _codes(plan))

    def test_executable_requires_exactly_one_main(self) -> None:
        plan = _closed_plan()
        plan["functions"] = plan["functions"][:2]
        plan["files"][1]["functions"] = []
        self.assertIn("runtime_entrypoint_missing", _codes(plan))

    def test_deterministic_completion_derives_file_and_module_dependencies(self) -> None:
        plan = _closed_plan()
        plan["files"][1]["source_dependencies"] = ["app.h"]
        plan["modules"][1]["dependencies"] = []
        changes = complete_deterministic_dependencies(plan)
        self.assertTrue(changes)
        self.assertIn("provider.h", plan["files"][1]["source_dependencies"])
        self.assertIn("provider", plan["modules"][1]["dependencies"])
        self.assertEqual(analyze_implementability(plan), [])

    def test_call_contract_signature_is_recovered_only_from_canonical_callee(self) -> None:
        plan = _closed_plan()
        contract = plan["functions"][-1]["call_contracts"][0]
        contract["SIGNATURE"] = contract["NAME"]
        changes = complete_deterministic_dependencies(plan)
        callee = next(item for item in plan["functions"] if item["name"] == contract["NAME"])
        self.assertEqual(contract["SIGNATURE"], callee["signature"]["RAW"])
        self.assertIn("deterministic_call_contract_signature_completion", {item["code"] for item in changes})
        self.assertNotIn("call_contract_signature_mismatch", _codes(plan))

    def test_planned_symbol_removes_only_exact_stale_forbidden_entry(self) -> None:
        plan = _closed_plan()
        plan["forbidden_symbols"] = [
            {"NAME": "main", "KIND": "", "REASON": "stale"},
            {"NAME": "main_*", "KIND": "", "REASON": "pattern remains"},
        ]
        changes = complete_deterministic_dependencies(plan)
        self.assertEqual([item["NAME"] for item in plan["forbidden_symbols"]], ["main_*"])
        self.assertIn("deterministic_forbidden_symbol_reconciliation", {item["code"] for item in changes})

    def test_fresh_failure_regression_private_planned_function_wins_over_exact_forbidden(self) -> None:
        plan = _closed_plan()
        private_function = plan["functions"][-1]
        plan["forbidden_symbols"] = [{"NAME": private_function["name"], "KIND": "FUNC", "REASON": "stale"}]
        changes = complete_deterministic_dependencies(plan)
        self.assertEqual(plan["forbidden_symbols"], [])
        self.assertIn("deterministic_forbidden_symbol_reconciliation", {item["code"] for item in changes})

    def test_valid_semantic_patch_restores_entrypoint_closure(self) -> None:
        plan = _closed_plan()
        main = plan["functions"].pop()
        plan["files"][1]["functions"] = []
        patch = {
            "patch_id": "patch:add-main",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": main["id"],
                    "artifact": main,
                    "reason": "Restore the unique executable entrypoint and cleanup chain.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_EXECUTABLE"]},
                    "affected_artifact_ids": ["module:app", "file:fixture/app"],
                }
            ],
        }
        self.assertEqual(validate_semantic_patch(plan, patch), [])
        repaired = apply_semantic_patch(plan, patch)
        self.assertEqual(analyze_implementability(repaired), [])

    def test_patch_can_update_an_artifact_added_earlier_in_the_same_patch(self) -> None:
        plan = _closed_plan()
        added = {
            "id": "type:extra_t",
            "file": "file:fixture/app",
            "name": "extra_t",
            "visibility": "PRIVATE",
            "role": "Initial role.",
            "type_spec": {"TYPE_KIND": "OPAQUE"},
        }
        patch_value = {
            "patch_id": "patch:add-then-update",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "type",
                    "artifact_id": added["id"],
                    "artifact": added,
                    "reason": "Add helper type.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                    "affected_artifact_ids": ["file:fixture/app"],
                },
                {
                    "op": "update",
                    "artifact_kind": "type",
                    "artifact_id": added["id"],
                    "changes": {"role": "Final role."},
                    "reason": "Complete helper metadata.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                    "affected_artifact_ids": ["file:fixture/app"],
                },
            ],
        }
        self.assertEqual(validate_semantic_patch(plan, patch_value), [])
        repaired = apply_semantic_patch(plan, patch_value)
        self.assertEqual(next(item for item in repaired["types"] if item["id"] == added["id"])["role"], "Final role.")

    def test_correction_delta_merges_after_primary_add(self) -> None:
        plan = _closed_plan()
        added = {
            "id": "type:extra_t",
            "file": "file:fixture/app",
            "name": "extra_t",
            "visibility": "PRIVATE",
            "role": "Initial role.",
            "type_spec": {"TYPE_KIND": "OPAQUE"},
        }
        metadata = {
            "reason": "Fixture closure.",
            "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
            "affected_artifact_ids": ["file:fixture/app"],
        }
        primary = {"patch_id": "primary", "operations": [{"op": "add", "artifact_kind": "type", "artifact_id": added["id"], "artifact": added, **metadata}]}
        correction = {"patch_id": "correction", "operations": [{"op": "update", "artifact_kind": "type", "artifact_id": added["id"], "changes": {"role": "Final role."}, **metadata}]}
        merged = merge_semantic_patches(primary, correction)
        self.assertEqual(validate_semantic_patch(plan, merged), [])
        repaired = apply_semantic_patch(plan, merged)
        self.assertEqual(next(item for item in repaired["types"] if item["id"] == added["id"])["role"], "Final role.")

    def test_illegal_semantic_patch_is_rejected_explicitly(self) -> None:
        plan = _closed_plan()
        patch = {
            "patch_id": "patch:illegal",
            "operations": [
                {
                    "op": "update",
                    "artifact_kind": "function",
                    "artifact_id": "function:missing",
                    "changes": {"protocol_behavior": "invented"},
                    "reason": "Invalid fixture.",
                    "provenance": {"kind": "protocol_fact", "refs": []},
                    "affected_artifact_ids": [],
                }
            ],
        }
        errors = validate_semantic_patch(plan, patch)
        self.assertTrue(any("unknown stable ID" in item for item in errors))
        self.assertTrue(any("forbidden fields" in item for item in errors))

    def test_bounded_patch_correction_can_restore_closure(self) -> None:
        plan = _closed_plan()
        main = plan["functions"].pop()
        plan["files"][1]["functions"] = []
        invalid = {"patch_id": "invalid", "operations": []}
        valid = {
            "patch_id": "patch:add-main",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": main["id"],
                    "artifact": main,
                    "reason": "Restore executable closure.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_EXECUTABLE"]},
                    "affected_artifact_ids": ["module:app", "file:fixture/app"],
                }
            ],
        }
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.pipeline.request_semantic_patch",
            side_effect=[(invalid, usage), (valid, usage)],
        ) as repair:
            _, report, total_usage = _close_implementability(
                plan,
                {"engineering_rules": [], "open_assumptions": []},
                Path(raw),
                api_key_env="UNUSED",
                resume_from=None,
            )
        self.assertEqual(repair.call_count, 2)
        self.assertTrue(report["success"], report)
        self.assertEqual(total_usage["total_tokens"], 6)

    def test_correction_receives_structural_and_residual_errors_together(self) -> None:
        plan = _closed_plan()
        main = plan["functions"].pop()
        plan["files"][1]["functions"] = []
        invalid = {
            "patch_id": "invalid-extra-field",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "type",
                    "artifact_id": "type:extra_t",
                    "artifact": {
                        "id": "type:extra_t",
                        "file": "file:fixture/app",
                        "name": "extra_t",
                        "visibility": "PRIVATE",
                        "role": "Unused fixture type.",
                        "type_spec": {"TYPE_KIND": "OPAQUE"},
                        "protocol_behavior": "forbidden",
                    },
                    "reason": "Exercise combined validation feedback.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_EXECUTABLE"]},
                    "affected_artifact_ids": ["file:fixture/app"],
                }
            ],
        }
        valid = {
            "patch_id": "patch:add-main",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": main["id"],
                    "artifact": main,
                    "reason": "Restore executable closure.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_EXECUTABLE"]},
                    "affected_artifact_ids": ["module:app", "file:fixture/app"],
                }
            ],
        }
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

        def respond(*args, previous_patch=None, validation_errors=None, **kwargs):
            if previous_patch is None:
                return invalid, usage
            self.assertTrue(any("forbidden fields" in item for item in validation_errors))
            self.assertTrue(any("residual closure runtime_entrypoint_missing" in item for item in validation_errors))
            return valid, usage

        with tempfile.TemporaryDirectory() as raw, patch("agent.planning.pipeline.request_semantic_patch", side_effect=respond):
            _, report, _ = _close_implementability(
                plan,
                {"engineering_rules": [], "open_assumptions": []},
                Path(raw),
                api_key_env="UNUSED",
                resume_from=None,
            )
        self.assertTrue(report["success"], report)

    def test_correction_prompt_names_required_caller_updates(self) -> None:
        captured = {}

        class FakeClient:
            def __init__(self, api_key_env: str) -> None:
                pass

            def generate_with_usage(self, request):
                captured["request"] = request
                return SimpleNamespace(
                    content=json.dumps({"patch_id": "fixture", "operations": []}),
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                )

        payload = {
            "related_artifacts": {
                "functions": [{"id": "function:fixture/app/caller", "name": "caller"}],
            }
        }
        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            request_semantic_patch(
                payload,
                api_key_env="UNUSED",
                log_dir=Path(raw),
                previous_patch={"patch_id": "primary", "operations": []},
                validation_errors=[
                    "residual closure access_service_missing: Function caller has no explicit provider/access path for fixture_handle_t"
                ],
            )
        task = json.loads(captured["request"].messages[-1]["content"])
        self.assertEqual(task["required_dependency_update_checklist"][0]["caller_artifact_id"], "function:fixture/app/caller")
        self.assertEqual(task["required_dependency_update_checklist"][0]["required_provider_return_type"], "fixture_handle_t")
        self.assertNotIn("previous_patch", task)
        self.assertIn("diagnostic_groups", task)

    def test_truncated_patch_records_usage_and_parse_failure(self) -> None:
        class FakeClient:
            def __init__(self, api_key_env: str) -> None:
                pass

            def generate_with_usage(self, request):
                return SimpleNamespace(
                    content='{"patch_id":"truncated","operations":[',
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7, total_tokens=12),
                )

        with tempfile.TemporaryDirectory() as raw, patch("agent.common.llm_client.FixedQwenClient", FakeClient):
            with self.assertRaisesRegex(ValueError, "semantic_patch_json_parse_failure"):
                request_semantic_patch(
                    {"related_artifacts": {}},
                    api_key_env="UNUSED",
                    log_dir=Path(raw),
                )
            usage = json.loads((Path(raw) / "01_semantic_patch_usage.json").read_text())
            failure = json.loads((Path(raw) / "01_semantic_patch_failure.json").read_text())
        self.assertEqual(usage["total_tokens"], 12)
        self.assertEqual(failure["kind"], "json_parse_failure")

    def test_compile_resume_recovers_a_stored_validated_correction(self) -> None:
        plan = _closed_plan()
        main = plan["functions"].pop()
        plan["files"][1]["functions"] = []
        correction = {
            "patch_id": "patch:add-main",
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": main["id"],
                    "artifact": main,
                    "reason": "Restore executable closure.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_EXECUTABLE"]},
                    "affected_artifact_ids": ["module:app", "file:fixture/app"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.pipeline.request_semantic_patch",
            side_effect=AssertionError("compile_specs resume must not call the model"),
        ):
            semantic_root = Path(raw) / "semantic_closure"
            semantic_root.mkdir()
            (semantic_root / "02_patch_correction_response.raw.txt").write_text(json.dumps(correction), encoding="utf-8")
            _, report, usage = _close_implementability(
                plan,
                {"engineering_rules": [], "open_assumptions": []},
                Path(raw),
                api_key_env="UNUSED",
                resume_from="compile_specs",
            )
        self.assertTrue(report["success"], report)
        self.assertTrue(report["semantic_patch_reused"])
        self.assertEqual(usage["total_tokens"], 0)

    def test_patch_failure_stops_after_targeted_correction_with_diagnostic(self) -> None:
        plan = _closed_plan()
        plan["functions"].pop()
        plan["files"][1]["functions"] = []
        invalid = {"patch_id": "invalid", "operations": []}
        zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.pipeline.request_semantic_patch",
            return_value=(invalid, zero),
        ) as repair:
            _, report, _ = _close_implementability(
                plan,
                {"engineering_rules": [], "open_assumptions": []},
                Path(raw),
                api_key_env="UNUSED",
                resume_from=None,
            )
        self.assertEqual(repair.call_count, 2)
        self.assertFalse(report["success"])
        self.assertIn("semantic_patch_invalid", {item["code"] for item in report["final_diagnostics"]})


if __name__ == "__main__":
    unittest.main()
