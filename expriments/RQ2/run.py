#!/usr/bin/env python3
"""MQTT/CoAP RQ2 controller. Preparation and reporting never call a model."""

from __future__ import annotations

import argparse
import difflib
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema

from evaluation.mqtt_check import TEST_IDS
from evaluation.coap_check import TEST_IDS as COAP_TEST_IDS
from specforge.agent import run_agent
from specforge.documents import digest, hashes, prepare, read_json, save_json
from specforge.evaluation import evaluate_project
from specforge.llm import LLM, ModelConfig, total_usage
from specforge.pipeline import LIMITS, framework_hashes, now, project_hashes, verify_project
from specforge.specs import project_path, validate
from specforge.tools import ToolRuntime, bounded_output
from spec_views import CONDITIONS, make_view

SOURCE_RUN = ROOT / "runs/paper/round_03/mqtt_01"
SOURCE = SOURCE_RUN / "specs/r002"
SOURCE_MANIFEST_SHA256 = "8859611d0d3c14189ac52da28713d18c4990cb756880f32d3923567e7c58b954"
REPETITIONS = 3
SCHEDULE_SEED = 20261008
CONTRACT = {"binary_name": "mqtt_broker", "argv_contract": "./mqtt_broker <port>"}
PLANNING_SENTENCE = ("Public types, interfaces, ownership and processing paths must be planned\n"
                     "before implementing the project. ")
HISTORY_JOBS = ("01_facts", "02_design", "03_specs", "04_spec_review",
                "05_code", "06_spec_repair", "07_spec_review")
PROTOCOLS = {
    "mqtt": {"source_run": SOURCE_RUN, "revision": 2,
             "manifest_sha256": SOURCE_MANIFEST_SHA256, "contract": CONTRACT,
             "test_ids": TEST_IDS, "evaluator": "mqtt_check.py", "protocol_input": "protocol.pdf"},
    "coap": {"source_run": ROOT / "runs/paper/round_03/coap_01", "revision": 1,
             "manifest_sha256": "3efcb47897796ee2d421f500e1d7b490acff68a6ccff971c956c76908a38d41d",
             "contract": {"binary_name": "coap_server", "argv_contract": "./coap_server <port>"},
             "test_ids": COAP_TEST_IDS, "evaluator": "coap_check.py", "protocol_input": "protocol.txt"}}

DELIVERY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "files", "tests"],
    "properties": {
        "schema_version": {"const": 1},
        "files": {"type": "array", "minItems": 1, "uniqueItems": True,
                  "items": {"type": "string", "minLength": 1}},
        "tests": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "path", "requirement_ids"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
                "requirement_ids": {"type": "array", "uniqueItems": True,
                                    "items": {"type": "string", "minLength": 1}}}}}}}


def runtime_hashes() -> dict:
    result = framework_hashes()
    for name in ("run.py", "spec_views.py", "report.py", "coder.md", "README.md",
                 "tests/test_rq2.py", ".gitignore"):
        path = SUITE / name
        result[path.relative_to(ROOT).as_posix()] = digest(path)
    result["evaluation/mqtt_check.py"] = digest(ROOT / "evaluation/mqtt_check.py")
    result["evaluation/coap_check.py"] = digest(ROOT / "evaluation/coap_check.py")
    return result


def aggregate_usage(jobs: list[dict]) -> dict:
    unknown_call = any(j.get("reason") == "api_error" or j.get("usage_unknown") for j in jobs)
    usage = total_usage([j["usage"] for j in jobs] + ([{}] if unknown_call else []))
    usage["requests"] = sum(j["usage"].get("requests", 0) for j in jobs)
    if unknown_call:
        usage["elapsed_seconds"] = None
    usage["total_tokens"] = (usage["input_tokens"] + usage["output_tokens"]
                             if usage["input_tokens"] is not None and usage["output_tokens"] is not None else None)
    return usage


