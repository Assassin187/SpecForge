from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..common.llm_client import FIXED_MODEL, FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .analyzer import analyze_protocol_profile
from .architecture import generate_candidate_architectures, normalize_candidate_modules
from .constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN, MODULE_BUDGET_TARGET
from .decision_graph import derive_design_decisions
from .dependency_graph import apply_dependency_graph_to_modules, build_dependency_graph
from .file_layout import FileLayoutGenerationError, apply_file_layout_to_modules, build_file_layout
from .ir import build_planning_ir, load_target_profile
from .knowledge_base import load_rules
from .models import ArchitectureReview, ImplementationPlan, PlanningDiagnostic, PlanningIR, PlanningResult
from .prompts import build_implementation_plan_prompt
from .rule_engine import activate_rules
from .scorer import architecture_scores_from_review, judge_candidate_architectures, lint_candidate_architectures
from .spec_blueprint import blueprint_to_jsonable, build_spec_blueprint
from .spec_compiler import compile_spec_bundle
from .verifier import verify_output_dir


class PlanningLogger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._event_log_path = self.root / "000_stage_events.log"

    def write(self, label: str, content: str, suffix: str = ".txt") -> Path:
        self._counter += 1
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "log"
        path = self.root / f"{self._counter:03d}_{safe_label}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def log_event(self, message: str) -> Path:
        with self._event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
        return self._event_log_path


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def _run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def default_output_dir(facts_path: str | Path, target_slug: str) -> Path:
    facts_file = Path(facts_path)
    protocol_name = "protocol"
    if facts_file.exists():
        try:
            protocol_name = json.loads(facts_file.read_text(encoding="utf-8")).get("protocol_meta", {}).get("protocol_name", "protocol")
        except Exception:
            protocol_name = "protocol"
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "agent" / "planning" / "out" / _safe_slug(str(protocol_name)) / target_slug / _run_timestamp()


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


STEP_LOGS_DIRNAME = "_step_logs"


README_STEP_DEFINITIONS = (
    (0, "output_dir_init", "输出目录和日志初始化"),
    (1, "input_validation", "输入验证"),
    (2, "target_profile", "Target Profile 加载"),
    (3, "planning_ir", "Planning IR 构建"),
    (4, "protocol_profile", "Protocol Profile 分析"),
    (5, "expert_activations", "专家规则激活"),
    (6, "architecture_selection", "候选架构生成与评审"),
    (7, "implementation_plan", "工程实现计划生成"),
    (8, "dependency_graph", "依赖图生成"),
    (9, "file_layout", "文件布局生成"),
    (10, "spec_blueprint", "SpecBlueprint 展开"),
    (11, "spec_bundle", "Spec Bundle 编译"),
    (12, "design_decisions", "派生设计决策记录"),
    (13, "run_manifest", "Run Manifest 和 Token Usage 写入"),
    (14, "verification", "验证与 Verification Report"),
)


STEP_OUTPUTS: dict[int, tuple[str, ...]] = {
    0: ("stage_events_log",),
    3: ("planning_ir",),
    4: ("protocol_profile",),
    5: ("expert_activations",),
    6: ("candidate_architectures", "architecture_review"),
    7: ("implementation_plan", "implementation_plan_v2"),
    8: ("step_08_dependency_graph",),
    9: ("step_09_file_layout", "file_layout_failure"),
    10: ("spec_blueprint", "expansion_candidates"),
    11: ("module_spec", "spec_root"),
    12: ("design_decisions",),
    13: ("run_manifest", "token_usage_summary"),
    14: ("planning_verification_report",),
}


STEP_ARTIFACT_FILENAMES: dict[str, str] = {
    "planning_ir": "003_planning_ir.json",
    "protocol_profile": "004_protocol_profile.json",
    "expert_activations": "005_expert_activations.json",
    "candidate_architectures": "006_candidate_architectures.json",
    "architecture_review": "006_architecture_review.json",
    "implementation_plan": "007_implementation_plan.json",
    "implementation_plan_v2": "007_implementation_plan_v2.json",
    "step_08_dependency_graph": "008_dependency_graph.json",
    "step_09_file_layout": "009_file_layout.json",
    "file_layout_failure": "009_file_layout_failure.json",
    "spec_blueprint": "010_spec_blueprint.json",
    "expansion_candidates": "010_expansion_candidates.json",
    "design_decisions": "012_design_decisions.json",
    "run_manifest": "013_run_manifest.json",
    "token_usage_summary": "013_token_usage_summary.json",
    "planning_verification_report": "014_planning_verification_report.json",
}


def _step_logs_dir(output_dir: Path) -> Path:
    return output_dir / STEP_LOGS_DIRNAME


def _step_artifact_path(output_dir: Path, key: str) -> Path:
    return _step_logs_dir(output_dir) / STEP_ARTIFACT_FILENAMES.get(key, f"{key}.json")


def _write_step_json(output_dir: Path, key: str, data: Any) -> Path:
    return _write_json(_step_artifact_path(output_dir, key), data)


