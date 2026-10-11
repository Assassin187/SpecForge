"""An independent SYSSPEC planner; native Facts and Agent machinery are reused."""

from __future__ import annotations

import json
import shutil
import time
from copy import copy
from pathlib import Path

from specforge.agent import run_agent
from specforge.documents import hashes, read_json, save_json
from specforge.llm import ModelConfig
from specforge.pipeline import LIMITS, Pipeline, now
from specforge.tools import ToolRuntime
from spec_adapter import generic_checks, publish_generic

SUITE = Path(__file__).resolve().parent


class NativePlanning(Pipeline):
    """Measure native jobs and apply RQ2's frozen Design output allowance."""

    @classmethod
    def new(cls, run, *, stage_models, **kwargs):
        planner = super().new(run, **kwargs)
        planner.state["stage_models"] = stage_models
        planner.save()
        return planner

    def model(self):
        model = super().model()
        if self.state["current_stage"] == "design":
            # Reuse the client without changing the cached model or native profile.
            model = copy(model)
            model.config = ModelConfig.from_record(self.state["stage_models"]["design"])
        return model

    def job(self, *args, **kwargs):
        started, stamp = time.monotonic(), now()
        count = len(self.state["jobs"])
        try:
            return super().job(*args, **kwargs)
        finally:
            if len(self.state["jobs"]) > count:
                self.state["jobs"][-1].update(started_at=stamp, finished_at=now(), spec_revision=self.state["spec_revision"],
                                             wall_seconds=round(time.monotonic() - started, 3))
                self.save()


class GenericPipeline(NativePlanning):
    # Facts deliberately delegates to Pipeline.job with the native prompt/schema.
    def job(self, role, work, inputs, task, gate, limit, coder=False):
        if role == "facts":
            return super().job(role, work, inputs, task, gate, limit, coder)
        if coder:
            raise ValueError("RQ2's common controller owns all code jobs")
        fact_view = self.run / "logs/planner_facts"
        fact_view.mkdir(exist_ok=True)
        for name in ("scope.json", "facts.json"):
            shutil.copyfile(self.run / "facts" / name, fact_view / name)
        inputs = {**inputs, "/facts": fact_view, "/schemas": SUITE / "schemas", "/reports": self.run / "reports"}
        name = f"{len(self.state['jobs']) + 1:02d}_{role}"
        record = {"name": name, "role": role, "passed": False, "reason": "running",
                  "responses": 0, "usage": {"requests": 0}, "work": str(work.relative_to(self.run)),
                  "spec_revision": self.state["spec_revision"]}
        self.state["jobs"].append(record)
        started = time.monotonic()
        record["started_at"] = now()
        self.save()
        prefix = (SUITE / "generic_planner.md").read_text()
        prefix += "\nExact navigation index schema (not a protocol behavior schema):\n" + json.dumps(
            read_json(SUITE / "schemas/generic_plan.schema.json"), separators=(",", ":"))
        prefix += "\nStage filesystem: /work writable outputs; /facts, /schemas, /reports read-only; /logs read-only command logs.\n"
        runtime = ToolRuntime(work, inputs, self.run / "logs" / name / "commands", gate)

        def progress(snapshot):
            record.update(snapshot)
            record["working_hashes"] = {p: h for p, h in hashes(work).items() if p != "WORKLOG.md"}
            self.save()

        try:
            result = run_agent(self.model(), runtime, prefix, task, LIMITS[limit], self.run / "logs" / name / "api", progress)
        finally:
            record.update(finished_at=now(), wall_seconds=round(time.monotonic() - started, 3))
            self.save()
        record.update(result)
        self.save()
        return result

    def spec_gate(self, work, design=False, review=False):
        result = generic_checks(work, read_json(self.run / "facts/scope.json"), design=design, review=review)
        target = self.run / "reports" / ("design.json" if design else f"specs_r{self.state['spec_revision']:03d}.json")
        save_json(target, result)
        return {k: v for k, v in result.items() if k != "abi"}

    def do_design(self):
        return self.job("design", self.run / "design_work", {},
                        "DESIGN: Independently plan a complete engineering design from your own /facts/scope.json and "
                        "/facts/facts.json. Choose modules, processing paths, data structures, ownership and ABI. "
                        "Write plan.json, project.spec, each planned file .spec, and real public headers under abi/. "
                        "Enumerate planned functions and their exact signatures; function contracts are completed next. "
                        "Do not implement C sources. Call check.",
                        lambda: self.spec_gate(self.run / "design_work", design=True), "design")

    def do_specs(self, repair=None):
        work = self.bundle()
        if not work.exists():
            shutil.copytree(self.run / "design_work", work)
        shutil.copyfile(self.run / "facts/scope.json", work / "scope.json")
        scope = read_json(work / "scope.json")
        requirements = "\n".join(f'- {r["id"]}: {r["description"]}' for r in scope["requirements"])
        task = ("BEHAVIOR: Complete all planned function .spec documents in SYSSPEC. Resolve exact signatures, "
                "dependencies, pre/postconditions, wire encoding, conditions, state transitions, error precedence, "
                "caller results, ownership, resource release and I/O progress. Use exact bytes/examples/tests as useful. "
                "You may improve your own design and ABI when necessary, keeping plan.json consistent. "
                "No implementation sources. Call check.\n\nBinding scope requirements:\n" + requirements +
                "\n\nChecked own design navigation:\n" + json.dumps(read_json(work / "plan.json"), ensure_ascii=False))
        if repair:
            task += "\nSPEC REPAIR: Repair the concrete development gap using approved own Facts and your current specs:\n" + json.dumps(repair, ensure_ascii=False)
        result = self.job("spec_repair" if repair else "specs", work, {}, task, lambda: self.spec_gate(work), "specs")
        if not result["passed"]:
            return result
        (work / "WORKLOG.md").unlink(missing_ok=True)
        (work / "review.json").unlink(missing_ok=True)
        facts = "\n".join(f'- {f["id"]}: {f["statement"]}; values={json.dumps(f["values"], ensure_ascii=False)}'
                          for f in read_json(self.run / "facts/facts.json")["facts"])
        task = ("INDEPENDENT REVIEW: Start a fresh semantic audit of this saved project, with no author completion "
                "claims or previous review. Derive obligations from the complete approved facts and binding scope. "
                "Audit every requirement against saved algorithms and contracts. Recompute numeric/byte encodings "
                "and replay concrete examples from the actual saved values. Check condition overlap and mandatory "
                "error precedence; state/response correctness; producer/consumer return values and consumed counts; "
                "cross-function pre/postconditions; repeated operations; first allocation, replacement, failed "
                "ownership transfer and final cleanup; partial reads/writes, EOF and readiness/progress. Ensure "
                "facts and generic contracts do not narrow required legal inputs. Fix concrete semantic errors "
                "and keep design, headers and function contracts consistent. Record review.json as "
                '{"requirements":[{"id":"R01","spec_refs":["function.spec#SPECIFICATION"],'
                '"semantic_review":"specific obligations, calculations, paths checked and corrections"}]} '
                "with exactly one substantive independent record per requirement. Run calculations/probes early "
                "within this response budget, not at its end. Call check.\n\nBinding requirements:\n" + requirements +
                "\n\nComplete approved own facts:\n" + facts)
        result = self.job("spec_review", work, {}, task, lambda: self.spec_gate(work, review=True), "review")
        if result["passed"]:
            publish_generic(work, scope, self.state["spec_revision"])
        return result
