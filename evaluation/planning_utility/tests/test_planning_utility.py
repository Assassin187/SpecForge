from __future__ import annotations

import inspect
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent.coder.llm_client import LLMResponse, LLMUsage
from agent.planning.adapters.target_profile import load_target_profile

from evaluation.planning_utility import baseline_runner, prompts
from evaluation.planning_utility.baseline_runner import (
    FSDirectCoderRunner,
    NLPlanCodeRunner,
    validate_source_tree_skeleton,
)
from evaluation.planning_utility.configs import PROTOCOLS, ProtocolConfig
from evaluation.planning_utility.header_context import (
    HeaderExtraction,
    extract_header_declarations,
    fit_header_context,
)
from evaluation.planning_utility.requirements import build_allowed_inputs, write_json


class FakeLLM:
    def __init__(self) -> None:
        self.pair_calls = 0
        self.plan_prompts: list[str] = []
        self.skeleton_prompts: list[str] = []
        self.pair_prompts: list[str] = []
        self.requests: list[Any] = []

    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: Any) -> LLMResponse:
        self.requests.append(request)
        content = "\n".join(message["content"] for message in request.messages)
        if "source tree skeleton" in content:
            self.skeleton_prompts.append(content)
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
            self.pair_prompts.append(content)
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
            self.plan_prompts.append(content)
            return LLMResponse(
                "# Plan\nFULL_PLAN_DETAIL_ONLY\n\n## Code-generation brief\n- Build a tiny server-shaped C program.\n",
                LLMUsage(10, 5, 15),
            )
        return LLMResponse(
            "#include <stdio.h>\nint main(int argc, char **argv) { (void)argc; (void)argv; puts(\"ok\"); return 0; }\n",
            LLMUsage(30, 20, 50),
        )


class RepairLLM:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: Any) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse("int main(void) { return 0; }\n", LLMUsage(30, 20, 50))


class RetryOnceLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.returned_invalid_pair = False

    def generate_with_usage(self, request: Any) -> LLMResponse:
        content = "\n".join(message["content"] for message in request.messages)
        if "generation_unit" in content and '"paths":["app.h","app.c"]' in content and not self.returned_invalid_pair:
            self.returned_invalid_pair = True
            self.requests.append(request)
            self.pair_calls += 1
            self.pair_prompts.append(content)
            return LLMResponse(
                json.dumps(
                    {
                        "files": [
                            {"path": "app.h", "content": "#ifndef APP_H\n#define APP_H\n#endif\n", "note": "extra"},
                            {"path": "app.c", "content": '#include "app.h"\n'},
                        ]
                    }
                ),
                LLMUsage(30, 20, 50),
            )
        return super().generate_with_usage(request)


class RetrySkeletonOnceLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.returned_invalid_skeleton = False

    def generate_with_usage(self, request: Any) -> LLMResponse:
        content = "\n".join(message["content"] for message in request.messages)
        if "source tree skeleton" in content and not self.returned_invalid_skeleton:
            self.returned_invalid_skeleton = True
            self.requests.append(request)
            self.skeleton_prompts.append(content)
            return LLMResponse('```json\n{"files": []}\n```', LLMUsage(10, 5, 15))
        return super().generate_with_usage(request)


