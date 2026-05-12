from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .models import PlanningIR
from .prompts import build_dependency_graph_prompt


DEPENDENCY_SCHEMA = "dependency_graph/v1alpha1"


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _decision_refs_from_plan(implementation_plan: dict[str, Any]) -> list[str]:
    traceability = implementation_plan.get("traceability", {})
    if not isinstance(traceability, dict):
        return []
    raw_refs = traceability.get("decision_ids", [])
    if not isinstance(raw_refs, list):
        return []
    return [str(item).strip() for item in raw_refs if str(item).strip()]


def _module_by_name(module_graph: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("name")): item for item in module_graph if isinstance(item, dict) and item.get("name")}


def _module_names(module_graph: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("name")) for item in module_graph if isinstance(item, dict) and item.get("name")]


def _capability_owner(module_graph: list[dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for module in module_graph:
        name = str(module.get("name", ""))
        for capability in _strings(module.get("owned_capabilities")):
            owners.setdefault(capability, name)
    return owners


def _canonical_type_by_module(canonical_types: list[dict[str, Any]], protocol_slug: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in canonical_types:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_module", "")).strip()
        type_name = str(item.get("type_name", "")).strip()
        if owner and type_name:
            result[owner] = type_name
    return result or {module: f"{protocol_slug}_{module}_t" for module in result}


def _canonical_file_by_module(canonical_types: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in canonical_types:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_module", "")).strip()
        owner_file = str(item.get("owner_file", "")).strip()
        if owner and owner_file:
            result[owner] = owner_file
    return result


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


def _load_json_payload(content: str) -> dict[str, Any] | None:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_graph_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("dependency_graph"), dict):
        return dict(payload["dependency_graph"])
    plan = payload.get("implementation_plan")
    if isinstance(plan, dict) and isinstance(plan.get("dependency_graph"), dict):
        return dict(plan["dependency_graph"])
    return {}


def _resolve_module(raw: Any, aliases: dict[str, str]) -> str:
    text = str(raw).strip()
    if text in aliases:
        return aliases[text]
    return aliases.get(_safe_slug(text), "")


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("consumer_module", "")),
        str(edge.get("provider_module", "")),
        str(edge.get("dependency_kind", "")),
    )


def _edge_id(prefix: str, *parts: Any) -> str:
    return ":".join([prefix, *[_safe_slug(str(part)) for part in parts if str(part).strip()]])


def _would_create_cycle(edges: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in [*edges, candidate]:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if consumer and provider:
            graph[consumer].append(provider)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _merge_edge(existing: dict[str, Any], new_edge: dict[str, Any]) -> None:
    for key in ("required_capabilities", "evidence_refs", "decision_refs", "source_steps"):
        values = [*existing.get(key, []), *new_edge.get(key, [])]
        existing[key] = sorted({str(item) for item in values if str(item).strip()})
    reasons = [str(existing.get("reason", "")).strip(), str(new_edge.get("reason", "")).strip()]
    existing["reason"] = "; ".join(dict.fromkeys(reason for reason in reasons if reason))
    existing["priority"] = max(int(existing.get("priority", 0)), int(new_edge.get("priority", 0)))


def _add_candidate_edge(
    edges: list[dict[str, Any]],
    *,
    consumer: str,
    provider: str,
    dependency_kind: str,
    capabilities: list[str],
    reason: str,
    evidence_refs: list[str],
    decision_refs: list[str],
    source_step: str,
    priority: int,
) -> None:
    if not consumer or not provider or consumer == provider:
        return
    edge = {
        "consumer_module": consumer,
        "provider_module": provider,
        "dependency_kind": dependency_kind,
        "required_capabilities": sorted({cap for cap in capabilities if cap}),
        "reason": reason,
        "evidence_refs": sorted({ref for ref in evidence_refs if ref}),
        "decision_refs": sorted({ref for ref in decision_refs if ref}),
        "source_steps": [source_step],
        "priority": priority,
    }
    for existing in edges:
        if _edge_key(existing) == _edge_key(edge):
            _merge_edge(existing, edge)
            return
    edges.append(edge)


def _relationship_edges(
    chosen_architecture: dict[str, Any],
    module_graph: list[dict[str, Any]],
    decision_refs: list[str],
) -> list[dict[str, Any]]:
    aliases = {name: name for name in _module_names(module_graph)}
    aliases.update({_safe_slug(name): name for name in _module_names(module_graph)})
    edges: list[dict[str, Any]] = []
    for text in _strings(chosen_architecture.get("component_relationships")):
        if "->" not in text:
            continue
        parts = [part.strip() for part in text.split("->") if part.strip()]
        for left, right in zip(parts, parts[1:]):
            left_module = _resolve_module(left, aliases)
            right_module = _resolve_module(right, aliases)
            if left_module and right_module:
                _add_candidate_edge(
                    edges,
                    consumer=right_module,
                    provider=left_module,
                    dependency_kind="architecture_dataflow",
                    capabilities=[],
                    reason=f"Selected architecture relationship: {text}",
                    evidence_refs=[],
                    decision_refs=decision_refs,
                    source_step="selected_architecture.component_relationships",
                    priority=10,
                )
    return edges


ROLE_COMPOSITION_CAPABILITIES = {
    "transport_io",
    "connection_lifecycle",
    "connection_buffering",
    "message_decode",
    "message_encode",
    "incremental_message_framing",
    "datagram_io",
    "datagram_message_framing",
    "peer_address_handling",
    "state_machine",
    "state_transition_validation",
    "session_state_ownership",
    "recovery_cleanup_policy",
    "routing_dispatch",
    "resource_ownership",
    "protocol_error_policy",
    "connection_termination",
    "error_response_encoding",
    "timer_source",
    "timeout_handling",
    "semantic_dispatch",
}

ROUTING_CONSUMER_CAPABILITIES = {
    "message_decode",
    "message_encode",
    "resource_ownership",
    "session_state_ownership",
    "state_machine",
    "state_transition_validation",
}

STATE_CONSUMER_CAPABILITIES = {
    "resource_ownership",
}

ERROR_CONSUMER_CAPABILITIES = {
    "connection_lifecycle",
    "transport_io",
}

KEYWORD_CAPABILITY_HINTS = {
    "decode": ["message_decode"],
    "encode": ["message_encode"],
    "route": ["routing_dispatch"],
    "routing": ["routing_dispatch"],
    "store": ["resource_ownership"],
    "resource": ["resource_ownership"],
    "session": ["session_state_ownership", "state_machine"],
    "state": ["state_machine", "state_transition_validation"],
    "error": ["protocol_error_policy"],
    "close": ["connection_termination", "connection_lifecycle"],
    "terminate": ["connection_termination"],
    "timer": ["timer_source"],
    "timeout": ["timeout_handling"],
    "transport": ["transport_io"],
    "connection": ["connection_lifecycle"],
}


def _capability_edges(
    module_graph: list[dict[str, Any]],
    profile: dict[str, Any],
    handler_matrix: list[dict[str, Any]],
    decision_refs: list[str],
) -> list[dict[str, Any]]:
    del profile
    edges: list[dict[str, Any]] = []
    owners = _capability_owner(module_graph)
    modules = _module_by_name(module_graph)
    for module in module_graph:
        name = str(module.get("name", ""))
        owned = set(_strings(module.get("owned_capabilities")))
        consumer_caps: set[str] = set()
        dependency_kind = "capability_dependency"
        priority = 40
        if "role_composition" in owned:
            consumer_caps.update(ROLE_COMPOSITION_CAPABILITIES)
            dependency_kind = "role_composition_orchestration"
            priority = 80
        if "semantic_dispatch" in owned or "routing_dispatch" in owned:
            consumer_caps.update(ROUTING_CONSUMER_CAPABILITIES)
            dependency_kind = "semantic_or_routing_dispatch"
            priority = max(priority, 70)
        if owned.intersection({"state_machine", "session_state_ownership", "recovery_cleanup_policy"}):
            consumer_caps.update(STATE_CONSUMER_CAPABILITIES)
            priority = max(priority, 65)
        if owned.intersection({"protocol_error_policy", "connection_termination", "error_response_encoding"}):
            consumer_caps.update(ERROR_CONSUMER_CAPABILITIES)
            priority = max(priority, 60)
        for capability in sorted(consumer_caps):
            provider = owners.get(capability, "")
            if not provider or provider == name or provider not in modules:
                continue
            provider_refs = _strings(modules[provider].get("evidence_refs"))
            _add_candidate_edge(
                edges,
                consumer=name,
                provider=provider,
                dependency_kind=dependency_kind,
                capabilities=[capability],
                reason=f"Module '{name}' consumes capability '{capability}' owned by '{provider}'.",
                evidence_refs=provider_refs,
                decision_refs=decision_refs,
                source_step="capability_ownership",
                priority=priority,
            )
    for row in handler_matrix:
        if not isinstance(row, dict):
            continue
        consumer = str(row.get("handler_module", "")).strip()
        action_text = " ".join(str(row.get(key, "")) for key in ("path_summary", "surface_unit")).lower()
        for keyword, capabilities in KEYWORD_CAPABILITY_HINTS.items():
            if keyword not in action_text:
                continue
            for capability in capabilities:
                provider = owners.get(capability, "")
                if not provider or provider == consumer or provider not in modules:
                    continue
                _add_candidate_edge(
                    edges,
                    consumer=consumer,
                    provider=provider,
                    dependency_kind="handler_action_dependency",
                    capabilities=[capability],
                    reason=f"Handler path references '{keyword}', requiring capability '{capability}'.",
                    evidence_refs=_strings(row.get("evidence_refs")),
                    decision_refs=decision_refs,
                    source_step="handler_matrix",
                    priority=75,
                )
    return edges


def _normalize_llm_edges(raw_graph: dict[str, Any], module_graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = _module_names(module_graph)
    aliases = {name: name for name in names}
    aliases.update({_safe_slug(name): name for name in names})
    edges: list[dict[str, Any]] = []
    for item in raw_graph.get("module_edges", []):
        if not isinstance(item, dict):
            continue
        consumer = _resolve_module(item.get("consumer_module"), aliases)
        provider = _resolve_module(item.get("provider_module"), aliases)
        _add_candidate_edge(
            edges,
            consumer=consumer,
            provider=provider,
            dependency_kind=str(item.get("dependency_kind", "llm_suggested_dependency")).strip() or "llm_suggested_dependency",
            capabilities=_strings(item.get("required_capabilities")),
            reason=str(item.get("reason", "LLM-suggested dependency")).strip(),
            evidence_refs=_strings(item.get("evidence_refs")),
            decision_refs=_strings(item.get("decision_refs")),
            source_step="dependency_graph_llm",
            priority=50,
        )
    return edges


def _select_acyclic_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    ordered = sorted(
        edges,
        key=lambda edge: (
            -int(edge.get("priority", 0)),
            str(edge.get("consumer_module", "")),
            str(edge.get("provider_module", "")),
            str(edge.get("dependency_kind", "")),
        ),
    )
    for edge in ordered:
        key = _edge_key(edge)
        if key in seen:
            continue
        if _would_create_cycle(selected, edge):
            continue
        seen.add(key)
        selected.append(edge)
    return sorted(selected, key=lambda edge: (str(edge.get("consumer_module", "")), str(edge.get("provider_module", "")), str(edge.get("dependency_kind", ""))))


def _public_symbol(protocol_slug: str, module_name: str, suffix: str) -> str:
    return f"{protocol_slug}_{module_name}_{suffix}"


def _interface_contracts(
    module_edges: list[dict[str, Any]],
    protocol_slug: str,
    canonical_by_module: dict[str, str],
    owner_file_by_module: dict[str, str],
) -> list[dict[str, Any]]:
    consumers_by_provider: dict[str, set[str]] = defaultdict(set)
    caps_by_provider: dict[str, set[str]] = defaultdict(set)
    for edge in module_edges:
        provider = str(edge.get("provider_module", ""))
        consumer = str(edge.get("consumer_module", ""))
        if provider and consumer:
            consumers_by_provider[provider].add(consumer)
            caps_by_provider[provider].update(str(cap) for cap in edge.get("required_capabilities", []) if str(cap).strip())
    contracts = []
    for provider in sorted(consumers_by_provider):
        contracts.append(
            {
                "contract_id": _edge_id("interface_contract", provider),
                "provider_module": provider,
                "consumer_modules": sorted(consumers_by_provider[provider]),
                "provided_capabilities": sorted(caps_by_provider[provider]),
                "symbols": [_public_symbol(protocol_slug, provider, "create"), _public_symbol(protocol_slug, provider, "destroy")],
                "public_type": canonical_by_module.get(provider, f"{protocol_slug}_{provider}_t"),
                "public_header": owner_file_by_module.get(provider, ""),
                "visibility": "public",
            }
        )
    return contracts


def _function_edges(module_edges: list[dict[str, Any]], protocol_slug: str) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in module_edges:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if not consumer or not provider:
            continue
        for suffix, kind in (("create", "initializes_provider"), ("destroy", "releases_provider")):
            edges.append(
                {
                    "edge_id": _edge_id("function_edge", consumer, _public_symbol(protocol_slug, consumer, suffix), provider, kind),
                    "caller": _public_symbol(protocol_slug, consumer, suffix),
                    "caller_module": consumer,
                    "callee": _public_symbol(protocol_slug, provider, suffix),
                    "callee_module": provider,
                    "dependency_kind": kind,
                    "required_capabilities": list(edge.get("required_capabilities", [])),
                    "reason": str(edge.get("reason", "")),
                    "evidence_refs": list(edge.get("evidence_refs", [])),
                    "decision_refs": list(edge.get("decision_refs", [])),
                    "source_module_edge": str(edge.get("edge_id", "")),
                }
            )
    return edges


def _data_edges(
    module_edges: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    protocol_slug: str,
    canonical_by_module: dict[str, str],
) -> list[dict[str, Any]]:
    rows_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in handler_matrix:
        if isinstance(row, dict) and row.get("handler_module") and row.get("handler_function"):
            rows_by_module[str(row["handler_module"])].append(row)

    edges: list[dict[str, Any]] = []
    for edge in module_edges:
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        public_type = canonical_by_module.get(provider, f"{protocol_slug}_{provider}_t")
        if not consumer or not provider:
            continue
        target_functions = [_public_symbol(protocol_slug, consumer, "create"), _public_symbol(protocol_slug, consumer, "destroy")]
        target_functions.extend(str(row["handler_function"]) for row in rows_by_module.get(consumer, []))
        for function_name in target_functions:
            edges.append(
                {
                    "edge_id": _edge_id("data_edge", function_name, provider, public_type),
                    "function": function_name,
                    "module": consumer,
                    "provider_module": provider,
                    "data_kind": "provider_public_handle",
                    "struct": public_type,
                    "required_capabilities": list(edge.get("required_capabilities", [])),
                    "reason": str(edge.get("reason", "")),
                    "evidence_refs": list(edge.get("evidence_refs", [])),
                    "decision_refs": list(edge.get("decision_refs", [])),
                    "source_module_edge": str(edge.get("edge_id", "")),
                }
            )
    return edges


def _topological_generation_order(module_graph: list[dict[str, Any]]) -> list[str]:
    graph = {str(item.get("name")): [str(dep) for dep in item.get("dependencies", [])] for item in module_graph}
    order: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str) -> None:
        if node in visited or node in visiting:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)
        order.append(node)

    for module in module_graph:
        visit(str(module.get("name", "")))
    return [name for name in order if name]


def apply_dependency_graph_to_modules(
    module_graph: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    canonical_types: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    protocol_name: str,
) -> list[dict[str, Any]]:
    protocol_slug = _safe_slug(protocol_name)
    type_by_module = _canonical_type_by_module(canonical_types, protocol_slug)
    deps_by_consumer: dict[str, set[str]] = defaultdict(set)
    for edge in dependency_graph.get("module_edges", []):
        if not isinstance(edge, dict):
            continue
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if consumer and provider and consumer != provider:
            deps_by_consumer[consumer].add(provider)
    handlers_by_module: dict[str, list[str]] = defaultdict(list)
    for row in handler_matrix:
        if isinstance(row, dict) and row.get("handler_module") and row.get("handler_function"):
            handlers_by_module[str(row["handler_module"])].append(str(row["handler_function"]))

    updated: list[dict[str, Any]] = []
    for module in module_graph:
        name = str(module.get("name", ""))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        artifacts = [
            {"KIND": "PUBLIC_HEADER", "PATH": f"{path}.h"},
            {"KIND": "SOURCE", "PATH": f"{path}.c"},
            {"KIND": "PUBLIC_TYPE", "NAME": type_by_module.get(name, f"{protocol_slug}_{name}_t")},
            {"KIND": "PUBLIC_FUNCTION", "NAME": _public_symbol(protocol_slug, name, "create")},
            {"KIND": "PUBLIC_FUNCTION", "NAME": _public_symbol(protocol_slug, name, "destroy")},
        ]
        artifacts.extend({"KIND": "PUBLIC_FUNCTION", "NAME": function_name} for function_name in sorted(handlers_by_module.get(name, [])))
        item = dict(module)
        item["dependencies"] = sorted(deps_by_consumer.get(name, set()))
        item["artifacts"] = artifacts
        updated.append(item)
    return updated


def build_dependency_graph(
    planning_ir: PlanningIR,
    profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    implementation_plan: dict[str, Any],
    llm_client: FixedQwenClient | None = None,
    log: Any = None,
    log_artifact: Any = None,
    register_usage: Any = None,
) -> dict[str, Any]:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    module_graph = [item for item in implementation_plan.get("module_graph", []) if isinstance(item, dict)]
    handler_matrix = [item for item in implementation_plan.get("handler_matrix", []) if isinstance(item, dict)]
    canonical_types = [item for item in implementation_plan.get("canonical_types", []) if isinstance(item, dict)]
    decision_refs = _decision_refs_from_plan(implementation_plan)
    canonical_by_module = _canonical_type_by_module(canonical_types, protocol_slug)
    owner_file_by_module = _canonical_file_by_module(canonical_types)

    raw_graph: dict[str, Any] = {}
    messages = build_dependency_graph_prompt(
        {
            "protocol_name": planning_ir.protocol_name,
            "normalized_roles": planning_ir.normalized_roles,
            "minimum_v1_surface": [item.get("name") for item in planning_ir.minimum_v1.get("must_support_surface", []) if isinstance(item, dict)],
            "blocking_questions": planning_ir.open_questions.get("blocking", []),
        },
        profile,
        chosen_architecture,
        implementation_plan,
    )
    if llm_client is not None:
        if log_artifact is not None:
            log_artifact("dependency_graph_prompt", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"dependency_graph llm_request_start messages={len(messages)}")
        try:
            response = _generate_with_usage(
                llm_client,
                LLMRequest(messages=messages, top_p=0.3, temperature=0.1, is_stream=True),
            )
        except Exception:
            response = None
            if log is not None:
                log("dependency_graph llm_failed fallback=deterministic")
        if response is not None:
            if register_usage is not None:
                register_usage("dependency_graph", "generate", response.usage)
            if log_artifact is not None:
                log_artifact("dependency_graph_response", response.content, ".txt")
            payload = _load_json_payload(response.content)
            if log_artifact is not None and isinstance(payload, dict):
                log_artifact("dependency_graph_response_normalized", json.dumps(payload, ensure_ascii=False, indent=2), ".json")
            raw_graph = _extract_graph_payload(payload)
            if log is not None:
                log(f"dependency_graph llm_response_done module_edges={len(raw_graph.get('module_edges', []))}")

    candidate_edges: list[dict[str, Any]] = []
    candidate_edges.extend(_normalize_llm_edges(raw_graph, module_graph))
    candidate_edges.extend(_relationship_edges(chosen_architecture, module_graph, decision_refs))
    candidate_edges.extend(_capability_edges(module_graph, profile, handler_matrix, decision_refs))
    module_edges = _select_acyclic_edges(candidate_edges)
    module_edges = [
        {
            **edge,
            "edge_id": _edge_id("module_edge", edge.get("consumer_module", ""), edge.get("provider_module", ""), edge.get("dependency_kind", "")),
        }
        for edge in module_edges
    ]
    interface_contracts = _interface_contracts(module_edges, protocol_slug, canonical_by_module, owner_file_by_module)
    function_edges = _function_edges(module_edges, protocol_slug)
    data_edges = _data_edges(module_edges, handler_matrix, protocol_slug, canonical_by_module)
    return {
        "schema_version": DEPENDENCY_SCHEMA,
        "origin": "llm_plus_deterministic" if raw_graph else "deterministic",
        "module_edges": [{key: value for key, value in edge.items() if key != "priority"} for edge in module_edges],
        "interface_contracts": interface_contracts,
        "function_edges": function_edges,
        "data_edges": data_edges,
        "generation_order": _topological_generation_order(apply_dependency_graph_to_modules(module_graph, {"module_edges": module_edges}, canonical_types, handler_matrix, planning_ir.protocol_name)),
        "validation": {
            "candidate_edge_count": len(candidate_edges),
            "selected_module_edge_count": len(module_edges),
            "dropped_cycle_or_duplicate_count": max(0, len(candidate_edges) - len(module_edges)),
            "empty_allowed": len(module_graph) <= 1,
        },
    }
