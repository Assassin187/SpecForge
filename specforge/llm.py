"""Thin DeepSeek Chat Completions adapter; no second orchestration layer."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from openai import OpenAI


@dataclass(frozen=True)
class ModelConfig:
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    key_env: str = "DS_API"
    reasoning_effort: str = "high"
    max_tokens: int = 32768

    def record(self) -> dict:
        return {**self.__dict__, "thinking": "enabled", "stream": False}


class LLM:
    def __init__(self, config: ModelConfig | None = None, client=None):
        self.config = config or ModelConfig()
        if client is None:
            key = os.environ.get(self.config.key_env)
            if not key:
                raise RuntimeError(f"Set {self.config.key_env} before running a model stage")
            client = OpenAI(api_key=key, base_url=self.config.base_url, timeout=240, max_retries=2)
        self.client = client

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        started = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.config.model, messages=messages, tools=tools, stream=False,
            max_tokens=self.config.max_tokens, reasoning_effort=self.config.reasoning_effort,
            extra_body={"thinking": {"type": "enabled"}},
        )
        raw = response.model_dump()
        choice = raw["choices"][0]
        message = choice["message"]
        assistant = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            assistant["tool_calls"] = [{"id": t["id"], "type": "function", "function": t["function"]}
                                       for t in message["tool_calls"]]
        if message.get("reasoning_content") is not None:
            assistant["reasoning_content"] = message["reasoning_content"]
        usage = raw.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return {"message": assistant, "finish_reason": choice["finish_reason"], "response": raw,
                "usage": {"input_tokens": usage.get("prompt_tokens"),
                          "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                          "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
                          "output_tokens": usage.get("completion_tokens"),
                          "reasoning_tokens": details.get("reasoning_tokens"),
                          "requests": 1, "elapsed_seconds": round(time.monotonic() - started, 3)}}


def total_usage(entries: list[dict]) -> dict:
    result = {}
    for name in ("input_tokens", "cache_hit_tokens", "cache_miss_tokens", "output_tokens", "reasoning_tokens"):
        values = [e.get(name) for e in entries]
        result[name] = sum(values) if values and all(v is not None for v in values) else None
    result["requests"] = len(entries)
    result["elapsed_seconds"] = round(sum(e.get("elapsed_seconds", 0) for e in entries), 3)
    # Reasoning tokens are a subset of output tokens and are not added again.
    return result

