#!/usr/bin/env python3
"""Run the official MetaGPT SOP with SpecForge's model configuration."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# MetaGPT creates a metagpt/tools/schemas namespace in its output root.
# Put the real checkout first so subsequent runs keep importing the package.
sys.path[:0] = [str(ROOT / "MetaGPT"), str(ROOT.parents[2])]
from specforge.llm import ModelConfig

MODEL = ModelConfig()
SMOKE = sys.argv[1:] == ["--smoke"]
CHECK = sys.argv[1:] == ["--check"]
OFFLINE = CHECK or sys.argv[1:] == ["--help"]
key = os.environ.get(MODEL.key_env)
if not key and not OFFLINE:
    raise SystemExit(f"Set {MODEL.key_env}, the same key used by SpecForge.")

# Keep MetaGPT's files and configuration inside this baseline directory.
os.environ.setdefault("METAGPT_PROJECT_ROOT", str(ROOT))
import metagpt.const as metagpt_const

metagpt_const.CONFIG_ROOT = ROOT / "config"
from metagpt.config2 import Config, config
from metagpt.configs.llm_config import LLMConfig, LLMType

config.llm = LLMConfig(
    api_type=LLMType.OPENAI,
    model=MODEL.model,
    base_url=MODEL.base_url,
    api_key=key or "sk-offline-check",
    max_token=1024 if SMOKE else MODEL.max_tokens,
    stream=False,
    timeout=240,
)
# Roles and Actions also call Config.default() internally.
Config.default = classmethod(lambda cls: config)

# MetaGPT prices are USD per 1K tokens. Use the published peak cache-miss
# rates as a conservative estimate; actual billing can be lower.
# Source (2026-10-08): https://api-docs.deepseek.com/quick_start/pricing/
from metagpt.utils.token_counter import TOKEN_COSTS

TOKEN_COSTS[MODEL.model] = {"prompt": 0.0003, "completion": 0.0012}

from metagpt.provider.llm_provider_registry import register_provider
from metagpt.provider.openai_api import OpenAILLM


@register_provider(LLMType.OPENAI)
class SpecForgeLLM(OpenAILLM):
    """Supply the DeepSeek parameters absent from MetaGPT v0.8.2."""

    def _cons_kwargs(self, messages, timeout=0, **extra_kwargs):
        kwargs = super()._cons_kwargs(messages, timeout=timeout, **extra_kwargs)
        kwargs.pop("temperature")
        kwargs["extra_body"] = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": MODEL.reasoning_effort,
        }
        return kwargs

    def _make_client_kwargs(self):
        kwargs = super()._make_client_kwargs()
        if SMOKE:
            kwargs["max_retries"] = 0
        return kwargs

    async def _achat_completion(self, messages, timeout=0):
        response = await super()._achat_completion(messages, timeout=timeout)
        if SMOKE:
            self.last_response = response
        return response

    async def acompletion_text(self, messages, stream=False, timeout=0):
        # The smoke test bypasses the framework's connection retries as well.
        if SMOKE:
            response = await self._achat_completion(messages, timeout=timeout)
            return self.get_choice_text(response)
        return await super().acompletion_text(messages, stream=False, timeout=timeout)


def check():
    """Import and initialize the standard team without sending requests."""
    from metagpt.context import Context
    from metagpt.roles import Architect, Engineer, ProductManager, ProjectManager, QaEngineer
    from metagpt.software_company import generate_repo
    from metagpt.team import Team

    roles = [ProductManager(), Architect(), ProjectManager(), Engineer(), QaEngineer()]
    company = Team(context=Context(config=config))
    company.hire(roles)
    for role in roles:
        for item in [role, *role.actions]:
            llm = item.llm
            kwargs = llm._cons_kwargs([{"role": "user", "content": "offline check"}])
            assert isinstance(llm, SpecForgeLLM), type(llm)
            assert kwargs["model"] == MODEL.model
            assert str(llm.aclient.base_url).rstrip("/") == MODEL.base_url.rstrip("/")
            assert llm.config.api_key == (key or "sk-offline-check")
            assert kwargs["max_tokens"] == MODEL.max_tokens
            assert kwargs["extra_body"] == {
                "thinking": {"type": "enabled"}, "reasoning_effort": MODEL.reasoning_effort
            }
            assert not llm.config.stream
    assert callable(generate_repo)
    assert MODEL.model in company.cost_manager.token_costs
    report = {
        "passed": True,
        "real_llm_requests": 0,
        "python": sys.version,
        "upstream_commit": subprocess.check_output(
            ["git", "-C", str(ROOT / "MetaGPT"), "rev-parse", "HEAD"], text=True
        ).strip(),
        "specforge_model": MODEL.record(),
        "roles_and_actions_checked": [type(role).__name__ for role in roles],
        "credentials_source": MODEL.key_env,
        "mermaid_engine": config.mermaid.engine,
    }
    (ROOT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


async def smoke():
    """One real, short native WriteCode call; no retries or full team run."""
    from metagpt.actions.write_code import WriteCode
    from metagpt.context import Context

    output = ROOT / "smoke" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    prompt = (
        "Write only a C99 function int add(int a, int b) that returns a + b. "
        "Return one fenced c code block. No main function, no explanation."
    )
    action = WriteCode(context=Context(config=config))
    try:
        # Call the original action body once, bypassing its six-attempt decorator.
        code = await WriteCode.write_code.__wrapped__(action, prompt)
        response = action.llm.last_response.model_dump()
        (output / "response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n")
        (output / "add.c").write_text(code + "\n")
        usage = response["usage"]
        report = {
            "passed": response["choices"][0]["finish_reason"] == "stop" and bool(code.strip()),
            "real_llm_requests": 1,
            "request": {
                "model": MODEL.model,
                "base_url": MODEL.base_url,
                "thinking": "enabled",
                "reasoning_effort": MODEL.reasoning_effort,
                "stream": False,
                "max_tokens": config.llm.max_token,
                "prompt": prompt,
            },
            "response_model": response["model"],
            "usage": usage,
            "estimated_usd_upper_bound": round(
                (usage["prompt_tokens"] * 0.3 + usage["completion_tokens"] * 1.2) / 1_000_000, 9
            ),
            "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
            "pricing_checked_date": "2026-10-08",
            "output": str(output),
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["passed"]:
            raise RuntimeError("Smoke completion was empty or truncated; see response.json.")
    finally:
        await action.llm.aclient.close()


if __name__ == "__main__":
    if CHECK:
        check()
    elif SMOKE:
        asyncio.run(smoke())
    else:
        from metagpt.software_company import app

        app()
