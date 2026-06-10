from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.coder.cli import build_parser as build_coder_parser
from agent.common.llm_client import QWEN_BASE_URL, FixedQwenClient, chat_with_llm_with_usage
from agent.facts.cli import build_parser as build_facts_parser
from agent.planning.cli import build_parser as build_planning_parser


ALTERNATE_API_KEY_ENV = "ALI_API_2"


class ApiKeyCliTests(unittest.TestCase):
    def test_three_stage_commands_accept_api_key_env(self) -> None:
        facts = build_facts_parser().parse_args(
            ["extract", "--protocol-name", "coap", "--doc", "rfc7252.txt", "--api-key-env", ALTERNATE_API_KEY_ENV]
        )
        planning = build_planning_parser().parse_args(
            ["plan", "--facts", "facts.json", "--target-profile", "target.json", "--api-key-env", ALTERNATE_API_KEY_ENV]
        )
        coder = build_coder_parser().parse_args(["--api-key-env", ALTERNATE_API_KEY_ENV, "generate"])

        self.assertEqual(facts.api_key_env, ALTERNATE_API_KEY_ENV)
        self.assertEqual(planning.api_key_env, ALTERNATE_API_KEY_ENV)
        self.assertEqual(coder.api_key_env, ALTERNATE_API_KEY_ENV)

    def test_selected_api_key_env_is_used(self) -> None:
        with patch.dict(os.environ, {ALTERNATE_API_KEY_ENV: "test-key"}, clear=True):
            status = FixedQwenClient(ALTERNATE_API_KEY_ENV).self_check()

        self.assertEqual(status["api_key_env"], ALTERNATE_API_KEY_ENV)
        self.assertTrue(status["api_key_set"])
        self.assertEqual(status["base_url"], QWEN_BASE_URL)

    def test_missing_selected_api_key_env_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, ALTERNATE_API_KEY_ENV):
                FixedQwenClient(ALTERNATE_API_KEY_ENV).self_check()

    def test_selected_key_uses_fixed_dashscope_url(self) -> None:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )
        with patch.dict(os.environ, {ALTERNATE_API_KEY_ENV: "test-key"}, clear=True):
            with patch("agent.common.llm_client.OpenAI") as openai:
                openai.return_value.chat.completions.create.return_value = response
                chat_with_llm_with_usage("test-model", [], is_stream=False, attempts=1, api_key_env=ALTERNATE_API_KEY_ENV)

        openai.assert_called_once_with(api_key="test-key", base_url=QWEN_BASE_URL)


if __name__ == "__main__":
    unittest.main()
