"""No live model calls: actual Agent/Pipeline/tool loops with scripted completions."""

import copy
import json
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SUITE = Path(__file__).resolve().parents[1]
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SUITE))

import run as rq2
from generic_planner import GenericPipeline, NativePlanning
from report import aggregate_usage, job_evidence, summarize_experiment, trial_account
from spec_adapter import (check_delivery, copy_publication, describe_bundle, generic_checks,
                          publish_generic, sections)
from specforge.documents import digest, hashes, read_json, save_json
from specforge.llm import MODEL_CONFIGS
from specforge.pipeline import Pipeline, verify_project
from specforge.specs import scaffold, validate
from specforge.tools import ToolRuntime


def scope_fixture():
    rows = []
    for number, line in enumerate((ROOT / "cases/mqtt_min/REQUIREMENTS.md").read_text().splitlines(), 1):
        match = re.search(r"\b(R\d+):\s*(.*)", line)
        if match:
            rows.append({"id": match[1], "description": match[2],
                         "source": {"file": "REQUIREMENTS.md", "line_start": number, "line_end": number}})
    return {"schema_version": 1, "protocol": "MQTT", "version": "3.1.1", "role": "BROKER",
            "language": "C99", "runtime": "Linux", "requirements": rows,
            "required_capabilities": [r["description"] for r in rows], "excluded_features": [],
            "runtime_contract": dict(rq2.CONTRACT), "engineering_defaults": []}


def facts_fixture():
    # Valid location from the raw PDF, not a historical model-produced artifact.
    return {"schema_version": 1, "facts": [{"id": "F1", "category": "message_format", "entity": "MQTT",
            "statement": "Synthetic lifecycle-test fact; not a protocol correctness claim.", "values": {},
            "evidence": [{"chunk_id": "p001", "line_start": 1, "line_end": 1}]}], "open_questions": []}


def spec_text(signature="Project outputs", behavior="Synthetic fixture: preserve CONNACK 20 02 00 00; binary NUL, ownership and test example."):
    return f"[PROMPT]\nFixture responsibility\n[RELY]\nNone\n[GUARANTEE]\n{signature}\n[SPECIFICATION]\n{behavior}\n"


def generic_fixture(scope, revised=False, design=False):
    signature = "int g_value(int flag);" if revised else "int g_value(void);"
    functions = [{"id": "G_main", "name": "main", "source": "g_main.c", "spec": "functions/main.spec", "signature": "int main(void);"},
                 {"id": "G_value", "name": "g_value", "source": "g_value.c", "spec": "functions/value.spec",
                  "signature": signature, "header": "include/g_api.h"}]
    plan = {"schema_version": 1, "project_spec": "project.spec",
            "files": [{"path": "g_main.c", "spec": "files/main.spec"}, {"path": "g_value.c", "spec": "files/value.spec"}],
            "headers": [{"path": "include/g_api.h", "artifact": "abi/include/g_api.h"}], "functions": functions,
            "requirements": [{"id": r["id"], "spec_refs": ["functions/value.spec#SPECIFICATION"]} for r in scope["requirements"]]}
    files = {"plan.json": json.dumps(plan), "project.spec": spec_text(), "files/main.spec": spec_text(),
             "files/value.spec": spec_text(), "abi/include/g_api.h": "#ifndef G_API_H\n#define G_API_H\n" + signature + "\n#endif\n"}
    if not design:
        files.update({f["spec"]: spec_text(f["signature"]) for f in functions})
    return files


def review_fixture(scope):
    return {"requirements": [{"id": r["id"], "spec_refs": ["functions/value.spec#SPECIFICATION"],
            "semantic_review": "Fixture reviewer independently checked exact signature, byte example and resource/caller path."}
            for r in scope["requirements"]]}