def historical_cost(state: dict) -> dict:
    jobs = {j["name"]: j for j in state["jobs"]}
    names = HISTORY_JOBS[:4] if state["spec_revision"] == 1 else HISTORY_JOBS
    selected = [jobs[name] for name in names]
    if not all(jobs[name]["passed"] for name in names if name != "05_code"):
        raise ValueError("Source specifications were not published after successful review")
    if state["spec_revision"] == 2 and jobs["05_code"]["reason"] != "spec_gap":
        raise ValueError("Historical r002 provenance lacks the expected coding feedback")
    absent_usage = {k: 0 for k in ("input_tokens", "output_tokens", "cache_hit_tokens",
                                 "cache_miss_tokens", "reasoning_tokens", "requests",
                                 "elapsed_seconds", "total_tokens")}
    return {
        "source_formation": aggregate_usage(selected),
        "initial_specs": aggregate_usage(selected[:4]),
        "coding_feedback": aggregate_usage(selected[4:5]) if selected[4:5] else absent_usage,
        "spec_repair_and_review": aggregate_usage(selected[5:]) if selected[5:] else absent_usage,
        "jobs": [{k: j[k] for k in ("name", "passed", "reason", "responses", "usage")} for j in selected],
        "initial_spec_wall_seconds": sum(state["stages"][s]["elapsed_seconds"]
                                         for s in ("prepare", "facts", "design", "specs")),
        "source_formation_wall_seconds": None,
        "wall_time_note": "Full source formation wall time is not separately recorded; summed model latency is not wall time.",
        "excluded_jobs": [j["name"] for j in state["jobs"] if j["name"] not in names],
        "actual_new_spend": False, "shared_source": True}


def preflight(protocol: str = "mqtt") -> dict:
    versions = {"python": sys.version, "python_executable": sys.executable}
    for name in ("openai", "jsonschema"):
        versions[name] = importlib.metadata.version(name)
    clients = ("coap-client-notls",) if protocol == "coap" else ("mosquitto_pub", "mosquitto_sub")
    documents = () if protocol == "coap" else ("pdftotext",)
    for name in ("gcc", "make", "bwrap", *documents, *clients):
        path = shutil.which(name)
        if path is None:
            raise RuntimeError("Required executable unavailable: " + name)
        flag = "-v" if name == "pdftotext" else "--help" if name.startswith("mosquitto_") else "--version"
        output = subprocess.run([path] if name == "coap-client-notls" else [path, flag],
                                capture_output=True, text=True, timeout=10)
        versions[name] = {"path": path, "sha256": digest(Path(path).resolve()),
                          "version": (output.stdout + output.stderr).splitlines()[:2]}
    with tempfile.TemporaryDirectory(prefix="rq2_preflight_") as directory:
        base = Path(directory)
        secret = base / "outside_marker"
        secret.write_text("not a task input")
        runtime = ToolRuntime(base / "work", {}, base / "logs", lambda: {"passed": False})
        command = "test ! -e " + str(secret) + " && test -d /proc && test -x /usr/bin/gcc"
        result = runtime.command(command, timeout=10)
        if result["exit_code"] != 0 or result["timed_out"]:
            raise RuntimeError("Isolation preflight failed: " + result["output"])
    return versions


