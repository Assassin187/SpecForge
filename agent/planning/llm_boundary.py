from __future__ import annotations

import json
from typing import Any

from .config import PlanningConfig
from .diagnostics import PlanningDiagnostic


def extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        raw = json.loads(stripped)
        return raw if isinstance(raw, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def request_json_candidate(
    *,
    prompt_name: str,
    messages: list[dict[str, str]],
    config: PlanningConfig,
    temperature: float | None = None,
) -> tuple[dict[str, Any] | None, list[PlanningDiagnostic], dict[str, Any]]:
    try:
        from agent.common.llm_client import FIXED_MODEL, chat_with_llm_with_usage

        response = chat_with_llm_with_usage(
            FIXED_MODEL,
            messages=messages,
            top_p=config.llm_top_p,
            temperature=config.llm_temperature if temperature is None else temperature,
            is_stream=True,
            enable_thinking=True,
            max_completion_tokens=config.llm_max_completion_tokens,
            attempts=1,
        )
    except Exception as exc:  # noqa: BLE001
        return None, [PlanningDiagnostic("warning", "llm_request_failed", f"{prompt_name} failed: {exc}")], {
            "enabled": True,
            "prompt_name": prompt_name,
            "temperature": config.llm_temperature if temperature is None else temperature,
            "failed": True,
        }

    candidate = extract_json_object(response.content)
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    meta = {
        "enabled": True,
        "prompt_name": prompt_name,
        "usage": usage,
        "content_length": len(response.content),
        "temperature": config.llm_temperature if temperature is None else temperature,
        "raw_response": response.content,
        "hit_completion_limit": bool(config.llm_max_completion_tokens and response.usage.completion_tokens >= config.llm_max_completion_tokens),
    }
    if candidate is None:
        reason = f"{prompt_name} did not return a JSON object"
        if meta["hit_completion_limit"]:
            reason += f"; completion hit max_completion_tokens={config.llm_max_completion_tokens}, likely truncated"
        return None, [PlanningDiagnostic("warning", "invalid_llm_json", reason)], meta
    return candidate, [], meta
