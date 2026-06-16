from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.stages.architecture import select_architecture
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.coder_spec_lowering import (
    extract_c_signature_type_refs,
    lower_signature_for_coder,
    normalize_data_visibility_for_coder,
    normalize_param_ownership_for_coder,
)
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.stages.implementation_plan_merger import _protocol_metadata, normalize_file_layout_candidate
from agent.planning.stages.specs_compiler import compile_spec_bundle
from agent.planning.tests.current_flow_fixtures import current_architecture_candidates, current_implementation_plan
from agent.planning.validators.coder_compat import validate_coder_compatibility
from agent.planning.validators.coder_schema import validate_coder_spec_bundle_against_schema
from agent.coder.generation import render_header
from agent.coder.specs import load_spec_bundle_from_root


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


def _zap_plan() -> dict:
    return {
        "schema_version": "implementation_plan/v1",
        "protocol_name": "zapline",
        "protocol_metadata": {"name": "ZapLine", "protocol_version": "1.2"},
        "target_profile": {"target_role": "relay"},
        "roles": ["fallback_role"],
        "canonical_types": [
            {
                "type_id": "type:zapline_frame",
                "name": "zapline_frame_t",
                "kind": "struct",
                "owner_module_id": "framing",
                "source_message_ids": [],
                "source_field_ids": [],
                "fields": [
                    {"field_name": "opcode", "field_type": "uint8_t", "required": True, "source_field_id": "", "validation_notes": "Frame opcode."},
                    {"field_name": "payload", "field_type": "buffer", "required": True, "source_field_id": "", "validation_notes": "Frame payload bytes."},
                ],
                "enum_values": [],
                "trace_ref_keys": [],
                "status": "supported",
            }
        ],
        "module_artifacts": [
            {
                "module_id": "framing",
                "role": "Encode and inspect ZapLine frames.",
                "dependencies": [],
                "artifacts": [
                    {"name": "zapline_framing_t", "kind": "TYPE", "role": "Opaque framing context."},
                    {"name": "zapline_frame_t", "kind": "TYPE", "role": "Public frame data."},
                    {"name": "zapline_frame_encode", "kind": "FUNC", "role": "Frame encoder public API."},
                ],
            }
        ],
        "file_layout": {
            "files": [
                {
                    "file_id": "file:zapline/framing/framing",
                    "module_id": "framing",
                    "source_path": "zapline/framing/framing.c",
                    "header_path": "zapline/framing/framing.h",
                    "responsibility": "Frame codec unit.",
                    "exports": ["func:framing:encode"],
                    "exports_type_ids": ["type:zapline_frame"],
                    "imports_allowed": [],
                }
            ],
        },
        "function_contracts": [
            {
                "function_id": "func:framing:encode",
                "file_id": "file:zapline/framing/framing",
                "module_id": "framing",
                "name": "zapline_frame_encode",
                "function_kind": "public_api",
                "visibility": "public",
                "api_surface": "public",
                "exported": True,
                "export_reason": "Frame encoding is the module public API.",
                "public_api_role": "frame_encoder",
                "purpose": "Encode one ZapLine frame.",
                "signature": {
                    "raw": "int zapline_frame_encode(zapline_framing_t* ctx, const uint8_t* input, size_t len)",
                    "name": "zapline_frame_encode",
                    "return_type": "int",
                    "params": [
                        {"type": "zapline_framing_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"},
                        {"type": "const uint8_t*", "name": "input", "nullable": False, "ownership": "borrowed"},
                        {"type": "size_t", "name": "len", "nullable": False, "ownership": "borrowed"},
                    ],
                },
                "interface_type_declarations": [{"name": "zapline_frame_t", "visibility": "public", "reason": "Public frame object."}],
                "behavior_contract": {"preconditions": ["ctx != NULL"], "postconditions": ["returns status"], "input": "frame bytes", "action": "encode", "output": "status"},
            },
            {
                "function_id": "func:framing:crc",
                "file_id": "file:zapline/framing/framing",
                "module_id": "framing",
                "name": "zapline_crc_update",
                "function_kind": "internal_helper",
                "visibility": "internal",
                "api_surface": "module_internal",
                "exported": False,
                "export_reason": "",
                "public_api_role": "",
                "purpose": "Update checksum.",
                "signature": {"raw": "static uint16_t zapline_crc_update(uint16_t crc, uint8_t byte)", "name": "zapline_crc_update", "return_type": "uint16_t", "params": []},
                "behavior_contract": {"input": "crc and byte", "action": "update crc", "output": "crc"},
            },
            {
                "function_id": "func:framing:debug",
                "file_id": "file:zapline/framing/framing",
                "module_id": "framing",
                "name": "zapline_debug_dump",
                "function_kind": "internal_helper",
                "visibility": "static",
                "storage_class": "static",
                "api_surface": "static_helper",
                "exported": False,
                "export_reason": "",
                "public_api_role": "",
                "purpose": "Static debug helper.",
                "signature": {"raw": "static void zapline_debug_dump(void)", "name": "zapline_debug_dump", "return_type": "void", "params": []},
                "behavior_contract": {"input": "none", "action": "dump", "output": "none"},
            },
        ],
        "access_path_table": [],
        "module_generation_order": ["framing"],
    }


