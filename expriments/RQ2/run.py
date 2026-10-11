#!/usr/bin/env python3
"""MQTT RQ2: raw D, independently generated SYSSPEC G, native protocol Specs P."""

from __future__ import annotations

import argparse
import fcntl
import importlib.metadata
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.mqtt_check import TEST_IDS
from specforge.agent import run_agent
from specforge.documents import digest, hashes, prepare, read_json, save_json
from specforge.evaluation import evaluate_project
from specforge.llm import LLM, MODEL_CONFIGS, ModelConfig
from specforge.pipeline import LIMITS, framework_hashes, now, project_hashes, verify_project
from specforge.specs import scaffold
from specforge.tools import ToolRuntime, bounded_output
from generic_planner import GenericPipeline, NativePlanning
from spec_adapter import DELIVERY_SCHEMA, check_delivery, copy_publication, describe_bundle

CONDITIONS = ("D", "G", "P")
SCHEMA_VERSION = 2
SCHEDULE_SEED = 20261009
CONTRACT = {"binary_name": "mqtt_broker", "argv_contract": "./mqtt_broker <port>"}


def runtime_hashes() -> dict:
    result = framework_hashes()
    result["specforge/evaluation.py"] = digest(ROOT / "specforge/evaluation.py")
    result["evaluation/mqtt_check.py"] = digest(ROOT / "evaluation/mqtt_check.py")
    for path in SUITE.rglob("*"):
        relative = path.relative_to(SUITE)
        if path.is_file() and not any(p in ("archives", "runs", "__pycache__") for p in relative.parts):
            result[path.relative_to(ROOT).as_posix()] = digest(path)
    return result


def preflight() -> dict:
    versions = {"python": sys.version, "python_executable": sys.executable}
    for name in ("openai", "jsonschema"):
        versions[name] = importlib.metadata.version(name)
    for name in ("gcc", "make", "bwrap", "pdftotext", "mosquitto_pub", "mosquitto_sub"):
        path = shutil.which(name)
        if path is None:
            raise RuntimeError("Required executable unavailable: " + name)
        flag = "-v" if name == "pdftotext" else "--help" if name.startswith("mosquitto_") else "--version"
        output = subprocess.run([path, flag], capture_output=True, text=True, timeout=10)
        versions[name] = {"path": path, "sha256": digest(Path(path).resolve()),
                          "version": (output.stdout + output.stderr).splitlines()[:2]}
    with tempfile.TemporaryDirectory(prefix="rq2_preflight_") as directory:
        base = Path(directory)
        secret = base / "outside_marker"
        secret.write_text("not a task input")
        runtime = ToolRuntime(base / "work", {}, base / "logs", lambda: {"passed": False})
        result = runtime.command("test ! -e " + str(secret) + " && test -d /proc && test -x /usr/bin/gcc", timeout=10)
        if result["exit_code"] != 0 or result["timed_out"]:
            raise RuntimeError("Isolation preflight failed: " + result["output"])
    return versions


