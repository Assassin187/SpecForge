from __future__ import annotations


SCHEMA_VERSION = "protocol_profile/v1"


ALLOWED_TRANSPORT_SHAPES = {"stream", "datagram", "mixed", "unknown"}
ALLOWED_STATEFULNESS = {"stateless", "connection_state", "session_state", "persistent_state", "unknown"}
ALLOWED_INTENSITY = {"low", "medium", "high", "unknown"}
ALLOWED_TIMING_MODELS = {"event_driven", "timer_driven", "unknown"}
