from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .facts import write_json
from .registry import (
    CanonicalPlanningRegistry,
    RegistryBindingError,
    RegistryInvariantError,
)


REQUIRED_REQUEST_FIELDS = {
    "requested_kind",
    "semantic_role",
    "requested_owner",
    "required_by",
    "reason",
    "provenance",
    "preferred_visibility",
}
ALLOWED_REQUEST_FIELDS = REQUIRED_REQUEST_FIELDS | {"proposed_name"}


class InventoryAmendmentProcessor:
    def __init__(self, registry: CanonicalPlanningRegistry | None = None, *, log_path: Path | None = None) -> None:
        self.registry = registry
        self.log_path = log_path
        self.records: list[dict[str, Any]] = []
        self._processed_scopes: set[str] = set()

    def set_registry(self, registry: CanonicalPlanningRegistry | None) -> None:
        self.registry = registry

    def process(
        self,
        requests: Any,
        *,
        stage_id: str,
        partition_id: str | None = None,
    ) -> list[dict[str, Any]]:
        values = requests if isinstance(requests, list) else []
        if not values:
            return []
        scope = f"{stage_id}:{partition_id or 'whole_stage'}"
        if scope in self._processed_scopes:
            outcomes = [self._outcome(scope, stage_id, partition_id, request, "unresolved", "amendment_round_limit") for request in values]
            self.records.extend(outcomes)
            self._write_log()
            return outcomes
        self._processed_scopes.add(scope)
        outcomes = [self._process_one(scope, stage_id, partition_id, request) for request in values]
        self.records.extend(outcomes)
        self._write_log()
        return outcomes

    def pending_for_stage(self, stage_id: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records
            if record["stage_id"] == stage_id and record["status"] in {"accepted_pending_rerun", "unresolved"}
        ]

    def count_for_stage(self, stage_id: str) -> int:
        return sum(record["stage_id"] == stage_id for record in self.records)

    def resolve_after_local_rerun(self, artifact_id: str, *, stage_id: str) -> None:
        for record in self.records:
            if (
                record["artifact_id"] == artifact_id
                and record["status"] == "accepted_pending_rerun"
                and any(item["stage_id"] == stage_id for item in record["rerun_partitions"])
            ):
                record["status"] = "resolved_after_local_rerun"
                record["diagnostic"] = "required local definition committed"
        self._write_log()

    def _process_one(
        self,
        scope: str,
        stage_id: str,
        partition_id: str | None,
        request: Any,
    ) -> dict[str, Any]:
        error = validate_artifact_request(request, self.registry)
        if error:
            return self._outcome(scope, stage_id, partition_id, request, "unresolved", error)
        assert isinstance(request, dict) and self.registry is not None
        kind = str(request["requested_kind"])
        name = str(request.get("proposed_name", ""))
        owner = self.registry.resolve(request["requested_owner"], expected_kinds={"file"})
        try:
            existing = self.registry.resolve(name, expected_kinds={kind})
        except RegistryBindingError as exc:
            if exc.code != "unknown_artifact_id":
                return self._outcome(scope, stage_id, partition_id, request, "unresolved", exc.code)
        else:
            if existing["owner_file_id"] != owner["artifact_id"]:
                return self._outcome(scope, stage_id, partition_id, request, "unresolved", "artifact_request_owner_conflict")
            return self._outcome(
                scope,
                stage_id,
                partition_id,
                request,
                "bound_existing",
                "request bound to existing canonical artifact",
                artifact_id=existing["artifact_id"],
            )
        try:
            entry = self.registry.register_amendment(
                requested_kind=kind,
                proposed_name=name,
                requested_owner=str(request["requested_owner"]),
                preferred_visibility=str(request["preferred_visibility"]),
                provenance=deepcopy(request["provenance"]),
            )
            self.registry.apply_overlay(
                entry["artifact_id"], {"semantic_role": str(request["semantic_role"])}
            )
        except (RegistryBindingError, RegistryInvariantError) as exc:
            return self._outcome(scope, stage_id, partition_id, request, "unresolved", str(exc))
        return self._outcome(
            scope,
            stage_id,
            partition_id,
            request,
            "accepted_pending_rerun",
            "canonical identity registered without interface or behavior",
            artifact_id=entry["artifact_id"],
            rerun_partitions=_rerun_partitions(kind, str(request["required_by"])),
        )

    @staticmethod
    def _outcome(
        scope: str,
        stage_id: str,
        partition_id: str | None,
        request: Any,
        status: str,
        diagnostic: str,
        *,
        artifact_id: str | None = None,
        rerun_partitions: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "scope": scope,
            "stage_id": stage_id,
            "partition_id": partition_id,
            "status": status,
            "artifact_id": artifact_id,
            "diagnostic": diagnostic,
            "request": deepcopy(request),
            "rerun_partitions": rerun_partitions or [],
        }

    def _write_log(self) -> None:
        if self.log_path is not None:
            write_json(
                self.log_path,
                {
                    "kind": "INVENTORY_AMENDMENT_LOG",
                    "schema_version": 1,
                    "bounded_rounds": sorted(self._processed_scopes),
                    "records": self.records,
                },
            )


def validate_artifact_request(
    request: Any,
    registry: CanonicalPlanningRegistry | None,
) -> str | None:
    if not isinstance(request, dict):
        return "artifact_request_invalid_shape"
    if not REQUIRED_REQUEST_FIELDS <= set(request) or set(request) - ALLOWED_REQUEST_FIELDS:
        return "artifact_request_invalid_fields"
    if registry is None:
        return "artifact_request_registry_unavailable"
    if str(request.get("requested_kind")) not in {"type", "callback", "function", "constant"}:
        return "artifact_request_kind_unsupported"
    name = str(request.get("proposed_name", ""))
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return "artifact_request_proposed_name_invalid"
    if not str(request.get("semantic_role", "")).strip() or not str(request.get("reason", "")).strip():
        return "artifact_request_rationale_missing"
    if str(request.get("preferred_visibility")) not in {"public", "private"}:
        return "artifact_request_visibility_invalid"
    provenance = request.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") not in {
        "inferred_engineering_decision",
        "engineering_decision",
        "open_assumption",
    }:
        return "artifact_request_provenance_invalid"
    if not isinstance(provenance.get("refs"), list) or not provenance["refs"]:
        return "artifact_request_provenance_refs_missing"
    try:
        registry.resolve(request.get("requested_owner"), expected_kinds={"file"})
        registry.resolve(request.get("required_by"))
    except RegistryBindingError as exc:
        return exc.code
    return None


def _rerun_partitions(kind: str, required_by: str) -> list[dict[str, str]]:
    stages = (
        ["type_and_access_path_design", "function_interface_design", "function_behavior_design", "function_call_contract_closure"]
        if kind in {"type", "callback"}
        else ["function_interface_design", "function_behavior_design", "function_call_contract_closure"]
        if kind == "function"
        else ["dependency_closure"]
    )
    return [{"stage_id": stage_id, "required_by": required_by} for stage_id in stages]