def prepare_experiment(out: Path, *, repetitions: int = REPETITIONS,
                       conditions: tuple[str, ...] = CONDITIONS, protocol: str = "mqtt") -> dict:
    if repetitions not in (1, REPETITIONS):
        raise ValueError("Use one pilot repetition or the three planned repetitions")
    if not conditions or len(set(conditions)) != len(conditions) or not set(conditions) <= set(CONDITIONS):
        raise ValueError("Select distinct supported conditions")
    conditions = tuple(c for c in CONDITIONS if c in conditions)
    out = out.resolve()
    if out.exists():
        raise ValueError("Use a new experiment directory")
    if protocol not in PROTOCOLS:
        raise ValueError("Select MQTT or CoAP")
    profile = PROTOCOLS[protocol]
    source_run = profile["source_run"]
    source = source_run / "specs" / f"r{profile['revision']:03d}"
    contract = profile["contract"]
    started = time.monotonic()
    if digest(source / "bundle.json") != profile["manifest_sha256"]:
        raise ValueError("The approved publication manifest changed")
    source_state = read_json(source_run / "run.json")
    source_manifest = read_json(source / "bundle.json")
    if source_state["spec_revision"] != profile["revision"] or source_state["model"] != ModelConfig().record():
        raise ValueError("Source revision/model differs from the approved experiment")
    for name, expected in source_state["input_hashes"].items():
        if digest(source_run / "inputs" / name) != expected:
            raise ValueError("Historical input changed: " + name)
    for name, expected in source_state["stages"]["prepare"]["artifact_hashes"]["documents"].items():
        if digest(source_run / "documents" / name) != expected:
            raise ValueError("Historical extracted document changed: " + name)
    # Only the approved shared verifier extension may differ from the source framework.
    for name, expected in source_state["framework_hashes"].items():
        if name != "specforge/pipeline.py" and digest(ROOT / name) != expected:
            raise ValueError("Source framework differs outside the approved verifier extension: " + name)
    versions = preflight(protocol)
    for name in ("openai", "jsonschema"):
        if versions[name] != source_state["versions"][name]:
            raise ValueError("Pinned dependency version differs from source: " + name)
    source_validation = validate(source)
    if not source_validation["passed"]:
        raise ValueError("Invalid source publication: " + "; ".join(source_validation["errors"]))
    scope = read_json(source / "scope.json")
    requirements = re.findall(r"\b(R\d+)\s*:", (source_run / "inputs/REQUIREMENTS.md").read_text())
    if set(requirements) != {f"R{i:02d}" for i in range(1, 13)} or {r["id"] for r in scope["requirements"]} != set(requirements):
        raise ValueError(protocol + " requirements must remain R01–R12")
    if Path(scope["runtime_contract"]["binary_name"]).as_posix() != contract["binary_name"]:
        raise ValueError("Source startup contract differs from " + contract["argv_contract"])

    out.mkdir(parents=True)
    control = out / "control"
    control.mkdir()
    save_json(control / "source_validation.json", source_validation)
    save_json(control / "source_manifest.json", source_manifest)
    source_cost = historical_cost(source_state)
    save_json(control / "historical_cost.json", source_cost)
    direct = out / "views/direct"
    view_started = time.monotonic()
    prepare(direct, source_run / "inputs/TASK.md", source_run / "inputs/REQUIREMENTS.md",
            source_run / "inputs" / profile["protocol_input"])
    # Use the exact frozen extraction supplied to the historical Facts agent.
    shutil.rmtree(direct / "documents")
    (direct / "documents").mkdir()
    for name in source_state["stages"]["prepare"]["artifact_hashes"]["documents"]:
        shutil.copyfile(source_run / "documents" / name, direct / "documents" / name)
    task = direct / "inputs/TASK.md"
    original_task = task.read_text()
    if original_task.count(PLANNING_SENTENCE) != 1:
        raise ValueError("The approved Direct task planning sentence is not unique")
    direct_task = original_task.replace(PLANNING_SENTENCE, "", 1)
    task.write_text(direct_task, encoding="utf-8")
    (control / "direct_task.diff").write_text("".join(difflib.unified_diff(
        original_task.splitlines(keepends=True), direct_task.splitlines(keepends=True),
        fromfile="historical/TASK.md", tofile="direct/TASK.md")), encoding="utf-8")
    views = {"direct": {"hashes": hashes(direct), "planned_sources": [], "headers": [],
                        "bytes": sum(p.stat().st_size for p in direct.rglob("*") if p.is_file()),
                        "elapsed_seconds": round(time.monotonic() - view_started, 3)}}
    for condition in conditions:
        if condition == "direct":
            continue
        view_started = time.monotonic()
        transformation = make_view(source, out / "views" / condition, condition)
        transformation["elapsed_seconds"] = round(time.monotonic() - view_started, 3)
        save_json(control / (condition + "_transform.json"), transformation)
        views[condition] = {k: transformation[k] for k in ("hashes", "planned_sources", "headers", "bytes", "elapsed_seconds")}
    shutil.copyfile(SUITE / "coder.md", control / "coder.md")
    save_json(control / "delivery_schema.json", DELIVERY_SCHEMA)
    evaluator = control / "evaluator" / profile["evaluator"]
    evaluator.parent.mkdir()
    shutil.copyfile(ROOT / "evaluation" / profile["evaluator"], evaluator)
    save_json(control / "runtime/bundle.json", {"runtime_contract": contract})

    rng = random.Random(SCHEDULE_SEED)
    trials = []
    for repeat in range(1, repetitions + 1):
        block = list(conditions)
        rng.shuffle(block)
        for condition in block:
            trials.append({"id": f"r{repeat:02d}_{condition}", "condition": condition, "repeat": repeat,
                           "status": "pending", "generation_passed": False, "jobs": [],
                           "implementation_repairs": 0, "evaluation": None,
                           "initial_snapshot": None, "initial_evaluation": None,
                           "repair_events": []})
    record = {
        "schema_version": 1, "protocol": protocol, "created_at": now(), "model": ModelConfig().record(),
        "source": str(source), "source_manifest_sha256": profile["manifest_sha256"],
        "source_framework_hashes": source_state["framework_hashes"], "input_hashes": source_state["input_hashes"],
        "framework_hashes": runtime_hashes(), "versions": versions, "views": views,
        "requirement_ids": sorted(set(requirements)), "runtime_contract": contract,
        "expected_scenarios": list(profile["test_ids"]), "repetitions": repetitions, "schedule_seed": SCHEDULE_SEED,
        "conditions": list(conditions),
        "initial_policy": "complete neutral delivery, before commands or source revision; independent evaluation after generation",
        "limits": {"code": LIMITS["code"], "repair": LIMITS["repair"], "implementation_repairs": 3},
        "trials": trials, "evaluation_started": False, "generation_finished": False,
        "prepare_elapsed_seconds": round(time.monotonic() - started, 3),
        "control_hashes": hashes(control)}
    save_json(out / "experiment.json", record)
    return record


