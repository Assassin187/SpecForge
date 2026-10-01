import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from specforge.agent import run_agent
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


if __name__ == "__main__":
    unittest.main()

