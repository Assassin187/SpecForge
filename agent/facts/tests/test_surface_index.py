from __future__ import annotations

import unittest
from pathlib import Path

from agent.facts.document_loader import load_documents
from agent.facts.preprocess import build_chunks
from agent.facts.surface_index import build_surface_index, resolve_capabilities
from agent.facts.target_profile import load_target_profile


ROOT = Path(__file__).resolve().parents[3]


class SurfaceIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        docs, diagnostics = load_documents([ROOT / "document" / "MQTT_3.1.1.txt"])
        if diagnostics:
            raise AssertionError(diagnostics)
        cls.chunks = build_chunks(docs)
        cls.index = build_surface_index(cls.chunks)

    def test_mqtt_body_headings_produce_all_control_packet_surfaces(self) -> None:
        chapter_three = [item for item in self.index if item["section_anchor"].count(".") == 1 and item["section_anchor"].startswith("3.")]
        self.assertEqual(len(chapter_three), 14)
        self.assertEqual(len({item["name"] for item in chapter_three}), 14)

    def test_capability_resolution_uses_document_index(self) -> None:
        profile = load_target_profile(ROOT / "agent" / "facts" / "target_profiles" / "mqtt_min.json", "mqtt")
        resolutions = resolve_capabilities(profile, self.index, self.chunks)
        by_capability = {
            item["capability_id"]: {candidate["surface"] for candidate in item["candidates"]}
            for item in resolutions
        }
        self.assertIn("CONNECT", by_capability["connection_establishment"])
        self.assertTrue({"PUBLISH", "SUBSCRIBE"} <= by_capability["qos0_publish_subscribe"])
        self.assertIn("PINGREQ", by_capability["keepalive_exchange"])
        self.assertIn("DISCONNECT", by_capability["graceful_disconnect"])

    def test_generic_command_and_method_headings_are_indexed(self) -> None:
        from agent.facts.models import Chunk

        chunks = [
            Chunk("x1", "x.txt", "2.1 FETCH - Retrieve a resource", "s1", 1, 1, "FETCH request", []),
            Chunk("x2", "x.txt", "2.2 RESULT - Return the result", "s2", 2, 1, "RESULT reply", []),
            Chunk("x3", "x.txt", "3.1 GET - Read a resource", "s3", 3, 1, "GET method", []),
        ]
        self.assertEqual([item["name"] for item in build_surface_index(chunks)], ["FETCH", "RESULT", "GET"])


if __name__ == "__main__":
    unittest.main()
