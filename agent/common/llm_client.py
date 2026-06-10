from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, OpenAI, OpenAIError


FIXED_MODEL = "qwen3-max-2026-01-23"
# FIXED_MODEL = "qwen3.7-max-2026-05-20"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_API_KEY_ENV = "ALI_API"
DEFAULT_RETRY_ATTEMPTS = 10
DEFAULT_RETRY_DELAY = 2


@dataclass
class LLMRequest:
    messages: list[dict[str, str]]
    top_p: float
    temperature: float
    is_stream: bool = True
    enable_thinking: bool = False
    max_completion_tokens: int | None = None


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: LLMUsage


def _coerce_usage(usage_obj: Any) -> LLMUsage:
    if usage_obj is None:
        return LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage_obj, "total_tokens", 0) or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _is_retryable_openai_error(exc: OpenAIError) -> bool:
    if isinstance(exc, APIConnectionError):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        return False
    if status_code in {408, 409, 429}:
        return True
    return int(status_code) >= 500


def chat_with_llm_with_usage(
    model: str,
    messages: list[dict[str, str]],
    top_p: float = 0.5,
    temperature: float = 0.5,
    is_stream: bool = True,
    enable_thinking: bool = False,
    max_completion_tokens: int | None = None,
    delay: int = DEFAULT_RETRY_DELAY,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    api_key_env: str = DEFAULT_API_KEY_ENV,
) -> LLMResponse:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is not set in the environment.")

    client = OpenAI(
        api_key=api_key,
        base_url=QWEN_BASE_URL,
    )

    for attempt in range(attempts):
        try:
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "top_p": top_p,
                "temperature": temperature,
                "stream": is_stream,
                "extra_body": {"enable_thinking": enable_thinking},
            }
            if max_completion_tokens is not None:
                request_kwargs["max_tokens"] = max_completion_tokens
            if is_stream:
                request_kwargs["stream_options"] = {"include_usage": True}
            response = client.chat.completions.create(**request_kwargs)

            if is_stream:
                answer_content = ""
                usage = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
                for chunk in response:
                    if getattr(chunk, "usage", None) is not None:
                        usage = _coerce_usage(chunk.usage)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if getattr(delta, "content", None):
                        answer_content += delta.content
                return LLMResponse(content=answer_content, usage=usage)

            content = response.choices[0].message.content
            return LLMResponse(
                content=content if isinstance(content, str) else "",
                usage=_coerce_usage(getattr(response, "usage", None)),
            )
        except OpenAIError as exc:
            if _is_retryable_openai_error(exc) and attempt < attempts - 1:
                print(f"Model response request failed (attempt {attempt + 1}/{attempts}), retrying in {delay} seconds...")
                print(f"Error message: {type(exc).__name__}: {exc}")
                time.sleep(delay)
            else:
                print("Model response request failed, about to throw exception and exit...")
                raise


def chat_with_llm(
    model: str,
    messages: list[dict[str, str]],
    top_p: float = 0.5,
    temperature: float = 0.5,
    is_stream: bool = True,
    enable_thinking: bool = False,
    max_completion_tokens: int | None = None,
    delay: int = DEFAULT_RETRY_DELAY,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    api_key_env: str = DEFAULT_API_KEY_ENV,
) -> str:
    return chat_with_llm_with_usage(
        model,
        messages,
        top_p=top_p,
        temperature=temperature,
        is_stream=is_stream,
        enable_thinking=enable_thinking,
        max_completion_tokens=max_completion_tokens,
        delay=delay,
        attempts=attempts,
        api_key_env=api_key_env,
    ).content


class FixedQwenClient:
    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self.api_key_env = api_key_env

    def ensure_ready(self) -> None:
        if not os.getenv(self.api_key_env):
            raise RuntimeError(f"{self.api_key_env} is not set in the environment.")

    def generate(self, request: LLMRequest) -> str:
        return self.generate_with_usage(request).content

    def generate_with_usage(self, request: LLMRequest) -> LLMResponse:
        self.ensure_ready()
        response = chat_with_llm_with_usage(
            FIXED_MODEL,
            request.messages,
            top_p=request.top_p,
            temperature=request.temperature,
            is_stream=request.is_stream,
            enable_thinking=request.enable_thinking,
            max_completion_tokens=request.max_completion_tokens,
            api_key_env=self.api_key_env,
        )
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from chat_with_llm_with_usage, got {type(response)!r}")
        return response

    def self_check(self) -> dict[str, Any]:
        self.ensure_ready()
        return {
            "model": FIXED_MODEL,
            "base_url": QWEN_BASE_URL,
            "api_key_env": self.api_key_env,
            "api_key_set": bool(os.getenv(self.api_key_env)),
        }
