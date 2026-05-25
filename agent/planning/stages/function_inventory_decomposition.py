from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecompositionRule:
    rule_id: str
    positive_terms: list[str]
    artifact_terms: list[str]
    capability_terms: list[str]
    doc_ref_terms: list[str]
    hint: str


DECOMPOSITION_RULES = [
    DecompositionRule(
        rule_id="transport_runtime_io",
        positive_terms=["transport", "connection", "endpoint", "socket", "tcp", "udp", "event loop", "poll", "epoll", "select", "read", "write", "send", "receive", "accept", "close", "timeout", "timer", "buffer", "backpressure"],
        artifact_terms=["connection", "endpoint", "socket", "server", "read", "write", "send", "receive", "accept", "close", "flush", "timer"],
        capability_terms=["transport", "connection", "timeout", "timer", "runtime", "io"],
        doc_ref_terms=["transport", "connection", "endpoint", "socket", "tcp", "udp", "event", "timer", "timeout", "backpressure"],
        hint="If this module owns transport/runtime I/O, do not collapse the runtime into a single init/run function. Consider function families for lifecycle control, endpoint or connection creation/destruction, accept or datagram receive paths, readable/writable/error/close/timeout event handling, input/output buffer operations, connection lookup/removal, polling interest updates, and cleanup on I/O failure. Keep low-level socket/buffer helpers internal unless consumers need a stable boundary.",
    ),
    DecompositionRule(
        rule_id="framing_and_parsing",
        positive_terms=["framing", "packetization", "stream reassembly", "line oriented parsing", "datagram parsing", "decode", "parse", "command", "method", "header", "option", "field", "wire format", "delimiter", "length prefix", "remaining length", "partial input"],
        artifact_terms=["frame", "framing", "decode", "decoder", "parse", "parser", "command", "method", "header", "option", "field", "packet"],
        capability_terms=["message_decode", "decode", "parse", "framing", "field", "wire"],
        doc_ref_terms=["decoder", "decode", "parse", "framing", "field", "header", "option", "delimiter", "length"],
        hint="If this module owns framing or parsing, split coarse decode logic into frame/message boundary detection, incremental parser state management, primitive field or token readers, line/delimiter or length-prefix parsing, message-specific or command-specific parsers, semantic validation helpers, malformed/incomplete input handling, decoded object construction, and cleanup of partially decoded objects. Avoid one function that simultaneously reads bytes, validates protocol semantics, dispatches handlers, and updates session state.",
    ),
    DecompositionRule(
        rule_id="encoding_and_response",
        positive_terms=["encode", "serialize", "response", "reply", "status code", "writer", "packet builder", "output buffer", "header writer", "option writer", "payload writer", "acknowledgement", "error response"],
        artifact_terms=["encode", "encoder", "serialize", "response", "reply", "writer", "builder", "ack", "error"],
        capability_terms=["message_encode", "encode", "serialize", "response", "ack"],
        doc_ref_terms=["encoder", "encode", "serialize", "response", "reply", "status", "ack", "error_response"],
        hint="If this module owns encoding or response generation, split generic serialization into response/message-specific encoders, primitive writers for fields/tokens/headers/options, status or reason construction, payload/body writer helpers, buffer sizing/allocation/growth helpers, encoded buffer ownership cleanup, and protocol error response helpers. Keep common writer utilities internal, and expose only stable encode/send-response APIs required by consumers.",
    ),
    DecompositionRule(
        rule_id="dispatch_and_handlers",
        positive_terms=["dispatch", "handler", "command table", "method routing", "packet routing", "semantic dispatch", "state machine", "protocol event", "unsupported command", "malformed request"],
        artifact_terms=["dispatch", "handle", "handler", "route", "command", "method", "event"],
        capability_terms=["semantic_dispatch", "state_machine", "protocol_error_policy", "handler"],
        doc_ref_terms=["handler", "dispatch", "command", "method", "unsupported", "malformed", "state_machine"],
        hint="If this module owns dispatch or protocol handlers, include a clear dispatch boundary between parsed messages and semantic actions. Consider function families for message/command classification, handler lookup, per-message or per-command handlers, unsupported or malformed request handling, shared precondition checks, common response helpers, protocol error handling, and state-machine integration. Avoid merging transport callbacks, parser logic, semantic dispatch, handler behavior, response encoding, and connection cleanup into one coarse handler.",
    ),
    DecompositionRule(
        rule_id="session_transaction_state",
        positive_terms=["session", "transaction", "conversation", "client state", "connection state", "login state", "request context", "mail transaction", "control session", "state transition", "lifecycle", "expiry"],
        artifact_terms=["session", "transaction", "state", "context", "login", "lifecycle", "expiry"],
        capability_terms=["state_machine", "session", "transaction", "lifecycle", "timeout", "expiry"],
        doc_ref_terms=["session", "transaction", "state", "login", "lifecycle", "expiry", "timeout"],
        hint="If this module owns session or transaction state, include function families for state object lifecycle, lookup or get-or-create, binding/unbinding state to a connection or endpoint, legal state transition validation, transaction begin/update/commit/abort, reset after completion or failure, timeout/expiry cleanup, and per-client/session destruction. Separate protocol state changes from low-level transport I/O, and make cleanup/error paths explicit.",
    ),
    DecompositionRule(
        rule_id="registry_routing_namespace",
        positive_terms=["registry", "routing table", "subscription", "resource tree", "path tree", "topic", "mailbox", "recipient set", "namespace", "match", "lookup", "index", "route target", "filter"],
        artifact_terms=["registry", "route", "routing", "subscription", "resource", "path", "topic", "mailbox", "recipient", "lookup", "match", "index", "filter"],
        capability_terms=["routing", "registry", "subscription", "resource", "lookup", "match"],
        doc_ref_terms=["registry", "routing", "subscription", "resource", "path", "topic", "mailbox", "recipient", "lookup", "match"],
        hint="If this module owns a registry, routing index, or namespace, include function families for registry lifecycle, entry add/remove/update, lookup/match, key/filter/path/resource validation, result collection or iteration, duplicate/conflict handling, owner/session removal cleanup, and result-list cleanup. Keep internal data-structure traversal helpers private, while exposing only the stable operations needed by routing, delivery, or handler modules.",
    ),
    DecompositionRule(
        rule_id="payload_data_transfer",
        positive_terms=["payload", "body", "data", "file transfer", "blockwise", "publish payload", "data channel", "stream body", "attachment", "content transfer", "chunk", "upload", "download"],
        artifact_terms=["payload", "body", "data", "file", "block", "chunk", "transfer", "upload", "download", "content"],
        capability_terms=["payload", "data", "transfer", "body", "blockwise", "publish"],
        doc_ref_terms=["payload", "body", "data", "file", "block", "chunk", "transfer", "upload", "download"],
        hint="If this module owns payload or data transfer, include function families for transfer setup, receive/append/finalize, chunk or block handling, size/limit validation, payload ownership tracking, body-state management, delivery to the next subsystem, transfer abort/rollback, data-channel close, and payload cleanup. Avoid mixing payload accumulation, protocol command handling, storage/routing decisions, response generation, and resource cleanup in a single function.",
    ),
    DecompositionRule(
        rule_id="application_orchestration_cleanup",
        positive_terms=["top level runtime", "application boundary", "broker orchestration", "server orchestration", "client orchestration", "subsystem composition", "lifecycle", "resource ownership", "error policy", "recovery", "cleanup", "callback registration", "adapter"],
        artifact_terms=["create", "configure", "start", "run", "stop", "destroy", "cleanup", "callback", "adapter", "app"],
        capability_terms=["role_composition", "protocol_error_policy", "connection_termination", "lifecycle", "cleanup", "recovery"],
        doc_ref_terms=["runtime", "application", "orchestration", "lifecycle", "cleanup", "callback", "adapter", "error_policy"],
        hint="If this module owns application orchestration or cross-subsystem composition, include function families for create/configure/start/run/stop/destroy, subsystem initialization and teardown ordering, callback registration, transport-to-protocol event adapters, protocol-to-transport send helpers, graceful shutdown, fatal error handling, rollback of partially initialized resources, and centralized cleanup paths. Public APIs should describe lifecycle or stable integration boundaries; detailed subsystem glue should usually remain internal.",
    ),
]

