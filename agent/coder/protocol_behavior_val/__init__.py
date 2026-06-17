from __future__ import annotations

from pathlib import Path

from . import coap, http, mqtt, smtp


_RUNNERS = {
    "mqtt": mqtt,
    "coap": coap,
    "http": http,
    "smtp": smtp,
}


def verify_protocol_behavior(protocol_slug: str, project_dir: Path, binary_name: str) -> tuple[bool, list[dict[str, str]], str | None]:
    scenarios: list[dict[str, str]] = []
    runner = _RUNNERS.get(protocol_slug)
    if runner is None:
        scenarios.append({"name": "runtime_smoke", "status": "skipped", "detail": f"no verifier for protocol '{protocol_slug}'"})
        return True, scenarios, None

    try:
        runner.run(project_dir, binary_name, scenarios)
    except Exception as exc:  # noqa: BLE001
        scenarios.append({"name": "runtime_error", "status": "failed", "detail": str(exc)})
        return False, scenarios, str(exc)

    # Determine overall success: all expected scenarios must have passed
    expected = set(runner.EXPECTED_SCENARIOS)
    passed = {s["name"] for s in scenarios if s.get("status") == "passed"}
    all_passed = expected.issubset(passed)
    return all_passed, scenarios, None
