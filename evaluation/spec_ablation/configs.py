from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_EXAMPLE_ROOT = REPO_ROOT / "specs-example"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "evaluation" / "spec_ablation" / "specs"


@dataclass(frozen=True)
class ProtocolConfig:
    protocol: str
    specs_root: Path


PROTOCOL_ORDER: tuple[str, ...] = ("mqtt", "http", "coap", "smtp")
PROTOCOL_CONFIGS: dict[str, ProtocolConfig] = {
    protocol: ProtocolConfig(protocol=protocol, specs_root=SPECS_EXAMPLE_ROOT / f"{protocol}_specs")
    for protocol in PROTOCOL_ORDER
}


def protocol_keys(value: str | None = None) -> list[str]:
    if value in (None, "all"):
        return list(PROTOCOL_ORDER)
    if value not in PROTOCOL_CONFIGS:
        raise ValueError(f"Unsupported protocol '{value}'")
    return [value]


def rel_to_repo(path: Path | str | None) -> str:
    if path is None:
        return ""
    value = Path(path)
    try:
        return value.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:  # noqa: BLE001
        return str(value)

