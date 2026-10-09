"""No real model calls: view checks, mocked orchestration and local gates."""

import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE))
sys.path.insert(0, str(SUITE.parents[1]))

import run as rq2
from report import _artifacts, summarize_experiment
from spec_views import CONDITIONS, make_view
from specforge.documents import digest, hashes, read_json, save_json
from specforge.pipeline import verify_project
from specforge.tools import ToolRuntime


def usage():
    return {"input_tokens": 100, "output_tokens": 25, "cache_hit_tokens": 80,
            "cache_miss_tokens": 20, "reasoning_tokens": 5, "requests": 1, "elapsed_seconds": 0.5}


def successful_evaluation(test_ids=rq2.TEST_IDS):
    return {"passed": True, "errors": [], "builds": {
        p: {"exit_code": 0, "timed_out": False} for p in ("normal", "sanitize")},
        "phases": {p: {"passed": True, "required_count": len(test_ids),
                      "scenarios": [{"id": name, "status": "passed"} for name in test_ids]}
                   for p in ("normal", "sanitize")}}


class FixtureLLM:
    """Return locally constructed tool responses through the real agent loop."""
    config = rq2.ModelConfig()

    def __init__(self, batches):
        self.batches = iter(batches)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append({"messages": messages, "tools": tools})
        return {"message": {"role": "assistant", "content": None, "tool_calls": [
                    {"id": f"fixture_{i}", "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(next(self.batches))]},
                "usage": usage(), "finish_reason": "tool_calls", "response": {"fixture": True}}


def write_project(project):
    project.mkdir(parents=True, exist_ok=True)
    (project / "main.c").write_text(
        "#include <stdio.h>\nint main(int argc,char **argv){volatile int n=argc;puts(argv[0]);return n<1;}\n")
    (project / "other.c").write_text("typedef int fixture_translation_unit;\n")
    (project / "README.md").write_text("Compiler fixture, not a generated MQTT implementation.\n")
    (project / "dev.py").write_text("import subprocess\nassert subprocess.run(['./mqtt_broker']).returncode == 0\n")
    (project / "Makefile").write_text(
        "CC=gcc\nCFLAGS=-std=c99 -Wall -Wextra -Wpedantic -Werror\n"
        "all: mqtt_broker\nmqtt_broker: main.c other.c\n"
        "\t$(CC) $(CFLAGS) main.c other.c $(LDFLAGS) -o $@\n"
        "test:\n\tpython3 dev.py\n"
        "sanitize: CFLAGS += -fsanitize=address,undefined -g -fno-pie\n"
        "sanitize: LDFLAGS += -fsanitize=address,undefined -no-pie\n"
        "sanitize: clean all\nclean:\n\trm -f mqtt_broker\n.PHONY: all test sanitize clean\n")
    save_json(project / "delivery.json", {"schema_version": 1,
        "files": ["main.c", "other.c", "README.md", "Makefile", "dev.py"],
        "tests": [{"id": "fixture", "path": "dev.py", "requirement_ids": [f"R{i:02d}" for i in range(1, 13)]}]})


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.behavior = {"INPUT": "literal input", "ACTION": "literal action with wire value 0x20",
                         "OUTPUT": "literal output", "PRECONDITION": "literal precondition",
                         "POSTCONDITION": "literal postcondition", "INVARIANTS_USED": ["literal invariant"]}
        vector = [{"NAME": "vector", "INPUT": {}, "EXPECT": {"byte": "0x20"}}]
        docs = {
            "scope.json": {"protocol": "MQTT", "version": "3.1.1", "role": "broker", "language": "C99",
                           "runtime": "Linux", "runtime_contract": rq2.CONTRACT,
                           "requirements": [{"id": "R01", "description": "literal requirement", "source": {"line_start": 3}}],
                           "required_capabilities": ["literal capability"], "excluded_features": ["TLS"],
                           "engineering_defaults": [{"decision": "literal decision", "source": "original document"}]},
            "module_spec.json": {"KIND": "PROTOCOL_MODULE_SPEC", "PROTOCOL": {"NAME": "MQTT"},
                                 "MODULES": [{"NAME": "codec", "ROLE": "literal module", "FILES": ["src/main.c"]}],
                                 "GENERATION_ORDER": ["codec"], "TEST_VECTORS": vector,
                                 "WIRE_MAPPING": [{"name": "module mapping"}], "DOC_REF": ["secret source location"]},
            "files/main.json": {"KIND": "FILE_SPEC", "FILE": {"TRACE_ID": "main", "ROLE": "literal file"},
                                "SOURCE": {"PATH": "src/main.c", "DEPENDENCY": ["src/api.h"],
                                           "INTERFACE": [{"TRACE_ID": "fn", "SIGNATURE": "int f(void)"}]},
                                "HEADER": {"PATH": "src/api.h", "INTERFACE": []},
                                "TEST_VECTORS": vector, "CALL_CONTRACTS": [{"NAME": "file call"}]},
            "functions/f.json": {"KIND": "FUNCTION_SPEC", "TRACE_ID": "fn", "ROLE": "literal role",
                                  "SIGNATURE": {"RAW": "int f(void)", "NAME": "f", "RETURN": "int", "PARAMS": []},
                                  "RELY": {"STRUCT": [], "FUNC": [], "VAR": []}, "LOGIC": self.behavior,
                                  "TEST_VECTORS": vector, "WIRE_MAPPING": [{"FIELD": "literal field"}],
                                  "CALL_CONTRACTS": [{"NAME": "g", "OWNERSHIP": "literal ownership"}]},
            "traceability.json": {"requirements": [{"requirement_id": "R01",
                "test_ids": ["functions/f.json#/TEST_VECTORS/0"],
                "spec_refs": ["functions/f.json#/LOGIC", "functions/f.json#/WIRE_MAPPING/0",
                              "functions/f.json#/CALL_CONTRACTS/0"]}]}}
        for name, content in docs.items():
            save_json(self.source / name, content)
        abi = self.source / "abi/src/api.h"
        abi.parent.mkdir(parents=True)
        abi.write_text("int f(void);\n")
        (self.source / "SUMMARY.md").write_text("Published navigation\n")
        manifest = {"module": "module_spec.json", "files": [{"path": "src/main.c", "spec": "files/main.json"}],
                    "headers": [{"path": "src/api.h", "artifact": "abi/src/api.h"}],
                    "functions": [{"spec": "functions/f.json", "name": "f"}], "hashes": hashes(self.source),
                    "runtime_contract": rq2.CONTRACT, "origin": "automatic"}
        save_json(self.source / "bundle.json", manifest)
        (self.source / "audit.py").write_text("unpublished reviewer source\n")
        (self.source / "old_implementation.c").write_text("unpublished old implementation\n")
        self.before = hashes(self.source)

    def tearDown(self):
        self.assertEqual(hashes(self.source), self.before)
        self.temp.cleanup()

    def test_generic_preserves_literal_behavior_abi_and_has_no_enhanced_fields(self):
        view = self.root / "generic"
        first = make_view(self.source, view, "generic")
        second = make_view(self.source, self.root / "generic2", "generic")
        self.assertEqual(first, second)
        body = (view / "functions/f.spec").read_text()
        for value in self.behavior.values():
            for text in value if isinstance(value, list) else [value]:
                self.assertIn(text, body)
        for section in ("[RELY]", "[GUARANTEE]", "[SPECIFICATION]"):
            self.assertIn(section, body)
        for key in ("CALL_CONTRACTS", "WIRE_MAPPING", "TEST_VECTORS", "TRACE_ID"):
            self.assertNotIn(key, body)
        self.assertEqual(digest(view / "abi/src/api.h"), digest(self.source / "abi/src/api.h"))
        self.assertFalse(list(view.rglob("*.json")))
        self.assertFalse((view / "traceability.json").exists())
        self.assertFalse((view / "audit.py").exists())
        self.assertNotIn("secret source location", (view / "project.spec").read_text())

    def test_vector_ablation_clears_references_at_all_levels_and_keeps_contracts(self):
        view = self.root / "no_vectors"
        changes = make_view(self.source, view, "full_no_vectors")
        for path in ("module_spec.json", "files/main.json", "functions/f.json"):
            self.assertNotIn("TEST_VECTORS", read_json(view / path))
        trace = read_json(view / "traceability.json")["requirements"][0]
        self.assertEqual(trace["test_ids"], [])
        function = read_json(view / "functions/f.json")
        self.assertEqual(function["LOGIC"], self.behavior)
        self.assertIn("CALL_CONTRACTS", function)
        self.assertTrue(any(c["operation"] == "remove_dangling_reference" for c in changes["changes"]))
        for name, expected in read_json(view / "bundle.json")["hashes"].items():
            self.assertEqual(digest(view / name), expected)

    def test_contract_ablation_is_field_only(self):
        view = self.root / "no_contracts"
        make_view(self.source, view, "full_no_contracts")
        function = read_json(view / "functions/f.json")
        self.assertEqual(function["LOGIC"], self.behavior)
        self.assertIn("TEST_VECTORS", function)
        self.assertNotIn("WIRE_MAPPING", function)
        self.assertNotIn("CALL_CONTRACTS", function)
        self.assertEqual(read_json(view / "traceability.json")["requirements"][0]["spec_refs"],
                         ["functions/f.json#/LOGIC"])

    def test_full_is_exact_allowlist_copy(self):
        view = self.root / "full"
        make_view(self.source, view, "full")
        expected = set(read_json(self.source / "bundle.json")["hashes"]) | {"bundle.json"}
        self.assertEqual(set(hashes(view)), expected)
        for name in expected:
            self.assertEqual(digest(view / name), digest(self.source / name))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        write_project(self.project)
        self.view = {"planned_sources": [], "headers": []}
        self.ids = [f"R{i:02d}" for i in range(1, 13)]

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_verifier_accepts_neutral_delivery_and_runs_real_sanitizer(self):
        consistency = rq2.check_delivery(self.project, self.ids, self.view, self.root)
        self.assertTrue(consistency["passed"], consistency["errors"])
        result = verify_project(self.project, self.root / "reports", self.root / "logs",
                                consistency=consistency, runtime_contract=rq2.CONTRACT)
        self.assertTrue(result["passed"], result["errors"])
        self.assertTrue(result["phases"]["sanitize"]["binary_unchanged"])
        self.assertNotIn("mqtt_check", str(result))

    def test_coverage_inventory_and_abi_errors_are_not_bypassed(self):
        data = read_json(self.project / "delivery.json")
        data["tests"][0]["requirement_ids"].remove("R12")
        save_json(self.project / "delivery.json", data)
        self.assertFalse(rq2.check_delivery(self.project, self.ids, self.view, self.root)["passed"])
        data["tests"][0]["requirement_ids"].append("R12")
        data["files"].append("../outside.c")
        save_json(self.project / "delivery.json", data)
        self.assertFalse(rq2.check_delivery(self.project, self.ids, self.view, self.root)["passed"])
        (self.root / "abi").mkdir()
        (self.root / "abi/api.h").write_text("int expected;\n")
        (self.project / "api.h").write_text("int changed;\n")
        data["files"].remove("../outside.c")
        data["files"].append("api.h")
        save_json(self.project / "delivery.json", data)
        self.view["headers"] = [{"path": "api.h", "artifact": "abi/api.h"}]
        self.assertFalse(rq2.check_delivery(self.project, self.ids, self.view, self.root)["passed"])

    def test_sanitizer_instrumentation_is_required_even_with_passing_self_test(self):
        makefile = self.project / "Makefile"
        makefile.write_text(makefile.read_text().replace("-fsanitize=address,undefined", ""))
        consistency = rq2.check_delivery(self.project, self.ids, self.view, self.root)
        result = verify_project(self.project, self.root / "reports", self.root / "logs",
                                consistency=consistency, runtime_contract=rq2.CONTRACT)
        self.assertFalse(result["passed"])
        self.assertIn("Sanitizer binary lacks ASan/UBSan instrumentation", result["errors"])


class EvaluationTests(unittest.TestCase):
    def test_missing_partial_duplicate_and_skipped_scenarios_cannot_pass(self):
        for kind in ("missing", "partial", "duplicate", "skipped", "wrong_id", "missing_builds"):
            raw = successful_evaluation()
            if kind == "missing":
                raw["phases"].pop("sanitize")
            elif kind == "partial":
                raw["phases"]["sanitize"]["scenarios"].pop()
            elif kind == "duplicate":
                raw["phases"]["normal"]["scenarios"][-1]["id"] = rq2.TEST_IDS[0]
            elif kind == "wrong_id":
                raw["phases"]["normal"]["scenarios"][-1]["id"] = "not_mqtt"
            elif kind == "missing_builds":
                raw.pop("builds")
            else:
                raw["phases"]["normal"]["scenarios"][0]["status"] = "environment_blocked"
            with self.subTest(kind=kind):
                report = rq2.normalize_evaluation(raw, list(rq2.TEST_IDS))
                self.assertFalse(report["passed"])
                for phase in ("normal", "sanitize"):
                    self.assertEqual(len(report["phases"][phase]["scenarios"]), 16)
        self.assertTrue(rq2.normalize_evaluation(successful_evaluation(), list(rq2.TEST_IDS))["passed"])


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.template = Path(cls.scratch.name) / "prepared"
        with patch.object(rq2, "preflight", return_value={"openai": "1.97.0", "jsonschema": "4.24.0"}):
            rq2.prepare_experiment(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name) / "experiment"
        shutil.copytree(self.template, self.run)

    def tearDown(self):
        self.temp.cleanup()

    def mock_generation(self, root, state, trial, *, save_state=None):
        project = root / "trials" / trial["id"] / "project"
        project.mkdir(parents=True)
        (project / "main.c").write_text("fixture only\n")
        initial = project.parent / "initial_project"
        shutil.copytree(project, initial)
        trial.update(status="completed", generation_passed=True, delivery_hashes=hashes(project),
                     initial_snapshot={"path": str(initial.relative_to(root)), "hashes": hashes(initial),
                                       "responses": 1, "elapsed_seconds": 1.25},
                     elapsed_seconds=1.25, stop_reason="development_passed",
                     jobs=[{"name": "01_code", "responses": 1, "passed": True, "reason": "gate_passed",
                            "usage": usage(), "wall_seconds": 1.25}])

    def test_prepare_matrix_task_difference_and_source_provenance(self):
        state = rq2.load_frozen(self.run)
        self.assertEqual(len(state["trials"]), 15)
        for repeat in range(1, 4):
            self.assertEqual({t["condition"] for t in state["trials"] if t["repeat"] == repeat}, set(CONDITIONS))
        original = (rq2.SOURCE_RUN / "inputs/TASK.md").read_text()
        direct = (self.run / "views/direct/inputs/TASK.md").read_text()
        self.assertEqual(direct, original.replace(rq2.PLANNING_SENTENCE, "", 1))
        self.assertIn("Private helpers may be chosen during coding.", direct)
        self.assertEqual(state["limits"], {"code": 180, "repair": 40, "implementation_repairs": 3})
        history = read_json(self.run / "control/historical_cost.json")
        self.assertEqual(history["source_formation"]["total_tokens"], 13654560)
        self.assertEqual(history["excluded_jobs"], ["08_repair", "09_repair"])

    def test_spec_views_cannot_access_raw_history_or_evaluator_and_headers_are_readonly(self):
        state = rq2.load_frozen(self.run)
        for condition in CONDITIONS[1:]:
            work = Path(self.temp.name) / condition
            work.mkdir()
            header = state["views"][condition]["headers"][0]
            local = work / header["path"]
            local.parent.mkdir(parents=True)
            shutil.copyfile(self.run / "views" / condition / header["artifact"], local)
            runtime = ToolRuntime(work, {"/specs": self.run / "views" / condition},
                                  Path(self.temp.name) / (condition + "_logs"), lambda: {"passed": False},
                                  readonly=[local], coder=True)
            probe = runtime.command("test ! -e /inputs && test ! -e /documents && test ! -e /harness"
                                    " && test ! -e " + str(rq2.SOURCE_RUN) +
                                    " && test ! -e " + str(self.run / "control"), timeout=10)
            self.assertEqual(probe["exit_code"], 0, probe["output"])
            write = runtime.dispatch("write_file", {"path": "/work/" + header["path"], "content": "changed"})
            self.assertIn("error", write)
            command = runtime.command("echo changed > /work/" + header["path"], timeout=10)
            self.assertNotEqual(command["exit_code"], 0)
        direct_runtime = ToolRuntime(Path(self.temp.name) / "direct_work",
            {"/inputs": self.run / "views/direct/inputs", "/documents": self.run / "views/direct/documents"},
            Path(self.temp.name) / "direct_logs", lambda: {"passed": False})
        self.assertEqual(direct_runtime.command("test ! -e /specs && test -f /inputs/TASK.md", timeout=10)["exit_code"], 0)

    def test_changed_input_is_rejected(self):
        path = self.run / "views/full_no_vectors/functions"
        next(path.glob("*.json")).write_text("{}")
        with self.assertRaisesRegex(ValueError, "Frozen input view changed"):
            rq2.load_frozen(self.run)

    def test_native_agent_with_fixture_model_reaches_neutral_gate_and_records_build(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "full")
        fixture = Path(self.temp.name) / "fixture"
        write_project(fixture)
        sources = state["views"]["full"]["planned_sources"]
        main = (fixture / "main.c").read_text()
        declaration = (fixture / "other.c").read_text()
        makefile = (fixture / "Makefile").read_text().replace("main.c other.c", " ".join(sources))
        delivery = read_json(fixture / "delivery.json")
        delivery["files"] = [*sources, *(h["path"] for h in state["views"]["full"]["headers"]),
                             "Makefile", "README.md", "dev.py"]
        files = {name: main if i == 0 else declaration for i, name in enumerate(sources)}
        files.update({"Makefile": makefile, "README.md": (fixture / "README.md").read_text(),
                      "dev.py": (fixture / "dev.py").read_text(),
                      "delivery.json": json.dumps(delivery)})
        model = FixtureLLM([
            [("write_file", {"path": "/work/" + name, "content": content}) for name, content in files.items()],
            [("check", {})]])
        with patch.object(rq2, "LLM", return_value=model):
            rq2._run_trial(self.run, state, trial)
        self.assertTrue(trial["generation_passed"])
        self.assertEqual(trial["implementation_repairs"], 0)
        self.assertEqual(trial["jobs"][0]["responses"], 2)
        self.assertEqual(trial["first_normal_build"]["responses"], 2)
        self.assertEqual(trial["initial_snapshot"]["responses"], 2)
        self.assertEqual(hashes(self.run / trial["initial_snapshot"]["path"]), trial["initial_snapshot"]["hashes"])
        self.assertEqual(trial["repair_events"], [])
        self.assertNotIn("run_command", {d["function"]["name"] for d in model.requests[0]["tools"]})
        self.assertEqual(len(model.requests), 2)
        prefix = model.requests[0]["messages"][0]["content"]
        self.assertIn("neutral delivery.json schema", prefix)
        self.assertNotIn("canonical Spec test references", prefix)
        summarize_experiment(self.run, state)
        with (self.run / "summary/evidence.jsonl").open() as handle:
            entries = [json.loads(line) for line in handle]
        self.assertTrue(any(e.get("tool") == "write_file" for e in entries))

    def test_spec_gap_stops_native_agent_without_upstream_or_implementation_repair(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "generic")
        before = hashes(self.run / "views/generic")
        model = FixtureLLM([
            [("write_file", {"path": "/work/conflict.md", "content": "fixture conflict evidence"})],
            [("report_spec_gap", {"kind": "behavior", "spec_refs": ["scope.md#Requirements"],
                                  "problem": "fixture conflict, not a real source diagnosis",
                                  "evidence": "/work/conflict.md"})]])
        with patch.object(rq2, "LLM", return_value=model):
            rq2._run_trial(self.run, state, trial)
        self.assertEqual(trial["stop_reason"], "spec_gap")
        self.assertFalse(trial["generation_passed"])
        self.assertEqual(len(trial["jobs"]), 1)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(hashes(self.run / "views/generic"), before)

    def test_all_conditions_share_initial_and_three_repair_limits(self):
        state = rq2.load_frozen(self.run)
        for condition in CONDITIONS:
            trial = next(t for t in state["trials"] if t["condition"] == condition)
            limits = []
            def agent(llm, runtime, prefix, task, response_limit, log_dir, progress):
                limits.append(response_limit)
                return {"passed": False, "reason": "response_limit", "responses": response_limit, "usage": usage()}
            def verify(project, reports, logs, **kwargs):
                return {"passed": False, "project_hashes": rq2.project_hashes(project),
                        "report_path": str(reports / "fixture/report.json"), "errors": ["fixture failure"],
                        "builds": {}, "phases": {}}
            with patch.object(rq2, "LLM"), patch.object(rq2, "run_agent", side_effect=agent), \
                    patch.object(rq2, "verify_project", side_effect=verify), \
                    patch.object(rq2, "check_delivery", return_value={"passed": True, "errors": [], "test_count": 1}):
                rq2._run_trial(self.run, state, trial)
            self.assertEqual(limits, [180, 40, 40, 40])
            self.assertEqual(trial["stop_reason"], "implementation_repair_limit")
            self.assertEqual(trial["implementation_repairs"], 3)

    def test_api_exception_retains_observed_usage_but_total_cost_is_unknown(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "direct")
        model = FixtureLLM([[("write_file", {"path": "/work/WORKLOG.md", "content": "fixture checkpoint"})]])
        with patch.object(rq2, "LLM", return_value=model):
            rq2._run_trial(self.run, state, trial)
        self.assertEqual(trial["stop_reason"], "api_error")
        self.assertEqual(len(trial["jobs"]), 1)
        self.assertEqual(trial["jobs"][0]["usage"]["input_tokens"], 100)
        self.assertIsNone(rq2.aggregate_usage(trial["jobs"])["total_tokens"])
        summarize_experiment(self.run, state)
        row = next(r for r in read_json(self.run / "summary/results.json")["trials"]
                   if r["trial"] == trial["id"])
        self.assertEqual(row["api_errors"], 1)
        self.assertIsNone(row["total_tokens"])
        self.assertIsNone(row["model_wait_seconds"])
        with (self.run / "summary/stages.csv").open() as handle:
            stage = next(r for r in csv.DictReader(handle) if r["trial"] == trial["id"])
        self.assertEqual(stage["total_tokens"], "")

    def test_truncated_response_does_not_count_unexecuted_spec_reads(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "full")
        model = FixtureLLM([[("read_file", {"path": "/specs/scope.json"})]])
        complete = model.complete
        def truncated(messages, tools):
            response = complete(messages, tools)
            response["finish_reason"] = "length"
            return response
        with patch.object(model, "complete", side_effect=truncated), patch.object(rq2, "LLM", return_value=model):
            rq2._run_trial(self.run, state, trial)
        metrics, accesses, evidence = _artifacts(self.run, trial)
        self.assertEqual(trial["jobs"][0]["reason"], "truncated_response")
        self.assertEqual(metrics["requested_tool_calls"], 1)
        self.assertEqual(metrics["tool_calls"], 0)
        self.assertEqual(accesses[0]["execution_status"], "not_executed")
        self.assertIsNone(evidence[0]["tool_results"])

    def test_controller_failure_retains_usage_from_last_saved_response(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "direct")
        fixture = Path(self.temp.name) / "fixture"
        write_project(fixture)
        model = FixtureLLM([[("write_file", {"path": "/work/" + p.name, "content": p.read_text()})
                             for p in fixture.iterdir()], [("check", {})]])
        with patch.object(rq2, "LLM", return_value=model), \
                patch.object(rq2, "verify_project", side_effect=RuntimeError("fixture gate exception")):
            with self.assertRaisesRegex(RuntimeError, "fixture gate exception"):
                rq2._run_trial(self.run, state, trial)
        self.assertEqual(trial["jobs"][0]["reason"], "controller_error")
        self.assertEqual(trial["jobs"][0]["responses"], 2)
        self.assertEqual(rq2.aggregate_usage(trial["jobs"])["total_tokens"], 250)
        self.assertFalse(trial["jobs"][0]["usage_unknown"])

    def test_failed_attempts_are_retained_and_evaluated_without_replacement(self):
        observed = []
        def generate(root, state, trial):
            self.mock_generation(root, state, trial)
            observed.append(trial["id"])
            if len(observed) == 1:
                raise RuntimeError("fixture generation failure")
        def evaluate(project, bundle, evaluator, destination):
            before = hashes(project)
            self.assertEqual(read_json(bundle / "bundle.json")["runtime_contract"], rq2.CONTRACT)
            result = successful_evaluation()
            if project.parent.name == observed[0]:
                result["passed"] = False
                result["phases"]["normal"]["scenarios"][0]["status"] = "failed"
            self.assertEqual(hashes(project), before)
            return result
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=generate):
            state = rq2.generate_experiment(self.run)
        self.assertEqual(len(observed), 15)
        self.assertEqual(len(set(observed)), 15)
        self.assertEqual(state["trials"][0]["status"], "failed")
        with self.assertRaisesRegex(ValueError, "already recorded"):
            rq2.generate_experiment(self.run)
        with patch.object(rq2, "evaluate_project", side_effect=evaluate) as evaluator:
            state = rq2.evaluate_experiment(self.run)
            self.assertEqual(evaluator.call_count, 30)
        self.assertFalse(state["trials"][0]["evaluation"]["passed"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            rq2.generate_experiment(self.run)
        with patch.object(rq2, "evaluate_project") as evaluator:
            rq2.evaluate_experiment(self.run)
            evaluator.assert_not_called()

    def test_controller_blocks_early_evaluation_and_modified_delivery(self):
        with self.assertRaisesRegex(ValueError, "Finish all generation"):
            rq2.evaluate_experiment(self.run)
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=self.mock_generation):
            state = rq2.generate_experiment(self.run)
        project = self.run / "trials" / state["trials"][0]["id"] / "project"
        (project / "main.c").write_text("edited after generation\n")
        with self.assertRaisesRegex(ValueError, "Delivery changed"):
            rq2.evaluate_experiment(self.run)

    def test_one_repetition_parallel_generation_retains_all_five_trials(self):
        pilot = Path(self.temp.name) / "pilot"
        with patch.object(rq2, "preflight", return_value={"openai": "1.97.0", "jsonschema": "4.24.0"}):
            state = rq2.prepare_experiment(pilot, repetitions=1)
        self.assertEqual(len(state["trials"]), 5)
        self.assertEqual(state["repetitions"], 1)
        barrier = threading.Barrier(5, timeout=10)
        def generate(root, local_state, trial, *, save_state):
            trial["status"] = "running"
            save_state()
            barrier.wait()
            self.mock_generation(root, local_state, trial)
            if trial["condition"] == "generic":
                raise RuntimeError("fixture parallel failure")
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), \
                patch.object(rq2, "_run_trial", side_effect=generate) as agent:
            state = rq2.generate_experiment(pilot, workers=5)
        self.assertEqual(agent.call_count, 5)
        self.assertEqual(state["execution"]["workers"], 5)
        self.assertEqual(len({t["id"] for t in state["trials"]}), 5)
        self.assertEqual(sum(t["status"] == "completed" for t in state["trials"]), 4)
        self.assertEqual(sum(t["status"] == "failed" for t in state["trials"]), 1)
        self.assertEqual(read_json(pilot / "experiment.json"), state)
        with patch.object(rq2, "evaluate_project", return_value=successful_evaluation()) as evaluator:
            state = rq2.evaluate_experiment(pilot)
        self.assertEqual(evaluator.call_count, 10)
        summarize_experiment(pilot, state)
        summary = read_json(pilot / "summary/results.json")
        self.assertTrue(summary["experiment_complete"])
        self.assertTrue(all(g["planned_attempts"] == 1 for g in summary["conditions"].values()))
        self.assertIn("1 generations per condition", summary["limitations"][0])

    def test_summary_keeps_denominators_unknown_usage_and_human_annotations(self):
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=self.mock_generation):
            rq2.generate_experiment(self.run)
        raw = successful_evaluation()
        raw["passed"] = False
        raw["phases"]["normal"]["scenarios"][0].update(status="failed", error="fixture error")
        with patch.object(rq2, "evaluate_project", return_value=raw):
            state = rq2.evaluate_experiment(self.run)
        state["trials"][0]["jobs"][0]["usage"]["input_tokens"] = None
        save_json(self.run / "experiment.json", state)
        summarize_experiment(self.run, state)
        summary = read_json(self.run / "summary/results.json")
        self.assertIsNone(summary["trials"][0]["total_tokens"])
        for group in summary["conditions"].values():
            self.assertEqual(group["planned_attempts"], 3)
            self.assertEqual(group["independent_pass_rate"], 0)
        with (self.run / "summary/scenarios.csv").open() as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 960)
        path = self.run / "summary/attribution.csv"
        with path.open() as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]["review_status"] = "verified"
        rows[0]["candidate_category"] = "spec_contradiction"
        rows[0]["root_cause"] = "manually verified fixture"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fields)
            writer.writeheader()
            writer.writerows(rows)
        summarize_experiment(self.run, state)
        with path.open() as handle:
            restored = next(csv.DictReader(handle))
        self.assertEqual(restored["root_cause"], "manually verified fixture")

    def test_selected_dgf_conditions_have_three_trials_and_no_ablation_comparisons(self):
        pilot = Path(self.temp.name) / "dgf"
        with patch.object(rq2, "preflight", return_value={"openai": "1.97.0", "jsonschema": "4.24.0"}):
            state = rq2.prepare_experiment(pilot, repetitions=1, conditions=("direct", "generic", "full"))
        self.assertEqual(state["conditions"], ["direct", "generic", "full"])
        self.assertEqual({t["condition"] for t in state["trials"]}, set(state["conditions"]))
        self.assertFalse((pilot / "views/full_no_vectors").exists())
        summarize_experiment(pilot, state)
        summary = read_json(pilot / "summary/results.json")
        self.assertEqual(set(summary["conditions"]), set(state["conditions"]))
        self.assertEqual({c["baseline"] for c in summary["contrasts"]}, {"direct", "generic"})

    def test_coap_dgf_source_cost_inputs_and_initial_final_evaluations(self):
        pilot = Path(self.temp.name) / "coap_dgf"
        with patch.object(rq2, "preflight", return_value={"openai": "1.97.0", "jsonschema": "4.24.0"}):
            state = rq2.prepare_experiment(pilot, protocol="coap", repetitions=1,
                                           conditions=("direct", "generic", "full"))
        profile = rq2.PROTOCOLS["coap"]
        self.assertEqual(state["protocol"], "coap")
        self.assertEqual(state["source"], str(profile["source_run"] / "specs/r001"))
        self.assertEqual(state["runtime_contract"], profile["contract"])
        self.assertEqual(state["expected_scenarios"], list(rq2.COAP_TEST_IDS))
        self.assertEqual(len(state["expected_scenarios"]), 10)
        self.assertEqual(len(state["trials"]), 3)
        self.assertEqual(digest(pilot / "control/evaluator/coap_check.py"),
                         digest(rq2.ROOT / "evaluation/coap_check.py"))
        self.assertFalse((pilot / "control/evaluator/mqtt_check.py").exists())
        original = profile["source_run"] / "inputs"
        direct = pilot / "views/direct/inputs"
        self.assertEqual((direct / "TASK.md").read_text(),
                         (original / "TASK.md").read_text().replace(rq2.PLANNING_SENTENCE, "", 1))
        for name in ("REQUIREMENTS.md", "protocol.txt"):
            self.assertEqual(digest(direct / name), digest(original / name))
        history = read_json(pilot / "control/historical_cost.json")
        self.assertEqual([j["name"] for j in history["jobs"]], list(rq2.HISTORY_JOBS[:4]))
        self.assertEqual(history["excluded_jobs"], ["05_code"])
        self.assertEqual(history["coding_feedback"]["total_tokens"], 0)
        self.assertEqual(history["spec_repair_and_review"]["total_tokens"], 0)
        self.assertEqual(history["source_formation"]["total_tokens"],
                         sum(j["usage"]["input_tokens"] + j["usage"]["output_tokens"] for j in history["jobs"]))
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=self.mock_generation):
            state = rq2.generate_experiment(pilot, workers=3)
        with patch.object(rq2, "evaluate_project", side_effect=lambda *args: successful_evaluation(rq2.COAP_TEST_IDS)) as evaluator:
            state = rq2.evaluate_experiment(pilot)
        self.assertEqual(evaluator.call_count, 6)
        for call in evaluator.call_args_list:
            self.assertEqual(call.args[2], pilot / "control/evaluator/coap_check.py")
        self.assertTrue(all(t["initial_evaluation"]["passed"] and t["evaluation"]["passed"] for t in state["trials"]))
        summarize_experiment(pilot, state)
        summary = read_json(pilot / "summary/results.json")
        self.assertEqual(summary["protocol"], "coap")
        with (pilot / "summary/scenarios.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3 * 2 * 2 * 10)
        self.assertEqual({r["scenario"] for r in rows}, set(rq2.COAP_TEST_IDS))
        report = (pilot / "summary/REPORT.md").read_text()
        for phrase in ("CoAP RQ2", "out of 10", "10/10 (passed)", "Historical r001 formation", "without historical coding feedback"):
            self.assertIn(phrase, report)
        for phrase in ("MQTT", "16/16", "r002"):
            self.assertNotIn(phrase, report)

    def test_coap_evaluation_rejects_mqtt_scenarios(self):
        report = rq2.normalize_evaluation(successful_evaluation(), list(rq2.COAP_TEST_IDS))
        self.assertFalse(report["passed"])
        self.assertTrue(all(not p["complete"] for p in report["phases"].values()))
        self.assertTrue(all(s["status"] == "not_executed" for p in report["phases"].values() for s in p["scenarios"]))

    def test_initial_runtime_blocks_execution_and_source_overwrite(self):
        project = Path(self.temp.name) / "work"
        trial = {"initial_snapshot": None, "repair_events": []}
        runtime = rq2.RQ2Runtime(project, {}, Path(self.temp.name) / "logs",
                                lambda: {"passed": False}, coder=True, trial=trial)
        first = runtime.dispatch("write_file", {"path": "/work/a.c", "content": "original"})
        self.assertIn("written", first)
        for name, args in (("write_file", {"path": "/work/a.c", "content": "changed"}),
                           ("edit_file", {"path": "/work/a.c", "old": "original", "new": "changed"}),
                           ("run_command", {"command": "touch /work/executed"})):
            self.assertIn("error", runtime.dispatch(name, args))
        self.assertEqual((project / "a.c").read_text(), "original")
        self.assertFalse((project / "executed").exists())

    def test_incomplete_check_does_not_build_or_freeze_an_initial_project(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "direct")
        fixture = Path(self.temp.name) / "fixture"
        write_project(fixture)
        model = FixtureLLM([[("check", {})],
                            [("write_file", {"path": "/work/" + p.name, "content": p.read_text()})
                             for p in fixture.iterdir()], [("check", {})]])
        with patch.object(rq2, "LLM", return_value=model):
            rq2._run_trial(self.run, state, trial)
        self.assertEqual(trial["initial_snapshot"]["responses"], 3)
        self.assertEqual(len(trial["gate_events"]), 1)
        self.assertEqual(trial["gate_events"][0]["responses"], 3)
        self.assertFalse((self.run / "trials" / trial["id"] / "reports/verify_002").exists())

    def test_initial_failed_build_is_preserved_and_repairs_include_shell_and_rewrites(self):
        state = rq2.load_frozen(self.run)
        trial = next(t for t in state["trials"] if t["condition"] == "direct")
        fixture = Path(self.temp.name) / "fixture"
        write_project(fixture)
        broken = (fixture / "main.c").read_text().replace("return n<1;", "return n<1 BROKEN;")
        batches = [[("write_file", {"path": "/work/" + p.name,
                                    "content": broken if p.name == "main.c" else p.read_text()})
                    for p in fixture.iterdir()], [("check", {})],
                   [("edit_file", {"path": "/work/main.c", "old": "n<1 BROKEN", "new": "n<1"}),
                    ("run_command", {"command": "sed -i 's/fixture_translation_unit/repaired_translation_unit/' other.c"}),
                    ("write_file", {"path": "/work/dev.py", "content": (fixture / "dev.py").read_text() + "# repaired fixture\n"})],
                   [("check", {})]]
        model = FixtureLLM(batches)
        with patch.object(rq2, "LLM", return_value=model), patch.object(rq2, "evaluate_project") as evaluator:
            rq2._run_trial(self.run, state, trial)
            evaluator.assert_not_called()
        initial = self.run / trial["initial_snapshot"]["path"]
        self.assertEqual((initial / "main.c").read_text(), broken)
        self.assertEqual(hashes(initial), trial["initial_snapshot"]["hashes"])
        self.assertNotEqual(trial["gate_events"][0]["builds"]["normal"]["exit_code"], 0)
        self.assertTrue(trial["generation_passed"])
        self.assertEqual(len(trial["repair_events"]), 3)
        self.assertEqual({e["tool"] for e in trial["repair_events"]}, {"edit_file", "run_command", "write_file"})
        self.assertIn("run_command", {d["function"]["name"] for d in model.requests[2]["tools"]})
        summarize_experiment(self.run, state)
        row = next(r for r in read_json(self.run / "summary/results.json")["trials"] if r["trial"] == trial["id"])
        self.assertEqual((row["repair_operations"], row["repair_source_operations"], row["repair_test_operations"], row["repair_responses"]),
                         (3, 2, 1, 1))

    def test_initial_and_final_evaluations_use_separate_frozen_projects(self):
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=self.mock_generation):
            state = rq2.generate_experiment(self.run)
        def evaluate(project, bundle, evaluator, destination):
            raw = successful_evaluation()
            if project.name == "initial_project":
                raw["passed"] = False
                raw["builds"]["normal"]["exit_code"] = 2
                raw["phases"].pop("normal")
            return raw
        with patch.object(rq2, "evaluate_project", side_effect=evaluate) as evaluator:
            state = rq2.evaluate_experiment(self.run)
        self.assertEqual(evaluator.call_count, 30)
        self.assertTrue(all(t["evaluation"]["passed"] and not t["initial_evaluation"]["passed"] for t in state["trials"]))
        summarize_experiment(self.run, state)
        rows = read_json(self.run / "summary/results.json")["trials"]
        self.assertTrue(all(not r["initial_normal_build_passed"] and r["normal_build_passed"] for r in rows))
        self.assertTrue(all(r["initial_normal_behavior_status"] == "not_executed" for r in rows))
        with patch.object(rq2, "evaluate_project") as evaluator:
            rq2.evaluate_experiment(self.run)
            evaluator.assert_not_called()

    def test_changed_initial_project_is_rejected_before_evaluation(self):
        with patch.dict(os.environ, {"DS_API": "fixture-not-a-key"}), patch.object(rq2, "_run_trial", side_effect=self.mock_generation):
            state = rq2.generate_experiment(self.run)
        initial = self.run / state["trials"][0]["initial_snapshot"]["path"]
        (initial / "main.c").write_text("modified after freezing")
        with self.assertRaisesRegex(ValueError, "Initial delivery changed"):
            rq2.evaluate_experiment(self.run)


if __name__ == "__main__":
    unittest.main()
