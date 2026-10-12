"""Offline checks for the authorized 60 -> 120 semantic-review continuation."""

import copy
import hashlib
import json
import shutil
from unittest.mock import patch

from test_rq2 import CONFIG, TemporaryTest, adapter, fixture, hashes, native, read_json, runner, save_json
import resume as continuation

USAGE = {"input_tokens": 10, "output_tokens": 3, "reasoning_tokens": 2,
         "cache_hit_tokens": 1, "cache_miss_tokens": 9, "requests": 1, "elapsed_seconds": 0}


class Reviewer:
    config = adapter.EXPERIMENT_MODEL

    def __init__(self, work, *, complete=True):
        self.work = work
        self.complete_review = complete
        self.messages = []

    def complete(self, messages, tools):
        self.messages.append(copy.deepcopy(messages))
        trace = read_json(self.work / "traceability.json")
        assert trace["requirements"][0]["semantic_review"] == "already independently reviewed"
        calls = []
        if self.complete_review:
            trace["requirements"][1]["semantic_review"] = "F1 independently checked during continuation"
            calls = [{"id": "write", "type": "function", "function": {"name": "write_file",
                      "arguments": json.dumps({"path": "/work/traceability.json", "content": json.dumps(trace)})}},
                     {"id": "check", "type": "function", "function": {"name": "check", "arguments": "{}"}}]
        return {"message": {"role": "assistant", "content": "continuing review", "tool_calls": calls},
                "finish_reason": "tool_calls" if calls else "stop", "usage": USAGE.copy(),
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 3,
                             "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 9,
                             "completion_tokens_details": {"reasoning_tokens": 2}}}}


