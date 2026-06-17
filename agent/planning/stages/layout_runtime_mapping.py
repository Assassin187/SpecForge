from __future__ import annotations

from typing import Any

from ..diagnostics import PlanningDiagnostic


REQUIRED_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "codec/parser/serializer": ("codec", "parser", "serializer", "decode", "decoder", "encode", "encoder", "framing", "packet"),
    "session/state": ("session", "state", "state_machine"),
    "transport/network": ("transport", "network", "tcp", "udp", "connection", "socket", "epoll"),
    "router/topic/resource": ("router", "route", "routing", "topic", "resource", "subscribe", "subscription"),
    "broker/app/server": ("broker", "app", "server", "client", "role_composition", "application"),
    "timer/lifecycle": ("timer", "timeout", "keepalive", "keep_alive", "schedule", "lifecycle"),
    "error handling": ("error", "malformed", "invalid", "validation", "cleanup", "recovery"),
    "runtime entrypoint": ("runtime_entrypoint", "entrypoint", "main.c", " main "),
}


def build_layout_runtime_mapping_report(plan: dict[str, Any]) -> tuple[dict[str, Any], list[PlanningDiagnostic]]:
    rows = [_role_mapping(role, aliases, plan) for role, aliases in REQUIRED_ROLE_ALIASES.items()]
    diagnostics = _diagnostics_for_rows(rows)
    return {
        "schema_version": "layout_runtime_mapping_report/v1",
        "status": "failed" if any(item.level == "error" for item in diagnostics) else "passed",
        "required_roles": rows,
        "diagnostics": [
            {"level": item.level, "code": item.code, "message": item.message, "path": item.path}
            for item in diagnostics
        ],
        "summary": {
            "required_role_count": len(rows),
            "mapped_role_count": sum(1 for row in rows if row["status"] in {"mapped", "merged"}),
            "merged_role_count": sum(1 for row in rows if row["status"] == "merged"),
        },
    }, diagnostics


def validate_layout_runtime_mapping_report(report: dict[str, Any], *, path: str | None = None) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    if report.get("schema_version") != "layout_runtime_mapping_report/v1":
        diagnostics.append(PlanningDiagnostic("error", "invalid_layout_runtime_mapping_report", "layout/runtime mapping report schema_version must be layout_runtime_mapping_report/v1", path))
        return diagnostics
    for row in report.get("required_roles", []) if isinstance(report.get("required_roles", []), list) else []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip()
        status = str(row.get("status", "")).strip()
        if status == "missing":
            diagnostics.append(PlanningDiagnostic("error", "layout_required_role_unmapped", f"required role '{role}' has no owning module/file mapping", path))
        if status == "merged" and not str(row.get("merged_role_explanation", "")).strip():
            diagnostics.append(PlanningDiagnostic("warning", "layout_merged_role_missing_explanation", f"merged role '{role}' lacks explicit explanation", path))
    return diagnostics


def _role_mapping(role: str, aliases: tuple[str, ...], plan: dict[str, Any]) -> dict[str, Any]:
    if role == "runtime entrypoint":
        return _runtime_entrypoint_mapping(role, plan)
    modules = [item for item in plan.get("module_artifacts", []) if isinstance(item, dict)]
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, module in enumerate(modules):
        module_id = str(module.get("module_id", "")).strip()
        text = _module_text(module)
        score = sum(1 for alias in aliases if alias in text)
        if not score:
            continue
        module_files = [item for item in files if str(item.get("module_id", "")).strip() == module_id]
        module_functions = [item for item in functions if str(item.get("module_id", "")).strip() == module_id]
        scored.append((score, -index, {"module": module, "files": module_files, "functions": module_functions}))
    if not scored:
        return _missing_row(role, aliases)
    selected = max(scored, key=lambda item: (item[0], item[1]))[2]
    module = selected["module"]
    module_id = str(module.get("module_id", "")).strip()
    files = selected["files"]
    functions = selected["functions"]
    owning_file = _best_file(role, aliases, files, functions)
    public_api = [
        {
            "function_id": str(function.get("function_id", "")),
            "name": str(function.get("name", "")),
            "file_id": str(function.get("file_id", "")),
        }
        for function in functions
        if _is_public(function) and _matches_any(_function_text(function), aliases)
    ][:12]
    if not public_api:
        public_api = [
            {
                "function_id": str(function.get("function_id", "")),
                "name": str(function.get("name", "")),
                "file_id": str(function.get("file_id", "")),
            }
            for function in functions
            if _is_public(function)
        ][:8]
    merged_aliases = _matched_aliases(_module_text(module), aliases)
    return {
        "role": role,
        "status": "merged" if len(merged_aliases) > 1 else "mapped",
        "owning_module": module_id,
        "owning_file": str(owning_file.get("file_id", "")),
        "owning_header": str(owning_file.get("header_path", "")),
        "owning_source": str(owning_file.get("source_path") or owning_file.get("path") or ""),
        "exported_public_api": public_api,
        "dependency_relationship": _dependency_relationship(module, module_id, plan),
        "missing_role_explanation": "",
        "merged_role_explanation": _merged_explanation(module_id, role, merged_aliases),
    }


