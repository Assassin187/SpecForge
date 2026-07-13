from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.planning.amendment import InventoryAmendmentProcessor
from agent.planning.registry import build_registry


def _registry():
    artifacts = [
        {
            "stage_id": "module_file_plan",
            "artifact": {
                "modules": [{"name": "core"}],
                "files": [
                    {
                        "id": "core.h",
                        "module": "core",
                        "header_path": "core.h",
                        "source_path": "core.c",
                    }
                ],
            },
        },
        {
            "stage_id": "public_artifact_inventory",
            "artifact": {
                "types": [{"symbol": "core_t", "owner_file": "core.h", "visibility": "public", "kind": "type"}],
                "functions": [
                    {
                        "symbol": "core_run",
                        "owner_file": "core.c",
                        "visibility": "public",
                    }
                ],
                "constants_or_macros": [],
            },
        },
    ]
    return build_registry(artifacts, protocol_slug="fixture")


def _request(**changes):
    value = {
        "requested_kind": "callback",
        "proposed_name": "core_event_callback_t",
        "semantic_role": "Deliver a decoded event without adding protocol behavior.",
        "requested_owner": "core.h",
        "required_by": "core_run",
        "reason": "The existing function interface requires a typed callback identity.",
        "provenance": {"kind": "inferred_engineering_decision", "refs": ["RULE_CALLBACK"]},
        "preferred_visibility": "public",
    }
    value.update(changes)
    return value


class AmendmentTests(unittest.TestCase):
    def test_valid_request_registers_only_identity_and_schedules_local_reruns(self) -> None:
        registry = _registry()
        before = {item["artifact_id"]: item for item in registry.snapshot()["entries"]}
        with tempfile.TemporaryDirectory() as raw:
            processor = InventoryAmendmentProcessor(registry, log_path=Path(raw) / "amendments.json")
            outcomes = processor.process([_request()], stage_id="type_and_access_path_design")
            self.assertTrue((Path(raw) / "amendments.json").exists())
        outcome = outcomes[0]
        self.assertEqual(outcome["status"], "accepted_pending_rerun")
        entry = registry.resolve("core_event_callback_t", expected_kinds={"callback"})
        self.assertEqual(entry["status"], "requested")
        self.assertEqual(entry["definition_stage"], "inventory_amendment")
        self.assertNotIn("signature", entry)
        self.assertEqual(
            [item["stage_id"] for item in outcome["rerun_partitions"]],
            [
                "type_and_access_path_design",
                "function_interface_design",
                "function_behavior_design",
                "function_call_contract_closure",
            ],
        )
        after = {item["artifact_id"]: item for item in registry.snapshot()["entries"]}
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(len(after), len(before) + 1)

    def test_existing_artifact_is_bound_without_registry_growth(self) -> None:
        registry = _registry()
        count = len(registry.snapshot()["entries"])
        processor = InventoryAmendmentProcessor(registry)
        outcome = processor.process(
            [
                _request(
                    requested_kind="function",
                    proposed_name="core_run",
                    requested_owner="core.c",
                    preferred_visibility="public",
                )
            ],
            stage_id="function_call_contract_closure",
            partition_id="core",
        )[0]
        self.assertEqual(outcome["status"], "bound_existing")
        self.assertEqual(len(registry.snapshot()["entries"]), count)
        self.assertEqual(processor.pending_for_stage("function_call_contract_closure"), [])

    def test_invalid_provenance_is_unresolved_and_blocks_without_mutation(self) -> None:
        registry = _registry()
        before = registry.snapshot()
        processor = InventoryAmendmentProcessor(registry)
        outcome = processor.process(
            [_request(provenance={"kind": "protocol_fact", "refs": []})],
            stage_id="type_and_access_path_design",
        )[0]
        self.assertEqual(outcome["status"], "unresolved")
        self.assertEqual(outcome["diagnostic"], "artifact_request_provenance_invalid")
        self.assertEqual(registry.snapshot(), before)
        self.assertEqual(len(processor.pending_for_stage("type_and_access_path_design")), 1)

    def test_amendment_round_is_bounded_once_per_partition(self) -> None:
        registry = _registry()
        processor = InventoryAmendmentProcessor(registry)
        first = processor.process([_request()], stage_id="type_and_access_path_design", partition_id="core")[0]
        second = processor.process(
            [_request(proposed_name="second_callback_t")],
            stage_id="type_and_access_path_design",
            partition_id="core",
        )[0]
        self.assertEqual(first["status"], "accepted_pending_rerun")
        self.assertEqual(second["status"], "unresolved")
        self.assertEqual(second["diagnostic"], "amendment_round_limit")


if __name__ == "__main__":
    unittest.main()
