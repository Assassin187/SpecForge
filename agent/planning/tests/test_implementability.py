from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.common.c_types import compatible_c_type
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
        {
            "NAME": "fixture_handle_destroy",
            "SIGNATURE": destroy["signature"]["RAW"],
            "argument_semantics": [
                {
                    "parameter": "handle", "source_kind": "prior_result",
                    "source_ref": create["id"], "source_type": "fixture_handle_t*",
                }
            ],
        },
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
        "test_vectors": [{"NAME": "runtime_fixture", "INPUT": {}, "EXPECT": {"exit": 0}, "LEVEL": "RUNTIME"}],
    }


def _codes(plan: dict) -> set[str]:
    return {item["code"] for item in analyze_implementability(plan)}


class ImplementabilityTests(unittest.TestCase):
    def test_closed_fixture_is_implementable(self) -> None:
        self.assertEqual(analyze_implementability(_closed_plan()), [])

    def test_direct_call_arguments_require_ordered_typed_providers(self) -> None:
        plan = _closed_plan()
        destroy_call = plan["functions"][-1]["call_contracts"][1]
        destroy_call["argument_semantics"] = [{
            "parameter": "wrong_parameter",
            "source_kind": "prior_result",
            "source_ref": "function:fixture/provider/fixture_handle_create",
            "source_type": "int",
        }]

        codes = _codes(plan)
        self.assertIn("call_argument_provider_mismatch", codes)

        destroy_call["argument_semantics"][0]["parameter"] = "handle"
        codes = _codes(plan)
        self.assertIn("call_argument_type_mismatch", codes)
        self.assertIn("call_argument_source_unresolved", codes)

    def test_tagged_type_is_not_silently_ignored(self) -> None:
        plan = _closed_plan()
        create = plan["functions"][0]
        create["signature"] = {
            "RAW": "struct shared_buffer *fixture_handle_create(void)",
            "NAME": "fixture_handle_create",
            "RETURN": "struct shared_buffer *",
            "PARAMS": [],
        }

        diagnostics = analyze_implementability(plan)

        unresolved = [item for item in diagnostics if item["code"] == "unresolved_type"]
        self.assertTrue(any(item["details"].get("symbol") == "struct shared_buffer" for item in unresolved))

    def test_system_aggregate_header_is_completed_deterministically(self) -> None:
        plan = _closed_plan()
        provider = plan["functions"][0]
        provider["signature"] = {
            "RAW": "struct iovec fixture_handle_create(void)",
            "NAME": "fixture_handle_create",
            "RETURN": "struct iovec",
            "PARAMS": [],
        }

        missing = [item for item in analyze_implementability(plan) if item["code"] == "missing_system_header_dependency"]
        self.assertEqual(missing[0]["details"]["required_type"], "struct iovec")
        self.assertEqual(missing[0]["details"]["required_header"], "sys/uio.h")
        self.assertEqual(missing[0]["details"]["use_mode"], "by_value")
        changes = complete_deterministic_dependencies(plan)

        self.assertIn("sys/uio.h", plan["files"][0]["header_system_dependencies"])
        self.assertTrue(any(item["code"] == "deterministic_system_dependency_completion" for item in changes))
        self.assertNotIn("missing_system_header_dependency", _codes(plan))

    def test_const_pointer_provider_is_compatible_only_in_safe_direction(self) -> None:
        plan = _closed_plan()
        destroy = plan["functions"][1]
        destroy["signature"]["RAW"] = "void fixture_handle_destroy(const fixture_handle_t* handle)"
        destroy["signature"]["PARAMS"][0]["TYPE"] = "const fixture_handle_t*"
        destroy_call = plan["functions"][-1]["call_contracts"][1]
        destroy_call["SIGNATURE"] = destroy["signature"]["RAW"]

        self.assertNotIn("call_argument_type_mismatch", _codes(plan))

        destroy["signature"]["PARAMS"][0]["TYPE"] = "fixture_handle_t*"
        destroy["signature"]["RAW"] = "void fixture_handle_destroy(fixture_handle_t* handle)"
        destroy_call["SIGNATURE"] = destroy["signature"]["RAW"]
        destroy_call["argument_semantics"][0]["source_type"] = "const fixture_handle_t*"
        self.assertIn("call_argument_type_mismatch", _codes(plan))

        self.assertFalse(compatible_c_type("const fixture_handle_t**", "fixture_handle_t**"))

    def test_wire_consumer_requires_typed_view_of_foreign_opaque_type(self) -> None:
        plan = _closed_plan()
        consumer = plan["functions"][-1]
        consumer["signature"] = {
            "RAW": "int main(fixture_handle_t* handle)",
            "NAME": "main",
            "RETURN": "int",
            "PARAMS": [
                {"TYPE": "fixture_handle_t*", "NAME": "handle", "NULLABLE": False, "OWNERSHIP": "BORROWED"}
            ],
        }
        consumer["wire_mapping"] = [{"PACKET": "fixture", "WIRE_FIELD": "value", "STRATEGY": "parse_and_skip"}]

        opaque = [item for item in analyze_implementability(plan) if item["code"] == "opaque_cross_owner_access_unproven"]
        self.assertEqual(opaque[0]["details"]["required_type"], "fixture_handle_t")
        self.assertEqual(opaque[0]["details"]["use_mode"], "requires_complete_representation")

        consumer["access_paths"] = [
            {
                "TARGET": "fixture_handle_t.bytes",
                "TYPE": "const uint8_t *",
                "ACCESSOR": "fixture_handle_data",
            }
        ]
        self.assertNotIn("opaque_cross_owner_access_unproven", _codes(plan))

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

    def test_closed_world_reports_every_missing_provider_service(self) -> None:
        plan = _closed_plan()
        caller = plan["functions"][-1]
        caller["rely"]["FUNC"] = [
            {"NAME": name, "SIGNATURE": f"void {name}(void)"}
            for name in ("lookup_record", "record_session", "session_channel", "channel_metadata")
        ]

        missing = [item for item in analyze_implementability(plan) if item["code"] == "unresolved_callee"]

        self.assertEqual(
            {item["details"]["symbol"] for item in missing},
            {"lookup_record", "record_session", "session_channel", "channel_metadata"},
        )
        self.assertTrue(all(item["compile_critical"] for item in missing))

    def test_runtime_flow_missing_is_compile_critical(self) -> None:
        plan = _closed_plan()
        plan["runtime_entrypoint"] = {
            "main_function": plan["functions"][-1]["id"],
            "startup_services": [plan["functions"][0]["id"]],
            "run_services": [], "cleanup_services": [],
        }
        plan["lifecycle_matrix"] = [{
            "resource_id": "fixture", "type_id": plan["types"][0]["id"],
            "create_function": plan["functions"][0]["id"],
            "use_functions": [], "destroy_function": plan["functions"][1]["id"],
        }]
        plan.pop("runtime_flow", None)

        diagnostic = next(
            item for item in analyze_implementability(plan) if item["code"] == "runtime_flow_missing"
        )
        self.assertTrue(diagnostic["compile_critical"])

    def test_borrowed_provider_cannot_feed_consumed_parameter(self) -> None:
        plan = _closed_plan()
        main = plan["functions"][-1]
        destroy = plan["functions"][1]
        main["signature"]["PARAMS"].append({
            "TYPE": "fixture_handle_t*", "NAME": "borrowed_handle",
            "NULLABLE": False, "OWNERSHIP": "BORROWED",
        })
        destroy["signature"]["PARAMS"][0]["OWNERSHIP"] = "CONSUMED"
        main["call_contracts"][1]["argument_semantics"] = [{
            "parameter": "handle", "source_kind": "caller_param",
            "source_ref": "borrowed_handle", "source_type": "fixture_handle_t*",
        }]

        contradiction = next(
            item for item in analyze_implementability(plan)
            if item["code"] == "ownership_contract_contradiction"
        )
        self.assertTrue(contradiction["compile_critical"])

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
        route["call_contracts"] = [
            {"NAME": "fixture_handle_use", "SIGNATURE": use["signature"]["RAW"], "argument_semantics": []}
        ]
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
        plan["callback_bindings"] = [
            {
                "binding_id": "binding:bad",
                "owner_function_id": plan["functions"][2]["id"],
                "consumer_function_id": register["id"],
                "consumer_parameter": "on_event",
                "callback_type_id": "type:inline_callback",
                "provider_function_id": bad["id"],
                "user_data_source": "none",
                "trace_refs": ["fact:fixture"],
            }
        ]
        self.assertIn("callback_signature_mismatch", _codes(plan))

    def test_executable_requires_exactly_one_main(self) -> None:
        plan = _closed_plan()
        plan["functions"] = plan["functions"][:2]
        plan["files"][1]["functions"] = []
        self.assertIn("runtime_entrypoint_missing", _codes(plan))

        plan = _closed_plan()
        duplicate = dict(plan["functions"][-1])
        duplicate["id"] = "function:fixture/app/duplicate_main"
        plan["functions"].append(duplicate)
        plan["files"][1]["functions"].append(duplicate["id"])
        self.assertIn("runtime_entrypoint_ambiguous", _codes(plan))

    def test_enum_values_must_be_nonempty_and_grounded(self) -> None:
        plan = _closed_plan()
        enum_type = {
            "id": "type:fixture_kind_t",
            "file": "file:fixture/provider",
            "name": "fixture_kind_t",
            "visibility": "PUBLIC",
            "role": "Fixture discriminant.",
            "type_spec": {"TYPE_KIND": "ENUM", "ENUM_VALUES": []},
        }
        plan["types"].append(enum_type)
        plan["files"][0]["types"].append(enum_type["id"])
        self.assertIn("empty_enum_values", _codes(plan))

        enum_type["type_spec"]["ENUM_VALUES"] = [{"NAME": "FIXTURE_ONE", "VALUE": 1, "ROLE": "one"}]
        self.assertIn("enum_value_grounding_missing", _codes(plan))
        enum_type["trace_refs"] = ["fact:fixture_kind"]
        self.assertNotIn("enum_value_grounding_missing", _codes(plan))

    def test_opaque_type_cannot_cross_function_or_callback_abi_by_value(self) -> None:
        plan = _closed_plan()
        destroy = plan["functions"][1]
        destroy["signature"]["RAW"] = "void fixture_handle_destroy(fixture_handle_t handle)"
        destroy["signature"]["PARAMS"][0]["TYPE"] = "fixture_handle_t"
        self.assertIn("opaque_type_by_value", _codes(plan))
        destroy["signature"]["RAW"] = "void fixture_handle_destroy(fixture_handle_t* handle)"
        destroy["signature"]["PARAMS"][0]["TYPE"] = "fixture_handle_t*"

        callback = {
            "id": "type:fixture_callback_t",
            "file": "file:fixture/provider",
            "name": "fixture_callback_t",
            "visibility": "PUBLIC",
            "role": "Fixture callback.",
            "type_spec": {
                "TYPE_KIND": "CALLBACK",
                "CALLBACK_SIGNATURE": "void (*fixture_callback_t)(fixture_handle_t handle)",
            },
        }
        plan["types"].append(callback)
        plan["files"][0]["types"].append(callback["id"])
        self.assertIn("opaque_type_by_value", _codes(plan))

    def test_wire_functions_and_call_edges_reject_semantic_placeholders(self) -> None:
        plan = _closed_plan()
        decoder = _function(
            "function:fixture/provider/decode_frame",
            "file:fixture/provider",
            "decode_frame",
            "bool decode_frame(const uint8_t* bytes, size_t len)",
            "bool",
            [
                {"TYPE": "const uint8_t*", "NAME": "bytes", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                {"TYPE": "size_t", "NAME": "len", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            ],
        )
        decoder["role"] = "Decode one wire frame."
        decoder["wire_obligation"] = {"required": True, "trace_refs": ["fact:minimum_v1"]}
        plan["functions"].append(decoder)
        plan["files"][0]["functions"].append(decoder["id"])
        self.assertIn("required_wire_mapping_missing", _codes(plan))
        decoder["wire_mapping"] = [
            {"PACKET": "fixture", "WIRE_FIELD": "frame", "STRATEGY": "parse_and_skip"}
        ]
        self.assertNotIn("required_wire_mapping_missing", _codes(plan))

        decoder.pop("wire_mapping")
        decoder.pop("wire_obligation")
        self.assertNotIn("required_wire_mapping_missing", _codes(plan))

        contract = plan["functions"][-2]["call_contracts"][0]
        contract.update({"condition": "never", "call_purpose": "register callback", "relation_kind": "CALL"})
        codes = _codes(plan)
        self.assertIn("placeholder_call_edge", codes)

    def test_empty_inventory_has_explicit_task_evaporation_codes(self) -> None:
        plan = _closed_plan()
        plan["functions"] = []
        plan["types"] = []
        plan["test_vectors"] = []
        for file_item in plan["files"]:
            file_item["functions"] = []
            file_item["types"] = []
            file_item["test_vectors"] = []
        diagnostics = analyze_implementability(plan)
        codes = {item["code"] for item in diagnostics}
        self.assertIn("task_evaporation_function_inventory_empty", codes)
        self.assertIn("task_evaporation_type_inventory_empty", codes)
        self.assertIn("task_evaporation_test_inventory_empty", codes)
        stages = {item["code"]: item["authoritative_stage"] for item in diagnostics}
        self.assertEqual(stages["task_evaporation_function_inventory_empty"], "public_artifact_inventory")
        self.assertEqual(stages["task_evaporation_type_inventory_empty"], "public_artifact_inventory")
        self.assertEqual(stages["task_evaporation_test_inventory_empty"], "function_test_vector_design")

    def test_rely_contract_drift_is_a_nonfatal_semantic_diagnostic(self) -> None:
        plan = _closed_plan()
        plan["functions"][-1]["call_contracts"] = []
        diagnostic = next(
            item for item in analyze_implementability(plan) if item["code"] == "call_contract_rely_drift"
        )
        self.assertEqual(diagnostic["owner_layer"], "semantic_closure")
        self.assertEqual(diagnostic["authoritative_stage"], "function_call_contract_closure")

    def test_deterministic_completion_derives_file_and_module_dependencies(self) -> None:
        plan = _closed_plan()
        plan["files"][1]["source_dependencies"] = ["app.h"]
        plan["modules"][1]["dependencies"] = []
        changes = complete_deterministic_dependencies(plan)
        self.assertTrue(changes)
        self.assertIn("provider.h", plan["files"][1]["source_dependencies"])
        self.assertIn("provider", plan["modules"][1]["dependencies"])
        self.assertEqual(analyze_implementability(plan), [])

    def test_deterministic_completion_does_not_create_module_cycle(self) -> None:
        plan = _closed_plan()
        provider, app = plan["modules"]
        provider["dependencies"] = ["app"]
        app["dependencies"] = []

        changes = complete_deterministic_dependencies(plan)

        self.assertEqual(app["dependencies"], [])
        self.assertNotIn(
            "deterministic_module_dependency_completion",
            {item["code"] for item in changes},
        )
        self.assertIn("module_dependency_conflict", _codes(plan))

    def test_typed_callback_binding_derives_provider_dependencies(self) -> None:
        plan = _closed_plan()
        provider = _function(
            "function:fixture/callbacks/on_event",
            "file:fixture/callbacks",
            "on_event",
            "void on_event(int value)",
            "void",
            [{"TYPE": "int", "NAME": "value", "NULLABLE": False, "OWNERSHIP": "BORROWED"}],
        )
        plan["modules"].insert(1, {"id": "module:callbacks", "name": "callbacks", "role": "Callbacks.", "dependencies": []})
        plan["files"].append(
            {
                "id": "file:fixture/callbacks", "module": "callbacks", "trace_id": "fixture/callbacks",
                "role": "Callback providers.", "header_path": "callbacks.h", "source_path": "callbacks.c",
                "header_dependencies": [], "source_dependencies": ["callbacks.h"], "types": [],
                "functions": [provider["id"]],
            }
        )
        plan["functions"].append(provider)
        plan["callback_bindings"] = [
            {
                "binding_id": "binding:event",
                "owner_function_id": "function:fixture/app/main",
                "consumer_function_id": "function:fixture/provider/fixture_handle_create",
                "consumer_parameter": "callback",
                "callback_type_id": "type:event_callback_t",
                "provider_function_id": provider["id"],
                "user_data_source": "none",
                "trace_refs": ["fact:fixture"],
            }
        ]
        complete_deterministic_dependencies(plan)
        app_file = next(item for item in plan["files"] if item["id"] == "file:fixture/app")
        app_module = next(item for item in plan["modules"] if item["name"] == "app")
        self.assertIn("callbacks.h", app_file["source_dependencies"])
        self.assertIn("callbacks", app_module["dependencies"])

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

        dependency_patch = {
            "patch_id": "patch:derived-dependency",
            "operations": [
                {
                    "op": "update",
                    "artifact_kind": "file",
                    "artifact_id": "file:fixture/app",
                    "changes": {"source_dependencies": []},
                    "reason": "Invalidly delete a derived edge.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                    "affected_artifact_ids": ["file:fixture/app"],
                }
            ],
        }
        self.assertTrue(
            any("forbidden fields" in item for item in validate_semantic_patch(plan, dependency_patch))
        )

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

    def test_semantic_patch_respects_remaining_whole_fresh_budget(self) -> None:
        class FakeClient:
            calls = 0

            def __init__(self, api_key_env: str) -> None:
                pass

            def generate_with_usage(self, request):
                FakeClient.calls += 1
                raise AssertionError("budget guard must run before the model call")

        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.common.llm_client.FixedQwenClient", FakeClient
        ):
            with self.assertRaisesRegex(ValueError, "whole_fresh_token_ceiling"):
                request_semantic_patch(
                    {"related_artifacts": {}},
                    api_key_env="UNUSED",
                    log_dir=Path(raw),
                    tokens_used=783000,
                    token_ceiling=783804,
                )

        self.assertEqual(FakeClient.calls, 0)

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

    def test_compile_critical_patch_cannot_update_outside_targeted_slice(self) -> None:
        plan = _closed_plan()
        plan["functions"].pop()
        plan["files"][1]["functions"] = []
        out_of_scope = {
            "patch_id": "out-of-scope",
            "operations": [
                {
                    "op": "update",
                    "artifact_kind": "function",
                    "artifact_id": "function:fixture/provider/fixture_handle_create",
                    "changes": {"role": "Unrelated update."},
                    "reason": "Exercise the targeted slice gate.",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                    "affected_artifact_ids": ["function:fixture/provider/fixture_handle_create"],
                }
            ],
        }
        zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        with tempfile.TemporaryDirectory() as raw, patch(
            "agent.planning.pipeline.request_semantic_patch",
            return_value=(out_of_scope, zero),
        ) as repair:
            _, report, _ = _close_implementability(
                plan,
                {"engineering_rules": [], "open_assumptions": []},
                Path(raw),
                api_key_env="UNUSED",
                resume_from=None,
            )
        self.assertEqual(repair.call_count, 2)
        self.assertTrue(
            any("outside targeted diagnostic slice" in item for item in report["semantic_patch_validation_errors"])
        )


if __name__ == "__main__":
    unittest.main()
