from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Diagnostic, SpecBundle
from .protocol_behavior_val import verify_protocol_behavior
from .specs import canonical_signature_for_header, normalize_repo_path


@dataclass
class VerificationResult:
    ok: bool
    diagnostics: list[Diagnostic]
    compile_stdout: str = ""
    compile_stderr: str = ""
    scenarios: list[dict[str, str]] = field(default_factory=list)


def _bundle_slug(bundle: SpecBundle) -> str:
    return re.sub(r"[^a-z0-9]+", "_", bundle.protocol.name.lower()).strip("_") or "protocol"


def _bundle_binary_name(bundle: SpecBundle) -> str:
    roles = [role.lower() for role in bundle.protocol.roles]
    suffix = "broker" if "broker" in roles else "server" if "server" in roles else "app"
    return f"{_bundle_slug(bundle)}_{suffix}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


class ProjectVerifier:
    def __init__(self, bundle: SpecBundle, output_dir: str | Path) -> None:
        self.bundle = bundle
        self.output_dir = Path(output_dir)
        nested = self.output_dir / _bundle_slug(bundle)
        self.project_dir = nested if nested.exists() else self.output_dir

    def verify_structure(self) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        expected_paths = {"Makefile"}
        for module in self.bundle.modules_in_order:
            expected_paths.update(normalize_repo_path(path) for path in module.files)
        for relative_path in sorted(expected_paths):
            if not (self.project_dir / relative_path).exists():
                diagnostics.append(Diagnostic("error", "missing_generated_file", f"Missing generated file '{relative_path}'", relative_path))

        for file_spec in self.bundle.file_specs_by_trace.values():
            if not file_spec.header_path:
                continue
            header_path = self.project_dir / file_spec.header_path
            if not header_path.is_file():
                continue
            content = _normalize_text(header_path.read_text(encoding="utf-8"))
            for interface in file_spec.header_interfaces:
                canonical = _normalize_text(canonical_signature_for_header(self.bundle, file_spec, interface))
                if canonical not in content:
                    diagnostics.append(Diagnostic("error", "missing_signature", f"Header '{file_spec.header_path}' does not contain '{interface.name}' with canonical signature", file_spec.header_path))
        return diagnostics

    def compile_project(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["make", _bundle_binary_name(self.bundle)], cwd=self.project_dir, text=True, capture_output=True, check=False)

    def verify(self) -> VerificationResult:
        diagnostics = self.verify_structure()
        compile_result = self.compile_project()
        if compile_result.returncode != 0:
            diagnostics.append(Diagnostic("error", "compile_failed", "Generated project failed to compile", "Makefile"))
            return VerificationResult(False, diagnostics, compile_result.stdout, compile_result.stderr)
        behavior = self.verify_behavior()
        diagnostics.extend(behavior.diagnostics)
        return VerificationResult(not any(item.level == "error" for item in diagnostics), diagnostics, compile_result.stdout, compile_result.stderr, behavior.scenarios)

    def verify_behavior(self) -> VerificationResult:
        slug = _bundle_slug(self.bundle)
        ok, scenarios, error = verify_protocol_behavior(slug, self.project_dir, _bundle_binary_name(self.bundle))
        if not ok:
            return VerificationResult(
                False,
                [Diagnostic("error", "behavior_verification_failed", error or "protocol behavior verification failed", _bundle_binary_name(self.bundle))],
                scenarios=scenarios,
            )
        return VerificationResult(True, [], scenarios=scenarios)
