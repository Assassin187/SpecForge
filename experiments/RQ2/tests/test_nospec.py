import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from specforge.documents import digest, hashes, read_json, save_json
from specforge.llm import LLM, MODEL_CONFIGS
from specforge.pipeline import Pipeline, framework_hashes, project_hashes
from expriments.RQ2 import adapter, run


def call(name, arguments, identifier="c1"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def response(calls=(), finish="tool_calls"):
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": None,
            "reasoning_content": "offline test", "tool_calls": list(calls)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25}}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(model_dump=lambda: value)


def project_files(binary="coap_server", requirement_ids=("R01",)):
    """A temporary development-gate fixture, never supplied to real generation."""
    files = {
        "server.h": "int fixture_add(int value);\n",
        "helper.c": '#include "server.h"\nint fixture_add(int value){return value+1;}\n',
        "server.c": '#include <stdio.h>\n#include "server.h"\nint main(int argc,char **argv){'
                    'volatile int n=argc;(void)argv;printf("%d\\n",fixture_add(n));return 0;}\n',
        "README.md": "Temporary offline development-gate fixture.\n",
        "dev.py": "import subprocess\nr=subprocess.run(['./" + binary + "','12345'],capture_output=True,text=True)\n"
                  "assert r.returncode == 0, r.stderr\nassert r.stdout.strip() == '3'\n",
        "Makefile": "CC=gcc\nCFLAGS=-std=c99 -Wall -Wextra -Wpedantic -Werror\n"
                    "all: " + binary + "\n" + binary + ": server.c helper.c server.h\n"
                    "\t$(CC) $(CFLAGS) server.c helper.c $(LDFLAGS) -o $@\n"
                    "test:\n\tpython3 dev.py\n"
                    "sanitize: CFLAGS += -fsanitize=address,undefined -g -fno-omit-frame-pointer -fno-pie\n"
                    "sanitize: LDFLAGS += -fsanitize=address,undefined -no-pie\n"
                    "sanitize: clean all\nclean:\n\trm -f " + binary + "\n.PHONY: all test sanitize clean\n",
    }
    files["delivery.json"] = json.dumps({"schema_version": 1, "files": list(files), "tests": [
        {"id": "fixture", "path": "dev.py", "requirement_ids": list(requirement_ids)}]})
    return files


def write_project(project, files):
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def write_evaluator(path, *, status="passed", required_count=1, missing=False):
    path.write_text(
        "import argparse,json,subprocess\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('--binary');p.add_argument('--out');a=p.parse_args()\n"
        "r=subprocess.run([a.binary,'12345'],capture_output=True,text=True)\n"
        "assert r.returncode == 0, r.stderr\nassert r.stdout.strip() == '3'\n"
        "out=Path(a.out);out.mkdir(parents=True,exist_ok=True)\n" +
        ("" if missing else "(out/'report.json').write_text(json.dumps(" + repr({
            "passed": True, "required_count": required_count, "scenarios": [{"id": "fixture", "status": status}]}) + "))\n"))


class NoSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "coap"
        self.pipeline = adapter.NoSpecPipeline.new(self.run, case="coap", llm=LLM(client=FakeClient([])))
        case = adapter.CASES["coap"]
        self.assertTrue(self.pipeline.freeze_inputs(case["task"], case["requirements"], case["protocol"]))
        # Local gate fixtures use one engineering requirement, not a protocol result.
        self.requirements = self.root / "fixture_requirements.md"
        self.requirements.write_text("- R01: Run the temporary development fixture.\n")
        self.files = project_files()
        self.project = self.run / "project"

    def tearDown(self):
        self.temp.cleanup()

    def use_gate_fixture(self):
        write_project(self.project, self.files)
        (self.run / "inputs/REQUIREMENTS.md").write_text(self.requirements.read_text())
        self.pipeline.state["stages"]["prepare"]["artifact_hashes"] = self.pipeline.stage_hashes("prepare")
        self.pipeline.save()

    def test_raw_inputs_and_skipped_stages_without_spec_artifacts(self):
        case = adapter.CASES["coap"]
        self.assertEqual(digest(case["task"]), digest(self.run / "inputs/TASK.md"))
        self.assertEqual(digest(case["requirements"]), digest(self.run / "inputs/REQUIREMENTS.md"))
        self.assertEqual(digest(case["protocol"]), digest(self.run / "inputs/protocol.txt"))
        self.assertEqual(self.pipeline.state["limits"], {"code": 300, "repair": 40, "max_repairs": 3})
        for stage in ("facts", "design", "specs"):
            self.assertEqual(self.pipeline.state["stages"][stage]["status"], "skipped")
        for directory in ("facts", "design_work", "specs"):
            self.assertFalse((self.run / directory).exists())
        for filename in ("scope.json", "facts.json", "module_spec.json", "bundle.json", "traceability.json"):
            self.assertFalse(list(self.run.rglob(filename)))
        reference = Pipeline.new(self.root / "reference")
        self.assertTrue(reference.freeze_inputs(case["task"], case["requirements"], case["protocol"]))
        self.assertEqual(hashes(self.run / "documents"), hashes(reference.run / "documents"))

    def test_runtime_isolation_and_editable_headers(self):
        runtime = adapter.NoSpecRuntime(self.project, {
            "/inputs": self.run / "inputs", "/documents": self.run / "documents", "/reports": self.run / "reports"},
            self.run / "logs/fixture", lambda: {"passed": False}, coder=True)
        self.assertNotIn("report_spec_gap", [t["function"]["name"] for t in runtime.tool_definitions()])
        self.assertEqual(set(runtime.inputs), {"/inputs", "/documents", "/reports"})
        result = runtime.dispatch("write_file", {"path": "/work/public.h", "content": "int first(void);\n"})
        self.assertIn("written", result)
        result = runtime.dispatch("edit_file", {"path": "/work/public.h", "old": "first", "new": "second"})
        self.assertIn("written", result)
        for path in ("/specs/bundle.json", "/facts/facts.json", "/harness/evaluate.py", "/home/ljf/SpecForge/evaluation/coap_check.py"):
            self.assertIn("error", runtime.dispatch("read_file", {"path": path}))
        self.assertIn("error", runtime.dispatch("write_file", {"path": "/inputs/TASK.md", "content": "changed"}))
        result = runtime.command("test ! -e /specs && test ! -e /harness && test ! -e /home/ljf/SpecForge && test -z \"$DS_API\"", timeout=10)
        self.assertEqual(result["exit_code"], 0, result["output"])

    def test_delivery_coverage_inventory_and_no_spec_references(self):
        write_project(self.project, self.files)
        check = lambda: adapter.delivery_checks(self.project, self.requirements)
        self.assertTrue(check()["passed"])
        original = read_json(self.project / "delivery.json")
        mutations = (
            (lambda d: d["tests"][0].update(requirement_ids=[]), "missing ['R01']"),
            (lambda d: d["tests"][0].update(requirement_ids=["R99"]), "Unknown test requirements"),
            (lambda d: d["tests"].append(dict(d["tests"][0])), "Duplicate development test ID"),
            (lambda d: d["tests"][0].update(path="absent.py"), "Test file absent"),
            (lambda d: d["files"].remove("helper.c"), "Unlisted source"),
            (lambda d: d["files"].append("missing.c"), "Missing or unsafe delivery file"),
            (lambda d: d["tests"][0].update(spec_test_refs=["functions/test.json#/TEST_VECTORS/0"]), "Additional properties"),
        )
        for mutate, message in mutations:
            with self.subTest(message=message):
                delivery = copy.deepcopy(original)
                mutate(delivery)
                save_json(self.project / "delivery.json", delivery)
                result = check()
                self.assertFalse(result["passed"])
                self.assertIn(message, "\n".join(result["errors"]))
        schema = read_json(adapter.ROOT / "schemas/artifacts.schema.json")["$defs"]["delivery"]
        self.assertIn("spec_test_refs", schema["properties"]["tests"]["items"]["required"])

    def test_shared_development_gate_and_cache(self):
        self.use_gate_fixture()
        result = self.pipeline.verify_gate()
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(set(result["phases"]), {"normal", "sanitize"})
        self.assertEqual(set(result["builds"]), {"normal", "sanitize", "delivery"})
        self.assertTrue((self.project / "coap_server").is_file())
        self.assertEqual(self.pipeline.verify_gate(), result)
        self.assertEqual(len(list((self.run / "reports").glob("verify_*"))), 1)
        report = read_json(self.run / result["report"].lstrip("/"))
        self.assertIn("output", report["builds"]["normal"])
        self.assertNotIn("output", result["builds"]["normal"])
        (self.project / "dev.py").write_text("raise AssertionError('offline failure')\n")
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertIn("offline failure", result["phases"]["normal"]["command"]["output"])

    def test_missing_manifest_does_not_hide_build_or_test_diagnostics(self):
        self.use_gate_fixture()
        (self.project / "delivery.json").unlink()
        (self.project / "dev.py").write_text("raise AssertionError('failed without manifest')\n")
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertIn("delivery.json", "\n".join(result["errors"]))
        for phase in ("normal", "sanitize"):
            self.assertEqual(result["builds"][phase]["exit_code"], 0)
            self.assertFalse(result["phases"][phase]["passed"])

    def test_build_failure_and_missing_sanitizer_instrumentation(self):
        self.use_gate_fixture()
        (self.project / "helper.c").write_text("invalid C syntax\n")
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertEqual(result["phases"]["normal"]["reason"], "build_failed")
        (self.project / "helper.c").write_text(self.files["helper.c"])
        makefile = self.project / "Makefile"
        makefile.write_text(makefile.read_text().replace("-fsanitize=address,undefined", ""))
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertIn("Sanitizer binary lacks ASan/UBSan instrumentation", result["errors"])

    def test_sanitizer_runtime_failure(self):
        self.use_gate_fixture()
        (self.project / "helper.c").write_text('#include "server.h"\nint fixture_add(int value){volatile int v=2147483647;v+=value;return 3;}\n')
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["phases"]["normal"]["passed"])
        self.assertFalse(result["phases"]["sanitize"]["passed"])
        self.assertIn("runtime error:", result["phases"]["sanitize"]["command"]["output"])

    def test_development_tests_cannot_replace_sanitizer_binary(self):
        self.use_gate_fixture()
        makefile = self.project / "Makefile"
        makefile.write_text(makefile.read_text().replace("test:\n", "test:\n\t$(MAKE) clean\n\t$(MAKE) all\n"))
        result = self.pipeline.verify_gate()
        self.assertFalse(result["passed"])
        self.assertFalse(result["phases"]["sanitize"]["binary_unchanged"])

    def test_native_agent_stops_on_pass_and_uses_raw_inputs(self):
        self.use_gate_fixture()
        client = FakeClient([response([call("check", {})])])
        self.pipeline.llm = LLM(client=client)
        before = framework_hashes()
        with patch.object(Pipeline, "do_facts", side_effect=AssertionError("Facts must be skipped")), \
                patch.object(Pipeline, "do_design", side_effect=AssertionError("Design must be skipped")), \
                patch.object(Pipeline, "do_specs", side_effect=AssertionError("Specs must be skipped")):
            self.assertTrue(self.pipeline.execute())
        self.assertTrue(self.pipeline.state["generation_passed"])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(self.pipeline.state["jobs"]), 1)
        self.assertEqual(self.pipeline.state["jobs"][0]["responses"], 1)
        request = client.requests[0]
        self.assertIn("0/300 responses already used", request["messages"][1]["content"])
        self.assertIn("/inputs/TASK.md", request["messages"][0]["content"])
        self.assertNotIn("/specs", request["messages"][0]["content"])
        self.assertNotIn("spec_test_refs", request["messages"][0]["content"])
        self.assertEqual(framework_hashes(), before)

    def test_first_job_and_three_repair_limits_are_terminal(self):
        def exhausted(llm, runtime, prefix, task, limit, log_dir, progress):
            return {"passed": False, "reason": "response_limit", "responses": limit,
                    "usage": {"requests": limit, "input_tokens": limit, "output_tokens": limit}}
        with patch.object(self.pipeline, "verify_gate", return_value={"passed": False, "errors": ["fixture failure"]}), \
                patch.object(adapter, "run_agent", side_effect=exhausted) as agent:
            self.assertFalse(self.pipeline.execute())
            self.assertEqual([c.args[4] for c in agent.call_args_list], [300, 40, 40, 40])
            self.assertEqual(self.pipeline.state["implementation_repairs"], 3)
            self.assertEqual(self.pipeline.state["usage"]["requests"], 420)
            self.assertEqual([j["name"] for j in self.pipeline.state["jobs"]], ["01_code", "02_repair", "03_repair", "04_repair"])
            self.assertFalse(self.pipeline.resume())
            self.assertEqual(agent.call_count, 4)

    def test_api_resume_preserves_job_budget_usage_and_model(self):
        self.use_gate_fixture()
        first = FakeClient([response([call("read_file", {"path": "/inputs/TASK.md"})]), RuntimeError("offline interruption")])
        self.pipeline.llm = LLM(MODEL_CONFIGS["qwen3.8-flash"], client=first)
        self.pipeline.state["model"] = self.pipeline.llm.config.record()
        self.pipeline.save()
        self.assertFalse(self.pipeline.execute())
        self.assertEqual(self.pipeline.state["jobs"][0]["reason"], "api_error")
        self.assertEqual(self.pipeline.state["jobs"][0]["responses"], 1)
        self.assertEqual(self.pipeline.state["implementation_repairs"], 0)
        second = FakeClient([response([call("check", {})])])
        reopened = adapter.NoSpecPipeline(self.run)
        with patch("specforge.pipeline.LLM", side_effect=lambda config: LLM(config, client=second)) as model:
            self.assertTrue(reopened.resume())
        model.assert_called_once_with(MODEL_CONFIGS["qwen3.8-flash"])
        self.assertEqual(len(reopened.state["jobs"]), 1)
        self.assertEqual(reopened.state["jobs"][0]["responses"], 2)
        self.assertEqual(reopened.state["usage"]["requests"], 2)
        self.assertEqual(reopened.state["usage"]["input_tokens"], 200)
        self.assertIn("1/300 responses already used", second.requests[0]["messages"][1]["content"])
        self.assertTrue((self.run / "logs/01_code/api/request_002.json").is_file())

    def test_last_repair_api_error_resumes_same_round(self):
        def failed(llm, runtime, prefix, task, limit, log_dir, progress):
            last = self.pipeline.state["implementation_repairs"] == 3
            return {"passed": False, "reason": "api_error" if last else "response_limit", "responses": 1,
                    "usage": {"requests": 1}}
        with patch.object(self.pipeline, "verify_gate", return_value={"passed": False, "errors": ["failure"]}), \
                patch.object(adapter, "run_agent", side_effect=failed):
            self.assertFalse(self.pipeline.execute())
            with patch.object(adapter, "run_agent", return_value={"passed": False, "reason": "response_limit", "responses": 40,
                                                                  "usage": {"requests": 40}}) as agent:
                self.assertFalse(self.pipeline.resume())
                self.assertEqual(agent.call_count, 1)
                self.assertEqual(agent.call_args.args[4], 40)
        self.assertEqual(self.pipeline.state["implementation_repairs"], 3)
        self.assertEqual(len(self.pipeline.state["jobs"]), 4)

    def test_resume_rejects_changed_inputs_and_adapter(self):
        (self.run / "inputs/TASK.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifacts changed"):
            self.pipeline.resume()
        self.assertTrue(self.pipeline.state["manual_edits"])
        self.pipeline.state["adapter_hashes"] = {}
        self.pipeline.save()
        with self.assertRaisesRegex(ValueError, "adapter/prompts changed"):
            self.pipeline.resume()

    def test_original_pipeline_cannot_resume_nospec_and_reverse(self):
        with self.assertRaisesRegex(ValueError, "Historical runs"):
            Pipeline(self.run).resume()
        original = Pipeline.new(self.root / "original")
        before = digest(original.run / "run.json")
        with self.assertRaisesRegex(ValueError, "NoSpec-SpecForge"):
            adapter.NoSpecPipeline(original.run).resume()
        self.assertEqual(digest(original.run / "run.json"), before)
        self.assertEqual(run.main(["resume", "--run", str(original.run)]), 1)
        self.assertEqual(digest(original.run / "run.json"), before)

    def test_independent_evaluation_runs_both_builds_and_preserves_original(self):
        self.use_gate_fixture()
        evaluator = self.root / "evaluator.py"
        write_evaluator(evaluator)
        before = hashes(self.project)
        report = adapter.evaluate_project(self.project, self.pipeline.state["runtime_contract"], evaluator, self.root / "evaluation")
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(set(report["phases"]), {"normal", "sanitize"})
        self.assertEqual(hashes(self.project), before)
        self.assertEqual(report["evaluator_sha256"], digest(evaluator))

    def test_missing_incomplete_and_skipped_independent_cases_fail(self):
        self.use_gate_fixture()
        evaluator = self.root / "evaluator.py"
        for name, settings in (("missing", {"missing": True}), ("incomplete", {"required_count": 2}), ("skipped", {"status": "skipped"})):
            with self.subTest(name=name):
                write_evaluator(evaluator, **settings)
                result = adapter.evaluate_project(self.project, self.pipeline.state["runtime_contract"], evaluator, self.root / name)
                self.assertFalse(result["passed"])
                self.assertIn("normal independent evaluation failed", result["errors"])
                self.assertIn("sanitize independent evaluation failed", result["errors"])

    def test_evaluation_is_terminal_and_never_invokes_model(self):
        self.use_gate_fixture()
        self.assertTrue(self.pipeline.stage("code", lambda: {"passed": True}))
        self.assertTrue(self.pipeline.stage("verify", self.pipeline.do_verify))
        evaluator = self.root / "evaluator.py"
        write_evaluator(evaluator)
        with patch.dict(adapter.CASES["coap"], {"evaluator": evaluator}), \
                patch.object(adapter, "run_agent", side_effect=AssertionError("Evaluator must not invoke model")):
            report = adapter.evaluate_run(self.run)
        self.assertTrue(report["passed"])
        reopened = adapter.NoSpecPipeline(self.run)
        self.assertTrue(reopened.state["evaluation_started"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            reopened.resume()
        (self.project / "helper.c").write_text("changed")
        with self.assertRaisesRegex(ValueError, "delivery changed"):
            adapter.evaluate_run(self.run)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_four_cases_prepare_without_api_calls(self):
        output = self.root / "batch"
        with patch("specforge.llm.OpenAI") as client, patch.object(adapter, "run_agent") as agent, \
                patch.object(run, "evaluate_run") as evaluate:
            self.assertEqual(run.main(["run", "--out", str(output), "--until", "prepare"]), 0)
        client.assert_not_called()
        agent.assert_not_called()
        evaluate.assert_not_called()
        summary = read_json(output / "summary.json")
        self.assertEqual(list(summary["runs"]), list(adapter.CASES))
        self.assertEqual(summary["cases"], ["mqtt", "coap", "http11", "smtp"])
        self.assertEqual(summary["totals"], {"runs": 4, "generation_passed": 0, "independent_passed": 0})
        for case, inputs in adapter.CASES.items():
            state = read_json(output / case / "run.json")
            self.assertEqual(state["runtime_contract"], inputs["runtime_contract"])
            self.assertIn(inputs["runtime_contract"]["argv_contract"], inputs["task"].read_text())
            self.assertEqual(state["stages"]["prepare"]["status"], "passed")
            self.assertEqual(state["jobs"], [])
            self.assertEqual(summary["runs"][case]["evaluation"]["status"], "not_executed")
        self.assertEqual(read_json(output / "mqtt/documents/index.json")["extractor"], "pdftotext -layout")
        with self.assertRaises(SystemExit):
            run.main(["run", "--out", str(output), "--until", "prepare"])

    def test_single_case_model_and_failed_case_does_not_stop_batch(self):
        output = self.root / "single"
        self.assertEqual(run.main(["run", "--out", str(output), "--cases", "coap", "--model", "qwen3.8-flash", "--until", "prepare"]), 0)
        self.assertEqual(read_json(output / "coap/run.json")["model"], MODEL_CONFIGS["qwen3.8-flash"].record())
        self.assertEqual(read_json(output / "summary.json")["cases"], ["coap"])
        output = self.root / "failed_batch"
        original = adapter.NoSpecPipeline.freeze_inputs

        def prepare(pipeline, task, requirements, protocol):
            if pipeline.state["case"] == "coap":
                return pipeline.stage("prepare", lambda: {"passed": False, "error": "offline failure"})
            return original(pipeline, task, requirements, protocol)

        with patch.object(adapter.NoSpecPipeline, "freeze_inputs", prepare):
            self.assertEqual(run.main(["run", "--out", str(output), "--cases", "coap", "http11", "--until", "prepare"]), 1)
        summary = read_json(output / "summary.json")
        self.assertEqual(summary["runs"]["coap"]["stages"]["prepare"]["status"], "failed")
        self.assertEqual(summary["runs"]["http11"]["stages"]["prepare"]["status"], "passed")

    def test_cli_native_generation_resume_evaluation_and_summary(self):
        output = self.root / "batch"
        self.assertEqual(run.main(["run", "--out", str(output), "--cases", "coap", "--until", "prepare"]), 0)
        target = output / "coap"
        state = read_json(target / "run.json")
        requirement_ids = adapter.re.findall(r"\b(R\d+)\s*:", (target / "inputs/REQUIREMENTS.md").read_text())
        files = project_files(requirement_ids=requirement_ids)
        writes = [call("write_file", {"path": "/work/" + name, "content": content}, f"c{number}")
                  for number, (name, content) in enumerate(files.items())]
        first = FakeClient([response(writes), RuntimeError("offline API interruption")])
        evaluator = self.root / "evaluator.py"
        write_evaluator(evaluator)
        with patch("specforge.pipeline.LLM", side_effect=lambda config: LLM(config, client=first)), \
                patch.dict(adapter.CASES["coap"], {"evaluator": evaluator}):
            self.assertEqual(run.main(["resume", "--run", str(target)]), 1)
        summary = read_json(output / "summary.json")
        self.assertFalse(summary["runs"]["coap"]["generation_passed"])
        self.assertEqual(summary["runs"]["coap"]["evaluation"]["status"], "not_executed")
        second = FakeClient([response([call("check", {})])])
        with patch("specforge.pipeline.LLM", side_effect=lambda config: LLM(config, client=second)), \
                patch.dict(adapter.CASES["coap"], {"evaluator": evaluator}):
            self.assertEqual(run.main(["resume", "--run", str(target)]), 0)
            before = hashes(target / "project")
            self.assertEqual(run.main(["evaluate", "--run", str(target)]), 0)
            self.assertEqual(hashes(target / "project"), before)
        summary = read_json(output / "summary.json")
        row = summary["runs"]["coap"]
        self.assertTrue(row["generation_passed"])
        self.assertEqual(row["evaluation"]["status"], "passed")
        self.assertEqual(row["evaluation"]["report"], "evaluation/evaluation_002/report.json")
        self.assertEqual(row["evaluation"]["phases"]["normal"]["passed_count"], 1)
        self.assertEqual(row["usage"]["requests"], 2)
        self.assertGreater(row["elapsed_seconds"], 0)
        self.assertTrue(row["resumed"])
        self.assertFalse(row["fresh"])
        self.assertTrue(read_json(target / "run.json")["evaluation_started"])
        self.assertEqual(summary["totals"]["independent_passed"], 1)
        self.assertEqual(state["model"], read_json(target / "run.json")["model"])
        with patch.object(adapter, "evaluate_project", side_effect=RuntimeError("offline evaluator failure")):
            self.assertEqual(run.main(["evaluate", "--run", str(target)]), 1)
        summary = read_json(output / "summary.json")
        self.assertEqual(summary["runs"]["coap"]["evaluation"]["status"], "failed")
        self.assertEqual(summary["totals"]["independent_passed"], 0)
        self.assertIn("offline evaluator failure", summary["errors"]["coap"])
        self.assertIsNone(read_json(target / "run.json")["evaluation"])
        self.assertTrue((target / "evaluation/evaluation_002/report.json").is_file())

    def test_run_automatically_evaluates_without_repairing_independent_failure(self):
        output = self.root / "batch"
        requirements = adapter.CASES["coap"]["requirements"].read_text()
        files = project_files(requirement_ids=adapter.re.findall(r"\b(R\d+)\s*:", requirements))
        writes = [call("write_file", {"path": "/work/" + name, "content": content}, f"c{number}")
                  for number, (name, content) in enumerate(files.items())]
        client = FakeClient([response(writes), response([call("check", {})])])
        evaluator = self.root / "evaluator.py"
        write_evaluator(evaluator, status="failed")
        with patch("specforge.pipeline.LLM", side_effect=lambda config: LLM(config, client=client)), \
                patch.dict(adapter.CASES["coap"], {"evaluator": evaluator}):
            self.assertEqual(run.main(["run", "--out", str(output), "--cases", "coap"]), 1)
        state = read_json(output / "coap/run.json")
        self.assertTrue(state["generation_passed"])
        self.assertTrue(state["evaluation_started"])
        self.assertFalse(state["evaluation"]["passed"])
        self.assertEqual(state["implementation_repairs"], 0)
        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(len(client.requests), 2)
        summary = read_json(output / "summary.json")
        self.assertEqual(summary["runs"]["coap"]["evaluation"]["status"], "failed")
        self.assertEqual(summary["totals"], {"runs": 1, "generation_passed": 1, "independent_passed": 0})


if __name__ == "__main__":
    unittest.main()
