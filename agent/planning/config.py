from __future__ import annotations

from dataclasses import dataclass, field


FACTS_INPUT_FORMAT_VERSION = "protocol_facts/v2alpha1"
TARGET_PROFILE_FORMAT_VERSION = "target_profile/v1"
CODER_OUTPUT_FORMAT_VERSION = "spec_bundle/current"
PROMPT_VERSION = "planning/prompts/v0"

LLM_STAGE_CONFIG_COMPAT_ALIASES = {
    "implementation_plan_5_2a": ("implementation_plan_5_2",),
    "implementation_plan_5_2b": ("implementation_plan_5_3",),
    "implementation_plan_5_3": ("implementation_plan_5_4a",),
    "implementation_plan_5_4a": ("implementation_plan_5_4b",),
    "implementation_plan_5_4b": ("implementation_plan_5_4c",),
    "implementation_plan_5_4c": ("implementation_plan_5_4d",),
    "implementation_plan_5_4d": ("implementation_plan_5_4e",),
    "implementation_plan_5_4e": ("implementation_plan_5_4f",),
    "implementation_plan_5_5a": ("implementation_plan_5_5",),
    "implementation_plan_5_5b": ("implementation_plan_5_4g",),
}


@dataclass(frozen=True)
class LLMStageConfig:
    temperature: float | None = None
    top_p: float | None = None
    max_completion_tokens: int | None = None
    max_retries: int | None = None
    enable_thinking: bool | None = None


def default_llm_stage_configs() -> dict[str, LLMStageConfig]:
    return {
        "protocol_profile": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "architecture_candidate_high_variance": LLMStageConfig(temperature=0.7, enable_thinking=True),
        "architecture_candidate_low_variance": LLMStageConfig(temperature=0.2, enable_thinking=True),
        "architecture_ranking": LLMStageConfig(temperature=0.2, enable_thinking=True),
        "implementation_plan_5_1": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_2a": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_2b": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=True),
        "implementation_plan_5_3": LLMStageConfig(temperature=0.3, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4a": LLMStageConfig(temperature=0.3, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4b": LLMStageConfig(temperature=0.2, max_retries=2, enable_thinking=False),
        "implementation_plan_5_4c": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4d": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4e": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_5a": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_5b": LLMStageConfig(temperature=0.2, max_retries=1, enable_thinking=False),
        "implementation_plan_5_6": LLMStageConfig(temperature=0.2, max_retries=1, enable_thinking=False),
        "implementation_plan_5_7": LLMStageConfig(temperature=0.2, max_retries=1, enable_thinking=False),
        "implementation_plan_5_2": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4f": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_5": LLMStageConfig(temperature=0.2, max_retries=3, enable_thinking=False),
        "implementation_plan_5_4g": LLMStageConfig(temperature=0.2, max_retries=1, enable_thinking=False),
    }


@dataclass(frozen=True)
class PlanningConfig:
    llm_temperature: float = 0.2
    llm_top_p: float = 0.5
    llm_max_completion_tokens: int = 16384
    llm_max_retries: int = 3
    llm_stage_configs: dict[str, LLMStageConfig] = field(default_factory=default_llm_stage_configs)
    facts_input_format_version: str = FACTS_INPUT_FORMAT_VERSION
    target_profile_format_version: str = TARGET_PROFILE_FORMAT_VERSION
    coder_output_format_version: str = CODER_OUTPUT_FORMAT_VERSION
    prompt_version: str = PROMPT_VERSION

    def llm_stage_config(self, stage: str) -> LLMStageConfig:
        default = default_llm_stage_configs().get(stage, LLMStageConfig())
        override = self.llm_stage_configs.get(stage)
        if override is None:
            for alias in LLM_STAGE_CONFIG_COMPAT_ALIASES.get(stage, ()):
                override = self.llm_stage_configs.get(alias)
                if override is not None:
                    break
        if override is None:
            return default
        return LLMStageConfig(
            temperature=default.temperature if override.temperature is None else override.temperature,
            top_p=default.top_p if override.top_p is None else override.top_p,
            max_completion_tokens=default.max_completion_tokens if override.max_completion_tokens is None else override.max_completion_tokens,
            max_retries=default.max_retries if override.max_retries is None else override.max_retries,
            enable_thinking=default.enable_thinking if override.enable_thinking is None else override.enable_thinking,
        )

    def llm_temperature_for(self, stage: str) -> float:
        value = self.llm_stage_config(stage).temperature
        return self.llm_temperature if value is None else value

    def llm_top_p_for(self, stage: str) -> float:
        value = self.llm_stage_config(stage).top_p
        return self.llm_top_p if value is None else value

    def llm_max_completion_tokens_for(self, stage: str) -> int:
        value = self.llm_stage_config(stage).max_completion_tokens
        return self.llm_max_completion_tokens if value is None else value

    def llm_max_retries_for(self, stage: str) -> int:
        value = self.llm_stage_config(stage).max_retries
        return self.llm_max_retries if value is None else value

    def llm_enable_thinking_for(self, stage: str) -> bool:
        return bool(self.llm_stage_config(stage).enable_thinking)
