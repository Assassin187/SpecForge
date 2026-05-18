from __future__ import annotations

from dataclasses import dataclass


FACTS_INPUT_FORMAT_VERSION = "protocol_facts/v2alpha1"
TARGET_PROFILE_FORMAT_VERSION = "target_profile/v1"
CODER_OUTPUT_FORMAT_VERSION = "spec_bundle/current"
PROMPT_VERSION = "planning/prompts/v0"


@dataclass(frozen=True)
class PlanningConfig:
    llm_temperature: float = 0.2
    llm_top_p: float = 0.5
    llm_max_completion_tokens: int = 16384
    llm_max_retries: int = 3
    facts_input_format_version: str = FACTS_INPUT_FORMAT_VERSION
    target_profile_format_version: str = TARGET_PROFILE_FORMAT_VERSION
    coder_output_format_version: str = CODER_OUTPUT_FORMAT_VERSION
    prompt_version: str = PROMPT_VERSION
