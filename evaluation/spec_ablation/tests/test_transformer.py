from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.spec_ablation.configs import PROTOCOL_ORDER
from evaluation.spec_ablation.transformer import (
    FORBIDDEN_VISIBLE_TERMS,
    S2_PROJECT_GRAPH_FORBIDDEN_TERMS,
    S3_INTERFACE_GROUNDING_FORBIDDEN_TERMS,
    transform_many,
    transform_protocol,
)


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

GENERIC_SIGNATURE_PARAMS_INVARIANT = "调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。"


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

    def _manifest(self, protocol: str, view: str = "s1") -> dict:
        return _read_json(self.root / protocol / view / "transformation_manifest.json")

    def test_all_protocols_convert_with_expected_counts(self) -> None:
        for protocol in PROTOCOL_ORDER:
            with self.subTest(protocol=protocol):
                index = _read_json(self.root / protocol / "view_set_manifest.json")
                self.assertEqual(set(index["views"]), {"s1", "s2", "s3"})
                for view in ("s1", "s2", "s3"):
                    manifest = self._manifest(protocol, view)
                    self.assertEqual(manifest["view"], view)
                    self.assertEqual(manifest["transformer_version"], f"spec_ablation_{view}_transformer/v2")
                    self.assertEqual(manifest["validation"]["source_loader"]["status"], "passed")
                    self.assertEqual(manifest["validation"]["leakage_scan"]["status"], "passed")
                    self.assertEqual(manifest["counts"]["functions"], EXPECTED_FUNCTIONS[protocol])
                    self.assertEqual(manifest["counts"]["headers"], EXPECTED_HEADERS[protocol])
                    self.assertEqual(len(list((self.root / protocol / view / "specfs_projection" / "functions").rglob("*.spec"))), EXPECTED_FUNCTIONS[protocol])
                    self.assertEqual(len(list((self.root / protocol / view / "specfs_projection" / "headers").rglob("*.header"))), EXPECTED_HEADERS[protocol])
                self.assertNotIn("project_graph", self._manifest(protocol, "s1")["artifacts"])
                self.assertNotIn("project_graphs", self._manifest(protocol, "s1")["counts"])
                self.assertFalse((self.root / protocol / "s1" / "specfs_projection" / "project_graph.json").exists())
                self.assertEqual(self._manifest(protocol, "s2")["counts"]["project_graphs"], 1)
                self.assertEqual(
                    self._manifest(protocol, "s2")["validation"]["s2_project_graph_leakage_scan"]["status"],
                    "passed",
                )
                self.assertEqual(self._manifest(protocol, "s3")["counts"]["project_graphs"], 1)
                self.assertEqual(self._manifest(protocol, "s3")["counts"]["interface_groundings"], 1)
                self.assertIn("project_graph", self._manifest(protocol, "s3")["artifacts"])
                self.assertIn("interface_grounding", self._manifest(protocol, "s3")["artifacts"])
                self.assertEqual(
                    self._manifest(protocol, "s3")["validation"]["s2_project_graph_leakage_scan"]["status"],
                    "passed",
                )
                self.assertEqual(
                    self._manifest(protocol, "s3")["validation"]["s3_interface_grounding_leakage_scan"]["status"],
                    "passed",
                )

    def test_s2_project_graph_exposes_structure_without_full_specforge_fields(self) -> None:
        for protocol in PROTOCOL_ORDER:
            with self.subTest(protocol=protocol):
                manifest = self._manifest(protocol, "s2")
                artifact = manifest["artifacts"]["project_graph"]
                path = self.root / protocol / "s2" / artifact["artifact_path"]
                graph = _read_json(path)
                text = path.read_text(encoding="utf-8")

                self.assertEqual(graph["visibility"], "s2_coder_visible_project_graph")
                self.assertGreater(len(graph["modules"]), 0)
                self.assertGreater(len(graph["files"]), 0)
                self.assertGreater(len(graph["functions"]), 0)
                self.assertIn("module_generation_order", graph)
                self.assertIn("module_dependency_edges", graph)
                self.assertIn("file_dependency_edges", graph)
                self.assertIn("source_positions", graph)
                self.assertIn("contains_file_trace_ids", graph["modules"][0])
                self.assertIn("contains_function_trace_ids", graph["files"][0])
                self.assertIn("linkage", graph["functions"][0])
                for term in S2_PROJECT_GRAPH_FORBIDDEN_TERMS:
                    self.assertNotIn(term, text)

    def test_s3_interface_grounding_exposes_only_type_and_interface_fields(self) -> None:
        manifest = self._manifest("mqtt", "s3")
        artifact = manifest["artifacts"]["interface_grounding"]
        path = self.root / "mqtt" / "s3" / artifact["artifact_path"]
        grounding = _read_json(path)
        text = path.read_text(encoding="utf-8")

        self.assertEqual(grounding["visibility"], "s3_coder_visible_interface_grounding")
        self.assertGreater(len(grounding["files"]), 0)
        for term in S3_INTERFACE_GROUNDING_FORBIDDEN_TERMS:
            self.assertNotIn(term, text)

        type_specs = {
            item["name"]: item
            for file_item in grounding["files"]
            for item in file_item.get("header_type_specs", [])
        }
        connect_payload = type_specs["mqtt_connect_payload_t"]
        fields = {item["NAME"] for item in connect_payload["type_spec"]["FIELDS"]}
        self.assertEqual(fields, {"client_id", "keep_alive", "clean_session"})
        self.assertNotIn("will_topic", fields)
        self.assertNotIn("username", fields)

        signatures = {
            item["name"]: item["signature"]
            for file_item in grounding["files"]
            for item in [*file_item.get("header_interfaces", []), *file_item.get("source_interfaces", [])]
        }
        self.assertEqual(
            signatures["mqtt_tcp_server_create"],
            "mqtt_tcp_server_t* mqtt_tcp_server_create(uint16_t port, mqtt_tcp_callbacks_t cb, void* user)",
        )
        self.assertEqual(
            signatures["try_parse_remaining_length"],
            "static bool try_parse_remaining_length(const uint8_t* buf, size_t buf_len, size_t start, remaining_length_t* out)",
        )

    def test_function_artifacts_have_only_s1_sections_and_no_forbidden_terms(self) -> None:
        expected_tags = ["[PROMPT]", "[RELY]", "[GUARANTEE]", "[SPECIFICATION]"]
        for protocol in PROTOCOL_ORDER:
            for view in ("s1", "s2", "s3"):
                for path in (self.root / protocol / view / "specfs_projection" / "functions").rglob("*.spec"):
                    with self.subTest(protocol=protocol, view=view, path=path.name):
                        data = path.read_bytes()
                        self.assertNotIn(b"\x00", data)
                        text = data.decode("utf-8")
                        tags = [line.strip() for line in text.splitlines() if line.startswith("[") and line.endswith("]")]
                        self.assertEqual(tags, expected_tags)
                        self.assertNotIn(GENERIC_SIGNATURE_PARAMS_INVARIANT, text)
                        for term in FORBIDDEN_VISIBLE_TERMS:
                            self.assertNotIn(term, text)

    def test_header_artifacts_expose_declarations_without_specforge_fields(self) -> None:
        for protocol in PROTOCOL_ORDER:
            for view in ("s1", "s2", "s3"):
                for path in (self.root / protocol / view / "specfs_projection" / "headers").rglob("*.header"):
                    with self.subTest(protocol=protocol, view=view, path=path.name):
                        text = path.read_text(encoding="utf-8")
                        self.assertNotIn("#pragma once", text)
                        self.assertNotIn('extern "C"', text)
                        self.assertRegex(text, r"(#include|typedef|#define|;)")
                        for term in FORBIDDEN_VISIBLE_TERMS:
                            self.assertNotIn(term, text)

    def test_rely_declarations_do_not_render_duplicate_type_tags(self) -> None:
        pattern = r"typedef\s+(struct|union|enum)\s+(struct|union|enum)\b"
        for protocol in PROTOCOL_ORDER:
            for view in ("s1", "s2", "s3"):
                for path in (self.root / protocol / view / "specfs_projection" / "functions").rglob("*.spec"):
                    with self.subTest(protocol=protocol, view=view, path=path.name):
                        self.assertNotRegex(path.read_text(encoding="utf-8"), pattern)

    def test_rely_resolution_keeps_external_posix_calls_but_resolves_local_typedefs(self) -> None:
        smtp = self._manifest("smtp", "s1")
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
            for view in ("s1", "s2", "s3"):
                with self.subTest(protocol=protocol, view=view):
                    manifest = _read_json(self.root / protocol / view / "execution_manifest.json")
                    self.assertEqual(manifest["view"], view)
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
            self.assertEqual(first["views"]["s1"]["counts"], second["views"]["s1"]["counts"])
            self.assertEqual(first["views"]["s2"]["counts"], second["views"]["s2"]["counts"])
            self.assertEqual(first["views"]["s3"]["counts"], second["views"]["s3"]["counts"])


if __name__ == "__main__":
    unittest.main()
