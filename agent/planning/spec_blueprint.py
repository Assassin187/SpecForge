from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import PlanningIR, SpecBlueprint, TargetProfile


BLUEPRINT_SCHEMA = "spec_blueprint/v1alpha1"
GENERIC_C_PROFILE = "generic_c_from_plan"


def _safe_slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_") or "x"


def _decision_ids(decisions: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("decision_id")) for item in decisions if isinstance(item, dict) and item.get("decision_id")]


def _profile_ref(profile_id: str, name: str) -> str:
    return f"{profile_id}:{name}"


def _signature(return_type: str, name: str, params: list[dict[str, Any]]) -> str:
    raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}".strip() for item in params) or "void"
    return f"{return_type} {name}({raw_params})"


def _function_interface(function: dict[str, Any]) -> dict[str, Any]:
    signature = dict(function.get("signature", {}))
    return {
        "TRACE_ID": str(function.get("trace_id", "")),
        "SIGNATURE": str(signature.get("RAW", "")),
        "NAME": str(function.get("name", "")),
        "KIND": "FUNC",
        "ROLE": str(function.get("role", "")),
        "VISIBILITY": str(function.get("visibility", "public")).upper(),
    }


def _header_interface(function: dict[str, Any]) -> dict[str, Any]:
    source = _function_interface(function)
    return {
        "SIGNATURE": source["SIGNATURE"],
        "NAME": source["NAME"],
        "KIND": source["KIND"],
        "FUNCTION_TYPE": str(function.get("function_type", "ALGORITHM")),
        "ROLE": source["ROLE"],
        "VISIBILITY": source["VISIBILITY"],
    }


def _build_modules(
    implementation_plan: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for module in implementation_plan.get("module_graph", []):
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        modules.append(
            {
                "name": name,
                "role": str(module.get("role", "")),
                "dependencies": [str(item) for item in module.get("dependencies", [])],
                "files": [f"{path}.h", f"{path}.c"],
                "artifacts": list(module.get("artifacts", [])),
                "evidence_refs": list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "module_from_implementation_plan")],
            }
        )
    return modules


def _build_files(
    implementation_plan: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    canonical_by_module = {
        str(item.get("owner_module")): str(item.get("type_name"))
        for item in implementation_plan.get("canonical_types", [])
        if isinstance(item, dict) and item.get("owner_module") and item.get("type_name")
    }
    for module in implementation_plan.get("module_graph", []):
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        trace_id = f"{protocol_slug}/{name}/{name}"
        public_type = canonical_by_module.get(name, f"{protocol_slug}_{name}_t")
        files.append(
            {
                "trace_id": trace_id,
                "module": name,
                "lang": "C",
                "role": str(module.get("role", "")),
                "header_path": f"{path}.h",
                "source_path": f"{path}.c",
                "header_dependencies": [],
                "source_dependencies": [f"{path}.h"],
                "header_data": [
                    {
                        "NAME": public_type,
                        "KIND": "TYPE",
                        "VISIBILITY": "PUBLIC",
                        "ROLE": f"Opaque handle for {name} module state",
                    }
                ],
                "source_data": [
                    {
                        "NAME": f"struct {public_type.rstrip('_t')}",
                        "KIND": "TYPE",
                        "VISIBILITY": "PRIVATE",
                        "ROLE": f"Private state owned by the {name} module",
                    }
                ],
                "header_interfaces": [],
                "source_interfaces": [],
                "evidence_refs": list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "file_from_module_graph")],
            }
        )
    return files


def _build_lifecycle_functions(
    module_graph: list[dict[str, Any]],
    canonical_by_module: dict[str, str],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for module in module_graph:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        trace_id = f"{protocol_slug}/{name}/{name}"
        public_type = canonical_by_module.get(name, f"{protocol_slug}_{name}_t")
        for suffix, return_type, params, role in (
            ("create", f"{public_type}*", [], f"Create and initialize {name} module state"),
            (
                "destroy",
                "void",
                [{"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": True, "OWNERSHIP": "BORROWED"}],
                f"Release resources owned by {name} module state",
            ),
        ):
            fname = f"{protocol_slug}_{name}_{suffix}"
            functions.append(
                {
                    "trace_id": f"{trace_id}/{fname}",
                    "module": name,
                    "file_trace_id": trace_id,
                    "name": fname,
                    "function_type": "ALGORITHM",
                    "signature": {"RAW": _signature(return_type, fname, params), "NAME": fname, "RETURN": return_type, "PARAMS": params},
                    "role": role,
                    "visibility": "public",
                    "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                    "logic": {
                        "INPUT": "module construction inputs",
                        "ACTION": role,
                        "OUTPUT": "module handle or cleanup side effect",
                        "INVARIANTS_USED": ["Preserve implementation_plan canonical ownership boundaries"],
                    },
                    "evidence_refs": list(module.get("evidence_refs", [])),
                    "decision_refs": decision_refs,
                    "profile_refs": [_profile_ref(profile_id, "lifecycle_function")],
                }
            )
    return functions


