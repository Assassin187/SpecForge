from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "evaluation" / "planning_utility" / "out"
PROFILE_ROOT = Path(__file__).resolve().parent / "target_profiles"


@dataclass(frozen=True)
class ProtocolConfig:
    protocol: str
    facts_path: Path
    target_profile_path: Path
    binary_name: str
    argv_contract: str
    transport: str


PROTOCOLS: dict[str, ProtocolConfig] = {
    "http": ProtocolConfig(
        protocol="http",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "http_min" / "protocol_facts.json",
        target_profile_path=PROFILE_ROOT / "planning_target_profile_http.json",
        binary_name="http_server",
        argv_contract="./http_server <port> <document_root>",
        transport="tcp",
    ),
    "mqtt": ProtocolConfig(
        protocol="mqtt",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_mqtt.json",
        binary_name="mqtt_broker",
        argv_contract="./mqtt_broker <port>",
        transport="tcp",
    ),
    "coap": ProtocolConfig(
        protocol="coap",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "coap_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_coap.json",
        binary_name="coap_server",
        argv_contract="./coap_server <port>",
        transport="udp",
    ),
    "smtp": ProtocolConfig(
        protocol="smtp",
        facts_path=REPO_ROOT / "agent" / "facts" / "gold_facts" / "smtp_min" / "protocol_facts.json",
        target_profile_path=REPO_ROOT / "agent" / "planning" / "planning_target_profile_smtp_min.json",
        binary_name="smtp_server",
        argv_contract="./smtp_server <port> <mail_store_dir>",
        transport="tcp",
    ),
}


def rel_to_repo(path: Path | str | None) -> str:
    if path is None:
        return ""
    value = Path(path)
    try:
        return value.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:  # noqa: BLE001
        return str(value)