def prepare_experiment(out: Path, *, conditions=CONDITIONS, repetitions=1, protocol="mqtt",
                       model="deepseek-flash") -> dict:
    if protocol != "mqtt" or model != "deepseek-flash" or repetitions != 1:
        raise ValueError("This frozen first study supports MQTT, deepseek-flash and one attempt per condition")
    if not conditions or len(set(conditions)) != len(conditions) or not set(conditions) <= set(CONDITIONS):
        raise ValueError("Choose distinct D/G/P conditions")
    out = out.resolve()
    if out.exists():
        raise ValueError("Use a new experiment directory")
    started = time.monotonic()
    versions = preflight()
    case = ROOT / "cases/mqtt_min"
    # The controller contract is a literal requirement in raw TASK, not a P artifact.
    if "./mqtt_broker <port>" not in (case / "TASK.md").read_text():
        raise ValueError("Raw task startup contract changed")
    control = out / "control"
    frozen = prepare(control / "raw", case / "TASK.md", case / "REQUIREMENTS.md", case / "spec/mqtt-v3.1.1-os.pdf")
    requirements = re.findall(r"\b(R\d+)\s*:", (control / "raw/inputs/REQUIREMENTS.md").read_text())
    if len(requirements) != len(set(requirements)) or not requirements:
        raise ValueError("Raw requirement IDs must be nonempty and unique")
    shutil.copyfile(SUITE / "coder.md", control / "coder.md")
    save_json(control / "delivery_schema.json", DELIVERY_SCHEMA)
    save_json(control / "runtime/bundle.json", {"runtime_contract": CONTRACT})
    evaluator = control / "evaluator/mqtt_check.py"
    evaluator.parent.mkdir()
    shutil.copyfile(ROOT / "evaluation/mqtt_check.py", evaluator)
    order = [c for c in CONDITIONS if c in conditions]
    random.Random(SCHEDULE_SEED).shuffle(order)
    trials = [{"id": "r01_" + c, "condition": c, "repeat": 1, "status": "pending",
               "generation_passed": False, "jobs": [], "implementation_repairs": 0, "spec_repairs": 0,
               "gate_events": [], "modification_events": [], "spec_revisions": [],
               "initial_snapshot": None, "initial_evaluation": None, "evaluation": None} for c in order]
    record = {"schema_version": SCHEMA_VERSION, "experiment": "independent-specs",
              "protocol": protocol, "created_at": now(), "model": MODEL_CONFIGS[model].record(),
              "stage_models": {"design": replace(MODEL_CONFIGS[model], max_tokens=2 * MODEL_CONFIGS[model].max_tokens).record()},
              "framework_hashes": runtime_hashes(), "versions": versions,
              "raw": frozen, "raw_sources": {"task": str(case / "TASK.md"),
                                               "requirements": str(case / "REQUIREMENTS.md"),
                                               "protocol": str(case / "spec/mqtt-v3.1.1-os.pdf")},
              "requirement_ids": sorted(requirements), "runtime_contract": CONTRACT,
              "expected_scenarios": list(TEST_IDS), "conditions": list(conditions), "repetitions": 1,
              "schedule_seed": SCHEDULE_SEED, "schedule": order,
              "initial_policy": "snapshot after initial code job, including its development feedback, before extra repairs",
              "limits": {**LIMITS, "implementation_repairs": 3, "spec_repairs": 1},
              "trials": trials, "generation_finished": False, "evaluation_started": False,
              "prepare_elapsed_seconds": round(time.monotonic() - started, 3)}
    frozen_design = {k: record[k] for k in ("schema_version", "experiment", "protocol", "model", "stage_models", "raw",
                    "requirement_ids", "runtime_contract", "expected_scenarios", "conditions", "repetitions",
                    "schedule_seed", "schedule", "limits", "initial_policy")}
    frozen_design["attempts"] = [{k: t[k] for k in ("id", "condition", "repeat")} for t in trials]
    save_json(control / "design.json", frozen_design)
    record["control_hashes"] = hashes(control)
    save_json(out / "experiment.json", record)
    return record


def load_frozen(run: Path) -> dict:
    state = read_json(run / "experiment.json")
    if state.get("schema_version") != SCHEMA_VERSION or state.get("experiment") != "independent-specs":
        raise ValueError("Historical experiments are read-only; prepare a fresh end-to-end run")
    if state["framework_hashes"] != runtime_hashes():
        raise ValueError("Framework or RQ2 changed after preparation; create a new experiment")
    if state["control_hashes"] != hashes(run / "control"):
        raise ValueError("Frozen controller inputs changed")
    design = read_json(run / "control/design.json")
    if any(state[k] != value for k, value in design.items() if k != "attempts"):
        raise ValueError("Frozen experimental design changed in mutable state")
    if [{k: t[k] for k in ("id", "condition", "repeat")} for t in state["trials"]] != design["attempts"]:
        raise ValueError("Frozen attempt identities or denominator changed")
    return state


def coder_prompt(condition: str, frozen_prefix: str, descriptor=None) -> tuple[str, str]:
    prefix = frozen_prefix + "\nExact neutral delivery.json schema:\n" + json.dumps(DELIVERY_SCHEMA, separators=(",", ":"))
    if condition == "D":
        task = ("Implement the entire project from /inputs/TASK.md, /inputs/REQUIREMENTS.md and the MQTT "
                "standard in /documents. Start with /documents/SUMMARY.md and index.json. Use normal internal "
                "engineering planning and progress records to meet the raw task's planning requirement. "
                "There is no separately published specification handoff or upstream specification repair.")
        view_note = "/inputs and /documents: read-only original inputs."
    else:
        navigation = ("project.spec, plan.json and relevant file/function .spec documents (SYSSPEC)" if condition == "G"
                      else "bundle.json and relevant module/file/function JSON specifications")
        task = "Implement the entire project from /specs/SUMMARY.md, scope.json, " + navigation + ". Read relevant dependencies on demand."
        task += "\nPlanned sources: " + ", ".join(descriptor["planned_sources"])
        task += "\nImmutable public headers: " + ", ".join(h["path"] for h in descriptor["headers"])
        task += "\nThe controller allows at most one own-spec revision followed by fresh review when you report an evidenced specification gap."
        view_note = "/specs: read-only published specifications and public ABI."
    prefix += "\nStage view:\n" + view_note + "\n/work: project; /reports: this trial's development reports; /logs: this job's command logs.\n"
    return prefix, task + "\nSave sources, Makefile, README, real development tests and delivery.json; compile, self-test and call check."


