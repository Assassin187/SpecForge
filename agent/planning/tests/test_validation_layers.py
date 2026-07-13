from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.planning.facts import read_json
from agent.planning.registry import RegistryBindingError, RegistryInvariantError
from agent.planning.validation_layers import (
    HARD_FAILURE_CODES,
    ValidationIssue,
    ValidationLedger,
    classify_hard_failure,
    classify_partition_error,
    semantic_layer_summary,
    validate_deterministic_change_log,
)


class ValidationLayerTests(unittest.TestCase):
    def test_structural_shape_error_has_partition_regeneration_owner(self) -> None:
        issue = classify_partition_error(ValueError("typed_delta_invalid_shape: fixture"))
        self.assertEqual(issue.layer, "structural")
        self.assertEqual(issue.recovery, "partition_regeneration")

    def test_registry_reference_error_has_binding_owner(self) -> None:
        issue = classify_partition_error(
            RegistryBindingError("artifact_kind_mismatch", "type:fixture", {"function"})
        )
        self.assertEqual(issue.layer, "binding")
        self.assertEqual(issue.recovery, "local_semantic_correction_or_artifact_request")

    def test_one_diagnostic_cannot_be_owned_by_two_layers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "validation_layers.json"
            ledger = ValidationLedger(path)
            structural = ValidationIssue("structural", "fixture", "same", "partition_regeneration")
            binding = ValidationIssue("binding", "fixture", "same", "local_semantic_correction_or_artifact_request")
            ledger.record(structural, stage_id="stage", partition_id="part", outcome="unresolved")
            with self.assertRaisesRegex(RegistryInvariantError, "validation_diagnostic_owner_conflict"):
                ledger.record(binding, stage_id="stage", partition_id="part", outcome="unresolved")
            self.assertEqual(read_json(path)["events"][0]["layer"], "structural")

    def test_semantic_summary_does_not_claim_partition_diagnostics(self) -> None:
        summary = semantic_layer_summary(
            [{"code": "runtime_entrypoint_missing"}],
            [{"validation_layer": "binding"}],
        )
        self.assertEqual(summary["binding"]["status"], "failed")
        self.assertEqual(summary["semantic"]["status"], "failed")
        self.assertEqual(summary["semantic"]["diagnostic_codes"], ["runtime_entrypoint_missing"])

    def test_deterministic_change_requires_explicit_provenance(self) -> None:
        valid = {
            "code": "deterministic_dependency_completion",
            "artifact_id": "file:fixture",
            "field": "source_dependencies",
            "reason": "uniquely implied by a canonical call edge",
            "source_artifact_id": "function:fixture/callee",
        }
        validate_deterministic_change_log([valid])
        invalid = dict(valid)
        invalid.pop("source_artifact_id")
        with self.assertRaisesRegex(RegistryInvariantError, "deterministic_change_provenance_missing"):
            validate_deterministic_change_log([invalid])

    def test_hard_failure_allowlist_is_closed_and_classified(self) -> None:
        self.assertEqual(
            HARD_FAILURE_CODES,
            {
                "facts_read_failure",
                "registry_corruption",
                "canonical_artifact_mutation",
                "pipeline_state_corruption",
                "deterministic_internal_invariant",
            },
        )
        self.assertEqual(classify_hard_failure(ValueError("bad facts"), facts_read=True), "facts_read_failure")
        self.assertEqual(
            classify_hard_failure(RegistryInvariantError("canonical_fields_immutable: fixture")),
            "canonical_artifact_mutation",
        )
        self.assertEqual(
            classify_hard_failure(RegistryInvariantError("registry_entry_incomplete: fixture")),
            "registry_corruption",
        )
        self.assertEqual(
            classify_hard_failure(RuntimeError("registry_snapshot_missing: fixture")),
            "pipeline_state_corruption",
        )
        self.assertEqual(classify_hard_failure(AssertionError("fixture")), "deterministic_internal_invariant")


if __name__ == "__main__":
    unittest.main()