def _build_handler_functions(
    handler_matrix: list[dict[str, Any]],
    files: list[dict[str, Any]],
    canonical_by_module: dict[str, str],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    file_trace_by_module = {str(item.get("module")): str(item.get("trace_id")) for item in files}
    for row in handler_matrix:
        if not isinstance(row, dict):
            continue
        module = str(row.get("handler_module", "handler_dispatch"))
        file_trace_id = file_trace_by_module.get(module, f"{protocol_slug}/{module}/{module}")
        public_type = canonical_by_module.get(module, f"{protocol_slug}_{module}_t")
        surface = str(row.get("surface_unit", "surface"))
        fname = str(row.get("handler_function") or f"{protocol_slug}_{module}_handle_{_safe_slug(surface)}")
        params = [
            {"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            {"TYPE": "const void*", "NAME": "message", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
        functions.append(
            {
                "trace_id": f"{file_trace_id}/{fname}",
                "module": module,
                "file_trace_id": file_trace_id,
                "name": fname,
                "function_type": "EVENT",
                "signature": {"RAW": _signature("int", fname, params), "NAME": fname, "RETURN": "int", "PARAMS": params},
                "role": f"Handle protocol surface '{surface}' according to implementation_plan.handler_matrix",
                "visibility": "public",
                "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                "event": {
                    "TRIGGER": f"decoded protocol surface {surface}",
                    "PRECONDITION": "decoded message and module state are valid",
                    "INPUT": f"decoded {surface} message and module state",
                    "ACTION": str(row.get("path_summary", "decode -> handler -> policy/store")),
                    "STATE_CHANGE": "Apply state/resource updates required by the implementation plan",
                    "RESPONSE": "0 on success, non-zero on protocol or processing failure",
                    "EVENT_TYPE": "PROTOCOL_HANDLER",
                },
                "evidence_refs": list(row.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "handler_function_from_handler_matrix")],
            }
        )
    return functions


def _attach_interfaces(files: list[dict[str, Any]], functions: list[dict[str, Any]]) -> None:
    files_by_trace = {str(item.get("trace_id")): item for item in files}
    for function in functions:
        file_item = files_by_trace.get(str(function.get("file_trace_id", "")))
        if file_item is None:
            continue
        file_item.setdefault("source_interfaces", []).append(_function_interface(function))
        if str(function.get("visibility", "public")).lower() == "public":
            file_item.setdefault("header_interfaces", []).append(_header_interface(function))


def _build_plan_driven_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    target_profile: TargetProfile,
) -> SpecBlueprint:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    profile_id = GENERIC_C_PROFILE
    decision_refs = _decision_ids(decisions)
    module_graph = [item for item in implementation_plan.get("module_graph", []) if isinstance(item, dict)]
    handler_matrix = [item for item in implementation_plan.get("handler_matrix", []) if isinstance(item, dict)]
    canonical_by_module = {
        str(item.get("owner_module")): str(item.get("type_name"))
        for item in implementation_plan.get("canonical_types", [])
        if isinstance(item, dict) and item.get("owner_module") and item.get("type_name")
    }
    modules = _build_modules(implementation_plan, protocol_slug, decision_refs, profile_id)
    files = _build_files(implementation_plan, protocol_slug, decision_refs, profile_id)
    functions = _build_lifecycle_functions(module_graph, canonical_by_module, protocol_slug, decision_refs, profile_id)
    functions.extend(_build_handler_functions(handler_matrix, files, canonical_by_module, protocol_slug, decision_refs, profile_id))
    _attach_interfaces(files, functions)
    data = {
        "kind": "SPEC_BLUEPRINT",
        "schema_version": BLUEPRINT_SCHEMA,
        "protocol_name": planning_ir.protocol_name,
        "target_profile": target_profile.raw,
        "expansion_profile": profile_id,
        "source": "implementation_plan_v2",
        "implementation_plan_ref": "implementation_plan_v2.json",
        "generation_order": [item["name"] for item in modules],
        "consistency_rules": [
            {"NAME": "unique_public_type_owner", "DESC": "Every canonical public type must have one owner only."},
            {"NAME": "acyclic_module_dependencies", "DESC": "Module dependencies must be forward-safe for current coder."},
            {"NAME": "handler_matrix_surface_coverage", "DESC": "Every required surface in scope_decisions must map to a handler function."},
        ],
        "modules": modules,
        "files": files,
        "functions": functions,
        "coverage": {
            "module_count": len(modules),
            "file_spec_count": len(files),
            "function_spec_count": len(functions),
            "required_surface_count": len(implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", [])),
            "handler_function_count": len(handler_matrix),
        },
        "traceability": {
            "decision_refs": decision_refs,
            "source_artifacts": ["protocol_facts.json", "target_profile.json", "implementation_plan_v2.json", "design_decisions.json"],
        },
    }
    return SpecBlueprint(data)


def build_spec_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    target_profile: TargetProfile,
    llm_client: Any | None = None,
) -> tuple[SpecBlueprint, list[dict[str, Any]]]:
    del llm_client
    blueprint = _build_plan_driven_blueprint(planning_ir, implementation_plan, decisions, target_profile)
    candidates = [
        {
            "candidate_id": f"profile:{GENERIC_C_PROFILE}",
            "origin": "implementation_plan_v2",
            "selected": True,
            "expansion_profile": blueprint.data["expansion_profile"],
            "selection_score": 1.0,
            "applicable": True,
            "coverage": blueprint.data.get("coverage", {}),
            "notes": [
                "Generated only from protocol_facts.json, target_profile.json, implementation_plan_v2.json, and design_decisions.json.",
                "No protocol-specific catalog or external baseline is used by the planning flow.",
                "Verifier remains the final acceptance gate for the selected blueprint.",
            ],
        }
    ]
    return blueprint, candidates


def blueprint_to_jsonable(blueprint: SpecBlueprint) -> dict[str, Any]:
    return asdict(blueprint)["data"]
