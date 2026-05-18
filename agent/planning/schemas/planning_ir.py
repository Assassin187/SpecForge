from __future__ import annotations


SCHEMA_VERSION = "planning_ir/v1"
TARGET_DIRECTIVES_SCHEMA_VERSION = "target_directives/v1"


FACT_SECTION_EXCLUSIONS = {"evidence_index", "open_questions", "planning_inputs"}


REQUIRED_FACT_SECTIONS = {
    "transport",
    "interaction_model",
    "message_model",
    "state_model",
    "routing_model",
    "resource_model",
    "error_and_limits",
    "minimum_v1",
    "open_questions",
    "evidence_index",
}
