from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.planning_utility.contract_closure_analysis import (
    analyze_batch,
    analyze_contract_closure,
    write_flat_csv,
)
from evaluation.planning_utility.recalculate_saved_rq1_metrics import _aggregate


def _write_makefile(project: Path, sources: list[str]) -> None:
    (project / "Makefile").write_text(
        "CC ?= cc\n"
        "CFLAGS ?= -std=c11 -Wall -Wextra -I.\n"
        f"SRCS = {' '.join(sources)}\n"
        "TARGET = fixture_app\n"
        "fixture_app: $(SRCS)\n"
        "\t$(CC) $(CFLAGS) $(SRCS) -o $@\n"
        "clean:\n"
        "\trm -f $(TARGET)\n",
        encoding="utf-8",
    )


class ContractClosureAnalysisTests(unittest.TestCase):
    def test_header_self_containment_and_source_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            _write_makefile(project, ["core.c"])
            (project / "good.h").write_text(
                "#ifndef GOOD_H\n#define GOOD_H\n#include <stddef.h>\nsize_t good_size(void);\n#endif\n",
                encoding="utf-8",
            )
            (project / "bad.h").write_text(
                "#ifndef BAD_H\n#define BAD_H\nsize_t bad_size(void);\n#endif\n",
                encoding="utf-8",
            )
            (project / "core.c").write_text(
                '#include "good.h"\nsize_t good_size(void) { return sizeof(int); }\nint main(void) { return 0; }\n',
                encoding="utf-8",
            )
            original = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}

            result = analyze_contract_closure(project, "fixture_app", method="fixture", run_id="headers")

            metric = result["metrics"]["header_self_containment"]
            self.assertEqual((metric["passed"], metric["total"], metric["rate"]), (1, 2, 0.5))
            self.assertEqual(
                {item["path"]: item["passed"] for item in metric["items"]},
                {"bad.h": False, "good.h": True},
            )
            self.assertTrue(result["source_hash_preservation"]["preserved"])
            self.assertEqual(
                {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()},
                original,
            )

    def test_public_api_statuses_and_unique_symbol_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            sources = ["ready.c", "mismatch.c", "stub.c", "dup_a.c", "dup_b.c", "main.c"]
            _write_makefile(project, sources)
            (project / "api.h").write_text(
                "#ifndef API_H\n#define API_H\n"
                "int api_ready(int value);\n"
                "int api_missing(void);\n"
                "int api_mismatch(int value);\n"
                "void api_stub(void);\n"
                "int api_duplicate(void);\n"
                "#endif\n",
                encoding="utf-8",
            )
            (project / "ready.c").write_text(
                '#include "api.h"\nint api_ready(int value) { return value + 1; }\n', encoding="utf-8"
            )
            (project / "mismatch.c").write_text(
                "long api_mismatch(long value) { return value + 1; }\n", encoding="utf-8"
            )
            (project / "stub.c").write_text('#include "api.h"\nvoid api_stub(void) {}\n', encoding="utf-8")
            (project / "dup_a.c").write_text("int api_duplicate(void) { return 2; }\n", encoding="utf-8")
            (project / "dup_b.c").write_text("int api_duplicate(void) { return 3; }\n", encoding="utf-8")
            (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

            result = analyze_contract_closure(project, "fixture_app")

            api = result["metrics"]["public_api_realization"]
            statuses = {item["symbol"]: item["reasons"] for item in api["items"]}
            self.assertEqual((api["passed"], api["total"]), (1, 5))
            self.assertEqual(statuses["api_ready"], [])
            self.assertEqual(statuses["api_missing"], ["missing_definition"])
            self.assertEqual(statuses["api_mismatch"], ["signature_mismatch"])
            self.assertEqual(statuses["api_stub"], ["stub_definition"])
            self.assertEqual(statuses["api_duplicate"], ["duplicate_external_definitions"])

            owners = result["metrics"]["ownership_consistency"]["public_symbol_owners"]
            owner_status = {item["symbol"]: item["reasons"] for item in owners["items"]}
            self.assertEqual(owner_status["api_ready"], [])
            self.assertEqual(owner_status["api_missing"], ["missing_external_owner"])
            self.assertEqual(owner_status["api_duplicate"], ["duplicate_external_owners"])

    def test_cross_file_calls_and_opaque_type_ownership_are_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            sources = ["provider.c", "caller_good.c", "caller_hidden.c", "widget.c", "widget_dup.c", "main.c"]
            _write_makefile(project, sources)
            (project / "provider.h").write_text(
                "#ifndef PROVIDER_H\n#define PROVIDER_H\nint provided(int value);\n#endif\n",
                encoding="utf-8",
            )
            (project / "provider.c").write_text(
                '#include "provider.h"\nint provided(int value) { return value + 1; }\n', encoding="utf-8"
            )
            (project / "caller_good.c").write_text(
                '#include "provider.h"\nint caller_good(void) { return provided(1); }\n', encoding="utf-8"
            )
            (project / "caller_hidden.c").write_text(
                "int provided(int value);\nint caller_hidden(void) { return provided(2); }\n", encoding="utf-8"
            )
            (project / "widget.h").write_text(
                "#ifndef WIDGET_H\n#define WIDGET_H\ntypedef struct widget widget_t;\nwidget_t *widget_create(void);\n#endif\n",
                encoding="utf-8",
            )
            (project / "widget.c").write_text(
                '#include "widget.h"\n#include <stdlib.h>\nstruct widget { int value; };\n'
                "widget_t *widget_create(void) { return malloc(sizeof(widget_t)); }\n",
                encoding="utf-8",
            )
            (project / "widget_dup.c").write_text(
                '#include "widget.h"\nstruct widget { int other; };\nint widget_other(widget_t *item) { return item->other; }\n',
                encoding="utf-8",
            )
            (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

            result = analyze_contract_closure(project, "fixture_app")

            calls = result["metrics"]["cross_file_dependency_closure"]["function_calls"]["items"]
            provided = [item for item in calls if item["symbol"] == "provided"]
            self.assertEqual(len(provided), 2)
            self.assertEqual(
                {item["caller_path"]: item["passed"] for item in provided},
                {"caller_good.c": True, "caller_hidden.c": False},
            )
            opaque = result["metrics"]["ownership_consistency"]["opaque_type_owners"]["items"]
            widget = next(item for item in opaque if item["symbol"] == "widget")
            self.assertFalse(widget["passed"])
            self.assertIn("multiple_concrete_owners", widget["reasons"])

    def test_obligation_metrics_use_frozen_external_denominators(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            _write_makefile(project, ["main.c"])
            (project / "main.c").write_text(
                "#define TEST_CONNECT 1\n"
                "#define TEST_CONNACK 2\n"
                "int decode_connect_packet(int value) { return value + TEST_CONNECT; }\n"
                "int encode_connack_packet(int value) { return value + TEST_CONNACK; }\n"
                "int unrelated_public_api(int value) { return value + 7; }\n"
                "int main(void) { return encode_connack_packet(decode_connect_packet(1)); }\n",
                encoding="utf-8",
            )
            rubric = {
                "schema_version": "planning_utility_obligation_rubric/v1",
                "rubric_id": "fixture/v1",
                "protocol": "fixture",
                "profile": "test",
                "capabilities": [
                    {
                        "id": "connect_decode",
                        "all_term_groups": [["connect"], ["decode"]],
                    },
                    {
                        "id": "connack_output",
                        "all_term_groups": [["connack"], ["encode"]],
                    },
                ],
                "obligations": [
                    {
                        "id": "connect_connack",
                        "required_capabilities": ["connect_decode", "connack_output"],
                        "grounding": {
                            "kind": "named_integer_constants",
                            "markers": [
                                {"id": "connect", "name_terms": ["connect"], "accepted_values": [1]},
                                {"id": "connack", "name_terms": ["connack"], "accepted_values": [2]},
                            ],
                        },
                    }
                ],
            }

            result = analyze_contract_closure(project, "fixture_app", obligation_rubric=rubric)

            realization = result["metrics"]["required_obligation_realization"]
            path = result["metrics"]["executable_call_path_closure"]
            grounding = result["metrics"]["semantic_grounding_closure"]
            self.assertEqual((realization["passed"], realization["total"]), (1, 1))
            self.assertEqual(
                (realization["capabilities_realized"], realization["capabilities_total"]),
                (2, 2),
            )
            self.assertEqual((path["passed"], path["total"]), (1, 1))
            self.assertEqual((grounding["passed"], grounding["total"]), (1, 1))
            self.assertNotEqual(realization["total"], len(result["metrics"]["public_api_realization"]["items"]))
            self.assertEqual(result["obligation_rubric"]["rubric_id"], "fixture/v1")

    def test_obligation_metrics_reject_unreachable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            _write_makefile(project, ["main.c"])
            (project / "main.c").write_text(
                "#define TEST_CONNECT 1\n"
                "int decode_connect_packet(int value) { return value + TEST_CONNECT; }\n"
                "int encode_connack_packet(int value) { return value + 2; }\n"
                "int main(void) { return 0; }\n",
                encoding="utf-8",
            )
            rubric = {
                "schema_version": "planning_utility_obligation_rubric/v1",
                "rubric_id": "fixture/unreachable/v1",
                "protocol": "fixture",
                "profile": "test",
                "capabilities": [
                    {"id": "connect_decode", "all_term_groups": [["connect"], ["decode"]]},
                    {"id": "connack_output", "all_term_groups": [["connack"], ["encode"]]},
                ],
                "obligations": [
                    {
                        "id": "connect_connack",
                        "required_capabilities": ["connect_decode", "connack_output"],
                        "grounding": {
                            "kind": "named_integer_constants",
                            "markers": [
                                {"id": "connect", "name_terms": ["connect"], "accepted_values": [1]}
                            ],
                        },
                    }
                ],
            }

            result = analyze_contract_closure(project, "fixture_app", obligation_rubric=rubric)

            realization = result["metrics"]["required_obligation_realization"]
            path = result["metrics"]["executable_call_path_closure"]
            self.assertEqual((realization["passed"], realization["total"]), (1, 1))
            self.assertEqual((path["passed"], path["total"]), (0, 1))
            self.assertIn("capability_not_reachable_from_main", path["items"][0]["reasons"])

    def test_batch_manifest_keeps_method_and_sample_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            _write_makefile(project, ["main.c"])
            (project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            manifest = root / "runs.json"
            manifest.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "candidate-1",
                                "method": "full-specforge",
                                "sample_status": "candidate_diagnostic",
                                "project_dir": str(project),
                                "binary_name": "fixture_app",
                                "qualification_passed": False,
                                "cohort": "candidate_test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = analyze_batch(manifest)

            self.assertEqual(result["run_count"], 1)
            run = result["runs"][0]
            self.assertEqual((run["run_id"], run["method"], run["sample_status"]), (
                "candidate-1",
                "full-specforge",
                "candidate_diagnostic",
            ))
            self.assertFalse(run["metadata"]["qualification_passed"])
            for metric in run["metrics"].values():
                self.assertEqual(metric["total"], 0)
                self.assertIsNone(metric["rate"])
            csv_path = root / "metrics.csv"
            write_flat_csv(csv_path, result["runs"])
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("header_self_containment_rate", csv_text)
            self.assertIn("cohort,formal_rq1_replicate,qualification_passed", csv_text)
            self.assertIn("candidate-1,full-specforge,candidate_diagnostic,candidate_test,,False", csv_text)

    def test_saved_rq1_aggregation_uses_uniform_contract_families(self) -> None:
        def phase(counts: dict[str, int], native: bool, kloc: float) -> dict[str, object]:
            obligation = {"passed": 1, "total": 2, "rate": 0.5}
            return {
                "classifier": {
                    "category_counts": counts,
                    "native_compile_passed": native,
                    "sound_build_passed": False,
                    "source_kloc": kloc,
                },
                "obligations": {
                    name: obligation
                    for name in (
                        "required_obligation_realization",
                        "executable_call_path_closure",
                        "semantic_grounding_closure",
                    )
                },
            }

        runs = [
            {
                "pre": phase({"C1": 2, "C2": 2, "C3": 4, "C4": 2, "C5": 0}, False, 1.0),
                "post": phase({"C1": 1, "C2": 1, "C3": 2, "C4": 1, "C5": 0}, True, 1.0),
                "repair_calls": 2,
                "repair_tokens": 10_000,
            },
            {
                "pre": phase({"C1": 0, "C2": 2, "C3": 0, "C4": 0, "C5": 2}, False, 2.0),
                "post": phase({"C1": 0, "C2": 1, "C3": 0, "C4": 0, "C5": 1}, False, 2.0),
                "repair_calls": 1,
                "repair_tokens": 5_000,
            },
        ]

        result = _aggregate("fixture", runs)

        self.assertEqual(result["C2_C5"], {"before": 12, "after": 6, "closure_rate": 0.5})
        self.assertEqual(result["categories"]["C1"]["affected_projects_before"], 1)
        self.assertEqual(result["native_compile_recoveries"], 1)
        self.assertEqual(result["post_native_compile_success"], 1)
        self.assertEqual(result["post_C2_C5_diagnostics_per_kloc"]["median"], 2.5)


if __name__ == "__main__":
    unittest.main()
