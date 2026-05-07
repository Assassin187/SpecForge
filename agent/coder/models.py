from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProtocolMeta:
    name: str
    spec_version: str
    roles: list[str]


@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class FunctionSignature:
    raw: str
    name: str
    return_type: str
    params: list[dict[str, Any]]


@dataclass(frozen=True)
class FunctionSpec:
    trace_id: str
    function_type: str
    role: str
    signature: FunctionSignature
    rely: dict[str, Any]
    body: dict[str, Any]
    source_path: Path
    raw: dict[str, Any]


@dataclass(frozen=True)
class HeaderInterface:
    signature: str
    name: str
    kind: str
    function_type: str
    role: str
    visibility: str


@dataclass(frozen=True)
class SourceInterface:
    trace_id: str
    signature: str
    name: str
    kind: str
    role: str
    visibility: str


@dataclass(frozen=True)
class FileSpec:
    trace_id: str
    role: str
    lang: str
    header_path: str
    source_path: str
    header_dependencies: list[str]
    source_dependencies: list[str]
    header_data: list[dict[str, Any]]
    source_data: list[dict[str, Any]]
    header_interfaces: list[HeaderInterface]
    source_interfaces: list[SourceInterface]
    spec_path: Path
    raw: dict[str, Any]


@dataclass(frozen=True)
class ModuleEntry:
    name: str
    role: str
    dependencies: list[str]
    files: list[str]
    artifacts: list[dict[str, Any]]
    doc_ref: list[str]
    raw: dict[str, Any]


@dataclass
class SpecBundle:
    protocol: ProtocolMeta
    module_spec_path: Path
    spec_root: Path
    generation_order: list[str]
    modules_in_order: list[ModuleEntry]
    file_specs_by_trace: dict[str, FileSpec]
    file_specs_by_header_path: dict[str, FileSpec]
    file_specs_by_source_path: dict[str, FileSpec]
    function_specs_by_trace: dict[str, FunctionSpec]
    consistency_rules: list[dict[str, Any]]
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(diag.level == "error" for diag in self.diagnostics)

    def diagnostics_by_level(self, level: str) -> list[Diagnostic]:
        return [diag for diag in self.diagnostics if diag.level == level]
