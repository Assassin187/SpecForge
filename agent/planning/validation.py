from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .facts import stable_json_hash
from .implementability import analyze_implementability, closure_diagnostics_as_models
from .knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from .models import Diagnostic, to_jsonable
from .planner import build_planning_context


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object in {path}")
    return data


def _iter_spec_json(specs_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(specs_root.rglob("*_spec.json")):
        out.append((path, _load_json(path)))
    return out


def _error(code: str, message: str, path: str | Path | None = None) -> Diagnostic:
    return Diagnostic(
        "error",
        code,
        message,
        str(path) if path is not None else None,
        "post_planning_validation",
        "compile_specs",
        "regenerate_authoritative_partition",
    )


def _warning(code: str, message: str, path: str | Path | None = None) -> Diagnostic:
    return Diagnostic(
        "warning",
        code,
        message,
        str(path) if path is not None else None,
        "post_planning_validation",
        "compile_specs",
        "inspect_and_regenerate_authoritative_partition",
    )


def schema_shape_check(specs_root: str | Path) -> list[Diagnostic]:
    root = Path(specs_root)
    diagnostics: list[Diagnostic] = []
    module_specs = []
    for path, raw in _iter_spec_json(root):
        kind = raw.get("KIND")
        if kind == "PROTOCOL_MODULE_SPEC":
            module_specs.append((path, raw))
            for key in ("PROTOCOL", "MODULES", "GENERATION_ORDER", "CONSISTENCY_RULES"):
                if key not in raw:
                    diagnostics.append(_error("schema_shape_missing_field", f"Module spec missing {key}", path))
            if not isinstance(raw.get("MODULES"), list) or not raw.get("MODULES"):
                diagnostics.append(_error("schema_shape_invalid_modules", "MODULES must be a non-empty array", path))
        elif kind == "FILE_SPEC":
            for key in ("FILE", "SOURCE"):
                if not isinstance(raw.get(key), dict):
                    diagnostics.append(_error("schema_shape_missing_field", f"File spec missing object {key}", path))
            file_meta = raw.get("FILE", {})
            if isinstance(file_meta, dict):
                for key in ("TRACE_ID", "LANG", "ROLE", "DOC_REF"):
                    if key not in file_meta:
                        diagnostics.append(_error("schema_shape_missing_file_meta", f"FILE missing {key}", path))
            source = raw.get("SOURCE", {})
            if isinstance(source, dict):
                for key in ("PATH", "DEPENDENCY", "DATA", "INTERFACE"):
                    if key not in source:
                        diagnostics.append(_error("schema_shape_missing_source", f"SOURCE missing {key}", path))
            header = raw.get("HEADER")
            if header is not None:
                if not isinstance(header, dict):
                    diagnostics.append(_error("schema_shape_invalid_header", "HEADER must be an object when present", path))
                else:
                    for key in ("PATH", "DEPENDENCY", "DATA", "INTERFACE"):
                        if key not in header:
                            diagnostics.append(_error("schema_shape_missing_header", f"HEADER missing {key}", path))
        elif kind == "FUNCTION_SPEC":
            for key in ("TRACE_ID", "FUNCTION_TYPE", "ROLE", "SIGNATURE", "RELY"):
                if key not in raw:
                    diagnostics.append(_error("schema_shape_missing_field", f"Function spec missing {key}", path))
            signature = raw.get("SIGNATURE", {})
            if isinstance(signature, dict):
                for key in ("RAW", "NAME", "RETURN", "PARAMS"):
                    if key not in signature:
                        diagnostics.append(_error("schema_shape_missing_signature", f"SIGNATURE missing {key}", path))
            if raw.get("FUNCTION_TYPE") == "ALGORITHM" and "LOGIC" not in raw:
                diagnostics.append(_error("schema_shape_missing_logic", "ALGORITHM function requires LOGIC", path))
            if raw.get("FUNCTION_TYPE") == "EVENT" and "EVENT" not in raw:
                diagnostics.append(_error("schema_shape_missing_event", "EVENT function requires EVENT", path))
        else:
            diagnostics.append(_warning("schema_shape_unknown_kind", f"Unknown spec kind {kind!r}", path))
    if len(module_specs) != 1:
        diagnostics.append(_error("schema_shape_module_spec_count", f"Expected exactly one module spec, found {len(module_specs)}", root))
    return diagnostics


def structural_consistency_check(plan: dict[str, Any], specs_root: str | Path) -> list[Diagnostic]:
    root = Path(specs_root)
    diagnostics: list[Diagnostic] = []
    specs = _iter_spec_json(root)
    module_raw = next((raw for _, raw in specs if raw.get("KIND") == "PROTOCOL_MODULE_SPEC"), None)
    if module_raw is None:
        return [_error("missing_module_spec", "No PROTOCOL_MODULE_SPEC was generated", root)]

    module_names = [module["NAME"] for module in module_raw.get("MODULES", []) if isinstance(module, dict)]
    if len(module_names) != len(set(module_names)):
        diagnostics.append(_error("duplicate_module_name", "Module names must be unique", root))
    positions = {name: idx for idx, name in enumerate(module_raw.get("GENERATION_ORDER", []))}
    for module in module_raw.get("MODULES", []):
        if not isinstance(module, dict):
            continue
        name = module.get("NAME")
        if name not in positions:
            diagnostics.append(_error("module_omitted_from_generation_order", f"{name} missing from GENERATION_ORDER", root))
        for dep in module.get("DEPENDENCIES", []):
            if dep not in module_names:
                diagnostics.append(_error("unknown_module_dependency", f"{name} depends on unknown module {dep}", root))
            elif positions.get(dep, 9999) > positions.get(name, -1):
                diagnostics.append(_error("module_order_violation", f"{name} appears before dependency {dep}", root))

    file_specs = [raw for _, raw in specs if raw.get("KIND") == "FILE_SPEC"]
    function_specs = [raw for _, raw in specs if raw.get("KIND") == "FUNCTION_SPEC"]
    file_traces = {raw.get("FILE", {}).get("TRACE_ID") for raw in file_specs if isinstance(raw.get("FILE"), dict)}
    header_paths = {raw.get("HEADER", {}).get("PATH") for raw in file_specs if isinstance(raw.get("HEADER"), dict)}
    source_paths = {raw.get("SOURCE", {}).get("PATH") for raw in file_specs if isinstance(raw.get("SOURCE"), dict)}
    header_paths.discard(None)
    source_paths.discard(None)
    if len(source_paths) != len([raw for raw in file_specs if isinstance(raw.get("SOURCE"), dict)]):
        diagnostics.append(_error("duplicate_source_path", "SOURCE.PATH values must be unique", root))
    module_files = {path for module in module_raw.get("MODULES", []) if isinstance(module, dict) for path in module.get("FILES", [])}
    known_files = header_paths | source_paths
    for path in module_files:
        if path not in known_files:
            diagnostics.append(_error("module_file_without_file_spec", f"Module references {path} but no file spec owns it", root))
    for raw in file_specs:
        file_path = "<unknown>"
        if isinstance(raw.get("FILE"), dict):
            file_path = str(raw["FILE"].get("TRACE_ID", file_path))
        for dep in raw.get("HEADER", {}).get("DEPENDENCY", []) if isinstance(raw.get("HEADER"), dict) else []:
            if dep not in header_paths:
                diagnostics.append(_error("unknown_header_dependency", f"{file_path} header depends on unknown {dep}", root))
        for dep in raw.get("SOURCE", {}).get("DEPENDENCY", []) if isinstance(raw.get("SOURCE"), dict) else []:
            if dep not in header_paths:
                diagnostics.append(_error("unknown_source_dependency", f"{file_path} source depends on unknown {dep}", root))

    function_traces = {raw.get("TRACE_ID") for raw in function_specs}
    source_interface_traces = {
        item.get("TRACE_ID")
        for raw in file_specs
        for item in raw.get("SOURCE", {}).get("INTERFACE", [])
        if isinstance(raw.get("SOURCE"), dict) and isinstance(item, dict)
    }
    for trace in function_traces:
        parent = str(trace).rsplit("/", 1)[0]
        if parent not in file_traces:
            diagnostics.append(_error("orphan_function_spec", f"{trace} parent {parent} has no file spec", root))
        if trace not in source_interface_traces:
            diagnostics.append(_error("function_not_in_source_interface", f"{trace} has no SOURCE.INTERFACE entry", root))
    for trace in source_interface_traces:
        if trace not in function_traces:
            diagnostics.append(_error("source_interface_without_function_spec", f"{trace} has no FUNCTION_SPEC", root))

    diagnostics.extend(plan_to_spec_preservation_check(plan, specs_root))
    return diagnostics


def plan_to_spec_preservation_check(plan: dict[str, Any], specs_root: str | Path) -> list[Diagnostic]:
    root = Path(specs_root)
    diagnostics: list[Diagnostic] = []
    specs = _iter_spec_json(root)
    module_raw = next((raw for _, raw in specs if raw.get("KIND") == "PROTOCOL_MODULE_SPEC"), {})
    plan_modules = {module["name"] for module in plan.get("modules", [])}
    spec_modules = {module.get("NAME") for module in module_raw.get("MODULES", []) if isinstance(module, dict)}
    if plan_modules != spec_modules:
        diagnostics.append(_error("plan_to_spec_module_drift", f"Plan modules {sorted(plan_modules)} differ from specs {sorted(spec_modules)}", root))
    spec_module_items = {
        str(item.get("NAME")): item
        for item in module_raw.get("MODULES", [])
        if isinstance(item, dict)
    }
    for module in plan.get("modules", []):
        if not isinstance(module, dict):
            continue
        emitted = spec_module_items.get(str(module.get("name")), {})
        if emitted.get("DEPENDENCIES", []) != module.get("dependencies", []):
            diagnostics.append(
                _error(
                    "plan_to_spec_module_dependency_drift",
                    f"Module {module.get('id')} dependencies differ after lowering",
                    root,
                )
            )
    plan_files = {file_item["trace_id"] for file_item in plan.get("files", [])}
    spec_files = {raw.get("FILE", {}).get("TRACE_ID") for _, raw in specs if raw.get("KIND") == "FILE_SPEC" and isinstance(raw.get("FILE"), dict)}
    if plan_files != spec_files:
        diagnostics.append(_error("plan_to_spec_file_drift", f"Plan files {sorted(plan_files)} differ from specs {sorted(spec_files)}", root))
    plan_functions = {function["trace_id"] for function in plan.get("functions", [])}
    spec_functions = {raw.get("TRACE_ID") for _, raw in specs if raw.get("KIND") == "FUNCTION_SPEC"}
    if plan_functions != spec_functions:
        diagnostics.append(_error("plan_to_spec_function_drift", f"Plan functions {sorted(plan_functions)} differ from specs {sorted(spec_functions)}", root))
        for trace_id in sorted(plan_functions - spec_functions):
            diagnostics.append(
                _error(
                    "required_function_spec_not_materialized",
                    f"Required FUNCTION_SPEC {trace_id} was not materialized",
                    root,
                )
            )

    file_specs = {
        str(raw.get("FILE", {}).get("TRACE_ID")): raw
        for _, raw in specs
        if raw.get("KIND") == "FILE_SPEC" and isinstance(raw.get("FILE"), dict)
    }
    function_specs = {
        str(raw.get("TRACE_ID")): raw for _, raw in specs if raw.get("KIND") == "FUNCTION_SPEC"
    }
    data_by_name = {
        str(item.get("NAME")): item
        for raw in file_specs.values()
        for block in (raw.get("HEADER"), raw.get("SOURCE"))
        if isinstance(block, dict)
        for item in block.get("DATA", [])
        if isinstance(item, dict)
    }
    for type_item in plan.get("types", []):
        if not isinstance(type_item, dict):
            continue
        emitted = data_by_name.get(str(type_item.get("name")), {})
        planned_type_spec = deepcopy(type_item.get("type_spec"))
        if isinstance(planned_type_spec, dict) and planned_type_spec.get("TYPE_KIND") == "ENUM":
            planned_type_spec["ENUM_VALUES"] = [
                {key: value[key] for key in ("NAME", "VALUE", "ROLE") if key in value}
                for value in planned_type_spec.get("ENUM_VALUES", [])
                if isinstance(value, dict)
            ]
        if emitted.get("TYPE_SPEC") != planned_type_spec:
            diagnostics.append(
                _error(
                    "plan_to_spec_type_semantic_drift",
                    f"Type {type_item.get('id')} TYPE_SPEC differs after lowering",
                    root,
                )
            )
    for item in plan.get("constants_or_macros", []):
        if not isinstance(item, dict):
            continue
        emitted = data_by_name.get(str(item.get("name")))
        if emitted is None or ("value" in item and emitted.get("VALUE") != item.get("value")):
            diagnostics.append(
                _error("plan_to_spec_constant_drift", f"Constant {item.get('id')} was not preserved", root)
            )
    for function in plan.get("functions", []):
        if not isinstance(function, dict):
            continue
        emitted = function_specs.get(str(function.get("trace_id")))
        if emitted is None:
            continue
        expected = {
            "SIGNATURE": function.get("signature", {}),
            "RELY": function.get("rely", {}),
            "CALL_CONTRACTS": function.get("call_contracts", []),
            "ACCESS_PATHS": function.get("access_paths", []),
            "FORBIDDEN_SYMBOLS": function.get("forbidden_symbols", []),
            "TEST_VECTORS": function.get("test_vectors", []),
        }
        if function.get("wire_mapping"):
            expected["WIRE_MAPPING"] = function["wire_mapping"]
        if function.get("function_type") == "EVENT":
            expected["EVENT"] = function.get("event")
        else:
            expected["LOGIC"] = function.get("logic")
        drifted = [key for key, value in expected.items() if emitted.get(key, []) != value]
        if drifted:
            diagnostics.append(
                _error(
                    "plan_to_spec_function_semantic_drift",
                    f"Function {function.get('id')} differs in {sorted(drifted)}",
                    root,
                )
            )
        expected_public = str(function.get("visibility", "")).lower() == "public"
        emitted_public = any(
            isinstance(item, dict) and item.get("NAME") == function.get("name")
            for item in emitted.get("PUBLIC_SYMBOLS", [])
        )
        if emitted_public != expected_public:
            diagnostics.append(
                _error(
                    "plan_to_spec_function_visibility_drift",
                    f"Function {function.get('id')} visibility differs after lowering",
                    root,
                )
            )
    for file_item in plan.get("files", []):
        if not isinstance(file_item, dict):
            continue
        emitted = file_specs.get(str(file_item.get("trace_id")))
        if emitted is None:
            continue
        dependency_drift = (
            emitted.get("SOURCE", {}).get("DEPENDENCY", []) != file_item.get("source_dependencies", [])
            or (
                bool(file_item.get("header_path"))
                and emitted.get("HEADER", {}).get("DEPENDENCY", []) != file_item.get("header_dependencies", [])
            )
        )
        if dependency_drift:
            diagnostics.append(
                _error("plan_to_spec_file_dependency_drift", f"File {file_item.get('id')} dependencies differ", root)
            )
        if emitted.get("FORBIDDEN_SYMBOLS", []) != file_item.get("forbidden_symbols", []):
            diagnostics.append(
                _error("plan_to_spec_file_forbidden_symbol_drift", f"File {file_item.get('id')} forbidden symbols differ", root)
            )
        if emitted.get("TEST_VECTORS", []) != file_item.get("test_vectors", []):
            diagnostics.append(
                _error("plan_to_spec_file_test_vector_drift", f"File {file_item.get('id')} test vectors differ", root)
            )
    if module_raw.get("CONSISTENCY_RULES", []) != plan.get("consistency_rules", []):
        diagnostics.append(_error("plan_to_spec_consistency_rule_drift", "Consistency rules differ", root))
    if module_raw.get("FORBIDDEN_SYMBOLS", []) != plan.get("forbidden_symbols", []):
        diagnostics.append(_error("plan_to_spec_protocol_forbidden_symbol_drift", "Protocol forbidden symbols differ", root))
    if module_raw.get("TEST_VECTORS", []) != plan.get("test_vectors", []):
        diagnostics.append(_error("plan_to_spec_runtime_test_vector_drift", "Runtime test vectors differ", root))

    sidecar_path = root / "planning_semantic_mapping.json"
    if sidecar_path.is_file():
        sidecar = _load_json(sidecar_path)
        mappings = {str(item.get("plan_id")): item for item in sidecar.get("artifacts", []) if isinstance(item, dict)}
        for collection in ("modules", "files", "types", "constants_or_macros", "functions"):
            for item in plan.get(collection, []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                mapping = mappings.get(str(item["id"]))
                expected_refs = {
                    "trace_refs": [str(ref) for ref in item.get("trace_refs", [])],
                    "decision_refs": [str(ref) for ref in item.get("decision_refs", [])],
                    "rule_refs": [str(ref) for ref in item.get("rule_refs", [])],
                }
                if mapping is None or any(mapping.get(key, []) != value for key, value in expected_refs.items()):
                    diagnostics.append(
                        _error("plan_to_spec_traceability_drift", f"Artifact {item['id']} traceability differs", sidecar_path)
                    )
    else:
        diagnostics.append(_error("plan_to_spec_traceability_sidecar_missing", "Planning traceability sidecar is missing", root))
    return diagnostics


def fact_decision_assumption_separation_check(
    facts_before_hash: str,
    facts_after: dict[str, Any],
    plan: dict[str, Any],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if facts_before_hash != stable_json_hash(facts_after):
        diagnostics.append(_error("facts_modified", "Protocol facts changed during planning"))
    for decision in plan.get("engineering_decisions", []):
        if not decision.get("supporting_fact_refs") and not decision.get("activated_rule_refs"):
            diagnostics.append(_error("decision_without_support", f"{decision.get('decision_id')} has no fact/rule support"))
    for assumption in plan.get("open_assumptions", []):
        text = json.dumps(assumption, ensure_ascii=False)
        if '"fact_id"' in text:
            diagnostics.append(_error("assumption_written_as_fact", f"{assumption.get('assumption_id')} appears to declare facts"))
    return diagnostics


def reference_isolation_check(package_root: str | Path | None = None) -> list[Diagnostic]:
    root = Path(package_root) if package_root else Path(__file__).resolve().parent
    diagnostics: list[Diagnostic] = []
    forbidden = ("specs-example/" + "mqtt_specs", "specs-example\\" + "mqtt_specs")
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern in text:
                diagnostics.append(_error("reference_dependency", f"Normal planning code references {pattern}", path))
    return diagnostics


def anti_hardcoding_scan(package_root: str | Path | None = None) -> list[Diagnostic]:
    root = Path(package_root) if package_root else Path(__file__).resolve().parent
    diagnostics: list[Diagnostic] = []
    forbidden_patterns = [
        "mqtt" + "_broker_create",
        "mqtt" + "_tcp_server",
        "mqtt" + "_message_router",
        "mqtt" + "_topic_tree",
        "session" + "_manager",
        "broker" + "_app",
        "protocol_codec" + '", "session',
    ]
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if pattern in text:
                diagnostics.append(_error("hardcoded_reference_inventory", f"Found reference-inventory pattern {pattern!r}", path))
    return diagnostics


def facts_sensitivity_check(facts: dict[str, Any], original_plan: dict[str, Any] | None = None) -> list[Diagnostic]:
    original_characteristics = normalize_characteristics(facts)
    original_rules = activate_engineering_rules(original_characteristics)
    original_assumptions = extract_open_assumptions(facts, original_characteristics)
    original_context = build_planning_context(facts, original_characteristics, original_rules, original_assumptions)

    variant = deepcopy(facts)
    variant.setdefault("transport", {}).setdefault("network_stack", {})["value"] = "udp"
    variant.setdefault("transport", {}).setdefault("connection_model", {})["value"] = "connectionless"
    variant["routing_model"] = {"dispatch_keys": [], "dispatch_targets": [], "matching_rules": []}
    variant["state_model"] = {"state_nodes": [], "transitions": [], "timers_and_constants": [], "invariants": []}
    minimum = variant.setdefault("minimum_v1", {})
    if isinstance(minimum.get("must_support_surface"), list) and minimum["must_support_surface"]:
        minimum["must_support_surface"] = minimum["must_support_surface"][:1]

    characteristics = normalize_characteristics(variant)
    rules = activate_engineering_rules(characteristics)
    assumptions = extract_open_assumptions(variant, characteristics)
    variant_context = build_planning_context(variant, characteristics, rules, assumptions)
    original_rule_ids = {rule["rule_id"] for rule in original_context["engineering_rules"]}
    variant_rule_ids = {rule["rule_id"] for rule in variant_context["engineering_rules"]}
    diagnostics: list[Diagnostic] = []
    if original_rule_ids == variant_rule_ids:
        diagnostics.append(_error("facts_sensitivity_rules_static", "Activated engineering rules did not change for altered facts"))
    if original_context["characteristics"] == variant_context["characteristics"]:
        diagnostics.append(_error("facts_sensitivity_context_static", "LLM planning context did not change for altered facts"))
    return diagnostics


def validate_planning_run(
    facts: dict[str, Any],
    facts_before_hash: str,
    plan: dict[str, Any],
    specs_root: str | Path,
) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic(
            str(item.get("level", "error")),
            str(item.get("code", "semantic_lowering_error")),
            str(item.get("message", "Compiler could not losslessly lower planning semantics")),
            str(item.get("path")) if item.get("path") else None,
            str(item.get("owner_layer", "compiler")),
            str(item.get("authoritative_stage", "final_plan_assembly")),
            str(item.get("recovery_action", "regenerate_authoritative_partition")),
        )
        for item in plan.get("lowering_diagnostics", [])
        if isinstance(item, dict)
    ]
    diagnostics.extend(closure_diagnostics_as_models(analyze_implementability(plan)))
    diagnostics.extend(schema_shape_check(specs_root))
    diagnostics.extend(structural_consistency_check(plan, specs_root))
    diagnostics.extend(reference_isolation_check())
    diagnostics.extend(anti_hardcoding_scan())
    diagnostics.extend(facts_sensitivity_check(facts, plan))
    diagnostics.extend(fact_decision_assumption_separation_check(facts_before_hash, facts, plan))
    return diagnostics


def diagnostics_to_json(diagnostics: list[Diagnostic]) -> list[dict[str, Any]]:
    return [to_jsonable(diag) for diag in diagnostics]
