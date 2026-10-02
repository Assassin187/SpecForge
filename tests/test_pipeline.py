import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specforge.documents import ROOT, read_json, save_json
from specforge.pipeline import Pipeline, verify_project
from specforge.specs import publish, scaffold
from specforge.evaluation import evaluate_run


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name) / "run"
        self.pipeline = Pipeline.new(self.run)

    def tearDown(self):
        self.temp.cleanup()

    def test_stage_gates_and_resume_hashes(self):
        case = ROOT / "cases/mqtt_min"
        self.assertTrue(self.pipeline.freeze_inputs(case / "TASK.md", case / "REQUIREMENTS.md", case / "spec/mqtt-v3.1.1-os.pdf"))
        self.assertTrue(self.pipeline.execute("prepare"))
        with patch.object(self.pipeline, "execute", return_value=True):
            self.assertTrue(self.pipeline.resume())
        self.assertTrue(self.pipeline.state["resumed"])
        (self.run / "inputs/TASK.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifacts changed"):
            self.pipeline.resume()
        self.assertTrue(self.pipeline.state["manual_edits"])

    def test_failure_does_not_advance(self):
        self.pipeline.state["stages"]["prepare"]["status"] = "passed"
        with patch.object(self.pipeline, "do_facts", return_value={"passed": False, "reason": "bad_evidence"}):
            self.assertFalse(self.pipeline.execute())
        self.assertEqual(self.pipeline.state["stages"]["design"]["status"], "pending")

    def test_facts_gate_requires_concrete_runtime_binary(self):
        scope = read_json(ROOT / "assets/mqtt_reference/bundle/scope.json")
        for binary, accepted in (("Executable name chosen by the implementer", False),
                                 ("<server>", False), ("bin/local_server", True)):
            with self.subTest(binary=binary):
                scope["runtime_contract"]["binary_name"] = binary
                save_json(self.run / "facts/scope.json", scope)
                with patch("specforge.pipeline.check_facts", return_value={"passed": True, "errors": []}):
                    report = self.pipeline.facts_gate()
                self.assertEqual(report["passed"], accepted)
                self.assertEqual(read_json(self.run / "reports/facts.json"), report)
                if not accepted:
                    self.assertIn("runtime_contract.binary_name", report["errors"][0])

    def test_spec_publication_waits_for_semantic_review(self):
        reference = ROOT / "assets/mqtt_reference/bundle"
        shutil.copytree(reference, self.run / "design_work", dirs_exist_ok=True)
        for path in (self.run / "design_work/files").glob("*.json"):
            file_spec = read_json(path)
            for interface in file_spec["SOURCE"]["INTERFACE"]:
                interface.setdefault("FUNCTION_TYPE", "ALGORITHM")
            save_json(path, file_spec)
        save_json(self.run / "facts/scope.json", read_json(reference / "scope.json"))
        facts = [{"id": "F1", "statement": "Unsupported features SHOULD use the generic rejection.", "values": {}},
                 {"id": "F2", "statement": "Undefined message boundaries MUST use the framing rejection and close.", "values": {"close": True}}]
        save_json(self.run / "facts/facts.json", {"facts": facts})
        save_json(self.run / "design_work/traceability.json", {"requirements": [{"requirement_id": "R01", "semantic_review": "author claim"}]})
        (self.run / "design_work/WORKLOG.md").write_text("Author: everything is complete; just run check.")
        for behavior_passed, review_passed in ((False, True), (True, False), (True, True)):
            with self.subTest(behavior=behavior_passed, review=review_passed):
                with patch.object(self.pipeline, "job", side_effect=[{"passed": behavior_passed},
                                                                   {"passed": review_passed}]) as jobs, \
                        patch("specforge.pipeline.publish") as publication:
                    result = self.pipeline.do_specs()
                self.assertEqual(result["passed"], behavior_passed and review_passed)
                self.assertEqual([call.args[0] for call in jobs.call_args_list],
                                 ["specs", "spec_review"] if behavior_passed else ["specs"])
                if behavior_passed:
                    trace = read_json(self.pipeline.bundle() / "traceability.json")
                    self.assertNotIn("semantic_review", trace["requirements"][0])
                    self.assertFalse((self.pipeline.bundle() / "WORKLOG.md").exists())
                    self.assertEqual(jobs.call_args.args[-1], "review")
                    self.assertEqual(set(jobs.call_args.args[2]), {"/facts", "/schemas"})
                    for requirement in read_json(self.run / "facts/scope.json")["requirements"]:
                        self.assertIn(requirement["description"], jobs.call_args.args[3])
                    for fact in facts:
                        self.assertIn(fact["id"], jobs.call_args.args[3])
                        self.assertIn(fact["statement"], jobs.call_args.args[3])
                self.assertEqual(publication.call_count, int(behavior_passed and review_passed))

    def test_review_gate_requires_each_requirement_record(self):
        work = self.run / "design_work"
        save_json(self.run / "facts/facts.json", {"facts": []})
        save_json(self.run / "facts/scope.json", {"requirements": []})
        rows = [{"requirement_id": "R01", "semantic_review": "checked"},
                {"requirement_id": "R02"}]
        for review, accepted in ((None, False), (" ", False), ("F02 derived response and checked state", True)):
            with self.subTest(review=review):
                if review is None:
                    rows[1].pop("semantic_review", None)
                else:
                    rows[1]["semantic_review"] = review
                save_json(work / "traceability.json", {"requirements": rows})
                with patch("specforge.pipeline.validate", return_value={"passed": True, "errors": []}):
                    result = self.pipeline.spec_gate(work, review=True)
                self.assertEqual(result["passed"], accepted)
                if not accepted:
                    self.assertIn("R02", result["errors"][0])

    def test_scaffold_copies_only_planned_interfaces(self):
        shutil.copytree(ROOT / "assets/mqtt_reference/bundle", self.pipeline.bundle())
        project = self.run / "project"
        scaffold(self.pipeline.bundle(), project)
        self.assertFalse((project / "Makefile").exists())
        self.assertFalse((project / "README.md").exists())
        self.assertFalse((project / "tests").exists())
        result = verify_project(project, self.run / "reports", self.run / "logs", bundle=self.pipeline.bundle())
        self.assertFalse(result["passed"])
        self.assertTrue(any("delivery.json" in error for error in result["errors"]))
        self.assertEqual(result["phases"]["normal"]["reason"], "build_failed")
        self.assertEqual(result["phases"]["sanitize"]["reason"], "build_failed")

    def test_three_repairs_and_one_spec_repair(self):
        shutil.copytree(ROOT / "assets/mqtt_reference/bundle", self.pipeline.bundle())
        scaffold(self.pipeline.bundle(), self.run / "project")
        failure = {"passed": False, "errors": ["runtime failure"], "phases": {}}
        with patch.object(self.pipeline, "job", return_value={"passed": False, "reason": "response_limit"}) as job, \
                patch.object(self.pipeline, "verify_gate", return_value=failure):
            result = self.pipeline.do_code()
        self.assertFalse(result["passed"])
        self.assertEqual(job.call_count, 4)
        self.assertEqual(self.pipeline.state["implementation_repairs"], 3)
        self.pipeline.state["implementation_repairs"] = 0
        gap = {"passed": False, "reason": "spec_gap", "gap": {"kind": "interface", "problem": "missing", "spec_refs": ["module_spec.json#/PROTOCOL"], "evidence": "/logs/a.log"}}
        def republish(_gap):
            shutil.copyfile(ROOT / "assets/mqtt_reference/bundle/bundle.json", self.pipeline.bundle() / "bundle.json")
            publish(self.pipeline.bundle(), 2, "manual_reference")
            return {"passed": True}
        with patch.object(self.pipeline, "job", return_value=gap), patch.object(self.pipeline, "do_specs", side_effect=republish), \
                patch.object(self.pipeline, "verify_gate", return_value=failure):
            result = self.pipeline.do_code()
        self.assertEqual(result["reason"], "spec_repair_limit")
        self.assertEqual(self.pipeline.state["spec_repairs"], 1)
        self.assertTrue((self.run / "snapshots/before_spec_repair/specs").is_dir())

    def test_evaluation_is_terminal_and_never_calls_model(self):
        with self.assertRaisesRegex(ValueError, "completed"):
            evaluate_run(self.run, ROOT / "evaluation/mqtt_check.py")
        self.pipeline.state["evaluation_started"] = True
        self.pipeline.save()
        with patch.object(self.pipeline, "model") as model:
            with self.assertRaisesRegex(ValueError, "frozen"):
                self.pipeline.resume()
            model.assert_not_called()

    def test_coder_mounts_only_specs_and_development_reports(self):
        shutil.copytree(ROOT / "assets/mqtt_reference/bundle", self.pipeline.bundle())
        scaffold(self.pipeline.bundle(), self.run / "project")
        from specforge.llm import LLM
        from specforge.tools import ToolRuntime
        model = LLM(client=object())
        with patch.object(self.pipeline, "model", return_value=model), patch("specforge.pipeline.run_agent", return_value={"passed": True}) as agent:
            self.pipeline.job("code", self.run / "project", {"/specs": self.pipeline.bundle()}, "task", lambda: {"passed": True}, "code", coder=True)
        runtime = agent.call_args.args[1]
        self.assertEqual(set(runtime.inputs), {"/specs", "/reports"})
        self.assertNotIn("mqtt_check", agent.call_args.args[2])
        self.assertNotIn(self.run / "project/Makefile", runtime.readonly)
        self.assertNotIn(self.run / "project/README.md", runtime.readonly)
        self.assertTrue(all(p.is_file() for p in runtime.readonly))
        self.assertIn("error", runtime.dispatch("read_file", {"path": "/harness/evaluate.py"}))
        self.assertIn("error", runtime.dispatch("read_file", {"path": "/evaluation/report.json"}))


if __name__ == "__main__":
    unittest.main()

