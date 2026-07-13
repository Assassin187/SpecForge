from __future__ import annotations

from typing import Any, Iterable


USAGE_FIELDS = ("usage", "repair_usage", "semantic_correction_usage", "inventory_amendment_usage")


def build_run_metrics(
    stage_records: Iterable[dict[str, Any]],
    *,
    expected_stages: int,
    run_status: str,
    candidate_produced: bool = False,
    qualified_produced: bool = False,
    diagnostics: Iterable[dict[str, Any] | str] = (),
    semantic_patch_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    records = list(stage_records)
    diagnostic_items = [*diagnostics]
    for record in records:
        for key in ("stage_validation_error", "error"):
            if record.get(key):
                diagnostic_items.append(str(record[key]))

    codes = [
        str(item.get("code", "")) if isinstance(item, dict) else str(item).split(":", 1)[0]
        for item in diagnostic_items
    ]
    completed_stages = sum(record.get("status") == "completed" for record in records)
    partition_total = sum(int(record.get("partitions_total", 0)) for record in records)
    partition_completed = sum(
        int(
            record.get(
                "partitions_completed",
                int(record.get("partitions_total", 0)) - int(record.get("unresolved_partitions", 0))
                if record.get("status") == "completed"
                else 0,
            )
        )
        for record in records
    )
    unresolved_partitions = sum(int(record.get("unresolved_partitions", 0)) for record in records)
    corrections = sum(int(record.get("local_corrections", 0)) for record in records)
    correction_successes = sum(int(record.get("local_correction_successes", 0)) for record in records)

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for record in records:
        for field in USAGE_FIELDS:
            _add_usage(usage, record.get(field, {}))
    _add_usage(usage, semantic_patch_usage or {})
    stage_requests = sum(int(record.get("request_count", 0)) for record in records)
    prompt_characters = sum(int(record.get("prompt_characters", 0)) for record in records)
    semantic_requests = int((semantic_patch_usage or {}).get("request_count", 0))
    semantic_prompt_characters = int((semantic_patch_usage or {}).get("prompt_characters", 0))

    repair_attempts = sum(bool(record.get("repaired_json")) for record in records)
    truncation_failures = sum("truncat" in str(item).lower() for item in diagnostic_items)
    return {
        "kind": "PLANNING_STABILITY_METRICS",
        "schema_version": 1,
        "run_status": run_status,
        "stage_survival": {
            "completed": completed_stages,
            "expected": expected_stages,
            "rate": completed_stages / expected_stages if expected_stages else 1.0,
        },
        "partition_survival": {
            "completed": partition_completed,
            "total": partition_total,
            "unresolved": unresolved_partitions,
            "rate": partition_completed / partition_total if partition_total else 1.0,
        },
        "binding": {
            "unknown_id_count": sum("unknown_stable_id" in code or "unknown_id" in code for code in codes),
            "artifact_kind_mismatch_count": sum("kind_mismatch" in code for code in codes),
        },
        "recovery": {
            "local_corrections": corrections,
            "local_correction_successes": correction_successes,
            "local_correction_rate": correction_successes / corrections if corrections else None,
            "inventory_amendments": sum(int(record.get("inventory_amendments", 0)) for record in records),
        },
        "json_stability": {
            "repair_attempts": repair_attempts,
            "truncation_failures": truncation_failures,
        },
        "production": {"candidate": candidate_produced, "qualified": qualified_produced},
        "diagnostic_codes": codes,
        "request_accounting": {
            "structured_requests": stage_requests,
            "semantic_requests": semantic_requests,
            "total_requests": stage_requests + semantic_requests,
            "prompt_characters": prompt_characters + semantic_prompt_characters,
        },
        "token_accounting": {**usage, "observable_lower_bound": usage["total_tokens"]},
    }


def _add_usage(total: dict[str, int], item: dict[str, Any]) -> None:
    total["prompt_tokens"] += int(item.get("prompt_tokens", 0))
    total["completion_tokens"] += int(item.get("completion_tokens", 0))
    total["total_tokens"] += int(item.get("total_tokens", 0))
