from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.coder.models import SpecBundle
from agent.coder.specs import load_spec_bundle_from_root, normalize_repo_path

from .configs import REPO_ROOT
from .transformer import FORBIDDEN_VISIBLE_TERMS, S2_PROJECT_GRAPH_FORBIDDEN_TERMS


SUPPORTED_VIEWS = {"s1", "s2"}

S1_PROMPT_FORBIDDEN_TERMS: tuple[str, ...] = (
    *FORBIDDEN_VISIBLE_TERMS,
    "GENERATION_ORDER",
    "MODULES",
    "module list",
    "module membership",
    "module role",
    "module dependency graph",
    "full source file list",
    "full function-to-file map",
    "file dependency graph",
    "current file position",
    "global project graph",
    "Machine-readable constraints",
    "Consistency rules",
)

S2_PROMPT_FORBIDDEN_TERMS: tuple[str, ...] = (
    *S2_PROJECT_GRAPH_FORBIDDEN_TERMS,
    "Machine-readable constraints",
    "Consistency rules",
)

PROMPT_FORBIDDEN_TERMS = S1_PROMPT_FORBIDDEN_TERMS


def prompt_forbidden_terms(view_name: str) -> tuple[str, ...]:
    normalized = view_name.lower().strip()
    if normalized == "s1":
        return S1_PROMPT_FORBIDDEN_TERMS
    if normalized == "s2":
        return S2_PROMPT_FORBIDDEN_TERMS
    raise UnsupportedViewError(f"unsupported ablation view '{view_name}'")


def visible_boundary_name(view_name: str) -> str:
    normalized = view_name.lower().strip()
    if normalized == "s1":
        return "s1_local_specfs"
    if normalized == "s2":
        return "s2_project_graph_specfs"
    raise UnsupportedViewError(f"unsupported ablation view '{view_name}'")


class ViewSpecError(RuntimeError):
    """Raised when an ablation view artifact set is incomplete or unsafe."""


class UnsupportedViewError(RuntimeError):
    """Raised for reserved but not-yet-implemented ablation views."""


@dataclass(frozen=True)
class AblationViewContext:
    view_name: str
    view_root: Path
    source_bundle: SpecBundle
    execution_manifest: dict[str, Any]
    transformation_manifest: dict[str, Any]
    function_blocks_by_trace: dict[str, str]
    headers_by_path: dict[str, str]
    project_graph: dict[str, Any] | None = None

    def function_blocks_for_source(self, source_path: str) -> list[str]:
        source_path = normalize_repo_path(source_path)
        for item in self.execution_manifest.get("sources", []):
            if normalize_repo_path(str(item.get("source_path", ""))) != source_path:
                continue
            blocks: list[str] = []
            for function in item.get("functions", []):
                trace_id = str(function.get("trace_id", ""))
                block = self.function_blocks_by_trace.get(trace_id)
                if block is not None:
                    blocks.append(block)
            return blocks
        return []

    def header_declarations_for_source(self, source_path: str) -> dict[str, str]:
        source_path = normalize_repo_path(source_path)
        file_spec = self.source_bundle.file_specs_by_source_path.get(source_path)
        if file_spec is None:
            return {}
        headers: dict[str, str] = {}
        paths = [file_spec.header_path, *file_spec.source_dependencies]
        for path in paths:
            normalized = normalize_repo_path(path)
            if not normalized or normalized in headers:
                continue
            content = self.headers_by_path.get(normalized)
            if content is not None:
                headers[normalized] = content
        return headers


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_repo_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _assert_no_visible_leakage(path: Path, text: str, forbidden_terms: tuple[str, ...]) -> None:
    if "\x00" in text:
        raise ViewSpecError(f"view artifact contains NUL byte: {path}")
    hits = [term for term in forbidden_terms if term in text]
    if hits:
        raise ViewSpecError(f"view artifact leaks forbidden terms {hits}: {path}")


