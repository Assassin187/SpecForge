from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openai import OpenAIError

from agent.coder import generation
from agent.coder.generation import (
    ProjectGenerator,
    _compact_compile_diagnostics,
    _validate_repair_candidate,
    render_header,
)
from agent.coder.llm_client import LLMRequest, LLMResponse, LLMUsage
from agent.coder.models import FileSpec, ModuleEntry, ProtocolMeta, SpecBundle
from agent.coder.verifier import VerificationResult
from agent.common.llm_client import _is_retryable_openai_error


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[LLMRequest] = []

    def ensure_ready(self) -> None:
        return None

    def generate_with_usage(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(self.content, LLMUsage(0, 0, 0))


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["make"], returncode, stdout, stderr)


def _file_spec(source_path: str, header_path: str) -> FileSpec:
    return FileSpec(
        trace_id="coap/protocol/file_coap_message",
        role="test file",
        lang="C",
        header_path=header_path,
        source_path=source_path,
        header_dependencies=[],
        header_system_dependencies=[],
        source_dependencies=[header_path],
        header_data=[],
        source_data=[],
        header_interfaces=[],
        source_interfaces=[],
        spec_path=Path("coap_message_spec.json"),
        raw={"PUBLIC_SYMBOLS": []},
    )


def _bundle(spec_root: Path) -> SpecBundle:
    file_spec = _file_spec("protocol/coap_message.c", "protocol/coap_message.h")
    module = ModuleEntry(
        name="protocol",
        role="test protocol",
        dependencies=[],
        files=["protocol/coap_message.h", "protocol/coap_message.c"],
        artifacts=[],
        doc_ref=[],
        raw={},
    )
    return SpecBundle(
        protocol=ProtocolMeta("coap", "1", ["server"]),
        module_spec_path=spec_root / "coap_module_spec.json",
        spec_root=spec_root,
        generation_order=["protocol"],
        modules_in_order=[module],
        file_specs_by_trace={file_spec.trace_id: file_spec},
        file_specs_by_header_path={file_spec.header_path: file_spec},
        file_specs_by_source_path={file_spec.source_path: file_spec},
        function_specs_by_trace={},
        consistency_rules=[],
        diagnostics=[],
    )