def load_frozen(run: Path) -> dict:
    state = read_json(run / "experiment.json")
    if state["framework_hashes"] != runtime_hashes():
        raise ValueError("Framework or suite changed after preparation; create a new experiment")
    if state["control_hashes"] != hashes(run / "control"):
        raise ValueError("Frozen controller inputs changed")
    for condition, view in state["views"].items():
        if view["hashes"] != hashes(run / "views" / condition):
            raise ValueError("Frozen input view changed: " + condition)
    return state


def check_delivery(project: Path, requirement_ids: list[str], view: dict, view_root: Path) -> dict:
    errors = []
    try:
        delivery = read_json(project / "delivery.json")
        errors += [f"delivery.json: {'/'.join(map(str, e.absolute_path))}: {e.message}"
                   for e in jsonschema.Draft202012Validator(DELIVERY_SCHEMA).iter_errors(delivery)]
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing or invalid delivery.json: {exc}"], "test_count": 0}
    if errors:
        return {"passed": False, "errors": errors, "test_count": 0}
    listed = set(delivery["files"])
    for name in listed:
        path = (project / name).resolve()
        if not project_path(name) or not path.is_relative_to(project.resolve()) or not path.is_file():
            errors.append("Missing or unsafe delivery file: " + name)
    for name in ("Makefile", "README.md"):
        path = project / name
        if name not in listed or not path.is_file() or not path.read_text().strip():
            errors.append("Missing nonempty delivery document: " + name)
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (".c", ".h", ".py", ".sh") and path.relative_to(project).as_posix() not in listed:
            errors.append("Unlisted source or development test: " + path.relative_to(project).as_posix())
    if len([p for p in listed if p.endswith(".c") and (project / p).is_file()]) < 2:
        errors.append("The independent project must have multiple C source files")
    ids = [t["id"] for t in delivery["tests"]]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate development test ID")
    covered = set()
    for test in delivery["tests"]:
        if test["path"] not in listed:
            errors.append("Test file absent from inventory: " + test["path"])
        covered.update(test["requirement_ids"])
    if covered != set(requirement_ids):
        errors.append(f"Requirement coverage differs: missing {sorted(set(requirement_ids) - covered)}, "
                      f"unknown {sorted(covered - set(requirement_ids))}")
    for name in view["planned_sources"]:
        if name not in listed:
            errors.append("Planned source missing: " + name)
    for header in view["headers"]:
        path = project / header["path"]
        if header["path"] not in listed or not path.is_file() or digest(path) != digest(view_root / header["artifact"]):
            errors.append("Public header differs from frozen ABI: " + header["path"])
    return {"passed": not errors, "errors": errors, "test_count": len(ids)}


