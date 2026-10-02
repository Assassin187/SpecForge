import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from specforge.agent import run_agent
from specforge.documents import ROOT, read_json
from specforge.llm import LLM, total_usage
from specforge.tools import ToolRuntime


def response(calls=(), finish="tool_calls", reasoning="thought"):
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": None,
            "reasoning_content": reasoning, "tool_calls": list(calls)}}],
            "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20,
                      "completion_tokens": 25, "completion_tokens_details": {"reasoning_tokens": 5}}}


def call(name, arguments, identifier="c1"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        value = next(self.responses)
        return SimpleNamespace(model_dump=lambda: value)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input"
        self.inputs.mkdir()
        self.runtime = ToolRuntime(self.root / "work", {"/input": self.inputs}, self.root / "logs", lambda: {"passed": False})

    def tearDown(self):
        self.temp.cleanup()

    def test_native_multiple_calls_reasoning_and_usage(self):
        client = FakeClient([response([call("write_file", {"path": "/work/a", "content": "ok"}),
                                      call("read_file", {"path": "/work/a"}, "c2")]), response(finish="stop")])
        self.runtime.check_callback = lambda: {"passed": (self.runtime.work / "a").read_text() == "ok"}
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 4, self.root / "api")
        self.assertTrue(report["passed"])
        history = client.requests[1]["messages"]
        self.assertEqual(history[2]["reasoning_content"], "thought")
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"], ["c1", "c2"])
        self.assertEqual(report["usage"]["output_tokens"], 50)
        self.assertEqual(report["usage"]["reasoning_tokens"], 10)
        self.assertEqual(report["usage"]["cache_hit_tokens"], 160)

    def test_invalid_model_response_reports_api_error_without_passing_gate(self):
        for index, choices in enumerate((None, [], [None], [{"message": None, "finish_reason": "stop"}])):
            with self.subTest(choices=choices):
                raw = {"choices": choices, "error": {"message": "upstream unavailable", "code": "service_error"}}
                client = FakeClient([raw])
                self.runtime.check_callback = lambda: {"passed": True}
                logs = self.root / f"invalid_api_{index}"
                report = run_agent(LLM(client=client), self.runtime, "role", "task", 2, logs)
                self.assertFalse(report["passed"])
                self.assertEqual(report["reason"], "api_error")
                self.assertIn("missing completion choice/message", report["error"])
                self.assertIn("upstream unavailable", report["error"])
                self.assertIn("service_error", report["error"])
                self.assertEqual(read_json(logs / "api_error_001.json")["error"], report["error"])
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(report["responses"], 0)

    def test_unprocessed_queue_timeout_retries_same_request_and_remains_bounded(self):
        queued = {"choices": None, "error": {"message":
            "We were unable to start processing your request within the 900-second timeout limit. Please try again later."}}
        client = FakeClient([queued, response([call("write_file", {"path": "/work/a", "content": "done"})]),
                             response(finish="stop")])
        self.runtime.check_callback = lambda: {"passed": (self.runtime.work / "a").exists()}
        logs = self.root / "queue_recovered"
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 3, logs)
        self.assertTrue(report["passed"])
        self.assertEqual(report["responses"], 2)
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(client.requests[0], client.requests[1])
        self.assertEqual(report["usage"]["input_tokens"], 200)
        self.assertEqual(read_json(logs / "response_001.json")["queue_errors"], [queued])
        self.assertEqual((self.runtime.work / "a").read_text(), "done")
        client = FakeClient([queued, queued, queued])
        self.runtime.check_callback = lambda: {"passed": True}
        logs = self.root / "queue_exhausted"
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 3, logs)
        self.assertFalse(report["passed"])
        self.assertEqual(report["reason"], "api_error")
        self.assertEqual(report["responses"], 0)
        self.assertEqual(len(client.requests), 3)
        self.assertTrue(all(request == client.requests[0] for request in client.requests))
        self.assertIn("900-second timeout", read_json(logs / "api_error_001.json")["error"])

    def test_truncated_calls_do_not_mutate(self):
        client = FakeClient([response([call("write_file", {"path": "/work/a", "content": "no"})], finish="length")])
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 2, self.root / "api")
        self.assertEqual(report["reason"], "truncated_response")
        self.assertFalse((self.runtime.work / "a").exists())

    def test_checkpoint_and_bounded_disk_resume(self):
        calls = [response([call("list_files", {"path": "/work"})]) for _ in range(24)]
        calls += [response([call("write_file", {"path": "/work/WORKLOG.md", "content": "Done twenty-four responses; next write a."})]),
                  response([call("write_file", {"path": "/work/a", "content": "done"})]), response(finish="stop")]
        client = FakeClient(calls)
        self.runtime.check_callback = lambda: {"passed": (self.runtime.work / "a").exists()}
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 40, self.root / "api")
        self.assertEqual(report["context_resets"], 1)
        self.assertIn("0/40 responses already used; 40 remain", client.requests[0]["messages"][1]["content"])
        self.assertEqual(len(client.requests[25]["messages"]), 2)
        self.assertIn("Done twenty-four", client.requests[25]["messages"][1]["content"])
        # Resuming consumes only the unused part of this job's original budget.
        saved = json.loads((self.root / "api/progress.json").read_text())
        saved["responses"] = 40
        (self.root / "api/progress.json").write_text(json.dumps(saved))
        second = FakeClient([])
        resumed = run_agent(LLM(client=second), self.runtime, "role", "task", 40, self.root / "api")
        self.assertEqual(resumed["responses"], 40)
        self.assertEqual(len(second.requests), 0)

    def test_controller_feedback_survives_reset_and_api_resume(self):
        first = response([call("write_file", {"path": "/work/missing.json", "content": "{"})])
        calls = [first] + [response([call("list_files", {"path": "/work"})]) for _ in range(23)]
        calls += [response([call("write_file", {"path": "/work/WORKLOG.md", "content": "All artifacts complete; just finish."})]),
                  {"choices": [], "error": {"message": "service unavailable"}}]
        def gate():
            passed = (self.runtime.work / "missing.json").exists()
            return {"passed": passed, "errors": [] if passed else ["Missing artifact: /work/missing.json"]}
        self.runtime.check_callback = gate
        logs = self.root / "feedback_api"
        client = FakeClient(calls)
        stopped = run_agent(LLM(client=client), self.runtime, "role", "task", 40, logs)
        self.assertEqual(stopped["reason"], "api_error")
        self.assertEqual(stopped["responses"], 25)
        fresh_context = client.requests[25]["messages"][1]["content"]
        self.assertIn("takes precedence over checkpoint claims", fresh_context)
        self.assertIn("Missing artifact: /work/missing.json", fresh_context)
        self.assertIn("Expecting property name", fresh_context)
        self.assertFalse(read_json(logs / "gate_025.json")["passed"])
        second = FakeClient([response([call("write_file", {"path": "/work/missing.json", "content": "{}"})]),
                             response([call("check", {})])])
        resumed = run_agent(LLM(client=second), self.runtime, "role", "task", 40, logs)
        self.assertTrue(resumed["passed"])
        self.assertEqual(resumed["responses"], 27)
        self.assertIn("Missing artifact: /work/missing.json", second.requests[0]["messages"][1]["content"])
        self.assertNotIn("write_error", read_json(logs / "progress.json")["controller_feedback"])

    def test_spec_schema_error_names_the_unexpected_field(self):
        spec = next(value for path in sorted((ROOT / "assets/mqtt_reference/bundle/files").glob("*.json"))
                    if (value := read_json(path))["SOURCE"].get("DATA"))
        spec["SOURCE"]["DATA"][0]["UNEXPECTED_FIELD"] = "int"
        result = self.runtime.dispatch("write_file", {"path": "/work/files/fixture.json", "content": json.dumps(spec)})
        self.assertIn("SOURCE/DATA/0", result["error"])
        self.assertIn("UNEXPECTED_FIELD", result["error"])
        self.assertFalse((self.runtime.work / "files/fixture.json").exists())

    def test_paths_symlinks_readonly_and_unique_edits(self):
        self.assertIn("error", self.runtime.dispatch("write_file", {"path": "/input/a", "content": "no"}))
        self.assertIn("error", self.runtime.dispatch("read_file", {"path": "/work/../input/a"}))
        (self.runtime.work / "link").symlink_to(self.inputs)
        self.assertIn("error", self.runtime.dispatch("write_file", {"path": "/work/link/a", "content": "no"}))
        self.runtime.dispatch("write_file", {"path": "/work/a", "content": "a a"})
        self.assertIn("error", self.runtime.dispatch("edit_file", {"path": "/work/a", "old": "a", "new": "b"}))
        self.assertIn("written", self.runtime.dispatch("edit_file", {"path": "/work/a", "old": "a a", "new": "b"}))
        self.assertIn("error", self.runtime.dispatch("read_file", {"path": "/work/a", "line_count": 999}))

    def test_large_previous_input_does_not_checkpoint_new_context_twice(self):
        first = response([call("list_files", {"path": "/work"})])
        first["usage"]["prompt_tokens"] = 65000
        checkpoint = response([call("write_file", {"path": "/work/WORKLOG.md", "content": "Next write a."})])
        checkpoint["usage"]["prompt_tokens"] = 65000
        third = response([call("write_file", {"path": "/work/a", "content": "done"})])
        client = FakeClient([first, checkpoint, third, response(finish="stop")])
        self.runtime.check_callback = lambda: {"passed": (self.runtime.work / "a").exists()}
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 14, self.root / "api")
        self.assertTrue(report["passed"])
        self.assertEqual(report["context_resets"], 1)
        self.assertGreater(len(client.requests[2]["tools"]), 1)

    def test_controller_gate_preserves_repair_window_after_checkpoint_without_finishing_job(self):
        for initially_passed in (False, True):
            with self.subTest(initially_passed=initially_passed):
                intake = response([call("list_files", {"path": "/work"})])
                intake["usage"]["prompt_tokens"] = 65000
                client = FakeClient([
                    response([call("write_file", {"path": "/work/callback.json", "content":
                        '{"signature":"void (*)(int)"}'})]),
                    response([call("list_files", {"path": "/work"})]), intake,
                    response([call("write_file", {"path": "/work/WORKLOG.md", "content": f"Continue the saved design {initially_passed}."})]),
                    response([call("edit_file", {"path": "/work/callback.json", "old": "void (*)(int)",
                        "new": "void (*accept_fn)(int)"})]), response(finish="stop")])
                def gate():
                    passed = initially_passed or "accept_fn" in (self.runtime.work / "callback.json").read_text()
                    return {"passed": passed, "errors": [] if passed else ["CALLBACK_SIGNATURE must name its typedef: accept_fn"]}
                self.runtime.check_callback = gate
                logs = self.root / f"controller_gate_{initially_passed}"
                report = run_agent(LLM(client=client), self.runtime, "role", "DESIGN job", 16, logs)
                self.assertTrue(report["passed"])
                self.assertEqual(report["responses"], 6)
                self.assertEqual(report["context_resets"], 1)
                self.assertEqual(read_json(logs / "gate_004.json")["passed"], initially_passed)
                feedback = client.requests[4]["messages"][-1]["content"]
                self.assertIn("Only 12 model responses remain", feedback)
                if not initially_passed:
                    self.assertIn("CALLBACK_SIGNATURE must name its typedef: accept_fn", feedback)
                self.assertEqual(read_json(self.runtime.work / "callback.json")["signature"], "void (*accept_fn)(int)")
                self.assertEqual(len(client.requests[4]["tools"]), len(self.runtime.tool_definitions()))

    def test_final_budget_keeps_artifact_and_check_tools_available(self):
        first = response([call("list_files", {"path": "/work"})])
        first["usage"]["prompt_tokens"] = 65000
        client = FakeClient([first,
                             response([call("write_file", {"path": "/work/a", "content": "done"})]),
                             response([call("check", {})])])
        self.runtime.check_callback = lambda: {"passed": (self.runtime.work / "a").exists()}
        report = run_agent(LLM(client=client), self.runtime, "role", "task", 3, self.root / "api")
        self.assertTrue(report["passed"])
        self.assertEqual(report["context_resets"], 0)
        self.assertTrue(all(len(r["tools"]) > 1 for r in client.requests))
        self.assertIn("0/3 responses already used; 3 remain", client.requests[0]["messages"][1]["content"])

    def test_read_pagination_does_not_skip_truncated_lines(self):
        (self.runtime.work / "large").write_text("\n".join(str(i) + "x" * 400 for i in range(50)))
        first = self.runtime.dispatch("read_file", {"path": "/work/large"})
        number = first["next_line"]
        self.assertLess(number, 50)
        self.assertEqual(len(first["text"].splitlines()), number - 1)
        second = self.runtime.dispatch("read_file", {"path": "/work/large", "start_line": number})
        self.assertTrue(second["text"].startswith(f"L{number:04d}: {number-1}"))

    def test_invalid_json_write_and_edit_preserve_valid_artifact(self):
        self.assertIn("error", self.runtime.dispatch("write_file", {"path": "/work/spec.json", "content": '{"bad":'}))
        self.assertFalse((self.runtime.work / "spec.json").exists())
        self.runtime.dispatch("write_file", {"path": "/work/spec.json", "content": '{"value": 1}'})
        self.assertIn("error", self.runtime.dispatch("edit_file", {"path": "/work/spec.json", "old": "1", "new": "unquoted"}))
        self.assertEqual(json.loads((self.runtime.work / "spec.json").read_text()), {"value": 1})

    def test_spec_schema_errors_are_immediate_and_preserve_artifacts(self):
        spec = next(value for path in sorted((ROOT / "assets/mqtt_reference/bundle/functions").rglob("*.json"))
                    if "LOGIC" in (value := read_json(path)) and value.get("TEST_VECTORS"))
        path = "/work/functions/algorithm.json"
        original = json.dumps(spec)
        action = spec["LOGIC"]["ACTION"]
        spec["LOGIC"]["ACTION"] = [action]
        result = self.runtime.dispatch("write_file", {"path": path, "content": json.dumps(spec)})
        self.assertIn("LOGIC/ACTION", result["error"])
        self.assertFalse((self.runtime.work / "functions/algorithm.json").exists())
        self.assertIn("written", self.runtime.dispatch("write_file", {"path": path, "content": original}))
        result = self.runtime.dispatch("edit_file", {"path": path, "old": '"ACTION": ' + json.dumps(action),
                                                      "new": '"ACTION": ' + json.dumps([action])})
        self.assertIn("LOGIC/ACTION", result["error"])
        self.assertEqual((self.runtime.work / "functions/algorithm.json").read_text(), original)
        spec["LOGIC"]["ACTION"] = action
        spec["TEST_VECTORS"][0]["INPUT"] = [spec["TEST_VECTORS"][0]["INPUT"]]
        result = self.runtime.dispatch("write_file", {"path": path, "content": json.dumps(spec)})
        self.assertIn("TEST_VECTORS/0/INPUT", result["error"])
        self.assertEqual((self.runtime.work / "functions/algorithm.json").read_text(), original)

    def test_project_json_data_is_not_treated_as_a_spec(self):
        for coder, content in ((False, '[1, 2]'), (False, '{"value": 1}'),
                               (True, '{"KIND": "FUNCTION_SPEC", "payload": "test data"}')):
            with self.subTest(coder=coder, content=content):
                self.runtime.coder = coder
                self.assertIn("written", self.runtime.dispatch("write_file", {"path": "/work/data.json", "content": content}))
                self.assertEqual((self.runtime.work / "data.json").read_text(), content)

    def test_command_view_failure_and_timeout(self):
        header = self.runtime.work / "include"
        header.mkdir()
        (header / "api.h").write_text("original")
        self.runtime.readonly = [header]
        result = self.runtime.command('test -z "$DS_API" && test ! -e /home/ljf/SpecForge && gcc --version', timeout=10)
        self.assertEqual(result["exit_code"], 0, result["output"])
        self.assertNotEqual(self.runtime.command("echo changed > include/api.h")["exit_code"], 0)
        self.assertEqual((header / "api.h").read_text(), "original")
        timed = self.runtime.command("sleep 20 & wait", timeout=1)
        self.assertTrue(timed["timed_out"])
        self.assertLess(timed["elapsed_seconds"], 4)


    def test_json_field_selection_preserves_exact_values_and_escapes(self):
        value = {"a/b": {"~x": [0, False, None, "λ"]}, "unused": "large unrelated data"}
        path = self.runtime.work / "fields.json"
        path.write_text(json.dumps(value))
        refs = ["/a~1b/~0x/0", "/a~1b/~0x/1", "/a~1b/~0x/2", "/a~1b/~0x/3"]
        result = self.runtime.dispatch("read_file", {"path": "/work/fields.json", "json_pointers": refs})
        selected = json.loads("\n".join(line.split(": ", 1)[1] for line in result["text"].splitlines()))
        self.assertEqual(selected, dict(zip(refs, [0, False, None, "λ"])))
        self.assertEqual(result["json_pointers"], refs)
        self.assertNotIn("unused", result["text"])
        self.assertEqual(json.loads(path.read_text()), value)
        bad = self.runtime.dispatch("read_file", {"path": "/work/fields.json", "json_pointers": [refs[0], "/missing"]})
        self.assertIn("Invalid JSON selection", bad["error"])
        self.assertIn("available root keys: a/b, unused", bad["error"])
        self.assertNotIn("text", bad)
        self.assertEqual(json.loads(path.read_text()), value)

    def test_json_selection_pagination_retains_the_next_line(self):
        value = {"rows": [str(i) + "x" * 400 for i in range(90)]}
        (self.runtime.work / "rows.json").write_text(json.dumps(value))
        args = {"path": "/work/rows.json", "json_pointers": ["/rows"]}
        first = self.runtime.dispatch("read_file", args)
        number = first["next_line"]
        self.assertLess(number, first["total_lines"])
        self.assertEqual(len(first["text"].splitlines()), number - 1)
        second = self.runtime.dispatch("read_file", {**args, "start_line": number})
        self.assertTrue(second["text"].startswith(f"L{number:04d}:"))
        self.assertEqual(first["json_pointers"], second["json_pointers"])

    def test_scripted_spec_schema_feedback_checks_changed_planner_artifacts(self):
        self.runtime.inputs["/schemas"] = ROOT / "schemas"
        spec = next(value for path in sorted((ROOT / "assets/mqtt_reference/bundle/files").glob("*.json"))
                    if (value := read_json(path))["SOURCE"].get("DATA"))
        self.assertIn("written", self.runtime.dispatch("write_file", {"path": "/work/files/fixture.json", "content": json.dumps(spec)}))
        bad = self.runtime.command("python3 - <<'PY'\nimport json\np='files/fixture.json'\nd=json.load(open(p))\nd['SOURCE']['DATA'][0]['UNEXPECTED_FIELD']='int'\njson.dump(d,open(p,'w'))\nPY")
        self.assertEqual(bad["exit_code"], 0)
        self.assertIn("SOURCE/DATA/0", bad["spec_errors"][0])
        self.assertIn("UNEXPECTED_FIELD", bad["spec_errors"][0])
        self.assertTrue((self.runtime.work / "files/fixture.json").exists())
        self.assertNotIn("spec_errors", self.runtime.command("true"))
        fixed = self.runtime.command("python3 - <<'PY'\nimport json\np='files/fixture.json'\nd=json.load(open(p))\nd['SOURCE']['DATA'][0].pop('UNEXPECTED_FIELD')\njson.dump(d,open(p,'w'))\nPY")
        self.assertNotIn("spec_errors", fixed)
        self.runtime.coder = True
        ordinary = self.runtime.command("printf '{' > files/fixture.json")
        self.assertEqual(ordinary["exit_code"], 0)
        self.assertNotIn("spec_errors", ordinary)

    def test_large_command_keeps_head_and_failure_tail_with_full_disk_log(self):
        result = self.runtime.command("python3 - <<'PY'\nprint('START')\nprint('x' * 12000)\nprint('FAIL: actionable assertion at the end')\nPY")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["output"]), 8000)
        self.assertIn("START", result["output"])
        self.assertIn("FAIL: actionable assertion at the end", result["output"])
        full = self.runtime.resolve(result["log"]).read_text()
        self.assertIn("x" * 12000, full)


if __name__ == "__main__":
    unittest.main()

