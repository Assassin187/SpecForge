from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..diagnostics import PlanningDiagnostic
from ..stages.coder_spec_lowering import ARTIFACT_KINDS, extract_c_signature_type_refs, is_artifact_name_for_coder, normalize_type_key


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _module_file_specs(bundle: Any, module: Any) -> list[Any]:
    file_specs = []
    for file_path in module.files:
        normalized = file_path.replace("\\", "/").strip()
        while normalized.startswith("../"):
            normalized = normalized[3:]
        while normalized.startswith("./"):
            normalized = normalized[2:]
        spec = bundle.file_specs_by_header_path.get(normalized) or bundle.file_specs_by_source_path.get(normalized)
        if spec is not None and spec not in file_specs:
            file_specs.append(spec)
    return file_specs


def _artifact_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("NAME", "")).strip(), str(item.get("KIND", "")).strip().upper()


def _check_artifact_item(item: dict[str, Any], diagnostics: list[PlanningDiagnostic], path: str | None) -> None:
    name, kind = _artifact_key(item)
    if kind not in ARTIFACT_KINDS:
        diagnostics.append(PlanningDiagnostic("error", "coder_artifact_invalid_kind", f"Artifact '{name}' has invalid kind '{kind}'", path))
    if not is_artifact_name_for_coder(name):
        diagnostics.append(PlanningDiagnostic("error", "coder_artifact_invalid_name", f"Artifact name '{name}' is not a C symbol", path))


def _read_sidecar(bundle: Any, filename: str) -> dict[str, Any]:
    path = Path(bundle.spec_root) / filename
    if not path.exists():
        return {}
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _planning_intent(bundle: Any) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    decisions = _read_sidecar(bundle, "planning_decisions.json")
    refs = _read_sidecar(bundle, "planning_ir_refs.json")
    decision_items = decisions.get("items", {}) if isinstance(decisions.get("items"), dict) else {}
    ref_items = refs.get("items", {}) if isinstance(refs.get("items"), dict) else {}
    modules: dict[str, dict[str, Any]] = {}
    functions: dict[str, dict[str, Any]] = {}
    for key, decision in decision_items.items():
        if not isinstance(decision, dict):
            continue
        ref = ref_items.get(key, {}) if isinstance(ref_items.get(key), dict) else {}
        module_id = str(ref.get("module_id", "") or key).strip()
        function_id = str(ref.get("function_id", "")).strip()
        file_id = str(ref.get("file_id", "")).strip()
        if function_id:
            functions[function_id] = {"decision": decision, "ref": ref}
        elif module_id and not file_id:
            modules[module_id] = {"decision": decision, "ref": ref}
    unresolved = refs.get("unresolved_lowering", []) if isinstance(refs.get("unresolved_lowering"), list) else []
    return modules, functions, [item for item in unresolved if isinstance(item, dict)]


