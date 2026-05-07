from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import PlanningIR, SpecBlueprint, TargetProfile


MQTT_MIN_PROFILE = "mqtt_min_broker_epoll_c"
BLUEPRINT_SCHEMA = "spec_blueprint/v1alpha1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_") or "x"


def _decision_ids(decisions: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("decision_id")) for item in decisions if isinstance(item, dict) and item.get("decision_id")]


def _template_ref(name: str) -> str:
    return f"{MQTT_MIN_PROFILE}:{name}"


def _infer_module_for_file(trace_id: str, module_entries: list[dict[str, Any]]) -> str:
    parts = trace_id.split("/")
    if len(parts) >= 2:
        trace_segment = parts[1]
        for module in module_entries:
            name = str(module.get("NAME", ""))
            if name == trace_segment:
                return name
        if trace_segment in {"protocol", "network", "broker", "topic", "router", "main"}:
            mapping = {
                "protocol": "protocol_codec",
                "network": "network",
                "broker": "broker_app" if len(parts) > 2 and parts[2] == "broker" else "session",
                "topic": "topic",
                "router": "router",
                "main": "broker_app",
            }
            if trace_segment == "broker" and len(parts) > 2 and parts[2] in {"session", "session_manager"}:
                return "session"
            return mapping[trace_segment]
    return str(module_entries[-1].get("NAME", "module")) if module_entries else "module"


def _template_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    target_profile: TargetProfile,
) -> SpecBlueprint:
    template_root = _repo_root() / "specs-example" / "mqtt_specs"
    module_spec_path = template_root / "mqtt_module_spec.json"
    module_spec = _read_json(module_spec_path)
    decision_refs = _decision_ids(decisions)
    modules = []
    files = []
    functions = []
    module_entries = list(module_spec.get("MODULES", []))

    for entry in module_entries:
        name = str(entry.get("NAME", ""))
        modules.append(
            {
                "name": name,
                "role": str(entry.get("ROLE", "")),
                "dependencies": [str(item) for item in entry.get("DEPENDENCIES", [])],
                "files": [str(item) for item in entry.get("FILES", [])],
                "artifacts": list(entry.get("ARTIFACTS", [])),
                "doc_ref": list(entry.get("DOC_REF", [])),
                "evidence_refs": list(entry.get("DOC_REF", [])),
                "decision_refs": decision_refs,
                "template_refs": [_template_ref(f"module:{name}")],
                "raw_entry": entry,
            }
        )

    for path in sorted(template_root.rglob("*_spec.json")):
        if path.resolve() == module_spec_path.resolve():
            continue
        raw = _read_json(path)
        kind = raw.get("KIND")
        if kind == "FILE_SPEC":
            trace_id = str(raw.get("FILE", {}).get("TRACE_ID", ""))
            module = _infer_module_for_file(trace_id, module_entries)
            header = raw.get("HEADER", {}) if isinstance(raw.get("HEADER"), dict) else {}
            source = raw.get("SOURCE", {}) if isinstance(raw.get("SOURCE"), dict) else {}
            files.append(
                {
                    "trace_id": trace_id,
                    "module": module,
                    "lang": str(raw.get("FILE", {}).get("LANG", "C")),
                    "role": str(raw.get("FILE", {}).get("ROLE", "")),
                    "header_path": str(header.get("PATH", "")),
                    "source_path": str(source.get("PATH", "")),
                    "header_dependencies": list(header.get("DEPENDENCY", [])),
                    "source_dependencies": list(source.get("DEPENDENCY", [])),
                    "header_data": list(header.get("DATA", [])),
                    "source_data": list(source.get("DATA", [])),
                    "header_interfaces": list(header.get("INTERFACE", [])),
                    "source_interfaces": list(source.get("INTERFACE", [])),
                    "evidence_refs": list(raw.get("FILE", {}).get("DOC_REF", [])),
                    "decision_refs": decision_refs,
                    "template_refs": [_template_ref(f"file:{trace_id}")],
                    "raw_spec": raw,
                }
            )
        elif kind == "FUNCTION_SPEC":
            trace_id = str(raw.get("TRACE_ID", ""))
            file_trace_id = trace_id.rsplit("/", 1)[0] if "/" in trace_id else ""
            module = _infer_module_for_file(file_trace_id, module_entries)
            functions.append(
                {
                    "trace_id": trace_id,
                    "module": module,
                    "file_trace_id": file_trace_id,
                    "name": str(raw.get("SIGNATURE", {}).get("NAME", "")),
                    "function_type": str(raw.get("FUNCTION_TYPE", "ALGORITHM")),
                    "signature": dict(raw.get("SIGNATURE", {})),
                    "role": str(raw.get("ROLE", "")),
                    "visibility": "private" if str(raw.get("SIGNATURE", {}).get("RAW", "")).startswith("static ") else "public",
                    "rely": dict(raw.get("RELY", {"STRUCT": [], "FUNC": [], "VAR": []})),
                    "logic": raw.get("LOGIC"),
                    "event": raw.get("EVENT"),
                    "wire_mapping": list(raw.get("WIRE_MAPPING", [])),
                    "access_paths": list(raw.get("ACCESS_PATHS", [])),
                    "call_contracts": list(raw.get("CALL_CONTRACTS", [])),
                    "test_vectors": list(raw.get("TEST_VECTORS", [])),
                    "evidence_refs": [],
                    "decision_refs": decision_refs,
                    "template_refs": [_template_ref(f"function:{trace_id}")],
                    "raw_spec": raw,
                }
            )

    data = {
        "kind": "SPEC_BLUEPRINT",
        "schema_version": BLUEPRINT_SCHEMA,
        "protocol_name": planning_ir.protocol_name,
        "target_profile": target_profile.raw,
        "expansion_profile": MQTT_MIN_PROFILE,
        "source": "rule_template",
        "source_template_root": str(template_root),
        "implementation_plan_ref": "implementation_plan_v2.json",
        "module_spec_template": module_spec,
        "generation_order": list(module_spec.get("GENERATION_ORDER", [item.get("NAME") for item in module_entries])),
        "consistency_rules": list(module_spec.get("CONSISTENCY_RULES", [])),
        "modules": modules,
        "files": files,
        "functions": functions,
        "coverage": {
            "module_count": len(modules),
            "file_spec_count": len(files),
            "function_spec_count": len(functions),
            "minimum_v1_surface_count": len(planning_ir.minimum_v1.get("must_support_surface", [])),
        },
        "traceability": {
            "decision_refs": decision_refs,
            "template_refs": [_template_ref("template_root")],
        },
    }
    return SpecBlueprint(data)