def _file_spec_json(manifest: dict, filename: str) -> dict:
    path = next(path for path in Path(manifest["spec_root"]).rglob("*_spec.json") if path.name == filename)
    return json.loads(path.read_text(encoding="utf-8"))


def _add_payload_module(plan: dict, *, with_type: bool = True, with_function: bool = False) -> None:
    artifacts = []
    if with_type:
        artifacts.append({"name": "zapline_payload_t", "kind": "TYPE", "role": "Public payload object."})
        plan["canonical_types"].append(
            {
                "type_id": "type:zapline_payload",
                "name": "zapline_payload_t",
                "kind": "struct",
                "owner_module_id": "payload",
                "source_message_ids": [],
                "source_field_ids": [],
                "fields": [{"field_name": "length", "field_type": "size_t", "required": True, "source_field_id": "", "validation_notes": "Payload length."}],
                "enum_values": [],
                "trace_ref_keys": [],
                "status": "supported",
            }
        )
    if with_function:
        artifacts.append({"name": "zapline_payload_open", "kind": "FUNC", "role": "Open payload provider."})
        plan["function_contracts"].append(
            {
                "function_id": "func:payload:open",
                "file_id": "file:zapline/payload/payload",
                "module_id": "payload",
                "name": "zapline_payload_open",
                "function_kind": "public_api",
                "visibility": "public",
                "api_surface": "public",
                "exported": True,
                "export_reason": "Payload provider public API.",
                "public_api_role": "payload_open",
                "purpose": "Open payload provider.",
                "signature": {"raw": "int zapline_payload_open(void)", "name": "zapline_payload_open", "return_type": "int", "params": []},
                "behavior_contract": {"input": "none", "action": "open payload", "output": "status"},
            }
        )
    plan["module_artifacts"].append({"module_id": "payload", "role": "Payload provider.", "dependencies": [], "artifacts": artifacts})
    plan["file_layout"]["files"].append(
        {
            "file_id": "file:zapline/payload/payload",
            "module_id": "payload",
            "source_path": "zapline/payload/payload.c",
            "header_path": "zapline/payload/payload.h",
            "responsibility": "Payload provider unit.",
            "exports": ["func:payload:open"] if with_function else [],
            "exports_type_ids": ["type:zapline_payload"] if with_type else [],
            "imports_allowed": [],
        }
    )
    plan["module_generation_order"] = ["payload", *[item for item in plan["module_generation_order"] if item != "payload"]]