class RQ2Runtime(ToolRuntime):
    """Observe revisions from the start; keep the native tool abilities intact."""

    def __init__(self, *args, trial, **kwargs):
        super().__init__(*args, **kwargs)
        self.trial = trial

    def dispatch(self, name, args):
        observe = name in ("write_file", "edit_file", "run_command")
        before = project_hashes(self.work) if observe else {}
        result = super().dispatch(name, args)
        if observe:
            after = project_hashes(self.work)
            # Count actual revisions/deletions of existing artifacts, not first creation.
            changed = [{"path": p, "before_sha256": before[p], "after_sha256": after.get(p)}
                       for p in sorted(before) if before[p] != after.get(p)]
            if changed:
                self.trial["modification_events"].append({"job": self.logs.parent.name,
                    "response": len(list((self.logs.parent / "api").glob("response_*.json"))), "tool": name, "files": changed})
        return result


def _initial_snapshot(run, trial_root, trial):
    project = trial_root / "project"
    target = trial_root / "initial_project"
    shutil.copytree(project, target)
    trial["initial_snapshot"] = {"path": str(target.relative_to(run)), "hashes": hashes(target),
                                  "project_hashes": project_hashes(target), "created_at": now(),
                                  "policy": "after initial code job; includes in-job feedback"}


def _handoff(planner, run, trial_root, state, trial):
    revision = planner.state["spec_revision"]
    descriptor = describe_bundle(planner.bundle(), trial["condition"], state["requirement_ids"],
                                 state["runtime_contract"], revision)
    view = trial_root / "views" / f"r{revision:03d}"
    copy_publication(planner.bundle(), descriptor, view)
    record = {**descriptor, "view": str(view.relative_to(run)), "created_at": now()}
    trial["spec_revisions"].append(record)
    # Transport/provenance metadata stays outside the Coder's reports mount.
    save_json(trial_root / f"handoff_r{revision:03d}.json", record)
    scaffold(view, trial_root / "project")
    return descriptor, view


def _repair_specs(planner, run, trial_root, state, trial, runtime, gap, save_state):
    # Evidence is copied to the planner's own reports; no controller/evaluator mount.
    source = runtime.resolve(gap["evidence"])
    target = planner.run / "reports/development_spec_gap.log"
    shutil.copyfile(source, target)
    gap = {**gap, "evidence": "/reports/" + target.name}
    snapshot = trial_root / "snapshots/before_spec_repair"
    snapshot.mkdir(parents=True)
    shutil.copytree(planner.bundle(), snapshot / "specs")
    shutil.copytree(trial_root / "project", snapshot / "project")
    trial["spec_repairs"] += 1
    trial["spec_gap"] = gap
    previous = planner.bundle()
    planner.state["spec_repairs"] += 1
    planner.state["spec_revision"] += 1
    shutil.copytree(previous, planner.bundle())
    (planner.bundle() / "bundle.json").unlink()
    planner.save()
    save_state()
    started = time.monotonic()
    result = planner.do_specs(gap)
    trial["spec_repair_result"] = {**result, "wall_seconds": round(time.monotonic() - started, 3)}
    if not result["passed"]:
        save_state()
        return None
    planner.state["stages"]["specs"]["artifact_hashes"] = planner.stage_hashes("specs")
    planner.save()
    # Deleted public headers are controller-owned; other implementation files are retained.
    new_paths = {h["path"] for h in read_json(planner.bundle() / "bundle.json")["headers"]}
    for h in read_json(previous / "bundle.json")["headers"]:
        if h["path"] not in new_paths:
            (trial_root / "project" / h["path"]).unlink()
    return _handoff(planner, run, trial_root, state, trial)


