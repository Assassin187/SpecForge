from __future__ import annotations

import unittest
from pathlib import Path

from agent.facts.document_loader import load_documents
from agent.facts.preprocess import build_chunks
from agent.facts.scope_resolution import resolve_scope
from agent.facts.surface_index import build_surface_index, resolve_capabilities
from agent.facts.target_profile import load_target_profile


ROOT = Path(__file__).resolve().parents[3]


class ScopeResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        docs, diagnostics = load_documents([ROOT / "document" / "MQTT_3.1.1.txt"])
        if diagnostics:
            raise AssertionError(diagnostics)
        chunks = build_chunks(docs)
        profile = load_target_profile(ROOT / "agent" / "facts" / "target_profiles" / "mqtt_min.json", "mqtt")
        index = build_surface_index(chunks)
        resolutions = resolve_capabilities(profile, index, chunks)
        cls.scope = resolve_scope(profile, index, resolutions, chunks)

    def test_mandatory_response_closure(self) -> None:
        self.assertTrue(
            {"CONNECT", "CONNACK", "PUBLISH", "SUBSCRIBE", "SUBACK", "PINGREQ", "PINGRESP", "DISCONNECT"}
            <= set(self.scope["included_surface"])
        )

    def test_qos_zero_does_not_close_over_higher_qos_transactions(self) -> None:
        self.assertTrue({"PUBACK", "PUBREC", "PUBREL", "PUBCOMP"}.isdisjoint(self.scope["included_surface"]))
        self.assertEqual(self.scope["closure_status"], "complete")


if __name__ == "__main__":
    unittest.main()