def native_fixture(scope, revised=False, design=False):
    # Hand-authored tiny native fixture, independent of G and of historical bundles.
    signature = "int p_result(int flag)" if revised else "int p_result(void)"
    module = {"KIND": "PROTOCOL_MODULE_SPEC", "PROTOCOL": {"NAME": "MQTT", "SPEC_VERSION": "3.1.1", "ROLES": ["BROKER"]},
              "MODULES": [{"NAME": "fixture", "ROLE": "native lifecycle test", "DEPENDENCIES": [], "ARTIFACTS": [],
                           "FILES": ["p_entry.c", "p_result.c", "include/p_api.h"], "DOC_REF": []}],
              "GENERATION_ORDER": ["fixture"], "CONSISTENCY_RULES": []}
    entry = {"KIND": "FILE_SPEC", "FILE": {"TRACE_ID": "p_entry", "LANG": "C99", "ROLE": "entry", "DOC_REF": []},
             "SOURCE": {"PATH": "p_entry.c", "DEPENDENCY": ["include/p_api.h"], "DATA": [], "INTERFACE": [{
                 "TRACE_ID": "p/main", "SIGNATURE": "int main(void)", "NAME": "main", "KIND": "FUNC", "ROLE": "entry",
                 "VISIBILITY": "public", "FUNCTION_TYPE": "ENTRYPOINT"}]}}
    result = {"KIND": "FILE_SPEC", "FILE": {"TRACE_ID": "p_result", "LANG": "C99", "ROLE": "result", "DOC_REF": []},
              "HEADER": {"PATH": "include/p_api.h", "DEPENDENCY": [], "DATA": [], "INTERFACE": [{
                  "NAME": "p_result", "SIGNATURE": signature, "KIND": "FUNC", "ROLE": "result",
                  "FUNCTION_TYPE": "ALGORITHM", "VISIBILITY": "public"}]},
              "SOURCE": {"PATH": "p_result.c", "DEPENDENCY": ["include/p_api.h"], "DATA": [], "INTERFACE": [{
                  "TRACE_ID": "p/result", "SIGNATURE": signature, "NAME": "p_result", "KIND": "FUNC", "ROLE": "result",
                  "VISIBILITY": "public", "FUNCTION_TYPE": "ALGORITHM"}]}}
    files = {"module_spec.json": json.dumps(module), "files/entry.json": json.dumps(entry), "files/result.json": json.dumps(result),
             "abi/include/p_api.h": "#ifndef P_API_H\n#define P_API_H\n" + signature + ";\n#endif\n"}
    if design:
        return files
    for name, trace, raw, kind in (("main", "p/main", "int main(void)", "ENTRYPOINT"),
                                   ("p_result", "p/result", signature, "ALGORITHM")):
        params = [{"TYPE": "int", "NAME": "flag", "NULLABLE": False, "OWNERSHIP": "BORROWED"}] if revised and name == "p_result" else []
        function = {"KIND": "FUNCTION_SPEC", "TRACE_ID": trace, "FUNCTION_TYPE": kind, "ROLE": "fixture",
                    "SIGNATURE": {"RAW": raw, "NAME": name, "RETURN": "int", "PARAMS": params},
                    "RELY": {"STRUCT": [], "FUNC": [], "VAR": []},
                    "LOGIC": {"INPUT": "fixture", "ACTION": "Return the fixture result.", "OUTPUT": "int", "INVARIANTS_USED": []},
                    "WIRE_MAPPING": [{"PACKET": "fixture", "WIRE_FIELD": "value", "STRATEGY": "parse_and_skip"}],
                    "TEST_VECTORS": [{"NAME": "fixture", "INPUT": {}, "EXPECT": {}, "TRACE_REFS": ["R01"]}]}
        files["functions/" + name + ".json"] = json.dumps(function)
    trace = {"schema_version": 1, "requirements": [{"requirement_id": r["id"], "fact_ids": ["F1"],
             "spec_refs": ["functions/p_result.json#/LOGIC/ACTION"], "test_ids": ["functions/p_result.json#/TEST_VECTORS/0"]}
             for r in scope["requirements"]], "processing_chain": [{"id": "path", "description": "fixture path",
             "spec_refs": ["functions/main.json#/LOGIC/ACTION"]}], "engineering_decisions": []}
    files["traceability.json"] = json.dumps(trace)
    return files


def code_fixture(condition, scope, revised=False):
    if condition == "G":
        source, helper, name, header = "g_main.c", "g_value.c", "g_value", "include/g_api.h"
    elif condition == "P":
        source, helper, name, header = "p_entry.c", "p_result.c", "p_result", "include/p_api.h"
    else:
        source, helper, name, header = "d_start.c", "d_count.c", "d_count", "include/d_api.h"
    declaration = f"int {name}(int flag)" if revised else f"int {name}(void)"
    invocation = f"{name}(0)" if revised else f"{name}()"
    files = {source: f'#include "{header}"\nint main(void) {{ return {invocation} != 7; }}\n',
             helper: f'#include "{header}"\n{declaration} {{ ' + ("(void)flag; " if revised else "") + "volatile int base = 6; return base + 1; }\n",
             "README.md": "Synthetic development fixture, not an MQTT implementation.\n",
             "test.py": "import subprocess\nassert subprocess.run(['./mqtt_broker']).returncode == 0\n",
             "Makefile": "CC=gcc\nFLAGS=-std=c99 -Wall -Wextra -Wpedantic -Werror\n"
                         f"all:\n\t$(CC) $(FLAGS) {source} {helper} -o mqtt_broker\n"
                         f"sanitize:\n\t$(CC) $(FLAGS) -g -fno-pie -no-pie -fsanitize=address,undefined {source} {helper} -o mqtt_broker\n"
                         "test:\n\t/usr/bin/python3 -B test.py\nclean:\n\trm -f mqtt_broker\n"}
    if condition == "D":
        files[header] = declaration + ";\n"
    delivery = {"schema_version": 1, "files": [source, helper, header, "Makefile", "README.md", "test.py"],
                "tests": [{"id": "fixture", "path": "test.py", "requirement_ids": [r["id"] for r in scope["requirements"]]}]}
    files["delivery.json"] = json.dumps(delivery)
    return files