def _run_trial(run: Path, state: dict, trial: dict, *, llm=None) -> None:
    trial_root = run / "trials" / trial["id"]
    trial_root.mkdir(parents=True)
    project, reports = trial_root / "project", trial_root / "reports"
    project.mkdir()
    reports.mkdir()
    started = time.monotonic()
    trial.update(status="running", started_at=now())
    save_state = lambda: save_json(run / "experiment.json", state)
    save_state()
    planner, descriptor, view = None, None, None
    try:
        if trial["condition"] in ("G", "P"):
            planner_cls = GenericPipeline if trial["condition"] == "G" else NativePlanning
            planner = planner_cls.new(trial_root / "planning", model="deepseek-flash", llm=llm,
                                      stage_models=state["stage_models"])
            trial["planning_state"] = str((planner.run / "run.json").relative_to(run))
            raw = run / "control/raw/inputs"
            if not planner.freeze_inputs(raw / "TASK.md", raw / "REQUIREMENTS.md", raw / "protocol.pdf"):
                trial["stop_reason"] = "planning_prepare_failed"
                return
            if (hashes(planner.run / "inputs") != state["raw"]["inputs"]
                    or hashes(planner.run / "documents") != state["raw"]["documents"]):
                raise ValueError("Independent planner's raw inputs/extraction differ from frozen inputs")
            if not planner.execute(until="specs"):
                trial["stop_reason"] = planner.state["stop_reason"]
                return
            trial["first_spec_publication"] = {"passed": True, "revision": 1, "created_at": now(),
                                                "wall_seconds": round(time.monotonic() - started, 3)}
            descriptor, view = _handoff(planner, run, trial_root, state, trial)
        model = llm if llm is not None else LLM(ModelConfig.from_record(state["model"]))
        cache = None
        for index in range(1 + state["limits"]["implementation_repairs"]):
            role = "code" if index == 0 else "repair"
            trial["implementation_repairs"] = index
            name = f"{index + 1:02d}_{role}"
            job_root = trial_root / "logs" / name
            job = {"name": name, "role": role, "responses": 0, "passed": False, "reason": "running",
                   "usage": {"requests": 0}, "started_at": now(), "spec_revision": descriptor["revision"] if descriptor else None}
            trial["jobs"].append(job)
            readonly = [project / h["path"] for h in descriptor["headers"]] if descriptor else []
            inputs = {"/reports": reports, **({"/specs": view} if descriptor else
                      {"/inputs": run / "control/raw/inputs", "/documents": run / "control/raw/documents"})}

            def gate():
                nonlocal cache
                signature = project_hashes(project)
                if cache is None or signature != cache["project_hashes"]:
                    consistency = check_delivery(project, state["requirement_ids"], descriptor, view)
                    cache = verify_project(project, reports, trial_root / "logs/verifier", consistency=consistency,
                                           runtime_contract=state["runtime_contract"], readonly_headers=readonly)
                    trial["gate_events"].append({"job": name, "passed": cache["passed"],
                        "report": str(Path(cache["report_path"]).relative_to(run)), "errors": cache["errors"],
                        "responses": sum(j["responses"] for j in trial["jobs"][:-1])
                                     + len(list((job_root / "api").glob("response_*.json"))),
                        "elapsed_seconds": round(time.monotonic() - started, 3)})
                    save_state()
                result = json.loads(json.dumps({k: cache[k] for k in ("passed", "errors", "builds", "phases")}))
                result["report"] = "/reports/" + str(Path(cache["report_path"]).relative_to(reports))
                for command in [*result["builds"].values(), *(p["command"] for p in result["phases"].values() if "command" in p)]:
                    output = command.pop("output", "")
                    if command["exit_code"] or command.get("timed_out"):
                        command["output"] = bounded_output(output, 2000)
                return result

            def progress(snapshot):
                job.update(snapshot)
                trial["elapsed_seconds"] = round(time.monotonic() - started, 3)
                save_state()

            prefix, task = coder_prompt(trial["condition"], (run / "control/coder.md").read_text(), descriptor)
            if index:
                task += "\nContinue this existing implementation. Fix the current development failures and implement any newly reviewed contracts:\n" + json.dumps(gate(), ensure_ascii=False)
            runtime = RQ2Runtime(project, inputs, job_root / "commands", gate, readonly=readonly, coder=True, trial=trial)
            job_started = time.monotonic()
            try:
                result = run_agent(model, runtime, prefix, task, state["limits"][role], job_root / "api", progress)
                job.update(result)
            except BaseException:
                job["reason"] = "controller_error"
                raise
            finally:
                job.update(finished_at=now(), wall_seconds=round(time.monotonic() - job_started, 3))
                if index == 0:
                    _initial_snapshot(run, trial_root, trial)
                save_state()
            if result["reason"] == "api_error":
                trial["stop_reason"] = "api_error"
                break
            if result.get("gap"):
                trial["spec_gap"] = result["gap"]
                if planner is None:
                    trial["stop_reason"] = "direct_input_gap"
                    break
                if trial["spec_repairs"] >= state["limits"]["spec_repairs"]:
                    trial["stop_reason"] = "spec_repair_limit"
                    break
                if index == state["limits"]["implementation_repairs"]:
                    trial["stop_reason"] = "implementation_repair_limit"
                    break
                repaired = _repair_specs(planner, run, trial_root, state, trial, runtime, result["gap"], save_state)
                if repaired is None:
                    trial["stop_reason"] = "spec_repair_failed"
                    break
                descriptor, view = repaired
                cache = None
                # Always use the next counted repair job to adjust to this revision.
                continue
            if gate()["passed"]:
                trial.update(generation_passed=True, stop_reason="development_passed")
                break
            trial["stop_reason"] = "implementation_repair_limit"
    finally:
        if planner:
            trial["planning_stages"] = planner.state["stages"]
        trial.update(status="completed" if trial["generation_passed"] else "failed", finished_at=now(),
                     elapsed_seconds=round(time.monotonic() - started, 3), project_hashes=project_hashes(project),
                     delivery_hashes=hashes(project))
        save_state()


