from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConcreteFunctionSlot:
    family: str
    suffix: str
    purpose: str
    function_kind: str = ""


@dataclass(frozen=True)
class DecompositionRule:
    rule_id: str
    positive_terms: list[str]
    artifact_terms: list[str]
    capability_terms: list[str]
    doc_ref_terms: list[str]
    hint: str
    expected_function_families: list[str]
    concrete_function_slots: list[ConcreteFunctionSlot]


DECOMPOSITION_RULES = [
    DecompositionRule(
        rule_id="transport_runtime_io",
        positive_terms=["transport", "connection", "endpoint", "socket", "tcp", "udp", "event loop", "poll", "epoll", "select", "read", "write", "send", "receive", "accept", "close", "timeout", "timer", "buffer", "backpressure"],
        artifact_terms=["connection", "endpoint", "socket", "server", "read", "write", "send", "receive", "accept", "close", "flush", "timer"],
        capability_terms=["transport", "connection", "timeout", "timer", "runtime", "io"],
        doc_ref_terms=["transport", "connection", "endpoint", "socket", "tcp", "udp", "event", "timer", "timeout", "backpressure"],
        hint="If this module owns transport/runtime I/O, do not collapse the runtime into a single init/run function. Consider function families for lifecycle control, endpoint or connection creation/destruction, accept or datagram receive paths, readable/writable/error/close/timeout event handling, input/output buffer operations, connection lookup/removal, polling interest updates, and cleanup on I/O failure. Keep low-level socket/buffer helpers internal unless consumers need a stable boundary.",
        expected_function_families=["lifecycle_control", "accept_or_receive_path", "read_path", "write_or_flush_path", "event_callback_or_dispatch", "connection_or_endpoint_management", "timeout_or_error_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("lifecycle_control", "init_runtime", "Initialize transport runtime state and owned descriptors.", "resource_lifecycle"),
            ConcreteFunctionSlot("accept_or_receive_path", "accept_loop", "Accept or receive new transport events before per-connection handling.", "internal_helper"),
            ConcreteFunctionSlot("read_path", "read_into_buffer", "Read transport bytes into the module input buffer.", "internal_helper"),
            ConcreteFunctionSlot("write_or_flush_path", "flush_output", "Flush buffered outbound bytes to the transport.", "internal_helper"),
            ConcreteFunctionSlot("event_callback_or_dispatch", "poll_events", "Dispatch readable, writable, close, and timeout transport events.", "internal_helper"),
            ConcreteFunctionSlot("connection_or_endpoint_management", "close_connection", "Close and detach a connection or endpoint record.", "resource_lifecycle"),
            ConcreteFunctionSlot("timeout_or_error_cleanup", "cleanup_failed_io", "Clean up transport resources after timeout or I/O failure.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="framing_and_parsing",
        positive_terms=["framing", "packetization", "stream reassembly", "line oriented parsing", "datagram parsing", "decode", "parse", "command", "method", "header", "option", "field", "wire format", "delimiter", "length prefix", "remaining length", "partial input"],
        artifact_terms=["frame", "framing", "decode", "decoder", "parse", "parser", "command", "method", "header", "option", "field", "packet"],
        capability_terms=["message_decode", "decode", "parse", "framing", "field", "wire"],
        doc_ref_terms=["decoder", "decode", "parse", "framing", "field", "header", "option", "delimiter", "length"],
        hint="If this module owns framing or parsing, split coarse decode logic into frame/message boundary detection, incremental parser state management, primitive field or token readers, line/delimiter or length-prefix parsing, message-specific or command-specific parsers, semantic validation helpers, malformed/incomplete input handling, decoded object construction, and cleanup of partially decoded objects. Avoid one function that simultaneously reads bytes, validates protocol semantics, dispatches handlers, and updates session state.",
        expected_function_families=["parser_context_lifecycle", "feed_or_parse_entry", "frame_boundary_detection", "primitive_reader_or_tokenizer", "message_or_command_specific_parser", "validation_or_malformed_input_handling", "decoded_object_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("parser_context_lifecycle", "decoder_init", "Initialize parser cursor or decode context state.", "parser"),
            ConcreteFunctionSlot("feed_or_parse_entry", "feed_bytes", "Feed incremental bytes into the parser entry path.", "parser"),
            ConcreteFunctionSlot("frame_boundary_detection", "parse_frame_boundary", "Detect frame boundaries and incomplete input.", "parser"),
            ConcreteFunctionSlot("primitive_reader_or_tokenizer", "read_field", "Read primitive wire fields or tokens.", "parser"),
            ConcreteFunctionSlot("message_or_command_specific_parser", "decode_message", "Decode a message-specific packet or command body.", "parser"),
            ConcreteFunctionSlot("validation_or_malformed_input_handling", "validate_frame", "Validate parsed fields and reject malformed or incomplete input.", "validator"),
            ConcreteFunctionSlot("decoded_object_cleanup", "cleanup_decoded_message", "Clean up partially decoded message objects.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="encoding_and_response",
        positive_terms=["encode", "serialize", "response", "reply", "status code", "writer", "packet builder", "output buffer", "header writer", "option writer", "payload writer", "acknowledgement", "error response"],
        artifact_terms=["encode", "encoder", "serialize", "response", "reply", "writer", "builder", "ack", "error"],
        capability_terms=["message_encode", "encode", "serialize", "response", "ack"],
        doc_ref_terms=["encoder", "encode", "serialize", "response", "reply", "status", "ack", "error_response"],
        hint="If this module owns encoding or response generation, split generic serialization into response/message-specific encoders, primitive writers for fields/tokens/headers/options, status or reason construction, payload/body writer helpers, buffer sizing/allocation/growth helpers, encoded buffer ownership cleanup, and protocol error response helpers. Keep common writer utilities internal, and expose only stable encode/send-response APIs required by consumers.",
        expected_function_families=["encode_or_response_entry", "message_or_response_specific_encoder", "primitive_writer", "status_header_or_option_writer", "buffer_size_or_allocation_helper", "error_response_helper", "encoded_buffer_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("encode_or_response_entry", "encode_packet", "Encode a protocol packet or response entry point.", "serializer"),
            ConcreteFunctionSlot("message_or_response_specific_encoder", "encode_response", "Encode a message-specific response payload.", "serializer"),
            ConcreteFunctionSlot("primitive_writer", "write_field", "Write primitive wire fields into an output buffer.", "serializer"),
            ConcreteFunctionSlot("status_header_or_option_writer", "write_status", "Write status, header, option, or reason fields.", "serializer"),
            ConcreteFunctionSlot("buffer_size_or_allocation_helper", "reserve_output", "Reserve or grow output buffer capacity.", "internal_helper"),
            ConcreteFunctionSlot("error_response_helper", "encode_error_response", "Build protocol error response payloads.", "error_helper"),
            ConcreteFunctionSlot("encoded_buffer_cleanup", "free_encoded_buffer", "Release encoded buffer ownership.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="dispatch_and_handlers",
        positive_terms=["dispatch", "handler", "command table", "method routing", "packet routing", "semantic dispatch", "state machine", "protocol event", "unsupported command", "malformed request"],
        artifact_terms=["dispatch", "handle", "handler", "route", "command", "method", "event"],
        capability_terms=["semantic_dispatch", "state_machine", "protocol_error_policy", "handler"],
        doc_ref_terms=["handler", "dispatch", "command", "method", "unsupported", "malformed", "state_machine"],
        hint="If this module owns dispatch or protocol handlers, include a clear dispatch boundary between parsed messages and semantic actions. Consider function families for message/command classification, handler lookup, per-message or per-command handlers, unsupported or malformed request handling, shared precondition checks, common response helpers, protocol error handling, and state-machine integration. Avoid merging transport callbacks, parser logic, semantic dispatch, handler behavior, response encoding, and connection cleanup into one coarse handler.",
        expected_function_families=["dispatch_boundary", "message_or_command_classification", "handler_lookup_or_switch", "per_message_or_command_handler", "unsupported_or_malformed_handler", "shared_precondition_check", "protocol_error_handling"],
        concrete_function_slots=[
            ConcreteFunctionSlot("dispatch_boundary", "dispatch_message", "Dispatch a parsed message into semantic handling.", "handler"),
            ConcreteFunctionSlot("message_or_command_classification", "classify_message", "Classify message or command type before handler lookup.", "handler"),
            ConcreteFunctionSlot("handler_lookup_or_switch", "select_handler", "Select the handler branch for a message or command.", "handler"),
            ConcreteFunctionSlot("per_message_or_command_handler", "handle_message", "Handle one concrete message or command path.", "handler"),
            ConcreteFunctionSlot("unsupported_or_malformed_handler", "handle_unsupported", "Handle unsupported commands or malformed requests.", "handler"),
            ConcreteFunctionSlot("shared_precondition_check", "check_preconditions", "Check shared dispatch preconditions before state changes.", "validator"),
            ConcreteFunctionSlot("protocol_error_handling", "handle_protocol_error", "Handle protocol errors at the dispatch boundary.", "error_helper"),
        ],
    ),
    DecompositionRule(
        rule_id="session_transaction_state",
        positive_terms=["session", "transaction", "conversation", "client state", "connection state", "login state", "request context", "mail transaction", "control session", "state transition", "lifecycle", "expiry"],
        artifact_terms=["session", "transaction", "state", "context", "login", "lifecycle", "expiry"],
        capability_terms=["state_machine", "session", "transaction", "lifecycle", "timeout", "expiry"],
        doc_ref_terms=["session", "transaction", "state", "login", "lifecycle", "expiry", "timeout"],
        hint="If this module owns session or transaction state, include function families for state object lifecycle, lookup or get-or-create, binding/unbinding state to a connection or endpoint, legal state transition validation, transaction begin/update/commit/abort, reset after completion or failure, timeout/expiry cleanup, and per-client/session destruction. Separate protocol state changes from low-level transport I/O, and make cleanup/error paths explicit.",
        expected_function_families=["state_object_lifecycle", "lookup_or_get_or_create", "bind_or_unbind_to_connection", "legal_state_transition_check", "transaction_update_or_commit_abort", "timeout_or_expiry_cleanup", "per_session_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("state_object_lifecycle", "create_state", "Create or initialize session/transaction state.", "resource_lifecycle"),
            ConcreteFunctionSlot("lookup_or_get_or_create", "get_or_create_state", "Lookup or create session/transaction state.", "state_machine"),
            ConcreteFunctionSlot("bind_or_unbind_to_connection", "bind_connection", "Bind state to a connection or endpoint.", "state_machine"),
            ConcreteFunctionSlot("legal_state_transition_check", "validate_state_transition", "Validate legal state transitions.", "validator"),
            ConcreteFunctionSlot("transaction_update_or_commit_abort", "update_transaction", "Update, commit, abort, or roll back transaction state.", "state_machine"),
            ConcreteFunctionSlot("timeout_or_expiry_cleanup", "expire_state", "Clean up expired session or transaction state.", "resource_lifecycle"),
            ConcreteFunctionSlot("per_session_cleanup", "cleanup_session", "Clean up per-session resources and ownership.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="registry_routing_namespace",
        positive_terms=["registry", "routing table", "subscription", "resource tree", "path tree", "topic", "mailbox", "recipient set", "namespace", "match", "lookup", "index", "route target", "filter"],
        artifact_terms=["registry", "route", "routing", "subscription", "resource", "path", "topic", "mailbox", "recipient", "lookup", "match", "index", "filter"],
        capability_terms=["routing", "registry", "subscription", "resource", "lookup", "match"],
        doc_ref_terms=["registry", "routing", "subscription", "resource", "path", "topic", "mailbox", "recipient", "lookup", "match"],
        hint="If this module owns a registry, routing index, or namespace, include function families for registry lifecycle, entry add/remove/update, lookup/match, key/filter/path/resource validation, result collection or iteration, duplicate/conflict handling, owner/session removal cleanup, and result-list cleanup. Keep internal data-structure traversal helpers private, while exposing only the stable operations needed by routing, delivery, or handler modules.",
        expected_function_families=["registry_lifecycle", "add_remove_update_entry", "lookup_or_match", "key_filter_path_validation", "result_collection_or_iteration", "duplicate_or_conflict_handling", "owner_removal_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("registry_lifecycle", "init_registry", "Initialize registry or routing index state.", "resource_lifecycle"),
            ConcreteFunctionSlot("add_remove_update_entry", "update_entry", "Add, remove, or update one registry entry.", "internal_helper"),
            ConcreteFunctionSlot("lookup_or_match", "match_entry", "Lookup or match entries in the routing index.", "internal_helper"),
            ConcreteFunctionSlot("key_filter_path_validation", "validate_filter", "Validate keys, filters, or paths before insertion.", "validator"),
            ConcreteFunctionSlot("result_collection_or_iteration", "collect_matches", "Collect or iterate matched routing results.", "internal_helper"),
            ConcreteFunctionSlot("duplicate_or_conflict_handling", "resolve_conflict", "Resolve duplicate routes, conflicting subscriptions, or replacement policy.", "internal_helper"),
            ConcreteFunctionSlot("owner_removal_cleanup", "cleanup_owner_entries", "Remove all registry entries owned by a session or route owner.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="payload_data_transfer",
        positive_terms=["payload", "body", "data", "file transfer", "blockwise", "publish payload", "data channel", "stream body", "attachment", "content transfer", "chunk", "upload", "download"],
        artifact_terms=["payload", "body", "data", "file", "block", "chunk", "transfer", "upload", "download", "content"],
        capability_terms=["payload", "data", "transfer", "body", "blockwise", "publish"],
        doc_ref_terms=["payload", "body", "data", "file", "block", "chunk", "transfer", "upload", "download"],
        hint="If this module owns payload or data transfer, include function families for transfer setup, receive/append/finalize, chunk or block handling, size/limit validation, payload ownership tracking, body-state management, delivery to the next subsystem, transfer abort/rollback, data-channel close, and payload cleanup. Avoid mixing payload accumulation, protocol command handling, storage/routing decisions, response generation, and resource cleanup in a single function.",
        expected_function_families=["transfer_setup", "receive_append_finalize", "chunk_or_block_handling", "size_limit_validation", "payload_ownership_tracking", "delivery_helper", "abort_or_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("transfer_setup", "begin_transfer", "Initialize a payload/data transfer.", "internal_helper"),
            ConcreteFunctionSlot("receive_append_finalize", "append_payload", "Append received payload data and finalize when complete.", "internal_helper"),
            ConcreteFunctionSlot("chunk_or_block_handling", "process_chunk", "Process one payload chunk or block.", "internal_helper"),
            ConcreteFunctionSlot("size_limit_validation", "validate_payload_size", "Validate payload size limits.", "validator"),
            ConcreteFunctionSlot("payload_ownership_tracking", "track_payload_owner", "Track payload ownership across receive and delivery boundaries.", "internal_helper"),
            ConcreteFunctionSlot("delivery_helper", "deliver_payload", "Deliver completed payload data to the next subsystem.", "internal_helper"),
            ConcreteFunctionSlot("abort_or_cleanup", "cleanup_payload", "Abort or clean up payload transfer resources.", "resource_lifecycle"),
        ],
    ),
    DecompositionRule(
        rule_id="application_orchestration_cleanup",
        positive_terms=["top level runtime", "application boundary", "broker orchestration", "server orchestration", "client orchestration", "subsystem composition", "lifecycle", "resource ownership", "error policy", "recovery", "cleanup", "callback registration", "adapter"],
        artifact_terms=["create", "configure", "start", "run", "stop", "destroy", "cleanup", "callback", "adapter", "app"],
        capability_terms=["role_composition", "protocol_error_policy", "connection_termination", "lifecycle", "cleanup", "recovery"],
        doc_ref_terms=["runtime", "application", "orchestration", "lifecycle", "cleanup", "callback", "adapter", "error_policy"],
        hint="If this module owns application orchestration or cross-subsystem composition, include function families for create/configure/start/run/stop/destroy, subsystem initialization and teardown ordering, callback registration, transport-to-protocol event adapters, protocol-to-transport send helpers, graceful shutdown, fatal error handling, rollback of partially initialized resources, and centralized cleanup paths. Public APIs should describe lifecycle or stable integration boundaries; detailed subsystem glue should usually remain internal.",
        expected_function_families=["create_configure_start_run_stop_destroy", "subsystem_init_teardown_order", "callback_registration_or_adapter", "protocol_to_transport_send_helper", "graceful_shutdown", "fatal_error_or_rollback", "centralized_cleanup"],
        concrete_function_slots=[
            ConcreteFunctionSlot("create_configure_start_run_stop_destroy", "configure_runtime", "Configure top-level runtime lifecycle state.", "resource_lifecycle"),
            ConcreteFunctionSlot("subsystem_init_teardown_order", "init_subsystems", "Initialize subsystems in dependency order.", "resource_lifecycle"),
            ConcreteFunctionSlot("callback_registration_or_adapter", "register_callbacks", "Register callback adapters across subsystems.", "internal_helper"),
            ConcreteFunctionSlot("protocol_to_transport_send_helper", "send_protocol_bytes", "Bridge protocol output to transport send helpers.", "internal_helper"),
            ConcreteFunctionSlot("graceful_shutdown", "shutdown_runtime", "Perform graceful shutdown across subsystems.", "resource_lifecycle"),
            ConcreteFunctionSlot("fatal_error_or_rollback", "rollback_runtime", "Roll back partially initialized runtime state after fatal errors.", "error_helper"),
            ConcreteFunctionSlot("centralized_cleanup", "cleanup_runtime", "Centralize runtime cleanup and teardown.", "resource_lifecycle"),
        ],
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


def select_top_decomposition_hints(module_artifact: dict[str, Any], context: dict[str, Any] | None = None, max_hints: int = 3) -> dict[str, Any]:
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
    owner_text = f"{role_text} {artifact_text} {capability_text} {doc_ref_text} {core_text}"

    def rule_has_required_evidence(rule_id: str, score: int, evidence: list[str]) -> bool:
        if score < 2 or not any(not item.startswith(("provider_or_consumer_boundary", "core_design_owner_evidence")) for item in evidence):
            return False
        if rule_id == "transport_runtime_io":
            return bool(_contains(owner_text, ["transport", "connection", "socket", "tcp", "udp", "epoll", "event loop", "read", "write", "send", "receive", "accept", "close"]))
        if rule_id == "session_transaction_state":
            return bool(_contains(owner_text, ["session", "transaction", "client state", "connection state", "login state", "request context"]))
        return True

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
        if rule_has_required_evidence(rule.rule_id, score, evidence):
            scored.append((score, index, rule, evidence))

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_hints]
    selected_rule_ids = [item[2].rule_id for item in selected]
    return {
        "selected_rule_ids": selected_rule_ids,
        "detected_rule_ids": selected_rule_ids,
        "selected_decomposition_hints": [item[2].hint for item in selected],
        "expected_function_families_by_rule": {item[2].rule_id: item[2].expected_function_families for item in selected},
        "recommended_concrete_slots_by_rule": {
            item[2].rule_id: [
                {
                    "family": slot.family,
                    "suffix": slot.suffix,
                    "purpose": slot.purpose,
                    "function_kind": slot.function_kind,
                }
                for slot in item[2].concrete_function_slots
            ]
            for item in selected
        },
        "evidence_summary": [f"{item[2].rule_id}: score={item[0]}; evidence={'; '.join(item[3]) or 'fallback'}" for item in selected],
    }
