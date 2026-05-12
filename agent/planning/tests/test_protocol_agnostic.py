from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from agent.common.llm_client import LLMRequest, LLMResponse, LLMUsage
from agent.coder.specs import load_spec_bundle
from agent.planning.analyzer import analyze_protocol_profile
from agent.planning.architecture import normalize_candidate_modules
from agent.planning.dependency_graph import apply_dependency_graph_to_modules, build_dependency_graph
from agent.planning.file_layout import _normalize_raw_layout, apply_file_layout_to_modules, build_file_layout
from agent.planning.ir import build_planning_ir, load_target_profile
from agent.planning.knowledge_base import load_rules
from agent.planning.models import CandidateArchitecture
from agent.planning.constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN
from agent.planning.planner import PlanningAgent
from agent.planning.rule_engine import activate_rules
from agent.planning.scorer import lint_candidate_architectures
from agent.planning.spec_blueprint import blueprint_to_jsonable, build_spec_blueprint
from agent.planning.verifier import verify_output_dir


ROOT = Path(__file__).resolve().parents[3]
PLANNING = ROOT / "agent" / "planning"
STEP_LOG_NAMES = {
    "candidate_architectures": "006_candidate_architectures.json",
    "architecture_review": "006_architecture_review.json",
    "implementation_plan": "007_implementation_plan.json",
    "spec_blueprint": "010_spec_blueprint.json",
    "design_decisions": "012_design_decisions.json",
    "run_manifest": "013_run_manifest.json",
    "token_usage_summary": "013_token_usage_summary.json",
}


def _step_log(output_dir: Path, key: str) -> Path:
    return output_dir / "_step_logs" / STEP_LOG_NAMES[key]


def _json_after(text: str, marker: str) -> Any:
    start = text.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[start:].lstrip())
    return value


def _strategy_from_prompt(text: str) -> str:
    marker = "Generation strategy: "
    if marker not in text:
        return "generic"
    return text.split(marker, 1)[1].split("\n", 1)[0].strip().rstrip(".")


def _capability_modules(
    profile: dict[str, Any],
    target_profile: dict[str, Any],
    *,
    bad_role_composition: bool = False,
    unconventional_names: bool = False,
) -> list[dict[str, Any]]:
    required = [str(item) for item in profile.get("required_capabilities", [])]
    if unconventional_names:
        buckets = {
            "event_loop": {"transport_io", "connection_lifecycle", "connection_buffering", "datagram_io", "peer_address_handling", "timer_source", "timeout_handling"},
            "packetizer": {"message_decode", "message_encode", "incremental_message_framing", "datagram_message_framing"},
            "command_coordinator": {"semantic_dispatch", "state_machine", "state_transition_validation", "protocol_error_policy", "connection_termination", "error_response_encoding"},
            "subscription_index": {"session_state_ownership", "resource_ownership", "routing_dispatch", "recovery_cleanup_policy", "canonical_type_ownership"},
            f"{target_profile.get('target_role', 'role')}_facade": {"role_composition"},
        }
    else:
        buckets = {
            "transport_runtime": {"transport_io", "connection_lifecycle", "connection_buffering", "datagram_io", "peer_address_handling", "timer_source", "timeout_handling"},
            "protocol_codec": {"message_decode", "message_encode", "incremental_message_framing", "datagram_message_framing"},
            "semantic_core": {"semantic_dispatch", "state_machine", "state_transition_validation", "protocol_error_policy", "connection_termination", "error_response_encoding"},
            "resource_state": {"session_state_ownership", "resource_ownership", "routing_dispatch", "recovery_cleanup_policy", "canonical_type_ownership"},
            ("protocol_error_handler" if bad_role_composition else f"{target_profile.get('target_role', 'role')}_composition"): {"role_composition"},
        }
    modules: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for name, owned in buckets.items():
        caps = [cap for cap in required if cap in owned]
        assigned.update(caps)
        modules.append(
            {
                "name": name,
                "role": f"Own {name.replace('_', ' ')} capabilities for the target protocol.",
                "owned_capabilities": caps,
                "evidence_refs": [],
                "dependencies": [],
            }
        )
    leftovers = [cap for cap in required if cap not in assigned]
    modules[-1]["owned_capabilities"].extend(leftovers)
    return modules