class ContinuationTests(TemporaryTest):
    def pipeline(self, name, *, experiment=False, complete=True):
        directory = self.root / name
        reviewer = Reviewer(directory / "specs/r001", complete=complete)
        pipeline = (adapter.RQ2Pipeline if experiment else native.Pipeline).new(directory, llm=reviewer)
        if experiment:
            pipeline.configure(CONFIG, "basic")
        facts, scope = fixture(pipeline.run / "design_work")
        scope["requirements"].append({**scope["requirements"][0], "id": "R2"})
        save_json(pipeline.run / "facts/scope.json", scope)
        save_json(pipeline.run / "facts/facts.json", facts)
        shutil.copytree(pipeline.run / "design_work", pipeline.bundle())
        trace = read_json(pipeline.bundle() / "traceability.json")
        trace["requirements"][0]["semantic_review"] = "already independently reviewed"
        trace["requirements"].append({**trace["requirements"][0], "requirement_id": "R2"})
        trace["requirements"][1].pop("semantic_review")
        save_json(pipeline.bundle() / "traceability.json", trace)
        (pipeline.bundle() / "WORKLOG.md").write_text("Review checkpoint: R1 done, next finish R2.")
        for stage in ("prepare", "facts", "design"):
            pipeline.state["stages"][stage] = {"status": "passed", "artifact_hashes": pipeline.stage_hashes(stage)}
        pipeline.state["stages"]["specs"] = {"status": "failed", "started_at": "original_start",
                                             "elapsed_seconds": 5, "result": {"passed": False}}
        pipeline.state.update(current_stage="specs", stop_reason="specs_failed")
        pipeline.state["jobs"] = [
            {"name": "03_specs", "passed": True, "reason": "gate_passed", "responses": 1, "usage": USAGE.copy()},
            {"name": "04_spec_review", "passed": False, "reason": "response_limit", "responses": 60,
             "work": "specs/r001", "usage": {**USAGE, "requests": 60},
             "working_hashes": {p: sha for p, sha in hashes(pipeline.bundle()).items() if p != "WORKLOG.md"}}]
        api = pipeline.run / "logs/04_spec_review/api"
        save_json(api / "progress.json", {"passed": False, "reason": "response_limit", "responses": 60,
                                         "context_resets": 3, "usage_entries": [USAGE.copy() for _ in range(60)],
                                         "controller_feedback": {"gate": {"passed": False, "errors": ["R2 missing"]}}})
        save_json(api / "result.json", {"passed": False, "reason": "response_limit", "responses": 60})
        save_json(api / "response_060.json", {"original_response": True})
        pipeline.save()
        return pipeline, reviewer

    def test_both_controllers_resume_at_61_preserving_review_and_usage(self):
        original_limits = native.LIMITS.copy()
        for experiment in (False, True):
            pipeline, reviewer = self.pipeline(str(experiment), experiment=experiment)
            api = pipeline.run / "logs/04_spec_review/api"
            original_response = (api / "response_060.json").read_bytes()
            original_job = pipeline.job
            monitor = patch("specforge.specs.validate", side_effect=AssertionError("hidden full validation")) \
                if experiment else patch.object(native, "publish", wraps=native.publish)
            with monitor:
                self.assertTrue(continuation.continue_review(pipeline, until="specs"))
            self.assertEqual([job["name"] for job in pipeline.state["jobs"]], ["03_specs", "04_spec_review"])
            self.assertIn("60/120 responses already used; 60 remain", reviewer.messages[0][1]["content"])
            self.assertIn("Review checkpoint: R1 done", reviewer.messages[0][1]["content"])
            self.assertEqual(pipeline.state["jobs"][-1]["responses"], 61)
            self.assertEqual(pipeline.state["jobs"][-1]["usage"]["requests"], 61)
            self.assertEqual(len(read_json(api / "progress.json")["usage_entries"]), 61)
            self.assertEqual((api / "response_060.json").read_bytes(), original_response)
            self.assertTrue((api / "request_061.json").exists())
            self.assertEqual(pipeline.job, original_job)
            self.assertEqual(native.LIMITS, original_limits)
            self.assertFalse(pipeline.state["manual_edits"])
            self.assertGreaterEqual(pipeline.state["stages"]["specs"]["elapsed_seconds"], 5)
            self.assertEqual((pipeline.bundle() / "bundle.json").exists(), not experiment)

    def test_extension_grants_only_sixty_more_responses(self):
        original_limits = native.LIMITS.copy()
        pipeline, reviewer = self.pipeline("exhaustion", experiment=True, complete=False)
        self.assertFalse(continuation.continue_review(pipeline, until="specs"))
        self.assertEqual(len(reviewer.messages), 60)
        self.assertEqual(pipeline.state["jobs"][-1]["responses"], 120)
        self.assertEqual(pipeline.state["jobs"][-1]["reason"], "response_limit")
        self.assertTrue(read_json(pipeline.bundle() / "traceability.json")["requirements"][0]["semantic_review"])
        self.assertEqual(native.LIMITS, original_limits)

    def test_only_exact_native_budget_edit_can_rebase_framework(self):
        original = b'LIMITS = {"review": 60}\n'
        edited = b'LIMITS = {"review": 120}\n'
        before = {"specforge/pipeline.py": hashlib.sha256(original).hexdigest(), "prompts/coder.md": "same"}
        after = {**before, "specforge/pipeline.py": hashlib.sha256(edited).hexdigest()}
        with patch("pathlib.Path.read_bytes", return_value=edited):
            self.assertEqual(continuation.approved_budget_hashes(before, after), after)
        with patch("pathlib.Path.read_bytes", return_value=edited + b"# unrelated edit\n"):
            with self.assertRaises(ValueError):
                continuation.approved_budget_hashes(before, after)
        with patch("pathlib.Path.read_bytes", return_value=edited):
            with self.assertRaises(ValueError):
                continuation.approved_budget_hashes(before, {**after, "prompts/coder.md": "changed"})

    def test_other_models_and_other_failure_reasons_rejected(self):
        pipeline, _ = self.pipeline("guard")
        for model, reason in (("deepseek-flash", "response_limit"), ("gpt-6-luna", "api_error")):
            pipeline.state["model"]["model"] = model
            pipeline.state["jobs"][-1]["reason"] = reason
            with self.assertRaises(ValueError):
                continuation.continue_review(pipeline, until="specs")

    def test_native_downstream_follows_resumed_review(self):
        pipeline, _ = self.pipeline("downstream")
        events = []
        def code():
            self.assertTrue(pipeline.state["jobs"][-1]["passed"])
            events.append("code")
            return {"passed": True}
        def verify():
            events.append("verify")
            return {"passed": True}
        with patch.object(pipeline, "do_code", side_effect=code), patch.object(pipeline, "do_verify", side_effect=verify):
            self.assertTrue(continuation.continue_review(pipeline, until="verify"))
        self.assertEqual(events, ["code", "verify"])
        self.assertEqual(pipeline.state["stop_reason"], "complete")

    def test_rq2_archives_failed_placeholders_and_reconnects_variants(self):
        experiment = self.root / "paired"
        pipeline, reviewer = self.pipeline("paired/common/run", experiment=True)
        old_config = copy.deepcopy(CONFIG)
        old_config["budgets"]["review"] = 60
        save_json(experiment / "frozen/config.json", old_config)
        shutil.copytree(pipeline.run / "facts", experiment / "frozen/facts")
        state = {"order": ["V0", "V2", "V1"], "common_status": "failed", "variants_started": True,
                 "evaluation_finished": True, "variant_execution": "parallel",
                 "attempts": {v: {"status": "failed"} for v in CONFIG["variants"]}}
        save_json(experiment / "experiment.json", state)
        save_json(experiment / "common/result.json", {"passed": False, "reason": "specs_failed", "elapsed_seconds": 5})
        for variant in CONFIG["variants"]:
            save_json(experiment / "variants" / variant / "result.json", {"reason": "common_failed"})
            save_json(experiment / "evaluation" / variant / "initial/result.json", {"status": "not_executed"})
        (experiment / "summary.md").write_text("original failure report")
        environment = {"sha256": "fixture_environment"}
        runner.freeze_record(experiment, environment, runner.protected_hashes(), runner.experiment_hashes())
        before_inputs = hashes(experiment / "frozen/facts")
        def variants(path):
            self.assertEqual(path, experiment)
            current = read_json(path / "experiment.json")
            self.assertEqual(current["common_status"], "passed")
            self.assertFalse(current["variants_started"])
            self.assertEqual(current["variant_execution"], "parallel")
            self.assertTrue(all(v["status"] == "registered" for v in current["attempts"].values()))
            self.assertTrue((path / "frozen/draft/traceability.json").exists())
            runner.assert_frozen(path)
        with patch.object(runner, "preflight", return_value=environment), \
                patch.object(adapter.RQ2Pipeline, "model", return_value=reviewer), \
                patch.object(runner, "variants", side_effect=variants) as launched, \
                patch("specforge.specs.validate", side_effect=AssertionError("hidden full validation")):
            self.assertTrue(continuation.resume_rq2(experiment))
            launched.assert_called_once()
        archive = experiment / "continuations/review_120_001"
        self.assertEqual((archive / "before_experiment/summary.md").read_text(), "original failure report")
        self.assertTrue(read_json(archive / "before_experiment/experiment.json")["variants_started"])
        self.assertEqual(read_json(archive / "before_experiment/frozen/config.json")["budgets"]["review"], 60)
        self.assertEqual(read_json(experiment / "frozen/config.json")["budgets"]["review"], 120)
        self.assertEqual(hashes(experiment / "frozen/facts"), before_inputs)
        record = read_json(archive / "record.json")
        self.assertEqual(record["responses_after"], 61)
        self.assertTrue(record["prior_api_unchanged"])
        self.assertGreaterEqual(read_json(experiment / "common/result.json")["elapsed_seconds"], 5)
        with self.assertRaises(ValueError):
            continuation.prepare_rq2(experiment)
