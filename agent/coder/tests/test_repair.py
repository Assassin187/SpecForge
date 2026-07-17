from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from openai import OpenAIError

from agent.coder import generation
from agent.coder.cli import build_parser
from agent.coder.generation import (
    ProjectGenerator,
    _classify_repair_targets,
    _compact_compile_diagnostics,
    _validate_repair_candidate,
    render_header,
)
from agent.coder.llm_client import LLMRequest, LLMResponse, LLMUsage
from agent.coder.models import FileSpec, HeaderInterface, ModuleEntry, ProtocolMeta, SpecBundle
from agent.coder.specs import load_spec_bundle_from_root, validate_rendered_headers_compile
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


def _main_bundle(spec_root: Path) -> SpecBundle:
    file_spec = FileSpec(
        trace_id="smtp/main",
        role="source-only entrypoint",
        lang="C",
        header_path="",
        source_path="main.c",
        header_dependencies=[],
        header_system_dependencies=[],
        source_dependencies=["server/smtp_server.h"],
        header_data=[],
        source_data=[],
        header_interfaces=[],
        source_interfaces=[],
        spec_path=spec_root / "main_spec.json",
        raw={},
    )
    module = ModuleEntry(
        name="server_app",
        role="runtime app",
        dependencies=[],
        files=["main.c"],
        artifacts=[],
        doc_ref=[],
        raw={},
    )
    return SpecBundle(
        protocol=ProtocolMeta("smtp", "1", ["server"], 2525),
        module_spec_path=spec_root / "smtp_module_spec.json",
        spec_root=spec_root,
        generation_order=["server_app"],
        modules_in_order=[module],
        file_specs_by_trace={file_spec.trace_id: file_spec},
        file_specs_by_header_path={},
        file_specs_by_source_path={file_spec.source_path: file_spec},
        function_specs_by_trace={},
        consistency_rules=[],
        diagnostics=[],
    )


def _http_main_bundle() -> SpecBundle:
    repo_root = Path(__file__).resolve().parents[3]
    full = load_spec_bundle_from_root(repo_root / "specs-example/http_specs", validate_rendered_headers=False)
    file_spec = full.file_specs_by_source_path["main.c"]
    function_specs = {
        item.trace_id: full.function_specs_by_trace[item.trace_id]
        for item in file_spec.source_interfaces
        if item.trace_id in full.function_specs_by_trace
    }
    module = ModuleEntry(
        name="main",
        role="HTTP entrypoint",
        dependencies=[],
        files=["main.c"],
        artifacts=[],
        doc_ref=[],
        raw={},
    )
    return SpecBundle(
        protocol=full.protocol,
        module_spec_path=full.module_spec_path,
        spec_root=full.spec_root,
        generation_order=["main"],
        modules_in_order=[module],
        file_specs_by_trace={file_spec.trace_id: file_spec},
        file_specs_by_header_path={},
        file_specs_by_source_path={"main.c": file_spec},
        function_specs_by_trace=function_specs,
        consistency_rules=full.consistency_rules,
        diagnostics=[],
    )