def _generic_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    target_profile: TargetProfile,
) -> SpecBlueprint:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    decision_refs = _decision_ids(decisions)
    module_graph = list(implementation_plan.get("module_graph", []))
    handler_matrix = list(implementation_plan.get("handler_matrix", []))
    modules = []
    files = []
    functions = []

    for module in module_graph:
        name = str(module.get("name", "module"))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        public_type = f"{protocol_slug}_{name}_t"
        trace_id = f"{protocol_slug}/{name}/{name}"
        modules.append(
            {
                "name": name,
                "role": str(module.get("role", "")),
                "dependencies": [str(item) for item in module.get("dependencies", [])],
                "files": [f"{path}.h", f"{path}.c"],
                "artifacts": list(module.get("artifacts", [])),
                "evidence_refs": [],
                "decision_refs": decision_refs,
                "template_refs": ["generic_blueprint:module"],
            }
        )
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
                "header_data": [{"NAME": public_type, "KIND": "TYPE", "VISIBILITY": "PUBLIC", "ROLE": f"Opaque {name} module handle"}],
                "source_data": [{"NAME": f"struct {public_type.rstrip('_t')}", "KIND": "TYPE", "VISIBILITY": "PRIVATE", "ROLE": f"Private {name} module state"}],
                "header_interfaces": [],
                "source_interfaces": [],
                "evidence_refs": [],
                "decision_refs": decision_refs,
                "template_refs": ["generic_blueprint:file"],
            }
        )
        for suffix, return_type, params, role in (
            ("create", f"{public_type}*", [], f"Create {name} module state"),
            ("destroy", "void", [{"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": True, "OWNERSHIP": "BORROWED"}], f"Destroy {name} module state"),
        ):
            fname = f"{protocol_slug}_{name}_{suffix}"
            sig = {"RAW": _signature(return_type, fname, params), "NAME": fname, "RETURN": return_type, "PARAMS": params}
            functions.append(
                {
                    "trace_id": f"{trace_id}/{fname}",
                    "module": name,
                    "file_trace_id": trace_id,
                    "name": fname,
                    "function_type": "ALGORITHM",
                    "signature": sig,
                    "role": role,
                    "visibility": "public",
                    "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                    "logic": {"INPUT": "module inputs", "ACTION": role, "OUTPUT": "module handle or cleanup side effect", "INVARIANTS_USED": ["Maintain module-local ownership invariants"]},
                    "evidence_refs": [],
                    "decision_refs": decision_refs,
                    "template_refs": ["generic_blueprint:function"],
                }
            )

    for row in handler_matrix:
        module = str(row.get("handler_module", "handler_dispatch"))
        file_trace_id = next((str(item.get("trace_id")) for item in files if item.get("module") == module), f"{protocol_slug}/{module}/{module}")
        public_type = f"{protocol_slug}_{module}_t"
        fname = str(row.get("handler_function", f"{protocol_slug}_{module}_handle"))
        params = [{"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": False, "OWNERSHIP": "BORROWED"}, {"TYPE": "const void*", "NAME": "message", "NULLABLE": False, "OWNERSHIP": "BORROWED"}]
        functions.append(
            {
                "trace_id": f"{file_trace_id}/{fname}",
                "module": module,
                "file_trace_id": file_trace_id,
                "name": fname,
                "function_type": "EVENT",
                "signature": {"RAW": _signature("int", fname, params), "NAME": fname, "RETURN": "int", "PARAMS": params},
                "role": f"Handle {row.get('surface_unit')} for minimum_v1 path",
                "visibility": "public",
                "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                "event": {
                    "TRIGGER": f"decoded {row.get('surface_unit')}",
                    "PRECONDITION": "decoded message and module state are valid",
                    "INPUT": f"decoded {row.get('surface_unit')} and module state",
                    "ACTION": str(row.get("path_summary", "decode -> handler -> policy/store")),
                    "STATE_CHANGE": "implementation-defined state update",
                    "RESPONSE": "0 on success, non-zero on protocol or processing failure",
                    "EVENT_TYPE": "PROTOCOL_HANDLER",
                },
                "evidence_refs": list(row.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "template_refs": ["generic_blueprint:handler"],
            }
        )

    data = {
        "kind": "SPEC_BLUEPRINT",
        "schema_version": BLUEPRINT_SCHEMA,
        "protocol_name": planning_ir.protocol_name,
        "target_profile": target_profile.raw,
        "expansion_profile": "generic_c",
        "source": "generic_rule_template",
        "generation_order": [item["name"] for item in modules],
        "consistency_rules": [
            {"NAME": "unique_public_type_owner", "DESC": "Every canonical public type must have one owner only."},
            {"NAME": "acyclic_module_dependencies", "DESC": "Module dependencies must be forward-safe for current coder."},
        ],
        "modules": modules,
        "files": files,
        "functions": functions,
        "coverage": {
            "module_count": len(modules),
            "file_spec_count": len(files),
            "function_spec_count": len(functions),
            "minimum_v1_surface_count": len(planning_ir.minimum_v1.get("must_support_surface", [])),
        },
        "traceability": {
            "decision_refs": decision_refs,
            "template_refs": ["generic_blueprint"],
        },
    }
    return SpecBlueprint(data)