class InvalidMainLLM(FakeLLM):
    def generate_with_usage(self, request: Any) -> LLMResponse:
        content = "\n".join(message["content"] for message in request.messages)
        if "generation_unit" in content and '"kind":"main"' in content:
            self.requests.append(request)
            self.pair_calls += 1
            self.pair_prompts.append(content)
            return LLMResponse('{"files":[', LLMUsage(30, 20, 50))
        return super().generate_with_usage(request)


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

    def test_clang_header_extraction_preserves_complete_public_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            header = root / "api.h"
            header.write_text(
                """/* comment with ; before the guard */
#ifndef API_H
#define API_H
#include <stdint.h>
#define API_SUM(a, b) \\
    ((a) + (b))
struct forward;
typedef enum api_state {
    API_IDLE = 0, /* comment ; must not end the enum */
    API_READY = 1
} api_state_t;
typedef struct api_context {
    int fd;
    union {
        uint32_t number;
        const char *name;
    } value;
} api_context_t;
typedef void (*api_callback)(api_context_t *context, int status);
extern const int api_version;
int api_run(
    api_context_t *context,
    api_callback callback
);
static inline int api_private(void) { return 1; }
#endif
""",
                encoding="utf-8",
            )
            extraction = extract_header_declarations(header, root)
            rendered = "\n\n".join(extraction.blocks)
            self.assertEqual(extraction.status, "parsed")
            self.assertIn("#include <stdint.h>", rendered)
            self.assertNotIn("#define API_H", rendered)
            self.assertIn("#define API_SUM(a, b) \\\n    ((a) + (b))", rendered)
            self.assertIn("struct forward;", rendered)
            self.assertIn("API_READY = 1\n} api_state_t;", rendered)
            self.assertIn("const char *name;\n    } value;\n} api_context_t;", rendered)
            self.assertIn("typedef void (*api_callback)(api_context_t *context, int status);", rendered)
            self.assertIn("extern const int api_version;", rendered)
            self.assertIn("int api_run(\n    api_context_t *context,\n    api_callback callback\n);", rendered)
            self.assertNotIn("api_private", rendered)
            self.assertNotIn("comment ;", rendered)
            self.assertNotIn("typedef unsigned", rendered)

    def test_header_extraction_fallback_and_budget_keep_whole_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            header = root / "fallback.h"
            header.write_text(
                "#ifndef FALLBACK_H\n#define FALLBACK_H\n/* remove me; */\nstruct item { int value; };\n#endif\n",
                encoding="utf-8",
            )
            missing = extract_header_declarations(header, root, clang_path="")
            self.assertEqual(missing.status, "fallback")
            self.assertEqual(missing.blocks, ("struct item { int value; };",))

            invalid_ast = subprocess.CompletedProcess([], 1, stdout="not-json", stderr="bad ast")
            with patch("evaluation.planning_utility.header_context.subprocess.run", return_value=invalid_ast):
                invalid = extract_header_declarations(header, root, clang_path="clang")
            self.assertEqual(invalid.status, "fallback")
            self.assertIn("JSONDecodeError", invalid.reason)

            header.write_text("struct broken { int value;\n", encoding="utf-8")
            damaged = extract_header_declarations(header, root)
            self.assertEqual(damaged.status, "fallback")
            self.assertIn("struct broken { int value;", damaged.blocks[0])

        first = "struct first { int value; };"
        second = "struct second { int value; int extra; };"
        context, diagnostics = fit_header_context(
            [("api.h", HeaderExtraction((first, second), "parsed"))],
            len(first.encode("utf-8")),
        )
        self.assertEqual(context, {"api.h": first})
        self.assertEqual(diagnostics[0]["status"], "omitted_due_budget")
        self.assertNotIn("struct second", context["api.h"])

    def test_json_parsing_and_pair_schema_are_strict(self) -> None:
        for text in ('```json\n{"files": []}\n```', 'prefix {"files": []}', '{"files": []} suffix'):
            with self.subTest(text=text):
                with self.assertRaises(json.JSONDecodeError):
                    baseline_runner._parse_json_response(text)
        with self.assertRaisesRegex(ValueError, "top-level keys"):
            FSDirectCoderRunner._parse_pair_completion_response(
                json.dumps({"files": [{"path": "main.c", "content": "int main(void){return 0;}"}], "note": "x"}),
                ["main.c"],
            )

    def test_pair_json_retries_once_then_records_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = RetryOnceLLM()
            result = FSDirectCoderRunner(
                _toy_config(tmp),
                tmp / "retry_once",
                llm_client=llm,
                max_repair_rounds=0,
            ).run()
            self.assertTrue(result.success)
            self.assertEqual(result.summary["json_retry_count"], 1)
            retried = [item for item in result.summary["json_generation_attempts"] if item["retry_count"]]
            self.assertEqual(len(retried), 1)
            self.assertEqual(retried[0]["status"], "passed")
            self.assertEqual(retried[0]["subject"], "app.h__app.c")
            self.assertTrue(retried[0]["rejection_reasons"])
            self.assertIsNone(result.summary["failed_generation_unit"])
            self.assertEqual(result.summary["generated_files"], ["app.h", "app.c", "main.c"])
            self.assertTrue(result.summary["header_context_diagnostics"])
            self.assertEqual(result.summary["header_context_diagnostics"][0]["status"], "parsed")
            self.assertTrue(llm.requests)
            self.assertTrue(all(request.max_completion_tokens == 16384 for request in llm.requests))

    def test_skeleton_json_retries_once_then_records_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            result = FSDirectCoderRunner(
                _toy_config(tmp),
                tmp / "skeleton_retry",
                llm_client=RetrySkeletonOnceLLM(),
                max_repair_rounds=0,
            ).run()
            self.assertTrue(result.success)
            self.assertEqual(result.summary["json_retry_count"], 1)
            first = result.summary["json_generation_attempts"][0]
            self.assertEqual(first["stage"], "source_tree_skeleton")
            self.assertEqual(first["attempt_count"], 2)
            self.assertEqual(first["status"], "passed")
            self.assertIn("Expecting value", first["rejection_reasons"][0])

    def test_exhausted_json_retry_records_partial_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            runner = FSDirectCoderRunner(
                _toy_config(tmp),
                tmp / "retry_exhausted",
                llm_client=InvalidMainLLM(),
                max_repair_rounds=0,
            )
            result = runner.run()
            self.assertFalse(result.success)
            self.assertEqual(result.summary["source_tree_skeleton_status"], "passed")
            self.assertEqual(result.summary["pair_completion_status"], "failed")
            self.assertEqual(result.summary["compile_status"], "not_run")
            self.assertEqual(result.summary["failure_stage"], "pair_completion")
            self.assertIn("llm_json_error", result.summary["failure_categories"])
            self.assertEqual(result.summary["json_retry_count"], 1)
            self.assertEqual(result.summary["generated_pairs"], [{"kind": "pair", "paths": ["app.h", "app.c"]}])
            self.assertEqual(result.summary["generated_files"], ["app.h", "app.c"])
            self.assertEqual(result.summary["failed_generation_unit"]["paths"], ["main.c"])
            self.assertEqual(result.summary["json_generation_attempts"][-1]["status"], "failed")
            self.assertEqual(result.summary["json_generation_attempts"][-1]["attempt_count"], 2)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["generated_files"], ["app.h", "app.c"])
            self.assertEqual(manifest["failed_generation_unit"]["paths"], ["main.c"])
        with self.assertRaisesRegex(ValueError, "keys must be exactly"):
            FSDirectCoderRunner._parse_pair_completion_response(
                json.dumps({"files": [{"path": "main.c", "content": "int main(void){return 0;}", "note": "x"}]}),
                ["main.c"],
            )

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
        for forbidden_key in ("role", "description", "functions", "types", "dependencies", "interfaces", "behavior"):
            with self.subTest(forbidden_key=forbidden_key):
                self.assertTrue(
                    validate_source_tree_skeleton(
                        {
                            "files": [
                                {"path": "app.h", "kind": "header", "order": 0, forbidden_key: "forbidden"},
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
            direct = FSDirectCoderRunner(config, tmp / "fs", llm_client=fs_llm, max_repair_rounds=3).run()
            self.assertTrue(direct.success)
            self.assertTrue((tmp / "fs" / "coder_out" / "_agent_logs" / "run_manifest.json").is_file())
            self.assertEqual(direct.summary["method"], "fs-direct-coder")
            self.assertEqual(direct.summary["source_tree_skeleton_status"], "passed")
            self.assertEqual(direct.summary["pair_completion_status"], "passed")
            self.assertEqual(direct.summary["repair_iterations"], 0)
            self.assertEqual(direct.summary["repair_stop_reason"], "compile_succeeded")
            self.assertIn("compile", direct.summary["timings"])
            self.assertNotIn("compile_repair", direct.summary["timings"])
            self.assertEqual(fs_llm.pair_calls, 2)
            self.assertTrue(all(request.max_completion_tokens == 16384 for request in fs_llm.requests))
            self.assertFalse((tmp / "fs" / "project_strategy.json").exists())
            self.assertFalse((tmp / "fs" / "nl_plan.md").exists())
            direct_manifest = json.loads(direct.manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(direct_manifest["repair_enabled"])
            self.assertEqual(direct_manifest["max_repair_rounds"], 0)
            skeleton = json.loads((tmp / "fs" / "source_tree_skeleton.json").read_text())
            for item in skeleton["files"]:
                self.assertLessEqual(set(item), {"path", "kind", "order"})

            nl_llm = FakeLLM()
            nl_runner = NLPlanCodeRunner(config, tmp / "nl", llm_client=nl_llm, max_repair_rounds=0)
            nl = nl_runner.run()
            self.assertTrue(nl.success)
            self.assertTrue((tmp / "nl" / "nl_plan.md").is_file())
            self.assertTrue((tmp / "nl" / "source_tree_skeleton.json").is_file())
            self.assertFalse((tmp / "nl" / "project_strategy.json").exists())
            self.assertEqual(nl.summary["method"], "nl-plan-code")
            self.assertEqual(nl.summary["nl_plan_status"], "passed")
            self.assertEqual(nl.summary["nl_plan_guard_status"], "passed")
            self.assertEqual(nl.summary["nl_plan_guard"], {"status": "passed", "findings": []})
            self.assertEqual(nl.summary["source_tree_skeleton_status"], "passed")
            self.assertEqual(nl.summary["pair_completion_status"], "passed")
            self.assertEqual(nl.summary["strategy_generation_status"], "not_applicable")
            self.assertEqual(nl.summary["file_generation_status"], "not_applicable")
            self.assertEqual(nl.summary["generated_pairs"], [{"kind": "pair", "paths": ["app.h", "app.c"]}])
            self.assertEqual(nl.summary["generated_files"], ["app.h", "app.c", "main.c"])
            self.assertEqual(nl.summary["planning_artifact_guard"]["status"], "passed")
            self.assertEqual(set(nl.summary["stage_token_usage"]), {"nl_plan", "source_tree_skeleton", "pair_completion"})
            self.assertEqual(nl_llm.pair_calls, 2)
            self.assertIsNone(nl_llm.requests[0].max_completion_tokens)
            self.assertTrue(all(request.max_completion_tokens == 16384 for request in nl_llm.requests[1:]))

            plan = (tmp / "nl" / "nl_plan.md").read_text(encoding="utf-8")
            normalized_plan = plan.lower()
            with self.assertRaises(json.JSONDecodeError):
                json.loads(plan)
            for forbidden in (
                "```json",
                "```yaml",
                "\n---\n",
                "| ---",
                "project_strategy.json",
                "dependency graph",
                "dependency_graph",
                "type ownership table",
                "module inventory",
                "function inventory",
                "file_spec",
                "function_spec",
            ):
                self.assertNotIn(forbidden, normalized_plan)

            self.assertIn("JSON, YAML, Markdown tables", nl_llm.plan_prompts[0])
            self.assertIn("Natural-language plan brief", nl_llm.skeleton_prompts[0])
            self.assertIn('"nl_plan_brief"', nl_llm.pair_prompts[0])
            self.assertIn("close every JSON string, array, and object", nl_llm.pair_prompts[0])
            self.assertIn("Do not put design deliberation", nl_llm.pair_prompts[0])
            self.assertNotIn("FULL_PLAN_DETAIL_ONLY", nl_llm.skeleton_prompts[0])
            self.assertNotIn("FULL_PLAN_DETAIL_ONLY", nl_llm.pair_prompts[0])
            self.assertNotIn("Natural-language plan brief", fs_llm.skeleton_prompts[0])
            self.assertNotIn('"nl_plan_brief"', fs_llm.pair_prompts[0])

            manifest = json.loads((tmp / "nl" / "coder_out" / "_agent_logs" / "run_manifest.json").read_text())
            self.assertEqual(manifest["nl_plan_guard_status"], "passed")
            self.assertEqual(manifest["nl_plan_guard"], {"status": "passed", "findings": []})

            (tmp / "nl" / "project_strategy.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(nl_runner._planning_artifact_guard()["status"], "failed")

    def test_nl_plan_reuses_fs_direct_generation_and_verification_methods(self) -> None:
        for method_name in (
            "_run_skeleton_pair_completion",
            "_generate_source_tree_skeleton",
            "_generate_pair_completion",
            "_run_static_checks",
            "_compile_and_repair",
            "_run_behavior",
        ):
            with self.subTest(method_name=method_name):
                self.assertIs(getattr(NLPlanCodeRunner, method_name), getattr(FSDirectCoderRunner, method_name))

    def test_existing_source_repair_flow_can_still_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _toy_config(tmp)
            repair_llm = RepairLLM()
            runner = FSDirectCoderRunner(config, tmp / "repair", llm_client=repair_llm, max_repair_rounds=1)
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
            self.assertEqual(len(repair_llm.requests), 1)
            self.assertIsNone(repair_llm.requests[0].max_completion_tokens)

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
        self.assertFalse(hasattr(baseline_runner, "validate_project_strategy"))
        self.assertFalse(hasattr(prompts, "build_strategy_messages"))
        self.assertFalse(hasattr(prompts, "build_file_messages"))


if __name__ == "__main__":
    unittest.main()