def _intent_public_function_ids(function_intent: dict[str, dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for function_id, item in function_intent.items():
        decision = item.get("decision", {})
        if bool(decision.get("exported")) or str(decision.get("api_surface", "")).lower() == "public" or str(decision.get("visibility", "")).lower() == "public":
            result.add(function_id)
    return result


def validate_coder_semantics(bundle: Any) -> list[PlanningDiagnostic]:
    diagnostics: list[PlanningDiagnostic] = []
    module_raw = _read_json(Path(bundle.module_spec_path))
    module_intent, function_intent, unresolved_lowering = _planning_intent(bundle)
    public_function_intent_ids = _intent_public_function_ids(function_intent)

    public_artifact_count = 0
    module_artifacts_by_name = {str(module.get("NAME", "")): module.get("ARTIFACTS", []) for module in module_raw.get("MODULES", []) if isinstance(module, dict)}
    function_specs_by_name = {
        spec.signature.name.strip()
        for spec in bundle.function_specs_by_trace.values()
        if spec.signature.name.strip()
    }
    header_names_by_module: dict[str, set[str]] = {}
    public_data_by_module: dict[str, set[tuple[str, str]]] = {}
    module_name_by_file_path: dict[str, str] = {}

    for module in bundle.modules_in_order:
        artifacts = module_artifacts_by_name.get(module.name, module.artifacts)
        artifact_keys = {_artifact_key(item) for item in artifacts if isinstance(item, dict)}
        file_specs = _module_file_specs(bundle, module)
        for file_spec in file_specs:
            module_name_by_file_path[str(file_spec.spec_path)] = module.name
        module_public_headers = {
            (item.name.strip(), "FUNC")
            for file_spec in file_specs
            for item in file_spec.header_interfaces
            if item.visibility == "public"
        }
        for item in artifacts:
            if isinstance(item, dict):
                public_artifact_count += 1
                _check_artifact_item(item, diagnostics, str(bundle.module_spec_path))
                if _artifact_key(item)[1] == "FUNC" and _artifact_key(item) not in module_public_headers:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_artifact_func_missing_header",
                            f"Artifact FUNC '{item.get('NAME')}' is not declared in any module HEADER.INTERFACE",
                            str(bundle.module_spec_path),
                        )
                    )

        for file_spec in file_specs:
            public_headers = {(item.name.strip(), "FUNC") for item in file_spec.header_interfaces if item.visibility == "public"}
            source_public = [item for item in file_spec.source_interfaces if item.visibility == "public"]
            for item in source_public:
                if (item.name.strip(), "FUNC") not in public_headers:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_source_missing_header",
                            f"Public source function '{item.name}' is not declared in HEADER.INTERFACE",
                            str(file_spec.spec_path),
                        )
                    )
            for name, kind in public_headers:
                if (name, kind) not in artifact_keys:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_func_missing_artifact",
                            f"Public function '{name}' is missing from module '{module.name}' ARTIFACTS",
                            str(file_spec.spec_path),
                        )
                    )
            for item in file_spec.header_data:
                if str(item.get("VISIBILITY", "")).upper() != "PUBLIC":
                    continue
                key = (str(item.get("NAME", "")).strip(), str(item.get("KIND", "")).strip().upper())
                public_data_by_module.setdefault(module.name, set()).add(key)
                if key[1] in ARTIFACT_KINDS and key not in artifact_keys:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_data_missing_artifact",
                            f"Public data symbol '{key[0]}' is missing from module '{module.name}' ARTIFACTS",
                            str(file_spec.spec_path),
                        )
                    )

            file_public_symbols = file_spec.raw.get("PUBLIC_SYMBOLS", [])
            for symbol in file_public_symbols if isinstance(file_public_symbols, list) else []:
                if not isinstance(symbol, dict):
                    continue
                _check_artifact_item(symbol, diagnostics, str(file_spec.spec_path))
                if str(symbol.get("KIND", "")).upper() == "FUNC" and (str(symbol.get("NAME", "")).strip(), "FUNC") not in public_headers:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_symbol_missing_header",
                            f"Public FUNC symbol '{symbol.get('NAME')}' is not declared in HEADER.INTERFACE",
                            str(file_spec.spec_path),
                        )
                    )
            for name, _kind in public_headers:
                if name not in function_specs_by_name:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_header_missing_function_spec",
                            f"Public header function '{name}' has no corresponding FUNCTION_SPEC",
                            str(file_spec.spec_path),
                        )
                    )
            header_names_by_module.setdefault(module.name, set()).update(name for name, _kind in public_headers)

    public_type_keys_by_module: dict[str, set[str]] = {}
    all_public_type_keys: set[str] = set()
    for module_name, data_keys in public_data_by_module.items():
        keys = {normalize_type_key(name) for name, kind in data_keys if kind == "TYPE"}
        public_type_keys_by_module.setdefault(module_name, set()).update(key for key in keys if key)
    for module_name, artifacts in module_artifacts_by_name.items():
        for artifact in artifacts if isinstance(artifacts, list) else []:
            if isinstance(artifact, dict) and _artifact_key(artifact)[1] == "TYPE":
                key = normalize_type_key(artifact.get("NAME", ""))
                if key:
                    public_type_keys_by_module.setdefault(module_name, set()).add(key)
    for keys in public_type_keys_by_module.values():
        all_public_type_keys.update(keys)

    for file_spec in bundle.file_specs_by_trace.values():
        file_type_keys = {
            normalize_type_key(item.get("NAME", ""))
            for item in file_spec.header_data
            if isinstance(item, dict) and item.get("KIND") == "TYPE" and str(item.get("VISIBILITY", "")).upper() == "PUBLIC"
        }
        module_name = module_name_by_file_path.get(str(file_spec.spec_path), "")
        module_type_keys = public_type_keys_by_module.get(module_name, set())
        for interface in file_spec.header_interfaces:
            if interface.visibility != "public":
                continue
            for ref in extract_c_signature_type_refs(interface.signature):
                key = str(ref.get("key", ""))
                if key and key not in file_type_keys and key not in module_type_keys and key not in all_public_type_keys:
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_signature_unknown_type",
                            f"Public function '{interface.name}' in module '{module_name or '<unknown>'}' references undeclared public type '{ref.get('raw')}'",
                            str(file_spec.spec_path),
                        )
                    )

    all_public_headers = {
        (item.name.strip(), "FUNC")
        for file_spec in bundle.file_specs_by_trace.values()
        for item in file_spec.header_interfaces
        if item.visibility == "public"
    }
    for symbol in module_raw.get("PUBLIC_SYMBOLS", []) if isinstance(module_raw.get("PUBLIC_SYMBOLS"), list) else []:
        if isinstance(symbol, dict):
            _check_artifact_item(symbol, diagnostics, str(bundle.module_spec_path))
            if _artifact_key(symbol)[1] == "FUNC" and _artifact_key(symbol) not in all_public_headers:
                diagnostics.append(
                    PlanningDiagnostic(
                        "error",
                        "coder_public_symbol_missing_header",
                        f"Public FUNC symbol '{symbol.get('NAME')}' is not declared in HEADER.INTERFACE",
                        str(bundle.module_spec_path),
                    )
                )

    if public_artifact_count == 0:
        level = "error" if module_intent or public_function_intent_ids else "warning"
        diagnostics.append(PlanningDiagnostic(level, "coder_no_public_artifacts", "Spec bundle contains no module public artifacts", str(bundle.module_spec_path)))

    for item in unresolved_lowering:
        diagnostics.append(
            PlanningDiagnostic(
                "error",
                "coder_public_lowering_unresolved",
                str(item.get("reason", "Public API lowering is unresolved.")),
                str(bundle.spec_root),
            )
        )

    modules_exposing_public_api = 0
    modules_explicitly_internal = 0
    for module_id, item in module_intent.items():
        decision = item.get("decision", {})
        policy = decision.get("public_api_policy", {}) if isinstance(decision.get("public_api_policy"), dict) else {}
        exposes = bool(policy.get("exposes_public_api"))
        if exposes:
            modules_exposing_public_api += 1
            expected_function_roles = [str(role) for role in policy.get("expected_public_function_roles", []) if str(role).strip()]
            expected_type_roles = [str(role) for role in policy.get("expected_public_type_roles", []) if str(role).strip()]
            resolved_type_roles = decision.get("resolved_public_type_roles", {}) if isinstance(decision.get("resolved_public_type_roles"), dict) else {}
            header_names = header_names_by_module.get(module_id, set())
            public_data = public_data_by_module.get(module_id, set())
            artifacts = module_artifacts_by_name.get(module_id, [])
            artifact_keys = {_artifact_key(artifact) for artifact in artifacts if isinstance(artifact, dict)}
            has_public_func = bool(header_names)
            has_public_data = bool(public_data)
            if not has_public_func and not has_public_data:
                diagnostics.append(PlanningDiagnostic("error", "coder_public_api_policy_has_no_public_surface", f"module '{module_id}' exposes public API but has no public function or data surface", str(bundle.module_spec_path)))
            if expected_function_roles and not has_public_func:
                diagnostics.append(PlanningDiagnostic("error", "coder_public_api_policy_missing_function_surface", f"module '{module_id}' expects public function roles but HEADER.INTERFACE is empty", str(bundle.module_spec_path)))
            if expected_type_roles and not any(kind == "TYPE" for _name, kind in public_data | artifact_keys):
                diagnostics.append(PlanningDiagnostic("error", "coder_public_api_policy_missing_type_surface", f"module '{module_id}' expects public type roles but has no public TYPE artifact or HEADER.DATA", str(bundle.module_spec_path)))
            module_type_keys = public_type_keys_by_module.get(module_id, set())
            for role in expected_type_roles:
                role_keys = {normalize_type_key(role), normalize_type_key(resolved_type_roles.get(role, ""))}
                if not (role_keys - {""}) & (module_type_keys | all_public_type_keys):
                    diagnostics.append(
                        PlanningDiagnostic(
                            "error",
                            "coder_public_api_policy_missing_expected_type_role",
                            f"module '{module_id}' expected public type role '{role}' is not represented by public HEADER.DATA or ARTIFACTS",
                            str(bundle.module_spec_path),
                        )
                    )
        else:
            modules_explicitly_internal += 1
            if not str(policy.get("no_public_api_reason", "")).strip():
                diagnostics.append(PlanningDiagnostic("error", "coder_internal_module_missing_reason", f"module '{module_id}' declares no public API without no_public_api_reason", str(bundle.module_spec_path)))
    if module_intent and modules_exposing_public_api == 0 and modules_explicitly_internal == len(module_intent):
        diagnostics.append(PlanningDiagnostic("warning", "coder_all_modules_internal", "All planning modules declare no public API; verify target profile does not require a library/application surface", str(bundle.module_spec_path)))

    function_symbol_by_id: dict[str, str] = {}
    for item in function_intent.values():
        ref = item.get("ref", {})
        decision = item.get("decision", {})
        function_id = str(ref.get("function_id", "")).strip()
        if function_id:
            function_symbol_by_id[function_id] = str(ref.get("name", "") or decision.get("name", "")).strip()
    all_header_names = {name for names in header_names_by_module.values() for name in names}
    for function_id in public_function_intent_ids:
        symbol = function_symbol_by_id.get(function_id, "")
        if symbol and symbol not in all_header_names:
            diagnostics.append(PlanningDiagnostic("error", "coder_planned_public_function_missing_header", f"planned public function '{function_id}' is missing from HEADER.INTERFACE", str(bundle.module_spec_path)))

    try:
        from agent.coder.generation import render_header
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        diagnostics.append(PlanningDiagnostic("warning", "coder_header_render_unavailable", f"Could not import header renderer: {exc}", str(bundle.spec_root)))
        return diagnostics

    for file_spec in bundle.file_specs_by_trace.values():
        if not file_spec.header_interfaces and not any(str(item.get("VISIBILITY", "")).upper() == "PUBLIC" for item in file_spec.header_data):
            continue
        try:
            rendered = render_header(bundle, file_spec)
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            diagnostics.append(PlanningDiagnostic("error", "coder_header_render_failed", f"Header render failed: {exc}", str(file_spec.spec_path)))
            continue
        for item in file_spec.header_interfaces:
            if item.visibility == "public" and item.name not in rendered:
                diagnostics.append(PlanningDiagnostic("error", "coder_header_render_missing_function", f"Rendered header omitted public function '{item.name}'", str(file_spec.spec_path)))
        for item in file_spec.header_data:
            name = str(item.get("NAME", "")).strip()
            if str(item.get("VISIBILITY", "")).upper() == "PUBLIC" and name and name not in rendered:
                diagnostics.append(PlanningDiagnostic("error", "coder_header_render_missing_data", f"Rendered header omitted public data symbol '{name}'", str(file_spec.spec_path)))

    return diagnostics
