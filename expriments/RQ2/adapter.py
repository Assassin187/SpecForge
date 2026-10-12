"""Scoped experiment adaptations; native sources and on-disk prompts stay unchanged."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from specforge import pipeline as native
from specforge.documents import hashes, read_json, save_json

import checks


EXPERIMENT_MODEL = replace(native.MODEL_CONFIGS["gpt-6.1-sol"], model="gpt-6-luna", reasoning_effort="max")


PROMPT_REPLACEMENTS = {
    "remaining responses to fix ABI/reference errors.": "remaining responses to fix the errors returned by check.",
    "writing their owning artifact and use checks for cross-module consistency.":
        "writing their owning artifact and self-review cross-module consistency.",
    "Compile saved headers early with run_command using C99, strict warnings and\n"
    "the include roots appropriate to your chosen layout. Catch syntax and warning\n"
    "errors while their owning artifact is still in context; finish with check.":
        "Normal autonomous compilation and consistency self-checks remain available.\n"
        "Use check for the current stage's mandatory diagnostics before completion.",
    "symbols. External functions are checked against SYSTEM_DEPENDENCY.":
        "symbols. Declare external functions with their SYSTEM_DEPENDENCY headers.",
    "Use check to fix schema, ABI and reference errors before completing.":
        "Use check to fix the current stage's reported errors before completing.",
}

PRECODE_TASK = (
    "PRE-CODE REVISION: self-review the current complete protocol Specs and actual public headers. "
    "Read /facts and the saved diagnostics below; correct the reported errors and any contradictions "
    "found by normal autonomous self-review. Preserve the frozen scope, complete protocol behavior, "
    "traceability and planning vectors. Review the semantics of every affected change against the facts "
    "and update the corresponding requirement semantic_review records with checked derivations and "
    "corrections. Keep correct interfaces and algorithms. This is one bounded revision round; "
    "call check before completion.\n\nCurrent check diagnostics:\n"
)


def planner_prompt(prefix: str) -> str:
    for old, new in PROMPT_REPLACEMENTS.items():
        if prefix.count(old) != 1:
            raise ValueError(f"Native Planner prompt changed: {old!r}")
        prefix = prefix.replace(old, new)
    return prefix


class RQ2Pipeline(native.Pipeline):
    @classmethod
    def new(cls, run: Path, *, spec_only: bool = False, model: str = "deepseek-flash", llm=None):
        with patch.dict(native.MODEL_CONFIGS, {EXPERIMENT_MODEL.model: EXPERIMENT_MODEL}):
            return super().new(run, spec_only=spec_only, model=model, llm=llm)

    def __init__(self, run: Path, llm=None):
        super().__init__(run, llm)
        self.experiment_config = None
        self.policy = "basic"
        self.phase = "common"

    def configure(self, config: dict, policy: str):
        self.experiment_config = config
        self.policy = policy
        return self

    def spec_gate(self, work: Path, design: bool = False, review: bool = False):
        started = time.monotonic()
        metadata = {"kind": "spec", "policy": self.policy, "phase": self.phase, "design": design, "review": review,
                    "created_at": native.now(), "content_hashes": hashes(work)}
        records = self.run / "reports/rq2_gates"
        records.mkdir(exist_ok=True)
        target = records / f"gate_{len(list(records.glob('gate_*.json'))) + 1:05d}.json"
        try:
            result = checks.check(work, self.policy, facts=read_json(self.run / "facts/facts.json"),
                                  scope=read_json(self.run / "facts/scope.json"), design=design, review=review)
        except Exception as exc:
            save_json(target, {**metadata, "passed": False, "error": f"{type(exc).__name__}: {exc}",
                               "elapsed_seconds": time.monotonic() - started})
            raise
        result = {**result, **metadata, "elapsed_seconds": time.monotonic() - started}
        save_json(target, result)
        save_json(self.run / "reports" / ("design.json" if design else f"specs_r{self.state['spec_revision']:03d}.json"), result)
        # Full probes and exact hashes remain in the report; the model sees all allowed errors.
        return {k: result[k] for k in ("passed", "errors", "policy", "phase")} | {
            "report": "/reports/" + target.relative_to(self.run / "reports").as_posix()}

    def package_gate(self, work: Path, phase: str) -> dict:
        started = time.monotonic()
        metadata = {"kind": "package", "policy": self.policy, "phase": phase,
                    "created_at": native.now(), "content_hashes": hashes(work)}
        records = self.run / "reports/rq2_gates"
        records.mkdir(exist_ok=True)
        target = records / f"gate_{len(list(records.glob('gate_*.json'))) + 1:05d}.json"
        try:
            result = checks.package_integrity(work)
        except Exception as exc:
            save_json(target, {**metadata, "passed": False, "error": f"{type(exc).__name__}: {exc}",
                               "elapsed_seconds": time.monotonic() - started})
            raise
        save_json(target, {**metadata, **result, "elapsed_seconds": time.monotonic() - started})
        return result

    def job(self, role, work, inputs, task, gate, limit, coder=False):
        original_agent = native.run_agent
        old_phase = self.phase
        self.phase = role
        first_coder = coder and not (self.run / "snapshots/initial/metadata.json").exists()
        result = {"passed": False, "reason": "exception"}

        def adapted_agent(llm, runtime, prefix, native_task, response_limit, log_dir, on_progress=None):
            if not coder and role != "facts":
                prefix = planner_prompt(prefix)
            budget = "precode" if role.startswith("precode_round_") else limit
            return original_agent(llm, runtime, prefix, native_task,
                                  self.experiment_config["budgets"][budget], log_dir, on_progress)

        try:
            with patch.object(native, "run_agent", adapted_agent):
                result = super().job(role, work, inputs, task, gate, limit, coder=coder)
            return result
        except Exception as exc:
            result = {"passed": False, "reason": "exception", "error": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            self.phase = old_phase
            if first_coder:
                self.snapshot("initial", result)

    def do_specs(self, repair: dict | None = None):
        old_policy = self.policy
        if repair:
            self.policy = "full"
        original_publish = native.publish

        def adapted_publish(directory, revision, origin="automatic"):
            if not repair and old_policy == "basic":
                return None  # Common do_specs preserves the native fresh review, but leaves an unpublished draft.
            manifest = original_publish(directory, revision, origin)
            integrity = self.package_gate(directory, "postcode_publish")
            if not integrity["passed"]:
                raise ValueError(integrity["errors"])
            self.state["rq2_last_published"] = str(directory.relative_to(self.run))
            self.save()
            return manifest

        try:
            with patch.object(native, "publish", adapted_publish):
                return super().do_specs(repair)
        finally:
            self.policy = old_policy

    def publish_specs(self):
        result = self.spec_gate(self.bundle(), review=True)
        if not result["passed"]:
            return result
        native.publish(self.bundle(), self.state["spec_revision"])
        integrity = self.package_gate(self.bundle(), "precode_publish")
        save_json(self.run / "reports/rq2_package.json", integrity)
        if not integrity["passed"]:
            return integrity
        self.state["rq2_last_published"] = str(self.bundle().relative_to(self.run))
        self.save()
        return result

    def last_published(self) -> Path | None:
        relative = self.state.get("rq2_last_published")
        return self.run / relative if relative else None

    def snapshot(self, name: str, result: dict) -> dict:
        destination = self.run / "snapshots" / name
        destination.mkdir()
        shutil.copytree(self.run / "project", destination / "project")
        bundle = self.last_published()
        if bundle:
            shutil.copytree(bundle, destination / "specs")
        metadata = {"created_at": native.now(), "result": result,
                    "published_revision": str(bundle.relative_to(self.run)) if bundle else None,
                    "project_hashes": hashes(destination / "project"),
                    "spec_hashes": hashes(destination / "specs") if bundle else None,
                    "code": checks.code_complete(destination / "project", destination / "specs" if bundle else None)}
        save_json(destination / "metadata.json", metadata)
        return metadata

    def precode(self) -> dict:
        root = self.run / "snapshots/precode"
        root.mkdir()
        for number in range(1, self.experiment_config["precode_rounds"] + 1):
            role = f"precode_round_{number}"
            self.phase = role
            destination = root / role
            destination.mkdir()
            shutil.copytree(self.bundle(), destination / "before")
            before = self.spec_gate(self.bundle(), review=True)
            save_json(destination / "before.json", {"gate": before, "hashes": hashes(self.bundle())})
            result = {"passed": False, "reason": "exception"}
            try:
                result = self.job(role, self.bundle(), {"/facts": self.run / "facts", "/schemas": native.ROOT / "schemas"},
                                  PRECODE_TASK + json.dumps(before, ensure_ascii=False),
                                  lambda: self.spec_gate(self.bundle(), review=True), "repair")
            finally:
                shutil.copytree(self.bundle(), destination / "after")
                save_json(destination / "result.json", {"job": result, "hashes": hashes(self.bundle()),
                                                       "gate": self.spec_gate(self.bundle(), review=True)})
        self.phase = "precode_final"
        return self.publish_specs()

    def do_code(self):
        integrity = self.package_gate(self.bundle(), "code_load")
        if not integrity["passed"]:
            return integrity
        return super().do_code()
