import json
import shutil
import tempfile
import unittest
from pathlib import Path

from specforge.documents import ROOT, artifact_errors, check_facts, prepare, read_json, save_json
from specforge.specs import abi_checks, publish, validate


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "bundle"
        shutil.copytree(ROOT / "assets/mqtt_reference/bundle", self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def mutate(self, kind, callback):
        for path in self.directory.rglob("*.json"):
            value = read_json(path)
            if value.get("KIND") == kind:
                callback(value)
                save_json(path, value)
                return path

    def test_reference_and_unknown_dependency(self):
        self.assertTrue(validate(self.directory)["passed"])
        self.mutate("FUNCTION_SPEC", lambda v: v["RELY"]["FUNC"].append({"NAME": "unknown_project_call", "KIND": "CALL", "ROLE": "missing"}))
        report = validate(self.directory, published=False)
        self.assertTrue(any("Unknown project function" in e for e in report["errors"]))

    def test_missing_file_spec_identifies_both_planned_paths(self):
        path = next(path for path in sorted((self.directory / "files").glob("*.json"))
                    if "HEADER" in read_json(path))
        spec = read_json(path)
        path.unlink()
        report = validate(self.directory, design=True, published=False)
        self.assertFalse(report["passed"])
        error = next(error for error in report["errors"] if error.startswith("Module FILES"))
        self.assertIn("missing FileSpecs", error)
        self.assertIn(spec["SOURCE"]["PATH"], error)
        self.assertIn(spec["HEADER"]["PATH"], error)

    def test_public_type_and_header_mismatch(self):
        for path in (self.directory / "files").glob("*.json"):
            value = read_json(path)
            if value.get("HEADER", {}).get("DATA"):
                value["HEADER"]["DATA"][0].pop("TYPE_SPEC", None)
                save_json(path, value)
                break
        self.assertTrue(any("TYPE_SPEC" in e for e in validate(self.directory, published=False)["errors"]))
        header = next((self.directory / "abi").rglob("*.h"))
        header.write_text("#error invalid_header\n")
        self.assertTrue(any("ABI mismatch" in e for e in validate(self.directory, published=False)["errors"]))

    def test_test_vector_requirement_references(self):
        scope = read_json(self.directory / "scope.json")
        scope["requirements"] = [{"id": "R01", "description": "fixture requirement"}]
        save_json(self.directory / "scope.json", scope)
        path = self.mutate("FUNCTION_SPEC", lambda v: v.update(TEST_VECTORS=[{
            "NAME": "requirement check", "INPUT": {}, "EXPECT": {}, "TRACE_REFS": ["R01"]}]))
        self.assertTrue(validate(self.directory, published=False)["passed"])
        value = read_json(path)
        value["TEST_VECTORS"][0]["TRACE_REFS"] = ["R99"]
        save_json(path, value)
        self.assertTrue(any("Unknown test-vector reference" in e for e in validate(self.directory, published=False)["errors"]))

    def test_missing_behavior_hash_change_and_parameter_conflict(self):
        def change(v):
            behavior = v.get("LOGIC") or v.get("EVENT")
            behavior["ACTION"] = ""
        self.mutate("FUNCTION_SPEC", change)
        report = validate(self.directory)
        self.assertTrue(any("Missing function behavior" in e for e in report["errors"]))
        self.assertTrue(any("hash mismatch" in e for e in report["errors"]))

    def test_pdf_pages_and_real_evidence(self):
        run = Path(self.temp.name) / "run"
        case = ROOT / "cases/mqtt_min"
        result = prepare(run, case / "TASK.md", case / "REQUIREMENTS.md", case / "spec/mqtt-v3.1.1-os.pdf")
        self.assertGreater(result["pages"], 1)
        index = read_json(run / "documents/index.json")
        self.assertEqual(index["chunks"][0]["chunk_id"], "p001")
        scope = {"schema_version": 1, "protocol": "MQTT", "version": "3.1.1", "role": "BROKER", "language": "C99", "runtime": "Linux",
                 "requirements": [{"id": f"R{i:02}", "description": "requirement", "source": {"file": "REQUIREMENTS.md", "line_start": i + 2, "line_end": i + 2}} for i in range(1, 13)],
                 "required_capabilities": [], "excluded_features": [], "runtime_contract": {"binary_name": "mqtt_broker", "argv_contract": "./mqtt_broker <port>"}, "engineering_defaults": []}
        facts = {"schema_version": 1, "facts": [{"id": "F1", "category": "message_format", "entity": "CONNECT", "statement": "rule", "values": {},
                 "evidence": [{"chunk_id": "p001", "line_start": 99999, "line_end": 99999}]}], "open_questions": []}
        save_json(run / "facts/scope.json", scope)
        save_json(run / "facts/facts.json", facts)
        self.assertTrue(any("Invalid evidence" in e for e in check_facts(run / "facts", run / "inputs", run / "documents")["errors"]))

    def test_identity_is_input_driven(self):
        scope = read_json(self.directory / "scope.json")
        scope.update(protocol="example", version="7", role="SERVER")
        scope["requirements"] = [{"id": "R01", "description": "example", "source": {"file": "REQUIREMENTS.md", "line_start": 1, "line_end": 1}}]
        scope["runtime_contract"] = {"binary_name": "example_server", "argv_contract": "./example_server <port>"}
        self.assertEqual(artifact_errors("scope", scope), [])
        save_json(self.directory / "scope.json", scope)
        bundle = publish(self.directory, 1, "manual_reference")
        self.assertEqual(bundle["protocol"], "example")
        self.assertEqual(bundle["runtime_contract"]["binary_name"], "example_server")
        summary = (self.directory / "SUMMARY.md").read_text()
        self.assertIn("example_server", summary)
        self.assertNotIn("./mqtt_broker", summary)

    def test_arbitrary_layout_and_unbounded_design_count(self):
        manifest = read_json(self.directory / "bundle.json")
        replacement = {row["path"]: "engine/" + row["path"].removeprefix("src/") for row in manifest["files"]}
        replacement.update({row["path"]: "public_api/" + row["path"].removeprefix("include/") for row in manifest["headers"]})
        for header in manifest["headers"]:
            destination = self.directory / "abi" / replacement[header["path"]]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.directory / header["artifact"], destination)
        def change(value):
            if isinstance(value, str):
                return replacement.get(value, value)
            if isinstance(value, dict):
                return {k: change(v) for k, v in value.items()}
            if isinstance(value, list):
                return [change(v) for v in value]
            return value
        for path in [self.directory / "module_spec.json", *(self.directory / "files").glob("*.json")]:
            save_json(path, change(read_json(path)))
        (self.directory / "bundle.json").unlink()
        report = validate(self.directory, design=True, published=False)
        self.assertTrue(report["passed"], report["errors"])
        self.assertGreater(report["counts"]["files"], 4)

    def test_udp_system_functions_resolve_from_headers(self):
        def change(v):
            v["RELY"]["FUNC"].append({"NAME": "recvfrom", "KIND": "CALL", "ROLE": "datagram receive"})
        function = self.mutate("FUNCTION_SPEC", change)
        trace = read_json(function)["TRACE_ID"]
        for path in (self.directory / "files").glob("*.json"):
            value = read_json(path)
            if any(i["TRACE_ID"] == trace for i in value["SOURCE"]["INTERFACE"]):
                value["SOURCE"].setdefault("SYSTEM_DEPENDENCY", []).append("sys/socket.h")
                save_json(path, value)
        report = validate(self.directory, published=False)
        self.assertTrue(report["passed"], report["errors"])

    def test_namespaced_header_include_root(self):
        directory = Path(self.temp.name) / "namespace"
        header = directory / "abi" / "public_api" / "domain" / "codec.h"
        header.parent.mkdir(parents=True)
        (header.parent / "types.h").write_text("typedef unsigned int message_id;\n")
        header.write_text('#include "domain/types.h"\nmessage_id decode(void);\n')
        spec = {"HEADER": {"PATH": "public_api/domain/codec.h", "DATA": [], "INTERFACE": [
            {"NAME": "decode", "SIGNATURE": "message_id decode(void);"}]}}
        report = abi_checks(directory, [("files/codec.json", spec)], [])
        self.assertTrue(report["passed"], report["errors"])

    def test_text_chunks_preserve_source_positions(self):
        standard = Path(self.temp.name) / "protocol.txt"
        standard.write_text("\n".join(f"{n}    original line" for n in range(410)))
        run = Path(self.temp.name) / "text_run"
        case = ROOT / "cases/mqtt_min"
        prepare(run, case / "TASK.md", case / "REQUIREMENTS.md", standard)
        index = read_json(run / "documents/index.json")
        self.assertEqual(len(index["chunks"]), 3)
        self.assertEqual(index["chunks"][1]["source_line_start"], 201)
        self.assertTrue((run / "documents/chunk_001.txt").read_text().startswith("0    original line"))


if __name__ == "__main__":
    unittest.main()

