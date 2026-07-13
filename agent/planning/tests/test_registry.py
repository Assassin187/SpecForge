from __future__ import annotations

import unittest
from pathlib import Path

from agent.planning.facts import read_json
from agent.planning.registry import (
    CanonicalPlanningRegistry,
    RegistryBindingError,
    RegistryInvariantError,
    build_registry,
    register_semantic_patch_additions,
)


ROOT = Path(__file__).resolve().parents[3]
RUN3_STAGES = ROOT / "agent/planning/out/mqtt_compile_goal_run3/_planning/stage_logs"


def _run3_artifacts() -> list[dict]:
    stages = (
        ("module_file_plan", "03_module_file_plan"),
        ("public_artifact_inventory", "04_public_artifact_inventory"),
        ("type_and_access_path_design", "05_type_and_access_path_design"),
        ("function_interface_design", "06_function_interface_design"),
        ("function_test_vector_design", "09_function_test_vector_design"),
    )
    return [{"stage_id": stage_id, "artifact": read_json(RUN3_STAGES / directory / "artifact.json")} for stage_id, directory in stages]


class RegistryTests(unittest.TestCase):
    def test_split_source_does_not_claim_dedicated_header_alias(self) -> None:
        artifacts = [
            {
                "stage_id": "module_file_plan",
                "artifact": {
                    "modules": [{"name": "codec"}],
                    "files": [
                        {
                            "id": "codec.h",
                            "module": "codec",
                            "header_path": "include/codec.h",
                            "source_path": None,
                        },
                        {
                            "id": "codec.c",
                            "module": "codec",
                            "header_path": "include/codec.h",
                            "source_path": "src/codec.c",
                        },
                    ],
                },
            },
            {
                "stage_id": "public_artifact_inventory",
                "artifact": {
                    "types": [],
                    "constants_or_macros": [],
                    "functions": [
                        {
                            "symbol": "codec_feed",
                            "owner_file": "codec.h",
                            "visibility": "public",
                        }
                    ],
                },
            },
        ]
        registry = build_registry(artifacts, protocol_slug="fixture")
        header = registry.resolve("codec.h", expected_kinds={"file"})
        source = registry.resolve("codec.c", expected_kinds={"file"})
        function = registry.resolve("codec_feed", expected_kinds={"function"})
        self.assertNotEqual(header["artifact_id"], source["artifact_id"])
        self.assertEqual(function["owner_file_id"], header["artifact_id"])

    def test_run3_registry_migration_covers_all_artifact_kinds(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        counts = {kind: len(registry.typed_view({kind})) for kind in ("module", "file", "type", "callback", "function", "constant", "test")}
        self.assertEqual(counts["module"], 5)
        self.assertEqual(counts["file"], 9)
        self.assertEqual(counts["type"], 8)
        self.assertEqual(counts["callback"], 1)
        self.assertEqual(counts["function"], 25)
        self.assertEqual(counts["constant"], 5)
        self.assertGreater(counts["test"], 0)
        self.assertEqual(
            registry.resolve("mqtt_decoder.h", expected_kinds={"file"})["artifact_id"],
            registry.resolve("mqtt_decoder.c", expected_kinds={"file"})["artifact_id"],
        )
        self.assertEqual(
            CanonicalPlanningRegistry.from_snapshot(registry.snapshot()).snapshot(), registry.snapshot()
        )

    def test_typed_view_rejects_known_symbol_with_wrong_kind(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        with self.assertRaisesRegex(RegistryBindingError, "artifact_kind_mismatch"):
            registry.resolve("mqtt_connection_t", expected_kinds={"function"})

    def test_canonical_fields_are_immutable(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        with self.assertRaisesRegex(RegistryInvariantError, "canonical_fields_immutable"):
            registry.apply_overlay("mqtt_decoder_feed", {"canonical_name": "renamed"})

    def test_same_id_with_different_owner_is_rejected(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        entry = registry.resolve("mqtt_decoder_feed", expected_kinds={"function"})
        conflicting = dict(entry)
        conflicting["owner_file_id"] = registry.resolve("broker.c", expected_kinds={"file"})["artifact_id"]
        with self.assertRaisesRegex(RegistryInvariantError, "registry_canonical_conflict"):
            registry.register(conflicting)

    def test_same_alias_across_kinds_requires_typed_binding(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        function = registry.resolve("mqtt_decoder_feed", expected_kinds={"function"})
        duplicate_name = {
            **function,
            "artifact_id": "constant:mqtt_decoder_feed",
            "artifact_kind": "constant",
            "owner_module_id": function["owner_module_id"],
            "owner_file_id": function["owner_file_id"],
            "definition_stage": "public_artifact_inventory",
        }
        registry.register(duplicate_name, aliases=["mqtt_decoder_feed"])
        with self.assertRaisesRegex(RegistryBindingError, "ambiguous_artifact_id"):
            registry.resolve("mqtt_decoder_feed")
        self.assertEqual(
            registry.resolve("mqtt_decoder_feed", expected_kinds={"function"})["artifact_id"],
            function["artifact_id"],
        )

    def test_semantic_patch_additions_are_explicit_and_provenanced(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        owner = registry.resolve("broker.c", expected_kinds={"file"})
        artifact_id = f"function:{owner['canonical_name']}/fixture_helper"
        helper = {
            "id": artifact_id,
            "file": owner["artifact_id"],
            "name": "fixture_helper",
            "visibility": "private",
        }
        patch = {
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": artifact_id,
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                }
            ]
        }
        register_semantic_patch_additions(registry, {"functions": [helper]}, patch)
        entry = registry.resolve(artifact_id, expected_kinds={"function"})
        self.assertEqual(entry["definition_stage"], "bounded_semantic_patch")
        self.assertEqual(entry["provenance"]["kind"], "engineering_decision")

    def test_semantic_patch_file_id_is_normalized_from_source_path(self) -> None:
        registry = build_registry(_run3_artifacts(), protocol_slug="mqtt")
        patch = {
            "operations": [
                {
                    "op": "add",
                    "artifact_kind": "file",
                    "artifact_id": "file:mqtt/main/main",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                },
                {
                    "op": "add",
                    "artifact_kind": "function",
                    "artifact_id": "function:mqtt/main/main",
                    "provenance": {"kind": "engineering_decision", "refs": ["RULE_FIXTURE"]},
                },
            ]
        }
        plan = {
            "protocol": {"slug": "mqtt"},
            "files": [
                {
                    "id": "file:mqtt/main/main",
                    "module": "mqtt_broker_core",
                    "trace_id": "mqtt/main/main",
                    "source_path": "main.c",
                }
            ],
            "functions": [
                {
                    "id": "function:mqtt/main/main",
                    "file": "file:mqtt/main/main",
                    "name": "main",
                    "visibility": "private",
                }
            ],
        }
        register_semantic_patch_additions(registry, plan, patch)
        self.assertEqual(
            registry.resolve("file:mqtt/main/main", expected_kinds={"file"})["artifact_id"], "file:mqtt/main"
        )
        self.assertEqual(
            registry.resolve("function:mqtt/main/main", expected_kinds={"function"})["canonical_name"], "main"
        )


if __name__ == "__main__":
    unittest.main()
