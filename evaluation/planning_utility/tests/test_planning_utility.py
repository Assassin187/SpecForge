from __future__ import annotations

import inspect
import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent.coder.llm_client import LLMResponse, LLMUsage
from evaluation.planning_utility import baseline_runner, full_specforge_adapter, prompts, repair_cli
from evaluation.planning_utility.baseline_runner import (
    FSDirectCoderRunner,
    NLPlanCodeRunner,
    validate_source_tree_skeleton,
)
from evaluation.planning_utility.bounded_repair import BoundedCRepairRunner, RepairConfig
from evaluation.planning_utility.configs import PROTOCOLS, ProtocolConfig
from evaluation.planning_utility.header_context import (
    HeaderExtraction,
    extract_header_declarations,
    fit_header_context,
)
from evaluation.planning_utility.requirements import build_allowed_inputs, load_target_profile, write_json


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
        return LLMResponse(
            "--- main.c\n+++ main.c\n@@ -1 +1 @@\n-int main(void) { return missing_symbol(); }\n+int main(void) { return 0; }\n",
            LLMUsage(30, 20, 50),
        )


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


def _planning_fixture(
    root: Path,
    *,
    qualified: bool,
    specs_generated: bool = True,
    fatal_reason_code: str | None = None,
) -> Path:
    planning_root = root / "_planning"
    specs_root = planning_root / "candidate_planning_package" / "specs"
    if specs_generated:
        specs_root.mkdir(parents=True)
    manifest = {
        "kind": "PLANNING_RUN_MANIFEST",
        "run_status": (
            "failed_internal"
            if fatal_reason_code
            else "completed_with_qualified_specs"
            if qualified
            else "completed_with_candidate_only"
        ),
        "facts_path": str(root / "protocol_facts.json"),
        "qualification_passed": qualified,
        "planning_validation_passed": qualified,
        "coder_loader_passed": specs_generated,
        "specs_generated": specs_generated,
        "specs_root": str(specs_root) if specs_generated else None,
        "candidate_specs_root": str(specs_root) if specs_generated else None,
        "candidate_root": str(planning_root / "candidate_planning_package"),
        "fatal": fatal_reason_code is not None,
        "fatal_reason_code": fatal_reason_code,
        "hard_failure_code": fatal_reason_code,
        "diagnostic_counts": {"error": 0 if qualified else 1, "warning": 0},
        "token_accounting": {"total_tokens": 17},
        "written_files": [],
    }
    write_json(planning_root / "run_manifest.json", manifest)
    write_json(
        planning_root / "diagnostics.json",
        [] if qualified else [{"level": "error", "code": "fixture_gap", "message": "fixture is not qualified"}],
    )
    return root


