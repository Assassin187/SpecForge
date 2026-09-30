"""SpecForge command line."""

import argparse
import json
import sys
from pathlib import Path

from .documents import read_json
from .pipeline import Pipeline, STAGES, verify_project
from .specs import validate


def main() -> int:
    parser = argparse.ArgumentParser(prog="specforge")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for name in ("task", "requirements", "protocol", "out"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--until", choices=STAGES, default="verify")
    resume = commands.add_parser("resume")
    resume.add_argument("--run", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--specs", type=Path, required=True)
    code = commands.add_parser("code")
    code.add_argument("--specs", type=Path, required=True)
    code.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            p = Pipeline.new(args.out)
            return 0 if p.freeze_inputs(args.task, args.requirements, args.protocol) and p.execute(args.until) else 1
        if args.command == "resume":
            return 0 if Pipeline(args.run).resume() else 1
        if args.command == "validate":
            report = validate(args.specs.resolve())
            print(json.dumps({k: v for k, v in report.items() if k != "abi"}, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 1
        if args.command == "code":
            import shutil
            report = validate(args.specs.resolve())
            if not report["passed"]:
                print(json.dumps(report["errors"], ensure_ascii=False))
                return 1
            p = Pipeline.new(args.out, spec_only=True)
            shutil.copytree(args.specs, p.bundle())
            for stage in STAGES[:-1]:
                p.state["stages"][stage] = {"status": "passed", "source": "spec_only"}
            p.state["stages"]["specs"]["artifact_hashes"] = p.stage_hashes("specs")
            p.save()
            return 0 if p.execute() else 1
        project = args.project.resolve()
        bundle = None
        if (project.parent / "run.json").is_file():
            state = read_json(project.parent / "run.json")
            bundle = project.parent / "specs" / f"r{state['spec_revision']:03d}"
            spec_report = validate(bundle)
            if not spec_report["passed"]:
                print(json.dumps(spec_report["errors"], ensure_ascii=False))
                return 1
        report = verify_project(project, project.parent / "reports", project.parent / "logs/verifier", bundle=bundle)
        print(json.dumps({k: v for k, v in report.items() if k not in ("builds", "phases")}, indent=2))
        return 0 if report["passed"] else 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

