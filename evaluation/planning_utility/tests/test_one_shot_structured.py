from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.common.llm_client import LLMResponse, LLMUsage
from evaluation.planning_utility.configs import ProtocolConfig
from evaluation.planning_utility.one_shot_structured import (
    METHOD_ONE_SHOT,
    ONE_SHOT_MAX_COMPLETION_TOKENS,
    _generate_planning_run,
    run_one_shot_structured,
)
from evaluation.planning_utility.requirements import read_json, write_json
from evaluation.planning_utility.run_matrix import METHODS


class _FakeClient:
    response = ""
    requests = []

    def __init__(self, _api_key_env: str) -> None:
        return None

    def generate_with_usage(self, request):
        self.__class__.requests.append(request)
        return LLMResponse(self.__class__.response, LLMUsage(11, 22, 33))


def _config(root: Path) -> ProtocolConfig:
    facts = root / "facts.json"
    profile = root / "profile.json"
    write_json(facts, {"protocol_meta": {"protocol_name": "toy"}, "minimum_v1": {}})
    write_json(
        profile,
        {
            "target_role": "server",
            "language": "C",
            "runtime": "Linux",
            "scope": "minimum_v1",
            "deployment_constraints": {},
        },
    )
    return ProtocolConfig("toy", facts, profile, "toy_app", "./toy_app <port>", "tcp")


class OneShotStructuredTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.requests = []

    def _context_patches(self):
        return (
            patch("evaluation.planning_utility.one_shot_structured.normalize_characteristics", return_value={}),
            patch("evaluation.planning_utility.one_shot_structured.activate_engineering_rules", return_value=[]),
            patch("evaluation.planning_utility.one_shot_structured.extract_open_assumptions", return_value=[]),
            patch(
                "evaluation.planning_utility.one_shot_structured.build_planning_context",
                return_value={"facts": {"protocol_meta": {"protocol_name": "toy"}}, "target_profile_visible_to_planner": False},
            ),
        )

    def test_one_shot_calls_model_once_and_compiles_plan(self) -> None:
        _FakeClient.response = json.dumps({"implementation_plan": {"schema_version": "specforge_planning_ir_v1"}})
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _config(root)

            def fake_compile(plan, specs_root, *, clean):
                specs_root.mkdir(parents=True)
                write_json(specs_root / "toy_module_spec.json", {"KIND": "PROTOCOL_MODULE_SPEC"})
                write_json(specs_root / "toy_file_spec.json", {"KIND": "FILE_SPEC"})
                write_json(specs_root / "toy_function_spec.json", {"KIND": "FUNCTION_SPEC"})
                (specs_root / "SUMMARY.md").write_text("# toy\n", encoding="utf-8")
                mapping = specs_root / "planning_semantic_mapping.json"
                write_json(mapping, {})
                return {
                    "module_spec": str(specs_root / "toy_module_spec.json"),
                    "summary": str(specs_root / "SUMMARY.md"),
                    "semantic_mapping": str(mapping),
                    "written_files": [str(specs_root / "toy_module_spec.json")],
                    "diagnostics": [],
                }

            patches = self._context_patches()
            with patch("evaluation.planning_utility.one_shot_structured.FixedQwenClient", _FakeClient), \
                 patch("evaluation.planning_utility.one_shot_structured.normalize_plan_for_compiler", side_effect=lambda plan: plan), \
                 patch("evaluation.planning_utility.one_shot_structured.compile_specs", side_effect=fake_compile) as compiler, \
                 patches[0], patches[1], patches[2], patches[3]:
                _generate_planning_run(config, root / "run", api_key_env="ALI_API")

            manifest = read_json(root / "run" / "_planning" / "run_manifest.json")
            self.assertEqual(manifest["run_status"], "completed_with_one_shot_specs")
            self.assertEqual(manifest["logical_planning_call_count"], 1)
            self.assertFalse(manifest["planning_validator_used"])
            self.assertEqual(len(_FakeClient.requests), 1)
            self.assertEqual(_FakeClient.requests[0].max_completion_tokens, ONE_SHOT_MAX_COMPLETION_TOKENS)
            compiler.assert_called_once()

    def test_invalid_envelope_fails_without_compiler_retry(self) -> None:
        _FakeClient.response = json.dumps({"wrong": {}})
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _config(root)
            patches = self._context_patches()
            with patch("evaluation.planning_utility.one_shot_structured.FixedQwenClient", _FakeClient), \
                 patch("evaluation.planning_utility.one_shot_structured.compile_specs") as compiler, \
                 patches[0], patches[1], patches[2], patches[3]:
                _generate_planning_run(config, root / "run", api_key_env="ALI_API")

            manifest = read_json(root / "run" / "_planning" / "run_manifest.json")
            self.assertEqual(manifest["fatal_reason_code"], "one_shot_invalid_envelope")
            self.assertEqual(manifest["content_retry_count"], 0)
            self.assertEqual(len(_FakeClient.requests), 1)
            compiler.assert_not_called()

    def test_matrix_registers_one_shot_method(self) -> None:
        self.assertEqual(METHOD_ONE_SHOT, "one-shot-structured-planning")
        self.assertIn(METHOD_ONE_SHOT, METHODS)

    def test_one_shot_reuses_full_specforge_coder_repair_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _config(root)
            with patch("evaluation.planning_utility.one_shot_structured._generate_planning_run"), \
                 patch(
                     "evaluation.planning_utility.one_shot_structured.run_full_specforge",
                     return_value={"method": METHOD_ONE_SHOT},
                 ) as adapter:
                summary = run_one_shot_structured(config, root / "out", api_key_env="ALI_API", max_repair_rounds=4)

            self.assertEqual(summary["planning_call_count"], 1)
            self.assertFalse(summary["planning_validator_used"])
            self.assertEqual(adapter.call_args.kwargs["method"], METHOD_ONE_SHOT)
            self.assertTrue(adapter.call_args.kwargs["skip_planning_validate"])
            self.assertTrue(adapter.call_args.kwargs["planning_is_fresh"])
            self.assertEqual(adapter.call_args.kwargs["max_repair_rounds"], 4)


if __name__ == "__main__":
    unittest.main()