def _load_function_blocks(view_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for item in manifest.get("artifacts", {}).get("functions", []):
        trace_id = str(item.get("trace_id", ""))
        artifact_path = str(item.get("artifact_path", ""))
        expected_hash = str(item.get("sha256", ""))
        if not trace_id or not artifact_path:
            raise ViewSpecError("function artifact record is missing trace_id or artifact_path")
        path = view_root / artifact_path
        if not path.is_file():
            raise ViewSpecError(f"missing function artifact: {path}")
        text = path.read_text(encoding="utf-8")
        _assert_no_visible_leakage(path, text, FORBIDDEN_VISIBLE_TERMS)
        if expected_hash and sha256_text(text) != expected_hash:
            raise ViewSpecError(f"function artifact hash mismatch: {path}")
        blocks[trace_id] = text
    return blocks


def _load_headers(view_root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in manifest.get("artifacts", {}).get("headers", []):
        header_path = normalize_repo_path(str(item.get("header_path", "")))
        artifact_path = str(item.get("artifact_path", ""))
        expected_hash = str(item.get("sha256", ""))
        if not header_path or not artifact_path:
            raise ViewSpecError("header artifact record is missing header_path or artifact_path")
        path = view_root / artifact_path
        if not path.is_file():
            raise ViewSpecError(f"missing header artifact: {path}")
        text = path.read_text(encoding="utf-8")
        _assert_no_visible_leakage(path, text, FORBIDDEN_VISIBLE_TERMS)
        if expected_hash and sha256_text(text) != expected_hash:
            raise ViewSpecError(f"header artifact hash mismatch: {path}")
        headers[header_path] = text
    return headers


def _load_project_graph(view_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    artifact = manifest.get("artifacts", {}).get("project_graph", {})
    artifact_path = str(artifact.get("artifact_path", ""))
    expected_hash = str(artifact.get("sha256", ""))
    if not artifact_path:
        raise ViewSpecError("S2 manifest is missing project_graph artifact")
    path = view_root / artifact_path
    if not path.is_file():
        raise ViewSpecError(f"missing project graph artifact: {path}")
    text = path.read_text(encoding="utf-8")
    _assert_no_visible_leakage(path, text, S2_PROJECT_GRAPH_FORBIDDEN_TERMS)
    if expected_hash and sha256_text(text) != expected_hash:
        raise ViewSpecError(f"project graph artifact hash mismatch: {path}")
    graph = json.loads(text)
    if graph.get("visibility") != "s2_coder_visible_project_graph":
        raise ViewSpecError("S2 project graph has an unexpected visibility marker")
    return graph


def _validate_execution_manifest(view_root: Path, execution_manifest: dict[str, Any]) -> None:
    if execution_manifest.get("visibility") != "hidden_evaluator_only":
        raise ViewSpecError("execution_manifest must be hidden_evaluator_only")
    for source in execution_manifest.get("sources", []):
        if not source.get("source_path"):
            raise ViewSpecError("execution manifest source is missing source_path")
        for function in source.get("functions", []):
            artifact = str(function.get("artifact_path", ""))
            if artifact and not (view_root / artifact).is_file():
                raise ViewSpecError(f"execution manifest references missing function artifact: {artifact}")


def load_ablation_view_context(
    view_root: str | Path,
    *,
    view_name: str = "s1",
    full_spec_root: str | Path | None = None,
) -> AblationViewContext:
    normalized_view = view_name.lower().strip()
    if normalized_view not in SUPPORTED_VIEWS:
        raise UnsupportedViewError(f"unsupported ablation view '{view_name}'")

    root = Path(view_root).expanduser().resolve()
    if root.name == "specfs_projection":
        root = root.parent
    if (root / normalized_view).is_dir():
        root = root / normalized_view
    if not root.is_dir():
        raise ViewSpecError(f"view root not found: {root}")

    transformation_manifest = read_json(root / "transformation_manifest.json")
    execution_manifest = read_json(root / "execution_manifest.json")
    manifest_view = str(transformation_manifest.get("view", normalized_view)).lower().strip()
    if manifest_view != normalized_view:
        raise ViewSpecError(f"view root contains '{manifest_view}' artifacts, not '{normalized_view}'")
    if transformation_manifest.get("validation", {}).get("leakage_scan", {}).get("status") != "passed":
        raise ViewSpecError("transformation manifest leakage_scan did not pass")
    if transformation_manifest.get("validation", {}).get("source_loader", {}).get("status") != "passed":
        raise ViewSpecError("transformation manifest source_loader did not pass")
    if normalized_view == "s2" and transformation_manifest.get("validation", {}).get("s2_project_graph_leakage_scan", {}).get("status") != "passed":
        raise ViewSpecError("transformation manifest s2_project_graph_leakage_scan did not pass")
    _validate_execution_manifest(root, execution_manifest)

    source_root = resolve_repo_path(full_spec_root or transformation_manifest.get("source_root", ""))
    source_bundle = load_spec_bundle_from_root(source_root, validate_rendered_headers=False)
    if source_bundle.has_errors():
        codes = ", ".join(diag.code for diag in source_bundle.diagnostics if diag.level == "error")
        raise ViewSpecError(f"source bundle has loader errors: {codes}")

    function_blocks = _load_function_blocks(root, transformation_manifest)
    headers = _load_headers(root, transformation_manifest)
    expected = transformation_manifest.get("counts", {})
    if int(expected.get("functions", len(function_blocks))) != len(function_blocks):
        raise ViewSpecError("function artifact count mismatch")
    if int(expected.get("headers", len(headers))) != len(headers):
        raise ViewSpecError("header artifact count mismatch")
    project_graph = _load_project_graph(root, transformation_manifest) if normalized_view == "s2" else None

    return AblationViewContext(
        view_name=normalized_view,
        view_root=root,
        source_bundle=source_bundle,
        execution_manifest=execution_manifest,
        transformation_manifest=transformation_manifest,
        function_blocks_by_trace=function_blocks,
        headers_by_path=headers,
        project_graph=project_graph,
    )