def coder_prompt(condition: str, frozen_prefix: str) -> tuple[str, str]:
    prefix = frozen_prefix + "\nExact neutral delivery.json schema:\n" + json.dumps(DELIVERY_SCHEMA, separators=(",", ":"))
    if condition == "direct":
        task = ("Implement the entire project from /inputs/TASK.md, /inputs/REQUIREMENTS.md and the "
                "protocol text in /documents. Start with /documents/SUMMARY.md and index.json. "
                "There is no intermediate planning or specification stage; do not create separate "
                "design/specification artifacts. WORKLOG.md is only a progress/continuation checkpoint.")
        view_note = "/inputs and /documents: read-only task inputs."
    elif condition == "generic":
        task = ("Implement the entire project from /specs/SUMMARY.md, scope.md, project.spec and "
                "the relevant file/function .spec documents. They use RELY/GUARANTEE/SPECIFICATION. "
                "Read the relevant function and its dependencies on demand.")
        view_note = "/specs: read-only engineering specifications."
    else:
        task = ("Implement the entire project from /specs/SUMMARY.md, bundle.json, scope.json "
                "and the relevant file/function JSON specifications. Read relevant functions and "
                "dependencies on demand, using json_pointers only for fields that actually exist.")
        view_note = "/specs: read-only engineering specifications."
    prefix += "\nStage view:\n" + view_note + "\n/work: project outputs; /reports: current development reports; /logs: command logs.\n"
    return prefix, task + "\nSave implementation, Makefile, README, development tests and delivery.json; call check."


class RQ2Runtime(ToolRuntime):
    """Freeze the first complete draft, then observe native repair operations."""

    def __init__(self, *args, trial: dict, **kwargs):
        super().__init__(*args, **kwargs)
        self.trial = trial

    def tool_definitions(self) -> list[dict]:
        definitions = super().tool_definitions()
        if self.trial["initial_snapshot"] is None:
            definitions = [d for d in definitions if d["function"]["name"] not in ("edit_file", "run_command")]
            for definition in definitions:
                if definition["function"]["name"] == "write_file":
                    definition["function"]["description"] += " Initial draft: create each implementation/test/Makefile once; submit the complete delivery with check before revising."
        return definitions

    def dispatch(self, name: str, args: dict) -> dict:
        pending = self.trial["initial_snapshot"] is None
        if pending:
            if name in ("edit_file", "run_command"):
                return {"error": "Initial draft: submit the complete delivery with check before commands or revisions."}
            if name == "write_file":
                try:
                    path = self.resolve(args["path"], write=True)
                except ValueError as exc:
                    return {"error": str(exc)}
                if path.exists() and path.name not in ("WORKLOG.md", "README.md", "delivery.json"):
                    return {"error": "Initial draft file already exists; submit the complete delivery with check before revising it."}
        observe = not pending and name in ("write_file", "edit_file", "run_command")
        before = project_hashes(self.work) if observe else {}
        result = super().dispatch(name, args)
        if observe:
            after = project_hashes(self.work)
            inventory = read_json(self.work.parent / "initial_project/delivery.json")
            test_paths = {t["path"] for t in inventory["tests"]}
            test_dirs = {str(Path(p).parent) for p in test_paths if Path(p).parent != Path(".")}
            changed = []
            for path in sorted(before.keys() | after.keys()):
                if before.get(path) == after.get(path):
                    continue
                category = ("build" if Path(path).name == "Makefile" else
                            "test" if path in test_paths or any(Path(path).is_relative_to(d) for d in test_dirs) else
                            "source" if Path(path).suffix in (".c", ".h", ".py", ".sh") else None)
                if category is not None and (path in inventory["files"] or path in before):
                    changed.append({"path": path, "category": category,
                                    "before_sha256": before.get(path), "after_sha256": after.get(path)})
            if changed:
                self.trial["repair_events"].append({
                    "job": self.logs.parent.name,
                    "response": len(list((self.logs.parent / "api").glob("response_*.json"))),
                    "tool": name, "files": changed})
        return result


