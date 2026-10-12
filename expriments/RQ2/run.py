"""One fresh MQTT draft, isolated parallel variants, then blind evaluation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from specforge.documents import digest, hashes, read_json, save_json
from specforge.evaluation import evaluate_project
from specforge.pipeline import now
from specforge.tools import ToolRuntime

import checks
from adapter import EXPERIMENT_MODEL, RQ2Pipeline, planner_prompt


def signature(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def protected_hashes() -> dict:
    paths = [ROOT / "pyproject.toml"]
    for name in ("specforge", "prompts", "schemas", "cases", "evaluation", "tests"):
        paths += [p for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def experiment_hashes() -> dict:
    paths = [HERE / name for name in ("checks.py", "adapter.py", "run.py", "resume.py", "report.py", "config.json", "README.md", ".gitignore")]
    paths += sorted((HERE / "tests").glob("*.py"))
    return {p.relative_to(HERE).as_posix(): digest(p) for p in paths}


def read_config(path: Path) -> dict:
    config = read_json(path)
    expected = read_json(HERE / "config.json")
    # This implementation is the approved single MQTT pilot, not a general experiment framework.
    if config != expected or config["model"] != EXPERIMENT_MODEL.record():
        raise ValueError("Configuration must match the frozen MQTT pilot settings in config.json")
    if (config["protocol"] != "MQTT" or config["variants"] != ["V0", "V1", "V2"] or config["seed"] != 20261011
            or config["variant_execution"] != "parallel"
            or (config["precode_rounds"], config["implementation_repairs"], config["spec_repairs"], config["required_scenarios"]) != (2, 3, 1, 16)):
        raise ValueError("This pilot requires MQTT, seed 20261011, two revision rounds, 3/1 repair limits and 16 scenarios")
    return config


def preflight(config: dict, *, require_key: bool = True) -> dict:
    if require_key and not os.environ.get(config["model"]["key_env"]):
        raise RuntimeError(f"Set {config['model']['key_env']} before generation")
    versions = {name: importlib.metadata.version(name) for name in ("openai", "jsonschema")}
    versions["python"] = sys.version
    tools = {}
    for name in ("gcc", "make", "pdftotext", "bwrap", "nm"):
        path = shutil.which(name)
        if not path:
            raise RuntimeError(f"Missing required tool: {name}")
        result = subprocess.run([path, "-v" if name == "pdftotext" else "--version"], capture_output=True, text=True, timeout=10)
        tools[name] = {"path": path, "sha256": digest(Path(path)), "version": (result.stdout + result.stderr).splitlines()[0]}
    with tempfile.TemporaryDirectory(prefix="rq2_preflight_") as directory:
        work = Path(directory) / "work"
        work.mkdir()
        result = ToolRuntime(work, {}, Path(directory) / "logs", lambda: {"passed": False}).command("true", timeout=10)
        if result["exit_code"] != 0 or result["timed_out"]:
            raise RuntimeError(f"Native command sandbox unavailable: {result['output']}")
    for name, relative in config["inputs"].items():
        if not (ROOT / relative).is_file():
            raise ValueError(f"Missing {name}: {relative}")
    return {"versions": versions, "tools": tools, "sha256": signature({"versions": versions, "tools": tools}),
            "key_environment": {config["model"]["key_env"]: "present" if os.environ.get(config["model"]["key_env"]) else "missing"}}


def assert_frozen(experiment: Path, *, require_key: bool = True):
    frozen = read_json(experiment / "freeze.json")
    if hashes(experiment / "frozen") != frozen["artifacts"]:
        raise ValueError("Frozen inputs, draft or evaluator changed")
    if protected_hashes() != frozen["native_hashes"] or experiment_hashes() != frozen["experiment_hashes"]:
        raise ValueError("Native or experiment code changed since registration")
    config = read_json(experiment / "frozen/config.json")
    environment = preflight(config, require_key=require_key)
    if environment["sha256"] != frozen["environment"]["sha256"]:
        raise ValueError("Tool/dependency versions changed since registration")


def freeze_record(experiment: Path, environment: dict, native_hashes: dict, source_hashes: dict):
    save_json(experiment / "freeze.json", {"created_at": now(), "environment": environment,
                                          "native_hashes": native_hashes, "experiment_hashes": source_hashes,
                                          "artifacts": hashes(experiment / "frozen")})


def worker_common(experiment: Path) -> dict:
    config = read_json(experiment / "frozen/config.json")
    pipeline = RQ2Pipeline.new(experiment / "common/run", model=config["model"]["model"]).configure(config, "basic")
    inputs = experiment / "frozen/inputs"
    if not pipeline.freeze_inputs(inputs / "TASK.md", inputs / "REQUIREMENTS.md", inputs / "protocol.pdf"):
        return {"passed": False, "reason": "prepare_failed"}
    if not pipeline.execute("facts"):
        return {"passed": False, "reason": pipeline.state["stop_reason"]}
    for filename in ("scope.json", "facts.json"):
        destination = experiment / "frozen/facts" / filename
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(pipeline.run / "facts" / filename, destination)
    passed = pipeline.execute("specs")
    return complete_common(experiment, pipeline, passed)


def complete_common(experiment: Path, pipeline: RQ2Pipeline, passed: bool) -> dict:
    fact_hashes = hashes(experiment / "frozen/facts")
    if any(digest(pipeline.run / "facts" / p) != sha for p, sha in fact_hashes.items()):
        raise ValueError("Approved facts changed during planning")
    if passed:
        pipeline.phase = "common_final"
        gate = pipeline.spec_gate(pipeline.bundle(), review=True)
        passed = gate["passed"]
    if passed:
        inventory = checks.artifact_inventory(pipeline.bundle())
        destination = experiment / "frozen/draft"
        for relative in inventory:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pipeline.bundle() / relative, target)
        save_json(experiment / "common/draft_inventory.json", {"files": inventory, "sha256": signature(inventory)})
    return {"passed": passed, "reason": "common_passed" if passed else pipeline.state["stop_reason"] or "common_final_gate_failed"}


def branch_pipeline(experiment: Path, variant: str) -> RQ2Pipeline:
    config = read_json(experiment / "frozen/config.json")
    pipeline = RQ2Pipeline.new(experiment / "variants" / variant / "run", spec_only=False,
                               model=config["model"]["model"]).configure(config, variant)
    shared = experiment / "common/run"
    shared_state = read_json(shared / "run.json")
    for name in ("inputs", "documents"):
        if hashes(shared / name) != shared_state["stages"]["prepare"]["artifact_hashes"][name]:
            raise ValueError(f"Prepared common {name} changed")
        shutil.copytree(shared / name, pipeline.run / name, dirs_exist_ok=True)
    shutil.copytree(experiment / "frozen/facts", pipeline.run / "facts", dirs_exist_ok=True)
    inventory = read_json(experiment / "common/draft_inventory.json")
    if hashes(experiment / "frozen/draft") != inventory["files"]:
        raise ValueError("Frozen common draft changed")
    shutil.copytree(experiment / "frozen/draft", pipeline.bundle())
    actual = hashes(pipeline.bundle())
    if actual != inventory["files"]:
        raise ValueError("Forked draft must be byte-identical")
    for name in ("prepare", "facts", "design"):
        pipeline.state["stages"][name] = {"status": "passed", "shared_from": "common/run",
                                          "artifact_hashes": shared_state["stages"][name]["artifact_hashes"]}
    pipeline.state["rq2"] = {"variant": variant, "initial_hashes": actual, "initial_sha256": signature(actual)}
    pipeline.save()
    return pipeline


def worker_variant(experiment: Path, variant: str) -> dict:
    pipeline = branch_pipeline(experiment, variant)
    result = {"passed": False, "reason": "exception"}
    try:
        if not pipeline.stage("specs", pipeline.precode):
            result = {"passed": False, "reason": "precode_failed", "details": pipeline.state["stages"]["specs"]["result"]}
        else:
            passed = pipeline.execute("verify")
            result = {"passed": passed, "reason": "generation_passed" if passed else pipeline.state["stop_reason"]}
        return result
    finally:
        pipeline.snapshot("final", result)
        if not (pipeline.run / "snapshots/initial/metadata.json").exists():
            save_json(pipeline.run / "snapshots/initial_missing.json", {"reason": "Coder was not started", "result": result})


def worker(experiment: Path, variant: str | None) -> int:
    destination = experiment / (f"variants/{variant}" if variant else "common")
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        result = worker_variant(experiment, variant) if variant else worker_common(experiment)
    except Exception as exc:
        result = {"passed": False, "reason": "controller_error", "error": f"{type(exc).__name__}: {exc}"}
    result.update(finished_at=now(), elapsed_seconds=time.monotonic() - started)
    save_json(destination / "result.json", result)
    return 0 if result["passed"] else 1


def launch(experiment: Path, variant: str | None) -> dict:
    destination = experiment / (f"variants/{variant}" if variant else "common")
    destination.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(HERE / "run.py"), "_worker", "--experiment", str(experiment)]
    if variant:
        command += ["--variant", variant]
    print(f"Starting {variant or 'common draft'}; log: {destination / 'process.log'}", flush=True)
    started = time.monotonic()
    with (destination / "process.log").open("x") as output:
        process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT)
    if (destination / "result.json").is_file():
        result = read_json(destination / "result.json")
    else:
        result = {"passed": False, "reason": "worker_process_failed", "exit_code": process.returncode,
                  "elapsed_seconds": time.monotonic() - started, "finished_at": now()}
        save_json(destination / "result.json", result)
    print(f"{variant or 'common draft'}: {result['reason']}", flush=True)
    return result


def common_failures(experiment: Path, reason: str):
    state = read_json(experiment / "experiment.json")
    for variant in state["order"]:
        destination = experiment / "variants" / variant
        destination.mkdir(parents=True, exist_ok=True)
        result = {"passed": False, "reason": "common_failed", "common_reason": reason, "elapsed_seconds": 0,
                  "initial_missing": "Common stage failed; Coder not started", "final_missing": "Common stage failed; branch not started"}
        save_json(destination / "result.json", result)
        state["attempts"][variant] = {"status": "failed", "result": result}
    save_json(experiment / "experiment.json", state)


def draft(config_path: Path, experiment: Path) -> bool:
    if experiment.exists():
        raise ValueError("Output must be a new experiment directory; existing attempts cannot be overwritten")
    config = read_config(config_path)
    environment = preflight(config)
    before = protected_hashes()
    sources = experiment_hashes()
    order = list(config["variants"])
    random.Random(config["seed"]).shuffle(order)
    experiment.mkdir(parents=True)
    frozen = experiment / "frozen"
    frozen.mkdir()
    save_json(frozen / "config.json", config)
    for name, filename in (("task", "TASK.md"), ("requirements", "REQUIREMENTS.md"), ("standard", "protocol.pdf")):
        target = frozen / "inputs" / filename
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / config["inputs"][name], target)
    target = frozen / "evaluator/mqtt_check.py"
    target.parent.mkdir()
    shutil.copyfile(ROOT / config["inputs"]["evaluator"], target)
    prompts = frozen / "prompts"
    prompts.mkdir()
    for name in ("facts", "coder", "planner"):
        value = (ROOT / "prompts" / f"{name}.md").read_text()
        (prompts / f"{name}.md").write_text(planner_prompt(value) if name == "planner" else value)
    save_json(experiment / "experiment.json", {"schema_version": 1, "protocol": "MQTT", "created_at": now(),
                                               "order": order, "seed": config["seed"], "common_status": "registered",
                                               "variant_execution": config["variant_execution"], "controller_pid": os.getpid(),
                                               "attempts": {v: {"status": "registered"} for v in config["variants"]},
                                               "variants_started": False})
    freeze_record(experiment, environment, before, sources)
    result = launch(experiment, None)
    state = read_json(experiment / "experiment.json")
    state["common_status"] = "passed" if result["passed"] else "failed"
    save_json(experiment / "experiment.json", state)
    freeze_record(experiment, environment, before, sources)
    if protected_hashes() != before:
        raise RuntimeError("Native files changed during common generation")
    if not result["passed"]:
        common_failures(experiment, result["reason"])
        from report import write_report
        write_report(experiment)
    return result["passed"]


def build_initial(experiment: Path, variant: str, snapshot: Path) -> dict:
    destination = experiment / "evaluation" / variant / "build0"
    destination.mkdir(parents=True)
    before = hashes(snapshot / "project")
    project = destination / "project"
    shutil.copytree(snapshot / "project", project)
    started = time.monotonic()
    result = ToolRuntime(project, {}, destination / "commands", lambda: {"passed": False}).command("make clean && make all", timeout=120)
    binary = read_json(snapshot / "specs/bundle.json")["runtime_contract"]["binary_name"]
    report = {"passed": result["exit_code"] == 0 and not result["timed_out"] and (project / binary).is_file(),
              "command": result, "binary_created": (project / binary).is_file(), "elapsed_seconds": time.monotonic() - started}
    if hashes(snapshot / "project") != before:
        raise RuntimeError("Build0 changed the original initial snapshot")
    save_json(destination / "result.json", report)
    return report


def evaluate_snapshot(experiment: Path, variant: str, name: str) -> dict:
    snapshot = experiment / "variants" / variant / "run/snapshots" / name
    destination = experiment / "evaluation" / variant / name
    if not (snapshot / "metadata.json").is_file():
        result = {"passed": False, "status": "not_executed", "reason": "snapshot_missing", "build0": False}
    else:
        metadata = read_json(snapshot / "metadata.json")
        if not metadata["code"]["passed"]:
            result = {"passed": False, "status": "not_executed", "reason": "incomplete_project",
                      "code_errors": metadata["code"]["errors"], "build0": False}
        else:
            started = time.monotonic()
            build0 = None
            if name == "initial":
                try:
                    build0 = build_initial(experiment, variant, snapshot)
                except Exception as exc:
                    build0 = {"passed": False, "reason": "build_error", "error": f"{type(exc).__name__}: {exc}"}
                    save_json(experiment / "evaluation" / variant / "build0/result.json", build0)
            try:
                result = evaluate_project(snapshot / "project", snapshot / "specs", experiment / "frozen/evaluator/mqtt_check.py", destination)
                expected = read_json(experiment / "frozen/config.json")["required_scenarios"]
                complete = all(phase.get("required_count") == expected and len(phase.get("scenarios", [])) == expected
                               and all(s["status"] == "passed" for s in phase["scenarios"])
                               for phase in (result["phases"].get("normal", {}), result["phases"].get("sanitize", {})))
                result["passed"] = result["passed"] and complete
                result["status"] = "passed" if result["passed"] else "failed"
            except Exception as exc:
                result = {"passed": False, "status": "failed", "reason": "evaluation_error", "error": f"{type(exc).__name__}: {exc}", "build0": False}
            result["build0"] = bool(build0 and build0["passed"])
            result["elapsed_seconds"] = time.monotonic() - started
    destination.mkdir(parents=True, exist_ok=True)
    save_json(destination / "result.json", result)
    return result


def variants(experiment: Path):
    state = read_json(experiment / "experiment.json")
    if state["variants_started"]:
        raise ValueError("Variants already started; interrupted or failed attempts must not be rerun")
    assert_frozen(experiment, require_key=state["common_status"] == "passed")
    state["variants_started"] = True
    state["controller_pid"] = os.getpid()
    save_json(experiment / "experiment.json", state)
    if state["common_status"] != "passed":
        common_failures(experiment, read_json(experiment / "common/result.json")["reason"])
    else:
        for variant in state["order"]:
            state["attempts"][variant] = {"status": "running", "started_at": now()}
        save_json(experiment / "experiment.json", state)

        def generate(variant):
            assert_frozen(experiment)
            # Only subprocess launch/wait runs in these threads; model adaptations stay in each child.
            return launch(experiment, variant)

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(generate, variant): variant for variant in state["order"]}
            for future in as_completed(futures):
                variant = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"passed": False, "reason": "controller_error", "error": f"{type(exc).__name__}: {exc}"}
                    destination = experiment / "variants" / variant
                    destination.mkdir(parents=True, exist_ok=True)
                    save_json(destination / "result.json", result)
                # The controller alone writes shared registration state.
                state["attempts"][variant] = {"status": "passed" if result["passed"] else "failed", "result": result}
                save_json(experiment / "experiment.json", state)
    # Every model process has ended before any independent feedback is computed.
    evaluation_error = None
    try:
        assert_frozen(experiment, require_key=False)
    except Exception as exc:
        evaluation_error = {"passed": False, "status": "not_executed", "reason": "frozen_state_changed",
                            "error": f"{type(exc).__name__}: {exc}", "build0": False}
    for variant in state["order"]:
        for name in ("initial", "final"):
            if evaluation_error:
                save_json(experiment / "evaluation" / variant / name / "result.json", evaluation_error)
            else:
                evaluate_snapshot(experiment, variant, name)
    state = read_json(experiment / "experiment.json")
    state["finished_at"] = now()
    state["evaluation_finished"] = True
    state["freeze_control_error"] = evaluation_error
    state["native_unchanged"] = protected_hashes() == read_json(experiment / "freeze.json")["native_hashes"]
    save_json(experiment / "experiment.json", state)
    from report import write_report
    write_report(experiment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("draft")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command = commands.add_parser("variants")
    command.add_argument("--experiment", type=Path, required=True)
    command = commands.add_parser("batch")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command = commands.add_parser("_worker", help=argparse.SUPPRESS)
    command.add_argument("--experiment", type=Path, required=True)
    command.add_argument("--variant", choices=("V0", "V1", "V2"))
    args = parser.parse_args()
    if args.command == "draft":
        return 0 if draft(args.config.resolve(), args.out.resolve()) else 1
    if args.command == "variants":
        variants(args.experiment.resolve())
        return 0
    if args.command == "batch":
        print(f"Batch PID: {os.getpid()}", flush=True)
        draft(args.config.resolve(), args.out.resolve())
        # Common failure still needs three terminal records and explicit missing evaluations.
        variants(args.out.resolve())
        return 0
    return worker(args.experiment.resolve(), args.variant)


if __name__ == "__main__":
    raise SystemExit(main())