def _signature(return_type: str, name: str, params: list[dict[str, Any]]) -> str:
    raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}".strip() for item in params) or "void"
    return f"{return_type} {name}({raw_params})"


def build_spec_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    decisions: list[dict[str, Any]],
    target_profile: TargetProfile,
    llm_client: Any | None = None,
) -> tuple[SpecBlueprint, list[dict[str, Any]]]:
    del llm_client
    protocol = planning_ir.protocol_name.lower()
    profile_hint = str(target_profile.raw.get("implementation_profile", "") or target_profile.raw.get("spec_expansion_profile", "")).lower()
    scope = str(target_profile.scope).lower()
    surfaces = {str(item.get("name", "")).upper() for item in planning_ir.minimum_v1.get("must_support_surface", []) if isinstance(item, dict)}
    mqtt_min_surfaces = {"CONNECT", "CONNACK", "SUBSCRIBE", "SUBACK", "PUBLISH", "PINGREQ", "PINGRESP", "DISCONNECT"}
    use_mqtt_template = protocol == "mqtt" and (
        "mqtt_min" in profile_hint
        or "minimum" in profile_hint
        or (scope in {"minimum_v1", "minimum", "min"} and surfaces == mqtt_min_surfaces)
    )
    blueprint = _template_blueprint(planning_ir, implementation_plan, decisions, target_profile) if use_mqtt_template else _generic_blueprint(planning_ir, implementation_plan, decisions, target_profile)
    candidates = [
        {
            "candidate_id": blueprint.data["source"],
            "origin": "rule_template",
            "selected": True,
            "expansion_profile": blueprint.data["expansion_profile"],
            "coverage": blueprint.data.get("coverage", {}),
            "notes": [
                "LLM expansion candidates are not enabled in this implementation pass.",
                "Verifier remains the final acceptance gate for the selected blueprint.",
            ],
        }
    ]
    return blueprint, candidates


def blueprint_to_jsonable(blueprint: SpecBlueprint) -> dict[str, Any]:
    return asdict(blueprint)["data"]
