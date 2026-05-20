from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..diagnostics import PlanningDiagnostic


KIND_TO_SCHEMA = {
    "PROTOCOL_MODULE_SPEC": "module_spec_schema.json",
    "FILE_SPEC": "file_spec_schema.json",
    "FUNCTION_SPEC": "function_spec_schema.json",
}


def default_schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "specs-example" / "specs_schema"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_path(error_path: Any) -> str:
    parts = [str(part) for part in error_path]
    return "$" + "".join(f"[{part}]" if part.isdigit() else f".{part}" for part in parts)


def validate_coder_spec_bundle_against_schema(spec_root: str | Path, schema_root: str | Path | None = None) -> list[PlanningDiagnostic]:
    root = Path(spec_root)
    schemas = Path(schema_root) if schema_root is not None else default_schema_root()
    diagnostics: list[PlanningDiagnostic] = []
    validators: dict[str, Draft202012Validator] = {}

    for kind, filename in KIND_TO_SCHEMA.items():
        schema_path = schemas / filename
        if not schema_path.exists():
            diagnostics.append(PlanningDiagnostic("error", "coder_schema_missing_schema", f"Missing schema file '{schema_path}'", str(schema_path)))
            continue
        try:
            schema = _read_json(schema_path)
            Draft202012Validator.check_schema(schema)
            validators[kind] = Draft202012Validator(schema)
        except (OSError, json.JSONDecodeError, SchemaError) as exc:
            diagnostics.append(PlanningDiagnostic("error", "coder_schema_read_error", f"Could not load schema '{schema_path}': {exc}", str(schema_path)))

    if diagnostics:
        return diagnostics

    spec_paths = sorted(root.rglob("*_spec.json"))
    if not spec_paths:
        return [PlanningDiagnostic("error", "coder_schema_no_specs", f"No *_spec.json files found under '{root}'", str(root))]

    for path in spec_paths:
        try:
            raw = _read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append(PlanningDiagnostic("error", "coder_schema_read_error", f"Could not read spec '{path}': {exc}", str(path)))
            continue
        kind = str(raw.get("KIND", ""))
        validator = validators.get(kind)
        if validator is None:
            diagnostics.append(PlanningDiagnostic("error", "coder_schema_unknown_kind", f"Unsupported spec KIND '{kind}'", str(path)))
            continue
        for error in sorted(validator.iter_errors(raw), key=lambda item: list(item.path)):
            diagnostics.append(
                PlanningDiagnostic(
                    "error",
                    "coder_schema_invalid",
                    f"{_json_path(error.path)}: {error.message}",
                    str(path),
                )
            )
    return diagnostics
