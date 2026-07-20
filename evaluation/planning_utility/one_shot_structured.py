from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.common.llm_client import FixedQwenClient, LLMRequest
from agent.planning.compiler import compile_specs, normalize_plan_for_compiler
from agent.planning.facts import stable_json_hash
from agent.planning.knowledge import activate_engineering_rules, extract_open_assumptions, normalize_characteristics
from agent.planning.planner import PLANNING_STAGES, build_planning_context

from .configs import ProtocolConfig, rel_to_repo
from .full_specforge_adapter import run_full_specforge
from .requirements import read_json, write_json


METHOD_ONE_SHOT = "one-shot-structured-planning"
ONE_SHOT_MAX_COMPLETION_TOKENS = 16000


def _usage_payload(response: Any) -> dict[str, int]:
    usage = response.usage
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _messages(context: dict[str, Any]) -> list[dict[str, str]]:
    final_stage = PLANNING_STAGES[-1]
    contract = {
        "schema_version": "specforge_planning_ir_v1",
        "required_top_level_fields": final_stage.required_output,
        "required_object_shapes": {
            "function": {
                "required": ["id", "name", "owner_file", "visibility", "function_type", "signature", "trace_refs"],
                "signature": {
                    "required": ["RAW", "NAME", "RETURN", "PARAMS"],
                    "rules": [
                        "RAW is a complete C declaration without a trailing semicolon.",
                        "NAME exactly equals the function name.",
                        "PARAMS is an array of {NAME, TYPE, ROLE, NULLABLE, OWNERSHIP}; NULLABLE is boolean.",
                        "Every function, including private helpers, requires a complete signature.",
                    ],
                },
                "behavior": "Provide exactly one of LOGIC or EVENT, plus WIRE_MAPPING and TEST_VECTORS when applicable.",
            },
            "file": {
                "required": ["id", "module", "header_path", "source_path", "types", "functions", "trace_refs"],
                "rules": ["types and functions contain exact IDs defined at top level.", "Every source file has one paired header path."],
            },
            "type": {
                "required": ["id", "name", "owner_file", "visibility", "type_spec", "trace_refs"],
                "rules": ["TYPE_SPEC includes TYPE_KIND and all fields/values/signature required by its kind."],
            },
            "module": {
                "required": ["id", "name", "role", "dependencies", "files", "trace_refs"],
                "rules": [
                    "dependencies contains exact module name values, never module IDs.",
                    "files contains exact file IDs whose module equals this module name.",
                    "modules is in topological generation order: every dependency appears earlier than its dependent module.",
                ],
            },
        },
        "compiler_rules": [
            "Return one JSON object with exactly one implementation_plan object.",
            "The plan must be self-contained and directly consumable by normalize_plan_for_compiler and compile_specs.",
            "Use only protocol facts and the supplied engineering rules; make unsupported policy choices explicit in open_assumptions.",
            "Every module, file, type, function, dependency, call contract, wire mapping, test vector, and trace reference needed by the compiler must be present in this one plan.",
            "Referential integrity is mandatory: file.module equals a module.name; module.dependencies use module.name; type.owner_file and function.owner_file equal file.id; file.types and file.functions use the corresponding top-level IDs.",
            "Do not return stage artifacts, C source code, Markdown, explanations, reference specs, or any fields outside the response envelope.",
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "You are the One-shot Structured Planning baseline for SpecForge. "
                "Return strict JSON only. You must generate one complete implementation plan in one response. "
                "Do not access or imitate existing specs, generated code, or local paths."
            ),
        },
        {
            "role": "user",
            "content": "Generate the required one-shot implementation plan.\n\n"
            + json.dumps({"contract": contract, "planning_context": context}, ensure_ascii=False, indent=2),
        },
    ]


def _write_failure(
    planning_dir: Path,
    facts_path: Path,
    facts_hash: str,
    *,
    code: str,
    message: str,
    token_accounting: dict[str, int],
) -> None:
    planning_root = planning_dir / "_planning"
    diagnostics = [{"level": "error", "code": code, "message": message}]
    write_json(planning_root / "diagnostics.json", diagnostics)
    write_json(
        planning_root / "run_manifest.json",
        {
            "kind": "ONE_SHOT_PLANNING_RUN_MANIFEST",
            "planner_mode": "one_shot_structured",
            "run_status": "failed_internal",
            "facts_path": str(facts_path),
            "facts_sha256": facts_hash,
            "fatal": True,
            "fatal_reason_code": code,
            "hard_failure_code": code,
            "specs_generated": False,
            "candidate_root": str(planning_root),
            "candidate_specs_root": None,
            "specs_root": None,
            "qualification_passed": False,
            "planning_validation_passed": False,
            "coder_loader_passed": None,
            "implementation_ready": False,
            "coder_release_eligible": False,
            "coder_release_blockers": [code],
            "diagnostic_counts": {"error": 1, "warning": 0},
            "token_accounting": token_accounting,
            "logical_planning_call_count": 1,
            "content_retry_count": 0,
            "continuation_count": 0,
            "planning_validator_used": False,
            "semantic_closure_used": False,
        },
    )


