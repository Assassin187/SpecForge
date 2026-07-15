from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.planning_utility.no_repair_analysis import analyze_no_repair
from evaluation.planning_utility.repair_diagnostics import (
    create_diagnostic_snapshot,
    high_risk_diagnostic_audit,
)


def _write_specs(root: Path, files: dict[str, str], functions: list[tuple[str, str]]) -> None:
    root.mkdir(parents=True)
    for trace_id, source_path in files.items():
        path = root / f"{trace_id.replace('/', '_')}_spec.json"
        path.write_text(
            json.dumps(
                {
                    "KIND": "FILE_SPEC",
                    "FILE": {"TRACE_ID": trace_id},
                    "SOURCE": {"PATH": source_path},
                }
            ),
            encoding="utf-8",
        )
    for trace_id, name in functions:
        path = root / f"{trace_id.replace('/', '_')}_spec.json"
        path.write_text(
            json.dumps(
                {
                    "KIND": "FUNCTION_SPEC",
                    "TRACE_ID": trace_id,
                    "SIGNATURE": {"NAME": name, "RAW": f"int {name}(void)"},
                }
            ),
            encoding="utf-8",
        )


def _write_makefile(project: Path, sources: str = "core.c") -> None:
    (project / "Makefile").write_text(
        "CC ?= cc\n"
        "CFLAGS ?= -std=c11 -Wall -Wextra\n"
        f"SRCS = {sources}\n"
        "TARGET = fixture_app\n"
        "fixture_app: $(SRCS)\n"
        "\t$(CC) $(CFLAGS) $(SRCS) -o $@\n"
        "clean:\n"
        "\trm -f $(TARGET) *.o\n",
        encoding="utf-8",
    )


