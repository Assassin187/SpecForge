from __future__ import annotations

from pathlib import Path

from ..diagnostics import PlanningDiagnostic
from .coder_schema import validate_coder_spec_bundle_against_schema
from .coder_semantics import validate_coder_semantics


def validate_coder_compatibility(spec_root: str | Path, schema_root: str | Path | None = None, *, strict_schema: bool = True) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    schema_diags = validate_coder_spec_bundle_against_schema(spec_root, schema_root)
    diagnostics.extend(schema_diags)
    if strict_schema and any(item.level == "error" for item in schema_diags):
        return diagnostics
    try:
        from agent.coder.specs import load_spec_bundle_from_root

        bundle = load_spec_bundle_from_root(spec_root, validate_rendered_headers=True)
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        return [PlanningDiagnostic("error", "coder_loader_failed", f"Coder loader failed: {exc}", str(spec_root))]
    for item in bundle.diagnostics:
        diagnostics.append(
            PlanningDiagnostic(
                item.level,
                f"coder_{item.code}",
                item.message,
                item.path,
            )
        )
    diagnostics.extend(validate_coder_semantics(bundle))
    return diagnostics