class CoderRepairTests(unittest.TestCase):
    def _generator(self, tmp: Path, llm: FakeLLM, **kwargs: Any) -> ProjectGenerator:
        generator = ProjectGenerator(_bundle(tmp), llm, tmp / "out", max_repair_rounds=3, **kwargs)
        generator.project_dir.mkdir(parents=True)
        (generator.project_dir / "protocol").mkdir()
        (generator.project_dir / "protocol/coap_message.h").write_text("#pragma once\n", encoding="utf-8")
        (generator.project_dir / "protocol/coap_message.c").write_text("int broken;\n", encoding="utf-8")
        return generator

    def test_default_prompt_builders_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            generator = ProjectGenerator(_bundle(Path(raw_tmp)), FakeLLM(""), Path(raw_tmp) / "out")

        self.assertIs(generator.source_prompt_builder, generation.build_source_prompt)
        self.assertIs(generator.main_source_prompt_builder, generation.build_main_source_prompt)
        self.assertIs(generator.repair_prompt_builder, generation.build_repair_prompt)
        self.assertIsNone(generator.prompt_observer)

    def test_skip_repair_cli_stops_after_code_generation(self) -> None:
        args = build_parser().parse_args(["--skip-repair", "generate"])
        self.assertTrue(args.skip_repair)

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = ProjectGenerator(_bundle(tmp), FakeLLM("int generated;\n"), tmp / "out", skip_repair=True)
            with patch("agent.coder.generation._compile_project") as compile_project:
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior") as verify_behavior:
                    result = generator.generate()

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertTrue(result.success)
        self.assertEqual(result.repair_stop_reason, "skipped_by_cli")
        self.assertTrue(manifest["skip_repair"])
        self.assertFalse(manifest["compile_run"])
        self.assertIsNone(manifest["compile_success"])
        self.assertFalse(manifest["verification_run"])
        compile_project.assert_not_called()
        verify_behavior.assert_not_called()

    def test_repair_existing_project_compiles_and_repairs_without_regeneration(self) -> None:
        args = build_parser().parse_args(["repair", "--project-dir", "/tmp/existing-project"])
        self.assertEqual(args.project_dir, Path("/tmp/existing-project"))

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = self._generator(tmp, FakeLLM("int fixed;\n"))
            existing_log = tmp / "out/_agent_logs/001_existing.txt"
            existing_log.write_text("keep\n", encoding="utf-8")
            original_project_dir = generator.project_dir
            source_path = original_project_dir / "protocol/coap_message.c"
            fail = _completed(1, stderr="protocol/coap_message.c:1:1: error: bad source\n")
            with patch.object(generator, "prepare_output_dir") as prepare_output_dir, patch(
                "agent.coder.generation._compile_project", side_effect=[fail, _completed(0)]
            ) as compile_project:
                result = generator.repair_existing(generator.project_dir)

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            original_source = source_path.read_text(encoding="utf-8")
            repaired_source = (generator.project_dir / "protocol/coap_message.c").read_text(encoding="utf-8")
            preserved_log = existing_log.read_text(encoding="utf-8")

        self.assertTrue(result.success)
        self.assertEqual(original_source, "int broken;\n")
        self.assertEqual(repaired_source, "int fixed;\n")
        self.assertEqual(preserved_log, "keep\n")
        self.assertNotEqual(generator.project_dir, original_project_dir)
        self.assertTrue(generator.project_dir.parent.name.startswith("coap_repair_"))
        self.assertEqual(manifest["mode"], "repair_existing")
        self.assertEqual(manifest["source_project_dir"], str(original_project_dir.resolve()))
        self.assertEqual(manifest["project_dir"], str(generator.project_dir))
        self.assertEqual(manifest["repair"]["rounds_attempted"], 1)
        self.assertEqual(compile_project.call_count, 2)
        prepare_output_dir.assert_not_called()

    def test_injected_source_prompt_builder_and_observer_are_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int generated;\n")
            messages = [
                {"role": "system", "content": "custom source system"},
                {"role": "user", "content": "custom source prompt"},
            ]
            builder_subjects: list[str] = []
            observer_events: list[tuple[str, str]] = []

            def source_builder(bundle, module, file_spec, function_specs, generated_header, dependency_headers):
                builder_subjects.append(file_spec.source_path)
                return messages

            def observer(stage: str, subject: str, observed_messages: list[dict[str, str]]) -> None:
                self.assertEqual(llm.requests, [])
                self.assertFalse(list((tmp / "out/_agent_logs").glob("*prompt_protocol_coap_message.c.txt")))
                self.assertIs(observed_messages, messages)
                observer_events.append((stage, subject))

            generator = ProjectGenerator(
                _bundle(tmp),
                llm,
                tmp / "out",
                source_prompt_builder=source_builder,
                prompt_observer=observer,
            )
            with patch("agent.coder.generation._compile_project", return_value=_completed(0)):
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior", return_value=VerificationResult(True, [])):
                    result = generator.generate()

        self.assertTrue(result.success)
        self.assertEqual(builder_subjects, ["protocol/coap_message.c"])
        self.assertEqual(observer_events, [("source_generation", "protocol/coap_message.c")])
        self.assertEqual(llm.requests[0].messages, messages)

    def test_injected_main_source_prompt_builder_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int main(void) { return 0; }\n")
            messages = [
                {"role": "system", "content": "custom main system"},
                {"role": "user", "content": "custom main prompt"},
            ]

            def source_builder(*args):
                self.fail("main.c must not use source_prompt_builder")

            def main_source_builder(bundle, module, file_spec, function_specs, generated_header, dependency_headers):
                return messages

            generator = ProjectGenerator(
                _main_bundle(tmp),
                llm,
                tmp / "out",
                source_prompt_builder=source_builder,
                main_source_prompt_builder=main_source_builder,
            )
            with patch("agent.coder.generation._compile_project", return_value=_completed(0)):
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior", return_value=VerificationResult(True, [])):
                    result = generator.generate()

        self.assertTrue(result.success)
        self.assertEqual(llm.requests[0].messages, messages)

    def test_injected_repair_prompt_builder_and_observer_are_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int fixed;\n")
            messages = [
                {"role": "system", "content": "custom repair system"},
                {"role": "user", "content": "custom repair prompt"},
            ]
            builder_subjects: list[str] = []
            observer_events: list[tuple[str, str]] = []

            def repair_builder(
                bundle,
                module,
                file_spec,
                target_path,
                canonical_header,
                current_content,
                compile_errors,
                dependency_headers,
            ):
                builder_subjects.append(target_path)
                return messages

            def observer(stage: str, subject: str, observed_messages: list[dict[str, str]]) -> None:
                self.assertEqual(llm.requests, [])
                self.assertFalse(list((tmp / "out/_agent_logs").glob("*repair_prompt_*")))
                self.assertIs(observed_messages, messages)
                observer_events.append((stage, subject))

            generator = self._generator(
                tmp,
                llm,
                repair_prompt_builder=repair_builder,
                prompt_observer=observer,
            )
            fail = _completed(1, stderr="protocol/coap_message.c:1:1: error: bad source\n")
            success = _completed(0)
            with patch("agent.coder.generation._compile_project", side_effect=[fail, success]):
                outcome = generator._repair_until_compiles([], "coap_server")

        self.assertEqual(outcome.stop_reason, "compile_succeeded")
        self.assertEqual(builder_subjects, ["protocol/coap_message.c"])
        self.assertEqual(observer_events, [("repair", "protocol/coap_message.c")])
        self.assertEqual(llm.requests[0].messages, messages)

    def test_prompt_observer_failure_blocks_generation_llm_and_prompt_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int generated;\n")

            def reject_prompt(stage: str, subject: str, messages: list[dict[str, str]]) -> None:
                raise RuntimeError("prompt rejected")

            generator = ProjectGenerator(_bundle(tmp), llm, tmp / "out", prompt_observer=reject_prompt)
            with self.assertRaisesRegex(RuntimeError, "prompt rejected"):
                generator.generate()

            self.assertEqual(llm.requests, [])
            self.assertFalse(list((tmp / "out/_agent_logs").glob("*prompt_protocol_coap_message.c.txt")))

    def test_prompt_observer_failure_blocks_repair_llm_and_prompt_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int fixed;\n")

            def reject_prompt(stage: str, subject: str, messages: list[dict[str, str]]) -> None:
                raise RuntimeError("prompt rejected")

            generator = self._generator(tmp, llm, prompt_observer=reject_prompt)
            fail = _completed(1, stderr="protocol/coap_message.c:1:1: error: bad source\n")
            with patch("agent.coder.generation._compile_project", return_value=fail):
                with self.assertRaisesRegex(RuntimeError, "prompt rejected"):
                    generator._repair_until_compiles([], "coap_server")

            self.assertEqual(llm.requests, [])
            self.assertFalse(list((tmp / "out/_agent_logs").glob("*repair_prompt_*")))

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

    def test_fresh02_linker_diagnostics_route_broker_source_with_full_context(self) -> None:
        stderr = """/usr/bin/ld: /tmp/ccuDJMka.o: in function `mqtt_decoder_feed':
mqtt_broker.c:(.text+0x20): multiple definition of `mqtt_decoder_feed'; /tmp/ccJ6pape.o:mqtt_decoder.c:(.text+0x0): first defined here
/usr/bin/ld: /tmp/ccuDJMka.o: in function `mqtt_decoder_feed':
mqtt_broker.c:(.text+0x32): undefined reference to `mqtt_decode_and_dispatch'
collect2: error: ld returned 1 exit status
"""
        with tempfile.TemporaryDirectory() as raw_tmp:
            project_dir = Path(raw_tmp)
            broker = _file_spec("mqtt_broker.c", "mqtt_broker.h")
            decoder = _file_spec("mqtt_decoder.c", "mqtt_decoder.h")
            bundle = _bundle(project_dir)
            bundle.file_specs_by_source_path = {
                "mqtt_broker.c": broker,
                "mqtt_decoder.c": decoder,
            }
            repairable, blocking = _classify_repair_targets("", stderr, project_dir, bundle)
            compact = _compact_compile_diagnostics(
                "", stderr, "mqtt_broker.c", project_dir
            )

        self.assertEqual(repairable, ["mqtt_broker.c"])
        self.assertEqual(blocking, [])
        self.assertIn("multiple definition of `mqtt_decoder_feed'", compact)
        self.assertIn("mqtt_decoder.c:(.text+0x0): first defined here", compact)
        self.assertIn("undefined reference to `mqtt_decode_and_dispatch'", compact)

    def test_unmapped_linker_diagnostic_has_explicit_stop_reason(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int changed;\n")
            generator = self._generator(tmp, llm)
            fail = _completed(
                1,
                stderr="/usr/bin/ld: Scrt1.o: undefined reference to `main'\n",
            )

            with patch("agent.coder.generation._compile_project", return_value=fail):
                outcome = generator._repair_until_compiles([], "coap_server")

        self.assertEqual(outcome.stop_reason, "unmapped_linker_diagnostics")
        self.assertEqual(llm.requests, [])

    def test_repair_candidate_adding_linker_root_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int fixed(void) { return missing_symbol(); }\n")
            generator = self._generator(tmp, llm)
            generator.max_repair_rounds = 1
            source = generator.project_dir / "protocol/coap_message.c"
            original = source.read_text(encoding="utf-8")
            compile_error = _completed(
                1, stderr="protocol/coap_message.c:1:1: error: bad source\n"
            )
            linker_error = _completed(
                1,
                stderr=(
                    "/usr/bin/ld: /tmp/coap.o: in function `fixed':\n"
                    "protocol/coap_message.c:(.text+0x1): undefined reference to `missing_symbol'\n"
                ),
            )

            with patch(
                "agent.coder.generation._compile_project",
                side_effect=[compile_error, linker_error],
            ):
                repaired_files: list[str] = []
                outcome = generator._repair_until_compiles(repaired_files, "coap_server")
            restored = source.read_text(encoding="utf-8")

        self.assertEqual(outcome.stop_reason, "repair_stagnated")
        self.assertEqual(restored, original)
        self.assertEqual(repaired_files, [])
        self.assertIn("new_compile_roots", outcome.rejected_candidates[0]["reason"])

    def test_repair_stops_when_compile_fingerprint_does_not_decrease(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int changed;\n")
            generator = self._generator(tmp, llm)
            source = generator.project_dir / "protocol/coap_message.c"
            original = source.read_text(encoding="utf-8")
            same_error = _completed(1, stderr="protocol/coap_message.c:1:1: error: same root\n")

            with patch("agent.coder.generation._compile_project", side_effect=[same_error, same_error]):
                outcome = generator._repair_until_compiles([], "coap_server")
            restored = source.read_text(encoding="utf-8")

        self.assertEqual(outcome.stop_reason, "repair_stagnated")
        self.assertEqual(restored, original)
        self.assertEqual(len(llm.requests), 1)
        self.assertFalse(outcome.fingerprint_history[0]["accepted"])

    def test_repair_accepts_strict_subset_of_compile_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            generator = self._generator(tmp, FakeLLM("int partially_fixed;\n"))
            generator.max_repair_rounds = 1
            before = _completed(
                1,
                stderr=(
                    "protocol/coap_message.c:1:1: error: first root\n"
                    "protocol/coap_message.c:2:1: error: second root\n"
                ),
            )
            after = _completed(1, stderr="protocol/coap_message.c:2:1: error: second root\n")

            with patch("agent.coder.generation._compile_project", side_effect=[before, after]):
                repaired_files: list[str] = []
                outcome = generator._repair_until_compiles(repaired_files, "coap_server")

        self.assertEqual(outcome.stop_reason, "max_rounds_exhausted")
        self.assertEqual(repaired_files, ["protocol/coap_message.c"])
        self.assertTrue(outcome.fingerprint_history[0]["accepted"])

    def test_unknown_external_accessor_is_spec_contract_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int changed;\n")
            generator = self._generator(tmp, llm)
            compile_error = _completed(
                1,
                stderr=(
                    "protocol/coap_message.c:3:1: error: implicit declaration of function "
                    "‘ghost_accessor’ [-Werror=implicit-function-declaration]\n"
                ),
            )

            with patch("agent.coder.generation._compile_project", return_value=compile_error):
                outcome = generator._repair_until_compiles([], "coap_server")

        self.assertEqual(outcome.stop_reason, "spec_contract_blocked")
        self.assertEqual(outcome.blocking_files, ["ghost_accessor"])
        self.assertEqual(llm.requests, [])

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

    def test_rendered_header_probe_requires_complete_by_value_system_type(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            base = _file_spec("protocol/coap_message.c", "protocol/coap_message.h")
            interface = HeaderInterface(
                signature="struct iovec coap_message_view(void)",
                name="coap_message_view",
                kind="FUNC",
                function_type="ALGORITHM",
                role="Return a system scatter/gather view.",
                visibility="public",
            )

            def checked_bundle(system_dependencies: list[str]) -> SpecBundle:
                file_spec = FileSpec(
                    **{
                        **base.__dict__,
                        "header_system_dependencies": system_dependencies,
                        "header_interfaces": [interface],
                    }
                )
                bundle = _bundle(tmp)
                bundle.file_specs_by_trace = {file_spec.trace_id: file_spec}
                bundle.file_specs_by_header_path = {file_spec.header_path: file_spec}
                bundle.file_specs_by_source_path = {file_spec.source_path: file_spec}
                return bundle

            missing = checked_bundle([])
            validate_rendered_headers_compile(missing, tmp / "missing")
            complete = checked_bundle(["sys/uio.h"])
            validate_rendered_headers_compile(complete, tmp / "complete")

        self.assertTrue(any(item.code == "rendered_header_compile_error" for item in missing.diagnostics))
        self.assertFalse(complete.has_errors(), complete.diagnostics)

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

    def test_http_main_specs_use_llm_generation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            main_source = """#include "server/http_server.h"

#include <stdint.h>
#include <stdlib.h>

static uint16_t parse_port(const char* s) {
    if (!s || !s[0]) {
        return 0;
    }
    char* end = NULL;
    unsigned long raw = strtoul(s, &end, 10);
    if ((end && *end != '\\0') || raw == 0 || raw > 65535UL) {
        return 0;
    }
    return (uint16_t)raw;
}

int main(int argc, char** argv) {
    uint16_t port = argc > 1 ? parse_port(argv[1]) : 8080;
    if (port == 0) {
        port = 8080;
    }
    const char* root_dir = (argc > 2 && argv[2][0]) ? argv[2] : ".";
    http_server_t* server = http_server_create(port, root_dir);
    if (!server) {
        return 1;
    }
    if (http_server_start(server) != 0) {
        http_server_destroy(server);
        return 1;
    }
    (void)http_server_run(server);
    http_server_destroy(server);
    return 0;
}
"""
            llm = FakeLLM(main_source)
            generator = ProjectGenerator(_http_main_bundle(), llm, tmp / "out")

            with patch("agent.coder.generation._compile_project", return_value=_completed(0)):
                with patch("agent.coder.verifier.ProjectVerifier.verify_behavior", return_value=VerificationResult(True, [])):
                    result = generator.generate()

            generated = generator.project_dir / "main.c"
            generated_content = generated.read_text(encoding="utf-8")
            prompt = llm.requests[0].messages[1]["content"]
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            prompt_logs = list((tmp / "out/_agent_logs").glob("*prompt_main.c.txt"))

        self.assertTrue(result.success)
        self.assertEqual(len(llm.requests), 1)
        self.assertEqual(generated_content, main_source)
        self.assertIn("Generate the full C source file `main.c`", prompt)
        self.assertIn("Entrypoint raw specs", prompt)
        self.assertIn("argv[1]", prompt)
        self.assertIn("8080", prompt)
        self.assertIn("argv[2]", prompt)
        self.assertIn("root_dir", prompt)
        self.assertIn("Never pass NULL", prompt)
        self.assertIn("http_server_create", prompt)
        self.assertIn("http_server_start", prompt)
        self.assertIn("http_server_run", prompt)
        self.assertIn("http_server_destroy", prompt)
        self.assertIn("This is a source-only file", prompt)
        self.assertIn("server/http_server.h", prompt)
        self.assertEqual(manifest["llm_call_usage"][0]["subject"], "main.c")
        self.assertIn("source_generation", manifest["stage_token_usage"])
        self.assertTrue(prompt_logs)

    def test_duplicate_specified_function_is_blocked_before_compile(self) -> None:
        duplicate_source = """#include <stdint.h>
static uint16_t parse_port(const char* s) { (void)s; return 0; }
int main(int argc, char** argv) { (void)argc; (void)argv; return 0; }
int main(int argc, char** argv) { (void)argc; (void)argv; return 0; }
"""
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM(duplicate_source)
            generator = ProjectGenerator(_http_main_bundle(), llm, tmp / "out")
            with patch("agent.coder.generation._compile_project") as compile_project:
                result = generator.generate()
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertFalse(result.success)
        self.assertEqual(len(llm.requests), 2)
        self.assertEqual(result.repair_stop_reason, "source_integrity_failed")
        self.assertFalse(manifest["source_integrity"]["passed"])
        self.assertTrue(manifest["source_integrity"]["retry_used"])
        compile_project.assert_not_called()

    def test_source_only_main_compile_error_is_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            llm = FakeLLM("int main(void) { return 0; }\n")
            generator = ProjectGenerator(_main_bundle(tmp), llm, tmp / "out")
            generator.project_dir.mkdir(parents=True)
            (generator.project_dir / "main.c").write_text("int main(void) { return missing; }\n", encoding="utf-8")
            fail = _completed(1, stderr="main.c:1:25: error: undeclared identifier 'missing'\n")
            success = _completed(0)

            with patch("agent.coder.generation._compile_project", side_effect=[fail, success]):
                outcome = generator._repair_until_compiles([], "smtp_server")

            prompt = llm.requests[0].messages[1]["content"]

        self.assertEqual(outcome.stop_reason, "compile_succeeded")
        self.assertIn("Repair the source file `main.c`", prompt)

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
