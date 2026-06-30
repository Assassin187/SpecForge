from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.coder.cli import default_output_dir
from agent.coder.generation import render_makefile
from agent.coder.models import FileSpec, ModuleEntry, ProtocolMeta, SpecBundle
from agent.coder.protocol_behavior_val import coap
from agent.coder.verifier import ProjectVerifier


def _bundle(root: Path, *, protocol: str = "coap", default_port: int | None = None) -> SpecBundle:
    file_spec = FileSpec(
        trace_id=f"{protocol}/app/main",
        role="runtime app",
        lang="C",
        header_path="",
        source_path="main.c",
        header_dependencies=[],
        header_system_dependencies=[],
        source_dependencies=[],
        header_data=[],
        source_data=[],
        header_interfaces=[],
        source_interfaces=[],
        spec_path=root / "main_spec.json",
        raw={},
    )
    module = ModuleEntry("app", "runtime app", [], ["main.c"], [], [], {})
    return SpecBundle(
        protocol=ProtocolMeta(protocol, "1", ["server"], default_port),
        module_spec_path=root / f"{protocol}_module_spec.json",
        spec_root=root,
        generation_order=["app"],
        modules_in_order=[module],
        file_specs_by_trace={file_spec.trace_id: file_spec},
        file_specs_by_header_path={},
        file_specs_by_source_path={"main.c": file_spec},
        function_specs_by_trace={},
        consistency_rules=[],
    )


class VerifierTests(unittest.TestCase):
    def test_structure_allows_source_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

            diagnostics = ProjectVerifier(_bundle(root), root).verify_structure()

        self.assertFalse(diagnostics)

    def test_behavior_failure_is_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            verifier = ProjectVerifier(_bundle(Path(raw_tmp)), raw_tmp)
            with patch.object(coap, "run", side_effect=RuntimeError("bad response")):
                result = verifier.verify_behavior()

        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostics[0].code, "behavior_verification_failed")
        self.assertEqual(result.scenarios[0]["name"], "coap_get_hello")
        self.assertEqual(result.scenarios[0]["status"], "failed")
        self.assertTrue(all(item["status"] == "skipped" for item in result.scenarios[1:]))

    def test_default_output_dir_uses_protocol_binary_name(self) -> None:
        output_dir = default_output_dir(_bundle(Path("."), protocol="coap"))

        self.assertTrue(output_dir.name.startswith("coap_server_"))

    def test_makefile_rejects_implicit_function_declarations(self) -> None:
        rendered = render_makefile(_bundle(Path(".")))

        self.assertIn("-Werror=implicit-function-declaration", rendered)


if __name__ == "__main__":
    unittest.main()
