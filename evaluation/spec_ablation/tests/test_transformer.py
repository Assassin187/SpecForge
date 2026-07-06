from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.spec_ablation.configs import PROTOCOL_ORDER
from evaluation.spec_ablation.transformer import FORBIDDEN_VISIBLE_TERMS, transform_many, transform_protocol


EXPECTED_FUNCTIONS = {
    "mqtt": 89,
    "http": 62,
    "coap": 60,
    "smtp": 68,
}

EXPECTED_HEADERS = {
    "mqtt": 10,
    "http": 7,
    "coap": 5,
    "smtp": 8,
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class TransformerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        transform_many(PROTOCOL_ORDER, cls.root, overwrite=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _manifest(self, protocol: str) -> dict:
        return _read_json(self.root / protocol / "transformation_manifest.json")

    def test_all_protocols_convert_with_expected_counts(self) -> None:
        for protocol in PROTOCOL_ORDER:
            with self.subTest(protocol=protocol):
                manifest = self._manifest(protocol)
                self.assertEqual(manifest["validation"]["source_loader"]["status"], "passed")
                self.assertEqual(manifest["validation"]["leakage_scan"]["status"], "passed")
                self.assertEqual(manifest["counts"]["functions"], EXPECTED_FUNCTIONS[protocol])
                self.assertEqual(manifest["counts"]["headers"], EXPECTED_HEADERS[protocol])
                self.assertEqual(len(list((self.root / protocol / "specfs_projection" / "functions").rglob("*.spec"))), EXPECTED_FUNCTIONS[protocol])
                self.assertEqual(len(list((self.root / protocol / "specfs_projection" / "headers").rglob("*.header"))), EXPECTED_HEADERS[protocol])

    def test_function_artifacts_have_only_s1_sections_and_no_forbidden_terms(self) -> None:
        expected_tags = ["[PROMPT]", "[RELY]", "[GUARANTEE]", "[SPECIFICATION]"]
        for protocol in PROTOCOL_ORDER:
            for path in (self.root / protocol / "specfs_projection" / "functions").rglob("*.spec"):
                with self.subTest(protocol=protocol, path=path.name):
                    text = path.read_text(encoding="utf-8")
                    tags = [line.strip() for line in text.splitlines() if line.startswith("[") and line.endswith("]")]
                    self.assertEqual(tags, expected_tags)
                    for term in FORBIDDEN_VISIBLE_TERMS:
                        self.assertNotIn(term, text)

    def test_header_artifacts_expose_declarations_without_specforge_fields(self) -> None:
        for protocol in PROTOCOL_ORDER:
            for path in (self.root / protocol / "specfs_projection" / "headers").rglob("*.header"):
                with self.subTest(protocol=protocol, path=path.name):
                    text = path.read_text(encoding="utf-8")
                    self.assertNotIn("#pragma once", text)
                    self.assertNotIn('extern "C"', text)
                    self.assertRegex(text, r"(#include|typedef|#define|;)")
                    for term in FORBIDDEN_VISIBLE_TERMS:
                        self.assertNotIn(term, text)

    def test_rely_declarations_do_not_render_duplicate_type_tags(self) -> None:
        pattern = r"typedef\s+(struct|union|enum)\s+(struct|union|enum)\b"
        for protocol in PROTOCOL_ORDER:
            for path in (self.root / protocol / "specfs_projection" / "functions").rglob("*.spec"):
                with self.subTest(protocol=protocol, path=path.name):
                    self.assertNotRegex(path.read_text(encoding="utf-8"), pattern)

    def test_rely_resolution_keeps_external_posix_calls_but_resolves_local_typedefs(self) -> None:
        smtp = self._manifest("smtp")
        external_names = {
            item["name"]
            for item in smtp["rely_resolution"]
            if item["strategy"] == "external_name_only"
        }
        self.assertIn("recv", external_names)
        self.assertIn("send", external_names)
        self.assertNotIn("smtp_client_node_t", external_names)
        self.assertNotIn("smtp_session_node_t", external_names)

    def test_execution_manifest_is_hidden_control_artifact(self) -> None:
        for protocol in PROTOCOL_ORDER:
            with self.subTest(protocol=protocol):
                manifest = _read_json(self.root / protocol / "execution_manifest.json")
                self.assertEqual(manifest["visibility"], "hidden_evaluator_only")
                self.assertGreater(len(manifest["sources"]), 0)
                self.assertIn("functions", manifest["sources"][0])

    def test_transformation_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            first = root / "first"
            second = root / "second"
            transform_protocol("mqtt", first, overwrite=True)
            transform_protocol("mqtt", second, overwrite=True)
            self.assertEqual(_tree_hash(first), _tree_hash(second))

    def test_existing_output_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = Path(raw_tmp) / "mqtt"
            first = transform_protocol("mqtt", target)
            with self.assertRaises(Exception):
                transform_protocol("mqtt", target)
            second = transform_protocol("mqtt", target, overwrite=True)
            self.assertEqual(first["counts"], second["counts"])


if __name__ == "__main__":
    unittest.main()
