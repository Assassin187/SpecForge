from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningDiagnostic:
    level: str
    code: str
    message: str
    path: str | None = None


def has_errors(diagnostics: list[PlanningDiagnostic]) -> bool:
    return any(item.level == "error" for item in diagnostics)


def diagnostics_to_dict(diagnostics: list[PlanningDiagnostic]) -> list[dict[str, str | None]]:
    return [
        {
            "level": item.level,
            "code": item.code,
            "message": item.message,
            "path": item.path,
        }
        for item in diagnostics
    ]
