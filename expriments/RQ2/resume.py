"""User-authorized continuation of GPT Luna semantic reviews from 60 to 120."""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from specforge import pipeline as native
from specforge.documents import hashes, read_json, save_json
from specforge.evaluation import evaluate_run

import run as runner
from adapter import RQ2Pipeline

REVIEW_LIMIT = 120


def approved_budget_hashes(before: dict, after: dict) -> dict:
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    if changed:
        source = runner.ROOT / "specforge/pipeline.py"
        original = source.read_bytes().replace(b'"review": 120', b'"review": 60', 1)
        if changed != {"specforge/pipeline.py"} or hashlib.sha256(original).hexdigest() != before["specforge/pipeline.py"]:
            raise ValueError("Native changes exceed the authorized review budget 60 -> 120")
    return after


def exhausted_review(pipeline: native.Pipeline) -> dict:
    state = pipeline.state
    if (state["model"]["model"] != "gpt-6-luna" or state["model"]["reasoning_effort"] != "max"
            or state["current_stage"] != "specs" or state["stop_reason"] != "specs_failed"):
        raise ValueError("Continuation requires a failed gpt-6-luna/max semantic review")
    jobs = state["jobs"]
    if (len(jobs) < 2 or not jobs[-2]["name"].endswith("_specs") or not jobs[-2]["passed"]
            or not jobs[-1]["name"].endswith("_spec_review") or jobs[-1]["reason"] != "response_limit"
            or jobs[-1]["responses"] != 60 or jobs[-1]["passed"]):
        raise ValueError("Behavior must have passed; semantic review must have exhausted 60 responses")
    progress = read_json(pipeline.run / "logs" / jobs[-1]["name"] / "api/progress.json")
    if progress["responses"] != 60 or progress["reason"] != "response_limit" or progress["passed"]:
        raise ValueError("Saved review progress does not match the failed job")
    return progress


def archive_review(pipeline: native.Pipeline, root: Path) -> Path:
    progress = exhausted_review(pipeline)
    original_framework = pipeline.state["framework_hashes"]
    framework = approved_budget_hashes(original_framework, native.framework_hashes())
    destination = root / "continuations/review_120_001"
    destination.mkdir(parents=True)
    before = destination / "before"
    job = pipeline.state["jobs"][-1]["name"]
    for relative in ("run.json", f"logs/{job}/api/progress.json",
                     f"logs/{job}/api/result.json", f"reports/specs_r{pipeline.state['spec_revision']:03d}.json"):
        source = pipeline.run / relative
        if source.exists():
            target = before / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    shutil.copytree(pipeline.bundle(), before / "specs" / pipeline.bundle().name)
    record = {"status": "running", "started_at": native.now(), "controller_pid": os.getpid(),
              "authorization": "User requested review budget 120 and continuation of the two review-budget failures",
              "model": pipeline.state["model"], "review_limit_before": 60, "review_limit_after": REVIEW_LIMIT,
              "responses_before": progress["responses"], "additional_responses_available": REVIEW_LIMIT - progress["responses"],
              "prior_api_hashes": {p: sha for p, sha in hashes(pipeline.run / "logs" / job / "api").items()
                                   if p.startswith(("request_", "response_", "tools_"))},
              "entry_sha256": runner.digest(Path(__file__)),
              "framework_hashes_before": original_framework, "framework_hashes_after": framework}
    save_json(destination / "record.json", record)
    pipeline.state["budget_overrides"] = {"review": REVIEW_LIMIT}
    pipeline.state["framework_hashes"] = framework
    pipeline.state["review_continuation"] = {"record": str(destination.relative_to(root) / "record.json"),
                                            "review_limit_before": 60, "review_limit_after": REVIEW_LIMIT,
                                            "responses_before": progress["responses"], "controller_pid": os.getpid()}
    pipeline.save()
    return destination


def continue_review(pipeline: native.Pipeline, *, until: str) -> bool:
    """Reuse the native review task, progress, tools, gates and downstream loop."""
    exhausted_review(pipeline)
    original_job = pipeline.job
    old_stage = copy.deepcopy(pipeline.state["stages"]["specs"])
    previous_behavior = copy.deepcopy(pipeline.state["jobs"][-2])
    preserved = {name: (pipeline.bundle() / name).read_bytes() if (pipeline.bundle() / name).exists() else None
                 for name in ("traceability.json", "WORKLOG.md")}
    behavior_skipped = False
    review_restored = False

    def continued_job(role, work, inputs, task, gate, limit, coder=False):
        nonlocal behavior_skipped, review_restored
        if role == "specs" and not behavior_skipped:
            behavior_skipped = True
            return previous_behavior
        if role == "spec_review" and behavior_skipped and not review_restored:
            # Native do_specs clears author claims before a fresh review. This is
            # the same independent review, so restore its existing work/checkpoint.
            for name, content in preserved.items():
                path = work / name
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            review_restored = True
        return original_job(role, work, inputs, task, gate, limit, coder=coder)

    try:
        with patch.object(pipeline, "job", continued_job), patch.dict(native.LIMITS, {"review": REVIEW_LIMIT}), \
                patch.object(pipeline, "execute", lambda: native.Pipeline.execute(pipeline, until)):
            return pipeline.resume()
    finally:
        if review_restored:
            stage = pipeline.state["stages"]["specs"]
            stage["started_at"] = old_stage["started_at"]
            stage["elapsed_seconds"] += old_stage["elapsed_seconds"]
            pipeline.save()