class FakePlanningLLM:
    def __init__(
        self,
        *,
        bad_role_composition: bool = False,
        force_reject_all: bool = False,
        unconventional_names: bool = False,
        generation_delay: float = 0.0,
        invalid_file_layout_attempts: int = 0,
        header_only_file_layout_attempts: int = 0,
        file_layout_edge_mode: str = "complete",
        file_layout_usage: LLMUsage | None = None,
    ) -> None:
        self.bad_role_composition = bad_role_composition
        self.force_reject_all = force_reject_all
        self.unconventional_names = unconventional_names
        self.generation_delay = generation_delay
        self.invalid_file_layout_attempts = invalid_file_layout_attempts
        self.header_only_file_layout_attempts = header_only_file_layout_attempts
        self.file_layout_edge_mode = file_layout_edge_mode
        self.file_layout_usage = file_layout_usage
        self.requests: list[LLMRequest] = []
        self.generator_requests: list[LLMRequest] = []
        self.judge_requests: list[LLMRequest] = []
        self.file_layout_requests: list[LLMRequest] = []
        self._lock = threading.Lock()
        self._active_generator_requests = 0
        self.max_concurrent_generator_requests = 0

    def self_check(self) -> dict[str, Any]:
        return {"ok": True}

    def _file_layout_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        placements: list[dict[str, Any]] = []
        module_api_file: dict[str, str] = {}
        function_file: dict[str, str] = {}
        canonical_by_module = {
            str(item.get("owner_module")): str(item.get("type_name"))
            for item in context.get("canonical_types", [])
            if isinstance(item, dict) and item.get("owner_module") and item.get("type_name")
        }
        functions_by_module = context.get("functions_by_module", {})
        functions_by_module = functions_by_module if isinstance(functions_by_module, dict) else {}
        for module in context.get("modules", []):
            if not isinstance(module, dict):
                continue
            name = str(module.get("name", ""))
            base = str(module.get("path", f"{name}/{name}"))
            directory = base.rsplit("/", 1)[0] if "/" in base else "."
            functions = [item for item in functions_by_module.get(name, []) if isinstance(item, dict)]
            api_file_id = f"{name}:api"
            module_api_file[name] = api_file_id
            api_functions = [str(item.get("name")) for item in functions if str(item.get("unit", "api")) == "api" or str(item.get("kind")) == "lifecycle"]
            public_symbols = [canonical_by_module.get(name, f"{name}_t"), *[str(item.get("name")) for item in functions if str(item.get("visibility", "public")) == "public"]]
            files.append(
                {
                    "file_id": api_file_id,
                    "module": name,
                    "path": f"{base}.c",
                    "owns_header": f"{base}.h",
                    "file_kind": "api_source",
                    "visibility": "public",
                    "role": f"Public API source for {name}",
                    "defines_functions": api_functions,
                    "declares_symbols": public_symbols,
                    "uses_types": [],
                    "evidence_refs": module.get("evidence_refs", []),
                    "decision_refs": ["dec_file_layout"],
                }
            )
            for function_name in api_functions:
                function_file[function_name] = api_file_id
                placements.append(
                    {
                        "function_name": function_name,
                        "module": name,
                        "file_id": api_file_id,
                        "visibility": "public",
                        "placement_reason": "API and lifecycle functions stay in the public source unit.",
                    }
                )
            units = sorted({str(item.get("unit", "")) for item in functions if str(item.get("unit", "")) not in {"", "api"}})
            for unit in units:
                unit_functions = [str(item.get("name")) for item in functions if str(item.get("unit", "")) == unit]
                file_id = f"{name}:{unit}"
                source_path = f"{directory}/main.c" if unit == "main" else f"{directory}/{name}_{unit}.c"
                files.append(
                    {
                        "file_id": file_id,
                        "module": name,
                        "path": source_path,
                        "owns_header": "",
                        "file_kind": f"{unit}_source",
                        "visibility": "private",
                        "role": f"{unit} implementation source for {name}",
                        "defines_functions": unit_functions,
                        "declares_symbols": [],
                        "uses_types": [],
                        "evidence_refs": module.get("evidence_refs", []),
                        "decision_refs": ["dec_file_layout"],
                    }
                )
                for function_name in unit_functions:
                    function_file[function_name] = file_id
                    placements.append(
                        {
                            "function_name": function_name,
                            "module": name,
                            "file_id": file_id,
                            "visibility": "public",
                            "placement_reason": f"{unit} capability functions stay in their implementation source unit.",
                        }
                    )
        file_edges: list[dict[str, Any]] = []

        def add_edge(edge_id: str, consumer_file: str, provider_file: str, dependency_kind: str, required_symbols: list[str]) -> None:
            if not consumer_file or not provider_file:
                return
            file_edges.append(
                {
                    "edge_id": f"file_edge:{len(file_edges)}",
                    "consumer_file": consumer_file,
                    "provider_file": provider_file,
                    "dependency_kind": dependency_kind,
                    "include_scope": "header" if "compile_time" in dependency_kind else "source",
                    "required_symbols": [item for item in required_symbols if item],
                    "source_graph_edges": [edge_id],
                    "reason": "Project dependency graph edge into C source/header relationship.",
                }
            )

        dependency_graph = context.get("dependency_graph", {})
        dependency_graph = dependency_graph if isinstance(dependency_graph, dict) else {}
        for edge in dependency_graph.get("module_edges", []):
            if isinstance(edge, dict):
                add_edge(
                    str(edge.get("edge_id", "")),
                    module_api_file.get(str(edge.get("consumer_module", "")), ""),
                    module_api_file.get(str(edge.get("provider_module", "")), ""),
                    str(edge.get("dependency_kind", "module_dependency")),
                    [],
                )
        for edge in dependency_graph.get("function_edges", []):
            if isinstance(edge, dict):
                add_edge(
                    str(edge.get("edge_id", "")),
                    function_file.get(str(edge.get("caller", "")), ""),
                    function_file.get(str(edge.get("callee", "")), "") or module_api_file.get(str(edge.get("callee_module", "")), ""),
                    str(edge.get("dependency_kind", "function_call")),
                    [str(edge.get("callee", ""))],
                )
        for edge in dependency_graph.get("data_edges", []):
            if isinstance(edge, dict):
                add_edge(
                    str(edge.get("edge_id", "")),
                    function_file.get(str(edge.get("function", "")), ""),
                    module_api_file.get(str(edge.get("provider_module", "")), ""),
                    str(edge.get("data_kind", "data_access")),
                    [str(edge.get("struct", ""))],
                )
        return {
            "schema_version": "file_layout/v1alpha1",
            "origin": "llm",
            "files": files,
            "file_edges": file_edges,
            "function_placement": placements,
            "unresolved_layout_questions": [],
        }

    def generate_with_usage(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        content = request.messages[-1]["content"]
        if "Produce JSON with key 'candidate'" in content:
            with self._lock:
                self.generator_requests.append(request)
                self._active_generator_requests += 1
                self.max_concurrent_generator_requests = max(
                    self.max_concurrent_generator_requests,
                    self._active_generator_requests,
                )
            if self.generation_delay:
                time.sleep(self.generation_delay)
            with self._lock:
                self._active_generator_requests -= 1
            profile = _json_after(content, "protocol_profile:\n")
            target_profile = _json_after(content, "target_profile:\n")
            strategy = _strategy_from_prompt(content)
            modules = _capability_modules(
                profile,
                target_profile,
                bad_role_composition=self.bad_role_composition,
                unconventional_names=self.unconventional_names,
            )
            payload = {
                "candidate": {
                    "candidate_id": f"candidate_{strategy}",
                    "title": "Capability-Oriented Architecture",
                    "summary": "Assigns required capabilities to a compact set of generic implementation boundaries.",
                    "modules": modules,
                    "thread_model": target_profile.get("runtime", ""),
                    "component_relationships": ["transport_runtime -> protocol_codec", "protocol_codec -> semantic_core", "semantic_core -> resource_state"],
                    "strengths": ["Complete capability ownership", "Compact module budget"],
                    "risks": [],
                }
            }
        elif "You are the architecture judge" in content:
            self.judge_requests.append(request)
            candidates = _json_after(content, "candidates:\n")
            scores = []
            selected = ""
            for candidate in candidates:
                misplaced_role = any(
                    "role_composition" in module.get("owned_capabilities", [])
                    and any(token in str(module.get("name", "")).lower() for token in ("error", "codec", "type"))
                    for module in candidate.get("modules", [])
                )
                verdict = "reject" if self.force_reject_all or misplaced_role else "accept"
                recommended = verdict == "accept" and not selected
                if recommended:
                    selected = candidate["candidate_id"]
                scores.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "verdict": verdict,
                        "total_score": 86 if verdict == "accept" else 20,
                        "scores": {
                            "protocol_fit": 10,
                            "role_composition_fit": 0 if misplaced_role else 10,
                            "capability_ownership_semantics": 8,
                            "module_cohesion": 8,
                            "separation_of_concerns": 8,
                            "coder_usability": 8,
                            "traceability": 8,
                            "module_count_reasonableness": 8,
                        },
                        "major_issues": [
                            {
                                "severity": "major",
                                "issue": "role_composition is misplaced on a local error/codec/type module.",
                            }
                        ] if misplaced_role else [],
                        "rationale": "Accepted by fake judge." if verdict == "accept" else "Rejected by fake judge.",
                        "recommended_selection": recommended,
                    }
                )
            payload = {
                "architecture_review": {
                    "selected_candidate_id": selected,
                    "rejection_reason": "" if selected else "No candidate accepted by fake judge.",
                    "scores": scores,
                }
            }
        elif "Produce JSON with key 'file_layout'" in content:
            self.file_layout_requests.append(request)
            if len(self.file_layout_requests) <= self.invalid_file_layout_attempts:
                payload = {"file_layout": {"schema_version": "file_layout/v1alpha1", "origin": "llm", "files": [], "file_edges": [], "function_placement": [], "unresolved_layout_questions": []}}
            else:
                context = _json_after(content, "layout_context:\n")
                layout = self._file_layout_payload(context)
                if self.file_layout_edge_mode == "empty":
                    layout["file_edges"] = []
                elif self.file_layout_edge_mode == "partial":
                    layout["file_edges"] = layout["file_edges"][:1]
                elif self.file_layout_edge_mode == "malformed":
                    layout["file_edges"] = {"not": "an array"}
                if len(self.file_layout_requests) <= self.header_only_file_layout_attempts:
                    first_file = layout["files"][0]
                    layout["files"].append(
                        {
                            **first_file,
                            "file_id": f"{first_file['file_id']}:header",
                            "path": str(first_file["owns_header"]),
                            "owns_header": "",
                            "file_kind": "header",
                            "defines_functions": [],
                        }
                    )
                payload = {"file_layout": layout}
            response_text = json.dumps(payload)
            usage = self.file_layout_usage or LLMUsage(max(1, len(content) // 4), max(1, len(response_text) // 4), max(2, (len(content) + len(response_text)) // 4))
            return LLMResponse(content=response_text, usage=usage)
        else:
            payload = {
                "protocol_description": {
                    "interaction_model": "derived_from_profile",
                    "transport_shape": "derived_from_profile",
                    "statefulness": "derived_from_profile",
                },
                "test_plan": {
                    "unit_tests": ["module budget", "capability coverage", "handler coverage"],
                    "integration_tests": ["compiled spec bundle loads"],
                },
            }
        return LLMResponse(content=json.dumps(payload), usage=LLMUsage(1, 1, 2))


class ProtocolAgnosticPlanningTests(unittest.TestCase):
    def _run_mqtt_plan(self, tmp_root: Path, llm: FakePlanningLLM | None = None):
        llm = llm or FakePlanningLLM()
        agent = PlanningAgent(
            ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
            ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
            output_dir=tmp_root / "mqtt_min",
            llm_client=llm,
        )
        return llm, agent.plan()

    def test_dependency_graph_synthesis_is_acyclic_and_applies_dependencies(self) -> None:
        target_profile, target_diags = load_target_profile(ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json")
        self.assertEqual(target_diags, [])
        planning_ir, ir_diags = build_planning_ir(ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json", target_profile)
        self.assertEqual(ir_diags, [])
        self.assertIsNotNone(planning_ir)
        assert planning_ir is not None
        profile = analyze_protocol_profile(planning_ir)
        module_graph = [
            {"name": "transport_runtime", "role": "transport", "path": "x/transport_runtime/transport_runtime", "dependencies": [], "owned_capabilities": ["transport_io", "connection_lifecycle"], "evidence_refs": []},
            {"name": "protocol_codec", "role": "codec", "path": "x/protocol_codec/protocol_codec", "dependencies": [], "owned_capabilities": ["message_decode", "message_encode"], "evidence_refs": []},
            {"name": "semantic_core", "role": "semantic", "path": "x/semantic_core/semantic_core", "dependencies": [], "owned_capabilities": ["semantic_dispatch", "state_machine"], "evidence_refs": []},
            {"name": "resource_state", "role": "resources", "path": "x/resource_state/resource_state", "dependencies": [], "owned_capabilities": ["resource_ownership"], "evidence_refs": []},
            {"name": "server_composition", "role": "role composition", "path": "x/server_composition/server_composition", "dependencies": [], "owned_capabilities": ["role_composition"], "evidence_refs": []},
        ]
        canonical_types = [
            {"type_name": f"x_{module['name']}_t", "owner_module": module["name"], "owner_file": f"{module['path']}.h", "visibility": "public"}
            for module in module_graph
        ]
        handler_matrix = [
            {
                "surface_unit": "CONNECT",
                "handler_module": "semantic_core",
                "handler_function": "x_semantic_core_handle_connect",
                "path_summary": "decode -> semantic dispatch -> resource store",
                "evidence_refs": [],
            }
        ]
        implementation_plan = {
            "module_graph": module_graph,
            "canonical_types": canonical_types,
            "handler_matrix": handler_matrix,
            "traceability": {"decision_ids": ["dec_architecture_choice"]},
        }
        graph = build_dependency_graph(
            planning_ir,
            profile.data,
            {"component_relationships": ["transport_runtime -> protocol_codec", "protocol_codec -> semantic_core", "semantic_core -> resource_state"]},
            implementation_plan,
            llm_client=None,
        )
        selected_edges = {(edge["consumer_module"], edge["provider_module"]) for edge in graph["module_edges"]}

        self.assertIn(("server_composition", "semantic_core"), selected_edges)
        self.assertIn(("semantic_core", "resource_state"), selected_edges)
        self.assertNotIn(("resource_state", "semantic_core"), selected_edges)

        updated = apply_dependency_graph_to_modules(module_graph, graph, canonical_types, handler_matrix, planning_ir.protocol_name)
        dependencies_by_module = {module["name"]: module["dependencies"] for module in updated}
        self.assertIn("semantic_core", dependencies_by_module["server_composition"])
        self.assertIn("resource_state", dependencies_by_module["semantic_core"])

    def test_file_layout_synthesis_splits_complex_modules_and_projects_dependencies(self) -> None:
        target_profile, target_diags = load_target_profile(ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json")
        self.assertEqual(target_diags, [])
        planning_ir, ir_diags = build_planning_ir(ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json", target_profile)
        self.assertEqual(ir_diags, [])
        self.assertIsNotNone(planning_ir)
        assert planning_ir is not None
        profile = analyze_protocol_profile(planning_ir)
        module_graph = [
            {"name": "codec", "role": "codec", "path": "x/codec/codec", "dependencies": [], "owned_capabilities": ["message_decode", "message_encode"], "evidence_refs": []},
            {"name": "semantic", "role": "semantic handlers", "path": "x/semantic/semantic", "dependencies": [], "owned_capabilities": ["semantic_dispatch", "protocol_error_policy"], "evidence_refs": []},
        ]
        canonical_types = [
            {"type_name": "mqtt_codec_t", "owner_module": "codec", "owner_file": "x/codec/codec.h", "visibility": "public"},
            {"type_name": "mqtt_semantic_t", "owner_module": "semantic", "owner_file": "x/semantic/semantic.h", "visibility": "public"},
        ]
        handler_matrix = [
            {"surface_unit": "CONNECT", "handler_module": "semantic", "handler_function": "mqtt_semantic_handle_connect", "path_summary": "decode -> semantic dispatch", "evidence_refs": []},
            {"surface_unit": "PUBLISH", "handler_module": "semantic", "handler_function": "mqtt_semantic_handle_publish", "path_summary": "decode -> semantic dispatch", "evidence_refs": []},
        ]
        dependency_graph = {
            "module_edges": [
                {
                    "edge_id": "module_edge:semantic:codec:compile_time_include",
                    "consumer_module": "semantic",
                    "provider_module": "codec",
                    "dependency_kind": "compile_time_include",
                    "required_capabilities": ["message_decode"],
                    "reason": "semantic public API consumes codec decoded message contracts",
                    "evidence_refs": [],
                    "decision_refs": ["dec_dependency_graph"],
                }
            ],
            "interface_contracts": [],
            "function_edges": [
                {
                    "edge_id": "function_edge:mqtt_semantic_create:mqtt_codec_create",
                    "caller": "mqtt_semantic_create",
                    "caller_module": "semantic",
                    "callee": "mqtt_codec_create",
                    "callee_module": "codec",
                    "dependency_kind": "initializes_provider",
                    "required_capabilities": ["message_decode"],
                    "reason": "semantic initializes codec dependency",
                    "evidence_refs": [],
                    "decision_refs": ["dec_dependency_graph"],
                    "source_module_edge": "module_edge:semantic:codec:compile_time_include",
                }
            ],
            "data_edges": [
                {
                    "edge_id": "data_edge:mqtt_semantic_create:codec:mqtt_codec_t",
                    "function": "mqtt_semantic_create",
                    "module": "semantic",
                    "provider_module": "codec",
                    "data_kind": "provider_public_handle",
                    "struct": "mqtt_codec_t",
                    "required_capabilities": ["message_decode"],
                    "reason": "semantic stores codec handle",
                    "evidence_refs": [],
                    "decision_refs": ["dec_dependency_graph"],
                    "source_module_edge": "module_edge:semantic:codec:compile_time_include",
                }
            ],
        }
        implementation_plan = {
            "schema_version": "implementation_plan/v2alpha3",
            "module_graph": apply_dependency_graph_to_modules(module_graph, dependency_graph, canonical_types, handler_matrix, planning_ir.protocol_name),
            "canonical_types": canonical_types,
            "handler_matrix": handler_matrix,
            "dependency_graph": dependency_graph,
            "traceability": {"decision_ids": ["dec_dependency_graph"]},
        }
        file_layout = build_file_layout(
            planning_ir,
            profile.data,
            {"component_relationships": []},
            implementation_plan,
            llm_client=None,
        )
        implementation_plan["file_layout"] = file_layout
        implementation_plan["module_graph"] = apply_file_layout_to_modules(
            list(implementation_plan["module_graph"]),
            file_layout,
            canonical_types,
            handler_matrix,
            planning_ir.protocol_name,
        )
        blueprint, _ = build_spec_blueprint(planning_ir, implementation_plan, target_profile)
        spec_blueprint = blueprint_to_jsonable(blueprint)
        files_by_id = {item["file_id"]: item for item in spec_blueprint["files"]}
        semantic_api = files_by_id["semantic:api"]

        self.assertGreater(len(file_layout["files"]), len(module_graph))
        self.assertIn("x/codec/codec.h", semantic_api["header_dependencies"])
        self.assertIn("module_edge:semantic:codec:compile_time_include", semantic_api["dependency_refs"])
        semantic_create = next(item for item in spec_blueprint["functions"] if item["name"] == "mqtt_semantic_create")
        self.assertIn("function_edge:mqtt_semantic_create:mqtt_codec_create", semantic_create["dependency_refs"])
        self.assertIn("data_edge:mqtt_semantic_create:codec:mqtt_codec_t", semantic_create["dependency_refs"])

    def test_file_layout_bool_owns_header_preserves_explicit_canonical_header_path(self) -> None:
        module_graph = [
            {"name": "mqtt_codec", "role": "codec", "path": "mqtt/mqtt_codec/mqtt_codec", "dependencies": [], "owned_capabilities": [], "evidence_refs": []}
        ]
        canonical_types = [
            {"type_name": "mqtt_mqtt_codec_t", "owner_module": "mqtt_codec", "owner_file": "mqtt/mqtt_codec/mqtt_codec.h", "visibility": "public"}
        ]
        raw_layout = {
            "files": [
                {
                    "file_id": "mqtt_codec_encode",
                    "module": "mqtt_codec",
                    "path": "mqtt/mqtt_codec/mqtt_codec_encode.c",
                    "owns_header": True,
                    "header_path": "mqtt/mqtt_codec/mqtt_codec.h",
                    "defines_functions": ["mqtt_mqtt_codec_create", "mqtt_mqtt_codec_destroy"],
                }
            ],
            "function_placement": [
                {"function_name": "mqtt_mqtt_codec_create", "module": "mqtt_codec", "file_id": "mqtt_codec_encode"},
                {"function_name": "mqtt_mqtt_codec_destroy", "module": "mqtt_codec", "file_id": "mqtt_codec_encode"},
            ],
        }

        layout, errors = _normalize_raw_layout(raw_layout, module_graph, canonical_types, [], "mqtt", ["dec_file_layout"])

        self.assertEqual(errors, [])
        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertEqual(layout["files"][0]["owns_header"], "mqtt/mqtt_codec/mqtt_codec.h")
        self.assertEqual(layout["files"][0]["header_path"], "mqtt/mqtt_codec/mqtt_codec.h")

    def test_file_layout_rejects_public_header_that_differs_from_canonical_owner_file(self) -> None:
        module_graph = [
            {"name": "mqtt_codec", "role": "codec", "path": "mqtt/mqtt_codec/mqtt_codec", "dependencies": [], "owned_capabilities": [], "evidence_refs": []}
        ]
        canonical_types = [
            {"type_name": "mqtt_mqtt_codec_t", "owner_module": "mqtt_codec", "owner_file": "mqtt/mqtt_codec/mqtt_codec.h", "visibility": "public"}
        ]
        raw_layout = {
            "files": [
                {
                    "file_id": "mqtt_codec_encode",
                    "module": "mqtt_codec",
                    "path": "mqtt/mqtt_codec/mqtt_codec_encode.c",
                    "owns_header": "mqtt/mqtt_codec/mqtt_codec_encode.h",
                    "defines_functions": ["mqtt_mqtt_codec_create", "mqtt_mqtt_codec_destroy"],
                }
            ],
            "function_placement": [
                {"function_name": "mqtt_mqtt_codec_create", "module": "mqtt_codec", "file_id": "mqtt_codec_encode"},
                {"function_name": "mqtt_mqtt_codec_destroy", "module": "mqtt_codec", "file_id": "mqtt_codec_encode"},
            ],
        }

        layout, errors = _normalize_raw_layout(raw_layout, module_graph, canonical_types, [], "mqtt", ["dec_file_layout"])

        self.assertIsNone(layout)
        self.assertTrue(any("canonical type owner_file" in error for error in errors), errors)

    def test_role_composition_role_is_normalized_to_owned_capability(self) -> None:
        modules = normalize_candidate_modules(
            [
                {
                    "name": "broker_core",
                    "role": "role-composition",
                    "owned_capabilities": ["semantic_dispatch"],
                    "evidence_refs": [],
                }
            ],
            [],
        )

        self.assertIn("role_composition", modules[0]["owned_capabilities"])
        self.assertIn("semantic_dispatch", modules[0]["owned_capabilities"])

    def test_candidate_lint_uses_broad_hard_module_range(self) -> None:
        target_profile, target_diags = load_target_profile(ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json")
        self.assertEqual(target_diags, [])
        planning_ir, ir_diags = build_planning_ir(ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json", target_profile)
        self.assertEqual(ir_diags, [])
        self.assertIsNotNone(planning_ir)
        assert planning_ir is not None
        profile = analyze_protocol_profile(planning_ir)
        activations = activate_rules(load_rules(), profile)

        def candidate_with_count(count: int) -> CandidateArchitecture:
            return CandidateArchitecture(
                candidate_id=f"candidate_{count}",
                title=f"{count} modules",
                summary="module-count lint fixture",
                modules=[
                    {
                        "name": f"module_{idx}",
                        "role": "generic module",
                        "owned_capabilities": [],
                        "evidence_refs": [],
                        "dependencies": [],
                    }
                    for idx in range(count)
                ],
                thread_model="test",
                component_relationships=[],
                strengths=[],
                risks=[],
                origin="test",
            )

        lint_results = {
            item.candidate_id: item
            for item in lint_candidate_architectures(
                planning_ir,
                profile,
                activations,
                [candidate_with_count(1), candidate_with_count(4), candidate_with_count(8), candidate_with_count(13)],
            )
        }

        self.assertFalse(lint_results["candidate_1"].module_count_ok)
        self.assertTrue(lint_results["candidate_4"].module_count_ok)
        self.assertTrue(lint_results["candidate_8"].module_count_ok)
        self.assertFalse(lint_results["candidate_13"].module_count_ok)

    def test_protocol_fixtures_use_generic_planning(self) -> None:
        import tempfile

        fixtures = [
            (ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json", ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json"),
            (PLANNING / "fixtures" / "ftp" / "protocol_facts.json", PLANNING / "fixtures" / "server_c_linux_epoll_minimum_v1.json"),
            (PLANNING / "fixtures" / "smtp" / "protocol_facts.json", PLANNING / "fixtures" / "server_c_linux_epoll_minimum_v1.json"),
            (PLANNING / "fixtures" / "coap" / "protocol_facts.json", PLANNING / "fixtures" / "server_c_linux_epoll_minimum_v1.json"),
        ]
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_root = Path(raw_tmp)
            for facts_path, target_profile_path in fixtures:
                with self.subTest(facts_path=facts_path):
                    llm = FakePlanningLLM(generation_delay=0.02)
                    agent = PlanningAgent(facts_path, target_profile_path, output_dir=tmp_root / facts_path.parent.name, llm_client=llm)
                    result = agent.plan()

                    self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
                    self.assertEqual(len(llm.generator_requests), 3)
                    self.assertGreater(llm.max_concurrent_generator_requests, 1)
                    self.assertTrue(all(request.temperature == 0.8 and request.top_p == 0.9 for request in llm.generator_requests))
                    self.assertEqual(len(llm.judge_requests), 1)
                    self.assertEqual(llm.judge_requests[0].temperature, 0.1)
                    self.assertEqual(llm.judge_requests[0].top_p, 0.2)
                    candidate_payload = json.loads(_step_log(result.output_dir, "candidate_architectures").read_text(encoding="utf-8"))
                    self.assertEqual(len(candidate_payload["candidates"]), 3)
                    self.assertEqual(len(candidate_payload["lint_results"]), 3)
                    architecture_review = json.loads(_step_log(result.output_dir, "architecture_review").read_text(encoding="utf-8"))
                    self.assertEqual(architecture_review["selected_candidate_id"], "candidate_balanced_layered")
                    implementation_plan = json.loads(_step_log(result.output_dir, "implementation_plan").read_text(encoding="utf-8"))
                    module_count = len(implementation_plan["module_graph"])
                    self.assertEqual(implementation_plan["schema_version"], "implementation_plan/v2alpha3")
                    self.assertIn("file_layout", implementation_plan)
                    self.assertGreater(len(implementation_plan["file_layout"]["files"]), module_count)
                    self.assertGreaterEqual(module_count, MODULE_BUDGET_MIN)
                    self.assertLessEqual(module_count, MODULE_BUDGET_MAX)
                    self.assertEqual(implementation_plan["validation_report"]["missing_capabilities"], [])
                    dependency_graph = implementation_plan["dependency_graph"]
                    if module_count > 1:
                        self.assertGreater(len(dependency_graph["module_edges"]), 0)
                        self.assertTrue(any(module["dependencies"] for module in implementation_plan["module_graph"]))
                    spec_blueprint = json.loads(_step_log(result.output_dir, "spec_blueprint").read_text(encoding="utf-8"))
                    source_dependency_counts = [len(item["source_dependencies"]) for item in spec_blueprint["files"]]
                    self.assertTrue(any(count > 1 for count in source_dependency_counts))
                    event_rely_counts = [
                        len(function["rely"]["STRUCT"]) + len(function["rely"]["FUNC"]) + len(function["rely"]["VAR"])
                        for function in spec_blueprint["functions"]
                        if function["function_type"] == "EVENT"
                    ]
                    self.assertTrue(any(count > 0 for count in event_rely_counts))
                    _step_log(result.output_dir, "design_decisions").unlink()
                    verify_without_decisions = verify_output_dir(result.output_dir)
                    self.assertTrue(
                        verify_without_decisions.ok,
                        "design_decisions.json should be a derived artifact, not a verifier-required planning input",
                    )
                    module_spec = next((result.output_dir / "spec_bundle").glob("*_module_spec.json"))
                    bundle = load_spec_bundle(module_spec, result.output_dir / "spec_bundle")
                    blocking_codes = {"missing_function_spec", "signature_mismatch", "header_signature_mismatch", "orphan_function_spec"}
                    self.assertFalse(
                        [diag.__dict__ for diag in bundle.diagnostics if diag.level == "error" or diag.code in blocking_codes],
                        "compiled specs must load cleanly enough for coder input",
                    )

    def test_file_layout_llm_retry_is_used_without_fallback(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm, result = self._run_mqtt_plan(
                Path(raw_tmp),
                FakePlanningLLM(invalid_file_layout_attempts=1, file_layout_usage=LLMUsage(500, 500, 1000)),
            )

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertEqual(len(llm.file_layout_requests), 2)
            implementation_plan = json.loads(_step_log(result.output_dir, "implementation_plan").read_text(encoding="utf-8"))
            validation = implementation_plan["file_layout"]["validation"]
            self.assertEqual(implementation_plan["file_layout"]["origin"], "llm")
            self.assertTrue(validation["accepted"])
            self.assertEqual(validation["attempt_count"], 2)
            self.assertNotIn("fallback_used", validation)

    def test_file_layout_derives_file_edges_when_llm_edges_are_missing_or_invalid(self) -> None:
        import tempfile

        for edge_mode in ("empty", "partial", "malformed"):
            with self.subTest(edge_mode=edge_mode):
                with tempfile.TemporaryDirectory() as raw_tmp:
                    llm, result = self._run_mqtt_plan(
                        Path(raw_tmp),
                        FakePlanningLLM(file_layout_edge_mode=edge_mode, file_layout_usage=LLMUsage(500, 500, 1000)),
                    )

                    self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
                    self.assertEqual(len(llm.file_layout_requests), 1)
                    implementation_plan = json.loads(_step_log(result.output_dir, "implementation_plan").read_text(encoding="utf-8"))
                    file_layout = implementation_plan["file_layout"]
                    validation = file_layout["validation"]
                    self.assertEqual(file_layout["origin"], "llm")
                    self.assertTrue(validation["accepted"])
                    self.assertEqual(validation["attempt_count"], 1)
                    self.assertEqual(validation["attempts"][0]["errors"], [])
                    self.assertIsInstance(file_layout["file_edges"], list)
                    self.assertGreater(len(file_layout["file_edges"]), 0)

                    dependency_graph = implementation_plan["dependency_graph"]
                    expected_graph_ids = {
                        str(edge["edge_id"])
                        for section in ("module_edges", "function_edges", "data_edges")
                        for edge in dependency_graph.get(section, [])
                        if isinstance(edge, dict) and edge.get("edge_id")
                    }
                    projected_graph_ids = {
                        str(ref)
                        for edge in file_layout["file_edges"]
                        if isinstance(edge, dict)
                        for ref in edge.get("source_graph_edges", [])
                        if str(ref).strip()
                    }
                    self.assertFalse(expected_graph_ids - projected_graph_ids)

    def test_file_layout_rejects_header_only_file_entries_then_retries(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm, result = self._run_mqtt_plan(
                Path(raw_tmp),
                FakePlanningLLM(header_only_file_layout_attempts=1, file_layout_usage=LLMUsage(500, 500, 1000)),
            )

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertEqual(len(llm.file_layout_requests), 2)
            self.assertEqual(llm.file_layout_requests[0].max_completion_tokens, 8000)
            implementation_plan = json.loads(_step_log(result.output_dir, "implementation_plan").read_text(encoding="utf-8"))
            attempts = implementation_plan["file_layout"]["validation"]["attempts"]
            self.assertIn("header-only entry", attempts[0]["errors"][0])
            for item in implementation_plan["file_layout"]["files"]:
                self.assertTrue(str(item["source_path"]).endswith(".c"))
                self.assertFalse(str(item["source_path"]).endswith(".h"))

    def test_file_layout_invalid_llm_output_blocks_planning_without_fallback(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm, result = self._run_mqtt_plan(
                Path(raw_tmp),
                FakePlanningLLM(invalid_file_layout_attempts=99, file_layout_usage=LLMUsage(500, 500, 1000)),
            )

            self.assertFalse(result.success)
            self.assertEqual(len(llm.file_layout_requests), 3)
            self.assertIn("file_layout_invalid_llm_output", [diag.code for diag in result.diagnostics])
            run_manifest = json.loads(_step_log(result.output_dir, "run_manifest").read_text(encoding="utf-8"))
            self.assertEqual(run_manifest["failure"]["stage"], "file_layout")
            self.assertEqual(run_manifest["failure"]["code"], "file_layout_invalid_llm_output")
            step_artifacts = {item["step"]: item for item in run_manifest["step_artifacts"]}
            self.assertTrue(any(artifact["name"] == "step_08_dependency_graph" for artifact in step_artifacts[8]["artifacts"]))
            self.assertTrue(any(artifact["name"] == "file_layout_failure" for artifact in step_artifacts[9]["artifacts"]))

    def test_file_layout_token_budget_blocks_planning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            _, result = self._run_mqtt_plan(
                Path(raw_tmp),
                FakePlanningLLM(file_layout_usage=LLMUsage(20_000, 11_000, 31_000)),
            )

            self.assertFalse(result.success)
            self.assertIn("file_layout_token_budget_exceeded", [diag.code for diag in result.diagnostics])
            token_summary = json.loads(_step_log(result.output_dir, "token_usage_summary").read_text(encoding="utf-8"))
            self.assertEqual(token_summary["file_layout_failure"]["code"], "file_layout_token_budget_exceeded")

    def test_file_layout_token_budget_is_per_call_not_cumulative(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            _, result = self._run_mqtt_plan(
                Path(raw_tmp),
                FakePlanningLLM(invalid_file_layout_attempts=1, file_layout_usage=LLMUsage(8_000, 8_000, 16_000)),
            )

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            root_entries = {path.name for path in result.output_dir.iterdir()}
            self.assertIn("_agent_logs", root_entries)
            self.assertIn("_step_logs", root_entries)
            self.assertIn("spec_bundle", root_entries)
            self.assertFalse([path.name for path in result.output_dir.glob("*.json")])
            run_manifest = json.loads(_step_log(result.output_dir, "run_manifest").read_text(encoding="utf-8"))
            self.assertGreater(run_manifest["shared_stage_token_usage"]["file_layout"]["total_tokens"], 30_000)
            step_artifacts = {item["step"]: item for item in run_manifest["step_artifacts"]}
            self.assertTrue(any(artifact["name"] == "step_08_dependency_graph" for artifact in step_artifacts[8]["artifacts"]))
            self.assertTrue(any(artifact["name"] == "step_09_file_layout" for artifact in step_artifacts[9]["artifacts"]))
            self.assertTrue(any(artifact["name"] == "planning_verification_report" for artifact in step_artifacts[14]["artifacts"]))

    def test_file_layout_prompt_uses_compact_context(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm, result = self._run_mqtt_plan(Path(raw_tmp))

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            content = llm.file_layout_requests[0].messages[-1]["content"]
            self.assertIn("layout_context:", content)
            self.assertNotIn("implementation_plan:", content)
            self.assertNotIn("resource_lifecycle", content)
            self.assertNotIn("allowed_graph_edge_ids", content)
            self.assertLess(len(content), 45_000)

    def test_mqtt_minimum_specs_include_coder_ready_entrypoints_and_handlers(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            _, result = self._run_mqtt_plan(Path(raw_tmp))

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            function_names = set()
            source_paths = set()
            for path in (result.output_dir / "spec_bundle").rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("KIND") == "FUNCTION_SPEC":
                    function_names.add(raw["SIGNATURE"]["NAME"])
                if raw.get("KIND") == "FILE_SPEC":
                    source_paths.add(raw["SOURCE"]["PATH"])
            expected = {
                "main",
                "mqtt_transport_runtime_run",
                "mqtt_protocol_codec_decode_packet",
                "mqtt_protocol_codec_encode_response",
                "mqtt_resource_state_add_session",
                "mqtt_resource_state_subscribe",
                "mqtt_resource_state_publish",
                "mqtt_semantic_core_handle_connect",
                "mqtt_semantic_core_handle_subscribe",
                "mqtt_semantic_core_handle_publish",
                "mqtt_semantic_core_handle_pingreq",
                "mqtt_semantic_core_handle_disconnect",
            }
            self.assertTrue(expected.issubset(function_names), sorted(expected - function_names))
            self.assertFalse({"mqtt_semantic_core_handle_connack", "mqtt_semantic_core_handle_suback", "mqtt_semantic_core_handle_pingresp"} & function_names)
            self.assertTrue(any(path.endswith("/main.c") for path in source_paths))

    def test_unconventional_but_valid_module_names_do_not_block_planning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm = FakePlanningLLM(unconventional_names=True)
            agent = PlanningAgent(
                ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
                ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
                output_dir=Path(raw_tmp) / "unconventional_names",
                llm_client=llm,
            )
            result = agent.plan()

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            self.assertNotIn("ungrounded_module_name", [diag.code for diag in result.diagnostics])
            implementation_plan = json.loads(_step_log(result.output_dir, "implementation_plan").read_text(encoding="utf-8"))
            module_names = {module["name"] for module in implementation_plan["module_graph"]}
            self.assertIn("event_loop", module_names)
            self.assertIn("packetizer", module_names)
            candidate_payload = json.loads(_step_log(result.output_dir, "candidate_architectures").read_text(encoding="utf-8"))
            self.assertTrue(
                any(result["ungrounded_module_name_tokens"] for result in candidate_payload["lint_results"]),
                "candidate lint should keep ungrounded module names as nonblocking telemetry",
            )

    def test_judge_rejects_misplaced_role_composition(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm = FakePlanningLLM(bad_role_composition=True)
            agent = PlanningAgent(
                ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
                ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
                output_dir=Path(raw_tmp) / "misplaced_role",
                llm_client=llm,
            )
            result = agent.plan()

            self.assertFalse(result.success)
            self.assertIn("architecture_judge_rejected_all", [diag.code for diag in result.diagnostics])

    def test_judge_reject_all_stops_planning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmp:
            llm = FakePlanningLLM(force_reject_all=True)
            agent = PlanningAgent(
                ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
                ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
                output_dir=Path(raw_tmp) / "reject_all",
                llm_client=llm,
            )
            result = agent.plan()

            self.assertFalse(result.success)
            self.assertIn("architecture_judge_rejected_all", [diag.code for diag in result.diagnostics])

    def test_planning_core_has_no_protocol_name_branches(self) -> None:
        blocked_terms = ("m" + "qtt", "f" + "tp", "s" + "mtp", "c" + "oap")
        offenders = []
        for path in PLANNING.glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            hits = [term for term in blocked_terms if term in text]
            if hits:
                offenders.append((path.name, hits))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
