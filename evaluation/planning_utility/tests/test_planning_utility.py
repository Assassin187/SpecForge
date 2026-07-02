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
from evaluation.planning_utility.baseline_runner import (
    FSDirectCoderRunner,
    NLPlanCodeRunner,
    validate_project_strategy,
    validate_source_tree_skeleton,
)
from evaluation.planning_utility.configs import PROTOCOLS, ProtocolConfig
from evaluation.planning_utility.requirements import build_allowed_inputs, write_json


class FakeLLM:
    def __init__(self) -> None:
        self.pair_calls = 0

    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: Any) -> LLMResponse:
        content = request.messages[-1]["content"]
        if "source tree skeleton" in content:
            return LLMResponse(
                json.dumps(
                    {
                        "files": [
                            {"path": "app.h", "kind": "header", "order": 0},
                            {"path": "app.c", "kind": "source", "order": 1},
                            {"path": "main.c", "kind": "main", "order": 2},
                        ]
                    }
                ),
                LLMUsage(10, 5, 15),
            )
        if "generation_unit" in content:
            self.pair_calls += 1
            if '"kind":"main"' in content:
                return LLMResponse(
                    json.dumps(
                        {
                            "files": [
                                {
                                    "path": "main.c",
                                    "content": '#include "app.h"\nint main(int argc, char **argv) { return app_run(argc, argv); }\n',
                                }
                            ]
                        }
                    ),
                    LLMUsage(30, 20, 50),
                )
            return LLMResponse(
                json.dumps(
                    {
                        "files": [
                            {"path": "app.h", "content": "#ifndef APP_H\n#define APP_H\nint app_run(int argc, char **argv);\n#endif\n"},
                            {
                                "path": "app.c",
                                "content": '#include "app.h"\n#include <stdio.h>\nint app_run(int argc, char **argv) { (void)argc; (void)argv; puts("ok"); return 0; }\n',
                            },
                        ]
                    }
                ),
                LLMUsage(30, 20, 50),
            )
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


class RepairLLM:
    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: Any) -> LLMResponse:
        return LLMResponse("int main(void) { return 0; }\n", LLMUsage(30, 20, 50))


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

    def test_source_tree_skeleton_validation_rejects_planning_fields(self) -> None:
        self.assertFalse(
            validate_source_tree_skeleton(
                {
                    "files": [
                        {"path": "app.h", "kind": "header", "order": 0},
                        {"path": "app.c", "kind": "source", "order": 1},
                        {"path": "main.c", "kind": "main", "order": 2},
                    ]
                }
            )
        )
        self.assertTrue(
            validate_source_tree_skeleton(
                {
                    "files": [
                        {"path": "app.h", "kind": "header", "order": 0, "interfaces": ["app_run"]},
                        {"path": "app.c", "kind": "source", "order": 1},
                        {"path": "main.c", "kind": "main", "order": 2},
                    ]
                }
            )
        )

    def test_fs_direct_and_nl_runners_write_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _toy_config(tmp)
            fs_llm = FakeLLM()
            direct = FSDirectCoderRunner(config, tmp / "fs", llm_client=fs_llm, max_repair_rounds=0).run()
            self.assertTrue(direct.success)
            self.assertTrue((tmp / "fs" / "coder_out" / "_agent_logs" / "run_manifest.json").is_file())
            self.assertEqual(direct.summary["method"], "fs-direct-coder")
            self.assertEqual(direct.summary["source_tree_skeleton_status"], "passed")
            self.assertEqual(direct.summary["pair_completion_status"], "passed")
            self.assertEqual(fs_llm.pair_calls, 2)
            self.assertFalse((tmp / "fs" / "project_strategy.json").exists())
            self.assertFalse((tmp / "fs" / "nl_plan.md").exists())
            skeleton = json.loads((tmp / "fs" / "source_tree_skeleton.json").read_text())
            for item in skeleton["files"]:
                self.assertLessEqual(set(item), {"path", "kind", "order"})

            nl = NLPlanCodeRunner(config, tmp / "nl", llm_client=FakeLLM(), max_repair_rounds=0).run()
            self.assertTrue(nl.success)
            self.assertTrue((tmp / "nl" / "nl_plan.md").is_file())
            self.assertTrue((tmp / "nl" / "project_strategy.json").is_file())
            self.assertEqual(nl.summary["nl_plan_status"], "passed")

    def test_existing_source_repair_flow_can_still_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _toy_config(tmp)
            runner = FSDirectCoderRunner(config, tmp / "repair", llm_client=RepairLLM(), max_repair_rounds=1)
            runner._prepare_output_dir()
            (runner.project_dir / "main.c").write_text("int main(void) { return missing_symbol(); }\n", encoding="utf-8")
            (runner.project_dir / "Makefile").write_text(
                "CC ?= gcc\nCFLAGS ?= -std=c11 -Wall -Werror=implicit-function-declaration -I.\n"
                "TARGET ?= toy_app\nSRCS = main.c\nall: $(TARGET)\n$(TARGET): $(SRCS)\n\t$(CC) $(CFLAGS) -o $@ $(SRCS)\n",
                encoding="utf-8",
            )
            result, stop_reason, rounds, blocking_files, rejected = runner._compile_and_repair()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(stop_reason, "compile_succeeded")
            self.assertEqual(rounds, 1)
            self.assertFalse(blocking_files)
            self.assertFalse(rejected)

    def test_baseline_source_avoids_forbidden_spec_imports(self) -> None:
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