def _step_artifact_index(
    output_dir: Path,
    artifact_paths: dict[str, Path],
) -> list[dict[str, Any]]:
    available: dict[str, Path] = {
        "stage_events_log": output_dir / "_agent_logs" / "000_stage_events.log",
        **artifact_paths,
    }
    result: list[dict[str, Any]] = []
    for step, stage, title in README_STEP_DEFINITIONS:
        artifacts = []
        for key in STEP_OUTPUTS.get(step, ()):
            path = available.get(key)
            if path is not None:
                artifacts.append({"name": key, "path": str(path)})
        result.append({"step": step, "stage": stage, "title": title, "artifacts": artifacts})
    return result


def _spec_counts(spec_root: Path) -> dict[str, int]:
    counts = {"module_spec_count": 0, "file_spec_count": 0, "function_spec_count": 0}
    if not spec_root.exists():
        return counts
    for path in spec_root.rglob("*_spec.json"):
        try:
            kind = json.loads(path.read_text(encoding="utf-8")).get("KIND")
        except Exception:
            continue
        if kind == "PROTOCOL_MODULE_SPEC":
            counts["module_spec_count"] += 1
        elif kind == "FILE_SPEC":
            counts["file_spec_count"] += 1
        elif kind == "FUNCTION_SPEC":
            counts["function_spec_count"] += 1
    return counts


def _build_verification_report(
    output_dir: Path,
    implementation_plan: dict[str, Any],
    spec_blueprint: dict[str, Any],
    diagnostics: list[PlanningDiagnostic],
) -> dict[str, Any]:
    spec_root = output_dir / "spec_bundle"
    generated_counts = _spec_counts(spec_root)
    return {
        "schema_version": "planning_verification_report/v1alpha1",
        "output_dir": str(output_dir),
        "implementation_plan_schema": implementation_plan.get("schema_version"),
        "expansion_profile": spec_blueprint.get("expansion_profile"),
        "coverage": {
            "minimum_surface_count": len(implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", [])),
            "handler_matrix_count": len(implementation_plan.get("handler_matrix", [])),
            "blueprint_module_count": len(spec_blueprint.get("modules", [])),
            "blueprint_file_count": len(spec_blueprint.get("files", [])),
            "blueprint_function_count": len(spec_blueprint.get("functions", [])),
            "module_budget": implementation_plan.get("module_budget"),
            "implementation_validation": implementation_plan.get("validation_report"),
        },
        "spec_counts": {
            "generated": generated_counts,
        },
        "diagnostics": [
            {"level": diag.level, "code": diag.code, "message": diag.message, "path": diag.path}
            for diag in diagnostics
        ],
        "acceptance": {
            "coder_compatibility": not any(diag.level == "error" and diag.code.startswith("coder_") for diag in diagnostics),
            "planning_verifier_ok": not any(diag.level == "error" for diag in diagnostics),
            "end_to_end_coder_compile_smoke": "not_run_by_planning_verifier",
        },
    }


def _usage_to_dict(usage: LLMUsage) -> dict[str, int]:
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _add_usage(left: LLMUsage, right: LLMUsage) -> LLMUsage:
    return LLMUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _maybe_llm_json(llm_client: FixedQwenClient | None, request: LLMRequest) -> dict[str, Any] | None:
    if llm_client is None:
        return None
    try:
        content = llm_client.generate(request)
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        return json.loads(cleaned)
    except Exception:
        return None


def _generate_with_usage(llm_client: FixedQwenClient | None, request: LLMRequest) -> LLMResponse | None:
    if llm_client is None:
        return None
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def _implementation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("implementation_plan")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in payload.items():
            if key != "implementation_plan" and key not in merged:
                merged[key] = value
        return merged
    return payload


def _detect_protocol_specific_planner_logic() -> list[PlanningDiagnostic]:
    planning_root = Path(__file__).resolve().parent
    blocked_terms = ("m" + "qtt", "f" + "tp", "s" + "mtp", "c" + "oap")
    diagnostics: list[PlanningDiagnostic] = []
    for path in sorted(planning_root.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        hits = [term for term in blocked_terms if term in text]
        if hits:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "protocol_specific_planner_logic_detected",
                    f"Core planning source contains protocol-specific terms: {', '.join(hits)}",
                    str(path),
                )
            )
    return diagnostics


def _impl_module_name(name: str) -> str:
    return _safe_slug(name)