def _generate_planning_run(config: ProtocolConfig, planning_dir: Path, *, api_key_env: str) -> None:
    planning_root = planning_dir / "_planning"
    planning_root.mkdir(parents=True, exist_ok=True)
    facts = read_json(config.facts_path)
    facts_hash = stable_json_hash(facts)
    characteristics = normalize_characteristics(facts)
    rules = activate_engineering_rules(characteristics)
    assumptions = extract_open_assumptions(facts, characteristics)
    context = build_planning_context(facts, characteristics, rules, assumptions)
    messages = _messages(context)
    write_json(planning_root / "planning_context.json", context)
    write_json(planning_root / "one_shot_prompt.json", messages)

    client = FixedQwenClient(api_key_env)
    response = client.generate_with_usage(
        LLMRequest(
            messages=messages,
            top_p=0.2,
            temperature=0.1,
            is_stream=True,
            enable_thinking=False,
            max_completion_tokens=ONE_SHOT_MAX_COMPLETION_TOKENS,
        )
    )
    raw_response = response.content
    (planning_root / "one_shot_response.raw.txt").write_text(raw_response, encoding="utf-8")
    usage = _usage_payload(response)
    write_json(planning_root / "one_shot_usage.json", usage)
    if raw_response.lstrip().startswith("```"):
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_non_json", message="response contains a Markdown fence", token_accounting=usage)
        return
    try:
        envelope = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_invalid_json", message=str(exc), token_accounting=usage)
        return
    if not isinstance(envelope, dict) or set(envelope) != {"implementation_plan"}:
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_invalid_envelope", message="response must contain only implementation_plan", token_accounting=usage)
        return
    plan = envelope["implementation_plan"]
    if not isinstance(plan, dict) or plan.get("schema_version") != "specforge_planning_ir_v1":
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_invalid_plan", message="implementation_plan must use specforge_planning_ir_v1", token_accounting=usage)
        return
    write_json(planning_root / "implementation_plan.json", plan)
    try:
        normalized = normalize_plan_for_compiler(plan)
        specs_root = planning_root / "specs"
        compile_manifest = compile_specs(normalized, specs_root, clean=True)
    except Exception as exc:  # noqa: BLE001 - malformed one-shot plans must be recorded as planning failures.
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_spec_compile_failed", message=str(exc), token_accounting=usage)
        return
    write_json(planning_root / "compiler_manifest.json", compile_manifest)
    inventory = {"module": 0, "file": 0, "function": 0}
    for path in specs_root.rglob("*_spec.json"):
        kind = read_json(path).get("KIND")
        if kind == "PROTOCOL_MODULE_SPEC":
            inventory["module"] += 1
        elif kind == "FILE_SPEC":
            inventory["file"] += 1
        elif kind == "FUNCTION_SPEC":
            inventory["function"] += 1
    if not all(inventory.values()) or not (specs_root / "SUMMARY.md").is_file():
        _write_failure(planning_dir, config.facts_path, facts_hash, code="one_shot_incomplete_specs", message="compiler did not materialize a complete spec set", token_accounting=usage)
        return
    write_json(
        planning_root / "run_manifest.json",
        {
            "kind": "ONE_SHOT_PLANNING_RUN_MANIFEST",
            "planner_mode": "one_shot_structured",
            "run_status": "completed_with_one_shot_specs",
            "facts_path": str(config.facts_path),
            "facts_sha256": facts_hash,
            "fatal": False,
            "specs_generated": True,
            "candidate_root": str(planning_root),
            "candidate_specs_root": str(specs_root),
            "specs_root": str(specs_root),
            "module_spec": compile_manifest["module_spec"],
            "summary": compile_manifest["summary"],
            "semantic_mapping": compile_manifest["semantic_mapping"],
            "written_files": compile_manifest["written_files"],
            "qualification_passed": True,
            "planning_validation_passed": False,
            "coder_loader_passed": None,
            "implementation_ready": True,
            "coder_release_eligible": True,
            "coder_release_blockers": [],
            "diagnostic_counts": {"error": 0, "warning": len(compile_manifest.get("diagnostics", []))},
            "token_accounting": usage,
            "logical_planning_call_count": 1,
            "content_retry_count": 0,
            "continuation_count": 0,
            "planning_validator_used": False,
            "semantic_closure_used": False,
            "spec_inventory": inventory,
            "response_sha256": hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
        },
    )


def run_one_shot_structured(
    config: ProtocolConfig,
    output_dir: Path,
    *,
    api_key_env: str,
    max_repair_rounds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    planning_dir = output_dir / "one_shot_planning"
    _generate_planning_run(config, planning_dir, api_key_env=api_key_env)
    summary = run_full_specforge(
        config,
        output_dir,
        api_key_env=api_key_env,
        max_repair_rounds=max_repair_rounds,
        existing_planning_dir=planning_dir,
        method=METHOD_ONE_SHOT,
        skip_planning_validate=True,
        planning_is_fresh=True,
    )
    summary["implementation_plan_path"] = rel_to_repo(planning_dir / "_planning" / "implementation_plan.json")
    summary["planning_call_count"] = 1
    summary["planning_validator_used"] = False
    write_json(output_dir / "summary.json", summary)
    return summary
