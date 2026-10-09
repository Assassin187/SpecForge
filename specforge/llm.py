"""Shared Chat Completions adapter with selectable model profiles."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields

from openai import OpenAI


@dataclass(frozen=True)
class ModelConfig:
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    key_env: str = "DS_API"
    reasoning_effort: str = "high"
    max_tokens: int = 65536
    extra_body: dict = field(default_factory=lambda: {"thinking": {"type": "enabled"}})

    def record(self) -> dict:
        record = asdict(self)
        # Existing experiment consumers compare the frozen DeepSeek record.
        if self.extra_body == {"thinking": {"type": "enabled"}}:
            record.pop("extra_body")
        return {**record, "thinking": "enabled", "stream": False}

    @classmethod
    def from_record(cls, record: dict) -> ModelConfig:
        return cls(**{item.name: record[item.name] for item in fields(cls) if item.name in record})


# Add future API-compatible models here with their endpoint, key and request body.
MODEL_CONFIGS = {
    "deepseek-flash": ModelConfig(),
    "qwen3.8-flash": ModelConfig(model="qwen3.8-flash",
                                 base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                                 key_env="ALI_API", extra_body={"enable_thinking": True}),
    "gpt-6.1-sol": ModelConfig(model="gpt-6.1-sol", base_url="http://172.16.0.160:50199/v1",
                               key_env="DES_CODEX_API", extra_body={}),
}


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
        queue_errors = []
        for attempt in range(3):
            response = self.client.chat.completions.create(
                model=self.config.model, messages=messages, tools=tools, stream=False,
                max_tokens=self.config.max_tokens, reasoning_effort=self.config.reasoning_effort,
                extra_body=self.config.extra_body,
            )
            raw = response.model_dump()
            choices = raw.get("choices")
            if (isinstance(choices, list) and choices and isinstance(choices[0], dict)
                    and isinstance(choices[0].get("message"), dict)):
                break
            error = raw.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            # Non-streaming keep-alive responses can carry this error without
            # an HTTP failure, so the SDK's existing retries do not apply.
            if (attempt < 2 and isinstance(message, str)
                    and "unable to start processing your request" in message
                    and "timeout limit" in message):
                queue_errors.append(raw)
                print(f"  model API queue timeout: retrying unprocessed request ({attempt + 1}/2)", flush=True)
                continue
            raise RuntimeError("Invalid model API response (missing completion choice/message): "
                               + json.dumps(raw, ensure_ascii=False)[:4000])
        choice = choices[0]
        message = choice["message"]
        assistant = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            assistant["tool_calls"] = [{"id": t["id"], "type": "function", "function": t["function"]}
                                       for t in message["tool_calls"]]
        if message.get("reasoning_content") is not None:
            assistant["reasoning_content"] = message["reasoning_content"]
        usage = raw.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cache_hit = usage.get("prompt_cache_hit_tokens")
        if cache_hit is None:
            cache_hit = prompt_details.get("cached_tokens")
        cache_miss = usage.get("prompt_cache_miss_tokens")
        if cache_miss is None and cache_hit is not None and usage.get("prompt_tokens") is not None:
            cache_miss = usage["prompt_tokens"] - cache_hit
        return {"message": assistant, "finish_reason": choice["finish_reason"], "response": raw,
                "queue_errors": queue_errors,
                "usage": {"input_tokens": usage.get("prompt_tokens"),
                          "cache_hit_tokens": cache_hit,
                          "cache_miss_tokens": cache_miss,
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

