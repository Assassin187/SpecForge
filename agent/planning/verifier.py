from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..coder.specs import load_spec_bundle
from .models import PlanningDiagnostic, VerificationResult


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _has_cycle(modules: list[dict[str, Any]]) -> bool:
    graph = {str(item.get("name")): [str(dep) for dep in item.get("dependencies", [])] for item in modules}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def verify_output_dir(output_dir: str | Path) -> VerificationResult:
    out_dir = Path(output_dir)
    diagnostics: list[PlanningDiagnostic] = []
    required = [
        "planning_ir.json",
        "protocol_profile.json",
        "expert_activations.json",
        "candidate_architectures.json",
        "design_decisions.json",
        "implementation_plan.json",
        "implementation_plan_v2.json",
        "spec_blueprint.json",
        "expansion_candidates.json",
        "run_manifest.json",
    ]
    for filename in required:
        path = out_dir / filename
        if not path.exists():
            diagnostics.append(PlanningDiagnostic("error", "missing_artifact", f"Missing required planning artifact '{filename}'", str(path)))
    if diagnostics:
        return VerificationResult(False, diagnostics)

    implementation_plan = _load_json(out_dir / "implementation_plan.json")
    spec_blueprint = _load_json(out_dir / "spec_blueprint.json")
    module_graph = list(implementation_plan.get("module_graph", []))
    canonical_types = list(implementation_plan.get("canonical_types", []))
    handler_matrix = list(implementation_plan.get("handler_matrix", []))
    minimum_surface = {
        str(item.get("name"))
        for item in implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", [])
        if isinstance(item, dict) and item.get("name")
    }

    owners = {}
    for item in canonical_types:
        type_name = str(item.get("type_name", ""))
        owner = str(item.get("owner_module", ""))
        if not type_name:
            continue
        if type_name in owners and owners[type_name] != owner:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_canonical_owner", f"Type '{type_name}' has multiple owners", str(out_dir / "implementation_plan.json")))
        owners[type_name] = owner

    if _has_cycle(module_graph):
        diagnostics.append(PlanningDiagnostic("error", "module_dependency_cycle", "Module graph contains dependency cycle", str(out_dir / "implementation_plan.json")))

    handled = {str(item.get("surface_unit")) for item in handler_matrix if isinstance(item, dict)}
    for name in sorted(minimum_surface):
        if name not in handled:
            diagnostics.append(PlanningDiagnostic("error", "minimum_v1_coverage_gap", f"Surface '{name}' missing from handler_matrix", str(out_dir / "implementation_plan.json")))

    blueprint_kind = str(spec_blueprint.get("kind", ""))
    if blueprint_kind != "SPEC_BLUEPRINT":
        diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_kind", "spec_blueprint.json must have kind='SPEC_BLUEPRINT'", str(out_dir / "spec_blueprint.json")))

    for section in ("modules", "files", "functions"):
        items = spec_blueprint.get(section, [])
        if not isinstance(items, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_section", f"Blueprint section '{section}' must be a list", str(out_dir / "spec_blueprint.json")))
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_item", f"Blueprint item '{section}[{idx}]' must be an object", str(out_dir / "spec_blueprint.json")))
                continue
            has_trace = any(item.get(key) for key in ("evidence_refs", "decision_refs", "template_refs"))
            if not has_trace:
                ident = item.get("trace_id") or item.get("name") or idx
                diagnostics.append(PlanningDiagnostic("error", "missing_blueprint_traceability", f"Blueprint {section} item '{ident}' has no evidence_refs, decision_refs, or template_refs", str(out_dir / "spec_blueprint.json")))

    spec_bundle_dir = out_dir / "spec_bundle"
    module_specs = list(spec_bundle_dir.glob("*_module_spec.json"))
    if not module_specs:
        diagnostics.append(PlanningDiagnostic("error", "missing_compiled_spec", "Missing compiled module spec", str(spec_bundle_dir)))
        return VerificationResult(False, diagnostics)
    bundle = load_spec_bundle(module_specs[0], spec_bundle_dir)
    for diag in bundle.diagnostics:
        level = "error" if diag.level == "error" else "warning"
        diagnostics.append(PlanningDiagnostic(level, f"coder_{diag.code}", diag.message, diag.path))
    return VerificationResult(not any(diag.level == "error" for diag in diagnostics), diagnostics)