def _run_trial(run: Path, state: dict, trial: dict, *, save_state=None) -> None:
    if save_state is None:
        save_state = lambda: save_json(run / "experiment.json", state)
    trial_root = run / "trials" / trial["id"]
    trial_root.mkdir(parents=True)
    project = trial_root / "project"
    project.mkdir()
    reports = trial_root / "reports"
    reports.mkdir()
    view_root = run / "views" / trial["condition"]
    view = state["views"][trial["condition"]]
    readonly = []
    for header in view["headers"]:
        path = project / header["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(view_root / header["artifact"], path)
        readonly.append(path)
    inputs = ({"/inputs": view_root / "inputs", "/documents": view_root / "documents"}
              if trial["condition"] == "direct" else {"/specs": view_root})
    inputs["/reports"] = reports
    prefix, initial_task = coder_prompt(trial["condition"], (run / "control/coder.md").read_text())
    prefix += ("\nInitial-draft measurement overrides the build/self-test instructions until submission: "
               "first save the entire implementation, Makefile, README, development tests and delivery.json. "
               "No commands, compilation, self-tests or edits to existing code are available before this draft is frozen. "
               "Write each source/test/Makefile once; WORKLOG.md, README.md and delivery.json may be updated. "
               "Use read_file/search/list_files to inspect inputs. Call check once all neutral delivery files exist. "
               "Incomplete checkpoints do not run builds. The first complete neutral delivery is copied privately "
               "before the controller's first build; then commands and edit_file become available for ordinary "
               "development checks and repairs. Independent behavior evaluation occurs only after all generation "
               "ends and never provides feedback. Keep using the same overall response budget.")
    llm = LLM()
    started = time.monotonic()
    trial.update(status="running", started_at=now(), gate_events=[], stop_reason=None)
    save_state()
    cache = None
    for index in range(4):
        role = "code" if index == 0 else "repair"
        job_root = trial_root / "logs" / f"{index + 1:02d}_{role}"
        job = {"name": f"{index + 1:02d}_{role}", "responses": 0,
               "passed": False, "reason": "running", "usage": {"requests": 0}}
        trial["jobs"].append(job)
        trial["implementation_repairs"] = index
        previous_responses = sum(j.get("responses", 0) for j in trial["jobs"][:-1])

        def gate():
            nonlocal cache
            if trial["initial_snapshot"] is None:
                readiness = check_delivery(project, state["requirement_ids"], view, view_root)
                if not readiness["passed"]:
                    return {"passed": False, "stage": "initial_draft", "errors": readiness["errors"],
                            "builds": {}, "phases": {}, "note": "Complete the neutral delivery before building or revising code."}
                snapshot = trial_root / "initial_project"
                shutil.copytree(project, snapshot)
                trial["initial_snapshot"] = {
                    "path": str(snapshot.relative_to(run)), "hashes": hashes(snapshot),
                    "job": job["name"],
                    "responses": previous_responses + len(list((job_root / "api").glob("response_*.json"))),
                    "elapsed_seconds": round(time.monotonic() - started, 3), "created_at": now()}
                save_state()
            signature = project_hashes(project)
            if cache is None or cache["project_hashes"] != signature:
                consistency = check_delivery(project, state["requirement_ids"], view, view_root)
                cache = verify_project(project, reports, trial_root / "logs/verifier",
                                       consistency=consistency, runtime_contract=state["runtime_contract"],
                                       readonly_headers=readonly)
                responses = previous_responses + len(list((job_root / "api").glob("response_*.json")))
                elapsed = round(time.monotonic() - started, 3)
                event = {"job": job["name"], "responses": responses, "elapsed_seconds": elapsed,
                         "passed": cache["passed"], "report": str(Path(cache["report_path"]).relative_to(trial_root)),
                         "errors": cache["errors"],
                         "builds": {k: {"exit_code": v["exit_code"], "timed_out": v["timed_out"]}
                                    for k, v in cache["builds"].items()}}
                if not trial["gate_events"]:
                    trial["initial_snapshot"]["development_report"] = event["report"]
                trial["gate_events"].append(event)
                normal = cache["builds"].get("normal", {})
                if normal.get("exit_code") == 0 and not normal.get("timed_out") and "first_normal_build" not in trial:
                    trial["first_normal_build"] = {"responses": responses, "elapsed_seconds": elapsed}
                save_state()
            result = {k: cache[k] for k in ("passed", "errors", "builds", "phases")}
            result = json.loads(json.dumps(result))
            result["report"] = "/reports/" + Path(cache["report_path"]).relative_to(reports).as_posix()
            for command in [*result["builds"].values(), *(p["command"] for p in result["phases"].values() if "command" in p)]:
                command["output"] = bounded_output(command.get("output", ""), 2000) if command["exit_code"] else ""
            return result

        def progress(snapshot):
            job.update(snapshot)
            trial["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save_state()

        task = initial_task
        if index:
            task += "\nContinue the same project. Fix the current development failures:\n" + json.dumps(gate(), ensure_ascii=False)
        runtime = RQ2Runtime(project, inputs, job_root / "commands", gate,
                             readonly=readonly, coder=True, trial=trial)
        job_started = time.monotonic()
        try:
            result = run_agent(llm, runtime, prefix, task, state["limits"][role], job_root / "api", progress)
        except BaseException:
            responses = [read_json(p) for p in sorted((job_root / "api").glob("response_*.json"))]
            job.update(passed=False, reason="controller_error", responses=len(responses),
                       usage=total_usage([r["usage"] for r in responses]),
                       usage_unknown=len(list((job_root / "api").glob("request_*.json"))) > len(responses))
            raise
        finally:
            job["wall_seconds"] = round(time.monotonic() - job_started, 3)
        job.update(result)
        if result.get("gap"):
            trial["spec_gap"] = result["gap"]
            trial["stop_reason"] = "spec_gap"
            break
        if result["reason"] == "api_error":
            trial["stop_reason"] = result["reason"]
            break
        if gate()["passed"]:
            trial["generation_passed"] = True
            trial["stop_reason"] = "development_passed"
            break
        if trial["initial_snapshot"] is None:
            trial["stop_reason"] = "initial_incomplete"
            break
        trial["stop_reason"] = "implementation_repair_limit"
    trial.update(status="completed" if trial["generation_passed"] else "failed", finished_at=now(),
                 elapsed_seconds=round(time.monotonic() - started, 3), project_hashes=project_hashes(project),
                 delivery_hashes=hashes(project))


def _generate_trial(run: Path, state: dict, trial: dict, save_state=None) -> None:
    print("Generating " + trial["id"], flush=True)
    try:
        if save_state is None:
            _run_trial(run, state, trial)
        else:
            _run_trial(run, state, trial, save_state=save_state)
    except BaseException as exc:
        trial.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                     generation_passed=False, stop_reason="controller_error",
                     error=f"{type(exc).__name__}: {exc}", finished_at=now())
        project = run / "trials" / trial["id"] / "project"
        trial["delivery_hashes"] = hashes(project)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            (save_state or (lambda: save_json(run / "experiment.json", state)))()
            raise
    (save_state or (lambda: save_json(run / "experiment.json", state)))()
    print(trial["id"] + ": " + trial["status"] + " (" + trial["stop_reason"] + ")", flush=True)


def generate_experiment(run: Path, *, workers: int = 1) -> dict:
    if not 1 <= workers <= len(CONDITIONS):
        raise ValueError("Generation workers must be between one and five")
    state = load_frozen(run)
    if state["evaluation_started"]:
        raise ValueError("Generation is frozen after independent evaluation starts")
    if state["generation_finished"]:
        raise ValueError("Generation already recorded; failed trials must not be replaced")
    if not os.environ.get(state["model"]["key_env"]):
        raise RuntimeError("Set " + state["model"]["key_env"] + " before generation")
    started = time.monotonic()
    state["execution"] = {"workers": workers, "mode": "parallel" if workers > 1 else "serial", "started_at": now()}
    for trial in state["trials"]:
        if trial["status"] == "running":
            trial.update(status="interrupted", stop_reason="controller_interrupted")
            project = run / "trials" / trial["id"] / "project"
            trial["delivery_hashes"] = hashes(project)
    save_json(run / "experiment.json", state)
    pending = [t for t in state["trials"] if t["status"] == "pending"]
    if workers == 1:
        for trial in pending:
            _generate_trial(run, state, trial)
    else:
        lock = Lock()
        # Each worker owns its mutable state; only the controller snapshot is shared.
        def execute(trial_id):
            with lock:
                local_state = json.loads(json.dumps(state))
            local_trial = next(t for t in local_state["trials"] if t["id"] == trial_id)
            def persist():
                with lock:
                    shared = next(t for t in state["trials"] if t["id"] == trial_id)
                    shared.update(json.loads(json.dumps(local_trial)))
                    save_json(run / "experiment.json", state)
            _generate_trial(run, local_state, local_trial, persist)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(execute, [t["id"] for t in pending]))
    state["generation_finished"] = True
    state["generation_finished_at"] = now()
    state["execution"]["wall_seconds"] = round(time.monotonic() - started, 3)
    save_json(run / "experiment.json", state)
    return state


