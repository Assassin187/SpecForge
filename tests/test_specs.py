import json
import shutil
import tempfile
import unittest
from pathlib import Path

from specforge.documents import ROOT, check_facts, prepare, read_json, save_json
from specforge.specs import validate


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


if __name__ == "__main__":
    unittest.main()

