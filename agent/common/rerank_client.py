from __future__ import annotations

from .llm_client import LLMRequest, LLMResponse, LLMUsage


class QwenRerankClient:
    def __init__(self, llm_client: object) -> None:
        self.llm_client = llm_client

    def rerank(self, messages: list[dict[str, str]]) -> str:
        return self.rerank_with_usage(messages).content

    def rerank_with_usage(self, messages: list[dict[str, str]]) -> LLMResponse:
        generate_with_usage = getattr(self.llm_client, "generate_with_usage", None)
        if callable(generate_with_usage):
            return generate_with_usage(
                LLMRequest(
                    messages=messages,
                    top_p=0.1,
                    temperature=0.1,
                    is_stream=True,
                )
            )
        generate = getattr(self.llm_client, "generate", None)
        if not callable(generate):
            raise RuntimeError("llm_client does not provide a callable generate() method")
        response = generate(
            LLMRequest(
                messages=messages,
                top_p=0.1,
                temperature=0.1,
                is_stream=True,
            )
        )
        if not isinstance(response, str):
            raise RuntimeError(f"Expected string response from llm_client.generate(), got {type(response)!r}")
        return LLMResponse(content=response, usage=LLMUsage(0, 0, 0))
