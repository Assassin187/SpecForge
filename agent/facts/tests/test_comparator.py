from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.facts.comparator import compare_facts


FACTS_DIR = Path(__file__).resolve().parents[1]
GOLD = FACTS_DIR / "gold_facts" / "mqtt_min" / "protocol_facts.json"
CONTRACT = FACTS_DIR / "contracts" / "mqtt_min_gold_contract.json"
RUBRIC = FACTS_DIR / "contracts" / "mqtt_min_semantic_rubric.json"


class ComparatorTests(unittest.TestCase):
    def _candidate(self, mutate=None) -> Path:
        value = json.loads(GOLD.read_text(encoding="utf-8"))
        if mutate:
            mutate(value)
        directory = Path(tempfile.mkdtemp())
        path = directory / "candidate.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.addCleanup(directory.rmdir)
        self.addCleanup(path.unlink)
        return path

    def test_gold_shape_passes_and_ignores_prose_and_evidence_ids(self) -> None:
        def paraphrase(value):
            value["minimum_v1"]["must_support_surface"][0]["summary"] += " Paraphrased."
            value["minimum_v1"]["must_support_surface"][0]["evidence_refs"] = ["different_id"]

        report = compare_facts(self._candidate(paraphrase), GOLD, CONTRACT, RUBRIC)
        self.assertTrue(report["structural"]["ok"])

    def test_reason_instead_of_summary_is_precisely_reported(self) -> None:
        def break_shape(value):
            item = value["minimum_v1"]["must_support_surface"][0]
            item["reason"] = item.pop("summary")

        report = compare_facts(self._candidate(break_shape), GOLD, CONTRACT, RUBRIC)
        mismatch_paths = {item["path"] for item in report["structural"]["mismatches"]}
        self.assertIn("$.minimum_v1.must_support_surface[0]", mismatch_paths)

    def test_object_instead_of_list_is_reported(self) -> None:
        def break_type(value):
            value["state_model"]["transitions"] = {}

        report = compare_facts(self._candidate(break_type), GOLD, CONTRACT, RUBRIC)
        mismatch_paths = {item["path"] for item in report["structural"]["mismatches"]}
        self.assertIn("$.state_model.transitions", mismatch_paths)

    def test_varint_alias_and_negated_persistence_are_not_false_gaps(self) -> None:
        report = compare_facts(self._candidate(), GOLD, CONTRACT, RUBRIC)
        remaining = next(item for item in report["semantic"]["checks"] if item["concept"] == "remaining_length_varint")
        persistent = next(item for item in report["semantic"]["forbidden_scope_checks"] if item["concept"] == "persistent session")
        self.assertTrue(remaining["passed"])
        self.assertFalse(persistent["present"])
        qos = next(item for item in report["semantic"]["checks"] if item["concept"] == "QoS1_QoS2")
        self.assertTrue(qos["passed"])


if __name__ == "__main__":
    unittest.main()
