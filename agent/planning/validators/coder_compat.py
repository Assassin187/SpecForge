from __future__ import annotations

from pathlib import Path

from ..diagnostics import PlanningDiagnostic


def validate_coder_compatibility(spec_root: str | Path) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    try:
        from agent.coder.specs import load_spec_bundle_from_root

        bundle = load_spec_bundle_from_root(spec_root)
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
    return diagnostics

