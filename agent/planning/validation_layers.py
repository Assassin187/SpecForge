from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .facts import write_json
from .registry import RegistryBindingError, RegistryInvariantError


RECOVERY_BY_LAYER = {
    "structural": "partition_regeneration",
    "binding": "local_semantic_correction_or_artifact_request",
    "semantic": "bounded_semantic_closure",
}
HARD_FAILURE_CODES = {
    "facts_read_failure",
    "registry_corruption",
    "canonical_artifact_mutation",
    "pipeline_state_corruption",
    "deterministic_internal_invariant",
    "candidate_serialization_impossible",
}
DETERMINISTIC_CHANGE_REQUIRED_FIELDS = {
    "code",
    "artifact_id",
    "field",
    "reason",
    "source_artifact_id",
}


@dataclass(frozen=True)
class ValidationIssue:
    layer: str
    code: str
    message: str
    recovery: str


class LayeredValidationError(ValueError):
    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(f"{issue.layer}:{issue.code}: {issue.message}")
        self.issue = issue


class ValidationLedger:
    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = log_path
        self.events: dict[str, dict[str, Any]] = {}
        self._write()

    def record(
        self,
        issue: ValidationIssue,
        *,
        stage_id: str,
        partition_id: str,
        outcome: str,
    ) -> str:
        diagnostic_id = _diagnostic_id(issue.code, issue.message, stage_id, partition_id)
        current = self.events.get(diagnostic_id)
        if current is not None and current["layer"] != issue.layer:
            raise RegistryInvariantError(
                f"validation_diagnostic_owner_conflict: {diagnostic_id} claimed by {current['layer']} and {issue.layer}"
            )
        if current is None:
            current = {
                "diagnostic_id": diagnostic_id,
                "layer": issue.layer,
                "code": issue.code,
                "message": issue.message,
                "recovery": issue.recovery,
                "stage_id": stage_id,
                "partition_id": partition_id,
                "outcomes": [],
            }
            self.events[diagnostic_id] = current
        if outcome not in current["outcomes"]:
            current["outcomes"].append(outcome)
        self._write()
        return diagnostic_id

    def _write(self) -> None:
        if self.log_path is not None:
            write_json(
                self.log_path,
                {
                    "kind": "PLANNING_VALIDATION_LEDGER",
                    "schema_version": 1,
                    "recovery_by_layer": RECOVERY_BY_LAYER,
                    "events": list(self.events.values()),
                },
            )


def classify_partition_error(error: Exception) -> ValidationIssue:
    message = str(error)
    if message.startswith("candidate_serialization_impossible:"):
        return "candidate_serialization_impossible"
    code = message.split(":", 1)[0]
    if isinstance(error, RegistryBindingError):
        code = error.code
        layer = "binding"
    elif code in {
        "typed_delta_invalid_shape",
        "typed_delta_forbidden_field",
        "typed_delta_forbidden_inventory",
        "overlay_invalid_shape",
        "overlay_forbidden_field",
    }:
        layer = "structural"
    else:
        layer = "binding"
    return ValidationIssue(layer, code, message, RECOVERY_BY_LAYER[layer])


def classify_hard_failure(error: BaseException, *, facts_read: bool = False) -> str:
    if facts_read:
        return "facts_read_failure"
    message = str(error)
    if isinstance(error, RegistryInvariantError):
        if "deterministic_change_provenance_missing" in message:
            return "deterministic_internal_invariant"
        if "canonical_fields_immutable" in message:
            return "canonical_artifact_mutation"
        return "registry_corruption"
    if "registry_snapshot_missing" in message or "Cannot resume" in message:
        return "pipeline_state_corruption"
    return "deterministic_internal_invariant"


def semantic_layer_summary(
    final_diagnostics: list[dict[str, Any]],
    unresolved_partitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = {
        "structural": {"status": "passed_before_qualification", "recovery": RECOVERY_BY_LAYER["structural"]},
        "binding": {"status": "passed_before_qualification", "recovery": RECOVERY_BY_LAYER["binding"]},
        "semantic": {
            "status": "passed" if not final_diagnostics else "failed",
            "recovery": RECOVERY_BY_LAYER["semantic"],
            "diagnostic_codes": [str(item.get("code", "")) for item in final_diagnostics],
        },
    }
    for item in unresolved_partitions or []:
        layer = str(item.get("validation_layer", "binding"))
        if layer in {"structural", "binding"}:
            summary[layer]["status"] = "failed"
    return summary


def validate_deterministic_change_log(changes: list[dict[str, Any]]) -> None:
    for index, change in enumerate(changes):
        missing = DETERMINISTIC_CHANGE_REQUIRED_FIELDS - set(change)
        empty = {
            field
            for field in DETERMINISTIC_CHANGE_REQUIRED_FIELDS
            if field in change and not str(change[field] or "").strip()
        }
        if missing or empty:
            raise RegistryInvariantError(
                "deterministic_change_provenance_missing: "
                f"change[{index}] missing={sorted(missing)}, empty={sorted(empty)}"
            )


def _diagnostic_id(code: str, message: str, stage_id: str, partition_id: str) -> str:
    payload = "\x1f".join((code, message, stage_id, partition_id)).encode("utf-8")
    return "diag:" + hashlib.sha256(payload).hexdigest()[:16]
