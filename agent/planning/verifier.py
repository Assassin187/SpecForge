from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..coder.specs import load_spec_bundle
from .constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN
from .models import PlanningDiagnostic, VerificationResult


FILE_LAYOUT_TOKEN_BUDGET = 30_000
CODER_BLOCKING_CODES = {
    "missing_function_spec",
    "name_mismatch",
    "signature_mismatch",
    "header_signature_mismatch",
    "header_without_source",
    "orphan_function_spec",
    "unmapped_module_file",
}


STEP_ARTIFACT_FILENAMES = {
    "planning_ir.json": "003_planning_ir.json",
    "protocol_profile.json": "004_protocol_profile.json",
    "expert_activations.json": "005_expert_activations.json",
    "candidate_architectures.json": "006_candidate_architectures.json",
    "architecture_review.json": "006_architecture_review.json",
    "implementation_plan.json": "007_implementation_plan.json",
    "implementation_plan_v2.json": "007_implementation_plan_v2.json",
    "spec_blueprint.json": "010_spec_blueprint.json",
    "expansion_candidates.json": "010_expansion_candidates.json",
    "run_manifest.json": "013_run_manifest.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _planning_artifact_path(out_dir: Path, filename: str) -> Path:
    step_path = out_dir / "_step_logs" / STEP_ARTIFACT_FILENAMES.get(filename, filename)
    if step_path.exists():
        return step_path
    return out_dir / filename


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


def _is_main_source(source_path: str) -> bool:
    return source_path == "main.c" or source_path.endswith("/main.c")


def _dependency_graph_has_cycle(edges: list[dict[str, Any]]) -> bool:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if consumer and provider:
            graph.setdefault(consumer, []).append(provider)
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


def _owned_capabilities(modules: list[dict[str, Any]]) -> set[str]:
    owned: set[str] = set()
    for module in modules:
        raw = module.get("owned_capabilities", [])
        if not isinstance(raw, list):
            continue
        owned.update(str(item) for item in raw if str(item).strip())
    return owned


def _function_rely_empty(function: dict[str, Any]) -> bool:
    rely = function.get("rely", {})
    if not isinstance(rely, dict):
        return True
    for key in ("STRUCT", "FUNC", "VAR"):
        value = rely.get(key, [])
        if isinstance(value, list) and value:
            return False
    return True


def _dependency_refs_from_item(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"GRAPH_EDGE_ID", "FILE_EDGE_ID"} and str(item).strip():
                refs.add(str(item))
            elif key in {"GRAPH_EDGE_IDS", "DEPENDENCY_REFS", "dependency_refs", "source_graph_edges"} and isinstance(item, list):
                refs.update(str(ref) for ref in item if str(ref).strip())
            else:
                refs.update(_dependency_refs_from_item(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_dependency_refs_from_item(item))
    return refs


def _schema_root() -> Path:
    return Path(__file__).resolve().parents[2] / "specs-example" / "specs_schema"


def _schema_for_kind(kind: str) -> Path | None:
    if kind == "PROTOCOL_MODULE_SPEC":
        return _schema_root() / "module_spec_schema.json"
    if kind == "FILE_SPEC":
        return _schema_root() / "file_spec_schema.json"
    if kind == "FUNCTION_SPEC":
        return _schema_root() / "function_spec_schema.json"
    return None


def _schema_diagnostics_for_spec(path: Path) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    try:
        raw = _load_json(path)
    except Exception as exc:  # noqa: BLE001
        return [PlanningDiagnostic("error", "compiled_spec_invalid_json", f"{path.name} is not valid JSON: {exc}", str(path))]
    schema_path = _schema_for_kind(str(raw.get("KIND", "")))
    if schema_path is None:
        return [PlanningDiagnostic("error", "compiled_spec_unknown_kind", f"{path.name} has unsupported KIND '{raw.get('KIND')}'", str(path))]
    try:
        schema = _load_json(schema_path)
    except Exception as exc:  # noqa: BLE001
        return [PlanningDiagnostic("error", "compiled_spec_schema_unavailable", f"Cannot load schema '{schema_path}': {exc}", str(schema_path))]
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(raw), key=lambda item: list(item.path)):
        loc = "$" + "".join(f"[{part!r}]" if isinstance(part, int) else f".{part}" for part in error.path)
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "compiled_spec_schema_error",
                f"{path.name} violates {schema_path.name} at {loc}: {error.message}",
                str(path),
            )
        )
    return diagnostics


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _requires_inbound_handler(surface: dict[str, Any], target_role: str) -> bool:
    role = str(target_role or "").lower()
    if not any(token in role for token in ("server", "broker", "responder", "listener")):
        return True
    name = str(surface.get("name", "")).lower()
    summary = str(surface.get("summary", "")).strip().lower()
    first_word = summary.split(" ", 1)[0] if summary else ""
    if name.endswith(("ack", "resp", "response")):
        return False
    if first_word in {"encode", "reply", "respond", "send"}:
        return False
    return True


