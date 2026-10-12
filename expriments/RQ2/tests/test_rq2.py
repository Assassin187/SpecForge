"""Controlled fixtures remain in temporary directories, outside real generation."""

import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from specforge import pipeline as native
from specforge.documents import hashes, read_json, save_json
from specforge.evaluation import evaluate_project
from specforge.llm import LLM, ModelConfig
from specforge.specs import publish, scaffold, validate
from specforge.tools import ToolRuntime
import adapter
import checks
import report
import run as runner

CONFIG = read_json(HERE / "config.json")


def fixture(directory):
    scope = {"schema_version": 1, "protocol": "MQTT", "version": "3.1.1", "role": "BROKER", "language": "C99", "runtime": "Linux",
             "requirements": [{"id": "R1", "description": "controlled fixture", "source": {"file": "REQUIREMENTS.md", "line_start": 1, "line_end": 1}}],
             "required_capabilities": [], "excluded_features": [], "runtime_contract": {"binary_name": "fixture", "argv_contract": "./fixture"}, "engineering_defaults": []}
    facts = {"schema_version": 1, "facts": [{"id": "F1", "category": "message_format", "entity": "fixture", "statement": "controlled fact", "values": {},
                                           "evidence": [{"chunk_id": "p001", "line_start": 1, "line_end": 1}]}], "open_questions": []}
    module = {"KIND": "PROTOCOL_MODULE_SPEC", "PROTOCOL": {"NAME": "MQTT", "SPEC_VERSION": "3.1.1", "ROLES": ["BROKER"]},
              "MODULES": [{"NAME": "engine", "ROLE": "fixture", "DEPENDENCIES": [], "ARTIFACTS": [], "FILES": ["engine.c", "public/engine.h"], "DOC_REF": []}],
              "GENERATION_ORDER": ["engine"], "CONSISTENCY_RULES": []}
    interface = {"NAME": "decode", "KIND": "FUNC", "SIGNATURE": "int decode(const packet *p)", "ROLE": "decode", "VISIBILITY": "public", "FUNCTION_TYPE": "ALGORITHM"}
    file = {"KIND": "FILE_SPEC", "FILE": {"TRACE_ID": "engine", "LANG": "C", "ROLE": "fixture", "DOC_REF": []},
            "HEADER": {"PATH": "public/engine.h", "DEPENDENCY": [], "INTERFACE": [interface], "DATA": [
                {"NAME": "packet", "KIND": "TYPE", "VISIBILITY": "PUBLIC", "ROLE": "fixture", "TYPE_SPEC": {"TYPE_KIND": "STRUCT", "FIELDS": [{"NAME": "payload", "TYPE": "unsigned int", "ROLE": "fixture payload"}]}}]},
            "SOURCE": {"PATH": "engine.c", "DEPENDENCY": ["public/engine.h"], "SYSTEM_DEPENDENCY": [], "DATA": [], "INTERFACE": [
                {**interface, "TRACE_ID": "engine/decode"}, {"NAME": "main", "TRACE_ID": "engine/main", "KIND": "FUNC", "SIGNATURE": "int main(void)",
                                                        "ROLE": "entry", "VISIBILITY": "public", "FUNCTION_TYPE": "ENTRYPOINT"}]}}
    function = {"KIND": "FUNCTION_SPEC", "TRACE_ID": "engine/decode", "FUNCTION_TYPE": "ALGORITHM", "ROLE": "decode", "SIGNATURE": {
        "NAME": "decode", "RAW": "int decode(const packet *p)", "RETURN": "int", "PARAMS": [{"NAME": "p", "TYPE": "const packet *", "NULLABLE": False, "OWNERSHIP": "BORROWED"}]},
        "RELY": {"STRUCT": [], "FUNC": [], "VAR": []}, "LOGIC": {"INPUT": "packet", "ACTION": "return payload", "OUTPUT": "int", "INVARIANTS_USED": []},
        "WIRE_MAPPING": [{"PACKET": "fixture", "WIRE_FIELD": "payload", "STRATEGY": "store_in_field"}],
        "TEST_VECTORS": [{"NAME": "zero", "INPUT": {"payload": 0}, "EXPECT": {"value": 0}, "LEVEL": "FUNCTION", "TRACE_REFS": ["R1", "engine/decode"]}]}
    entry = copy.deepcopy(function)
    entry.update(TRACE_ID="engine/main", FUNCTION_TYPE="ENTRYPOINT", ROLE="entry", SIGNATURE={"NAME": "main", "RAW": "int main(void)", "RETURN": "int", "PARAMS": []})
    trace = {"schema_version": 1, "requirements": [{"requirement_id": "R1", "fact_ids": ["F1"], "spec_refs": ["functions/decode.json#/LOGIC/ACTION"],
              "test_ids": ["functions/decode.json#/TEST_VECTORS/0"], "semantic_review": "author review"}],
             "processing_chain": [{"id": "flow", "description": "fixture flow", "spec_refs": ["functions/main.json#/LOGIC/ACTION"]}], "engineering_decisions": []}
    for name, value in {"module_spec.json": module, "files/engine.json": file, "functions/decode.json": function, "functions/main.json": entry,
                        "scope.json": scope, "traceability.json": trace}.items():
        save_json(directory / name, value)
    header = directory / "abi/public/engine.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text("#ifndef ENGINE_H\n#define ENGINE_H\ntypedef struct { unsigned int payload; } packet;\nint decode(const packet *p);\n#endif\n")
    return facts, scope


