from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import PlanningDiagnostic


@dataclass(frozen=True)
class TargetProfile:
    target_role: str
    language: str
    runtime: str
    scope: str
    deployment_constraints: dict[str, Any]
    role_aliases: dict[str, str]
    raw: dict[str, Any]

    @property
    def slug(self) -> str:
        parts = [self.target_role, self.language, self.runtime, self.scope]
        safe_parts: list[str] = []
        for part in parts:
            text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(part))
            safe_parts.append(text.strip("_") or "x")
        return "__".join(safe_parts)


@dataclass
class PlanningResult:
    success: bool
    output_dir: Path
    diagnostics: list[PlanningDiagnostic] = field(default_factory=list)
    artifact_paths: dict[str, Path] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return any(item.level == "error" for item in self.diagnostics)


JsonObject = dict[str, Any]
