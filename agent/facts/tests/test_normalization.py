from __future__ import annotations

import unittest

from agent.facts.extraction import CATEGORY_FACT_DEFAULTS, _normalize_gold_compatible, _prune_excluded_scope
from agent.facts.target_profile import load_target_profile
from pathlib import Path


class GoldCompatibleNormalizationTests(unittest.TestCase):
    def test_normalizes_category_keys_and_critical_item_shapes(self) -> None:
        payload = {
            "protocol_meta": {"protocol_name": "x", "source_documents": ["x.txt"], "document_count": 1},
            **{key: __import__("copy").deepcopy(value) for key, value in CATEGORY_FACT_DEFAULTS.items()},
        }
        payload["message_model"]["surface_catalog"] = [
            {"name": "FETCH", "kind": "command", "direction": "client_to_server", "summary": "Fetch.", "fields": ["bad"], "evidence_refs": ["doc_1"]}
        ]
        payload["minimum_v1"]["must_support_surface"] = [
            {"name": "FETCH", "reason": "Required.", "evidence_refs": ["doc_1"]}
        ]

        normalized = _normalize_gold_compatible(payload)

        self.assertEqual(set(normalized["message_model"]), {"framing", "surface_catalog", "message_or_command_entries", "field_constraints"})
        self.assertEqual(set(normalized["state_model"]), {"state_nodes", "transitions", "timers_and_constants", "invariants"})
        self.assertEqual(set(normalized["resource_model"]), {"resource_objects", "lifecycle_rules", "persistence_scope"})
        self.assertEqual(set(normalized["error_and_limits"]), {"error_matrix", "limits", "security"})
        self.assertEqual(set(normalized["message_model"]["surface_catalog"][0]), {"name", "kind", "direction", "summary", "evidence_refs"})
        self.assertEqual(
            set(normalized["minimum_v1"]["must_support_surface"][0]),
            {"name", "summary", "evidence_refs"},
        )
        self.assertEqual(normalized["minimum_v1"]["must_support_surface"][0]["summary"], "Required.")

    def test_prunes_excluded_state_without_removing_included_surface(self) -> None:
        import copy

        outputs = {key: {"facts": copy.deepcopy(value)} for key, value in CATEGORY_FACT_DEFAULTS.items()}
        outputs["message_model"]["facts"]["surface_catalog"] = [
            {"name": "CONNECT", "summary": "Includes optional authentication fields."}
        ]
        outputs["state_model"]["facts"]["state_nodes"] = [
            {"name": "QoS 2 transaction", "summary": "Persistent QoS 2 state."},
            {"name": "Connected", "summary": "Active connection."},
        ]
        profile = load_target_profile(Path(__file__).resolve().parents[1] / "target_profiles" / "mqtt_min.json", "mqtt")

        _prune_excluded_scope(outputs, ["CONNECT"], ["PUBREC"], profile)

        self.assertEqual([item["name"] for item in outputs["message_model"]["facts"]["surface_catalog"]], ["CONNECT"])
        self.assertEqual([item["name"] for item in outputs["state_model"]["facts"]["state_nodes"]], ["Connected"])


if __name__ == "__main__":
    unittest.main()