def project_fixture(project, bundle):
    scaffold(bundle, project)
    (project / "engine.c").write_text('#include "public/engine.h"\nint decode(const packet *p) { return (int)p->payload; }\nint main(void) { return 0; }\n')
    (project / "Makefile").write_text("all:\n\t$(CC) -std=c99 engine.c -o fixture\nclean:\n\trm -f fixture\nsanitize:\n\t$(CC) -fsanitize=address,undefined engine.c -o fixture\ntest:\n\t./fixture\n")
    (project / "README.md").write_text("Controlled fixture only.\n")
    (project / "test.py").write_text("assert True\n")
    save_json(project / "delivery.json", {"schema_version": 1, "files": ["engine.c", "public/engine.h", "Makefile", "README.md", "test.py"],
                                          "tests": [{"id": "test", "path": "test.py", "requirement_ids": ["R1"], "spec_test_refs": ["functions/decode.json#/TEST_VECTORS/0"]}]})


class FakeLLM:
    config = adapter.EXPERIMENT_MODEL

    def __init__(self, work):
        self.work, self.messages = work, []

    def complete(self, messages, tools):
        self.messages.append(copy.deepcopy(messages))
        calls = []
        if "SEMANTIC REVIEW job" in messages[1]["content"]:
            trace = read_json(self.work / "traceability.json")
            assert "semantic_review" not in trace["requirements"][0]
            trace["requirements"][0]["semantic_review"] = "F1 independently re-derived fixture behavior"
            calls.append({"id": "write", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "/work/traceability.json", "content": json.dumps(trace)})}})
        calls.append({"id": "check", "type": "function", "function": {"name": "check", "arguments": "{}"}})
        return {"message": {"role": "assistant", "content": None, "tool_calls": calls}, "finish_reason": "tool_calls",
                "usage": {"input_tokens": 10, "output_tokens": 3, "reasoning_tokens": 2, "cache_hit_tokens": 1, "cache_miss_tokens": 9, "requests": 1, "elapsed_seconds": 0},
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 2}, "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 9}}}


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()


class GateTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.directory = self.root / "bundle"
        self.facts, self.scope = fixture(self.directory)

    def gate(self, policy, **kwargs):
        return checks.check(self.directory, policy, facts=self.facts, scope=self.scope, **kwargs)

    def mutate(self, name, callback):
        value = read_json(self.directory / name)
        callback(value)
        save_json(self.directory / name, value)

    def test_valid_sample_and_pure_probes(self):
        result = self.gate("V2", review=True)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(len(result["headers"]["probes"]), 2)
        self.assertTrue(all(p["probe"] == '#include "public/engine.h"\n' for p in result["headers"]["probes"]))

    def test_signature_field_and_type_mismatches_separated(self):
        changes = [lambda v: v["HEADER"]["DATA"][0]["TYPE_SPEC"]["FIELDS"][0].update(NAME="missing"),
                   lambda v: v["HEADER"]["DATA"][0]["TYPE_SPEC"]["FIELDS"][0].update(TYPE="int"),
                   lambda v: v["SOURCE"]["INTERFACE"][0].update(SIGNATURE="void decode(const packet *p)")]
        for change in changes:
            fixture(self.directory)
            self.mutate("files/engine.json", change)
            for policy in ("V0", "V1"):
                self.assertTrue(self.gate(policy)["passed"])
            self.assertFalse(self.gate("V2")["passed"])

    def test_broken_headers_only_mandatory_in_v1_v2(self):
        (self.directory / "abi/public/engine.h").write_text("#error controlled_syntax_error\n")
        self.assertTrue(self.gate("V0")["passed"])
        for policy in ("V1", "V2"):
            self.assertFalse(self.gate(policy)["passed"])

    def test_module_ownership_and_function_dependencies_only_v2(self):
        self.mutate("module_spec.json", lambda v: v["MODULES"][0]["FILES"].append("engine.c"))
        self.mutate("functions/main.json", lambda v: v["RELY"]["FUNC"].append({"NAME": "missing_dependency", "KIND": "CALL", "ROLE": "controlled"}))
        self.assertTrue(self.gate("V0")["passed"])
        self.assertTrue(self.gate("V1")["passed"])
        self.assertFalse(self.gate("V2")["passed"])

    def test_common_presence_coverage_and_references(self):
        mutations = [lambda: (self.directory / "functions/decode.json").unlink(), lambda: (self.directory / "abi/public/engine.h").unlink(),
                     lambda: self.mutate("module_spec.json", lambda v: v["MODULES"][0]["FILES"].append("missing.c")),
                     lambda: self.mutate("traceability.json", lambda v: v["requirements"][0].update(requirement_id="R999")),
                     lambda: self.mutate("traceability.json", lambda v: v["requirements"][0].update(fact_ids=["F999"])),
                     lambda: self.mutate("traceability.json", lambda v: v["requirements"][0].update(spec_refs=["functions/decode.json#/missing"])),
                     lambda: self.mutate("traceability.json", lambda v: v["requirements"][0].update(test_ids=["functions/decode.json#/TEST_VECTORS/99"])),
                     lambda: self.mutate("functions/decode.json", lambda v: v["TEST_VECTORS"][0].update(TRACE_REFS=["missing"])),
                     lambda: self.mutate("functions/decode.json", lambda v: v["LOGIC"].update(ACTION=" ")),
                     lambda: self.mutate("files/engine.json", lambda v: v["HEADER"]["DATA"][0].update(TYPE_SPEC={"TYPE_KIND": "STRUCT"})),
                     lambda: self.mutate("files/engine.json", lambda v: v["HEADER"]["DATA"][0].update(TYPE_SPEC={"TYPE_KIND": "ALIAS", "ALIAS_OF": " "})),
                     lambda: self.mutate("functions/main.json", lambda v: v.update(CALL_CONTRACTS=[{"NAME": "decode", "SIGNATURE": "int decode(const packet *p)"}])),
                     lambda: self.mutate("scope.json", lambda v: v["runtime_contract"].update(binary_name="changed"))]
        for change in mutations:
            fixture(self.directory)
            change()
            for policy in ("V0", "V1", "V2"):
                self.assertFalse(self.gate(policy)["passed"])

    def test_json_pointer_array_syntax_and_object_keys(self):
        items = {"spec.json": {"array": ["value"], "object": {"-1": "legal key", "a/b": "escaped"}}}
        self.assertEqual(checks.resolved_reference(items, "spec.json#/object/-1"), "legal key")
        self.assertEqual(checks.resolved_reference(items, "spec.json#/object/a~1b"), "escaped")
        for ref in ("spec.json#/array/-1", "spec.json#/array/00", "spec.json#/object/a~2b"):
            with self.assertRaises(ValueError):
                checks.resolved_reference(items, ref)

    def test_design_does_not_require_function_specs_and_traceability(self):
        shutil.rmtree(self.directory / "functions")
        (self.directory / "traceability.json").unlink()
        (self.directory / "scope.json").unlink()
        with patch("specforge.specs.validate", side_effect=AssertionError("hidden full gate")):
            self.assertTrue(self.gate("basic", design=True)["passed"])

    def test_design_reports_missing_native_navigation_field(self):
        self.mutate("files/engine.json", lambda v: v["SOURCE"]["INTERFACE"][0].pop("FUNCTION_TYPE"))
        result = self.gate("basic", design=True)
        self.assertFalse(result["passed"])
        self.assertIn("native behavior-job navigation", str(result["errors"]))

    def test_legal_alternate_layout_and_type(self):
        self.mutate("files/engine.json", lambda v: v["HEADER"]["DATA"][0].update(TYPE_SPEC={"TYPE_KIND": "ALIAS", "ALIAS_OF": "unsigned int"}))
        (self.directory / "abi/public/engine.h").write_text("#ifndef ENGINE_H\n#define ENGINE_H\ntypedef unsigned int packet;\nint decode(const packet *p);\n#endif\n")
        file = read_json(self.directory / "files/engine.json")
        file["SOURCE"]["PATH"], file["HEADER"]["PATH"] = "src/engine.c", "api/domain/engine.h"
        file["SOURCE"]["DEPENDENCY"] = ["api/domain/engine.h"]
        save_json(self.directory / "files/engine.json", file)
        self.mutate("module_spec.json", lambda v: v["MODULES"][0].update(FILES=["src/engine.c", "api/domain/engine.h"]))
        target = self.directory / "abi/api/domain/engine.h"
        target.parent.mkdir(parents=True)
        shutil.move(self.directory / "abi/public/engine.h", target)
        result = self.gate("V2")
        self.assertTrue(result["passed"], result["errors"])

    def test_package_integrity_preserves_native_spec_filename_support(self):
        shutil.move(self.directory / "files/engine.json", self.directory / "files/engine layout.json")
        shutil.move(self.directory / "functions/decode.json", self.directory / "functions/decode behavior.json")
        trace = read_json(self.directory / "traceability.json")
        trace["requirements"][0]["spec_refs"] = ["functions/decode behavior.json#/LOGIC/ACTION"]
        trace["requirements"][0]["test_ids"] = ["functions/decode behavior.json#/TEST_VECTORS/0"]
        save_json(self.directory / "traceability.json", trace)
        self.assertTrue(self.gate("V2")["passed"])
        publish(self.directory, 1)
        with patch("specforge.specs.validate", side_effect=AssertionError("hidden check")):
            self.assertTrue(checks.package_integrity(self.directory)["passed"])
        (self.directory / "SUMMARY.md").write_text("modified publication")
        self.assertFalse(checks.package_integrity(self.directory)["passed"])


class ControllerTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.pipeline = adapter.RQ2Pipeline.new(self.root / "run", model=CONFIG["model"]["model"]).configure(CONFIG, "basic")
        self.facts, self.scope = fixture(self.pipeline.run / "design_work")
        save_json(self.pipeline.run / "facts/facts.json", self.facts)
        save_json(self.pipeline.run / "facts/scope.json", self.scope)

    def published(self, policy):
        self.pipeline.policy = policy
        shutil.copytree(self.pipeline.run / "design_work", self.pipeline.bundle())
        self.assertTrue(self.pipeline.publish_specs()["passed"])

    def test_gpt_luna_profile_native_requests_and_scoped_registration(self):
        self.assertEqual(self.pipeline.state["model"], CONFIG["model"])
        self.assertNotIn("gpt-6-luna", native.MODEL_CONFIGS)
        config = ModelConfig.from_record(self.pipeline.state["model"])
        self.assertEqual(config, adapter.EXPERIMENT_MODEL)
        self.assertEqual(runner.read_config(HERE / "config.json")["model"], config.record())
        client = Mock()
        client.chat.completions.create.return_value.model_dump.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
        result = LLM(config, client=client).complete([{"role": "user", "content": "test"}], [])
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["extra_body"], {})
        self.assertEqual((request["reasoning_effort"], request["max_tokens"], request["stream"]), ("max", 65536, False))
        self.assertNotIn("reasoning_content", result["message"])

    def test_common_fresh_review_and_scoped_bindings(self):
        work = self.pipeline.bundle()
        model = FakeLLM(work)
        self.pipeline.llm = model
        (self.pipeline.run / "design_work/WORKLOG.md").write_text("author: everything reviewed")
        original_agent, original_publish = native.run_agent, native.publish
        with patch("specforge.specs.validate", side_effect=AssertionError("hidden full gate")):
            result = self.pipeline.do_specs()
        self.assertTrue(result["passed"])
        self.assertFalse((work / "bundle.json").exists())
        self.assertEqual(len(model.messages), 2)
        self.assertEqual(len(model.messages[1]), 2)
        self.assertNotIn("author: everything reviewed", str(model.messages[1]))
        self.assertIn("180 responses", model.messages[0][1]["content"])
        self.assertIn(f"{CONFIG['budgets']['review']} responses", model.messages[1][1]["content"])
        self.assertIs(native.run_agent, original_agent)
        self.assertIs(native.publish, original_publish)
        gates = list((self.pipeline.run / "reports/rq2_gates").glob("*.json"))
        self.assertTrue(gates)
        self.assertTrue(all(read_json(p)["policy"] == "basic" for p in gates))
        self.assertTrue(all("content_hashes" in read_json(p) and "elapsed_seconds" in read_json(p) for p in gates))

    def test_two_rounds_even_on_success_or_budget_exhaustion(self):
        shutil.copytree(self.pipeline.run / "design_work", self.pipeline.bundle())
        self.pipeline.policy = "V0"
        budgets, prefixes, tasks = [], [], []
        def fake_agent(llm, runtime, prefix, task, response_limit, log_dir, on_progress):
            budgets.append(response_limit)
            prefixes.append(prefix)
            tasks.append(task.split("Current check diagnostics:")[0])
            return {"passed": len(budgets) == 2, "reason": "response_limit" if len(budgets) == 1 else "gate_passed"}
        with patch.object(self.pipeline, "model", return_value=object()), patch.object(native, "run_agent", side_effect=fake_agent), \
                patch("specforge.specs.validate", side_effect=AssertionError("hidden full gate")):
            self.assertTrue(self.pipeline.precode()["passed"])
        self.assertEqual(budgets, [40, 40])
        self.assertEqual(prefixes[0], prefixes[1])
        self.assertEqual(tasks[0], tasks[1])
        self.assertEqual([j["name"] for j in self.pipeline.state["jobs"]], ["01_precode_round_1", "02_precode_round_2"])
        self.assertTrue((self.pipeline.run / "snapshots/precode/precode_round_2/after").is_dir())

    def test_successful_first_round_still_runs_second_fresh_task(self):
        shutil.copytree(self.pipeline.run / "design_work", self.pipeline.bundle())
        self.pipeline.policy = "V0"
        self.pipeline.llm = FakeLLM(self.pipeline.bundle())
        self.assertTrue(self.pipeline.precode()["passed"])
        self.assertEqual(len(self.pipeline.llm.messages), 2)
        self.assertTrue(all(j["passed"] for j in self.pipeline.state["jobs"]))
        self.assertTrue(all("40 responses" in messages[1]["content"] for messages in self.pipeline.llm.messages))

    def test_v0_v1_publish_load_code_and_evaluation_never_full_validate(self):
        with patch("specforge.specs.validate", side_effect=AssertionError("hidden full gate")), patch("specforge.pipeline.validate", side_effect=AssertionError("hidden pipeline gate")):
            self.published("V0")
            self.assertTrue(checks.package_integrity(self.pipeline.bundle())["passed"])
            seen = []
            def fake_agent(llm, runtime, prefix, task, response_limit, log_dir, on_progress):
                seen.append(runtime)
                project_fixture(runtime.work, self.pipeline.bundle())
                return {"passed": True, "reason": "gate_passed"}
            development = {"passed": True, "errors": [], "phases": {}, "builds": {}, "project_hashes": {}, "report_path": str(self.pipeline.run / "reports/mock.json")}
            with patch.object(native, "run_agent", side_effect=fake_agent), patch.object(self.pipeline, "model", return_value=object()), patch.object(native, "verify_project", return_value=development):
                self.assertTrue(self.pipeline.do_code()["passed"])
            self.assertEqual(set(seen[0].inputs), {"/specs", "/reports"})
            self.assertTrue(all(p.is_file() for p in seen[0].readonly))
            self.assertIn("error", seen[0].dispatch("write_file", {"path": "/work/public/engine.h", "content": "changed"}))
            self.assertIn("error", seen[0].dispatch("read_file", {"path": "/harness/evaluate.py"}))
            self.assertIn("error", seen[0].dispatch("read_file", {"path": "/evaluation/result.json"}))
            with patch.object(ToolRuntime, "command", return_value={"exit_code": 1, "timed_out": False}):
                evaluate_project(self.pipeline.run / "project", self.pipeline.bundle(), native.ROOT / "evaluation/mqtt_check.py", self.root / "evaluation")
            self.pipeline.policy = "V1"
            self.assertTrue(self.pipeline.spec_gate(self.pipeline.bundle(), review=True)["passed"])
            self.assertTrue(checks.package_integrity(self.pipeline.bundle())["passed"])

    def test_gap_snapshot_precedes_failed_repair_and_final_uses_last_publication(self):
        self.published("V0")
        original = hashes(self.pipeline.bundle())
        def fake_agent(llm, runtime, prefix, task, response_limit, log_dir, on_progress):
            if runtime.coder:
                project_fixture(runtime.work, self.pipeline.bundle())
                (runtime.logs / "gap.log").write_text("controlled missing interface")
                return {"passed": False, "reason": "spec_gap", "gap": {"kind": "interface", "problem": "controlled", "spec_refs": ["module_spec.json#/PROTOCOL"], "evidence": "/logs/gap.log"}}
            self.assertTrue((self.pipeline.run / "snapshots/initial/metadata.json").is_file())
            self.assertEqual(self.pipeline.policy, "full")
            runtime.check_callback()
            return {"passed": False, "reason": "response_limit"}
        with patch.object(native, "run_agent", side_effect=fake_agent), patch.object(self.pipeline, "model", return_value=object()), patch("specforge.specs.validate", wraps=validate) as full:
            result = self.pipeline.do_code()
        self.assertEqual(result["reason"], "spec_repair_failed")
        self.assertTrue(full.called)
        self.assertEqual(self.pipeline.state["spec_revision"], 2)
        self.pipeline.snapshot("final", result)
        initial = read_json(self.pipeline.run / "snapshots/initial/metadata.json")
        final = read_json(self.pipeline.run / "snapshots/final/metadata.json")
        self.assertEqual(initial["result"]["reason"], "spec_gap")
        self.assertEqual(final["published_revision"], "specs/r001")
        self.assertEqual(final["spec_hashes"], original)
        self.assertEqual(initial["spec_hashes"], original)

    def test_postcode_repairs_full_gate_and_fresh_review_for_all_variants(self):
        for variant in CONFIG["variants"]:
            pipeline = adapter.RQ2Pipeline.new(self.root / variant, model=CONFIG["model"]["model"]).configure(CONFIG, variant)
            fixture(pipeline.run / "design_work")
            save_json(pipeline.run / "facts/facts.json", self.facts)
            save_json(pipeline.run / "facts/scope.json", self.scope)
            pipeline.state["spec_revision"] = 2
            pipeline.llm = FakeLLM(pipeline.bundle())
            with patch("specforge.specs.validate", wraps=validate) as full:
                self.assertTrue(pipeline.do_specs({"problem": "controlled gap"})["passed"])
            self.assertTrue(full.called)
            self.assertEqual(pipeline.policy, variant)
            self.assertEqual(pipeline.state["rq2_last_published"], "specs/r002")
            self.assertTrue(all(read_json(p)["policy"] == "full" for p in (pipeline.run / "reports/rq2_gates").glob("*.json")))
            self.assertEqual([j["name"].split("_", 1)[1] for j in pipeline.state["jobs"]], ["spec_repair", "spec_review"])

    def test_coder_exception_snapshots_and_restores_wrapper(self):
        self.published("V0")
        original = native.run_agent
        with patch.object(self.pipeline, "model", return_value=object()), patch.object(native, "run_agent", side_effect=RuntimeError("controlled")):
            with self.assertRaisesRegex(RuntimeError, "controlled"):
                self.pipeline.job("code", self.pipeline.run / "project", {"/specs": self.pipeline.bundle()}, "task", lambda: {}, "code", coder=True)
        self.assertIs(native.run_agent, original)
        self.assertEqual(read_json(self.pipeline.run / "snapshots/initial/metadata.json")["result"]["reason"], "exception")

    def test_initial_coder_snapshots_api_and_budget_failures(self):
        for reason in ("api_error", "response_limit"):
            pipeline = adapter.RQ2Pipeline.new(self.root / reason, model=CONFIG["model"]["model"]).configure(CONFIG, "V1")
            fixture(pipeline.bundle())
            save_json(pipeline.run / "facts/facts.json", self.facts)
            save_json(pipeline.run / "facts/scope.json", self.scope)
            self.assertTrue(pipeline.publish_specs()["passed"])
            with patch.object(pipeline, "model", return_value=object()), patch.object(native, "run_agent", return_value={"passed": False, "reason": reason}):
                pipeline.job("code", pipeline.run / "project", {"/specs": pipeline.bundle()}, "task", lambda: {}, "code", coder=True)
            self.assertEqual(read_json(pipeline.run / "snapshots/initial/metadata.json")["result"]["reason"], reason)


