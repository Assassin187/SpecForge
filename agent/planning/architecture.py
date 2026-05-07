from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .models import CandidateArchitecture, ExpertActivation, PlanningIR, ProtocolProfile
from .prompts import build_architecture_prompt


def _normalize_module_entry(module: Any) -> dict[str, str] | None:
    if isinstance(module, dict):
        name = str(module.get("name") or "").strip()
        if not name:
            return None
        role = str(module.get("role") or f"Own responsibilities for {name}").strip()
        return {"name": name, "role": role}
    if isinstance(module, str):
        name = module.strip()
        if not name:
            return None
        return {"name": name, "role": f"Own responsibilities for {name}"}
    return None


def normalize_candidate_modules(modules: Any, fallback: list[dict[str, Any]]) -> list[dict[str, str]]:
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
    common_modules = [
        {"name": "transport_runtime", "role": "Own connection lifecycle, event loop, socket or transport adapter"},
        {"name": "protocol_codec", "role": "Own framing, decode, encode, and shared protocol data types"},
        {"name": "handler_dispatch", "role": "Map decoded surface units to handlers and shared policies"},
    ]
    if any("router" in component for act in activations for component in act.required_components):
        common_modules.append({"name": "router", "role": "Own routing/dispatch decisions over protocol messages and resource keys"})
    if any("store" in component for act in activations for component in act.required_components):
        common_modules.append({"name": "state_store", "role": "Own resource/session state and lifecycle persistence"})
    common_modules.append({"name": f"{protocol_slug}_app", "role": f"Compose modules for target role {planning_ir.target_profile.target_role}"})

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


def _generate_with_usage(llm_client: FixedQwenClient, messages: list[dict[str, str]]) -> LLMResponse:
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    request = LLMRequest(
        messages=messages,
        top_p=0.4,
        temperature=0.2,
        is_stream=True,
    )
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def generate_candidate_architectures(
    planning_ir: PlanningIR,
    profile: ProtocolProfile,
    activations: list[ExpertActivation],
    llm_client: FixedQwenClient | None,
    log: Callable[[str], None] | None = None,
    log_artifact: Callable[[str, str, str], None] | None = None,
    register_usage: Callable[[str, str, LLMUsage], None] | None = None,
) -> list[CandidateArchitecture]:
    heuristic = _heuristic_candidates(planning_ir, profile, activations)
    if llm_client is None:
        if log is not None:
            log(f"architecture llm skipped fallback=heuristic candidates={len(heuristic)}")
        return heuristic
    try:
        messages = build_architecture_prompt(profile.data, planning_ir.target_profile.raw, [item.__dict__ for item in activations])
        if log_artifact is not None:
            log_artifact("architecture_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"architecture llm_request_start messages={len(messages)}")
        response = _generate_with_usage(llm_client, messages)
        if register_usage is not None:
            register_usage("candidate_architectures", "generate", response.usage)
        if log is not None:
            log(
                f"architecture llm_response_done chars={len(response.content)} "
                f"(tokens={response.usage.total_tokens} in={response.usage.prompt_tokens} out={response.usage.completion_tokens})"
            )
        if log_artifact is not None:
            log_artifact("architecture_response", response.content, ".txt")
        payload = _load_json_payload(response.content)
        items = payload.get("candidates", [])
        candidates: list[CandidateArchitecture] = []
        for idx, item in enumerate(items[:3]):
            if not isinstance(item, dict):
                continue
            candidates.append(
                CandidateArchitecture(
                    candidate_id=str(item.get("candidate_id") or f"candidate_llm_{idx+1}"),
                    title=str(item.get("title") or heuristic[idx].title),
                    summary=str(item.get("summary") or heuristic[idx].summary),
                    modules=normalize_candidate_modules(item.get("modules"), heuristic[idx].modules),
                    thread_model=str(item.get("thread_model") or planning_ir.target_profile.runtime),
                    component_relationships=[str(v) for v in item.get("component_relationships", [])],
                    strengths=[str(v) for v in item.get("strengths", [])],
                    risks=[str(v) for v in item.get("risks", [])],
                    origin="llm",
                )
            )
        selected = candidates or heuristic
        if log is not None:
            log(f"architecture llm_parse_done candidates={len(selected)} source={'llm' if candidates else 'heuristic_fallback'}")
        return selected
    except Exception:
        if log is not None:
            log("architecture llm_failed fallback=heuristic")
        return heuristic