_WORD_RE = re.compile(r"[^a-z0-9]+")


def _norm(value: Any) -> str:
    return _WORD_RE.sub(" ", str(value).lower()).strip()


def _join(values: list[Any]) -> str:
    return _norm(" ".join(str(value) for value in values if str(value).strip()))


def _contains(text: str, terms: list[str]) -> list[str]:
    found: list[str] = []
    padded = f" {text} "
    for term in terms:
        normalized = _norm(term)
        if normalized and f" {normalized} " in padded:
            found.append(term)
    return found


def _artifact_text(artifacts: list[dict[str, Any]]) -> str:
    return _join([value for artifact in artifacts for value in (artifact.get("name", ""), artifact.get("role", "")) if isinstance(artifact, dict)])


def _core_design_text(module_id: str, context: dict[str, Any]) -> str:
    summary = context.get("core_design_summary", {})
    if not isinstance(summary, dict):
        return ""
    values: list[Any] = []
    for section, owner_key in (
        ("handler_matrix", "owner_module_id"),
        ("state_design", "owner_module_id"),
        ("resource_lifecycle", "owner_module_id"),
        ("error_strategy", "owner_module_id"),
    ):
        for item in summary.get(section, []):
            if isinstance(item, dict) and str(item.get(owner_key, "")) == module_id:
                values.extend(item.values())
    return _join(values)