class OrchestrationTests(TemporaryTest):
    def experiment(self):
        experiment = self.root / "experiment"
        common = adapter.RQ2Pipeline.new(experiment / "common/run", model=CONFIG["model"]["model"]).configure(CONFIG, "basic")
        facts, scope = fixture(common.bundle())
        save_json(common.run / "facts/facts.json", facts)
        save_json(common.run / "facts/scope.json", scope)
        for name in ("prepare", "facts", "design"):
            common.state["stages"][name] = {"status": "passed", "artifact_hashes": common.stage_hashes(name)}
        common.save()
        save_json(experiment / "frozen/config.json", CONFIG)
        shutil.copytree(common.run / "facts", experiment / "frozen/facts")
        shutil.copytree(common.bundle(), experiment / "frozen/draft")
        inventory = hashes(common.bundle())
        save_json(experiment / "common/draft_inventory.json", {"files": inventory, "sha256": runner.signature(inventory)})
        save_json(experiment / "common/result.json", {"passed": True, "reason": "common_passed", "elapsed_seconds": 4})
        save_json(experiment / "experiment.json", {"order": ["V0", "V2", "V1"], "variant_execution": "parallel", "common_status": "passed", "variants_started": False, "attempts": {v: {"status": "registered"} for v in CONFIG["variants"]}})
        save_json(experiment / "freeze.json", {"native_hashes": runner.protected_hashes()})
        return experiment

    def test_three_forks_byte_identical_without_shared_job_cost(self):
        experiment = self.experiment()
        common = hashes(experiment / "frozen/draft")
        for variant in CONFIG["variants"]:
            pipeline = runner.branch_pipeline(experiment, variant)
            self.assertEqual(hashes(pipeline.bundle()), common)
            self.assertEqual(pipeline.state["jobs"], [])
            self.assertFalse(pipeline.state["spec_only"])
            self.assertEqual(pipeline.state["rq2"]["initial_hashes"], common)

    def test_branch_facts_come_from_frozen_approval(self):
        experiment = self.experiment()
        approved = read_json(experiment / "frozen/facts/facts.json")
        save_json(experiment / "common/run/facts/facts.json", {"facts": [{"id": "changed"}]})
        pipeline = runner.branch_pipeline(experiment, "V0")
        self.assertEqual(read_json(pipeline.run / "facts/facts.json"), approved)

    def test_frozen_inputs_rejected_before_any_new_model_request(self):
        experiment = self.experiment()
        runner.freeze_record(experiment, {"sha256": "test"}, runner.protected_hashes(), runner.experiment_hashes())
        save_json(experiment / "frozen/facts/facts.json", {"facts": []})
        with patch.object(runner, "preflight", side_effect=AssertionError("reject artifacts first")):
            with self.assertRaisesRegex(ValueError, "Frozen inputs"):
                runner.assert_frozen(experiment)

    def test_changed_freeze_blocks_evaluation_and_records_all_six_outcomes(self):
        experiment = self.experiment()
        def launch(experiment, variant):
            result = {"passed": False, "reason": "controlled"}
            save_json(experiment / "variants" / variant / "result.json", result)
            return result
        with patch.object(runner, "assert_frozen", side_effect=[None, None, None, None, ValueError("changed evaluator")]), \
                patch.object(runner, "launch", side_effect=launch), patch.object(runner, "evaluate_snapshot", side_effect=AssertionError("frozen evaluator changed")):
            runner.variants(experiment)
        state = read_json(experiment / "experiment.json")
        self.assertIsNotNone(state["freeze_control_error"])
        for variant in CONFIG["variants"]:
            for name in ("initial", "final"):
                result = read_json(experiment / "evaluation" / variant / name / "result.json")
                self.assertEqual(result["reason"], "frozen_state_changed")

    def test_real_worker_subprocess_stops_before_model_when_key_is_absent(self):
        experiment = self.root / "subprocess"
        save_json(experiment / "frozen/config.json", CONFIG)
        for name, filename in (("task", "TASK.md"), ("requirements", "REQUIREMENTS.md"), ("standard", "protocol.pdf")):
            target = experiment / "frozen/inputs" / filename
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(native.ROOT / CONFIG["inputs"][name], target)
        with patch.dict(os.environ, {CONFIG["model"]["key_env"]: ""}):
            result = runner.launch(experiment, None)
        self.assertFalse(result["passed"])
        state = read_json(experiment / "common/run/run.json")
        self.assertEqual(state["stages"]["prepare"]["status"], "passed")
        self.assertEqual(state["stages"]["facts"]["status"], "failed")
        self.assertIn(f"Set {CONFIG['model']['key_env']}", state["stages"]["facts"]["result"]["error"])

    def test_common_failure_three_failed_attempts_no_replacement(self):
        experiment = self.root / "failed"
        def launch(experiment, variant):
            self.assertIsNone(variant)
            result = {"passed": False, "reason": "facts_failed", "elapsed_seconds": 1}
            save_json(experiment / "common/result.json", result)
            return result
        with patch.object(runner, "preflight", return_value={}), patch.object(runner, "launch", side_effect=launch) as called:
            self.assertFalse(runner.draft(HERE / "config.json", experiment))
        called.assert_called_once()
        state = read_json(experiment / "experiment.json")
        self.assertEqual(state["order"], ["V0", "V2", "V1"])
        self.assertTrue(all(r["status"] == "failed" for r in state["attempts"].values()))
        self.assertEqual(len(read_json(experiment / "summary.json")["rows"]), 3)
        self.assertFalse((experiment / "frozen/draft").exists())
        with self.assertRaisesRegex(ValueError, "new experiment"):
            runner.draft(HERE / "config.json", experiment)

    def test_parallel_branches_isolate_failure_and_evaluate_after_all_generation(self):
        experiment = self.experiment()
        completed = []
        barrier = threading.Barrier(3)
        def launch(experiment, variant):
            barrier.wait(timeout=10)
            completed.append(variant)
            if variant == "V2":
                raise RuntimeError("controlled branch launch failure")
            result = {"passed": True, "reason": "controlled", "elapsed_seconds": 1}
            save_json(experiment / "variants" / variant / "result.json", result)
            return result
        def evaluation(experiment, variant, name):
            self.assertEqual(sorted(completed), ["V0", "V1", "V2"])
            self.assertFalse((experiment / "evaluation").is_relative_to(experiment / "variants" / variant / "run"))
            return {}
        with patch.object(runner, "assert_frozen"), patch.object(runner, "launch", side_effect=launch) as called, patch.object(runner, "evaluate_snapshot", side_effect=evaluation) as evaluated:
            runner.variants(experiment)
        self.assertEqual(called.call_count, 3)
        self.assertEqual(evaluated.call_count, 6)
        self.assertFalse(barrier.broken)
        attempts = read_json(experiment / "experiment.json")["attempts"]
        self.assertEqual(attempts["V2"]["status"], "failed")
        self.assertEqual(attempts["V2"]["result"]["reason"], "controller_error")
        self.assertTrue(all(attempts[v]["status"] == "passed" for v in ("V0", "V1")))
        with patch.object(runner, "launch") as called:
            with self.assertRaisesRegex(ValueError, "already started"):
                runner.variants(experiment)
            called.assert_not_called()

    def test_batch_entry_chains_common_and_variants_even_after_common_failure(self):
        experiment = self.root / "batch"
        for passed in (True, False):
            events = []
            def draft(config, out):
                events.append("draft")
                self.assertEqual(out, experiment)
                return passed
            def variants(out):
                self.assertEqual(events, ["draft"])
                self.assertEqual(out, experiment)
                events.append("variants")
            with patch.object(runner, "draft", side_effect=draft), patch.object(runner, "variants", side_effect=variants), \
                    patch.object(sys, "argv", ["run.py", "batch", "--config", str(HERE / "config.json"), "--out", str(experiment)]):
                self.assertEqual(runner.main(), 0)
            self.assertEqual(events, ["draft", "variants"])

    def test_code_metric_allows_failed_compile_without_delivery_gate(self):
        bundle = self.root / "bundle"
        fixture(bundle)
        publish(bundle, 1)
        project = self.root / "project"
        project_fixture(project, bundle)
        (project / "engine.c").write_text("controlled invalid C but complete project\n")
        with patch("specforge.specs.delivery_checks", side_effect=AssertionError("not Code")):
            self.assertTrue(checks.code_complete(project, bundle)["passed"])
        (project / "test.py").unlink()
        self.assertFalse(checks.code_complete(project, bundle)["passed"])

    def test_build0_copy_and_required_binary(self):
        experiment = self.experiment()
        pipeline = runner.branch_pipeline(experiment, "V0")
        pipeline.publish_specs()
        project_fixture(pipeline.run / "project", pipeline.bundle())
        pipeline.snapshot("initial", {"passed": True})
        snapshot = pipeline.run / "snapshots/initial"
        before = hashes(snapshot / "project")
        result = runner.build_initial(experiment, "V0", snapshot)
        self.assertTrue(result["passed"], result)
        self.assertEqual(hashes(snapshot / "project"), before)

    def test_missing_snapshot_explicitly_not_evaluated(self):
        experiment = self.experiment()
        with patch.object(runner, "evaluate_project", side_effect=AssertionError("no complete project")):
            result = runner.evaluate_snapshot(experiment, "V0", "initial")
        self.assertEqual(result["status"], "not_executed")
        self.assertEqual(result["reason"], "snapshot_missing")

    def test_independent_accept_requires_all_sixteen_in_both_modes(self):
        experiment = self.experiment()
        evaluator = experiment / "frozen/evaluator/mqtt_check.py"
        evaluator.parent.mkdir()
        shutil.copyfile(native.ROOT / "evaluation/mqtt_check.py", evaluator)
        pipeline = runner.branch_pipeline(experiment, "V0")
        pipeline.publish_specs()
        project_fixture(pipeline.run / "project", pipeline.bundle())
        pipeline.snapshot("final", {"passed": False, "reason": "controlled method failure"})
        for missing in (False, True):
            phase = {"passed": True, "required_count": 16, "scenarios": [{"status": "passed"} for _ in range(15 if missing else 16)]}
            with patch.object(runner, "evaluate_project", return_value={"passed": True, "builds": {}, "phases": {"normal": copy.deepcopy(phase), "sanitize": copy.deepcopy(phase)}, "errors": []}) as evaluated:
                result = runner.evaluate_snapshot(experiment, "V0", "final")
            self.assertEqual(result["passed"], not missing)
            evaluated.assert_called_once()
            self.assertEqual(evaluated.call_args.args[2], evaluator)