class CoderSchemaLoweringTests(unittest.TestCase):
    def test_existing_example_specs_are_schema_valid(self) -> None:
        for protocol in ("mqtt", "coap"):
            diagnostics = validate_coder_spec_bundle_against_schema(ROOT / "specs-example" / f"{protocol}_specs")
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

    def test_protocol_metadata_extracts_default_port_from_facts(self) -> None:
        planning_ir = {
            "protocol_facts": {
                "protocol_meta": {"protocol_name": "CoAP"},
                "timers_and_constants": [{"name": "default_port", "value_or_rule": "5683 unless overridden"}],
            }
        }

        metadata = _protocol_metadata(planning_ir, {}, "coap")

        self.assertEqual(metadata["default_port"], 5683)

    def test_normalizers_match_coder_schema_enums(self) -> None:
        self.assertEqual(normalize_param_ownership_for_coder("borrowed"), "BORROWED")
        self.assertEqual(normalize_param_ownership_for_coder("value"), "UNKNOWN")
        self.assertEqual(normalize_param_ownership_for_coder("transferred"), "TRANSFER")
        self.assertEqual(normalize_data_visibility_for_coder("internal"), "PRIVATE")

    def test_compiled_planning_bundle_is_strict_schema_valid(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target, target_diags = load_target_profile(_target_profile(tmp))
            self.assertIsNotNone(target, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            candidates = current_architecture_candidates(planning_ir, profile, constraints)
            selected = select_architecture(candidates, profile)
            plan = current_implementation_plan(planning_ir, profile, constraints, selected)
            manifest, _ = compile_spec_bundle(plan, tmp)
            spec_root = Path(manifest["spec_root"])
            self.assertFalse(list(spec_root.glob("planning_*.json")))
            sidecar_paths = [Path(path) for path in manifest["sidecar_paths"]]
            self.assertEqual({path.parent for path in sidecar_paths}, {tmp})
            self.assertTrue(all(path.exists() for path in sidecar_paths))
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            coder_diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in coder_diagnostics if diag.level == "error"])
            bundle = load_spec_bundle_from_root(manifest["spec_root"])
            self.assertEqual(bundle.protocol.name, "mqtt")
            self.assertEqual(bundle.protocol.spec_version, "3.1.1")
            self.assertEqual(bundle.protocol.roles, ["BROKER"])
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertTrue(module_spec.get("TEST_VECTORS"))
            self.assertTrue(module_spec.get("FORBIDDEN_SYMBOLS"))

            for path in Path(manifest["spec_root"]).rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("TRACEABILITY", raw)
                if raw.get("KIND") == "FUNCTION_SPEC":
                    self.assertNotIn("CAPABILITY_IDS", raw)
                    self.assertNotIn("STATE_ACCESS", raw)
                    self.assertNotIn("CALLS_ALLOWED", raw)
                    if raw.get("WIRE_MAPPING"):
                        self.assertTrue(raw.get("TEST_VECTORS"))

    def test_non_mqtt_public_interfaces_artifacts_and_metadata_lowering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(_zap_plan(), Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

            bundle = load_spec_bundle_from_root(manifest["spec_root"])
            self.assertEqual(bundle.protocol.name, "ZapLine")
            self.assertEqual(bundle.protocol.spec_version, "1.2")
            self.assertEqual(bundle.protocol.roles, ["RELAY"])

            file_spec = next(iter(bundle.file_specs_by_trace.values()))
            header_names = [item.name for item in file_spec.header_interfaces]
            source_names = [item.name for item in file_spec.source_interfaces]
            public_types = {item["NAME"]: item for item in file_spec.header_data if item.get("KIND") == "TYPE" and item.get("VISIBILITY") == "PUBLIC"}
            self.assertIn("zapline_frame_encode", header_names)
            self.assertIn("zapline_frame_encode", source_names)
            self.assertNotIn("zapline_crc_update", header_names)
            self.assertNotIn("zapline_debug_dump", header_names)
            self.assertEqual(public_types["zapline_frame_t"]["TYPE_SPEC"]["TYPE_KIND"], "STRUCT")

            rendered = render_header(bundle, file_spec)
            self.assertIn("zapline_frame_encode", rendered)
            self.assertIn("typedef struct zapline_frame", rendered)
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            artifacts = module_spec["MODULES"][0]["ARTIFACTS"]
            artifact_keys = {(item["NAME"], item["KIND"]) for item in artifacts}
            self.assertIn(("zapline_frame_encode", "FUNC"), artifact_keys)
            self.assertIn(("zapline_framing_t", "TYPE"), artifact_keys)
            self.assertIn(("zapline_frame_t", "TYPE"), artifact_keys)
            self.assertNotIn("FILE_SPEC", {item["KIND"] for item in artifacts})
            self.assertFalse([item for item in artifacts if item["NAME"].startswith(("file:", "func:")) or "/" in item["NAME"]])

    def test_planned_module_artifacts_lower_to_module_spec(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["module_artifacts"][0]["artifacts"] = [
            {"name": "zapline_runtime_t", "kind": "TYPE", "role": "Planned runtime handle."},
            {"name": "zapline_runtime_run", "kind": "FUNC", "role": "Run the planned runtime boundary."},
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            artifact_keys = {(item["NAME"], item["KIND"]) for item in module_spec["MODULES"][0]["ARTIFACTS"]}
            self.assertIn(("zapline_runtime_t", "TYPE"), artifact_keys)
            self.assertIn(("zapline_runtime_run", "FUNC"), artifact_keys)

    def test_function_wire_mapping_lowers_rule_and_source(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["access_path_table"] = [
            {
                "access_path_id": "access:opcode",
                "path": "frame.opcode",
                "field_id": "field:opcode",
                "owner_module_id": "framing",
                "c_type": "uint8_t",
                "role": "Frame opcode target.",
            }
        ]
        plan["function_contracts"][0]["access_paths"] = ["access:opcode"]
        plan["function_contracts"][0]["wire_mapping"] = [
            {
                "mapping_id": "wire:opcode",
                "field_id": "field:opcode",
                "message": "ZapFrame",
                "field": "opcode",
                "access_path_id": "",
                "direction": "serialize",
                "strategy": "store_in_field",
                "target_path": "buffer",
                "source_expr": "input[0]",
                "rule": "opcode byte maps directly to frame.opcode",
            }
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            function_specs = []
            for path in Path(manifest["spec_root"]).rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("KIND") == "FUNCTION_SPEC":
                    function_specs.append(raw)
            mapping = next(item for spec in function_specs for item in spec.get("WIRE_MAPPING", []))
            self.assertEqual(mapping["STRATEGY"], "store_in_field")
            self.assertEqual(mapping["TARGET"], "frame.opcode")
            self.assertEqual(mapping["SOURCE"], "input[0]")
            self.assertEqual(mapping["RULE"], "opcode byte maps directly to frame.opcode")

    def test_module_files_prefer_actual_file_layout_over_seed_files(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["module_artifacts"][0]["files"] = ["stale/framing.h", "stale/framing.c"]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertEqual(module_spec["MODULES"][0]["FILES"], ["zapline/framing/framing.h", "zapline/framing/framing.c"])
            bundle = load_spec_bundle_from_root(manifest["spec_root"])
            self.assertEqual(bundle.modules_in_order[0].files, ["zapline/framing/framing.h", "zapline/framing/framing.c"])

    def test_internal_call_contract_lowers_to_function_spec(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["function_contracts"][0]["call_contracts"] = [
            {
                "callee_function_id": "func:framing:crc",
                "call_kind": "utility",
                "required": False,
                "service_requirement_ids": [],
                "call_reason": "Use internal checksum helper.",
                "param_bindings": [],
                "return_binding": {"policy": "use_return_value", "target_ref": "crc", "cleanup_function_id": ""},
                "failure_behavior": "return_error",
            }
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            function_specs = []
            for path in Path(manifest["spec_root"]).rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("KIND") == "FUNCTION_SPEC":
                    function_specs.append(raw)
            function_spec = next(item for item in function_specs if item["SIGNATURE"]["NAME"] == "zapline_frame_encode")
            self.assertEqual(function_spec["CALL_CONTRACTS"][0]["NAME"], "zapline_crc_update")
            self.assertEqual(function_spec["RELY"]["FUNC"][0]["NAME"], "zapline_crc_update")

    def test_public_signature_external_type_lowers_to_header_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        _add_payload_module(plan, with_type=True)
        public_function = plan["function_contracts"][0]
        public_function["signature"]["raw"] = "int zapline_frame_encode(zapline_payload_t* payload, size_t len)"
        public_function["signature"]["params"] = [
            {"type": "zapline_payload_t*", "name": "payload", "nullable": False, "ownership": "borrowed"},
            {"type": "size_t", "name": "len", "nullable": False, "ownership": "borrowed"},
        ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            framing_spec = _file_spec_json(manifest, "framing_spec.json")
            bundle = load_spec_bundle_from_root(manifest["spec_root"])

            self.assertEqual(framing_spec["HEADER"]["DEPENDENCY"], ["zapline/payload/payload.h"])
            self.assertFalse([diag.__dict__ for diag in bundle.diagnostics if diag.level == "error"])

    def test_source_call_dependency_does_not_pollute_header_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        _add_payload_module(plan, with_type=False, with_function=True)
        public_function = plan["function_contracts"][0]
        public_function["calls_allowed"] = ["func:payload:open"]
        public_function["call_contracts"] = [
            {
                "callee_function_id": "func:payload:open",
                "call_kind": "utility",
                "required": True,
                "service_requirement_ids": [],
                "call_reason": "Use payload provider from implementation only.",
                "param_bindings": [],
                "return_binding": {"policy": "use_return_value", "target_ref": "status", "cleanup_function_id": ""},
                "failure_behavior": "return_error",
            }
        ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            framing_spec = _file_spec_json(manifest, "framing_spec.json")

            self.assertIn("zapline/payload/payload.h", framing_spec["SOURCE"]["DEPENDENCY"])
            self.assertNotIn("zapline/payload/payload.h", framing_spec["HEADER"]["DEPENDENCY"])

    def test_same_header_and_primitive_types_do_not_create_project_header_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["function_contracts"][0]["signature"]["raw"] = "bool zapline_frame_encode(zapline_frame_t* frame, const uint8_t* input, size_t len)"
        plan["function_contracts"][0]["signature"]["return_type"] = "bool"
        plan["function_contracts"][0]["signature"]["params"] = [
            {"type": "zapline_frame_t*", "name": "frame", "nullable": False, "ownership": "borrowed"},
            {"type": "const uint8_t*", "name": "input", "nullable": False, "ownership": "borrowed"},
            {"type": "size_t", "name": "len", "nullable": False, "ownership": "borrowed"},
        ]
        plan["type_inventory"] = [
            {
                "type_id": "type:zapline_flags",
                "name": "zapline_flags_t",
                "module_id": "framing",
                "kind": "struct",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "Public primitive/system field container.",
                "fields": [
                    {"field_name": "enabled", "field_type": "bool"},
                    {"field_name": "length", "field_type": "size_t"},
                    {"field_name": "opcode", "field_type": "uint8_t"},
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            framing_spec = _file_spec_json(manifest, "framing_spec.json")

            self.assertEqual(framing_spec["HEADER"]["DEPENDENCY"], [])

    def test_public_system_types_lower_to_header_system_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["function_contracts"][0]["signature"]["raw"] = "ssize_t zapline_frame_encode(struct sockaddr_in* peer, bool enabled, const uint8_t* input, size_t len)"
        plan["function_contracts"][0]["signature"]["return_type"] = "ssize_t"
        plan["function_contracts"][0]["signature"]["params"] = [
            {"type": "struct sockaddr_in*", "name": "peer", "nullable": False, "ownership": "borrowed"},
            {"type": "bool", "name": "enabled", "nullable": False, "ownership": "borrowed"},
            {"type": "const uint8_t*", "name": "input", "nullable": False, "ownership": "borrowed"},
            {"type": "size_t", "name": "len", "nullable": False, "ownership": "borrowed"},
        ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            framing_spec = _file_spec_json(manifest, "framing_spec.json")
            system_dependencies = set(framing_spec["HEADER"]["SYSTEM_DEPENDENCY"])

            self.assertTrue({"sys/types.h", "sys/socket.h", "netinet/in.h", "stdbool.h", "stdint.h", "stddef.h"}.issubset(system_dependencies))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

    def test_strict_coder_compatibility_rejects_missing_rendered_header_system_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["function_contracts"][0]["signature"]["raw"] = "ssize_t zapline_frame_encode(zapline_framing_t* ctx)"
        plan["function_contracts"][0]["signature"]["return_type"] = "ssize_t"
        plan["function_contracts"][0]["signature"]["params"] = [{"type": "zapline_framing_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"}]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            spec_path = next(path for path in Path(manifest["spec_root"]).rglob("framing_spec.json"))
            raw = json.loads(spec_path.read_text(encoding="utf-8"))
            raw["HEADER"]["SYSTEM_DEPENDENCY"] = [item for item in raw["HEADER"].get("SYSTEM_DEPENDENCY", []) if item != "sys/types.h"]
            spec_path.write_text(json.dumps(raw), encoding="utf-8")

            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertTrue(any(diag.code == "coder_rendered_header_compile_error" for diag in diagnostics), [diag.__dict__ for diag in diagnostics])

    def test_public_data_external_type_refs_lower_to_header_dependency(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        _add_payload_module(plan, with_type=True)
        plan["type_inventory"] = [
            {
                "type_id": "type:zapline_envelope",
                "name": "zapline_envelope_t",
                "module_id": "framing",
                "kind": "struct",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "Public envelope with external payload.",
                "fields": [{"field_name": "payload", "field_type": "zapline_payload_t*"}],
            },
            {
                "type_id": "type:zapline_payload_alias",
                "name": "zapline_payload_alias_t",
                "module_id": "framing",
                "kind": "alias",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "Alias to external payload pointer.",
                "ownership_lifetime": "zapline_payload_t*",
            },
            {
                "type_id": "type:zapline_payload_callback",
                "name": "zapline_payload_callback_t",
                "module_id": "framing",
                "kind": "callback_type",
                "visibility": "public",
                "defined_in": "public_header",
                "purpose": "Callback consuming external payload.",
                "callback_signature": {
                    "return_type": "void",
                    "params": [{"name": "payload", "type": "zapline_payload_t*", "nullable": False, "ownership": "BORROWED"}],
                },
            },
        ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            framing_spec = _file_spec_json(manifest, "framing_spec.json")
            bundle = load_spec_bundle_from_root(manifest["spec_root"])

            self.assertEqual(framing_spec["HEADER"]["DEPENDENCY"], ["zapline/payload/payload.h"])
            self.assertFalse([diag.__dict__ for diag in bundle.diagnostics if diag.level == "error"])

    def test_invalid_planning_symbols_lower_to_canonical_c_specs(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        public_function = plan["function_contracts"][0]
        public_function["function_id"] = "func:framing:handler_io_event"
        public_function["name"] = "handler:io_event"
        public_function["signature"] = {
            "raw": "void handler:io_event",
            "name": "handler:io_event",
            "return_type": "int",
            "params": [
                {"type": "zapline_framing_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"},
                {"type": "size_t", "name": "available", "nullable": False, "ownership": "borrowed"},
            ],
        }
        public_function["call_contracts"] = [
            {
                "callee_function_id": "func:framing:crc",
                "call_kind": "utility",
                "required": False,
                "call_reason": "Use checksum helper.",
                "param_bindings": [],
                "return_binding": {"policy": "use_return_value", "target_ref": "crc"},
                "failure_behavior": "return_error",
            }
        ]
        plan["function_contracts"][1]["signature"] = {
            "raw": "static uint16_t zapline_crc_update",
            "name": "zapline_crc_update",
            "return_type": "uint16_t",
            "params": [
                {"type": "uint16_t", "name": "crc", "nullable": False, "ownership": "borrowed"},
                {"type": "uint8_t", "name": "byte", "nullable": False, "ownership": "borrowed"},
            ],
        }

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            function_specs = []
            for path in Path(manifest["spec_root"]).rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("KIND") == "FUNCTION_SPEC":
                    function_specs.append(raw)
            function_spec = next(item for item in function_specs if item["SIGNATURE"]["NAME"] == "handler_io_event")
            self.assertNotIn(":", function_spec["TRACE_ID"])
            self.assertEqual(function_spec["SIGNATURE"]["RAW"], "int handler_io_event(zapline_framing_t* ctx, size_t available)")
            self.assertEqual(function_spec["CALL_CONTRACTS"][0]["SIGNATURE"], "static uint16_t zapline_crc_update(uint16_t crc, uint8_t byte)")
            self.assertEqual(function_spec["CALL_CONTRACTS"][0]["NAME"], function_spec["RELY"]["FUNC"][0]["NAME"])

    def test_dependency_graph_lowers_compile_order_module_dependencies_only(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["module_artifacts"].append(
            {
                "module_id": "relay",
                "role": "Run relay flow using framing.",
                "dependencies": [],
                "artifacts": [{"name": "zapline_relay_run", "kind": "FUNC", "role": "Run relay flow."}],
            }
        )
        plan["file_layout"]["files"].append(
            {
                "file_id": "file:zapline/relay/relay",
                "module_id": "relay",
                "source_path": "zapline/relay/relay.c",
                "header_path": "zapline/relay/relay.h",
                "responsibility": "Relay flow unit.",
                "exports": ["func:relay:run"],
                "exports_type_ids": [],
                "imports_allowed": ["file:zapline/framing/framing"],
            }
        )
        plan["function_contracts"].append(
            {
                "function_id": "func:relay:run",
                "file_id": "file:zapline/relay/relay",
                "module_id": "relay",
                "name": "zapline_relay_run",
                "function_kind": "public_api",
                "visibility": "public",
                "api_surface": "public",
                "exported": True,
                "export_reason": "Relay flow API.",
                "public_api_role": "relay_run",
                "purpose": "Run relay flow.",
                "signature": {"raw": "int zapline_relay_run(zapline_relay_t* ctx)", "name": "zapline_relay_run", "return_type": "int", "params": [{"type": "zapline_relay_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"}]},
                "behavior_contract": {"input": "ctx", "action": "run", "output": "status"},
            }
        )
        plan["dependency_graph"] = {
            "schema_version": "dependency_graph/v1",
            "module_edges": [{"from": "relay", "to": "framing", "kind": "function_call"}],
            "file_edges": [],
            "function_edges": [],
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            modules = {item["NAME"]: item for item in module_spec["MODULES"]}
            self.assertEqual(modules["relay"]["DEPENDENCIES"], [])

        declared_dep_plan = copy.deepcopy(plan)
        declared_dep_plan["dependency_graph"]["module_edges"] = [{"from": "relay", "to": "framing", "kind": "signature_dependency"}]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(declared_dep_plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            modules = {item["NAME"]: item for item in module_spec["MODULES"]}
            self.assertEqual(modules["relay"]["DEPENDENCIES"], ["framing"])
            self.assertLess(module_spec["GENERATION_ORDER"].index("framing"), module_spec["GENERATION_ORDER"].index("relay"))

        declared_dep_plan["dependency_graph"]["module_edges"] = []
        next(item for item in declared_dep_plan["module_artifacts"] if item["module_id"] == "relay")["dependencies"] = ["framing"]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(declared_dep_plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertLess(module_spec["GENERATION_ORDER"].index("framing"), module_spec["GENERATION_ORDER"].index("relay"))

    def test_source_only_main_file_enters_module_files_and_loader(self) -> None:
        plan = copy.deepcopy(_zap_plan())
        plan["file_layout"]["files"].append(
            {
                "file_id": "file:main",
                "module_id": "framing",
                "kind": "source_only_entrypoint",
                "source_path": "main.c",
                "header_path": "",
                "responsibility": "Source-only runtime entrypoint.",
                "exports": [],
                "exports_type_ids": [],
                "implements": ["func:framing:main"],
                "imports_allowed": ["file:zapline/framing/framing"],
            }
        )
        plan["function_contracts"].append(
            {
                "function_id": "func:framing:main",
                "file_id": "file:main",
                "module_id": "framing",
                "name": "main",
                "function_kind": "public_api",
                "coder_function_type": "ENTRYPOINT",
                "visibility": "internal",
                "api_surface": "module_internal",
                "exported": False,
                "export_reason": "",
                "public_api_role": "",
                "purpose": "Start the process.",
                "signature": {
                    "raw": "int main(int argc, char** argv)",
                    "name": "main",
                    "return_type": "int",
                    "params": [
                        {"type": "int", "name": "argc", "nullable": False, "ownership": "borrowed"},
                        {"type": "char**", "name": "argv", "nullable": False, "ownership": "borrowed"},
                    ],
                },
                "behavior_contract": {"input": "argc/argv", "action": "start", "output": "status"},
            }
        )
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertIn("main.c", module_spec["MODULES"][0]["FILES"])
            main_spec = next(path for path in Path(manifest["spec_root"]).rglob("*_spec.json") if path.name == "main_spec.json")
            self.assertNotIn("HEADER", json.loads(main_spec.read_text(encoding="utf-8")))
            bundle = load_spec_bundle_from_root(manifest["spec_root"])
            self.assertIn("main.c", bundle.modules_in_order[0].files)

    def test_protocol_metadata_uses_neutral_fallbacks(self) -> None:
        plan = _zap_plan()
        plan.pop("protocol_metadata")
        plan.pop("target_profile")
        plan["protocol_name"] = ""
        plan["roles"] = []
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertEqual(module_spec["PROTOCOL"]["NAME"], "UNSPECIFIED_PROTOCOL")
            self.assertEqual(module_spec["PROTOCOL"]["SPEC_VERSION"], "unspecified")
            self.assertEqual(module_spec["PROTOCOL"]["ROLES"], ["UNSPECIFIED_ROLE"])

    def test_scalar_target_directives_lower_protocol_metadata(self) -> None:
        plan = _zap_plan()
        plan.pop("protocol_metadata")
        plan.pop("target_profile")
        plan["target_directives_ref"] = {
            "schema_version": "target_directives/v1",
            "directives": {
                "target_role": "relay",
                "protocol_version": "2.0",
                "scope": "ZapLine relay profile",
            },
        }
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            module_spec = json.loads(Path(manifest["module_spec_path"]).read_text(encoding="utf-8"))
            self.assertEqual(module_spec["PROTOCOL"]["NAME"], "zapline")
            self.assertEqual(module_spec["PROTOCOL"]["SPEC_VERSION"], "2.0")
            self.assertEqual(module_spec["PROTOCOL"]["ROLES"], ["RELAY"])
            self.assertEqual(module_spec["PROTOCOL"]["SCOPE"], "ZapLine relay profile")

    def test_coder_semantics_reject_missing_header_and_bad_artifact_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(_zap_plan(), Path(raw_tmp))
            spec_root = Path(manifest["spec_root"])
            file_path = next(path for path in spec_root.rglob("*_spec.json") if path.name == "framing_spec.json")
            file_spec = json.loads(file_path.read_text(encoding="utf-8"))
            file_spec["HEADER"]["INTERFACE"] = []
            file_path.write_text(json.dumps(file_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            diagnostics = validate_coder_compatibility(spec_root)
            self.assertIn("coder_public_source_missing_header", {diag.code for diag in diagnostics if diag.level == "error"})

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(_zap_plan(), Path(raw_tmp))
            module_path = Path(manifest["module_spec_path"])
            module_spec = json.loads(module_path.read_text(encoding="utf-8"))
            module_spec["MODULES"][0]["ARTIFACTS"][0]["NAME"] = "file:zapline/framing"
            module_path.write_text(json.dumps(module_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_artifact_invalid_name", {diag.code for diag in diagnostics if diag.level == "error"})

    def test_coder_semantics_reject_public_lowering_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            plan = _zap_plan()
            plan["function_contracts"][0]["signature"] = {"raw": "", "name": "", "return_type": "", "params": []}
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_public_lowering_unresolved", {diag.code for diag in diagnostics if diag.level == "error"})

    def test_normalized_layout_does_not_export_internal_types_to_coder(self) -> None:
        plan = _zap_plan()
        plan["type_inventory"] = [
            {
                "type_id": "type:zapline_internal_cursor",
                "name": "zapline_internal_cursor_t",
                "module_id": "framing",
                "kind": "view_struct",
                "visibility": "module_internal",
                "defined_in": "internal_header",
            }
        ]
        plan["file_layout"]["files"][0]["exports_type_ids"] = ["type:zapline_frame", "type:zapline_internal_cursor"]
        normalized, stats = normalize_file_layout_candidate({"files": copy.deepcopy(plan["file_layout"]["files"])}, plan)
        self.assertEqual(normalized["files"][0]["exports_type_ids"], ["type:zapline_frame"])
        self.assertGreater(stats["layout_internal_export_dropped"], 0)
        plan["file_layout"]["files"] = normalized["files"]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertNotIn("coder_public_lowering_unresolved", {diag.code for diag in diagnostics if diag.level == "error"})
            file_path = next(path for path in Path(manifest["spec_root"]).rglob("*_spec.json") if path.name == "framing_spec.json")
            file_spec = json.loads(file_path.read_text(encoding="utf-8"))
            public_names = {item["NAME"] for item in file_spec.get("PUBLIC_SYMBOLS", [])}
            self.assertNotIn("zapline_internal_cursor_t", public_names)

    def test_coder_semantics_reject_missing_public_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(_zap_plan(), Path(raw_tmp))
            module_path = Path(manifest["module_spec_path"])
            module_spec = json.loads(module_path.read_text(encoding="utf-8"))
            module_spec["MODULES"][0]["ARTIFACTS"] = [item for item in module_spec["MODULES"][0]["ARTIFACTS"] if item["KIND"] != "FUNC"]
            module_path.write_text(json.dumps(module_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_public_func_missing_artifact", {diag.code for diag in diagnostics if diag.level == "error"})

        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(_zap_plan(), Path(raw_tmp))
            module_path = Path(manifest["module_spec_path"])
            module_spec = json.loads(module_path.read_text(encoding="utf-8"))
            module_spec["MODULES"][0]["ARTIFACTS"] = [item for item in module_spec["MODULES"][0]["ARTIFACTS"] if not (item["KIND"] == "TYPE" and item["NAME"] == "zapline_frame_t")]
            module_path.write_text(json.dumps(module_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_public_data_missing_artifact", {diag.code for diag in diagnostics if diag.level == "error"})

    def test_public_opaque_type_lowering_and_by_value_rejection(self) -> None:
        plan = _zap_plan()
        plan["canonical_types"] = [
            {
                "type_id": "type:zapline_cursor",
                "name": "zapline_cursor_t",
                "kind": "struct",
                "owner_module_id": "framing",
                "source_message_ids": [],
                "source_field_ids": [],
                "fields": [],
                "enum_values": [],
                "trace_ref_keys": [],
                "status": "supported",
            }
        ]
        plan["file_layout"]["files"][0]["exports_type_ids"] = ["type:zapline_cursor"]
        plan["function_contracts"][0]["signature"]["raw"] = "int zapline_frame_encode(zapline_framing_t* ctx, zapline_cursor_t* cursor)"
        plan["function_contracts"][0]["signature"]["params"] = [
            {"type": "zapline_framing_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"},
            {"type": "zapline_cursor_t*", "name": "cursor", "nullable": False, "ownership": "borrowed"},
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])
            file_path = next(path for path in Path(manifest["spec_root"]).rglob("*_spec.json") if path.name == "framing_spec.json")
            file_spec = json.loads(file_path.read_text(encoding="utf-8"))
            type_specs = {item["NAME"]: item["TYPE_SPEC"] for item in file_spec["HEADER"]["DATA"] if item.get("KIND") == "TYPE" and "TYPE_SPEC" in item}
            self.assertEqual(type_specs["zapline_cursor_t"]["TYPE_KIND"], "OPAQUE")

        plan["function_contracts"][0]["signature"]["raw"] = "int zapline_frame_encode(zapline_framing_t* ctx, zapline_cursor_t cursor)"
        plan["function_contracts"][0]["signature"]["params"][1]["type"] = "zapline_cursor_t"
        with tempfile.TemporaryDirectory() as raw_tmp:
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_public_lowering_unresolved", {diag.code for diag in diagnostics if diag.level == "error"})

    def test_coder_semantics_reject_public_signature_unknown_type_and_missing_role(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            plan = _zap_plan()
            plan["function_contracts"][0]["signature"]["raw"] = "int zapline_frame_encode(zapline_framing_t* ctx, unknown_frame_t* frame)"
            plan["function_contracts"][0]["signature"]["params"] = [
                {"type": "zapline_framing_t*", "name": "ctx", "nullable": False, "ownership": "borrowed"},
                {"type": "unknown_frame_t*", "name": "frame", "nullable": False, "ownership": "borrowed"},
            ]
            manifest, _ = compile_spec_bundle(plan, Path(raw_tmp))
            diagnostics = validate_coder_compatibility(manifest["spec_root"])
            self.assertIn("coder_public_signature_unknown_type", {diag.code for diag in diagnostics if diag.level == "error"})

    def test_lowering_renders_callback_type_and_anonymous_function_pointer_params(self) -> None:
        named = {
            "name": "mqtt_topic_tree_match",
            "signature": {
                "name": "mqtt_topic_tree_match",
                "return_type": "void",
                "params": [{"type": "mqtt_topic_match_callback_fn", "name": "callback", "nullable": False, "ownership": "BORROWED"}],
            },
        }
        self.assertIn("mqtt_topic_match_callback_fn callback", lower_signature_for_coder(named)["RAW"])

        anonymous = copy.deepcopy(named)
        anonymous["signature"]["params"][0]["type"] = "void (*)(uint32_t, void*)"
        self.assertIn("void (*callback)(uint32_t, void*)", lower_signature_for_coder(anonymous)["RAW"])

    def test_signature_type_ref_extraction_handles_function_pointer_commas(self) -> None:
        refs = extract_c_signature_type_refs("void mqtt_topic_tree_match(const mqtt_topic_tree_t* tree, void (*callback)(uint32_t sid, void* user_data), void* user_data)")
        raw_refs = {str(ref["raw"]) for ref in refs}
        self.assertIn("const mqtt_topic_tree_t*", raw_refs)
        self.assertNotIn("void (*)(uint32_t", raw_refs)
        self.assertNotIn("void*)", raw_refs)
        callback_refs = extract_c_signature_type_refs("void (*callback)(const mqtt_topic_tree_t* tree, uint32_t sid, void* user_data)")
        self.assertEqual({str(ref["raw"]) for ref in callback_refs}, {"const mqtt_topic_tree_t*"})

    def test_specs_compiler_has_no_protocol_role_special_cases(self) -> None:
        texts = [
            (ROOT / "agent" / "planning" / "stages" / "specs_compiler.py").read_text(encoding="utf-8"),
            (ROOT / "agent" / "planning" / "stages" / "coder_spec_lowering.py").read_text(encoding="utf-8"),
        ]
        for text in texts:
            for forbidden in ('"mqtt"', '"MQTT"', '"broker"', '"BROKER"', '"server"', '"SERVER"', '"client"', '"CLIENT"'):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