def generate_experiment(run: Path, *, llm=None) -> dict:
    state = load_frozen(run)
    if state["evaluation_started"]:
        raise ValueError("Generation is frozen after independent evaluation starts")
    if state["generation_finished"]:
        raise ValueError("Generation already recorded; failed attempts must not be replaced")
    if llm is None and not os.environ.get(state["model"]["key_env"]):
        raise RuntimeError("Set " + state["model"]["key_env"] + " before generation")
    if llm is not None and llm.config.record() != state["model"]:
        raise ValueError("Injected model differs from frozen profile")
    started = time.monotonic()
    execution = {"mode": "serial", "workers": 1, "started_at": now()}
    state.setdefault("execution_invocations", []).append(execution)
    state["execution"] = execution
    for trial in state["trials"]:
        if trial["status"] != "pending":
            if trial["status"] == "running":
                trial.update(status="interrupted", stop_reason="controller_interrupted", generation_passed=False,
                             recorded_elapsed_seconds=trial.get("elapsed_seconds"), elapsed_seconds=None,
                             delivery_hashes=hashes(run / "trials" / trial["id"] / "project"))
            continue
        print("Generating " + trial["id"], flush=True)
        try:
            _run_trial(run, state, trial, llm=llm)
        except BaseException as exc:
            trial.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                         generation_passed=False, stop_reason="controller_error", error=f"{type(exc).__name__}: {exc}")
            trial["delivery_hashes"] = hashes(run / "trials" / trial["id"] / "project")
            save_json(run / "experiment.json", state)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                execution.update(finished_at=now(), wall_seconds=round(time.monotonic() - started, 3), interrupted=True)
                save_json(run / "experiment.json", state)
                raise
        save_json(run / "experiment.json", state)
        print(trial["id"] + ": " + trial["status"] + " (" + trial["stop_reason"] + ")", flush=True)
    state.update(generation_finished=True, generation_finished_at=now())
    execution.update(finished_at=now(), wall_seconds=round(time.monotonic() - started, 3), interrupted=False)
    save_json(run / "experiment.json", state)
    return state


