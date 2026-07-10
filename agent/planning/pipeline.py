from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .compiler import compile_specs
from .facts import collect_top_level_fact_refs, read_json, stable_json_hash, write_json
from .knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from .models import Diagnostic, PlanningResult, to_jsonable
from .planner import LLMStructuredPlanner, build_planning_context
from .validation import diagnostics_to_json, validate_planning_run


def _as_diag(level: str, code: str, message: str, path: str | None = None) -> Diagnostic:
    return Diagnostic(level, code, message, path)


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _coder_validate(specs_root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    try:
        from agent.coder.specs import load_spec_bundle_from_root

        bundle = load_spec_bundle_from_root(specs_root, validate_rendered_headers=True)
        for diag in bundle.diagnostics:
            diagnostics.append(Diagnostic(diag.level, f"coder_{diag.code}", diag.message, diag.path))
    except Exception as exc:  # pragma: no cover - defensive path, tested by end-to-end command.
        diagnostics.append(_as_diag("error", "coder_validation_exception", f"{type(exc).__name__}: {exc}", str(specs_root)))
    return diagnostics


def _write_planning_artifacts(
    planning_root: Path,
    facts_path: Path,
    facts_hash: str,
    fact_refs: list[dict[str, Any]],
    characteristics: Any,
    rules: Any,
    assumptions: Any,
    context: dict[str, Any],
    plan: dict[str, Any],
    compile_manifest: dict[str, Any],
    diagnostics: list[Diagnostic],
    planner_mode: str,
) -> Path:
    write_json(planning_root / "fact_refs.json", fact_refs)
    write_json(planning_root / "normalized_characteristics.json", to_jsonable(characteristics))
    write_json(planning_root / "activated_engineering_rules.json", [to_jsonable(rule) for rule in rules])
    write_json(planning_root / "planning_context.json", context)
    write_json(planning_root / "selected_architecture.json", plan.get("architecture", {}))
    write_json(planning_root / "structured_planning_stages.json", plan.get("structured_planning_stages", []))
    write_json(planning_root / "structured_planning_usage.json", plan.get("structured_planning_usage", {}))
    write_json(planning_root / "implementation_plan.json", plan)
    write_json(planning_root / "engineering_decisions.json", plan.get("engineering_decisions", []))
    write_json(planning_root / "open_assumptions.json", [to_jsonable(item) for item in assumptions])
    write_json(planning_root / "diagnostics.json", diagnostics_to_json(diagnostics))
    manifest = {
        "kind": "PLANNING_RUN_MANIFEST",
        "facts_path": str(facts_path),
        "facts_sha256": facts_hash,
        "planner_mode": planner_mode,
        "specs_root": compile_manifest["specs_root"],
        "module_spec": compile_manifest["module_spec"],
        "summary": compile_manifest["summary"],
        "written_files": compile_manifest["written_files"],
        "diagnostic_counts": {
            "error": sum(1 for diag in diagnostics if diag.level == "error"),
            "warning": sum(1 for diag in diagnostics if diag.level == "warning"),
        },
    }
    manifest_path = planning_root / "run_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def run_planning(
    facts_path: str | Path,
    out: str | Path,
    *,
    planner_mode: str = "llm",
    api_key_env: str = "ALI_API",
    coder_validate: bool = True,
    resume_from: str | None = None,
) -> PlanningResult:
    facts_path = Path(facts_path)
    output_root = Path(out)
    facts = read_json(facts_path)
    facts_hash = stable_json_hash(facts)
    characteristics = normalize_characteristics(facts)
    rules = activate_engineering_rules(characteristics)
    assumptions = extract_open_assumptions(facts, characteristics)
    context = build_planning_context(facts, characteristics, rules, assumptions)
    if planner_mode != "llm":
        raise ValueError("Only planner_mode='llm' is supported; deterministic local plan generation is intentionally disabled.")
    planning_root = output_root / "_planning"
    output_root.mkdir(parents=True, exist_ok=True)
    if resume_from is None:
        _clean_dir(planning_root)
    else:
        planning_root.mkdir(parents=True, exist_ok=True)
    provider = LLMStructuredPlanner(api_key_env=api_key_env, stage_log_dir=planning_root / "stage_logs")
    plan = provider.build_plan(context, resume_from=resume_from)

    specs_root = output_root / f"{plan['protocol']['slug']}_specs"

    compile_manifest = compile_specs(plan, specs_root, clean=True)
    diagnostics = validate_planning_run(facts, facts_hash, plan, specs_root)
    if coder_validate:
        diagnostics.extend(_coder_validate(specs_root))
    manifest_path = _write_planning_artifacts(
        planning_root,
        facts_path,
        facts_hash,
        collect_top_level_fact_refs(facts),
        characteristics,
        rules,
        assumptions,
        context,
        plan,
        compile_manifest,
        diagnostics,
        planner_mode,
    )
    return PlanningResult(
        output_root=output_root,
        specs_root=specs_root,
        planning_root=planning_root,
        manifest_path=manifest_path,
        diagnostics=diagnostics,
    )


def validate_existing_run(run_dir: str | Path, *, coder_validate: bool = True) -> PlanningResult:
    root = Path(run_dir)
    planning_root = root / "_planning"
    manifest = read_json(planning_root / "run_manifest.json")
    facts = read_json(manifest["facts_path"])
    plan = read_json(planning_root / "implementation_plan.json")
    specs_root = Path(manifest["specs_root"])
    diagnostics = validate_planning_run(facts, stable_json_hash(facts), plan, specs_root)
    if coder_validate:
        diagnostics.extend(_coder_validate(specs_root))
    write_json(planning_root / "diagnostics.json", diagnostics_to_json(diagnostics))
    manifest["diagnostic_counts"] = {
        "error": sum(1 for diag in diagnostics if diag.level == "error"),
        "warning": sum(1 for diag in diagnostics if diag.level == "warning"),
    }
    write_json(planning_root / "run_manifest.json", manifest)
    return PlanningResult(root, specs_root, planning_root, planning_root / "run_manifest.json", diagnostics)