def normalize_evaluation(report: dict, expected: list[str]) -> dict:
    """Require the frozen scenario identities, not a report's self-declared count."""
    report = json.loads(json.dumps(report))
    errors = report.setdefault("errors", [])
    for phase in ("normal", "sanitize"):
        raw = report.setdefault("phases", {}).get(phase, {})
        scenarios = raw.get("scenarios", [])
        ids = [s.get("id") for s in scenarios]
        complete = len(ids) == len(expected) and set(ids) == set(expected) and raw.get("required_count") == len(expected)
        if not complete:
            errors.append(phase + ": incomplete or mismatched frozen protocol scenarios")
        build = report.get("builds", {}).get(phase, {})
        build_passed = build.get("exit_code") == 0 and not build.get("timed_out", True)
        if not build_passed:
            errors.append(phase + ": missing or failed build report")
        by_id = {s.get("id"): s for s in scenarios}
        raw["scenarios"] = [by_id.get(name, {"id": name, "status": "not_executed",
                                           "error": raw.get("reason", "missing_scenario")}) for name in expected]
        raw["required_count"] = len(expected)
        raw["complete"] = complete
        raw["passed"] = bool(complete and build_passed and raw.get("passed") and
                             all(s["status"] == "passed" for s in raw["scenarios"]))
        report["phases"][phase] = raw
    report["passed"] = bool(report.get("passed") and not errors and
                            all(report["phases"][p]["passed"] for p in ("normal", "sanitize")))
    return report