class StatisticsTests(TemporaryTest):
    def usage(self, run, input_tokens, output_tokens):
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "reasoning_tokens": 2, "cache_hit_tokens": 1, "cache_miss_tokens": input_tokens - 1, "requests": 1}
        save_json(run / "logs/01_code/api/response_001.json", {"response": {"usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "prompt_cache_hit_tokens": 1,
            "prompt_cache_miss_tokens": input_tokens - 1, "completion_tokens_details": {"reasoning_tokens": 2}}}})
        save_json(run / "run.json", {"jobs": [{"name": "01_code", "usage": usage}], "usage": usage})

    def test_raw_crosscheck_reasoning_not_added_missing_not_zero(self):
        self.usage(self.root / "run", 10, 5)
        result = report.run_usage(self.root / "run")
        self.assertEqual(result["tokens"], 15)
        self.assertTrue(result["jobs"][0]["matches"])
        save_json(self.root / "run/logs/01_code/api/response_002.json", {"response": {"usage": {}}})
        result = report.run_usage(self.root / "run")
        self.assertIsNone(result["tokens"])
        self.assertIsNone(result["input_tokens"])

    def test_request_without_returned_response_is_unknown_cost(self):
        self.usage(self.root / "run", 10, 5)
        save_json(self.root / "run/logs/01_code/api/request_002.json", {"messages": []})
        result = report.run_usage(self.root / "run")
        self.assertIsNone(result["tokens"])
        self.assertEqual(result["known_completion_subtotal"]["tokens"], 15)
        self.assertEqual(result["unreturned_request_count"], 1)

    def test_failures_shared_cost_initial_metrics_and_pending_audit(self):
        experiment = self.root / "experiment"
        save_json(experiment / "experiment.json", {"order": ["V0", "V2", "V1"], "variant_execution": "parallel", "attempts": {v: {"status": "failed"} for v in CONFIG["variants"]}})
        self.usage(experiment / "common/run", 100, 50)
        for variant in CONFIG["variants"]:
            self.usage(experiment / "variants" / variant / "run", 10, 5)
            save_json(experiment / "variants" / variant / "result.json", {"passed": False, "reason": "controlled"})
        snapshot = experiment / "variants/V0/run/snapshots/initial"
        save_json(snapshot / "metadata.json", {"code": {"passed": True}, "result": {"reason": "response_limit"}})
        (snapshot / "specs").mkdir()
        (snapshot / "project").mkdir()
        save_json(experiment / "evaluation/V0/initial/result.json", {"build0": False})
        save_json(experiment / "evaluation/V0/final/result.json", {"passed": True})
        with patch.object(runner, "evaluate_project", side_effect=AssertionError("report read-only")), patch("specforge.specs.validate", side_effect=AssertionError("report does not check")):
            result = report.write_report(experiment)
        self.assertEqual(result["registered_attempts"], 3)
        self.assertEqual(result["variant_execution"], "parallel")
        self.assertEqual([r["N"] for r in result["rows"]], [1, 1, 1])
        self.assertEqual([r["comparison_usage"]["tokens"] for r in result["rows"]], [165, 165, 165])
        self.assertEqual(result["actual_batch_usage"]["tokens"], 195)
        self.assertEqual(result["rows"][0]["Code"], 1)
        self.assertEqual(result["rows"][0]["Build0"], 0)
        self.assertEqual(result["rows"][0]["Accept"], 1)
        self.assertEqual([r["Defects"] for r in result["rows"]], ["pending", "NA", "NA"])
        audit = experiment / "audit/V0/defects.csv"
        audit.write_text("human entry\n")
        report.write_report(experiment)
        self.assertEqual(audit.read_text(), "human entry\n")


if __name__ == "__main__":
    unittest.main()
