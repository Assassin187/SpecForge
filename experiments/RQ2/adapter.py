"""NoSpec adaptation of the existing SpecForge controller and development gates."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

import jsonschema

from specforge.agent import run_agent
from specforge.documents import ROOT, digest, hashes, read_json, save_json
from specforge.llm import LLM
from specforge.pipeline import Pipeline, framework_hashes, now, project_hashes, verify_project
from specforge.specs import project_path
from specforge.tools import ToolRuntime, bounded_output

HERE = Path(__file__).resolve().parent
PIPELINE = "nospec-specforge"
STAGES = ("prepare", "code", "verify")
LIMITS = {"code": 300, "repair": 40, "max_repairs": 3}

# These are the explicit startup contracts in the unchanged case TASK files.
# No protocol behavior, source layout or implementation interfaces are supplied.
CASES = {
    name: {
        "task": ROOT / "cases" / (name + "_min") / "TASK.md",
        "requirements": ROOT / "cases" / (name + "_min") / "REQUIREMENTS.md",
        "protocol": ROOT / "cases" / (name + "_min") / "spec" / protocol,
        "runtime_contract": {"binary_name": binary, "argv_contract": argv},
        "evaluator": ROOT / "evaluation" / (name + "_check.py"),
    }
    for name, protocol, binary, argv in (
        ("mqtt", "mqtt-v3.1.1-os.pdf", "mqtt_broker", "./mqtt_broker <port>"),
        ("coap", "rfc7252.txt", "coap_server", "./coap_server <port>"),
        ("http11", "rfc9110-rfc9112.txt", "http_server", "./http_server <port>"),
        ("smtp", "rfc5321.txt", "smtp_server", "./smtp_server <port> <mail-dir>"),
    )
}

# read_json returns an independent object; the original schema stays unchanged.
DELIVERY_SCHEMA = read_json(ROOT / "schemas/artifacts.schema.json")["$defs"]["delivery"]
_test_schema = DELIVERY_SCHEMA["properties"]["tests"]["items"]
del _test_schema["properties"]["spec_test_refs"]
_test_schema["required"].remove("spec_test_refs")
DELIVERY_VALIDATOR = jsonschema.Draft202012Validator(DELIVERY_SCHEMA)


def adapter_hashes() -> dict:
    return {name: digest(HERE / name) for name in ("adapter.py", "run.py", "coder.md")}


def delivery_checks(project: Path, requirements: Path) -> dict:
    """Keep the original delivery checks, replacing only Spec-based coverage."""
    errors = []
    try:
        delivery = read_json(project / "delivery.json")
        errors += [f"delivery: {'/'.join(map(str, e.absolute_path))}: {e.message}"
                   for e in DELIVERY_VALIDATOR.iter_errors(delivery)]
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"Missing or invalid delivery.json: {exc}"], "test_count": 0}
    if errors:
        return {"passed": False, "errors": errors, "test_count": 0}
    # The original function always validates against its Spec-dependent schema,
    # so its inventory and requirement checks must be adapted locally.
    listed = set(delivery["files"])
    for path in listed:
        actual = (project / path).resolve()
        if not project_path(path) or not actual.is_relative_to(project.resolve()) or not actual.is_file():
            errors.append(f"Missing or unsafe delivery file: {path}")
    for name in ("Makefile", "README.md"):
        if name not in listed or not (project / name).is_file() or not (project / name).read_text().strip():
            errors.append(f"Missing nonempty delivery document: {name}")
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (".c", ".h", ".py", ".sh") and path.relative_to(project).as_posix() not in listed:
            errors.append(f"Unlisted source or development test: {path.relative_to(project)}")
    ids = [test["id"] for test in delivery["tests"]]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate development test ID")
    expected = set(re.findall(r"\b(R\d+)\s*:", requirements.read_text()))
    covered = set()
    for test in delivery["tests"]:
        if test["path"] not in listed:
            errors.append(f"Test file absent from delivery inventory: {test['path']}")
        if set(test["requirement_ids"]) - expected:
            errors.append(f"Unknown test requirements: {test['id']}")
        covered.update(test["requirement_ids"])
    if covered != expected:
        errors.append(f"Development tests must cover input requirements: missing {sorted(expected - covered)}")
    return {"passed": not errors, "errors": errors, "test_count": len(ids)}


class NoSpecRuntime(ToolRuntime):
    def tool_definitions(self) -> list[dict]:
        return [tool for tool in super().tool_definitions()
                if tool["function"]["name"] != "report_spec_gap"]


class NoSpecPipeline(Pipeline):
    @classmethod
    def new(cls, run: Path, *, case: str, model: str = "deepseek-flash", llm: LLM | None = None):
        contract = CASES[case]["runtime_contract"]
        pipeline = super().new(run, model=model, llm=llm)
        pipeline.state.update(schema_version=1, pipeline=PIPELINE, case=case,
                              runtime_contract=dict(contract), limits=dict(LIMITS), adapter_hashes=adapter_hashes(),
                              removed_steps=["facts", "design", "specs", "spec_review", "spec_repair"])
        for stage in ("facts", "design", "specs"):
            pipeline.state["stages"][stage] = {"status": "skipped", "reason": "no_spec_ablation"}
        for field in ("spec_revision", "spec_repairs", "spec_only"):
            del pipeline.state[field]
        for directory in ("facts", "design_work", "specs", "snapshots"):
            (pipeline.run / directory).rmdir()
        pipeline.save()
        return pipeline

    def job(self, role: str, task: str):
        work = self.run / "project"
        previous = self.state["jobs"][-1] if self.state["jobs"] else None
        if previous and previous["name"].endswith("_" + role) and not previous.get("passed") and (
                role != "repair" or previous.get("repair_round") == self.state["implementation_repairs"]):
            record = previous
            name = record["name"]
        else:
            name = f"{len(self.state['jobs']) + 1:02d}_{role}"
            record = {"name": name, "passed": False, "reason": "running", "usage": {"requests": 0}, "responses": 0,
                      "work": "project", "repair_round": self.state["implementation_repairs"]}
            self.state["jobs"].append(record)
            self.save()
        inputs = {"/inputs": self.run / "inputs", "/documents": self.run / "documents", "/reports": self.run / "reports"}
        runtime = NoSpecRuntime(work, inputs, self.run / "logs" / name / "commands", self.verify_gate, coder=True)
        prefix = (HERE / "coder.md").read_text()
        prefix += "\nExact delivery.json schema:\n" + json.dumps(DELIVERY_SCHEMA, separators=(",", ":"))
        prefix += "\nStage filesystem view:\n" + "\n".join(f"{alias}: read-only inputs" for alias in inputs)
        prefix += "\n/work: outputs; /logs: command logs (read-only).\n"

        def progress(snapshot):
            record.update(snapshot)
            record["working_hashes"] = project_hashes(work)
            self.save()

        result = run_agent(self.model(), runtime, prefix, task, self.state["limits"][role],
                           self.run / "logs" / name / "api", progress)
        record.update(result)
        self.save()
        return result

    def verify_gate(self):
        signature = project_hashes(self.run / "project")
        if self._verification_cache and signature == self._verification_cache["project_hashes"]:
            result = self._verification_cache
        else:
            result = verify_project(self.run / "project", self.run / "reports", self.run / "logs/verifier",
                                    consistency=delivery_checks(self.run / "project", self.run / "inputs/REQUIREMENTS.md"),
                                    runtime_contract=self.state["runtime_contract"])
            self._verification_cache = result
        # Same compact feedback as Pipeline.verify_gate; full evidence remains on disk.
        builds = {name: dict(command) for name, command in result.get("builds", {}).items()}
        phases = {name: {**phase, **({"command": dict(phase["command"])} if "command" in phase else {})}
                  for name, phase in result["phases"].items()}
        commands = [(command["exit_code"] == 0 and not command.get("timed_out"), command) for command in builds.values()]
        commands += [(phase["passed"], phase["command"]) for phase in phases.values() if "command" in phase]
        for passed, command in commands:
            output = command.pop("output", "")
            if not passed:
                command["output"] = bounded_output(output, 2000)
                command["truncated"] = command.get("truncated", False) or len(output) > 2000
        return {"passed": result["passed"], "errors": result["errors"],
                "report": "/reports/" + str(Path(result["report_path"]).relative_to(self.run / "reports")),
                "builds": builds, "phases": phases}

    def do_code(self):
        job_count = len([j for j in self.state["jobs"] if j["name"].endswith(("_code", "_repair"))])
        while True:
            previous = self.state["jobs"][-1] if self.state["jobs"] else {}
            pending_role = previous.get("name", "").split("_", 1)[-1] if previous.get("reason") in ("running", "api_error") else None
            maximum = self.state["limits"]["max_repairs"]
            if (job_count and self.state["implementation_repairs"] >= maximum
                    and previous.get("repair_round", -1) >= maximum and pending_role not in ("code", "repair")):
                return {"passed": False, "reason": "implementation_repair_limit", "verification": self.verify_gate()}
            task = ("Implement the whole project directly from /inputs/TASK.md, /inputs/REQUIREMENTS.md and the "
                    "original protocol text in /documents, including Makefile, README, development tests and "
                    "delivery.json. Build, self-test and pass the development check.")
            if job_count:
                task += "\nContinue the existing project from disk. Development failure evidence:\n" + json.dumps(self.verify_gate(), ensure_ascii=False)
            role = pending_role if pending_role in ("code", "repair") else "code" if not job_count else "repair"
            result = self.job(role, task)
            job_count += 1
            if result["reason"] == "api_error":
                return result
            gate = self.verify_gate()
            if gate["passed"]:
                self.state["project_hashes"] = project_hashes(self.run / "project")
                return {"passed": True, "verification": gate}
            if self.state["implementation_repairs"] >= maximum:
                return {"passed": False, "reason": "implementation_repair_limit", "verification": gate}
            self.state["implementation_repairs"] += 1
            self.save()

    def do_verify(self):
        result = self.verify_gate()
        if result["passed"]:
            self.state["project_hashes"] = project_hashes(self.run / "project")
            self.state["binary_sha256"] = digest(self.run / "project" / self.state["runtime_contract"]["binary_name"])
            self.state["generation_passed"] = True
        return result

    def execute(self, until: str = "verify") -> bool:
        self.state["stop_reason"] = None
        operations = {"code": self.do_code, "verify": self.do_verify}
        for stage in STAGES:
            if self.state["stages"][stage]["status"] != "passed":
                if stage == "prepare":
                    raise ValueError("Inputs must be prepared before execute")
                if not self.stage(stage, operations[stage]):
                    return False
            if stage == until:
                self.state["stop_reason"] = "complete" if until == "verify" else f"until_{until}"
                self.save()
                return True
        return True

    def resume(self) -> bool:
        if self.state.get("schema_version") != 1 or self.state.get("pipeline") != PIPELINE:
            raise ValueError("Resume requires a NoSpec-SpecForge run")
        if self.state["evaluation_started"]:
            raise ValueError("Generation is frozen after independent evaluation starts")
        if self.state["framework_hashes"] != framework_hashes() or self.state["adapter_hashes"] != adapter_hashes():
            self.state.update(manual_edits=True, resumed=True, fresh=False, stop_reason="framework_changed")
            self.save()
            raise ValueError("Framework or NoSpec adapter/prompts changed; create a new run")
        for stage in STAGES:
            data = self.state["stages"][stage]
            if data["status"] == "passed" and data["artifact_hashes"] != self.stage_hashes(stage):
                self.state.update(manual_edits=True, resumed=True, fresh=False, stop_reason="passed_artifact_changed")
                self.save()
                raise ValueError(f"Passed {stage} artifacts changed; upstream reuse is invalid")
        self.state.update(resumed=True, fresh=False)
        if self.state["jobs"]:
            last = self.state["jobs"][-1]
            if last.get("working_hashes") is not None and not last.get("passed"):
                if project_hashes(self.run / last["work"]) != last["working_hashes"]:
                    self.state["manual_edits"] = True
        self.save()
        return self.execute()


def evaluate_project(project: Path, runtime_contract: dict, evaluator: Path, destination: Path) -> dict:
    """Original independent evaluator orchestration, with no bundle dependency."""
    destination.mkdir(parents=True)
    original_hashes = hashes(project)
    work = destination / "project"
    shutil.copytree(project, work)
    harness = destination / "harness"
    harness.mkdir()
    shutil.copyfile(evaluator, harness / "evaluate.py")
    binary = runtime_contract["binary_name"]
    path = Path(binary)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Runtime binary must be a project-relative path")
    runtime = ToolRuntime(work, {"/harness": harness}, destination / "commands", lambda: {"passed": False})
    report = {"passed": False, "evaluator": str(evaluator.resolve()), "evaluator_sha256": digest(evaluator),
              "delivery_hashes": original_hashes, "builds": {}, "phases": {}, "errors": []}
    for phase, target in (("normal", "all"), ("sanitize", "sanitize")):
        build = runtime.command(f"make clean && make {target}", timeout=120)
        report["builds"][phase] = build
        if build["exit_code"] != 0 or build["timed_out"]:
            report["errors"].append(f"{phase} build failed")
            report["phases"][phase] = {"passed": False, "status": "not_executed", "reason": "build_failed"}
            continue
        if phase == "sanitize":
            probe = runtime.command("nm --undefined-only " + shlex.quote("/work/" + binary), timeout=10)
            if "__asan_init" not in probe["output"] or "__ubsan_handle" not in probe["output"]:
                report["errors"].append("Sanitizer binary lacks ASan/UBSan instrumentation")
        command = ("/usr/bin/python3 /harness/evaluate.py --binary " + shlex.quote("/work/" + binary)
                   + f" --out /work/.evaluation/{phase}")
        result = runtime.command(command, timeout=180)
        output = work / ".evaluation" / phase
        if (output / "report.json").is_file():
            phase_report = read_json(output / "report.json")
            shutil.copytree(output, destination / phase)
        else:
            phase_report = {"passed": False, "status": "not_executed", "reason": "missing_report"}
        scenarios = phase_report.get("scenarios", [])
        complete = bool(scenarios) and len(scenarios) == phase_report.get("required_count")
        if (result["exit_code"] != 0 or result["timed_out"] or not phase_report.get("passed")
                or not complete or any(s["status"] != "passed" for s in scenarios)):
            report["errors"].append(f"{phase} independent evaluation failed")
        report["phases"][phase] = phase_report
        if re.search(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer", result["output"]):
            report["errors"].append(f"{phase} sanitizer diagnostics")
    if hashes(project) != original_hashes:
        raise RuntimeError("Independent evaluation changed the original delivery")
    report["passed"] = not report["errors"] and len(report["phases"]) == 2
    save_json(destination / "report.json", report)
    return report


def evaluate_run(run: Path) -> dict:
    pipeline = NoSpecPipeline(run)
    state = pipeline.state
    if state.get("schema_version") != 1 or state.get("pipeline") != PIPELINE or not state.get("generation_passed"):
        raise ValueError("Independent evaluation requires a completed NoSpec-SpecForge generation")
    if project_hashes(pipeline.run / "project") != state["project_hashes"]:
        raise ValueError("Generated delivery changed before independent evaluation")
    state["evaluation_started"] = True
    state["evaluation"] = None
    pipeline.save()
    root = pipeline.run / "evaluation"
    root.mkdir(exist_ok=True)
    destination = root / f"evaluation_{len(list(root.glob('evaluation_*'))) + 1:03d}"
    report = evaluate_project(pipeline.run / "project", state["runtime_contract"], CASES[state["case"]]["evaluator"], destination)
    state["evaluation"] = {"passed": report["passed"], "report": str((destination / "report.json").relative_to(pipeline.run)),
                           "evaluator_sha256": report["evaluator_sha256"]}
    pipeline.save()
    return report
