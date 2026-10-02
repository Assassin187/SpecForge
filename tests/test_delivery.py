import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specforge.documents import ROOT, read_json, save_json
from specforge.evaluation import evaluate_project, evaluate_run
from specforge.pipeline import Pipeline, project_hashes, verify_project
from specforge.specs import delivery_checks, scaffold
from evaluation.coap_check import packet, parse, request


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        shutil.copytree(ROOT / "assets/mqtt_reference/bundle", self.bundle)
        scope = read_json(self.bundle / "scope.json")
        scope["requirements"] = [{"id": "R01", "description": "fixture execution"}]
        save_json(self.bundle / "scope.json", scope)
        function = next((self.bundle / "functions").rglob("*.json"))
        value = read_json(function)
        value["TEST_VECTORS"] = [{"NAME": "fixture", "INPUT": {}, "EXPECT": {"exit": 0}}]
        save_json(function, value)
        reference = function.relative_to(self.bundle).as_posix() + "#/TEST_VECTORS/0"
        manifest = read_json(self.bundle / "bundle.json")
        self.project = self.root / "project"
        scaffold(self.bundle, self.project)
        sources = [s["path"] for s in manifest["files"]]
        for number, name in enumerate(sources):
            source = self.project / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('#include <stdio.h>\nint main(int argc,char **argv){volatile int n=argc;n+=1;puts(argv[0]);return n<1;}\n'
                              if number == 0 else "typedef int fixture_translation_unit;\n")
        (self.project / "README.md").write_text("Agent-authored development fixture documentation.\n")
        (self.project / "dev.py").write_text("import subprocess\nassert subprocess.run(['./mqtt_broker']).returncode == 0\n")
        files = [*sources, *(h["path"] for h in manifest["headers"]), "README.md", "Makefile", "dev.py"]
        save_json(self.project / "delivery.json", {"schema_version": 1, "files": files, "tests": [
            {"id": "fixture", "path": "dev.py", "requirement_ids": ["R01"], "spec_test_refs": [reference]}]})
        (self.project / "Makefile").write_text(
            "CC=gcc\nCFLAGS=-std=c99 -Wall -Wextra -Wpedantic -Werror\nSOURCES=" + " ".join(sources) + "\n"
            "all: mqtt_broker\nmqtt_broker: $(SOURCES)\n\t$(CC) $(CFLAGS) $(SOURCES) $(LDFLAGS) -o $@\n"
            "test:\n\tpython3 dev.py\nsanitize: CFLAGS += -fsanitize=address,undefined -g -fno-pie\n"
            "sanitize: LDFLAGS += -fsanitize=address,undefined -no-pie\nsanitize: clean all\n"
            "clean:\n\trm -f mqtt_broker\n.PHONY: all test sanitize clean\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_agent_delivery_and_sanitized_tests(self):
        result = verify_project(self.project, self.root / "reports", self.root / "logs", bundle=self.bundle)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(set(result["phases"]), {"normal", "sanitize"})
        self.assertNotIn("mqtt_check", str(result))

    def test_development_tests_cannot_replace_current_binary(self):
        makefile = self.project / "Makefile"
        makefile.write_text(makefile.read_text().replace("test:\n", "test:\n\t$(MAKE) clean\n\t$(MAKE) all\n"))
        result = verify_project(self.project, self.root / "reports", self.root / "logs", bundle=self.bundle)
        self.assertFalse(result["passed"])
        self.assertFalse(result["phases"]["sanitize"]["binary_unchanged"])
        self.assertEqual(result["phases"]["sanitize"]["status"], "failed")
        self.assertTrue(any("replaced the current runtime binary" in error for error in result["errors"]))

    def test_independent_launch_uses_actual_argument_template(self):
        delivery = read_json(self.project / "delivery.json")
        delivery.update(schema_version=2, startup_args=["--directory", "{1}", "--port={0}"])
        save_json(self.project / "delivery.json", delivery)
        source = read_json(self.bundle / "bundle.json")["files"][0]["path"]
        (self.project / source).write_text(
            '#include <stdio.h>\nint main(int argc,char **argv){volatile int n=argc;n+=1;'
            'for(int i=1;i<argc;i++)puts(argv[i]);return n<1;}\n')
        evaluator = self.root / "argument_evaluator.py"
        evaluator.write_text(
            "import argparse,json,subprocess\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser();p.add_argument('--binary');p.add_argument('--out',type=Path)\n"
            "a=p.parse_args();a.out.mkdir(parents=True)\n"
            "r=subprocess.run([a.binary,'45123','capture folder'],capture_output=True,text=True)\n"
            "ok=r.returncode==0 and r.stdout=='--directory\\ncapture folder\\n--port=45123\\n'\n"
            "(a.out/'report.json').write_text(json.dumps({'passed':ok,'required_count':1,"
            "'scenarios':[{'id':'actual_cli','status':'passed' if ok else 'failed'}]}))\n"
            "raise SystemExit(0 if ok else 1)\n")
        result = evaluate_project(self.project, self.bundle, evaluator, self.root / "evaluation")
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["startup_args"], delivery["startup_args"])
        self.assertEqual(set(result["phases"]), {"normal", "sanitize"})
        delivery.pop("startup_args")
        save_json(self.project / "delivery.json", delivery)
        self.assertFalse(delivery_checks(self.project, self.bundle)["passed"])
        delivery["schema_version"] = 1
        save_json(self.project / "delivery.json", delivery)
        self.assertTrue(delivery_checks(self.project, self.bundle)["passed"])
        evaluator.write_text(evaluator.read_text().replace(
            "--directory\\ncapture folder\\n--port=45123\\n", "45123\\ncapture folder\\n"))
        legacy = evaluate_project(self.project, self.bundle, evaluator, self.root / "legacy_evaluation")
        self.assertTrue(legacy["passed"], legacy["errors"])
        self.assertIsNone(legacy["startup_args"])

    def test_missing_reference_failed_test_and_hashes(self):
        before = project_hashes(self.project)
        (self.project / "dev.py").write_text("raise AssertionError('development failure')\n")
        self.assertNotEqual(project_hashes(self.project), before)
        result = verify_project(self.project, self.root / "reports", self.root / "logs", bundle=self.bundle)
        self.assertFalse(result["passed"])
        delivery = read_json(self.project / "delivery.json")
        delivery["tests"][0]["spec_test_refs"] = ["functions/missing.json#/TEST_VECTORS/0"]
        save_json(self.project / "delivery.json", delivery)
        errors = delivery_checks(self.project, self.bundle)["errors"]
        self.assertTrue(any("functions/missing.json#/TEST_VECTORS/0" in e for e in errors))
        self.assertTrue(any("zero-based index" in e for e in errors))

    def test_manual_bundle_without_requirement_ids(self):
        scope = read_json(self.bundle / "scope.json")
        scope["requirements"] = []
        save_json(self.bundle / "scope.json", scope)
        delivery = read_json(self.project / "delivery.json")
        delivery["tests"][0]["requirement_ids"] = []
        save_json(self.project / "delivery.json", delivery)
        self.assertTrue(delivery_checks(self.project, self.bundle)["passed"])
        scope["requirements"] = [{"id": "R01", "description": "required behavior"}]
        save_json(self.bundle / "scope.json", scope)
        self.assertFalse(delivery_checks(self.project, self.bundle)["passed"])

    def test_independent_failure_records_without_agent_feedback(self):
        run = self.root / "run"
        pipeline = Pipeline.new(run)
        shutil.copytree(self.project, run / "project", dirs_exist_ok=True)
        shutil.copytree(self.bundle, pipeline.bundle())
        pipeline.state.update(generation_passed=True, project_hashes=project_hashes(run / "project"))
        pipeline.save()
        result = {"passed": False, "evaluator_sha256": "0" * 64}
        with patch("specforge.evaluation.evaluate_project", return_value=result), patch("specforge.specs.validate", return_value={"passed": True}), patch.object(pipeline, "model") as model:
            self.assertFalse(evaluate_run(run, Path("fixture_evaluator.py"))["passed"])
            model.assert_not_called()
        self.assertTrue(read_json(run / "run.json")["evaluation_started"])
        self.assertEqual(project_hashes(run / "project"), pipeline.state["project_hashes"])

    def test_coap_rfc_wire_fixtures_and_extended_options(self):
        # RFC 7252 Appendix A: CON GET /temperature, MID 0x7d34, no Token.
        wire = bytes.fromhex("40017d34bb") + b"temperature"
        self.assertEqual(request("temperature", 0x7d34, b""), wire)
        self.assertEqual(parse(wire)["options"], [(11, b"temperature")])
        raw = packet(2, 69, 0x7d34, payload=b"22.3 C")
        self.assertEqual(raw, bytes.fromhex("60457d34ff") + b"22.3 C")
        for length in (0, 12, 13, 268, 269):
            vector = packet(0, 1, 1, options=[(2048, b"x" * length)])
            self.assertEqual(parse(vector)["options"], [(2048, b"x" * length)])
        with self.assertRaises(AssertionError):
            parse(bytes.fromhex("60457d34ff"))


if __name__ == "__main__":
    unittest.main()