class NoRepairAnalysisTests(unittest.TestCase):
    def test_complete_project_has_full_definition_coverage_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            specs = root / "specs"
            project.mkdir()
            _write_makefile(project)
            (project / "core.c").write_text(
                "int required_fn(void) { return 7; }\n"
                "int main(void) { return required_fn() == 7 ? 0 : 1; }\n",
                encoding="utf-8",
            )
            _write_specs(specs, {"fixture/core": "core.c"}, [("fixture/core/required_fn", "required_fn")])
            original = (project / "core.c").read_bytes()

            with patch(
                "evaluation.planning_utility.no_repair_analysis.create_diagnostic_snapshot",
                wraps=create_diagnostic_snapshot,
            ) as snapshot:
                result = analyze_no_repair(project, specs, "fixture_app")

            snapshot.assert_called_once()
            analyzed_copy = Path(snapshot.call_args.args[0])
            self.assertNotEqual(analyzed_copy, project.resolve())
            self.assertFalse(analyzed_copy.exists())
            self.assertEqual((project / "core.c").read_bytes(), original)
            self.assertFalse((project / "fixture_app").exists())
            self.assertEqual(result["diagnostic_snapshot"]["build"]["returncode"], 0)
            self.assertEqual(
                result["definition_coverage"],
                {
                    "required_count": 1,
                    "covered_count": 1,
                    "coverage_rate": 1.0,
                    "passed": True,
                    "functions": [
                        {
                            "trace_id": "fixture/core/required_fn",
                            "name": "required_fn",
                            "required_source": "core.c",
                            "definition_count": 1,
                            "non_stub_definition_count": 1,
                            "status": "covered",
                            "definitions": [
                                {
                                    "path": "core.c",
                                    "storage": "extern",
                                    "is_stub": False,
                                    "stub_reasons": [],
                                }
                            ],
                        }
                    ],
                },
            )
            self.assertEqual(result["placeholders"], {"count": 0, "passed": True, "items": []})
            self.assertEqual(result["near_empty_required_sources"], {"count": 0, "passed": True, "sources": []})
            self.assertTrue(result["source_hash_preservation"]["preserved"])
            self.assertEqual(result["source_hash_preservation"]["changed_paths"], [])

    def test_missing_duplicate_stub_placeholder_and_near_empty_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            specs = root / "specs"
            project.mkdir()
            _write_makefile(project, "empty.c stub.c first.c second.c main.c")
            (project / "empty.c").write_text("#include <stddef.h>\n/* TODO: not implemented */\n", encoding="utf-8")
            (project / "stub.c").write_text("int stub_fn(void) { return 0; }\n", encoding="utf-8")
            (project / "first.c").write_text("int duplicate_fn(void) { return 1 + 1; }\n", encoding="utf-8")
            (project / "second.c").write_text("int duplicate_fn(void) { return 2 + 2; }\n", encoding="utf-8")
            (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            _write_specs(
                specs,
                {
                    "fixture/empty": "empty.c",
                    "fixture/stub": "stub.c",
                    "fixture/first": "first.c",
                },
                [
                    ("fixture/empty/missing_fn", "missing_fn"),
                    ("fixture/stub/stub_fn", "stub_fn"),
                    ("fixture/first/duplicate_fn", "duplicate_fn"),
                ],
            )

            result = analyze_no_repair(project, specs, "fixture_app")

            coverage = result["definition_coverage"]
            self.assertEqual((coverage["required_count"], coverage["covered_count"], coverage["passed"]), (3, 0, False))
            self.assertEqual(
                {item["name"]: item["status"] for item in coverage["functions"]},
                {"missing_fn": "missing", "stub_fn": "stub", "duplicate_fn": "duplicate"},
            )
            placeholder_kinds = {item["kind"] for item in result["placeholders"]["items"]}
            self.assertIn("marker", placeholder_kinds)
            self.assertIn("fixed_dummy_return", placeholder_kinds)
            self.assertFalse(result["placeholders"]["passed"])
            near_empty = result["near_empty_required_sources"]
            self.assertEqual({item["path"] for item in near_empty["sources"]}, {"empty.c", "stub.c"})
            self.assertFalse(near_empty["passed"])

    def test_clean_target_that_rewrites_source_only_changes_temporary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            specs = root / "specs"
            project.mkdir()
            (project / "core.c").write_text("int required_fn(void) { return 7; }\n", encoding="utf-8")
            (project / "Makefile").write_text(
                "fixture_app: core.c\n"
                "\t$(CC) core.c -o fixture_app\n"
                "clean:\n"
                "\tprintf 'int required_fn(void) { return 0; }\\n' > core.c\n",
                encoding="utf-8",
            )
            _write_specs(specs, {"fixture/core": "core.c"}, [("fixture/core/required_fn", "required_fn")])
            original = (project / "core.c").read_text(encoding="utf-8")

            result = analyze_no_repair(project, specs, "fixture_app")

            self.assertEqual((project / "core.c").read_text(encoding="utf-8"), original)
            self.assertTrue(result["source_hash_preservation"]["preserved"])

    def test_native_compile_pass_with_conflicting_shared_layout_is_not_sound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            _write_makefile(project, "first.c second.c main.c")
            (project / "first.c").write_text(
                "struct shared_buffer { char *data; unsigned long len; };\n"
                "int first(void) { return (int)sizeof(struct shared_buffer); }\n",
                encoding="utf-8",
            )
            (project / "second.c").write_text(
                "struct shared_buffer { char *data; unsigned long capacity; unsigned long length; };\n"
                "int second(void) { return (int)sizeof(struct shared_buffer); }\n",
                encoding="utf-8",
            )
            (project / "main.c").write_text(
                "int first(void); int second(void);\n"
                "int main(void) { return first() == second(); }\n",
                encoding="utf-8",
            )

            snapshot = create_diagnostic_snapshot(project, "fixture_app")

        self.assertEqual(snapshot["build"]["returncode"], 0)
        self.assertFalse(snapshot["shared_type_layout_audit"]["passed"])
        self.assertEqual(snapshot["shared_type_layout_audit"]["conflicts"][0]["type"], "struct shared_buffer")
        self.assertFalse(snapshot["sound_build"]["passed"])
        self.assertIn("shared_type_layout_conflict", snapshot["sound_build"]["diagnostic_codes"])

    def test_high_risk_warning_is_a_sound_build_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            result = high_risk_diagnostic_audit(
                project,
                {
                    "stdout": "",
                    "stderr": (
                        "core.c:10:5: warning: ‘free’ called on pointer ‘owner’ with nonzero offset 8 "
                        "[-Wfree-nonheap-object]\n"
                    ),
                },
                [],
                [],
            )

        self.assertFalse(result["passed"])
        self.assertEqual({item["code"] for item in result["items"]}, {"free_nonheap_object"})


if __name__ == "__main__":
    unittest.main()
