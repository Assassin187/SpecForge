from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .constraints import MODULE_BUDGET_MAX, MODULE_BUDGET_MIN
from .models import CandidateArchitecture, ExpertActivation, PlanningIR, ProtocolProfile
from .prompts import build_architecture_prompt


GENERATION_STRATEGIES = [
    "balanced_layered",
    "compact_minimum_scope",
    "separation_first",
]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _inferred_capabilities(name: str, role: str) -> list[str]:
    text = f"{name} {role}".lower().replace("_", "-")
    capabilities = []
    if "role-composition" in text or "target-role composition" in text or text.endswith("-composition"):
        capabilities.append("role_composition")
    return capabilities


def _normalize_module_entry(module: Any) -> dict[str, Any] | None:
    if isinstance(module, dict):
        name = str(module.get("name") or "").strip()
        if not name:
            return None
        role = str(module.get("role") or f"Own responsibilities for {name}").strip()
        owned_capabilities = _strings(module.get("owned_capabilities"))
        for capability in _inferred_capabilities(name, role):
            if capability not in owned_capabilities:
                owned_capabilities.append(capability)
        return {
            "name": name,
            "role": role,
            "dependencies": _strings(module.get("dependencies")),
            "owned_capabilities": owned_capabilities,
            "evidence_refs": _strings(module.get("evidence_refs")),
        }
    if isinstance(module, str):
        name = module.strip()
        if not name:
            return None
        return {"name": name, "role": f"Own responsibilities for {name}", "dependencies": [], "owned_capabilities": [], "evidence_refs": []}
    return None


