from __future__ import annotations


SCHEMA_VERSION = "planning_run_manifest/v1"


REQUIRED_KEYS = {
    "schema_version",
    "created_at",
    "status",
    "inputs",
    "validated_input_paths",
    "compatibility",
    "output_dir",
    "artifacts",
    "diagnostics",
}