def normalize_evaluation(report: dict, expected: list[str]) -> dict:
    report = json.loads(json.dumps(report))
    errors = report.setdefault("errors", [])
    for phase in ("normal", "sanitize"):
        raw = report.setdefault("phases", {}).get(phase, {})
        scenarios = raw.get("scenarios", [])
        ids = [s.get("id") for s in scenarios]
        complete = len(ids) == len(expected) and set(ids) == set(expected) and raw.get("required_count") == len(expected)
        if not complete:
            errors.append(phase + ": incomplete or mismatched frozen scenarios")
        build = report.get("builds", {}).get(phase, {})
        build_passed = build.get("exit_code") == 0 and not build.get("timed_out", True)
        if not build_passed:
            errors.append(phase + ": missing or failed build report")
        by_id = {s.get("id"): s for s in scenarios}
        raw["scenarios"] = [by_id.get(name, {"id": name, "status": "not_executed",
                                            "error": raw.get("reason", "missing_scenario")}) for name in expected]
        raw.update(required_count=len(expected), complete=complete)
        raw["passed"] = bool(complete and build_passed and raw.get("passed") and all(s.get("status") == "passed" for s in raw["scenarios"]))
        report["phases"][phase] = raw
    report["passed"] = bool(report.get("passed") and not errors and all(report["phases"][p]["passed"] for p in ("normal", "sanitize")))
    return report


def evaluate_experiment(run: Path) -> dict:
    state = load_frozen(run)
    if not state["generation_finished"] or any(t["status"] in ("running", "pending") for t in state["trials"]):
        raise ValueError("Finish all generation attempts before independent evaluation")
    for trial in state["trials"]:
        if hashes(run / "trials" / trial["id"] / "project") != trial["delivery_hashes"]:
            raise ValueError("Final delivery changed before evaluation: " + trial["id"])
        snapshot = trial["initial_snapshot"]
        if snapshot and hashes(run / snapshot["path"]) != snapshot["hashes"]:
            raise ValueError("Initial delivery changed before evaluation: " + trial["id"])
        for revision in trial["spec_revisions"]:
            if hashes(run / revision["view"]) != revision["hashes"]:
                raise ValueError("Published handoff changed before evaluation")
    state["evaluation_started"] = True
    save_json(run / "experiment.json", state)
    for trial in state["trials"]:
        trial_root = run / "trials" / trial["id"]
        if trial.get("planning_state"):
            path = run / trial["planning_state"]
            planning = read_json(path)
            planning["evaluation_started"] = True
            save_json(path, planning)
        snapshot = trial["initial_snapshot"]
        targets = [("initial_evaluation", run / snapshot["path"] if snapshot else None),
                   ("evaluation", trial_root / "project")]
        for key, project in targets:
            if trial[key] is not None:
                continue
            destination = trial_root / key
            started = time.monotonic()
            if project is None or not (project / "Makefile").is_file():
                raw = {"passed": False, "errors": ["No executable project; no Makefile"], "phases": {}}
            elif destination.exists():
                raw = {"passed": False, "errors": ["Previous evaluation was interrupted"], "phases": {}}
            else:
                try:
                    raw = evaluate_project(project, run / "control/runtime", run / "control/evaluator/mqtt_check.py", destination)
                except Exception as exc:
                    raw = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"], "phases": {}}
            raw["wall_seconds"] = round(time.monotonic() - started, 3)
            report = normalize_evaluation(raw, state["expected_scenarios"])
            expected_hashes = snapshot["hashes"] if key == "initial_evaluation" and snapshot else trial["delivery_hashes"]
            if project is not None and hashes(project) != expected_hashes:
                raise RuntimeError("Independent evaluation changed original delivery")
            name = key + "_report.json"
            save_json(trial_root / name, report)
            trial[key] = {"passed": report["passed"], "report": f"trials/{trial['id']}/{name}"}
            save_json(run / "experiment.json", state)
    state["evaluation_finished_at"] = now()
    save_json(run / "experiment.json", state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--protocol", choices=("mqtt",), default="mqtt")
    p.add_argument("--model", choices=("deepseek-flash",), default="deepseek-flash")
    p.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    p.add_argument("--repetitions", type=int, choices=(1,), default=1)
    for name in ("generate", "evaluate", "summarize"):
        p = commands.add_parser(name)
        p.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            state = prepare_experiment(args.out, conditions=tuple(args.conditions), repetitions=args.repetitions,
                                       protocol=args.protocol, model=args.model)
        else:
            run = args.run.resolve()
            with (run / ".controller.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == "generate":
                    state = generate_experiment(run)
                elif args.command == "evaluate":
                    state = evaluate_experiment(run)
                else:
                    from report import summarize_experiment
                    state = summarize_experiment(run, load_frozen(run))
        print(json.dumps({"command": args.command, "trials": len(state["trials"]),
                          "output": str(args.out if args.command == "prepare" else args.run)}, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
