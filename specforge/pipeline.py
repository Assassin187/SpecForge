"""Fixed stage controller, durable state and bounded implementation feedback."""

from __future__ import annotations

import importlib.metadata
import json
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .agent import run_agent
from .documents import ROOT, check_facts, digest, hashes, prepare, read_json, save_json
from .llm import LLM, ModelConfig, total_usage
from .specs import delivery_checks, project_path, publish, scaffold, validate
from .tools import ToolRuntime

STAGES = ("prepare", "facts", "design", "specs", "code", "verify")
LIMITS = {"facts": 40, "design": 100, "specs": 180, "code": 180, "repair": 40, "review": 60}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def framework_hashes() -> dict:
    paths = [ROOT / "pyproject.toml"]
    paths += [ROOT / "specforge" / name for name in ("__init__.py", "__main__.py", "llm.py", "agent.py", "tools.py",
                                                   "documents.py", "specs.py", "pipeline.py")]
    for name in ("prompts", "schemas", "tests"):
        paths += [p for p in (ROOT / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def project_hashes(project: Path) -> dict:
    selected = {p.relative_to(project).as_posix(): digest(p) for p in project.rglob("*")
                if p.is_file() and (p.suffix in (".c", ".h", ".py", ".sh", ".md") or p.name in ("Makefile", "delivery.json"))
                and p.name != "WORKLOG.md" and not any(part.startswith(".") for part in p.relative_to(project).parts)}
    delivery = project / "delivery.json"
    if delivery.is_file():
        try:
            inventory = read_json(delivery)
        except ValueError:
            inventory = {}
        for name in inventory.get("files", []):
            p = (project / name).resolve()
            if project_path(name) and p.is_relative_to(project.resolve()) and p.is_file():
                selected[name] = digest(p)
    return selected


def verify_project(project: Path, reports: Path, logs: Path, *, bundle: Path | None = None) -> dict:
    """Generic development gate; no independent protocol evaluator is visible."""
    project = project.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    stamp = len(list(reports.glob("verify_*"))) + 1
    destination = reports / f"verify_{stamp:03d}"
    destination.mkdir()
    signature = project_hashes(project)
    consistency = delivery_checks(project, bundle)
    errors = consistency["errors"]
    readonly = []
    binary = None
    if bundle:
        manifest = read_json(bundle / "bundle.json")
        readonly = [project / h["path"] for h in manifest["headers"]]
        binary = manifest["runtime_contract"]["binary_name"]
    report = {"passed": False, "project_hashes": signature, "kind": "development",
              "consistency": consistency, "errors": errors, "builds": {}, "phases": {}, "created_at": now()}
    if errors:
        save_json(destination / "report.json", report)
        report["report_path"] = str(destination / "report.json")
        return report
    runtime = ToolRuntime(project, {}, logs / f"verify_{stamp:03d}", lambda: {"passed": False}, readonly=readonly)
    for phase, target in (("normal", "all"), ("sanitize", "sanitize")):
        build = runtime.command(f"make clean && make {target}", timeout=120)
        report["builds"][phase] = build
        if build["exit_code"] != 0 or build["timed_out"]:
            report["errors"].append(f"{phase} clean build failed: {build['log']}")
            report["phases"][phase] = {"passed": False, "status": "not_executed", "reason": "build_failed"}
            continue
        if binary and not (project / binary).is_file():
            report["errors"].append(f"{phase} build did not create runtime binary: {binary}")
        if binary and phase == "sanitize":
            symbols = runtime.command("nm --undefined-only " + shlex.quote(binary), timeout=10)
            if "__asan_init" not in symbols["output"] or "__ubsan_handle" not in symbols["output"]:
                report["errors"].append("Sanitizer binary lacks ASan/UBSan instrumentation")
        binary_hash = digest(project / binary) if binary and (project / binary).is_file() else None
        command = "ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 make test"
        result = runtime.command(command, timeout=180)
        binary_unchanged = binary_hash is None or ((project / binary).is_file() and digest(project / binary) == binary_hash)
        full_output = (runtime.logs / Path(result["log"]).name).read_text(errors="replace")
        diagnostics = re.search(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer", full_output)
        phase_report = {"passed": result["exit_code"] == 0 and not result["timed_out"] and not diagnostics and binary_unchanged,
                        "status": "passed" if result["exit_code"] == 0 and not result["timed_out"] and not diagnostics and binary_unchanged else "failed",
                        "command": result, "test_count": consistency["test_count"], "binary_unchanged": binary_unchanged}
        if not binary_unchanged:
            report["errors"].append(f"{phase} development tests replaced the current runtime binary; test must preserve the selected build")
        report["phases"][phase] = phase_report
        if result["exit_code"] != 0 or not phase_report["passed"]:
            report["errors"].append(f"{phase} development tests failed")
    # Deliver a normal executable rather than leaving the sanitizer build active.
    final_build = runtime.command("make clean && make", timeout=120)
    report["builds"]["delivery"] = final_build
    if final_build["exit_code"] != 0:
        report["errors"].append("Final delivery build failed")
    report["passed"] = not report["errors"] and len(report["phases"]) == 2
    for record in [*report["builds"].values(), *(p["command"] for p in report["phases"].values() if "command" in p)]:
        filename = Path(record["log"]).name
        target = destination / "commands" / filename
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(runtime.logs / filename, target)
        record["log"] = "/reports/" + destination.name + "/commands/" + filename
    report["report_path"] = str(destination / "report.json")
    save_json(destination / "report.json", report)
    return report


class Pipeline:
    def __init__(self, run: Path, llm: LLM | None = None):
        self.run = run.resolve()
        self.llm = llm
        self.state = read_json(self.run / "run.json")
        self._verification_cache = None

    def save(self):
        self.state["updated_at"] = now()
        self.state["usage"] = total_usage([job["usage"] for job in self.state["jobs"]])
        # total_usage counts requests per entry; jobs contain aggregated requests.
        self.state["usage"]["requests"] = sum(j["usage"]["requests"] for j in self.state["jobs"])
        save_json(self.run / "run.json", self.state)

    @classmethod
    def new(cls, run: Path, *, spec_only: bool = False, llm: LLM | None = None):
        if run.exists():
            raise ValueError("Output must be a new run directory")
        run.mkdir(parents=True)
        for name in ("inputs", "documents", "facts", "design_work", "specs", "project", "snapshots", "reports", "logs"):
            (run / name).mkdir()
        versions = {name: importlib.metadata.version(name) for name in ("openai", "jsonschema")}
        versions["python"] = subprocess.check_output(["python3", "--version"], text=True).strip()
        for name in ("gcc", "make", "pdftotext", "bwrap"):
            flag = "-v" if name == "pdftotext" else "--version"
            output = subprocess.run([name, flag], capture_output=True, text=True, timeout=10)
            versions[name] = {"path": shutil.which(name), "version": (output.stdout + output.stderr).splitlines()[0]}
        state = {"schema_version": 2, "created_at": now(), "model": ModelConfig().record(), "versions": versions,
                 "framework_hashes": framework_hashes(), "stages": {s: {"status": "pending"} for s in STAGES},
                 "current_stage": "prepare", "spec_revision": 1, "implementation_repairs": 0, "spec_repairs": 0,
                 "fresh": not spec_only, "spec_only": spec_only, "resumed": False, "manual_edits": False,
                 "generation_passed": False, "evaluation_started": False, "evaluation": None,
                 "jobs": [], "stop_reason": None}
        save_json(run / "run.json", state)
        return cls(run, llm)

    def model(self):
        if self.llm is None:
            self.llm = LLM()
        return self.llm

    def stage(self, name: str, operation):
        self.state["current_stage"] = name
        self.state["stages"][name] = {"status": "running", "started_at": now()}
        self.save()
        print(f"[{name}] started", flush=True)
        started = time.monotonic()
        try:
            result = operation()
        except Exception as exc:
            result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
        self.state["stages"][name].update(status="passed" if result["passed"] else "failed", result=result,
                                          elapsed_seconds=round(time.monotonic() - started, 3), finished_at=now())
        if result["passed"]:
            self.state["stages"][name]["artifact_hashes"] = self.stage_hashes(name)
        else:
            self.state["stop_reason"] = f"{name}_failed"
        self.save()
        print(f"[{name}] {'passed' if result['passed'] else 'failed'}", flush=True)
        return result["passed"]

    def stage_hashes(self, stage: str):
        if stage == "prepare":
            return {"inputs": hashes(self.run / "inputs"), "documents": hashes(self.run / "documents")}
        if stage == "facts":
            return {name: digest(self.run / "facts" / name) for name in ("scope.json", "facts.json")}
        if stage == "design":
            return hashes(self.run / "design_work")
        if stage == "specs":
            return hashes(self.bundle())
        return project_hashes(self.run / "project")

    def bundle(self) -> Path:
        return self.run / "specs" / f"r{self.state['spec_revision']:03d}"

    def facts_gate(self):
        result = check_facts(self.run / "facts", self.run / "inputs", self.run / "documents")
        if result["passed"]:
            contract = read_json(self.run / "facts/scope.json")["runtime_contract"]
            if not project_path(contract["binary_name"]):
                result["passed"] = False
                result["errors"].append("runtime_contract.binary_name must be a concrete project-relative executable path; "
                                        "resolve delegated naming as an engineering decision, not prose or a placeholder")
        save_json(self.run / "reports/facts.json", result)
        return result

    def spec_gate(self, work: Path, design: bool = False, review: bool = False):
        result = validate(work, design=design, published=False,
                          facts=None if design else read_json(self.run / "facts/facts.json"),
                          scope=None if design else read_json(self.run / "facts/scope.json"))
        if result["passed"] and review:
            for row in read_json(work / "traceability.json")["requirements"]:
                if not row.get("semantic_review", "").strip():
                    result["errors"].append(f"Missing independent semantic review for {row['requirement_id']}")
            result["passed"] = not result["errors"]
        save_json(self.run / "reports" / ("design.json" if design else f"specs_r{self.state['spec_revision']:03d}.json"), result)
        # Keep full probes in the report, but return compact diagnostics to the model.
        return {k: v for k, v in result.items() if k != "abi"}

    def job(self, role: str, work: Path, inputs: dict[str, Path], task: str, gate, limit: str, coder: bool = False):
        if "/facts" in inputs:
            fact_view = self.run / "logs/planner_facts"
            fact_view.mkdir(exist_ok=True)
            for filename in ("scope.json", "facts.json"):
                shutil.copyfile(self.run / "facts" / filename, fact_view / filename)
            inputs = {**inputs, "/facts": fact_view}
        previous = self.state["jobs"][-1] if self.state["jobs"] else None
        if previous and previous["name"].endswith("_" + role) and not previous.get("passed") and (
                role != "repair" or previous.get("repair_round") == self.state["implementation_repairs"]):
            record = previous
            name = record["name"]
        else:
            name = f"{len(self.state['jobs']) + 1:02d}_{role}"
            record = {"name": name, "passed": False, "reason": "running", "usage": {"requests": 0}, "responses": 0,
                      "work": str(work.relative_to(self.run)), "repair_round": self.state["implementation_repairs"]}
            self.state["jobs"].append(record)
            self.save()
        readonly = [work / h["path"] for h in read_json(self.bundle() / "bundle.json")["headers"]] if coder else []
        inputs = {**inputs, "/reports": self.run / "reports"}
        runtime = ToolRuntime(work, inputs, self.run / "logs" / name / "commands", gate, readonly=readonly, coder=coder)
        prefix = (ROOT / "prompts" / ("coder.md" if coder else "facts.md" if role == "facts" else "planner.md")).read_text()
        if coder:
            syntax = read_json(ROOT / "schemas/artifacts.schema.json")["$defs"]["delivery"]
            prefix += "\nExact delivery.json schema:\n" + json.dumps(syntax, separators=(",", ":"))
        else:
            # Schema is fixed syntax, not protocol facts or a second design IR.
            # Compact inline copies avoid repeatedly paging through pretty JSON
            # after every checkpoint; the original files remain authoritative.
            if role == "design":
                syntax = {name: read_json(ROOT / "schemas" / (name + "_spec_schema.json")) for name in ("module", "file")}
            else:
                artifacts = read_json(ROOT / "schemas/artifacts.schema.json")["$defs"]
                syntax = {name: artifacts[name] for name in (("scope", "facts") if role == "facts" else ("traceability",))}
                if role != "facts":
                    syntax["function"] = read_json(ROOT / "schemas/function_spec_schema.json")
            prefix += "\nExact schema syntax for this task (already supplied; do not re-read it unless needed):\n" + json.dumps(syntax, separators=(",", ":"))
        prefix += "\nStage filesystem view:\n" + "\n".join(f"{alias}: read-only inputs" for alias in inputs) + "\n/work: outputs; /logs: command logs (read-only).\n"
        def progress(snapshot):
            record.update(snapshot)
            record["working_hashes"] = project_hashes(work) if coder else {p: h for p, h in hashes(work).items() if p != "WORKLOG.md"}
            self.save()
        result = run_agent(self.model(), runtime, prefix, task, LIMITS[limit], self.run / "logs" / name / "api", progress)
        if result.get("gap"):
            evidence = runtime.resolve(result["gap"]["evidence"])
            target = self.run / "reports" / f"{name}_spec_gap_evidence.log"
            shutil.copyfile(evidence, target)
            result["gap"] = {**result["gap"], "evidence": "/reports/" + target.name}
        record.update(result)
        self.save()
        return result

    def do_facts(self):
        return self.job("facts", self.run / "facts", {"/inputs": self.run / "inputs", "/documents": self.run / "documents", "/schemas": ROOT / "schemas"},
                        "Extract scope and protocol facts from the task, requirements and standard. Call check before completion.", self.facts_gate, "facts")

    def do_design(self):
        return self.job("design", self.run / "design_work", {"/facts": self.run / "facts", "/schemas": ROOT / "schemas"},
                        "DESIGN job: plan structure and public C interfaces, save module/file Specs and actual ABI headers. Function Specs are completed by the next job.",
                        lambda: self.spec_gate(self.run / "design_work", True), "design")

    def do_specs(self, repair: dict | None = None):
        work = self.bundle()
        if not work.exists():
            shutil.copytree(self.run / "design_work", work)
        shutil.copyfile(self.run / "facts/scope.json", work / "scope.json")
        task = "BEHAVIOR job: complete all function Specs, wire/call/resource contracts, planning test vectors and traceability."
        task += "\n\nChecked design function navigation (read only relevant files, start writing now):\n"
        for path in sorted((work / "files").glob("*.json")):
            file_spec = read_json(path)
            task += f"\n{path.relative_to(work)}: {file_spec['SOURCE']['PATH']}\n"
            for interface in file_spec["SOURCE"]["INTERFACE"]:
                task += f"- {interface['TRACE_ID']}: {interface['SIGNATURE']} ({interface['FUNCTION_TYPE']}); {interface['ROLE']}\n"
        task += "\nBinding scope requirements (algorithms must satisfy these, not merely link their IDs):\n"
        requirements = "\n".join(f"- {r['id']}: {r['description']}" for r in read_json(work / "scope.json")["requirements"])
        task += requirements
        if repair:
            task += "\nSPEC REPAIR: repair this concrete gap with the original facts and current Specs:\n" + json.dumps(repair, ensure_ascii=False)
        result = self.job("spec_repair" if repair else "specs", work, {"/facts": self.run / "facts", "/schemas": ROOT / "schemas"},
                          task, lambda: self.spec_gate(work), "specs")
        if result["passed"]:
            # Author checkpoints and review claims must not complete the review job.
            (work / "WORKLOG.md").unlink(missing_ok=True)
            traceability = read_json(work / "traceability.json")
            for row in traceability["requirements"]:
                row.pop("semantic_review", None)
            save_json(work / "traceability.json", traceability)
            facts = "\n".join(f"- {f['id']}: {f['statement']} Values: {json.dumps(f['values'], ensure_ascii=False)}"
                              for f in read_json(self.run / "facts/facts.json")["facts"])
            result = self.job("spec_review", work, {"/facts": self.run / "facts", "/schemas": ROOT / "schemas"},
                              "SEMANTIC REVIEW job: independently review the completed Specs against the approved scope and protocol facts before publication. "
                              "Re-derive externally observable behavior from /facts, rather than trusting the author's checkpoint or test expectations. "
                              "FIRST compare each binding scope requirement below with the actual accepted input domain, validation predicates, "
                              "storage capacities and feature paths. A normative minimum mandatory support range is not a maximum, and a mandatory "
                              "subset is not an exhaustive allowlist. Support the full requested legal class using permitted protocol choices, "
                              "and add representative positive vectors beyond the minimum subset where the scope requires them. "
                              "Correct scope narrowing in all affected algorithms, interfaces, storage and tests. "
                              "Independently derive a conditional decision table from ALL applicable facts below, including facts not cited by the author. "
                              "For each rule record its trigger, obligation, response and state/connection effect, then compare actual decision branches. "
                              "Check overlaps: a generic unsupported-feature policy must not hide a specific mandatory rejection. "
                              "Distinguish malformed or unframeable inputs from well-formed unsupported inputs according to the facts. "
                              "Repair missing rules and incorrect early-return precedence; add contrasting and overlap vectors and cite the applicable facts in the review. "
                              "NEXT batch-inspect all WIRE_MAPPING entries and related algorithms/tests. For every constant wire field, including "
                              "message types, status codes, flags and lengths, use run_command to calculate its numeric encoding from fact operands and rules. "
                              "Print the source fact value, expression, computed result and the actual value read from the saved Spec, and assert their equality. "
                              "Do not use a Spec's encoded literal as the derivation input or verify only lengths while accepting other constants mentally. "
                              "Correct every mismatch in WIRE_MAPPING, LOGIC/EVENT, call contracts and TEST_VECTORS. "
                              "Use run_command to extract ALL saved TEST_VECTORS INPUT/EXPECT with file paths and array indices in bounded batches. "
                              "Replay every existing input through the fact-derived decision table and saved LOGIC/EVENT; compare the actual saved EXPECT. "
                              "Do not limit this audit to traceability.test_ids or newly added vectors. After any rule correction, search related "
                              "algorithms, outputs, contracts and vectors for the old behavior, update all conflicts, then re-read and assert "
                              "the saved expectations for the corrected concrete cases. A corrected review narrative cannot excuse a stale vector. "
                              "THEN follow each required interaction through input, state changes, response and cleanup using traceability.requirements spec_refs. "
                              "Audit ownership across repeated legal interactions and different supported paths: allocations, retained storage, "
                              "moves, replacements and releases. Before replacing an owning pointer or aggregate, its previous resource must be "
                              "released or transferred. Clearing a length can retain allocated storage. Check first use, subsequent use, errors "
                              "and final teardown; correct lifecycle gaps in algorithms, contracts and repeated-operation vectors together. "
                              "Audit I/O progress for the chosen execution model: descriptor mode setup, exact readiness masks and operation "
                              "preconditions across producer/caller contracts. In a readiness loop, writable-only events must not dispatch "
                              "blocking reads, queued output must progress to an idle recipient, and one peer must not stall unrelated peers. "
                              "Follow required multi-participant interactions in different creation orders and correct mode setup, dispatch "
                              "conditions and progress vectors together; do not depend on extra peer input to drive pending output. "
                              "Inspect related Specs in batches; do not restart design or enumerate every helper. "
                              "Correct contradictions in all affected Specs and test expectations, preserve scope and traceability, "
                              "record the checked derivations and corrections in each traceability.requirements entry's semantic_review string. "
                              "Every scoped requirement needs its own review before check can pass. Save concise progress in WORKLOG.md, then call check."
                              "\n\nBinding scope requirements (preserve the requested domain; facts do not narrow it):\n" + requirements +
                              "\n\nComplete protocol facts summary (derive applicable obligations even when absent from current traceability):\n" + facts,
                              lambda: self.spec_gate(work, review=True), "review")
            if result["passed"]:
                publish(work, self.state["spec_revision"])
        return result

    def verify_gate(self):
        signature = project_hashes(self.run / "project")
        if self._verification_cache and signature == self._verification_cache["project_hashes"]:
            result = self._verification_cache
        else:
            result = verify_project(self.run / "project", self.run / "reports", self.run / "logs/verifier",
                                    bundle=self.bundle())
            self._verification_cache = result
        return {"passed": result["passed"], "errors": result["errors"],
                "report": "/reports/" + str(Path(result["report_path"]).relative_to(self.run / "reports")),
                "phases": result["phases"]}

    def do_code(self):
        scaffold(self.bundle(), self.run / "project")
        job_count = len([j for j in self.state["jobs"] if j["name"].endswith(("_code", "_repair"))])
        while True:
            previous = self.state["jobs"][-1] if self.state["jobs"] else {}
            pending_role = previous.get("name", "").split("_", 1)[-1] if previous.get("reason") in ("running", "api_error") else None
            if job_count and self.state["implementation_repairs"] >= 3 and previous.get("repair_round", -1) >= 3 and pending_role not in ("code", "repair"):
                return {"passed": False, "reason": "implementation_repair_limit", "verification": self.verify_gate()}
            task = "Implement the whole project from /specs, including Makefile, README, development tests and delivery.json. Build, self-test and pass the development check."
            if job_count:
                task += "\nContinue the existing project from disk. Development failure evidence:\n" + json.dumps(self.verify_gate(), ensure_ascii=False)
            role = pending_role if pending_role in ("code", "repair") else "code" if not job_count else "repair"
            view = self.run / "logs" / f"coder_specs_r{self.state['spec_revision']:03d}"
            view.mkdir(exist_ok=True)
            for relative in ["bundle.json", *read_json(self.bundle() / "bundle.json")["hashes"]]:
                target = view / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.bundle() / relative, target)
            result = self.job(role, self.run / "project", {"/specs": view},
                              task, self.verify_gate, "code" if role == "code" else "repair", coder=True)
            job_count += 1
            if result["reason"] == "api_error":
                return result
            if result.get("gap"):
                if self.state["spec_only"] or self.state["spec_repairs"] >= 1:
                    return {"passed": False, "reason": "spec_repair_limit", "gap": result["gap"]}
                self.state["spec_repairs"] += 1
                snapshot = self.run / "snapshots" / "before_spec_repair"
                snapshot.mkdir()
                shutil.copytree(self.bundle(), snapshot / "specs")
                shutil.copytree(self.run / "project", snapshot / "project")
                previous = self.bundle()
                self.state["spec_revision"] += 1
                shutil.copytree(previous, self.bundle())
                (self.bundle() / "bundle.json").unlink()
                self.save()
                if not self.do_specs(result["gap"])["passed"]:
                    return {"passed": False, "reason": "spec_repair_failed"}
                self.state["stages"]["specs"]["artifact_hashes"] = self.stage_hashes("specs")
                scaffold(self.bundle(), self.run / "project")
                self._verification_cache = None
                # The next job adjusts existing code to the new ABI; counts toward repair bound.
            gate = self.verify_gate()
            if gate["passed"]:
                self.state["project_hashes"] = project_hashes(self.run / "project")
                return {"passed": True, "verification": gate}
            if self.state["implementation_repairs"] >= 3:
                return {"passed": False, "reason": "implementation_repair_limit", "verification": gate}
            self.state["implementation_repairs"] += 1
            self.save()

    def do_verify(self):
        result = self.verify_gate()
        if result["passed"]:
            self.state["project_hashes"] = project_hashes(self.run / "project")
            binary = read_json(self.bundle() / "bundle.json")["runtime_contract"]["binary_name"]
            self.state["binary_sha256"] = digest(self.run / "project" / binary)
            self.state["generation_passed"] = True
        return result

    def execute(self, until: str = "verify") -> bool:
        self.state["stop_reason"] = None
        operations = {"facts": self.do_facts, "design": self.do_design, "specs": self.do_specs,
                      "code": self.do_code, "verify": self.do_verify}
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

    def freeze_inputs(self, task: Path, requirements: Path, protocol: Path):
        def operation():
            result = prepare(self.run, task, requirements, protocol)
            self.state["input_hashes"] = result["inputs"]
            return {"passed": True, **result}
        return self.stage("prepare", operation)

    def resume(self) -> bool:
        if self.state.get("schema_version") != 2:
            raise ValueError("Historical runs are read-only; create a version-2 run")
        if self.state["evaluation_started"]:
            raise ValueError("Generation is frozen after independent evaluation starts")
        if self.state["framework_hashes"] != framework_hashes():
            self.state.update(manual_edits=True, resumed=True, fresh=False, stop_reason="framework_changed")
            self.save()
            raise ValueError("Framework/prompts/schemas/tests changed; create a new run rather than mixing versions")
        for stage in STAGES:
            data = self.state["stages"][stage]
            if data.get("source") == "spec_only" and stage != "specs":
                continue
            if data["status"] == "passed" and data["artifact_hashes"] != self.stage_hashes(stage):
                self.state.update(manual_edits=True, resumed=True, fresh=False, stop_reason="passed_artifact_changed")
                self.save()
                raise ValueError(f"Passed {stage} artifacts changed; upstream reuse is invalid")
        self.state.update(resumed=True, fresh=False)
        if self.state["jobs"]:
            last = self.state["jobs"][-1]
            if last.get("working_hashes") is not None and not last.get("passed"):
                work = self.run / last["work"]
                actual = project_hashes(work) if last["name"].endswith(("_code", "_repair")) else {p: h for p, h in hashes(work).items() if p != "WORKLOG.md"}
                if actual != last["working_hashes"]:
                    self.state["manual_edits"] = True
        self.save()
        return self.execute()

