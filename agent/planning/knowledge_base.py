from __future__ import annotations

from .models import ExpertRule


def load_rules() -> list[ExpertRule]:
    return [
        ExpertRule(
            rule_id="stream_requires_incremental_decode",
            title="Stream Transport Requires Incremental Decode",
            description="Stream-oriented protocols need buffering and incremental frame parsing.",
            conditions={"transport_shape": "stream"},
            engineering_obligations=["Introduce connection input buffer", "Introduce incremental decoder boundary handling"],
            recommended_patterns=["layered_io_then_codec", "parse_loop_with_partial_frame_state"],
            required_capabilities=["connection_buffering", "incremental_message_framing"],
            spec_impacts=["Assign stream buffering to a runtime or codec module", "Assign frame parsing ownership to a codec/framing boundary"],
        ),
        ExpertRule(
            rule_id="timer_manager_required",
            title="Timer Manager Required",
            description="Timer-governed semantics need explicit timer handling.",
            conditions={"timing_model": "timer_driven"},
            engineering_obligations=["Model timer source and timeout handlers"],
            recommended_patterns=["event_loop_plus_timer_callbacks"],
            required_capabilities=["timer_source", "timeout_handling"],
            spec_impacts=["Assign timer handling to an existing runtime/state module unless facts require an independent owner", "Add timing tests"],
        ),
        ExpertRule(
            rule_id="routing_resource_split",
            title="Routing And Resource Split",
            description="Protocols with strong dispatch and resource objects benefit from router/store separation.",
            conditions={"routing_intensity": ["medium", "high"], "resource_intensity": ["medium", "high"]},
            engineering_obligations=["Separate routing logic from resource ownership", "Define resource store abstraction"],
            recommended_patterns=["router_plus_store", "command_handler_to_store"],
            required_capabilities=["routing_dispatch", "resource_ownership"],
            spec_impacts=["Assign routing behavior and resource ownership to explicit module capabilities", "Add store-facing interfaces when stateful resources exist"],
        ),
        ExpertRule(
            rule_id="persistent_session_store",
            title="Persistent State Needs Store",
            description="Persistent or resumable sessions require stable ownership and recovery hooks.",
            conditions={"statefulness": "persistent_state"},
            engineering_obligations=["Model session store", "Define recovery and cleanup path"],
            recommended_patterns=["session_store_with_rehydration"],
            required_capabilities=["session_state_ownership", "recovery_cleanup_policy"],
            spec_impacts=["Assign persistent state ownership", "Expose lifecycle cleanup functions"],
        ),
        ExpertRule(
            rule_id="connection_close_error_policy",
            title="Connection Close Error Policy",
            description="Close-on-error semantics require a centralized termination path.",
            conditions={"failure_semantics": "close_connection_on_protocol_error"},
            engineering_obligations=["Define terminal protocol error path", "Ensure handlers map malformed input to close action"],
            recommended_patterns=["shared_protocol_error_handler"],
            required_capabilities=["protocol_error_policy", "connection_termination"],
            spec_impacts=["Include terminal error behavior in handler matrix", "Add malformed-input tests"],
        ),
        ExpertRule(
            rule_id="canonical_ownership_required",
            title="Canonical Ownership Required",
            description="Planner must assign unique owner for public types and shared state.",
            conditions={"always": True},
            engineering_obligations=["Assign unique canonical owner for each public type"],
            recommended_patterns=["one_public_type_one_owner"],
            required_capabilities=["canonical_type_ownership"],
            spec_impacts=["Generate unique file ownership", "Verify no duplicate public type owners"],
        ),
    ]