class PlanningUtilityTests(unittest.TestCase):
    def test_protocol_configs_and_profiles_exist(self) -> None:
        self.assertEqual(set(PROTOCOLS), {"mqtt"})
        for config in PROTOCOLS.values():
            self.assertTrue(config.facts_path.is_file(), config.facts_path)
            self.assertTrue(config.target_profile_path.is_file(), config.target_profile_path)
            profile = load_target_profile(config.target_profile_path)
            self.assertEqual(profile["target_role"], "broker")
            self.assertEqual(profile["scope"], "minimum_v1")

    def test_target_profile_failures_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            with self.assertRaisesRegex(ValueError, "target_profile_missing"):
                load_target_profile(root / "missing.json")
            malformed = root / "malformed.json"
            malformed.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "target_profile_invalid_json"):
                load_target_profile(malformed)
            incomplete = root / "incomplete.json"
            write_json(incomplete, {"target_role": "broker"})
            with self.assertRaisesRegex(ValueError, "target_profile_missing_fields"):
                load_target_profile(incomplete)

    def test_full_specforge_candidate_is_preserved_but_formally_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            config = _toy_config(root / "inputs")
            source = _planning_fixture(root / "source", qualified=False)
            original_manifest = (source / "_planning" / "run_manifest.json").read_bytes()
            commands: list[list[str]] = []

            def fake_run(cmd, *, log_dir, name):
                del log_dir
                commands.append(cmd)
                return {
                    "name": name,
                    "command": cmd,
                    "returncode": 0,
                    "started_at": "start",
                    "ended_at": "end",
                    "stdout_path": "stdout",
                    "stderr_path": "stderr",
                    "stdout": "",
                    "stderr": "",
                }

            with patch("evaluation.planning_utility.full_specforge_adapter._run_command", side_effect=fake_run):
                summary = full_specforge_adapter.run_full_specforge(
                    config,
                    root / "out",
                    api_key_env="UNUSED",
                    max_repair_rounds=0,
                    existing_planning_dir=source,
                )

            self.assertEqual(summary["failure_stage"], "planning_qualification")
            self.assertEqual(summary["planning_status"], "candidate_only")
            self.assertFalse(summary["qualification_passed"])
            self.assertTrue(summary["specs_generated"])
            self.assertEqual(summary["nonfatal_no_specs_count"], 0)
            self.assertFalse(summary["fatal"])
            self.assertFalse(summary["target_profile_visible_to_planner"])
            self.assertEqual(len(commands), 1)
            self.assertIn("--run-dir", commands[0])
            self.assertNotIn("--target-profile", commands[0])
            self.assertNotIn("verify", commands[0])
            self.assertFalse(any("agent.coder" in command for command in commands))
            self.assertEqual((source / "_planning" / "run_manifest.json").read_bytes(), original_manifest)

    def test_full_specforge_uses_manifest_specs_root_and_current_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            config = _toy_config(root / "inputs")
            source = _planning_fixture(root / "source", qualified=True)
            commands: list[list[str]] = []

            def fake_run(cmd, *, log_dir, name):
                del log_dir
                commands.append(cmd)
                return {
                    "name": name,
                    "command": cmd,
                    "returncode": int("agent.coder" in cmd),
                    "started_at": "start",
                    "ended_at": "end",
                    "stdout_path": "stdout",
                    "stderr_path": "stderr",
                    "stdout": "",
                    "stderr": "fixture coder stop",
                }

            with patch("evaluation.planning_utility.full_specforge_adapter._run_command", side_effect=fake_run):
                summary = full_specforge_adapter.run_full_specforge(
                    config,
                    root / "out",
                    api_key_env="UNUSED",
                    max_repair_rounds=0,
                    existing_planning_dir=source,
                )

            self.assertEqual(summary["failure_stage"], "coder_validate")
            self.assertTrue(summary["qualification_passed"])
            self.assertEqual(len(commands), 2)
            coder_command = commands[1]
            self.assertIn("agent.coder", coder_command)
            self.assertIn(str(root / "out" / "planning_run" / "_planning" / "candidate_planning_package" / "specs"), coder_command)
            serialized = json.dumps(commands)
            for stale in ("--target-profile", "--output-dir\", \"planning_run", "verify", "spec_bundle"):
                self.assertNotIn(stale, serialized)

    def test_full_specforge_distinguishes_fatal_and_nonfatal_no_specs(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            config = _toy_config(root / "inputs")

            def fake_run(cmd, *, log_dir, name):
                del log_dir
                return {
                    "name": name,
                    "command": cmd,
                    "returncode": 1,
                    "started_at": "start",
                    "ended_at": "end",
                    "stdout_path": "stdout",
                    "stderr_path": "stderr",
                    "stdout": "",
                    "stderr": "fixture failure",
                }

            for name, fatal_reason, expected_stage, expected_count in (
                ("nonfatal", None, "planning_stability", 1),
                ("fatal", "candidate_serialization_impossible", "planning_fatal", 0),
            ):
                source = _planning_fixture(
                    root / name,
                    qualified=False,
                    specs_generated=False,
                    fatal_reason_code=fatal_reason,
                )
                with patch("evaluation.planning_utility.full_specforge_adapter._run_command", side_effect=fake_run):
                    summary = full_specforge_adapter.run_full_specforge(
                        config,
                        root / f"out_{name}",
                        api_key_env="UNUSED",
                        max_repair_rounds=0,
                        existing_planning_dir=source,
                    )
                self.assertEqual(summary["failure_stage"], expected_stage)
                self.assertEqual(summary["nonfatal_no_specs_count"], expected_count)
                self.assertEqual(summary["fatal"], fatal_reason is not None)

    def test_full_specforge_source_uses_only_current_planning_contract(self) -> None:
        source = inspect.getsource(full_specforge_adapter)
        for stale in ("--target-profile", '"verify"', "spec_bundle"):
            self.assertNotIn(stale, source)
        for current in ('"--out"', '"--run-dir"', 'manifest.get("specs_root")', '"qualification_passed"'):
            self.assertIn(current, source)

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

    def test_repair_prompts_use_concrete_issue_descriptions(self) -> None:
        diagnostics = [
            {
                "category": "C2",
                "phase": "compile",
                "path": "main.c",
                "symbol": "run_server",
                "message": "implicit declaration of function run_server",
                "planning_dependent": False,
            }
        ]
        messages = prompts.build_source_repair_messages(
            argv_contract="./toy_app <port>",
            target_path="main.c",
            target_content="int main(void) { return run_server(); }\n",
            related_headers={"app.h": "int run_server(int port);\n"},
            related_snippets={"main.c": "1: int main(void) { return run_server(); }"},
            diagnostics=diagnostics,
            classification={"category": "C2", "planning_dependent": False},
            allow_pair=False,
        )
        text = "\n".join(item["content"] for item in messages)
        for term in ("C1", "C2", "C3", "C4", "C5", "Forbidden fixes", "Out-of-scope", "module ownership", "message model", "state model"):
            self.assertNotIn(term, text)
        self.assertIn("local C syntax and build defects", text)
        self.assertIn("header/include/declaration visibility defects", text)
        self.assertIn("function/signature/linkage mismatches", text)

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
            self.assertEqual(direct.summary["repair_stop_reason"], "completed")
            self.assertIn("bounded_repair", direct.summary["timings"])
            self.assertTrue((tmp / "fs" / "coder_out" / "_agent_logs" / "repair_summary.json").is_file())
            self.assertEqual(fs_llm.pair_calls, 2)
            self.assertTrue(all(request.max_completion_tokens == 16384 for request in fs_llm.requests))
            self.assertFalse((tmp / "fs" / "project_strategy.json").exists())
            self.assertFalse((tmp / "fs" / "nl_plan.md").exists())
            direct_manifest = json.loads(direct.manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(direct_manifest["repair_enabled"])
            self.assertEqual(direct_manifest["max_repair_calls"], 6)
            self.assertEqual(direct_manifest["repair"]["bounded_generic_c_repair"]["link_after_repair"], "passed")
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
            "_run_bounded_repair",
        ):
            with self.subTest(method_name=method_name):
                self.assertIs(getattr(NLPlanCodeRunner, method_name), getattr(FSDirectCoderRunner, method_name))

    def test_existing_project_repair_flow_can_run_independently(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _toy_config(tmp)
            repair_llm = RepairLLM()
            project_dir = tmp / "coder_out" / "toy"
            project_dir.mkdir(parents=True)
            (project_dir / "main.c").write_text("int main(void) { return missing_symbol(); }\n", encoding="utf-8")
            (project_dir / "Makefile").write_text(
                "CC ?= gcc\nCFLAGS ?= -std=c11 -Wall -Werror=implicit-function-declaration -I.\n"
                "TARGET ?= toy_app\nSRCS = main.c\nall: $(TARGET)\n$(TARGET): $(SRCS)\n\t$(CC) $(CFLAGS) -o $@ $(SRCS)\n",
                encoding="utf-8",
            )
            summary = BoundedCRepairRunner(
                RepairConfig(
                    project_dir=project_dir,
                    method="fs-direct-coder",
                    protocol=config.protocol,
                    binary_name=config.binary_name,
                    argv_contract=config.argv_contract,
                    output_summary_path=tmp / "repair_summary.json",
                    max_repair_calls=1,
                ),
                llm_client=repair_llm,
            ).run()
            self.assertEqual(summary["link_after_repair"], "passed")
            self.assertEqual(summary["repair_stop_reason"], "completed")
            self.assertEqual(summary["llm_repair_calls"], 1)
            self.assertEqual(len(repair_llm.requests), 1)
            self.assertIsNone(repair_llm.requests[0].max_completion_tokens)

    def test_deterministic_c1_include_repair(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            project_dir = tmp / "coder_out" / "toy"
            project_dir.mkdir(parents=True)
            (project_dir / "main.c").write_text(
                "int main(void) { char *p = malloc(4); free(p); return 0; }\n",
                encoding="utf-8",
            )
            (project_dir / "Makefile").write_text(
                "CC ?= gcc\nCFLAGS ?= -std=c11 -Wall -Werror=implicit-function-declaration -I.\n"
                "TARGET ?= toy_app\nSRCS = main.c\nall: $(TARGET)\n$(TARGET): $(SRCS)\n\t$(CC) $(CFLAGS) -o $@ $(SRCS)\n",
                encoding="utf-8",
            )
            summary = BoundedCRepairRunner(
                RepairConfig(
                    project_dir=project_dir,
                    method="nl-plan-code",
                    protocol="toy",
                    binary_name="toy_app",
                    argv_contract="./toy_app <port>",
                    output_summary_path=tmp / "repair_summary.json",
                    max_repair_calls=0,
                ),
                llm_client=RepairLLM(),
            ).run()
            self.assertEqual(summary["llm_repair_calls"], 0)
            self.assertEqual(summary["link_after_repair"], "passed")
            self.assertIn("#include <stdlib.h>", (project_dir / "main.c").read_text(encoding="utf-8"))

    def test_repair_cli_copies_existing_project_before_modifying(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source_project_dir = tmp / "source" / "coder_out" / "toy"
            source_project_dir.mkdir(parents=True)
            (source_project_dir / "main.c").write_text(
                "int main(void) { char *p = malloc(4); free(p); return 0; }\n",
                encoding="utf-8",
            )
            (source_project_dir / "Makefile").write_text(
                "CC ?= gcc\nCFLAGS ?= -std=c11 -Wall -Werror=implicit-function-declaration -I.\n"
                "TARGET ?= toy_app\nSRCS = main.c\nall: $(TARGET)\n$(TARGET): $(SRCS)\n\t$(CC) $(CFLAGS) -o $@ $(SRCS)\n",
                encoding="utf-8",
            )
            run_root = tmp / "out" / "20260706_120000"
            requested_summary = tmp / "requested_repair_summary.json"

            result = repair_cli._run_one(
                {
                    "project_dir": str(source_project_dir),
                    "method": "fs-direct-coder",
                    "protocol": "toy",
                    "binary_name": "toy_app",
                    "argv_contract": "./toy_app <port>",
                    "output_summary_path": str(requested_summary),
                    "repair_run_root": str(run_root),
                },
                api_key_env="ALI_API",
                max_repair_calls=0,
                dry_run=False,
            )

            copied_project_dir = run_root / "toy" / "fs-direct-coder" / "coder_out" / "toy"
            self.assertEqual(result["status"], "ok")
            self.assertTrue(copied_project_dir.is_dir())
            self.assertNotIn("#include <stdlib.h>", (source_project_dir / "main.c").read_text(encoding="utf-8"))
            self.assertIn("#include <stdlib.h>", (copied_project_dir / "main.c").read_text(encoding="utf-8"))
            self.assertEqual(result["repair_project_dir"], str(copied_project_dir))
            self.assertEqual(result["canonical_summary_path"], str(copied_project_dir.parent / "_agent_logs" / "repair_summary.json"))
            self.assertFalse(requested_summary.exists())
            self.assertEqual(result["summary"]["source_project_dir"], str(source_project_dir))
            self.assertEqual(result["summary"]["project_dir"], str(copied_project_dir))
            self.assertEqual(result["summary"]["repair_run_root"], str(run_root))

    def test_repair_cli_batches_projects_under_one_generated_layout(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            run_root = tmp / "out" / "20260706_120000"
            rows = [
                {
                    "project_dir": str(tmp / "src1" / "coder_out" / "toy"),
                    "method": "fs-direct-coder",
                    "protocol": "toy",
                    "binary_name": "toy_app",
                    "argv_contract": "./toy_app <port>",
                    "output_summary_path": str(tmp / "outside.json"),
                },
                {
                    "project_dir": str(tmp / "src2" / "coder_out" / "toy"),
                    "method": "nl-plan-code",
                    "protocol": "toy",
                    "binary_name": "toy_app",
                    "argv_contract": "./toy_app <port>",
                },
            ]
            prepared = repair_cli._prepare_repair_rows(rows, run_root)
            self.assertEqual({row["repair_run_root"] for row in prepared}, {str(run_root)})
            self.assertEqual(prepared[0]["repair_method_dir"], "fs-direct-coder")
            self.assertEqual(prepared[1]["repair_method_dir"], "nl-plan-code")
            self.assertNotIn("output_summary_path", prepared[0])
            self.assertEqual(
                run_root / prepared[0]["protocol"] / prepared[0]["repair_method_dir"] / "coder_out" / prepared[0]["protocol"],
                run_root / "toy" / "fs-direct-coder" / "coder_out" / "toy",
            )

    def test_repair_cli_uses_repair_round_and_nested_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            rows = [
                {
                    "project_dir": str(
                        tmp
                        / "20260703_mqtt_no_repair_round_2"
                        / "20260703_123939"
                        / "mqtt"
                        / "fs-direct-coder"
                        / "coder_out"
                        / "mqtt"
                    ),
                    "method": "fs-direct-coder",
                    "protocol": "mqtt",
                    "binary_name": "mqtt_broker",
                    "argv_contract": "./mqtt_broker <port>",
                }
            ]
            now = datetime(2026, 7, 6, 15, 32, 28)
            with patch("evaluation.planning_utility.repair_cli.DEFAULT_OUTPUT_ROOT", tmp / "out"):
                run_root = repair_cli._fresh_run_root(rows, now)
            self.assertEqual(
                run_root,
                tmp / "out" / "20260706_mqtt_after_repair_round_2" / "20260706_153228",
            )

    def test_incomplete_source_tree_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            project_dir = tmp / "broken"
            project_dir.mkdir()
            summary = BoundedCRepairRunner(
                RepairConfig(
                    project_dir=project_dir,
                    method="fs-direct-coder",
                    protocol="toy",
                    binary_name="toy_app",
                    argv_contract="./toy_app <port>",
                    output_summary_path=tmp / "repair_summary.json",
                    max_repair_calls=6,
                ),
                llm_client=RepairLLM(),
            ).run()
            self.assertEqual(summary["repair_stop_reason"], "incomplete_source_tree")
            self.assertTrue((tmp / "repair_diagnostics.json").is_file())

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
        self.assertFalse(hasattr(baseline_runner.BaselineProjectRunner, "_compile_and_repair"))
        self.assertFalse(hasattr(prompts, "build_strategy_messages"))
        self.assertFalse(hasattr(prompts, "build_file_messages"))
        self.assertFalse(hasattr(prompts, "build_repair_messages"))


if __name__ == "__main__":
    unittest.main()