def evaluate_experiment(run: Path) -> dict:
    state = load_frozen(run)
    if not state["generation_finished"] or any(t["status"] in ("running", "pending") for t in state["trials"]):
        raise ValueError("Finish all generation attempts before independent evaluation")
    for trial in state["trials"]:
        project = run / "trials" / trial["id"] / "project"
        if hashes(project) != trial["delivery_hashes"]:
            raise ValueError("Delivery changed before evaluation: " + trial["id"])
        if trial.get("initial_snapshot"):
            snapshot = trial["initial_snapshot"]
            if hashes(run / snapshot["path"]) != snapshot["hashes"]:
                raise ValueError("Initial delivery changed before evaluation: " + trial["id"])
    state["evaluation_started"] = True
    save_json(run / "experiment.json", state)
    for trial in state["trials"]:
        trial_root = run / "trials" / trial["id"]
        snapshot = trial.get("initial_snapshot")
        targets = [("initial_evaluation", "initial", run / snapshot["path"] if snapshot else None,
                    snapshot["hashes"] if snapshot else None),
                   ("evaluation", "final", trial_root / "project", trial["delivery_hashes"])]
        for key, version, project, expected_hashes in targets:
            if trial.get(key) is not None:
                continue
            destination = trial_root / ("initial_evaluation" if version == "initial" else "evaluation")
            started = time.monotonic()
            if project is None:
                raw = {"passed": False, "errors": ["No complete initial delivery was submitted"], "phases": {}}
            elif destination.exists():
                # An interrupted evaluator is evidence; do not silently run it again.
                raw = {"passed": False, "errors": ["Previous evaluation was interrupted"], "phases": {}}
            else:
                try:
                    raw = evaluate_project(project, run / "control/runtime",
                                           run / "control/evaluator" / PROTOCOLS[state["protocol"]]["evaluator"], destination)
                except Exception as exc:
                    raw = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"], "phases": {}}
            raw["wall_seconds"] = round(time.monotonic() - started, 3)
            report = normalize_evaluation(raw, state["expected_scenarios"])
            if project is not None and hashes(project) != expected_hashes:
                raise RuntimeError("Independent evaluation changed " + version + " delivery: " + trial["id"])
            name = "initial_evaluation_report.json" if version == "initial" else "evaluation_report.json"
            save_json(trial_root / name, report)
            trial[key] = {"passed": report["passed"], "report": f"trials/{trial['id']}/{name}"}
            save_json(run / "experiment.json", state)
    state["evaluation_finished_at"] = now()
    save_json(run / "experiment.json", state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--out", required=True, type=Path)
    preparation.add_argument("--protocol", choices=PROTOCOLS, default="mqtt")
    preparation.add_argument("--repetitions", type=int, choices=(1, REPETITIONS), default=REPETITIONS)
    preparation.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    for name in ("generate", "evaluate", "summarize"):
        command = commands.add_parser(name)
        command.add_argument("--run", required=True, type=Path)
        if name == "generate":
            command.add_argument("--workers", type=int, choices=range(1, len(CONDITIONS) + 1), default=1)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            state = prepare_experiment(args.out, repetitions=args.repetitions, conditions=tuple(args.conditions),
                                       protocol=args.protocol)
        else:
            run = args.run.resolve()
            # Outside frozen control inputs: never mounted in an agent filesystem.
            with (run / ".controller.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == "generate":
                    state = generate_experiment(run, workers=args.workers)
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
