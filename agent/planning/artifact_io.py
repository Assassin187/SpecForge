from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


STEP_LOGS_DIRNAME = "_step_logs"
AGENT_LOGS_DIRNAME = "_agent_logs"
VALIDATION_REPORTS_DIRNAME = "_validation_reports"


def safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [to_jsonable(child) for child in value]
    if isinstance(value, tuple):
        return [to_jsonable(child) for child in value]
    return value


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "sha256": file_sha256(path),
    }


class ArtifactStore:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.agent_logs_dir = self.output_dir / AGENT_LOGS_DIRNAME
        self.step_logs_dir = self.output_dir / STEP_LOGS_DIRNAME
        self.validation_reports_dir = self.output_dir / VALIDATION_REPORTS_DIRNAME
        self.resume_metadata: dict[str, Any] | None = None
        self.stop_metadata: dict[str, Any] | None = None
        self.agent_logs_dir.mkdir(parents=True, exist_ok=True)
        self.step_logs_dir.mkdir(parents=True, exist_ok=True)
        self.validation_reports_dir.mkdir(parents=True, exist_ok=True)
        self._log_counter = 0
        self.event_log_path = self.agent_logs_dir / "000_stage_events.log"

    def log_event(self, message: str) -> Path:
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
        print(f"[agent.planning] {message.rstrip()}", flush=True)
        return self.event_log_path

    def write_agent_log(self, label: str, content: str, suffix: str = ".txt") -> Path:
        self._log_counter += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "log"
        path = self.agent_logs_dir / f"{self._log_counter:03d}_{safe}{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def step_path(self, filename: str) -> Path:
        if "validation_report" in filename:
            return self.validation_reports_dir / filename
        return self.step_logs_dir / filename

    def write_step_json(self, filename: str, data: Any) -> Path:
        return write_json(self.step_path(filename), data)