def _runtime_entrypoint_mapping(role: str, plan: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in plan.get("file_layout", {}).get("files", []) if isinstance(item, dict)]
    functions = [item for item in plan.get("function_contracts", []) if isinstance(item, dict)]
    main_file = next((item for item in files if str(item.get("source_path") or item.get("path") or "").replace("\\", "/").endswith("main.c")), {})
    entrypoint = next(
        (
            function
            for function in functions
            if str(function.get("coder_function_type", "")).upper() == "ENTRYPOINT"
            and str((function.get("signature", {}) if isinstance(function.get("signature"), dict) else {}).get("name") or function.get("name", "")) == "main"
        ),
        {},
    )
    if not main_file or not entrypoint:
        return _missing_row(role, REQUIRED_ROLE_ALIASES[role])
    return {
        "role": role,
        "status": "mapped",
        "owning_module": str(main_file.get("module_id") or entrypoint.get("module_id") or ""),
        "owning_file": str(main_file.get("file_id", "")),
        "owning_header": "",
        "owning_source": str(main_file.get("source_path") or main_file.get("path") or ""),
        "exported_public_api": [],
        "dependency_relationship": {
            "imports_allowed": [str(item) for item in main_file.get("imports_allowed", []) if str(item).strip()],
            "calls_allowed": [str(item) for item in entrypoint.get("calls_allowed", []) if str(item).strip()],
        },
        "missing_role_explanation": "",
        "merged_role_explanation": "",
    }


def _diagnostics_for_rows(rows: list[dict[str, Any]]) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    for row in rows:
        role = str(row.get("role", "")).strip()
        if row.get("status") == "missing":
            diagnostics.append(PlanningDiagnostic("error", "layout_required_role_unmapped", f"required role '{role}' has no owning module/file mapping"))
        if row.get("status") == "merged" and not str(row.get("merged_role_explanation", "")).strip():
            diagnostics.append(PlanningDiagnostic("warning", "layout_merged_role_missing_explanation", f"merged role '{role}' lacks explicit explanation"))
    return diagnostics


def _missing_row(role: str, aliases: tuple[str, ...]) -> dict[str, Any]:
    return {
        "role": role,
        "status": "missing",
        "owning_module": "",
        "owning_file": "",
        "owning_header": "",
        "owning_source": "",
        "exported_public_api": [],
        "dependency_relationship": {},
        "missing_role_explanation": f"No module/file text matched required aliases: {', '.join(aliases)}.",
        "merged_role_explanation": "",
    }


def _module_text(module: dict[str, Any]) -> str:
    pieces = [
        module.get("module_id", ""),
        module.get("name", ""),
        module.get("role", ""),
        module.get("purpose", ""),
        " ".join(str(item) for item in module.get("owned_capabilities", [])),
        " ".join(str(item) for item in module.get("dependencies", [])),
    ]
    for artifact in module.get("artifacts", []) if isinstance(module.get("artifacts", []), list) else []:
        if isinstance(artifact, dict):
            pieces.extend([artifact.get("name", ""), artifact.get("role", "")])
    return " ".join(str(item) for item in pieces).lower()


def _function_text(function: dict[str, Any]) -> str:
    return " ".join(
        str(function.get(key, ""))
        for key in ("function_id", "name", "purpose", "function_kind", "public_api_role", "grouping_hint")
    ).lower()


def _file_text(file_item: dict[str, Any]) -> str:
    return " ".join(
        str(file_item.get(key, ""))
        for key in ("file_id", "source_path", "header_path", "path", "responsibility")
    ).lower()


def _best_file(role: str, aliases: tuple[str, ...], files: list[dict[str, Any]], functions: list[dict[str, Any]]) -> dict[str, Any]:
    if not files:
        return {}
    scores: list[tuple[int, int, dict[str, Any]]] = []
    function_file_scores: dict[str, int] = {}
    for function in functions:
        if _matches_any(_function_text(function), aliases):
            file_id = str(function.get("file_id", ""))
            function_file_scores[file_id] = function_file_scores.get(file_id, 0) + 2
    for index, file_item in enumerate(files):
        text = _file_text(file_item)
        score = sum(1 for alias in aliases if alias in text) + function_file_scores.get(str(file_item.get("file_id", "")), 0)
        if role == "runtime entrypoint" and str(file_item.get("source_path") or file_item.get("path") or "").endswith("main.c"):
            score += 10
        scores.append((score, -index, file_item))
    return max(scores, key=lambda item: (item[0], item[1]))[2]


def _dependency_relationship(module: dict[str, Any], module_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    graph = plan.get("dependency_graph", {}) if isinstance(plan.get("dependency_graph"), dict) else {}
    return {
        "module_dependencies": [str(item) for item in module.get("dependencies", []) if str(item).strip()],
        "incoming_module_edges": [
            edge
            for edge in graph.get("module_edges", [])
            if isinstance(edge, dict) and str(edge.get("to", "")) == module_id
        ],
        "outgoing_module_edges": [
            edge
            for edge in graph.get("module_edges", [])
            if isinstance(edge, dict) and str(edge.get("from", "")) == module_id
        ],
    }


def _matches_any(text: str, aliases: tuple[str, ...]) -> bool:
    return any(alias in text for alias in aliases)


def _matched_aliases(text: str, aliases: tuple[str, ...]) -> list[str]:
    return [alias for alias in aliases if alias in text]


def _merged_explanation(module_id: str, role: str, matched_aliases: list[str]) -> str:
    if len(matched_aliases) <= 1:
        return ""
    return f"module '{module_id}' maps merged role '{role}' because module id/name/capability/artifact text matches aliases: {', '.join(matched_aliases)}."


def _is_public(function: dict[str, Any]) -> bool:
    return (
        bool(function.get("exported"))
        or str(function.get("visibility", "")).lower() == "public"
        or str(function.get("api_surface", "")).lower() == "public"
    )