def finish_record(destination: Path, pipeline: native.Pipeline, result: dict):
    record = read_json(destination / "record.json")
    job = next(j for j in pipeline.state["jobs"] if j["name"].endswith("_spec_review"))
    api = pipeline.run / "logs" / job["name"] / "api"
    actual = hashes(api)
    record.update(status="passed" if result["passed"] else "failed", finished_at=native.now(),
                  responses_after=job["responses"], result=result,
                  prior_api_unchanged=all(actual.get(p) == sha for p, sha in record["prior_api_hashes"].items()))
    save_json(destination / "record.json", record)
    if not record["prior_api_unchanged"]:
        raise RuntimeError("Continuation changed an original API request, response or tool record")


def resume_main(directory: Path) -> bool:
    pipeline = native.Pipeline(directory)
    exhausted_review(pipeline)
    if not os.environ.get(pipeline.state["model"]["key_env"]):
        raise RuntimeError(f"Set {pipeline.state['model']['key_env']} before generation")
    destination = archive_review(pipeline, directory)
    result = {"passed": False, "reason": "exception"}
    try:
        passed = continue_review(pipeline, until="verify")
        result = {"passed": passed, "reason": pipeline.state["stop_reason"]}
        if passed:
            report = evaluate_run(directory, runner.ROOT / "evaluation/mqtt_check.py")
            result["evaluation_passed"] = report["passed"]
        return passed
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        finish_record(destination, pipeline, result)


def prepare_rq2(experiment: Path):
    state = read_json(experiment / "experiment.json")
    old_config = read_json(experiment / "frozen/config.json")
    config = copy.deepcopy(old_config)
    config["budgets"]["review"] = REVIEW_LIMIT
    if old_config["budgets"]["review"] != 60 or config != runner.read_config(HERE / "config.json"):
        raise ValueError("The only approved configuration change is review budget 60 -> 120")
    pipeline = RQ2Pipeline(experiment / "common/run").configure(config, "basic")
    exhausted_review(pipeline)
    if state["common_status"] != "failed" or any((experiment / "variants" / v / "run").exists() for v in state["order"]):
        raise ValueError("Only a common-review failure with no started variant runs can continue")
    frozen = read_json(experiment / "freeze.json")
    if hashes(experiment / "frozen") != frozen["artifacts"]:
        raise ValueError("Original frozen inputs changed")
    native_hashes = approved_budget_hashes(frozen["native_hashes"], runner.protected_hashes())
    environment = runner.preflight(config)
    if environment["sha256"] != frozen["environment"]["sha256"]:
        raise ValueError("Tool/dependency versions changed")
    destination = archive_review(pipeline, experiment)
    for relative in ("experiment.json", "freeze.json", "frozen/config.json", "common/result.json"):
        target = destination / "before_experiment" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(experiment / relative, target)
    # These contain only common-failure placeholders, never generated branches.
    for name in ("variants", "evaluation", "audit", "summary.json", "summary.csv", "summary.md"):
        source = experiment / name
        if source.exists():
            shutil.move(source, destination / "before_experiment" / name)
    (experiment / "common/result.json").unlink()
    save_json(experiment / "frozen/config.json", config)
    state.update(common_status="resuming", variants_started=False, evaluation_finished=False, controller_pid=os.getpid(),
                 attempts={v: {"status": "registered"} for v in state["order"]},
                 review_continuation={"record": str(destination.relative_to(experiment) / "record.json"),
                                      "review_limit_before": 60, "review_limit_after": REVIEW_LIMIT, "responses_before": 60})
    for key in ("finished_at", "freeze_control_error", "native_unchanged"):
        state.pop(key, None)
    save_json(experiment / "experiment.json", state)
    record = read_json(destination / "record.json")
    record["source_hashes_before"] = frozen["experiment_hashes"]
    record["source_hashes_after"] = runner.experiment_hashes()
    record["native_hashes_before"] = frozen["native_hashes"]
    record["native_hashes_after"] = native_hashes
    save_json(destination / "record.json", record)
    runner.freeze_record(experiment, environment, native_hashes, runner.experiment_hashes())
    return pipeline, destination, environment, native_hashes


def resume_rq2(experiment: Path) -> bool:
    pipeline, destination, environment, native_hashes = prepare_rq2(experiment)
    original = read_json(destination / "before_experiment/common/result.json")
    started = time.monotonic()
    result = {"passed": False, "reason": "exception"}
    try:
        passed = continue_review(pipeline, until="specs")
        result = runner.complete_common(experiment, pipeline, passed)
    except Exception as exc:
        result = {"passed": False, "reason": "controller_error", "error": f"{type(exc).__name__}: {exc}"}
    result.update(finished_at=native.now(), elapsed_seconds=original["elapsed_seconds"] + time.monotonic() - started)
    save_json(experiment / "common/result.json", result)
    finish_record(destination, pipeline, result)
    state = read_json(experiment / "experiment.json")
    state["common_status"] = "passed" if result["passed"] else "failed"
    save_json(experiment / "experiment.json", state)
    runner.freeze_record(experiment, environment, native_hashes, runner.experiment_hashes())
    runner.variants(experiment)
    return result["passed"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("main")
    command.add_argument("--run", type=Path, required=True)
    command = commands.add_parser("rq2")
    command.add_argument("--experiment", type=Path, required=True)
    args = parser.parse_args()
    print(f"Continuation PID: {os.getpid()}", flush=True)
    passed = resume_main(args.run.resolve()) if args.command == "main" else resume_rq2(args.experiment.resolve())
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