def select_top_decomposition_hints(module_artifact: dict[str, Any], context: dict[str, Any] | None = None, max_hints: int = 2) -> dict[str, Any]:
    context = context or {}
    module_id = str(module_artifact.get("module_id", ""))
    artifacts = [item for item in module_artifact.get("artifacts", []) if isinstance(item, dict)]
    role_text = _join([module_artifact.get("role", ""), module_artifact.get("purpose", ""), " ".join(str(item) for item in module_artifact.get("responsibilities", []))])
    artifact_text = _artifact_text(artifacts)
    capability_text = _join([*(module_artifact.get("owned_capabilities", []) or []), *(module_artifact.get("owned_capability_ids", []) or [])])
    doc_ref_text = _join(module_artifact.get("doc_ref", []) or [])
    provider_artifacts = [
        artifact
        for provider in context.get("provider_module_artifacts", [])
        if isinstance(provider, dict)
        for artifact in provider.get("artifacts", [])
        if isinstance(artifact, dict)
    ]
    consumer_artifacts = [
        artifact
        for consumer in context.get("consumer_module_artifact_dependencies", [])
        if isinstance(consumer, dict)
        for artifact in consumer.get("artifacts", [])
        if isinstance(artifact, dict)
    ]
    boundary_text = _artifact_text(provider_artifacts + consumer_artifacts)
    core_text = _core_design_text(module_id, context)

    scored: list[tuple[int, int, DecompositionRule, list[str]]] = []
    for index, rule in enumerate(DECOMPOSITION_RULES):
        score = 0
        evidence: list[str] = []
        matches = _contains(role_text, rule.positive_terms)
        if matches:
            score += 2 * len(matches)
            evidence.append(f"role:{','.join(matches[:3])}")
        matches = _contains(artifact_text, rule.artifact_terms)
        if matches:
            score += 2 * len(matches)
            evidence.append(f"artifact:{','.join(matches[:3])}")
        matches = _contains(capability_text, rule.capability_terms)
        if matches:
            score += 3 * len(matches)
            evidence.append(f"capability:{','.join(matches[:3])}")
        matches = _contains(doc_ref_text, rule.doc_ref_terms)
        if matches:
            score += len(matches)
            evidence.append(f"doc_ref:{','.join(matches[:3])}")
        if _contains(boundary_text, rule.artifact_terms + rule.positive_terms):
            score += 1
            evidence.append("provider_or_consumer_boundary")
        if _contains(core_text, rule.artifact_terms + rule.positive_terms + rule.capability_terms):
            score += 1
            evidence.append("core_design_owner_evidence")
        scored.append((score, index, rule, evidence))

    positive = [item for item in scored if item[0] > 0]
    selected = sorted(positive or [item for item in scored if item[2].rule_id == "application_orchestration_cleanup"], key=lambda item: (-item[0], item[1]))[: max(1, max_hints)]
    if len(selected) < max_hints:
        by_id = {item[2].rule_id: item for item in scored}
        fallback = by_id["application_orchestration_cleanup"]
        if fallback[2].rule_id not in {item[2].rule_id for item in selected}:
            selected.append(fallback)
    selected = selected[:max_hints]
    return {
        "detected_rule_ids": [item[2].rule_id for item in selected],
        "selected_decomposition_hints": [item[2].hint for item in selected],
        "evidence_summary": [f"{item[2].rule_id}: score={item[0]}; evidence={'; '.join(item[3]) or 'fallback'}" for item in selected],
    }
