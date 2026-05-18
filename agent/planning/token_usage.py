from __future__ import annotations

from datetime import datetime
from typing import Any


def _empty_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class TokenUsageTracker:
    def __init__(self) -> None:
        self._attempts: list[dict[str, Any]] = []

    def add_attempt(
        self,
        *,
        stage: str,
        prompt_name: str,
        attempt: int,
        meta: dict[str, Any],
        accepted: bool | None = None,
    ) -> None:
        usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
        self._attempts.append(
            {
                "stage": stage,
                "prompt_name": prompt_name,
                "attempt": attempt,
                "accepted": accepted,
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                "hit_completion_limit": bool(meta.get("hit_completion_limit")) if isinstance(meta, dict) else False,
                "failed": bool(meta.get("failed")) if isinstance(meta, dict) else False,
            }
        )

    def mark_attempt_accepted(self, *, stage: str, prompt_name: str, attempt: int) -> None:
        for item in reversed(self._attempts):
            if item["stage"] == stage and item["prompt_name"] == prompt_name and item["attempt"] == attempt:
                item["accepted"] = True
                return

    def summary(self) -> dict[str, Any]:
        by_stage: dict[str, dict[str, Any]] = {}
        total = _empty_usage()
        for item in self._attempts:
            stage = item["stage"]
            bucket = by_stage.setdefault(
                stage,
                {
                    **_empty_usage(),
                    "attempt_count": 0,
                    "accepted_attempt_count": 0,
                    "failed_attempt_count": 0,
                    "hit_completion_limit_count": 0,
                    "prompts": {},
                },
            )
            bucket["attempt_count"] += 1
            if item.get("accepted"):
                bucket["accepted_attempt_count"] += 1
            if item.get("failed"):
                bucket["failed_attempt_count"] += 1
            if item.get("hit_completion_limit"):
                bucket["hit_completion_limit_count"] += 1
            prompt_bucket = bucket["prompts"].setdefault(item["prompt_name"], {**_empty_usage(), "attempt_count": 0})
            prompt_bucket["attempt_count"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                bucket[key] += int(item[key])
                prompt_bucket[key] += int(item[key])
                total[key] += int(item[key])
        return {
            "schema_version": "planning_token_usage_summary/v1",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "total": total,
            "by_stage": by_stage,
            "attempts": list(self._attempts),
        }

