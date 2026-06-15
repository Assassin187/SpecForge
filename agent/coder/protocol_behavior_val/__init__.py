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
        failed_index = len(scenarios)
        failed_name = runner.EXPECTED_SCENARIOS[failed_index] if failed_index < len(runner.EXPECTED_SCENARIOS) else "runtime_smoke"
        scenarios.append({"name": failed_name, "status": "failed", "detail": str(exc)})
        for name in runner.EXPECTED_SCENARIOS[failed_index + 1 :]:
            scenarios.append({"name": name, "status": "skipped", "detail": f"not run after {failed_name} failed"})
        return False, scenarios, str(exc)

    return True, scenarios, None