class CoderRepairTests(unittest.TestCase):
    def _generator(self, tmp: Path, llm: FakeLLM) -> ProjectGenerator:
        generator = ProjectGenerator(_bundle(tmp), llm, tmp / "out", max_repair_rounds=3)
        generator.project_dir.mkdir(parents=True)
        (generator.project_dir / "protocol").mkdir()
        (generator.project_dir / "protocol/coap_message.h").write_text("#pragma once\n", encoding="utf-8")
        (generator.project_dir / "protocol/coap_message.c").write_text("int broken;\n", encoding="utf-8")
        return generator

    def test_header_error_blocks_repair_without_llm_or_header_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int changed;\n")
            generator = self._generator(tmp, llm)
            original_header = (generator.project_dir / "protocol/coap_message.h").read_text(encoding="utf-8")
            fail = _completed(1, stderr="protocol/coap_message.h:1:1: error: bad header\n")

            with patch("agent.coder.generation._compile_project", return_value=fail):
                outcome = generator._repair_until_compiles([], "coap_server")

            self.assertEqual(outcome.stop_reason, "deterministic_header_compile_error")
            self.assertEqual(outcome.blocking_files, ["protocol/coap_message.h"])
            self.assertEqual(llm.requests, [])
            self.assertEqual((generator.project_dir / "protocol/coap_message.h").read_text(encoding="utf-8"), original_header)
            self.assertFalse(list((tmp / "out/_agent_logs").glob("*repair_prompt_*_*.h.txt")))

    def test_source_repair_prompt_target_matches_written_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            fixed = '#include "protocol/coap_message.h"\nint fixed(void) { return 0; }\n'
            llm = FakeLLM(fixed)
            generator = self._generator(tmp, llm)
            fail = _completed(1, stderr="protocol/coap_message.c:3:1: error: bad source\n")
            success = _completed(0)

            with patch("agent.coder.generation._compile_project", side_effect=[fail, success]):
                outcome = generator._repair_until_compiles([], "coap_server")

            self.assertEqual(outcome.stop_reason, "compile_succeeded")
            self.assertEqual(len(llm.requests), 1)
            prompt = llm.requests[0].messages[1]["content"]
            self.assertIn("Repair the source file `protocol/coap_message.c`", prompt)
            self.assertIn("Canonical header content:\n#pragma once", prompt)
            self.assertEqual((generator.project_dir / "protocol/coap_message.c").read_text(encoding="utf-8"), fixed)

    def test_repair_request_size_guard_skips_llm(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int changed;\n")
            generator = self._generator(tmp, llm)
            fail = _completed(1, stderr="protocol/coap_message.c:3:1: error: bad source\n")

            with patch("agent.coder.generation._compile_project", return_value=fail):
                with patch.object(generation, "MAX_REPAIR_REQUEST_BYTES", 100):
                    outcome = generator._repair_until_compiles([], "coap_server")

            self.assertEqual(outcome.stop_reason, "repair_request_too_large")
            self.assertEqual(llm.requests, [])
            self.assertTrue(list((tmp / "out/_agent_logs").glob("*repair_prompt_rejected_size_*")))

    def test_compact_diagnostics_caps_bytes_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            project_dir = Path(raw_tmp)
            stderr = "\n".join(
                f"protocol/coap_message.c:{idx}:1: error: bad source {idx}\n    detail {idx}"
                for idx in range(200)
            )
            compact = _compact_compile_diagnostics("", stderr, "protocol/coap_message.c", project_dir, max_bytes=1024)

        self.assertLessEqual(len(compact.encode("utf-8")), 1024)
        self.assertIn("diagnostics truncated", compact)

    def test_repair_candidate_validation_rejects_header_like_and_self_include(self) -> None:
        self.assertEqual(_validate_repair_candidate("protocol/coap_message.c", "old", "#pragma once\n")[1], "header_like_response")
        self.assertEqual(
            _validate_repair_candidate(
                "protocol/coap_message.c",
                "old",
                '#include "protocol/coap_message.c"\nint x;\n',
            )[1],
            "self_include_response",
        )
        self.assertEqual(_validate_repair_candidate("protocol/coap_message.c", "same\n", "same\n")[1], "unchanged_response")

    def test_render_header_supports_system_dependencies_and_array_members(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            bundle = _bundle(tmp)
            file_spec = _file_spec("protocol/coap_message.c", "protocol/coap_message.h")
            file_spec = FileSpec(
                **{
                    **file_spec.__dict__,
                    "header_system_dependencies": ["sys/socket.h"],
                    "header_data": [
                        {"NAME": "COAP_MAX_TOKEN_LEN", "KIND": "MACRO", "VISIBILITY": "PUBLIC", "VALUE": "8"},
                        {
                            "NAME": "coap_message_t",
                            "KIND": "TYPE",
                            "VISIBILITY": "PUBLIC",
                            "TYPE_SPEC": {
                                "TYPE_KIND": "STRUCT",
                                "FIELDS": [
                                    {"NAME": "token", "TYPE": "uint8_t", "ARRAY_LEN": "COAP_MAX_TOKEN_LEN"},
                                ],
                            },
                        },
                    ],
                }
            )

            rendered = render_header(bundle, file_spec)

        self.assertIn("#include <sys/socket.h>", rendered)
        self.assertIn("#define COAP_MAX_TOKEN_LEN 8", rendered)
        self.assertIn("uint8_t token[COAP_MAX_TOKEN_LEN];", rendered)

    def test_dependency_headers_expand_project_quoted_includes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = ProjectGenerator(_bundle(tmp), FakeLLM(""), tmp / "out")
            generator.project_dir.mkdir(parents=True)
            (generator.project_dir / "protocol").mkdir()
            (generator.project_dir / "server").mkdir()
            (generator.project_dir / "server/server.h").write_text('#include "protocol/coap_codec.h"\n', encoding="utf-8")
            (generator.project_dir / "protocol/coap_codec.h").write_text('#include "protocol/coap_message.h"\n', encoding="utf-8")
            (generator.project_dir / "protocol/coap_message.h").write_text("typedef int coap_message_t;\n", encoding="utf-8")

            headers = generator._dependency_headers(["server/server.h"])

        self.assertIn("server/server.h", headers)
        self.assertIn("protocol/coap_codec.h", headers)
        self.assertIn("protocol/coap_message.h", headers)

    def test_behavior_failure_does_not_fail_compiled_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = ProjectGenerator(_bundle(tmp), FakeLLM("int generated;\n"), tmp / "out")
            behavior = VerificationResult(
                False,
                [],
                scenarios=[{"name": "coap_get_hello", "status": "failed", "detail": "bad response"}],
            )

            with patch("agent.coder.generation._compile_project", return_value=_completed(0)):
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior", return_value=behavior):
                    result = generator.generate()

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertTrue(result.success)
        self.assertEqual(result.repair_stop_reason, "compile_succeeded")
        self.assertTrue(manifest["generation_success"])
        self.assertTrue(manifest["compile_success"])
        self.assertTrue(manifest["verification_run"])
        self.assertFalse(manifest["verification_success"])
        self.assertEqual(manifest["repair"]["stop_reason"], "compile_succeeded")
        self.assertEqual(manifest["verification"]["scenarios"][0]["status"], "failed")

    def test_behavior_checks_do_not_run_when_generation_does_not_compile(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = ProjectGenerator(_bundle(tmp), FakeLLM("int generated;\n"), tmp / "out", max_repair_rounds=0)

            with patch("agent.coder.generation._compile_project", return_value=_completed(1, stderr="compile failed")):
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior") as verify_behavior:
                    result = generator.generate()

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertFalse(result.success)
        self.assertEqual(result.repair_stop_reason, "max_rounds_exhausted")
        self.assertFalse(manifest["generation_success"])
        self.assertFalse(manifest["compile_success"])
        self.assertFalse(manifest["verification_run"])
        self.assertIsNone(manifest["verification_success"])
        verify_behavior.assert_not_called()

    def test_openai_error_retry_classification(self) -> None:
        class StatusError(OpenAIError):
            pass

        bad_request = StatusError("bad")
        bad_request.status_code = 400
        rate_limited = StatusError("rate")
        rate_limited.status_code = 429
        server_error = StatusError("server")
        server_error.status_code = 500

        self.assertFalse(_is_retryable_openai_error(bad_request))
        self.assertTrue(_is_retryable_openai_error(rate_limited))
        self.assertTrue(_is_retryable_openai_error(server_error))


if __name__ == "__main__":
    unittest.main()
