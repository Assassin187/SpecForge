"""Run one NoSpec trial per selected protocol and preserve its evidence."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from specforge.documents import read_json, save_json
from specforge.llm import MODEL_CONFIGS
from specforge.pipeline import now
from expriments.RQ2.adapter import CASES, LIMITS, PIPELINE, STAGES, NoSpecPipeline, evaluate_run


def refresh_summary(root: Path) -> dict:
    summary = read_json(root / "summary.json")
    rows = {}
    for case in summary["cases"]:
        run = root / case
        if not (run / "run.json").is_file():
            rows[case] = {"status": "not_started", "error": summary["errors"].get(case)}
            continue
        state = read_json(run / "run.json")
        row = {"run": str(run), "generation_passed": state["generation_passed"],
               "stop_reason": state["stop_reason"], "current_stage": state["current_stage"],
               "implementation_repairs": state["implementation_repairs"], "limits": state["limits"],
               "usage": state["usage"], "elapsed_seconds": state.get("elapsed_seconds", 0),
               "fresh": state["fresh"], "resumed": state["resumed"], "manual_edits": state["manual_edits"],
               "stages": {name: {key: value for key, value in stage.items()
                                  if key in ("status", "elapsed_seconds", "result", "reason")}
                          for name, stage in state["stages"].items()},
               "error": summary["errors"].get(case), "development": None,
               "evaluation": {"status": "not_executed", "reason": "generation_incomplete"}}
        reports = list((run / "reports").glob("verify_*/report.json"))
        if reports:
            report = read_json(max(reports, key=lambda p: int(p.parent.name.split("_")[-1])))
            row["development"] = {
                "passed": report["passed"], "errors": report["errors"],
                "builds": {name: {k: v for k, v in build.items() if k in ("exit_code", "timed_out", "elapsed_seconds", "log")}
                           for name, build in report["builds"].items()},
                "phases": {name: {k: v for k, v in phase.items() if k in ("passed", "status", "test_count", "binary_unchanged")}
                           for name, phase in report["phases"].items()},
            }
        if state["evaluation"]:
            report = read_json(run / state["evaluation"]["report"])
            row["evaluation"] = {"status": "passed" if report["passed"] else "failed",
                                 "report": state["evaluation"]["report"], "errors": report["errors"],
                                 "evaluator_sha256": report["evaluator_sha256"], "builds": report["builds"],
                                 "phases": {name: {**phase, "passed_count": sum(s["status"] == "passed" for s in phase.get("scenarios", []))}
                                            for name, phase in report["phases"].items()}}
        elif state["evaluation_started"]:
            row["evaluation"] = {"status": "failed", "reason": "missing_report"}
        elif any(state["stages"][stage]["status"] == "failed" for stage in STAGES):
            row["evaluation"]["reason"] = "generation_failed"
        rows[case] = row
    summary.update(updated_at=now(), runs=rows, totals={
        "runs": len(rows), "generation_passed": sum(r.get("generation_passed", False) for r in rows.values()),
        "independent_passed": sum(r.get("evaluation", {}).get("status") == "passed" for r in rows.values()),
    })
    save_json(root / "summary.json", summary)
    return summary


def record_elapsed(run: Path, started: float):
    state = read_json(run / "run.json")
    state["elapsed_seconds"] = round(state.get("elapsed_seconds", 0) + time.monotonic() - started, 3)
    state["updated_at"] = now()
    save_json(run / "run.json", state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run each selected protocol once in a new output directory")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    run.add_argument("--model", choices=MODEL_CONFIGS, default="deepseek-flash")
    run.add_argument("--until", choices=STAGES, default="verify")
    for name in ("resume", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        root = args.out.resolve()
        if root.exists():
            parser.error("Output must be a new experiment directory")
        selected = list(dict.fromkeys(args.cases))
        root.mkdir(parents=True)
        save_json(root / "summary.json", {"schema_version": 1, "pipeline": PIPELINE, "created_at": now(),
                                         "model": MODEL_CONFIGS[args.model].record(), "limits": dict(LIMITS),
                                         "cases": selected, "until": args.until, "errors": {}, "runs": {}})
        passed = True
        for case in selected:
            started = time.monotonic()
            target = root / case
            try:
                pipeline = NoSpecPipeline.new(target, case=case, model=args.model)
                inputs = CASES[case]
                result = pipeline.freeze_inputs(inputs["task"], inputs["requirements"], inputs["protocol"])
                result = result and pipeline.execute(args.until)
                if result and pipeline.state["generation_passed"]:
                    result = evaluate_run(target)["passed"]
                passed = passed and result
            except Exception as exc:
                passed = False
                summary = read_json(root / "summary.json")
                summary["errors"][case] = f"{type(exc).__name__}: {exc}"
                save_json(root / "summary.json", summary)
                print(f"[{case}] {summary['errors'][case]}", file=sys.stderr)
            finally:
                if (target / "run.json").is_file():
                    record_elapsed(target, started)
                refresh_summary(root)
        print(f"Summary: {root / 'summary.json'}", flush=True)
        return 0 if passed else 1
    target = args.run.resolve()
    started = time.monotonic()
    accepted = False
    error = None
    try:
        pipeline = NoSpecPipeline(target)
        if pipeline.state.get("schema_version") != 1 or pipeline.state.get("pipeline") != PIPELINE:
            raise ValueError("This command requires a NoSpec-SpecForge run")
        accepted = True
        if args.command == "resume":
            result = pipeline.resume()
            if result:
                result = evaluate_run(target)["passed"]
        else:
            result = evaluate_run(target)["passed"]
        return 0 if result else 1
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr)
        return 1
    finally:
        if accepted:
            record_elapsed(target, started)
            summary_path = target.parent / "summary.json"
            if summary_path.is_file():
                summary = read_json(summary_path)
                if error:
                    summary["errors"][pipeline.state["case"]] = error
                else:
                    summary["errors"].pop(pipeline.state["case"], None)
                save_json(summary_path, summary)
                refresh_summary(target.parent)


if __name__ == "__main__":
    raise SystemExit(main())
