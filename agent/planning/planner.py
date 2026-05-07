from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..common.llm_client import FIXED_MODEL, FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .analyzer import analyze_protocol_profile
from .architecture import generate_candidate_architectures, normalize_candidate_modules
from .decision_graph import build_design_decisions
from .ir import build_planning_ir, load_target_profile
from .knowledge_base import load_rules
from .models import ImplementationPlan, PlanningDiagnostic, PlanningIR, PlanningResult
from .prompts import build_implementation_plan_prompt
from .rule_engine import activate_rules
from .scorer import score_candidate_architectures
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


def default_output_dir(facts_path: str | Path, target_slug: str) -> Path:
    facts_file = Path(facts_path)
    protocol_name = "protocol"
    if facts_file.exists():
        try:
            protocol_name = json.loads(facts_file.read_text(encoding="utf-8")).get("protocol_meta", {}).get("protocol_name", "protocol")
        except Exception:
            protocol_name = "protocol"
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "agent" / "planning" / "out" / _safe_slug(str(protocol_name)) / target_slug


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def _impl_module_name(name: str) -> str:
    return _safe_slug(name)


def _build_module_graph(planning_ir: PlanningIR, chosen_architecture: dict[str, Any], activations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    raw_modules = []
    seen = set()
    for item in normalize_candidate_modules(chosen_architecture.get("modules", []), []):
        name = _impl_module_name(str(item.get("name")))
        if name in seen:
            continue
        seen.add(name)
        raw_modules.append({"name": name, "role": str(item.get("role"))})
    preferred_order = ["transport_runtime", "protocol_codec", "state_store", "router", "handler_dispatch"]
    name_order = {name: idx for idx, name in enumerate(preferred_order)}
    ordered = sorted(raw_modules, key=lambda item: (name_order.get(item["name"], 999), item["name"]))
    modules: list[dict[str, Any]] = []
    existing = {item["name"] for item in ordered}
    for item in ordered:
        name = item["name"]
        dependencies: list[str] = []
        if name == "router":
            dependencies = [dep for dep in ["protocol_codec", "state_store"] if dep in existing]
        elif name == "handler_dispatch":
            dependencies = [dep for dep in ["protocol_codec", "router", "state_store"] if dep in existing]
        elif name.endswith("_app"):
            dependencies = [module["name"] for module in modules]
        modules.append(
            {
                "name": name,
                "role": item["role"],
                "dependencies": dependencies,
                "path": f"{protocol_slug}/{name}/{name}",
                "artifacts": [],
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


def _build_handler_matrix(planning_ir: PlanningIR, module_graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dispatch_module = next((item["name"] for item in module_graph if item["name"] == "handler_dispatch"), module_graph[-1]["name"] if module_graph else "handler_dispatch")
    rows = []
    for item in planning_ir.minimum_v1.get("must_support_surface", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        surface = str(item["name"])
        func_name = f"{_safe_slug(planning_ir.protocol_name)}_{dispatch_module}_handle_{_safe_slug(surface)}"
        rows.append(
            {
                "surface_unit": surface,
                "handler_module": dispatch_module,
                "handler_function": func_name,
                "path_summary": f"decode -> {dispatch_module} -> policy/store",
                "evidence_refs": list(item.get("evidence_refs", [])),
            }
        )
    return rows


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


def _build_implementation_plan(
    planning_ir: PlanningIR,
    profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    decisions: list[dict[str, Any]],
    activations: list[dict[str, Any]],
    llm_client: FixedQwenClient | None,
    log: Any = None,
    log_artifact: Any = None,
    register_usage: Any = None,
) -> ImplementationPlan:
    module_graph = _build_module_graph(planning_ir, chosen_architecture, activations)
    canonical_types = _build_canonical_types(planning_ir.protocol_name, module_graph)
    handler_matrix = _build_handler_matrix(planning_ir, module_graph)
    file_plan, function_plan = _build_file_and_function_plan(planning_ir, module_graph, handler_matrix)
    plan = {
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
            "policy": next((item["selected_option"] for item in decisions if item["decision_type"] == "error_policy"), "mixed"),
            "error_matrix": planning_ir.error_matrix,
        },
        "test_plan": {
            "unit_tests": ["type ownership uniqueness", "handler_matrix minimum_v1 coverage", "module dependency acyclic"],
            "integration_tests": ["compiled spec bundle validates in coder"],
        },
        "traceability": {
            "decision_ids": [item["decision_id"] for item in decisions],
            "evidence_index_size": len(planning_ir.evidence_by_id),
        },
        "file_plan": file_plan,
        "function_plan": function_plan,
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
        decisions,
    )
    if llm_client is None:
        if log is not None:
            log("implementation_plan llm skipped fallback=deterministic")
        payload = None
    else:
        if log_artifact is not None:
            log_artifact("implementation_plan_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"implementation_plan llm_request_start messages={len(messages)}")
        response = _generate_with_usage(
            llm_client,
            LLMRequest(
                messages=messages,
                top_p=0.4,
                temperature=0.2,
                is_stream=True,
            ),
        )
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
    if isinstance(payload, dict):
        for key in ("protocol_description", "scope_decisions", "state_design", "resource_lifecycle", "error_strategy", "test_plan", "traceability"):
            if isinstance(payload.get(key), dict):
                plan[key] = payload[key]
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
                diagnostics.append(PlanningDiagnostic("warning", "llm_self_check_failed", str(exc), str(self.target_profile_path)))
                self._log(f"llm self-check warning error={type(exc).__name__}")
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
        artifact_paths: dict[str, Path] = {}
        artifact_paths["planning_ir"] = _write_json(self.output_dir / "planning_ir.json", asdict(planning_ir))
        self.logs.write("planning_ir", json.dumps(_to_jsonable(asdict(planning_ir)), ensure_ascii=False, indent=2), ".json")
        self._log("artifact=planning_ir written")

        self._log("stage=protocol_profile analyze start")
        profile = analyze_protocol_profile(planning_ir)
        artifact_paths["protocol_profile"] = _write_json(self.output_dir / "protocol_profile.json", profile.data)
        self.logs.write("protocol_profile", json.dumps(profile.data, ensure_ascii=False, indent=2), ".json")
        self._log("stage=protocol_profile analyze done")

        self._log("stage=expert_rules activate start")
        activations = activate_rules(load_rules(), profile)
        artifact_paths["expert_activations"] = _write_json(self.output_dir / "expert_activations.json", [asdict(item) for item in activations])
        self.logs.write("expert_activations", json.dumps([asdict(item) for item in activations], ensure_ascii=False, indent=2), ".json")
        self._log(f"stage=expert_rules activate done activations={len(activations)}")

        self._log("stage=architectures generate start")
        candidates = generate_candidate_architectures(
            planning_ir,
            profile,
            activations,
            self.llm_client,
            log=self._log,
            log_artifact=self.logs.write,
            register_usage=register_usage,
        )
        scores = score_candidate_architectures(planning_ir, profile, activations, candidates)
        candidate_payload = {
            "candidates": [asdict(item) for item in candidates],
            "scores": [asdict(item) for item in scores],
        }
        artifact_paths["candidate_architectures"] = _write_json(self.output_dir / "candidate_architectures.json", candidate_payload)
        self.logs.write("candidate_architectures", json.dumps(candidate_payload, ensure_ascii=False, indent=2), ".json")
        self._log(f"stage=architectures generate done candidates={len(candidates)}")

        chosen = next((candidate for candidate in candidates if any(score.selected and score.candidate_id == candidate.candidate_id for score in scores)), candidates[0])
        self._log(f"stage=design_decisions build start selected={chosen.candidate_id}")
        decisions = build_design_decisions(
            planning_ir,
            profile,
            chosen,
            activations,
            scores,
            self.llm_client,
            log=self._log,
            log_artifact=self.logs.write,
            register_usage=register_usage,
        )
        artifact_paths["design_decisions"] = _write_json(self.output_dir / "design_decisions.json", [asdict(item) for item in decisions])
        self.logs.write("design_decisions", json.dumps([asdict(item) for item in decisions], ensure_ascii=False, indent=2), ".json")
        self._log(f"stage=design_decisions build done decisions={len(decisions)}")

        self._log("stage=implementation_plan build start")
        implementation_plan = _build_implementation_plan(
            planning_ir,
            profile.data,
            asdict(chosen),
            [asdict(item) for item in decisions],
            [asdict(item) for item in activations],
            self.llm_client,
            log=self._log,
            log_artifact=self.logs.write,
            register_usage=register_usage,
        )
        artifact_paths["implementation_plan"] = _write_json(self.output_dir / "implementation_plan.json", implementation_plan.data)
        self.logs.write("implementation_plan", json.dumps(implementation_plan.data, ensure_ascii=False, indent=2), ".json")
        self._log("stage=implementation_plan build done")

        self._log("stage=spec_bundle compile start")
        compiled = compile_spec_bundle(implementation_plan.data, self.output_dir, target_profile)
        artifact_paths.update(compiled)
        self._log(f"stage=spec_bundle compile done artifacts={len(compiled)}")

        run_manifest = {
            "model": FIXED_MODEL,
            "facts_path": str(self.facts_path),
            "target_profile_path": str(self.target_profile_path),
            "output_dir": str(self.output_dir),
            "artifacts": {key: str(value) for key, value in artifact_paths.items()},
            "selected_architecture": chosen.candidate_id,
            "decision_count": len(decisions),
            "rule_activation_count": len(activations),
            "llm_call_usage": llm_call_usage,
            "shared_stage_token_usage": shared_stage_usage,
            "workflow_token_usage": _usage_to_dict(workflow_usage),
        }
        artifact_paths["run_manifest"] = _write_json(self.output_dir / "run_manifest.json", run_manifest)
        self.logs.write("run_manifest", json.dumps(run_manifest, ensure_ascii=False, indent=2), ".json")
        self._log("artifact=run_manifest written")
        self.logs.write(
            "token_usage_summary",
            json.dumps(
                {
                    "llm_call_usage": llm_call_usage,
                    "shared_stage_token_usage": shared_stage_usage,
                    "workflow_token_usage": _usage_to_dict(workflow_usage),
                },
                ensure_ascii=False,
                indent=2,
            ),
            ".json",
        )
        self._log(
            f"workflow token usage "
            f"(total={workflow_usage.total_tokens} in={workflow_usage.prompt_tokens} out={workflow_usage.completion_tokens})"
        )

        self._log("stage=verify start")
        verify_result = verify_output_dir(self.output_dir)
        diagnostics.extend(verify_result.diagnostics)
        success = not any(diag.level == "error" for diag in diagnostics)
        self._log(f"stage=verify done diagnostics={len(verify_result.diagnostics)}")
        self._log(f"plan done success={'yes' if success else 'no'} artifacts={len(artifact_paths)} diagnostics={len(diagnostics)}")
        return PlanningResult(success, self.output_dir, diagnostics, artifact_paths)