def write_fixture(directory, files):
    for name, text in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class ScriptedLLM:
    """Stage-aware fake implementing the real native LLM.complete interface."""
    config = MODEL_CONFIGS["deepseek-flash"]

    def __init__(self, gap_condition=None, second_gap=False):
        self.scope = scope_fixture()
        self.requests = []
        self.gap_condition = gap_condition
        self.second_gap = second_gap
        self.code_calls = {}
        self.revised = set()

    def complete(self, messages, tools):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        task, prefix = messages[1]["content"], messages[0]["content"]
        calls, files = [], {}
        if "Extract scope and protocol facts" in task:
            files = {"scope.json": json.dumps(self.scope), "facts.json": json.dumps(facts_fixture())}
        elif task.startswith("DESIGN:"):
            files = generic_fixture(self.scope, design=True)
        elif task.startswith("DESIGN job:"):
            files = native_fixture(self.scope, design=True)
        elif task.startswith("BEHAVIOR:"):
            revised = "SPEC REPAIR:" in task
            if revised:
                self.revised.add("G")
            files = generic_fixture(self.scope, revised=revised)
            # Author review claim must be deleted before the independent job.
            files["review.json"] = json.dumps(review_fixture(self.scope))
            files["WORKLOG.md"] = "Author claims review is complete."
        elif task.startswith("BEHAVIOR job:"):
            revised = "SPEC REPAIR:" in task
            if revised:
                self.revised.add("P")
            files = native_fixture(self.scope, revised=revised)
        elif task.startswith("INDEPENDENT REVIEW:"):
            self.assert_no_author_claim(messages)
            files = {"review.json": json.dumps(review_fixture(self.scope))}
        elif "SEMANTIC REVIEW" in task or "independent semantic" in task.lower():
            trace = json.loads(native_fixture(self.scope, revised="P" in self.revised)["traceability.json"])
            for row in trace["requirements"]:
                row["semantic_review"] = "Fixture independent reviewer replayed this saved behavior path."
            files = {"traceability.json": json.dumps(trace)}
        else:
            condition = "D" if "/inputs/TASK.md" in task else "G" if "SYSSPEC" in task else "P"
            self.code_calls[condition] = self.code_calls.get(condition, 0) + 1
            if self.gap_condition == condition and (self.code_calls[condition] == 1 or self.second_gap):
                files = {"gap.log": "Synthetic observed own-ABI contradiction."}
                ref = "functions/value.spec#GUARANTEE" if condition == "G" else "functions/p_result.json#/SIGNATURE"
                calls.append(("report_spec_gap", {"kind": "interface", "spec_refs": [ref],
                    "problem": "Synthetic gap requesting a revised own ABI", "evidence": "/work/gap.log"}))
            else:
                files = code_fixture(condition, self.scope, revised=condition in self.revised)
        writes = [("write_file", {"path": "/work/" + name, "content": text}) for name, text in files.items()]
        sequence = writes + (calls or [("check", {})])
        message = {"role": "assistant", "content": None, "reasoning_content": "scripted test",
                   "tool_calls": [{"id": f"t{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                                  for i, (name, args) in enumerate(sequence)]}
        usage = {"input_tokens": 100, "output_tokens": 25, "cache_hit_tokens": 80, "cache_miss_tokens": 20,
                 "reasoning_tokens": 5, "requests": 1, "elapsed_seconds": 0.01}
        return {"message": message, "finish_reason": "tool_calls", "usage": usage, "queue_errors": []}

    @staticmethod
    def assert_no_author_claim(messages):
        if "Author claims review is complete" in messages[1]["content"]:
            raise AssertionError("Independent review inherited author checkpoint")


def acceptance_fixture(project, bundle, evaluator, destination):
    destination.mkdir(parents=True)
    assert project != destination
    phases = {mode: {"passed": True, "required_count": len(rq2.TEST_IDS),
              "scenarios": [{"id": name, "status": "passed"} for name in rq2.TEST_IDS]}
              for mode in ("normal", "sanitize")}
    return {"passed": True, "errors": [], "builds": {p: {"exit_code": 0, "timed_out": False} for p in phases}, "phases": phases}


class RQ2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rq2_test_")
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, conditions=("G",)):
        path = self.directory / ("run_" + "".join(conditions))
        with patch("specforge.llm.OpenAI") as api:
            state = rq2.prepare_experiment(path, conditions=conditions)
        api.assert_not_called()
        return path, state

    def bundle(self):
        work = self.directory / "bundle"
        write_fixture(work, generic_fixture(scope_fixture()))
        save_json(work / "scope.json", scope_fixture())
        save_json(work / "review.json", review_fixture(scope_fixture()))
        publish_generic(work, scope_fixture(), 1)
        return work

    def test_archive_and_core_bytes_match_preimplementation_baseline(self):
        archive = SUITE / "archives/legacy_20261009T152647Z"
        self.assertTrue(read_json(archive / "VERIFIED.json")["verified"])
        baseline = read_json(archive / "baseline_hashes.json")
        for name, expected in baseline.items():
            self.assertEqual(digest(ROOT / name), expected, name)
        inventory = read_json(archive / "inventory.json")
        original = archive / "legacy"
        actual = {p.relative_to(original).as_posix() for p in original.rglob("*")}
        self.assertEqual(actual, set(inventory))
        for name, record in inventory.items():
            path = original / name
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), record["mode"], name)
            if record["kind"] == "file":
                self.assertEqual(digest(path), record["sha256"], name)
        for name, expected in read_json(archive / "VERIFIED.json")["archives"].items():
            self.assertEqual(digest(archive / name), expected, name)

    def test_prepare_keeps_raw_task_bytes_and_has_no_historical_sources(self):
        path, state = self.prepare(rq2.CONDITIONS)
        self.assertEqual((path / "control/raw/inputs/TASK.md").read_bytes(), (ROOT / "cases/mqtt_min/TASK.md").read_bytes())
        self.assertIn("before implementing", (path / "control/raw/inputs/TASK.md").read_text())
        self.assertNotIn("source_manifest_sha256", state)
        self.assertEqual(len(state["trials"]), 3)
        self.assertEqual(state["limits"], {**rq2.LIMITS, "implementation_repairs": 3, "spec_repairs": 1})
        self.assertEqual(len(state["expected_scenarios"]), 16)
        self.assertEqual(state["model"]["max_tokens"], 65536)
        self.assertEqual(state["stage_models"]["design"]["max_tokens"], 131072)
        self.assertEqual(rq2.load_frozen(path), state)

    def test_legacy_schema_and_control_drift_are_rejected(self):
        path, state = self.prepare()
        save_json(path / "experiment.json", {"schema_version": 1})
        with self.assertRaisesRegex(ValueError, "Historical"):
            rq2.load_frozen(path)
        save_json(path / "experiment.json", state)
        (path / "control/raw/inputs/TASK.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen"):
            rq2.load_frozen(path)

    def test_prepared_report_is_pending_and_does_not_create_a_model_client(self):
        path, state = self.prepare(rq2.CONDITIONS)
        with patch("specforge.llm.OpenAI") as api:
            summarize_experiment(path, state)
        api.assert_not_called()
        self.assertIn("| G | pending | pending | pending |", (path / "summary/README.md").read_text())
        result = read_json(path / "summary/results.json")
        self.assertFalse(result["generation_finished"])
        self.assertTrue(all(a["accounting"]["requests_recorded"] == 0 for a in result["trials"]))

    def test_model_budget_and_attempt_denominator_are_frozen(self):
        path, state = self.prepare(rq2.CONDITIONS)
        for mutate in (lambda s: s["model"].update(model="another-model"),
                       lambda s: s["limits"].update(implementation_repairs=4),
                       lambda s: s["stage_models"]["design"].update(max_tokens=65536),
                       lambda s: s["trials"].pop(),
                       lambda s: s["trials"][0].update(condition="P")):
            changed = copy.deepcopy(state)
            mutate(changed)
            save_json(path / "experiment.json", changed)
            with self.assertRaisesRegex(ValueError, "Frozen"):
                rq2.load_frozen(path)

    def test_generic_preserves_protocol_details_and_handoff_bytes(self):
        bundle = self.bundle()
        before = (bundle / "functions/value.spec").read_bytes()
        descriptor = describe_bundle(bundle, "G", [r["id"] for r in scope_fixture()["requirements"]], rq2.CONTRACT, 1)
        view = self.directory / "view"
        copy_publication(bundle, descriptor, view)
        self.assertEqual(before, (view / "functions/value.spec").read_bytes())
        self.assertIn("20 02 00 00", before.decode())
        self.assertEqual(hashes(view), descriptor["hashes"])
        self.assertFalse((view / "facts.json").exists())
        self.assertFalse((view / "traceability.json").exists())

    def test_generic_invalid_signature_reference_artifact_or_section_fails(self):
        bundle = self.bundle()
        for mutation, expected in ((lambda: (bundle / "functions/value.spec").write_text(spec_text("int wrong(void);")), "GUARANTEE"),
                                   (lambda: (bundle / "abi/include/g_api.h").write_text("void g_value(void);"), "ABI"),
                                   (lambda: (bundle / "files/value.spec").unlink(), "Missing"),
                                   (lambda: (bundle / "project.spec").write_text("[PROMPT]\nOnly one section"), "sections")):
            with self.subTest(expected=expected):
                shutil.rmtree(bundle)
                bundle = self.bundle()
                mutation()
                result = generic_checks(bundle, scope_fixture(), review=True)
                self.assertFalse(result["passed"])
        shutil.rmtree(bundle)
        bundle = self.bundle()
        plan = read_json(bundle / "plan.json")
        plan["functions"][0]["spec"] = "../../outside.spec"
        save_json(bundle / "plan.json", plan)
        self.assertFalse(generic_checks(bundle, scope_fixture())["passed"])

    def test_published_mutation_and_stale_revision_fail(self):
        bundle = self.bundle()
        args = (bundle, "G", [r["id"] for r in scope_fixture()["requirements"]], rq2.CONTRACT)
        with self.assertRaisesRegex(ValueError, "Stale"):
            describe_bundle(*args, 2)
        (bundle / "functions/value.spec").write_text(spec_text("int g_value(void);", "Changed behavior after publication"))
        with self.assertRaisesRegex(ValueError, "changed"):
            describe_bundle(*args, 1)

    def test_runtime_contract_and_requirement_identity_are_not_inherited_or_rewritten(self):
        bundle = self.bundle()
        ids = [r["id"] for r in scope_fixture()["requirements"]]
        with self.assertRaisesRegex(ValueError, "runtime contract"):
            describe_bundle(bundle, "G", ids, {"binary_name": "wrong", "argv_contract": "./wrong"}, 1)
        with self.assertRaisesRegex(ValueError, "requirement IDs"):
            describe_bundle(bundle, "G", ["R99"], rq2.CONTRACT, 1)

    def test_review_requires_all_requirements_and_no_author_claim_can_skip_job(self):
        pipeline = GenericPipeline.new(self.directory / "planner", llm=ScriptedLLM(), stage_models={
            "design": {**MODEL_CONFIGS["deepseek-flash"].record(), "max_tokens": 131072}})
        case = ROOT / "cases/mqtt_min"
        pipeline.freeze_inputs(case / "TASK.md", case / "REQUIREMENTS.md", case / "spec/mqtt-v3.1.1-os.pdf")
        self.assertTrue(pipeline.execute("specs"))
        self.assertEqual([j["name"].split("_", 1)[1] for j in pipeline.state["jobs"]], ["facts", "design", "specs", "spec_review"])
        self.assertFalse((pipeline.bundle() / "WORKLOG.md").exists())
        review = read_json(pipeline.bundle() / "review.json")
        review["requirements"].pop()
        save_json(pipeline.bundle() / "review.json", review)
        self.assertFalse(generic_checks(pipeline.bundle(), scope_fixture(), review=True)["passed"])

    def test_fake_llm_dgp_complete_lifecycle_with_native_p_and_equal_facts(self):
        path, state = self.prepare(rq2.CONDITIONS)
        model = ScriptedLLM()
        state = rq2.generate_experiment(path, llm=model)
        self.assertTrue(all(t["generation_passed"] for t in state["trials"]),
                        [(t["id"], t.get("stop_reason"), t.get("error")) for t in state["trials"]])
        self.assertEqual(model.config.record(), state["model"])
        self.assertEqual(MODEL_CONFIGS["deepseek-flash"].max_tokens, 65536)
        design_requests = []
        for request_path in path.glob("trials/**/request_*.json"):
            request = read_json(request_path)
            role = request_path.parent.parent.name.split("_", 1)[1]
            expected = state["stage_models"]["design"] if role == "design" else state["model"]
            self.assertEqual(request["model_config"], expected, str(request_path))
            if role == "design":
                design_requests.append(request_path)
        self.assertEqual(len(design_requests), 2)
        facts_requests = [r for r in model.requests if "Extract scope and protocol facts" in r["messages"][1]["content"]]
        self.assertEqual(len(facts_requests), 2)
        self.assertEqual(facts_requests[0], facts_requests[1])
        self.assertNotEqual(path / "trials/r01_G/planning/facts", path / "trials/r01_P/planning/facts")
        self.assertIn("module_spec.json", hashes(path / "trials/r01_P/planning/specs/r001"))
        self.assertTrue(validate(path / "trials/r01_P/planning/specs/r001")["passed"])
        with patch.object(rq2, "evaluate_project", side_effect=acceptance_fixture) as evaluate, patch("specforge.llm.OpenAI") as api:
            state = rq2.evaluate_experiment(path)
            self.assertEqual(evaluate.call_count, 6)
            summarize_experiment(path, state)
        api.assert_not_called()
        for trial in state["trials"]:
            self.assertTrue(all(e["responses"] >= 1 for e in trial["gate_events"]))
            account = trial_account(path, trial)
            expected = 1 if trial["condition"] == "D" else 5
            self.assertEqual(account["accounting"]["responses_recorded"], expected)
            self.assertEqual(account["accounting"]["usage"]["total_tokens"], expected * 125)
            self.assertTrue(trial["evaluation"]["passed"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            rq2.generate_experiment(path, llm=model)

    def test_g_runs_without_any_p_artifacts_and_sandbox_cannot_read_hidden_inputs(self):
        path, state = self.prepare(("G",))
        state = rq2.generate_experiment(path, llm=ScriptedLLM())
        self.assertTrue(state["trials"][0]["generation_passed"])
        self.assertFalse((path / "trials/r01_P").exists())
        self.assertFalse(list((path / "trials/r01_G/reports").glob("handoff*")))
        self.assertTrue((path / "trials/r01_G/handoff_r001.json").is_file())
        trial = state["trials"][0]
        descriptor = trial["spec_revisions"][0]
        work = path / "trials/r01_G/project"
        marker = self.directory / "hidden_acceptance.py"
        marker.write_text("hidden")
        runtime = rq2.RQ2Runtime(work, {"/specs": path / descriptor["view"], "/reports": path / "trials/r01_G/reports"},
                                 self.directory / "commands", lambda: {"passed": True}, trial=trial)
        for name in ("/inputs/TASK.md", "/facts/facts.json", "/harness/evaluate.py", str(marker), "/specs/../../hidden_acceptance.py"):
            self.assertIn("error", runtime.dispatch("read_file", {"path": name}))
        result = runtime.command(f"test ! -e {marker} && test ! -e {ROOT}/runs && test ! -e /harness && test ! -e /facts && test ! -e /inputs", timeout=10)
        self.assertEqual(result["exit_code"], 0, result["output"])
        self.assertEqual(set(runtime.inputs), {"/specs", "/reports"})

    def test_commands_and_edits_are_available_from_first_job_and_abi_is_readonly(self):
        bundle = self.bundle()
        project = self.directory / "project"
        scaffold(bundle, project)
        trial = {"modification_events": []}
        runtime = rq2.RQ2Runtime(project, {}, self.directory / "logs/code/commands", lambda: {"passed": False},
                                 readonly=[project / "include/g_api.h"], coder=True, trial=trial)
        names = {t["function"]["name"] for t in runtime.tool_definitions()}
        self.assertTrue({"run_command", "edit_file", "write_file"} <= names)
        self.assertEqual(runtime.dispatch("run_command", {"command": "printf old > source.c", "timeout": 10})["exit_code"], 0)
        result = runtime.dispatch("edit_file", {"path": "/work/source.c", "old": "old", "new": "new"})
        self.assertNotIn("error", result)
        self.assertEqual(len(trial["modification_events"]), 1)
        self.assertIn("error", runtime.dispatch("write_file", {"path": "/work/include/g_api.h", "content": "bad"}))
        self.assertNotEqual(runtime.command("printf bad > include/g_api.h", timeout=10)["exit_code"], 0)

    def test_spec_repair_for_both_arms_refreshes_abi_view_review_and_budget(self):
        for condition in ("G", "P"):
            with self.subTest(condition=condition):
                path, state = self.prepare((condition,))
                model = ScriptedLLM(gap_condition=condition)
                state = rq2.generate_experiment(path, llm=model)
                trial = state["trials"][0]
                self.assertTrue(trial["generation_passed"], trial.get("error"))
                self.assertEqual(trial["implementation_repairs"], 1)
                self.assertEqual(trial["spec_repairs"], 1)
                self.assertEqual([j["spec_revision"] for j in trial["jobs"]], [1, 2])
                planner = read_json(path / trial["planning_state"])
                roles = [j["name"].split("_", 1)[1] for j in planner["jobs"]]
                self.assertEqual(roles, ["facts", "design", "specs", "spec_review", "spec_repair", "spec_review"])
                self.assertEqual(len(trial["spec_revisions"]), 2)
                h = trial["spec_revisions"][1]["headers"][0]["path"]
                self.assertIn("int flag", (path / "trials" / trial["id"] / "project" / h).read_text())
                self.assertNotIn("int flag", (path / trial["initial_snapshot"]["path"] / h).read_text())
                self.assertEqual(hashes(path / trial["initial_snapshot"]["path"]), trial["initial_snapshot"]["hashes"])
                self.assertTrue((path / "trials" / trial["id"] / "snapshots/before_spec_repair/specs/bundle.json").is_file())
                self.assertTrue(any(e["job"] == "02_repair" for e in trial["gate_events"]))

    def test_second_spec_gap_terminates_without_extra_review_or_replacement(self):
        path, state = self.prepare()
        state = rq2.generate_experiment(path, llm=ScriptedLLM(gap_condition="G", second_gap=True))
        trial = state["trials"][0]
        self.assertEqual(trial["stop_reason"], "spec_repair_limit")
        self.assertEqual(len(trial["jobs"]), 2)
        self.assertEqual(trial["spec_repairs"], 1)
        with self.assertRaisesRegex(ValueError, "must not be replaced"):
            rq2.generate_experiment(path, llm=ScriptedLLM())

    def test_spec_repair_failure_remains_a_failed_attempt(self):
        path, state = self.prepare()
        original = GenericPipeline.do_specs
        def fail_repair(self, repair=None):
            return {"passed": False, "reason": "response_limit"} if repair else original(self)
        with patch.object(GenericPipeline, "do_specs", fail_repair):
            state = rq2.generate_experiment(path, llm=ScriptedLLM(gap_condition="G"))
        trial = state["trials"][0]
        self.assertEqual(trial["stop_reason"], "spec_repair_failed")
        self.assertEqual(trial["spec_repairs"], 1)
        self.assertEqual(len(trial["spec_revisions"]), 1)

    def test_common_code_limit_is_initial_plus_three_repairs(self):
        path, state = self.prepare(("D",))
        def failed_agent(llm, runtime, prefix, task, limit, logs, progress):
            return {"passed": False, "reason": "response_limit", "responses": limit, "usage": {"requests": 0}}
        failure = {"passed": False, "errors": ["fixture failure"], "builds": {}, "phases": {}}
        def failed_gate(project, reports, logs, **kwargs):
            return {**failure, "project_hashes": rq2.project_hashes(project), "report_path": str(reports / "verify_001/report.json")}
        with patch.object(rq2, "run_agent", side_effect=failed_agent) as jobs, patch.object(rq2, "verify_project", side_effect=failed_gate):
            state = rq2.generate_experiment(path, llm=ScriptedLLM())
        self.assertEqual([c.args[4] for c in jobs.call_args_list], [180, 40, 40, 40])
        self.assertEqual(state["trials"][0]["implementation_repairs"], 3)
        self.assertEqual(state["trials"][0]["stop_reason"], "implementation_repair_limit")

    def test_gap_on_last_repair_cannot_buy_an_extra_planning_or_code_job(self):
        path, state = self.prepare()
        count = 0
        def failed_agent(llm, runtime, prefix, task, limit, logs, progress):
            nonlocal count
            count += 1
            if count == 4:
                return {"passed": False, "reason": "spec_gap", "responses": 1, "usage": {"requests": 0},
                        "gap": {"evidence": "/logs/unused", "spec_refs": ["project.spec#SPECIFICATION"]}}
            return {"passed": False, "reason": "response_limit", "responses": limit, "usage": {"requests": 0}}
        def failed_gate(project, reports, logs, **kwargs):
            return {"passed": False, "errors": ["fixture"], "builds": {}, "phases": {},
                    "project_hashes": rq2.project_hashes(project), "report_path": str(reports / "verify_001/report.json")}
        with patch.object(rq2, "run_agent", side_effect=failed_agent), patch.object(rq2, "verify_project", side_effect=failed_gate):
            state = rq2.generate_experiment(path, llm=ScriptedLLM())
        trial = state["trials"][0]
        self.assertEqual(count, 4)
        self.assertEqual(trial["stop_reason"], "implementation_repair_limit")
        self.assertEqual(trial["spec_repairs"], 0)
        self.assertEqual(len(trial["spec_revisions"]), 1)

    def test_interrupted_attempt_not_restarted_when_pending_arms_continue(self):
        path, state = self.prepare(("D", "G"))
        first = state["trials"][0]
        first.update(status="running", jobs=[{"name": "01_code", "role": "code", "usage": {"requests": 0}}])
        save_json(path / "experiment.json", state)
        with patch.object(rq2, "_run_trial") as run_trial:
            def done(run, state, trial, **kwargs):
                trial.update(status="failed", stop_reason="fixture_stop", delivery_hashes={})
            run_trial.side_effect = done
            state = rq2.generate_experiment(path, llm=ScriptedLLM())
        self.assertEqual(run_trial.call_count, 1)
        self.assertEqual(state["trials"][0]["status"], "interrupted")
        self.assertEqual(len(state["trials"][0]["jobs"]), 1)

    def test_fresh_g_and_p_review_wall_time_and_cost_are_separately_recorded(self):
        path, state = self.prepare()
        state = rq2.generate_experiment(path, llm=ScriptedLLM(gap_condition="G"))
        account = trial_account(path, state["trials"][0])
        for phase in ("facts", "design", "specs", "spec_review", "spec_repair", "spec_repair_review", "code", "repair"):
            self.assertEqual(account["phases"][phase]["jobs"], 1, phase)
            self.assertEqual(account["phases"][phase]["usage"]["total_tokens"], 125, phase)
            self.assertGreater(account["phases"][phase]["wall_seconds"], 0, phase)
        self.assertEqual(account["accounting"]["usage"]["total_tokens"], 1000)

    def test_real_independent_evaluator_runs_copies_and_never_changes_source_snapshots(self):
        # Exercise the real evaluator adapter/sandbox with a deterministic fake
        # harness. Its success is lifecycle evidence, not MQTT accuracy evidence.
        project = self.directory / "project"
        write_fixture(project, code_fixture("D", scope_fixture()))
        runtime_bundle = self.directory / "runtime"
        save_json(runtime_bundle / "bundle.json", {"runtime_contract": rq2.CONTRACT})
        harness = self.directory / "fake_evaluator.py"
        harness.write_text("import argparse,json,pathlib\np=argparse.ArgumentParser()\np.add_argument('--binary')\np.add_argument('--out')\na=p.parse_args()\n"
                           "o=pathlib.Path(a.out);o.mkdir(parents=True)\n(o/'report.json').write_text(json.dumps("
                           + repr({"passed": True, "required_count": 16,
                                   "scenarios": [{"id": name, "status": "passed"} for name in rq2.TEST_IDS]}) + "))\n")
        before = hashes(project)
        report = rq2.evaluate_project(project, runtime_bundle, harness, self.directory / "independent")
        self.assertTrue(rq2.normalize_evaluation(report, list(rq2.TEST_IDS))["passed"], report["errors"])
        self.assertEqual(hashes(project), before)
        self.assertTrue((self.directory / "independent/project/mqtt_broker").is_file())

    def test_upstream_failure_keeps_cost_and_attempt_and_does_not_call_coder(self):
        path, state = self.prepare()
        model = ScriptedLLM()
        model.complete = lambda messages, tools: (_ for _ in ()).throw(RuntimeError("synthetic API error"))
        state = rq2.generate_experiment(path, llm=model)
        trial = state["trials"][0]
        self.assertEqual(trial["stop_reason"], "facts_failed")
        self.assertFalse(trial["jobs"])
        account = trial_account(path, trial)
        self.assertTrue(account["accounting"]["usage_unknown"])
        self.assertIsNone(account["accounting"]["usage"]["total_tokens"])
        self.assertEqual(account["accounting"]["known_recorded_usage"]["total_tokens"], 0)
        self.assertEqual(account["accounting"]["requests_recorded"], 1)
        with patch.object(rq2, "evaluate_project") as evaluator:
            state = rq2.evaluate_experiment(path)
        evaluator.assert_not_called()
        self.assertFalse(state["trials"][0]["evaluation"]["passed"])

    def test_development_gate_real_normal_and_sanitizer_preserves_publication(self):
        bundle = self.bundle()
        descriptor = describe_bundle(bundle, "G", [r["id"] for r in scope_fixture()["requirements"]], rq2.CONTRACT, 1)
        project = self.directory / "project"
        scaffold(bundle, project)
        write_fixture(project, code_fixture("G", scope_fixture()))
        consistency = check_delivery(project, descriptor["requirement_ids"], descriptor, bundle)
        self.assertTrue(consistency["passed"], consistency)
        before = hashes(bundle)
        report = verify_project(project, self.directory / "reports", self.directory / "logs", consistency=consistency,
                                runtime_contract=rq2.CONTRACT, readonly_headers=[project / "include/g_api.h"])
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(set(report["phases"]), {"normal", "sanitize"})
        self.assertEqual(before, hashes(bundle))
        (project / "include/g_api.h").write_text("drift")
        self.assertFalse(check_delivery(project, descriptor["requirement_ids"], descriptor, bundle)["passed"])

    def test_acceptance_missing_duplicate_blocked_or_skipped_never_passes(self):
        valid = acceptance_fixture(None, None, None, self.directory / "eval")
        self.assertTrue(rq2.normalize_evaluation(valid, list(rq2.TEST_IDS))["passed"])
        for mutation in (lambda r: r["phases"]["normal"]["scenarios"].pop(),
                         lambda r: r["phases"]["sanitize"]["scenarios"].append(r["phases"]["sanitize"]["scenarios"][0]),
                         lambda r: r["phases"]["normal"]["scenarios"][0].update(status="environment_blocked"),
                         lambda r: r["phases"]["normal"]["scenarios"][0].update(status="skipped"),
                         lambda r: r["builds"].pop("normal")):
            report = copy.deepcopy(valid)
            mutation(report)
            result = rq2.normalize_evaluation(report, list(rq2.TEST_IDS))
            self.assertFalse(result["passed"])
            self.assertEqual(len(result["phases"]["normal"]["scenarios"]), 16)

    def test_evaluation_after_all_generation_and_original_snapshots_unchanged(self):
        path, state = self.prepare(("D",))
        with self.assertRaisesRegex(ValueError, "Finish all"):
            rq2.evaluate_experiment(path)
        state = rq2.generate_experiment(path, llm=ScriptedLLM())
        trial = state["trials"][0]
        initial = path / trial["initial_snapshot"]["path"]
        final = path / "trials/r01_D/project"
        before = (hashes(initial), hashes(final))
        with patch.object(rq2, "evaluate_project", side_effect=acceptance_fixture):
            rq2.evaluate_experiment(path)
        self.assertEqual(before, (hashes(initial), hashes(final)))

    def test_truncation_counted_unknown_usage_not_zero(self):
        api = self.directory / "api"
        save_json(api / "request_001.json", {})
        save_json(api / "request_002.json", {})
        save_json(api / "response_001.json", {"finish_reason": "length", "usage": {"input_tokens": 10, "output_tokens": 5}})
        save_json(api / "api_error_002.json", {"error": "unknown"})
        job = job_evidence({"reason": "api_error"}, api, self.directory, "facts")
        account = aggregate_usage([job])
        self.assertEqual(account["truncated_responses"], 1)
        self.assertIsNone(account["usage"]["total_tokens"])
        self.assertEqual(account["known_recorded_usage"]["total_tokens"], 15)
        self.assertEqual(account["requests_recorded"], 2)


if __name__ == "__main__":
    unittest.main()
