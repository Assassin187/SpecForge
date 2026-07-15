from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.facts.cli import build_parser
from agent.facts.target_profile import TargetProfileError, build_profile_evidence, load_target_profile


FACTS_DIR = Path(__file__).resolve().parents[1]
MQTT_PROFILE = FACTS_DIR / "target_profiles" / "mqtt_min.json"


class TargetProfileTests(unittest.TestCase):
    def _write_profile(self, mutate=None) -> Path:
        data = json.loads(MQTT_PROFILE.read_text(encoding="utf-8"))
        if mutate:
            mutate(data)
        directory = Path(tempfile.mkdtemp())
        path = directory / "profile.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(directory.rmdir)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_loads_profile_and_builds_protocol_semantic_projection(self) -> None:
        profile = load_target_profile(MQTT_PROFILE, "MQTT")

        self.assertEqual(profile.data["target_role"], "broker")
        self.assertEqual(len(profile.sha256), 64)
        self.assertEqual(len(profile.semantic_projection_sha256), 64)
        self.assertNotIn("language", profile.semantic_projection)
        self.assertNotIn("runtime", profile.semantic_projection)
        self.assertNotIn("binary_name", profile.semantic_projection["runtime_contract"])
        self.assertEqual(profile.semantic_projection["runtime_contract"], {"transport": "tcp"})
        evidence = build_profile_evidence(profile)
        self.assertTrue(evidence)
        self.assertTrue(all(item["evidence_id"].startswith("profile_") for item in evidence))
        self.assertTrue(all(set(item) == {"evidence_id", "doc_path", "section_hint", "chunk_id", "excerpt"} for item in evidence))

    def test_rejects_wrong_schema_version(self) -> None:
        path = self._write_profile(lambda data: data.update(schema_version="target_profile/v1"))
        with self.assertRaisesRegex(TargetProfileError, "schema_version"):
            load_target_profile(path, "mqtt")

    def test_rejects_protocol_mismatch(self) -> None:
        with self.assertRaisesRegex(TargetProfileError, "protocol_name mismatch"):
            load_target_profile(MQTT_PROFILE, "coap")

    def test_rejects_duplicate_capability_ids(self) -> None:
        def duplicate(data):
            data["required_capabilities"].append(dict(data["required_capabilities"][0]))

        path = self._write_profile(duplicate)
        with self.assertRaisesRegex(TargetProfileError, "duplicate capability_id"):
            load_target_profile(path, "mqtt")

    def test_rejects_contradictory_scope_policy(self) -> None:
        def contradict(data):
            data["scope_policy"]["conformance_mode"] = "full"

        path = self._write_profile(contradict)
        with self.assertRaisesRegex(TargetProfileError, "intentional_subset"):
            load_target_profile(path, "mqtt")

    def test_rejects_capability_constraint_contradiction(self) -> None:
        def contradict(data):
            data["required_capabilities"][0]["requires"] = {"persistence": True}

        path = self._write_profile(contradict)
        with self.assertRaisesRegex(TargetProfileError, "requires persistence=True"):
            load_target_profile(path, "mqtt")

    def test_validate_and_extract_require_target_profile(self) -> None:
        parser = build_parser()
        for command in ("validate", "extract"):
            with self.assertRaises(SystemExit):
                parser.parse_args([command, "--protocol-name", "mqtt", "--doc", "mqtt.txt"])


if __name__ == "__main__":
    unittest.main()
