from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.coder.llm_client import LLMResponse, LLMUsage
from agent.planning.adapters.target_profile import load_target_profile

from evaluation.planning_utility import baseline_runner, prompts
from evaluation.planning_utility.baseline_runner import DirectCodeAgentRunner, NLPlanCodeRunner, validate_project_strategy
from evaluation.planning_utility.configs import PROTOCOLS, ProtocolConfig
from evaluation.planning_utility.requirements import build_allowed_inputs, write_json


class FakeLLM:
    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: Any) -> LLMResponse:
        content = request.messages[-1]["content"]
        if "natural-language engineering plan" in content:
            return LLMResponse("# Plan\nBuild a tiny server-shaped C program.\n", LLMUsage(10, 5, 15))
        if "project strategy" in content:
            return LLMResponse(
                json.dumps(
                    {
                        "modules": [{"name": "app", "purpose": "entrypoint", "depends_on": []}],
                        "files": [{"path": "main.c", "kind": "source", "purpose": "entrypoint", "interfaces": ["main"]}],
                        "entrypoint": {"binary": "toy_app", "argv": "./toy_app <port>"},
                        "build": {"language": "C11", "target": "toy_app"},
                    }
                ),
                LLMUsage(20, 10, 30),
            )
        return LLMResponse(
            "#include <stdio.h>\nint main(int argc, char **argv) { (void)argc; (void)argv; puts(\"ok\"); return 0; }\n",
            LLMUsage(30, 20, 50),
        )


def _toy_config(tmp: Path) -> ProtocolConfig:
    facts_path = tmp / "protocol_facts.json"
    target_path = tmp / "target_profile.json"
    write_json(
        facts_path,
        {
            "protocol_meta": {"protocol_name": "toy", "source_documents": ["/home/ljf/SpecForge/specs-example/x"]},
            "minimum_v1": {"must_support_surface": [{"name": "RUN", "summary": "program starts"}]},
        },
    )
    write_json(
        target_path,
        {
            "target_role": "server",
            "language": "C",
            "runtime": "Linux",
            "scope": "minimum_v1",
            "deployment_constraints": {"tls_mode": "none"},
        },
    )
    return ProtocolConfig(
        protocol="toy",
        facts_path=facts_path,
        target_profile_path=target_path,
        binary_name="toy_app",
        argv_contract="./toy_app <port>",
        transport="tcp",
    )


class PlanningUtilityTests(unittest.TestCase):
    def test_protocol_configs_and_profiles_exist(self) -> None:
        self.assertEqual(set(PROTOCOLS), {"http", "mqtt", "coap", "smtp"})
        for config in PROTOCOLS.values():
            self.assertTrue(config.facts_path.is_file(), config.facts_path)
            self.assertTrue(config.target_profile_path.is_file(), config.target_profile_path)
            profile, diagnostics = load_target_profile(config.target_profile_path)
            self.assertIsNotNone(profile)
            self.assertFalse([diag for diag in diagnostics if diag.level == "error"])

    def test_minimum_requirements_are_extracted_and_sanitized(self) -> None:
        for config in PROTOCOLS.values():
            allowed = build_allowed_inputs(
                config.facts_path,
                config.target_profile_path,
                {"binary_name": config.binary_name, "argv_contract": config.argv_contract},
            )
            requirements = allowed["minimum_requirements"]
            self.assertGreater(requirements["surface_count"], 0)
            serialized = json.dumps(allowed["facts_view"], ensure_ascii=False)
            self.assertNotIn("specs-example", serialized)

    def test_strategy_path_validation(self) -> None:
        self.assertFalse(validate_project_strategy({"files": [{"path": "main.c"}]}))
        self.assertTrue(validate_project_strategy({"files": [{"path": "/tmp/main.c"}]}))
        self.assertTrue(validate_project_strategy({"files": [{"path": "../main.c"}]}))
        self.assertTrue(validate_project_strategy({"files": [{"path": "main.py"}]}))

    def test_direct_and_nl_runners_write_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _toy_config(tmp)
            direct = DirectCodeAgentRunner(config, tmp / "direct", llm_client=FakeLLM(), max_repair_rounds=0).run()
            self.assertTrue(direct.success)
            self.assertTrue((tmp / "direct" / "coder_out" / "_agent_logs" / "run_manifest.json").is_file())
            self.assertEqual(direct.summary["strategy_generation_status"], "passed")
            self.assertEqual(direct.summary["compile_status"], "passed")

            nl = NLPlanCodeRunner(config, tmp / "nl", llm_client=FakeLLM(), max_repair_rounds=0).run()
            self.assertTrue(nl.success)
            self.assertTrue((tmp / "nl" / "nl_plan.md").is_file())
            self.assertEqual(nl.summary["nl_plan_status"], "passed")

    def test_baseline_source_avoids_forbidden_direct_imports(self) -> None:
        source = inspect.getsource(baseline_runner) + inspect.getsource(prompts)
        forbidden = (
            "agent.planning",
            "agent.coder.specs",
            "load_spec_bundle",
            "discover_module_spec",
            "validate_rendered_headers_compile",
            "build_source_prompt",
            "build_repair_prompt",
        )
        for term in forbidden:
            self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()