def normalize_candidate_modules(modules: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_modules = modules if isinstance(modules, list) and modules else fallback
    normalized = []
    seen = set()
    for module in raw_modules:
        item = _normalize_module_entry(module)
        if item is None or item["name"] in seen:
            continue
        seen.add(item["name"])
        normalized.append(item)
    if normalized:
        return normalized
    return [dict(item) for item in fallback]


def _heuristic_candidates(planning_ir: PlanningIR, profile: ProtocolProfile, activations: list[ExpertActivation]) -> list[CandidateArchitecture]:
    protocol_slug = planning_ir.protocol_name.lower().replace(" ", "_")
    required_surface = profile.data.get("required_surface_units", []) or []
    required_capabilities = [component for act in activations for component in act.required_capabilities]
    common_modules = [
        {
            "name": "transport_runtime",
            "role": "Own transport I/O, connection or datagram lifecycle, buffering, and runtime integration",
            "owned_capabilities": [cap for cap in required_capabilities if cap in {"transport_io", "connection_lifecycle", "connection_buffering", "datagram_io", "peer_address_handling"}],
            "evidence_refs": [],
        },
        {
            "name": "protocol_codec",
            "role": "Own message framing, decode, encode, and shared protocol data types",
            "owned_capabilities": [cap for cap in required_capabilities if "message" in cap],
            "evidence_refs": [],
        },
        {
            "name": "semantic_core",
            "role": "Own semantic dispatch, state transition policy, and role-specific command handling",
            "owned_capabilities": [cap for cap in required_capabilities if cap in {"semantic_dispatch", "state_machine", "state_transition_validation", "protocol_error_policy", "connection_termination"}],
            "evidence_refs": [],
        },
    ]
    common_modules.append(
        {
            "name": "resource_store",
            "role": "Own session/resource state, lookup, lifecycle, and cleanup policy",
            "owned_capabilities": [cap for cap in required_capabilities if cap in {"session_state_ownership", "resource_ownership", "routing_dispatch", "recovery_cleanup_policy", "canonical_type_ownership"}],
            "evidence_refs": [],
        }
    )
    common_modules.append(
        {
            "name": f"{protocol_slug}_{planning_ir.target_profile.target_role.lower()}_app",
            "role": f"Compose modules for target role {planning_ir.target_profile.target_role}",
            "owned_capabilities": ["role_composition"],
            "evidence_refs": [],
        }
    )

    candidates = [
        CandidateArchitecture(
            candidate_id="candidate_layered",
            title="Layered Runtime + Codec + Handlers",
            summary="Prioritizes clean separation between transport, codec, handler dispatch, and state ownership.",
            modules=common_modules,
            thread_model=str(planning_ir.target_profile.runtime),
            component_relationships=[
                "transport_runtime -> protocol_codec",
                "protocol_codec -> handler_dispatch",
                "handler_dispatch -> state_store/router",
            ],
            strengths=["Best coder compatibility", "Deterministic ownership boundaries"],
            risks=["Can introduce additional indirection"],
            origin="heuristic",
        ),
        CandidateArchitecture(
            candidate_id="candidate_pipeline",
            title="Pipeline-Centric Command Processing",
            summary="Uses a decode -> validate -> execute pipeline with explicit policy stages.",
            modules=common_modules,
            thread_model=str(planning_ir.target_profile.runtime),
            component_relationships=[
                "transport_runtime -> protocol_codec",
                "protocol_codec -> validation_stage",
                "validation_stage -> handler_dispatch",
            ],
            strengths=["Strong malformed-input containment", "Clear error policy insertion points"],
            risks=["May spread protocol semantics across more stages"],
            origin="heuristic",
        ),
        CandidateArchitecture(
            candidate_id="candidate_state_first",
            title="State-First Session Core",
            summary=f"Centers execution around state transitions and minimum scope surfaces: {', '.join(required_surface[:6]) or 'none'}",
            modules=common_modules,
            thread_model=str(planning_ir.target_profile.runtime),
            component_relationships=[
                "transport_runtime -> protocol_codec",
                "protocol_codec -> session_core",
                "session_core -> router/state_store",
            ],
            strengths=["Fits stateful protocols", "Keeps lifecycle reasoning near handlers"],
            risks=["Can over-couple state and surface handling"],
            origin="heuristic",
        ),
    ]
    return candidates


def _load_json_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


def _generate_with_usage(
    llm_client: FixedQwenClient,
    messages: list[dict[str, str]],
    *,
    top_p: float,
    temperature: float,
) -> LLMResponse:
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    request = LLMRequest(
        messages=messages,
        top_p=top_p,
        temperature=temperature,
        is_stream=True,
    )
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def _candidate_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    item = payload.get("candidate")
    if isinstance(item, dict):
        return item
    items = payload.get("candidates")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    if isinstance(payload.get("modules"), list):
        return payload
    return None


def generate_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    llm_client: FixedQwenClient | None,
    log: Callable[[str], None] | None = None,
    log_artifact: Callable[[str, str, str], None] | None = None,
    register_usage: Callable[[str, str, LLMUsage], None] | None = None,
) -> list[CandidateArchitecture]:
    if llm_client is None:
        if log is not None:
            log("architecture llm_required_missing fallback=disabled")
        return []
    candidates: list[CandidateArchitecture] = []
    seen_ids: set[str] = set()
    requests: list[tuple[int, str, list[dict[str, str]]]] = []
    for round_index, strategy in enumerate(GENERATION_STRATEGIES, start=1):
        if log is not None:
            log(f"architecture generator_round_start round={round_index} strategy={strategy}")
        messages = build_architecture_prompt(
            profile.data,
            planning_ir.target_profile.raw,
            [item.__dict__ for item in activations],
            strategy,
            round_index,
        )
        if log_artifact is not None:
            log_artifact(f"architecture_generator_{round_index}_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        requests.append((round_index, strategy, messages))

    responses: dict[int, LLMResponse] = {}
    failures: dict[int, tuple[str, str]] = {}

    def run_request(round_index: int, strategy: str, messages: list[dict[str, str]]) -> tuple[int, str, LLMResponse]:
        if log is not None:
            log(f"architecture generator_request_start round={round_index} messages={len(messages)}")
        return round_index, strategy, _generate_with_usage(llm_client, messages, top_p=0.9, temperature=0.8)

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        future_map = {
            executor.submit(run_request, round_index, strategy, messages): (round_index, strategy)
            for round_index, strategy, messages in requests
        }
        for future in as_completed(future_map):
            round_index, strategy = future_map[future]
            try:
                done_round, _, response = future.result()
                responses[done_round] = response
            except Exception as exc:
                failures[round_index] = (strategy, type(exc).__name__)

    for round_index, strategy, _messages in requests:
        if round_index in failures:
            failed_strategy, error_name = failures[round_index]
            if log is not None:
                log(f"architecture generator_round_failed round={round_index} strategy={failed_strategy} error={error_name}")
            continue
        response = responses.get(round_index)
        if response is None:
            if log is not None:
                log(f"architecture generator_round_failed round={round_index} strategy={strategy} error=missing_response")
            continue
        try:
            if register_usage is not None:
                register_usage("candidate_architectures", f"generate_round_{round_index}", response.usage)
            if log is not None:
                log(
                    f"architecture generator_response_done round={round_index} chars={len(response.content)} "
                    f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
                )
            if log_artifact is not None:
                log_artifact(f"architecture_generator_{round_index}_response", response.content, ".txt")
            payload = _load_json_payload(response.content)
            item = _candidate_payload(payload)
            if not isinstance(item, dict):
                if log is not None:
                    log(f"architecture generator_parse_failed round={round_index} reason=missing_candidate")
                continue
            modules = normalize_candidate_modules(item.get("modules"), [])
            candidate_id = str(item.get("candidate_id") or f"candidate_{strategy}").strip() or f"candidate_{strategy}"
            if candidate_id in seen_ids:
                candidate_id = f"{candidate_id}_{round_index}"
            seen_ids.add(candidate_id)
            candidates.append(
                CandidateArchitecture(
                    candidate_id=candidate_id,
                    title=str(item.get("title") or f"{strategy} architecture"),
                    summary=str(item.get("summary") or "Generated candidate architecture."),
                    modules=modules,
                    thread_model=str(item.get("thread_model") or planning_ir.target_profile.runtime),
                    component_relationships=[str(v) for v in item.get("component_relationships", [])],
                    strengths=[str(v) for v in item.get("strengths", [])],
                    risks=[str(v) for v in item.get("risks", [])],
                    origin="generator_llm",
                    generation_round=round_index,
                    generation_strategy=strategy,
                )
            )
        except Exception as exc:
            if log is not None:
                log(f"architecture generator_round_failed round={round_index} strategy={strategy} error={type(exc).__name__}")
    if log is not None:
        log(f"architecture generators_done candidates={len(candidates)} source=generator_llm")
    return candidates