def _file_layout_call_usage_diagnostics(payload: dict[str, Any], path: Path) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    for item in payload.get("llm_call_usage", []):
        if not isinstance(item, dict) or item.get("stage") != "file_layout":
            continue
        usage = item.get("usage", {})
        if not isinstance(usage, dict):
            continue
        total_tokens = _int_value(usage.get("total_tokens"))
        if total_tokens > FILE_LAYOUT_TOKEN_BUDGET:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "file_layout_token_budget_exceeded",
                    f"{path.name} records file_layout {item.get('call_type')} total_tokens={total_tokens}; per-call budget is {FILE_LAYOUT_TOKEN_BUDGET}",
                    str(path),
                )
            )
    return diagnostics


def verify_output_dir(output_dir: str | Path) -> VerificationResult:
    out_dir = Path(output_dir)
    diagnostics: list[PlanningDiagnostic] = []
    required = [
        "planning_ir.json",
        "protocol_profile.json",
        "expert_activations.json",
        "candidate_architectures.json",
        "architecture_review.json",
        "implementation_plan.json",
        "implementation_plan_v2.json",
        "spec_blueprint.json",
        "expansion_candidates.json",
        "run_manifest.json",
    ]
    for filename in required:
        path = _planning_artifact_path(out_dir, filename)
        if not path.exists():
            diagnostics.append(PlanningDiagnostic("error", "missing_artifact", f"Missing required planning artifact '{filename}'", str(path)))
    if diagnostics:
        return VerificationResult(False, diagnostics)

    implementation_plan_path = _planning_artifact_path(out_dir, "implementation_plan.json")
    protocol_profile_path = _planning_artifact_path(out_dir, "protocol_profile.json")
    spec_blueprint_path = _planning_artifact_path(out_dir, "spec_blueprint.json")
    run_manifest_path = _planning_artifact_path(out_dir, "run_manifest.json")
    implementation_plan = _load_json(implementation_plan_path)
    protocol_profile = _load_json(protocol_profile_path)
    spec_blueprint = _load_json(spec_blueprint_path)
    run_manifest = _load_json(run_manifest_path)
    module_graph = list(implementation_plan.get("module_graph", []))
    canonical_types = list(implementation_plan.get("canonical_types", []))
    handler_matrix = list(implementation_plan.get("handler_matrix", []))
    dependency_graph = implementation_plan.get("dependency_graph", {})
    dependency_graph = dependency_graph if isinstance(dependency_graph, dict) else {}
    file_layout = implementation_plan.get("file_layout", {})
    file_layout = file_layout if isinstance(file_layout, dict) else {}
    module_edges = [item for item in dependency_graph.get("module_edges", []) if isinstance(item, dict)]
    target_role = str(implementation_plan.get("target_profile", {}).get("target_role", ""))
    minimum_surface = {
        str(item.get("name"))
        for item in implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", [])
        if isinstance(item, dict) and item.get("name") and _requires_inbound_handler(item, target_role)
    }

    owners = {}
    for item in canonical_types:
        type_name = str(item.get("type_name", ""))
        owner = str(item.get("owner_module", ""))
        if not type_name:
            continue
        if type_name in owners and owners[type_name] != owner:
            diagnostics.append(PlanningDiagnostic("error", "duplicate_canonical_owner", f"Type '{type_name}' has multiple owners", str(implementation_plan_path)))
        owners[type_name] = owner

    if _has_cycle(module_graph):
        diagnostics.append(PlanningDiagnostic("error", "module_dependency_cycle", "Module graph contains dependency cycle", str(implementation_plan_path)))
    if _dependency_graph_has_cycle(module_edges):
        diagnostics.append(PlanningDiagnostic("error", "dependency_graph_cycle", "dependency_graph.module_edges contains a dependency cycle", str(implementation_plan_path)))
    if not (MODULE_BUDGET_MIN <= len(module_graph) <= MODULE_BUDGET_MAX):
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "module_budget_violation",
                f"Module graph has {len(module_graph)} modules; expected {MODULE_BUDGET_MIN}-{MODULE_BUDGET_MAX}",
                str(implementation_plan_path),
            )
        )
    required_capabilities = {str(item) for item in protocol_profile.get("required_capabilities", []) if str(item).strip()}
    capability_gaps = sorted(required_capabilities - _owned_capabilities(module_graph))
    if capability_gaps:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "capability_coverage_gap",
                f"Module graph does not assign required capabilities: {', '.join(capability_gaps)}",
                str(implementation_plan_path),
            )
        )

    module_names = {str(item.get("name")) for item in module_graph if isinstance(item, dict)}
    layout_files = [item for item in file_layout.get("files", []) if isinstance(item, dict)]
    layout_file_ids = [str(item.get("file_id")) for item in layout_files if item.get("file_id")]
    layout_file_id_set = set(layout_file_ids)
    owner_file_by_module = {
        str(item.get("owner_module")): str(item.get("owner_file"))
        for item in canonical_types
        if isinstance(item, dict) and item.get("owner_module") and item.get("owner_file")
    }
    if not file_layout:
        diagnostics.append(PlanningDiagnostic("error", "missing_file_layout", "implementation_plan.file_layout is required", str(implementation_plan_path)))
    layout_validation = file_layout.get("validation", {})
    layout_validation = layout_validation if isinstance(layout_validation, dict) else {}
    if file_layout:
        if str(file_layout.get("origin", "")) != "llm" or str(layout_validation.get("origin", "")) != "llm":
            diagnostics.append(PlanningDiagnostic("error", "file_layout_not_llm_origin", "implementation_plan.file_layout must be an accepted LLM proposal", str(implementation_plan_path)))
        if layout_validation.get("fallback_used") is True:
            diagnostics.append(PlanningDiagnostic("error", "file_layout_fallback_used", "file_layout.validation.fallback_used is not allowed in LLM-only layout mode", str(implementation_plan_path)))
        if layout_validation.get("accepted") is not True:
            diagnostics.append(PlanningDiagnostic("error", "file_layout_not_accepted", "file_layout.validation.accepted must be true", str(implementation_plan_path)))
        if _int_value(layout_validation.get("attempt_count")) < 1:
            diagnostics.append(PlanningDiagnostic("error", "file_layout_attempts_missing", "file_layout.validation.attempt_count must record at least one LLM attempt", str(implementation_plan_path)))
        if not isinstance(layout_validation.get("attempts"), list) or not layout_validation.get("attempts"):
            diagnostics.append(PlanningDiagnostic("error", "file_layout_attempt_log_missing", "file_layout.validation.attempts must record each LLM layout attempt", str(implementation_plan_path)))
    diagnostics.extend(_file_layout_call_usage_diagnostics(run_manifest, run_manifest_path))
    token_logs = [
        path
        for path in (
            out_dir / "_step_logs" / "013_token_usage_summary.json",
            *sorted((out_dir / "_agent_logs").glob("*token_usage_summary*.json")),
        )
        if path.exists()
    ]
    if token_logs:
        try:
            token_summary = _load_json(token_logs[-1])
            diagnostics.extend(_file_layout_call_usage_diagnostics(token_summary, token_logs[-1]))
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(PlanningDiagnostic("error", "token_summary_unreadable", f"Cannot read token usage summary: {exc}", str(token_logs[-1])))
    if len(layout_file_ids) != len(layout_file_id_set):
        diagnostics.append(PlanningDiagnostic("error", "duplicate_file_layout_id", "file_layout.files contains duplicate file_id values", str(implementation_plan_path)))
    source_paths = [str(item.get("source_path") or item.get("path")) for item in layout_files if item.get("source_path") or item.get("path")]
    header_paths = [str(item.get("owns_header") or item.get("header_path")) for item in layout_files if item.get("owns_header") or item.get("header_path")]
    if len(source_paths) != len(set(source_paths)):
        diagnostics.append(PlanningDiagnostic("error", "duplicate_file_layout_source", "file_layout.files contains duplicate source paths", str(implementation_plan_path)))
    if len(header_paths) != len(set(header_paths)):
        diagnostics.append(PlanningDiagnostic("error", "duplicate_file_layout_header", "file_layout.files contains duplicate owned headers", str(implementation_plan_path)))
    files_by_layout_module: dict[str, list[dict[str, Any]]] = {}
    for item in layout_files:
        module_name = str(item.get("module", ""))
        source_path = str(item.get("source_path") or item.get("path") or "")
        header_path = str(item.get("owns_header") or item.get("header_path") or "").strip()
        if module_name and module_name not in module_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_file_layout_module", f"file_layout file '{item.get('file_id')}' references unknown module '{module_name}'", str(implementation_plan_path)))
        if _is_main_source(source_path):
            if header_path:
                diagnostics.append(PlanningDiagnostic("error", "main_file_layout_header", f"main source file '{item.get('file_id')}' must not own a header", str(implementation_plan_path)))
        elif not header_path:
            diagnostics.append(PlanningDiagnostic("error", "missing_file_layout_header", f"non-main file_layout file '{item.get('file_id')}' must own a header", str(implementation_plan_path)))
        files_by_layout_module.setdefault(module_name, []).append(item)
    for module_name in module_names:
        module_files = files_by_layout_module.get(module_name, [])
        if not module_files:
            diagnostics.append(PlanningDiagnostic("error", "missing_module_file_layout", f"Module '{module_name}' has no file_layout.files entry", str(implementation_plan_path)))
        if not any(item.get("owns_header") or item.get("header_path") for item in module_files):
            diagnostics.append(PlanningDiagnostic("error", "missing_module_header_owner", f"Module '{module_name}' has no file_layout header owner", str(implementation_plan_path)))
        expected_header = owner_file_by_module.get(module_name)
        if expected_header:
            owned_headers = [str(item.get("owns_header") or item.get("header_path")) for item in module_files if item.get("owns_header") or item.get("header_path")]
            if owned_headers.count(expected_header) != 1:
                diagnostics.append(PlanningDiagnostic("error", "missing_canonical_file_layout_header", f"Module '{module_name}' must own exactly one canonical header '{expected_header}'", str(implementation_plan_path)))
    for edge in file_layout.get("file_edges", []):
        if not isinstance(edge, dict):
            diagnostics.append(PlanningDiagnostic("error", "invalid_file_edge", "file_layout.file_edges must contain objects", str(implementation_plan_path)))
            continue
        for key in ("consumer_file", "provider_file"):
            if str(edge.get(key, "")) not in layout_file_id_set:
                diagnostics.append(PlanningDiagnostic("error", "unknown_file_edge_file", f"file_layout edge references unknown {key} '{edge.get(key)}'", str(implementation_plan_path)))
    if len(module_names) > 1 and not module_edges:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "dependency_graph_empty",
                "dependency_graph.module_edges is empty for a multi-module implementation plan",
                str(implementation_plan_path),
            )
        )
    expected_deps: dict[str, set[str]] = {name: set() for name in module_names}
    for edge in module_edges:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if consumer not in module_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_consumer", f"Dependency edge references unknown consumer module '{consumer}'", str(implementation_plan_path)))
            continue
        if provider not in module_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_provider", f"Dependency edge references unknown provider module '{provider}'", str(implementation_plan_path)))
            continue
        if consumer == provider:
            diagnostics.append(PlanningDiagnostic("error", "self_module_dependency", f"Module '{consumer}' depends on itself", str(implementation_plan_path)))
            continue
        expected_deps.setdefault(consumer, set()).add(provider)
    for module in module_graph:
        name = str(module.get("name", ""))
        actual = {str(dep) for dep in module.get("dependencies", []) if str(dep).strip()}
        expected = expected_deps.get(name, set())
        if actual != expected:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "module_dependency_mismatch",
                    f"Module '{name}' dependencies {sorted(actual)} do not match dependency_graph providers {sorted(expected)}",
                    str(implementation_plan_path),
                )
            )

    handled = {str(item.get("surface_unit")) for item in handler_matrix if isinstance(item, dict)}
    for name in sorted(minimum_surface):
        if name not in handled:
            diagnostics.append(PlanningDiagnostic("error", "minimum_v1_coverage_gap", f"Surface '{name}' missing from handler_matrix", str(implementation_plan_path)))

    blueprint_kind = str(spec_blueprint.get("kind", ""))
    if blueprint_kind != "SPEC_BLUEPRINT":
        diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_kind", "spec_blueprint.json must have kind='SPEC_BLUEPRINT'", str(spec_blueprint_path)))

    for section in ("modules", "files", "functions"):
        items = spec_blueprint.get(section, [])
        if not isinstance(items, list):
            diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_section", f"Blueprint section '{section}' must be a list", str(spec_blueprint_path)))
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                diagnostics.append(PlanningDiagnostic("error", "invalid_blueprint_item", f"Blueprint item '{section}[{idx}]' must be an object", str(spec_blueprint_path)))
                continue
            has_trace = any(item.get(key) for key in ("evidence_refs", "decision_refs", "profile_refs"))
            if not has_trace:
                ident = item.get("trace_id") or item.get("name") or idx
                diagnostics.append(PlanningDiagnostic("error", "missing_blueprint_traceability", f"Blueprint {section} item '{ident}' has no evidence_refs, decision_refs, or profile_refs", str(spec_blueprint_path)))

    blueprint_functions = [item for item in spec_blueprint.get("functions", []) if isinstance(item, dict)]
    function_names = {str(item.get("name")) for item in blueprint_functions if item.get("name")}
    placements = [item for item in file_layout.get("function_placement", []) if isinstance(item, dict)]
    placed_functions = [str(item.get("function_name")) for item in placements if item.get("function_name")]
    if len(placed_functions) != len(set(placed_functions)):
        diagnostics.append(PlanningDiagnostic("error", "duplicate_function_placement", "file_layout.function_placement contains duplicate functions", str(implementation_plan_path)))
    missing_placements = sorted(function_names - set(placed_functions))
    if missing_placements:
        diagnostics.append(PlanningDiagnostic("error", "missing_function_placement", f"Functions missing from file_layout.function_placement: {', '.join(missing_placements)}", str(implementation_plan_path)))
    unknown_placements = sorted(set(placed_functions) - function_names)
    if unknown_placements:
        diagnostics.append(PlanningDiagnostic("error", "unknown_function_placement", f"file_layout.function_placement references unknown functions: {', '.join(unknown_placements)}", str(implementation_plan_path)))
    for placement in placements:
        if str(placement.get("file_id", "")) not in layout_file_id_set:
            diagnostics.append(PlanningDiagnostic("error", "unknown_function_placement_file", f"Function placement for '{placement.get('function_name')}' references unknown file_id '{placement.get('file_id')}'", str(implementation_plan_path)))
    canonical_type_names = {str(item.get("type_name")) for item in canonical_types if isinstance(item, dict) and item.get("type_name")}
    for edge in dependency_graph.get("function_edges", []):
        if not isinstance(edge, dict):
            continue
        caller = str(edge.get("caller", ""))
        callee = str(edge.get("callee", ""))
        callee_kind = str(edge.get("callee_kind", ""))
        if caller and caller not in function_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_caller", f"Function edge references unknown caller '{caller}'", str(implementation_plan_path)))
        if callee and callee not in function_names and callee_kind not in {"external", "runtime_primitive"}:
            diagnostics.append(PlanningDiagnostic("error", "unknown_dependency_callee", f"Function edge references unknown callee '{callee}'", str(implementation_plan_path)))
    for edge in dependency_graph.get("data_edges", []):
        if not isinstance(edge, dict):
            continue
        function = str(edge.get("function", ""))
        provider = str(edge.get("provider_module", ""))
        struct = str(edge.get("struct", ""))
        if function and function not in function_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_data_edge_function", f"Data edge references unknown function '{function}'", str(implementation_plan_path)))
        if provider and provider not in module_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_data_edge_provider", f"Data edge references unknown provider module '{provider}'", str(implementation_plan_path)))
        if struct and struct not in canonical_type_names:
            diagnostics.append(PlanningDiagnostic("error", "unknown_data_edge_struct", f"Data edge references unknown struct/type '{struct}'", str(implementation_plan_path)))
    deps_by_module = {str(item.get("name")): set(str(dep) for dep in item.get("dependencies", []) if str(dep).strip()) for item in module_graph}
    for function in blueprint_functions:
        module = str(function.get("module", ""))
        if len(module_names) > 1 and str(function.get("function_type")) == "EVENT" and deps_by_module.get(module) and _function_rely_empty(function):
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "empty_handler_rely",
                    f"Handler function '{function.get('name')}' has empty RELY despite module dependencies",
                    str(spec_blueprint_path),
                )
            )
    owner_file_by_module = {
        str(item.get("owner_module")): str(item.get("owner_file"))
        for item in canonical_types
        if isinstance(item, dict) and item.get("owner_module") and item.get("owner_file")
    }
    files_by_module: dict[str, list[dict[str, Any]]] = {}
    for item in spec_blueprint.get("files", []):
        if isinstance(item, dict) and item.get("module"):
            files_by_module.setdefault(str(item.get("module")), []).append(item)
    for edge in module_edges:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        file_items = files_by_module.get(consumer, [])
        provider_header = owner_file_by_module.get(provider)
        if not file_items or not provider_header:
            continue
        source_deps = {
            str(dep)
            for file_item in file_items
            for dep in file_item.get("source_dependencies", [])
            if str(dep).strip()
        }
        if provider_header not in source_deps:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "missing_source_dependency",
                    f"Source for module '{consumer}' does not include provider header '{provider_header}'",
                    str(spec_blueprint_path),
                )
            )

    graph_edge_ids: set[str] = set()
    for section in ("module_edges", "function_edges", "data_edges"):
        for edge in dependency_graph.get(section, []):
            if isinstance(edge, dict) and edge.get("edge_id"):
                graph_edge_ids.add(str(edge["edge_id"]))
    projected_refs = _dependency_refs_from_item(spec_blueprint.get("modules", []))
    projected_refs.update(_dependency_refs_from_item(spec_blueprint.get("files", [])))
    projected_refs.update(_dependency_refs_from_item(spec_blueprint.get("functions", [])))
    missing_projection = sorted(graph_edge_ids - projected_refs)
    if missing_projection:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "unprojected_dependency_graph_edges",
                f"Dependency graph edges are not projected into specs: {', '.join(missing_projection)}",
                str(spec_blueprint_path),
            )
        )

    spec_bundle_dir = out_dir / "spec_bundle"
    module_specs = list(spec_bundle_dir.glob("*_module_spec.json"))
    if not module_specs:
        diagnostics.append(PlanningDiagnostic("error", "missing_compiled_spec", "Missing compiled module spec", str(spec_bundle_dir)))
        return VerificationResult(False, diagnostics)
    for spec_path in sorted(spec_bundle_dir.rglob("*_spec.json")):
        diagnostics.extend(_schema_diagnostics_for_spec(spec_path))
    bundle = load_spec_bundle(module_specs[0], spec_bundle_dir)
    for diag in bundle.diagnostics:
        level = "error" if diag.level == "error" or diag.code in CODER_BLOCKING_CODES else "warning"
        diagnostics.append(PlanningDiagnostic(level, f"coder_{diag.code}", diag.message, diag.path))
    return VerificationResult(not any(diag.level == "error" for diag in diagnostics), diagnostics)