def _build_module_graph(planning_ir: PlanningIR, chosen_architecture: dict[str, Any], activations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del activations
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    raw_modules = []
    seen = set()
    for item in normalize_candidate_modules(chosen_architecture.get("modules", []), []):
        name = _impl_module_name(str(item.get("name")))
        if name in seen:
            continue
        seen.add(name)
        raw_modules.append(
            {
                "name": name,
                "role": str(item.get("role")),
                "dependencies": [str(dep) for dep in item.get("dependencies", []) if str(dep).strip()],
                "owned_capabilities": [str(cap) for cap in item.get("owned_capabilities", []) if str(cap).strip()],
                "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if str(ref).strip()],
            }
        )
    ordered = raw_modules
    modules: list[dict[str, Any]] = []
    existing = {item["name"] for item in ordered}
    for item in ordered:
        name = item["name"]
        dependencies = [dep for dep in item.get("dependencies", []) if dep in existing and dep != name]
        modules.append(
            {
                "name": name,
                "role": item["role"],
                "dependencies": dependencies,
                "path": f"{protocol_slug}/{name}/{name}",
                "artifacts": [],
                "owned_capabilities": item.get("owned_capabilities", []),
                "evidence_refs": item.get("evidence_refs", []),
            }
        )
    return modules


def _build_canonical_types(protocol_name: str, module_graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    protocol_slug = _safe_slug(protocol_name)
    return [
        {
            "type_name": f"{protocol_slug}_{module['name']}_t",
            "owner_module": module["name"],
            "owner_file": f"{module['path']}.h",
            "visibility": "public",
        }
        for module in module_graph
    ]


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


def _build_handler_matrix(planning_ir: PlanningIR, module_graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dispatch_module = next(
        (
            item["name"]
            for item in module_graph
            if "semantic_dispatch" in item.get("owned_capabilities", [])
            or "handler" in str(item.get("name", "")).lower()
            or "dispatch" in str(item.get("name", "")).lower()
        ),
        module_graph[-1]["name"] if module_graph else "semantic_dispatch",
    )
    rows = []
    for item in planning_ir.minimum_v1.get("must_support_surface", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if not _requires_inbound_handler(item, planning_ir.target_profile.target_role):
            continue
        surface = str(item["name"])
        func_name = f"{_safe_slug(planning_ir.protocol_name)}_{dispatch_module}_handle_{_safe_slug(surface)}"
        rows.append(
            {
                "surface_unit": surface,
                "handler_module": dispatch_module,
                "handler_function": func_name,
                "path_summary": f"decode -> {dispatch_module} -> protocol action",
                "evidence_refs": list(item.get("evidence_refs", [])),
            }
        )
    return rows


def _normalize_handler_matrix(
    planning_ir: PlanningIR,
    module_graph: list[dict[str, Any]],
    rows: Any,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    module_names = {str(item.get("name")) for item in module_graph if isinstance(item, dict)}
    normalized: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("surface_unit"):
            continue
        source_surface = next(
            (
                surface
                for surface in planning_ir.minimum_v1.get("must_support_surface", [])
                if isinstance(surface, dict) and str(surface.get("name", "")) == str(item.get("surface_unit", ""))
            ),
            {"name": item.get("surface_unit")},
        )
        if not _requires_inbound_handler(source_surface, planning_ir.target_profile.target_role):
            continue
        module = str(item.get("handler_module") or item.get("handling_module") or "").strip()
        module = _safe_slug(module) if module else ""
        if module not in module_names:
            continue
        surface = str(item["surface_unit"])
        function_name = str(item.get("handler_function") or "").strip()
        if not function_name:
            function_name = f"{protocol_slug}_{module}_handle_{_safe_slug(surface)}"
        action_parts = []
        for key in ("path_summary", "downstream_actions", "validation_checks", "state_transitions"):
            value = item.get(key)
            if isinstance(value, list):
                action_parts.extend(str(part) for part in value)
            elif value:
                action_parts.append(str(value))
        normalized.append(
            {
                "surface_unit": surface,
                "handler_module": module,
                "handler_function": function_name,
                "path_summary": " -> ".join(action_parts) if action_parts else f"decode -> {module} -> protocol action",
                "evidence_refs": list(item.get("evidence_refs", [])) if isinstance(item.get("evidence_refs", []), list) else [],
            }
        )
    return normalized


def _required_capabilities(profile: dict[str, Any], activations: list[dict[str, Any]]) -> set[str]:
    required = {str(item) for item in profile.get("required_capabilities", []) if str(item).strip()}
    for activation in activations:
        for capability in activation.get("required_capabilities", []):
            text = str(capability).strip()
            if text:
                required.add(text)
    return required


def _owned_capabilities(module_graph: list[dict[str, Any]]) -> set[str]:
    owned: set[str] = set()
    for module in module_graph:
        for capability in module.get("owned_capabilities", []):
            text = str(capability).strip()
            if text:
                owned.add(text)
    return owned


def _module_budget_ok(module_graph: list[dict[str, Any]]) -> bool:
    return MODULE_BUDGET_MIN <= len(module_graph) <= MODULE_BUDGET_MAX


def _capability_gaps(profile: dict[str, Any], activations: list[dict[str, Any]], module_graph: list[dict[str, Any]]) -> list[str]:
    return sorted(_required_capabilities(profile, activations) - _owned_capabilities(module_graph))


def _build_file_and_function_plan(planning_ir: PlanningIR, module_graph: list[dict[str, Any]], handler_matrix: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    file_plan: list[dict[str, Any]] = []
    function_plan: list[dict[str, Any]] = []
    for module in module_graph:
        public_type = f"{protocol_slug}_{module['name']}_t"
        header_path = f"{module['path']}.h"
        source_path = f"{module['path']}.c"
        header_dependencies = []
        if module["name"] == "handler_dispatch":
            header_dependencies = [f"{protocol_slug}/protocol_codec/protocol_codec.h"]
        source_dependencies = [header_path]
        trace_id = f"{protocol_slug}/{module['name']}/{module['name']}"
        file_plan.append(
            {
                "module": module["name"],
                "trace_id": trace_id,
                "header_path": header_path,
                "source_path": source_path,
                "public_type": public_type,
                "header_dependencies": header_dependencies,
                "source_dependencies": source_dependencies,
            }
        )
        lifecycle_funcs = [
            {
                "name": f"{protocol_slug}_{module['name']}_create",
                "return_type": f"{public_type}*",
                "params": [],
                "role": f"Create {module['name']} module state",
            },
            {
                "name": f"{protocol_slug}_{module['name']}_destroy",
                "return_type": "void",
                "params": [{"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": True, "OWNERSHIP": "BORROWED"}],
                "role": f"Destroy {module['name']} module state",
            },
        ]
        for func in lifecycle_funcs:
            function_plan.append(
                {
                    "module": module["name"],
                    "trace_id": f"{trace_id}/{func['name']}",
                    "name": func["name"],
                    "return_type": func["return_type"],
                    "params": func["params"],
                    "function_type": "ALGORITHM",
                    "visibility": "public",
                    "role": func["role"],
                    "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                    "logic": {
                        "input": "module inputs",
                        "action": func["role"],
                        "output": "module handle or cleanup side effect",
                        "invariants": ["Maintain module-local ownership invariants"],
                    },
                }
            )
        for row in handler_matrix:
            if row["handler_module"] != module["name"]:
                continue
            function_plan.append(
                {
                    "module": module["name"],
                    "trace_id": f"{trace_id}/{row['handler_function']}",
                    "name": row["handler_function"],
                    "return_type": "int",
                    "params": [
                        {"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                        {"TYPE": "const void*", "NAME": "message", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
                    ],
                    "function_type": "EVENT",
                    "visibility": "public",
                    "role": f"Handle {row['surface_unit']} for minimum_v1 path",
                    "rely": {"STRUCT": [], "FUNC": [], "VAR": []},
                    "logic": {
                        "input": f"decoded {row['surface_unit']} and module state",
                        "action": row["path_summary"],
                        "output": "0 on success, non-zero on protocol or processing failure",
                        "invariants": ["Preserve canonical ownership boundaries", "Apply shared error policy when violated"],
                    },
                }
            )
    return file_plan, function_plan


def _derived_decision_ids(planning_ir: PlanningIR) -> list[str]:
    ids = [
        "dec_target_scope",
        "dec_runtime_shape",
        "dec_architecture_choice",
        "dec_state_ownership",
        "dec_error_policy",
        "dec_handler_coverage",
        "dec_dependency_graph",
        "dec_file_layout",
    ]
    if planning_ir.facts.get("routing_model", {}).get("dispatch_keys"):
        ids.append("dec_routing_split")
    return ids


def _build_implementation_plan(
    planning_ir: PlanningIR,
    profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    activations: list[dict[str, Any]],
    llm_client: FixedQwenClient | None,
    log: Any = None,
    log_artifact: Any = None,
    log_step_artifact: Any = None,
    register_usage: Any = None,
) -> ImplementationPlan:
    module_graph = _build_module_graph(planning_ir, chosen_architecture, activations)
    canonical_types = _build_canonical_types(planning_ir.protocol_name, module_graph)
    handler_matrix = _build_handler_matrix(planning_ir, module_graph)
    plan = {
        "schema_version": "implementation_plan/v2alpha3",
        "planning_authority": "llm_required",
        "module_budget": {"target": MODULE_BUDGET_TARGET, "min": MODULE_BUDGET_MIN, "max": MODULE_BUDGET_MAX},
        "target_profile": planning_ir.target_profile.raw,
        "protocol_name": planning_ir.protocol_name,
        "protocol_description": {
            "interaction_model": profile.get("interaction_model"),
            "transport_shape": profile.get("transport_shape"),
            "statefulness": profile.get("statefulness"),
        },
        "scope_decisions": {
            "mode": planning_ir.target_profile.scope,
            "minimum_v1_surface": list(planning_ir.minimum_v1.get("must_support_surface", [])),
            "deferred_features": list(planning_ir.minimum_v1.get("may_defer_features", [])),
            "assumptions": list(planning_ir.minimum_v1.get("implementation_assumptions", [])),
        },
        "module_graph": module_graph,
        "canonical_types": canonical_types,
        "state_design": {
            "state_nodes": planning_ir.state_nodes,
            "transitions": planning_ir.transitions,
            "invariants": [
                "Canonical public types have one owner module",
                "Minimum-v1 handlers must preserve state and error-policy consistency",
            ],
        },
        "handler_matrix": handler_matrix,
        "resource_lifecycle": {
            "resource_objects": planning_ir.resource_objects,
            "lifecycle_rules": list(planning_ir.facts.get("resource_model", {}).get("lifecycle_rules", [])),
        },
        "error_strategy": {
            "policy": str(profile.get("failure_semantics") or "mixed"),
            "error_matrix": planning_ir.error_matrix,
        },
        "test_plan": {
            "unit_tests": ["type ownership uniqueness", "handler_matrix minimum_v1 coverage", "module dependency acyclic", "spec_blueprint traceability coverage"],
            "integration_tests": ["compiled spec bundle validates in coder", "facts -> planning -> coder -> compile -> protocol smoke tests"],
        },
        "traceability": {
            "decision_ids": _derived_decision_ids(planning_ir),
            "evidence_index_size": len(planning_ir.evidence_by_id),
        },
        "unresolved_questions": planning_ir.open_questions,
    }
    messages = build_implementation_plan_prompt(
        {
            "protocol_name": planning_ir.protocol_name,
            "normalized_roles": planning_ir.normalized_roles,
            "minimum_v1_surface": [item.get("name") for item in planning_ir.minimum_v1.get("must_support_surface", []) if isinstance(item, dict)],
            "blocking_questions": planning_ir.open_questions.get("blocking", []),
        },
        profile,
        chosen_architecture,
    )
    if llm_client is None:
        if log is not None:
            log("implementation_plan llm_required_missing fallback=disabled")
        return ImplementationPlan({})
    else:
        if log_artifact is not None:
            log_artifact("implementation_plan_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"implementation_plan llm_request_start messages={len(messages)}")
        try:
            response = _generate_with_usage(
                llm_client,
                LLMRequest(
                    messages=messages,
                    top_p=0.4,
                    temperature=0.2,
                    is_stream=True,
                ),
            )
        except Exception:
            response = None
            if log is not None:
                log("implementation_plan llm_failed fallback=disabled")
        payload = None
        if response is not None:
            if register_usage is not None:
                register_usage("implementation_plan", "generate", response.usage)
            if log is not None:
                log(
                    f"implementation_plan llm_response_done chars={len(response.content)} "
                    f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
                )
            if log_artifact is not None:
                log_artifact("implementation_plan_response", response.content, ".txt")
            try:
                cleaned = response.content.strip()
                if cleaned.startswith("```"):
                    lines = cleaned.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    cleaned = "\n".join(lines).strip()
                payload = json.loads(cleaned)
            except Exception:
                payload = None
        if log is not None:
            log(f"implementation_plan llm_response_done merged={'yes' if isinstance(payload, dict) else 'no'}")
        if log_artifact is not None and isinstance(payload, dict):
            log_artifact("implementation_plan_response_normalized", json.dumps(payload, ensure_ascii=False, indent=2), ".json")
    if not isinstance(payload, dict):
        return ImplementationPlan({})
    if isinstance(payload, dict):
        payload = _implementation_payload(payload)
        for key in (
            "protocol_description",
            "scope_decisions",
            "state_design",
            "resource_lifecycle",
            "error_strategy",
            "test_plan",
            "traceability",
            "unresolved_questions",
        ):
            if isinstance(payload.get(key), dict):
                plan[key] = payload[key]
        if isinstance(payload.get("module_graph"), list):
            candidate_module_graph = _build_module_graph(planning_ir, {"modules": payload["module_graph"]}, activations)
            if (
                _module_budget_ok(candidate_module_graph)
                and not _capability_gaps(profile, activations, candidate_module_graph)
            ):
                plan["module_graph"] = candidate_module_graph
            elif log is not None:
                log("implementation_plan module_graph_override_rejected using_selected_architecture_scaffold")
        plan["canonical_types"] = _build_canonical_types(planning_ir.protocol_name, list(plan.get("module_graph", [])))
        if isinstance(payload.get("handler_matrix"), list):
            plan["handler_matrix"] = _normalize_handler_matrix(planning_ir, list(plan.get("module_graph", [])), payload["handler_matrix"])
        if not plan.get("handler_matrix"):
            plan["handler_matrix"] = _build_handler_matrix(planning_ir, list(plan.get("module_graph", [])))
    module_graph = list(plan.get("module_graph", []))
    plan["canonical_types"] = _build_canonical_types(planning_ir.protocol_name, module_graph)
    dependency_graph = build_dependency_graph(
        planning_ir,
        profile,
        chosen_architecture,
        plan,
        llm_client,
        log=log,
        log_artifact=log_artifact,
        register_usage=register_usage,
    )
    plan["dependency_graph"] = dependency_graph
    if log_step_artifact is not None:
        log_step_artifact("step_08_dependency_graph", dependency_graph)
    plan["module_graph"] = apply_dependency_graph_to_modules(
        module_graph,
        dependency_graph,
        list(plan.get("canonical_types", [])),
        list(plan.get("handler_matrix", [])),
        planning_ir.protocol_name,
    )
    file_layout = build_file_layout(
        planning_ir,
        profile,
        chosen_architecture,
        plan,
        llm_client,
        log=log,
        log_artifact=log_artifact,
        register_usage=register_usage,
    )
    plan["file_layout"] = file_layout
    if log_step_artifact is not None:
        log_step_artifact("step_09_file_layout", file_layout)
    plan["module_graph"] = apply_file_layout_to_modules(
        list(plan.get("module_graph", [])),
        file_layout,
        list(plan.get("canonical_types", [])),
        list(plan.get("handler_matrix", [])),
        planning_ir.protocol_name,
    )
    module_graph = list(plan.get("module_graph", []))
    capability_gaps = _capability_gaps(profile, activations, module_graph)
    plan["validation_report"] = {
        "module_budget_ok": _module_budget_ok(module_graph),
        "module_count": len(module_graph),
        "required_capability_count": len(_required_capabilities(profile, activations)),
        "missing_capabilities": capability_gaps,
        "dependency_graph": dependency_graph.get("validation", {}),
        "file_layout": file_layout.get("validation", {}),
    }
    return ImplementationPlan(plan)


class PlanningAgent:
    def __init__(
        self,
        facts_path: str | Path,
        target_profile_path: str | Path,
        output_dir: str | Path | None = None,
        llm_client: FixedQwenClient | None = None,
    ) -> None:
        self.facts_path = Path(facts_path)
        self.target_profile_path = Path(target_profile_path)
        self.llm_client = llm_client
        self.logs = PlanningLogger(Path(output_dir) / "_agent_logs") if output_dir else None
        target_slug = "invalid"
        if self.target_profile_path.exists():
            try:
                target_profile, _ = load_target_profile(self.target_profile_path)
                if target_profile is not None:
                    target_slug = target_profile.slug
            except Exception:
                target_slug = "invalid"
        self.output_dir = Path(output_dir) if output_dir else default_output_dir(self.facts_path, target_slug)
        self.logs = PlanningLogger(self.output_dir / "_agent_logs")

    def _log(self, message: str) -> None:
        text = f"[agent.planning] {message}"
        print(text, flush=True)
        self.logs.log_event(text)

    def validate_inputs(self, skip_llm_check: bool = False) -> list[PlanningDiagnostic]:
        self._log("validate inputs start")
        diagnostics: list[PlanningDiagnostic] = []
        diagnostics.extend(_detect_protocol_specific_planner_logic())
        if not self.facts_path.exists():
            diagnostics.append(PlanningDiagnostic("error", "missing_facts_path", "Facts path does not exist", str(self.facts_path)))
        if not self.target_profile_path.exists():
            diagnostics.append(PlanningDiagnostic("error", "missing_target_profile_path", "Target profile path does not exist", str(self.target_profile_path)))
        target_profile, target_diags = load_target_profile(self.target_profile_path)
        diagnostics.extend(target_diags)
        if target_profile and target_profile.language.lower() not in {"c"}:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "unsupported_target_language",
                    f"Current spec compiler only emits coder-compatible C specs, got language='{target_profile.language}'",
                    str(self.target_profile_path),
                )
            )
        if self.facts_path.exists() and target_profile:
            _, ir_diags = build_planning_ir(self.facts_path, target_profile)
            diagnostics.extend(ir_diags)
        if not skip_llm_check and self.llm_client is not None:
            try:
                self.llm_client.self_check()
                self._log("llm self-check passed")
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(PlanningDiagnostic("error", "llm_self_check_failed", str(exc), str(self.target_profile_path)))
                self._log(f"llm self-check error={type(exc).__name__}")
        elif not skip_llm_check and self.llm_client is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "llm_required_missing",
                    "Planning requires an LLM client for architecture and implementation-plan generation.",
                    str(self.target_profile_path),
                )
            )
        self._log(f"validate inputs done diagnostics={len(diagnostics)}")
        return diagnostics

    def plan(self) -> PlanningResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs = PlanningLogger(self.output_dir / "_agent_logs")
        self._log(
            f"plan start facts={self.facts_path} target_profile={self.target_profile_path} "
            f"output_dir={self.output_dir} llm={'enabled' if self.llm_client is not None else 'disabled'}"
        )
        workflow_usage = LLMUsage(0, 0, 0)
        llm_call_usage: list[dict[str, Any]] = []
        shared_stage_usage: dict[str, dict[str, int]] = {}
        artifact_paths: dict[str, Path] = {}

        def register_usage(stage: str, call_type: str, usage: LLMUsage) -> None:
            nonlocal workflow_usage
            workflow_usage = _add_usage(workflow_usage, usage)
            llm_call_usage.append(
                {
                    "stage": stage,
                    "call_type": call_type,
                    "usage": _usage_to_dict(usage),
                }
            )
            existing = shared_stage_usage.get(stage, _usage_to_dict(LLMUsage(0, 0, 0)))
            shared_stage_usage[stage] = _usage_to_dict(_add_usage(LLMUsage(**existing), usage))

        def log_artifact(label: str, content: str, suffix: str = ".txt") -> Path:
            return self.logs.write(label, content, suffix)

        def log_step_artifact(label: str, payload: Any) -> Path:
            path = _write_step_json(self.output_dir, label, payload)
            artifact_paths[label] = path
            return path

        diagnostics = self.validate_inputs(skip_llm_check=False)
        if any(diag.level == "error" for diag in diagnostics):
            self._log("plan aborted validation_errors=yes")
            return PlanningResult(False, self.output_dir, diagnostics, {})
        target_profile, target_diags = load_target_profile(self.target_profile_path)
        diagnostics.extend(target_diags)
        self._log("stage=target_profile load done")
        planning_ir, ir_diags = build_planning_ir(self.facts_path, target_profile)
        diagnostics.extend(ir_diags)
        if planning_ir is None:
            self._log("stage=planning_ir build failed")
            return PlanningResult(False, self.output_dir, diagnostics, {})
        self._log(
            f"stage=planning_ir build done protocol={planning_ir.protocol_name} "
            f"surfaces={len(planning_ir.surface_units)} states={len(planning_ir.state_nodes)}"
        )
        artifact_paths["planning_ir"] = log_step_artifact("planning_ir", asdict(planning_ir))
        self._log("artifact=planning_ir written")

        self._log("stage=protocol_profile analyze start")
        profile = analyze_protocol_profile(planning_ir)
        artifact_paths["protocol_profile"] = log_step_artifact("protocol_profile", profile.data)
        self._log("stage=protocol_profile analyze done")

        self._log("stage=expert_rules activate start")
        activations = activate_rules(load_rules(), profile)
        artifact_paths["expert_activations"] = log_step_artifact("expert_activations", [asdict(item) for item in activations])
        self._log(f"stage=expert_rules activate done activations={len(activations)}")

        self._log("stage=architectures generate start")
        candidates = generate_candidate_architectures(
            planning_ir,
            profile,
            activations,
            self.llm_client,
            log=self._log,
            log_artifact=log_artifact,
            register_usage=register_usage,
        )
        lint_results = lint_candidate_architectures(planning_ir, profile, activations, candidates)
        candidate_payload = {
            "candidates": [asdict(item) for item in candidates],
            "lint_results": [asdict(item) for item in lint_results],
        }
        artifact_paths["candidate_architectures"] = log_step_artifact("candidate_architectures", candidate_payload)
        self._log(f"stage=architectures generate done candidates={len(candidates)}")
        if not candidates:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "llm_planning_invalid_json",
                    "LLM did not produce any valid architecture candidates.",
                    str(_step_artifact_path(self.output_dir, "candidate_architectures")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        self._log("stage=architecture_judge start")
        review = judge_candidate_architectures(
            planning_ir,
            profile,
            activations,
            candidates,
            lint_results,
            self.llm_client,
            log=self._log,
            log_artifact=log_artifact,
            register_usage=register_usage,
        )
        if review is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "architecture_judge_invalid_json",
                    "Architecture judge did not produce a valid architecture_review JSON object.",
                    str(_step_artifact_path(self.output_dir, "architecture_review")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        selected_candidate_id = review.selected_candidate_id or next(
            (
                item.candidate_id
                for item in review.scores
                if item.recommended_selection and item.verdict.lower() == "accept"
            ),
            "",
        )
        if selected_candidate_id != review.selected_candidate_id:
            review = ArchitectureReview(
                selected_candidate_id=selected_candidate_id,
                rejection_reason=review.rejection_reason,
                scores=review.scores,
            )
        artifact_paths["architecture_review"] = log_step_artifact("architecture_review", asdict(review))
        scores = architecture_scores_from_review(review, candidates)
        selected_score = next((score for score in scores if score.selected), None)
        selected_review = next((item for item in review.scores if item.candidate_id == selected_candidate_id), None)
        lint_by_id = {item.candidate_id: item for item in lint_results}
        selected_lint = lint_by_id.get(selected_candidate_id)
        if selected_score is None or selected_review is None or selected_review.verdict.lower() != "accept":
            reason_text = review.rejection_reason or "; ".join(
                f"{item.candidate_id}: {item.verdict} {item.rationale}".strip()
                for item in review.scores
            )
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "architecture_judge_rejected_all",
                    reason_text or "Architecture judge did not accept any candidate architecture.",
                    str(_step_artifact_path(self.output_dir, "architecture_review")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        if selected_lint is None or not selected_lint.ok:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "architecture_judge_selected_lint_failed",
                    f"Architecture judge selected candidate '{selected_candidate_id}' but hard lint failed: {selected_lint.schema_errors if selected_lint else 'missing lint result'}",
                    str(_step_artifact_path(self.output_dir, "architecture_review")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        self._log(f"stage=architecture_judge done selected={selected_candidate_id}")

        chosen = next(candidate for candidate in candidates if selected_candidate_id == candidate.candidate_id)
        self._log("stage=implementation_plan build start")
        try:
            implementation_plan = _build_implementation_plan(
                planning_ir,
                profile.data,
                asdict(chosen),
                [asdict(item) for item in activations],
                self.llm_client,
                log=self._log,
                log_artifact=log_artifact,
                log_step_artifact=log_step_artifact,
                register_usage=register_usage,
            )
        except FileLayoutGenerationError as exc:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    exc.code,
                    str(exc),
                    str(_step_artifact_path(self.output_dir, "implementation_plan")),
                )
            )
            failure_payload = {
                "stage": "file_layout",
                "code": exc.code,
                "message": str(exc),
                "attempts": exc.attempts,
            }
            artifact_paths["file_layout_failure"] = log_step_artifact("file_layout_failure", failure_payload)
            token_summary_payload = {
                "llm_call_usage": llm_call_usage,
                "shared_stage_token_usage": shared_stage_usage,
                "workflow_token_usage": _usage_to_dict(workflow_usage),
                "file_layout_failure": failure_payload,
            }
            artifact_paths["token_usage_summary"] = log_step_artifact("token_usage_summary", token_summary_payload)
            artifact_paths["run_manifest"] = _step_artifact_path(self.output_dir, "run_manifest")
            failure_manifest = {
                "model": FIXED_MODEL,
                "facts_path": str(self.facts_path),
                "target_profile_path": str(self.target_profile_path),
                "output_dir": str(self.output_dir),
                "artifacts": {key: str(value) for key, value in artifact_paths.items()},
                "step_artifacts": _step_artifact_index(self.output_dir, artifact_paths),
                "failure": failure_payload,
                "llm_call_usage": llm_call_usage,
                "shared_stage_token_usage": shared_stage_usage,
                "workflow_token_usage": _usage_to_dict(workflow_usage),
            }
            _write_json(artifact_paths["run_manifest"], failure_manifest)
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        if not implementation_plan.data:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "llm_plan_schema_violation",
                    "LLM did not produce a usable implementation plan.",
                    str(_step_artifact_path(self.output_dir, "implementation_plan")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        module_graph = list(implementation_plan.data.get("module_graph", []))
        if not _module_budget_ok(module_graph):
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "module_budget_violation",
                    f"Implementation plan has {len(module_graph)} modules; expected {MODULE_BUDGET_MIN}-{MODULE_BUDGET_MAX}.",
                    str(_step_artifact_path(self.output_dir, "implementation_plan")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        gaps = _capability_gaps(profile.data, [asdict(item) for item in activations], module_graph)
        if gaps:
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "capability_coverage_gap",
                    f"Implementation plan does not assign required capabilities: {', '.join(gaps)}",
                    str(_step_artifact_path(self.output_dir, "implementation_plan")),
                )
            )
            return PlanningResult(False, self.output_dir, diagnostics, artifact_paths)
        artifact_paths["implementation_plan"] = log_step_artifact("implementation_plan", implementation_plan.data)
        artifact_paths["implementation_plan_v2"] = log_step_artifact("implementation_plan_v2", implementation_plan.data)
        self._log("stage=implementation_plan build done")

        self._log("stage=spec_blueprint expand start")
        spec_blueprint, expansion_candidates = build_spec_blueprint(
            planning_ir,
            implementation_plan.data,
            target_profile,
            self.llm_client,
        )
        spec_blueprint_data = blueprint_to_jsonable(spec_blueprint)
        artifact_paths["spec_blueprint"] = log_step_artifact("spec_blueprint", spec_blueprint_data)
        artifact_paths["expansion_candidates"] = log_step_artifact("expansion_candidates", {"candidates": expansion_candidates})
        self._log(
            f"stage=spec_blueprint expand done profile={spec_blueprint_data.get('expansion_profile')} "
            f"files={len(spec_blueprint_data.get('files', []))} functions={len(spec_blueprint_data.get('functions', []))}"
        )

        self._log("stage=spec_bundle compile start")
        compiled = compile_spec_bundle(spec_blueprint_data, self.output_dir, target_profile)
        artifact_paths.update(compiled)
        self._log(f"stage=spec_bundle compile done artifacts={len(compiled)}")

        self._log("stage=design_decisions derive start")
        decisions = derive_design_decisions(
            planning_ir,
            profile,
            chosen,
            activations,
            scores,
            implementation_plan.data,
        )
        artifact_paths["design_decisions"] = log_step_artifact("design_decisions", [asdict(item) for item in decisions])
        self._log(f"stage=design_decisions derive done decisions={len(decisions)}")

        artifact_paths["run_manifest"] = _step_artifact_path(self.output_dir, "run_manifest")
        token_summary_payload = {
            "llm_call_usage": llm_call_usage,
            "shared_stage_token_usage": shared_stage_usage,
            "workflow_token_usage": _usage_to_dict(workflow_usage),
        }
        artifact_paths["token_usage_summary"] = log_step_artifact("token_usage_summary", token_summary_payload)
        run_manifest = {
            "model": FIXED_MODEL,
            "facts_path": str(self.facts_path),
            "target_profile_path": str(self.target_profile_path),
            "output_dir": str(self.output_dir),
            "artifacts": {key: str(value) for key, value in artifact_paths.items()},
            "step_artifacts": _step_artifact_index(self.output_dir, artifact_paths),
            "selected_architecture": chosen.candidate_id,
            "decision_count": len(decisions),
            "rule_activation_count": len(activations),
            "llm_call_usage": llm_call_usage,
            "shared_stage_token_usage": shared_stage_usage,
            "workflow_token_usage": _usage_to_dict(workflow_usage),
        }
        _write_json(artifact_paths["run_manifest"], run_manifest)
        self._log("artifact=run_manifest written")
        self._log(
            f"workflow token usage "
            f"(total={workflow_usage.total_tokens} in={workflow_usage.prompt_tokens} out={workflow_usage.completion_tokens})"
        )

        self._log("stage=verify start")
        verify_result = verify_output_dir(self.output_dir)
        diagnostics.extend(verify_result.diagnostics)
        verification_report = _build_verification_report(self.output_dir, implementation_plan.data, spec_blueprint_data, diagnostics)
        artifact_paths["planning_verification_report"] = log_step_artifact("planning_verification_report", verification_report)
        run_manifest["artifacts"] = {key: str(value) for key, value in artifact_paths.items()}
        run_manifest["step_artifacts"] = _step_artifact_index(self.output_dir, artifact_paths)
        _write_json(artifact_paths["run_manifest"], run_manifest)
        success = not any(diag.level == "error" for diag in diagnostics)
        self._log(f"stage=verify done diagnostics={len(verify_result.diagnostics)}")
        self._log(f"plan done success={'yes' if success else 'no'} artifacts={len(artifact_paths)} diagnostics={len(diagnostics)}")
        return PlanningResult(success, self.output_dir, diagnostics, artifact_paths)
